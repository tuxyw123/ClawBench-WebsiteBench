from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from deals_catalog import (
    DEALS_DEFAULT_CARDS_FIXTURE,
    load_deals_default_card_offers,
)
from search_commerce import (
    SEARCH_COMMERCE_EVIDENCE_CLASS,
    SEARCH_COMMERCE_FIXTURE,
    load_search_commerce_cards,
)


TARGET_ASIN = "B0874XN4D8"
T9_ASIN = "B0CHFSWM2P"
SHEETS_ASIN = "B01M16WBW1"
OKAPI_ASIN = "B0BG6B2D4D"
SANDISK_ASIN = "B08HN37XC1"
BOOK_ASIN = "168281808X"
BEAUTY_ASIN = "B074PVTPBW"
AILUN_ASIN = "B0BJPXXM7D"
VAULT_X_ASIN = "B071V91LGC"
UPSIMPLES_ASIN = "B0BQR2BQYZ"
INSTANT_POT_ASIN = "B00FLYWNYQ"
JANSPORT_ASIN = "B07K74LDCH"
AIR_FILTER_ASIN = "B088BZTYFP"

AVAILABLE_STATUS = "AVAILABLE"
UNAVAILABLE_SELECTION_COPY = "No verified offer for this selection"

# The retained T7 source render displays this exact Blue/1 TB price and uses
# gallery image 09 for the Blue swatch.  No child-variant ASIN was captured, so
# the transaction target below deliberately keeps variant_asin unset instead
# of inventing one.
T7_BLUE_PRICE_MINOR = 26_789
T7_BLUE_IMAGE_PATH = (
    "/static/assets/source-current/2026-07-21/pdp-t7/gallery-09.jpg"
)

# The T7 task fixture intentionally freezes only the transaction identity.  Its
# option labels are nevertheless visible in the retained source render (and in
# the current T7 PDP implementation), so keep them here instead of inventing
# variant prices or treating the labels as new commerce offers.
T7_SOURCE_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "label": "Color",
        "default": "Titan Gray",
        "options": ("Titan Gray", "Blue"),
    },
    {
        "label": "Memory Storage Capacity",
        "default": "1 TB",
        "options": ("1 TB", "2 TB", "2.1 TB", "4.0 TB"),
    },
)


def _option_names(raw_options: Any) -> tuple[str, ...]:
    if not isinstance(raw_options, list):
        return ()
    names: list[str] = []
    for raw_option in raw_options:
        if isinstance(raw_option, Mapping):
            raw_name = raw_option.get("name")
        else:
            raw_name = raw_option
        if not isinstance(raw_name, str) or not raw_name:
            continue
        if raw_name not in names:
            names.append(raw_name)
    return tuple(names)


def option_groups_from_detail(
    detail: Mapping[str, Any], product: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], ...]:
    """Normalize only option labels and values that exist in captured PDP data."""

    choice_groups = detail.get("choice_groups")
    if isinstance(choice_groups, list) and choice_groups:
        groups: list[dict[str, Any]] = []
        for raw_group in choice_groups:
            if not isinstance(raw_group, Mapping):
                continue
            label = raw_group.get("label")
            default = raw_group.get("value")
            options = _option_names(raw_group.get("options"))
            if not isinstance(label, str) or not label or not isinstance(default, str) or not default:
                continue
            if default not in options:
                options = (default, *options)
            groups.append({"label": label, "default": default, "options": options})
        return tuple(groups)

    groups = []
    primary_label = detail.get("primary_option_label", "Digital Storage Capacity")
    primary_default = detail.get("primary_option_value") or (product or {}).get("capacity")
    primary_options = _option_names(
        detail.get("primary_options", detail.get("capacity_options"))
    )
    if isinstance(primary_label, str) and primary_label and isinstance(primary_default, str) and primary_default:
        if primary_default not in primary_options:
            primary_options = (primary_default, *primary_options)
        groups.append(
            {
                "label": primary_label,
                "default": primary_default,
                "options": primary_options,
            }
        )

    secondary_label = detail.get("secondary_option_label", "Color")
    secondary_default = detail.get("secondary_option_value") or (product or {}).get("color")
    secondary_options = _option_names(detail.get("color_options"))
    if isinstance(secondary_label, str) and secondary_label and isinstance(secondary_default, str) and secondary_default:
        if secondary_default not in secondary_options:
            secondary_options = (secondary_default, *secondary_options)
        groups.append(
            {
                "label": secondary_label,
                "default": secondary_default,
                "options": secondary_options,
            }
        )
    return tuple(groups)


