from __future__ import annotations

import http.client
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer, digest  # noqa: E402
from store import Store  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"
COMPARE_PATH = "/gp/compare"
COMPARE_ADD_PATH = "/gp/compare/add"
COMPARE_REMOVE_PATH = "/gp/compare/remove"
COMPARE_CLEAR_PATH = "/gp/compare/clear"

T7_ASIN = "B0874XN4D8"
T9_ASIN = "B0CHFSWM2P"
SHEETS_ASIN = "B01M16WBW1"
OKAPI_ASIN = "B0BG6B2D4D"
SANDISK_ASIN = "B08HN37XC1"
SANDISK_1TB_ASIN = "B08GTYFC37"
SANDISK_PRO_ASIN = "B08GV9M64L"
SSD_ASINS = (
    T7_ASIN,
    T9_ASIN,
    SANDISK_ASIN,
    SANDISK_1TB_ASIN,
    SANDISK_PRO_ASIN,
)
SEARCH_BOOK_ASIN = "0345483448"
SEARCH_BOOK_EXACT_ASIN = "B0GRFWYP37"

SPARSE_ASIN = "B07CRG94G3"
UNKNOWN_ASIN = "B000000000"

PDP_PATHS = {
    T7_ASIN: "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8",
    T9_ASIN: "/SAMSUNG-Portable-Professionals-MU-PG1T0B-AM/dp/B0CHFSWM2P",
    SHEETS_ASIN: "/Queen-Size-Piece-Sheet-Set/dp/B01M16WBW1",
    OKAPI_ASIN: "/Safari-Ltd-Okapi/dp/B0BG6B2D4D",
    SANDISK_ASIN: "/SanDisk-2TB-Extreme-Portable-SDSSDE61-2T00-G25/dp/B08HN37XC1",
}

TITLE_FRAGMENTS = {
    T7_ASIN: "samsung t7 portable ssd",
    T9_ASIN: "samsung t9 portable ssd",
    SHEETS_ASIN: "queen size 4 piece sheet set",
    OKAPI_ASIN: "safari ltd. okapi figure",
    SANDISK_ASIN: "sandisk 2tb extreme portable ssd",
    SANDISK_1TB_ASIN: "sandisk 1tb extreme portable ssd",
    SANDISK_PRO_ASIN: "sandisk 1tb extreme pro portable ssd",
}

T7_DEFAULT_OPTIONS = {
    "Color": "Titan Gray",
    "Memory Storage Capacity": "1 TB",
}
T7_BLUE_OPTIONS = {
    "Color": "Blue",
    "Memory Storage Capacity": "1 TB",
}

PASSWORD = "Correct-Horse-921"
ACCOUNT_EMAIL = "compare-owner@example.test"

FormFields = Mapping[str, str] | Sequence[tuple[str, str]]


def normalized_text(parts: Sequence[str]) -> str:
    return " ".join("".join(parts).split())


