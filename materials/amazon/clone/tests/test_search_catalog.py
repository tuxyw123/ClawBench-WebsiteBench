from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from browse_breadth import load_browse_breadth  # noqa: E402
from home_catalog import load_home_product_catalog  # noqa: E402
from search_catalog import (  # noqa: E402
    SOURCE_DEPARTMENT_BY_SLUG,
    SearchRequest,
    SearchValidationError,
    build_search_hit,
    build_search_page,
    parse_search_request,
    refine_search_hits,
    search_home_catalog,
    search_home_hits,
    search_href,
)


FIXTURE_ROOT = ROOT / "fixtures"


def product(
    asin: str,
    *,
    title: str = "Example product",
    department: str = "computers",
    brand: str | None = None,
    price_minor: int | None = None,
    currency: str = "USD",
    rating: str | None = None,
    reviews: int | None = None,
    availability: str | None = None,
) -> dict[str, object]:
    department_record = SOURCE_DEPARTMENT_BY_SLUG[department]
    value: dict[str, object] = {
        "asin": asin,
        "title": title,
        "placements": [
            {
                "railKey": department_record["rail_key"],
                "railTitle": department_record["rail_title"],
                "ordinal": 0,
            }
        ],
    }
    if brand is not None:
        value["brand"] = brand
    if price_minor is not None:
        value["price_minor"] = price_minor
        value["currency"] = currency
    if rating is not None:
        value["rating"] = rating
    if reviews is not None:
        value["reviews"] = reviews
    if availability is not None:
        value["pdp"] = {"availability": availability}
    return value


def hit(
    asin: str,
    *,
    source_index: int,
    relevance: int,
    department: str = "computers",
    brand: str | None = None,
    price_minor: int | None = None,
    rating: str | None = None,
    reviews: int | None = None,
    availability: str | None = None,
):
    return build_search_hit(
        product(
            asin,
            department=department,
            brand=brand,
            price_minor=price_minor,
            rating=rating,
            reviews=reviews,
            availability=availability,
        ),
        relevance=relevance,
        source_index=source_index,
    )


class SearchRequestParsingTests(unittest.TestCase):
    def test_parses_every_supported_parameter_and_canonicalizes_href(self) -> None:
        request = parse_search_request(
            "field-keywords=%20Portable%20%20SSD%20"
            "&i=computers"
            "&brand=sandisk&brand=Samsung&brand=SAMSUNG"
            "&minPrice=89.90&maxPrice=300"
            "&rating=4-up&availability=in-stock"
            "&sort=price-asc&page=2"
        )
        self.assertEqual(request.query, "Portable SSD")
        self.assertEqual(request.department, "computers")
        self.assertEqual(request.brands, ("Samsung", "sandisk"))
        self.assertEqual(request.min_price_minor, 8_990)
        self.assertEqual(request.max_price_minor, 30_000)
        self.assertEqual(request.rating, "4-up")
        self.assertEqual(request.availability, "in-stock")
        self.assertEqual(request.sort, "price-asc")
        self.assertEqual(request.page, 2)

        href = search_href(request)
        self.assertEqual(
            href,
            "/s?k=Portable+SSD&i=computers"
            "&brand=Samsung&brand=sandisk"
            "&minPrice=89.9&maxPrice=300"
            "&rating=4-up&availability=in-stock"
            "&sort=price-asc&page=2",
        )
        self.assertEqual(parse_search_request(urlsplit(href).query), request)

    def test_defaults_preserve_the_current_empty_search_contract(self) -> None:
        for raw_query in ("", "k=", "field-keywords=&i=aps"):
            with self.subTest(raw_query=raw_query):
                request = parse_search_request(raw_query)
                self.assertEqual(request.query, "portable ssd")
                self.assertEqual(request.department, "aps")

        for raw_query in ("i=books", "k=&i=books"):
            with self.subTest(raw_query=raw_query):
                request = parse_search_request(raw_query)
                self.assertEqual(request.query, "")
                self.assertEqual(request.department, "books")

    def test_clear_overrides_keep_only_query_and_department(self) -> None:
        request = parse_search_request(
            "k=portable+ssd&i=computers&brand=Samsung"
            "&minPrice=100&maxPrice=250&rating=4-up"
            "&availability=in-stock&sort=rating-desc&page=3"
        )
        self.assertEqual(
            search_href(
                request,
                brands=(),
                min_price_minor=None,
                max_price_minor=None,
                rating=None,
                availability=None,
                sort="relevance",
                page=1,
            ),
            "/s?k=portable+ssd&i=computers",
        )

    def test_rejects_ambiguous_malformed_and_out_of_range_queries(self) -> None:
        invalid_queries = (
            "k=one&k=two",
            "k=one&field-keywords=two",
            "unknown=value",
            "i=not-a-department",
            "brand=",
            "minPrice=",
            "maxPrice=-1",
            "minPrice=1.001",
            "minPrice=2&maxPrice=1",
            "minPrice=1000000.01",
            "rating=",
            "rating=5-up",
            "availability=available",
            "sort=featured",
            "page=0",
            "page=01",
            "page=1001",
            "k=%ZZ",
            "k=%FF",
            "k",
            "k=line%0Abreak",
            "k=" + ("x" * 201),
            "k=x&" + "&".join(f"brand=brand-{index}" for index in range(9)),
        )
        for raw_query in invalid_queries:
            with self.subTest(raw_query=raw_query):
                with self.assertRaises(SearchValidationError):
                    parse_search_request(raw_query)


