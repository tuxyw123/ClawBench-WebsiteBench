from __future__ import annotations

import copy
import json
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_commerce import (  # noqa: E402
    EXPECTED_DEPARTMENT_COUNTS,
    SEARCH_COMMERCE_EVIDENCE_CLASS,
    SEARCH_COMMERCE_FIXTURE,
    SearchCommerceError,
    load_search_commerce_cards,
    normalize_search_commerce_cards,
)


FIXTURE_ROOT = ROOT / "fixtures"
EXPECTED_ASINS = (
    "B0GRFWYP37",
    "B0DPGNGRCC",
    "B0CGL336B7",
    "1481215663",
    "0345483448",
    "1524797677",
    "B091NJQ29P",
    "B014E2D6BY",
    "B0B2RM68G2",
    "B093TSRPQM",
    "B09MFMCTRK",
    "B0B5HN65QQ",
    "B0725WFLMB",
    "B072F54342",
    "B081RJ8DW1",
    "B0DX6RVJF7",
    "B0D1CRYR8C",
    "B0D9V8R5X9",
    "B0DBVH4ZBT",
    "B0C49PMJMW",
)


class SearchCommerceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(
            (FIXTURE_ROOT / SEARCH_COMMERCE_FIXTURE).read_text(encoding="utf-8")
        )
        cls.cards = load_search_commerce_cards(FIXTURE_ROOT)

    def test_loader_preserves_all_twenty_cards_and_department_source_order(self) -> None:
        self.assertEqual(tuple(card["asin"] for card in self.cards), EXPECTED_ASINS)
        self.assertEqual(len({card["asin"] for card in self.cards}), 20)
        self.assertEqual(
            dict(Counter(card["department_slugs"][0] for card in self.cards)),
            EXPECTED_DEPARTMENT_COUNTS,
        )
        self.assertTrue(
            all(card["captured_queries"] == ("best sellers",) for card in self.cards)
        )

    def test_projection_keeps_exact_offer_facts_without_pdp_or_deals_semantics(self) -> None:
        raw_by_asin = {product["asin"]: product for product in self.payload["products"]}
        forbidden = {
            "availability",
            "deal_label",
            "discount_percent",
            "limited_time_deal",
            "list_price_minor",
            "options",
            "pdp",
            "seller",
            "themes",
        }
        for card in self.cards:
            raw = raw_by_asin[card["asin"]]
            with self.subTest(asin=card["asin"]):
                self.assertEqual(card["evidence_class"], SEARCH_COMMERCE_EVIDENCE_CLASS)
                self.assertEqual(card["title"], raw["title"])
                self.assertEqual(card["price_minor"], raw["price_minor"])
                self.assertEqual(card["currency"], "USD")
                self.assertEqual(card["image_path"], raw["image_path"])
                self.assertEqual(card["rating"], raw["ratingDisplay"])
                self.assertEqual(card["reviews_display"], raw["reviewsDisplay"])
                self.assertEqual(card["reviews_exact"], raw["reviewsExact"])
                self.assertEqual(card["sponsored"], raw["sponsored"])
                self.assertEqual(card["format"], raw.get("format"))
                self.assertFalse(set(card).intersection(forbidden - {"list_price_minor"}))
                self.assertIsNone(card["list_price_minor"])

    def test_abbreviated_review_copy_never_becomes_an_exact_count(self) -> None:
        abbreviated = [card for card in self.cards if card["reviews_display"].endswith("K")]
        exact = [card for card in self.cards if not card["reviews_display"].endswith("K")]
        self.assertEqual(len(abbreviated), 15)
        self.assertTrue(
            all(card["reviews_exact"] is None and card["reviews"] == 0 for card in abbreviated)
        )
        self.assertTrue(
            all(card["reviews_exact"] == card["reviews"] > 0 for card in exact)
        )

    def test_every_declared_asset_is_present_and_no_untracked_jpeg_is_used(self) -> None:
        asset_dir = (
            ROOT / "static/assets/source-current/2026-07-22/search-commerce"
        )
        self.assertEqual(
            {path.stem for path in asset_dir.glob("*.jpg")},
            set(EXPECTED_ASINS),
        )
        self.assertTrue(all((ROOT / card["image_path"].removeprefix("/")).is_file() for card in self.cards))

    def test_strict_boundary_rejects_k_to_exact_deals_fields_and_asset_drift(self) -> None:
        mutations = []
        k_to_exact = copy.deepcopy(self.payload)
        next(
            product
            for product in k_to_exact["products"]
            if product["reviewsDisplay"].endswith("K")
        )["reviewsExact"] = 19700
        mutations.append(k_to_exact)

        deals_field = copy.deepcopy(self.payload)
        deals_field["products"][0]["deal_label"] = "Limited time deal"
        mutations.append(deals_field)

        asset_drift = copy.deepcopy(self.payload)
        asset_drift["products"][0]["asset"]["sha256"] = "0" * 64
        mutations.append(asset_drift)

        for payload in mutations:
            with self.subTest(first_asin=payload["products"][0]["asin"]):
                with self.assertRaises(SearchCommerceError):
                    normalize_search_commerce_cards(payload, FIXTURE_ROOT)


if __name__ == "__main__":
    unittest.main()
