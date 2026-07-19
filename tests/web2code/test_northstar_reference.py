"""W2 behavior tests for the private Northstar reference and local mailbox."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = REPO_ROOT / "websitebench" / "northstar-market"
REFERENCE_ROOT = SITE_ROOT / "reference"
MAILBOX_ROOT = REPO_ROOT / "websitebench" / "services" / "mailbox"
for import_root in (REFERENCE_ROOT, MAILBOX_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from benchmail.app import Mailbox, create_admin_app as create_mail_admin  # noqa: E402
from benchmail.app import create_delivery_app as create_mail_delivery  # noqa: E402
from benchmail.app import create_public_app as create_mail_public  # noqa: E402
from northstar.app import build_runtime, create_admin_app, create_public_app  # noqa: E402
from northstar.store import DomainError  # noqa: E402


@pytest.fixture
def runtime(tmp_path: Path):
    messages: list[dict[str, str]] = []

    async def deliver(to: str, subject: str, text: str) -> None:
        messages.append({"to": to, "subject": subject, "text": text})

    value = build_runtime(
        database_path=tmp_path / "northstar.sqlite3",
        fixture_dir=SITE_ROOT / "public" / "fixtures",
        fixture_schema_path=REPO_ROOT / "websitebench" / "schemas" / "fixture.schema.json",
        admin_token="test-admin-token",
        public_site_url="http://northstar.test",
        deliver_mail=deliver,
        initial_fixture=SITE_ROOT / "public" / "fixtures" / "1102.json",
    )
    value.test_messages = messages
    return value


def fixture_account(seed: int = 1102, index: int = 1) -> tuple[str, str]:
    return f"shopper{index}.{seed}@example.test", f"Northstar{seed}Test{index}"


def login_user(runtime, *, index: int = 1, device: str = "device-account"):
    email, password = fixture_account(index=index)
    session, user = runtime.store.login(email=email, password=password, device_key=device)
    return session, user


def add_first_product(runtime, user, device: str, quantity: int = 1) -> dict:
    product = runtime.store.featured_products(1)[0]
    runtime.store.add_to_cart(
        product_id=product["id"], quantity=quantity, user=user, device_key=device
    )
    return product


def checkout(runtime, user, *, key: str = "checkout-key", card: str = "4242 4242 4242 4242"):
    return runtime.store.checkout(
        user=user,
        idempotency_key=key,
        shipping_method="standard",
        address={
            "full_name": "Ava Chen",
            "line1": "100 Test Way",
            "line2": "",
            "city": "Portland",
            "state": "OR",
            "zip_code": "97205",
        },
        card_number=card,
        expiration="12/30",
        cvv="123",
    )


def test_all_generated_fixtures_validate_and_have_unique_references() -> None:
    schema = json.loads(
        (REPO_ROOT / "websitebench" / "schemas" / "fixture.schema.json").read_text()
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    fixture_paths = sorted(SITE_ROOT.glob("**/fixtures/*.json"))

    assert sorted(int(path.stem) for path in fixture_paths) == [
        1101,
        1102,
        9101,
        9102,
        9103,
        9104,
        9105,
        9199,
    ]
    for path in fixture_paths:
        fixture = json.loads(path.read_text())
        validator.validate(fixture)
        categories = {item["id"] for item in fixture["catalog"]["categories"]}
        products = fixture["catalog"]["products"]
        assert len({item["id"] for item in products}) == 48
        assert len({item["slug"] for item in products}) == 48
        assert len({item["sku"] for item in products}) == 48
        assert all(item["category_id"] in categories for item in products)
    concurrency = json.loads((SITE_ROOT / "judge" / "fixtures" / "9199.json").read_text())
    stock_one = concurrency["scenario"]["stock_one_product_id"]
    assert next(item for item in concurrency["catalog"]["products"] if item["id"] == stock_one)[
        "inventory"
    ] == 1


def test_registration_throttle_boundary_and_verification_single_use(runtime) -> None:
    issued = runtime.store.register(
        email=" New.User@Example.Test ",
        password="StrongPass123",
        confirm_password="StrongPass123",
        device_key="device-registration",
    )
    assert issued and issued[0] == "new.user@example.test"

    with pytest.raises(DomainError, match="Please wait") as throttled:
        runtime.store.register(
            email="other@example.test",
            password="StrongPass123",
            confirm_password="StrongPass123",
            device_key="device-registration",
        )
    assert throttled.value.status == 429

    runtime.db.advance_clock(300)
    second = runtime.store.register(
        email="other@example.test",
        password="StrongPass123",
        confirm_password="StrongPass123",
        device_key="device-registration",
    )
    assert second is not None

    runtime.store.verify_email(issued[1])
    with pytest.raises(DomainError, match="already used"):
        runtime.store.verify_email(issued[1])


def test_invalid_registration_does_not_consume_throttle_and_token_expires(runtime) -> None:
    with pytest.raises(DomainError) as invalid:
        runtime.store.register(
            email="not-an-email",
            password="short",
            confirm_password="different",
            device_key="device-invalid",
        )
    assert invalid.value.fields
    issued = runtime.store.register(
        email="valid@example.test",
        password="ValidPass123",
        confirm_password="ValidPass123",
        device_key="device-invalid",
    )
    assert issued
    runtime.db.advance_clock(1801)
    with pytest.raises(DomainError, match="expired"):
        runtime.store.verify_email(issued[1])


def test_session_boundary_and_password_reset_invalidates_every_session(runtime) -> None:
    email, password = fixture_account()
    session_one, user = runtime.store.login(email=email, password=password, device_key="device-one")
    session_two, _ = runtime.store.login(email=email, password=password, device_key="device-two")
    runtime.db.advance_clock(86400)
    assert runtime.store.user_for_session(session_one)["id"] == user["id"]
    runtime.db.advance_clock(1)
    assert runtime.store.user_for_session(session_one) is None

    session_three, _ = runtime.store.login(email=email, password=password, device_key="device-three")
    issued = runtime.store.forgot_password(email)
    assert issued
    runtime.store.reset_password(
        token=issued[1], password="ChangedPass123", confirm_password="ChangedPass123"
    )
    assert runtime.store.user_for_session(session_two) is None
    assert runtime.store.user_for_session(session_three) is None
    with pytest.raises(DomainError, match="incorrect"):
        runtime.store.login(email=email, password=password, device_key="old-password")
    runtime.store.login(email=email, password="ChangedPass123", device_key="new-password")
    with pytest.raises(DomainError, match="already used"):
        runtime.store.reset_password(
            token=issued[1], password="AnotherPass123", confirm_password="AnotherPass123"
        )


def test_guest_and_account_carts_merge_once_with_cap(runtime) -> None:
    _session, user = login_user(runtime, device="account-cart-device")
    product = add_first_product(runtime, user, "account-cart-device", quantity=3)
    runtime.store.add_to_cart(
        product_id=product["id"], quantity=4, user=None, device_key="guest-cart-device"
    )
    email, password = fixture_account()
    runtime.store.login(email=email, password=password, device_key="guest-cart-device")
    merged = runtime.store.cart(user=user, device_key="guest-cart-device")
    assert merged["lines"][0]["quantity"] == min(product["inventory"], 5)

    runtime.store.login(email=email, password=password, device_key="guest-cart-device")
    retried = runtime.store.cart(user=user, device_key="guest-cart-device")
    assert retried["lines"][0]["quantity"] == merged["lines"][0]["quantity"]
    assert runtime.store.cart(user=None, device_key="guest-cart-device")["count"] == 0


def test_decline_is_side_effect_free_and_success_is_idempotent(runtime) -> None:
    _session, user = login_user(runtime)
    product = add_first_product(runtime, user, "device-account", quantity=2)
    before_inventory = runtime.store.product_by_id(product["id"])["inventory"]

    with pytest.raises(DomainError, match="declined") as declined:
        checkout(runtime, user, key="decline-key", card="4000 0000 0000 0002")
    assert declined.value.status == 402
    assert runtime.store.product_by_id(product["id"])["inventory"] == before_inventory
    assert runtime.store.cart(user=user, device_key="device-account")["count"] == 2

    order = checkout(runtime, user, key="success-key")
    expected_subtotal = product["price_cents"] * 2
    assert order["subtotal_cents"] == expected_subtotal
    assert order["shipping_cents"] == (0 if expected_subtotal >= 7500 else 599)
    assert order["tax_cents"] == (expected_subtotal * 825 + 5000) // 10000
    after_first = runtime.db.normalized_state()
    retried = checkout(runtime, user, key="success-key")
    assert retried["id"] == order["id"]
    assert runtime.db.normalized_state() == after_first
    serialized = json.dumps(after_first)
    assert "4242424242424242" not in serialized
    assert '"cvv"' not in serialized


def test_cancellation_at_boundary_restocks_once_and_cross_account_is_404(runtime) -> None:
    _session, user = login_user(runtime, index=1)
    product = add_first_product(runtime, user, "device-account", quantity=2)
    initial_inventory = product["inventory"]
    order = checkout(runtime, user, key="cancel-key")
    assert runtime.store.product_by_id(product["id"])["inventory"] == initial_inventory - 2

    _other_session, other_user = login_user(runtime, index=2, device="other-device")
    with pytest.raises(DomainError) as hidden:
        runtime.store.order_for_user(user_id=other_user["id"], order_number=order["order_number"])
    assert hidden.value.status == 404

    runtime.db.advance_clock(1800)
    cancelled = runtime.store.cancel_order(user_id=user["id"], order_number=order["order_number"])
    assert cancelled["status"] == "cancelled"
    assert runtime.store.product_by_id(product["id"])["inventory"] == initial_inventory
    runtime.store.cancel_order(user_id=user["id"], order_number=order["order_number"])
    assert runtime.store.product_by_id(product["id"])["inventory"] == initial_inventory


def test_cancellation_after_boundary_is_rejected(runtime) -> None:
    _session, user = login_user(runtime)
    product = add_first_product(runtime, user, "device-account")
    order = checkout(runtime, user, key="late-cancel-key")
    inventory_after_order = runtime.store.product_by_id(product["id"])["inventory"]
    runtime.db.advance_clock(1801)
    with pytest.raises(DomainError, match="window has closed"):
        runtime.store.cancel_order(user_id=user["id"], order_number=order["order_number"])
    assert runtime.store.product_by_id(product["id"])["inventory"] == inventory_after_order


def test_stock_one_checkout_is_atomic_under_concurrency(tmp_path: Path) -> None:
    runtime = build_runtime(
        database_path=tmp_path / "concurrency.sqlite3",
        fixture_dir=SITE_ROOT / "judge" / "fixtures",
        fixture_schema_path=REPO_ROOT / "websitebench" / "schemas" / "fixture.schema.json",
        initial_fixture=SITE_ROOT / "judge" / "fixtures" / "9199.json",
        deliver_mail=None,
    )
    fixture = json.loads((SITE_ROOT / "judge" / "fixtures" / "9199.json").read_text())
    product_id = fixture["scenario"]["stock_one_product_id"]
    users = []
    for index in (1, 2):
        _session, user = runtime.store.login(
            email=f"shopper{index}.9199@example.test",
            password=f"Northstar9199Test{index}",
            device_key=f"concurrent-device-{index}",
        )
        runtime.store.add_to_cart(
            product_id=product_id,
            quantity=1,
            user=user,
            device_key=f"concurrent-device-{index}",
        )
        users.append(user)

    def place(index: int):
        try:
            return checkout(runtime, users[index], key=f"concurrent-{index}")
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(place, (0, 1)))

    successes = [value for value in results if isinstance(value, dict)]
    failures = [value for value in results if isinstance(value, DomainError)]
    assert len(successes) == 1
    assert [error.code for error in failures] == ["insufficient_stock"]
    state = runtime.db.normalized_state()
    assert next(item for item in state["products"] if item["id"] == product_id)["inventory"] == 0
    assert len(state["orders"]) == 1


def test_public_routes_admin_contract_and_state_schema(runtime) -> None:
    public = TestClient(create_public_app(runtime))
    admin = TestClient(create_admin_app(runtime))
    auth = {"X-Bench-Admin-Token": "test-admin-token"}

    for path in ("/", "/search", "/cart", "/register", "/login", "/forgot-password"):
        response = public.get(path)
        assert response.status_code == 200, path
        assert "Northstar" in response.text
    assert public.get("/__bench/state").status_code == 404
    assert admin.get("/__bench/state").status_code == 404
    state = admin.get("/__bench/state", headers=auth).json()
    admin_schema = json.loads(
        (REPO_ROOT / "websitebench" / "schemas" / "admin-contract.schema.json").read_text()
    )
    Draft202012Validator(
        {"$defs": admin_schema["$defs"], "$ref": "#/$defs/state_response"},
        format_checker=FormatChecker(),
    ).validate(state)
    clock = admin.post("/__bench/clock/advance", headers=auth, json={"seconds": 300})
    assert clock.json()["now"] == "2026-01-15T12:05:00Z"


def test_browser_registration_delivers_verification_to_local_mailbox(runtime) -> None:
    client = TestClient(create_public_app(runtime))
    response = client.post(
        "/register",
        data={
            "email": "browser.user@example.test",
            "password": "BrowserPass123",
            "confirm_password": "BrowserPass123",
        },
    )
    assert response.status_code == 200
    assert "Check your email" in response.text
    assert len(runtime.test_messages) == 1
    link = runtime.test_messages[0]["text"].split()[-1]
    verified = client.get(link)
    assert verified.status_code == 200
    assert "Your email is verified" in verified.text


def test_mailbox_delivery_query_and_privileged_reset(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "mailbox.sqlite3")
    public = TestClient(create_mail_public(mailbox, "delivery-secret"))
    admin = TestClient(create_mail_admin(mailbox, "admin-secret"))
    payload = {
        "schema_version": 1,
        "to": "Shopper@Example.Test",
        "subject": "Verify your email",
        "text": "Open http://northstar.test/verify?token=abc",
    }
    assert public.post("/api/v1/messages", json=payload).status_code == 404
    delivered = public.post(
        "/api/v1/messages",
        headers={"Authorization": "Bearer delivery-secret"},
        json=payload,
    )
    assert delivered.status_code == 202
    inbox = public.get("/api/v1/inbox", params={"recipient": "shopper@example.test"}).json()
    assert inbox["messages"][0]["links"] == ["http://northstar.test/verify?token=abc"]
    assert admin.post("/__bench/reset").status_code == 404
    reset = admin.post("/__bench/reset", headers={"X-Bench-Admin-Token": "admin-secret"})
    assert reset.json()["removed"] == 1
    assert mailbox.count() == 0


def test_delivery_only_mailbox_cannot_query_tokens(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "delivery.sqlite3")
    delivery = TestClient(create_mail_delivery(mailbox, "delivery-secret"))
    payload = {
        "schema_version": 1,
        "to": "shopper@example.test",
        "subject": "Reset your password",
        "text": "Open http://candidate.test/reset-password?token=secret",
    }

    accepted = delivery.post(
        "/api/v1/messages",
        headers={"Authorization": "Bearer delivery-secret"},
        json=payload,
    )
    assert accepted.status_code == 202
    assert delivery.get("/api/v1/inbox?recipient=shopper@example.test").status_code == 404
    assert delivery.get("/").status_code == 404
