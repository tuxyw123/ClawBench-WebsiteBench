from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import parse_qsl, urlencode


TOKEN_RE = re.compile(r"[a-z0-9]+")
INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9a-fA-F]{2})")
MONEY_INPUT_RE = re.compile(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?")
PAGE_INPUT_RE = re.compile(r"[1-9][0-9]*")

DEFAULT_SEARCH_QUERY = "portable ssd"
DEFAULT_SEARCH_DEPARTMENT = "aps"
SEARCH_PAGE_SIZE = 36
MAX_SEARCH_PAGE = 1_000
MAX_SEARCH_QUERY_LENGTH = 200
MAX_SEARCH_BRANDS = 8
MAX_SEARCH_BRAND_LENGTH = 64
MAX_SEARCH_QUERY_FIELDS = 24
MAX_RAW_SEARCH_QUERY_LENGTH = 4_096
MAX_SEARCH_PRICE_MINOR = 100_000_000

SEARCH_SORTS = frozenset(
    {"relevance", "price-asc", "price-desc", "rating-desc"}
)


class SearchValidationError(ValueError):
    """Raised when a public search query cannot be interpreted unambiguously."""


@dataclass(frozen=True)
class SearchRequest:
    """Canonical, validated state for a search results request."""

    query: str = DEFAULT_SEARCH_QUERY
    department: str = DEFAULT_SEARCH_DEPARTMENT
    brands: tuple[str, ...] = ()
    min_price_minor: int | None = None
    max_price_minor: int | None = None
    rating: Literal["4-up"] | None = None
    availability: Literal["in-stock"] | None = None
    sort: Literal[
        "relevance", "price-asc", "price-desc", "rating-desc"
    ] = "relevance"
    page: int = 1


@dataclass(frozen=True)
class SearchHit:
    """A ranked product plus only the shopping facts its evidence supplies."""

    product: Mapping[str, Any]
    relevance: int
    source_index: int
    departments: tuple[str, ...]
    brand: str | None
    price_minor: int | None
    rating_value: Decimal | None
    availability: Literal["in-stock"] | None

    @property
    def asin(self) -> str:
        return str(self.product.get("asin") or "")


@dataclass(frozen=True)
class SearchPage:
    """One deterministic page from an evidence-aware result set."""

    request: SearchRequest
    items: tuple[SearchHit, ...]
    total: int
    page: int
    page_size: int
    page_count: int

    @property
    def hits(self) -> tuple[SearchHit, ...]:
        """Alias for callers that use the domain term rather than UI terminology."""

        return self.items

# Shopping-intent words do not identify a product or department.  Ignoring them
# lets links such as "beauty products" resolve through the evidenced Beauty rail
# without inventing a separate taxonomy.
INTENT_TOKENS = {
    "best",
    "deal",
    "deals",
    "discover",
    "featured",
    "find",
    "finds",
    "for",
    "item",
    "items",
    "latest",
    "more",
    "new",
    "product",
    "products",
    "seller",
    "sellers",
    "shop",
    "shopping",
    "top",
    "trending",
    "you",
    "your",
}

# These are category labels already present in the frozen home rail identity.
# They are search aliases, not additional product claims.
RAIL_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "related-items": ("related", "recently viewed"),
    "best-sellers-home-kitchen": ("home", "kitchen"),
    "top-sellers-toys": ("toy", "toys"),
    "best-sellers-computers-accessories": (
        "computer",
        "computers",
        "accessory",
        "accessories",
        "electronics",
    ),
    "best-sellers-books": ("book", "books"),
    "top-picks-singapore": ("singapore", "top picks"),
    "best-sellers-beauty-personal-care": (
        "beauty",
        "personal care",
    ),
}

