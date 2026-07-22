from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


DEALS_EVIDENCE_FIXTURE = "deals-evidence.json"
DEALS_DEFAULT_CARDS_FIXTURE = "deals-current-2026-07-22.json"
DEALS_THEME_CHIPS: tuple[tuple[str, str], ...] = (
    ("lightning-deals", "Lightning deals"),
    ("customers-most-loved", "Customers' Most-Loved"),
    ("outlet", "Outlet"),
    ("lowest-price-365", "Lowest Price in 365 Days"),
    ("premium-brands", "Premium Brands"),
    ("summer-favorites", "Summer Favorites"),
    ("beauty", "Beauty"),
    ("fashion", "Fashion"),
    ("home", "Home"),
    ("toys", "Toys & Games"),
    ("electronics", "Electronics"),
    ("devices", "Devices"),
    ("kitchen", "Kitchen"),
    ("everyday-essentials", "Everyday Essentials"),
    ("amazon-brands", "Amazon Brands"),
    ("computers", "Computers & Accessories"),
    ("pet-supplies", "Pet Supplies"),
    ("furniture", "Furniture"),
    ("tvs-accessories", "TVs & Accessories"),
    ("home-diy-appliances", "Home DIY & Appliances"),
    ("sports-outdoors", "Sports & Outdoors"),
    ("grocery", "Grocery"),
    ("health-household", "Health & Household"),
    ("cell-phones-accessories", "Cell Phones & Accessories"),
    ("small-business", "Small Business"),
    ("video-games", "Video Games"),
    ("lawn-garden", "Lawn & Garden"),
    ("automotive", "Automotive"),
    ("camera-photo", "Camera & Photo"),
    ("books", "Books"),
    ("jewelry", "Jewelry"),
    ("baby", "Baby"),
    ("office-supplies", "Office Supplies"),
    ("musical-instruments", "Musical Instruments"),
    ("refurbished", "Refurbished Products"),
    ("coupons", "Coupons"),
)
DEALS_THEME_LABELS = dict(DEALS_THEME_CHIPS)
DEALS_SOURCE_PRICE_LIMIT_MINOR = 15_800
DEALS_DISCOUNT_LIMIT = 75
_MONEY_INPUT = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,2})?")
_DISCOUNT_INPUT = re.compile(r"(?:0|[1-9][0-9]?)")


class DealsCatalogError(ValueError):
    pass


def _department_slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    if not slug:
        raise DealsCatalogError("deal department must have a usable label")
    return slug


