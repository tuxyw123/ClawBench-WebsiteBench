from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from browse_breadth import (  # noqa: E402
    DEAL_MEMBERSHIP_FIELDS,
    DEPARTMENT_SLUGS_FIELD,
    EXPECTED_HOME_RAILS,
    HOME_BROWSE_FIELDS,
    INSTANT_POT_ASIN,
    OFFER_DISPLAY_FIELDS,
    build_department_commerce_supplements,
    build_home_rail_sections,
    build_portable_ssd_supplement,
    combine_verified_offer_display,
    load_browse_breadth,
)
from home_catalog import load_home_product_catalog  # noqa: E402


FIXTURE_ROOT = ROOT / "fixtures"
FORBIDDEN_SPARSE_FIELDS = {
    "price_minor",
    "list_price_minor",
    "currency",
    "rating",
    "reviews",
    "availability",
    "pdp",
}


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class BrowseBreadthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_home_product_catalog(FIXTURE_ROOT)
        cls.frozen = fixture("task-frozen-900136-v1.json")
        cls.direct = fixture("home-pdp-evidence.json")

    def test_home_sections_preserve_all_seven_source_rails_and_157_unique_asins(self) -> None:
        sections = build_home_rail_sections(self.catalog)
        self.assertEqual(
            [(section["key"], section["title"], section["count"]) for section in sections],
            list(EXPECTED_HOME_RAILS),
        )
        products = [product for section in sections for product in section["products"]]
        asins = [product["asin"] for product in products]
        self.assertEqual(len(products), 157)
        self.assertEqual(len(set(asins)), 157)
        self.assertTrue(all(set(product) <= HOME_BROWSE_FIELDS for product in products))
        self.assertTrue(all(not (set(product) & FORBIDDEN_SPARSE_FIELDS) for product in products))

    def test_portable_ssd_supplement_adds_27_sparse_home_matches_without_frozen_overlap(self) -> None:
        frozen_products = self.frozen["products"]
        self.assertIsInstance(frozen_products, list)
        supplement = build_portable_ssd_supplement(self.catalog, frozen_products)
        frozen_asins = {product["asin"] for product in frozen_products}
        supplement_asins = [product["asin"] for product in supplement]
        self.assertEqual(len(supplement), 27)
        self.assertEqual(len(set(supplement_asins)), 27)
        self.assertFalse(frozen_asins.intersection(supplement_asins))
        self.assertTrue(
            all(product.get("evidence_tier") == "home-card-only" for product in supplement)
        )
        self.assertTrue(all(set(product) <= HOME_BROWSE_FIELDS for product in supplement))
        self.assertTrue(
            all(not (set(product) & FORBIDDEN_SPARSE_FIELDS) for product in supplement)
        )
        self.assertTrue(
            all(str(product["image_path"]).startswith("/static/") for product in supplement)
        )

    def test_verified_offer_union_has_19_exact_source_prices_and_direct_override(self) -> None:
        frozen_products = self.frozen["products"]
        direct_products = self.direct["products"]
        self.assertIsInstance(frozen_products, list)
        self.assertIsInstance(direct_products, list)
        offers = combine_verified_offer_display(frozen_products, direct_products)
        self.assertEqual(len(offers), 19)
        self.assertEqual(len({offer["asin"] for offer in offers}), 19)
        self.assertTrue(all(set(offer) <= OFFER_DISPLAY_FIELDS for offer in offers))
        self.assertTrue(all(offer["currency"] == "USD" for offer in offers))

        expected = {product["asin"]: product for product in frozen_products}
        expected.update({product["asin"]: product for product in direct_products})
        for offer in offers:
            source = expected[offer["asin"]]
            self.assertEqual(offer["price_minor"], source["price_minor"])
            self.assertEqual(offer.get("list_price_minor"), source.get("list_price_minor"))
            self.assertEqual(offer["title"], source["title"])
            self.assertEqual(offer["image_path"], source["image_path"])

        direct_override = next(offer for offer in offers if offer["asin"] == "B08HN37XC1")
        self.assertEqual(direct_override["price_minor"], 31_699)
        self.assertEqual(direct_override["source"], "direct-pdp")
        book = next(offer for offer in offers if offer["asin"] == "168281808X")
        self.assertEqual(book["price_minor"], 1_749)
        self.assertEqual(book["list_price_minor"], 2_499)
        self.assertEqual(book["rating"], "")
        self.assertEqual(book["reviews"], 0)
        new_direct_prices = {
            "B00FLYWNYQ": 10_396,
            "B07K74LDCH": 5_763,
            "B088BZTYFP": 2_785,
        }
        self.assertEqual(
            {
                offer["asin"]: offer["price_minor"]
                for offer in offers
                if offer["asin"] in new_direct_prices
            },
            new_direct_prices,
        )

    def test_department_commerce_supplement_is_exact_taxonomy_backed_and_deal_free(self) -> None:
        frozen_products = self.frozen["products"]
        direct_products = self.direct["products"]
        self.assertIsInstance(frozen_products, list)
        self.assertIsInstance(direct_products, list)
        offers = combine_verified_offer_display(frozen_products, direct_products)
        supplements = build_department_commerce_supplements(
            self.catalog,
            self.frozen,
            self.direct,
            offers,
        )
        expected_asins = (
            INSTANT_POT_ASIN,
            *(str(product["asin"]) for product in frozen_products),
        )
        self.assertEqual(tuple(product["asin"] for product in supplements), expected_asins)
        self.assertEqual(len(set(expected_asins)), 10)
        self.assertEqual(
            [product[DEPARTMENT_SLUGS_FIELD] for product in supplements],
            [("home-kitchen",), *(("computers",) for _ in frozen_products)],
        )

        offer_by_asin = {str(offer["asin"]): offer for offer in offers}
        for product in supplements:
            with self.subTest(asin=product["asin"]):
                offer = offer_by_asin[str(product["asin"])]
                self.assertEqual(product["price_minor"], offer["price_minor"])
                self.assertEqual(product["currency"], offer["currency"])
                self.assertEqual(product["image_path"], offer["image_path"])
                self.assertTrue(str(product["image_path"]).startswith("/static/"))
                self.assertFalse(set(product).intersection(DEAL_MEMBERSHIP_FIELDS))

        instant = supplements[0]
        self.assertEqual(instant.get("evidence_tier"), "pdp-direct")
        self.assertEqual(instant["pdp"]["page_category"], "Home & Kitchen")
        sparse_home_duplicates = {"B0874XN4D8", "B0C5JQ68FY", "B0CHFSWM2P"}
        self.assertTrue(
            all(
                product.get("evidence_tier") != "home-card-only"
                for product in supplements
                if product["asin"] in sparse_home_duplicates
            )
        )

    def test_loader_exposes_the_five_integration_views(self) -> None:
        breadth = load_browse_breadth(FIXTURE_ROOT)
        self.assertEqual(set(breadth), {
            "rail_sections",
            "portable_ssd_supplement",
            "verified_offers",
            "department_commerce_supplements",
            "search_commerce_cards",
        })
        self.assertEqual(len(breadth["rail_sections"]), 7)
        self.assertEqual(len(breadth["portable_ssd_supplement"]), 27)
        self.assertEqual(len(breadth["verified_offers"]), 19)
        self.assertEqual(len(breadth["department_commerce_supplements"]), 10)
        self.assertEqual(len(breadth["search_commerce_cards"]), 20)


if __name__ == "__main__":
    unittest.main()