# Five departments are directly evidenced by named rails in the captured home
# page.  These definitions are deliberately rail-backed: selecting a department
# must not pull in a product merely because its title happens to contain words
# such as "book", "home", or "computer".
SOURCE_DEPARTMENTS: tuple[dict[str, Any], ...] = (
    {
        "slug": "books",
        "title": "Books",
        "query": "books",
        "rail_key": "best-sellers-books",
        "rail_title": "Best Sellers in Books",
        "aliases": ("book", "books"),
        "featured_asins": ("168281808X",),
    },
    {
        "slug": "home-kitchen",
        "title": "Home & Kitchen",
        "query": "home kitchen",
        "rail_key": "best-sellers-home-kitchen",
        "rail_title": "Best Sellers in Home & Kitchen",
        "aliases": ("home", "kitchen", "home kitchen", "home and kitchen"),
        "featured_asins": ("B01M16WBW1",),
    },
    {
        "slug": "toys-games",
        "title": "Toys & Games",
        "query": "toys",
        "rail_key": "top-sellers-toys",
        "rail_title": "Top Sellers in Toys for you",
        "aliases": ("toy", "toys", "toys games", "toys and games"),
        "featured_asins": ("B0BG6B2D4D",),
    },
    {
        "slug": "computers",
        "title": "Computers & Accessories",
        "query": "computers accessories",
        "rail_key": "best-sellers-computers-accessories",
        "rail_title": "Best Sellers in Computers & Accessories",
        "aliases": (
            "computer",
            "computers",
            "computer accessories",
            "computers accessories",
            "computer and accessories",
            "computers and accessories",
            "electronics",
        ),
        "featured_asins": (),
    },
    {
        "slug": "beauty-personal-care",
        "title": "Beauty & Personal Care",
        "query": "beauty personal care",
        "rail_key": "best-sellers-beauty-personal-care",
        "rail_title": "Best Sellers in Beauty & Personal Care",
        "aliases": (
            "beauty",
            "personal care",
            "beauty personal care",
            "beauty and personal care",
        ),
        "featured_asins": ("B074PVTPBW",),
    },
)

SOURCE_DEPARTMENT_BY_SLUG: dict[str, dict[str, Any]] = {
    str(department["slug"]): department for department in SOURCE_DEPARTMENTS
}
SOURCE_DEPARTMENT_SLUG_BY_RAIL: dict[str, str] = {
    str(department["rail_key"]): str(department["slug"])
    for department in SOURCE_DEPARTMENTS
}
SEARCH_DEPARTMENTS = frozenset(
    {DEFAULT_SEARCH_DEPARTMENT, *SOURCE_DEPARTMENT_BY_SLUG}
)
SEARCH_QUERY_PARAMETERS = frozenset(
    {
        "k",
        "field-keywords",
        "i",
        "brand",
        "minPrice",
        "maxPrice",
        "rating",
        "availability",
        "sort",
        "page",
    }
)
SEARCH_SCALAR_PARAMETERS = SEARCH_QUERY_PARAMETERS - {"brand"}


def _clean_public_text(value: str, label: str, limit: int) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise SearchValidationError(f"{label} contains control characters")
    cleaned = " ".join(value.strip().split())
    if len(cleaned) > limit:
        raise SearchValidationError(f"{label} is too long")
    return cleaned


def _parse_money_parameter(value: str, label: str) -> int:
    if MONEY_INPUT_RE.fullmatch(value) is None:
        raise SearchValidationError(
            f"{label} must be a non-negative amount with at most two decimals"
        )
    try:
        minor = int(Decimal(value) * 100)
    except (InvalidOperation, ValueError):
        raise SearchValidationError(f"{label} is not a valid amount") from None
    if minor > MAX_SEARCH_PRICE_MINOR:
        raise SearchValidationError(f"{label} exceeds the supported search range")
    return minor


def _one_parameter(
    grouped: Mapping[str, list[str]], name: str, default: str = ""
) -> str:
    values = grouped.get(name, [])
    if len(values) > 1:
        raise SearchValidationError(f"{name} may appear only once")
    return values[0] if values else default


