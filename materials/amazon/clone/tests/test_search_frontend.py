from __future__ import annotations

import http.client
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from browse_breadth import EXPECTED_HOME_RAILS  # noqa: E402
from search_catalog import SOURCE_DEPARTMENTS  # noqa: E402
from server import (  # noqa: E402
    BROWSE_BREADTH,
    DEALS_CATALOG,
    HOME_PRODUCT_CATALOG,
    PublicHandler,
    ReusableThreadingHTTPServer,
)
from store import Store  # noqa: E402


SSD_SEARCH_ASINS = (
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
SEARCH_PAGE_SIZE = 16


def normalized_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class SearchDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self.current: dict[str, object] | None = None
        self.text_parts: list[str] = []
        self.in_title = False
        self.in_main = False
        self.in_pagination = False
        self.in_active_filters = False
        self.in_header_department = False
        self.in_sort = False
        self.main_hrefs: list[str] = []
        self.pagination_hrefs: list[str] = []
        self.active_filter_links: list[dict[str, str | None]] = []
        self.clear_filter_hrefs: list[str] = []
        self.filter_toggles: list[dict[str, str | None]] = []
        self.header_departments: dict[str, bool] = {}
        self.selected_sort: str | None = None

    @property
    def text(self) -> str:
        return normalized_text(self.text_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "main" and attributes.get("id") == "main":
            self.in_main = True
        elif tag == "nav" and "search-pagination" in classes:
            self.in_pagination = True
        elif tag == "nav" and "active-search-filters" in classes:
            self.in_active_filters = True
        elif tag == "select" and attributes.get("name") == "i":
            self.in_header_department = True
        elif tag == "select" and attributes.get("id") == "search-sort":
            self.in_sort = True

        if tag == "button" and "data-search-filter-toggle" in attributes:
            self.filter_toggles.append(attributes)
        if tag == "option" and self.in_header_department:
            value = attributes.get("value") or ""
            self.header_departments[value] = "selected" in attributes
        if tag == "option" and self.in_sort and "selected" in attributes:
            self.selected_sort = attributes.get("value")
        if tag == "a":
            href = attributes.get("href") or ""
            if self.in_main:
                self.main_hrefs.append(href)
            if self.in_pagination:
                self.pagination_hrefs.append(href)
            if self.in_active_filters:
                self.active_filter_links.append(attributes)
            if "search-filter-clear" in classes:
                self.clear_filter_hrefs.append(href)

        if tag == "article" and "search-result" in classes:
            self.current = {
                "asin": attributes.get("data-asin"),
                "evidence_level": attributes.get("data-evidence-level"),
                "classes": classes,
                "title_parts": [],
                "text_parts": [],
                "hrefs": [],
                "images": [],
                "forms": [],
                "has_price": False,
                "has_rating": False,
                "has_bought": False,
            }
            return
        if self.current is None:
            return
        if tag == "h2":
            self.in_title = True
        if tag == "a":
            href = attributes.get("href") or ""
            if "/dp/" in href:
                hrefs = self.current["hrefs"]
                assert isinstance(hrefs, list)
                hrefs.append(href)
            if "result-price" in classes:
                self.current["has_price"] = True
            if "rating" in classes:
                self.current["has_rating"] = True
        if tag == "img":
            images = self.current["images"]
            assert isinstance(images, list)
            images.append(attributes.get("src") or "")
        if tag == "form":
            forms = self.current["forms"]
            assert isinstance(forms, list)
            forms.append(attributes.get("action") or "")
        if "bought" in classes:
            self.current["has_bought"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.in_main = False
        elif tag == "nav" and self.in_pagination:
            self.in_pagination = False
        elif tag == "nav" and self.in_active_filters:
            self.in_active_filters = False
        elif tag == "select" and self.in_header_department:
            self.in_header_department = False
        elif tag == "select" and self.in_sort:
            self.in_sort = False
        if self.current is None:
            return
        if tag == "h2":
            self.in_title = False
        elif tag == "article":
            title_parts = self.current.pop("title_parts")
            text_parts = self.current.pop("text_parts")
            assert isinstance(title_parts, list)
            assert isinstance(text_parts, list)
            self.current["title"] = normalized_text(title_parts)
            self.current["text"] = normalized_text(text_parts)
            self.cards.append(self.current)
            self.current = None
            self.in_title = False

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self.current is not None:
            card_parts = self.current["text_parts"]
            assert isinstance(card_parts, list)
            card_parts.append(data)
            if self.in_title:
                title_parts = self.current["title_parts"]
                assert isinstance(title_parts, list)
                title_parts.append(data)


class HomeSearchLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if href.startswith("/s?"):
            self.hrefs.append(href)


class BrowseSurfaceParser(HTMLParser):
    CARD_CLASSES = {
        "browse-supplement-card",
        "site-directory-card",
        "verified-offer-card",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self.current: dict[str, object] | None = None
        self.sections: list[tuple[str | None, str | None, str | None]] = []
        self.directory_anchors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "section" and "site-directory-section" in classes:
            self.sections.append(
                (
                    attributes.get("id"),
                    attributes.get("data-rail-key"),
                    attributes.get("data-product-count"),
                )
            )
        if tag == "a" and (attributes.get("href") or "").startswith("#rail-"):
            self.directory_anchors.append(attributes.get("href") or "")
        card_class = next(iter(classes.intersection(self.CARD_CLASSES)), None)
        if tag == "article" and card_class is not None:
            self.current = {
                "kind": card_class,
                "asin": attributes.get("data-asin"),
                "currency": attributes.get("data-currency"),
                "classes": set(classes),
                "hrefs": [],
                "images": [],
                "forms": [],
                "text_parts": [],
            }
            return
        if self.current is None:
            return
        current_classes = self.current["classes"]
        assert isinstance(current_classes, set)
        current_classes.update(classes)
        if tag == "a":
            hrefs = self.current["hrefs"]
            assert isinstance(hrefs, list)
            hrefs.append(attributes.get("href") or "")
        elif tag == "img":
            images = self.current["images"]
            assert isinstance(images, list)
            images.append(attributes.get("src") or "")
        elif tag == "form":
            forms = self.current["forms"]
            assert isinstance(forms, list)
            forms.append(attributes.get("action") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag != "article" or self.current is None:
            return
        parts = self.current.pop("text_parts")
        assert isinstance(parts, list)
        self.current["text"] = normalized_text(parts)
        self.cards.append(self.current)
        self.current = None

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        parts = self.current["text_parts"]
        assert isinstance(parts, list)
        parts.append(data)


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class QueryAwareSearchTests(unittest.TestCase):
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

    def response(self, path: str) -> tuple[int, str]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload.decode("utf-8")

    def document(self, path: str) -> tuple[SearchDocumentParser, str]:
        status, html = self.response(path)
        self.assertEqual(status, 200, path)
        parser = SearchDocumentParser()
        parser.feed(html)
        return parser, html

    @staticmethod
    def page_path(path: str, page: int) -> str:
        parts = urlsplit(path)
        pairs = [
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if name != "page"
        ]
        if page != 1:
            pairs.append(("page", str(page)))
        query = urlencode(pairs)
        return parts.path + (f"?{query}" if query else "")

    def documents_for_total(
        self, path: str, total: int
    ) -> list[tuple[SearchDocumentParser, str]]:
        page_count = max(1, (total + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
        return [self.document(self.page_path(path, page)) for page in range(1, page_count + 1)]

    def cards_for_total(
        self, path: str, total: int
    ) -> tuple[list[tuple[SearchDocumentParser, str]], list[dict[str, object]]]:
        documents = self.documents_for_total(path, total)
        return documents, [
            card for parser, _ in documents for card in parser.cards
        ]

    def test_portable_ssd_query_keeps_the_frozen_nine_product_contract(self) -> None:
        catalog = {product["asin"]: product for product in self.store.products()}
        for path in (
            "/s?k=portable+ssd",
            "/s/ref=nb_sb_noss?field-keywords=Portable%20SSD",
        ):
            with self.subTest(path=path):
                parser, _ = self.document(path)
                self.assertEqual(len(parser.cards), SEARCH_PAGE_SIZE)
                self.assertEqual(
                    tuple(card["asin"] for card in parser.cards[: len(SSD_SEARCH_ASINS)]),
                    SSD_SEARCH_ASINS,
                )
                for card in parser.cards[: len(SSD_SEARCH_ASINS)]:
                    asin = card["asin"]
                    assert isinstance(asin, str)
                    self.assertEqual(card["title"], catalog[asin]["title"])

    def test_portable_cards_do_not_borrow_t7_social_or_delivery_copy(self) -> None:
        parser, _ = self.document("/s?k=portable+ssd")
        cards = {str(card["asin"]): card for card in parser.cards}
        for asin in ("B08GV9M64L", "B09VLK9W3S"):
            with self.subTest(asin=asin):
                self.assertFalse(cards[asin]["has_bought"])
                self.assertNotIn("$7.30 delivery", cards[asin]["text"])
        self.assertTrue(cards["B0CHFSWM2P"]["has_bought"])
        self.assertIn("10K+ bought in past month", cards["B0CHFSWM2P"]["text"])
        self.assertIn("$7.61 delivery Monday, July 27", cards["B0CHFSWM2P"]["text"])

    def test_portable_ssd_spans_three_pages_with_27_browse_only_tail_cards(self) -> None:
        documents, cards = self.cards_for_total("/s?k=portable+ssd", 36)
        self.assertEqual([len(parser.cards) for parser, _ in documents], [16, 16, 4])
        self.assertEqual(len(cards), 36)
        self.assertEqual(len({card["asin"] for card in cards}), 36)
        self.assertEqual(
            tuple(card["asin"] for card in cards[: len(SSD_SEARCH_ASINS)]),
            SSD_SEARCH_ASINS,
        )

        expected = list(BROWSE_BREADTH["portable_ssd_supplement"])
        tail = cards[len(SSD_SEARCH_ASINS) :]
        self.assertEqual(
            [card["asin"] for card in tail],
            [product["asin"] for product in expected],
        )
        self.assertEqual(len(tail), 27)
        for parser, _ in documents:
            self.assertNotIn("#", parser.main_hrefs)
        for card, product in zip(tail, expected, strict=True):
            self.assertEqual(card["evidence_level"], "homepage-browse")
            self.assertEqual(card["forms"], [])
            self.assertFalse(card["has_price"])
            self.assertFalse(card["has_rating"])
            self.assertFalse(card["has_bought"])
            self.assertNotIn("Add to cart", card["text"])
            self.assertIn(product["canonical_path"], card["hrefs"])
            self.assertTrue(all(str(image).startswith("/static/") for image in card["images"]))

    def test_site_directory_exposes_every_unique_home_catalog_product(self) -> None:
        _, html = self.document("/gp/site-directory")
        surface = BrowseSurfaceParser()
        surface.feed(html)
        cards = [card for card in surface.cards if card["kind"] == "site-directory-card"]
        asins = tuple(card["asin"] for card in cards)
        self.assertEqual(len(asins), 157)
        self.assertEqual(len(set(asins)), 157)
        self.assertEqual(set(asins), set(HOME_PRODUCT_CATALOG))
        self.assertEqual(
            surface.sections,
            [
                (f"rail-{key}", key, str(count))
                for key, _, count in EXPECTED_HOME_RAILS
            ],
        )
        self.assertEqual(
            surface.directory_anchors,
            [f"#rail-{key}" for key, _, _ in EXPECTED_HOME_RAILS],
        )
        self.assertIn('data-total-products="157"', html)
        self.assertTrue(all(card["forms"] == [] for card in cards))
        self.assertTrue(
            all(all(str(image).startswith("/static/") for image in card["images"]) for card in cards)
        )
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_deals_lists_all_29_strict_usd_offers_with_live_detail_and_cart_forms(self) -> None:
        _, html = self.document("/gp/goldbox/")
        surface = BrowseSurfaceParser()
        surface.feed(html)
        cards = [card for card in surface.cards if card["kind"] == "verified-offer-card"]
        offers = list(DEALS_CATALOG)
        self.assertEqual([card["asin"] for card in cards], [offer["asin"] for offer in offers])
        self.assertEqual(len(cards), 29)
        self.assertEqual(len({card["asin"] for card in cards}), 29)
        self.assertEqual(html.count('class="deals-limited-label"'), 12)
        self.assertEqual(html.count('class="deals-discount"'), 13)
        self.assertNotIn('class="deal-badge"', html)
        for card, offer in zip(cards, offers, strict=True):
            self.assertEqual(card["currency"], "USD")
            self.assertEqual(card["forms"], ["/gp/cart/add.html"])
            self.assertIn(offer["canonical_path"], card["hrefs"])
            whole, cents = divmod(offer["price_minor"], 100)
            self.assertIn(f"${whole:,}.{cents:02d}", card["text"])
            self.assertTrue(all(str(image).startswith("/static/") for image in card["images"]))

            _, detail_html = self.document(offer["canonical_path"])
            self.assertIn(f'data-asin="{offer["asin"]}"', detail_html)

        self.assertIn("B01M16WBW1", {card["asin"] for card in cards})
        self.assertIn("B0BG6B2D4D", {card["asin"] for card in cards})
        self.assertTrue(
            {"B00FLYWNYQ", "B07K74LDCH", "B088BZTYFP"}
            <= {card["asin"] for card in cards}
        )

    def test_every_home_search_destination_has_source_backed_results(self) -> None:
        _, home_html = self.document("/")
        links = HomeSearchLinkParser()
        links.feed(home_html)
        destinations = tuple(dict.fromkeys(links.hrefs))
        supplemental = {
            str(product["asin"]): product
            for product in (
                *BROWSE_BREADTH["department_commerce_supplements"],
                *BROWSE_BREADTH["search_commerce_cards"],
            )
        }
        self.assertGreaterEqual(len(destinations), 20)
        for path in destinations:
            with self.subTest(path=path):
                parser, html = self.document(path)
                self.assertGreater(len(parser.cards), 0)
                for card in parser.cards:
                    asin = card["asin"]
                    self.assertIsInstance(asin, str)
                    if asin in HOME_PRODUCT_CATALOG and asin not in supplemental:
                        product = HOME_PRODUCT_CATALOG[asin]
                        if product.get("evidence_tier") == "home-card-only":
                            self.assertFalse(card["has_price"])
                self.assertNotIn("http://", html)
                self.assertNotIn("https://", html)

    def test_books_query_uses_home_evidence_and_is_deterministic(self) -> None:
        first_documents, first_cards = self.cards_for_total("/s?k=books", 32)
        second_documents, second_cards = self.cards_for_total("/s?k=books", 32)
        self.assertEqual(
            [len(parser.cards) for parser, _ in first_documents], [16, 16]
        )
        first_asins = tuple(card["asin"] for card in first_cards)
        self.assertEqual(first_asins, tuple(card["asin"] for card in second_cards))
        self.assertGreater(len(first_asins), 20)
        self.assertNotEqual(first_asins[: len(SSD_SEARCH_ASINS)], SSD_SEARCH_ASINS)
        for parser, html in first_documents + second_documents:
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("#", parser.main_hrefs)
            self.assertEqual(
                {
                    slug
                    for slug, selected in parser.header_departments.items()
                    if selected
                },
                {"aps"},
            )
            main_opening_tag = html.split('<main id="main"', 1)[1].split(">", 1)[0]
            self.assertNotIn("data-department=", main_opening_tag)
            self.assertNotIn("data-source-rail=", main_opening_tag)

        self.assertEqual(len(first_cards), 32)
        self.assertEqual(first_cards[0]["asin"], "168281808X")
        search_cards = {
            str(product["asin"]): product
            for product in BROWSE_BREADTH["search_commerce_cards"]
        }
        for card in first_cards:
            asin = card["asin"]
            assert isinstance(asin, str)
            product = search_cards.get(asin) or HOME_PRODUCT_CATALOG[asin]
            self.assertEqual(card["title"], product["title"])
            if product.get("evidence_tier") == "home-card-only":
                self.assertFalse(card["has_price"])
                self.assertFalse(card["has_rating"])
                self.assertFalse(card["has_bought"])
            else:
                self.assertTrue(card["has_price"])
                self.assertEqual(
                    card["has_rating"],
                    isinstance(product.get("rating"), str)
                    and bool(product.get("rating"))
                    and (
                        (
                            isinstance(product.get("reviews"), int)
                            and int(product.get("reviews", 0)) > 0
                        )
                        or bool(product.get("reviews_display"))
                    ),
                )
            self.assertTrue(all(str(href).startswith("/") for href in card["hrefs"]))
            self.assertTrue(all(str(image).startswith("/static/") for image in card["images"]))

    def test_category_parameter_and_keyword_query_share_the_same_catalog_results(self) -> None:
        _, keyword_cards = self.cards_for_total("/s?k=books", 32)
        expected = tuple(card["asin"] for card in keyword_cards)
        for path in ("/s?i=books", "/s?k=&i=books"):
            with self.subTest(path=path):
                documents, category_cards = self.cards_for_total(path, 32)
                self.assertEqual(
                    [len(parser.cards) for parser, _ in documents], [16, 16]
                )
                self.assertEqual(
                    tuple(card["asin"] for card in category_cards), expected
                )
                for parser, html in documents:
                    self.assertTrue(parser.header_departments["books"])
                    self.assertIn('data-department="books"', html)
                    self.assertIn('data-source-rail="best-sellers-books"', html)

    def test_real_get_filters_sort_chips_clear_and_header_state(self) -> None:
        path = (
            "/s?k=portable+ssd&i=computers&brand=SanDisk"
            "&minPrice=100&maxPrice=300&rating=4-up&sort=price-asc"
        )
        parser, html = self.document(path)

        self.assertEqual(
            tuple(card["asin"] for card in parser.cards),
            ("B0C5JQ68FY", "B08GTYFC37", "B08GV9M64L"),
        )
        self.assertEqual(parser.selected_sort, "price-asc")
        self.assertEqual(
            {slug for slug, selected in parser.header_departments.items() if selected},
            {"computers"},
        )
        self.assertEqual(
            {
                link.get("aria-label")
                for link in parser.active_filter_links
            },
            {
                "Remove brand SanDisk",
                "Remove price filter",
                "Remove rating filter",
            },
        )
        self.assertEqual(
            parser.clear_filter_hrefs,
            ["/s?k=portable+ssd&i=computers"],
        )
        self.assertIn(
            '<form class="search-sort-form" method="get" action="/s">', html
        )
        self.assertNotIn("#", parser.main_hrefs)

    def test_availability_and_repeatable_brand_filters_use_real_get_state(self) -> None:
        cases = (
            (
                "/s?k=portable+ssd&availability=in-stock",
                ("B08HN37XC1", "B0874XN4D8", "B0CHFSWM2P"),
                {"Remove availability filter"},
            ),
            (
                "/s?k=portable+ssd&brand=Samsung&brand=SanDisk&sort=price-desc",
                (
                    "B08HN37XC1",
                    "B09VLK9W3S",
                    "B0CHFSWM2P",
                    "B0874XN4D8",
                    "B08GV9M64L",
                    "B08GTYFC37",
                    "B0C5JQ68FY",
                ),
                {"Remove brand Samsung", "Remove brand SanDisk"},
            ),
        )
        for path, expected_asins, expected_chips in cases:
            with self.subTest(path=path):
                parser, _ = self.document(path)
                self.assertEqual(
                    tuple(card["asin"] for card in parser.cards), expected_asins
                )
                self.assertEqual(
                    {
                        link.get("aria-label")
                        for link in parser.active_filter_links
                    },
                    expected_chips,
                )
                self.assertNotIn("#", parser.main_hrefs)

    def test_pager_preserves_keyword_department_and_sort_query_state(self) -> None:
        path = "/s?k=books&i=books&sort=rating-desc"
        first, _ = self.document(path)
        second, _ = self.document(f"{path}&page=2")

        self.assertEqual([len(first.cards), len(second.cards)], [16, 16])
        self.assertEqual(
            len({card["asin"] for card in first.cards + second.cards}), 32
        )
        self.assertEqual(first.selected_sort, "rating-desc")
        self.assertEqual(second.selected_sort, "rating-desc")
        self.assertTrue(first.header_departments["books"])
        self.assertTrue(second.header_departments["books"])
        self.assertGreater(len(first.pagination_hrefs), 0)
        self.assertGreater(len(second.pagination_hrefs), 0)

        for href in first.pagination_hrefs:
            with self.subTest(page="first", href=href):
                parts = urlsplit(href)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                self.assertEqual(parts.path, "/s")
                self.assertEqual(query["k"], "books")
                self.assertEqual(query["i"], "books")
                self.assertEqual(query["sort"], "rating-desc")
                self.assertEqual(query["page"], "2")
        for href in second.pagination_hrefs:
            with self.subTest(page="second", href=href):
                parts = urlsplit(href)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                self.assertEqual(parts.path, "/s")
                self.assertEqual(query["k"], "books")
                self.assertEqual(query["i"], "books")
                self.assertEqual(query["sort"], "rating-desc")
                self.assertNotIn("page", query)

    def test_mobile_filters_button_targets_the_live_filter_panel(self) -> None:
        parser, html = self.document("/s?k=portable+ssd")
        self.assertEqual(len(parser.filter_toggles), 1)
        toggle = parser.filter_toggles[0]
        self.assertEqual(toggle.get("type"), "button")
        self.assertEqual(toggle.get("aria-controls"), "search-refinements")
        self.assertEqual(toggle.get("aria-expanded"), "false")
        self.assertIn("☰ Filters", html)
        self.assertIn('id="search-refinements"', html)
        self.assertIn("data-search-filter-close", html)
        self.assertNotIn("#", parser.main_hrefs)

    def test_invalid_duplicate_and_out_of_range_search_parameters_return_400(self) -> None:
        paths = (
            "/s?k=portable+ssd&unsupported=value",
            "/s?k=portable+ssd&i=not-a-department",
            "/s?k=portable+ssd&sort=featured",
            "/s?k=portable+ssd&availability=yes",
            "/s?k=portable+ssd&brand=",
            "/s?k=one&k=two",
            "/s?k=one&field-keywords=two",
            "/s?k=portable+ssd&sort=relevance&sort=price-asc",
            "/s?k=portable+ssd&minPrice=1&minPrice=2",
            "/s?k=portable+ssd&page=1&page=2",
            "/s?k=portable+ssd&minPrice=300&maxPrice=100",
            "/s?k=portable+ssd&page=0",
            "/s?k=portable+ssd&page=1001",
            "/s?k=portable+ssd&page=4",
        )
        for path in paths:
            with self.subTest(path=path):
                status, _ = self.response(path)
                self.assertEqual(status, 400)

    def test_five_source_departments_are_dense_distinct_and_evidence_honest(self) -> None:
        expected_counts = {
            "books": 32,
            "home-kitchen": 22,
            "toys-games": 27,
            "computers": 31,
            "beauty-personal-care": 21,
        }
        expected_purchasable = {
            "books": 7,
            "home-kitchen": 6,
            "toys-games": 3,
            "computers": 16,
            "beauty-personal-care": 7,
        }
        expected_featured = {
            "books": "168281808X",
            "home-kitchen": "B01M16WBW1",
            "toys-games": "B0BG6B2D4D",
            "beauty-personal-care": "B074PVTPBW",
        }
        supplements_by_slug = {
            slug: tuple(
                product
                for product in (
                    *BROWSE_BREADTH["department_commerce_supplements"],
                    *BROWSE_BREADTH["search_commerce_cards"],
                )
                if slug in product["department_slugs"]
            )
            for slug in expected_counts
        }
        supplement_by_asin = {
            str(product["asin"]): product
            for products in supplements_by_slug.values()
            for product in products
        }
        all_department_asins: dict[str, tuple[object, ...]] = {}
        total_purchasable = 0
        for department in SOURCE_DEPARTMENTS:
            slug = str(department["slug"])
            rail_key = str(department["rail_key"])
            path = f"/s?i={slug}"
            with self.subTest(department=slug):
                expected_count = expected_counts[slug]
                documents, cards = self.cards_for_total(path, expected_count)
                source_asins = [
                    product["asin"]
                    for product in HOME_PRODUCT_CATALOG.values()
                    if product["placements"][0]["railKey"] == rail_key
                ]
                featured_asins = [
                    str(asin) for asin in department.get("featured_asins", ())
                ]
                source_order = (
                    [asin for asin in featured_asins if asin in source_asins]
                    + [asin for asin in source_asins if asin not in featured_asins]
                )
                supplemental_asins = [
                    product["asin"] for product in supplements_by_slug[slug]
                ]
                expected_asins = tuple(
                    [asin for asin in source_order if asin not in supplemental_asins]
                    + supplemental_asins
                )
                actual_asins = tuple(card["asin"] for card in cards)
                all_department_asins[slug] = actual_asins
                expected_page_lengths = [SEARCH_PAGE_SIZE]
                if expected_count > SEARCH_PAGE_SIZE:
                    expected_page_lengths.append(expected_count - SEARCH_PAGE_SIZE)
                self.assertEqual(
                    [len(parser.cards) for parser, _ in documents],
                    expected_page_lengths,
                )
                self.assertEqual(len(actual_asins), expected_count)
                self.assertEqual(actual_asins, expected_asins)
                self.assertEqual(len(set(actual_asins)), expected_count)
                for parser, html in documents:
                    self.assertIn(f'data-department="{slug}"', html)
                    self.assertIn(f'data-source-rail="{rail_key}"', html)
                    self.assertIn(str(department["title"]), parser.text)
                    self.assertTrue(parser.header_departments[slug])
                    self.assertNotIn("#", parser.main_hrefs)
                    for href in parser.pagination_hrefs:
                        self.assertIn(("i", slug), parse_qsl(urlsplit(href).query))

                    main_html = html.split('<main id="main"', 1)[1].split(
                        "</main>", 1
                    )[0]
                    self.assertNotIn("Hard Drive Size", main_html)
                    self.assertNotIn("Storage Capacity</h3>", main_html)
                    self.assertNotIn("Popular Shopping Ideas", main_html)

                if slug in expected_featured:
                    self.assertEqual(actual_asins[0], expected_featured[slug])

                purchasable = 0
                for card in cards:
                    asin = card["asin"]
                    assert isinstance(asin, str)
                    product = supplement_by_asin.get(asin) or HOME_PRODUCT_CATALOG[asin]
                    if product.get("evidence_tier") == "home-card-only":
                        self.assertEqual(card["evidence_level"], "homepage-browse")
                        self.assertFalse(card["has_price"])
                        self.assertFalse(card["has_rating"])
                        self.assertFalse(card["has_bought"])
                        self.assertEqual(card["forms"], [])
                    else:
                        if product.get("evidence_tier") == "pdp-direct":
                            self.assertEqual(card["evidence_level"], "verified-offer")
                        self.assertTrue(card["has_price"])
                        self.assertEqual(
                            card["has_rating"],
                            isinstance(product.get("rating"), str)
                            and bool(product.get("rating"))
                            and (
                                (
                                    isinstance(product.get("reviews"), int)
                                    and int(product.get("reviews", 0)) > 0
                                )
                                or bool(product.get("reviews_display"))
                            ),
                        )
                        self.assertIn("/gp/cart/add.html", card["forms"])
                        purchasable += 1
                self.assertEqual(purchasable, expected_purchasable[slug])
                total_purchasable += purchasable

        self.assertEqual(total_purchasable, 39)
        self.assertEqual(all_department_asins["home-kitchen"].count("B00FLYWNYQ"), 1)
        self.assertEqual(
            tuple(
                asin
                for asin in all_department_asins["computers"]
                if asin in SSD_SEARCH_ASINS
            ),
            tuple(
                str(product["asin"])
                for product in BROWSE_BREADTH["department_commerce_supplements"]
                if "computers" in product["department_slugs"]
            ),
        )
        for slug, products in supplements_by_slug.items():
            direct_search_asins = tuple(
                str(product["asin"])
                for product in products
                if product.get("evidence_class") == "direct-search-card"
            )
            self.assertEqual(
                tuple(
                    asin
                    for asin in all_department_asins[slug]
                    if asin in direct_search_asins
                ),
                direct_search_asins,
            )

    def test_header_and_all_directory_link_to_each_source_department(self) -> None:
        _, home_html = self.document("/")
        _, directory_html = self.document("/gp/site-directory")
        for department in SOURCE_DEPARTMENTS:
            slug = str(department["slug"])
            href = f"/s?i={slug}"
            with self.subTest(department=slug):
                self.assertIn(f'href="{href}"', home_html)
                self.assertIn(f'<option value="{slug}">', home_html)
                self.assertIn(f'href="{href}"', directory_html)

    def test_title_tokens_narrow_to_the_matching_home_product(self) -> None:
        parser, _ = self.document("/s?k=threshing")
        self.assertEqual(tuple(card["asin"] for card in parser.cards), ("168281808X",))
        card = parser.cards[0]
        self.assertEqual(
            set(card["hrefs"]),
            {"/Threshing-Day-Wing-Claw-Collection-Empyrean/dp/168281808X"},
        )
        self.assertTrue(card["has_price"])
        self.assertFalse(card["has_rating"])

    def test_new_direct_okapi_evidence_appears_in_query_results(self) -> None:
        parser, _ = self.document("/s?k=okapi")
        self.assertEqual(tuple(card["asin"] for card in parser.cards), ("B0BG6B2D4D",))
        card = parser.cards[0]
        self.assertEqual(set(card["hrefs"]), {"/Safari-Ltd-Okapi/dp/B0BG6B2D4D"})
        self.assertTrue(card["has_price"])
        self.assertTrue(card["has_rating"])
        self.assertFalse(card["has_bought"])

    def test_direct_pdp_fields_appear_only_when_the_evidence_contains_them(self) -> None:
        parser, _ = self.document("/s?k=queen+sheets")
        self.assertGreaterEqual(len(parser.cards), 1)
        self.assertEqual(parser.cards[0]["asin"], "B01M16WBW1")
        self.assertTrue(parser.cards[0]["has_price"])
        self.assertTrue(parser.cards[0]["has_rating"])
        self.assertTrue(parser.cards[0]["has_bought"])
        for card in parser.cards[1:]:
            self.assertFalse(card["has_bought"])
            if card["evidence_level"] == "direct-search-card":
                self.assertTrue(card["has_price"])
                self.assertTrue(card["has_rating"])
                self.assertIn("/gp/cart/add.html", card["forms"])
            else:
                self.assertFalse(card["has_price"])
                self.assertFalse(card["has_rating"])

    def test_unmatched_query_renders_a_real_zero_result_state(self) -> None:
        parser, html = self.document("/s?k=zzyzxxyy+qqqvvv")
        self.assertEqual(parser.cards, [])
        self.assertIn("No results for", parser.text)
        self.assertIn("0 results for", parser.text)
        self.assertNotIn("Clear filters</a>", html)
        self.assertIn("Return to Amazon home", parser.text)
        self.assertNotIn("Samsung T7 Portable SSD", html)

    def test_filtered_department_empty_state_names_the_department(self) -> None:
        parser, html = self.document("/s?i=books&brand=NoSuchBrand")
        self.assertEqual(parser.cards, [])
        self.assertIn("No results in Books", parser.text)
        self.assertNotIn("No results for “”", html)
        self.assertIn("Clear filters", parser.text)


if __name__ == "__main__":
    unittest.main()
