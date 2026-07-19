"""Authenticated FastAPI application for WebsiteBench corpus QA."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    AuthManager,
    AuthSettings,
    LoginLimiter,
)
from .clone_process import CloneGatewayError, CloneProcessManager
from .discovery import CorpusIndex, discover_corpus
from .evidence import EvidenceStore
from .gateway import HOP_BY_HOP_HEADERS, rewrite_clone_body, rewrite_location, rewrite_set_cookie
from .reviews import ReviewConflict, ReviewError, ReviewStore, empty_review


PACKAGE_ROOT = Path(__file__).resolve().parent
SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else "/"


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _gateway_allowlist() -> set[str]:
    return {
        value.strip()
        for value in os.environ.get("CLAWBENCH_VIEWER_CLONE_ALLOWLIST", "").split(",")
        if value.strip()
    }


def _template_context(
    request: Request,
    *,
    index: CorpusIndex,
    auth: AuthManager,
    **values: Any,
) -> dict[str, Any]:
    session = auth.session(request.cookies.get(SESSION_COOKIE))
    return {
        "request": request,
        "profile": index.profile,
        "session": session,
        "csrf_token": session.get("csrf") if session else "",
        **values,
    }


def create_app(
    repo_root: Path | None = None,
    *,
    profile: str = "internal",
    settings: AuthSettings | None = None,
    review_root: Path | None = None,
    evidence_root: Path | None = None,
    public_allowlist: Path | None = None,
) -> FastAPI:
    root = (repo_root or Path.cwd()).resolve()
    settings = settings or AuthSettings.from_env()
    auth = AuthManager(settings)
    limiter = LoginLimiter(settings.login_attempts, settings.login_window_seconds)
    index = discover_corpus(root, profile=profile, public_allowlist=public_allowlist)
    reviews = ReviewStore(
        review_root or root / "artifacts" / "websitebench-viewer" / "reviews", root
    )
    evidence = EvidenceStore(
        evidence_root or root / "artifacts" / "websitebench-viewer" / "visual", root
    )
    clone_manager = CloneProcessManager(root, index.items, _gateway_allowlist())
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            clone_manager.close()

    app = FastAPI(
        title="WebsiteBench Clone Atlas",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")
    app.state.corpus_index = index
    app.state.review_store = reviews
    app.state.evidence_store = evidence
    app.state.clone_manager = clone_manager

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        clone_response = request.url.path.startswith("/clone/")
        nonce = response.headers.get("x-clawbench-script-nonce")
        if nonce:
            del response.headers["x-clawbench-script-nonce"]
        if clone_response and nonce:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; "
                f"style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-{nonce}'; "
                "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; "
                "form-action 'self'; frame-ancestors 'self'"
            )
        else:
            response.headers["Content-Security-Policy"] = SECURITY_POLICY
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if clone_response else "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    def session_or_none(request: Request) -> dict[str, Any] | None:
        return auth.session(request.cookies.get(SESSION_COOKIE))

    def require_page(request: Request) -> dict[str, Any] | RedirectResponse:
        session = session_or_none(request)
        if session is None:
            return RedirectResponse(
                f"/login?next={request.url.path}", status_code=303
            )
        return session

    def require_api(request: Request, *, csrf: bool = False) -> dict[str, Any]:
        session = session_or_none(request)
        if session is None:
            raise HTTPException(401, "authentication required")
        if csrf and not auth.csrf_matches(session, request.headers.get("x-csrf-token")):
            raise HTTPException(403, "invalid CSRF token")
        return session

    def current_review(item: dict[str, Any]) -> dict[str, Any]:
        review = reviews.load(item["key"])
        if profile == "public" and review and not (
            review["gate"] == "approve" and review["visibility"] == "public"
        ):
            review = None
        return review or empty_review(item["key"], item["artifact_fingerprint"])

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "profile": profile}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str | None = None) -> Response:
        if session_or_none(request):
            return RedirectResponse(_safe_next(next), status_code=303)
        token = auth.login_csrf()
        response = templates.TemplateResponse(
            request,
            "login.html",
            _template_context(
                request,
                index=index,
                auth=auth,
                login_csrf=token,
                next_path=_safe_next(next),
                error=None,
            ),
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            token,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
            max_age=15 * 60,
            path="/login",
        )
        return response

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
        next_path: str = Form("/"),
    ) -> Response:
        client = _client_key(request)
        if not limiter.allowed(client):
            raise HTTPException(429, "too many login attempts; try again later")
        csrf_ok = auth.verify_login_csrf(
            csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE)
        )
        credentials_ok = csrf_ok and auth.verify_password(username, password)
        if not credentials_ok:
            limiter.failure(client)
            token = auth.login_csrf()
            response = templates.TemplateResponse(
                request,
                "login.html",
                _template_context(
                    request,
                    index=index,
                    auth=auth,
                    login_csrf=token,
                    next_path=_safe_next(next_path),
                    error="Login failed. Check the credentials and try again.",
                ),
                status_code=401,
            )
            response.set_cookie(
                LOGIN_CSRF_COOKIE,
                token,
                secure=settings.cookie_secure,
                httponly=True,
                samesite="strict",
                max_age=15 * 60,
                path="/login",
            )
            return response
        limiter.success(client)
        response = RedirectResponse(_safe_next(next_path), status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            auth.session_token(),
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
            max_age=settings.session_seconds,
            path="/",
        )
        response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
        return response

    @app.post("/logout")
    async def logout(request: Request, csrf_token: str = Form("")) -> Response:
        session = session_or_none(request)
        if session is None or not auth.csrf_matches(session, csrf_token):
            raise HTTPException(403, "invalid CSRF token")
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        session = require_page(request)
        if isinstance(session, RedirectResponse):
            return session
        data = index.as_dict()
        item_reviews = {item["key"]: current_review(item) for item in index.items}
        return templates.TemplateResponse(
            request,
            "home.html",
            _template_context(
                request, index=index, auth=auth, summary=data["summary"], items=index.items,
                reviews=item_reviews,
            ),
        )

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks_page(request: Request) -> Response:
        session = require_page(request)
        if isinstance(session, RedirectResponse):
            return session
        return templates.TemplateResponse(
            request,
            "tasks.html",
            _template_context(
                request, index=index, auth=auth, items=index.items,
                reviews={item["key"]: current_review(item) for item in index.items},
            ),
        )

    @app.get("/tasks/{item_key}", response_class=HTMLResponse)
    async def task_detail(request: Request, item_key: str) -> Response:
        session = require_page(request)
        if isinstance(session, RedirectResponse):
            return session
        item = index.by_key(item_key)
        if item is None:
            raise HTTPException(404, "corpus item not found")
        visual = evidence.load(item_key) or item.get("visual_evidence")
        return templates.TemplateResponse(
            request,
            "task_detail.html",
            _template_context(
                request, index=index, auth=auth, item=item, review=current_review(item),
                visual=visual, gateway_allowed=clone_manager.is_allowed(item_key),
            ),
        )

    @app.get("/compare", response_class=HTMLResponse)
    async def compare_page(
        request: Request,
        items: list[str] = Query(default=[]),
        keys: str | None = None,
    ) -> Response:
        session = require_page(request)
        if isinstance(session, RedirectResponse):
            return session
        selected_keys = items or ([part for part in (keys or "").split(",") if part])
        if not selected_keys:
            selected_keys = [item["key"] for item in index.items[:2]]
        selected_keys = list(dict.fromkeys(selected_keys))
        if len(selected_keys) > 4:
            raise HTTPException(400, "compare accepts at most four corpus items")
        selected = [index.by_key(key) for key in selected_keys]
        selected = [item for item in selected if item is not None]
        return templates.TemplateResponse(
            request,
            "compare.html",
            _template_context(
                request, index=index, auth=auth, items=index.items, selected=selected,
                selected_keys=selected_keys,
                reviews={item["key"]: current_review(item) for item in selected},
                selection_error="Choose 2–4 tasks to compare." if len(selected) < 2 else None,
            ),
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> Response:
        session = require_page(request)
        if isinstance(session, RedirectResponse):
            return session
        run = index.run_by_id(run_id)
        if run is None:
            raise HTTPException(404, "valid websitebench.result.v1 run not found")
        item = next(item for item in index.items if run in item["official_runs"])
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            _template_context(request, index=index, auth=auth, run=run, item=item),
        )

    @app.get("/methodology", response_class=HTMLResponse)
    async def methodology(request: Request) -> Response:
        session = require_page(request)
        if isinstance(session, RedirectResponse):
            return session
        return templates.TemplateResponse(
            request, "methodology.html", _template_context(request, index=index, auth=auth)
        )

    @app.get("/api/reviews/export")
    async def reviews_export(request: Request) -> Response:
        require_api(request)
        bundle = reviews.export(public_only=profile == "public")
        return Response(
            json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=websitebench-reviews.json"},
        )

    @app.get("/api/reviews/{item_key}")
    async def review_get(request: Request, item_key: str) -> dict[str, Any]:
        require_api(request)
        item = index.by_key(item_key)
        if item is None:
            raise HTTPException(404, "corpus item not found")
        return current_review(item)

    @app.put("/api/reviews/{item_key}")
    async def review_put(request: Request, item_key: str) -> Response:
        session = require_api(request, csrf=True)
        if profile == "public":
            raise HTTPException(403, "review writes are disabled in the public profile")
        item = index.by_key(item_key)
        if item is None:
            raise HTTPException(404, "corpus item not found")
        try:
            body = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "request body must be JSON") from exc
        if body.get("artifact_fingerprint") not in {None, item["artifact_fingerprint"]}:
            return JSONResponse(
                {"error": "artifact fingerprint changed; reload before reviewing"}, status_code=409
            )
        try:
            review = reviews.save(
                item_key,
                body.get("review", body),
                expected_revision=int(body.get("expected_revision", -1)),
                artifact_fingerprint=item["artifact_fingerprint"],
                default_reviewer=session["username"],
            )
        except ReviewConflict as exc:
            return JSONResponse(
                {"error": str(exc), "current_revision": exc.current}, status_code=409
            )
        except (ReviewError, TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse(review)

    @app.post("/api/reviews/import")
    async def reviews_import(request: Request) -> Response:
        require_api(request, csrf=True)
        if profile == "public":
            raise HTTPException(403, "review imports are disabled in the public profile")
        try:
            bundle = await request.json()
            known = {item["key"] for item in index.items}
            unknown = [review.get("item_key") for review in bundle.get("reviews", []) if review.get("item_key") not in known]
            if unknown:
                raise ReviewError(f"unknown corpus item keys: {', '.join(unknown)}")
            imported = reviews.import_batch(bundle)
        except ReviewConflict as exc:
            return JSONResponse(
                {"error": str(exc), "current_revision": exc.current}, status_code=409
            )
        except (ReviewError, AttributeError, json.JSONDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse({"imported": len(imported)})

    @app.get("/artifacts/{item_key}/{artifact_path:path}")
    async def artifact(request: Request, item_key: str, artifact_path: str) -> Response:
        require_api(request)
        item = index.by_key(item_key)
        if item is None:
            raise HTTPException(404, "corpus item not found")
        if profile == "public":
            raise HTTPException(404, "artifact not published")
        try:
            if artifact_path.startswith("legacy/"):
                number = int(artifact_path.removeprefix("legacy/"))
                registered = item.get("legacy_screenshots", [])
                relative = registered[number]
                path = (root / relative).resolve()
                clone_root = (root / item["internal"]["clone_root"]).resolve()
                if clone_root not in path.parents or not path.is_file():
                    raise FileNotFoundError(artifact_path)
            else:
                path = evidence.resolve(item_key, artifact_path)
        except (FileNotFoundError, IndexError, ValueError, KeyError):
            raise HTTPException(404, "artifact not found") from None
        return FileResponse(path)

    async def proxy_clone(request: Request, item_key: str, clone_path: str = "") -> Response:
        session = require_api(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("x-csrf-token")
            source = request.headers.get("origin") or request.headers.get("referer")
            source_host = urlsplit(source).netloc if source else ""
            same_origin = bool(
                source_host
                and source_host.lower() == request.headers.get("host", "").lower()
            )
            if not auth.csrf_matches(session, supplied) and not same_origin:
                raise HTTPException(403, "invalid CSRF origin or token")
        if profile != "internal":
            raise HTTPException(404, "clone gateway is unavailable")
        try:
            base = clone_manager.ensure(item_key)
        except CloneGatewayError as exc:
            raise HTTPException(403, str(exc)) from exc
        target = f"{base}/{clone_path}"
        if request.url.query:
            target += f"?{request.url.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS | {"host", "content-length", "accept-encoding", "cookie", "x-csrf-token", "origin", "referer"}
        }
        headers["origin"] = base
        headers["referer"] = target
        clone_cookies = {
            key: value
            for key, value in request.cookies.items()
            if key not in {SESSION_COOKIE, LOGIN_CSRF_COOKIE}
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=30,
                trust_env=False,
                cookies=clone_cookies,
            ) as client:
                backend = await client.request(
                    request.method,
                    target,
                    headers=headers,
                    content=await request.body(),
                )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"clone gateway failed: {exc}") from exc
        content_type = backend.headers.get("content-type")
        nonce = secrets.token_urlsafe(18)
        body = rewrite_clone_body(
            backend.content, content_type, item_key, script_nonce=nonce
        )
        response_headers = {
            key: value
            for key, value in backend.headers.items()
            if key.lower()
            not in HOP_BY_HOP_HEADERS
            | {"content-length", "content-encoding", "content-security-policy", "x-frame-options", "set-cookie", "location"}
        }
        if backend.headers.get("location"):
            response_headers["location"] = rewrite_location(backend.headers["location"], item_key)
        response = Response(body, status_code=backend.status_code, headers=response_headers)
        response.headers["x-clawbench-script-nonce"] = nonce
        for cookie in backend.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", rewrite_set_cookie(cookie, item_key))
        return response

    app.add_api_route(
        "/clone/{item_key}", proxy_clone, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    app.add_api_route(
        "/clone/{item_key}/{clone_path:path}", proxy_clone,
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    return app
