from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_store import (  # noqa: E402
    LOCAL_REVIEW_PROVENANCE,
    MAX_BODY_LENGTH,
    MAX_HEADLINE_LENGTH,
    REVIEW_SORT_HELPFUL,
    ReviewAuthenticationRequired,
    ReviewNotFound,
    ReviewPermissionDenied,
    ReviewSchemaError,
    ReviewValidationError,
    get_review,
    install_schema,
    list_reviews,
    register_review_product,
    reset_review_data,
    toggle_helpful_vote,
    upsert_review,
)
from review_catalog import normalize_local_reviews  # noqa: E402


PRODUCT_A = "B000000001"
PRODUCT_B = "B000000002"
UNKNOWN_PRODUCT = "B000000099"
NOW = "2026-07-22T12:00:00Z"


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        install_schema(self.conn)
        self.add_catalog_product(PRODUCT_A)
        self.add_commerce_product(PRODUCT_B)

    def tearDown(self) -> None:
        self.conn.close()

    def add_catalog_product(self, asin: str) -> None:
        self.conn.execute(
            """
            INSERT INTO catalog_products(
                asin,slug,title,brand,capacity,color,price_minor,list_price_minor,
                currency,rating,reviews,image_path,badge,evidence_class
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                asin,
                f"product-{asin.lower()}",
                f"Product {asin}",
                "QA Brand",
                "1 TB",
                "Black",
                1000,
                None,
                "USD",
                "4.5",
                10,
                "/static/test.jpg",
                "",
                "test-evidence",
            ),
        )

    def add_commerce_product(self, asin: str) -> None:
        self.conn.execute(
            """
            INSERT INTO commerce_offers(
                asin,slug,canonical_path,title,brand,capacity,color,price_minor,
                list_price_minor,currency,rating,reviews,image_path,badge,
                evidence_class,source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                asin,
                f"product-{asin.lower()}",
                f"/dp/{asin}",
                f"Product {asin}",
                "QA Brand",
                "2 TB",
                "Blue",
                2000,
                None,
                "USD",
                "4.7",
                20,
                "/static/test-2.jpg",
                "",
                "test-evidence",
                "task-fixture",
            ),
        )

    def add_account(self, number: int, name: str | None = None) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO accounts(
                email_normalized,display_name,password_salt,password_hash,
                password_scheme,created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                f"shopper-{number}@example.test",
                name or f"Shopper {number}",
                b"salt",
                b"hash",
                "scrypt-v1",
                NOW,
            ),
        )
        return int(cursor.lastrowid)

    def add_session(self, digest: str, account_id: int | None = None) -> str:
        self.conn.execute(
            """
            INSERT INTO browser_sessions(
                session_digest,reset_epoch,created_at,account_id
            ) VALUES (?,?,?,?)
            """,
            (digest, 1, NOW, account_id),
        )
        return digest

    def add_placed_order(self, account_id: int, asin: str, number: int) -> None:
        address_id = int(
            self.conn.execute(
                """
                INSERT INTO addresses(
                    account_id,full_name,address_line1,address_line2,city,
                    state_region,postal_code,country_code,phone,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    account_id,
                    "QA Shopper",
                    "1 Test Street",
                    "",
                    "Singapore",
                    "Singapore",
                    "018989",
                    "SG",
                    "+65 6000 0000",
                    NOW,
                    NOW,
                ),
            ).lastrowid
        )
        checkout_id = int(
            self.conn.execute(
                """
                INSERT INTO checkout_sessions(
                    account_id,idempotency_key,status,address_id,delivery_method,
                    shipping_minor,currency,created_at,updated_at,placed_at
                ) VALUES (?,?,'PLACED',?,'standard',0,'USD',?,?,?)
                """,
                (account_id, f"checkout-{number}", address_id, NOW, NOW, NOW),
            ).lastrowid
        )
        payment_id = int(
            self.conn.execute(
                """
                INSERT INTO payment_attempts(
                    checkout_id,account_id,method,status,amount_minor,currency,
                    cart_fingerprint,is_simulation,created_at
                ) VALUES (?,?,'test-card','APPROVED',1000,'USD',?,1,?)
                """,
                (checkout_id, account_id, f"fingerprint-{number}", NOW),
            ).lastrowid
        )
        order_id = int(
            self.conn.execute(
                """
                INSERT INTO orders(
                    account_id,checkout_id,payment_attempt_id,idempotency_key,status,
                    items_subtotal_minor,shipping_minor,total_minor,currency,
                    delivery_method,shipping_address_json,is_simulation,created_at
                ) VALUES (?,?,?,?,'PLACED',1000,0,1000,'USD','standard','{}',1,?)
                """,
                (account_id, checkout_id, payment_id, f"order-{number}", NOW),
            ).lastrowid
        )
        self.conn.execute(
            """
            INSERT INTO order_items(
                order_id,ordinal,asin,title,image_path,quantity,selection_json,
                unit_price_minor,line_total_minor,currency
            ) VALUES (?,1,?,?,?,1,'{}',1000,1000,'USD')
            """,
            (order_id, asin, f"Product {asin}", "/static/test.jpg"),
        )

    def write_review(
        self,
        account_number: int,
        *,
        asin: str = PRODUCT_A,
        rating: int = 5,
        at: str = NOW,
    ) -> tuple[int, str, dict[str, object]]:
        account_id = self.add_account(account_number)
        session = self.add_session(f"account-session-{account_number}", account_id)
        row = upsert_review(
            self.conn,
            session_digest=session,
            asin=asin,
            rating=rating,
            headline=f"Headline {account_number}",
            body=f"Body written by shopper {account_number}.",
            at=at,
        )
        return account_id, session, row

    def test_schema_install_is_additive_idempotent_and_versioned(self) -> None:
        install_schema(self.conn)
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("catalog_products", tables)
        self.assertIn("product_reviews", tables)
        self.assertIn("review_helpful_votes", tables)
        self.assertIn("review_product_catalog", tables)
        self.assertEqual(
            self.conn.execute(
                "SELECT schema_version FROM local_review_schema_meta WHERE singleton=1"
            ).fetchone()[0],
            1,
        )

        self.conn.execute(
            "UPDATE local_review_schema_meta SET schema_version=99 WHERE singleton=1"
        )
        with self.assertRaisesRegex(ReviewSchemaError, "version"):
            install_schema(self.conn)

    def test_review_requires_server_authenticated_session_and_existing_product(self) -> None:
        guest = self.add_session("guest")
        with self.assertRaises(ReviewAuthenticationRequired):
            upsert_review(
                self.conn,
                session_digest=guest,
                asin=PRODUCT_A,
                rating=5,
                headline="Guest review",
                body="Guests cannot write reviews.",
            )
        with self.assertRaises(ReviewNotFound):
            upsert_review(
                self.conn,
                session_digest="missing-session",
                asin=PRODUCT_A,
                rating=5,
                headline="Missing session",
                body="This must not be accepted.",
            )

        account_id = self.add_account(1)
        session = self.add_session("signed-in", account_id)
        with self.assertRaisesRegex(ReviewNotFound, "product"):
            upsert_review(
                self.conn,
                session_digest=session,
                asin=UNKNOWN_PRODUCT,
                rating=5,
                headline="Unknown item",
                body="This item does not exist.",
            )

        direct_only = upsert_review(
            self.conn,
            session_digest=session,
            asin=PRODUCT_B,
            rating=4,
            headline="Commerce product",
            body="Direct commerce offers count as existing products.",
            at=NOW,
        )
        self.assertEqual(direct_only["provenance"], LOCAL_REVIEW_PROVENANCE)

    def test_server_registered_browse_product_can_be_reviewed_without_an_offer(self) -> None:
        account_id = self.add_account(11, "Browse-only Reviewer")
        session = self.add_session("browse-only-reviewer", account_id)
        registered = register_review_product(
            self.conn, asin=UNKNOWN_PRODUCT.lower(), source_scope="home_snapshot"
        )
        self.assertEqual(registered, UNKNOWN_PRODUCT)
        self.assertIsNone(
            self.conn.execute(
                "SELECT asin FROM catalog_products WHERE asin=?", (UNKNOWN_PRODUCT,)
            ).fetchone()
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT asin FROM commerce_offers WHERE asin=?", (UNKNOWN_PRODUCT,)
            ).fetchone()
        )

        review = upsert_review(
            self.conn,
            session_digest=session,
            asin=UNKNOWN_PRODUCT,
            rating=4,
            headline="Known browse-only product",
            body="The review scope does not create a transaction offer.",
            at=NOW,
        )
        self.assertEqual(review["rating"], 4)
        self.assertFalse(review["verified_purchase"])

        self.conn.execute(
            "DELETE FROM review_product_catalog WHERE asin=?", (UNKNOWN_PRODUCT,)
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM product_reviews WHERE asin=?", (UNKNOWN_PRODUCT,)
            ).fetchone()[0],
            0,
        )

        with self.assertRaises(ReviewValidationError):
            register_review_product(
                self.conn, asin=UNKNOWN_PRODUCT, source_scope="client_form"
            )

    def test_one_review_per_account_and_asin_is_updated_in_place(self) -> None:
        account_id = self.add_account(1, "Editing Shopper")
        session = self.add_session("editing-session", account_id)
        first = upsert_review(
            self.conn,
            session_digest=session,
            asin=PRODUCT_A.lower(),
            rating=2,
            headline="First headline",
            body="First body.",
            at="2026-07-20T10:00:00Z",
        )
        second = upsert_review(
            self.conn,
            session_digest=session,
            asin=PRODUCT_A,
            rating=5,
            headline="  Updated   headline  ",
            body="Updated body.",
            at="2026-07-22T10:00:00Z",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["title"], "Updated headline")
        self.assertEqual(second["rating"], 5)
        self.assertEqual(second["created_at"], "2026-07-20T10:00:00Z")
        self.assertEqual(second["updated_at"], "2026-07-22T10:00:00Z")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM product_reviews WHERE account_id=? AND asin=?",
                (account_id, PRODUCT_A),
            ).fetchone()[0],
            1,
        )

        upsert_review(
            self.conn,
            session_digest=session,
            asin=PRODUCT_B,
            rating=4,
            headline="A different product",
            body="One account may review multiple products.",
            at=NOW,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM product_reviews WHERE account_id=?",
                (account_id,),
            ).fetchone()[0],
            2,
        )

    def test_validation_boundaries_and_parameterized_content(self) -> None:
        account_id = self.add_account(1)
        session = self.add_session("validation-session", account_id)
        base = {
            "conn": self.conn,
            "session_digest": session,
            "asin": PRODUCT_A,
            "rating": 5,
            "headline": "Valid headline",
            "body": "Valid body.",
        }
        for bad_rating in (True, 0, 6, "5"):
            with self.subTest(rating=bad_rating), self.assertRaises(ReviewValidationError):
                upsert_review(**{**base, "rating": bad_rating})  # type: ignore[arg-type]
        for field, value in (
            ("headline", "   "),
            ("headline", "h" * (MAX_HEADLINE_LENGTH + 1)),
            ("body", "\n\t"),
            ("body", "b" * (MAX_BODY_LENGTH + 1)),
        ):
            with self.subTest(field=field), self.assertRaises(ReviewValidationError):
                upsert_review(**{**base, field: value})

        hostile = "Great'); DROP TABLE accounts; --"
        row = upsert_review(
            self.conn,
            session_digest=session,
            asin=PRODUCT_A,
            rating=5,
            headline=hostile,
            body="<script>alert('escaped by the renderer')</script>",
            at=NOW,
        )
        self.assertEqual(row["title"], hostile)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1
        )
        with self.assertRaises(ReviewValidationError):
            list_reviews(self.conn, "B0000' OR 1=1 --")

    def test_verified_purchase_is_dynamically_derived_from_real_order_rows(self) -> None:
        account_id, session, row = self.write_review(1)
        self.assertFalse(row["verified_purchase"])
        self.add_placed_order(account_id, PRODUCT_B, 1)
        self.assertFalse(get_review(self.conn, row["id"])["verified_purchase"])
        self.add_placed_order(account_id, PRODUCT_A, 2)
        self.assertTrue(get_review(self.conn, row["id"])["verified_purchase"])

        with self.assertRaises(TypeError):
            upsert_review(  # type: ignore[call-arg]
                self.conn,
                session_digest=session,
                asin=PRODUCT_A,
                rating=5,
                headline="Cannot assert verification",
                body="The API has no client verification parameter.",
                verified_purchase=True,
            )
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(product_reviews)")
        }
        self.assertNotIn("verified_purchase", columns)

    def test_list_supports_star_filter_and_stable_recent_helpful_sorts(self) -> None:
        _, _, oldest = self.write_review(
            1, rating=5, at="2026-07-20T12:00:00Z"
        )
        _, _, newest = self.write_review(
            2, rating=3, at="2026-07-22T12:00:00Z"
        )
        _, _, middle = self.write_review(
            3, rating=5, at="2026-07-21T12:00:00Z"
        )
        guest_one = self.add_session("helpful-guest-1")
        guest_two = self.add_session("helpful-guest-2")
        guest_three = self.add_session("helpful-guest-3")
        toggle_helpful_vote(
            self.conn, session_digest=guest_one, review_id=oldest["id"], at=NOW
        )
        toggle_helpful_vote(
            self.conn, session_digest=guest_two, review_id=oldest["id"], at=NOW
        )
        toggle_helpful_vote(
            self.conn, session_digest=guest_three, review_id=middle["id"], at=NOW
        )

        recent = list_reviews(self.conn, PRODUCT_A)
        self.assertEqual(
            [row["id"] for row in recent],
            [newest["id"], middle["id"], oldest["id"]],
        )
        helpful = list_reviews(self.conn, PRODUCT_A, sort=REVIEW_SORT_HELPFUL)
        self.assertEqual(
            [row["id"] for row in helpful],
            [oldest["id"], middle["id"], newest["id"]],
        )
        five_star = list_reviews(self.conn, PRODUCT_A, star=5)
        self.assertEqual(
            [row["id"] for row in five_star], [middle["id"], oldest["id"]]
        )
        with self.assertRaisesRegex(ReviewValidationError, "sort"):
            list_reviews(self.conn, PRODUCT_A, sort="DROP TABLE product_reviews")
        with self.assertRaisesRegex(ReviewValidationError, "rating"):
            list_reviews(self.conn, PRODUCT_A, star=0)

    def test_helpful_vote_is_toggleable_unique_per_guest_or_account_and_not_own(self) -> None:
        author_id, author_session, review = self.write_review(1)
        review_id = review["id"]
        guest_a = self.add_session("guest-a")
        guest_b = self.add_session("guest-b")

        on = toggle_helpful_vote(
            self.conn, session_digest=guest_a, review_id=review_id, at=NOW
        )
        self.assertEqual(on, {"review_id": review_id, "found_helpful": True, "helpful_count": 1})
        viewed = get_review(
            self.conn, review_id, viewer_session_digest=guest_a
        )
        self.assertTrue(viewed["viewer_found_helpful"])
        off = toggle_helpful_vote(
            self.conn, session_digest=guest_a, review_id=review_id, at=NOW
        )
        self.assertEqual(off["found_helpful"], False)
        self.assertEqual(off["helpful_count"], 0)

        toggle_helpful_vote(
            self.conn, session_digest=guest_a, review_id=review_id, at=NOW
        )
        toggle_helpful_vote(
            self.conn, session_digest=guest_b, review_id=review_id, at=NOW
        )
        self.assertEqual(get_review(self.conn, review_id)["helpful_count"], 2)

        voter_id = self.add_account(2)
        voter_session_one = self.add_session("voter-session-one", voter_id)
        voter_session_two = self.add_session("voter-session-two", voter_id)
        toggle_helpful_vote(
            self.conn, session_digest=voter_session_one, review_id=review_id, at=NOW
        )
        self.assertEqual(get_review(self.conn, review_id)["helpful_count"], 3)
        account_toggle_off = toggle_helpful_vote(
            self.conn, session_digest=voter_session_two, review_id=review_id, at=NOW
        )
        self.assertFalse(account_toggle_off["found_helpful"])
        self.assertEqual(account_toggle_off["helpful_count"], 2)

        with self.assertRaises(ReviewPermissionDenied):
            toggle_helpful_vote(
                self.conn,
                session_digest=author_session,
                review_id=review_id,
                at=NOW,
            )
        with self.assertRaises(ReviewNotFound):
            toggle_helpful_vote(
                self.conn,
                session_digest="missing-session",
                review_id=review_id,
                at=NOW,
            )
        self.assertEqual(
            self.conn.execute(
                """
                SELECT COUNT(*) FROM review_helpful_votes
                WHERE review_id=? AND voter_account_id=?
                """,
                (int(review_id), voter_id),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(author_id, 1)

    def test_database_constraints_reject_direct_self_vote_and_unknown_product(self) -> None:
        author_id, _, review = self.write_review(1)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "own review"):
            self.conn.execute(
                """
                INSERT INTO review_helpful_votes(
                    review_id,voter_account_id,voter_session_digest,created_at
                ) VALUES (?,?,NULL,?)
                """,
                (int(review["id"]), author_id, NOW),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "product does not exist"):
            self.conn.execute(
                """
                INSERT INTO product_reviews(
                    account_id,asin,rating,headline,body,created_at,updated_at
                ) VALUES (?,?,5,'Headline','Body',?,?)
                """,
                (author_id, UNKNOWN_PRODUCT, NOW, NOW),
            )

    def test_rows_match_review_catalog_contract_and_viewer_capabilities(self) -> None:
        _, session, row = self.write_review(1)
        required = {
            "id",
            "provenance",
            "author_display_name",
            "rating",
            "title",
            "body",
            "created_at",
            "verified_purchase",
            "helpful_count",
        }
        self.assertLessEqual(required, set(row))
        self.assertEqual(row["provenance"], "local_user_review")
        self.assertIs(row["verified_purchase"], False)
        self.assertIsInstance(row["helpful_count"], int)
        self.assertTrue(row["owned_by_viewer"])
        self.assertFalse(row["can_mark_helpful"])
        normalized = normalize_local_reviews([row])
        self.assertEqual(normalized[0]["provenance"], "local_user_review")
        self.assertEqual(normalized[0]["title"], row["title"])

        anonymous = self.add_session("anonymous-viewer")
        anonymous_row = list_reviews(
            self.conn, PRODUCT_A, viewer_session_digest=anonymous
        )[0]
        self.assertFalse(anonymous_row["owned_by_viewer"])
        self.assertTrue(anonymous_row["can_mark_helpful"])
        self.assertEqual(session, "account-session-1")

    def test_explicit_reset_and_foreign_key_cleanup_leave_no_orphans(self) -> None:
        account_id, _, review = self.write_review(1)
        guest = self.add_session("reset-guest")
        toggle_helpful_vote(
            self.conn, session_digest=guest, review_id=review["id"], at=NOW
        )
        reset_review_data(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM product_reviews").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM review_helpful_votes").fetchone()[0], 0
        )

        upsert_review(
            self.conn,
            session_digest="account-session-1",
            asin=PRODUCT_A,
            rating=5,
            headline="Written again",
            body="This review will cascade with its account.",
            at=NOW,
        )
        self.conn.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM product_reviews").fetchone()[0], 0
        )

    def test_product_cleanup_waits_until_asin_is_absent_from_both_registries(self) -> None:
        self.add_commerce_product(PRODUCT_A)
        _, _, review = self.write_review(1)
        self.conn.execute("DELETE FROM catalog_products WHERE asin=?", (PRODUCT_A,))
        self.assertIsNotNone(get_review(self.conn, review["id"]))
        self.conn.execute("DELETE FROM commerce_offers WHERE asin=?", (PRODUCT_A,))
        self.assertIsNone(get_review(self.conn, review["id"]))


if __name__ == "__main__":
    unittest.main()
