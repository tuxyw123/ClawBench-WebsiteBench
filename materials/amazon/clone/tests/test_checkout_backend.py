from __future__ import annotations

import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as server_module  # noqa: E402
from mail_transport import SMTPConfig  # noqa: E402
from payment_methods import (  # noqa: E402
    SANDBOX_BANK_APPROVED,
    SANDBOX_CARD_APPROVED,
    SANDBOX_CARD_DECLINED,
)
from server import PublicHandler, ReusableThreadingHTTPServer, digest  # noqa: E402
from store import AddressRevisionConflict, ContractError, Store  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"

CART_PATH = "/gp/cart/view.html"
CART_ADD_PATH = "/gp/cart/add.html"
CART_UPDATE_PATH = "/gp/cart/update.html"
CART_DELETE_PATH = "/gp/cart/delete.html"
CART_SAVE_PATH = "/gp/cart/save-for-later.html"
BUY_NOW_PATH = "/gp/buy/now"
BUY_NOW_CONTINUE_PATH = "/gp/buy/now/continue"
CHECKOUT_PATH = "/gp/buy/spc/handlers/display.html"
ADDRESS_PATH = "/gp/buy/addressselect/handlers/display.html"
DELIVERY_PATH = "/gp/buy/shipoptionselect/handlers/display.html"
PAYMENT_PATH = "/gp/buy/payselect/handlers/display.html"
PLACE_ORDER_PATH = "/gp/buy/place-order"
ORDER_DETAIL_PATH = "/gp/your-account/order-details"
ORDER_EMAIL_RETRY_PATH = "/gp/your-account/order-email/retry"
ORDER_HISTORY_PATH = "/gp/css/order-history"
ADDRESS_BOOK_PATH = "/a/addresses"
ADDRESS_CREATE_PATH = "/a/addresses/create"
ADDRESS_UPDATE_PATH = "/a/addresses/update"
ADDRESS_DELETE_PATH = "/a/addresses/delete"
ADDRESS_DEFAULT_PATH = "/a/addresses/set-default"

FIXTURE_ASIN = "B08GTYFC37"
FIXTURE_PRICE = 18_999
DIRECT_ASIN = "B08HN37XC1"
DIRECT_PRICE = 31_699
SAVED_ASIN = "B01M16WBW1"

PASSWORD = "Correct-Horse-921"
TEST_PAYMENT_METHOD = SANDBOX_CARD_APPROVED
STANDARD_SHIPPING = 0
EXPEDITED_SHIPPING = 1_299

