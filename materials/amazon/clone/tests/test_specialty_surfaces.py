from __future__ import annotations

import http.client
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer, digest  # noqa: E402
from store import Store  # noqa: E402


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class SpecialtySurfaceTests(unittest.TestCase):
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
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        fields: dict[str, str] | None = None,
        raw_body: bytes | None = None,
        cookie: str = "",
        origin: str | None = "same",
        content_type: str = "application/x-www-form-urlencoded",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        body = raw_body
        if fields is not None:
            body = urlencode(fields).encode("utf-8")
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = content_type
            headers["Content-Length"] = str(len(body))
        if method == "POST" and origin is not None:
            headers["Origin"] = (
                f"http://{self.host}:{self.port}" if origin == "same" else origin
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
    def cookie_from(headers: dict[str, list[str]]) -> str:
        return headers["set-cookie"][-1].split(";", 1)[0]

    def anonymous_cookie(self) -> str:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return self.cookie_from(headers)

    @staticmethod
    def location(headers: dict[str, list[str]]) -> str:
        return headers["location"][0]

    def test_specialty_landings_are_reachable_and_video_stays_a_placeholder(self) -> None:
        expectations = {
            "/gift-cards/b/?ie=UTF8&node=2238192011": (
                'data-navigation-page="gift-cards"',
                "Shop gifts by department",
                'name="design"',
                'name="amount"',
                "/gc/redeem/",
            ),
            "/b/?_encoding=UTF8&node=12766669011": (
                'data-navigation-page="sell"',
                "Sell on Amazon",
                "Create an account",
                'action="/b/sell/draft"',
            ),
            "/gp/browse.html?node=16115931011": (
                'data-navigation-page="registry"',
                "Find a registry",
                "Create a registry",
                'action="/registry/create"',
            ),
            "/Amazon-Video/b/?node=2858778011": (
                'data-navigation-page="prime-video"',
                "Video service is outside this shopping clone",
                "No streaming service connected",
            ),
        }
        for path, markers in expectations.items():
            with self.subTest(path=path):
                status, _, payload = self.request("GET", path)
                self.assertEqual(status, 200)
                page = payload.decode("utf-8")
                for marker in markers:
                    self.assertIn(marker, page)

        status, _, gift_payload = self.request("GET", "/gift-cards/b/")
        self.assertEqual(status, 200)
        gift_page = gift_payload.decode("utf-8")
        for forbidden in (
            'name="cardNumber"',
            'name="expiration"',
            'name="securityCode"',
            'name="cvv"',
        ):
            self.assertNotIn(forbidden, gift_page)

        status, _, _ = self.request("GET", "/Amazon-Video/detail/demo-title")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/Amazon-Video/play/demo-title")
        self.assertEqual(status, 404)

    def test_gift_preview_is_strict_and_owned_by_the_creating_session(self) -> None:
        owner_cookie = self.anonymous_cookie()
        status, headers, _ = self.request(
            "POST",
            "/gift-cards/purchase-preview",
            fields={
                "design": "birthday",
                "amount": "100",
                "recipientKind": "gift",
            },
            cookie=owner_cookie,
        )
        self.assertEqual(status, 303)
        location = self.location(headers)
        self.assertRegex(location, r"^/gift-cards/purchase-preview\?previewID=[1-9][0-9]*$")

        status, _, payload = self.request("GET", location, cookie=owner_cookie)
        self.assertEqual(status, 200)
        page = payload.decode("utf-8")
        self.assertIn("$100.00", page)
        self.assertIn("Preview only — not purchased", page)
        self.assertIn("no payment", page.lower())

        other_cookie = self.anonymous_cookie()
        status, _, _ = self.request("GET", location, cookie=other_cookie)
        self.assertEqual(status, 404)

        status, _, _ = self.request(
            "POST",
            "/gift-cards/purchase-preview",
            fields={
                "design": "classic",
                "amount": "50",
                "recipientKind": "self",
                "cardNumber": "4111111111111111",
            },
            cookie=owner_cookie,
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            "/gift-cards/purchase-preview",
            fields={
                "design": "classic",
                "amount": "50",
                "recipientKind": "self",
            },
            cookie=owner_cookie,
            origin="https://attacker.example",
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "GET", location + "&unexpected=1", cookie=owner_cookie
        )
        self.assertEqual(status, 404)

    def test_redeem_stores_only_a_fingerprint_and_never_enumerates_codes(self) -> None:
        cookie = self.anonymous_cookie()
        raw_codes = ("FICTION-AAA-100", "FICTION-BBB-200")
        for code in raw_codes:
            status, headers, _ = self.request(
                "POST",
                "/gc/redeem/",
                fields={"claimCode": code},
                cookie=cookie,
            )
            self.assertEqual(status, 303)
            self.assertEqual(
                self.location(headers), "/gc/balance/?status=not-applied"
            )
            status, _, payload = self.request(
                "GET", self.location(headers), cookie=cookie
            )
            self.assertEqual(status, 200)
            page = payload.decode("utf-8")
            self.assertIn("No balance was applied.", page)
            self.assertNotIn(code, page)

        with self.store.connect() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(specialty_gift_redemption_attempts)"
                )
            }
            rows = connection.execute(
                "SELECT code_fingerprint,status FROM specialty_gift_redemption_attempts ORDER BY redemption_id"
            ).fetchall()
        self.assertNotIn("claim_code", columns)
        self.assertNotIn("raw_code", columns)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["code_fingerprint"], rows[1]["code_fingerprint"])
        for row in rows:
            self.assertRegex(str(row["code_fingerprint"]), r"^[0-9a-f]{64}$")
            self.assertEqual(row["status"], "NOT_APPLIED")
            self.assertNotIn(str(row["code_fingerprint"]), raw_codes)

        status, _, payload = self.request(
            "POST",
            "/gc/redeem/",
            fields={"claimCode": "short"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertNotIn("exists", payload.decode("utf-8").lower())

    def test_sell_draft_is_validated_persisted_and_session_owned(self) -> None:
        owner_cookie = self.anonymous_cookie()
        fields = {
            "title": "Handmade reading lamp",
            "category": "home",
            "condition": "like-new",
            "price": "29.99",
            "quantity": "2",
            "description": "A local listing draft for validation.",
        }
        status, headers, _ = self.request(
            "POST", "/b/sell/draft", fields=fields, cookie=owner_cookie
        )
        self.assertEqual(status, 303)
        location = self.location(headers)
        self.assertRegex(location, r"^/b/sell/draft\?draftID=[1-9][0-9]*$")
        status, _, payload = self.request("GET", location, cookie=owner_cookie)
        self.assertEqual(status, 200)
        page = payload.decode("utf-8")
        self.assertIn("Handmade reading lamp", page)
        self.assertIn("$59.98 before any hypothetical fees", page)
        self.assertIn("not published", page.lower())

        other_cookie = self.anonymous_cookie()
        status, _, _ = self.request("GET", location, cookie=other_cookie)
        self.assertEqual(status, 404)

        bad_fields = {**fields, "price": "5000.01"}
        status, _, _ = self.request(
            "POST", "/b/sell/draft", fields=bad_fields, cookie=owner_cookie
        )
        self.assertEqual(status, 400)
        duplicate_body = urlencode(fields).encode("utf-8") + b"&title=Duplicate"
        status, _, _ = self.request(
            "POST",
            "/b/sell/draft",
            raw_body=duplicate_body,
            cookie=owner_cookie,
        )
        self.assertEqual(status, 400)

        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT session_digest,title,status FROM specialty_seller_drafts"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], fields["title"])
        self.assertEqual(rows[0]["status"], "LOCAL_DRAFT")
        self.assertEqual(
            rows[0]["session_digest"], digest(owner_cookie.split("=", 1)[1])
        )

    def test_registry_search_never_exposes_another_sessions_draft(self) -> None:
        owner_cookie = self.anonymous_cookie()
        private_title = "Quasar Quiet Wedding Picks"
        fields = {
            "registryType": "wedding",
            "ownerName": "Avery Example",
            "registryName": private_title,
            "eventDate": "2030-06-14",
        }
        status, headers, _ = self.request(
            "POST", "/registry/create", fields=fields, cookie=owner_cookie
        )
        self.assertEqual(status, 303)
        detail_location = self.location(headers)
        status, _, payload = self.request(
            "GET", detail_location, cookie=owner_cookie
        )
        self.assertEqual(status, 200)
        self.assertIn(private_title, payload.decode("utf-8"))

        owner_search = "/registry/search?" + urlencode({"query": "Quasar Quiet"})
        status, _, payload = self.request("GET", owner_search, cookie=owner_cookie)
        self.assertEqual(status, 200)
        self.assertIn(private_title, payload.decode("utf-8"))

        other_cookie = self.anonymous_cookie()
        status, _, _ = self.request("GET", detail_location, cookie=other_cookie)
        self.assertEqual(status, 404)
        status, _, payload = self.request("GET", owner_search, cookie=other_cookie)
        self.assertEqual(status, 200)
        self.assertNotIn(private_title, payload.decode("utf-8"))

        demo_search = "/registry/search?" + urlencode({"query": "Welcome Home"})
        status, _, payload = self.request("GET", demo_search, cookie=other_cookie)
        self.assertEqual(status, 200)
        page = payload.decode("utf-8")
        self.assertIn("Welcome Home Picks", page)
        self.assertIn("Local demo result — no personal data", page)

        status, _, _ = self.request(
            "GET", "/registry/search?query=Quasar&query=Quiet", cookie=owner_cookie
        )
        self.assertEqual(status, 400)

    def test_reset_cascades_specialty_session_state(self) -> None:
        cookie = self.anonymous_cookie()
        status, _, _ = self.request(
            "POST",
            "/gift-cards/purchase-preview",
            fields={
                "design": "classic",
                "amount": "25",
                "recipientKind": "self",
            },
            cookie=cookie,
        )
        self.assertEqual(status, 303)
        self.store.reset()
        with self.store.connect() as connection:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM specialty_gift_card_previews"
            ).fetchone()[0]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
