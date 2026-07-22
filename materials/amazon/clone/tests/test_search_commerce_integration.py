from __future__ import annotations

import http.client
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    BROWSE_BREADTH,
    PublicHandler,
    ReusableThreadingHTTPServer,
    digest,
)
from store import Store  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"
SEARCH_PATH = "/s?k=portable+ssd"
ADD_PATH = "/gp/cart/add.html"
CART_PATH = "/gp/cart/view.html"

QUOTED_ASINS = (
    "B08HN37XC1",
    "B08GTYFC37",
    "B0F6NKYDTY",
    "B0BGKXX9TK",
    "B0874XN4D8",
    "B0C5JQ68FY",
    "B08GV9M64L",
    "B09VLK9W3S",
    "B0CHFSWM2P",
)
PLAIN_ASIN = "B08GTYFC37"
BEST_SELLERS_PATH = "/s?k=best+sellers"

FormFields = Mapping[str, str] | Sequence[tuple[str, str]]


class SearchCommerceParser(HTMLParser):
    """Capture the executable form contract of each rendered search card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self.current_card: dict[str, object] | None = None
        self.current_form: dict[str, object] | None = None
        self.current_select: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "search-result" in classes:
            self.current_card = {
                "asin": attributes.get("data-asin"),
                "evidence_level": attributes.get("data-evidence-level"),
                "forms": [],
                "text_parts": [],
            }
            return
        if self.current_card is None:
            return
        if tag == "form":
            self.current_form = {
                "action": attributes.get("action") or "",
                "method": (attributes.get("method") or "get").lower(),
                "classes": classes,
                "hidden": {},
                "selects": {},
                "submit_buttons": 0,
            }
            return
        if self.current_form is None:
            return
        if tag == "input" and attributes.get("name"):
            hidden = self.current_form["hidden"]
            assert isinstance(hidden, dict)
            hidden[str(attributes["name"])] = attributes.get("value") or ""
        elif tag == "select" and attributes.get("name"):
            self.current_select = str(attributes["name"])
            selects = self.current_form["selects"]
            assert isinstance(selects, dict)
            selects[self.current_select] = []
        elif tag == "option" and self.current_select is not None:
            selects = self.current_form["selects"]
            assert isinstance(selects, dict)
            values = selects[self.current_select]
            assert isinstance(values, list)
            values.append(attributes.get("value") or "")
        elif tag == "button" and (attributes.get("type") or "submit") == "submit":
            self.current_form["submit_buttons"] = (
                int(self.current_form["submit_buttons"]) + 1
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.current_select = None
        elif tag == "form" and self.current_card is not None:
            assert self.current_form is not None
            forms = self.current_card["forms"]
            assert isinstance(forms, list)
            forms.append(self.current_form)
            self.current_form = None
            self.current_select = None
        elif tag == "article" and self.current_card is not None:
            parts = self.current_card.pop("text_parts")
            assert isinstance(parts, list)
            self.current_card["text"] = " ".join("".join(parts).split())
            self.cards.append(self.current_card)
            self.current_card = None
            self.current_form = None
            self.current_select = None

    def handle_data(self, data: str) -> None:
        if self.current_card is None:
            return
        parts = self.current_card["text_parts"]
        assert isinstance(parts, list)
        parts.append(data)


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class SearchCommerceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        QuietPublicHandler.store = self.store
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

    @property
    def same_origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(
        self,
        method: str,
        path: str,
        *,
        fields: FormFields | None = None,
        cookie: str = "",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        body = urlencode(fields, doseq=True).encode("utf-8") if fields is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Content-Length"] = str(len(body))
        if method == "POST":
            headers["Origin"] = self.same_origin
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
        raise AssertionError("response did not set a browser session cookie")

    @staticmethod
    def session_digest(cookie: str) -> str:
        name, token = cookie.split("=", 1)
        if name != SESSION_COOKIE or not token:
            raise AssertionError(f"invalid session cookie: {cookie!r}")
        return digest(token)

    def search_document(
        self, path: str = SEARCH_PATH, *, cookie: str = ""
    ) -> tuple[SearchCommerceParser, dict[str, list[str]], bytes]:
        status, headers, payload = self.request("GET", path, cookie=cookie)
        self.assertEqual(status, 200, (path, payload.decode("utf-8", errors="replace")))
        parser = SearchCommerceParser()
        parser.feed(payload.decode("utf-8"))
        return parser, headers, payload

    @staticmethod
    def quick_add_forms(card: dict[str, object]) -> list[dict[str, object]]:
        forms = card["forms"]
        assert isinstance(forms, list)
        return [
            form
            for form in forms
            if isinstance(form, dict) and form.get("action") == ADD_PATH
        ]

    def test_quoted_first_page_cards_expose_real_post_quick_add_forms(self) -> None:
        parser, _, _ = self.search_document()
        quoted_cards = parser.cards[: len(QUOTED_ASINS)]
        self.assertEqual(
            tuple(card["asin"] for card in quoted_cards), QUOTED_ASINS
        )

        for card in quoted_cards:
            with self.subTest(asin=card["asin"]):
                forms = self.quick_add_forms(card)
                self.assertEqual(len(forms), 1)
                form = forms[0]
                self.assertEqual(form["method"], "post")
                self.assertIn("generic-cart-form", form["classes"])
                self.assertEqual(form["submit_buttons"], 1)
                hidden = form["hidden"]
                selects = form["selects"]
                assert isinstance(hidden, dict)
                assert isinstance(selects, dict)
                self.assertEqual(hidden["ASIN"], card["asin"])
                self.assertEqual(selects["quantity"], [str(value) for value in range(1, 31)])

    def test_all_later_browse_only_cards_have_no_shopping_form(self) -> None:
        expected = tuple(
            str(product["asin"])
            for product in BROWSE_BREADTH["portable_ssd_supplement"]
        )
        self.assertEqual(len(expected), 27)
        observed: list[str] = []

        for page_number in (1, 2, 3):
            suffix = "" if page_number == 1 else f"&page={page_number}"
            parser, _, _ = self.search_document(SEARCH_PATH + suffix)
            for card in parser.cards:
                asin = str(card["asin"])
                if asin not in QUOTED_ASINS:
                    observed.append(asin)
                    with self.subTest(page=page_number, asin=asin):
                        self.assertEqual(card["evidence_level"], "homepage-browse")
                        self.assertEqual(card["forms"], [])
                        self.assertNotIn("Add to cart", str(card["text"]))

        self.assertEqual(tuple(observed), expected)

    def test_quick_add_posts_the_selected_product_and_quantity(self) -> None:
        parser, headers, _ = self.search_document()
        cookie = self.session_cookie(headers)
        card = next(card for card in parser.cards if card["asin"] == PLAIN_ASIN)
        self.assertEqual(self.store.default_product_options(PLAIN_ASIN), {})
        form = self.quick_add_forms(card)[0]
        hidden = form["hidden"]
        assert isinstance(hidden, dict)
        fields = {str(name): str(value) for name, value in hidden.items()}
        fields["quantity"] = "2"

        status, response_headers, body = self.request(
            "POST", ADD_PATH, fields=fields, cookie=cookie
        )
        self.assertEqual((status, response_headers.get("location"), body), (303, [CART_PATH], b""))

        session_digest = self.session_digest(cookie)
        lines = self.store.cart(session_digest)
        self.assertEqual(self.store.cart_count(session_digest), 2)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["asin"], PLAIN_ASIN)
        self.assertEqual(lines[0]["quantity"], 2)
        self.assertEqual(lines[0]["selected_options"], {})

        cart_status, _, cart_html = self.request("GET", CART_PATH, cookie=cookie)
        self.assertEqual(cart_status, 200)
        self.assertIn(f'data-asin="{PLAIN_ASIN}"'.encode(), cart_html)
        self.assertIn(b'<span id="nav-cart-count">2</span>', cart_html)

    def test_fact_filters_exclude_cards_with_unknown_facts(self) -> None:
        browse_asins = {
            str(product["asin"])
            for product in BROWSE_BREADTH["portable_ssd_supplement"]
        }
        cases = {
            "price": ("&maxPrice=1000", set(QUOTED_ASINS)),
            "rating": ("&rating=4-up", set(QUOTED_ASINS)),
            "availability": (
                "&availability=in-stock",
                {"B08HN37XC1", "B0874XN4D8", "B0CHFSWM2P"},
            ),
        }
        for label, (suffix, expected_asins) in cases.items():
            with self.subTest(filter=label):
                parser, _, _ = self.search_document(SEARCH_PATH + suffix)
                observed = {str(card["asin"]) for card in parser.cards}
                self.assertEqual(observed, expected_asins)
                self.assertTrue(observed.isdisjoint(browse_asins))
                self.assertTrue(
                    all(card["evidence_level"] != "homepage-browse" for card in parser.cards)
                )

    def test_client_price_field_is_rejected_and_server_quote_wins(self) -> None:
        parser, headers, _ = self.search_document()
        cookie = self.session_cookie(headers)
        card = next(card for card in parser.cards if card["asin"] == PLAIN_ASIN)
        form = self.quick_add_forms(card)[0]
        hidden = form["hidden"]
        assert isinstance(hidden, dict)
        valid_fields = {str(name): str(value) for name, value in hidden.items()}
        valid_fields["quantity"] = "1"

        tampered_fields = {**valid_fields, "price_minor": "1"}
        status, _, _ = self.request(
            "POST", ADD_PATH, fields=tampered_fields, cookie=cookie
        )
        session_digest = self.session_digest(cookie)
        self.assertEqual(status, 400)
        self.assertEqual(self.store.cart_count(session_digest), 0)

        status, response_headers, body = self.request(
            "POST", ADD_PATH, fields=valid_fields, cookie=cookie
        )
        self.assertEqual((status, response_headers.get("location"), body), (303, [CART_PATH], b""))
        line = self.store.cart(session_digest)[0]
        offer = self.store.commerce_offer(PLAIN_ASIN)
        assert offer is not None
        self.assertEqual(line["price_minor"], offer["price_minor"])
        self.assertNotEqual(line["price_minor"], 1)

    def test_twenty_current_search_cards_are_prioritized_once_with_live_forms(self) -> None:
        expected_products = tuple(BROWSE_BREADTH["search_commerce_cards"])
        first, _, _ = self.search_document(BEST_SELLERS_PATH)
        second, _, _ = self.search_document(BEST_SELLERS_PATH + "&page=2")
        cards = (first.cards + second.cards)[:20]
        self.assertEqual(
            tuple(card["asin"] for card in cards),
            tuple(product["asin"] for product in expected_products),
        )
        self.assertEqual(len({card["asin"] for card in cards}), 20)
        for card in cards:
            with self.subTest(asin=card["asin"]):
                self.assertEqual(card["evidence_level"], "direct-search-card")
                forms = self.quick_add_forms(card)
                self.assertEqual(len(forms), 1)
                self.assertEqual(forms[0]["hidden"], {"ASIN": card["asin"]})
                self.assertIn("Verified search-card offer", str(card["text"]))

        combined_html = (
            self.search_document(BEST_SELLERS_PATH)[2]
            + self.search_document(BEST_SELLERS_PATH + "&page=2")[2]
        ).decode("utf-8")
        self.assertIn("19.7K", combined_html)
        self.assertNotIn("19,700", combined_html)
        self.assertIn("320K", combined_html)
        self.assertNotIn("320,000", combined_html)

    def test_search_card_offer_count_default_quote_and_transaction_are_server_owned(self) -> None:
        with self.store.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM commerce_offers").fetchone()[0],
                49,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM commerce_offers WHERE evidence_class='direct-search-card'"
                ).fetchone()[0],
                20,
            )

        target = next(
            product
            for product in BROWSE_BREADTH["search_commerce_cards"]
            if product["asin"] == "1481215663"
        )
        parser, headers, _ = self.search_document(BEST_SELLERS_PATH)
        cookie = self.session_cookie(headers)
        card = next(card for card in parser.cards if card["asin"] == target["asin"])
        form = self.quick_add_forms(card)[0]
        hidden = form["hidden"]
        assert isinstance(hidden, dict)
        fields = {str(name): str(value) for name, value in hidden.items()}
        fields["quantity"] = "2"

        invalid = {**fields, "option.Format": "Paperback"}
        invalid_status, _, _ = self.request(
            "POST", ADD_PATH, fields=invalid, cookie=cookie
        )
        self.assertEqual(invalid_status, 400)

        status, response_headers, body = self.request(
            "POST", ADD_PATH, fields=fields, cookie=cookie
        )
        self.assertEqual((status, response_headers.get("location"), body), (303, [CART_PATH], b""))
        line = self.store.cart(self.session_digest(cookie))[0]
        self.assertEqual(line["asin"], target["asin"])
        self.assertEqual(line["quantity"], 2)
        self.assertEqual(line["selected_options"], {})
        self.assertEqual(line["price_minor"], target["price_minor"])
        self.assertEqual(line["image_path"], target["image_path"])
        self.assertEqual(
            line["display_availability"],
            "Available from captured search-card offer",
        )

    def test_search_card_detail_is_a_narrow_shell_not_deals_or_full_pdp(self) -> None:
        product = BROWSE_BREADTH["search_commerce_cards"][0]
        status, _, payload = self.request("GET", str(product["canonical_path"]))
        html = payload.decode("utf-8")
        main_html = html.split('<main id="main"', 1)[1].split("</main>", 1)[0]
        self.assertEqual(status, 200)
        self.assertIn('data-pdp-variant="direct-search-card"', html)
        self.assertIn("card-evidence detail shell", html)
        self.assertIn(str(product["title"]), html)
        self.assertIn(str(product["reviews_display"]), html)
        self.assertIn("Add to cart", html)
        self.assertNotIn("Today's Deals", main_html)
        self.assertNotIn("Limited time deal", main_html)
        self.assertNotIn("Other sellers on Amazon", main_html)


if __name__ == "__main__":
    unittest.main()
