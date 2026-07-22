from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from home_catalog import load_home_product_catalog
from search_catalog import search_home_catalog
from search_commerce import load_search_commerce_cards


EXPECTED_HOME_RAILS: tuple[tuple[str, str, int], ...] = (
    ("related-items", "Related to items you've viewed", 25),
    ("best-sellers-home-kitchen", "Best Sellers in Home & Kitchen", 19),
    ("top-sellers-toys", "Top Sellers in Toys for you", 26),
    (
        "best-sellers-computers-accessories",
        "Best Sellers in Computers & Accessories",
        17,
    ),
    ("best-sellers-books", "Best Sellers in Books", 26),
    ("top-picks-singapore", "Top picks for Singapore", 28),
    (
        "best-sellers-beauty-personal-care",
        "Best Sellers in Beauty & Personal Care",
        16,
    ),
)

HOME_BROWSE_FIELDS = frozenset(
    {
        "asin",
        "title",
        "image_path",
        "canonical_path",
        "title_status",
        "evidence_tier",
        "evidence_class",
        "placements",
    }
)

OFFER_DISPLAY_FIELDS = frozenset(
    {
        "asin",
        "slug",
        "canonical_path",
        "title",
        "brand",
        "capacity",
        "color",
        "price_minor",
        "list_price_minor",
        "currency",
        "rating",
        "reviews",
        "image_path",
        "badge",
        "evidence_class",
        "source",
    }
)

DEPARTMENT_SLUGS_FIELD = "department_slugs"
INSTANT_POT_ASIN = "B00FLYWNYQ"
EXTERNAL_SSD_RANKING_ID = "external-ssd"
EXTERNAL_SSD_RANKING_TITLE = "Best Sellers in External Solid State Drives"
DEAL_MEMBERSHIP_FIELDS = frozenset(
    {
        "captured_discount_percent",
        "deal_label",
        "deals_evidence_key",
        "discount_percent",
        "limited_time_deal",
        "themes",
    }
)


