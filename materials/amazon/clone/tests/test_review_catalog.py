from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from review_catalog import (  # noqa: E402
    FIXTURE_PATH,
    build_review_view,
    get_review_evidence,
    normalize_local_reviews,
    render_reviews_section,
    supported_review_asins,
)


RICH_ASINS = {
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
}


def local_review(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "local-1",
        "provenance": "local_user_review",
        "author_display_name": "QA Shopper",
        "rating": 5,
        "title": "Fast local test",
        "body": "This row was created inside the clone.",
        "created_at": "2026-07-22",
        "verified_purchase": True,
        "helpful_count": 2,
    }
    row.update(overrides)
    return row


class ReviewEvidenceTests(unittest.TestCase):
    def test_all_supported_pdps_have_source_review_evidence(self) -> None:
        self.assertEqual(set(supported_review_asins()), RICH_ASINS)
        expected = {
            "B0874XN4D8": (4.7, 38085),
            "B0CHFSWM2P": (4.6, 2894),
            "B01M16WBW1": (4.5, 447592),
            "B0BG6B2D4D": (4.8, 33),
            "B08HN37XC1": (4.6, 91231),
            "168281808X": (None, 0),
            "B074PVTPBW": (4.6, 184921),
            "B0BJPXXM7D": (4.6, 143321),
            "B071V91LGC": (4.8, 8730),
            "B0BQR2BQYZ": (4.5, 41071),
            "B00FLYWNYQ": (4.7, 173211),
            "B07K74LDCH": (4.7, 19909),
            "B088BZTYFP": (4.6, 17115),
        }
        for asin, pair in expected.items():
            evidence = get_review_evidence(asin)
            self.assertIsNotNone(evidence)
            summary = evidence["rating_summary"]
            self.assertEqual((summary["rating"], summary["rating_count"]), pair)
            self.assertEqual(summary["provenance"], "source_snapshot_aggregate")
            self.assertTrue((REPOSITORY_ROOT / summary["evidence_path"]).is_file())

    def test_t7_histogram_and_topics_are_snapshot_bound(self) -> None:
        evidence = get_review_evidence("B0874XN4D8")
        self.assertEqual(
            evidence["histogram"]["percent_by_star"],
            {"5": 87, "4": 8, "3": 2, "2": 0, "1": 3},
        )
        self.assertEqual(evidence["histogram"]["captured_rating_count"], 38071)
        self.assertEqual(len(evidence["source_topics"]), 8)
        self.assertEqual(evidence["source_topics"][0]["label"], "Reliability")
        self.assertEqual(evidence["source_topics"][-1]["negative"], 201)

    def test_no_complete_source_review_card_is_claimed(self) -> None:
        for asin in RICH_ASINS:
            evidence = get_review_evidence(asin)
            self.assertFalse(evidence["individual_review_evidence"]["available"])
            self.assertIn("author", evidence["individual_review_evidence"]["missing_fields"])
            self.assertIn("helpful_count", evidence["individual_review_evidence"]["missing_fields"])

        t7 = get_review_evidence("B0874XN4D8")
        self.assertEqual(len(t7["source_excerpts"]), 3)
        for excerpt in t7["source_excerpts"]:
            self.assertEqual(excerpt["provenance"], "source_topic_excerpt")
            self.assertLessEqual(len(excerpt["excerpt"].split()), 25)
            self.assertEqual(
                set(excerpt["missing_fields"]),
                {"author", "title", "date", "rating", "helpful_count"},
            )

    def test_fixture_contains_no_seeded_local_review(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn('"provenance": "local_user_review"', FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertIn("No individual review card is seeded", payload["snapshot_policy"]["seed_policy"])

    def test_book_zero_review_evidence_has_no_rating_or_review_content(self) -> None:
        evidence = get_review_evidence("168281808X")
        self.assertEqual(
            evidence["rating_summary"]["zero_rating_copy"],
            "There are 0 customer reviews",
        )
        self.assertIsNone(evidence["rating_summary"]["rating"])
        self.assertEqual(evidence["rating_summary"]["rating_count"], 0)
        self.assertIsNone(evidence["histogram"])
        self.assertEqual(evidence["source_topics"], [])
        self.assertEqual(evidence["source_excerpts"], [])

    def test_beauty_evidence_is_aggregate_only(self) -> None:
        evidence = get_review_evidence("B074PVTPBW")
        self.assertEqual(
            (evidence["rating_summary"]["rating"], evidence["rating_summary"]["rating_count"]),
            (4.6, 184921),
        )
        self.assertIsNone(evidence["histogram"])
        self.assertEqual(evidence["source_topics"], [])
        self.assertEqual(evidence["source_excerpts"], [])
        self.assertFalse(evidence["individual_review_evidence"]["available"])

    def test_latest_rich_pdp_reviews_are_aggregate_only_without_histograms(self) -> None:
        for asin in ("B00FLYWNYQ", "B07K74LDCH", "B088BZTYFP"):
            with self.subTest(asin=asin):
                evidence = get_review_evidence(asin)
                self.assertIsNotNone(evidence)
                assert evidence is not None
                self.assertIsNone(evidence["histogram"])
                self.assertEqual(evidence["source_topics"], [])
                self.assertEqual(evidence["source_excerpts"], [])
                self.assertFalse(evidence["individual_review_evidence"]["available"])
                self.assertIn(
                    "histogram",
                    evidence["individual_review_evidence"]["missing_fields"],
                )


class LocalReviewViewTests(unittest.TestCase):
    def test_local_summary_does_not_rewrite_source_aggregate(self) -> None:
        view = build_review_view(
            "B0874XN4D8",
            [local_review(), local_review(id="local-2", rating=1, verified_purchase=False)],
        )
        self.assertEqual(view["source_rating_summary"]["rating"], 4.7)
        self.assertEqual(view["source_rating_summary"]["rating_count"], 38085)
        self.assertEqual(view["local_summary"]["rating"], 3.0)
        self.assertEqual(view["local_summary"]["review_count"], 2)

    def test_star_filter_applies_only_to_local_rows(self) -> None:
        view = build_review_view(
            "B0874XN4D8",
            [local_review(), local_review(id="local-2", rating=1)],
            star=1,
        )
        self.assertEqual([row["rating"] for row in view["local_reviews"]], [1])
        self.assertEqual(view["source_histogram"]["percent_by_star"]["1"], 3)

    def test_local_review_validation_rejects_untrusted_provenance(self) -> None:
        with self.assertRaisesRegex(ValueError, "provenance"):
            normalize_local_reviews([local_review(provenance="source_snapshot_aggregate")])
        with self.assertRaisesRegex(ValueError, "rating"):
            normalize_local_reviews([local_review(rating=0)])
        with self.assertRaisesRegex(ValueError, "helpful_count"):
            normalize_local_reviews([local_review(helpful_count=-1)])
        with self.assertRaisesRegex(ValueError, "star filter"):
            build_review_view("B0874XN4D8", [local_review()], star=0)

    def test_fragment_escapes_user_content_and_labels_provenance(self) -> None:
        html = render_reviews_section(
            "B0874XN4D8",
            [
                local_review(
                    author_display_name="<script>alert(1)</script>",
                    title="<b>title</b>",
                    body="<img src=x onerror=alert(1)>",
                )
            ],
        )
        self.assertIn('data-provenance="source_snapshot_aggregate"', html)
        self.assertIn('data-provenance="local_user_review"', html)
        self.assertIn("Verified Purchase", html)
        self.assertIn("2 people found this helpful", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;b&gt;title&lt;/b&gt;", html)

    def test_histogram_uses_csp_safe_native_progress_without_inline_style(self) -> None:
        html = render_reviews_section("B0874XN4D8")
        self.assertEqual(html.count('class="review-histogram-track"'), 5)
        self.assertIn('<progress class="review-histogram-track"', html)
        self.assertNotIn("style=", html)

    def test_unknown_asin_has_no_invented_review_section(self) -> None:
        self.assertIsNone(build_review_view("B000000000"))
        self.assertEqual(render_reviews_section("B000000000"), "")

    def test_known_product_without_aggregate_has_natural_empty_source_state(self) -> None:
        asin = "B07CRG94G3"
        label = "Seagate Portable 2TB External Hard Drive"
        view = build_review_view(asin, product_label=label)
        self.assertIsNotNone(view)
        assert view is not None
        self.assertFalse(view["source_aggregate_available"])
        self.assertIsNone(view["source_rating_summary"])
        self.assertEqual(view["source_histogram"], None)
        self.assertEqual(view["source_topics"], [])
        self.assertEqual(view["source_excerpts"], [])
        self.assertEqual(view["local_summary"]["review_count"], 0)

        html = render_reviews_section(asin, product_label=label)
        self.assertIn('data-source-aggregate="unavailable"', html)
        self.assertIn('data-provenance="source_aggregate_unavailable"', html)
        self.assertIn("Review summary unavailable", html)
        self.assertIn("No source rating or review-count aggregate", html)
        self.assertNotIn('data-provenance="source_snapshot_aggregate"', html)
        self.assertNotIn("★★★★★", html)
        self.assertNotIn("out of 5", html)

    def test_local_rows_remain_separate_when_source_aggregate_is_unavailable(self) -> None:
        html = render_reviews_section(
            "B07CRG94G3",
            [local_review(rating=4, verified_purchase=False)],
            product_label="Seagate Portable 2TB External Hard Drive",
        )
        self.assertIn('data-provenance="source_aggregate_unavailable"', html)
        self.assertIn('data-provenance="local_user_review"', html)
        self.assertIn("QA Shopper", html)
        self.assertIn("★★★★☆", html)
        self.assertNotIn('data-provenance="source_snapshot_aggregate"', html)

    def test_book_zero_review_summary_never_renders_none_or_fake_stars(self) -> None:
        html = render_reviews_section("168281808X")
        self.assertIn("There are 0 customer reviews", html)
        self.assertIn('data-source-rating="none"', html)
        self.assertNotIn("None out of 5", html)
        self.assertNotIn("null out of 5", html)
        self.assertNotIn("★★★★★", html)

    def test_local_rows_do_not_rewrite_new_product_source_aggregates(self) -> None:
        for asin, expected in (
            ("168281808X", (None, 0)),
            ("B074PVTPBW", (4.6, 184921)),
        ):
            with self.subTest(asin=asin):
                view = build_review_view(asin, [local_review(rating=5)])
                source = view["source_rating_summary"]
                self.assertEqual((source["rating"], source["rating_count"]), expected)
                self.assertEqual(view["local_summary"]["rating"], 5.0)
                self.assertEqual(view["local_summary"]["review_count"], 1)


if __name__ == "__main__":
    unittest.main()
