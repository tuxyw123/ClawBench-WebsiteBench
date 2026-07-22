#!/usr/bin/env python3
"""Read-only verifier for the dated Amazon source-asset manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from PIL import Image


RESOURCE_SUFFIXES = {
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
    ".woff2",
}

EXPECTED_DESKTOP_CONTENT_HEADINGS = [
    "Get your game on",
    "Must-haves for every student",
    "Shop Fashion for less",
    "Must-have school supplies",
    "New home arrivals under $50",
    "Top categories in Kitchen appliancesTop categories in Kitchen appliances",
    "Fashion trends you like",
    "Easy updates for elevated spaces",
    "Related to items you've viewed",
    "Best Sellers in Home & Kitchen",
    "Gear up to get fit",
    "Have more fun with family",
    "Wireless Tech",
    "Gaming merchandise",
    "Top Sellers in Toys for you",
    "Best Sellers in Computers & Accessories",
    "Level up your gaming",
    "Deals on top categories",
    "Level up your beauty routine",
    "Level up your PC here",
    "Best Sellers in Books",
    "Top picks for Singapore",
    "Most-loved watches",
    "Finds for Home",
    "Transformers toys & more",
    "Discover these beauty products for you",
    "Best Sellers in Beauty & Personal Care",
]
PRESENT_PERSONALIZED_RAIL = "Related to items you've viewed"
ABSENT_PERSONALIZED_RAIL = (
    "Customers who viewed items in your browsing history also viewed"
)
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
EXPECTED_WIRELESS_TECH_ITEMS = [
    (
        "smartphones",
        "Smartphones",
        "smartphones",
        "/s?k=smartphones",
        "https://images-na.ssl-images-amazon.com/images/G/01/AmazonExports/Events/2024/BAU2024Aug/Smartphone_1x._SY116_CB566164844_.jpg",
    ),
    (
        "watches",
        "Watches",
        "smart watches",
        "/s?k=smart+watches",
        "https://images-na.ssl-images-amazon.com/images/G/01/AmazonExports/Events/2024/BAU2024Aug/Watches_1x._SY116_CB566164844_.jpg",
    ),
    (
        "headphones",
        "Headphones",
        "headphones",
        "/s?k=headphones",
        "https://images-na.ssl-images-amazon.com/images/G/01/AmazonExports/Events/2024/BAU2024Aug/Headphone_1x._SY116_CB566164844_.jpg",
    ),
    (
        "tablets",
        "Tablets",
        "tablets",
        "/s?k=tablets",
        "https://images-na.ssl-images-amazon.com/images/G/01/AmazonExports/Events/2024/BAU2024Aug/Tablet_1x._SY116_CB566164844_.jpg",
    ),
]
EXPECTED_TOP_PICKS_ASINS = [
    "B0CSD1FT18",
    "B0G1MQYHRD",
    "B00FLYWNYQ",
    "B0FTP511BW",
    "B0C9SWH3RC",
    "B0DP3JV2WB",
    "B0DXZW363G",
    "B0DKQ4RF3B",
    "B0FTPYT2H3",
    "B0DKTVC9CR",
    "B0FBPZ3RSB",
    "B0FJH6XRS3",
    "B07799WY99",
    "B003NMMVJ0",
    "B0DQPBR1RN",
    "B09BQD3YDY",
    "B0CG7DPXGW",
    "B0CLH89X2K",
    "B0FPF5QRV6",
    "B0007ZF4OA",
    "B000052Y5Q",
    "B0GT2JP76J",
    "B0FWBPFL4S",
    "B0G31J12SG",
    "B0DX2GJ1YR",
    "B0FKNGRQVR",
    "B0DK5VM9W2",
    "B0FY6T2FG6",
]
EXPECTED_TOP_PICKS_GEOMETRY = [
    (169, 200, 179, 210),
    (461, 200, 280, 210),
    (191, 200, 201, 210),
    (184, 200, 194, 210),
    (215, 200, 225, 210),
    (209, 200, 219, 210),
    (192, 200, 202, 210),
    (161, 200, 171, 210),
    (102, 200, 155, 210),
    (166, 200, 176, 210),
    (236, 200, 246, 210),
    (70, 200, 155, 210),
    (151, 200, 161, 210),
    (52, 200, 155, 210),
    (239, 200, 249, 210),
    (163, 200, 173, 210),
    (142, 200, 155, 210),
    (581, 200, 280, 210),
    (195, 200, 205, 210),
    (196, 200, 206, 210),
    (81, 200, 155, 210),
    (224, 200, 234, 210),
    (248, 200, 258, 210),
    (188, 200, 198, 210),
    (719, 200, 280, 210),
    (268, 200, 278, 210),
    (171, 200, 181, 210),
    (170, 200, 180, 210),
]
STANDARD_RAIL_SPECS = [
    (
        "Best Sellers in Home & Kitchen",
        "best-sellers-home-kitchen",
        19,
        {"left": 20, "top": 1534, "width": 960, "height": 282},
    ),
    (
        "Top Sellers in Toys for you",
        "top-sellers-toys",
        26,
        {"left": 20, "top": 2276, "width": 960, "height": 282},
    ),
    (
        "Best Sellers in Computers & Accessories",
        "best-sellers-computers-accessories",
        17,
        {"left": 20, "top": 2577, "width": 960, "height": 282},
    ),
    (
        "Best Sellers in Books",
        "best-sellers-books",
        26,
        {"left": 20, "top": 3319, "width": 960, "height": 282},
    ),
    (
        "Best Sellers in Beauty & Personal Care",
        "best-sellers-beauty-personal-care",
        16,
        {"left": 20, "top": 4362, "width": 960, "height": 282},
    ),
]


def inspect_resource(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    result: dict[str, object] = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if path.suffix.lower() == ".svg":
        root = ET.fromstring(data)
        if not root.tag.endswith("svg"):
            raise ValueError("root element is not svg")
        result.update(
            content_type="image/svg+xml",
            dimensions={
                "width": root.attrib.get("width"),
                "height": root.attrib.get("height"),
                "view_box": root.attrib.get("viewBox"),
            },
        )
        return result

    if path.suffix.lower() == ".woff2":
        if len(data) < 48 or data[:4] != b"wOF2":
            raise ValueError("invalid WOFF2 signature")
        declared_length = int.from_bytes(data[8:12], "big")
        table_count = int.from_bytes(data[12:14], "big")
        if declared_length != len(data) or table_count == 0:
            raise ValueError("invalid WOFF2 header")
        result.update(content_type="font/woff2", dimensions=None)
        return result

    with Image.open(path) as image:
        image.verify()
        image_format = image.format
    with Image.open(path) as image:
        result.update(
            content_type=Image.MIME.get(image_format),
            dimensions={"width": image.width, "height": image.height},
        )
    return result


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_clone_mirrors(
    root: Path,
    records: dict[str, dict[str, object]],
    errors: list[str],
) -> None:
    mirror_root = (
        root.parent.parent
        / "clone"
        / "static"
        / "assets"
        / "source-current"
        / root.name
    )
    for relative_path in records:
        source_path = root.joinpath(*PurePosixPath(relative_path).parts)
        mirror_path = mirror_root.joinpath(*PurePosixPath(relative_path).parts)
        if not mirror_path.is_file():
            fail(errors, f"{relative_path}: clone mirror is missing")
            continue
        if source_path.is_file() and source_path.read_bytes() != mirror_path.read_bytes():
            fail(errors, f"{relative_path}: clone mirror differs from source asset")


def validate_personalized_capture(
    root: Path, errors: list[str]
) -> dict[str, dict[str, object]]:
    capture_path = root / "home" / "personalized-rails.json"
    try:
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"personalized rail capture is invalid: {exc}")
        return {}

    if capture.get("schema") != "amazon-home-personalized-rails-capture.v1":
        fail(errors, "personalized rail capture schema is invalid")
    if capture.get("captured_at") != "2026-07-21T10:00:44.558Z":
        fail(errors, "personalized rail capture timestamp changed")
    if capture.get("source_url") != "https://www.amazon.com/":
        fail(errors, "personalized rail capture source_url must be Amazon home")
    if capture.get("remote_runtime_policy") != "forbidden":
        fail(errors, "personalized rail capture must forbid remote runtime assets")
    if capture.get("content_headings") != EXPECTED_DESKTOP_CONTENT_HEADINGS:
        fail(errors, "personalized rail capture must preserve all 27 desktop headings")

    context = capture.get("capture_context", {})
    if context.get("viewport") != {"width": 1280, "height": 720}:
        fail(errors, "personalized desktop viewport must be 1280x720")
    if context.get("document") != {"width": 1265, "height": 5610}:
        fail(errors, "personalized desktop document geometry changed")

    narrow = capture.get("narrowViewportCapture", {})
    if narrow.get("captured_at") != "2026-07-21T10:10:07.906Z":
        fail(errors, "narrow viewport capture timestamp changed")
    if narrow.get("desktop_user_agent_narrow_window") is not True:
        fail(errors, "narrow capture must be labeled as desktop-UA evidence")
    if narrow.get("viewport") != {"width": 390, "height": 844}:
        fail(errors, "narrow desktop viewport must be 390x844")
    if narrow.get("document") != {"scroll_width": 1000, "height": 5610}:
        fail(errors, "narrow desktop capture must preserve its 1000px minimum canvas")
    if narrow.get("personalized_rail") != {
        "title": PRESENT_PERSONALIZED_RAIL,
        "presence": "present",
        "rect": {"left": 20, "top": 1229, "width": 960, "height": 285},
        "item_count": 25,
    }:
        fail(errors, "narrow desktop personalized rail geometry changed")
    narrow_headings = narrow.get("observed_headings")
    if not isinstance(narrow_headings, list) or len(narrow_headings) != 22:
        fail(errors, "narrow desktop capture must preserve its 22 observed headings")
    elif not any(
        entry.get("title") == "Most-loved travel essentials"
        and entry.get("responsive_alternate") is True
        for entry in narrow_headings
        if isinstance(entry, dict)
    ):
        fail(errors, "narrow capture must retain the responsive alternate module")

    rails = capture.get("rails")
    if not isinstance(rails, list) or len(rails) != 2:
        fail(errors, "personalized rail capture must contain two presence records")
        return {}
    present, absent = rails
    if present.get("title") != PRESENT_PERSONALIZED_RAIL:
        fail(errors, "present personalized rail title changed")
    if present.get("presence") != "present":
        fail(errors, "source personalized rail must be marked present")
    if present.get("content_heading_ordinal") != 8:
        fail(errors, "present personalized rail heading ordinal must be 8")
    if present.get("rect") != {
        "top": 1229,
        "left": 20,
        "width": 1225,
        "height": 285,
    }:
        fail(errors, "desktop personalized rail geometry changed")

    items = present.get("items")
    if not isinstance(items, list) or len(items) != 25:
        fail(errors, "present personalized rail must contain exactly 25 items")
        items = []
    if present.get("item_count") != len(items):
        fail(errors, "present personalized rail item_count does not match items")

    records: dict[str, dict[str, object]] = {}
    seen_asins: set[str] = set()
    for ordinal, item in enumerate(items):
        label = f"personalized rail item {ordinal}"
        if not isinstance(item, dict):
            fail(errors, f"{label} must be an object")
            continue
        asin = item.get("asin")
        if item.get("ordinal") != ordinal:
            fail(errors, f"{label} ordinal changed")
        if not isinstance(asin, str) or not ASIN_RE.fullmatch(asin):
            fail(errors, f"{label} has an invalid ASIN")
            continue
        if asin in seen_asins:
            fail(errors, f"{label} duplicates ASIN {asin}")
        seen_asins.add(asin)
        if item.get("canonical_href") != f"/dp/{asin}":
            fail(errors, f"{label} canonical href must be /dp/{asin}")
        if not isinstance(item.get("title"), str) or not item["title"]:
            fail(errors, f"{label} title must be non-empty")

        image = item.get("image")
        if not isinstance(image, dict):
            fail(errors, f"{label} image must be an object")
            continue
        relative_path = f"home/personalized/{asin}.jpg"
        if image.get("local_relative_path") != relative_path:
            fail(errors, f"{label} must use deterministic ASIN image path")
        source_url = image.get("source_url")
        parsed = urlparse(source_url) if isinstance(source_url, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.netloc:
            fail(errors, f"{label} image source_url must be HTTPS")
        natural = image.get("natural_dimensions")
        if (
            not isinstance(natural, dict)
            or not isinstance(natural.get("width"), int)
            or not isinstance(natural.get("height"), int)
            or natural["width"] <= 0
            or natural["height"] <= 0
        ):
            fail(errors, f"{label} natural dimensions are invalid")
        card = item.get("card_dimensions")
        if (
            not isinstance(card, dict)
            or not isinstance(card.get("width"), int)
            or not isinstance(card.get("height"), int)
            or card["width"] <= 0
            or card["height"] <= 0
        ):
            fail(errors, f"{label} card dimensions are invalid")
        records[relative_path] = {
            "source_url": source_url,
            "dimensions": natural,
        }

    if absent.get("title") != ABSENT_PERSONALIZED_RAIL:
        fail(errors, "absent personalized rail title changed")
    if absent.get("presence") != "absent":
        fail(errors, "second personalized rail must be explicitly absent")
    if absent.get("item_count") != 0 or absent.get("items") != []:
        fail(errors, "absent personalized rail must not claim captured items")

    validate_clone_mirrors(root, records, errors)
    return records


def validate_wireless_tech_capture(
    root: Path, errors: list[str]
) -> dict[str, dict[str, object]]:
    capture_path = root / "home" / "wireless-tech.json"
    try:
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"Wireless Tech capture is invalid: {exc}")
        return {}

    if capture.get("schema") != "amazon-home-wireless-tech-capture.v1":
        fail(errors, "Wireless Tech capture schema is invalid")
    if capture.get("capturedAt") != "2026-07-21T10:15:04.002Z":
        fail(errors, "Wireless Tech capture timestamp changed")
    if capture.get("sourceUrl") != "https://www.amazon.com/":
        fail(errors, "Wireless Tech sourceUrl must be Amazon home")
    if capture.get("remoteRuntimePolicy") != "forbidden":
        fail(errors, "Wireless Tech capture must forbid remote runtime assets")

    context = capture.get("captureContext")
    if not isinstance(context, dict):
        fail(errors, "Wireless Tech captureContext must be an object")
        context = {}
    if context.get("viewport") != {"width": 390, "height": 844}:
        fail(errors, "Wireless Tech viewport must be 390x844")
    if context.get("desktopUserAgentNarrowWindow") is not True:
        fail(errors, "Wireless Tech capture must be labeled as desktop-UA narrow")

    card = capture.get("card")
    if not isinstance(card, dict):
        fail(errors, "Wireless Tech card must be an object")
        return {}
    if card.get("title") != "Wireless Tech":
        fail(errors, "Wireless Tech card title changed")
    if card.get("rect") != {"left": 20, "top": 3922, "width": 307, "height": 420}:
        fail(errors, "Wireless Tech card geometry changed")
    if card.get("cta") != {
        "label": "Discover more",
        "searchQuery": "electronics",
        "canonicalHref": "/s?k=electronics",
    }:
        fail(errors, "Wireless Tech CTA intent changed")

    items = card.get("items")
    if not isinstance(items, list) or len(items) != len(EXPECTED_WIRELESS_TECH_ITEMS):
        fail(errors, "Wireless Tech card must contain exactly four items")
        items = []
    if card.get("itemCount") != len(items):
        fail(errors, "Wireless Tech itemCount does not match items")

    records: dict[str, dict[str, object]] = {}
    for ordinal, item in enumerate(items):
        label = f"Wireless Tech item {ordinal}"
        if not isinstance(item, dict):
            fail(errors, f"{label} must be an object")
            continue
        expected_key, expected_label, expected_query, expected_href, expected_url = (
            EXPECTED_WIRELESS_TECH_ITEMS[ordinal]
        )
        if item.get("ordinal") != ordinal:
            fail(errors, f"{label} ordinal changed")
        if item.get("key") != expected_key or item.get("label") != expected_label:
            fail(errors, f"{label} identity changed")
        if item.get("searchQuery") != expected_query:
            fail(errors, f"{label} search query changed")
        if item.get("canonicalHref") != expected_href:
            fail(errors, f"{label} canonical href changed")
        image = item.get("image")
        if not isinstance(image, dict):
            fail(errors, f"{label} image must be an object")
            continue
        relative_path = f"home/wireless-tech/{expected_key}.jpg"
        if image.get("localRelativePath") != relative_path:
            fail(errors, f"{label} local image path changed")
        if image.get("sourceUrl") != expected_url:
            fail(errors, f"{label} source image URL changed")
        if image.get("naturalDimensions") != {"width": 186, "height": 116}:
            fail(errors, f"{label} natural dimensions changed")
        records[relative_path] = {
            "source_url": expected_url,
            "dimensions": {"width": 186, "height": 116},
        }

    validate_clone_mirrors(root, records, errors)
    return records


def validate_top_picks_capture(
    root: Path, errors: list[str]
) -> dict[str, dict[str, object]]:
    capture_path = root / "home" / "top-picks-singapore.json"
    try:
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"Top picks for Singapore capture is invalid: {exc}")
        return {}

    if capture.get("schema") != "amazon-home-top-picks-singapore-capture.v1":
        fail(errors, "Top picks for Singapore capture schema is invalid")
    if capture.get("capturedAt") is not None:
        fail(errors, "Top picks capture must not invent an unavailable timestamp")
    if capture.get("captureTimestampStatus") != "not supplied in source capture handoff":
        fail(errors, "Top picks timestamp provenance changed")
    if (
        capture.get("captureOrder")
        != "after wireless-tech capture at 2026-07-21T10:15:04.002Z"
    ):
        fail(errors, "Top picks capture-order evidence changed")
    if capture.get("sourceUrl") != "https://www.amazon.com/":
        fail(errors, "Top picks sourceUrl must be Amazon home")
    if capture.get("remoteRuntimePolicy") != "forbidden":
        fail(errors, "Top picks capture must forbid remote runtime assets")

    context = capture.get("captureContext")
    if not isinstance(context, dict):
        fail(errors, "Top picks captureContext must be an object")
        context = {}
    if context.get("viewport") != {"width": 390, "height": 844}:
        fail(errors, "Top picks viewport must be 390x844")
    if context.get("desktopUserAgentNarrowWindow") is not True:
        fail(errors, "Top picks capture must be labeled as desktop-UA narrow")

    rail = capture.get("rail")
    if not isinstance(rail, dict):
        fail(errors, "Top picks rail must be an object")
        return {}
    if rail.get("title") != "Top picks for Singapore":
        fail(errors, "Top picks rail title changed")
    if rail.get("rect") != {"left": 20, "top": 3620, "width": 960, "height": 282}:
        fail(errors, "Top picks rail geometry changed")

    items = rail.get("items")
    if not isinstance(items, list) or len(items) != len(EXPECTED_TOP_PICKS_ASINS):
        fail(errors, "Top picks rail must contain exactly 28 items")
        items = []
    if rail.get("itemCount") != len(items):
        fail(errors, "Top picks itemCount does not match items")

    records: dict[str, dict[str, object]] = {}
    observed_asins: list[str] = []
    for ordinal, item in enumerate(items):
        label = f"Top picks item {ordinal}"
        if not isinstance(item, dict):
            fail(errors, f"{label} must be an object")
            continue
        asin = item.get("asin")
        if item.get("ordinal") != ordinal:
            fail(errors, f"{label} ordinal changed")
        if not isinstance(asin, str) or not ASIN_RE.fullmatch(asin):
            fail(errors, f"{label} has an invalid ASIN")
            continue
        observed_asins.append(asin)
        if item.get("canonicalHref") != f"/dp/{asin}":
            fail(errors, f"{label} canonical href must be /dp/{asin}")
        if not isinstance(item.get("title"), str) or not item["title"]:
            fail(errors, f"{label} title must be non-empty")
        image = item.get("image")
        if not isinstance(image, dict):
            fail(errors, f"{label} image must be an object")
            continue
        relative_path = f"home/top-picks-singapore/{asin}.jpg"
        if image.get("localRelativePath") != relative_path:
            fail(errors, f"{label} must use deterministic ASIN image path")
        source_url = image.get("sourceUrl")
        parsed = urlparse(source_url) if isinstance(source_url, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.netloc:
            fail(errors, f"{label} source image URL must be HTTPS")
        expected_natural_width, expected_natural_height, expected_card_width, expected_card_height = (
            EXPECTED_TOP_PICKS_GEOMETRY[ordinal]
        )
        expected_natural = {
            "width": expected_natural_width,
            "height": expected_natural_height,
        }
        natural = image.get("naturalDimensions")
        if (
            not isinstance(natural, dict)
            or not isinstance(natural.get("width"), int)
            or not isinstance(natural.get("height"), int)
            or natural["width"] <= 0
            or natural["height"] <= 0
        ):
            fail(errors, f"{label} natural dimensions are invalid")
        elif natural != expected_natural:
            fail(errors, f"{label} natural dimensions changed")
        expected_card = {
            "width": expected_card_width,
            "height": expected_card_height,
        }
        card = item.get("cardDimensions")
        if (
            not isinstance(card, dict)
            or not isinstance(card.get("width"), int)
            or not isinstance(card.get("height"), int)
            or card["width"] <= 0
            or card["height"] <= 0
        ):
            fail(errors, f"{label} card dimensions are invalid")
        elif card != expected_card:
            fail(errors, f"{label} card dimensions changed")
        records[relative_path] = {
            "source_url": source_url,
            "dimensions": expected_natural,
        }

    if observed_asins != EXPECTED_TOP_PICKS_ASINS:
        fail(errors, "Top picks rail ASIN order changed")
    validate_clone_mirrors(root, records, errors)
    return records


def validate_standard_rails_capture(
    root: Path, errors: list[str]
) -> dict[str, dict[str, object]]:
    capture_path = root / "home" / "remaining-standard-rails-capture.json"
    try:
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"standard home rails capture is invalid: {exc}")
        return {}

    if capture.get("schema") != "amazon-home-standard-rails-capture.v1":
        fail(errors, "standard home rails capture schema is invalid")
    if capture.get("capturedAt") != "2026-07-21T10:23:13.644Z":
        fail(errors, "standard home rails capture timestamp changed")
    if capture.get("sourceUrl") != "https://www.amazon.com/":
        fail(errors, "standard home rails sourceUrl must be Amazon home")

    context = capture.get("captureContext")
    if not isinstance(context, dict):
        fail(errors, "standard home rails captureContext must be an object")
        context = {}
    if context.get("viewport") != {"width": 390, "height": 844}:
        fail(errors, "standard home rails viewport must be 390x844")
    if context.get("desktopUserAgentNarrowWindow") is not True:
        fail(errors, "standard rails capture must be labeled as desktop-UA narrow")
    if context.get("document") != {"width": 1000, "height": 5610}:
        fail(errors, "standard rails capture must preserve its 1000px document")

    rails = capture.get("rails")
    if not isinstance(rails, list) or len(rails) != len(STANDARD_RAIL_SPECS):
        fail(errors, "standard rails capture must contain exactly five rails")
        return {}

    records: dict[str, dict[str, object]] = {}
    for rail_ordinal, (rail, spec) in enumerate(zip(rails, STANDARD_RAIL_SPECS)):
        title, slug, expected_count, expected_rect = spec
        label = f"standard rail {rail_ordinal} ({title})"
        if not isinstance(rail, dict):
            fail(errors, f"{label} must be an object")
            continue
        if rail.get("title") != title:
            fail(errors, f"{label} title/order changed")
        if rail.get("present") is not True:
            fail(errors, f"{label} must be marked present")
        if rail.get("rect") != expected_rect:
            fail(errors, f"{label} geometry changed")
        items = rail.get("items")
        if not isinstance(items, list) or len(items) != expected_count:
            fail(errors, f"{label} must contain exactly {expected_count} items")
            items = []
        if rail.get("itemCount") != len(items):
            fail(errors, f"{label} itemCount does not match items")

        seen_asins: set[str] = set()
        for item_ordinal, item in enumerate(items):
            item_label = f"{label} item {item_ordinal}"
            if not isinstance(item, dict):
                fail(errors, f"{item_label} must be an object")
                continue
            asin = item.get("asin")
            if item.get("ordinal") != item_ordinal:
                fail(errors, f"{item_label} ordinal changed")
            if not isinstance(asin, str) or not ASIN_RE.fullmatch(asin):
                fail(errors, f"{item_label} has an invalid ASIN")
                continue
            if asin in seen_asins:
                fail(errors, f"{item_label} duplicates ASIN {asin}")
            seen_asins.add(asin)
            if item.get("canonicalHref") != f"/dp/{asin}":
                fail(errors, f"{item_label} canonical href must be /dp/{asin}")
            source_href = item.get("sourceHref")
            if not isinstance(source_href, str) or f"/dp/{asin}" not in source_href:
                fail(errors, f"{item_label} source href does not identify its ASIN")
            if not isinstance(item.get("title"), str) or not item["title"]:
                fail(errors, f"{item_label} title must be non-empty")

            image = item.get("image")
            if not isinstance(image, dict):
                fail(errors, f"{item_label} image must be an object")
                continue
            source_url = image.get("sourceUrl")
            parsed = urlparse(source_url) if isinstance(source_url, str) else None
            if parsed is None or parsed.scheme != "https" or not parsed.netloc:
                fail(errors, f"{item_label} source image URL must be HTTPS")
            natural = image.get("naturalDimensions")
            if (
                not isinstance(natural, dict)
                or not isinstance(natural.get("width"), int)
                or not isinstance(natural.get("height"), int)
                or natural["width"] <= 0
                or natural["height"] <= 0
            ):
                fail(errors, f"{item_label} natural dimensions are invalid")
            card = item.get("cardDimensions")
            if (
                not isinstance(card, dict)
                or not isinstance(card.get("width"), int)
                or not isinstance(card.get("height"), int)
                or card["width"] <= 0
                or card["height"] <= 0
            ):
                fail(errors, f"{item_label} card dimensions are invalid")
            relative_path = f"home/rails/{slug}/{asin}.jpg"
            records[relative_path] = {
                "source_url": source_url,
                "dimensions": natural,
            }

    if len(records) != 104:
        fail(errors, f"standard rails evidence resolved {len(records)} assets, expected 104")
    validate_clone_mirrors(root, records, errors)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("manifest.json"),
    )
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    root = manifest_path.parent
    errors: list[str] = []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read manifest: {exc}", file=sys.stderr)
        return 1

    if manifest.get("remote_runtime_policy") != "forbidden":
        fail(errors, "remote_runtime_policy must be 'forbidden'")

    personalized_records = validate_personalized_capture(root, errors)
    wireless_records = validate_wireless_tech_capture(root, errors)
    top_picks_records = validate_top_picks_capture(root, errors)
    standard_rail_records = validate_standard_rails_capture(root, errors)
    evidence_records = {
        **personalized_records,
        **wireless_records,
        **top_picks_records,
        **standard_rail_records,
    }
    if len(evidence_records) != sum(
        map(
            len,
            (
                personalized_records,
                wireless_records,
                top_picks_records,
                standard_rail_records,
            ),
        )
    ):
        fail(errors, "home item-level evidence asset paths overlap")

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        print("ERROR: assets must be an array", file=sys.stderr)
        return 1

    seen: set[str] = set()
    computed_corrupt: list[str] = []
    missing: list[str] = []
    home_records: dict[str, str] = {}
    home_failures: set[str] = set()
    home_manifest_path = root / "home" / "manifest.json"
    try:
        home_manifest = json.loads(home_manifest_path.read_text(encoding="utf-8"))
        home_records = {
            f"home/{entry['id']}.{Path(entry['path']).suffix.lstrip('.').lower()}": entry["url"]
            for entry in home_manifest.get("assets", [])
        }
        home_failures = {
            f"home/{entry['id']}.{Path(entry['name']).suffix.lstrip('.').lower()}"
            for entry in home_manifest.get("failures", [])
        }
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        fail(errors, f"home provenance manifest is invalid: {exc}")

    for index, asset in enumerate(assets):
        label = f"assets[{index}]"
        if not isinstance(asset, dict):
            fail(errors, f"{label} must be an object")
            continue
        relative_path = asset.get("relative_path")
        if not isinstance(relative_path, str):
            fail(errors, f"{label}.relative_path must be a string")
            continue
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts or "\\" in relative_path:
            fail(errors, f"{relative_path}: path must be normalized and relative")
            continue
        if relative_path in seen:
            fail(errors, f"{relative_path}: duplicate manifest entry")
            continue
        seen.add(relative_path)

        if asset.get("evidence_level") != "current-direct":
            fail(errors, f"{relative_path}: evidence_level must be current-direct")
        source_url = asset.get("source_url")
        if source_url is not None:
            parsed = urlparse(source_url)
            if parsed.scheme != "https" or not parsed.netloc:
                fail(errors, f"{relative_path}: source_url must be an HTTPS URL or null")
        if asset.get("corrupt") is not False:
            fail(errors, f"{relative_path}: manifest corrupt flag must be false")

        path = root.joinpath(*pure_path.parts)
        if not path.is_file():
            missing.append(relative_path)
            continue
        try:
            observed = inspect_resource(path)
        except Exception as exc:  # Pillow/XML expose format-specific exceptions.
            computed_corrupt.append(relative_path)
            fail(errors, f"{relative_path}: resource cannot be decoded: {exc}")
            continue
        for field in ("bytes", "sha256", "content_type", "dimensions"):
            if asset.get(field) != observed.get(field):
                fail(
                    errors,
                    f"{relative_path}: {field} mismatch; "
                    f"manifest={asset.get(field)!r}, observed={observed.get(field)!r}",
                )

        evidence_record = evidence_records.get(relative_path)
        if evidence_record is not None:
            if asset.get("source_url") != evidence_record.get("source_url"):
                fail(errors, f"{relative_path}: source_url differs from rail evidence")
            if observed.get("dimensions") != evidence_record.get("dimensions"):
                fail(errors, f"{relative_path}: dimensions differ from rail evidence")

        if relative_path.startswith("home/"):
            if relative_path in home_failures:
                fail(errors, f"{relative_path}: failed home-bundle item must be excluded")
            expected_url = home_records.get(relative_path)
            if expected_url is None:
                fail(errors, f"{relative_path}: absent from home/manifest.json successes")
            elif source_url != expected_url:
                fail(errors, f"{relative_path}: source_url differs from home/manifest.json")

    disk_assets = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in RESOURCE_SUFFIXES
    }
    if seen != disk_assets:
        for relative_path in sorted(disk_assets - seen):
            fail(errors, f"{relative_path}: resource exists but is absent from manifest")
        for relative_path in sorted(seen - disk_assets):
            if relative_path not in missing:
                fail(errors, f"{relative_path}: manifest entry has no resource file")

    manifested_personalized = {
        relative_path
        for relative_path in seen
        if relative_path.startswith("home/personalized/")
    }
    if manifested_personalized != set(personalized_records):
        fail(errors, "personalized rail evidence and manifest asset sets differ")
    manifested_wireless = {
        relative_path
        for relative_path in seen
        if relative_path.startswith("home/wireless-tech/")
    }
    if manifested_wireless != set(wireless_records):
        fail(errors, "Wireless Tech evidence and manifest asset sets differ")
    manifested_top_picks = {
        relative_path
        for relative_path in seen
        if relative_path.startswith("home/top-picks-singapore/")
    }
    if manifested_top_picks != set(top_picks_records):
        fail(errors, "Top picks evidence and manifest asset sets differ")
    manifested_standard_rails = {
        relative_path
        for relative_path in seen
        if relative_path.startswith("home/rails/")
    }
    if manifested_standard_rails != set(standard_rail_records):
        fail(errors, "standard rail evidence and manifest asset sets differ")

    summary = manifest.get("p0_summary", {})
    expected = summary.get("expected")
    downloaded = len(assets) - len(missing)
    if expected != len(assets):
        fail(errors, f"p0_summary.expected={expected!r}, but assets has {len(assets)} entries")
    if summary.get("downloaded") != downloaded:
        fail(errors, "p0_summary.downloaded does not match files on disk")
    if summary.get("missing") != missing:
        fail(errors, "p0_summary.missing does not match files on disk")
    if summary.get("corrupt") != computed_corrupt:
        fail(errors, "p0_summary.corrupt does not match decoded files")

    groups = manifest.get("expected_p0_resources", [])
    if sum(group.get("expected", 0) for group in groups) != expected:
        fail(errors, "expected_p0_resources counts do not sum to p0_summary.expected")
    for group in groups:
        prefix = f"{group.get('group')}/"
        actual = sum(relative_path.startswith(prefix) for relative_path in seen)
        if group.get("downloaded") != actual or group.get("expected") != actual:
            fail(errors, f"{group.get('group')}: group count does not match assets")
        if group.get("missing") != [] or group.get("corrupt") != []:
            fail(errors, f"{group.get('group')}: manifest claims an incomplete P0 group")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(assets)} P0 assets; downloaded={downloaded}; "
        "missing=0; corrupt=0; remote_runtime_policy=forbidden"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