class BrowseBreadthError(ValueError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrowseBreadthError(f"invalid breadth fixture {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise BrowseBreadthError(f"breadth fixture {path.name} must be an object")
    return value


def _home_browse_product(product: Mapping[str, Any]) -> dict[str, Any]:
    """Return the sparse, source-backed fields safe for browse-only cards."""

    projected = {
        field: product[field]
        for field in HOME_BROWSE_FIELDS
        if field in product
    }
    placements = projected.get("placements")
    if isinstance(placements, list):
        projected["placements"] = [dict(placement) for placement in placements]
    return projected


def build_home_rail_sections(
    catalog: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Group the 157 homepage products by their seven original source rails."""

    expected = {key: (title, count) for key, title, count in EXPECTED_HOME_RAILS}
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {
        key: [] for key, _, _ in EXPECTED_HOME_RAILS
    }
    seen_asins: set[str] = set()

    for product in catalog.values():
        asin = product.get("asin")
        placements = product.get("placements")
        if not isinstance(asin, str) or not isinstance(placements, list):
            raise BrowseBreadthError("home products require an ASIN and placements")
        if asin in seen_asins:
            raise BrowseBreadthError(f"duplicate home product {asin}")
        if len(placements) != 1 or not isinstance(placements[0], Mapping):
            raise BrowseBreadthError(
                f"home product {asin} must have exactly one original rail placement"
            )
        placement = placements[0]
        key = placement.get("railKey")
        title = placement.get("railTitle")
        ordinal = placement.get("ordinal")
        if key not in grouped or not isinstance(ordinal, int):
            raise BrowseBreadthError(f"home product {asin} has an unknown placement")
        if title != expected[key][0]:
            raise BrowseBreadthError(f"home rail {key} has an unexpected title")
        seen_asins.add(asin)
        grouped[key].append((ordinal, _home_browse_product(product)))

    sections: list[dict[str, Any]] = []
    for key, title, expected_count in EXPECTED_HOME_RAILS:
        products = tuple(product for _, product in sorted(grouped[key], key=lambda row: row[0]))
        if len(products) != expected_count:
            raise BrowseBreadthError(
                f"home rail {key} expected {expected_count} products, found {len(products)}"
            )
        sections.append(
            {
                "key": key,
                "title": title,
                "count": len(products),
                "products": products,
            }
        )

    if len(seen_asins) != 157:
        raise BrowseBreadthError(
            f"home browse breadth expected 157 unique ASINs, found {len(seen_asins)}"
        )
    return tuple(sections)


def _asins(products_or_asins: Iterable[Mapping[str, Any] | str]) -> set[str]:
    values: set[str] = set()
    for product in products_or_asins:
        asin = product if isinstance(product, str) else product.get("asin")
        if not isinstance(asin, str) or not asin:
            raise BrowseBreadthError("offer exclusions require valid ASINs")
        values.add(asin)
    return values


def build_portable_ssd_supplement(
    catalog: Mapping[str, Mapping[str, Any]],
    frozen_products_or_asins: Iterable[Mapping[str, Any] | str],
) -> tuple[dict[str, Any], ...]:
    """Return browse-only homepage matches not already in the frozen nine."""

    frozen_asins = _asins(frozen_products_or_asins)
    supplement = tuple(
        _home_browse_product(product)
        for product in search_home_catalog("portable ssd", catalog)
        if product.get("asin") not in frozen_asins
    )
    supplement_asins = [product.get("asin") for product in supplement]
    if len(supplement_asins) != len(set(supplement_asins)):
        raise BrowseBreadthError("portable SSD supplement contains duplicate ASINs")
    return supplement


def _offer_display_product(product: Mapping[str, Any], source: str) -> dict[str, Any]:
    asin = product.get("asin")
    price_minor = product.get("price_minor")
    currency = product.get("currency")
    if not isinstance(asin, str) or not asin:
        raise BrowseBreadthError("verified offers require an ASIN")
    if isinstance(price_minor, bool) or not isinstance(price_minor, int) or price_minor < 0:
        raise BrowseBreadthError(f"verified offer {asin} has an invalid price")
    if currency != "USD":
        raise BrowseBreadthError(f"verified offer {asin} is outside the USD baseline")

    normalized = dict(product)
    canonical_path = normalized.get("canonical_path") or normalized.get("canonicalPath")
    if canonical_path is None:
        slug = normalized.get("slug")
        canonical_path = f"/{slug}/dp/{asin}" if isinstance(slug, str) and slug else f"/dp/{asin}"
    normalized["canonical_path"] = canonical_path
    normalized["source"] = source
    return {
        field: normalized[field]
        for field in OFFER_DISPLAY_FIELDS
        if field in normalized
    }


def combine_verified_offer_display(
    frozen_products: Sequence[Mapping[str, Any]],
    direct_products: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Combine verified offers while letting newer direct evidence win by ASIN.

    The function never derives or adjusts a price. It only projects values from
    the two evidence inputs, preserving frozen order and appending new direct
    offers in their source order.
    """

    offers: dict[str, dict[str, Any]] = {}
    for product in frozen_products:
        projected = _offer_display_product(product, "task-fixture")
        asin = str(projected["asin"])
        if asin in offers:
            raise BrowseBreadthError(f"duplicate frozen offer {asin}")
        offers[asin] = projected
    for product in direct_products:
        projected = _offer_display_product(product, "direct-pdp")
        offers[str(projected["asin"])] = projected
    return tuple(offers.values())


def build_department_commerce_supplements(
    catalog: Mapping[str, Mapping[str, Any]],
    frozen_fixture: Mapping[str, Any],
    direct_fixture: Mapping[str, Any],
    verified_offers: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Expose already-verified offers under their directly evidenced taxonomy.

    This is deliberately a search/category view rather than a catalog merge:
    the 157 captured homepage cards retain their original rail membership, and
    Deals continues to use its own explicit membership fixture.  Existing rich
    direct-PDP records win over sparse homepage duplicates; otherwise the exact
    projected verified offer is used without deriving any commerce fact.
    """

    ranking = frozen_fixture.get("ranking")
    frozen_products = frozen_fixture.get("products")
    direct_products = direct_fixture.get("products")
    if (
        frozen_fixture.get("schema") != "amazon-clone.fixture.v1"
        or not isinstance(ranking, Mapping)
        or ranking.get("list_id") != EXTERNAL_SSD_RANKING_ID
        or ranking.get("title") != EXTERNAL_SSD_RANKING_TITLE
        or not isinstance(frozen_products, list)
        or len(frozen_products) != 9
    ):
        raise BrowseBreadthError("external SSD taxonomy evidence is invalid")
    if (
        direct_fixture.get("schema") != "amazon-clone.home-pdp-evidence.v1"
        or not isinstance(direct_products, list)
    ):
        raise BrowseBreadthError("direct PDP taxonomy evidence is invalid")

    direct_by_asin = {
        str(product.get("asin") or ""): product
        for product in direct_products
        if isinstance(product, Mapping)
    }
    instant_pot = direct_by_asin.get(INSTANT_POT_ASIN)
    instant_detail = instant_pot.get("pdp") if isinstance(instant_pot, Mapping) else None
    breadcrumb = (
        instant_detail.get("breadcrumb")
        if isinstance(instant_detail, Mapping)
        else None
    )
    if (
        not isinstance(instant_detail, Mapping)
        or instant_detail.get("page_category") != "Home & Kitchen"
        or not isinstance(breadcrumb, list)
        or not breadcrumb
        or breadcrumb[0] != "Home & Kitchen"
    ):
        raise BrowseBreadthError("Instant Pot lacks direct Home & Kitchen taxonomy")

    offer_by_asin: dict[str, Mapping[str, Any]] = {}
    for offer in verified_offers:
        asin = offer.get("asin")
        if not isinstance(asin, str) or not asin or asin in offer_by_asin:
            raise BrowseBreadthError("department commerce offers require unique ASINs")
        offer_by_asin[asin] = offer

    frozen_asins: list[str] = []
    for product in frozen_products:
        asin = product.get("asin") if isinstance(product, Mapping) else None
        if not isinstance(asin, str) or not asin or asin in frozen_asins:
            raise BrowseBreadthError("external SSD fixture requires nine unique ASINs")
        frozen_asins.append(asin)

    assignments = (
        (INSTANT_POT_ASIN, "home-kitchen"),
        *((asin, "computers") for asin in frozen_asins),
    )
    supplements: list[dict[str, Any]] = []
    for asin, department_slug in assignments:
        offer = offer_by_asin.get(asin)
        if offer is None:
            raise BrowseBreadthError(f"department commerce offer is missing: {asin}")
        catalog_product = catalog.get(asin)
        if (
            isinstance(catalog_product, Mapping)
            and catalog_product.get("evidence_tier") == "pdp-direct"
        ):
            product = dict(catalog_product)
        else:
            product = dict(offer)

        if (
            product.get("price_minor") != offer.get("price_minor")
            or product.get("currency") != "USD"
            or product.get("currency") != offer.get("currency")
            or product.get("image_path") != offer.get("image_path")
            or not isinstance(product.get("image_path"), str)
            or not str(product["image_path"]).startswith("/static/")
        ):
            raise BrowseBreadthError(
                f"department commerce offer changed verified facts: {asin}"
            )
        if set(product).intersection(DEAL_MEMBERSHIP_FIELDS):
            raise BrowseBreadthError(
                f"department commerce offer contains Deals membership: {asin}"
            )
        product[DEPARTMENT_SLUGS_FIELD] = (department_slug,)
        supplements.append(product)

    supplement_asins = [str(product["asin"]) for product in supplements]
    if len(supplements) != 10 or len(supplement_asins) != len(set(supplement_asins)):
        raise BrowseBreadthError("department commerce supplement must contain 10 offers")
    return tuple(supplements)


def load_browse_breadth(fixture_root: Path) -> dict[str, Any]:
    """Load all browse-breadth views from the immutable local evidence set."""

    root = fixture_root.resolve()
    frozen = _read_object(root / "task-frozen-900136-v1.json")
    direct = _read_object(root / "home-pdp-evidence.json")
    region = frozen.get("region")
    market = direct.get("marketContext")
    if not isinstance(region, Mapping) or (
        region.get("delivery_country"), region.get("currency")
    ) != ("Singapore", "USD"):
        raise BrowseBreadthError("frozen offers are outside the Singapore/USD baseline")
    if not isinstance(market, Mapping) or (
        market.get("deliveryCountry"), market.get("currency")
    ) != ("Singapore", "USD"):
        raise BrowseBreadthError("direct offers are outside the Singapore/USD baseline")
    frozen_products = frozen.get("products")
    direct_products = direct.get("products")
    if not isinstance(frozen_products, list) or not isinstance(direct_products, list):
        raise BrowseBreadthError("offer fixtures require product arrays")

    catalog = load_home_product_catalog(root)
    rail_sections = build_home_rail_sections(catalog)
    supplement = build_portable_ssd_supplement(catalog, frozen_products)
    offers = combine_verified_offer_display(frozen_products, direct_products)
    department_supplements = build_department_commerce_supplements(
        catalog,
        frozen,
        direct,
        offers,
    )
    search_commerce_cards = load_search_commerce_cards(root)
    if len(supplement) != 27:
        raise BrowseBreadthError(
            f"portable SSD supplement expected 27 products, found {len(supplement)}"
        )
    expected_offer_count = len(
        {
            str(product.get("asin") or "")
            for product in (*frozen_products, *direct_products)
        }
    )
    if len(offers) != expected_offer_count:
        raise BrowseBreadthError(
            "verified offer union expected "
            f"{expected_offer_count} products, found {len(offers)}"
        )
    return {
        "rail_sections": rail_sections,
        "portable_ssd_supplement": supplement,
        "verified_offers": offers,
        "department_commerce_supplements": department_supplements,
        "search_commerce_cards": search_commerce_cards,
    }