def _read_fixture(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DealsCatalogError(f"invalid Deals fixture: {path.name}") from exc
    if not isinstance(payload, dict):
        raise DealsCatalogError("Deals fixture must be an object")
    return payload


def load_deals_default_card_offers(
    fixture_root: Path,
) -> tuple[dict[str, Any], ...]:
    """Validate and project the ten current source Deal cards as base offers."""

    fixture_root = fixture_root.resolve()
    path = (fixture_root / DEALS_DEFAULT_CARDS_FIXTURE).resolve()
    if fixture_root not in path.parents:
        raise DealsCatalogError("Deals card fixture escapes fixture root")
    payload = _read_fixture(path)
    if payload.get("schema") != "amazon-clone.deals-default-card-offers.v1":
        raise DealsCatalogError("unsupported Deals card fixture schema")
    scope = payload.get("scope")
    products = payload.get("products")
    if (
        not isinstance(scope, dict)
        or scope.get("observed_card_count") != 10
        or scope.get("rating_or_reviews_captured") is not False
        or scope.get("delivery_or_inventory_captured") is not False
        or scope.get("option_matrix_captured") is not False
        or not isinstance(products, list)
        or len(products) != 10
    ):
        raise DealsCatalogError("Deals card evidence boundary is invalid")

    clone_root = fixture_root.parent
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for expected_order, raw in enumerate(products, 1):
        if not isinstance(raw, dict):
            raise DealsCatalogError("Deals card must be an object")
        asin = raw.get("asin")
        title = raw.get("title")
        brand = raw.get("brand")
        source_url = raw.get("source_product_url")
        image_path = raw.get("image_path")
        image_sha = raw.get("image_sha256")
        price = raw.get("price_minor")
        reference = raw.get("reference_price_minor")
        discount = raw.get("discount_percent")
        if (
            not isinstance(asin, str)
            or re.fullmatch(r"[A-Z0-9]{10}", asin) is None
            or asin in seen
            or raw.get("display_order") != expected_order
            or not isinstance(title, str)
            or not title.strip()
            or (brand is not None and (not isinstance(brand, str) or not brand.strip()))
            or not isinstance(source_url, str)
            or not isinstance(image_path, str)
            or not image_path.startswith("/static/")
            or raw.get("image_media_type") != "image/avif"
            or not isinstance(image_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", image_sha) is None
            or isinstance(price, bool)
            or not isinstance(price, int)
            or price < 0
            or isinstance(reference, bool)
            or not isinstance(reference, int)
            or reference <= price
            or isinstance(discount, bool)
            or not isinstance(discount, int)
            or not 1 <= discount <= DEALS_DISCOUNT_LIMIT
            or raw.get("deal_label") != "Limited time deal"
            or raw.get("reference_price_label") not in {"Typical", "List"}
            or raw.get("currency") != "USD"
            or "rating" in raw
            or "reviews" in raw
            or "delivery" in raw
            or "options" in raw
        ):
            raise DealsCatalogError(f"Deals source card is invalid: {asin!r}")
        parsed = urlsplit(source_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.amazon.com", "amazon.com"}
            or not parsed.path.endswith(f"/dp/{asin}")
            or parsed.query
            or parsed.fragment
        ):
            raise DealsCatalogError(f"Deals product URL is invalid: {asin}")
        local_asset = (clone_root / image_path.removeprefix("/")).resolve()
        static_root = (clone_root / "static").resolve()
        if (
            static_root not in local_asset.parents
            or not local_asset.is_file()
            or hashlib.sha256(local_asset.read_bytes()).hexdigest() != image_sha
        ):
            raise DealsCatalogError(f"Deals image integrity failed: {asin}")
        computed_discount = round((reference - price) * 100 / reference)
        if computed_discount != discount:
            raise DealsCatalogError(f"Deals discount arithmetic changed: {asin}")
        seen.add(asin)
        normalized.append(
            {
                "asin": asin,
                "slug": parsed.path.split("/")[1],
                "canonical_path": parsed.path,
                "title": title,
                "brand": brand or "",
                "capacity": "",
                "color": "",
                "price_minor": price,
                "list_price_minor": reference,
                "reference_price_label": str(raw["reference_price_label"]),
                "captured_discount_percent": discount,
                "currency": "USD",
                "rating": "",
                "reviews": 0,
                "image_path": image_path,
                "badge": "",
                "evidence_class": "direct-deals-card",
                "source": "direct-deals",
                "deal_label": "Limited time deal",
                "image_alt": str(raw.get("image_alt") or title),
            }
        )
    return tuple(normalized)


def load_deals_catalog(
    fixture_root: Path,
    verified_offers: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Join explicit Deals membership to strict commerce offers.

    ``verified_offers`` is an availability pool, not a Deals membership list.
    Only ASINs named by the Deals evidence fixture may enter this catalog, so a
    future ordinary commerce offer does not need unrelated Deals metadata.
    """

    fixture_root = fixture_root.resolve()
    metadata_path = (fixture_root / DEALS_EVIDENCE_FIXTURE).resolve()
    if fixture_root not in metadata_path.parents:
        raise DealsCatalogError("Deals fixture escapes fixture root")
    payload = _read_fixture(metadata_path)
    if payload.get("schema") != "amazon-clone.deals-evidence.v1":
        raise DealsCatalogError("unsupported Deals fixture schema")
    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise DealsCatalogError("Deals fixture has no products")

    metadata_by_asin: dict[str, dict[str, Any]] = {}
    orders: list[int] = []
    for raw in raw_products:
        if not isinstance(raw, dict):
            raise DealsCatalogError("Deals product metadata must be an object")
        asin = raw.get("asin")
        order = raw.get("displayOrder")
        department = raw.get("department")
        themes = raw.get("themes")
        limited_time = raw.get("limitedTimeDeal")
        evidence_key = raw.get("evidenceKey")
        if (
            not isinstance(asin, str)
            or not asin
            or asin in metadata_by_asin
            or isinstance(order, bool)
            or not isinstance(order, int)
            or order <= 0
            or (
                department is not None
                and (not isinstance(department, str) or not department.strip())
            )
            or not isinstance(themes, list)
            or not all(
                isinstance(theme, str) and theme in DEALS_THEME_LABELS
                for theme in themes
            )
            or len(themes) != len(set(themes))
            or not isinstance(limited_time, bool)
            or not isinstance(evidence_key, str)
            or not evidence_key.strip()
        ):
            raise DealsCatalogError(f"Deals metadata is invalid: {asin!r}")
        metadata_by_asin[asin] = raw
        orders.append(order)

    if sorted(orders) != list(range(1, len(orders) + 1)):
        raise DealsCatalogError("Deals display order must be contiguous")

    eligible_asins = frozenset(metadata_by_asin)
    current_card_offers = load_deals_default_card_offers(fixture_root)
    current_card_asins = {str(product["asin"]) for product in current_card_offers}
    missing_current_metadata = sorted(current_card_asins - eligible_asins)
    if missing_current_metadata:
        raise DealsCatalogError(
            "current Deals cards require display metadata: "
            + ", ".join(missing_current_metadata)
        )

    offer_by_asin: dict[str, dict[str, Any]] = {}
    all_offers: tuple[Mapping[str, Any], ...] = (
        *verified_offers,
        *current_card_offers,
    )
    for raw_offer in all_offers:
        offer = dict(raw_offer)
        asin = offer.get("asin")
        if not isinstance(asin, str) or not asin:
            raise DealsCatalogError("strict commerce offers require ASINs")
        if asin not in eligible_asins:
            continue
        if asin in offer_by_asin:
            raise DealsCatalogError("strict Deals offers require unique ASINs")
        price_minor = offer.get("price_minor")
        if (
            isinstance(price_minor, bool)
            or not isinstance(price_minor, int)
            or price_minor < 0
            or offer.get("currency") != "USD"
        ):
            raise DealsCatalogError(f"strict Deals offer is invalid: {asin}")
        offer_by_asin[asin] = offer

    missing_offers = sorted(eligible_asins - offer_by_asin.keys())
    if missing_offers:
        raise DealsCatalogError(
            "Deals metadata references unavailable offers: "
            f"offers_missing={missing_offers}"
        )

    captured_deal_evidence: dict[str, tuple[bool, int | None]] = {}
    for fixture_name in (
        "task-frozen-900136-v1.json",
        "home-pdp-evidence.json",
    ):
        fixture = _read_fixture(fixture_root / fixture_name)
        source_products = fixture.get("products")
        if not isinstance(source_products, list):
            raise DealsCatalogError(f"{fixture_name} has no products")
        for product in source_products:
            if not isinstance(product, dict) or not isinstance(
                product.get("asin"), str
            ):
                continue
            detail = product.get("pdp")
            deal_badge = (
                detail.get("deal_badge") if isinstance(detail, dict) else None
            )
            discount = (
                detail.get("discount_percent")
                if isinstance(detail, dict)
                else None
            )
            captured_deal_evidence[str(product["asin"])] = (
                deal_badge == "Limited time deal",
                discount if isinstance(discount, int) else None,
            )
    for product in current_card_offers:
        captured_deal_evidence[str(product["asin"])] = (
            product.get("deal_label") == "Limited time deal",
            int(product["captured_discount_percent"]),
        )

    catalog: list[dict[str, Any]] = []
    for asin, metadata in metadata_by_asin.items():
        offer = dict(offer_by_asin[asin])
        list_price = offer.get("list_price_minor")
        price = int(offer["price_minor"])
        arithmetic_discount: int | None = None
        if isinstance(list_price, int) and not isinstance(list_price, bool):
            if list_price > price:
                arithmetic_discount = round(
                    (list_price - price) * 100 / list_price
                )
            else:
                offer["list_price_minor"] = None
        expected_limited, captured_discount = captured_deal_evidence.get(
            asin, (False, None)
        )
        if bool(metadata["limitedTimeDeal"]) != expected_limited:
            raise DealsCatalogError(
                f"limited-time label does not match direct evidence: {asin}"
            )
        if captured_discount is not None and (
            arithmetic_discount is None
            or arithmetic_discount != captured_discount
        ):
            raise DealsCatalogError(
                f"captured discount does not match source evidence: {asin}"
            )
        rating_value: float | None = None
        try:
            if str(offer.get("rating") or "").strip():
                rating_value = float(str(offer["rating"]))
        except ValueError:
            rating_value = None
        offer.update(
            {
                "display_order": int(metadata["displayOrder"]),
                "department": (
                    str(metadata["department"])
                    if metadata["department"] is not None
                    else ""
                ),
                "department_slug": (
                    _department_slug(str(metadata["department"]))
                    if metadata["department"] is not None
                    else ""
                ),
                "themes": tuple(str(theme) for theme in metadata["themes"]),
                "limited_time_deal": expected_limited,
                "discount_percent": captured_discount,
                "rating_value": rating_value,
                "deals_evidence_key": str(metadata["evidenceKey"]),
            }
        )
        catalog.append(offer)
    return tuple(sorted(catalog, key=lambda product: product["display_order"]))


def _first(query: Mapping[str, Sequence[str]], key: str) -> str:
    values = query.get(key, ())
    return values[0].strip() if len(values) == 1 else ""


def _money_filter(
    query: Mapping[str, Sequence[str]], key: str, limit_minor: int
) -> tuple[int | None, str]:
    raw = _first(query, key)
    if not raw or _MONEY_INPUT.fullmatch(raw) is None:
        return None, ""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, ""
    minor = int(value * 100)
    if minor < 0 or minor > limit_minor:
        return None, ""
    canonical = f"{minor / 100:.2f}".rstrip("0").rstrip(".")
    return minor, canonical


def _discount_filter(
    query: Mapping[str, Sequence[str]], key: str
) -> tuple[int | None, str]:
    raw = _first(query, key)
    if not raw or _DISCOUNT_INPUT.fullmatch(raw) is None:
        return None, ""
    value = int(raw)
    if value < 0 or value > DEALS_DISCOUNT_LIMIT:
        return None, ""
    return value, str(value)


def build_deals_view(
    catalog: Sequence[Mapping[str, Any]],
    query: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Apply copyable GET filters to the evidence-backed Deals catalog."""

    products = [dict(product) for product in catalog]
    departments = {
        str(product["department_slug"]): str(product["department"])
        for product in products
        if str(product.get("department_slug") or "")
    }
    brands = {
        str(product.get("brand") or "")
        for product in products
        if str(product.get("brand") or "").strip()
    }
    theme = _first(query, "theme")
    if theme not in DEALS_THEME_LABELS:
        theme = ""
    department = _first(query, "department")
    if department not in departments:
        department = ""
    selected_brands: list[str] = []
    for brand in query.get("brand", ()):
        candidate = brand.strip()
        if candidate in brands and candidate not in selected_brands:
            selected_brands.append(candidate)
    rating = _first(query, "rating")
    if rating != "4-up":
        rating = ""
    price_limit_minor = max(
        DEALS_SOURCE_PRICE_LIMIT_MINOR,
        max((int(product["price_minor"]) for product in products), default=0),
    )
    min_price, min_price_text = _money_filter(
        query, "minPrice", price_limit_minor
    )
    max_price, max_price_text = _money_filter(
        query, "maxPrice", price_limit_minor
    )
    min_discount, min_discount_text = _discount_filter(query, "minDiscount")
    max_discount, max_discount_text = _discount_filter(query, "maxDiscount")
    deal_type = _first(query, "dealType")
    if deal_type != "limited-time":
        deal_type = ""
    range_error = ""
    if min_price is not None and max_price is not None and min_price > max_price:
        range_error = "Minimum price cannot exceed maximum price."
    elif (
        min_discount is not None
        and max_discount is not None
        and min_discount > max_discount
    ):
        range_error = "Minimum discount cannot exceed maximum discount."

    filtered: list[dict[str, Any]] = []
    if not range_error:
        for product in products:
            discount = product.get("discount_percent")
            discount_value = int(discount) if isinstance(discount, int) else None
            if theme and theme not in product.get("themes", ()):
                continue
            if department and product.get("department_slug") != department:
                continue
            if selected_brands and product.get("brand") not in selected_brands:
                continue
            if rating and (
                product.get("rating_value") is None
                or float(product["rating_value"]) < 4.0
            ):
                continue
            price = int(product["price_minor"])
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            if min_discount is not None or max_discount is not None:
                if discount_value is None:
                    continue
                if min_discount is not None and discount_value < min_discount:
                    continue
                if max_discount is not None and discount_value > max_discount:
                    continue
            if deal_type == "limited-time" and not product.get(
                "limited_time_deal"
            ):
                continue
            filtered.append(product)

    department_counts = Counter(
        str(product["department_slug"]) for product in products
    )
    brand_counts = Counter(str(product.get("brand") or "") for product in products)
    filters = {
        "theme": theme,
        "department": department,
        "brands": tuple(selected_brands),
        "rating": rating,
        "min_price_minor": min_price,
        "max_price_minor": max_price,
        "min_price_text": min_price_text,
        "max_price_text": max_price_text,
        "min_discount": min_discount,
        "max_discount": max_discount,
        "min_discount_text": min_discount_text,
        "max_discount_text": max_discount_text,
        "deal_type": deal_type,
        "range_error": range_error,
        "price_limit_minor": price_limit_minor,
        "price_limit_text": f"{price_limit_minor / 100:.2f}".rstrip("0").rstrip("."),
    }
    active_count = sum(
        (
            bool(theme),
            bool(department),
            len(selected_brands),
            bool(rating),
            min_price is not None,
            max_price is not None,
            min_discount is not None,
            max_discount is not None,
            bool(deal_type),
        )
    )
    return {
        "products": filtered,
        "all_count": len(products),
        "result_count": len(filtered),
        "filters": filters,
        "active_filter_count": active_count,
        "theme_chips": DEALS_THEME_CHIPS,
        "departments": tuple(
            {
                "slug": slug,
                "label": label,
                "count": department_counts[slug],
            }
            for slug, label in departments.items()
        ),
        "brands": tuple(
            {"label": brand, "count": brand_counts[brand]}
            for brand in sorted(brands, key=str.casefold)
        ),
    }