def parse_search_request(raw_query: str) -> SearchRequest:
    """Strictly parse the public ``/s`` query string into canonical state.

    The function consumes the raw query rather than a ``parse_qs`` mapping so
    duplicate scalar fields, malformed escapes, and ambiguous keyword aliases
    cannot be silently discarded.
    """

    if not isinstance(raw_query, str):
        raise SearchValidationError("search query must be text")
    if len(raw_query) > MAX_RAW_SEARCH_QUERY_LENGTH:
        raise SearchValidationError("search query string is too long")
    if INVALID_PERCENT_ESCAPE_RE.search(raw_query):
        raise SearchValidationError("search query contains an invalid percent escape")
    try:
        pairs = parse_qsl(
            raw_query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=MAX_SEARCH_QUERY_FIELDS,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise SearchValidationError(f"invalid search query string: {exc}") from None

    grouped: dict[str, list[str]] = {}
    for name, value in pairs:
        if name not in SEARCH_QUERY_PARAMETERS:
            raise SearchValidationError(f"unsupported search parameter: {name or '<empty>'}")
        grouped.setdefault(name, []).append(value)
    for name in SEARCH_SCALAR_PARAMETERS:
        if len(grouped.get(name, ())) > 1:
            raise SearchValidationError(f"{name} may appear only once")
    if "k" in grouped and "field-keywords" in grouped:
        raise SearchValidationError("use either k or field-keywords, not both")

    keyword_parameter = "k" if "k" in grouped else "field-keywords"
    raw_keywords = _one_parameter(grouped, keyword_parameter)
    query = _clean_public_text(
        raw_keywords, "search keywords", MAX_SEARCH_QUERY_LENGTH
    )

    raw_department = _one_parameter(grouped, "i", DEFAULT_SEARCH_DEPARTMENT).strip()
    department = raw_department or DEFAULT_SEARCH_DEPARTMENT
    if department not in SEARCH_DEPARTMENTS:
        raise SearchValidationError(f"unsupported search department: {department}")
    if not query and department == DEFAULT_SEARCH_DEPARTMENT:
        query = DEFAULT_SEARCH_QUERY

    raw_brands = grouped.get("brand", [])
    if len(raw_brands) > MAX_SEARCH_BRANDS:
        raise SearchValidationError(
            f"brand may appear at most {MAX_SEARCH_BRANDS} times"
        )
    brands: list[str] = []
    brand_keys: set[str] = set()
    for raw_brand in raw_brands:
        brand = _clean_public_text(
            raw_brand, "brand", MAX_SEARCH_BRAND_LENGTH
        )
        if not brand:
            raise SearchValidationError("brand must not be empty")
        key = brand.casefold()
        if key not in brand_keys:
            brand_keys.add(key)
            brands.append(brand)

    min_price_raw = _one_parameter(grouped, "minPrice")
    max_price_raw = _one_parameter(grouped, "maxPrice")
    min_price = (
        _parse_money_parameter(min_price_raw, "minPrice")
        if min_price_raw
        else None
    )
    max_price = (
        _parse_money_parameter(max_price_raw, "maxPrice")
        if max_price_raw
        else None
    )
    if "minPrice" in grouped and not min_price_raw:
        raise SearchValidationError("minPrice must not be empty")
    if "maxPrice" in grouped and not max_price_raw:
        raise SearchValidationError("maxPrice must not be empty")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise SearchValidationError("minPrice must not exceed maxPrice")

    rating_value = _one_parameter(grouped, "rating")
    if rating_value not in {"", "4-up"} or (
        "rating" in grouped and not rating_value
    ):
        raise SearchValidationError("rating must be 4-up")
    rating: Literal["4-up"] | None = "4-up" if rating_value else None

    availability_value = _one_parameter(grouped, "availability")
    if availability_value not in {"", "in-stock"} or (
        "availability" in grouped and not availability_value
    ):
        raise SearchValidationError("availability must be in-stock")
    availability: Literal["in-stock"] | None = (
        "in-stock" if availability_value else None
    )

    sort_value = _one_parameter(grouped, "sort", "relevance")
    if sort_value not in SEARCH_SORTS:
        raise SearchValidationError(
            "sort must be relevance, price-asc, price-desc, or rating-desc"
        )

    page_raw = _one_parameter(grouped, "page", "1")
    if PAGE_INPUT_RE.fullmatch(page_raw) is None:
        raise SearchValidationError("page must be a positive integer")
    page = int(page_raw)
    if page > MAX_SEARCH_PAGE:
        raise SearchValidationError(f"page must not exceed {MAX_SEARCH_PAGE}")

    return SearchRequest(
        query=query,
        department=department,
        brands=tuple(sorted(brands, key=lambda value: value.casefold())),
        min_price_minor=min_price,
        max_price_minor=max_price,
        rating=rating,
        availability=availability,
        sort=sort_value,  # type: ignore[arg-type]
        page=page,
    )

# A small set of broad destinations is promoted directly by the captured home
# page even though the corresponding product names do not repeat those words.
# Falling back to the named, source-observed rails keeps those links useful
# without adding prices, ratings, or any other product claim.
HOME_DESTINATION_RAIL_FALLBACKS: dict[str, tuple[str, ...]] = {
    "school supplies": (
        "best-sellers-books",
        "best-sellers-computers-accessories",
    ),
    "trending fashion": ("top-picks-singapore",),
    "fitness": ("top-picks-singapore",),
    "smartphones": ("best-sellers-computers-accessories",),
    "headphones": ("best-sellers-computers-accessories",),
    "watches": ("top-picks-singapore",),
    "summer essentials": ("top-picks-singapore",),
    "stationery": ("best-sellers-books",),
    "fashion": ("top-picks-singapore",),
    "dresses": ("top-picks-singapore",),
    "knits": ("top-picks-singapore",),
    "jackets": ("top-picks-singapore",),
    "jewelry": ("top-picks-singapore",),
    "clothing": ("top-picks-singapore",),
    "apparel": ("top-picks-singapore",),
    "trackers": ("top-picks-singapore",),
    "equipment": ("top-picks-singapore",),
    "baskets hampers": ("best-sellers-home-kitchen",),
    "hardware": ("best-sellers-home-kitchen",),
    "accent furniture": ("best-sellers-home-kitchen",),
    "wallpaper paint": ("best-sellers-home-kitchen",),
    "dining": ("best-sellers-home-kitchen",),
    "mugs": ("best-sellers-home-kitchen",),
    "nintendo": ("top-sellers-toys",),
    "desktops": ("best-sellers-computers-accessories",),
    "brushes": ("best-sellers-beauty-personal-care",),
    "sponges": ("best-sellers-beauty-personal-care",),
    "mirrors": ("best-sellers-beauty-personal-care",),
    "nails": ("best-sellers-beauty-personal-care",),
    "fragrance": ("best-sellers-beauty-personal-care",),
}


def normalize_search_query(value: str) -> str:
    """Return a stable, display-independent representation of a query."""

    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(TOKEN_RE.findall(folded))


def source_department_for_query(query: str) -> dict[str, Any] | None:
    """Resolve an exact, source-observed department destination."""

    normalized = normalize_search_query(query)
    for department in SOURCE_DEPARTMENTS:
        aliases = department.get("aliases", ())
        if normalized in {
            normalize_search_query(str(alias)) for alias in aliases
        }:
            return department
    return None


def source_department_for_rail(rail_key: str) -> dict[str, Any] | None:
    """Return navigation metadata for a captured department rail."""

    return next(
        (
            department
            for department in SOURCE_DEPARTMENTS
            if department.get("rail_key") == rail_key
        ),
        None,
    )


def _has_rail(product: Mapping[str, Any], rail_key: str) -> bool:
    placements = product.get("placements")
    return isinstance(placements, list) and any(
        isinstance(placement, Mapping)
        and placement.get("railKey") == rail_key
        for placement in placements
    )


def _explicit_department_slugs(product: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only known, explicitly supplied taxonomy slugs."""

    raw = product.get("department_slugs")
    if not isinstance(raw, (list, tuple)):
        return ()
    slugs: list[str] = []
    for value in raw:
        if (
            isinstance(value, str)
            and value in SOURCE_DEPARTMENT_BY_SLUG
            and value not in slugs
        ):
            slugs.append(value)
    return tuple(slugs)


def _captured_search_queries(product: Mapping[str, Any]) -> tuple[str, ...]:
    raw = product.get("captured_queries")
    if not isinstance(raw, (list, tuple)):
        return ()
    queries: list[str] = []
    for value in raw:
        normalized = normalize_search_query(value) if isinstance(value, str) else ""
        if normalized and normalized not in queries:
            queries.append(normalized)
    return tuple(queries)


def _source_department_products(
    department: Mapping[str, Any],
    catalog: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep source order, promoting only explicitly verified offers."""

    rail_key = str(department["rail_key"])
    department_slug = str(department["slug"])
    products = [
        product
        for product in catalog.values()
        if _has_rail(product, rail_key)
        or department_slug in _explicit_department_slugs(product)
    ]
    featured = {
        str(asin): index
        for index, asin in enumerate(department.get("featured_asins", ()))
    }
    source_order = {
        str(product.get("asin") or ""): index
        for index, product in enumerate(products)
    }
    products.sort(
        key=lambda product: (
            featured.get(str(product.get("asin") or ""), len(featured)),
            source_order[str(product.get("asin") or "")],
        )
    )
    return products


def _canonical_token(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_canonical_token(token) for token in normalize_search_query(value).split())


def _query_tokens(query: str) -> tuple[str, ...]:
    raw = _tokens(query)
    meaningful = tuple(token for token in raw if token not in INTENT_TOKENS)
    return meaningful or raw


def is_portable_ssd_contract_query(query: str) -> bool:
    """Identify the frozen benchmark query without broadening its result set."""

    return _query_tokens(query) == ("portable", "ssd")


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str) and value.strip():
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str) and item.strip():
                yield item


