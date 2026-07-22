from __future__ import annotations

import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import wishlist_store as wishlist  # noqa: E402
from home_catalog import load_home_product_catalog  # noqa: E402
from server import PublicHandler, ReusableThreadingHTTPServer, digest  # noqa: E402
from store import PDP_PATH, Store, TARGET_ASIN  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"
PASSWORD = "Correct-Horse-921"
INDEX_PATH = "/hz/wishlist/ls"
INTRO_PATH = "/hz/wishlist/intro"
CHOOSER_PATH = "/hz/wishlist/add"
CREATE_PATH = "/hz/wishlist/create"
RENAME_PATH = "/hz/wishlist/rename"
DELETE_PATH = "/hz/wishlist/delete"
ADD_PATH = "/hz/wishlist/add-item"
REMOVE_PATH = "/hz/wishlist/remove-item"
MOVE_PATH = "/hz/wishlist/move-to-cart"
CART_PATH = "/gp/cart/view.html"

FormFields = Mapping[str, str] | Sequence[tuple[str, str]]


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class WishlistBackendTests(unittest.TestCase):
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
        self.server = ReusableThreadingHTTPServer(
            ("127.0.0.1", 0), QuietPublicHandler
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
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
        cookie: str = "",
        origin: str | None = "same-origin",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        if fields is not None and raw_body is not None:
            raise ValueError("fields and raw_body are mutually exclusive")
        body = (
            urlencode(fields, doseq=True).encode("utf-8")
            if fields is not None
            else raw_body
        )
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Content-Length"] = str(len(body))
        if method == "POST" and origin is not None:
            headers["Origin"] = (
                self.same_origin if origin == "same-origin" else origin
            )
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
        raise AssertionError("response did not set a session cookie")

    @staticmethod
    def session_digest(cookie: str) -> str:
        name, token = cookie.split("=", 1)
        if name != SESSION_COOKIE or not token:
            raise AssertionError("invalid session cookie")
        return digest(token)

    def anonymous_cookie(self) -> str:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return self.session_cookie(headers)

    def register(self, email: str) -> str:
        cookie = self.anonymous_cookie()
        status, headers, body = self.request(
            "POST",
            "/ap/register",
            fields={
                "customerName": "Wishlist Owner",
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
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify",
            fields={"code": messages[0]["verification_code"]},
            cookie=cookie,
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))
        return self.session_cookie(headers)

    def assert_redirect(
        self,
        response: tuple[int, dict[str, list[str]], bytes],
        location: str,
    ) -> None:
        status, headers, body = response
        self.assertEqual((status, headers.get("location"), body), (303, [location], b""))

    def post(
        self,
        path: str,
        cookie: str,
        fields: FormFields,
        *,
        expected_location: str | None = None,
        origin: str | None = "same-origin",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        response = self.request(
            "POST", path, fields=fields, cookie=cookie, origin=origin
        )
        if expected_location is not None:
            self.assert_redirect(response, expected_location)
        return response

    def default_list(self, cookie: str) -> dict[str, object]:
        return wishlist.default_list_for_session(
            self.store, self.session_digest(cookie)
        )

    @staticmethod
    def chooser_query(asin: str, options: Mapping[str, str]) -> str:
        pairs = [("ASIN", asin)] + [
            (f"option.{label}", value) for label, value in options.items()
        ]
        return CHOOSER_PATH + "?" + urlencode(pairs)

    def test_intro_is_public_and_protected_routes_preserve_safe_continuation(self) -> None:
        status, _, intro = self.request("GET", INTRO_PATH)
        self.assertEqual(status, 200)
        self.assertIn(b'data-wishlist-page="intro"', intro)
        self.assertIn(b"Lists &amp; Registries", intro)
        self.assertIn(b"lists-intro/desktop-banner.jpg", intro)

        status, headers, body = self.request("GET", INDEX_PATH)
        self.assertEqual((status, body), (303, b""))
        location = urlsplit(headers["location"][0])
        self.assertEqual(location.path, "/ap/signin")
        self.assertEqual(
            parse_qs(location.query)["openid.return_to"], [INDEX_PATH]
        )

        defaults = self.store.default_product_options(TARGET_ASIN)
        target = self.chooser_query(TARGET_ASIN, defaults)
        status, headers, _ = self.request("GET", target)
        self.assertEqual(status, 303)
        continuation = parse_qs(urlsplit(headers["location"][0]).query)[
            "openid.return_to"
        ][0]
        self.assertEqual(continuation, target)

    def test_list_crud_is_account_owned_and_never_deletes_the_last_list(self) -> None:
        owner = self.register("wishlist-crud-owner@example.test")
        status, _, body = self.request("GET", INDEX_PATH, cookie=owner)
        self.assertEqual(status, 200)
        self.assertIn(b'data-wishlist-page="index"', body)
        self.assertIn(b"Shopping List", body)

        status, headers, _ = self.post(
            CREATE_PATH, owner, {"listName": "Birthday Ideas"}
        )
        self.assertEqual(status, 303)
        created_location = headers["location"][0]
        created_query = parse_qs(urlsplit(created_location).query)
        created_id = created_query["listID"][0]
        self.assertEqual(created_query["status"], ["created"])

        self.post(
            RENAME_PATH,
            owner,
            {"listID": created_id, "listName": "Books to Read"},
            expected_location=(
                INDEX_PATH
                + "?"
                + urlencode({"listID": created_id, "status": "renamed"})
            ),
        )
        status, _, detail = self.request(
            "GET", INDEX_PATH + "?" + urlencode({"listID": created_id}), cookie=owner
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Books to Read", detail)

        other = self.register("wishlist-crud-other@example.test")
        status, _, _ = self.request(
            "GET", INDEX_PATH + "?" + urlencode({"listID": created_id}), cookie=other
        )
        self.assertEqual(status, 404)
        status, _, _ = self.post(
            DELETE_PATH, other, {"listID": created_id}
        )
        self.assertEqual(status, 404)

        self.post(
            DELETE_PATH,
            owner,
            {"listID": created_id},
            expected_location=INDEX_PATH + "?status=deleted",
        )
        only_list = self.default_list(owner)
        status, _, _ = self.post(
            DELETE_PATH, owner, {"listID": str(only_list["list_id"])}
        )
        self.assertEqual(status, 409)

    def test_pdp_chooser_and_add_keep_complete_variant_identity(self) -> None:
        cookie = self.register("wishlist-variant@example.test")
        defaults = self.store.default_product_options(TARGET_ASIN)
        status, _, pdp = self.request("GET", PDP_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b'action="/hz/wishlist/add"', pdp)
        self.assertIn(b'name="option.Color" value="Titan Gray"', pdp)
        self.assertNotIn(b'name="price_minor"', pdp)

        target = self.chooser_query(TARGET_ASIN, defaults)
        status, _, chooser = self.request("GET", target, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b'data-wishlist-page="add-chooser"', chooser)
        self.assertIn(b"Titan Gray", chooser)

        list_id = str(self.default_list(cookie)["list_id"])
        add_fields = {
            "listID": list_id,
            "ASIN": TARGET_ASIN,
            **{f"option.{label}": value for label, value in defaults.items()},
        }
        expected_added = INDEX_PATH + "?" + urlencode(
            {"listID": list_id, "status": "added"}
        )
        self.post(ADD_PATH, cookie, add_fields, expected_location=expected_added)
        expected_duplicate = INDEX_PATH + "?" + urlencode(
            {"listID": list_id, "status": "already-added"}
        )
        self.post(ADD_PATH, cookie, add_fields, expected_location=expected_duplicate)

        blue = {**defaults, "Color": "Blue"}
        blue_fields = {
            "listID": list_id,
            "ASIN": TARGET_ASIN,
            **{f"option.{label}": value for label, value in blue.items()},
        }
        self.post(ADD_PATH, cookie, blue_fields, expected_location=expected_added)
        status, _, detail = self.request(
            "GET", INDEX_PATH + "?" + urlencode({"listID": list_id}), cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertIn(b"$267.89", detail)
        self.assertIn(b"$219.99", detail)
        self.assertEqual(detail.count(f'data-asin="{TARGET_ASIN}"'.encode()), 2)

    def test_browse_only_items_save_but_cannot_be_forged_into_cart(self) -> None:
        cookie = self.register("wishlist-browse-only@example.test")
        list_id = str(self.default_list(cookie)["list_id"])
        home_catalog = load_home_product_catalog(ROOT / "fixtures")
        browse_only_asin = next(
            asin
            for asin in home_catalog
            if self.store.commerce_offer(asin) is None
        )
        self.post(
            ADD_PATH,
            cookie,
            {"listID": list_id, "ASIN": browse_only_asin},
            expected_location=(
                INDEX_PATH
                + "?"
                + urlencode({"listID": list_id, "status": "added"})
            ),
        )
        detail = wishlist.list_for_session(
            self.store, self.session_digest(cookie), list_id
        )
        item = detail["items"][0]
        self.assertFalse(item["available_to_cart"])
        status, _, page = self.request(
            "GET", INDEX_PATH + "?" + urlencode({"listID": list_id}), cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Offer unavailable", page)
        status, _, _ = self.post(
            MOVE_PATH,
            cookie,
            {
                "listID": list_id,
                "itemID": str(item["item_id"]),
                "quantity": "1",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(self.store.cart_count(self.session_digest(cookie)), 0)

    def test_move_to_cart_requotes_server_side_and_removes_only_owned_item(self) -> None:
        cookie = self.register("wishlist-cart@example.test")
        list_id = str(self.default_list(cookie)["list_id"])
        defaults = self.store.default_product_options(TARGET_ASIN)
        self.post(
            ADD_PATH,
            cookie,
            {
                "listID": list_id,
                "ASIN": TARGET_ASIN,
                **{
                    f"option.{label}": value
                    for label, value in defaults.items()
                },
            },
        )
        detail = wishlist.list_for_session(
            self.store, self.session_digest(cookie), list_id
        )
        item = detail["items"][0]
        self.post(
            MOVE_PATH,
            cookie,
            {
                "listID": list_id,
                "itemID": str(item["item_id"]),
                "quantity": "1",
            },
            expected_location=CART_PATH,
        )
        remaining = wishlist.list_for_session(
            self.store, self.session_digest(cookie), list_id
        )
        self.assertEqual(remaining["items"], [])
        cart = self.store.cart(self.session_digest(cookie))
        self.assertEqual(len(cart), 1)
        self.assertEqual(cart[0]["asin"], TARGET_ASIN)
        self.assertEqual(cart[0]["selected_options"], defaults)

    def test_mutations_reject_cross_origin_duplicate_and_price_fields(self) -> None:
        cookie = self.register("wishlist-strict@example.test")
        list_id = str(self.default_list(cookie)["list_id"])
        status, _, _ = self.post(
            CREATE_PATH,
            cookie,
            {"listName": "Cross origin"},
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        status, _, _ = self.post(
            CREATE_PATH,
            cookie,
            [("listName", "One"), ("listName", "Two")],
        )
        self.assertEqual(status, 400)
        defaults = self.store.default_product_options(TARGET_ASIN)
        forged = {
            "listID": list_id,
            "ASIN": TARGET_ASIN,
            "price_minor": "1",
            **{f"option.{label}": value for label, value in defaults.items()},
        }
        status, _, _ = self.post(ADD_PATH, cookie, forged)
        self.assertEqual(status, 400)
        self.assertEqual(
            wishlist.list_for_session(
                self.store, self.session_digest(cookie), list_id
            )["items"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
