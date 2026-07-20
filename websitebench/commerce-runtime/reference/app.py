"""Browser-observable persistent reference app for compiled commerce variants."""

from __future__ import annotations

import html
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from clawbench.web2code.commerce_runtime import DomainError, PersistentCommerce


def _json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


spec = yaml.safe_load(Path(os.environ["VARIANT_SPEC"]).read_text(encoding="utf-8"))
initial_fixture = _json(os.environ["INITIAL_FIXTURE"])
store = PersistentCommerce(
    Path(os.environ.get("DATA_DIR", "/data")) / "commerce-state.json",
    spec=spec,
    initial_fixture=initial_fixture,
)
app = FastAPI(title=spec["display_name"], docs_url=None, redoc_url=None, openapi_url=None)
admin = FastAPI(title="WebsiteBench controlled admin", docs_url=None, redoc_url=None, openapi_url=None)


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def rules() -> str:
    labels = {
        "quantity": "Quantity",
        "pricing": "Pricing and tax",
        "inventory": "Inventory",
        "fulfillment": "Fulfillment",
        "cancellation": "Cancellation",
        "token_lifetime": "Account tokens",
    }
    rows = []
    for module in ("quantity", "pricing", "inventory", "fulfillment", "cancellation", "token_lifetime"):
        policy = spec["policies"][module]
        parameters = ", ".join(
            f"{key.replace('_', ' ')}: {json.dumps(value, sort_keys=True)}"
            for key, value in sorted(policy["parameters"].items())
        )
        rows.append(
            f"<li data-policy='{module}'><strong>{labels[module]}</strong>: "
            f"{policy['kind'].replace('_', ' ')} — {html.escape(parameters)}</li>"
        )
    return "".join(rows)


def page(request: Request, title: str, body: str, *, status: int = 200) -> HTMLResponse:
    user = getattr(request.state, "user", None)
    identity = (
        f"<span>Signed in as {html.escape(user['email'])}</span>"
        "<form action='/logout' method='post'><button>Sign out</button></form>"
        if user
        else "<a href='/login'>Sign in</a> <a href='/register'>Create account</a>"
    )
    document = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{html.escape(title)} — {html.escape(spec['display_name'])}</title>
