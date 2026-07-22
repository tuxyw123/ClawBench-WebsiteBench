from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_suggestions import (  # noqa: E402
    MAX_SUGGESTION_VALUE_LENGTH,
    SearchSuggestionRequest,
    SearchSuggestionValidationError,
    build_suggestion_corpus,
    parse_suggestion_request,
    suggest_search_terms,
)
from server import (  # noqa: E402
    HOME_PRODUCT_CATALOG,
    PublicHandler,
    ReusableThreadingHTTPServer,
)
from store import Store  # noqa: E402


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class SearchSuggestionDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = build_suggestion_corpus(HOME_PRODUCT_CATALOG)

    def test_corpus_is_local_catalog_derived_and_bounded(self) -> None:
        self.assertGreaterEqual(len(self.corpus), 170)
        self.assertEqual(len({item.value.casefold() for item in self.corpus}), len(self.corpus))
        self.assertTrue(all(1 <= len(item.value) <= MAX_SUGGESTION_VALUE_LENGTH + 1 for item in self.corpus))
        self.assertTrue(all(item.kind in {"query", "department", "product"} for item in self.corpus))

    def test_portable_query_prefers_stable_shopping_phrases(self) -> None:
        suggestions = suggest_search_terms(
            SearchSuggestionRequest("portable"), self.corpus
        )
        self.assertEqual(suggestions[0].value, "portable ssd")
        self.assertLessEqual(len(suggestions), 10)
        self.assertTrue(any(item.kind == "product" for item in suggestions))

    def test_department_scope_excludes_other_department_products(self) -> None:
        suggestions = suggest_search_terms(
            SearchSuggestionRequest("portable", department="books"), self.corpus
        )
        self.assertEqual(suggestions, ())
        book_suggestions = suggest_search_terms(
            SearchSuggestionRequest("thresh", department="books"), self.corpus
        )
        self.assertTrue(book_suggestions)
        self.assertTrue(all(item.department == "books" for item in book_suggestions))

    def test_short_query_does_not_expose_a_whole_catalog_dump(self) -> None:
        self.assertEqual(
            suggest_search_terms(SearchSuggestionRequest("s"), self.corpus), ()
        )

    def test_request_parser_is_strict(self) -> None:
        parsed = parse_suggestion_request("q=portable+ssd&i=computers")
        self.assertEqual(parsed.query, "portable ssd")
        self.assertEqual(parsed.department, "computers")
        for raw_query in (
            "",
            "q=one&q=two",
            "q=portable&i=books&i=computers",
            "q=portable&unknown=1",
            "q=portable&i=not-a-department",
            "q=bad%ZZ",
            "q=bad%00value",
        ):
            with self.subTest(raw_query=raw_query):
                with self.assertRaises(SearchSuggestionValidationError):
                    parse_suggestion_request(raw_query)


class SearchSuggestionHTTPTests(unittest.TestCase):
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

    def request(self, path: str) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, headers, payload

    def test_header_exposes_accessible_combobox_without_static_options(self) -> None:
        status, _, payload = self.request("/")
        self.assertEqual(status, 200)
        html = payload.decode("utf-8")
        self.assertIn('data-search-suggestions-endpoint="/search/suggestions"', html)
        self.assertIn('role="combobox"', html)
        self.assertIn('aria-autocomplete="list"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('aria-controls="nav-search-suggestions"', html)
        self.assertIn('id="nav-search-suggestions"', html)
        self.assertIn('role="listbox"', html)
        self.assertIn('data-search-suggestions hidden', html)

    def test_endpoint_returns_bounded_json_and_no_store(self) -> None:
        status, headers, payload = self.request(
            "/search/suggestions?q=portable+ssd&i=computers"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        document = json.loads(payload)
        self.assertEqual(document["query"], "portable ssd")
        self.assertEqual(document["department"], "computers")
        self.assertEqual(document["suggestions"][0]["value"], "portable ssd")
        self.assertLessEqual(len(document["suggestions"]), 10)
        self.assertTrue(
            all(set(item) == {"department", "kind", "value"} for item in document["suggestions"])
        )

    def test_endpoint_rejects_ambiguous_or_unknown_state(self) -> None:
        for path in (
            "/search/suggestions",
            "/search/suggestions?q=a&q=b",
            "/search/suggestions?q=portable&sort=price-asc",
            "/search/suggestions?q=portable&i=invalid",
            "/search/suggestions?q=bad%ZZ",
        ):
            with self.subTest(path=path):
                status, headers, payload = self.request(path)
                self.assertEqual(status, 400)
                self.assertEqual(
                    headers["content-type"], "application/json; charset=utf-8"
                )
                self.assertIn("error", json.loads(payload))

    def test_client_contract_supports_keyboard_mouse_and_request_cancellation(self) -> None:
        app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for marker in (
            'event.key === "ArrowDown"',
            'event.key === "ArrowUp"',
            'event.key === "Enter"',
            'event.key === "Escape"',
            'aria-activedescendant',
            "AbortController",
            "autocompleteForm.requestSubmit()",
            'document.addEventListener("pointerdown"',
        ):
            self.assertIn(marker, app)


if __name__ == "__main__":
    unittest.main()