class SearchFactProjectionTests(unittest.TestCase):
    def test_projects_only_explicit_supported_shopping_facts(self) -> None:
        rich = build_search_hit(
            product(
                "A000000001",
                brand="  Samsung  ",
                price_minor=19_999,
                rating="4.6",
                reviews=12,
                availability="In Stock",
            )
        )
        self.assertEqual(rich.departments, ("computers",))
        self.assertEqual(rich.brand, "Samsung")
        self.assertEqual(rich.price_minor, 19_999)
        self.assertEqual(rich.rating_value, Decimal("4.6"))
        self.assertEqual(rich.availability, "in-stock")

        sparse = build_search_hit(product("A000000002"))
        self.assertIsNone(sparse.brand)
        self.assertIsNone(sparse.price_minor)
        self.assertIsNone(sparse.rating_value)
        self.assertIsNone(sparse.availability)

    def test_does_not_infer_unknown_offer_facts(self) -> None:
        unsupported = product(
            "A000000003",
            brand="Example",
            price_minor=10_00,
            currency="SGD",
            rating="4.9",
            reviews=0,
            availability="Available from current source-backed offer",
        )
        projected = build_search_hit(unsupported)
        self.assertEqual(projected.brand, "Example")
        self.assertIsNone(projected.price_minor)
        self.assertIsNone(projected.rating_value)
        self.assertIsNone(projected.availability)

        low_stock = build_search_hit(
            product(
                "A000000004",
                availability="Only 3 left in stock - order soon.",
            )
        )
        self.assertEqual(low_stock.availability, "in-stock")


class SearchRefinementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hits = [
            hit(
                "A000000001",
                source_index=0,
                relevance=40,
                brand="Samsung",
                price_minor=10_000,
                rating="4.8",
                reviews=10,
                availability="In Stock",
            ),
            hit(
                "A000000002",
                source_index=1,
                relevance=30,
                brand="Samsung",
                price_minor=20_000,
                rating="4.2",
                reviews=10,
                availability="In Stock",
            ),
            hit(
                "A000000003",
                source_index=2,
                relevance=20,
                brand="SanDisk",
                price_minor=15_000,
                rating="4.5",
                reviews=10,
            ),
            hit(
                "A000000004",
                source_index=3,
                relevance=10,
            ),
            hit(
                "A000000005",
                source_index=4,
                relevance=50,
                department="books",
                brand="Samsung",
                price_minor=18_000,
                rating="4.9",
                reviews=10,
                availability="In Stock",
            ),
        ]

    def test_combines_departments_and_filter_dimensions_with_brand_or(self) -> None:
        request = SearchRequest(
            query="example",
            department="computers",
            brands=("samsung", "SanDisk"),
            min_price_minor=12_000,
            max_price_minor=21_000,
            rating="4-up",
            availability="in-stock",
        )
        page = refine_search_hits(request, self.hits)
        self.assertEqual(tuple(item.asin for item in page.items), ("A000000002",))
        self.assertEqual(page.total, 1)

    def test_sparse_unknowns_never_match_fact_filters(self) -> None:
        sparse = [self.hits[3]]
        requests = (
            SearchRequest(query="example", brands=("Samsung",)),
            SearchRequest(query="example", min_price_minor=0),
            SearchRequest(query="example", max_price_minor=100_000),
            SearchRequest(query="example", rating="4-up"),
            SearchRequest(query="example", availability="in-stock"),
        )
        for request in requests:
            with self.subTest(request=request):
                self.assertEqual(refine_search_hits(request, sparse).total, 0)

    def test_shopping_sorts_are_stable_and_put_unknowns_last(self) -> None:
        sortable = [
            hit(
                "A000000001",
                source_index=0,
                relevance=20,
                price_minor=20_000,
                rating="4.5",
                reviews=10,
            ),
            hit(
                "A000000002",
                source_index=1,
                relevance=40,
                price_minor=10_000,
            ),
            hit(
                "A000000003",
                source_index=2,
                relevance=30,
            ),
            hit(
                "A000000004",
                source_index=3,
                relevance=10,
                price_minor=10_000,
                rating="4.9",
                reviews=10,
            ),
        ]
        expected = {
            "relevance": (
                "A000000002",
                "A000000003",
                "A000000001",
                "A000000004",
            ),
            "price-asc": (
                "A000000002",
                "A000000004",
                "A000000001",
                "A000000003",
            ),
            "price-desc": (
                "A000000001",
                "A000000002",
                "A000000004",
                "A000000003",
            ),
            "rating-desc": (
                "A000000004",
                "A000000001",
                "A000000002",
                "A000000003",
            ),
        }
        for sort, expected_asins in expected.items():
            with self.subTest(sort=sort):
                page = refine_search_hits(
                    SearchRequest(query="example", sort=sort),  # type: ignore[arg-type]
                    sortable,
                )
                self.assertEqual(tuple(item.asin for item in page.items), expected_asins)

    def test_default_and_caller_selected_page_sizes_are_supported(self) -> None:
        candidates = [
            build_search_hit(
                product(f"A{index:09d}"),
                relevance=80 - index,
                source_index=index,
            )
            for index in range(80)
        ]
        default_page = refine_search_hits(
            SearchRequest(query="example", page=2), candidates
        )
        self.assertEqual(default_page.page_size, 36)
        self.assertEqual(default_page.page_count, 3)
        self.assertEqual(default_page.total, 80)
        self.assertEqual(len(default_page.items), 36)
        self.assertEqual(default_page.items[0].asin, "A000000036")
        self.assertEqual(default_page.items[-1].asin, "A000000071")

        direct_source_page = refine_search_hits(
            SearchRequest(query="example", page=2), candidates, page_size=16
        )
        self.assertEqual(direct_source_page.page_size, 16)
        self.assertEqual(direct_source_page.page_count, 5)
        self.assertEqual(len(direct_source_page.items), 16)
        self.assertEqual(direct_source_page.items[0].asin, "A000000016")
        self.assertEqual(direct_source_page.items[-1].asin, "A000000031")

        with self.assertRaises(SearchValidationError):
            refine_search_hits(
                SearchRequest(query="example", page=4), candidates
            )

        zero_results = refine_search_hits(
            SearchRequest(query="example"), []
        )
        self.assertEqual(zero_results.total, 0)
        self.assertEqual(zero_results.page, 1)
        self.assertEqual(zero_results.page_count, 1)
        self.assertEqual(zero_results.items, ())


class SearchCatalogCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_home_product_catalog(FIXTURE_ROOT)
        breadth = load_browse_breadth(FIXTURE_ROOT)
        cls.department_supplements = breadth["department_commerce_supplements"]
        cls.search_commerce_cards = breadth["search_commerce_cards"]
        cls.all_department_supplements = (
            *cls.department_supplements,
            *cls.search_commerce_cards,
        )

    def test_hit_api_preserves_legacy_search_catalog_order(self) -> None:
        for query in (
            "portable ssd",
            "books",
            "queen sheets",
            "school supplies",
            "zzyzxxyy qqqvvv",
        ):
            with self.subTest(query=query):
                legacy = search_home_catalog(query, self.catalog)
                hits = search_home_hits(query, self.catalog)
                self.assertEqual(
                    tuple(hit.asin for hit in hits),
                    tuple(str(product["asin"]) for product in legacy),
                )

    def test_empty_keyword_department_request_returns_the_full_source_rail(self) -> None:
        page = build_search_page(parse_search_request("i=books"), self.catalog)
        self.assertEqual(page.total, 26)
        self.assertEqual(len(page.items), 26)
        self.assertEqual(page.items[0].asin, "168281808X")
        self.assertTrue(
            all("books" in item.departments for item in page.items)
        )

    def test_department_and_keyword_are_an_actual_intersection(self) -> None:
        books = build_search_page(
            SearchRequest(query="queen sheets", department="books"),
            self.catalog,
        )
        home = build_search_page(
            SearchRequest(query="queen sheets", department="home-kitchen"),
            self.catalog,
        )
        self.assertEqual(books.total, 0)
        self.assertGreaterEqual(home.total, 1)
        self.assertEqual(home.items[0].asin, "B01M16WBW1")

    def test_real_sparse_department_facts_are_not_filled_for_filters(self) -> None:
        priced = build_search_page(
            SearchRequest(
                query="",
                department="books",
                min_price_minor=0,
            ),
            self.catalog,
        )
        rated = build_search_page(
            SearchRequest(query="", department="books", rating="4-up"),
            self.catalog,
        )
        in_stock = build_search_page(
            SearchRequest(
                query="",
                department="books",
                availability="in-stock",
            ),
            self.catalog,
        )
        self.assertEqual(tuple(item.asin for item in priced.items), ("168281808X",))
        self.assertEqual(rated.total, 0)
        self.assertEqual(in_stock.total, 0)

    def test_verified_department_supplements_append_once_in_stable_source_order(self) -> None:
        supplement_asins = {
            slug: tuple(
                str(product["asin"])
                for product in self.department_supplements
                if slug in product["department_slugs"]
            )
            for slug in ("home-kitchen", "computers")
        }
        expected_totals = {"home-kitchen": 20, "computers": 26}
        for slug, expected_total in expected_totals.items():
            with self.subTest(department=slug):
                query = str(SOURCE_DEPARTMENT_BY_SLUG[slug]["query"])
                base_asins = tuple(
                    str(product["asin"])
                    for product in search_home_catalog(query, self.catalog)
                )
                pages = [
                    build_search_page(
                        SearchRequest(query="", department=slug, page=page),
                        self.catalog,
                        department_supplements=self.department_supplements,
                        page_size=16,
                    )
                    for page in (1, 2)
                ]
                actual_asins = tuple(
                    hit.asin for page in pages for hit in page.items
                )
                self.assertEqual(actual_asins, base_asins + supplement_asins[slug])
                self.assertEqual(len(actual_asins), expected_total)
                self.assertEqual(len(set(actual_asins)), expected_total)
                self.assertEqual([len(page.items) for page in pages], [16, expected_total - 16])
                self.assertTrue(
                    all(slug in hit.departments for page in pages for hit in page.items)
                )

    def test_department_supplements_keep_filter_and_alias_semantics(self) -> None:
        computers = build_search_page(
            SearchRequest(
                query="",
                department="computers",
                min_price_minor=0,
                sort="price-asc",
            ),
            self.catalog,
            department_supplements=self.department_supplements,
            page_size=16,
        )
        home = build_search_page(
            SearchRequest(
                query="",
                department="home-kitchen",
                min_price_minor=0,
            ),
            self.catalog,
            department_supplements=self.department_supplements,
            page_size=16,
        )
        implicit_computers = build_search_page(
            SearchRequest(query="computers"),
            self.catalog,
            department_supplements=self.department_supplements,
            page_size=16,
        )
        instant_query = build_search_page(
            SearchRequest(query="instant pot", department="home-kitchen"),
            self.catalog,
            department_supplements=self.department_supplements,
            page_size=16,
        )
        unrelated = build_search_page(
            SearchRequest(query="queen sheets", department="home-kitchen"),
            self.catalog,
            department_supplements=self.department_supplements,
            page_size=16,
        )

        self.assertEqual(computers.total, 11)
        self.assertEqual(home.total, 4)
        self.assertEqual(implicit_computers.total, 26)
        self.assertEqual(instant_query.total, 1)
        self.assertEqual(instant_query.items[0].asin, "B00FLYWNYQ")
        self.assertNotIn("B00FLYWNYQ", tuple(hit.asin for hit in unrelated.items))
        self.assertEqual(len({hit.asin for hit in computers.items}), computers.total)
        self.assertEqual(
            tuple(hit.price_minor for hit in computers.items),
            tuple(sorted(hit.price_minor for hit in computers.items)),
        )

    def test_without_department_supplements_the_legacy_candidate_contract_is_unchanged(self) -> None:
        request = SearchRequest(query="", department="computers")
        legacy = build_search_page(request, self.catalog)
        self.assertEqual(legacy.total, 17)
        self.assertEqual(
            tuple(hit.asin for hit in legacy.items),
            tuple(
                str(product["asin"])
                for product in search_home_catalog("computers accessories", self.catalog)
            ),
        )

    def test_direct_search_cards_expand_all_five_departments_without_duplicates(self) -> None:
        expected_totals = {
            "books": 32,
            "home-kitchen": 22,
            "toys-games": 27,
            "computers": 31,
            "beauty-personal-care": 21,
        }
        for slug, expected_total in expected_totals.items():
            with self.subTest(department=slug):
                page = build_search_page(
                    SearchRequest(query="", department=slug),
                    self.catalog,
                    department_supplements=self.all_department_supplements,
                    page_size=100,
                )
                asins = tuple(hit.asin for hit in page.items)
                expected_new = tuple(
                    str(product["asin"])
                    for product in self.search_commerce_cards
                    if slug in product["department_slugs"]
                )
                self.assertEqual(page.total, expected_total)
                self.assertEqual(len(set(asins)), expected_total)
                self.assertEqual(
                    tuple(asin for asin in asins if asin in expected_new),
                    expected_new,
                )
                self.assertTrue(
                    all(slug in hit.departments for hit in page.items)
                )

        beauty = build_search_page(
            SearchRequest(query="", department="beauty-personal-care"),
            self.catalog,
            department_supplements=self.all_department_supplements,
            page_size=100,
        )
        biodance = [hit for hit in beauty.items if hit.asin == "B0B2RM68G2"]
        self.assertEqual(len(biodance), 1)
        self.assertEqual(biodance[0].product["evidence_class"], "direct-search-card")
        self.assertEqual(biodance[0].price_minor, 1_900)

    def test_captured_best_sellers_query_prioritizes_twenty_direct_cards(self) -> None:
        page = build_search_page(
            SearchRequest(query="best sellers"),
            self.catalog,
            department_supplements=self.all_department_supplements,
            page_size=100,
        )
        expected = tuple(str(product["asin"]) for product in self.search_commerce_cards)
        self.assertEqual(tuple(hit.asin for hit in page.items[:20]), expected)
        self.assertEqual(len({hit.asin for hit in page.items}), len(page.items))
        self.assertTrue(
            all(
                hit.product.get("evidence_class") == "direct-search-card"
                for hit in page.items[:20]
            )
        )

    def test_direct_search_card_title_rating_and_unknown_inventory_filters_are_honest(self) -> None:
        title = build_search_page(
            SearchRequest(query="Ruffian"),
            self.catalog,
            department_supplements=self.all_department_supplements,
            page_size=16,
        )
        rated = build_search_page(
            SearchRequest(query="", department="books", rating="4-up"),
            self.catalog,
            department_supplements=self.all_department_supplements,
            page_size=100,
        )
        in_stock = build_search_page(
            SearchRequest(query="", department="books", availability="in-stock"),
            self.catalog,
            department_supplements=self.all_department_supplements,
            page_size=100,
        )
        self.assertEqual(tuple(hit.asin for hit in title.items), ("B0GRFWYP37",))
        self.assertTrue(
            {str(product["asin"]) for product in self.search_commerce_cards if "books" in product["department_slugs"]}
            <= {hit.asin for hit in rated.items}
        )
        self.assertFalse(
            {str(product["asin"]) for product in self.search_commerce_cards}
            & {hit.asin for hit in in_stock.items}
        )


if __name__ == "__main__":
    unittest.main()