class CompareDocumentParser(HTMLParser):
    """Collect compare-table content and semantic add forms."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.table_parts: list[str] = []
        self.table_images: list[dict[str, str]] = []
        self.table_count = 0
        self._table_depth = 0
        self._form: dict[str, object] | None = None
        self.add_form_asins: list[str] = []
        self.add_forms: list[dict[str, str]] = []
        self.remove_line_ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag == "table":
            self.table_count += 1
            self._table_depth += 1
        elif self._table_depth and tag == "img":
            self.table_images.append(attributes)

        if tag == "form":
            self._form = {"action": attributes.get("action", ""), "inputs": {}}
        elif tag == "input" and self._form is not None:
            inputs = self._form["inputs"]
            assert isinstance(inputs, dict)
            name = attributes.get("name", "")
            if name:
                inputs[name] = attributes.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table_depth:
            self._table_depth -= 1
        if tag == "form" and self._form is not None:
            action = urlsplit(str(self._form["action"])).path
            inputs = self._form["inputs"]
            assert isinstance(inputs, dict)
            if action == COMPARE_ADD_PATH and isinstance(inputs.get("ASIN"), str):
                self.add_form_asins.append(inputs["ASIN"])
                self.add_forms.append(dict(inputs))
            elif action == COMPARE_REMOVE_PATH and isinstance(
                inputs.get("compareLineID"), str
            ):
                self.remove_line_ids.append(inputs["compareLineID"])
            self._form = None

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._table_depth:
            self.table_parts.append(data)

    @property
    def text(self) -> str:
        return normalized_text(self.text_parts)

    @property
    def table_text(self) -> str:
        return normalized_text(self.table_parts)


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class CompareBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        QuietPublicHandler.store = self.store
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
            raise AssertionError(f"invalid session cookie: {cookie!r}")
        return digest(token)

    def anonymous_cookie(self) -> str:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return self.session_cookie(headers)

    @staticmethod
    def parse_document(body: bytes) -> CompareDocumentParser:
        parser = CompareDocumentParser()
        parser.feed(body.decode("utf-8"))
        return parser

    def compare_document(self, cookie: str) -> CompareDocumentParser:
        status, _, body = self.request("GET", COMPARE_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        return self.parse_document(body)

    @staticmethod
    def assert_redirect(
        response: tuple[int, dict[str, list[str]], bytes], location: str = COMPARE_PATH
    ) -> None:
        status, headers, body = response
        if (status, headers.get("location"), body) != (303, [location], b""):
            raise AssertionError(
                f"expected 303 to {location!r}; got status={status}, "
                f"location={headers.get('location')!r}, body={body!r}"
            )

    def compare_mutation(
        self,
        path: str,
        cookie: str,
        fields: FormFields,
        *,
        expected_status: int = 303,
        origin: str | None = "same-origin",
    ) -> bytes:
        response = self.request(
            "POST", path, fields=fields, cookie=cookie, origin=origin
        )
        if expected_status == 303:
            self.assert_redirect(response)
        else:
            self.assertEqual(response[0], expected_status, response[2])
        return response[2]

    def add(
        self,
        cookie: str,
        asin: str,
        *,
        options: Mapping[str, str] | None = None,
        expected_status: int = 303,
    ) -> None:
        fields = {"ASIN": asin}
        fields.update(
            {
                f"option.{label}": value
                for label, value in (options or {}).items()
            }
        )
        self.compare_mutation(
            COMPARE_ADD_PATH,
            cookie,
            fields,
            expected_status=expected_status,
        )

    def remove(
        self, cookie: str, compare_line_id: str, *, expected_status: int = 303
    ) -> None:
        self.compare_mutation(
            COMPARE_REMOVE_PATH,
            cookie,
            {"compareLineID": compare_line_id},
            expected_status=expected_status,
        )

    def clear(self, cookie: str) -> None:
        self.compare_mutation(COMPARE_CLEAR_PATH, cookie, {})

    def assert_product_order(
        self, parser: CompareDocumentParser, expected_asins: Sequence[str]
    ) -> None:
        haystack = parser.table_text.casefold()
        cursor = -1
        for asin in expected_asins:
            fragment = TITLE_FRAGMENTS[asin]
            position = haystack.find(fragment, cursor + 1)
            self.assertGreater(
                position,
                cursor,
                f"{asin} ({fragment!r}) was not rendered in compare-column order",
            )
            cursor = position

    def register(self, cookie: str, email: str = ACCOUNT_EMAIL) -> str:
        status, headers, body = self.request(
            "POST",
            "/ap/register",
            fields={
                "customerName": "Compare Owner",
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

    def sign_in(self, cookie: str, email: str = ACCOUNT_EMAIL) -> str:
        self.assert_redirect(
            self.request(
                "POST",
                "/ap/signin",
                fields={"email": email},
                cookie=cookie,
            ),
            "/ap/signin?stage=password",
        )
        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={"password": PASSWORD},
            cookie=cookie,
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))
        rotated = self.session_cookie(headers)
        self.assertNotEqual(rotated, cookie)
        return rotated

    def sign_out(self, cookie: str) -> None:
        status, headers, body = self.request(
            "POST", "/ap/signout", fields={}, cookie=cookie
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))

    def test_variant_identity_limit_targeted_remove_and_clear(self) -> None:
        cookie = self.anonymous_cookie()
        session_digest = self.session_digest(cookie)
        self.add(cookie, T7_ASIN)
        self.add(cookie, T7_ASIN, options=T7_BLUE_OPTIONS)
        self.add(cookie, T9_ASIN)
        self.add(cookie, SANDISK_ASIN)

        lines = self.store.compare_items(session_digest)
        self.assertEqual(len(lines), 4)
        self.assertEqual([line["asin"] for line in lines[:2]], [T7_ASIN, T7_ASIN])
        self.assertEqual(
            [line["price_minor"] for line in lines[:2]], [21_999, 26_789]
        )
        self.assertNotEqual(lines[0]["selection_key"], lines[1]["selection_key"])
        self.assertNotEqual(lines[0]["image_path"], lines[1]["image_path"])

        # An exact duplicate is idempotent; a fifth compatible line is rejected.
        self.add(cookie, T7_ASIN, options=T7_BLUE_OPTIONS)
        self.assertEqual(len(self.store.compare_items(session_digest)), 4)
        self.add(cookie, SANDISK_1TB_ASIN, expected_status=409)
        self.assertEqual(len(self.store.compare_items(session_digest)), 4)

        blue_line_id = str(lines[1]["compare_line_id"])
        self.remove(cookie, blue_line_id)
        remaining = self.store.compare_items(session_digest)
        self.assertEqual(len(remaining), 3)
        self.assertEqual(
            [line["selected_options"] for line in remaining if line["asin"] == T7_ASIN],
            [T7_DEFAULT_OPTIONS],
        )
        self.remove(cookie, blue_line_id, expected_status=409)

        self.add(cookie, SANDISK_1TB_ASIN)
        self.assertEqual(len(self.store.compare_items(session_digest)), 4)
        self.clear(cookie)
        self.clear(cookie)
        self.assertEqual(self.store.compare_items(session_digest), [])

    def test_eligibility_malformed_forms_and_same_origin_are_enforced(self) -> None:
        cookie = self.anonymous_cookie()
        self.assertGreater(len(self.store.compare_eligible_asins()), 5)
        self.assertIn(SANDISK_1TB_ASIN, self.store.compare_eligible_asins())
        for asin in (SPARSE_ASIN, UNKNOWN_ASIN):
            with self.subTest(asin=asin):
                self.add(cookie, asin, expected_status=404)

        malformed: tuple[tuple[str, str, FormFields], ...] = (
            ("missing add ASIN", COMPARE_ADD_PATH, {}),
            (
                "duplicate add ASIN",
                COMPARE_ADD_PATH,
                (("ASIN", T7_ASIN), ("ASIN", T9_ASIN)),
            ),
            ("invalid add ASIN", COMPARE_ADD_PATH, {"ASIN": "not-an-asin"}),
            ("missing remove line", COMPARE_REMOVE_PATH, {}),
            ("legacy ASIN removal", COMPARE_REMOVE_PATH, {"ASIN": T7_ASIN}),
            (
                "invalid remove line",
                COMPARE_REMOVE_PATH,
                {"compareLineID": "not-a-line"},
            ),
            ("clear has fields", COMPARE_CLEAR_PATH, {"ASIN": T7_ASIN}),
            (
                "partial options",
                COMPARE_ADD_PATH,
                {"ASIN": T7_ASIN, "option.Color": "Blue"},
            ),
            (
                "extra option",
                COMPARE_ADD_PATH,
                {
                    "ASIN": T7_ASIN,
                    "option.Color": "Blue",
                    "option.Memory Storage Capacity": "1 TB",
                    "option.Price": "$0.01",
                },
            ),
            (
                "client price",
                COMPARE_ADD_PATH,
                {"ASIN": T7_ASIN, "price": "1"},
            ),
            (
                "client title",
                COMPARE_ADD_PATH,
                {"ASIN": T7_ASIN, "title": "forged"},
            ),
        )
        for label, path, fields in malformed:
            with self.subTest(case=label):
                self.compare_mutation(path, cookie, fields, expected_status=400)

        for label, options in (
            (
                "unsupported value",
                {"Color": "Purple", "Memory Storage Capacity": "1 TB"},
            ),
            (
                "unquoted combination",
                {"Color": "Blue", "Memory Storage Capacity": "2 TB"},
            ),
        ):
            with self.subTest(case=label):
                self.add(cookie, T7_ASIN, options=options, expected_status=400)

        self.add(cookie, T7_ASIN)
        self.add(cookie, SHEETS_ASIN, expected_status=409)
        self.assertEqual(
            [line["asin"] for line in self.store.compare_items(self.session_digest(cookie))],
            [T7_ASIN],
        )
        self.clear(cookie)

        for label, origin in (
            ("cross-origin", "https://evil.example"),
            ("missing-origin", None),
        ):
            with self.subTest(case=label):
                self.compare_mutation(
                    COMPARE_ADD_PATH,
                    cookie,
                    {"ASIN": T7_ASIN},
                    expected_status=403,
                    origin=origin,
                )
        self.assertEqual(self.compare_document(cookie).table_count, 0)

    def test_anonymous_sessions_and_opaque_line_mutations_are_isolated(self) -> None:
        first = self.anonymous_cookie()
        second = self.anonymous_cookie()
        self.assertNotEqual(first, second)

        self.add(first, T7_ASIN)
        self.add(first, T9_ASIN)
        first_lines = self.store.compare_items(self.session_digest(first))
        first_page = self.compare_document(first)
        self.assertEqual(first_page.table_count, 1)
        self.assert_product_order(first_page, (T7_ASIN, T9_ASIN))

        second_page = self.compare_document(second)
        self.assertEqual(second_page.table_count, 0)
        self.assertNotIn(TITLE_FRAGMENTS[T7_ASIN], second_page.text.casefold())
        self.add(second, SANDISK_ASIN)
        self.remove(
            second,
            str(first_lines[0]["compare_line_id"]),
            expected_status=409,
        )
        self.assertEqual(
            [line["asin"] for line in self.store.compare_items(self.session_digest(second))],
            [SANDISK_ASIN],
        )
        self.assert_product_order(self.compare_document(first), (T7_ASIN, T9_ASIN))

    def test_registration_and_login_merge_guest_and_account_comparisons(self) -> None:
        guest = self.anonymous_cookie()
        old_digest = self.session_digest(guest)
        self.add(guest, T7_ASIN)
        authenticated = self.register(guest)
        self.assertNotEqual(self.session_digest(authenticated), old_digest)
        self.assertEqual(
            [line["selected_options"] for line in self.store.compare_items(self.session_digest(authenticated))],
            [T7_DEFAULT_OPTIONS],
        )
        old_page = self.compare_document(guest)
        self.assertEqual(old_page.table_count, 0)

        # Account state persists while signed out; a compatible guest list is
        # merged account-first on the next sign-in.
        self.sign_out(authenticated)
        self.assertEqual(self.compare_document(authenticated).table_count, 0)
        returning_guest = self.anonymous_cookie()
        returning_digest = self.session_digest(returning_guest)
        self.add(returning_guest, T7_ASIN, options=T7_BLUE_OPTIONS)
        self.add(returning_guest, T9_ASIN)
        guest_line_ids = {
            str(line["compare_line_id"])
            for line in self.store.compare_items(returning_digest)
        }
        signed_in = self.sign_in(returning_guest)
        merged = self.store.compare_items(self.session_digest(signed_in))
        self.assertEqual([line["asin"] for line in merged], [T7_ASIN, T7_ASIN, T9_ASIN])
        self.assertEqual(
            [line["selected_options"] for line in merged[:2]],
            [T7_DEFAULT_OPTIONS, T7_BLUE_OPTIONS],
        )
        self.assertTrue(
            guest_line_ids.isdisjoint(
                {str(line["compare_line_id"]) for line in merged}
            )
        )
        self.assertEqual(self.compare_document(returning_guest).table_count, 0)

    def test_zero_one_and_multi_product_pages_show_the_expected_semantics(self) -> None:
        cookie = self.anonymous_cookie()
        empty = self.compare_document(cookie)
        self.assertEqual(empty.table_count, 0)
        self.assertIn("compare", empty.text.casefold())
        self.assertTrue(
            any(word in empty.text.casefold() for word in ("add", "choose", "select")),
            empty.text,
        )

        self.add(cookie, T7_ASIN)
        single = self.compare_document(cookie)
        self.assertEqual(single.table_count, 0)
        self.assertIn(TITLE_FRAGMENTS[T7_ASIN], single.text.casefold())
        self.assertIn("Titan Gray", single.text)
        self.assertEqual(len(single.remove_line_ids), 1)
        self.assertRegex(
            single.text.casefold(),
            r"(?:add|choose|select).*(?:another|second|at least 2|at least two)",
        )

        self.add(cookie, T7_ASIN, options=T7_BLUE_OPTIONS)
        self.add(cookie, T9_ASIN)
        table = self.compare_document(cookie)
        self.assertEqual(table.table_count, 1)
        self.assertEqual(len(table.remove_line_ids), 3)

        table_text = table.table_text
        folded = table_text.casefold()
        label_groups = (
            ("selected options",),
            ("price",),
            ("rating", "customer rating"),
            ("reviews", "review count"),
            ("brand",),
            ("category",),
            ("product family",),
            ("availability", "stock"),
            ("ships from", "shipper"),
            ("sold by", "seller"),
            ("delivery",),
            ("shipping",),
            ("returns",),
        )
        for alternatives in label_groups:
            with self.subTest(public_field=alternatives[0]):
                self.assertTrue(
                    any(label in folded for label in alternatives),
                    f"missing compare field label {alternatives!r} in {table_text!r}",
                )

        for expected in (
            T7_ASIN,
            T9_ASIN,
            "$219.99",
            "$267.89",
            "$239.99",
            "Titan Gray",
            "Blue",
            "4.6",
            "2,894",
            "Samsung",
            "Computers & Accessories",
            "External Solid State Drives",
            "In Stock",
            "$7.61",
            "30-day",
        ):
            with self.subTest(evidence=expected):
                self.assertIn(expected.casefold(), folded)

        self.assertGreaterEqual(len(table.table_images), 2)
        for image in table.table_images:
            self.assertTrue(image.get("src", "").startswith("/static/"), image)
            self.assertTrue(image.get("alt", "").strip(), image)

        # Core source-backed PDP attributes are unioned without filling gaps.
        self.assertIn("Digital Storage Capacity", table_text)
        self.assertIn("1 TB", table_text)
        self.assertIn("—", table_text)

    def test_compare_entry_points_follow_dynamic_offer_and_taxonomy_evidence(self) -> None:
        for asin, path in PDP_PATHS.items():
            with self.subTest(surface="rich-pdp", asin=asin):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 200)
                parser = self.parse_document(body)
                self.assertIn(asin, parser.add_form_asins)

        status, _, broader_task_pdp = self.request(
            "GET", f"/dp/{SANDISK_1TB_ASIN}"
        )
        self.assertEqual(status, 200)
        self.assertIn(
            SANDISK_1TB_ASIN,
            self.parse_document(broader_task_pdp).add_form_asins,
        )

        status, _, sparse_pdp = self.request("GET", f"/dp/{SPARSE_ASIN}")
        self.assertEqual(status, 200)
        self.assertIn(SPARSE_ASIN.encode("ascii"), sparse_pdp)
        self.assertNotIn(SPARSE_ASIN, self.parse_document(sparse_pdp).add_form_asins)

        status, _, rich_search = self.request("GET", "/s?k=okapi")
        self.assertEqual(status, 200)
        self.assertIn(OKAPI_ASIN, self.parse_document(rich_search).add_form_asins)

        status, headers, search_card = self.request(
            "GET", "/s?i=books&k=summer+island"
        )
        self.assertEqual(status, 200)
        search_parser = self.parse_document(search_card)
        self.assertIn(SEARCH_BOOK_ASIN, search_parser.add_form_asins)
        cookie = self.session_cookie(headers)
        self.add(cookie, SEARCH_BOOK_ASIN)
        search_line = self.store.compare_items(self.session_digest(cookie))[0]
        self.assertEqual(search_line["asin"], SEARCH_BOOK_ASIN)
        self.assertEqual(search_line["price_minor"], 1_083)
        self.assertEqual(search_line["selected_options"], {})
        self.assertEqual(search_line["reviews"], "50.4K")

        # Abbreviated source copy remains display evidence, not an invented
        # exact zero from the integer-only commerce-offer compatibility column.
        self.add(cookie, SEARCH_BOOK_EXACT_ASIN)
        search_compare = self.compare_document(cookie)
        self.assertIn("Customer reviews50.4K31", search_compare.table_text)

        status, _, sparse_search = self.request("GET", "/s?k=seagate+portable+2tb")
        self.assertEqual(status, 200)
        self.assertIn(SPARSE_ASIN.encode("ascii"), sparse_search)
        self.assertNotIn(SPARSE_ASIN, self.parse_document(sparse_search).add_form_asins)

        status, _, t7_pdp = self.request("GET", PDP_PATHS[T7_ASIN])
        self.assertEqual(status, 200)
        t7_forms = [
            form
            for form in self.parse_document(t7_pdp).add_forms
            if form.get("ASIN") == T7_ASIN
        ]
        self.assertTrue(t7_forms)
        self.assertEqual(t7_forms[0]["option.Color"], "Titan Gray")
        self.assertEqual(t7_forms[0]["option.Memory Storage Capacity"], "1 TB")

    def test_legacy_compare_table_keeps_only_one_current_eligible_family(self) -> None:
        with tempfile.TemporaryDirectory() as legacy_dir:
            database = Path(legacy_dir) / "legacy.sqlite3"
            legacy_store = Store(database, ROOT / "schema.sql", ROOT / "fixtures")
            legacy_store.reset()
            session_digest = "a" * 64
            legacy_store.ensure_session(session_digest)
            conn = sqlite3.connect(database)
            try:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("DROP TABLE compare_items")
                conn.execute(
                    """
                    CREATE TABLE compare_items (
                        session_digest TEXT NOT NULL
                            REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
                        asin TEXT NOT NULL REFERENCES catalog_products(asin),
                        position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 4),
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (session_digest,asin),
                        UNIQUE (session_digest,position)
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO compare_items(session_digest,asin,position,created_at)
                    VALUES (?,?,?,?)
                    """,
                    (
                        (session_digest, T7_ASIN, 1, "2026-07-21T12:00:00Z"),
                        # The old five-ASIN registry allowed cross-family rows.
                        # Source order makes the first eligible family win.
                        (session_digest, SHEETS_ASIN, 2, "2026-07-21T12:00:01Z"),
                        # This catalog offer has no source-backed compare profile.
                        (session_digest, SPARSE_ASIN, 3, "2026-07-21T12:00:02Z"),
                        (session_digest, T9_ASIN, 4, "2026-07-21T12:00:03Z"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            migrated = Store(database, ROOT / "schema.sql", ROOT / "fixtures")
            lines = migrated.compare_items(session_digest)
            self.assertEqual([line["asin"] for line in lines], [T7_ASIN, T9_ASIN])
            self.assertEqual([line["position"] for line in lines], [1, 2])
            self.assertEqual(
                {line["compare"]["family_key"] for line in lines},
                {"computers:external-solid-state-drives"},
            )
            self.assertEqual(lines[0]["selected_options"], T7_DEFAULT_OPTIONS)
            self.assertTrue(
                all(
                    re.fullmatch(r"[A-Za-z0-9_-]{24,128}", str(line["compare_line_id"]))
                    for line in lines
                )
            )
            with migrated.connect() as conn:
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(compare_items)")
                }
                self.assertTrue(
                    {"compare_line_id", "selection_json", "selection_key"}.issubset(
                        columns
                    )
                )
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
