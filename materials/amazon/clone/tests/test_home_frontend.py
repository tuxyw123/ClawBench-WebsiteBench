from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    PublicHandler,
    ReusableThreadingHTTPServer,
    SEARCH_COMMERCE_PRODUCT_CATALOG,
)
from home_catalog import load_home_product_catalog  # noqa: E402
from store import Store  # noqa: E402


FIRST_FOLD_CARD_TITLES = (
    "Get your game on",
    "Must-haves for every student",
    "Shop Fashion for less",
    "Must-have school supplies",
    "New home arrivals under $50",
    "Top categories in Kitchen appliances",
    "Fashion trends you like",
    "Easy updates for elevated spaces",
)

SOURCE_MODULE_TITLES = (
    "Related to items you've viewed",
    "Best Sellers in Home & Kitchen",
    "Gear up to get fit",
    "Have more fun with family",
    "Wireless Tech",
    "Gaming merchandise",
    "Top Sellers in Toys for you",
    "Best Sellers in Computers & Accessories",
    "Level up your gaming",
    "Deals on top categories",
    "Level up your beauty routine",
    "Level up your PC here",
    "Best Sellers in Books",
    "Top picks for Singapore",
    "Most-loved watches",
    "Finds for Home",
    "Transformers toys & more",
    "Discover these beauty products for you",
    "Best Sellers in Beauty & Personal Care",
)

PERSONALIZED_ASINS = (
    "B0CHFSWM2P",
    "B08HN37XC1",
    "B0874XN4D8",
    "B07CRG94G3",
    "B0F332MNX7",
    "B0C5JQ68FY",
    "B0BGL4SHY8",
    "B06W55K9N6",
    "B081SVSNVB",
    "B085L6TQ2S",
    "B0CKMWP1KH",
    "B0C4KKYF6Y",
    "B08BZ7Y8C7",
    "B01MSSJ32J",
    "B0DX68TWW4",
    "B08F27QGHX",
    "B0CMDJXZ19",
    "B0FV7VF5BQ",
    "B0DP35XJFQ",
    "B0CN6G33CW",
    "B0F9VY82J1",
    "B0C9WGS6MC",
    "B0CRYY9THJ",
    "B0DG16HCWB",
    "B09ZRD38D8",
)
PERSONALIZED_TITLES = (
    "Samsung T9 Portable SSD 1TB, USB 3.2 Gen 2x2 External Solid State Drive, Seq. Read Speeds Up to 2,000MB/s for Gaming,...",
    "SANDISK 2TB Extreme Portable SSD (Old Model) - Up to 1050MB/s, USB-C, USB 3.2 Gen 2, IP65 Water and Dust Resistance,...",
    "Samsung T7 Portable SSD, 1TB External Solid State Drive, Speeds Up to 1,050MB/s, USB 3.2 Gen 2, Reliable Storage for...",
    "Seagate Portable 2TB External Hard Drive HDD — USB 3.0 for PC, Mac, PlayStation, & Xbox -1-Year Rescue Service (STGX2000400)",
    "Crucial X10 2TB Portable SSD, Up to 2,100MB/s, USB 3.2 USB-C, External Solid State Drive, Compatible with Windows, Mac &...",
    "SANDISK 1TB Portable SSD - Up to 800MB/s, USB-C, USB 3.2 Gen 2, Updated Firmware - External Solid State Drive -...",
    "SSK Portable SSD 1TB External Solid State Drives, up to 1050MB/s USB C SSD External Hard Drive USB 3.2 Gen2 for iPhone...",
    "WD 2TB Elements Portable External Hard Drive for Windows, USB 3.2 Gen 1/USB 3.0 for PC & Mac, Plug and Play Ready -...",
    "Yinke Hard Case for SanDisk Extreme Pro/SanDisk Extreme Portable External SSD 500GB 1TB 2TB, Travel Case Protective...",
    "ProCase Hard Carrying Case for Samsung T7 Portable SSD -Black | Fits Samsung T7/T7 Touch SSD, 2 Cable Ties, Shockproof...",
    "Lacdo Hard Carrying Case for Samsung T9 Portable Solid State Drives 1TB 2TB 4TB USB 3.2 External SSD Hard EVA Shockproof...",
    "Case for SanDisk Extreme Portable SSD & Extreme PRO, Hard Shell Protective Storage Bag with Carabiner, Fits 1TB 2TB 4TB...",
    "ProCase Hard Carrying Case for Samsung T7 Portable SSD -Black | Fits Samsung T7/T7 Touch SSD, Silicone Cover Included,...",
    "LaCie Rugged USB-C, 4TB, Portable External Hard Drive, Drop, Shock, Dust, Rain Resistant, for Mac & PC (STFR4000800) |...",
    "Amazon Basics Portable External SSD, 2TB, 2000MB/s Speeds, USB 3.2 Gen 2, IP65 Water & Dust Resistant, Black",
    "Western Digital 1TB My Passport SSD Portable External Solid State Drive, Gray, Sturdy and Blazing Fast, Password...",
    "Samsung T5 EVO Portable SSD 4TB, USB 3.2 Gen 1 External Solid State Drive, Seq. Read Speeds Up to 460MB/s for Gaming and...",
    "UnionSine 1TB Ultra Slim Portable External Hard Drive HDD-USB 3.0 for PC, Mac, Laptop, PS4, Xbox one, Xbox 360-(Black)",
    "SANDISK 1TB Creator Pro Portable SSD - Up to 2000MB/s, for Laptops and Computers, USB-C, USB 3.2 Gen 2x2, IP65 Water and...",
    "Case Compatible with Samsung T9/ T7/ T7 Shield Portable SSD 1TB 2TB 4TB External Hard Drive, Storage Travel Carrying...",
    "GWCASE Hard Case for SanDisk Extreme Portable External Solid State Drive | Protective Storage Bag Fits for SanDisk...",
    "Crucial X10 Pro 2TB Portable SSD, Up to 2100MB/s Read, 2000MB/s Write, USB 3.2 USB-C, External Solid State Drive,...",
    "PAIYULE Carrying Case for Samsung T9/T7/T7 Shield Portable SSD 4TB 2TB 1TB, Hard Travel Storage Holder for USB External...",
    "Lexar 2TB Professional Go Portable SSD, Supports Apple 4K 60fps ProRes, Up to 1050MB/s, USB 3.2 Gen 2, Rugged, IP65,...",
    "Western Digital 1TB P40 Game Drive SSD - Up to 2,000MB/s, RGB Lighting, Portable External Solid State Drive , Compatible...",
)
PERSONALIZED_ASSET_ROOT = "/static/assets/source-current/2026-07-21/home/personalized"
TOP_PICKS_ASINS = (
    "B0CSD1FT18",
    "B0G1MQYHRD",
    "B00FLYWNYQ",
    "B0FTP511BW",
    "B0C9SWH3RC",
    "B0DP3JV2WB",
    "B0DXZW363G",
    "B0DKQ4RF3B",
    "B0FTPYT2H3",
    "B0DKTVC9CR",
    "B0FBPZ3RSB",
    "B0FJH6XRS3",
    "B07799WY99",
    "B003NMMVJ0",
    "B0DQPBR1RN",
    "B09BQD3YDY",
    "B0CG7DPXGW",
    "B0CLH89X2K",
    "B0FPF5QRV6",
    "B0007ZF4OA",
    "B000052Y5Q",
    "B0GT2JP76J",
    "B0FWBPFL4S",
    "B0G31J12SG",
    "B0DX2GJ1YR",
    "B0FKNGRQVR",
    "B0DK5VM9W2",
    "B0FY6T2FG6",
)
SOURCE_RAIL_TITLES = (
    "Related to items you've viewed",
    "Best Sellers in Home & Kitchen",
    "Top Sellers in Toys for you",
    "Best Sellers in Computers & Accessories",
    "Best Sellers in Books",
    "Top picks for Singapore",
    "Best Sellers in Beauty & Personal Care",
)
SOURCE_RAIL_ITEM_COUNTS = (25, 19, 26, 17, 26, 28, 16)
SOURCE_RAIL_KEYS = (
    "related-items",
    "best-sellers-home-kitchen",
    "top-sellers-toys",
    "best-sellers-computers-accessories",
    "best-sellers-books",
    "top-picks-singapore",
    "best-sellers-beauty-personal-care",
)

VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)", re.IGNORECASE)
MIN_WIDTH_RE = re.compile(r"(?:^|;)\s*min-width\s*:\s*([^;!}]+)", re.IGNORECASE)


def normalized_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


@dataclass
class HomeCard:
    classes: set[str]
    headings: list[str]
    images: list[str]
    tiles: list[dict[str, object]]


@dataclass(frozen=True)
class CssRule:
    selectors: tuple[str, ...]
    declarations: str
    media: tuple[str, ...]


class HomeDocumentParser(HTMLParser):
    """Extract home-page contracts without relying on formatting or attr order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._heading_parts: list[str] | None = None
        self._active_card: HomeCard | None = None
        self._active_tile: dict[str, object] | None = None
        self.main_seen = False
        self.main_headings: list[str] = []
        self.main_images: list[str] = []
        self.runtime_resource_urls: list[str] = []
        self.cards: list[HomeCard] = []
        self.stylesheets: list[str] = []
        self.primary_nav_hrefs: list[str] = []

    @property
    def in_main(self) -> bool:
        return any(tag == "main" and attrs.get("id") == "main" for tag, attrs in self._stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        starting_main = tag == "main" and attributes.get("id") == "main"
        inside_main = self.in_main or starting_main

        if starting_main:
            self.main_seen = True
        if tag == "link" and "stylesheet" in attributes.get("rel", "").lower().split():
            self.stylesheets.append(attributes.get("href", ""))
        if tag in {"img", "script", "source", "video", "audio", "iframe", "embed"}:
            for attribute in ("src", "poster"):
                if attributes.get(attribute):
                    self.runtime_resource_urls.append(attributes[attribute])
        if tag == "link" and set(attributes.get("rel", "").lower().split()).intersection(
            {"stylesheet", "icon", "preload", "modulepreload"}
        ):
            if attributes.get("href"):
                self.runtime_resource_urls.append(attributes["href"])
        if tag == "a" and any(
            ancestor_tag == "nav" and "nav-secondary" in ancestor_attrs.get("class", "").split()
            for ancestor_tag, ancestor_attrs in self._stack
        ):
            self.primary_nav_hrefs.append(attributes.get("href", ""))
        if inside_main:
            if tag == "article" and "home-card" in classes:
                self._active_card = HomeCard(classes, [], [], [])
            if tag == "a" and "home-tile" in classes and self._active_card is not None:
                self._active_tile = {
                    "href": attributes.get("href", ""),
                    "images": [],
                    "caption_parts": [],
                }
            if tag == "h2":
                self._heading_parts = []
            if tag == "img" and attributes.get("src"):
                source = attributes["src"]
                self.main_images.append(source)
                if self._active_card is not None:
                    self._active_card.images.append(source)
                if self._active_tile is not None:
                    tile_images = self._active_tile["images"]
                    assert isinstance(tile_images, list)
                    tile_images.append(source)

        if tag not in VOID_ELEMENTS:
            self._stack.append((tag, attributes))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h2" and self._heading_parts is not None:
            heading = normalized_text(self._heading_parts)
            self.main_headings.append(heading)
            if self._active_card is not None:
                self._active_card.headings.append(heading)
            self._heading_parts = None
        elif tag == "a" and self._active_tile is not None:
            if self._active_card is not None:
                self._active_card.tiles.append(self._active_tile)
            self._active_tile = None
        elif tag == "article" and self._active_card is not None:
            self.cards.append(self._active_card)
            self._active_card = None

        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._heading_parts is not None:
            self._heading_parts.append(data)
        if self._active_tile is not None:
            caption_parts = self._active_tile["caption_parts"]
            assert isinstance(caption_parts, list)
            caption_parts.append(data)


class PersonalizedRailParser(HTMLParser):
    """Extract the frozen item contract from the one source-backed product rail."""

    TARGET_LABEL = "Related to items you've viewed"

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rail_labels: list[str] = []
        self.target_count = 0
        self.items: list[dict[str, str]] = []
        self._in_target = False
        self._active_item: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "section" and "home-rail" in classes:
            label = attributes.get("aria-label", "")
            self.rail_labels.append(label)
            if label == self.TARGET_LABEL:
                self.target_count += 1
                self._in_target = True
        elif tag == "a" and self._in_target and "home-rail-item" in classes:
            self._active_item = {
                "asin": attributes.get("data-asin", ""),
                "href": attributes.get("href", ""),
            }
        elif tag == "img" and self._active_item is not None:
            for name in ("src", "alt", "width", "height"):
                self._active_item[name] = attributes.get(name, "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_item is not None:
            self.items.append(self._active_item)
            self._active_item = None
        elif tag == "section" and self._in_target:
            self._in_target = False


class HomeRailsParser(HTMLParser):
    """Collect each home rail and its item-level link/image contract in DOM order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rails: list[dict[str, object]] = []
        self._active_rail: dict[str, object] | None = None
        self._active_item: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "section" and "home-rail" in classes:
            self._active_rail = {
                "label": attributes.get("aria-label", ""),
                "items": [],
            }
        elif tag == "a" and self._active_rail is not None and "home-rail-item" in classes:
            self._active_item = {
                "asin": attributes.get("data-asin", ""),
                "href": attributes.get("href", ""),
            }
        elif tag == "img" and self._active_item is not None:
            for name in ("src", "alt", "width", "height"):
                self._active_item[name] = attributes.get(name, "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_item is not None:
            if self._active_rail is not None:
                items = self._active_rail["items"]
                assert isinstance(items, list)
                items.append(self._active_item)
            self._active_item = None
        elif tag == "section" and self._active_rail is not None:
            self.rails.append(self._active_rail)
            self._active_rail = None


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


def css_rules(css: str) -> list[CssRule]:
    """Return ordinary rules with the @media blocks that contain them."""

    rules: list[CssRule] = []
    source = CSS_COMMENT_RE.sub("", css)

    def walk(fragment: str, media: tuple[str, ...]) -> None:
        cursor = 0
        while True:
            opening = fragment.find("{", cursor)
            if opening < 0:
                return
            prelude = fragment[cursor:opening].strip()
            depth = 1
            closing = opening + 1
            quote = ""
            while closing < len(fragment) and depth:
                character = fragment[closing]
                if quote:
                    if character == quote and fragment[closing - 1] != "\\":
                        quote = ""
                elif character in {"'", '"'}:
                    quote = character
                elif character == "{":
                    depth += 1
                elif character == "}":
                    depth -= 1
                closing += 1
            if depth:
                return

            body = fragment[opening + 1 : closing - 1]
            lowered = prelude.lower()
            if lowered.startswith("@media"):
                walk(body, (*media, prelude))
            elif lowered.startswith(("@supports", "@layer", "@container")):
                walk(body, media)
            elif not lowered.startswith("@keyframes"):
                selectors = tuple(part.strip() for part in prelude.split(",") if part.strip())
                if selectors:
                    rules.append(CssRule(selectors, body, media))
            cursor = closing

    walk(source, ())
    return rules


def media_applies(media: tuple[str, ...], width: int) -> bool:
    for condition in media:
        for minimum in re.findall(r"min-width\s*:\s*(\d+)px", condition, re.IGNORECASE):
            if width < int(minimum):
                return False
        for maximum in re.findall(r"max-width\s*:\s*(\d+)px", condition, re.IGNORECASE):
            if width > int(maximum):
                return False
    return True


class HomeFrontendTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, list[str]], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        response_headers: dict[str, list[str]] = {}
        for name, value in response.getheaders():
            response_headers.setdefault(name.lower(), []).append(value)
        payload = response.read()
        connection.close()
        return response.status, response_headers, payload

    def document(self) -> tuple[HomeDocumentParser, str]:
        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        parser = HomeDocumentParser()
        parser.feed(html)
        self.assertTrue(parser.main_seen, "home response needs main#main")
        return parser, html

    def styles(self, parser: HomeDocumentParser) -> list[tuple[str, str]]:
        self.assertTrue(parser.stylesheets, "home response needs a local stylesheet")
        styles: list[tuple[str, str]] = []
        for href in parser.stylesheets:
            parsed = urlsplit(href)
            self.assertEqual(parsed.scheme, "", href)
            self.assertEqual(parsed.netloc, "", href)
            self.assertTrue(parsed.path.startswith("/static/"), href)
            status, _, body = self.request("GET", href)
            self.assertEqual(status, 200, href)
            styles.append((href, body.decode("utf-8")))
        return styles

    def test_home_first_fold_has_the_eight_frozen_cards_in_source_order(self) -> None:
        parser, _ = self.document()
        self.assertGreaterEqual(len(parser.cards), len(FIRST_FOLD_CARD_TITLES))
        actual = tuple(
            card.headings[0] if len(card.headings) == 1 else ""
            for card in parser.cards[: len(FIRST_FOLD_CARD_TITLES)]
        )
        self.assertEqual(actual, FIRST_FOLD_CARD_TITLES)

    def test_primary_nav_preserves_source_destination_parameters(self) -> None:
        parser, _ = self.document()
        self.assertEqual(
            tuple(parser.primary_nav_hrefs),
            (
                "/gp/site-directory",
                "/s?i=books",
                "/s?i=home-kitchen",
                "/s?i=toys-games",
                "/s?i=computers",
                "/s?i=beauty-personal-care",
                "/gp/goldbox/",
                "/Amazon-Video/b/?ie=UTF8&node=2858778011",
                "/gift-cards/b/?ie=UTF8&node=2238192011",
                "/b/?_encoding=UTF8&ld=AZUSSOA-sell&node=12766669011",
                "/gp/help/customer/display.html?nodeId=508510",
                "/gp/browse.html?node=16115931011",
            ),
        )

    def test_every_header_destination_is_live_and_no_header_link_is_a_hash_placeholder(self) -> None:
        _, html = self.document()
        header_start = html.index('<header class="site-header')
        header_end = html.index("</header>", header_start)
        header_markup = html[header_start:header_end]
        self.assertNotIn('href="#"', header_markup)
        self.assertIn('href="/gp/site-directory"', header_markup)
        self.assertIn('href="/gp/delivery/ajax/address-change.html"', header_markup)
        self.assertIn(
            'href="/customer-preferences/edit?preferencesReturnUrl=%2F"',
            header_markup,
        )

        public_paths = (
            "/ref=nav_logo",
            "/gp/site-directory",
            "/gp/delivery/ajax/address-change.html",
            "/customer-preferences/edit?preferencesReturnUrl=%2F",
            "/gp/cart/view.html",
            "/gp/goldbox/",
            "/Amazon-Video/b/?ie=UTF8&node=2858778011",
            "/gift-cards/b/?ie=UTF8&node=2238192011",
            "/b/?_encoding=UTF8&ld=AZUSSOA-sell&node=12766669011",
            "/gp/help/customer/display.html?nodeId=508510",
            "/gp/browse.html?node=16115931011",
        )
        for path in public_paths:
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(b'<main id="main"', body)

        for path in ("/gp/css/homepage.html", "/gp/css/order-history"):
            with self.subTest(path=path):
                status, headers, _ = self.request("GET", path)
                self.assertEqual(status, 303)
                self.assertTrue(headers.get("location", [""])[0].startswith("/ap/signin?"))

        status, _, deals_body = self.request("GET", "/gp/goldbox/")
        self.assertEqual(status, 200)
        self.assertNotIn(b"being prepared", deals_body)
        self.assertIn(b"/dp/", deals_body)

    def test_all_navigation_is_an_accessible_drawer_with_a_live_no_script_fallback(self) -> None:
        parser, html = self.document()
        self.assertEqual(html.count("data-all-menu-trigger"), 2)
        self.assertGreaterEqual(html.count('href="/gp/site-directory"'), 3)
        self.assertIn('data-all-menu-root aria-hidden="true"', html)
        self.assertIn(
            'id="nav-all-menu" class="nav-drawer" role="dialog" aria-modal="true"',
            html,
        )
        self.assertIn('aria-controls="nav-all-menu" aria-expanded="false"', html)
        for heading in (
            "Digital Content &amp; Devices",
            "Shop by Department",
            "Programs &amp; Features",
            "Help &amp; Settings",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, html)
        for destination in (
            "/s?i=books",
            "/s?i=home-kitchen",
            "/s?i=toys-games",
            "/s?i=computers",
            "/s?i=beauty-personal-care",
            "/gp/goldbox/",
            "/gp/help/customer/display.html?nodeId=508510",
        ):
            with self.subTest(destination=destination):
                self.assertIn(f'href="{destination}"', html)

        styles = "\n".join(css for _, css in self.styles(parser))
        self.assertIn(".nav-drawer-layer.is-open", styles)
        self.assertIn(".nav-drawer-layer.is-open .nav-drawer", styles)
        self.assertIn("body.all-menu-open", styles)

        status, _, script_body = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        script = script_body.decode("utf-8")
        self.assertIn('document.querySelectorAll("[data-all-menu-trigger]")', script)
        self.assertIn('document.body.classList.toggle("all-menu-open", open)', script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key !== "Tab"', script)
        self.assertIn("lastAllMenuTrigger.focus({ preventScroll: true })", script)

    def test_customer_service_hub_has_search_and_real_support_destinations(self) -> None:
        status, _, body = self.request(
            "GET", "/gp/help/customer/display.html?nodeId=508510"
        )
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertIn('data-help-page="customer-service"', html)
        self.assertIn("What can we help you with?", html)
        self.assertIn('class="help-search"', html)
        self.assertIn('name="help_keywords"', html)
        self.assertIn('name="nodeId" value="508510"', html)
        self.assertNotIn("navigation-landing-main", html)
        for label, destination in (
            ("Your Orders", "/gp/css/order-history"),
            ("Returns &amp; refunds", "/gp/help/customer/display.html?nodeId=201819200"),
            ("Shipping &amp; delivery", "/gp/help/customer/display.html?nodeId=468520"),
            ("Gift Cards &amp; gifts", "/gift-cards/b/?ie=UTF8&amp;node=2238192011"),
            ("Cart &amp; checkout", "/gp/cart/view.html"),
        ):
            with self.subTest(label=label):
                self.assertIn(label, html)
                self.assertIn(f'href="{destination}"', html)

        status, _, body = self.request(
            "GET",
            "/gp/help/customer/display.html?nodeId=508510&help_keywords=return",
        )
        self.assertEqual(status, 200)
        result_html = body.decode("utf-8")
        self.assertIn("Help results for “return”", result_html)
        result_start = result_html.index('class="help-search-results"')
        result_end = result_html.index("</section>", result_start)
        self.assertIn("Returns &amp; refunds", result_html[result_start:result_end])

        status, _, body = self.request(
            "GET",
            "/gp/help/customer/display.html?nodeId=508510&help_keywords=zzznomatch",
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "No exact help result for “zzznomatch”", body.decode("utf-8")
        )

    def test_shipping_returns_and_gift_cards_are_dedicated_navigation_pages(self) -> None:
        cases = (
            (
                "/gp/help/customer/display.html?nodeId=468520",
                'data-help-page="shipping-policies"',
                "Shipping Rates &amp; Policies",
            ),
            (
                "/gp/help/customer/display.html?nodeId=201819200",
                'data-help-page="returns-replacements"',
                "Returns &amp; Replacements",
            ),
            (
                "/gift-cards/b/?ie=UTF8&node=2238192011",
                'data-navigation-page="gift-cards"',
                "Amazon Gift Cards",
            ),
        )
        responses: dict[str, str] = {}
        for path, marker, heading in cases:
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 200)
                html = body.decode("utf-8")
                responses[path] = html
                self.assertIn(marker, html)
                self.assertIn(heading, html)
                self.assertNotIn("navigation-landing-main", html)

        shipping = responses[cases[0][0]]
        self.assertIn("Standard delivery", shipping)
        self.assertIn("FREE", shipping)
        self.assertIn("Expedited delivery", shipping)
        self.assertIn("$12.99", shipping)
        self.assertIn('href="/gp/delivery/ajax/address-change.html"', shipping)
        self.assertIn('href="/gp/css/order-history"', shipping)

        returns = responses[cases[1][0]]
        self.assertIn('class="return-steps"', returns)
        self.assertIn("Open Your Orders", returns)
        self.assertIn('href="/gp/css/order-history"', returns)

        gift_cards = responses[cases[2][0]]
        for destination in (
            "/s?i=books",
            "/s?i=home-kitchen",
            "/s?i=toys-games",
            "/s?i=beauty-personal-care",
            "/gp/goldbox/",
        ):
            self.assertIn(f'href="{destination}"', gift_cards)
        self.assertIn("Shop gifts by department", gift_cards)
        self.assertIn("Sign in", gift_cards)

        with self.store.connect() as connection:
            route_keys = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT route_key FROM navigation_events"
                ).fetchall()
            }
        self.assertTrue(
            {"SHIPPING_POLICIES", "RETURNS_REPLACEMENTS", "GIFT_CARDS"}.issubset(
                route_keys
            )
        )

    def test_footer_core_support_and_gift_links_are_live(self) -> None:
        _, html = self.document()
        footer_start = html.index('<footer class="site-footer')
        footer_end = html.index("</footer>", footer_start)
        footer = html[footer_start:footer_end]
        labels = (
            "Shipping Rates &amp; Policies",
            "Returns &amp; Replacements",
            "Help",
            "Registry &amp; Gift List",
            "Sell products on Amazon",
            "Customer Service",
            "Gift Cards",
            "Find a Gift",
            "Your Returns",
        )
        for label in labels:
            with self.subTest(label=label):
                matches = re.findall(
                    rf'<a href="([^"]+)"[^>]*>{re.escape(label)}</a>', footer
                )
                self.assertTrue(matches, label)
                self.assertTrue(all(href != "#" for href in matches))
        self.assertEqual(footer.count('class="footer-locale-button"'), 3)
        locale_start = footer.index('<div class="footer-locale">')
        locale_end = footer.index("</div>", locale_start)
        self.assertNotIn("<button", footer[locale_start:locale_end])

        parser = HomeDocumentParser()
        parser.feed(html)
        styles = "\n".join(css for _, css in self.styles(parser))
        for selector in (
            ".help-topic-grid",
            ".help-article-layout",
            ".shipping-choice-grid",
            ".return-steps",
            ".gift-cards-hero",
            ".gift-departments",
        ):
            self.assertIn(selector, styles)

    def test_home_contains_the_complete_current_source_module_sequence(self) -> None:
        parser, _ = self.document()
        expected = (*FIRST_FOLD_CARD_TITLES, *SOURCE_MODULE_TITLES)
        self.assertEqual(tuple(parser.main_headings), expected)
        self.assertEqual(len(expected), 27)
        self.assertEqual(len(parser.cards), 20)

    def test_home_has_one_source_backed_personalized_rail_with_exact_items(self) -> None:
        parser, html = self.document()
        personalized = PersonalizedRailParser()
        personalized.feed(html)

        self.assertEqual(personalized.target_count, 1)
        self.assertNotIn("See personalized recommendations", parser.main_headings)
        self.assertNotIn('class="home-personalized"', html)
        self.assertNotIn(
            "Customers who viewed items in your browsing history also viewed",
            personalized.rail_labels,
        )
        self.assertEqual(tuple(item["asin"] for item in personalized.items), PERSONALIZED_ASINS)
        self.assertEqual(tuple(item["alt"] for item in personalized.items), PERSONALIZED_TITLES)
        for asin, item in zip(PERSONALIZED_ASINS, personalized.items, strict=True):
            with self.subTest(asin=asin):
                self.assertEqual(item["href"], f"/dp/{asin}")
                self.assertEqual(item["src"], f"{PERSONALIZED_ASSET_ROOT}/{asin}.jpg")
                self.assertEqual(item["height"], "200")
                self.assertEqual(item["width"], "", "personalized images must keep intrinsic widths")

        rules = [rule for _, css in self.styles(parser) for rule in css_rules(css)]
        product_image_rules = [
            rule
            for rule in rules
            if ".home-product-rail .home-rail-item img" in rule.selectors
            and media_applies(rule.media, 1280)
        ]
        self.assertTrue(product_image_rules)
        self.assertTrue(
            any(
                re.search(r"width\s*:\s*auto", rule.declarations, re.IGNORECASE)
                and re.search(r"height\s*:\s*200px", rule.declarations, re.IGNORECASE)
                for rule in product_image_rules
            ),
            "personalized rail art must preserve intrinsic width at the source 200px height",
        )

    def test_top_picks_rail_uses_the_exact_source_item_order(self) -> None:
        _, html = self.document()
        rail_parser = HomeRailsParser()
        rail_parser.feed(html)
        matching = [rail for rail in rail_parser.rails if rail["label"] == "Top picks for Singapore"]
        self.assertEqual(len(matching), 1)
        items = matching[0]["items"]
        assert isinstance(items, list)
        self.assertEqual(tuple(item["asin"] for item in items), TOP_PICKS_ASINS)
        for asin, item in zip(TOP_PICKS_ASINS, items, strict=True):
            with self.subTest(asin=asin):
                self.assertEqual(item["href"], f"/dp/{asin}")
                self.assertEqual(
                    item["src"],
                    f"/static/assets/source-current/2026-07-21/home/top-picks-singapore/{asin}.jpg",
                )
                self.assertTrue(item["alt"])
                self.assertEqual(item["height"], "200")
                self.assertEqual(item["width"], "")

    def test_home_rail_fixture_is_self_contained_and_complete(self) -> None:
        fixture_path = ROOT / "fixtures" / "home-rails.json"
        fixture_text = fixture_path.read_text(encoding="utf-8")
        fixture = json.loads(fixture_text)
        self.assertEqual(fixture["schema"], "amazon-home-rails-fixture.v1")
        rails = fixture["rails"]
        self.assertEqual(tuple(rail["key"] for rail in rails), SOURCE_RAIL_KEYS)
        self.assertEqual(tuple(len(rail["items"]) for rail in rails), SOURCE_RAIL_ITEM_COUNTS)
        self.assertNotRegex(fixture_text, r"https?://")

        asset_root = ROOT / "static" / "assets" / "source-current" / "2026-07-21" / "home"
        for rail in rails:
            for item in rail["items"]:
                with self.subTest(rail=rail["key"], asin=item["asin"]):
                    self.assertEqual(item["href"], f'/dp/{item["asin"]}')
                    image_path = Path(item["imagePath"])
                    self.assertFalse(image_path.is_absolute())
                    self.assertNotIn("..", image_path.parts)
                    self.assertTrue((asset_root / image_path).is_file())

    def test_every_main_image_is_a_nonempty_local_static_asset(self) -> None:
        parser, _ = self.document()
        self.assertTrue(parser.main_images, "home main needs image content")
        static_root = (ROOT / "static").resolve()
        for source in dict.fromkeys(parser.main_images):
            with self.subTest(source=source):
                parsed = urlsplit(source)
                self.assertEqual(parsed.scheme, "", source)
                self.assertEqual(parsed.netloc, "", source)
                self.assertTrue(parsed.path.startswith("/static/"), source)
                candidate = (ROOT / parsed.path.lstrip("/")).resolve()
                self.assertTrue(candidate.is_relative_to(static_root), source)
                self.assertTrue(candidate.is_file(), source)
                self.assertGreater(candidate.stat().st_size, 0, source)
                status, _, payload = self.request("GET", source)
                self.assertEqual(status, 200, source)
                self.assertGreater(len(payload), 0, source)

    def test_home_cards_separate_heading_images_and_tile_captions(self) -> None:
        parser, _ = self.document()
        first_fold = parser.cards[: len(FIRST_FOLD_CARD_TITLES)]
        self.assertEqual(len(first_fold), len(FIRST_FOLD_CARD_TITLES))
        for position, card in enumerate(first_fold, start=1):
            with self.subTest(card=position):
                self.assertEqual(len(card.headings), 1)
                self.assertTrue(card.headings[0])
                self.assertTrue(card.images)
                if "home-card-quad" in card.classes:
                    self.assertEqual(len(card.tiles), 4)
                    for tile in card.tiles:
                        images = tile["images"]
                        caption_parts = tile["caption_parts"]
                        assert isinstance(images, list)
                        assert isinstance(caption_parts, list)
                        self.assertEqual(len(images), 1)
                        self.assertTrue(normalized_text(caption_parts), "tile caption must be text, not an image background")

    def test_wireless_tech_card_uses_the_exact_source_tiles(self) -> None:
        parser, _ = self.document()
        cards = [card for card in parser.cards if card.headings == ["Wireless Tech"]]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        expected = (
            ("Smartphones", "/s?k=smartphones", "/wireless-tech/smartphones.jpg"),
            ("Watches", "/s?k=smart+watches", "/wireless-tech/watches.jpg"),
            ("Headphones", "/s?k=headphones", "/wireless-tech/headphones.jpg"),
            ("Tablets", "/s?k=tablets", "/wireless-tech/tablets.jpg"),
        )
        self.assertEqual(len(card.tiles), 4)
        for tile, (label, href, image_suffix) in zip(card.tiles, expected, strict=True):
            caption_parts = tile["caption_parts"]
            images = tile["images"]
            assert isinstance(caption_parts, list)
            assert isinstance(images, list)
            with self.subTest(label=label):
                self.assertEqual(normalized_text(caption_parts), label)
                self.assertEqual(tile["href"], href)
                self.assertEqual(len(images), 1)
                self.assertTrue(str(images[0]).endswith(image_suffix), images[0])

    def test_home_runtime_resources_do_not_use_remote_http_urls(self) -> None:
        parser, _ = self.document()
        for resource in parser.runtime_resource_urls:
            with self.subTest(resource=resource):
                self.assertNotIn(urlsplit(resource).scheme.lower(), {"http", "https"})
        for stylesheet_href, css in self.styles(parser):
            for reference in CSS_URL_RE.findall(css):
                resolved = urljoin(stylesheet_href, reference.strip())
                with self.subTest(resource=resolved):
                    self.assertNotIn(urlsplit(resolved).scheme.lower(), {"http", "https"})

    def test_home_rails_keep_every_item_reachable_and_overlay_visible(self) -> None:
        parser, html = self.document()
        rail_parser = HomeRailsParser()
        rail_parser.feed(html)
        self.assertEqual(tuple(rail["label"] for rail in rail_parser.rails), SOURCE_RAIL_TITLES)
        rail_counts: list[int] = []
        for rail in rail_parser.rails:
            items = rail["items"]
            assert isinstance(items, list)
            rail_counts.append(len(items))
            for item in items:
                with self.subTest(rail=rail["label"], asin=item["asin"]):
                    self.assertTrue(item["asin"])
                    self.assertEqual(item["href"], f'/dp/{item["asin"]}')
                    self.assertTrue(item["src"].startswith("/static/assets/source-current/2026-07-21/home/"))
                    self.assertTrue(item["alt"])
                    self.assertEqual(item["height"], "200")
                    self.assertEqual(item["width"], "")
        self.assertEqual(tuple(rail_counts), SOURCE_RAIL_ITEM_COUNTS)
        self.assertNotIn("Customers who viewed items in your browsing history also viewed", html)
        self.assertNotIn("Best Sellers in Sports &amp; Outdoors", html)

        rails = re.findall(r'<section class="home-rail(?: [^"]*)?".*?</section>', html, re.DOTALL)
        self.assertEqual(len(rails), 7)
        for position, rail in enumerate(rails, start=1):
            with self.subTest(rail=position):
                self.assertIn("data-home-rail-track", rail)
                self.assertIn("data-home-rail-previous", rail)
                self.assertIn("data-home-rail-next", rail)
                self.assertGreaterEqual(rail.count('class="home-rail-item"'), 8)

        rules = [rule for _, css in self.styles(parser) for rule in css_rules(css)]
        track_rules = [rule for rule in rules if ".home-rail-track" in rule.selectors]
        self.assertTrue(track_rules)
        self.assertTrue(
            any(re.search(r"overflow-x\s*:\s*auto", rule.declarations, re.IGNORECASE) for rule in track_rules),
            "home rails must expose overflowed items through horizontal scrolling",
        )
        main_rules = [
            rule
            for rule in rules
            if ".home-main" in rule.selectors and media_applies(rule.media, 1365)
        ]
        self.assertTrue(main_rules)
        for rule in main_rules:
            self.assertIsNone(
                re.search(r"overflow\s*:\s*(?:clip|hidden)", rule.declarations, re.IGNORECASE),
                "home-main must not clip the source-positioned delivery overlay",
            )

        status, _, script = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        behavior = script.decode("utf-8")
        self.assertIn("[data-home-rail-previous]", behavior)
        self.assertIn("[data-home-rail-next]", behavior)
        self.assertIn("track.scrollBy", behavior)
        self.assertIn("explicitMobilePdp", behavior)
        self.assertIn("[data-product-option]", behavior)
        self.assertIn("data-product-option-field", behavior)
        self.assertIn("[data-product-quote-matrix]", behavior)
        self.assertIn("quoteBySelection", behavior)
        self.assertIn("writeQuotedPrice", behavior)
        self.assertIn("writeUnavailablePrice", behavior)
        self.assertIn("control.dataset.optionImage", behavior)
        self.assertIn('target.textContent = "—"', behavior)
        self.assertIn("No verified offer for this selection", behavior)
        self.assertIn('"/gp/cart/add.html"', behavior)
        self.assertIn("dataset.dynamicProductOption", behavior)
        self.assertIn("transactionSelectionKey", behavior)
        self.assertNotIn("dataset.optionPrice", behavior)
        self.assertIn("^\\/gp\\/aw\\/d\\/", behavior)
        self.assertNotIn('window.matchMedia("(max-width: 767px)")', behavior)
        self.assertNotIn("document.querySelectorAll('a[href=\"#\"]')", behavior)

    def test_every_home_rail_asin_has_a_bare_reachable_pdp_without_invented_ssd_copy(self) -> None:
        catalog = load_home_product_catalog(ROOT / "fixtures")
        self.assertEqual(len(catalog), 157)
        self.assertEqual(
            sum(
                product["placements"][0]["title"].rstrip().endswith(("...", "…"))
                for product in catalog.values()
            ),
            102,
        )
        self.assertEqual(
            sum(product["evidence_tier"] == "pdp-direct" for product in catalog.values()),
            11,
        )

        existing_ssd_asins = {"B0874XN4D8", "B08HN37XC1", "B0C5JQ68FY", "B0CHFSWM2P"}
        direct_search_asins = set(SEARCH_COMMERCE_PRODUCT_CATALOG)
        session_cookie = ""
        for asin, product in catalog.items():
            request_headers = {"Cookie": session_cookie} if session_cookie else None
            status, response_headers, body = self.request(
                "GET",
                f"/dp/{asin}",
                headers=request_headers,
            )
            if not session_cookie:
                session_cookie = response_headers["set-cookie"][0].split(";", 1)[0]
            with self.subTest(asin=asin):
                self.assertEqual(status, 200)
                self.assertIn(f'data-asin="{asin}"'.encode(), body)
                self.assertIn(b'id="productTitle"', body)
                self.assertNotIn(b"https://m.media-amazon.com", body)
                if asin not in existing_ssd_asins:
                    self.assertNotIn(b'<a href="#">External Solid State Drives</a>', body)
                if asin in direct_search_asins:
                    self.assertIn(b'data-pdp-variant="direct-search-card"', body)
                elif product["evidence_tier"] == "home-card-only" and asin not in existing_ssd_asins:
                    self.assertIn(b'data-pdp-variant="home-card-evidence"', body)
                    self.assertNotIn(b"Digital Storage Capacity", body)

        status, _, _ = self.request("GET", "/dp/B000000000", headers={"Cookie": session_cookie})
        self.assertEqual(status, 404)

    def test_new_cross_category_pdps_render_source_options_and_native_size_selector(self) -> None:
        cases = {
            "B0BJPXXM7D": (b"Ailun Screen Protector", b"iPad Pro 12.9 2022/2021/2020/2018"),
            "B071V91LGC": (b"Vault X 9 Pocket Zip", b"Only 12 left in stock"),
            "B0BQR2BQYZ": (b"upsimples 11x14 Picture Frame", b'data-product-option-select'),
            "B00FLYWNYQ": (b"Instant Pot Duo 7-in-1", b"3 Quarts"),
            "B07K74LDCH": (b"JanSport Laptop Backpack", b"Surreal Spots"),
            "B088BZTYFP": (b"Amazon Basics 16x20x1 Air Filter", b"Merv 11"),
        }
        for asin, expected in cases.items():
            with self.subTest(asin=asin):
                status, _, body = self.request("GET", f"/dp/{asin}")
                self.assertEqual(status, 200)
                self.assertIn(expected[0], body)
                self.assertIn(expected[1], body)
                self.assertIn(b'data-product-quote-matrix', body)
                self.assertIn(b'href="/product-reviews/', body)

        _, _, frame_body = self.request("GET", "/dp/B0BQR2BQYZ")
        self.assertIn(b'<select class="pdp-choice-select"', frame_body)
        self.assertIn(b'<option value="11x14" selected>11x14</option>', frame_body)
        self.assertIn(b'<option value="16x20">16x20</option>', frame_body)

    def test_latest_rich_pdps_render_only_captured_identity_offer_and_specs(self) -> None:
        cases = {
            "B00FLYWNYQ": (
                "Instant Pot Duo 7-in-1 Electric Pressure Cooker",
                (
                    "173,211",
                    "No Import Charges &amp; $35.82 Shipping to Singapore",
                    "$35.82 delivery Monday, July 27",
                    "Visit the Instant Pot Store",
                    "5.68 liters",
                    "13 one-touch Smart Programs.",
                ),
            ),
            "B07K74LDCH": (
                "JanSport Laptop Backpack",
                (
                    "19,909",
                    "$15.70 Shipping to Singapore",
                    "Fastest delivery Tuesday, July 28",
                    "Visit the JanSport Store",
                    "Only 4 left in stock - order soon.",
                    "Lifetime warranty with a repair-or-replace claim.",
                ),
            ),
            "B088BZTYFP": (
                "Amazon Basics 16x20x1 Air Filter 6-Pack",
                (
                    "17,115",
                    "#1 Best Seller",
                    "($4.64/count)",
                    "$30.50 Shipping to Singapore",
                    "19.75&quot;L x 15.75&quot;W x 0.75&quot;Th",
                    "Electrostatically charged synthetic material captures particles down to 3 microns.",
                ),
            ),
        }
        for asin, (title, expected_fragments) in cases.items():
            status, _, body = self.request("GET", f"/dp/{asin}")
            html = body.decode("utf-8")
            with self.subTest(asin=asin):
                self.assertEqual(status, 200)
                self.assertIn('data-pdp-variant="verified-detail"', html)
                self.assertIn(title, html)
                for fragment in expected_fragments:
                    self.assertIn(fragment, html)
                self.assertNotIn("https://m.media-amazon.com", html)

        _, _, filter_body = self.request("GET", "/dp/B088BZTYFP")
        filter_html = filter_body.decode("utf-8")
        self.assertNotIn("Visit the Amazon Basics Store", filter_html)
        self.assertIn('<option value="12x12x1">12x12x1</option>', filter_html)
        self.assertIn('<option value="16x20x1" selected>16x20x1</option>', filter_html)
        self.assertIn('data-option-value="Merv 11"', filter_html)

    def test_first_captured_home_pdp_uses_current_direct_sheet_set_evidence(self) -> None:
        for path in (
            "/dp/B01M16WBW1",
            "/Queen-Size-Piece-Sheet-Set/dp/B01M16WBW1",
        ):
            status, _, body = self.request("GET", path)
            html = body.decode("utf-8")
            with self.subTest(path=path):
                self.assertEqual(status, 200)
                self.assertIn('data-pdp-variant="verified-detail"', html)
                self.assertIn('data-page-category="Home &amp; Kitchen"', html)
                self.assertIn("CGK Unlimited", html)
                self.assertIn("447,592", html)
                self.assertIn("$12.74 delivery Monday, July 27", html)
                self.assertIn("Limited time deal", html)
                self.assertIn("Ships from", html)
                self.assertIn("Sold by", html)
                self.assertIn("30-day refund / replacement", html)
                self.assertIn("Available at checkout", html)
                self.assertIn("Sheet &amp; Pillowcase Sets", html)
                self.assertEqual(html.count("data-gallery-src="), 6)
                self.assertIn("gallery-08.jpg", html)
                self.assertIn("3+</span>", html)
                self.assertIn("7 VIDEOS", html)
                self.assertNotIn("External Solid State Drives", html)
                self.assertNotIn("Digital Storage Capacity", html)
                self.assertNotIn("portable storage", html)
                self.assertNotIn("https://m.media-amazon.com", html)

    def test_second_captured_home_pdp_uses_current_direct_okapi_evidence(self) -> None:
        for path in (
            "/dp/B0BG6B2D4D",
            "/Safari-Ltd-Okapi/dp/B0BG6B2D4D",
        ):
            status, _, body = self.request("GET", path)
            html = body.decode("utf-8")
            with self.subTest(path=path):
                self.assertEqual(status, 200)
                self.assertIn('data-pdp-variant="verified-detail"', html)
                self.assertIn('data-page-category="Toys &amp; Games"', html)
                self.assertIn("Safari Ltd. Okapi Figure", html)
                self.assertIn("Visit the Safari Ltd. Store", html)
                self.assertIn("(33)", html)
                self.assertIn("$7.08 delivery Wednesday, July 29", html)
                self.assertIn("Or fastest delivery Monday, July 27", html)
                self.assertIn("Toy Figures &amp; Playsets", html)
                self.assertIn("International Kindle Paperwhite", html)
                self.assertEqual(html.count("data-gallery-src="), 6)
                self.assertIn("gallery-06.jpg", html)
                self.assertNotIn("VIDEOS", html)
                self.assertIn("DISCOVER THE MYSTERY OF NATURE", html)
                self.assertIn("CHOKING HAZARD", html)
                self.assertIn("Ships from", html)
                self.assertIn("Safari Ltd.", html)
                self.assertNotIn("External Solid State Drives", html)
                self.assertNotIn("Digital Storage Capacity", html)
                self.assertNotIn("https://m.media-amazon.com", html)

    def test_third_captured_home_pdp_overrides_the_sparse_task_catalog_pdp(self) -> None:
        for path in (
            "/dp/B08HN37XC1",
            "/SanDisk-2TB-Extreme-Portable-SDSSDE61-2T00-G25/dp/B08HN37XC1",
        ):
            status, _, body = self.request("GET", path)
            html = body.decode("utf-8")
            with self.subTest(path=path):
                self.assertEqual(status, 200)
                self.assertIn('data-pdp-variant="verified-detail"', html)
                self.assertIn('data-page-category="Electronics"', html)
                self.assertIn("SANDISK 2TB Extreme Portable SSD (Old Model)", html)
                self.assertIn("Visit the Sandisk Store", html)
                self.assertIn("91,231", html)
                self.assertIn("#1 Best Seller", html)
                self.assertIn("5K+ bought in past month", html)
                self.assertIn("$41.38 Shipping &amp; Import Charges to Singapore", html)
                self.assertIn("$12.23 delivery Monday, July 27", html)
                self.assertIn("Sales For You", html)
                self.assertIn("Product support included", html)
                self.assertIn("Minority-Owned Business", html)
                self.assertIn("New (11) from $306.99 + $6.86 shipping", html)
                self.assertIn('Style: <strong data-selected-option-label="Style">Old Model</strong>', html)
                self.assertIn('Capacity: <strong data-selected-option-label="Capacity">2TB</strong>', html)
                self.assertIn('Color: <strong data-selected-option-label="Color">Black</strong>', html)
                self.assertIn('name="option.Style" value="Old Model"', html)
                self.assertIn('name="option.Capacity" value="2TB"', html)
                self.assertIn('name="option.Color" value="Black"', html)
                self.assertIn('data-option-value="New Model"', html)
                self.assertIn('data-option-value="8TB"', html)
                self.assertIn('data-option-value="Sky Blue"', html)
                self.assertIn("Sky Blue", html)
                self.assertIn("Quantity: 30", html)
                self.assertEqual(html.count("data-gallery-src="), 6)
                self.assertIn("gallery-06.jpg", html)
                self.assertIn("8 VIDEOS", html)
                self.assertIn("Digital Storage Capacity", html)
                self.assertIn("Get NVMe solid state performance", html)
                self.assertIn("brand-logo.jpg", html)
                self.assertNotIn("https://m.media-amazon.com", html)

    def test_transaction_quote_matrix_and_update_hooks_are_embedded_on_option_pdps(self) -> None:
        def attribute_json(html: str, name: str) -> object:
            match = re.search(rf'\b{name}="([^"]*)"', html)
            self.assertIsNotNone(match, name)
            assert match is not None
            return json.loads(unescape(match.group(1)))

        expected = {
            "B0874XN4D8": {
                "path": "/dp/B0874XN4D8",
                "quote_count": 2,
                "prices": {21_999, 26_789},
                "defaults": {"Color": "Titan Gray", "Memory Storage Capacity": "1 TB"},
            },
            "B0CHFSWM2P": {
                "path": "/dp/B0CHFSWM2P",
                "quote_count": 1,
                "prices": {23_999},
                "defaults": {"Color": "Black", "Digital Storage Capacity": "1 TB"},
            },
            "B08HN37XC1": {
                "path": "/dp/B08HN37XC1",
                "quote_count": 3,
                "prices": {31_699, 32_999},
                "defaults": {"Style": "Old Model", "Capacity": "2TB", "Color": "Black"},
            },
            "B00FLYWNYQ": {
                "path": "/dp/B00FLYWNYQ",
                "quote_count": 2,
                "prices": {8_999, 10_396},
                "defaults": {"Size": "6 Quarts"},
            },
            "B07K74LDCH": {
                "path": "/dp/B07K74LDCH",
                "quote_count": 13,
                "prices": {4_023, 4_999, 5_024, 5_499, 5_763, 6_030, 6_199, 6_200},
                "defaults": {"Color": "Black", "Size": "One Size"},
            },
            "B088BZTYFP": {
                "path": "/dp/B088BZTYFP",
                "quote_count": 2,
                "prices": {2_785},
                "defaults": {"Pattern Name": "16x20x1", "Style": "Merv 8"},
            },
        }
        for asin, contract in expected.items():
            status, _, body = self.request("GET", str(contract["path"]))
            html = body.decode("utf-8")
            with self.subTest(asin=asin):
                self.assertEqual(status, 200)
                self.assertIn("&quot;selected_options&quot;", html)
                quotes = attribute_json(html, "data-product-quote-matrix")
                defaults = attribute_json(html, "data-default-selected-options")
                self.assertIsInstance(quotes, list)
                assert isinstance(quotes, list)
                self.assertEqual(len(quotes), contract["quote_count"])
                self.assertEqual({quote["price_minor"] for quote in quotes}, contract["prices"])
                self.assertEqual(defaults, contract["defaults"])
                self.assertIn(
                    'data-option-unavailable-copy="No verified offer for this selection"',
                    html,
                )
                self.assertGreaterEqual(html.count("data-product-price"), 2)
                self.assertIn("data-product-add-to-cart", html)
                self.assertIn("data-product-buy-now", html)
                self.assertIn("data-product-availability", html)
                self.assertIn("data-product-quote-status", html)

    def test_home_quad_geometry_uses_natural_titles_and_source_image_height(self) -> None:
        parser, _ = self.document()
        rules = [rule for _, css in self.styles(parser) for rule in css_rules(css)]
        desktop_title_rules = [
            rule
            for rule in rules
            if ".home-card-quad h2" in rule.selectors and media_applies(rule.media, 1365)
        ]
        for rule in desktop_title_rules:
            self.assertIsNone(
                re.search(r"min-height\s*:", rule.declarations, re.IGNORECASE),
                "quad headings must keep the source page's natural one- or two-line height",
            )

        desktop_image_rules = [
            rule
            for rule in rules
            if ".home-tile img" in rule.selectors and media_applies(rule.media, 1365)
        ]
        self.assertTrue(
            any(re.search(r"height\s*:\s*116px", rule.declarations, re.IGNORECASE) for rule in desktop_image_rules),
            "desktop quad artwork should retain the frozen source height",
        )

        narrow_canvas_rail_rules = [
            rule
            for rule in rules
            if ".home-rail" in rule.selectors and media_applies(rule.media, 390)
        ]
        self.assertTrue(
            any(
                re.search(r"height\s*:\s*281\.5px", rule.declarations, re.IGNORECASE)
                for rule in narrow_canvas_rail_rules
            ),
            "a narrow browser must retain the source desktop rail height inside the fixed canvas",
        )
        self.assertFalse(
            any(
                re.search(r"height\s*:\s*auto", rule.declarations, re.IGNORECASE)
                for rule in narrow_canvas_rail_rules
            ),
            "viewport resizing must not switch the fixed Amazon canvas to the compact rail layout",
        )

        desktop_grid_rules = [
            rule
            for rule in rules
            if ".home-grid" in rule.selectors and media_applies(rule.media, 1280)
        ]
        self.assertTrue(
            any(
                re.search(r"margin\s*:\s*-295px\s+auto\s+0", rule.declarations, re.IGNORECASE)
                for rule in desktop_grid_rules
            ),
            "desktop home grid must start at the frozen source offset",
        )
        desktop_card_rules = [
            rule
            for rule in rules
            if ".home-card" in rule.selectors and media_applies(rule.media, 1280)
        ]
        self.assertTrue(
            any(re.search(r"height\s*:\s*420px", rule.declarations, re.IGNORECASE) for rule in desktop_card_rules),
            "desktop home cards must retain the source 420px height",
        )
        rail_heading_rules = [
            rule
            for rule in rules
            if ".home-rail-heading" in rule.selectors and media_applies(rule.media, 1280)
        ]
        self.assertTrue(
            any(
                re.search(r"min-height\s*:\s*32px", rule.declarations, re.IGNORECASE)
                for rule in rail_heading_rules
            ),
            "desktop rail headings must reserve the source 32px row",
        )
        standard_rail_rules = [
            rule
            for rule in rules
            if ".home-rail" in rule.selectors and media_applies(rule.media, 1280)
        ]
        related_rail_rules = [
            rule
            for rule in rules
            if ".home-related-rail" in rule.selectors and media_applies(rule.media, 1280)
        ]
        self.assertTrue(
            any(re.search(r"height\s*:\s*281\.5px", rule.declarations, re.IGNORECASE) for rule in standard_rail_rules),
            "standard source rails must use their captured 281.5px height",
        )
        self.assertTrue(
            any(re.search(r"height\s*:\s*285px", rule.declarations, re.IGNORECASE) for rule in related_rail_rules),
            "the personalized source rail must retain its captured 285px height",
        )

    def test_footer_contains_the_evidence_backed_desktop_and_mobile_directories(self) -> None:
        _, html = self.document()
        desktop_main_links = (
            "Investor Relations",
            "Amazon Devices",
            "Sell on Amazon Business",
            "Amazon Currency Converter",
            "Manage Your Content and Devices",
        )
        service_directory = (
            "Amazon Music",
            "Amazon Ads",
            "6pm",
            "AbeBooks",
            "Amazon Web Services",
            "Goodreads",
            "Kindle Direct Publishing",
            "Prime Video Direct",
            "eero WiFi",
            "PillPack",
        )
        mobile_links = (
            "Amazon Live",
            "Registry &amp; Gift List",
            "Recalls and Product Safety Alerts",
            "Browsing History",
            "Already a customer? Sign in",
        )
        for label in (*desktop_main_links, *service_directory, *mobile_links):
            with self.subTest(label=label):
                self.assertIn(label, html)

    def test_home_css_uses_native_scroll_for_a_fixed_desktop_canvas(self) -> None:
        parser, _ = self.document()
        rules = [rule for _, css in self.styles(parser) for rule in css_rules(css)]

        def applicable_min_widths(selector: str, width: int) -> list[str]:
            return [
                match.group(1).strip().lower()
                for rule in rules
                if selector in rule.selectors and media_applies(rule.media, width)
                for match in MIN_WIDTH_RE.finditer(rule.declarations)
            ]

        for width in (390, 900, 1440):
            with self.subTest(width=width):
                self.assertEqual(applicable_min_widths("body", width)[-1], "1000px")
                self.assertEqual(applicable_min_widths(".desktop-shell", width)[-1], "1000px")

        self.assertGreater(1000, 390, "390px must expose native horizontal page scrolling")
        self.assertLessEqual(1000, 1440, "the fixed canvas must fit a normal 1440px desktop viewport")

        page_root_rules = [
            rule
            for rule in rules
            if media_applies(rule.media, 390)
            and any(selector in {"html", "body"} for selector in rule.selectors)
        ]
        self.assertFalse(
            any(
                re.search(r"overflow(?:-x)?\s*:\s*(?:hidden|clip)", rule.declarations, re.IGNORECASE)
                for rule in page_root_rules
            ),
            "html/body must leave native horizontal and vertical browser scrollbars available",
        )

        rail_rules = [
            rule
            for rule in rules
            if ".home-rail-track" in rule.selectors and media_applies(rule.media, 390)
        ]
        self.assertTrue(
            any(re.search(r"overflow-x\s*:\s*auto", rule.declarations, re.IGNORECASE) for rule in rail_rules),
            "product rails must keep their independent horizontal scroller inside the page canvas",
        )

    def test_quad_captions_do_not_reuse_the_legacy_118px_background_tiles(self) -> None:
        parser, _ = self.document()
        rules = [rule for _, css in self.styles(parser) for rule in css_rules(css)]
        caption_rules = [
            rule
            for rule in rules
            if any(
                selector.strip() in {".quad-grid span", ".home-tile span", ".home-tile-caption"}
                for selector in rule.selectors
            )
        ]
        for rule in caption_rules:
            declarations = rule.declarations.lower()
            with self.subTest(selectors=rule.selectors):
                self.assertIsNone(
                    re.search(r"min-height\s*:\s*118px", declarations),
                    "quad captions must size as text instead of 118px artwork placeholders",
                )
                if any(selector.strip() == ".quad-grid span" for selector in rule.selectors):
                    self.assertIsNone(
                        re.search(r"(?:^|;)\s*background(?:-image)?\s*:", declarations),
                        "quad-grid captions must render beside <img> content, not as legacy backgrounds",
                    )


if __name__ == "__main__":
    unittest.main()