FormFields = Mapping[str, str] | Sequence[tuple[str, str]]


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class CheckoutBackendTests(unittest.TestCase):
    """HTTP contract for the simulated, account-owned checkout state machine."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        QuietPublicHandler.store = self.store
        QuietPublicHandler.smtp_config = None
        self.server = ReusableThreadingHTTPServer(("127.0.0.1", 0), QuietPublicHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    @property
    def same_origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(
        self,
        method: str,
        path: str,
        *,
        fields: FormFields | None = None,
        raw_body: bytes | None = None,
        content_type: str = "application/x-www-form-urlencoded",
        cookie: str = "",
        origin: str | None = "same-origin",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        if fields is not None and raw_body is not None:
            raise ValueError("fields and raw_body are mutually exclusive")
        body = urlencode(fields, doseq=True).encode("utf-8") if fields is not None else raw_body
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body))
        if method == "POST" and origin is not None:
            headers["Origin"] = self.same_origin if origin == "same-origin" else origin
        if cookie:
            headers["Cookie"] = cookie

        connection = http.client.HTTPConnection(self.host, self.port, timeout=8)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_headers: dict[str, list[str]] = {}
        for name, value in response.getheaders():
            response_headers.setdefault(name.lower(), []).append(value)
        payload = response.read()
        connection.close()
        return response.status, response_headers, payload

    @staticmethod
    def session_cookie(headers: dict[str, list[str]]) -> str:
        prefix = f"{SESSION_COOKIE}="
        for header in reversed(headers.get("set-cookie", [])):
            pair = header.split(";", 1)[0]
            if pair.startswith(prefix) and pair != prefix:
                return pair
        raise AssertionError("response did not set a non-empty browser session cookie")

    @staticmethod
    def session_digest(cookie: str) -> str:
        name, token = cookie.split("=", 1)
        if name != SESSION_COOKIE or not token:
            raise AssertionError(f"invalid browser session cookie: {cookie!r}")
        return digest(token)

    def anonymous_cookie(self) -> str:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return self.session_cookie(headers)

    def account_id(self, cookie: str) -> int:
        account = self.store.account_for_session(self.session_digest(cookie))
        self.assertIsNotNone(account)
        assert account is not None
        return int(account["account_id"])

    def register(self, cookie: str, email: str) -> str:
        status, headers, body = self.request(
            "POST",
            "/ap/register",
            fields={
                "customerName": "Checkout Buyer",
                "email": email,
                "password": PASSWORD,
                "passwordCheck": PASSWORD,
            },
            cookie=cookie,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/cvf/verify?purpose=registration"], b""),
        )
        messages = self.store.registration_outbox(self.session_digest(cookie))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["status"], "LOCAL_ONLY")
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify",
            fields={"code": messages[0]["verification_code"]},
            cookie=cookie,
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))
        rotated = self.session_cookie(headers)
        self.assertNotEqual(rotated, cookie)
        return rotated

    def add_item(self, cookie: str, asin: str, quantity: int = 1) -> None:
        self.assert_redirect(
            self.request(
                "POST",
                CART_ADD_PATH,
                fields={"ASIN": asin, "quantity": str(quantity)},
                cookie=cookie,
            ),
            CART_PATH,
        )

    def cart_line_id(self, cookie: str, asin: str) -> str:
        rows = [
            row
            for row in self.store.cart(self.session_digest(cookie))
            if str(row["asin"]) == asin
        ]
        self.assertEqual(len(rows), 1, (asin, rows))
        return str(rows[0]["line_id"])

    def save_item(self, cookie: str, asin: str) -> None:
        self.assert_redirect(
            self.request(
                "POST",
                CART_SAVE_PATH,
                fields={"lineID": self.cart_line_id(cookie, asin)},
                cookie=cookie,
            ),
            CART_PATH,
        )

    @staticmethod
    def address_fields(label: str = "Primary") -> dict[str, str]:
        return {
            "fullName": f"{label} Checkout Buyer",
            "addressLine1": f"{label} 10 Orchard Road",
            "addressLine2": "Unit 08-01",
            "city": "Singapore",
            "state": "Singapore",
            "postalCode": "238840",
            "countryCode": "SG",
            "phoneNumber": "+65 6123 4567",
        }

    @classmethod
    def canonical_address_fields(cls, label: str = "Primary") -> dict[str, str]:
        fields = cls.address_fields(label)
        return {
            "full_name": fields["fullName"],
            "address_line1": fields["addressLine1"],
            "address_line2": fields["addressLine2"],
            "city": fields["city"],
            "state_region": fields["state"],
            "postal_code": fields["postalCode"],
            "country_code": fields["countryCode"],
            "phone": fields["phoneNumber"],
        }

    @staticmethod
    def assert_redirect(
        response: tuple[int, dict[str, list[str]], bytes], location: str
    ) -> None:
        status, headers, body = response
        if (status, headers.get("location"), body) != (303, [location], b""):
            raise AssertionError(
                f"expected 303 to {location!r}, got status={status}, "
                f"location={headers.get('location')!r}, body={body!r}"
            )

    def signed_in_cart(
        self,
        email: str,
        *,
        asin: str = FIXTURE_ASIN,
        quantity: int = 1,
    ) -> str:
        cookie = self.anonymous_cookie()
        self.add_item(cookie, asin, quantity)
        return self.register(cookie, email)

    def start_checkout(self, cookie: str) -> None:
        self.assert_redirect(
            self.request("POST", CHECKOUT_PATH, fields={}, cookie=cookie),
            ADDRESS_PATH,
        )

    def submit_address(
        self, cookie: str, fields: FormFields | None = None
    ) -> None:
        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_PATH,
                fields=fields if fields is not None else self.address_fields(),
                cookie=cookie,
            ),
            DELIVERY_PATH,
        )

    def select_delivery(self, cookie: str, method: str) -> None:
        self.assert_redirect(
            self.request(
                "POST",
                DELIVERY_PATH,
                fields={"deliveryOption": method},
                cookie=cookie,
            ),
            PAYMENT_PATH,
        )

    def select_payment(self, cookie: str) -> None:
        self.assert_redirect(
            self.request(
                "POST",
                PAYMENT_PATH,
                fields={"paymentMethod": TEST_PAYMENT_METHOD},
                cookie=cookie,
            ),
            CHECKOUT_PATH,
        )

    def place_order(self, cookie: str, key: str) -> tuple[int, str]:
        status, headers, body = self.request(
            "POST",
            PLACE_ORDER_PATH,
            fields={"idempotencyKey": key},
            cookie=cookie,
        )
        self.assertEqual(status, 303, body)
        self.assertEqual(body, b"")
        locations = headers.get("location", [])
        self.assertEqual(len(locations), 1)
        parsed = urlsplit(locations[0])
        self.assertEqual(parsed.path, ORDER_DETAIL_PATH)
        values = parse_qs(parsed.query).get("orderID", [])
        self.assertEqual(len(values), 1)
        return int(values[0]), locations[0]

    def checkout_row(self, cookie: str) -> dict[str, Any]:
        account_id = self.account_id(cookie)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM checkout_sessions WHERE account_id=? ORDER BY checkout_id DESC LIMIT 1",
                (account_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return dict(row)

    def complete_checkout(
        self,
        cookie: str,
        *,
        delivery: str,
        key: str,
        address_label: str = "Primary",
    ) -> tuple[int, str]:
        self.start_checkout(cookie)
        self.submit_address(cookie, self.address_fields(address_label))
        self.select_delivery(cookie, delivery)
        self.select_payment(cookie)
        status, _, _ = self.request("GET", CHECKOUT_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        return self.place_order(cookie, key)

    def test_checkout_routes_require_an_authenticated_account(self) -> None:
        guest = self.anonymous_cookie()
        self.add_item(guest, FIXTURE_ASIN)

        for path in (ADDRESS_PATH, DELIVERY_PATH, PAYMENT_PATH, CHECKOUT_PATH):
            with self.subTest(method="GET", path=path):
                status, headers, body = self.request("GET", path, cookie=guest)
                self.assertEqual(status, 303)
                self.assertEqual(body, b"")
                location = urlsplit(headers["location"][0])
                self.assertEqual(location.path, "/ap/signin")
                self.assertEqual(parse_qs(location.query).get("openid.return_to"), [path])

        status, headers, body = self.request(
            "POST", CHECKOUT_PATH, fields={}, cookie=guest
        )
        self.assertEqual(status, 303)
        self.assertEqual(body, b"")
        location = urlsplit(headers["location"][0])
        self.assertEqual(location.path, "/ap/signin")
        self.assertEqual(
            parse_qs(location.query).get("openid.return_to"), [ADDRESS_PATH]
        )
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM checkout_sessions").fetchone()[0], 0)

    def test_signed_in_buy_now_orders_only_the_selection_and_preserves_cart(self) -> None:
        cookie = self.signed_in_cart(
            "buy-now-owner@example.test", asin=FIXTURE_ASIN, quantity=3
        )
        selected_options = {
            "Style": "Old Model",
            "Capacity": "2TB",
            "Color": "Sky Blue",
        }
        fields = {
            "ASIN": DIRECT_ASIN,
            "quantity": "2",
            **{
                f"option.{label}": value
                for label, value in selected_options.items()
            },
        }
        self.assert_redirect(
            self.request("POST", BUY_NOW_PATH, fields=fields, cookie=cookie),
            ADDRESS_PATH,
        )

        checkout = self.store.checkout(self.session_digest(cookie))
        self.assertIsNotNone(checkout)
        assert checkout is not None
        self.assertEqual(checkout["checkout_mode"], "BUY_NOW")
        self.assertEqual(len(checkout["items"]), 1)
        self.assertEqual(checkout["items"][0]["asin"], DIRECT_ASIN)
        self.assertEqual(checkout["items"][0]["quantity"], 2)
        self.assertEqual(
            checkout["items"][0]["selected_options"], selected_options
        )
        cart_before_order = self.store.cart(self.session_digest(cookie))
        self.assertEqual(
            [(line["asin"], line["quantity"]) for line in cart_before_order],
            [(FIXTURE_ASIN, 3)],
        )

        status, _, address_page = self.request("GET", ADDRESS_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Checkout <a href=\"/gp/cart/view.html\">(2 items)</a>", address_page)
        self.assertIn(DIRECT_ASIN.encode("ascii"), address_page)
        self.assertNotIn(FIXTURE_ASIN.encode("ascii"), address_page)

        self.submit_address(cookie, self.address_fields("Buy Now"))
        self.select_delivery(cookie, "standard")
        self.select_payment(cookie)
        status, _, review_page = self.request("GET", CHECKOUT_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Buy Now checkout", review_page)
        self.assertIn(b"Items already in your cart will remain there", review_page)
        order_id, _ = self.place_order(cookie, "buy-now-place-order-0001")

        order = self.store.order_for_session(self.session_digest(cookie), order_id)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order["checkout_mode"], "BUY_NOW")
        self.assertEqual(len(order["items"]), 1)
        self.assertEqual(order["items"][0]["asin"], DIRECT_ASIN)
        self.assertEqual(order["items"][0]["quantity"], 2)
        self.assertEqual(order["items"][0]["selected_options"], selected_options)
        cart_after_order = self.store.cart(self.session_digest(cookie))
        self.assertEqual(
            [(line["asin"], line["quantity"]) for line in cart_after_order],
            [(FIXTURE_ASIN, 3)],
        )

    def test_buy_now_control_is_live_and_targets_the_dedicated_post_route(self) -> None:
        cookie = self.anonymous_cookie()
        status, _, product_page = self.request(
            "GET", f"/dp/{DIRECT_ASIN}", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertIn(
            b'data-product-buy-now data-quote-can-enable="true"', product_page
        )
        self.assertNotIn(
            b'data-product-buy-now data-quote-can-enable="false" disabled',
            product_page,
        )
        status, _, script = self.request("GET", "/static/app.js", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(
            b'productCartForm.setAttribute("action", "/gp/buy/now")', script
        )
        self.assertIn(b"productCartForm.requestSubmit()", script)

    def test_guest_buy_now_survives_registration_rotation_without_entering_cart(self) -> None:
        guest = self.anonymous_cookie()
        self.add_item(guest, FIXTURE_ASIN, 2)
        status, headers, body = self.request(
            "POST",
            BUY_NOW_PATH,
            fields={"ASIN": DIRECT_ASIN, "quantity": "1"},
            cookie=guest,
        )
        self.assertEqual((status, body), (303, b""))
        signin = urlsplit(headers["location"][0])
        self.assertEqual(signin.path, "/ap/signin")
        self.assertEqual(
            parse_qs(signin.query).get("openid.return_to"),
            [BUY_NOW_CONTINUE_PATH],
        )
        with self.store.connect() as conn:
            pending = dict(
                conn.execute(
                    "SELECT * FROM pending_buy_now WHERE session_digest=?",
                    (self.session_digest(guest),),
                ).fetchone()
            )
        self.assertEqual((pending["asin"], pending["quantity"]), (DIRECT_ASIN, 1))
        self.assertEqual(
            [(line["asin"], line["quantity"]) for line in self.store.cart(self.session_digest(guest))],
            [(FIXTURE_ASIN, 2)],
        )

        email = "guest-buy-now@example.test"
        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={"email": email, "openid.return_to": BUY_NOW_CONTINUE_PATH},
            cookie=guest,
        )
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(urlsplit(headers["location"][0]).path, "/ap/register")
        status, headers, body = self.request(
            "POST",
            "/ap/register",
            fields={
                "customerName": "Buy Now Guest",
                "email": email,
                "password": PASSWORD,
                "passwordCheck": PASSWORD,
                "openid.return_to": BUY_NOW_CONTINUE_PATH,
            },
            cookie=guest,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/cvf/verify?purpose=registration"], b""),
        )
        outbox = self.store.registration_outbox(self.session_digest(guest))
        self.assertEqual(len(outbox), 1)
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify",
            fields={"code": outbox[0]["verification_code"]},
            cookie=guest,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, [BUY_NOW_CONTINUE_PATH], b""),
        )
        authenticated = self.session_cookie(headers)
        self.assertNotEqual(authenticated, guest)

        self.assert_redirect(
            self.request("GET", BUY_NOW_CONTINUE_PATH, cookie=authenticated),
            ADDRESS_PATH,
        )
        checkout = self.store.checkout(self.session_digest(authenticated))
        self.assertIsNotNone(checkout)
        assert checkout is not None
        self.assertEqual(checkout["checkout_mode"], "BUY_NOW")
        self.assertEqual(
            [(item["asin"], item["quantity"]) for item in checkout["items"]],
            [(DIRECT_ASIN, 1)],
        )
        self.assertEqual(
            [
                (line["asin"], line["quantity"])
                for line in self.store.cart(self.session_digest(authenticated))
            ],
            [(FIXTURE_ASIN, 2)],
        )
        with self.store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM pending_buy_now").fetchone()[0],
                0,
            )

    def test_guest_buy_now_survives_existing_account_signin_rotation(self) -> None:
        email = "existing-buy-now@example.test"
        self.register(self.anonymous_cookie(), email)

        guest = self.anonymous_cookie()
        self.add_item(guest, FIXTURE_ASIN, 1)
        status, headers, body = self.request(
            "POST",
            BUY_NOW_PATH,
            fields={"ASIN": DIRECT_ASIN, "quantity": "2"},
            cookie=guest,
        )
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(urlsplit(headers["location"][0]).path, "/ap/signin")

        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={"email": email, "openid.return_to": BUY_NOW_CONTINUE_PATH},
            cookie=guest,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/signin?stage=password"], b""),
        )
        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={"password": PASSWORD},
            cookie=guest,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, [BUY_NOW_CONTINUE_PATH], b""),
        )
        authenticated = self.session_cookie(headers)
        self.assert_redirect(
            self.request("GET", BUY_NOW_CONTINUE_PATH, cookie=authenticated),
            ADDRESS_PATH,
        )
        checkout = self.store.checkout(self.session_digest(authenticated))
        self.assertIsNotNone(checkout)
        assert checkout is not None
        self.assertEqual(checkout["checkout_mode"], "BUY_NOW")
        self.assertEqual(
            [(item["asin"], item["quantity"]) for item in checkout["items"]],
            [(DIRECT_ASIN, 2)],
        )
        self.assertEqual(
            [
                (line["asin"], line["quantity"])
                for line in self.store.cart(self.session_digest(authenticated))
            ],
            [(FIXTURE_ASIN, 1)],
        )

    def test_state_order_is_enforced_and_backtracking_invalidates_later_choices(self) -> None:
        cookie = self.signed_in_cart("state-machine@example.test")
        self.start_checkout(cookie)
        self.assertEqual(self.checkout_row(cookie)["status"], "CART_READY")

        status, _, _ = self.request(
            "POST",
            DELIVERY_PATH,
            fields={"deliveryOption": "standard"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={"paymentMethod": TEST_PAYMENT_METHOD},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        self.assert_redirect(self.request("GET", CHECKOUT_PATH, cookie=cookie), ADDRESS_PATH)

        self.submit_address(cookie)
        checkout = self.checkout_row(cookie)
        self.assertEqual(checkout["status"], "ADDRESS_SELECTED")
        address_id = int(checkout["address_id"])
        with self.store.connect() as conn:
            address = conn.execute(
                "SELECT * FROM addresses WHERE address_id=?", (address_id,)
            ).fetchone()
        self.assertIsNotNone(address)
        assert address is not None
        self.assertEqual(int(address["account_id"]), self.account_id(cookie))

        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={"paymentMethod": TEST_PAYMENT_METHOD},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        self.select_delivery(cookie, "standard")
        checkout = self.checkout_row(cookie)
        self.assertEqual(checkout["status"], "DELIVERY_SELECTED")
        self.assertEqual(checkout["shipping_minor"], STANDARD_SHIPPING)
        self.select_payment(cookie)
        self.assertEqual(self.checkout_row(cookie)["status"], "PAYMENT_SELECTED")

        # Going back to delivery supersedes the approved simulated payment.
        self.select_delivery(cookie, "expedited")
        checkout = self.checkout_row(cookie)
        self.assertEqual(checkout["status"], "DELIVERY_SELECTED")
        self.assertEqual(checkout["shipping_minor"], EXPEDITED_SHIPPING)
        self.assert_redirect(self.request("GET", CHECKOUT_PATH, cookie=cookie), PAYMENT_PATH)
        with self.store.connect() as conn:
            statuses = [
                row[0]
                for row in conn.execute(
                    "SELECT status FROM payment_attempts WHERE checkout_id=? ORDER BY payment_attempt_id",
                    (checkout["checkout_id"],),
                )
            ]
        self.assertEqual(statuses, ["SUPERSEDED"])

        self.select_payment(cookie)
        self.submit_address(cookie, self.address_fields("Replacement"))
        checkout = self.checkout_row(cookie)
        self.assertEqual(checkout["status"], "ADDRESS_SELECTED")
        self.assertIsNone(checkout["delivery_method"])
        self.assertIsNone(checkout["shipping_minor"])
        with self.store.connect() as conn:
            approved = conn.execute(
                "SELECT COUNT(*) FROM payment_attempts WHERE checkout_id=? AND status='APPROVED'",
                (checkout["checkout_id"],),
            ).fetchone()[0]
        self.assertEqual(approved, 0)

    def test_cart_mutations_after_payment_require_a_fresh_payment_approval(self) -> None:
        mutations = (
            (
                "add",
                CART_ADD_PATH,
                {"ASIN": FIXTURE_ASIN, "quantity": "1"},
            ),
            (
                "update",
                CART_UPDATE_PATH,
                None,
            ),
            (
                "delete",
                CART_DELETE_PATH,
                None,
            ),
        )
        for mutation, path, fields in mutations:
            with self.subTest(mutation=mutation):
                cookie = self.signed_in_cart(
                    f"stale-payment-{mutation}@example.test"
                )
                if mutation == "delete":
                    self.add_item(cookie, DIRECT_ASIN)
                    fields = {"lineID": self.cart_line_id(cookie, DIRECT_ASIN)}
                elif mutation == "update":
                    fields = {
                        "lineID": self.cart_line_id(cookie, FIXTURE_ASIN),
                        "quantity": "2",
                    }
                assert fields is not None
                self.start_checkout(cookie)
                self.submit_address(cookie, self.address_fields(mutation.title()))
                self.select_delivery(cookie, "standard")
                self.select_payment(cookie)
                original_checkout = self.checkout_row(cookie)
                self.assertEqual(original_checkout["status"], "PAYMENT_SELECTED")

                self.assert_redirect(
                    self.request("POST", path, fields=fields, cookie=cookie),
                    CART_PATH,
                )
                self.assert_redirect(
                    self.request(
                        "POST",
                        PLACE_ORDER_PATH,
                        fields={
                            "idempotencyKey": (
                                f"stale-{mutation}-checkout-order-0001"
                            )
                        },
                        cookie=cookie,
                    ),
                    PAYMENT_PATH + "?notice=cart-changed",
                )

                reconciled = self.store.checkout(self.session_digest(cookie))
                self.assertIsNotNone(reconciled)
                assert reconciled is not None
                self.assertEqual(reconciled["status"], "DELIVERY_SELECTED")
                self.assertIsNone(reconciled["payment"])
                with self.store.connect() as conn:
                    payment_statuses = [
                        row[0]
                        for row in conn.execute(
                            """
                            SELECT status FROM payment_attempts
                            WHERE checkout_id=? ORDER BY payment_attempt_id
                            """,
                            (int(original_checkout["checkout_id"]),),
                        )
                    ]
                self.assertEqual(payment_statuses, ["SUPERSEDED"])

                status, _, payment_page = self.request(
                    "GET",
                    PAYMENT_PATH + "?notice=cart-changed",
                    cookie=cookie,
                )
                self.assertEqual(status, 200)
                self.assertIn(b"Your cart changed", payment_page)
                self.assertIn(
                    b"select a sandbox payment method again", payment_page
                )

                status, _, _ = self.request(
                    "POST",
                    PLACE_ORDER_PATH,
                    fields={
                        "idempotencyKey": (
                            f"stale-{mutation}-checkout-order-0001"
                        )
                    },
                    cookie=cookie,
                )
                self.assertEqual(status, 409)
                with self.store.connect() as conn:
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM orders WHERE account_id=?",
                            (self.account_id(cookie),),
                        ).fetchone()[0],
                        0,
                    )

                self.select_payment(cookie)
                status, _, review_page = self.request(
                    "GET", CHECKOUT_PATH, cookie=cookie
                )
                self.assertEqual(status, 200)
                self.assertIn(b"Place your order", review_page)

    def test_unsupported_delivery_country_is_rejected_at_every_checkout_boundary(self) -> None:
        cookie = self.signed_in_cart("unsupported-country@example.test")
        session_digest = self.session_digest(cookie)
        self.start_checkout(cookie)

        unsupported_form = self.address_fields("Unsupported Form")
        unsupported_form["countryCode"] = "ZZ"
        status, _, _ = self.request(
            "POST", ADDRESS_PATH, fields=unsupported_form, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.checkout_row(cookie)["status"], "CART_READY")

        unsupported_fields = self.canonical_address_fields("Unsupported Store")
        unsupported_fields["country_code"] = "ZZ"
        with self.assertRaisesRegex(ContractError, "country is not supported"):
            self.store.create_address(session_digest, unsupported_fields)

        legacy = self.store.create_address(
            session_digest, self.canonical_address_fields("Legacy")
        )
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE addresses SET country_code='ZZ' WHERE address_id=?",
                (int(legacy["address_id"]),),
            )
        with self.assertRaisesRegex(ContractError, "country is not supported"):
            self.store.select_checkout_address(
                session_digest, legacy["address_id"], legacy["revision"]
            )

        valid = self.store.create_address(
            session_digest, self.canonical_address_fields("Validated")
        )
        selected = self.store.select_checkout_address(
            session_digest, valid["address_id"], valid["revision"]
        )
        self.assertEqual(selected["status"], "ADDRESS_SELECTED")
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE addresses SET country_code='ZZ' WHERE address_id=?",
                (int(valid["address_id"]),),
            )
        with self.assertRaisesRegex(ContractError, "country is not supported"):
            self.store.select_delivery(session_digest, "standard")

        with self.store.connect() as conn:
            conn.execute(
                "UPDATE addresses SET country_code='SG' WHERE address_id=?",
                (int(valid["address_id"]),),
            )
        self.store.select_delivery(session_digest, "standard")
        self.store.select_test_payment(session_digest, TEST_PAYMENT_METHOD)
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE addresses SET country_code='ZZ' WHERE address_id=?",
                (int(valid["address_id"]),),
            )
        self.assert_redirect(
            self.request(
                "POST",
                PLACE_ORDER_PATH,
                fields={"idempotencyKey": "unsupported-country-order-0001"},
                cookie=cookie,
            ),
            ADDRESS_PATH + "?notice=unsupported-delivery-country",
        )
        with self.store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0
            )

        reconciled = self.store.checkout(session_digest)
        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled["status"], "CART_READY")
        self.assertIsNone(reconciled["address"])
        self.assertIsNone(reconciled["payment"])

        status, _, address_page = self.request(
            "GET",
            ADDRESS_PATH + "?notice=unsupported-delivery-country",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Choose a supported delivery country", address_page)

    def test_addresses_are_account_owned_and_foreign_address_query_is_not_disclosed(self) -> None:
        first = self.signed_in_cart("first-address@example.test")
        self.start_checkout(first)
        self.submit_address(first, self.address_fields("First Secret"))
        first_checkout = self.checkout_row(first)

        second = self.signed_in_cart("second-address@example.test", asin=DIRECT_ASIN)
        self.start_checkout(second)
        self.submit_address(second, self.address_fields("Second Secret"))
        second_checkout = self.checkout_row(second)

        with self.store.connect() as conn:
            records = {
                int(row["address_id"]): dict(row)
                for row in conn.execute("SELECT * FROM addresses ORDER BY address_id")
            }
        self.assertEqual(
            int(records[int(first_checkout["address_id"])]["account_id"]),
            self.account_id(first),
        )
        self.assertEqual(
            int(records[int(second_checkout["address_id"])]["account_id"]),
            self.account_id(second),
        )

        path = ADDRESS_PATH + "?" + urlencode(
            {"addressId": second_checkout["address_id"]}
        )
        status, _, body = self.request("GET", path, cookie=first)
        self.assertEqual(status, 200)
        self.assertIn(b"First Secret", body)
        self.assertNotIn(b"Second Secret", body)

    def test_address_book_crud_defaults_ownership_and_strict_post_contract(self) -> None:
        owner = self.register(self.anonymous_cookie(), "address-book-owner@example.test")
        other = self.register(self.anonymous_cookie(), "address-book-other@example.test")

        guest = self.anonymous_cookie()
        status, headers, body = self.request("GET", ADDRESS_BOOK_PATH, cookie=guest)
        self.assertEqual((status, body), (303, b""))
        signin = urlsplit(headers["location"][0])
        self.assertEqual(signin.path, "/ap/signin")
        self.assertEqual(
            parse_qs(signin.query).get("openid.return_to"), [ADDRESS_BOOK_PATH]
        )

        status, _, _ = self.request(
            "POST",
            ADDRESS_CREATE_PATH,
            fields=self.address_fields("Cross Origin"),
            cookie=owner,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        malformed = self.address_fields("Malformed")
        malformed.pop("postalCode")
        status, _, _ = self.request(
            "POST", ADDRESS_CREATE_PATH, fields=malformed, cookie=owner
        )
        self.assertEqual(status, 400)
        duplicated = list(self.address_fields("Duplicated").items())
        duplicated.append(("city", "Elsewhere"))
        status, _, _ = self.request(
            "POST", ADDRESS_CREATE_PATH, fields=duplicated, cookie=owner
        )
        self.assertEqual(status, 400)

        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_CREATE_PATH,
                fields=self.address_fields("Home"),
                cookie=owner,
            ),
            ADDRESS_BOOK_PATH + "?status=added",
        )
        first = self.store.addresses_for_session(self.session_digest(owner))[0]
        self.assertTrue(first["is_default"])
        self.assertEqual(first["revision"], 1)

        second_form = {**self.address_fields("Office"), "makeDefault": "1"}
        self.assert_redirect(
            self.request(
                "POST", ADDRESS_CREATE_PATH, fields=second_form, cookie=owner
            ),
            ADDRESS_BOOK_PATH + "?status=added",
        )
        addresses = self.store.addresses_for_session(self.session_digest(owner))
        by_name = {address["full_name"]: address for address in addresses}
        first = by_name["Home Checkout Buyer"]
        second = by_name["Office Checkout Buyer"]
        self.assertFalse(first["is_default"])
        self.assertEqual(first["revision"], 2)
        self.assertTrue(second["is_default"])

        status, _, book = self.request("GET", ADDRESS_BOOK_PATH, cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn(b"Your Addresses", book)
        self.assertIn(b"Home 10 Orchard Road", book)
        self.assertIn(b"Office 10 Orchard Road", book)
        self.assertIn(b'action="/a/addresses/set-default"', book)
        self.assertIn(b'action="/a/addresses/delete"', book)

        status, _, default_edit = self.request(
            "GET",
            ADDRESS_BOOK_PATH + "/edit?" + urlencode({"addressID": second["address_id"]}),
            cookie=owner,
        )
        self.assertEqual(status, 200)
        self.assertIn(b"To change it, set another saved address as default", default_edit)
        self.assertNotIn(b'name="makeDefault"', default_edit)
        status, _, nondefault_edit = self.request(
            "GET",
            ADDRESS_BOOK_PATH + "/edit?" + urlencode({"addressID": first["address_id"]}),
            cookie=owner,
        )
        self.assertEqual(status, 200)
        self.assertIn(b'name="makeDefault" value="1"', nondefault_edit)

        foreign_edit = ADDRESS_BOOK_PATH + "/edit?" + urlencode(
            {"addressID": first["address_id"]}
        )
        status, _, foreign_body = self.request("GET", foreign_edit, cookie=other)
        self.assertEqual(status, 404)
        self.assertNotIn(b"Home 10 Orchard Road", foreign_body)
        status, _, _ = self.request(
            "POST",
            ADDRESS_DEFAULT_PATH,
            fields={
                "addressId": str(first["address_id"]),
                "addressRevision": str(first["revision"]),
            },
            cookie=other,
        )
        self.assertEqual(status, 404)

        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_DEFAULT_PATH,
                fields={
                    "addressId": str(first["address_id"]),
                    "addressRevision": str(first["revision"]),
                },
                cookie=owner,
            ),
            ADDRESS_BOOK_PATH + "?status=default",
        )
        addresses = self.store.addresses_for_session(self.session_digest(owner))
        by_id = {address["address_id"]: address for address in addresses}
        first = by_id[first["address_id"]]
        second = by_id[second["address_id"]]
        self.assertTrue(first["is_default"])
        self.assertFalse(second["is_default"])

        stale_update = {
            **self.address_fields("Stale Office"),
            "addressId": str(second["address_id"]),
            "addressRevision": "1",
        }
        status, _, conflict = self.request(
            "POST", ADDRESS_UPDATE_PATH, fields=stale_update, cookie=owner
        )
        self.assertEqual(status, 409)
        self.assertIn(b"changed in another request", conflict)

        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_DELETE_PATH,
                fields={
                    "addressId": str(first["address_id"]),
                    "addressRevision": str(first["revision"]),
                },
                cookie=owner,
            ),
            ADDRESS_BOOK_PATH + "?status=deleted",
        )
        remaining = self.store.addresses_for_session(self.session_digest(owner))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["address_id"], second["address_id"])
        self.assertTrue(remaining[0]["is_default"])
        with self.store.connect() as conn:
            archived = conn.execute(
                "SELECT is_archived,is_default FROM addresses WHERE address_id=?",
                (first["address_id"],),
            ).fetchone()
        self.assertEqual((archived["is_archived"], archived["is_default"]), (1, 0))

    def test_saved_address_selection_edit_invalidation_and_active_delete_guard(self) -> None:
        cookie = self.signed_in_cart("saved-address-checkout@example.test")
        session_digest = self.session_digest(cookie)
        first = self.store.create_address(
            session_digest, self.canonical_address_fields("First")
        )
        second = self.store.create_address(
            session_digest,
            self.canonical_address_fields("Default"),
            make_default=True,
        )
        self.start_checkout(cookie)

        status, _, address_page = self.request("GET", ADDRESS_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        selected_control = (
            f'name="addressSelection" value="{second["address_id"]}:'
            f'{second["revision"]}" checked'
        ).encode("ascii")
        self.assertIn(selected_control, address_page)
        self.assertIn(b"Manage addresses", address_page)

        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_PATH,
                fields={
                    "addressSelection": (
                        f'{second["address_id"]}:{second["revision"]}'
                    )
                },
                cookie=cookie,
            ),
            DELIVERY_PATH,
        )
        checkout = self.store.checkout(session_digest)
        self.assertEqual(checkout["status"], "ADDRESS_SELECTED")
        self.assertEqual(checkout["address"]["address_id"], second["address_id"])
        self.select_delivery(cookie, "expedited")
        self.select_payment(cookie)
        self.assertEqual(self.store.checkout(session_digest)["status"], "PAYMENT_SELECTED")

        update_form = {
            **self.address_fields("Edited Default"),
            "addressId": str(second["address_id"]),
            "addressRevision": str(second["revision"]),
        }
        self.assert_redirect(
            self.request(
                "POST", ADDRESS_UPDATE_PATH, fields=update_form, cookie=cookie
            ),
            ADDRESS_BOOK_PATH + "?status=updated",
        )
        checkout = self.store.checkout(session_digest)
        self.assertEqual(checkout["status"], "ADDRESS_SELECTED")
        self.assertEqual(checkout["address"]["full_name"], "Edited Default Checkout Buyer")
        self.assertIsNone(checkout["delivery_method"])
        self.assertIsNone(checkout["payment"])
        with self.store.connect() as conn:
            payment_statuses = [
                str(row["status"])
                for row in conn.execute(
                    "SELECT status FROM payment_attempts ORDER BY payment_attempt_id"
                )
            ]
        self.assertEqual(payment_statuses, ["SUPERSEDED"])

        status, _, _ = self.request(
            "POST",
            ADDRESS_PATH,
            fields={
                "addressSelection": f'{second["address_id"]}:{second["revision"]}'
            },
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        refreshed = self.store.address_for_session(session_digest, second["address_id"])
        assert refreshed is not None
        status, _, conflict = self.request(
            "POST",
            ADDRESS_DELETE_PATH,
            fields={
                "addressId": str(refreshed["address_id"]),
                "addressRevision": str(refreshed["revision"]),
            },
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        self.assertIn(b"active checkout", conflict)
        self.assertIsNotNone(
            self.store.address_for_session(session_digest, refreshed["address_id"])
        )
        self.assertNotEqual(first["address_id"], refreshed["address_id"])

    def test_buy_now_new_default_address_can_be_archived_after_order_without_losing_snapshot(self) -> None:
        cookie = self.signed_in_cart(
            "buy-now-address-book@example.test", asin=FIXTURE_ASIN, quantity=2
        )
        session_digest = self.session_digest(cookie)
        base = self.store.create_address(
            session_digest, self.canonical_address_fields("Home")
        )
        self.assert_redirect(
            self.request(
                "POST",
                BUY_NOW_PATH,
                fields={"ASIN": DIRECT_ASIN, "quantity": "1"},
                cookie=cookie,
            ),
            ADDRESS_PATH,
        )
        new_address_form = {
            **self.address_fields("Buy Now Destination"),
            "makeDefault": "1",
        }
        self.submit_address(cookie, new_address_form)
        checkout = self.store.checkout(session_digest)
        self.assertEqual(checkout["checkout_mode"], "BUY_NOW")
        self.assertEqual(len(checkout["saved_addresses"]), 2)
        selected_id = checkout["address"]["address_id"]
        self.assertNotEqual(selected_id, base["address_id"])
        self.assertTrue(checkout["address"]["is_default"])
        self.assertEqual(
            [(line["asin"], line["quantity"]) for line in self.store.cart(session_digest)],
            [(FIXTURE_ASIN, 2)],
        )

        self.select_delivery(cookie, "standard")
        self.select_payment(cookie)
        order_id, location = self.place_order(
            cookie, "buy-now-address-book-order-0001"
        )
        selected = self.store.address_for_session(session_digest, selected_id)
        assert selected is not None
        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_DELETE_PATH,
                fields={
                    "addressId": str(selected_id),
                    "addressRevision": str(selected["revision"]),
                },
                cookie=cookie,
            ),
            ADDRESS_BOOK_PATH + "?status=deleted",
        )
        self.assertIsNone(self.store.address_for_session(session_digest, selected_id))
        remaining = self.store.addresses_for_session(session_digest)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["address_id"], base["address_id"])
        self.assertTrue(remaining[0]["is_default"])

        order = self.store.order_for_session(session_digest, order_id)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(
            order["address"]["full_name"],
            "Buy Now Destination Checkout Buyer",
        )
        status, _, detail = self.request("GET", location, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Buy Now Destination Checkout Buyer", detail)
        self.assertEqual(
            [(line["asin"], line["quantity"]) for line in self.store.cart(session_digest)],
            [(FIXTURE_ASIN, 2)],
        )

    def test_concurrent_address_edits_and_default_switches_remain_consistent(self) -> None:
        cookie = self.register(self.anonymous_cookie(), "address-race@example.test")
        session_digest = self.session_digest(cookie)
        first = self.store.create_address(
            session_digest, self.canonical_address_fields("First")
        )
        second = self.store.create_address(
            session_digest, self.canonical_address_fields("Second")
        )
        third = self.store.create_address(
            session_digest, self.canonical_address_fields("Third")
        )

        barrier = threading.Barrier(2)
        edit_results: list[str] = []
        result_lock = threading.Lock()

        def edit_second(label: str) -> None:
            barrier.wait()
            try:
                self.store.update_address(
                    session_digest,
                    second["address_id"],
                    second["revision"],
                    self.canonical_address_fields(label),
                )
                outcome = "updated"
            except AddressRevisionConflict:
                outcome = "conflict"
            with result_lock:
                edit_results.append(outcome)

        edit_threads = [
            threading.Thread(target=edit_second, args=("Second A",)),
            threading.Thread(target=edit_second, args=("Second B",)),
        ]
        for thread in edit_threads:
            thread.start()
        for thread in edit_threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(edit_results, ["updated", "conflict"])

        current = {
            address["address_id"]: address
            for address in self.store.addresses_for_session(session_digest)
        }
        default_barrier = threading.Barrier(2)
        default_results: list[str] = []

        def set_default(address_id: int) -> None:
            default_barrier.wait()
            try:
                self.store.set_default_address(
                    session_digest,
                    address_id,
                    current[address_id]["revision"],
                )
                outcome = "updated"
            except AddressRevisionConflict:
                outcome = "conflict"
            with result_lock:
                default_results.append(outcome)

        default_threads = [
            threading.Thread(target=set_default, args=(second["address_id"],)),
            threading.Thread(target=set_default, args=(third["address_id"],)),
        ]
        for thread in default_threads:
            thread.start()
        for thread in default_threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(default_results, ["updated", "updated"])
        addresses = self.store.addresses_for_session(session_digest)
        default_addresses = [
            address for address in addresses if bool(address["is_default"])
        ]
        self.assertEqual(len(default_addresses), 1)
        self.assertIn(
            default_addresses[0]["address_id"],
            {second["address_id"], third["address_id"]},
        )
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            defaults = conn.execute(
                "SELECT COUNT(*) FROM addresses WHERE account_id=? AND is_default=1 AND is_archived=0",
                (self.account_id(cookie),),
            ).fetchone()[0]
        self.assertEqual(defaults, 1)
        self.assertNotEqual(default_addresses[0]["address_id"], first["address_id"])

    def test_checkout_only_address_schema_is_migrated_and_backfills_one_default(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy-addresses.sqlite3"
        current_schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
        address_book_columns = """    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