def _product_search_fields(product: Mapping[str, Any]) -> tuple[str, str, str]:
    title = str(product.get("title") or "")
    identity_values = [str(product.get("brand") or ""), str(product.get("asin") or "")]
    context_values: list[str] = []

    placements = product.get("placements")
    if isinstance(placements, list):
        for placement in placements:
            if not isinstance(placement, Mapping):
                continue
            rail_key = str(placement.get("railKey") or "")
            context_values.extend(_text_values(placement.get("railTitle")))
            context_values.extend(_text_values(rail_key.replace("-", " ")))
            context_values.extend(RAIL_CATEGORY_TERMS.get(rail_key, ()))

    detail = product.get("pdp")
    if isinstance(detail, Mapping):
        context_values.extend(_text_values(detail.get("page_category")))
        context_values.extend(_text_values(detail.get("breadcrumb")))
    context_values.extend(_text_values(product.get("captured_queries")))

    return title, " ".join(identity_values), " ".join(context_values)


def search_home_catalog(
    query: str,
    catalog: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank homepage-evidenced products using only their captured text fields.

    Ties retain the catalog's source order, so the same frozen fixture and query
    always produce the same card order.
    """

    department = source_department_for_query(query)
    if department is not None:
        return _source_department_products(department, catalog)

    query_tokens = _query_tokens(query)
    if not query_tokens:
        return []
    normalized_query = normalize_search_query(query)
    ranked: list[tuple[int, int, str, dict[str, Any]]] = []

    for source_index, product in enumerate(catalog.values()):
        title, identity, context = _product_search_fields(product)
        title_normalized = normalize_search_query(title)
        identity_normalized = normalize_search_query(identity)
        context_normalized = normalize_search_query(context)
        title_tokens = set(_tokens(title))
        identity_tokens = set(_tokens(identity))
        context_tokens = set(_tokens(context))

        matched: set[str] = set()
        score = 0
        if normalized_query and normalized_query == normalize_search_query(str(product.get("asin") or "")):
            matched.update(query_tokens)
            score += 10_000
        if normalized_query and normalized_query in title_normalized:
            score += 500
        elif normalized_query and normalized_query in identity_normalized:
            score += 240
        elif normalized_query and normalized_query in context_normalized:
            score += 150

        for token in query_tokens:
            if token in title_tokens:
                matched.add(token)
                score += 100
            elif token in identity_tokens:
                matched.add(token)
                score += 60
            elif token in context_tokens:
                matched.add(token)
                score += 35
            elif len(token) >= 3 and token in title_normalized:
                matched.add(token)
                score += 45
            elif len(token) >= 3 and token in context_normalized:
                matched.add(token)
                score += 15

        if not matched:
            continue
        score += (100 * len(matched)) // len(query_tokens)
        ranked.append((-score, source_index, str(product.get("asin") or ""), product))

    ranked.sort(key=lambda row: row[:3])
    if ranked:
        return [product for _, _, _, product in ranked]

    fallback_rails = HOME_DESTINATION_RAIL_FALLBACKS.get(normalized_query)
    if fallback_rails is None:
        return []
    fallback_keys = set(fallback_rails)
    return [
        product
        for product in catalog.values()
        if any(
            isinstance(placement, Mapping)
            and str(placement.get("railKey") or "") in fallback_keys
            for placement in product.get("placements", [])
        )
    ]


def _hit_departments(product: Mapping[str, Any]) -> tuple[str, ...]:
    departments = list(_explicit_department_slugs(product))
    placements = product.get("placements")
    if not isinstance(placements, list):
        return tuple(departments)
    for placement in placements:
        if not isinstance(placement, Mapping):
            continue
        slug = SOURCE_DEPARTMENT_SLUG_BY_RAIL.get(
            str(placement.get("railKey") or "")
        )
        if slug is not None and slug not in departments:
            departments.append(slug)
    return tuple(departments)


def _hit_brand(product: Mapping[str, Any]) -> str | None:
    brand = product.get("brand")
    if not isinstance(brand, str):
        return None
    cleaned = " ".join(brand.strip().split())
    return cleaned or None


def _hit_price_minor(product: Mapping[str, Any]) -> int | None:
    price = product.get("price_minor")
    if (
        isinstance(price, bool)
        or not isinstance(price, int)
        or price < 0
        or product.get("currency") != "USD"
    ):
        return None
    return price


def _hit_rating(product: Mapping[str, Any]) -> Decimal | None:
    rating = product.get("rating")
    reviews = product.get("reviews")
    reviews_display = product.get("reviews_display")
    if (
        isinstance(rating, bool)
        or rating is None
        or (
            (
                isinstance(reviews, bool)
                or not isinstance(reviews, int)
                or reviews <= 0
            )
            and (
                not isinstance(reviews_display, str)
                or not reviews_display.strip()
            )
        )
    ):
        return None
    try:
        value = Decimal(str(rating))
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0 or value > 5:
        return None
    return value


def _hit_availability(product: Mapping[str, Any]) -> Literal["in-stock"] | None:
    raw = product.get("availability")
    if not isinstance(raw, str):
        detail = product.get("pdp")
        raw = detail.get("availability") if isinstance(detail, Mapping) else None
    if not isinstance(raw, str):
        return None
    normalized = " ".join(raw.casefold().split())
    if "not in stock" in normalized or "out of stock" in normalized:
        return None
    if normalized == "in stock" or "left in stock" in normalized:
        return "in-stock"
    return None


def build_search_hit(
    product: Mapping[str, Any], *, relevance: int = 0, source_index: int = 0
) -> SearchHit:
    """Project one product into searchable facts without filling sparse fields."""

    return SearchHit(
        product=product,
        relevance=int(relevance),
        source_index=int(source_index),
        departments=_hit_departments(product),
        brand=_hit_brand(product),
        price_minor=_hit_price_minor(product),
        rating_value=_hit_rating(product),
        availability=_hit_availability(product),
    )


def search_home_hits(
    query: str,
    catalog: Mapping[str, dict[str, Any]],
) -> list[SearchHit]:
    """Return evidence-aware hits in exactly the legacy catalog search order."""

    products = search_home_catalog(query, catalog)
    count = len(products)
    return [
        build_search_hit(
            product,
            relevance=count - result_index,
            source_index=result_index,
        )
        for result_index, product in enumerate(products)
    ]


def candidate_search_hits(
    request: SearchRequest,
    catalog: Mapping[str, dict[str, Any]],
    department_supplements: Iterable[Mapping[str, Any]] = (),
) -> list[SearchHit]:
    """Resolve the keyword/department candidate set before shopping refinements."""

    _validate_search_request(request)
    query = request.query
    if not query and request.department != DEFAULT_SEARCH_DEPARTMENT:
        department = SOURCE_DEPARTMENT_BY_SLUG[request.department]
        query = str(department["query"])
    if not query:
        return []

    base_hits = search_home_hits(query, catalog)
    if request.department != DEFAULT_SEARCH_DEPARTMENT:
        target_department = request.department
    else:
        matched_department = source_department_for_query(query)
        target_department = (
            str(matched_department["slug"])
            if matched_department is not None
            else None
        )
    supplement_catalog: dict[str, dict[str, Any]] = {}
    for product in department_supplements:
        if target_department is not None and (
            target_department not in _explicit_department_slugs(product)
        ):
            continue
        if target_department is None and not _captured_search_queries(product):
            continue
        asin = product.get("asin")
        if isinstance(asin, str) and asin and asin not in supplement_catalog:
            supplement_catalog[asin] = dict(product)
    if not supplement_catalog:
        return base_hits

    supplemental_hits = search_home_hits(query, supplement_catalog)
    if not supplemental_hits:
        return base_hits

    supplemental_asins = {hit.asin for hit in supplemental_hits}
    normalized_query = normalize_search_query(query)
    captured_query_hits = [
        hit
        for hit in supplemental_hits
        if normalized_query in _captured_search_queries(hit.product)
    ]
    ordinary_supplemental_hits = [
        hit for hit in supplemental_hits if hit not in captured_query_hits
    ]
    products = [hit.product for hit in captured_query_hits]
    products.extend(
        hit.product for hit in base_hits if hit.asin not in supplemental_asins
    )
    products.extend(hit.product for hit in ordinary_supplemental_hits)
    count = len(products)
    return [
        build_search_hit(
            product,
            relevance=count - source_index,
            source_index=source_index,
        )
        for source_index, product in enumerate(products)
    ]


def _validate_search_request(request: SearchRequest) -> None:
    if not isinstance(request.query, str):
        raise SearchValidationError("search keywords must be text")
    canonical_query = _clean_public_text(
        request.query, "search keywords", MAX_SEARCH_QUERY_LENGTH
    )
    if canonical_query != request.query:
        raise SearchValidationError("search keywords must be canonical text")
    if not isinstance(request.department, str):
        raise SearchValidationError("search department must be text")
    if request.department not in SEARCH_DEPARTMENTS:
        raise SearchValidationError(
            f"unsupported search department: {request.department}"
        )
    if request.sort not in SEARCH_SORTS:
        raise SearchValidationError(f"unsupported search sort: {request.sort}")
    if (
        isinstance(request.page, bool)
        or not isinstance(request.page, int)
        or request.page < 1
        or request.page > MAX_SEARCH_PAGE
    ):
        raise SearchValidationError("page is outside the supported range")
    if request.rating not in {None, "4-up"}:
        raise SearchValidationError("rating must be 4-up")
    if request.availability not in {None, "in-stock"}:
        raise SearchValidationError("availability must be in-stock")
    for label, price in (
        ("minPrice", request.min_price_minor),
        ("maxPrice", request.max_price_minor),
    ):
        if price is not None and (
            isinstance(price, bool)
            or not isinstance(price, int)
            or price < 0
            or price > MAX_SEARCH_PRICE_MINOR
        ):
            raise SearchValidationError(f"{label} is outside the supported range")
    if (
        request.min_price_minor is not None
        and request.max_price_minor is not None
        and request.min_price_minor > request.max_price_minor
    ):
        raise SearchValidationError("minPrice must not exceed maxPrice")
    if len(request.brands) > MAX_SEARCH_BRANDS:
        raise SearchValidationError(
            f"brand may appear at most {MAX_SEARCH_BRANDS} times"
        )
    seen_brands: set[str] = set()
    for brand in request.brands:
        if not isinstance(brand, str):
            raise SearchValidationError("brand must be text")
        cleaned = _clean_public_text(brand, "brand", MAX_SEARCH_BRAND_LENGTH)
        if not cleaned:
            raise SearchValidationError("brand must not be empty")
        if cleaned != brand:
            raise SearchValidationError("brand must be canonical text")
        key = cleaned.casefold()
        if key in seen_brands:
            raise SearchValidationError("brands must be unique")
        seen_brands.add(key)


def _stable_hit_tail(hit: SearchHit) -> tuple[int, int, str]:
    return (-hit.relevance, hit.source_index, hit.asin)


def refine_search_hits(
    request: SearchRequest,
    hits: Iterable[SearchHit],
    *,
    page_size: int = SEARCH_PAGE_SIZE,
) -> SearchPage:
    """Apply evidence-aware filters, stable sorting, and deterministic paging."""

    _validate_search_request(request)
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or page_size < 1
        or page_size > 100
    ):
        raise ValueError("page_size must be an integer from 1 through 100")

    brand_keys = {brand.casefold() for brand in request.brands}
    filtered: list[SearchHit] = []
    for hit in hits:
        if (
            request.department != DEFAULT_SEARCH_DEPARTMENT
            and request.department not in hit.departments
        ):
            continue
        if brand_keys and (
            hit.brand is None or hit.brand.casefold() not in brand_keys
        ):
            continue
        if request.min_price_minor is not None and (
            hit.price_minor is None
            or hit.price_minor < request.min_price_minor
        ):
            continue
        if request.max_price_minor is not None and (
            hit.price_minor is None
            or hit.price_minor > request.max_price_minor
        ):
            continue
        if request.rating == "4-up" and (
            hit.rating_value is None or hit.rating_value < Decimal("4")
        ):
            continue
        if request.availability == "in-stock" and hit.availability != "in-stock":
            continue
        filtered.append(hit)

    if request.sort == "relevance":
        filtered.sort(key=_stable_hit_tail)
    elif request.sort == "price-asc":
        filtered.sort(
            key=lambda hit: (
                hit.price_minor is None,
                hit.price_minor if hit.price_minor is not None else 0,
                *_stable_hit_tail(hit),
            )
        )
    elif request.sort == "price-desc":
        filtered.sort(
            key=lambda hit: (
                hit.price_minor is None,
                -hit.price_minor if hit.price_minor is not None else 0,
                *_stable_hit_tail(hit),
            )
        )
    else:  # rating-desc
        filtered.sort(
            key=lambda hit: (
                hit.rating_value is None,
                -hit.rating_value if hit.rating_value is not None else Decimal(0),
                *_stable_hit_tail(hit),
            )
        )

    total = len(filtered)
    page_count = max(1, (total + page_size - 1) // page_size)
    if total > 0 and request.page > page_count:
        raise SearchValidationError(
            f"page {request.page} exceeds the {page_count} available search pages"
        )
    start = (request.page - 1) * page_size
    items = tuple(filtered[start : start + page_size])
    return SearchPage(
        request=request,
        items=items,
        total=total,
        page=request.page,
        page_size=page_size,
        page_count=page_count,
    )


def build_search_page(
    request: SearchRequest,
    catalog: Mapping[str, dict[str, Any]],
    *,
    department_supplements: Iterable[Mapping[str, Any]] = (),
    page_size: int = SEARCH_PAGE_SIZE,
) -> SearchPage:
    """Convenience composition for the homepage evidence catalog."""

    return refine_search_hits(
        request,
        candidate_search_hits(request, catalog, department_supplements),
        page_size=page_size,
    )


def _format_money_parameter(minor: int) -> str:
    whole, cents = divmod(minor, 100)
    if cents == 0:
        return str(whole)
    return f"{whole}.{cents:02d}".rstrip("0")


def search_href(request: SearchRequest, **overrides: Any) -> str:
    """Build one canonical, copyable ``/s`` URL from validated search state."""

    state = replace(request, **overrides) if overrides else request
    _validate_search_request(state)
    pairs: list[tuple[str, str]] = []
    if state.query:
        pairs.append(("k", state.query))
    if state.department != DEFAULT_SEARCH_DEPARTMENT:
        pairs.append(("i", state.department))
    pairs.extend(
        ("brand", brand)
        for brand in sorted(state.brands, key=lambda value: value.casefold())
    )
    if state.min_price_minor is not None:
        pairs.append(("minPrice", _format_money_parameter(state.min_price_minor)))
    if state.max_price_minor is not None:
        pairs.append(("maxPrice", _format_money_parameter(state.max_price_minor)))
    if state.rating is not None:
        pairs.append(("rating", state.rating))
    if state.availability is not None:
        pairs.append(("availability", state.availability))
    if state.sort != "relevance":
        pairs.append(("sort", state.sort))
    if state.page != 1:
        pairs.append(("page", str(state.page)))
    return "/s" + (f"?{urlencode(pairs)}" if pairs else "")
