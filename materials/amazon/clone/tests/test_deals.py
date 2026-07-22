from __future__ import annotations

import hashlib
import http.client
import json
import sys
import tempfile
import threading
import unittest
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deals_catalog import DealsCatalogError, load_deals_catalog  # noqa: E402
from server import (  # noqa: E402
    BROWSE_BREADTH,
    DEALS_CATALOG,
    PublicHandler,
    ReusableThreadingHTTPServer,
    digest,
)
from store import Store  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"
DEALS_PATH = "/gp/goldbox/"
ADD_PATH = "/gp/cart/add.html"
CART_PATH = "/gp/cart/view.html"
CURRENT_DEALS_FIXTURE = ROOT / "fixtures" / "deals-current-2026-07-22.json"

CURRENT_DEALS_ASINS = (
    "B01LYNW421",
    "B095CN96JS",
    "B0BVZFQ4DF",
    "B06X9M6CW7",
    "B0C1GP88C4",
    "B0BV241H3F",
    "B0DQDQVTT3",
    "B09BWFX1L6",
    "B00EINBSEW",
    "B0FBSQX5T3",
)

DEFAULT_DEALS_ASINS = (
    "B01LYNW421",
    "B08HN37XC1",
    "B0BG6B2D4D",
    "B074PVTPBW",
    "B095CN96JS",
    "168281808X",
    "B0BV241H3F",
    "B01M16WBW1",
    "B0BJPXXM7D",
    "B0BQR2BQYZ",
    "B0BVZFQ4DF",
    "B071V91LGC",
    "B0DQDQVTT3",
    "B00FLYWNYQ",
    "B09BWFX1L6",
    "B0874XN4D8",
    "B0C1GP88C4",
    "B07K74LDCH",
    "B00EINBSEW",
    "B088BZTYFP",
    "B0FBSQX5T3",
    "B0CHFSWM2P",
    "B06X9M6CW7",
    "B08GTYFC37",
    "B0BGKXX9TK",
    "B0F6NKYDTY",
    "B0C5JQ68FY",
    "B08GV9M64L",
    "B09VLK9W3S",
)

LIMITED_TIME_ASINS = (
    "B01LYNW421",
    "B095CN96JS",
    "B0BV241H3F",
    "B01M16WBW1",
    "B0BQR2BQYZ",
    "B0BVZFQ4DF",
    "B0DQDQVTT3",
    "B09BWFX1L6",
    "B0C1GP88C4",
    "B00EINBSEW",
    "B0FBSQX5T3",
    "B06X9M6CW7",
)

EXPLICIT_DISCOUNTS = {
    "B01LYNW421": 30,
    "B095CN96JS": 15,
    "B0BV241H3F": 18,
    "B01M16WBW1": 29,
    "B0BQR2BQYZ": 10,
    "B0BVZFQ4DF": 27,
    "B0DQDQVTT3": 13,
    "B09BWFX1L6": 15,
    "B0C1GP88C4": 40,
    "B00EINBSEW": 20,
    "B0FBSQX5T3": 20,
    "B0CHFSWM2P": 17,
    "B06X9M6CW7": 15,
}


