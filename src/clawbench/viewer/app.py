"""Public Amazon benchmark site with an authenticated review workspace."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import LOGIN_CSRF_COOKIE, SESSION_COOKIE, AuthManager, AuthSettings, LoginLimiter
from .clone_process import CloneGatewayError, CloneProcessManager
from .discovery import AMAZON_ITEM_KEY, CorpusIndex, discover_corpus
from .gateway import HOP_BY_HOP_HEADERS, rewrite_clone_body, rewrite_location, rewrite_set_cookie
from .reviews import ReviewConflict, ReviewError, ReviewStore, empty_review


PACKAGE_ROOT = Path(__file__).resolve().parent
# Some minimal Linux images do not register WebP in /etc/mime.types.  The
# public site sends ``X-Content-Type-Options: nosniff``, so an unknown WebP
# would otherwise be served as text/plain and browsers would refuse to render
# the hero artwork.
mimetypes.add_type("image/webp", ".webp", strict=True)
SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
EVIDENCE_TYPES = {"source", "clone", "pair", "heatmap", "full-page"}
EVIDENCE_VIEWPORTS = {"desktop", "tablet", "mobile"}
REPORT_LABELS_ZH = {
    "clone-verification": "克隆站验证报告",
    "gate2-report": "Gate 2 报告",
    "gate2-review": "Gate 2 审核记录",
    "gate3-report": "Gate 3 报告",
    "gate3-review": "Gate 3 审核记录",
    "gate4-report": "Gate 4 报告",
    "gate4-review": "Gate 4 审核记录",
    "gate4-approval": "Gate 4 批准记录",
}


def _safe_next(value: str | None, default: str = "/admin") -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else default


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _gateway_allowlist() -> set[str]:
    configured = {
        value.strip()
        for value in os.environ.get("CLAWBENCH_VIEWER_CLONE_ALLOWLIST", "").split(",")
        if value.strip()
    }
    return {AMAZON_ITEM_KEY} if AMAZON_ITEM_KEY in configured else set()


def _public_auth_settings() -> AuthSettings:
    return AuthSettings(
        username="disabled",
        password_hash="disabled",
        session_secret="public-profile-session-signing-key-disabled",
        cookie_secure=True,
    )


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


def _featured_evidence(index: CorpusIndex) -> list[dict[str, Any]]:
    records = index.evidence_registry.records
    preferences = [
        ("source", "product"),
        ("clone", "task cart"),
        ("pair", "product"),
        ("heatmap", "filtered search"),
        ("full-page", "storefront home"),
    ]
    selected: list[dict[str, Any]] = []
    for kind, phrase in preferences:
        match = next(
            (
                row
                for row in records
                if row["type"] == kind and phrase in row["scene"].casefold() and row not in selected
            ),
            None,
        )
        if match is None:
            match = next(
                (row for row in records if row["type"] == kind and row not in selected), None
            )
        if match:
            selected.append(match)
    return selected


def create_app(
    repo_root: Path | None = None,
    *,
    profile: str = "internal",
    settings: AuthSettings | None = None,
    review_root: Path | None = None,
    evidence_root: Path | None = None,
) -> FastAPI:
    del evidence_root  # Public evidence is fixed and never caller-selected.
    if profile not in {"internal", "public"}:
        raise ValueError("profile must be internal or public")
    root = (repo_root or Path.cwd()).resolve()
    if profile == "internal":
        settings = settings or AuthSettings.from_env()
    else:
        settings = settings or _public_auth_settings()
    auth = AuthManager(settings)
    limiter = LoginLimiter(settings.login_attempts, settings.login_window_seconds)
    index = discover_corpus(root, profile=profile)
    item = index.items[0]
    reviews = ReviewStore(
        review_root or root / "artifacts" / "websitebench-viewer" / "reviews",
        root,
        allowed_keys={AMAZON_ITEM_KEY},
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
        title="WebsiteBench Amazon",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")
    app.state.corpus_index = index
    app.state.review_store = reviews
    app.state.evidence_registry = index.evidence_registry
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
                f"/login?{urlencode({'next': request.url.path})}", status_code=303
            )
        return session

    def require_api(request: Request, *, csrf: bool = False) -> dict[str, Any]:
        session = session_or_none(request)
        if session is None:
            raise HTTPException(401, "authentication required")
        if csrf and not auth.csrf_matches(session, request.headers.get("x-csrf-token")):
            raise HTTPException(403, "invalid CSRF token")
        return session

    def current_review() -> dict[str, Any]:
        return reviews.load(AMAZON_ITEM_KEY) or empty_review(
            AMAZON_ITEM_KEY, item["artifact_fingerprint"]
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "profile": profile, "benchmark": "amazon"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "home.html",
            _template_context(
                request,
                index=index,
                auth=auth,
                item=item,
                leaderboard=index.leaderboard[:5],
                featured_evidence=_featured_evidence(index),
            ),
        )

    @app.get("/benchmark/amazon", response_class=HTMLResponse)
    async def amazon_benchmark(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "benchmark.html",
            _template_context(request, index=index, auth=auth, item=item),
        )

    @app.get("/leaderboard", response_class=HTMLResponse)
    async def leaderboard(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "leaderboard.html",
            _template_context(
                request, index=index, auth=auth, item=item, leaderboard=index.leaderboard
            ),
        )

    @app.get("/evidence", response_class=HTMLResponse)
    async def evidence_page(
        request: Request,
        gate: str | None = None,
        evidence_type: str | None = Query(default=None, alias="type"),
        viewport: str | None = None,
        page: int = Query(default=1, ge=1),
    ) -> Response:
        gate_number = int(gate) if gate in {"2", "3", "4"} else None
        selected_type = evidence_type if evidence_type in EVIDENCE_TYPES else None
        selected_viewport = viewport if viewport in EVIDENCE_VIEWPORTS else None
        filtered = index.evidence_registry.filter(
            gate=gate_number, kind=selected_type, viewport=selected_viewport
        )
        per_page = 24
        page_count = max(1, math.ceil(len(filtered) / per_page))
        current_page = min(page, page_count)
        start = (current_page - 1) * per_page

        def page_url(number: int) -> str:
            parameters: dict[str, Any] = {"page": number}
            if gate_number:
                parameters["gate"] = gate_number
            if selected_type:
                parameters["type"] = selected_type
            if selected_viewport:
                parameters["viewport"] = selected_viewport
            return f"/evidence?{urlencode(parameters)}"

        return templates.TemplateResponse(
            request,
            "evidence.html",
            _template_context(
                request,
                index=index,
                auth=auth,
                item=item,
                evidence_rows=filtered[start : start + per_page],
                evidence_total=len(filtered),
                selected_gate=str(gate_number) if gate_number else "",
                selected_type=selected_type or "",
                selected_viewport=selected_viewport or "",
                current_page=current_page,
                page_count=page_count,
                previous_url=page_url(current_page - 1) if current_page > 1 else None,
                next_url=page_url(current_page + 1) if current_page < page_count else None,
                page_urls=[(number, page_url(number)) for number in range(1, page_count + 1)],
            ),
        )

    @app.get("/methodology", response_class=HTMLResponse)
    async def methodology(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "methodology.html",
            _template_context(request, index=index, auth=auth, item=item),
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def public_run_detail(request: Request, run_id: str) -> Response:
        run = index.public_run_by_id(run_id)
        if run is None:
            raise HTTPException(404, "published Amazon run not found")
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            _template_context(
                request, index=index, auth=auth, item=item, run=run, internal_run=False
            ),
        )

    @app.get("/evidence/{evidence_id:path}")
    async def public_evidence(evidence_id: str) -> Response:
        try:
            path = index.evidence_registry.resolve(evidence_id)
        except FileNotFoundError:
            raise HTTPException(404, "evidence not registered") from None
        return FileResponse(path)

    if profile == "internal":

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
                    error=False,
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
            next_path: str = Form("/admin"),
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
                        error=True,
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
            response = RedirectResponse("/", status_code=303)
            response.delete_cookie(SESSION_COOKIE, path="/")
            return response

        @app.get("/admin", response_class=HTMLResponse)
        async def admin(request: Request) -> Response:
            session = require_page(request)
            if isinstance(session, RedirectResponse):
                return session
            reports = [
                {
                    "id": identifier,
                    "label": identifier.replace("-", " ").title(),
                    "label_zh": REPORT_LABELS_ZH.get(identifier, identifier),
                    "url": f"/admin/reports/{identifier}",
                }
                for identifier in item["internal"]["report_files"]
            ]
            return templates.TemplateResponse(
                request,
                "admin.html",
                _template_context(
                    request,
                    index=index,
                    auth=auth,
                    item=item,
                    review=current_review(),
                    evidence_rows=_featured_evidence(index),
                    reports=reports,
                    runs=index.runs,
                    invalid_runs=index.invalid_runs,
                    gateway_allowed=clone_manager.is_allowed(AMAZON_ITEM_KEY),
                ),
            )

        @app.get("/admin/runs/{run_id}", response_class=HTMLResponse)
        async def admin_run_detail(request: Request, run_id: str) -> Response:
            session = require_page(request)
            if isinstance(session, RedirectResponse):
                return session
            run = index.run_by_id(run_id)
            if run is None:
                raise HTTPException(404, "valid Amazon run not found")
            return templates.TemplateResponse(
                request,
                "run_detail.html",
                _template_context(
                    request, index=index, auth=auth, item=item, run=run, internal_run=True
                ),
            )

        @app.get("/admin/reports/{report_id}")
        async def raw_report(request: Request, report_id: str) -> Response:
            require_api(request)
            relative = item["internal"]["report_files"].get(report_id)
            if not relative:
                raise HTTPException(404, "report not registered")
            path = root / relative
            resolved = path.resolve()
            if (
                not path.is_file()
                or path.is_symlink()
                or (resolved != root and root not in resolved.parents)
            ):
                raise HTTPException(404, "report not found")
            media_type = "application/json" if path.suffix == ".json" else "text/markdown"
            return Response(path.read_text(encoding="utf-8"), media_type=media_type)

        @app.get("/api/reviews/export")
        async def reviews_export(request: Request) -> Response:
            require_api(request)
            bundle = reviews.export()
            return Response(
                json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
                media_type="application/json",
                headers={
                    "Content-Disposition": "attachment; filename=websitebench-amazon-review.json"
                },
            )

        @app.get("/api/reviews/{item_key}")
        async def review_get(request: Request, item_key: str) -> dict[str, Any]:
            require_api(request)
            if item_key != AMAZON_ITEM_KEY:
                raise HTTPException(404, "benchmark item not found")
            return current_review()

        @app.put("/api/reviews/{item_key}")
        async def review_put(request: Request, item_key: str) -> Response:
            session = require_api(request, csrf=True)
            if item_key != AMAZON_ITEM_KEY:
                raise HTTPException(404, "benchmark item not found")
            try:
                body = await request.json()
            except json.JSONDecodeError as exc:
                raise HTTPException(400, "request body must be JSON") from exc
            if body.get("artifact_fingerprint") not in {None, item["artifact_fingerprint"]}:
                return JSONResponse(
                    {"error": "artifact fingerprint changed; reload before reviewing"},
                    status_code=409,
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
            try:
                bundle = await request.json()
                imported = reviews.import_batch(bundle)
            except ReviewConflict as exc:
                return JSONResponse(
                    {"error": str(exc), "current_revision": exc.current}, status_code=409
                )
            except (ReviewError, AttributeError, json.JSONDecodeError) as exc:
                raise HTTPException(422, str(exc)) from exc
            return JSONResponse({"imported": len(imported)})

        async def proxy_clone(
            request: Request, item_key: str, clone_path: str = ""
        ) -> Response:
            session = require_api(request)
            if item_key != AMAZON_ITEM_KEY:
                raise HTTPException(404, "clone not available")
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
                if key.lower()
                not in HOP_BY_HOP_HEADERS
                | {
                    "host",
                    "content-length",
                    "accept-encoding",
                    "cookie",
                    "x-csrf-token",
                    "origin",
                    "referer",
                }
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
                | {
                    "content-length",
                    "content-encoding",
                    "content-security-policy",
                    "x-frame-options",
                    "set-cookie",
                    "location",
                }
            }
            if backend.headers.get("location"):
                response_headers["location"] = rewrite_location(
                    backend.headers["location"], item_key
                )
            response = Response(
                body, status_code=backend.status_code, headers=response_headers
            )
            response.headers["x-clawbench-script-nonce"] = nonce
            for cookie in backend.headers.get_list("set-cookie"):
                response.headers.append("set-cookie", rewrite_set_cookie(cookie, item_key))
            return response

        app.add_api_route(
            "/clone/{item_key}",
            proxy_clone,
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        )
        app.add_api_route(
            "/clone/{item_key}/{clone_path:path}",
            proxy_clone,
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        )

    return app
