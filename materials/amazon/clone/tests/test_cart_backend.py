from __future__ import annotations

import http.client
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer, digest  # noqa: E402
from store import Store  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"

ADD_PATH = "/gp/cart/add.html"
UPDATE_PATH = "/gp/cart/update.html"
DELETE_PATH = "/gp/cart/delete.html"
SAVE_PATH = "/gp/cart/save-for-later.html"
MOVE_PATH = "/gp/cart/move-to-cart.html"
CART_PATH = "/gp/cart/view.html"

# Frozen ranking/catalog offer, direct homepage PDP offers, and a deliberately
# incomplete homepage card. These constants make the evidence boundary explicit.
FIXTURE_ASIN = "B08GTYFC37"
SHEETS_ASIN = "B01M16WBW1"
OKAPI_ASIN = "B0BG6B2D4D"
BOOK_ASIN = "168281808X"
BEAUTY_ASIN = "B074PVTPBW"
INSTANT_POT_ASIN = "B00FLYWNYQ"
JANSPORT_ASIN = "B07K74LDCH"
AIR_FILTER_ASIN = "B088BZTYFP"
DIRECT_PRICE_ASIN = "B08HN37XC1"
T7_ASIN = "B0874XN4D8"
T9_ASIN = "B0CHFSWM2P"
SPARSE_ASIN = "B07CRG94G3"
UNKNOWN_ASIN = "B000000000"

EXPECTED_PRICES = {
    FIXTURE_ASIN: 18_999,
    SHEETS_ASIN: 2_124,
    OKAPI_ASIN: 1_299,
    BOOK_ASIN: 1_749,
    BEAUTY_ASIN: 1_299,
    INSTANT_POT_ASIN: 10_396,
    JANSPORT_ASIN: 5_763,
    AIR_FILTER_ASIN: 2_785,
    # Direct PDP evidence supersedes the older 30,799-cent search snapshot.
    DIRECT_PRICE_ASIN: 31_699,
}

PASSWORD = "Correct-Horse-921"
ACCOUNT_EMAIL = "cart-owner@example.test"