def load_source_option_specs(fixture_root: Path) -> dict[str, tuple[dict[str, Any], ...]]:
    """Build the option allow-list from the two retained PDP evidence fixtures."""

    specs: dict[str, tuple[dict[str, Any], ...]] = {TARGET_ASIN: T7_SOURCE_OPTIONS}
    fixture_names = ("task-frozen-900136-v1.json", "home-pdp-evidence.json")
    for fixture_name in fixture_names:
        candidate = (fixture_root / fixture_name).resolve()
        if fixture_root.resolve() not in candidate.parents or not candidate.is_file():
            raise ValueError(f"PDP option evidence fixture is unavailable: {fixture_name}")
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        products = payload.get("products")
        if not isinstance(products, list):
            raise ValueError(f"PDP option evidence fixture has no product list: {fixture_name}")
        for product in products:
            if not isinstance(product, Mapping):
                continue
            asin = product.get("asin")
            detail = product.get("pdp")
            if not isinstance(asin, str) or not isinstance(detail, Mapping):
                continue
            groups = option_groups_from_detail(detail, product)
            if groups:
                specs[asin] = groups
    return specs


def default_selection(
    spec: tuple[dict[str, Any], ...] | None,
) -> dict[str, str]:
    return {
        str(group["label"]): str(group["default"])
        for group in spec or ()
    }