def normalized_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class DealsDocumentParser(HTMLParser):
    """Collect the Deals cards and their actual submitted quick-add fields."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self.current: dict[str, object] | None = None
        self.current_form: dict[str, object] | None = None
        self.main_attributes: dict[str, str | None] = {}
        self.inputs: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "main" and "data-deals-page" in attributes:
            self.main_attributes = attributes
        if tag == "article" and "deals-card" in classes:
            self.current = {
                "asin": attributes.get("data-asin"),
                "currency": attributes.get("data-currency"),
                "department": attributes.get("data-department"),
                "brand": attributes.get("data-brand"),
                "price_minor": attributes.get("data-price-minor"),
                "classes": set(classes),
                "hrefs": [],
                "images": [],
                "forms": [],
                "text_parts": [],
            }
            return
        if tag == "input":
            self.inputs.append(attributes)
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
            images.append(attributes)
        elif tag == "form":
            self.current_form = {
                "method": (attributes.get("method") or "get").lower(),
                "action": attributes.get("action") or "",
                "classes": set(classes),
                "fields": {},
            }
            forms = self.current["forms"]
            assert isinstance(forms, list)
            forms.append(self.current_form)
        elif tag == "input" and self.current_form is not None:
            fields = self.current_form["fields"]
            assert isinstance(fields, dict)
            name = attributes.get("name")
            if name:
                fields[name] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current_form = None
        if tag != "article" or self.current is None:
            return
        text_parts = self.current.pop("text_parts")
        assert isinstance(text_parts, list)
        self.current["text"] = normalized_text(text_parts)
        self.cards.append(self.current)
        self.current = None
        self.current_form = None

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        text_parts = self.current["text_parts"]
        assert isinstance(text_parts, list)
        text_parts.append(data)


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


FormFields = Mapping[str, str] | Sequence[tuple[str, str]]


class DealsHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.store = Store(
            Path(cls.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        cls.store.reset()
        QuietPublicHandler.store = cls.store
        cls.server = ReusableThreadingHTTPServer(("127.0.0.1", 0), QuietPublicHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address
        fixture = json.loads(CURRENT_DEALS_FIXTURE.read_text(encoding="utf-8"))
        cls.current_products = tuple(fixture["products"])
        cls.current_by_asin = {
            str(product["asin"]): product for product in cls.current_products
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tempdir.cleanup()

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
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
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

    def deals_document(
        self, path: str = DEALS_PATH
    ) -> tuple[DealsDocumentParser, str]:
        status, _, payload = self.request("GET", path)
        self.assertEqual(status, 200, path)
        html = payload.decode("utf-8")
        parser = DealsDocumentParser()
        parser.feed(html)
        return parser, html

    def filtered_asins(self, query: str) -> tuple[str, ...]:
        parser, _ = self.deals_document(f"{DEALS_PATH}?{query}")
        return tuple(str(card["asin"]) for card in parser.cards)

    def test_default_grid_is_29_cross_category_strict_offers(self) -> None:
        parser, html = self.deals_document()
        asins = tuple(str(card["asin"]) for card in parser.cards)
        self.assertEqual(asins, DEFAULT_DEALS_ASINS)
        self.assertEqual(len(asins), 29)
        self.assertEqual(len(set(asins)), 29)
        self.assertEqual(parser.main_attributes.get("data-offer-count"), "29")
        self.assertEqual(parser.main_attributes.get("data-deals-result-count"), "29")
        self.assertIn("Showing 29 of 29 source-backed offers", html)
        self.assertIn('<h1 class="sr-only">Today\'s Deals</h1>', html)
        self.assertNotIn('class="deals-heading"', html)
        self.assertNotIn('class="deals-filter-heading"', html)
        self.assertNotIn('class="deals-results-heading"', html)

        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(
            ".deals-content { display: grid; grid-template-columns: 188px minmax(0, 1fr);",
            styles,
        )
        self.assertIn(
            ".deals-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));",
            styles,
        )

        # The first fold deliberately mixes an unassigned current deal with
        # electronics, toys, and beauty instead of returning an SSD wall.
        self.assertEqual(
            tuple(card["department"] for card in parser.cards[:4]),
            ("", "electronics", "toys-games", "beauty-personal-care"),
        )
        for card in parser.cards:
            asin = str(card["asin"])
            self.assertEqual(card["currency"], "USD")
            forms = card["forms"]
            assert isinstance(forms, list)
            self.assertEqual(len(forms), 1, asin)
            self.assertEqual(forms[0]["action"], ADD_PATH)
            self.assertEqual(forms[0]["method"], "post")
            self.assertEqual(forms[0]["fields"], {"ASIN": asin, "quantity": "1"})
            images = card["images"]
            assert isinstance(images, list)
            self.assertEqual(len(images), 1, asin)
            self.assertTrue(str(images[0]["src"]).startswith("/static/"), asin)
            self.assertTrue(all(str(href).startswith("/") for href in card["hrefs"]))

        discount_asins = {
            str(card["asin"])
            for card in parser.cards
            if "deals-discount" in card["classes"]
        }
        limited_asins = {
            str(card["asin"])
            for card in parser.cards
            if "deals-limited-label" in card["classes"]
        }
        self.assertEqual(discount_asins, set(EXPLICIT_DISCOUNTS))
        self.assertEqual(limited_asins, set(LIMITED_TIME_ASINS))
        for card in parser.cards:
            asin = str(card["asin"])
            if asin in EXPLICIT_DISCOUNTS:
                self.assertIn(f"-{EXPLICIT_DISCOUNTS[asin]}%", card["text"])

    def test_current_cards_render_exact_evidence_and_keep_unknowns_unknown(self) -> None:
        parser, html = self.deals_document()
        cards = {str(card["asin"]): card for card in parser.cards}
        self.assertEqual(tuple(product["asin"] for product in self.current_products), CURRENT_DEALS_ASINS)
        for product in self.current_products:
            asin = str(product["asin"])
            card = cards[asin]
            expected_path = urlsplit(str(product["source_product_url"])).path
            whole, cents = divmod(int(product["price_minor"]), 100)
            reference_whole, reference_cents = divmod(
                int(product["reference_price_minor"]), 100
            )
            self.assertEqual(card["department"], "", asin)
            self.assertEqual(card["brand"], product["brand"] or "", asin)
            self.assertEqual(card["price_minor"], str(product["price_minor"]), asin)
            self.assertIn(expected_path, card["hrefs"], asin)
            self.assertIn(str(product["title"]), card["text"], asin)
            self.assertIn(f"${whole:,}.{cents:02d}", card["text"], asin)
            self.assertIn(
                f"{product['reference_price_label']}: ${reference_whole:,}.{reference_cents:02d}",
                card["text"],
                asin,
            )
            self.assertIn(f"-{product['discount_percent']}%", card["text"], asin)
            self.assertIn("Limited time deal", card["text"], asin)
            self.assertNotIn("deals-rating", card["classes"], asin)
            images = card["images"]
            assert isinstance(images, list)
            self.assertEqual(images[0]["src"], product["image_path"], asin)
            self.assertEqual(images[0]["alt"], product["image_alt"], asin)
            self.assertNotIn(str(product["source_product_url"]), html)
            self.assertNotIn(str(product["image_source_url"]), html)

            local_asset = ROOT / str(product["image_path"]).removeprefix("/")
            self.assertTrue(local_asset.is_file(), asin)
            asset_bytes = local_asset.read_bytes()
            self.assertEqual(hashlib.sha256(asset_bytes).hexdigest(), product["image_sha256"])
            self.assertIn(b"ftypavif", asset_bytes[:32], asin)

        unknown_brand_card = cards["B0C1GP88C4"]
        self.assertEqual(unknown_brand_card["brand"], "")
        self.assertNotIn("deals-brand", unknown_brand_card["classes"])
        self.assertNotIn("Deals from this brand", html)
        brand_values = {
            str(attributes.get("value") or "")
            for attributes in parser.inputs
            if attributes.get("name") == "brand"
        }
        self.assertNotIn("", brand_values)

        # Some audit rows retain a separately claimed percentage. It is not
        # the evidenced card discount and must never leak into presentation.
        self.assertNotIn("-8%", cards["B095CN96JS"]["text"])
        self.assertNotIn("-33%", cards["B0DQDQVTT3"]["text"])
        self.assertNotIn("-11%", cards["B09BWFX1L6"]["text"])

    def test_copyable_get_filters_apply_source_evidence_boundaries(self) -> None:
        self.assertEqual(self.filtered_asins("dealType=limited-time"), LIMITED_TIME_ASINS)
        self.assertEqual(self.filtered_asins("theme=lightning-deals"), ())
        self.assertEqual(
            self.filtered_asins("theme=electronics"),
            (
                "B08HN37XC1",
                "B0BJPXXM7D",
                "B0874XN4D8",
                "B0CHFSWM2P",
                "B08GTYFC37",
                "B0BGKXX9TK",
                "B0F6NKYDTY",
                "B0C5JQ68FY",
                "B08GV9M64L",
                "B09VLK9W3S",
            ),
        )
        self.assertEqual(
            self.filtered_asins("department=computers-accessories"),
            (
                "B0BJPXXM7D",
                "B0874XN4D8",
                "B07K74LDCH",
                "B0CHFSWM2P",
                "B08GTYFC37",
                "B0BGKXX9TK",
                "B0F6NKYDTY",
                "B0C5JQ68FY",
                "B08GV9M64L",
                "B09VLK9W3S",
            ),
        )
        self.assertEqual(
            self.filtered_asins("brand=Amazon+Basics&brand=Samsung"),
            (
                "B095CN96JS",
                "B09BWFX1L6",
                "B0874XN4D8",
                "B088BZTYFP",
                "B0CHFSWM2P",
                "B09VLK9W3S",
            ),
        )
        rating_asins = self.filtered_asins("rating=4-up")
        self.assertEqual(len(rating_asins), 18)
        self.assertTrue(set(CURRENT_DEALS_ASINS).isdisjoint(rating_asins))

    def test_price_and_discount_ranges_are_server_owned_and_copyable(self) -> None:
        parser, html = self.deals_document(
            f"{DEALS_PATH}?minPrice=300&maxPrice=316.99"
        )
        self.assertEqual(tuple(card["asin"] for card in parser.cards), ("B08HN37XC1",))
        self.assertIn('name="minPrice" min="0" max="316.99"', html)
        self.assertIn('name="maxPrice" min="0" max="316.99"', html)
        self.assertIn('value="300"', html)
        self.assertIn('value="316.99"', html)
        self.assertEqual(
            self.filtered_asins("minDiscount=20&maxDiscount=30"),
            (
                "B01LYNW421",
                "B01M16WBW1",
                "B0BVZFQ4DF",
                "B00EINBSEW",
                "B0FBSQX5T3",
            ),
        )
        # Unknown discounts do not acquire a percentage derived from a list
        # price just to satisfy a range filter.
        self.assertEqual(
            self.filtered_asins("maxDiscount=15"),
            (
                "B095CN96JS",
                "B0BQR2BQYZ",
                "B0DQDQVTT3",
                "B09BWFX1L6",
                "B06X9M6CW7",
            ),
        )

        invalid, invalid_html = self.deals_document(
            f"{DEALS_PATH}?minPrice=50&maxPrice=10"
        )
        self.assertEqual(invalid.cards, [])
        self.assertEqual(invalid.main_attributes.get("data-deals-result-count"), "0")
        self.assertIn('role="alert">Minimum price cannot exceed maximum price.', invalid_html)
        self.assertIn("No deals match these filters", invalid_html)

    def test_every_deal_quick_add_completes_the_http_cart_round_trip(self) -> None:
        status, headers, _ = self.request("GET", DEALS_PATH)
        self.assertEqual(status, 200)
        cookie = self.session_cookie(headers)
        for asin in DEFAULT_DEALS_ASINS:
            with self.subTest(asin=asin):
                status, response_headers, payload = self.request(
                    "POST",
                    ADD_PATH,
                    fields={"ASIN": asin, "quantity": "1"},
                    cookie=cookie,
                )
                self.assertEqual(status, 303, payload.decode("utf-8", errors="replace"))
                self.assertEqual(response_headers.get("location"), [CART_PATH])
                self.assertEqual(payload, b"")

        token = cookie.split("=", 1)[1]
        lines = self.store.cart(digest(token))
        line_asins = tuple(str(line["asin"]) for line in lines)
        self.assertEqual(len(line_asins), 29)
        self.assertEqual(set(line_asins), set(DEFAULT_DEALS_ASINS))
        self.assertTrue(all(int(line["quantity"]) == 1 for line in lines))
        by_asin = {str(line["asin"]): line for line in lines}
        for asin, product in self.current_by_asin.items():
            self.assertEqual(by_asin[asin]["price_minor"], product["price_minor"])
            self.assertEqual(by_asin[asin]["image_path"], product["image_path"])

        status, _, cart_html = self.request("GET", CART_PATH, cookie=cookie)
        self.assertEqual(status, 200)
        for asin in DEFAULT_DEALS_ASINS:
            self.assertIn(f'data-asin="{asin}"'.encode("ascii"), cart_html)

    def test_current_deal_pdps_are_live_and_card_evidence_only(self) -> None:
        for product in self.current_products:
            asin = str(product["asin"])
            path = urlsplit(str(product["source_product_url"])).path
            with self.subTest(asin=asin):
                status, _, payload = self.request("GET", path)
                self.assertEqual(status, 200, path)
                html = payload.decode("utf-8")
                decoded_html = unescape(html)
                whole, cents = divmod(int(product["price_minor"]), 100)
                reference_whole, reference_cents = divmod(
                    int(product["reference_price_minor"]), 100
                )
                self.assertIn('data-pdp-variant="direct-deals-card"', html)
                self.assertIn('data-evidence-level="direct-deals-card"', html)
                self.assertIn(f'data-asin="{asin}"', html)
                self.assertIn(str(product["title"]), decoded_html)
                self.assertIn(str(product["image_path"]), html)
                self.assertIn(str(product["image_alt"]), decoded_html)
                self.assertIn(f"${whole:,}.{cents:02d}", html)
                self.assertIn(
                    f"{product['reference_price_label']}: <del>"
                    f"${reference_whole:,}.{reference_cents:02d}</del>",
                    html,
                )
                self.assertIn(f"-{product['discount_percent']}%", html)
                self.assertIn("Limited time deal", html)
                self.assertIn(
                    "Rating, reviews, delivery, inventory, and product options were not captured.",
                    html,
                )
                self.assertIn('action="/gp/cart/add.html"', html)
                self.assertIn('action="/gp/buy/now"', html)
                self.assertNotIn('class="pdp-rating"', html)
                self.assertNotIn('id="reviewsMedley"', html)
                self.assertNotIn('id="deliveryBlockMessage"', html)
                self.assertNotIn("data-product-option", html)
                if product["brand"] is None:
                    self.assertNotIn('class="deal-card-pdp-brand"', html)
                else:
                    brand = str(product["brand"])
                    self.assertIn(
                        '<a class="deal-card-pdp-brand" '
                        f'href="/s?{urlencode({"k": brand})}">{brand}</a>',
                        decoded_html,
                    )


class DealsCatalogBoundaryTests(unittest.TestCase):
    def test_non_deals_commerce_offer_does_not_change_deals_membership(self) -> None:
        extra_offer = dict(BROWSE_BREADTH["verified_offers"][0])
        extra_offer.update(
            {
                "asin": "B00000001X",
                "slug": "future-source-backed-product",
                "canonical_path": "/future-source-backed-product/dp/B00000001X",
                "price_minor": 1234,
            }
        )
        expanded_pool = (*BROWSE_BREADTH["verified_offers"], extra_offer)

        catalog = load_deals_catalog(ROOT / "fixtures", expanded_pool)

        self.assertEqual(
            tuple(product["asin"] for product in catalog),
            DEFAULT_DEALS_ASINS,
        )
        self.assertNotIn(
            extra_offer["asin"],
            {product["asin"] for product in catalog},
        )

    def test_deals_metadata_still_requires_a_matching_offer(self) -> None:
        incomplete_pool = tuple(
            product
            for product in BROWSE_BREADTH["verified_offers"]
            if product["asin"] != "B08HN37XC1"
        )

        with self.assertRaisesRegex(
            DealsCatalogError,
            r"offers_missing=\['B08HN37XC1'\]",
        ):
            load_deals_catalog(ROOT / "fixtures", incomplete_pool)

    def test_server_catalog_marks_only_current_cards_as_card_only_evidence(self) -> None:
        self.assertEqual(len(DEALS_CATALOG), 29)
        current = {
            str(product["asin"]): product
            for product in DEALS_CATALOG
            if product.get("evidence_class") == "direct-deals-card"
        }
        self.assertEqual(set(current), set(CURRENT_DEALS_ASINS))
        for asin, product in current.items():
            with self.subTest(asin=asin):
                self.assertEqual(product["department"], "")
                self.assertEqual(product["department_slug"], "")
                self.assertEqual(product["themes"], ())
                self.assertIsNone(product["rating_value"])
                self.assertEqual(product["rating"], "")
                self.assertEqual(product["reviews"], 0)
                self.assertTrue(product["limited_time_deal"])


if __name__ == "__main__":
    unittest.main()
