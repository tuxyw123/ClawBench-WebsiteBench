from __future__ import annotations

import http.client
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from render import (  # noqa: E402
    CUSTOMER_SERVICE_HREF,
    DELIVERY_PREFERENCE_HREF,
    RETURNS_REPLACEMENTS_HREF,
    SHIPPING_POLICIES_HREF,
)
from server import PublicHandler, ReusableThreadingHTTPServer  # noqa: E402
from store import Store  # noqa: E402


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class ShoppingEntrypointTests(unittest.TestCase):
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

    def get(self, path: str) -> tuple[int, str]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=8)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, body

    @staticmethod
    def main_markup(html: str) -> str:
        match = re.search(r'<main\b.*?</main>', html, flags=re.DOTALL)
        if match is None:
            raise AssertionError("page did not render a main landmark")
        return match.group(0)

    def test_pdp_purchase_help_and_browse_entrypoints_are_local_and_live(self) -> None:
        for asin in ("B0874XN4D8", "B0CHFSWM2P", "B07CRG94G3"):
            with self.subTest(asin=asin):
                status, html = self.get(f"/dp/{asin}")
                self.assertEqual(status, 200)
                main = self.main_markup(html)
                self.assertNotIn('href="#"', main)
                self.assertIn(f'href="{DELIVERY_PREFERENCE_HREF}"', main)
                self.assertIn('data-pdp-full-view-open', main)
                self.assertIn('id="pdp-full-view-dialog"', main)
                self.assertIn('aria-controls="pdp-full-view-dialog"', main)

        status, t7 = self.get("/dp/B0874XN4D8")
        self.assertEqual(status, 200)
        main = self.main_markup(t7)
        self.assertIn(f'href="{RETURNS_REPLACEMENTS_HREF}"', main)
        self.assertIn(f'href="{SHIPPING_POLICIES_HREF}"', main)
        self.assertIn('data-pdp-info-open', main)
        self.assertIn('id="pdp-secure-transaction-dialog"', main)
        self.assertIn("Other sellers on Amazon", main)
        self.assertIn(
            "Individual seller offers were not retained in this local snapshot",
            main,
        )
        self.assertNotIn("Available at a lower price from other sellers", main)
        self.assertNotIn("New (5) from", main)
        self.assertNotIn("$219.79", main)
        self.assertNotRegex(
            main,
            r'<section class="other-sellers">.*?<a\b',
            "the neutral seller-evidence boundary must not invent a clickable offer",
        )

        for path in (
            DELIVERY_PREFERENCE_HREF,
            RETURNS_REPLACEMENTS_HREF,
            SHIPPING_POLICIES_HREF,
            CUSTOMER_SERVICE_HREF,
        ):
            with self.subTest(destination=path):
                self.assertEqual(self.get(path)[0], 200)

        browse_hrefs = re.findall(r'<nav class="breadcrumb">.*?href="([^"]+)"', main)
        self.assertTrue(browse_hrefs)
        self.assertTrue(all(href.startswith("/s?") for href in browse_hrefs))
        self.assertEqual(self.get(browse_hrefs[0].replace("&amp;", "&"))[0], 200)

    def test_pdp_dialog_controls_have_named_local_targets_and_focus_code(self) -> None:
        status, html = self.get("/dp/B0874XN4D8")
        self.assertEqual(status, 200)
        main = self.main_markup(html)
        ids = set(re.findall(r'\bid="([^"]+)"', main))
        controlled = re.findall(
            r'<button[^>]+aria-controls="(pdp-[^"]+-dialog)"[^>]*>', main
        )
        self.assertTrue(
            {"pdp-full-view-dialog", "pdp-secure-transaction-dialog"}.issubset(
                controlled
            )
        )
        self.assertTrue(set(controlled).issubset(ids))
        self.assertIn('aria-labelledby="pdp-full-view-title"', main)
        self.assertIn('aria-labelledby="pdp-secure-transaction-title"', main)

        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("fullViewDialog.showModal()", app)
        self.assertIn("infoDialog.showModal()", app)
        self.assertIn("lastFullViewTrigger.focus", app)
        self.assertIn("lastInfoTrigger.focus", app)

    def test_cart_recommendations_link_to_known_products_and_scroll_by_keyboard(self) -> None:
        status, html = self.get("/gp/cart/view.html")
        self.assertEqual(status, 200)
        main = self.main_markup(html)
        self.assertNotIn('href="#"', main)
        self.assertIn('data-cart-recommendations-viewport', main)
        self.assertIn('tabindex="0"', main)
        self.assertIn('aria-controls="cart-recommendation-viewport"', main)

        asins = re.findall(
            r'<article class="cart-recommendation-card" data-asin="([A-Z0-9]{10})">',
            main,
        )
        self.assertEqual(len(asins), 6)
        self.assertEqual(len(set(asins)), 6)
        for asin in asins:
            with self.subTest(asin=asin):
                self.assertIn(f'href="/dp/{asin}"', main)
                self.assertIn(f'href="/product-reviews/{asin}"', main)
                self.assertEqual(self.get(f"/dp/{asin}")[0], 200)
                self.assertEqual(self.get(f"/product-reviews/{asin}")[0], 200)

        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('viewport.addEventListener("keydown"', app)
        self.assertIn('event.key === "ArrowLeft"', app)
        self.assertIn('event.key === "ArrowRight"', app)
        self.assertIn('viewport.scrollBy({ left:', app)


if __name__ == "__main__":
    unittest.main()