def validate_selection(
    spec: tuple[dict[str, Any], ...] | None,
    selection: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return a canonical selection, rejecting every non-observed label/value."""

    defaults = default_selection(spec)
    if selection is None or not selection:
        return defaults
    if not isinstance(selection, Mapping) or set(selection) != set(defaults):
        raise ValueError("product option selection is incomplete or unsupported")
    normalized: dict[str, str] = {}
    for group in spec or ():
        label = str(group["label"])
        value = selection.get(label)
        if not isinstance(value, str) or value not in group["options"]:
            raise ValueError(f"unsupported observed option value for {label}")
        normalized[label] = value
    return normalized


def normalize_complete_selection(
    spec: tuple[dict[str, Any], ...] | None,
    selection: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Return a canonical complete selection without trusting extra fields.

    ``None`` means the server-selected defaults.  This keeps add-to-cart entry
    points that do not render option fields deterministic.  An explicitly
    supplied mapping, however, must contain every captured label exactly once;
    an empty or partial client mapping cannot silently become a default choice.
    Valid values are normalized in source group order.
    """

    defaults = default_selection(spec)
    if selection is None:
        return defaults
    if not isinstance(selection, Mapping):
        raise ValueError("product option selection must be a mapping")
    if set(selection) != set(defaults):
        raise ValueError("product option selection is incomplete or unsupported")

    normalized: dict[str, str] = {}
    for group in spec or ():
        label = str(group["label"])
        value = selection.get(label)
        if not isinstance(value, str) or value not in group["options"]:
            raise ValueError(f"unsupported observed option value for {label}")
        normalized[label] = value
    return normalized


def canonical_selection_key(selection: Mapping[str, str]) -> str:
    """Serialize a server-normalized selection into a stable line identity."""

    if not isinstance(selection, Mapping) or any(
        not isinstance(label, str) or not isinstance(value, str)
        for label, value in selection.items()
    ):
        raise ValueError("canonical selection keys require string labels and values")
    return json.dumps(
        dict(selection),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fixture_products(fixture_root: Path, fixture_name: str) -> list[dict[str, Any]]:
    candidate = (fixture_root / fixture_name).resolve()
    if fixture_root.resolve() not in candidate.parents or not candidate.is_file():
        raise ValueError(f"PDP transaction evidence fixture is unavailable: {fixture_name}")
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    products = payload.get("products")
    if not isinstance(products, list):
        raise ValueError(
            f"PDP transaction evidence fixture has no product list: {fixture_name}"
        )
    return [dict(product) for product in products if isinstance(product, Mapping)]


def _base_quote_rule(
    selection: Mapping[str, str],
    *,
    offer_asin: str,
    currency: str,
    display_availability: str,
    evidence_key: str,
) -> dict[str, Any]:
    return {
        "selected_options": dict(selection),
        "price_minor": None,
        "currency": currency,
        "image_path": None,
        "availability": AVAILABLE_STATUS,
        "display_availability": display_availability,
        "target_kind": "base-offer",
        "variant_asin": offer_asin,
        "evidence_key": evidence_key,
    }


def _captured_selection_quote_rule(
    selection: Mapping[str, str],
    *,
    price_minor: int,
    image_path: str,
    display_availability: str,
    evidence_key: str,
) -> dict[str, Any]:
    if isinstance(price_minor, bool) or not isinstance(price_minor, int) or price_minor < 0:
        raise ValueError("captured option quote price must be a non-negative integer")
    if not isinstance(image_path, str) or not image_path.startswith("/static/"):
        raise ValueError("captured option quote image must be a local static asset")
    return {
        "selected_options": dict(selection),
        "price_minor": price_minor,
        "currency": "USD",
        "image_path": image_path,
        "availability": AVAILABLE_STATUS,
        "display_availability": display_availability,
        "target_kind": "captured-selection",
        "variant_asin": None,
        "evidence_key": evidence_key,
    }


def _usd_offer_copy_to_minor(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("captured option quote is missing its USD price")
    match = re.fullmatch(r"\$(\d+)\.(\d{2})", value)
    if match is None:
        raise ValueError("captured option quote has a malformed USD price")
    return int(match.group(1)) * 100 + int(match.group(2))


def load_source_transaction_quote_specs(
    fixture_root: Path,
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Build the complete-selection transaction allow-list from source evidence.

    Axis values remain available to the PDP renderer through
    :func:`load_source_option_specs`, but this catalog contains only complete
    combinations with enough evidence to create a server-side price quote.
    Consequently, a captured label/value combination is not automatically a
    purchasable Cartesian product.
    """

    fixture_root = fixture_root.resolve()
    option_specs = load_source_option_specs(fixture_root)
    fixture_names = ("task-frozen-900136-v1.json", "home-pdp-evidence.json")
    products_by_asin: dict[str, dict[str, Any]] = {}
    source_by_asin: dict[str, str] = {}
    for fixture_name in fixture_names:
        for product in _fixture_products(fixture_root, fixture_name):
            asin = product.get("asin")
            if not isinstance(asin, str) or not asin:
                continue
            products_by_asin[asin] = product
            source_by_asin[asin] = fixture_name
    for product in load_deals_default_card_offers(fixture_root):
        asin = str(product["asin"])
        if asin in products_by_asin:
            raise ValueError(f"duplicate Deals transaction offer: {asin}")
        products_by_asin[asin] = dict(product)
        source_by_asin[asin] = DEALS_DEFAULT_CARDS_FIXTURE
    for product in load_search_commerce_cards(fixture_root):
        asin = str(product["asin"])
        if asin in products_by_asin:
            raise ValueError(f"duplicate search-card transaction offer: {asin}")
        products_by_asin[asin] = dict(product)
        source_by_asin[asin] = SEARCH_COMMERCE_FIXTURE

    rules: dict[str, list[dict[str, Any]]] = {}

    # Every verified source offer with no captured option axes has one complete
    # selection: the empty selection.  Its transaction price and image still
    # come from the caller's server-owned base offer, never from the client.
    for asin, product in products_by_asin.items():
        if option_specs.get(asin):
            continue
        currency = product.get("currency")
        if not isinstance(currency, str) or not currency:
            raise ValueError(f"base transaction evidence has no currency: {asin}")
        detail = product.get("pdp")
        captured_availability = (
            detail.get("availability") if isinstance(detail, Mapping) else None
        )
        display_availability = (
            captured_availability
            if isinstance(captured_availability, str) and captured_availability
            else (
                "Available from captured search-card offer"
                if product.get("evidence_class") == SEARCH_COMMERCE_EVIDENCE_CLASS
                else "Available from current source-backed offer"
            )
        )
        rules[asin] = [
            _base_quote_rule(
                {},
                offer_asin=asin,
                currency=currency,
                display_availability=display_availability,
                evidence_key=str(
                    product.get("card_evidence_key")
                    or f"{source_by_asin[asin]}:{asin}:base-offer"
                ),
            )
        ]

    # New direct PDP captures can carry a declarative list of complete option
    # quotes.  Keeping those quotes beside the captured choices prevents this
    # module from accumulating product-specific price tables and, more
    # importantly, makes the evidence boundary explicit: an option may be
    # browseable without becoming purchasable until a complete source quote is
    # present here.
    for asin, product in products_by_asin.items():
        detail = product.get("pdp")
        raw_quotes = detail.get("transaction_quotes") if isinstance(detail, Mapping) else None
        if raw_quotes is None:
            continue
        if not isinstance(raw_quotes, list) or not raw_quotes:
            raise ValueError(f"captured transaction quotes must be a non-empty list: {asin}")
        spec = option_specs.get(asin)
        if not spec:
            raise ValueError(f"captured transaction quotes require option evidence: {asin}")
        if product.get("currency") != "USD":
            raise ValueError(f"captured transaction quotes require a USD product: {asin}")
        default_image = product.get("image_path")
        if not isinstance(default_image, str) or not default_image.startswith("/static/"):
            raise ValueError(f"captured transaction quotes require a local image: {asin}")
        captured_rules: list[dict[str, Any]] = []
        seen_selections: set[str] = set()
        for index, raw_quote in enumerate(raw_quotes):
            if not isinstance(raw_quote, Mapping):
                raise ValueError(f"captured transaction quote must be an object: {asin}[{index}]")
            selection = normalize_complete_selection(spec, raw_quote.get("selected_options"))
            selection_key = canonical_selection_key(selection)
            if selection_key in seen_selections:
                raise ValueError(f"duplicate captured transaction quote: {asin}[{index}]")
            seen_selections.add(selection_key)
            evidence_key = raw_quote.get("evidence_key")
            if not isinstance(evidence_key, str) or not evidence_key.strip():
                raise ValueError(f"captured transaction quote requires evidence: {asin}[{index}]")
            display_availability = raw_quote.get("display_availability")
            if not isinstance(display_availability, str) or not display_availability.strip():
                display_availability = detail.get("availability")
            if not isinstance(display_availability, str) or not display_availability.strip():
                raise ValueError(f"captured transaction quote requires availability: {asin}[{index}]")
            image_path = raw_quote.get("image_path", default_image)
            captured_rules.append(
                _captured_selection_quote_rule(
                    selection,
                    price_minor=raw_quote.get("price_minor"),
                    image_path=image_path,
                    display_availability=display_availability,
                    evidence_key=evidence_key,
                )
            )
        rules[asin] = captured_rules

    required_asins = {
        TARGET_ASIN,
        T9_ASIN,
        SHEETS_ASIN,
        OKAPI_ASIN,
        SANDISK_ASIN,
        BOOK_ASIN,
        BEAUTY_ASIN,
        INSTANT_POT_ASIN,
        JANSPORT_ASIN,
        AIR_FILTER_ASIN,
    }
    missing = required_asins - products_by_asin.keys()
    if missing:
        raise ValueError(
            "required PDP transaction evidence is missing: " + ", ".join(sorted(missing))
        )

    # T7 has one default base quote plus one explicitly priced Blue/1 TB quote.
    t7_default = default_selection(option_specs[TARGET_ASIN])
    rules[TARGET_ASIN] = [
        _base_quote_rule(
            t7_default,
            offer_asin=TARGET_ASIN,
            currency="USD",
            display_availability="In Stock",
            evidence_key="task-frozen-900136-v1.json:B0874XN4D8:base-offer",
        ),
        _captured_selection_quote_rule(
            {"Color": "Blue", "Memory Storage Capacity": "1 TB"},
            price_minor=T7_BLUE_PRICE_MINOR,
            image_path=T7_BLUE_IMAGE_PATH,
            display_availability="Available at captured option price",
            evidence_key="source-current-2026-07-21:pdp-t7:Blue:1 TB",
        ),
    ]

    # T9 and the sheet set expose captured axes, but only their current default
    # selections have a complete source price/offer in the retained fixtures.
    for asin in (T9_ASIN, SHEETS_ASIN):
        product = products_by_asin[asin]
        detail = product.get("pdp")
        availability = detail.get("availability") if isinstance(detail, Mapping) else None
        rules[asin] = [
            _base_quote_rule(
                default_selection(option_specs[asin]),
                offer_asin=asin,
                currency=str(product["currency"]),
                display_availability=(
                    availability
                    if isinstance(availability, str) and availability
                    else "Available from current source-backed offer"
                ),
                evidence_key=f"{source_by_asin[asin]}:{asin}:base-offer",
            )
        ]

    # The Books PDP exposes Kindle as a visible format choice, but the source
    # handoff establishes a physical international offer only for Hardcover.
    # Keep Kindle browseable in the PDP control while intentionally omitting a
    # transaction quote for it; digital delivery must not inherit shipping or
    # delivery semantics from the hardcover offer.
    book = products_by_asin[BOOK_ASIN]
    book_detail = book.get("pdp")
    book_availability = (
        book_detail.get("availability") if isinstance(book_detail, Mapping) else None
    )
    rules[BOOK_ASIN] = [
        _base_quote_rule(
            {"Format": "Hardcover"},
            offer_asin=BOOK_ASIN,
            currency=str(book["currency"]),
            display_availability=(
                book_availability
                if isinstance(book_availability, str) and book_availability
                else "Available from current source-backed offer"
            ),
            evidence_key="home-pdp-evidence.json:168281808X:Hardcover:base-offer",
        )
    ]

    # Both Beauty size prices were captured as complete source offers.  They
    # share the base ASIN because no child ASIN was retained, so selection_key
    # remains the server-owned cart/order identity.
    beauty = products_by_asin[BEAUTY_ASIN]
    beauty_detail = beauty.get("pdp")
    beauty_availability = (
        beauty_detail.get("availability")
        if isinstance(beauty_detail, Mapping)
        else None
    )
    beauty_image = str(beauty["image_path"])
    rules[BEAUTY_ASIN] = [
        _base_quote_rule(
            {"Size": "36 Count (Pack of 1)"},
            offer_asin=BEAUTY_ASIN,
            currency=str(beauty["currency"]),
            display_availability=(
                beauty_availability
                if isinstance(beauty_availability, str) and beauty_availability
                else "Available from current source-backed offer"
            ),
            evidence_key="home-pdp-evidence.json:B074PVTPBW:36 Count (Pack of 1):base-offer",
        ),
        _captured_selection_quote_rule(
            {"Size": "75 Count"},
            price_minor=1_829,
            image_path=beauty_image,
            display_availability=(
                beauty_availability
                if isinstance(beauty_availability, str) and beauty_availability
                else "Available at captured option price"
            ),
            evidence_key="home-pdp-evidence.json:B074PVTPBW:75 Count",
        ),
    ]

    # The current SanDisk PDP explicitly prices only the three color choices
    # while Style=Old Model and Capacity=2TB are selected.  Parse those prices
    # and images from the fixture, and do not infer any other cross-product.
    sandisk = products_by_asin[SANDISK_ASIN]
    detail = sandisk.get("pdp")
    groups = detail.get("choice_groups") if isinstance(detail, Mapping) else None
    if not isinstance(groups, list):
        raise ValueError("SanDisk option quote evidence has no choice groups")
    groups_by_label = {
        group.get("label"): group
        for group in groups
        if isinstance(group, Mapping) and isinstance(group.get("label"), str)
    }
    style_group = groups_by_label.get("Style")
    capacity_group = groups_by_label.get("Capacity")
    color_group = groups_by_label.get("Color")
    if not all(isinstance(group, Mapping) for group in (style_group, capacity_group, color_group)):
        raise ValueError("SanDisk option quote evidence is incomplete")
    if style_group.get("value") != "Old Model" or capacity_group.get("value") != "2TB":
        raise ValueError("SanDisk option quote evidence changed its captured selection")
    raw_colors = color_group.get("options")
    if not isinstance(raw_colors, list):
        raise ValueError("SanDisk option quote evidence has no color prices")
    colors = {
        option.get("name"): option
        for option in raw_colors
        if isinstance(option, Mapping) and isinstance(option.get("name"), str)
    }
    expected_prices = {"Black": 31_699, "Monterey": 32_999, "Sky Blue": 32_999}
    sandisk_rules: list[dict[str, Any]] = []
    for color, expected_price in expected_prices.items():
        option = colors.get(color)
        if not isinstance(option, Mapping):
            raise ValueError(f"SanDisk option quote evidence is missing {color}")
        price_minor = _usd_offer_copy_to_minor(option.get("offer_copy"))
        if price_minor != expected_price:
            raise ValueError(f"SanDisk captured option price changed for {color}")
        image_path = option.get("image")
        sandisk_rules.append(
            _captured_selection_quote_rule(
                {"Style": "Old Model", "Capacity": "2TB", "Color": color},
                price_minor=price_minor,
                image_path=image_path,
                display_availability=(
                    "In Stock" if color == "Black" else "Available at captured option price"
                ),
                evidence_key=f"home-pdp-evidence.json:B08HN37XC1:Old Model:2TB:{color}",
            )
        )
    rules[SANDISK_ASIN] = sandisk_rules

    return {asin: tuple(entries) for asin, entries in rules.items()}


def resolve_transaction_quote(
    asin: str,
    selection: Mapping[str, Any] | None,
    *,
    option_specs: Mapping[str, tuple[dict[str, Any], ...]],
    quote_specs: Mapping[str, tuple[dict[str, Any], ...]],
    base_offer: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve one server-owned quote, or ``None`` for an unquoted combination.

    The caller must supply the verified base commerce offer retrieved on the
    server.  Client price/image/availability fields are never accepted.  A
    complete combination may use captured UI values yet still return ``None``
    when the retained evidence does not establish a transaction price.
    """

    if not isinstance(asin, str) or not asin:
        raise ValueError("transaction quote requires an ASIN")
    if not isinstance(base_offer, Mapping) or base_offer.get("asin") != asin:
        raise ValueError("transaction quote requires the matching server base offer")
    base_price = base_offer.get("price_minor")
    if isinstance(base_price, bool) or not isinstance(base_price, int) or base_price < 0:
        raise ValueError("server base offer has an invalid price")
    base_currency = base_offer.get("currency")
    if not isinstance(base_currency, str) or not base_currency:
        raise ValueError("server base offer has an invalid currency")
    base_image = base_offer.get("image_path")
    if not isinstance(base_image, str) or not base_image.startswith("/static/"):
        raise ValueError("server base offer has an invalid image")

    normalized = normalize_complete_selection(option_specs.get(asin), selection)
    matching_rule = next(
        (
            rule
            for rule in quote_specs.get(asin, ())
            if rule.get("selected_options") == normalized
        ),
        None,
    )
    if matching_rule is None:
        return None
    if matching_rule.get("currency") != base_currency:
        raise ValueError("captured option quote currency does not match the base offer")

    captured_price = matching_rule.get("price_minor")
    price_minor = base_price if captured_price is None else captured_price
    captured_image = matching_rule.get("image_path")
    image_path = base_image if captured_image is None else captured_image
    target_kind = str(matching_rule["target_kind"])
    variant_asin = matching_rule.get("variant_asin")
    return {
        "asin": asin,
        "selected_options": normalized,
        "selection_key": canonical_selection_key(normalized),
        "price_minor": price_minor,
        "currency": base_currency,
        "image_path": image_path,
        "availability": str(matching_rule["availability"]),
        "display_availability": str(matching_rule["display_availability"]),
        "transaction_target": {
            "kind": target_kind,
            "offer_asin": asin,
            "variant_asin": variant_asin,
            "selection_key": canonical_selection_key(normalized),
        },
        "evidence_key": str(matching_rule["evidence_key"]),
    }
