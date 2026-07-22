from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
HOME_ASSET_ROOT = "/static/assets/source-current/2026-07-21/home"
HOME_RAIL_FIXTURE = "home-rails.json"
HOME_PDP_EVIDENCE_FIXTURE = "home-pdp-evidence.json"


class HomeCatalogError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HomeCatalogError(f"invalid catalog fixture {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HomeCatalogError(f"catalog fixture {path.name} must be an object")
    return payload


def _title_status(title: str) -> str:
    return "home-truncated" if title.rstrip().endswith(("...", "…")) else "home-observed"


def _validate_local_path(path: Any, label: str) -> str:
    if not isinstance(path, str) or not path.startswith("/static/"):
        raise HomeCatalogError(f"{label} must be a local /static/ path")
    if path.startswith(("http://", "https://")) or "\\" in path or ".." in Path(path).parts:
        raise HomeCatalogError(f"{label} is not a normalized local path")
    return path


def load_home_product_catalog(fixture_root: Path) -> dict[str, dict[str, Any]]:
    """Merge frozen homepage placements with incrementally captured direct PDP evidence."""

    fixture_root = fixture_root.resolve()
    rails_payload = _read_json(fixture_root / HOME_RAIL_FIXTURE)
    if rails_payload.get("schema") != "amazon-home-rails-fixture.v1":
        raise HomeCatalogError("unsupported home rail schema")

    catalog: dict[str, dict[str, Any]] = {}
    rails = rails_payload.get("rails")
    if not isinstance(rails, list) or not rails:
        raise HomeCatalogError("home rails must be a non-empty array")
    for rail in rails:
        if not isinstance(rail, dict):
            raise HomeCatalogError("home rail entries must be objects")
        key = rail.get("key")
        title = rail.get("title")
        items = rail.get("items")
        if not isinstance(key, str) or not isinstance(title, str) or not isinstance(items, list):
            raise HomeCatalogError("home rail identity and items are required")
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise HomeCatalogError(f"{key}[{index}] must be an object")
            asin = item.get("asin")
            observed_title = item.get("title")
            image_relative = item.get("imagePath")
            if not isinstance(asin, str) or not ASIN_RE.fullmatch(asin):
                raise HomeCatalogError(f"{key}[{index}] has an invalid ASIN")
            if not isinstance(observed_title, str) or not observed_title.strip():
                raise HomeCatalogError(f"{key}[{index}] has no observed title")
            if not isinstance(image_relative, str) or image_relative.startswith(("/", "http://", "https://")):
                raise HomeCatalogError(f"{key}[{index}] has an invalid local image reference")
            if item.get("href") != f"/dp/{asin}":
                raise HomeCatalogError(f"{key}[{index}] does not use the canonical bare PDP route")
            placement = {
                "surface": "home",
                "railKey": key,
                "railTitle": title,
                "ordinal": int(item.get("ordinal", index)),
                "title": observed_title,
                "imagePath": f"{HOME_ASSET_ROOT}/{image_relative}",
            }
            existing = catalog.get(asin)
            if existing is None:
                catalog[asin] = {
                    "asin": asin,
                    "title": observed_title,
                    "title_status": _title_status(observed_title),
                    "image_path": placement["imagePath"],
                    "canonical_path": f"/dp/{asin}",
                    "slug": None,
                    "pdp": None,
                    "evidence_tier": "home-card-only",
                    "evidence_class": "home-card-current",
                    "placements": [placement],
                }
            else:
                existing["placements"].append(placement)

    evidence_payload = _read_json(fixture_root / HOME_PDP_EVIDENCE_FIXTURE)
    if evidence_payload.get("schema") != "amazon-clone.home-pdp-evidence.v1":
        raise HomeCatalogError("unsupported home PDP evidence schema")
    direct_products = evidence_payload.get("products")
    if not isinstance(direct_products, list):
        raise HomeCatalogError("home PDP evidence products must be an array")
    seen_direct: set[str] = set()
    for direct in direct_products:
        if not isinstance(direct, dict):
            raise HomeCatalogError("direct PDP evidence entries must be objects")
        asin = direct.get("asin")
        if not isinstance(asin, str) or asin not in catalog or asin in seen_direct:
            raise HomeCatalogError("direct PDP evidence must target one unique homepage ASIN")
        seen_direct.add(asin)
        detail = direct.get("pdp")
        if not isinstance(detail, dict) or detail.get("schema") != "amazon-clone.pdp-evidence.v1":
            raise HomeCatalogError(f"{asin} direct evidence has an invalid PDP payload")
        gallery = detail.get("gallery")
        if not isinstance(gallery, list) or not gallery:
            raise HomeCatalogError(f"{asin} direct evidence requires a gallery")
        for index, path in enumerate(gallery):
            _validate_local_path(path, f"{asin}.pdp.gallery[{index}]")
        _validate_local_path(direct.get("image_path"), f"{asin}.image_path")
        video_thumbnail = detail.get("video_thumbnail")
        if video_thumbnail is not None:
            _validate_local_path(video_thumbnail, f"{asin}.pdp.video_thumbnail")
        for optional_path_key in ("top_promo_image", "brand_logo"):
            optional_path = detail.get(optional_path_key)
            if optional_path is not None:
                _validate_local_path(optional_path, f"{asin}.pdp.{optional_path_key}")
        for group_index, group in enumerate(detail.get("choice_groups", [])):
            if not isinstance(group, dict):
                raise HomeCatalogError(f"{asin}.pdp.choice_groups[{group_index}] must be an object")
            options = group.get("options", [])
            if not isinstance(options, list):
                raise HomeCatalogError(f"{asin}.pdp.choice_groups[{group_index}].options must be an array")
            for option_index, option in enumerate(options):
                if isinstance(option, dict) and option.get("image") is not None:
                    _validate_local_path(
                        option["image"],
                        f"{asin}.pdp.choice_groups[{group_index}].options[{option_index}].image",
                    )

        placements = catalog[asin]["placements"]
        normalized = dict(direct)
        normalized["placements"] = placements
        normalized["title_status"] = direct.get("titleStatus", "pdp-complete")
        normalized["canonical_path"] = direct.get("canonicalPath", f"/dp/{asin}")
        normalized["evidence_tier"] = "pdp-direct"
        catalog[asin] = normalized

    if len(catalog) != 157:
        raise HomeCatalogError(f"home catalog expected 157 unique ASINs, found {len(catalog)}")
    return catalog
