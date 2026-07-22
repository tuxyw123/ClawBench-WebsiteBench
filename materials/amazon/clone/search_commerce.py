from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SEARCH_COMMERCE_FIXTURE = "search-commerce-current-2026-07-22.json"
SEARCH_COMMERCE_SCHEMA = "amazon-clone.search-commerce-cards.v1"
SEARCH_COMMERCE_CAPTURED_AT = "2026-07-22T16:10:00+08:00"
SEARCH_COMMERCE_QUERY = "best sellers"
SEARCH_COMMERCE_EVIDENCE_CLASS = "direct-search-card"
EXPECTED_DEPARTMENT_COUNTS = {
    "books": 6,
    "beauty-personal-care": 6,
    "home-kitchen": 2,
    "toys-games": 1,
    "computers": 5,
}
EXPECTED_CAPTURE_POLICY = {
    "offerRule": "A frozen card is transaction-ready only when the same visible result card exposed a non-empty USD price and an Add to cart button.",
    "selectionBoundary": "Each card establishes only its displayed default offer. No unobserved size, color, format, seller, inventory count, delivery date, list price, or variant cross-product is inferred.",
    "pdpBoundary": "These are current public search-card observations, not complete PDP captures. The clone may expose a card-evidence detail shell but must not claim a full source PDP.",
    "reviewBoundary": "ratingDisplay and reviewsDisplay preserve visible aggregate copy. Values abbreviated with K are not converted into exact review counts.",
    "sponsoredBoundary": "Sponsored status is retained as observed; it does not change the price or Add to cart evidence rule.",
}
CARD_FIELDS = frozenset(
    {
        "asin",
        "sourceIndex",
        "sourceDepartment",
        "department",
        "sponsored",
        "title",
        "slug",
        "canonicalPath",
        "format",
        "price_minor",
        "ratingDisplay",
        "reviewsDisplay",
        "reviewsExact",
        "sourceImageUrl",
        "image_path",
        "asset",
    }
)
ASSET_FIELDS = frozenset({"bytes", "width", "height", "mime", "sha256"})
ASIN_RE = re.compile(r"[A-Z0-9]{10}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REVIEW_DISPLAY_RE = re.compile(r"(?:[1-9][0-9]*|[1-9][0-9]*\.[0-9]+)K?\Z")


class SearchCommerceError(ValueError):
    pass


def _jpeg_dimensions(payload: bytes) -> tuple[int, int] | None:
    """Read JPEG SOF dimensions without adding a runtime imaging dependency."""

    if len(payload) < 4 or payload[:2] != b"\xff\xd8":
        return None
    offset = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset < len(payload):
        while offset < len(payload) and payload[offset] != 0xFF:
            offset += 1
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            break
        marker = payload[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if offset + 2 > len(payload):
            return None
        segment_length = int.from_bytes(payload[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(payload):
            return None
        if marker in sof_markers:
            if segment_length < 7:
                return None
            height = int.from_bytes(payload[offset + 3 : offset + 5], "big")
            width = int.from_bytes(payload[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _validate_sources(raw: Any) -> None:
    if not isinstance(raw, list) or len(raw) != 2:
        raise SearchCommerceError("search commerce sources must contain two captures")
    expected_departments = ("All Departments", "Computers")
    for source, department in zip(raw, expected_departments, strict=True):
        if (
            not isinstance(source, Mapping)
            or set(source) != {"query", "department", "url"}
            or source.get("query") != SEARCH_COMMERCE_QUERY
            or source.get("department") != department
        ):
            raise SearchCommerceError("search commerce source identity changed")
        parsed = urlsplit(str(source.get("url") or ""))
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"amazon.com", "www.amazon.com"}
            or parsed.path != "/s"
        ):
            raise SearchCommerceError("search commerce source URL is invalid")


def normalize_search_commerce_cards(
    payload: Mapping[str, Any], fixture_root: Path
) -> tuple[dict[str, Any], ...]:
    """Validate the immutable card capture and project only evidenced fields."""

    fixture_root = fixture_root.resolve()
    if (
        payload.get("schema") != SEARCH_COMMERCE_SCHEMA
        or payload.get("capturedAt") != SEARCH_COMMERCE_CAPTURED_AT
        or payload.get("locale") != "en-US"
        or payload.get("deliveryRegion") != "Singapore"
        or payload.get("currency") != "USD"
        or payload.get("capturePolicy") != EXPECTED_CAPTURE_POLICY
    ):
        raise SearchCommerceError("search commerce capture boundary is invalid")
    _validate_sources(payload.get("sources"))
    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or len(raw_products) != 20:
        raise SearchCommerceError("search commerce capture must contain 20 cards")

    normalized: list[dict[str, Any]] = []
    seen_asins: set[str] = set()
    seen_source_positions: set[tuple[str, int]] = set()
    static_root = (fixture_root.parent / "static").resolve()
    for raw in raw_products:
        if not isinstance(raw, Mapping) or set(raw) not in {
            CARD_FIELDS,
            CARD_FIELDS - {"format"},
        }:
            raise SearchCommerceError("search commerce card fields changed")
        asin = raw.get("asin")
        source_index = raw.get("sourceIndex")
        source_department = raw.get("sourceDepartment")
        department = raw.get("department")
        title = raw.get("title")
        slug = raw.get("slug")
        canonical_path = raw.get("canonicalPath")
        price_minor = raw.get("price_minor")
        rating_display = raw.get("ratingDisplay")
        reviews_display = raw.get("reviewsDisplay")
        reviews_exact = raw.get("reviewsExact")
        image_path = raw.get("image_path")
        asset = raw.get("asset")
        if (
            not isinstance(asin, str)
            or ASIN_RE.fullmatch(asin) is None
            or asin in seen_asins
            or isinstance(source_index, bool)
            or not isinstance(source_index, int)
            or source_index < 1
            or source_department not in {"All Departments", "Computers"}
            or (source_department, source_index) in seen_source_positions
            or department not in EXPECTED_DEPARTMENT_COUNTS
            or (department == "computers") != (source_department == "Computers")
            or not isinstance(raw.get("sponsored"), bool)
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(slug, str)
            or not slug
            or "/" in slug
            or canonical_path != f"/{slug}/dp/{asin}"
            or isinstance(price_minor, bool)
            or not isinstance(price_minor, int)
            or price_minor < 0
        ):
            raise SearchCommerceError(f"invalid search commerce identity: {asin!r}")

        product_format = raw.get("format")
        if (
            (department == "books" and (not isinstance(product_format, str) or not product_format))
            or (department != "books" and product_format is not None)
        ):
            raise SearchCommerceError(f"invalid captured format boundary: {asin}")
        try:
            rating_value = Decimal(str(rating_display))
        except InvalidOperation as exc:
            raise SearchCommerceError(f"invalid captured rating: {asin}") from exc
        if (
            not isinstance(rating_display, str)
            or not rating_value.is_finite()
            or rating_value < 0
            or rating_value > 5
            or not isinstance(reviews_display, str)
            or REVIEW_DISPLAY_RE.fullmatch(reviews_display) is None
            or (
                reviews_display.endswith("K")
                and reviews_exact is not None
            )
            or (
                not reviews_display.endswith("K")
                and (
                    isinstance(reviews_exact, bool)
                    or not isinstance(reviews_exact, int)
                    or reviews_exact <= 0
                    or str(reviews_exact) != reviews_display
                )
            )
        ):
            raise SearchCommerceError(f"invalid captured review boundary: {asin}")

        source_image = urlsplit(str(raw.get("sourceImageUrl") or ""))
        if (
            source_image.scheme != "https"
            or source_image.hostname != "m.media-amazon.com"
            or not isinstance(image_path, str)
            or image_path
            != f"/static/assets/source-current/2026-07-22/search-commerce/{asin}.jpg"
            or not isinstance(asset, Mapping)
            or set(asset) != ASSET_FIELDS
            or asset.get("mime") != "image/jpeg"
            or isinstance(asset.get("bytes"), bool)
            or not isinstance(asset.get("bytes"), int)
            or int(asset["bytes"]) <= 0
            or isinstance(asset.get("width"), bool)
            or not isinstance(asset.get("width"), int)
            or int(asset["width"]) <= 0
            or isinstance(asset.get("height"), bool)
            or not isinstance(asset.get("height"), int)
            or int(asset["height"]) <= 0
            or not isinstance(asset.get("sha256"), str)
            or SHA256_RE.fullmatch(str(asset["sha256"])) is None
        ):
            raise SearchCommerceError(f"invalid captured image metadata: {asin}")
        local_asset = (fixture_root.parent / image_path.removeprefix("/")).resolve()
        if static_root not in local_asset.parents or not local_asset.is_file():
            raise SearchCommerceError(f"search commerce image is unavailable: {asin}")
        image_bytes = local_asset.read_bytes()
        if (
            len(image_bytes) != asset["bytes"]
            or hashlib.sha256(image_bytes).hexdigest() != asset["sha256"]
            or _jpeg_dimensions(image_bytes) != (asset["width"], asset["height"])
        ):
            raise SearchCommerceError(f"search commerce image integrity failed: {asin}")

        seen_asins.add(asin)
        seen_source_positions.add((str(source_department), int(source_index)))
        normalized.append(
            {
                "asin": asin,
                "slug": slug,
                "canonical_path": canonical_path,
                "title": title,
                "brand": "",
                "capacity": "",
                "color": "",
                "price_minor": price_minor,
                "list_price_minor": None,
                "currency": "USD",
                "rating": rating_display,
                # The legacy offer table needs an integer. Zero deliberately
                # means "not exact"; presentation uses reviews_display below.
                "reviews": reviews_exact if isinstance(reviews_exact, int) else 0,
                "reviews_exact": reviews_exact,
                "reviews_display": reviews_display,
                "image_path": image_path,
                "badge": "",
                "evidence_class": SEARCH_COMMERCE_EVIDENCE_CLASS,
                "department_slugs": (department,),
                "captured_queries": (SEARCH_COMMERCE_QUERY,),
                "source_index": source_index,
                "source_department": source_department,
                "sponsored": bool(raw["sponsored"]),
                "format": product_format,
                "card_evidence_key": (
                    f"{SEARCH_COMMERCE_FIXTURE}:{source_department}:"
                    f"{source_index}:{asin}"
                ),
            }
        )

    if dict(
        Counter(product["department_slugs"][0] for product in normalized)
    ) != EXPECTED_DEPARTMENT_COUNTS:
        raise SearchCommerceError("search commerce department counts changed")
    return tuple(normalized)


def load_search_commerce_cards(fixture_root: Path) -> tuple[dict[str, Any], ...]:
    fixture_root = fixture_root.resolve()
    candidate = (fixture_root / SEARCH_COMMERCE_FIXTURE).resolve()
    if fixture_root not in candidate.parents or not candidate.is_file():
        raise SearchCommerceError("search commerce fixture is unavailable")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchCommerceError("search commerce fixture is invalid") from exc
    if not isinstance(payload, Mapping):
        raise SearchCommerceError("search commerce fixture must be an object")
    return normalize_search_commerce_cards(payload, fixture_root)