<style>body{{font:16px system-ui;margin:auto;max-width:1100px;padding:1rem;color:#17212b}}header,nav{{display:flex;gap:1rem;align-items:center;flex-wrap:wrap}}header{{justify-content:space-between;border-bottom:1px solid #ccd5df}}main{{padding:1rem 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1rem}}article,section{{border:1px solid #ccd5df;border-radius:.5rem;padding:1rem;margin:.75rem 0}}label{{display:block;margin:.6rem 0}}input,select,button{{font:inherit;padding:.45rem}}.error{{background:#fee;color:#8b1111;padding:.7rem}}table{{border-collapse:collapse;width:100%}}td,th{{padding:.5rem;border-bottom:1px solid #ddd;text-align:left}}</style></head>
<body><header><h1><a href='/'>{html.escape(spec['display_name'])}</a></h1><nav><a href='/search'>Search</a><a href='/cart'>Cart</a><a href='/account/orders'>Orders</a>{identity}</nav></header>
<main>{body}</main><footer><details><summary>Observable purchase rules</summary><ul>{rules()}</ul></details></footer></body></html>"""
    return HTMLResponse(document, status_code=status)


def error_block(error: DomainError | None) -> str:
    return f"<p class='error' role='alert'>{html.escape(error.message)}</p>" if error else ""


@app.middleware("http")
async def identity(request: Request, call_next) -> Response:
    device = request.cookies.get("wb_device")
    if not device or len(device) > 128:
        device = secrets.token_urlsafe(18)
    request.state.device = device
    request.state.user = store.user_for_session(request.cookies.get("wb_session"))
    response = await call_next(request)
    if request.cookies.get("wb_device") != device:
        response.set_cookie("wb_device", device, max_age=31_536_000, httponly=True, samesite="lax")
    return response


@app.get("/healthz")
async def health() -> dict[str, object]:
    return {"status": "ok", "variant_id": spec["variant_id"], "seed": store.data["seed"]}


@app.api_route("/__bench/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def hide_admin(path: str) -> None:
    del path
    raise HTTPException(404)


def product_cards(products: list[dict]) -> str:
    return "".join(
        f"<article><h3><a href='/products/{item['slug']}'>{html.escape(item['title'])}</a></h3>"
        f"<p>{html.escape(item['brand'])}</p><p>{money(item['price_cents'])}</p>"
        f"<p>{item['inventory']} available</p></article>"
        for item in products
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return page(
        request,
        "Catalog",
        f"<section><h2>Purchase rules</h2><ul>{rules()}</ul></section>"
        f"<h2>Catalog</h2><div class='grid'>{product_cards(store.products()[:12])}</div>",
    )


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "") -> HTMLResponse:
    products = store.products(query=q)
    return page(
        request,
        "Search",
        f"<h2>Search products</h2><form><label>Search <input name='q' value='{html.escape(q)}'></label><button>Search</button></form>"
        f"<p>{len(products)} results</p><div class='grid'>{product_cards(products)}</div>",
    )


@app.get("/products/{slug}", response_class=HTMLResponse)
async def product(request: Request, slug: str) -> HTMLResponse:
    item = store.product_by_slug(slug)
    if not item:
        return page(request, "Not found", "<h2>Product not found</h2>", status=404)
    quantity_policy = spec["policies"]["quantity"]
    values: list[int]
    if quantity_policy["kind"] == "wholesale_case":
        parameters = quantity_policy["parameters"]
        values = list(range(parameters["minimum"], parameters["maximum"] + 1, parameters["case_size"]))
    else:
        maximum = int(quantity_policy["parameters"].get("maximum", quantity_policy["parameters"].get("limit", 12)))
        values = list(range(1, maximum + 1))
    options = "".join(f"<option value='{value}'>{value}</option>" for value in values)
    body = f"""<article><h2>{html.escape(item['title'])}</h2><p>{html.escape(item['description'])}</p>
<p><strong>{money(item['price_cents'])}</strong> · {item['inventory']} available</p>
<form action='/cart/add' method='post'><input type='hidden' name='product_id' value='{item['id']}'>
<input type='hidden' name='return_to' value='/cart'><label>Quantity <select name='quantity'>{options}</select></label>
<button>Add to cart</button></form></article><section><h2>Rules for this item</h2><ul>{rules()}</ul></section>"""
    return page(request, item["title"], body)


@app.post("/cart/add")
async def add_cart(
    request: Request,
    product_id: str = Form(...),
    quantity: int = Form(1),
    return_to: str = Form("/cart"),
) -> Response:
    try:
        store.add_to_cart(
            product_id=product_id,
            quantity=quantity,
            user=request.state.user,
            device=request.state.device,
        )
    except DomainError as error:
        return RedirectResponse(f"/cart?error={quote(error.message)}", status_code=303)
    target = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/cart"
    return RedirectResponse(target, status_code=303)


@app.get("/cart", response_class=HTMLResponse)
async def cart(request: Request, error: str = "") -> HTMLResponse:
    value = store.cart(user=request.state.user, device=request.state.device)
    rows = "".join(
        f"<tr><td>{html.escape(item['title'])}</td><td>{money(item['price_cents'])}</td><td>"
        f"<form action='/cart/update' method='post'><input type='hidden' name='product_id' value='{item['id']}'>"
        f"<label>Quantity <input type='number' name='quantity' min='0' value='{item['quantity']}'></label>"
        f"<button>Update cart</button></form></td><td>{money(item['line_total_cents'])}</td></tr>"
        for item in value["lines"]
    )
    summary = (
        f"<p>Subtotal: {money(value['subtotal_cents'])}; tax: {money(value['tax_cents'])}; "
        f"shipping: {money(value['shipping_cents'])}; total: {money(value['total_cents'])}</p>"
    )
    return page(
        request,
        "Cart",
        (f"<p class='error'>{html.escape(error)}</p>" if error else "")
        + "<h2>Your cart</h2><table><tr><th>Product</th><th>Price</th><th>Quantity</th><th>Total</th></tr>"
        + rows
        + "</table>"
        + summary
        + "<p><a href='/checkout'>Continue to checkout</a></p>",
    )


@app.post("/cart/update")
async def update_cart(
    request: Request,
    product_id: str = Form(...),
    quantity: int = Form(...),
) -> Response:
    try:
        store.update_cart(
            product_id=product_id,
            quantity=quantity,
            user=request.state.user,
            device=request.state.device,
        )
    except DomainError as error:
        return RedirectResponse(f"/cart?error={quote(error.message)}", status_code=303)
    return RedirectResponse("/cart", status_code=303)


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    return page(request, "Create account", """<h2>Create account</h2><form action='/register' method='post'>
<label>Email <input type='email' name='email'></label><label>Password <input type='password' name='password'></label>
<label>Confirm password <input type='password' name='confirm_password'></label><button>Create account</button></form>""")


async def send_mail(recipient: str, subject: str, text: str) -> None:
    endpoint = os.environ.get("MAILBOX_API_URL", "").rstrip("/")
    if not endpoint:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            endpoint + "/api/v1/messages",
            headers={"Authorization": f"Bearer {os.environ.get('MAILBOX_DELIVERY_TOKEN', '')}"},
            json={"schema_version": 1, "to": recipient, "subject": subject, "text": text},
        )
        response.raise_for_status()


@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
) -> HTMLResponse:
    try:
        token = store.register(email, password, confirm_password)
        base = os.environ.get("PUBLIC_SITE_URL", str(request.base_url)).rstrip("/")
        await send_mail(email, f"Verify your {spec['display_name']} email", f"Verify your account: {base}/verify?token={token}")
    except DomainError as error:
        return page(request, "Create account", error_block(error) + "<p><a href='/register'>Try again</a></p>", status=error.status)
    return page(request, "Check your email", "<h2>Check your email</h2><p>A verification link was sent to the local test mailbox.</p>")


@app.get("/verify", response_class=HTMLResponse)
async def verify(request: Request, token: str = "") -> HTMLResponse:
    try:
        store.verify(token)
    except DomainError as error:
        return page(request, "Verification unavailable", error_block(error), status=error.status)
    return page(request, "Email verified", "<h2>Email verified</h2><p><a href='/login'>Sign in</a></p>")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/") -> HTMLResponse:
    return page(request, "Sign in", f"""<h2>Sign in</h2><form action='/login' method='post'>
<input type='hidden' name='next' value='{html.escape(next)}'><label>Email <input type='email' name='email'></label>
<label>Password <input type='password' name='password'></label><button>Sign in</button></form>""")


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
) -> Response:
    try:
        token = store.login(email, password, device=request.state.device)
    except DomainError as error:
        return page(request, "Sign in", error_block(error) + "<p><a href='/login'>Try again</a></p>", status=error.status)
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie("wb_session", token, max_age=86400, httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout(request: Request) -> Response:
    store.logout(request.cookies.get("wb_session"))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("wb_session")
    return response


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_page(request: Request) -> HTMLResponse:
    return page(request, "Reset password", """<h2>Reset password</h2><form action='/forgot-password' method='post'>
<label>Email <input type='email' name='email'></label><button>Send reset link</button></form>""")


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_submit(request: Request, email: str = Form("")) -> HTMLResponse:
    token = store.forgot_password(email)
    if token:
        base = os.environ.get("PUBLIC_SITE_URL", str(request.base_url)).rstrip("/")
        await send_mail(email, f"Reset your {spec['display_name']} password", f"Reset your password: {base}/reset-password?token={token}")
    return page(request, "Check your email", "<h2>Check your email</h2><p>If an account exists, a reset link was sent.</p>")


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_page(request: Request, token: str = "") -> HTMLResponse:
    return page(request, "Choose password", f"""<h2>Choose a new password</h2><form action='/reset-password' method='post'>
<input type='hidden' name='token' value='{html.escape(token)}'><label>Password <input type='password' name='password'></label>
<label>Confirm password <input type='password' name='confirm_password'></label><button>Change password</button></form>""")


@app.post("/reset-password", response_class=HTMLResponse)
async def reset_submit(
    request: Request,
    token: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
) -> HTMLResponse:
    try:
        store.reset_password(token, password, confirm_password)
    except DomainError as error:
        return page(request, "Choose password", error_block(error), status=error.status)
    return page(request, "Password changed", "<h2>Password changed</h2><p><a href='/login'>Sign in</a></p>")


def checkout_form(value: dict) -> str:
    pickup = spec["policies"]["fulfillment"]["kind"] == "pickup_slots"
    pickup_fields = ""
    if pickup:
        stores = "".join(f"<option value='{name}'>{name.replace('-', ' ').title()}</option>" for name in sorted(store.data["store_stock"]))
        slots = "".join(f"<option value='{name}'>{name}: {slot['starts_at']} ({slot['capacity']} remaining)</option>" for name, slot in sorted(store.data["slots"].items()))
        pickup_fields = f"<label>Pickup store <select name='store'>{stores}</select></label><label>Pickup time slot <select name='slot'>{slots}</select></label>"
    address_fields = (
        "<label>Full name <input name='full_name'></label>"
        if pickup
        else "<label>Full name <input name='full_name'></label><label>Address line 1 <input name='line1'></label>"
        "<label>City <input name='city'></label><label>State <input name='state'></label>"
        "<label>ZIP code <input name='zip_code'></label>"
        "<label>Standard <input type='radio' name='shipping_method' value='standard' checked></label>"
    )
    return f"""<h2>Checkout</h2><p>Total: {money(value['total_cents'])}</p><form action='/checkout' method='post'>
<input type='hidden' name='idempotency_key' value='{secrets.token_urlsafe(16)}'>{pickup_fields}
{address_fields}
<label>Card number <input name='card_number'></label><label>Expiration <input name='expiration'></label><label>CVV <input name='cvv'></label>
<button>Place test order</button></form>"""


@app.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request) -> Response:
    if request.state.user is None:
        return RedirectResponse("/login?next=/checkout", status_code=303)
    value = store.cart(user=request.state.user, device=request.state.device)
    if not value["lines"]:
        return RedirectResponse("/cart?error=Your%20cart%20is%20empty.", status_code=303)
    return page(request, "Checkout", checkout_form(value))


@app.post("/checkout", response_class=HTMLResponse)
async def checkout_submit(
    request: Request,
    idempotency_key: str = Form(""),
    card_number: str = Form(""),
    store_name: str = Form("", alias="store"),
    slot: str = Form(""),
    full_name: str = Form(""),
    line1: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    zip_code: str = Form(""),
    shipping_method: str = Form("standard"),
    expiration: str = Form(""),
    cvv: str = Form(""),
) -> Response:
    del full_name, line1, city, state, zip_code, shipping_method, expiration, cvv
    try:
        order = store.checkout(
            user=request.state.user,
            device=request.state.device,
            idempotency_key=idempotency_key,
            card_number=card_number,
            store=store_name or None,
            slot=slot or None,
        )
    except DomainError as error:
        cart_value = store.cart(user=request.state.user, device=request.state.device)
        return page(request, "Checkout", error_block(error) + checkout_form(cart_value), status=error.status)
    return RedirectResponse(f"/checkout/success/{order['number']}", status_code=303)


@app.get("/checkout/success/{number}", response_class=HTMLResponse)
async def success(request: Request, number: str) -> HTMLResponse:
    if not request.state.user:
        return page(request, "Order not found", "<h2>Order not found</h2>", status=404)
    try:
        order = store.order_for(number, request.state.user["id"])
    except DomainError as error:
        return page(request, "Order not found", error_block(error), status=error.status)
    return page(request, "Order placed", f"<h2>Order placed</h2><p>Order number <strong>{html.escape(order['number'])}</strong></p><p><a href='/account/orders/{quote(order['number'])}'>View order</a></p>")


@app.get("/account/orders", response_class=HTMLResponse)
async def orders(request: Request) -> Response:
    if not request.state.user:
        return RedirectResponse("/login?next=/account/orders", status_code=303)
    values = store.orders_for(request.state.user["id"])
    body = "".join(f"<article><h3><a href='/account/orders/{quote(item['number'])}'>{html.escape(item['number'])}</a></h3><p>{item['status']} · {money(item['total_cents'])}</p></article>" for item in values)
    return page(request, "Orders", "<h2>Your orders</h2>" + (body or "<p>No orders yet.</p>"))


@app.get("/account/orders/{number}", response_class=HTMLResponse)
async def order_detail(request: Request, number: str) -> HTMLResponse:
    if not request.state.user:
        return page(request, "Order not found", "<h2>Order not found</h2>", status=404)
    try:
        order = store.order_for(number, request.state.user["id"])
    except DomainError as error:
        return page(request, "Order not found", error_block(error), status=error.status)
    cancel = f"<form action='/account/orders/{quote(number)}/cancel' method='post'><button>Cancel order</button></form>" if order["status"] == "placed" else ""
    return page(request, number, f"<h2>Order {html.escape(number)}</h2><p>Status: {order['status']}</p><p>Total: {money(order['total_cents'])}</p>{cancel}")


@app.post("/account/orders/{number}/cancel", response_class=HTMLResponse)
async def cancel_order(request: Request, number: str) -> Response:
    if not request.state.user:
        return RedirectResponse("/login", status_code=303)
    try:
        store.cancel(number, request.state.user["id"])
    except DomainError as error:
        return page(request, "Cancellation unavailable", error_block(error), status=error.status)
    return RedirectResponse(f"/account/orders/{quote(number)}", status_code=303)


def require_admin(token: str | None) -> None:
    expected = os.environ.get("BENCH_ADMIN_TOKEN", "development-only-token")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(401, "invalid benchmark admin token")


@admin.get("/healthz")
async def admin_health() -> dict[str, str]:
    return {"status": "ok"}


@admin.post("/__bench/reset")
async def admin_reset(
    payload: dict,
    token: str | None = Header(None, alias="X-Bench-Admin-Token"),
) -> dict[str, object]:
    require_admin(token)
    path = Path(str(payload["fixture_path"])).resolve(strict=True)
    roots = [Path(os.environ.get("BENCH_FIXTURE_DIR", "/bench-fixtures")).resolve()]
    public_root = Path(
        os.environ.get("BENCH_PUBLIC_FIXTURE_DIR", "/bench-public-fixtures")
    ).resolve()
    if public_root.exists():
        roots.append(public_root)
    if not any(path == root or root in path.parents for root in roots):
        raise HTTPException(400, "fixture path escapes fixture roots")
    fixture = _json(path)
    if int(payload["seed"]) != int(fixture["seed"]):
        raise HTTPException(400, "fixture seed mismatch")
    state = store.reset(fixture, run_id=str(payload.get("run_id", "judge")))
    return {"ok": True, "seed": state["seed"], "now": state["now"]}


@admin.get("/__bench/state")
async def admin_state(
    token: str | None = Header(None, alias="X-Bench-Admin-Token"),
) -> dict[str, object]:
    require_admin(token)
    return store.normalized_state()


@admin.post("/__bench/clock/advance")
async def admin_advance(
    payload: dict,
    token: str | None = Header(None, alias="X-Bench-Admin-Token"),
) -> dict[str, object]:
    require_admin(token)
    return store.advance(int(payload["seconds"]))


if __name__ == "__main__":
    thread = threading.Thread(
        target=lambda: uvicorn.run(
            admin,
            host="0.0.0.0",
            port=int(os.environ.get("BENCH_ADMIN_PORT", "8081")),
        ),
        daemon=True,
    )
    thread.start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
