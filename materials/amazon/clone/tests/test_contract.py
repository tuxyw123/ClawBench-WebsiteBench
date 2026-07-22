from __future__ import annotations

import hashlib
import http.client
import sys
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer, product_for_pdp  # noqa: E402
from store import (  # noqa: E402
    BEST_SELLERS_PATH,
    DESKTOP_TERMINAL_PATH,
    MOBILE_TERMINAL_PATH,
    PDP_PATH,
    Store,
    TARGET_ASIN,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SEARCH_FEATURED_ASINS = (
    "B08HN37XC1",
    "B08GTYFC37",
    "B0F6NKYDTY",
    "B0BGKXX9TK",
    TARGET_ASIN,
    "B0C5JQ68FY",
)
T9_ASIN = "B0CHFSWM2P"


def normalized_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class ResponseDisconnectTests(unittest.TestCase):
    def test_client_disconnect_during_body_write_is_normalized(self) -> None:
        class DisconnectingWriter:
            def write(self, _body: bytes) -> None:
                raise ConnectionAbortedError("client closed the connection")

        handler = object.__new__(PublicHandler)
        handler.command = "GET"
        handler.close_connection = False
        handler.wfile = DisconnectingWriter()
        handler.send_response = lambda _status: None
        handler.send_header = lambda _key, _value: None
        handler.end_headers = lambda: None

        handler._send(200, b"response body")

        self.assertTrue(handler.close_connection)

    def test_client_disconnect_during_request_read_is_not_logged(self) -> None:
        server = object.__new__(ReusableThreadingHTTPServer)
        try:
            raise ConnectionResetError("client reset keep-alive connection")
        except ConnectionResetError:
            server.handle_error(None, ("127.0.0.1", 0))


class SearchResultParser(HTMLParser):
    """Extract the public identity fields from semantic search result cards."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, object]] = []
        self.current: dict[str, object] | None = None
        self.in_title = False
        self.in_price = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "search-result" in classes:
            self.current = {
                "asin": attributes.get("data-asin"),
                "title_parts": [],
                "price_parts": [],
                "product_hrefs": [],
            }
            return
        if self.current is None:
            return
        if tag == "h2":
            self.in_title = True
        if tag == "a":
            href = attributes.get("href") or ""
            if "/dp/" in href:
                product_hrefs = self.current["product_hrefs"]
                assert isinstance(product_hrefs, list)
                product_hrefs.append(href)
            if "result-price" in classes:
                self.in_price = True

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "h2":
            self.in_title = False
        elif tag == "a" and self.in_price:
            self.in_price = False
        elif tag == "article":
            title_parts = self.current.pop("title_parts")
            price_parts = self.current.pop("price_parts")
            assert isinstance(title_parts, list)
            assert isinstance(price_parts, list)
            self.current["title"] = normalized_text(title_parts)
            self.current["price"] = normalized_text(price_parts)
            self.cards.append(self.current)
            self.current = None
            self.in_title = False
            self.in_price = False

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self.in_title:
            title_parts = self.current["title_parts"]
            assert isinstance(title_parts, list)
            title_parts.append(data)
        if self.in_price:
            price_parts = self.current["price_parts"]
            assert isinstance(price_parts, list)
            price_parts.append(data)


class ProductIdentityParser(HTMLParser):
    """Extract PDP identity and dangerous form targets without CSS assumptions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.main_asins: list[str | None] = []
        self.pdp_variants: list[str | None] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.class_tokens: set[str] = set()
        self.form_targets: set[str] = set()
        self.image_sources: list[str] = []

    @property
    def title(self) -> str:
        return normalized_text(self.title_parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.class_tokens.update((attributes.get("class") or "").split())
        if tag == "main" and attributes.get("id") == "main":
            self.main_asins.append(attributes.get("data-asin"))
            self.pdp_variants.append(attributes.get("data-pdp-variant"))
        if tag == "h1" and attributes.get("id") == "productTitle":
            self.in_title = True
        if tag == "form":
            for name in ("action", "data-desktop-action", "data-mobile-action"):
                value = attributes.get(name)
                if value:
                    self.form_targets.add(value)
        if tag == "img" and attributes.get("src"):
            self.image_sources.append(attributes["src"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self.in_title:
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


class StoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_rejected_post_journal_never_retains_or_hashes_secret_body(self) -> None:
        session = sha("rejected-secret-session")
        raw_body = b"password=audit-secret&code=654321"
        self.store.record_rejected_post(
            session,
            "/unknown-sensitive-target",
            "application/x-www-form-urlencoded",
            raw_body,
        )

        journal = self.store.journal()
        self.assertEqual(len(journal), 1)
        entry = journal[0]
        self.assertEqual(entry["canonical_form"], "<redacted rejected POST body>")
        self.assertEqual(
            entry["raw_body_sha256"],
            hashlib.sha256(b"<redacted rejected POST body>").hexdigest(),
        )
        self.assertNotEqual(
            entry["raw_body_sha256"], hashlib.sha256(raw_body).hexdigest()
        )
        serialized = repr(journal).casefold()
        self.assertNotIn("audit-secret", serialized)
        self.assertNotIn("654321", serialized)

    def test_exact_journey_creates_one_quantity_two_line(self) -> None:
        session = sha("session-one")
        capability = sha("flow-one")
        self.store.record_best_sellers(session, BEST_SELLERS_PATH, "http://localhost/")
        eligible = self.store.record_pdp(
            session,
            PDP_PATH,
            f"http://localhost:8153{BEST_SELLERS_PATH}",
            capability,
        )
        self.assertTrue(eligible)

        raw = f"ASIN={TARGET_ASIN}&quantity=2".encode()
        status, outcome = self.store.terminal_request(
            session,
            DESKTOP_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            raw,
            [("ASIN", TARGET_ASIN), ("quantity", "2")],
            capability,
        )
        self.assertEqual((status, outcome), (303, "accepted"))
        self.assertEqual(self.store.cart(session)[0]["quantity"], 2)
        self.assertEqual(
            self.store.cart(session)[0]["selected_options"],
            {"Color": "Titan Gray", "Memory Storage Capacity": "1 TB"},
        )
        self.assertEqual(self.store.normalized_state()["task_progress"][0]["stage"], "COMPLETE")
        self.assertEqual(len(self.store.normalized_state()["task_completions"]), 1)

        duplicate_status, duplicate_outcome = self.store.terminal_request(
            session,
            DESKTOP_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            raw,
            [("ASIN", TARGET_ASIN), ("quantity", "2")],
            capability,
        )
        self.assertEqual(duplicate_status, 409)
        self.assertIn(duplicate_outcome, {"wrong-navigation-stage", "capability-already-consumed"})
        self.assertEqual(self.store.cart(session)[0]["quantity"], 2)
        self.assertEqual(len(self.store.normalized_state()["task_completions"]), 1)

    def test_terminal_request_without_ordered_gets_is_rejected(self) -> None:
        session = sha("session-two")
        raw = f"ASIN={TARGET_ASIN}&quantity=2".encode()
        status, outcome = self.store.terminal_request(
            session,
            DESKTOP_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            raw,
            [("ASIN", TARGET_ASIN), ("quantity", "2")],
            sha("missing"),
        )
        self.assertEqual((status, outcome), (409, "missing-navigation-sequence"))
        self.assertEqual(self.store.cart(session), [])

    def test_rank_two_is_frozen_to_target(self) -> None:
        ranking = self.store.ranking()
        target = next(item for item in ranking if item["rank"] == 2)
        self.assertEqual(target["asin"], TARGET_ASIN)

    def test_mobile_terminal_path_uses_the_same_transaction_contract(self) -> None:
        session = sha("mobile-session")
        capability = sha("mobile-flow")
        self.store.record_best_sellers(session, BEST_SELLERS_PATH, "http://localhost/")
        self.assertTrue(
            self.store.record_pdp(
                session,
                PDP_PATH,
                f"http://localhost:8153{BEST_SELLERS_PATH}",
                capability,
            )
        )
        raw = f"ASIN={TARGET_ASIN}&quantity=2".encode()
        status, outcome = self.store.terminal_request(
            session,
            MOBILE_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            raw,
            [("ASIN", TARGET_ASIN), ("quantity", "2")],
            capability,
        )
        self.assertEqual((status, outcome), (303, "accepted"))
        self.assertEqual(self.store.journal()[0]["path"], MOBILE_TERMINAL_PATH)
        self.assertEqual(self.store.cart(session)[0]["quantity"], 2)


class PublicJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        PublicHandler.store = self.store
        self.server = ReusableThreadingHTTPServer(("127.0.0.1", 0), PublicHandler)
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
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, list[str]], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response_headers: dict[str, list[str]] = {}
        for name, value in response.getheaders():
            response_headers.setdefault(name.lower(), []).append(value)
        payload = response.read()
        connection.close()
        return response.status, response_headers, payload

    @staticmethod
    def cookie_pair(set_cookie: str) -> str:
        return set_cookie.split(";", 1)[0]

    def search_cards(self) -> list[dict[str, object]]:
        status, _, body = self.request("GET", "/s?k=portable+ssd")
        self.assertEqual(status, 200)
        parser = SearchResultParser()
        parser.feed(body.decode("utf-8"))
        self.assertGreaterEqual(len(parser.cards), len(SEARCH_FEATURED_ASINS))
        return parser.cards

    def test_search_first_six_cards_are_catalog_consistent(self) -> None:
        cards = self.search_cards()[: len(SEARCH_FEATURED_ASINS)]
        self.assertEqual(tuple(card["asin"] for card in cards), SEARCH_FEATURED_ASINS)

        catalog = {product["asin"]: product for product in self.store.products()}
        for card, asin in zip(cards, SEARCH_FEATURED_ASINS, strict=True):
            with self.subTest(asin=asin):
                product = catalog[asin]
                expected_href = f"/{product['slug']}/dp/{asin}"
                expected_price = f"${product['price_minor'] / 100:,.2f}"
                self.assertEqual(card["title"], product["title"])
                self.assertEqual(card["price"], expected_price)
                self.assertEqual(set(card["product_hrefs"]), {expected_href})

    def test_search_result_hrefs_render_matching_product_identity(self) -> None:
        cards = self.search_cards()[: len(SEARCH_FEATURED_ASINS)]
        catalog = {product["asin"]: product for product in self.store.products()}
        for card in cards:
            asin = card["asin"]
            assert isinstance(asin, str)
            hrefs = set(card["product_hrefs"])
            self.assertEqual(len(hrefs), 1)
            href = next(iter(hrefs))
            assert isinstance(href, str)
            with self.subTest(asin=asin, href=href):
                status, _, body = self.request("GET", href)
                self.assertEqual(status, 200)
                parser = ProductIdentityParser()
                parser.feed(body.decode("utf-8"))
                self.assertEqual(parser.main_asins, [asin])
                expected_product = product_for_pdp(self.store, asin)
                self.assertIsNotNone(expected_product)
                assert expected_product is not None
                self.assertEqual(parser.title, expected_product["title"])

    def test_non_t7_pdps_do_not_expose_t7_only_content_or_terminal_forms(self) -> None:
        terminal_paths = {DESKTOP_TERMINAL_PATH, MOBILE_TERMINAL_PATH}
        for product in self.store.products():
            if product["asin"] == TARGET_ASIN:
                continue
            href = f"/{product['slug']}/dp/{product['asin']}"
            with self.subTest(asin=product["asin"]):
                status, _, body = self.request("GET", href)
                self.assertEqual(status, 200)
                parser = ProductIdentityParser()
                parser.feed(body.decode("utf-8"))
                expected_product = product_for_pdp(self.store, product["asin"])
                self.assertIsNotNone(expected_product)
                assert expected_product is not None
                self.assertEqual(parser.main_asins, [product["asin"]])
                self.assertEqual(parser.title, expected_product["title"])
                if expected_product.get("pdp"):
                    self.assertEqual(parser.pdp_variants, ["verified-detail"])
                    self.assertIn("pdp-gallery", parser.class_tokens)
                else:
                    self.assertEqual(parser.pdp_variants, ["catalog-evidence"])
                    self.assertNotIn("pdp-gallery", parser.class_tokens)
                    self.assertNotIn("samsung-brand", parser.class_tokens)
                    self.assertFalse(
                        any("samsung-logo" in source for source in parser.image_sources),
                        "generic PDP must not borrow the T7 Samsung logo asset",
                    )
                self.assertNotIn(b"Titan Gray", body)
                self.assertTrue(parser.form_targets.isdisjoint(terminal_paths))

    def test_t9_detailed_pdp_uses_persisted_source_evidence(self) -> None:
        product = self.store.product(T9_ASIN)
        self.assertIsNotNone(product)
        assert product is not None
        detail = product["pdp"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["schema"], "amazon-clone.pdp-evidence.v1")
        self.assertEqual(len(detail["gallery"]), 6)
        self.assertEqual(detail["video_count"], 10)
        self.assertEqual(detail["gallery_more_count"], 6)

        href = f"/{product['slug']}/dp/{T9_ASIN}"
        status, _, body = self.request("GET", href)
        self.assertEqual(status, 200)
        parser = ProductIdentityParser()
        parser.feed(body.decode("utf-8"))
        self.assertEqual(parser.main_asins, [T9_ASIN])
        self.assertEqual(parser.pdp_variants, ["verified-detail"])
        self.assertEqual(parser.title, product["title"])
        for path in [detail["main_image"], *detail["gallery"], detail["video_thumbnail"]]:
            self.assertIn(path, parser.image_sources)
        self.assertIn(b"10 VIDEOS", body)
        self.assertIn(b">6+<", body)
        self.assertIn(b"10K+ bought in past month", body)
        self.assertIn(b'name="option.Digital Storage Capacity" value="1 TB"', body)
        self.assertIn(b'name="option.Color" value="Black"', body)
        self.assertIn(b'data-option-value="4 TB"', body)
        self.assertIn(b'data-option-value="Gray"', body)
        self.assertNotIn(DESKTOP_TERMINAL_PATH.encode(), body)
        self.assertNotIn(MOBILE_TERMINAL_PATH.encode(), body)

    def test_browser_http_sequence_and_public_admin_isolation(self) -> None:
        status, headers, body = self.request("GET", BEST_SELLERS_PATH)
        self.assertEqual(status, 200)
        self.assertIn(b'data-rank="2"', body)
        self.assertIn(TARGET_ASIN.encode(), body)
        session_cookie = self.cookie_pair(headers["set-cookie"][0])

        status, headers, body = self.request(
            "GET",
            PDP_PATH,
            headers={
                "Cookie": session_cookie,
                "Referer": f"http://{self.host}:{self.port}{BEST_SELLERS_PATH}",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn(b'id="addToCart"', body)
        self.assertIn(b'data-generic-action="/gp/cart/add.html"', body)
        terminal_form = body.split(b'<form id="addToCart"', 1)[1].split(
            b"</form>", 1
        )[0]
        self.assertNotIn(b'name="option.', terminal_form)
        flow_cookie = next(
            self.cookie_pair(value)
            for value in headers["set-cookie"]
            if value.startswith("amazon_clone_flow=")
        )

        raw = f"ASIN={TARGET_ASIN}&quantity=2".encode()
        status, headers, _ = self.request(
            "POST",
            DESKTOP_TERMINAL_PATH,
            body=raw,
            headers={
                "Cookie": f"{session_cookie}; {flow_cookie}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(raw)),
                "Referer": f"http://{self.host}:{self.port}{PDP_PATH}",
            },
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], ["/gp/cart/view.html"])

        status, _, body = self.request("GET", "/gp/cart/view.html", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"Subtotal (2 items)", body)
        self.assertIn(b">Quantity: 2</option>", body)

        status, _, _ = self.request("GET", "/__bench/state")
        self.assertEqual(status, 404)

    def test_terminal_body_is_byte_exact_and_stale_navigation_is_rejected(self) -> None:
        session = sha("exact-body-session")
        capability = sha("exact-body-flow")
        self.store.record_best_sellers(session, BEST_SELLERS_PATH, "http://localhost/")
        self.assertTrue(
            self.store.record_pdp(
                session,
                PDP_PATH,
                f"http://localhost:8153{BEST_SELLERS_PATH}",
                capability,
            )
        )
        encoded = f"ASIN={TARGET_ASIN}&quantity=%32".encode()
        status, outcome = self.store.terminal_request(
            session,
            DESKTOP_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            encoded,
            [("ASIN", TARGET_ASIN), ("quantity", "2")],
            capability,
        )
        self.assertEqual((status, outcome), (409, "wrong-form-body"))
        first_journal_entry = self.store.journal()[0]
        self.assertEqual(
            first_journal_entry["canonical_form"],
            "<redacted rejected POST body>",
        )
        self.assertNotEqual(
            first_journal_entry["raw_body_sha256"], hashlib.sha256(encoded).hexdigest()
        )
        self.store.record_read_route(session, "HOME", "/", PDP_PATH)
        canonical = f"ASIN={TARGET_ASIN}&quantity=2".encode()
        status, outcome = self.store.terminal_request(
            session,
            DESKTOP_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            canonical,
            [("ASIN", TARGET_ASIN), ("quantity", "2")],
            capability,
        )
        self.assertEqual((status, outcome), (409, "stale-navigation-sequence"))
        self.assertEqual(self.store.cart(session), [])

    def test_terminal_rejection_does_not_journal_attacker_credentials(self) -> None:
        session = sha("terminal-secret-session")
        capability = sha("terminal-secret-flow")
        raw_body = b"password=terminal-secret&code=123456"
        status, outcome = self.store.terminal_request(
            session,
            DESKTOP_TERMINAL_PATH,
            "application/x-www-form-urlencoded",
            raw_body,
            [("password", "terminal-secret"), ("code", "123456")],
            capability,
        )
        self.assertEqual((status, outcome), (409, "wrong-form-body"))
        serialized = repr(self.store.journal()).casefold()
        self.assertNotIn("terminal-secret", serialized)
        self.assertNotIn("123456", serialized)
        self.assertIn("<redacted rejected post body>", serialized)

    def test_nonterminal_post_is_drained_and_journaled(self) -> None:
        raw = b"checkout=true"
        status, _, _ = self.request(
            "POST",
            "/gp/buy/spc/handlers/display.html",
            body=raw,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(raw)),
            },
        )
        self.assertEqual(status, 404)
        journal = self.store.journal()
        self.assertEqual(journal[0]["outcome"], "rejected-nonterminal")
        self.assertEqual(journal[0]["path"], "/gp/buy/spc/handlers/display.html")

    def test_false_search_prefix_and_forged_target_pdp_are_not_routes(self) -> None:
        status, _, _ = self.request("GET", "/seller/not-a-search-route")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", f"/forged/dp/{TARGET_ASIN}")
        self.assertEqual(status, 404)

    def test_sell_landing_is_live_and_does_not_fall_through_to_the_ssd_category(self) -> None:
        status, _, body = self.request(
            "GET",
            "/b/?_encoding=UTF8&ld=AZUSSOA-sell&node=12766669011",
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Sell on Amazon", body)
        self.assertIn(b"Create an account", body)
        self.assertNotIn(b"portable ssd", body.lower())

    def test_every_frozen_ranking_product_has_a_real_pdp_route(self) -> None:
        for product in self.store.ranking():
            status, _, body = self.request("GET", f"/{product['slug']}/dp/{product['asin']}")
            self.assertEqual(status, 200)
            self.assertIn(product["asin"].encode(), body)


if __name__ == "__main__":
    unittest.main()
