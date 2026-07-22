from __future__ import annotations

import http.client
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer  # noqa: E402
from store import BEST_SELLERS_PATH, PDP_PATH, Store, TARGET_ASIN  # noqa: E402


FORBIDDEN_POPUP_MARKERS = (
    "delivery_overlay",
    "delivery-overlay",
    "notice-dismiss",
    "notice-change",
    "international shopping transition alert",
    "ship to a different country",
)


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class CountryPopupRemovalTests(unittest.TestCase):
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
        QuietPublicHandler.smtp_config = None
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

    def request_html(self, path: str) -> str:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        self.assertEqual(response.status, 200, path)
        return payload.decode("utf-8")

    def assert_popup_absent(self, content: str, source: str) -> None:
        lowered = content.lower()
        for marker in FORBIDDEN_POPUP_MARKERS:
            with self.subTest(source=source, marker=marker):
                self.assertNotIn(marker, lowered)

    def test_popup_dom_is_absent_across_every_former_surface_family(self) -> None:
        routes = (
            "/",
            BEST_SELLERS_PATH,
            "/s?k=books&i=books",
            "/gp/site-directory",
            "/gp/goldbox/",
            PDP_PATH,
            f"/product-reviews/{TARGET_ASIN}",
            "/gp/cart/view.html",
        )
        for path in routes:
            with self.subTest(path=path):
                html = self.request_html(path)
                self.assert_popup_absent(html, path)

        # Removing the transition popup must not remove the useful header
        # delivery control that leads to the local delivery-preference page.
        home = self.request_html("/")
        self.assertIn('class="nav-location desktop-only"', home)
        self.assertIn('href="/gp/delivery/ajax/address-change.html"', home)

    def test_popup_has_no_unreachable_renderer_css_or_javascript_residue(self) -> None:
        candidate_sources = (
            ROOT / "render.py",
            ROOT / "static" / "styles.css",
            ROOT / "static" / "app.js",
        )
        for path in candidate_sources:
            with self.subTest(path=path.name):
                self.assert_popup_absent(path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    unittest.main()
