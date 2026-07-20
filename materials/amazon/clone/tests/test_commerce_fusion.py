from __future__ import annotations

import importlib.util
import re
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


CLONE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = CLONE_ROOT / "server.py"
FASTAPI_PATH = CLONE_ROOT / "fastapi_app.py"
TARGET_ASIN = "B0874XN4D8"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


legacy = load_module("amazon_fusion_legacy", SERVER_PATH)
fastapi_edge = load_module("amazon_fusion_edge", FASTAPI_PATH)


def csrf(page: str) -> str:
    match = re.search(r'name=["\']csrf_token["\'] value=["\']([^"\']+)', page)
    assert match is not None
    return match.group(1)


def verification_token(page: str) -> str:
    match = re.search(r"/verify\?token=([A-Za-z0-9_-]+)", page)
    assert match is not None
    return match.group(1)


def reset_token(page: str) -> str:
    match = re.search(r"/reset-password\?token=([A-Za-z0-9_-]+)", page)
    assert match is not None
    return match.group(1)


def register_and_login(
    client: TestClient,
    *,
    email: str = "shopper@example.test",
    password: str = "correct-horse-42",
) -> str:
    register = client.get("/register")
    assert register.status_code == 200
    created = client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "confirm_password": password,
            "csrf_token": csrf(register.text),
        },
    )
    assert created.status_code == 200
    token = verification_token(created.text)
    verified = client.get(f"/verify?token={token}")
    assert verified.status_code == 200
    assert "Email verified" in verified.text

    login = client.get("/login")
    signed_in = client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": csrf(login.text),
            "next": "/account",
        },
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/account"
    return token


def test_account_lifecycle_and_guest_cart_merge(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app) as client:
        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["session"]["signed_in"] is False
        added = client.post(
            "/api/cart/add",
            json={"asin": TARGET_ASIN, "quantity": 1},
        )
        assert added.status_code == 200

        register_and_login(client)
        state = client.get("/api/bootstrap").json()
        assert state["session"]["signed_in"] is True
        assert state["session"]["email"] == "shopper@example.test"
        assert state["cart"]["total_quantity"] == 1
        assert "Hello, shopper" in client.get("/").text

        account = client.get("/account")
        assert account.status_code == 200
        assert "shopper@example.test" in account.text
        assert "/logout" in account.text

        logout = client.post(
            "/logout",
            data={"csrf_token": csrf(account.text)},
            follow_redirects=False,
        )
        assert logout.status_code == 303
        assert client.get("/api/bootstrap").json()["session"]["signed_in"] is False


def test_checkout_order_isolation_idempotency_and_cancellation(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app) as client:
        register_and_login(client)
        assert (
            client.post(
                "/api/cart/add",
                json={"asin": TARGET_ASIN, "quantity": 2},
            ).status_code
            == 200
        )

        checkout = client.get("/checkout")
        assert checkout.status_code == 200
        assert "Shipping address" in checkout.text
        assert "Test payment" in checkout.text
        form = {
            "csrf_token": csrf(checkout.text),
            "idempotency_key": "checkout-one",
            "full_name": "Test Shopper",
            "address_line": "1 Local Test Way",
            "city": "New York",
            "postal_code": "10001",
            "card_number": "4242 4242 4242 4242",
        }
        placed = client.post("/checkout", data=form, follow_redirects=False)
        assert placed.status_code == 303
        location = placed.headers["location"]
        assert re.fullmatch(r"/checkout/success/AMZ-\d{6}", location)

        replay = client.post("/checkout", data=form, follow_redirects=False)
        assert replay.status_code == 303
        assert replay.headers["location"] == location
        assert client.get("/api/bootstrap").json()["cart"]["total_quantity"] == 0

        success = client.get(location)
        assert success.status_code == 200
        assert "Order placed" in success.text
        assert "$439.98" in success.text

        orders = client.get("/account/orders")
        assert orders.status_code == 200
        assert location.rsplit("/", 1)[-1] in orders.text

        number = location.rsplit("/", 1)[-1]
        detail = client.get(f"/account/orders/{number}")
        cancelled = client.post(
            f"/account/orders/{number}/cancel",
            data={"csrf_token": csrf(detail.text)},
            follow_redirects=False,
        )
        assert cancelled.status_code == 303
        assert "Cancelled" in client.get(f"/account/orders/{number}").text

        with TestClient(app) as other:
            register_and_login(
                other,
                email="other@example.test",
                password="another-password-42",
            )
            assert other.get(f"/account/orders/{number}").status_code == 404


