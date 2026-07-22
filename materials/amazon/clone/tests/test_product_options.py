from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_options import (  # noqa: E402
    AIR_FILTER_ASIN,
    AILUN_ASIN,
    AVAILABLE_STATUS,
    BEAUTY_ASIN,
    BOOK_ASIN,
    INSTANT_POT_ASIN,
    JANSPORT_ASIN,
    OKAPI_ASIN,
    SANDISK_ASIN,
    SHEETS_ASIN,
    T7_BLUE_IMAGE_PATH,
    T7_BLUE_PRICE_MINOR,
    T9_ASIN,
    TARGET_ASIN,
    UPSIMPLES_ASIN,
    VAULT_X_ASIN,
    canonical_selection_key,
    default_selection,
    load_source_option_specs,
    load_source_transaction_quote_specs,
    normalize_complete_selection,
    resolve_transaction_quote,
)
from search_commerce import load_search_commerce_cards  # noqa: E402


FIXTURE_ROOT = ROOT / "fixtures"
DEALS_CARD_ASINS = (
    "B01LYNW421",
    "B095CN96JS",
    "B0BVZFQ4DF",
    "B06X9M6CW7",
    "B0C1GP88C4",
    "B0BV241H3F",
    "B0DQDQVTT3",
    "B09BWFX1L6",
    "B00EINBSEW",
    "B0FBSQX5T3",
)


def base_offer(
    asin: str,
    *,
    price_minor: int,
    image_path: str = "/static/server-owned-base-image.jpg",
) -> dict[str, object]:
    return {
        "asin": asin,
        "price_minor": price_minor,
        "currency": "USD",
        "image_path": image_path,
    }


class ProductTransactionQuoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.option_specs = load_source_option_specs(FIXTURE_ROOT)
        cls.quote_specs = load_source_transaction_quote_specs(FIXTURE_ROOT)
        fixture = json.loads(
            (FIXTURE_ROOT / "deals-current-2026-07-22.json").read_text(
                encoding="utf-8"
            )
        )
        cls.deals_products = tuple(fixture["products"])
        cls.search_card_products = load_search_commerce_cards(FIXTURE_ROOT)

    def quote(
        self,
        asin: str,
        selection: dict[str, object] | None,
        *,
        price_minor: int = 999,
        image_path: str = "/static/server-owned-base-image.jpg",
    ) -> dict[str, object] | None:
        return resolve_transaction_quote(
            asin,
            selection,
            option_specs=self.option_specs,
            quote_specs=self.quote_specs,
            base_offer=base_offer(
                asin, price_minor=price_minor, image_path=image_path
            ),
        )

    def test_complete_selection_normalizes_defaults_and_rejects_partial_client_maps(self) -> None:
        t7_spec = self.option_specs[TARGET_ASIN]
        self.assertEqual(
            normalize_complete_selection(t7_spec, None),
            {"Color": "Titan Gray", "Memory Storage Capacity": "1 TB"},
        )
        self.assertEqual(
            normalize_complete_selection(
                t7_spec,
                {"Memory Storage Capacity": "1 TB", "Color": "Blue"},
            ),
            {"Color": "Blue", "Memory Storage Capacity": "1 TB"},
        )
        for malformed in (
            {},
            {"Color": "Blue"},
            {
                "Color": "Blue",
                "Memory Storage Capacity": "1 TB",
                "price_minor": 1,
            },
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    normalize_complete_selection(t7_spec, malformed)

        self.assertEqual(normalize_complete_selection(None, None), {})
        self.assertEqual(normalize_complete_selection(None, {}), {})
        with self.assertRaises(ValueError):
            normalize_complete_selection(None, {"Color": "Brown"})

    def test_quote_catalog_contains_only_evidence_backed_complete_combinations(self) -> None:
        # Nine frozen plus eleven direct PDP offers overlap on SanDisk; the ten
        # Independent current Deals and search cards extend that 19-offer union
        # by ten and twenty default-only offers respectively.
        self.assertEqual(len(self.quote_specs), 49)
        self.assertEqual(len(self.quote_specs[TARGET_ASIN]), 2)
        self.assertEqual(len(self.quote_specs[T9_ASIN]), 1)
        self.assertEqual(len(self.quote_specs[SHEETS_ASIN]), 1)
        self.assertEqual(len(self.quote_specs[SANDISK_ASIN]), 3)
        self.assertEqual(len(self.quote_specs[OKAPI_ASIN]), 1)
        self.assertEqual(len(self.quote_specs[BOOK_ASIN]), 1)
        self.assertEqual(len(self.quote_specs[BEAUTY_ASIN]), 2)
        self.assertEqual(len(self.quote_specs[AILUN_ASIN]), 11)
        self.assertEqual(len(self.quote_specs[VAULT_X_ASIN]), 5)
        self.assertEqual(len(self.quote_specs[UPSIMPLES_ASIN]), 19)
        self.assertEqual(len(self.quote_specs[INSTANT_POT_ASIN]), 2)
        self.assertEqual(len(self.quote_specs[JANSPORT_ASIN]), 13)
        self.assertEqual(len(self.quote_specs[AIR_FILTER_ASIN]), 2)

    def test_current_deals_cards_have_only_one_empty_selection_base_quote(self) -> None:
        self.assertEqual(
            tuple(product["asin"] for product in self.deals_products),
            DEALS_CARD_ASINS,
        )
        for product in self.deals_products:
            asin = str(product["asin"])
            with self.subTest(asin=asin):
                self.assertNotIn(asin, self.option_specs)
                self.assertEqual(
                    self.quote_specs[asin],
                    (
                        {
                            "selected_options": {},
                            "price_minor": None,
                            "currency": "USD",
                            "image_path": None,
                            "availability": AVAILABLE_STATUS,
                            "display_availability": "Available from current source-backed offer",
                            "target_kind": "base-offer",
                            "variant_asin": asin,
                            "evidence_key": (
                                "deals-current-2026-07-22.json:"
                                f"{asin}:base-offer"
                            ),
                        },
                    ),
                )
                quote = self.quote(
                    asin,
                    None,
                    price_minor=int(product["price_minor"]),
                    image_path=str(product["image_path"]),
                )
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["selected_options"], {})
                self.assertEqual(quote["price_minor"], product["price_minor"])
                self.assertEqual(quote["image_path"], product["image_path"])
                self.assertEqual(
                    quote["evidence_key"],
                    f"deals-current-2026-07-22.json:{asin}:base-offer",
                )
                with self.assertRaises(ValueError):
                    self.quote(
                        asin,
                        {"Size": "invented"},
                        price_minor=int(product["price_minor"]),
                        image_path=str(product["image_path"]),
                    )

    def test_current_search_cards_have_only_one_empty_selection_base_quote(self) -> None:
        self.assertEqual(len(self.search_card_products), 20)
        for product in self.search_card_products:
            asin = str(product["asin"])
            with self.subTest(asin=asin):
                self.assertNotIn(asin, self.option_specs)
                self.assertEqual(len(self.quote_specs[asin]), 1)
                rule = self.quote_specs[asin][0]
                self.assertEqual(rule["selected_options"], {})
                self.assertIsNone(rule["price_minor"])
                self.assertIsNone(rule["image_path"])
                self.assertEqual(rule["currency"], "USD")
                self.assertEqual(
                    rule["display_availability"],
                    "Available from captured search-card offer",
                )
                self.assertEqual(rule["evidence_key"], product["card_evidence_key"])
                quote = self.quote(
                    asin,
                    None,
                    price_minor=int(product["price_minor"]),
                    image_path=str(product["image_path"]),
                )
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["selected_options"], {})
                self.assertEqual(quote["price_minor"], product["price_minor"])
                self.assertEqual(quote["image_path"], product["image_path"])
                with self.assertRaises(ValueError):
                    self.quote(
                        asin,
                        {"Format": "invented"},
                        price_minor=int(product["price_minor"]),
                        image_path=str(product["image_path"]),
                    )

    def test_new_rich_pdps_quote_only_observed_complete_selections(self) -> None:
        for size, expected_price in (("3 Quarts", 8999), ("6 Quarts", 10396)):
            with self.subTest(product="instant-pot", size=size):
                quote = self.quote(
                    INSTANT_POT_ASIN,
                    {"Size": size},
                    price_minor=1,
                )
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["price_minor"], expected_price)
                self.assertEqual(quote["selected_options"], {"Size": size})

        jansport_prices = {
            "Black": 5763,
            "Bad Bows": 6200,
            "Blue Dusk": 4999,
            "Camo Illusion": 5024,
            "Digital Fuchsia": 6200,
            "Faded Sage": 6200,
            "Flutter By Purple": 6200,
            "Grounded Grey": 4023,
            "Lavender Ash": 5499,
            "Navy": 6030,
            "Pastel Lilac": 6199,
            "Pink Ice": 6199,
            "Surreal Spots": 6200,
        }
        for color, expected_price in jansport_prices.items():
            with self.subTest(product="jansport", color=color):
                quote = self.quote(
                    JANSPORT_ASIN,
                    {"Color": color, "Size": "One Size"},
                    price_minor=1,
                )
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["price_minor"], expected_price)

        for style in ("Merv 8", "Merv 5"):
            with self.subTest(product="air-filter", style=style):
                quote = self.quote(
                    AIR_FILTER_ASIN,
                    {"Pattern Name": "16x20x1", "Style": style},
                    price_minor=1,
                )
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["price_minor"], 2785)

        self.assertIsNone(
            self.quote(
                AIR_FILTER_ASIN,
                {"Pattern Name": "16x20x1", "Style": "Merv 11"},
            )
        )
        self.assertIsNone(
            self.quote(
                AIR_FILTER_ASIN,
                {"Pattern Name": "12x12x1", "Style": "Merv 8"},
            )
        )

    def test_declarative_direct_pdp_quotes_cover_models_colors_and_only_captured_sizes(self) -> None:
        ailun = self.quote(
            AILUN_ASIN,
            {"Model": "iPad Pro 12.9 2022/2021/2020/2018"},
            price_minor=1,
        )
        self.assertIsNotNone(ailun)
        assert ailun is not None
        self.assertEqual(ailun["price_minor"], 989)

        vault = self.quote(VAULT_X_ASIN, {"Color": "Purple"}, price_minor=1)
        self.assertIsNotNone(vault)
        assert vault is not None
        self.assertEqual(vault["price_minor"], 3199)

        frame = self.quote(
            UPSIMPLES_ASIN,
            {"Color": "White", "Size": "11x14"},
            price_minor=1,
        )
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame["price_minor"], 809)
        self.assertIsNone(
            self.quote(
                UPSIMPLES_ASIN,
                {"Color": "White", "Size": "16x20"},
                price_minor=1,
            )
        )

    def test_t7_default_uses_server_base_offer_and_blue_uses_captured_quote(self) -> None:
        default_image = "/static/server-owned-t7.jpg"
        default_quote = self.quote(
            TARGET_ASIN,
            None,
            price_minor=21_999,
            image_path=default_image,
        )
        self.assertIsNotNone(default_quote)
        assert default_quote is not None
        self.assertEqual(default_quote["price_minor"], 21_999)
        self.assertEqual(default_quote["image_path"], default_image)
        self.assertEqual(default_quote["availability"], AVAILABLE_STATUS)
        self.assertEqual(
            default_quote["selected_options"], default_selection(self.option_specs[TARGET_ASIN])
        )
        self.assertEqual(default_quote["transaction_target"]["kind"], "base-offer")

        blue_selection = {"Color": "Blue", "Memory Storage Capacity": "1 TB"}
        blue_quote = self.quote(TARGET_ASIN, blue_selection, price_minor=1)
        self.assertIsNotNone(blue_quote)
        assert blue_quote is not None
        self.assertEqual(blue_quote["price_minor"], T7_BLUE_PRICE_MINOR)
        self.assertEqual(blue_quote["image_path"], T7_BLUE_IMAGE_PATH)
        self.assertEqual(blue_quote["selected_options"], blue_selection)
        self.assertEqual(
            blue_quote["transaction_target"],
            {
                "kind": "captured-selection",
                "offer_asin": TARGET_ASIN,
                "variant_asin": None,
                "selection_key": canonical_selection_key(blue_selection),
            },
        )
        self.assertTrue((ROOT / T7_BLUE_IMAGE_PATH.lstrip("/")).is_file())

    def test_t7_non_priced_capacities_do_not_produce_quotes(self) -> None:
        for selection in (
            {"Color": "Titan Gray", "Memory Storage Capacity": "2 TB"},
            {"Color": "Blue", "Memory Storage Capacity": "2 TB"},
            {"Color": "Titan Gray", "Memory Storage Capacity": "4.0 TB"},
        ):
            with self.subTest(selection=selection):
                self.assertIsNone(self.quote(TARGET_ASIN, selection))

    def test_t9_only_current_black_1tb_selection_has_a_transaction_quote(self) -> None:
        current = {"Digital Storage Capacity": "1 TB", "Color": "Black"}
        quote = self.quote(T9_ASIN, current, price_minor=23_999)
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote["price_minor"], 23_999)
        self.assertEqual(quote["selected_options"], current)

        # These values all appear on the captured PDP, but neither complete
        # selection has a retained transaction price.
        self.assertIsNone(
            self.quote(
                T9_ASIN,
                {"Digital Storage Capacity": "4 TB", "Color": "Gray"},
            )
        )
        self.assertIsNone(
            self.quote(
                T9_ASIN,
                {"Digital Storage Capacity": "2 TB", "Color": "Black"},
            )
        )

    def test_sandisk_quotes_only_old_model_2tb_captured_colors(self) -> None:
        expected = {
            "Black": (
                31_699,
                "/static/assets/source-current/2026-07-21/pdp-home/B08HN37XC1/color-black.jpg",
            ),
            "Monterey": (
                32_999,
                "/static/assets/source-current/2026-07-21/pdp-home/B08HN37XC1/color-monterey.jpg",
            ),
            "Sky Blue": (
                32_999,
                "/static/assets/source-current/2026-07-21/pdp-home/B08HN37XC1/color-sky-blue.jpg",
            ),
        }
        for color, (price_minor, image_path) in expected.items():
            with self.subTest(color=color):
                selection = {
                    "Style": "Old Model",
                    "Capacity": "2TB",
                    "Color": color,
                }
                quote = self.quote(SANDISK_ASIN, selection, price_minor=1)
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["price_minor"], price_minor)
                self.assertEqual(quote["image_path"], image_path)
                self.assertEqual(quote["selected_options"], selection)
                self.assertTrue((ROOT / image_path.lstrip("/")).is_file())

        self.assertIsNone(
            self.quote(
                SANDISK_ASIN,
                {"Style": "New Model", "Capacity": "4TB", "Color": "Sky Blue"},
            )
        )
        self.assertIsNone(
            self.quote(
                SANDISK_ASIN,
                {"Style": "Old Model", "Capacity": "4TB", "Color": "Black"},
            )
        )

    def test_sheet_default_and_no_option_okapi_use_matching_server_base_offer(self) -> None:
        sheet_quote = self.quote(SHEETS_ASIN, None, price_minor=2_124)
        self.assertIsNotNone(sheet_quote)
        assert sheet_quote is not None
        self.assertEqual(sheet_quote["price_minor"], 2_124)
        self.assertEqual(
            sheet_quote["selected_options"],
            {"Size": "Queen", "Color": "01 - White"},
        )

        okapi_image = "/static/server-owned-okapi.jpg"
        okapi_quote = self.quote(
            OKAPI_ASIN,
            {},
            price_minor=1_299,
            image_path=okapi_image,
        )
        self.assertIsNotNone(okapi_quote)
        assert okapi_quote is not None
        self.assertEqual(okapi_quote["selected_options"], {})
        self.assertEqual(okapi_quote["price_minor"], 1_299)
        self.assertEqual(okapi_quote["image_path"], okapi_image)
        with self.assertRaises(ValueError):
            self.quote(OKAPI_ASIN, {"Color": "Brown"})

    def test_book_only_quotes_the_captured_physical_hardcover_format(self) -> None:
        hardcover = self.quote(BOOK_ASIN, {"Format": "Hardcover"}, price_minor=1_749)
        self.assertIsNotNone(hardcover)
        assert hardcover is not None
        self.assertEqual(hardcover["price_minor"], 1_749)
        self.assertEqual(hardcover["selected_options"], {"Format": "Hardcover"})
        self.assertEqual(hardcover["transaction_target"]["kind"], "base-offer")
        self.assertIsNone(self.quote(BOOK_ASIN, {"Format": "Kindle"}, price_minor=1_199))

    def test_beauty_quotes_only_the_two_captured_size_offers(self) -> None:
        expected = {
            "36 Count (Pack of 1)": 1_299,
            "75 Count": 1_829,
        }
        for size, price_minor in expected.items():
            with self.subTest(size=size):
                quote = self.quote(BEAUTY_ASIN, {"Size": size}, price_minor=1_299)
                self.assertIsNotNone(quote)
                assert quote is not None
                self.assertEqual(quote["price_minor"], price_minor)
                self.assertEqual(quote["selected_options"], {"Size": size})
                expected_image = (
                    "/static/server-owned-base-image.jpg"
                    if size == "36 Count (Pack of 1)"
                    else "/static/assets/source-current/2026-07-21/"
                    "pdp-beauty/B074PVTPBW/main.jpg"
                )
                self.assertEqual(quote["image_path"], expected_image)

    def test_resolver_rejects_mismatched_or_client_shaped_base_offers(self) -> None:
        selection = {"Color": "Blue", "Memory Storage Capacity": "1 TB"}
        with self.assertRaises(ValueError):
            resolve_transaction_quote(
                TARGET_ASIN,
                selection,
                option_specs=self.option_specs,
                quote_specs=self.quote_specs,
                base_offer=base_offer(T9_ASIN, price_minor=23_999),
            )
        with self.assertRaises(ValueError):
            resolve_transaction_quote(
                TARGET_ASIN,
                selection,
                option_specs=self.option_specs,
                quote_specs=self.quote_specs,
                base_offer={
                    "asin": TARGET_ASIN,
                    "price_minor": "$0.01",
                    "currency": "USD",
                    "image_path": "/static/not-used.jpg",
                },
            )


if __name__ == "__main__":
    unittest.main()
