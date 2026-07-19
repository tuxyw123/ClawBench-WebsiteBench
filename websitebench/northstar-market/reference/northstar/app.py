"""Public storefront and private benchmark administration applications."""

from __future__ import annotations

import hmac
import html
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jsonschema import Draft202012Validator, FormatChecker

from .database import Database, format_utc
from .mail import DeliverMail, http_mailer
from .security import safe_next_path
from .store import DomainError, Store


REFERENCE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def rating(value: int) -> str:
    return f"{value / 100:.1f}"


TEMPLATES.env.filters["money"] = money
TEMPLATES.env.filters["rating"] = rating
TEMPLATES.env.filters["utc"] = lambda value: format_utc(value).replace("T", " ").replace("Z", " UTC")


@dataclass
class Runtime:
    db: Database
    store: Store
    deliver_mail: DeliverMail
    fixture_dir: Path
    fixture_schema: dict[str, Any]
    admin_token: str
    public_site_url: str


async def _discard_mail(_to: str, _subject: str, _text: str) -> None:
    return None


def build_runtime(
    *,
    database_path: Path | str | None = None,
    fixture_dir: Path | str | None = None,
    fixture_schema_path: Path | str | None = None,
    admin_token: str | None = None,
    public_site_url: str | None = None,
    deliver_mail: DeliverMail | None = None,
    initial_fixture: Path | str | None = None,
) -> Runtime:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    database = Database(database_path or data_dir / "northstar.sqlite3")
    fixtures = Path(fixture_dir or os.environ.get("BENCH_FIXTURE_DIR", "/bench-fixtures")).resolve()
    schema_path = Path(
        fixture_schema_path
        or os.environ.get("FIXTURE_SCHEMA_PATH", "/bench-schemas/fixture.schema.json")
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    if deliver_mail is None:
        mailbox_url = os.environ.get("MAILBOX_API_URL", "")
        mailbox_token = os.environ.get("MAILBOX_DELIVERY_TOKEN", "")
        deliver_mail = http_mailer(mailbox_url, mailbox_token) if mailbox_url else _discard_mail
    runtime = Runtime(
        db=database,
        store=Store(database),
        deliver_mail=deliver_mail,
        fixture_dir=fixtures,
        fixture_schema=schema,
        admin_token=admin_token or os.environ.get("BENCH_ADMIN_TOKEN", "development-only-token"),
        public_site_url=(public_site_url or os.environ.get("PUBLIC_SITE_URL", "")).rstrip("/"),
    )
    try:
        database.now()
    except RuntimeError:
        fixture_path = Path(initial_fixture or os.environ.get("INITIAL_FIXTURE", fixtures / "1101.json"))
        fixture = _load_fixture(runtime, fixture_path)
        database.reset(
            fixture,
            run_id="northstar-bootstrap",
            seed=fixture["seed"],
            now=fixture["now"],
        )
    return runtime


def _load_fixture(runtime: Runtime, path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(runtime.fixture_dir)
    except ValueError as exc:
        raise ValueError("fixture path escapes BENCH_FIXTURE_DIR") from exc
    fixture = json.loads(resolved.read_text(encoding="utf-8"))
    Draft202012Validator(
        runtime.fixture_schema, format_checker=FormatChecker()
    ).validate(fixture)
    return fixture


def _site_url(runtime: Runtime, request: Request) -> str:
    return runtime.public_site_url or str(request.base_url).rstrip("/")


def _render(
    runtime: Runtime,
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    user = getattr(request.state, "user", None)
    cart = runtime.store.cart(
        user=user, device_key=request.state.device_key
    )
    base = {
        "request": request,
        "user": user,
        "cart_count": cart["count"],
        "categories": runtime.store.categories(),
        "current_path": request.url.path,
    }
    if context:
        base.update(context)
    return TEMPLATES.TemplateResponse(
        request=request,
        name=template,
        context=base,
        status_code=status_code,
    )


def _require_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401)
    return user


def create_public_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="Northstar Market", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")

    @app.middleware("http")
    async def identity_middleware(request: Request, call_next: Any) -> Response:
        device_key = request.cookies.get("northstar_device", "")
        if not device_key or len(device_key) > 128:
            device_key = secrets.token_urlsafe(24)
        request.state.device_key = device_key
        request.state.user = runtime.store.user_for_session(request.cookies.get("northstar_session"))
        response = await call_next(request)
        if request.cookies.get("northstar_device") != device_key:
            response.set_cookie(
                "northstar_device",
                device_key,
                max_age=31_536_000,
                httponly=True,
                samesite="lax",
            )
        return response

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        runtime.db.now()
        return {"status": "ok"}

    @app.api_route("/__bench/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def hide_admin(rest: str) -> None:
        del rest
        raise HTTPException(status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return _render(
            runtime,
            request,
            "home.html",
            {"featured": runtime.store.featured_products(), "page_title": "Find your true north"},
        )

    @app.get("/search", response_class=HTMLResponse)
    async def search(
        request: Request,
        q: str = "",
        category: str = "",
        sort: str = "featured",
        page: int = 1,
    ) -> HTMLResponse:
        result = runtime.store.search(query=q, category=category, sort=sort, page=page)
        return _render(runtime, request, "search.html", {**result, "page_title": "Shop"})

    @app.get("/products/{slug}", response_class=HTMLResponse)
    async def product_detail(request: Request, slug: str) -> HTMLResponse:
        product = runtime.store.product_by_slug(slug)
        if not product:
            return _render(runtime, request, "not-found.html", {"page_title": "Not found"}, status_code=404)
        return _render(runtime, request, "product.html", {"product": product, "page_title": product["title"]})

    @app.get("/media/{key}.svg")
    async def media(key: str) -> Response:
        image = runtime.store.image_by_key(key)
        if not image:
            raise HTTPException(status_code=404)
        label = html.escape(image["label"])
        background = image["background"]
        accent = image["accent"]
        initials = "".join(word[0] for word in image["label"].split()[:2]).upper()
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 640" role="img" aria-label="{label}">
<rect width="800" height="640" rx="32" fill="{background}"/>
<circle cx="400" cy="290" r="178" fill="{accent}" opacity=".12"/>
<path d="M250 390 Q400 145 550 390 Z" fill="none" stroke="{accent}" stroke-width="22" stroke-linecap="round"/>
<circle cx="400" cy="285" r="68" fill="{accent}"/>
<text x="400" y="306" text-anchor="middle" font-family="Arial,sans-serif" font-size="54" font-weight="700" fill="white">{initials}</text>
<text x="400" y="535" text-anchor="middle" font-family="Arial,sans-serif" font-size="28" fill="{accent}">{label}</text>
</svg>"""
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/cart/add", response_class=HTMLResponse)
    async def add_cart(
        request: Request,
        product_id: str = Form(...),
        quantity: int = Form(1),
        return_to: str = Form("/cart"),
    ) -> Response:
        try:
            runtime.store.add_to_cart(
                product_id=product_id,
                quantity=quantity,
                user=request.state.user,
                device_key=request.state.device_key,
            )
        except DomainError as error:
            product = runtime.store.product_by_id(product_id)
            if product and return_to.startswith("/products/"):
                return _render(
                    runtime,
                    request,
                    "product.html",
                    {"product": product, "error": error.message, "page_title": product["title"]},
                    status_code=error.status,
                )
            return RedirectResponse(f"/cart?error={quote(error.message)}", status_code=303)
        return RedirectResponse(safe_next_path(return_to, "/cart"), status_code=303)

    @app.get("/cart", response_class=HTMLResponse)
    async def cart_page(request: Request, error: str = "", notice: str = "") -> HTMLResponse:
        cart = runtime.store.cart(user=request.state.user, device_key=request.state.device_key)
        return _render(
            runtime,
            request,
            "cart.html",
            {"cart": cart, "error": error, "notice": notice, "page_title": "Your cart"},
        )

    @app.post("/cart/update")
    async def cart_update(
        request: Request,
        product_id: str = Form(...),
        quantity: int = Form(...),
    ) -> Response:
        try:
            runtime.store.update_cart(
                product_id=product_id,
                quantity=quantity,
                user=request.state.user,
                device_key=request.state.device_key,
            )
        except DomainError as error:
            return RedirectResponse(f"/cart?error={quote(error.message)}", status_code=303)
        message = "Item removed." if quantity <= 0 else "Cart updated."
        return RedirectResponse(f"/cart?notice={quote(message)}", status_code=303)

    @app.get("/register", response_class=HTMLResponse)
    async def register_page(request: Request) -> HTMLResponse:
        return _render(runtime, request, "register.html", {"page_title": "Create account"})

    @app.post("/register", response_class=HTMLResponse)
    async def register_submit(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        confirm_password: str = Form(""),
    ) -> HTMLResponse:
        try:
            issued = runtime.store.register(
                email=email,
                password=password,
                confirm_password=confirm_password,
                device_key=request.state.device_key,
            )
            if issued:
                recipient, token = issued
                link = f"{_site_url(runtime, request)}/verify?token={quote(token)}"
                await runtime.deliver_mail(
                    recipient,
                    "Verify your Northstar Market email",
                    f"Welcome to Northstar Market. Open the link to verify your account: {link}",
                )
        except DomainError as error:
            return _render(
                runtime,
                request,
                "register.html",
                {
                    "page_title": "Create account",
                    "error": error.message,
                    "field_errors": error.fields,
                    "email": email,
                },
                status_code=error.status,
            )
        return _render(
            runtime,
            request,
            "message.html",
            {
                "page_title": "Check your email",
                "heading": "Check your email",
                "message": "If the address can be registered, a verification link has been sent to the local test mailbox.",
                "action_href": "/login",
                "action_label": "Go to sign in",
            },
        )

    @app.get("/verify", response_class=HTMLResponse)
    async def verify(request: Request, token: str = "") -> HTMLResponse:
        try:
            runtime.store.verify_email(token)
        except DomainError as error:
            return _render(
                runtime,
                request,
                "message.html",
                {
                    "page_title": "Verification unavailable",
                    "heading": "Verification unavailable",
                    "error": error.message,
                    "action_href": "/register",
                    "action_label": "Register again",
                },
                status_code=400,
            )
        return _render(
            runtime,
            request,
            "message.html",
            {
                "page_title": "Email verified",
                "heading": "Your email is verified",
                "message": "You can now sign in to Northstar Market.",
                "action_href": "/login",
                "action_label": "Sign in",
            },
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/") -> HTMLResponse:
        return _render(
            runtime,
            request,
            "login.html",
            {"page_title": "Sign in", "next": safe_next_path(next)},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        next: str = Form("/"),
    ) -> Response:
        try:
            session, _user = runtime.store.login(
                email=email, password=password, device_key=request.state.device_key
            )
        except DomainError as error:
            return _render(
                runtime,
                request,
                "login.html",
                {
                    "page_title": "Sign in",
                    "error": error.message,
                    "email": email,
                    "next": safe_next_path(next),
                    "unverified": error.code == "unverified_login",
                },
                status_code=error.status,
            )
        response = RedirectResponse(safe_next_path(next), status_code=303)
        response.set_cookie(
            "northstar_session", session, max_age=86400, httponly=True, samesite="lax"
        )
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        runtime.store.logout(request.cookies.get("northstar_session"))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("northstar_session")
        return response

    @app.get("/forgot-password", response_class=HTMLResponse)
    async def forgot_page(request: Request) -> HTMLResponse:
        return _render(runtime, request, "forgot.html", {"page_title": "Reset password"})

    @app.post("/forgot-password", response_class=HTMLResponse)
    async def forgot_submit(request: Request, email: str = Form("")) -> HTMLResponse:
        issued = runtime.store.forgot_password(email)
        if issued:
            recipient, token = issued
            link = f"{_site_url(runtime, request)}/reset-password?token={quote(token)}"
            await runtime.deliver_mail(
                recipient,
                "Reset your Northstar Market password",
                f"Open the link to reset your password: {link}",
            )
        return _render(
            runtime,
            request,
            "message.html",
            {
                "page_title": "Check your email",
                "heading": "Check your email",
                "message": "If an account exists, a reset link has been sent.",
                "action_href": "/login",
                "action_label": "Back to sign in",
            },
        )

    @app.get("/reset-password", response_class=HTMLResponse)
    async def reset_page(request: Request, token: str = "") -> HTMLResponse:
        return _render(
            runtime,
            request,
            "reset.html",
            {"page_title": "Choose a new password", "token": token},
        )

    @app.post("/reset-password", response_class=HTMLResponse)
    async def reset_submit(
        request: Request,
        token: str = Form(""),
        password: str = Form(""),
        confirm_password: str = Form(""),
    ) -> HTMLResponse:
        try:
            runtime.store.reset_password(
                token=token, password=password, confirm_password=confirm_password
            )
        except DomainError as error:
            return _render(
                runtime,
                request,
                "reset.html",
                {
                    "page_title": "Choose a new password",
                    "token": token,
                    "error": error.message,
                    "field_errors": error.fields,
                },
                status_code=error.status,
            )
        return _render(
            runtime,
            request,
            "message.html",
            {
                "page_title": "Password changed",
                "heading": "Password changed",
                "message": "Your password has been updated. Sign in again on every device.",
                "action_href": "/login",
                "action_label": "Sign in",
            },
        )

    @app.get("/checkout", response_class=HTMLResponse)
    async def checkout_page(request: Request) -> Response:
        if not request.state.user:
            return RedirectResponse("/login?next=/checkout", status_code=303)
        cart = runtime.store.cart(user=request.state.user, device_key=request.state.device_key)
        if not cart["lines"]:
            return RedirectResponse("/cart?error=Your%20cart%20is%20empty.", status_code=303)
        return _render(
            runtime,
            request,
            "checkout.html",
            {
                "page_title": "Checkout",
                "cart": cart,
                "idempotency_key": secrets.token_urlsafe(18),
                "values": {"shipping_method": "standard"},
            },
        )

    @app.post("/checkout", response_class=HTMLResponse)
    async def checkout_submit(
        request: Request,
        idempotency_key: str = Form(""),
        full_name: str = Form(""),
        line1: str = Form(""),
        line2: str = Form(""),
        city: str = Form(""),
        state: str = Form(""),
        zip_code: str = Form(""),
        shipping_method: str = Form("standard"),
        card_number: str = Form(""),
        expiration: str = Form(""),
        cvv: str = Form(""),
    ) -> Response:
        if not request.state.user:
            return RedirectResponse("/login?next=/checkout", status_code=303)
        address = {
            "full_name": full_name.strip(),
            "line1": line1.strip(),
            "line2": line2.strip(),
            "city": city.strip(),
            "state": state.strip(),
            "zip_code": zip_code.strip(),
        }
        values = {**address, "shipping_method": shipping_method, "expiration": expiration}
        try:
            order = runtime.store.checkout(
                user=request.state.user,
                idempotency_key=idempotency_key,
                shipping_method=shipping_method,
                address=address,
                card_number=card_number,
                expiration=expiration,
                cvv=cvv,
            )
        except DomainError as error:
            cart = runtime.store.cart(user=request.state.user, device_key=request.state.device_key)
            return _render(
                runtime,
                request,
                "checkout.html",
                {
                    "page_title": "Checkout",
                    "cart": cart,
                    "idempotency_key": idempotency_key,
                    "error": error.message,
                    "field_errors": error.fields,
                    "values": values,
                    "shortages": error.details.get("products", []),
                },
                status_code=error.status,
            )
        return RedirectResponse(f"/checkout/success/{order['order_number']}", status_code=303)

    @app.get("/checkout/success/{order_number}", response_class=HTMLResponse)
    async def checkout_success(request: Request, order_number: str) -> Response:
        if not request.state.user:
            return RedirectResponse(f"/login?next=/checkout/success/{quote(order_number)}", status_code=303)
        try:
            order = runtime.store.order_for_user(
                user_id=request.state.user["id"], order_number=order_number
            )
        except DomainError:
            return _render(runtime, request, "not-found.html", {"page_title": "Not found"}, status_code=404)
        return _render(
            runtime,
            request,
            "order-success.html",
            {"page_title": "Order placed", "order": order},
        )

    @app.get("/account/orders", response_class=HTMLResponse)
    async def orders_page(request: Request) -> Response:
        if not request.state.user:
            return RedirectResponse("/login?next=/account/orders", status_code=303)
        return _render(
            runtime,
            request,
            "orders.html",
            {
                "page_title": "Your orders",
                "orders": runtime.store.orders_for_user(request.state.user["id"]),
            },
        )

    @app.get("/account/orders/{order_number}", response_class=HTMLResponse)
    async def order_page(request: Request, order_number: str, error: str = "") -> Response:
        if not request.state.user:
            return RedirectResponse(f"/login?next=/account/orders/{quote(order_number)}", status_code=303)
        try:
            order = runtime.store.order_for_user(
                user_id=request.state.user["id"], order_number=order_number
            )
        except DomainError:
            return _render(runtime, request, "not-found.html", {"page_title": "Not found"}, status_code=404)
        now = runtime.db.now()
        return _render(
            runtime,
            request,
            "order-detail.html",
            {
                "page_title": f"Order {order_number}",
                "order": order,
                "can_cancel": order["status"] == "placed" and now <= order["placed_at"] + 1800,
                "error": error,
            },
        )

    @app.post("/account/orders/{order_number}/cancel")
    async def cancel_order(request: Request, order_number: str) -> Response:
        if not request.state.user:
            return RedirectResponse(f"/login?next=/account/orders/{quote(order_number)}", status_code=303)
        try:
            runtime.store.cancel_order(
                user_id=request.state.user["id"], order_number=order_number
            )
        except DomainError as error:
            if error.status == 404:
                return _render(runtime, request, "not-found.html", {"page_title": "Not found"}, status_code=404)
            return RedirectResponse(
                f"/account/orders/{quote(order_number)}?error={quote(error.message)}", status_code=303
            )
        return RedirectResponse(f"/account/orders/{quote(order_number)}", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_exception(request: Request, error: HTTPException) -> Response:
        if error.status_code == 401:
            return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
        if error.status_code == 404:
            return _render(runtime, request, "not-found.html", {"page_title": "Not found"}, status_code=404)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    return app


def create_admin_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="Northstar benchmark admin", docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(value: str | None) -> None:
        if not value or not hmac.compare_digest(value, runtime.admin_token):
            raise HTTPException(status_code=404)

    @app.get("/__bench/health")
    async def admin_health(x_bench_admin_token: str | None = Header(None)) -> dict[str, Any]:
        authorized(x_bench_admin_token)
        runtime.db.now()
        return {"schema_version": 1, "status": "ok"}

    @app.post("/__bench/reset")
    async def admin_reset(
        request: Request, x_bench_admin_token: str | None = Header(None)
    ) -> dict[str, Any]:
        authorized(x_bench_admin_token)
        body = await request.json()
        required = {"schema_version", "run_id", "seed", "now", "fixture_path"}
        if set(body) != required or body.get("schema_version") != 1:
            raise HTTPException(status_code=422, detail="invalid reset payload")
        try:
            fixture = _load_fixture(runtime, Path(body["fixture_path"]))
            runtime.db.reset(
                fixture,
                run_id=body["run_id"],
                seed=body["seed"],
                now=body["now"],
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "schema_version": 1,
            "status": "reset",
            "run_id": body["run_id"],
            "seed": body["seed"],
            "now": body["now"],
        }

    @app.post("/__bench/clock/advance")
    async def admin_clock(
        request: Request, x_bench_admin_token: str | None = Header(None)
    ) -> dict[str, Any]:
        authorized(x_bench_admin_token)
        body = await request.json()
        if set(body) != {"seconds"} or not isinstance(body["seconds"], int):
            raise HTTPException(status_code=422, detail="invalid clock payload")
        try:
            now = runtime.db.advance_clock(body["seconds"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"schema_version": 1, "now": format_utc(now)}

    @app.get("/__bench/state")
    async def admin_state(x_bench_admin_token: str | None = Header(None)) -> dict[str, Any]:
        authorized(x_bench_admin_token)
        return runtime.db.normalized_state()

    return app
