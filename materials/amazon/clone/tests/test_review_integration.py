from __future__ import annotations

import http.client
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    HOME_PRODUCT_CATALOG,
    MAX_FORM_BYTES,
    PublicHandler,
    ReusableThreadingHTTPServer,
    SEARCH_COMMERCE_PRODUCT_CATALOG,
    digest,
)
from store import Store, TARGET_ASIN, TEST_PAYMENT_METHOD  # noqa: E402


SESSION_COOKIE = "amazon_clone_session"
RICH_ASINS = (
    "B0874XN4D8",
    "B0CHFSWM2P",
    "B01M16WBW1",
    "B0BG6B2D4D",
    "B08HN37XC1",
    "168281808X",
    "B074PVTPBW",
    "B0BJPXXM7D",
    "B071V91LGC",
    "B0BQR2BQYZ",
    "B00FLYWNYQ",
    "B07K74LDCH",
    "B088BZTYFP",
)
NO_AGGREGATE_ASIN = "B07CRG94G3"
CATALOG_NO_AGGREGATE_ASIN = "B08GTYFC37"
UNKNOWN_ASIN = "B000000000"


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class ReviewIntegrationTests(unittest.TestCase):
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
        fields: dict[str, str] | None = None,
        raw_body: bytes | None = None,
        cookie: str = "",
        origin: str | None = "same-origin",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        if fields is not None and raw_body is not None:
            raise ValueError("fields and raw_body are mutually exclusive")
        body = urlencode(fields).encode("utf-8") if fields is not None else raw_body
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
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
    def cookie_from(headers: dict[str, list[str]]) -> str:
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
            raise AssertionError("invalid session cookie")
        return digest(token)

    def anonymous_cookie(self) -> str:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return self.cookie_from(headers)

    def account_cookie(self, number: int, name: str | None = None) -> str:
        cookie = self.anonymous_cookie()
        created = self.store.register_account(
            self.session_digest(cookie),
            f"review-shopper-{number}@example.test",
            name or f"Review Shopper {number}",
            "Correct-Horse-921",
        )
        self.assertTrue(created)
        return cookie

    def set_now(self, value: str) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE meta SET value=? WHERE key='controlled_now'", (value,)
            )

    def known_asins(self) -> tuple[str, ...]:
        with self.store.connect() as conn:
            core_asins = {
                str(row[0])
                for row in conn.execute(
                    "SELECT asin FROM catalog_products UNION SELECT asin FROM commerce_offers"
                )
            }
        return tuple(sorted(set(HOME_PRODUCT_CATALOG) | core_asins))

    def post_review(
        self,
        cookie: str,
        *,
        asin: str = TARGET_ASIN,
        rating: int = 5,
        headline: str = "A useful local review",
        body: str = "This review was written inside the local clone.",
        expected_status: int = 303,
    ) -> tuple[dict[str, list[str]], bytes]:
        status, headers, payload = self.request(
            "POST",
            f"/product-reviews/{asin}",
            fields={
                "rating": str(rating),
                "headline": headline,
                "body": body,
            },
            cookie=cookie,
        )
        self.assertEqual(status, expected_status, payload)
        return headers, payload

    def place_target_order(self, cookie: str) -> None:
        session = self.session_digest(cookie)
        self.store.add_cart_item(session, TARGET_ASIN, 1)
        self.store.start_checkout(session)
        self.store.save_checkout_address(
            session,
            {
                "full_name": "Verified Reviewer",
                "address_line1": "1 Test Street",
                "address_line2": "",
                "city": "Singapore",
                "state_region": "Singapore",
                "postal_code": "018989",
                "country_code": "SG",
                "phone": "+65 6000 0000",
            },
        )
        self.store.select_delivery(session, "standard")
        self.store.select_test_payment(session, TEST_PAYMENT_METHOD)
        self.store.place_order(session, "review-verified-order-0001")

    def test_all_known_product_review_destinations_are_live(self) -> None:
        cookie = self.anonymous_cookie()
        for asin in RICH_ASINS:
            with self.subTest(asin=asin, page="pdp"):
                status, _, body = self.request("GET", f"/dp/{asin}", cookie=cookie)
                html = body.decode("utf-8")
                self.assertEqual(status, 200)
                self.assertIn('id="customerReviews"', html)
                self.assertIn('data-provenance="source_snapshot_aggregate"', html)
                self.assertIn('data-provenance="local_user_review"', html)
        known_asins = self.known_asins()
        self.assertTrue(set(RICH_ASINS).issubset(known_asins))
        self.assertTrue(set(SEARCH_COMMERCE_PRODUCT_CATALOG).issubset(known_asins))
        for asin in known_asins:
            with self.subTest(asin=asin, page="reviews"):
                status, _, body = self.request(
                    "GET", f"/product-reviews/{asin}", cookie=cookie
                )
                html = body.decode("utf-8")
                self.assertEqual(status, 200, body)
                self.assertIn('id="customerReviews"', html)
                if asin in RICH_ASINS:
                    self.assertIn(
                        'data-provenance="source_snapshot_aggregate"', html
                    )
                else:
                    self.assertIn(
                        'data-provenance="source_aggregate_unavailable"', html
                    )
                    self.assertNotIn(
                        'data-provenance="source_snapshot_aggregate"', html
                    )

        self.assertEqual(
            self.request(
                "GET", f"/product-reviews/{UNKNOWN_ASIN}", cookie=cookie
            )[0],
            404,
        )

    def test_every_direct_search_card_has_a_non_invented_review_destination(self) -> None:
        cookie = self.anonymous_cookie()
        direct_search_asins = tuple(sorted(SEARCH_COMMERCE_PRODUCT_CATALOG))
        self.assertTrue(direct_search_asins)
        self.assertTrue(set(direct_search_asins).issubset(self.known_asins()))
        for asin in direct_search_asins:
            with self.subTest(asin=asin):
                status, _, body = self.request(
                    "GET", f"/product-reviews/{asin}", cookie=cookie
                )
                html = body.decode("utf-8")
                self.assertEqual(status, 200, body)
                if asin in RICH_ASINS:
                    self.assertIn(
                        'data-provenance="source_snapshot_aggregate"', html
                    )
                else:
                    self.assertIn(
                        'data-provenance="source_aggregate_unavailable"', html
                    )
                    self.assertNotIn(
                        'data-provenance="source_snapshot_aggregate"', html
                    )

    def test_home_only_product_without_aggregate_supports_local_review_filters_and_helpful(self) -> None:
        author = self.account_cookie(31, "Browse-only Reviewer")
        for path in (
            f"/dp/{NO_AGGREGATE_ASIN}",
            f"/product-reviews/{NO_AGGREGATE_ASIN}",
        ):
            status, _, body = self.request("GET", path, cookie=author)
            html = body.decode("utf-8")
            self.assertEqual(status, 200, body)
            self.assertIn('id="customerReviews"', html)
            self.assertIn(
                'data-provenance="source_aggregate_unavailable"', html
            )
            self.assertNotIn(
                'data-provenance="source_snapshot_aggregate"', html
            )
        pdp_html = self.request(
            "GET", f"/dp/{NO_AGGREGATE_ASIN}", cookie=author
        )[2].decode("utf-8")
        self.assertIn(
            f'href="/product-reviews/{NO_AGGREGATE_ASIN}"', pdp_html
        )

        headers, _ = self.post_review(
            author,
            asin=NO_AGGREGATE_ASIN,
            rating=4,
            headline="Local browse-only review",
            body="This known homepage product has no source aggregate.",
        )
        self.assertEqual(
            headers["location"],
            [f"/product-reviews/{NO_AGGREGATE_ASIN}#customerReviews"],
        )
        with self.store.connect() as conn:
            scope = conn.execute(
                "SELECT source_scope FROM review_product_catalog WHERE asin=?",
                (NO_AGGREGATE_ASIN,),
            ).fetchone()
        self.assertIsNotNone(scope)
        self.assertEqual(scope[0], "home_snapshot")

        status, _, filtered_body = self.request(
            "GET",
            f"/product-reviews/{NO_AGGREGATE_ASIN}?reviewStar=4&reviewSort=helpful",
            cookie=author,
        )
        filtered_html = filtered_body.decode("utf-8")
        self.assertEqual(status, 200, filtered_body)
        self.assertIn("Local browse-only review", filtered_html)
        self.assertIn('data-review-star-filter="4" aria-current="page"', filtered_html)
        self.assertIn('data-review-sort="helpful" aria-current="page"', filtered_html)
        self.assertNotIn("Verified Purchase", filtered_html)
        self.assertIn(
            'data-provenance="source_aggregate_unavailable"', filtered_html
        )

        review_id = self.store.reviews_for_session(
            self.session_digest(author), NO_AGGREGATE_ASIN
        )[0]["id"]
        guest = self.anonymous_cookie()
        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{NO_AGGREGATE_ASIN}/helpful",
            fields={"reviewId": review_id},
            cookie=guest,
        )
        self.assertEqual(status, 303)
        review = self.store.reviews_for_session(
            self.session_digest(guest), NO_AGGREGATE_ASIN
        )[0]
        self.assertEqual(review["helpful_count"], 1)
        self.assertTrue(review["viewer_found_helpful"])

        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{UNKNOWN_ASIN}",
            fields={"rating": "5", "headline": "Unknown", "body": "Unknown"},
            cookie=author,
        )
        self.assertEqual(status, 404)

    def test_book_and_beauty_local_reviews_do_not_rewrite_source_aggregates(self) -> None:
        cookie = self.account_cookie(1, "Cross-category Reviewer")
        cases = (
            (
                "168281808X",
                "There are 0 customer reviews",
                ("None out of 5", "null out of 5", "★★★★★"),
            ),
            (
                "B074PVTPBW",
                "184,921 ratings",
                ('class="review-topics"', 'class="review-histogram"'),
            ),
        )

        for index, (asin, source_copy, forbidden_before) in enumerate(cases, 1):
            with self.subTest(asin=asin, phase="source-only"):
                for path in (f"/dp/{asin}", f"/product-reviews/{asin}"):
                    status, _, body = self.request("GET", path, cookie=cookie)
                    self.assertEqual(status, 200, body)
                review_html = self.request(
                    "GET", f"/product-reviews/{asin}", cookie=cookie
                )[2].decode("utf-8")
                self.assertIn(source_copy, review_html)
                self.assertIn("No source review card", review_html)
                for unsupported_markup in forbidden_before:
                    self.assertNotIn(unsupported_markup, review_html)
                if asin == "B074PVTPBW":
                    self.assertIn("4.6 out of 5", review_html)

            headline = f"Local cross-category review {index}"
            self.post_review(
                cookie,
                asin=asin,
                rating=5,
                headline=headline,
                body="This local row must remain separate from source evidence.",
            )
            status, _, body = self.request(
                "GET", f"/product-reviews/{asin}", cookie=cookie
            )
            html = body.decode("utf-8")
            with self.subTest(asin=asin, phase="after-local-review"):
                self.assertEqual(status, 200, body)
                self.assertIn(source_copy, html)
                self.assertIn(headline, html)
                self.assertIn('data-provenance="source_snapshot_aggregate"', html)
                self.assertIn('data-provenance="local_user_review"', html)
                self.assertNotIn("None out of 5", html)
                self.assertNotIn("null out of 5", html)
                if asin == "B074PVTPBW":
                    self.assertIn("4.6 out of 5", html)

    def test_every_rendered_product_review_href_is_supported_and_live(self) -> None:
        cookie = self.anonymous_cookie()
        status, _, search_body = self.request(
            "GET", "/s?k=portable+ssd", cookie=cookie
        )
        self.assertEqual(status, 200)
        hrefs = set(
            re.findall(
                r'href="(/product-reviews/([A-Z0-9]{10}))"',
                search_body.decode("utf-8"),
            )
        )
        self.assertTrue(hrefs)
        known_asins = set(self.known_asins())
        for href, asin in hrefs:
            self.assertIn(asin, known_asins)
            self.assertEqual(self.request("GET", href, cookie=cookie)[0], 200)
        self.assertIn(
            f'/product-reviews/{CATALOG_NO_AGGREGATE_ASIN}',
            search_body.decode("utf-8"),
        )

        status, _, t7_body = self.request("GET", f"/dp/{TARGET_ASIN}", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(
            f'href="/product-reviews/{TARGET_ASIN}"', t7_body.decode("utf-8")
        )

    def test_anonymous_write_redirect_is_safe_and_mutations_require_same_origin(self) -> None:
        guest = self.anonymous_cookie()
        status, _, page = self.request(
            "GET", f"/product-reviews/{TARGET_ASIN}", cookie=guest
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Sign in to write a review", page)

        status, headers, body = self.request(
            "POST",
            f"/product-reviews/{TARGET_ASIN}",
            fields={"rating": "5", "headline": "Guest", "body": "Guest body"},
            cookie=guest,
        )
        self.assertEqual((status, body), (303, b""))
        location = urlsplit(headers["location"][0])
        self.assertEqual(location.path, "/ap/signin")
        return_to = parse_qs(location.query)["openid.return_to"][0]
        self.assertEqual(
            return_to, f"/product-reviews/{TARGET_ASIN}#reviewComposer"
        )
        self.assertFalse(urlsplit(return_to).scheme)
        self.assertFalse(urlsplit(return_to).netloc)

        for origin in (None, "https://evil.example"):
            with self.subTest(origin=origin):
                status, _, _ = self.request(
                    "POST",
                    f"/product-reviews/{TARGET_ASIN}",
                    fields={"rating": "5", "headline": "Guest", "body": "Body"},
                    cookie=guest,
                    origin=origin,
                )
                self.assertEqual(status, 403)

    def test_review_body_http_and_domain_limits_are_consistent_for_unicode(self) -> None:
        cookie = self.account_cookie(1)
        valid_unicode_body = "🙂" * 10_000
        encoded_valid_form = urlencode(
            {
                "rating": "5",
                "headline": "Unicode boundary",
                "body": valid_unicode_body,
            }
        ).encode("utf-8")
        self.assertGreater(len(encoded_valid_form), 16 * 1024)
        self.assertLessEqual(len(encoded_valid_form), MAX_FORM_BYTES)
        headers, _ = self.post_review(
            cookie,
            headline="Unicode boundary",
            body=valid_unicode_body,
        )
        self.assertEqual(
            headers["location"],
            [f"/product-reviews/{TARGET_ASIN}#customerReviews"],
        )
        self.post_review(cookie, body="🙂" * 10_001, expected_status=400)

        oversized = b"body=" + b"x" * MAX_FORM_BYTES
        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{TARGET_ASIN}",
            raw_body=oversized,
            cookie=cookie,
        )
        self.assertEqual(status, 413)

    def test_store_rotation_merges_guest_helpful_votes_without_duplicates_or_self_votes(self) -> None:
        first_author = self.account_cookie(1, "First Author")
        self.post_review(
            first_author,
            headline="Existing account vote target",
            body="The account already voted for this review.",
        )
        account = self.account_cookie(2, "Signing In Reviewer")
        self.post_review(
            account,
            headline="Future self vote target",
            body="A guest vote for this review must disappear after sign-in.",
        )
        third_author = self.account_cookie(3, "Third Author")
        self.post_review(
            third_author,
            headline="Migrated vote target",
            body="A non-conflicting guest vote should become an account vote.",
        )

        account_digest = self.session_digest(account)
        rows = self.store.reviews_for_session(account_digest, TARGET_ASIN)
        ids = {row["title"]: row["id"] for row in rows}
        self.store.toggle_review_helpful(
            account_digest, TARGET_ASIN, ids["Existing account vote target"]
        )

        guest = self.anonymous_cookie()
        guest_digest = self.session_digest(guest)
        for title in (
            "Existing account vote target",
            "Future self vote target",
            "Migrated vote target",
        ):
            self.store.toggle_review_helpful(
                guest_digest, TARGET_ASIN, ids[title]
            )

        self.store.begin_signin(
            guest_digest, "review-shopper-2@example.test", None
        )
        authenticated, _ = self.store.authenticate_session(
            guest_digest, "Correct-Horse-921"
        )
        self.assertTrue(authenticated)
        rotated_digest = "rotated-review-session"
        self.store.rotate_authenticated_session(guest_digest, rotated_digest)

        merged = {
            row["title"]: row
            for row in self.store.reviews_for_session(rotated_digest, TARGET_ASIN)
        }
        self.assertEqual(merged["Existing account vote target"]["helpful_count"], 1)
        self.assertTrue(
            merged["Existing account vote target"]["viewer_found_helpful"]
        )
        self.assertEqual(merged["Future self vote target"]["helpful_count"], 0)
        self.assertTrue(merged["Future self vote target"]["owned_by_viewer"])
        self.assertFalse(
            merged["Future self vote target"]["viewer_found_helpful"]
        )
        self.assertEqual(merged["Migrated vote target"]["helpful_count"], 1)
        self.assertTrue(merged["Migrated vote target"]["viewer_found_helpful"])

        account_id = self.store.account_for_session(rotated_digest)["account_id"]
        with self.store.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM review_helpful_votes "
                    "WHERE voter_session_digest=?",
                    (guest_digest,),
                ).fetchone()[0],
                0,
            )
            account_vote_ids = {
                str(row[0])
                for row in conn.execute(
                    "SELECT review_id FROM review_helpful_votes "
                    "WHERE voter_account_id=?",
                    (account_id,),
                )
            }
        self.assertEqual(
            account_vote_ids,
            {
                ids["Existing account vote target"],
                ids["Migrated vote target"],
            },
        )

    def test_http_signin_rotation_deduplicates_prelogin_helpful_identity(self) -> None:
        author = self.account_cookie(1, "Vote Author")
        self.post_review(
            author,
            headline="HTTP duplicate target",
            body="The signed-in account has already voted here.",
        )
        signing_in = self.account_cookie(2, "HTTP Signing In")
        self.post_review(
            signing_in,
            headline="HTTP self target",
            body="The pre-login guest vote must not become a self vote.",
        )
        signing_in_digest = self.session_digest(signing_in)
        rows = self.store.reviews_for_session(signing_in_digest, TARGET_ASIN)
        ids = {row["title"]: row["id"] for row in rows}
        self.store.toggle_review_helpful(
            signing_in_digest, TARGET_ASIN, ids["HTTP duplicate target"]
        )

        guest = self.anonymous_cookie()
        guest_digest = self.session_digest(guest)
        for title in ("HTTP duplicate target", "HTTP self target"):
            status, _, _ = self.request(
                "POST",
                f"/product-reviews/{TARGET_ASIN}/helpful",
                fields={"reviewId": ids[title]},
                cookie=guest,
            )
            self.assertEqual(status, 303)

        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={
                "email": "review-shopper-2@example.test",
                "openid.return_to": f"/product-reviews/{TARGET_ASIN}",
            },
            cookie=guest,
        )
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(headers["location"], ["/ap/signin?stage=password"])
        status, headers, body = self.request(
            "POST",
            "/ap/signin",
            fields={"password": "Correct-Horse-921"},
            cookie=guest,
        )
        self.assertEqual((status, body), (303, b""))
        self.assertEqual(
            headers["location"], [f"/product-reviews/{TARGET_ASIN}"]
        )
        rotated_cookie = self.cookie_from(headers)
        rotated_digest = self.session_digest(rotated_cookie)

        merged = {
            row["title"]: row
            for row in self.store.reviews_for_session(rotated_digest, TARGET_ASIN)
        }
        self.assertEqual(merged["HTTP duplicate target"]["helpful_count"], 1)
        self.assertTrue(merged["HTTP duplicate target"]["viewer_found_helpful"])
        self.assertEqual(merged["HTTP self target"]["helpful_count"], 0)
        self.assertTrue(merged["HTTP self target"]["owned_by_viewer"])
        with self.store.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM review_helpful_votes "
                    "WHERE voter_session_digest=?",
                    (guest_digest,),
                ).fetchone()[0],
                0,
            )

    def test_maximum_account_display_name_renders_without_a_server_error(self) -> None:
        display_name = "N" * 128
        cookie = self.account_cookie(1, display_name)
        self.post_review(cookie, headline="Long-name reviewer", body="Still valid.")
        status, _, body = self.request(
            "GET", f"/product-reviews/{TARGET_ASIN}", cookie=cookie
        )
        self.assertEqual(status, 200, body)
        self.assertIn(display_name, body.decode("utf-8"))

    def test_same_account_update_escaping_and_provenance_separation(self) -> None:
        cookie = self.account_cookie(1, "QA <Shopper>")
        self.set_now("2026-07-21T10:00:00Z")
        self.post_review(
            cookie,
            rating=2,
            headline="<script>first</script>",
            body="<img src=x onerror=alert(1)>",
        )
        with self.store.connect() as conn:
            first = dict(conn.execute("SELECT * FROM product_reviews").fetchone())

        self.set_now("2026-07-22T10:00:00Z")
        self.post_review(
            cookie,
            rating=5,
            headline="<b>Updated title</b>",
            body="Updated & safe body.",
        )
        with self.store.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM product_reviews")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["review_id"], first["review_id"])
        self.assertEqual(rows[0]["created_at"], "2026-07-21T10:00:00Z")
        self.assertEqual(rows[0]["updated_at"], "2026-07-22T10:00:00Z")

        status, _, body = self.request(
            "GET", f"/product-reviews/{TARGET_ASIN}", cookie=cookie
        )
        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('data-provenance="source_snapshot_aggregate"', html)
        self.assertIn('data-provenance="local_user_review"', html)
        self.assertIn("&lt;b&gt;Updated title&lt;/b&gt;", html)
        self.assertNotIn("<b>Updated title</b>", html)
        self.assertNotIn("<script>first</script>", html)
        self.assertNotIn("review-source-excerpt", html)
        self.assertIn("No source review card is invented here", html)
        review_section = html.split('id="customerReviews"', 1)[1]
        for mojibake in ("鈥", "鈽", "槄", "渰", "�"):
            self.assertNotIn(mojibake, review_section)

    def test_verified_purchase_comes_from_placed_order_not_review_form(self) -> None:
        purchased_cookie = self.account_cookie(1, "Verified Buyer")
        self.place_target_order(purchased_cookie)
        self.post_review(
            purchased_cookie,
            headline="Actually purchased",
            body="This account has a placed order item for the ASIN.",
        )
        other_cookie = self.account_cookie(2, "Browser Only")
        self.post_review(
            other_cookie,
            headline="Not purchased",
            body="This account has no placed order for the ASIN.",
        )

        rows = self.store.reviews_for_session(
            self.session_digest(other_cookie), TARGET_ASIN
        )
        by_title = {row["title"]: row for row in rows}
        self.assertTrue(by_title["Actually purchased"]["verified_purchase"])
        self.assertFalse(by_title["Not purchased"]["verified_purchase"])

        _, _, body = self.request(
            "GET", f"/product-reviews/{TARGET_ASIN}", cookie=other_cookie
        )
        html = body.decode("utf-8")
        purchased_card = html.split("Actually purchased", 1)[1].split("</article>", 1)[0]
        not_purchased_card = html.split("Not purchased", 1)[1].split("</article>", 1)[0]
        self.assertIn("Verified Purchase", purchased_card)
        self.assertNotIn("Verified Purchase", not_purchased_card)

    def test_star_filter_recent_helpful_sort_and_invalid_query(self) -> None:
        oldest = self.account_cookie(1)
        middle = self.account_cookie(2)
        newest = self.account_cookie(3)
        self.set_now("2026-07-20T12:00:00Z")
        self.post_review(oldest, rating=5, headline="Old five", body="Old body")
        self.set_now("2026-07-21T12:00:00Z")
        self.post_review(middle, rating=1, headline="Middle one", body="Middle body")
        self.set_now("2026-07-22T12:00:00Z")
        self.post_review(newest, rating=5, headline="New five", body="New body")

        review_id = self.store.reviews_for_session(
            self.session_digest(oldest), TARGET_ASIN
        )[-1]["id"]
        for _ in range(2):
            guest = self.anonymous_cookie()
            status, _, _ = self.request(
                "POST",
                f"/product-reviews/{TARGET_ASIN}/helpful",
                fields={"reviewId": review_id},
                cookie=guest,
            )
            self.assertEqual(status, 303)

        _, _, recent_body = self.request(
            "GET", f"/product-reviews/{TARGET_ASIN}", cookie=newest
        )
        recent = recent_body.decode("utf-8").split(
            'class="review-local-list"', 1
        )[1]
        self.assertLess(recent.index("New five"), recent.index("Old five"))

        _, _, helpful_body = self.request(
            "GET",
            f"/product-reviews/{TARGET_ASIN}?reviewSort=helpful",
            cookie=newest,
        )
        helpful = helpful_body.decode("utf-8").split(
            'class="review-local-list"', 1
        )[1]
        self.assertLess(helpful.index("Old five"), helpful.index("New five"))

        _, _, filtered_body = self.request(
            "GET",
            f"/product-reviews/{TARGET_ASIN}?reviewStar=1",
            cookie=newest,
        )
        filtered = filtered_body.decode("utf-8").split(
            'class="review-local-list"', 1
        )[1]
        self.assertIn("Middle one", filtered)
        self.assertNotIn("Old five", filtered)
        self.assertNotIn("New five", filtered)

        for query in ("reviewStar=0", "reviewStar=5&reviewStar=4", "reviewSort=popular"):
            with self.subTest(query=query):
                self.assertEqual(
                    self.request(
                        "GET",
                        f"/product-reviews/{TARGET_ASIN}?{query}",
                        cookie=newest,
                    )[0],
                    400,
                )

    def test_helpful_toggle_viewer_state_cross_product_and_self_vote(self) -> None:
        author = self.account_cookie(1)
        self.post_review(author, headline="Vote target", body="Vote target body")
        review_id = self.store.reviews_for_session(
            self.session_digest(author), TARGET_ASIN
        )[0]["id"]

        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{TARGET_ASIN}/helpful",
            fields={"reviewId": review_id},
            cookie=author,
        )
        self.assertEqual(status, 403)

        guest = self.anonymous_cookie()
        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{TARGET_ASIN}/helpful",
            fields={"reviewId": review_id},
            cookie=guest,
        )
        self.assertEqual(status, 303)
        _, _, body = self.request(
            "GET", f"/product-reviews/{TARGET_ASIN}", cookie=guest
        )
        html = body.decode("utf-8")
        self.assertIn('data-viewer-found-helpful="true"', html)
        self.assertIn('aria-pressed="true">Helpful ✓', html)

        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{TARGET_ASIN}/helpful",
            fields={"reviewId": review_id},
            cookie=guest,
        )
        self.assertEqual(status, 303)
        self.assertEqual(
            self.store.reviews_for_session(
                self.session_digest(guest), TARGET_ASIN
            )[0]["helpful_count"],
            0,
        )

        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{RICH_ASINS[1]}/helpful",
            fields={"reviewId": review_id},
            cookie=guest,
        )
        self.assertEqual(status, 404)
        status, _, _ = self.request(
            "POST",
            f"/product-reviews/{TARGET_ASIN}/helpful",
            fields={"reviewId": "999999"},
            cookie=guest,
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