"""
        archived_default_check = """    updated_at TEXT NOT NULL,
    CHECK (is_archived = 0 OR is_default = 0)
"""
        legacy_schema = current_schema.replace(address_book_columns, "").replace(
            archived_default_check, "    updated_at TEXT NOT NULL\n"
        )
        self.assertNotEqual(legacy_schema, current_schema)
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(legacy_schema)
            connection.execute(
                """
                INSERT INTO accounts(
                    account_id,email_normalized,display_name,password_salt,
                    password_hash,password_scheme,created_at
                ) VALUES (1,'legacy-address@example.test','Legacy',X'00',X'00','scrypt-v1','2026-01-01')
                """
            )
            connection.executemany(
                """
                INSERT INTO addresses(
                    account_id,full_name,address_line1,address_line2,city,
                    state_region,postal_code,country_code,phone,created_at,updated_at
                ) VALUES (1,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    (
                        "First Legacy",
                        "1 First Street",
                        "",
                        "Singapore",
                        "Singapore",
                        "111111",
                        "SG",
                        "",
                        "2026-01-01",
                        "2026-01-01",
                    ),
                    (
                        "Second Legacy",
                        "2 Second Street",
                        "",
                        "Singapore",
                        "Singapore",
                        "222222",
                        "SG",
                        "",
                        "2026-01-02",
                        "2026-01-02",
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        with migrated.connect() as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(addresses)")
            }
            rows = connection.execute(
                "SELECT address_id,is_default,is_archived,revision FROM addresses ORDER BY address_id"
            ).fetchall()
            indexes = {
                row["name"] for row in connection.execute("PRAGMA index_list(addresses)")
            }
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertTrue({"is_default", "is_archived", "revision"}.issubset(columns))
        self.assertEqual(
            [
                (row["address_id"], row["is_default"], row["is_archived"], row["revision"])
                for row in rows
            ],
            [(1, 1, 0, 1), (2, 0, 0, 1)],
        )
        self.assertIn("addresses_one_default_account_idx", indexes)
        with self.assertRaises(sqlite3.IntegrityError):
            with migrated.connect() as connection:
                connection.execute(
                    "UPDATE addresses SET is_default=1 WHERE address_id=2"
                )

    def test_oversized_address_ids_are_bounded_domain_errors(self) -> None:
        cookie = self.signed_in_cart("oversized-address-id@example.test")
        session_digest = self.session_digest(cookie)
        address = self.store.create_address(
            session_digest, self.canonical_address_fields("Safe")
        )
        self.start_checkout(cookie)

        for oversized in ("9223372036854775808", "9" * 5000):
            with self.subTest(length=len(oversized), route="edit"):
                status, _, _ = self.request(
                    "GET",
                    ADDRESS_BOOK_PATH + "/edit?" + urlencode({"addressID": oversized}),
                    cookie=cookie,
                )
                self.assertEqual(status, 404)
            with self.subTest(length=len(oversized), route="delete"):
                status, _, _ = self.request(
                    "POST",
                    ADDRESS_DELETE_PATH,
                    fields={"addressId": oversized, "addressRevision": "1"},
                    cookie=cookie,
                )
                self.assertEqual(status, 404)
            with self.subTest(length=len(oversized), route="checkout"):
                status, _, _ = self.request(
                    "POST",
                    ADDRESS_PATH,
                    fields={"addressSelection": f"{oversized}:1"},
                    cookie=cookie,
                )
                self.assertEqual(status, 409)
            status, _, _ = self.request("GET", "/", cookie=cookie)
            self.assertEqual(status, 200)

        self.assertEqual(
            self.store.addresses_for_session(session_digest)[0]["address_id"],
            address["address_id"],
        )

    def test_empty_cart_cancels_only_cart_checkout_and_releases_selected_address(self) -> None:
        cookie = self.signed_in_cart("empty-cart-checkout@example.test")
        session_digest = self.session_digest(cookie)
        address = self.store.create_address(
            session_digest, self.canonical_address_fields("Cancelable")
        )
        self.start_checkout(cookie)
        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_PATH,
                fields={
                    "addressSelection": (
                        f'{address["address_id"]}:{address["revision"]}'
                    )
                },
                cookie=cookie,
            ),
            DELIVERY_PATH,
        )
        self.assertEqual(self.store.checkout(session_digest)["status"], "ADDRESS_SELECTED")
        self.assert_redirect(
            self.request(
                "POST",
                CART_DELETE_PATH,
                fields={"lineID": self.cart_line_id(cookie, FIXTURE_ASIN)},
                cookie=cookie,
            ),
            CART_PATH,
        )
        self.assertIsNone(self.store.checkout(session_digest))
        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_DELETE_PATH,
                fields={
                    "addressId": str(address["address_id"]),
                    "addressRevision": str(address["revision"]),
                },
                cookie=cookie,
            ),
            ADDRESS_BOOK_PATH + "?status=deleted",
        )
        self.assertEqual(self.store.addresses_for_session(session_digest), [])
        self.assert_redirect(
            self.request("GET", ADDRESS_PATH, cookie=cookie), CART_PATH
        )

        buy_now = self.signed_in_cart(
            "empty-cart-buy-now-control@example.test",
            asin=FIXTURE_ASIN,
            quantity=1,
        )
        buy_now_digest = self.session_digest(buy_now)
        buy_now_address = self.store.create_address(
            buy_now_digest, self.canonical_address_fields("Buy Now")
        )
        self.assert_redirect(
            self.request(
                "POST",
                BUY_NOW_PATH,
                fields={"ASIN": DIRECT_ASIN, "quantity": "1"},
                cookie=buy_now,
            ),
            ADDRESS_PATH,
        )
        self.assert_redirect(
            self.request(
                "POST",
                ADDRESS_PATH,
                fields={
                    "addressSelection": (
                        f'{buy_now_address["address_id"]}:'
                        f'{buy_now_address["revision"]}'
                    )
                },
                cookie=buy_now,
            ),
            DELIVERY_PATH,
        )
        self.assert_redirect(
            self.request(
                "POST",
                CART_SAVE_PATH,
                fields={"lineID": self.cart_line_id(buy_now, FIXTURE_ASIN)},
                cookie=buy_now,
            ),
            CART_PATH,
        )
        checkout = self.store.checkout(buy_now_digest)
        self.assertIsNotNone(checkout)
        assert checkout is not None
        self.assertEqual(checkout["checkout_mode"], "BUY_NOW")
        self.assertEqual(checkout["status"], "ADDRESS_SELECTED")
        self.assertEqual(checkout["items"][0]["asin"], DIRECT_ASIN)

    def test_startup_rejects_cross_account_checkout_address_corruption(self) -> None:
        corrupt_path = Path(self.tempdir.name) / "cross-account-address.sqlite3"
        connection = sqlite3.connect(corrupt_path)
        try:
            connection.executescript(
                (ROOT / "schema.sql").read_text(encoding="utf-8")
            )
            connection.executemany(
                """
                INSERT INTO accounts(
                    account_id,email_normalized,display_name,password_salt,
                    password_hash,password_scheme,created_at
                ) VALUES (?,?,?,?,?,?,'2026-01-01')
                """,
                (
                    (1, "checkout-owner@example.test", "Checkout", b"a", b"a", "scrypt-v1"),
                    (2, "address-owner@example.test", "Address", b"b", b"b", "scrypt-v1"),
                ),
            )
            cursor = connection.execute(
                """
                INSERT INTO addresses(
                    account_id,full_name,address_line1,address_line2,city,
                    state_region,postal_code,country_code,phone,is_default,
                    is_archived,revision,created_at,updated_at
                ) VALUES (2,'Foreign','2 Foreign Street','','Singapore',
                          'Singapore','222222','SG','',1,0,1,'2026-01-01','2026-01-01')
                """
            )
            connection.execute(
                """
                INSERT INTO checkout_sessions(
                    account_id,idempotency_key,checkout_mode,status,address_id,
                    currency,created_at,updated_at
                ) VALUES (1,'cross-account-checkout','CART','ADDRESS_SELECTED',
                          ?,'USD','2026-01-01','2026-01-01')
                """,
                (int(cursor.lastrowid),),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(
            ContractError, "checkout address owned by another account"
        ):
            Store(corrupt_path, ROOT / "schema.sql", ROOT / "fixtures")

    def test_payment_is_simulated_and_pan_or_cvv_are_never_accepted_or_persisted(self) -> None:
        cookie = self.signed_in_cart("payment-safety@example.test")
        self.start_checkout(cookie)
        self.submit_address(cookie)
        self.select_delivery(cookie, "standard")

        pan = "4111111111111111"
        cvv = "cvv-canary-73X"
        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={
                "paymentMethod": TEST_PAYMENT_METHOD,
                "cardNumber": pan,
                "cvv": cvv,
            },
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={"paymentMethod": "4111111111111111"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={"paymentMethod": "test-card"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)

        with self.store.connect() as conn:
            columns = {
                str(row["name"]).casefold()
                for row in conn.execute("PRAGMA table_info(payment_attempts)")
            }
            self.assertTrue(
                {"method", "status", "amount_minor", "is_simulation"}.issubset(columns)
            )
            for forbidden in ("pan", "cvv", "card_number", "cardnumber", "security_code"):
                self.assertNotIn(forbidden, columns)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM payment_attempts").fetchone()[0], 0)

        self.select_payment(cookie)
        with self.store.connect() as conn:
            payment = dict(conn.execute("SELECT * FROM payment_attempts").fetchone())
            database_dump = "\n".join(conn.iterdump())
        self.assertEqual(payment["method"], TEST_PAYMENT_METHOD)
        self.assertEqual(payment["status"], "APPROVED")
        self.assertEqual(payment["is_simulation"], 1)
        self.assertNotIn(pan, database_dump)
        self.assertNotIn(cvv, database_dump)
        journal = json.dumps(self.store.journal(), sort_keys=True)
        self.assertNotIn(pan, journal)
        self.assertNotIn(cvv, journal)

    def test_payment_page_offers_non_secret_card_and_bank_sandbox_scenarios(self) -> None:
        cookie = self.signed_in_cart("sandbox-methods@example.test")
        self.start_checkout(cookie)
        self.submit_address(cookie)
        self.select_delivery(cookie, "standard")

        status, _, body = self.request("GET", PAYMENT_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        for method in (
            SANDBOX_CARD_APPROVED,
            SANDBOX_CARD_DECLINED,
            SANDBOX_BANK_APPROVED,
        ):
            self.assertIn(f'value="{method}"'.encode("utf-8"), body)
        for forbidden in (
            b'name="cardNumber"',
            b'name="cvv"',
            b'name="expiry"',
            b'name="routingNumber"',
        ):
            self.assertNotIn(forbidden, body)

    def test_declined_sandbox_card_can_be_retried_with_bank_method(self) -> None:
        cookie = self.signed_in_cart("sandbox-retry@example.test")
        self.start_checkout(cookie)
        self.submit_address(cookie)
        self.select_delivery(cookie, "standard")

        self.assert_redirect(
            self.request(
                "POST",
                PAYMENT_PATH,
                fields={"paymentMethod": SANDBOX_CARD_DECLINED},
                cookie=cookie,
            ),
            PAYMENT_PATH + "?notice=payment-declined",
        )
        status, _, body = self.request(
            "GET", PAYMENT_PATH + "?notice=payment-declined", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertIn(b"sandbox issuer declined", body)
        self.assertEqual(self.checkout_row(cookie)["status"], "DELIVERY_SELECTED")

        self.assert_redirect(
            self.request(
                "POST",
                PAYMENT_PATH,
                fields={"paymentMethod": SANDBOX_BANK_APPROVED},
                cookie=cookie,
            ),
            CHECKOUT_PATH,
        )
        self.assertEqual(self.checkout_row(cookie)["status"], "PAYMENT_SELECTED")
        with self.store.connect() as conn:
            attempts = [
                tuple(row)
                for row in conn.execute(
                    "SELECT method,status FROM payment_attempts ORDER BY payment_attempt_id"
                )
            ]
        self.assertEqual(
            attempts,
            [
                (SANDBOX_CARD_DECLINED, "DECLINED"),
                (SANDBOX_BANK_APPROVED, "APPROVED"),
            ],
        )
        status, _, review = self.request("GET", CHECKOUT_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Sandbox bank account", review)
        order_id, _ = self.place_order(cookie, "sandbox-bank-retry-order-0001")
        order = self.store.order_for_session(self.session_digest(cookie), order_id)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order["payment"]["method"], SANDBOX_BANK_APPROVED)
        self.assertEqual(order["payment"]["method_label"], "Sandbox bank account")

    def test_decline_supersedes_an_existing_approval_until_retry(self) -> None:
        cookie = self.signed_in_cart("sandbox-supersede@example.test")
        self.start_checkout(cookie)
        self.submit_address(cookie)
        self.select_delivery(cookie, "standard")
        self.assert_redirect(
            self.request(
                "POST",
                PAYMENT_PATH,
                fields={"paymentMethod": SANDBOX_CARD_APPROVED},
                cookie=cookie,
            ),
            CHECKOUT_PATH,
        )
        self.assert_redirect(
            self.request(
                "POST",
                PAYMENT_PATH,
                fields={"paymentMethod": SANDBOX_CARD_DECLINED},
                cookie=cookie,
            ),
            PAYMENT_PATH + "?notice=payment-declined",
        )
        with self.store.connect() as conn:
            statuses = [
                tuple(row)
                for row in conn.execute(
                    "SELECT method,status FROM payment_attempts ORDER BY payment_attempt_id"
                )
            ]
        self.assertEqual(
            statuses,
            [
                (SANDBOX_CARD_APPROVED, "SUPERSEDED"),
                (SANDBOX_CARD_DECLINED, "DECLINED"),
            ],
        )
        status, _, body = self.request(
            "POST",
            PLACE_ORDER_PATH,
            fields={"idempotencyKey": "declined-attempt-cannot-order"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        self.assertIn(b"Checkout state conflict", body)

    def test_payment_schema_migration_preserves_legacy_order_and_adds_declines(self) -> None:
        cookie = self.signed_in_cart("sandbox-migration@example.test")
        order_id, _ = self.complete_checkout(
            cookie,
            delivery="standard",
            key="sandbox-payment-migration-order",
        )
        database_path = self.store.db_path

        # Simulate the value persisted by pre-sandbox-scenario installations.
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE payment_attempts SET method='test-card' "
                "WHERE payment_attempt_id=(SELECT payment_attempt_id FROM orders WHERE order_id=?)",
                (order_id,),
            )

        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DROP INDEX IF EXISTS payment_attempts_one_approved_checkout_idx"
            )
            connection.execute(
                """
                CREATE TABLE payment_attempts_legacy_constraint (
                    payment_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checkout_id INTEGER NOT NULL
                        REFERENCES checkout_sessions(checkout_id) ON DELETE CASCADE,
                    account_id INTEGER NOT NULL
                        REFERENCES accounts(account_id) ON DELETE CASCADE,
                    method TEXT NOT NULL CHECK (method='test-card'),
                    status TEXT NOT NULL CHECK (
                        status IN ('APPROVED','SUPERSEDED')
                    ),
                    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
                    currency TEXT NOT NULL,
                    cart_fingerprint TEXT NOT NULL,
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation=1),
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO payment_attempts_legacy_constraint
                SELECT * FROM payment_attempts
                """
            )
            connection.execute("DROP TABLE payment_attempts")
            connection.execute(
                "ALTER TABLE payment_attempts_legacy_constraint "
                "RENAME TO payment_attempts"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX payment_attempts_one_approved_checkout_idx
                ON payment_attempts(checkout_id) WHERE status='APPROVED'
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Store(database_path, ROOT / "schema.sql", ROOT / "fixtures")
        order = migrated.order_for_session(self.session_digest(cookie), order_id)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order["payment"]["method"], "test-card")
        self.assertEqual(
            order["payment"]["method_label"], "Legacy sandbox test card"
        )
        with migrated.connect() as conn:
            table_sql = str(
                conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='payment_attempts'"
                ).fetchone()[0]
            )
            self.assertIn("sandbox-card-approved", table_sql)
            self.assertIn("'DECLINED'", table_sql)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_standard_and_expedited_shipping_are_included_in_payment_and_order_totals(self) -> None:
        cases = (
            ("standard", STANDARD_SHIPPING, "standard-total@example.test", "standard-total-key-0001"),
            ("expedited", EXPEDITED_SHIPPING, "expedited-total@example.test", "expedited-total-key-01"),
        )
        for method, shipping, email, key in cases:
            with self.subTest(delivery=method):
                cookie = self.signed_in_cart(email)
                order_id, _ = self.complete_checkout(
                    cookie,
                    delivery=method,
                    key=key,
                    address_label=method.title(),
                )
                with self.store.connect() as conn:
                    order = dict(
                        conn.execute(
                            "SELECT * FROM orders WHERE order_id=?", (order_id,)
                        ).fetchone()
                    )
                    payment = dict(
                        conn.execute(
                            "SELECT * FROM payment_attempts WHERE payment_attempt_id=?",
                            (order["payment_attempt_id"],),
                        ).fetchone()
                    )
                self.assertEqual(order["items_subtotal_minor"], FIXTURE_PRICE)
                self.assertEqual(order["shipping_minor"], shipping)
                self.assertEqual(order["total_minor"], FIXTURE_PRICE + shipping)
                self.assertEqual(order["delivery_method"], method)
                self.assertEqual(payment["amount_minor"], FIXTURE_PRICE + shipping)

    def test_place_order_snapshots_items_clears_active_cart_and_is_idempotent(self) -> None:
        cookie = self.anonymous_cookie()
        selected_options = {
            "Style": "Old Model",
            "Capacity": "2TB",
            "Color": "Sky Blue",
        }
        selected_price = 32_999
        selected_image = (
            "/static/assets/source-current/2026-07-21/pdp-home/"
            "B08HN37XC1/color-sky-blue.jpg"
        )
        self.assert_redirect(
            self.request(
                "POST",
                CART_ADD_PATH,
                fields={
                    "ASIN": DIRECT_ASIN,
                    "quantity": "2",
                    **{
                        f"option.{label}": value
                        for label, value in selected_options.items()
                    },
                },
                cookie=cookie,
            ),
            CART_PATH,
        )
        self.add_item(cookie, SAVED_ASIN, 1)
        self.save_item(cookie, SAVED_ASIN)
        cookie = self.register(cookie, "order-owner@example.test")

        key = "place-order-idempotency-0001"
        order_id, location = self.complete_checkout(
            cookie,
            delivery="expedited",
            key=key,
            address_label="Order Snapshot",
        )
        expected_subtotal = selected_price * 2
        expected_total = expected_subtotal + EXPEDITED_SHIPPING

        with self.store.connect() as conn:
            order = dict(
                conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            )
            items = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM order_items WHERE order_id=? ORDER BY ordinal", (order_id,)
                )
            ]
            shipment = dict(
                conn.execute("SELECT * FROM shipments WHERE order_id=?", (order_id,)).fetchone()
            )
            emails = [
                dict(row)
                for row in conn.execute("SELECT * FROM email_outbox WHERE order_id=?", (order_id,))
            ]

        self.assertEqual(order["status"], "PLACED")
        self.assertEqual(order["items_subtotal_minor"], expected_subtotal)
        self.assertEqual(order["shipping_minor"], EXPEDITED_SHIPPING)
        self.assertEqual(order["total_minor"], expected_total)
        self.assertEqual(order["currency"], "USD")
        self.assertEqual(order["delivery_method"], "expedited")
        self.assertEqual(order["is_simulation"], 1)
        address_snapshot = json.loads(order["shipping_address_json"])
        self.assertEqual(address_snapshot["address_line1"], "Order Snapshot 10 Orchard Road")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["asin"], DIRECT_ASIN)
        self.assertEqual(items[0]["quantity"], 2)
        self.assertEqual(json.loads(items[0]["selection_json"]), selected_options)
        self.assertEqual(items[0]["image_path"], selected_image)
        self.assertEqual(items[0]["unit_price_minor"], selected_price)
        self.assertEqual(items[0]["line_total_minor"], expected_subtotal)
        self.assertEqual(shipment["status"], "PREPARING")
        self.assertEqual(shipment["delivery_method"], "expedited")
        self.assertEqual(shipment["shipping_minor"], EXPEDITED_SHIPPING)
        self.assertEqual(len(emails), 1)
        self.assertEqual(emails[0]["recipient"], "order-owner@example.test")
        self.assertEqual(emails[0]["template"], "order-confirmation")
        self.assertEqual(emails[0]["status"], "LOCAL_ONLY")
        self.assertEqual(emails[0]["is_simulation"], 1)
        order_payload = self.store.order_for_session(
            self.session_digest(cookie), order_id
        )
        self.assertIsNotNone(order_payload)
        assert order_payload is not None
        self.assertEqual(order_payload["items"][0]["selected_options"], selected_options)
        self.assertEqual(order_payload["items"][0]["price_minor"], selected_price)
        self.assertEqual(order_payload["items"][0]["image_path"], selected_image)
        self.assertEqual(
            json.loads(emails[0]["payload_json"])["items"][0]["selected_options"],
            selected_options,
        )
        status, _, confirmation = self.request("GET", location, cookie=cookie)
        self.assertEqual(status, 200)
        for label, value in selected_options.items():
            self.assertIn(f"<dt>{label}:</dt><dd>{value}</dd>".encode(), confirmation)

        self.assertEqual(self.store.cart(self.session_digest(cookie)), [])
        saved = self.store.saved_cart(self.session_digest(cookie))
        self.assertEqual([(line["asin"], line["quantity"]) for line in saved], [(SAVED_ASIN, 1)])

        repeated_id, repeated_location = self.place_order(cookie, key)
        self.assertEqual((repeated_id, repeated_location), (order_id, location))
        with self.store.connect() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("orders", "order_items", "shipments", "email_outbox")
            }
        self.assertEqual(
            counts,
            {"orders": 1, "order_items": 1, "shipments": 1, "email_outbox": 1},
        )
        status, _, _ = self.request(
            "POST",
            PLACE_ORDER_PATH,
            fields={"idempotencyKey": "different-order-key-0002"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)

    def test_checkout_and_order_preserve_two_variants_of_the_same_asin(self) -> None:
        cookie = self.anonymous_cookie()
        selections = (
            {"Style": "Old Model", "Capacity": "2TB", "Color": "Black"},
            {"Style": "Old Model", "Capacity": "2TB", "Color": "Monterey"},
        )
        for selection, quantity in zip(selections, (1, 2), strict=True):
            self.assert_redirect(
                self.request(
                    "POST",
                    CART_ADD_PATH,
                    fields={
                        "ASIN": DIRECT_ASIN,
                        "quantity": str(quantity),
                        **{
                            f"option.{label}": value
                            for label, value in selection.items()
                        },
                    },
                    cookie=cookie,
                ),
                CART_PATH,
            )
        cookie = self.register(cookie, "variant-order@example.test")
        session_digest = self.session_digest(cookie)

        self.start_checkout(cookie)
        checkout = self.store.checkout(session_digest)
        self.assertIsNotNone(checkout)
        assert checkout is not None
        self.assertEqual(len(checkout["items"]), 2)
        self.assertEqual(
            [item["selected_options"] for item in checkout["items"]],
            list(selections),
        )
        self.assertEqual(
            [item["quantity"] for item in checkout["items"]], [1, 2]
        )

        self.submit_address(cookie, self.address_fields("Variant Order"))
        self.select_delivery(cookie, "standard")
        self.select_payment(cookie)
        order_id, _ = self.place_order(cookie, "same-asin-variant-order-0001")
        order = self.store.order_for_session(session_digest, order_id)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(len(order["items"]), 2)
        self.assertEqual(
            [item["selected_options"] for item in order["items"]],
            list(selections),
        )
        self.assertEqual([item["quantity"] for item in order["items"]], [1, 2])
        self.assertEqual(self.store.cart(session_digest), [])

        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT asin,selection_json,quantity
                FROM order_items WHERE order_id=? ORDER BY ordinal
                """,
                (order_id,),
            ).fetchall()
        self.assertEqual([row["asin"] for row in rows], [DIRECT_ASIN, DIRECT_ASIN])
        self.assertEqual(
            [json.loads(row["selection_json"]) for row in rows], list(selections)
        )

    def test_order_confirmation_can_use_smtp_delivery_state(self) -> None:
        cookie = self.signed_in_cart(
            "smtp-order@example.test", asin=FIXTURE_ASIN, quantity=1
        )
        self.start_checkout(cookie)
        self.submit_address(cookie)
        self.select_delivery(cookie, "standard")
        self.select_payment(cookie)
        QuietPublicHandler.smtp_config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="Amazon Clone <no-reply@example.test>",
        )
        delivery_started = threading.Event()
        release_delivery = threading.Event()
        captured: list[dict[str, str]] = []

        def capture_delivery(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            del config
            captured.append(
                {"recipient": recipient, "subject": subject, "body": body}
            )
            delivery_started.set()
            release_delivery.wait(3)

        try:
            with patch.object(
                server_module, "send_smtp_message", side_effect=capture_delivery
            ):
                status, headers, body = self.request(
                    "POST",
                    PLACE_ORDER_PATH,
                    fields={"idempotencyKey": "smtp-order-confirmation-0001"},
                    cookie=cookie,
                )
                self.assertEqual(status, 303)
                self.assertEqual(body, b"")
                self.assertTrue(delivery_started.wait(2))
                location = headers["location"][0]
                order_id = int(parse_qs(urlsplit(location).query)["orderID"][0])

                duplicate = self.request(
                    "POST",
                    PLACE_ORDER_PATH,
                    fields={"idempotencyKey": "smtp-order-confirmation-0001"},
                    cookie=cookie,
                )
                self.assertEqual(
                    (duplicate[0], duplicate[1].get("location"), duplicate[2]),
                    (303, [location], b""),
                )
                time.sleep(0.05)
                self.assertEqual(len(captured), 1)
                release_delivery.set()

                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    messages = self.store.mail_outbox(self.account_id(cookie))
                    if messages and messages[0]["status"] == "SMTP_SENT":
                        break
                    time.sleep(0.01)
                else:
                    self.fail(f"order email remained undelivered: {messages!r}")
        finally:
            release_delivery.set()

        messages = self.store.mail_outbox(self.account_id(cookie))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["status"], "SMTP_SENT")
        self.assertFalse(messages[0]["is_simulation"])
        self.assertEqual(messages[0]["delivery_attempts"], 1)
        self.assertIsNotNone(messages[0]["sent_at"])
        self.assertEqual(captured[0]["recipient"], "smtp-order@example.test")
        self.assertIn(str(order_id), captured[0]["body"])
        self.assertIn("order", captured[0]["subject"].lower())

    def test_failed_order_email_is_owner_scoped_and_retryable(self) -> None:
        owner = self.signed_in_cart(
            "retry-order-owner@example.test", asin=FIXTURE_ASIN, quantity=1
        )
        other = self.register(
            self.anonymous_cookie(), "retry-order-other@example.test"
        )
        self.start_checkout(owner)
        self.submit_address(owner)
        self.select_delivery(owner, "standard")
        self.select_payment(owner)
        QuietPublicHandler.smtp_config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="no-reply@example.test",
        )
        attempts: list[str] = []
        retry_finished = threading.Event()

        def fail_then_send(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            del config, recipient, subject
            attempts.append(body)
            if len(attempts) == 1:
                raise RuntimeError("synthetic order SMTP outage")
            retry_finished.set()

        with patch.object(
            server_module, "send_smtp_message", side_effect=fail_then_send
        ):
            order_id, location = self.place_order(
                owner, "smtp-order-retry-key-0001"
            )
            deadline = time.monotonic() + 3
            failed: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                messages = self.store.mail_outbox(self.account_id(owner))
                if messages and messages[0]["status"] == "SMTP_FAILED":
                    failed = messages[0]
                    break
                time.sleep(0.01)
            self.assertIsNotNone(failed)
            assert failed is not None
            self.assertEqual(failed["delivery_attempts"], 1)

            status, _, page = self.request("GET", location, cookie=owner)
            self.assertEqual(status, 200)
            self.assertIn(b"SMTP_FAILED", page)
            self.assertIn(b"Retry email delivery", page)
            self.assertNotIn(b"synthetic order SMTP outage", page)

            status, _, body = self.request(
                "POST",
                ORDER_EMAIL_RETRY_PATH,
                fields={"orderID": str(order_id)},
                cookie=other,
            )
            self.assertEqual((status, body), (404, b"Not Found"))
            self.assertEqual(len(attempts), 1)

            status, headers, body = self.request(
                "POST",
                ORDER_EMAIL_RETRY_PATH,
                fields={"orderID": str(order_id)},
                cookie=owner,
            )
            self.assertEqual(
                (status, headers.get("location"), body),
                (303, [location], b""),
            )
            self.assertTrue(retry_finished.wait(2))
            deadline = time.monotonic() + 3
            sent: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                messages = self.store.mail_outbox(self.account_id(owner))
                if messages and messages[0]["status"] == "SMTP_SENT":
                    sent = messages[0]
                    break
                time.sleep(0.01)

        self.assertIsNotNone(sent)
        assert sent is not None
        self.assertEqual(sent["delivery_attempts"], 2)
        self.assertEqual(attempts[0], attempts[1])
        _, _, sent_page = self.request("GET", location, cookie=owner)
        self.assertIn(b"SMTP_SENT", sent_page)
        self.assertNotIn(b"Retry email delivery", sent_page)

    def test_local_only_restart_never_requeues_legacy_order_mail(self) -> None:
        owner = self.signed_in_cart(
            "legacy-local-order@example.test", asin=FIXTURE_ASIN, quantity=1
        )
        order_id, location = self.complete_checkout(
            owner,
            delivery="standard",
            key="legacy-local-order-key-0001",
        )
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE email_outbox
                SET status='SMTP_FAILED',is_simulation=0,delivery_attempts=1,
                    last_error='SMTPDeliveryError'
                WHERE order_id=?
                """,
                (order_id,),
            )

        status, _, page = self.request("GET", location, cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn(b"LOCAL_ONLY", page)
        self.assertNotIn(b"configured SMTP service", page)
        self.assertNotIn(b"Retry email delivery", page)
        status, headers, body = self.request(
            "POST",
            ORDER_EMAIL_RETRY_PATH,
            fields={"orderID": str(order_id)},
            cookie=owner,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, [location], b""),
        )
        self.assertEqual(
            self.store.mail_outbox(self.account_id(owner))[0]["status"],
            "SMTP_FAILED",
        )
        self.assertEqual(self.store.reconcile_mail_for_local_only(), 1)
        localized = self.store.mail_outbox(self.account_id(owner))[0]
        self.assertEqual(localized["status"], "LOCAL_ONLY")
        self.assertTrue(localized["is_simulation"])

        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE email_outbox
                SET status='SMTP_PENDING',is_simulation=0,
                    claim_token='legacy-pending-claim'
                WHERE order_id=?
                """,
                (order_id,),
            )
        self.assertEqual(self.store.reconcile_mail_for_local_only(), 1)
        pending_localized = self.store.mail_outbox(self.account_id(owner))[0]
        self.assertEqual(pending_localized["status"], "LOCAL_ONLY")
        self.assertIsNone(pending_localized["claim_token"])

    def test_order_detail_and_history_are_scoped_to_the_owning_account(self) -> None:
        owner = self.signed_in_cart("history-owner@example.test")
        order_id, location = self.complete_checkout(
            owner,
            delivery="standard",
            key="history-order-key-00001",
            address_label="History Owner",
        )

        status, _, owner_detail = self.request("GET", location, cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn(str(order_id).encode("ascii"), owner_detail)
        self.assertIn(FIXTURE_ASIN.encode("ascii"), owner_detail)
        status, _, owner_history = self.request("GET", ORDER_HISTORY_PATH, cookie=owner)
        self.assertEqual(status, 200)
        order_history_marker = f"Order # {order_id}".encode("ascii")
        self.assertIn(order_history_marker, owner_history)

        other = self.register(self.anonymous_cookie(), "other-account@example.test")
        status, _, other_detail = self.request("GET", location, cookie=other)
        self.assertEqual(status, 404)
        self.assertNotIn(FIXTURE_ASIN.encode("ascii"), other_detail)
        status, _, other_history = self.request("GET", ORDER_HISTORY_PATH, cookie=other)
        self.assertEqual(status, 200)
        self.assertNotIn(order_history_marker, other_history)

        status, _, _ = self.request(
            "GET", ORDER_DETAIL_PATH + "?orderID=999999", cookie=owner
        )
        self.assertEqual(status, 404)

    def test_malformed_duplicate_cross_origin_and_out_of_order_posts_are_rejected(self) -> None:
        cookie = self.signed_in_cart("malformed-checkout@example.test")

        status, _, _ = self.request(
            "POST",
            BUY_NOW_PATH,
            fields={"ASIN": DIRECT_ASIN, "quantity": "1"},
            cookie=cookie,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST",
            BUY_NOW_PATH,
            fields=(("ASIN", DIRECT_ASIN), ("ASIN", FIXTURE_ASIN), ("quantity", "1")),
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM checkout_lines").fetchone()[0], 0)

        # The old benchmark's nonempty checkout-start POST remains an explicit 404.
        status, _, _ = self.request(
            "POST", CHECKOUT_PATH, fields={"checkout": "true"}, cookie=cookie
        )
        self.assertEqual(status, 404)
        status, _, _ = self.request(
            "POST", CHECKOUT_PATH, fields={}, cookie=cookie, origin="https://evil.example"
        )
        self.assertEqual(status, 403)
        self.start_checkout(cookie)

        malformed_address = dict(self.address_fields())
        malformed_address.pop("postalCode")
        status, _, _ = self.request(
            "POST", ADDRESS_PATH, fields=malformed_address, cookie=cookie
        )
        self.assertEqual(status, 400)
        duplicate_address = list(self.address_fields().items())
        duplicate_address.append(("city", "Elsewhere"))
        status, _, _ = self.request(
            "POST", ADDRESS_PATH, fields=duplicate_address, cookie=cookie
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            ADDRESS_PATH,
            fields=self.address_fields(),
            cookie=cookie,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        self.submit_address(cookie)

        status, _, _ = self.request(
            "POST",
            DELIVERY_PATH,
            fields=(("deliveryOption", "standard"), ("deliveryOption", "expedited")),
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            DELIVERY_PATH,
            fields={"deliveryOption": "overnight"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        status, _, _ = self.request(
            "POST",
            DELIVERY_PATH,
            fields={"deliveryOption": "standard"},
            cookie=cookie,
            origin=None,
        )
        self.assertEqual(status, 403)
        self.select_delivery(cookie, "standard")

        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields=(("paymentMethod", TEST_PAYMENT_METHOD), ("paymentMethod", "other")),
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={"paymentMethod": "visa"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)
        status, _, _ = self.request(
            "POST",
            PAYMENT_PATH,
            fields={"paymentMethod": TEST_PAYMENT_METHOD},
            cookie=cookie,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        self.select_payment(cookie)

        for label, fields in (
            ("empty", {}),
            ("short", {"idempotencyKey": "too-short"}),
            (
                "duplicate",
                (("idempotencyKey", "valid-idempotency-key-01"), ("idempotencyKey", "other-key-00000000001")),
            ),
        ):
            with self.subTest(place_order=label):
                status, _, _ = self.request(
                    "POST", PLACE_ORDER_PATH, fields=fields, cookie=cookie
                )
                self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            PLACE_ORDER_PATH,
            fields={"idempotencyKey": "valid-idempotency-key-01"},
            cookie=cookie,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