FormFields = Mapping[str, str] | Sequence[tuple[str, str]]


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class CartBackendTests(unittest.TestCase):
    """End-to-end HTTP contract for the core local marketplace cart.

    Successful mutations use POST-Redirect-GET (303 to ``CART_PATH``).
    Malformed forms are 400, cross-origin forms are 403, products without a
    verified commerce offer are 404, and valid operations against the wrong
    active/saved line state are 409.
    """

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

    def mutate(
        self,
        path: str,
        cookie: str,
        fields: FormFields,
        *,
        expected_status: int = 303,
        origin: str | None = "same-origin",
    ) -> tuple[dict[str, list[str]], bytes]:
        status, headers, body = self.request(
            "POST",
            path,
            fields=fields,
            cookie=cookie,
            origin=origin,
        )
        self.assertEqual(status, expected_status, (path, body.decode("utf-8", errors="replace")))
        if expected_status == 303:
            self.assertEqual(headers.get("location"), [CART_PATH])
            self.assertEqual(body, b"")
        return headers, body

    def add(self, cookie: str, asin: str, quantity: int | str = 1) -> None:
        self.mutate(ADD_PATH, cookie, {"ASIN": asin, "quantity": str(quantity)})

    def active_lines(self, cookie: str) -> dict[str, dict[str, object]]:
        lines = self.store.cart(self.session_digest(cookie))
        return {str(line["asin"]): line for line in lines}

    def saved_lines(self, cookie: str) -> dict[str, dict[str, object]]:
        lines = self.store.saved_cart(self.session_digest(cookie))
        return {str(line["asin"]): line for line in lines}

    def lines_for_asin(
        self, cookie: str, asin: str, *, saved: bool = False
    ) -> list[dict[str, object]]:
        rows = (
            self.store.saved_cart(self.session_digest(cookie))
            if saved
            else self.store.cart(self.session_digest(cookie))
        )
        return [row for row in rows if str(row["asin"]) == asin]

    def line_id(self, cookie: str, asin: str, *, saved: bool = False) -> str:
        rows = self.lines_for_asin(cookie, asin, saved=saved)
        self.assertEqual(len(rows), 1, (asin, rows))
        return str(rows[0]["line_id"])

    def register(self, cookie: str, *, email: str = ACCOUNT_EMAIL) -> str:
        status, headers, body = self.request(
            "POST",
            "/ap/register",
            fields={
                "customerName": "Cart Owner",
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

    def sign_in(self, cookie: str, *, email: str = ACCOUNT_EMAIL) -> str:
        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={"email": email},
            cookie=cookie,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/signin?stage=password"], b""),
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
        self.assertTrue(
            any(
                value.startswith(f"{SESSION_COOKIE}=") and "Max-Age=0" in value
                for value in headers.get("set-cookie", [])
            )
        )

    def test_fixture_and_direct_pdp_offers_are_addable_at_verified_prices(self) -> None:
        cookie = self.anonymous_cookie()
        for asin in EXPECTED_PRICES:
            with self.subTest(asin=asin):
                self.add(cookie, asin)

        lines = self.active_lines(cookie)
        self.assertEqual(set(lines), set(EXPECTED_PRICES))
        for asin, expected_price in EXPECTED_PRICES.items():
            with self.subTest(asin=asin, field="price_minor"):
                self.assertEqual(lines[asin]["price_minor"], expected_price)
                self.assertEqual(lines[asin]["quantity"], 1)

    def test_book_hardcover_quote_persists_book_image_price_and_selected_format(self) -> None:
        cookie = self.anonymous_cookie()
        self.mutate(
            ADD_PATH,
            cookie,
            {"ASIN": BOOK_ASIN, "quantity": "1", "option.Format": "Hardcover"},
        )
        line = self.active_lines(cookie)[BOOK_ASIN]
        self.assertEqual(line["price_minor"], 1_749)
        self.assertEqual(line["selected_options"], {"Format": "Hardcover"})
        self.assertEqual(
            line["image_path"],
            "/static/assets/source-current/2026-07-21/pdp-books/168281808X/main.jpg",
        )
        self.assertIn("Threshing Day", str(line["title"]))

        status, _, book_html = self.request("GET", f"/dp/{BOOK_ASIN}", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"There are 0 customer reviews", book_html)
        self.assertNotIn(b'class="pdp-rating"', book_html)
        self.assertIn(
            b"/static/assets/source-current/2026-07-21/pdp-books/168281808X/main.jpg",
            book_html,
        )

        self.mutate(
            ADD_PATH,
            cookie,
            {"ASIN": BOOK_ASIN, "quantity": "1", "option.Format": "Kindle"},
            expected_status=400,
        )

    def test_beauty_size_quotes_persist_the_selected_price_and_image(self) -> None:
        cookie = self.anonymous_cookie()
        for size, expected_price in (
            ("36 Count (Pack of 1)", 1_299),
            ("75 Count", 1_829),
        ):
            with self.subTest(size=size):
                self.mutate(
                    ADD_PATH,
                    cookie,
                    {"ASIN": BEAUTY_ASIN, "quantity": "1", "option.Size": size},
                )
                line = self.active_lines(cookie)[BEAUTY_ASIN]
                self.assertEqual(line["selected_options"], {"Size": size})
                self.assertEqual(line["price_minor"], expected_price)
                self.assertEqual(
                    line["image_path"],
                    "/static/assets/source-current/2026-07-21/"
                    "pdp-beauty/B074PVTPBW/main.jpg",
                )

    def test_latest_rich_pdp_quotes_persist_and_uncaptured_combinations_fail(self) -> None:
        cookie = self.anonymous_cookie()
        selections = {
            INSTANT_POT_ASIN: ({"Size": "3 Quarts"}, 8_999),
            JANSPORT_ASIN: ({"Color": "Blue Dusk", "Size": "One Size"}, 4_999),
            AIR_FILTER_ASIN: (
                {"Pattern Name": "16x20x1", "Style": "Merv 5"},
                2_785,
            ),
        }
        for asin, (selection, expected_price) in selections.items():
            with self.subTest(asin=asin):
                self.mutate(
                    ADD_PATH,
                    cookie,
                    {
                        "ASIN": asin,
                        "quantity": "1",
                        **{
                            f"option.{label}": value
                            for label, value in selection.items()
                        },
                    },
                )
                line = self.active_lines(cookie)[asin]
                self.assertEqual(line["selected_options"], selection)
                self.assertEqual(line["price_minor"], expected_price)

        for unavailable in (
            {"Pattern Name": "16x20x1", "Style": "Merv 11"},
            {"Pattern Name": "12x12x1", "Style": "Merv 8"},
        ):
            with self.subTest(unavailable=unavailable):
                self.mutate(
                    ADD_PATH,
                    cookie,
                    {
                        "ASIN": AIR_FILTER_ASIN,
                        "quantity": "1",
                        **{
                            f"option.{label}": value
                            for label, value in unavailable.items()
                        },
                    },
                    expected_status=400,
                )

    def test_source_backed_options_are_validated_persisted_and_rendered(self) -> None:
        self.assertEqual(
            self.store.default_product_options(T7_ASIN),
            {"Color": "Titan Gray", "Memory Storage Capacity": "1 TB"},
        )
        self.assertEqual(
            self.store.default_product_options(T9_ASIN),
            {"Digital Storage Capacity": "1 TB", "Color": "Black"},
        )
        self.assertEqual(
            self.store.default_product_options(DIRECT_PRICE_ASIN),
            {"Style": "Old Model", "Capacity": "2TB", "Color": "Black"},
        )

        cookie = self.anonymous_cookie()
        selected = {"Style": "Old Model", "Capacity": "2TB", "Color": "Monterey"}
        self.mutate(
            ADD_PATH,
            cookie,
            {
                "ASIN": DIRECT_PRICE_ASIN,
                "quantity": "2",
                **{f"option.{label}": value for label, value in selected.items()},
            },
        )
        line = self.active_lines(cookie)[DIRECT_PRICE_ASIN]
        self.assertEqual(line["selected_options"], selected)
        self.assertEqual(line["price_minor"], 32_999)
        self.assertEqual(
            line["image_path"],
            "/static/assets/source-current/2026-07-21/pdp-home/"
            "B08HN37XC1/color-monterey.jpg",
        )
        self.assertEqual(line["quantity"], 2)

        status, _, cart_html = self.request("GET", CART_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        for label, value in selected.items():
            self.assertIn(f"<dt>{label}:</dt><dd>{value}</dd>".encode(), cart_html)

    def test_t7_blue_quote_overrides_cart_and_admin_price_and_image(self) -> None:
        cookie = self.anonymous_cookie()
        selected = {"Color": "Blue", "Memory Storage Capacity": "1 TB"}
        self.mutate(
            ADD_PATH,
            cookie,
            {
                "ASIN": T7_ASIN,
                "quantity": "1",
                **{f"option.{label}": value for label, value in selected.items()},
            },
        )
        line = self.active_lines(cookie)[T7_ASIN]
        self.assertEqual(line["selected_options"], selected)
        self.assertEqual(line["price_minor"], 26_789)
        self.assertEqual(
            line["image_path"],
            "/static/assets/source-current/2026-07-21/pdp-t7/gallery-09.jpg",
        )

        state_line = next(
            row
            for row in self.store.normalized_state()["carts"]
            if row["session_digest"] == self.session_digest(cookie)
            and row["asin"] == T7_ASIN
        )
        self.assertEqual(state_line["selected_options"], selected)
        self.assertEqual(state_line["price_minor"], 26_789)
        self.assertEqual(state_line["image_path"], line["image_path"])

    def test_legacy_empty_option_selection_reads_as_server_default_quote(self) -> None:
        cookie = self.anonymous_cookie()
        session_digest = self.session_digest(cookie)
        self.store.add_cart_item(session_digest, T7_ASIN, 1)
        with self.store.connect() as conn:
            conn.execute(
                """
                UPDATE cart_lines SET selection_json='{}'
                WHERE asin=? AND cart_id=(
                    SELECT cart_id FROM carts WHERE session_digest=?
                )
                """,
                (T7_ASIN, session_digest),
            )

        line = self.store.cart(session_digest)[0]
        self.assertEqual(
            line["selected_options"],
            {"Color": "Titan Gray", "Memory Storage Capacity": "1 TB"},
        )
        self.assertEqual(line["price_minor"], 21_999)
        state_line = self.store.normalized_state()["carts"][0]
        self.assertEqual(state_line["selected_options"], line["selected_options"])
        self.assertEqual(state_line["price_minor"], line["price_minor"])

    def test_partial_forged_and_non_evidenced_option_fields_are_rejected(self) -> None:
        cookie = self.anonymous_cookie()
        malformed = (
            {
                "ASIN": DIRECT_PRICE_ASIN,
                "quantity": "1",
                "option.Style": "New Model",
            },
            {
                "ASIN": DIRECT_PRICE_ASIN,
                "quantity": "1",
                "option.Style": "Imaginary Model",
                "option.Capacity": "4TB",
                "option.Color": "Sky Blue",
            },
            {
                "ASIN": DIRECT_PRICE_ASIN,
                "quantity": "1",
                "option.Style": "New Model",
                "option.Capacity": "4TB",
                "option.Color": "Sky Blue",
            },
            {
                "ASIN": T9_ASIN,
                "quantity": "1",
                "option.Digital Storage Capacity": "4 TB",
                "option.Color": "Gray",
            },
            {
                "ASIN": T7_ASIN,
                "quantity": "1",
                "option.Color": "Titan Gray",
                "option.Memory Storage Capacity": "2 TB",
            },
            {
                "ASIN": OKAPI_ASIN,
                "quantity": "1",
                "option.Color": "Brown",
            },
        )
        for fields in malformed:
            with self.subTest(fields=fields):
                self.mutate(ADD_PATH, cookie, fields, expected_status=400)
        self.assertEqual(self.active_lines(cookie), {})

    def test_pre_option_database_is_migrated_without_rebuilding_user_tables(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy-options.sqlite3"
        legacy_schema = (ROOT / "schema.sql").read_text(encoding="utf-8").replace(
            "    selection_json TEXT NOT NULL DEFAULT '{}',\n", ""
        )
        legacy_conn = sqlite3.connect(legacy_path)
        try:
            legacy_conn.executescript(legacy_schema)
            legacy_conn.commit()
        finally:
            legacy_conn.close()

        migrated = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        with migrated.connect() as conn:
            for table in ("cart_lines", "account_cart_lines", "order_items"):
                with self.subTest(table=table):
                    columns = {
                        row["name"]
                        for row in conn.execute(f"PRAGMA table_info({table})")
                    }
                    self.assertIn("selection_json", columns)

    def test_legacy_line_identity_constraints_migrate_without_cart_data_loss(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy-line-identity.sqlite3"
        legacy = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        legacy.reset()
        session_digest = "legacy-line-owner-" + "a" * 48
        legacy.ensure_session(session_digest)
        selected = {"Color": "Blue", "Memory Storage Capacity": "1 TB"}
        legacy.add_cart_item(session_digest, T7_ASIN, 3, selected)

        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.executescript(
                """
                CREATE TABLE cart_lines_legacy (
                    cart_id INTEGER NOT NULL REFERENCES carts(cart_id) ON DELETE CASCADE,
                    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    line_state TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (line_state IN ('ACTIVE', 'SAVED')),
                    PRIMARY KEY (cart_id, asin)
                );
                INSERT INTO cart_lines_legacy(
                    cart_id,asin,quantity,selection_json,line_state
                )
                SELECT cart_id,asin,quantity,selection_json,line_state FROM cart_lines;
                DROP TABLE cart_lines;
                ALTER TABLE cart_lines_legacy RENAME TO cart_lines;

                CREATE TABLE account_cart_lines_legacy (
                    account_id INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
                    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    line_state TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (line_state IN ('ACTIVE', 'SAVED')),
                    PRIMARY KEY (account_id, asin)
                );
                INSERT INTO account_cart_lines_legacy(
                    account_id,asin,quantity,selection_json,line_state
                )
                SELECT account_id,asin,quantity,selection_json,line_state
                FROM account_cart_lines;
                DROP TABLE account_cart_lines;
                ALTER TABLE account_cart_lines_legacy RENAME TO account_cart_lines;

                CREATE TABLE checkout_lines_legacy (
                    checkout_id INTEGER NOT NULL
                        REFERENCES checkout_sessions(checkout_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (checkout_id, ordinal),
                    UNIQUE (checkout_id, asin)
                );
                INSERT INTO checkout_lines_legacy
                SELECT * FROM checkout_lines;
                DROP TABLE checkout_lines;
                ALTER TABLE checkout_lines_legacy RENAME TO checkout_lines;

                DROP TRIGGER IF EXISTS return_item_order_insert_guard;
                CREATE TABLE order_items_legacy (
                    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                    asin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
                    line_total_minor INTEGER NOT NULL CHECK (
                        line_total_minor = unit_price_minor * quantity
                    ),
                    currency TEXT NOT NULL,
                    UNIQUE (order_id, ordinal),
                    UNIQUE (order_id, asin)
                );
                INSERT INTO order_items_legacy
                SELECT * FROM order_items;
                DROP TABLE order_items;
                ALTER TABLE order_items_legacy RENAME TO order_items;
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        lines = migrated.cart(session_digest)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["asin"], T7_ASIN)
        self.assertEqual(lines[0]["quantity"], 3)
        self.assertEqual(lines[0]["selected_options"], selected)
        self.assertRegex(str(lines[0]["line_id"]), r"^[A-Za-z0-9_-]{24,128}$")

        with migrated.connect() as conn:
            def unique_columns(table: str) -> set[tuple[str, ...]]:
                result: set[tuple[str, ...]] = set()
                for index in conn.execute(f"PRAGMA index_list({table})"):
                    if index["unique"]:
                        result.add(
                            tuple(
                                row["name"]
                                for row in conn.execute(
                                    f"PRAGMA index_info({index['name']})"
                                )
                            )
                        )
                return result

            self.assertIn(
                ("cart_id", "asin", "selection_key"),
                unique_columns("cart_lines"),
            )
            self.assertNotIn(
                ("checkout_id", "asin"), unique_columns("checkout_lines")
            )
            self.assertNotIn(("order_id", "asin"), unique_columns("order_items"))
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_sparse_and_unknown_asins_are_rejected_without_mutation(self) -> None:
        cookie = self.anonymous_cookie()
        for asin in (SPARSE_ASIN, UNKNOWN_ASIN):
            with self.subTest(asin=asin):
                self.mutate(
                    ADD_PATH,
                    cookie,
                    {"ASIN": asin, "quantity": "1"},
                    expected_status=404,
                )
        self.assertEqual(self.active_lines(cookie), {})
        self.assertEqual(self.saved_lines(cookie), {})

    def test_repeated_adds_merge_quantities_and_cap_the_line_at_thirty(self) -> None:
        cookie = self.anonymous_cookie()
        self.add(cookie, FIXTURE_ASIN, 2)
        self.add(cookie, FIXTURE_ASIN, 3)
        self.assertEqual(self.active_lines(cookie)[FIXTURE_ASIN]["quantity"], 5)

        self.add(cookie, FIXTURE_ASIN, 30)
        self.assertEqual(self.active_lines(cookie)[FIXTURE_ASIN]["quantity"], 30)
        self.add(cookie, FIXTURE_ASIN, 1)
        self.assertEqual(self.active_lines(cookie)[FIXTURE_ASIN]["quantity"], 30)

    def test_sibling_variants_coexist_and_each_line_action_targets_only_one(self) -> None:
        cookie = self.anonymous_cookie()
        first_selection = {"Size": "36 Count (Pack of 1)"}
        second_selection = {"Size": "75 Count"}
        for selection, quantity in (
            (first_selection, 2),
            (second_selection, 3),
            (first_selection, 4),
        ):
            self.mutate(
                ADD_PATH,
                cookie,
                {
                    "ASIN": BEAUTY_ASIN,
                    "quantity": str(quantity),
                    "option.Size": selection["Size"],
                },
            )

        variants = {
            tuple(sorted(row["selected_options"].items())): row
            for row in self.lines_for_asin(cookie, BEAUTY_ASIN)
        }
        self.assertEqual(len(variants), 2)
        first = variants[tuple(sorted(first_selection.items()))]
        second = variants[tuple(sorted(second_selection.items()))]
        self.assertEqual((first["quantity"], second["quantity"]), (6, 3))
        self.assertNotEqual(first["line_id"], second["line_id"])

        status, _, cart_html = self.request("GET", CART_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(cart_html.count(f'data-asin="{BEAUTY_ASIN}"'.encode()), 2)
        for row in (first, second):
            self.assertIn(
                f'name="lineID" value="{row["line_id"]}"'.encode(), cart_html
            )

        self.mutate(
            UPDATE_PATH,
            cookie,
            {"lineID": str(second["line_id"]), "quantity": "7"},
        )
        self.mutate(SAVE_PATH, cookie, {"lineID": str(first["line_id"])})
        active = self.lines_for_asin(cookie, BEAUTY_ASIN)
        saved = self.lines_for_asin(cookie, BEAUTY_ASIN, saved=True)
        self.assertEqual(
            [(row["selected_options"], row["quantity"]) for row in active],
            [(second_selection, 7)],
        )
        self.assertEqual(
            [(row["selected_options"], row["quantity"]) for row in saved],
            [(first_selection, 6)],
        )

        self.mutate(DELETE_PATH, cookie, {"lineID": str(second["line_id"])})
        self.mutate(MOVE_PATH, cookie, {"lineID": str(first["line_id"])})
        remaining = self.lines_for_asin(cookie, BEAUTY_ASIN)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["selected_options"], first_selection)
        self.assertEqual(remaining[0]["quantity"], 6)

    def test_cart_line_identity_cannot_be_forged_or_reused_across_sessions(self) -> None:
        owner = self.anonymous_cookie()
        attacker = self.anonymous_cookie()
        self.add(owner, FIXTURE_ASIN, 2)
        self.add(attacker, FIXTURE_ASIN, 5)
        owner_line_id = self.line_id(owner, FIXTURE_ASIN)

        for path, fields in (
            (UPDATE_PATH, {"lineID": owner_line_id, "quantity": "9"}),
            (DELETE_PATH, {"lineID": owner_line_id}),
            (SAVE_PATH, {"lineID": owner_line_id}),
        ):
            with self.subTest(path=path):
                self.mutate(path, attacker, fields, expected_status=409)
        self.mutate(
            UPDATE_PATH,
            attacker,
            {"lineID": "not-a-valid-line-id", "quantity": "1"},
            expected_status=400,
        )

        self.mutate(SAVE_PATH, owner, {"lineID": owner_line_id})
        self.mutate(
            MOVE_PATH,
            attacker,
            {"lineID": owner_line_id},
            expected_status=409,
        )
        self.assertEqual(
            self.saved_lines(owner)[FIXTURE_ASIN]["quantity"], 2
        )
        self.assertEqual(
            self.active_lines(attacker)[FIXTURE_ASIN]["quantity"], 5
        )

    def test_update_and_delete_mutate_only_an_existing_active_line(self) -> None:
        cookie = self.anonymous_cookie()
        self.add(cookie, FIXTURE_ASIN, 2)
        line_id = self.line_id(cookie, FIXTURE_ASIN)

        self.mutate(
            UPDATE_PATH,
            cookie,
            {"lineID": line_id, "quantity": "7"},
        )
        self.assertEqual(self.active_lines(cookie)[FIXTURE_ASIN]["quantity"], 7)

        self.mutate(DELETE_PATH, cookie, {"lineID": line_id})
        self.assertEqual(self.active_lines(cookie), {})
        self.mutate(
            UPDATE_PATH,
            cookie,
            {"lineID": line_id, "quantity": "1"},
            expected_status=409,
        )
        self.mutate(
            DELETE_PATH,
            cookie,
            {"lineID": line_id},
            expected_status=409,
        )

    def test_save_for_later_and_move_to_cart_round_trip(self) -> None:
        cookie = self.anonymous_cookie()
        self.add(cookie, SHEETS_ASIN, 4)
        line_id = self.line_id(cookie, SHEETS_ASIN)

        self.mutate(SAVE_PATH, cookie, {"lineID": line_id})
        self.assertNotIn(SHEETS_ASIN, self.active_lines(cookie))
        self.assertEqual(self.saved_lines(cookie)[SHEETS_ASIN]["quantity"], 4)
        self.mutate(
            SAVE_PATH,
            cookie,
            {"lineID": line_id},
            expected_status=409,
        )

        self.mutate(MOVE_PATH, cookie, {"lineID": line_id})
        self.assertEqual(self.active_lines(cookie)[SHEETS_ASIN]["quantity"], 4)
        self.assertNotIn(SHEETS_ASIN, self.saved_lines(cookie))
        self.mutate(
            MOVE_PATH,
            cookie,
            {"lineID": line_id},
            expected_status=409,
        )

    def test_anonymous_browser_sessions_are_isolated(self) -> None:
        first = self.anonymous_cookie()
        second = self.anonymous_cookie()
        self.assertNotEqual(first, second)

        self.add(first, OKAPI_ASIN, 2)
        self.assertEqual(self.active_lines(first)[OKAPI_ASIN]["quantity"], 2)
        self.assertEqual(self.active_lines(second), {})

        status, _, second_cart = self.request("GET", CART_PATH, cookie=second)
        self.assertEqual(status, 200)
        self.assertNotIn(OKAPI_ASIN.encode("ascii"), second_cart)

    def test_registration_rotates_session_and_preserves_the_guest_cart(self) -> None:
        guest = self.anonymous_cookie()
        old_digest = self.session_digest(guest)
        self.add(guest, SHEETS_ASIN, 2)
        self.add(guest, OKAPI_ASIN, 1)

        authenticated = self.register(guest)
        self.assertNotEqual(self.session_digest(authenticated), old_digest)
        self.assertIsNotNone(self.store.account_for_session(self.session_digest(authenticated)))
        self.assertEqual(
            {asin: line["quantity"] for asin, line in self.active_lines(authenticated).items()},
            {SHEETS_ASIN: 2, OKAPI_ASIN: 1},
        )
        self.assertEqual(self.store.cart(old_digest), [])

    def test_existing_account_login_restores_and_merges_the_guest_cart(self) -> None:
        owner_guest = self.anonymous_cookie()
        self.add(owner_guest, FIXTURE_ASIN, 25)
        self.add(owner_guest, SHEETS_ASIN, 1)
        owner = self.register(owner_guest)
        self.sign_out(owner)

        returning_guest = self.anonymous_cookie()
        self.add(returning_guest, FIXTURE_ASIN, 10)
        self.add(returning_guest, OKAPI_ASIN, 2)
        authenticated = self.sign_in(returning_guest)

        lines = self.active_lines(authenticated)
        self.assertEqual(set(lines), {FIXTURE_ASIN, SHEETS_ASIN, OKAPI_ASIN})
        self.assertEqual(lines[FIXTURE_ASIN]["quantity"], 30)
        self.assertEqual(lines[SHEETS_ASIN]["quantity"], 1)
        self.assertEqual(lines[OKAPI_ASIN]["quantity"], 2)
        self.assertEqual(self.store.cart(self.session_digest(returning_guest)), [])

    def test_login_merge_combines_only_matching_variants(self) -> None:
        first_selection = {"Size": "36 Count (Pack of 1)"}
        second_selection = {"Size": "75 Count"}
        owner_guest = self.anonymous_cookie()
        self.mutate(
            ADD_PATH,
            owner_guest,
            {"ASIN": BEAUTY_ASIN, "quantity": "2", "option.Size": first_selection["Size"]},
        )
        owner = self.register(owner_guest, email="variant-owner@example.test")
        durable_line_id = str(self.lines_for_asin(owner, BEAUTY_ASIN)[0]["line_id"])
        self.sign_out(owner)

        returning_guest = self.anonymous_cookie()
        for selection, quantity in ((first_selection, 3), (second_selection, 4)):
            self.mutate(
                ADD_PATH,
                returning_guest,
                {
                    "ASIN": BEAUTY_ASIN,
                    "quantity": str(quantity),
                    "option.Size": selection["Size"],
                },
            )
        guest_line_ids = {
            str(row["line_id"])
            for row in self.lines_for_asin(returning_guest, BEAUTY_ASIN)
        }
        authenticated = self.sign_in(
            returning_guest, email="variant-owner@example.test"
        )

        variants = {
            tuple(sorted(row["selected_options"].items())): row
            for row in self.lines_for_asin(authenticated, BEAUTY_ASIN)
        }
        self.assertEqual(len(variants), 2)
        first = variants[tuple(sorted(first_selection.items()))]
        second = variants[tuple(sorted(second_selection.items()))]
        self.assertEqual((first["quantity"], second["quantity"]), (5, 4))
        self.assertEqual(first["line_id"], durable_line_id)
        self.assertNotIn(str(second["line_id"]), guest_line_ids)
        self.assertEqual(
            self.store.cart(self.session_digest(returning_guest)), []
        )

    def test_signout_does_not_expose_account_cart_to_new_or_replayed_session(self) -> None:
        guest = self.anonymous_cookie()
        self.add(guest, SHEETS_ASIN, 1)
        authenticated = self.register(guest)
        self.sign_out(authenticated)

        fresh = self.anonymous_cookie()
        self.assertEqual(self.active_lines(fresh), {})
        status, _, fresh_cart = self.request("GET", CART_PATH, cookie=fresh)
        self.assertEqual(status, 200)
        self.assertNotIn(SHEETS_ASIN.encode("ascii"), fresh_cart)

        status, _, replayed_cart = self.request("GET", CART_PATH, cookie=authenticated)
        self.assertEqual(status, 200)
        self.assertNotIn(SHEETS_ASIN.encode("ascii"), replayed_cart)
        self.assertEqual(self.active_lines(authenticated), {})

    def test_malformed_duplicate_and_out_of_range_forms_are_rejected(self) -> None:
        cookie = self.anonymous_cookie()
        malformed: tuple[tuple[str, str, FormFields], ...] = (
            ("missing quantity", ADD_PATH, {"ASIN": FIXTURE_ASIN}),
            (
                "unexpected field",
                ADD_PATH,
                {"ASIN": FIXTURE_ASIN, "quantity": "1", "offerListingID": "forged"},
            ),
            (
                "duplicate ASIN",
                ADD_PATH,
                (("ASIN", FIXTURE_ASIN), ("ASIN", OKAPI_ASIN), ("quantity", "1")),
            ),
            (
                "duplicate quantity",
                ADD_PATH,
                (("ASIN", FIXTURE_ASIN), ("quantity", "1"), ("quantity", "2")),
            ),
            ("invalid ASIN", ADD_PATH, {"ASIN": "not-an-asin", "quantity": "1"}),
            ("zero quantity", ADD_PATH, {"ASIN": FIXTURE_ASIN, "quantity": "0"}),
            ("negative quantity", ADD_PATH, {"ASIN": FIXTURE_ASIN, "quantity": "-1"}),
            ("decimal quantity", ADD_PATH, {"ASIN": FIXTURE_ASIN, "quantity": "1.5"}),
            ("quantity above cap", ADD_PATH, {"ASIN": FIXTURE_ASIN, "quantity": "31"}),
            ("blank quantity", ADD_PATH, {"ASIN": FIXTURE_ASIN, "quantity": ""}),
            ("delete carries quantity", DELETE_PATH, {"ASIN": FIXTURE_ASIN, "quantity": "1"}),
        )
        for label, path, fields in malformed:
            with self.subTest(case=label):
                self.mutate(path, cookie, fields, expected_status=400)

        status, _, _ = self.request(
            "POST",
            ADD_PATH,
            raw_body=b'{"ASIN":"B08GTYFC37","quantity":1}',
            content_type="application/json",
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.active_lines(cookie), {})
        self.assertEqual(self.saved_lines(cookie), {})

    def test_cart_mutations_require_an_explicit_same_origin(self) -> None:
        cookie = self.anonymous_cookie()
        fields = {"ASIN": FIXTURE_ASIN, "quantity": "1"}
        for label, origin in (
            ("cross-origin", "https://evil.example"),
            ("missing-origin", None),
        ):
            with self.subTest(case=label):
                self.mutate(
                    ADD_PATH,
                    cookie,
                    fields,
                    expected_status=403,
                    origin=origin,
                )
        self.assertEqual(self.active_lines(cookie), {})


if __name__ == "__main__":
    unittest.main()