def test_password_reset_is_single_use_and_revokes_sessions(tmp_path: Path) -> None:
    app = fastapi_edge.create_app(tmp_path / "amazon.sqlite3", legacy)
    with TestClient(app) as client:
        register_and_login(client)
        forgot = client.get("/forgot-password")
        requested = client.post(
            "/forgot-password",
            data={
                "email": "shopper@example.test",
                "csrf_token": csrf(forgot.text),
            },
        )
        assert requested.status_code == 200
        token = reset_token(requested.text)

        reset = client.get(f"/reset-password?token={token}")
        changed = client.post(
            "/reset-password",
            data={
                "token": token,
                "password": "new-correct-horse-84",
                "confirm_password": "new-correct-horse-84",
                "csrf_token": csrf(reset.text),
            },
        )
        assert changed.status_code == 200
        assert "Password changed" in changed.text
        assert client.get("/api/bootstrap").json()["session"]["signed_in"] is False

        reused = client.post(
            "/reset-password",
            data={
                "token": token,
                "password": "another-correct-pass-84",
                "confirm_password": "another-correct-pass-84",
                "csrf_token": csrf(client.get(f"/reset-password?token={token}").text),
            },
        )
        assert reused.status_code == 400

        login = client.get("/login")
        signed_in = client.post(
            "/login",
            data={
                "email": "shopper@example.test",
                "password": "new-correct-horse-84",
                "csrf_token": csrf(login.text),
                "next": "/account",
            },
            follow_redirects=False,
        )
        assert signed_in.status_code == 303


def test_auth_forms_require_csrf_and_do_not_store_payment_details(
    tmp_path: Path,
) -> None:
    database = tmp_path / "amazon.sqlite3"
    app = fastapi_edge.create_app(database, legacy)
    with TestClient(app) as client:
        rejected = client.post(
            "/register",
            data={
                "email": "shopper@example.test",
                "password": "correct-horse-42",
                "confirm_password": "correct-horse-42",
                "csrf_token": "invalid",
            },
        )
        assert rejected.status_code == 403
        verification = register_and_login(client)
        auth_cookie = client.cookies.get(fastapi_edge.AUTH_COOKIE)
        assert auth_cookie
        client.post("/api/cart/add", json={"asin": TARGET_ASIN, "quantity": 1})
        checkout = client.get("/checkout")
        response = client.post(
            "/checkout",
            data={
                "csrf_token": csrf(checkout.text),
                "idempotency_key": "safe-payment",
                "full_name": "Test Shopper",
                "address_line": "1 Local Test Way",
                "city": "New York",
                "postal_code": "10001",
                "card_number": "4242 4242 4242 4242",
            },
        )
        assert response.status_code == 200

    raw = database.read_bytes()
    assert b"4242 4242 4242 4242" not in raw
    assert b"correct-horse-42" not in raw
    assert verification.encode("ascii") not in raw
    assert auth_cookie.encode("ascii") not in raw


def test_commerce_pages_remain_server_owned_and_responsive(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    app = fastapi_edge.create_app(tmp_path / "browser.sqlite3", legacy)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(base, timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    else:
        pytest.fail("Amazon clone did not start")

    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            for index, (width, height) in enumerate(((1440, 1000), (390, 844))):
                context = browser.new_context(
                    viewport={"width": width, "height": height}
                )
                page = context.new_page()
                page.goto(base + "/register")
                assert page.locator("html").get_attribute("data-server-owned") == "true"
                assert page.get_by_role("heading", name="Create account").is_visible()
                email = f"browser-{index}@example.test"
                page.locator("input[name='email']").fill(email)
                page.locator("input[name='password']").fill("browser-password-42")
                page.locator("input[name='confirm_password']").fill(
                    "browser-password-42"
                )
                page.get_by_role("button", name="Create your local account").click()
                page.get_by_role("link", name="Verify email").click()
                page.get_by_role("link", name="Sign in", exact=True).click()
                page.locator("input[name='email']").fill(email)
                page.locator("input[name='password']").fill("browser-password-42")
                page.locator("form[action='/login'] button[type='submit']").click()
                page.wait_for_url(base + "/account")
                assert page.get_by_text(email, exact=True).is_visible()
                page.goto(base)
                assert page.evaluate(
                    """async (asin) => (await fetch('/api/cart/add', {
                      method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({asin, quantity: 1})
                    })).ok""",
                    TARGET_ASIN,
                )
                page.goto(base + "/gp/cart/view.html")
                page.get_by_role("link", name="Proceed to checkout").first.click()
                page.wait_for_url(base + "/checkout")
                assert page.get_by_text("Shipping address", exact=True).is_visible()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                )
                context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
