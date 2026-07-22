from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_SCHEMA = "amazon-deals-asset-manifest.v1"
EXPECTED_FIXTURE_SCHEMA = "amazon-clone.deals-default-card-offers.v1"


class VerificationError(ValueError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"JSON root must be an object: {path}")
    return value


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise VerificationError(f"invalid relative path: {relative!r}")
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise VerificationError(f"path escapes root: {relative}")
    return target


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "materials" / "amazon" / "clone").is_dir():
            return candidate
    raise VerificationError("repository root not found")


def _avif_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 32 or data[4:8] != b"ftyp":
        raise VerificationError("asset is not an ISO BMFF image")
    first_box_size = int.from_bytes(data[0:4], "big")
    if first_box_size < 16 or first_box_size > len(data):
        raise VerificationError("asset has a malformed ftyp box")
    brands = {
        data[offset : offset + 4]
        for offset in range(8, first_box_size - 3, 4)
    }
    if not ({b"avif", b"avis"} & brands):
        raise VerificationError("asset does not advertise an AVIF brand")

    search_from = 0
    while True:
        marker = data.find(b"ispe", search_from)
        if marker < 0:
            break
        box_start = marker - 4
        if box_start >= 0:
            box_size = int.from_bytes(data[box_start:marker], "big")
            box_end = box_start + box_size
            if box_size >= 20 and box_end <= len(data):
                width = int.from_bytes(data[marker + 8 : marker + 12], "big")
                height = int.from_bytes(data[marker + 12 : marker + 16], "big")
                if width > 0 and height > 0:
                    return width, height
        search_from = marker + 4
    raise VerificationError("asset has no valid AVIF ispe dimensions")


def _verify_file(path: Path, entry: dict[str, Any], side: str) -> bytes:
    if not path.is_file():
        raise VerificationError(f"{side} asset is missing: {path}")
    data = path.read_bytes()
    if len(data) != entry.get("bytes"):
        raise VerificationError(f"{side} byte count changed: {entry['asin']}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry.get("sha256"):
        raise VerificationError(f"{side} SHA-256 changed: {entry['asin']}")
    if entry.get("mime_type") != "image/avif":
        raise VerificationError(f"manifest MIME is not AVIF: {entry['asin']}")
    dimensions = _avif_dimensions(data)
    if dimensions != (entry.get("width"), entry.get("height")):
        raise VerificationError(f"{side} dimensions changed: {entry['asin']}")
    return data


def verify() -> int:
    capture_root = Path(__file__).resolve().parent
    repo_root = _repo_root()
    manifest = _read_object(capture_root / "manifest.json")
    evidence = _read_object(capture_root / "evidence.json")
    fixture = _read_object(
        repo_root / "materials" / "amazon" / "clone" / "fixtures"
        / "deals-current-2026-07-22.json"
    )
    if manifest.get("schema") != EXPECTED_SCHEMA:
        raise VerificationError("unsupported manifest schema")
    if fixture.get("schema") != EXPECTED_FIXTURE_SCHEMA:
        raise VerificationError("unsupported runtime fixture schema")
    if evidence.get("asset_manifest") != "manifest.json":
        raise VerificationError("evidence does not bind the asset manifest")
    if evidence.get("asset_verifier") != "verify_assets.py":
        raise VerificationError("evidence does not bind the verifier")

    entries = manifest.get("assets")
    source_products = evidence.get("products")
    runtime_products = fixture.get("products")
    if (
        manifest.get("asset_count") != 10
        or not isinstance(entries, list)
        or not isinstance(source_products, list)
        or not isinstance(runtime_products, list)
        or len(entries) != 10
        or len(source_products) != 10
        or len(runtime_products) != 10
    ):
        raise VerificationError("asset/product counts do not match")

    source_by_asin = {
        product.get("asin"): product
        for product in source_products
        if isinstance(product, dict)
    }
    runtime_by_asin = {
        product.get("asin"): product
        for product in runtime_products
        if isinstance(product, dict)
    }
    expected_names: set[str] = set()
    seen: set[str] = set()
    for ordinal, raw_entry in enumerate(entries, 1):
        if not isinstance(raw_entry, dict):
            raise VerificationError("manifest asset entry must be an object")
        entry = raw_entry
        asin = entry.get("asin")
        filename = entry.get("filename")
        digest = entry.get("sha256")
        if (
            not isinstance(asin, str)
            or ASIN_RE.fullmatch(asin) is None
            or asin in seen
            or filename != f"{asin}-card.avif"
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise VerificationError(f"invalid manifest identity at position {ordinal}")
        source_product = source_by_asin.get(asin)
        runtime_product = runtime_by_asin.get(asin)
        if not isinstance(source_product, dict) or not isinstance(runtime_product, dict):
            raise VerificationError(f"asset has no matching product: {asin}")
        if source_product.get("image_source_url") != entry.get("source_url"):
            raise VerificationError(f"source image URL changed: {asin}")
        if runtime_product.get("image_path") != entry.get("runtime_asset_path"):
            raise VerificationError(f"runtime image path changed: {asin}")
        if runtime_product.get("image_sha256") != digest:
            raise VerificationError(f"runtime fixture SHA-256 changed: {asin}")

        source_path = _inside(capture_root, str(entry.get("source_asset_path") or ""))
        runtime_relative = str(entry.get("runtime_asset_path") or "")
        if not runtime_relative.startswith("/static/"):
            raise VerificationError(f"runtime path is not local static: {asin}")
        runtime_path = _inside(
            repo_root / "materials" / "amazon" / "clone" / "static",
            runtime_relative.removeprefix("/static/"),
        )
        source_data = _verify_file(source_path, entry, "source")
        runtime_data = _verify_file(runtime_path, entry, "runtime")
        if source_data != runtime_data:
            raise VerificationError(f"source/runtime bytes differ: {asin}")
        expected_names.add(filename)
        seen.add(asin)

    if set(source_by_asin) != seen or set(runtime_by_asin) != seen:
        raise VerificationError("source/runtime product ASIN sets differ")
    source_names = {
        path.name for path in (capture_root / "images").glob("*.avif") if path.is_file()
    }
    runtime_names = {
        path.name
        for path in (
            repo_root
            / "materials"
            / "amazon"
            / "clone"
            / "static"
            / "assets"
            / "source-current"
            / "2026-07-22"
            / "deals"
        ).glob("*.avif")
        if path.is_file()
    }
    if source_names != expected_names or runtime_names != expected_names:
        raise VerificationError("source/runtime asset filename sets differ")
    print(
        "verified 10 Deals AVIF assets: source/runtime bytes, MIME, "
        "dimensions, paths, and SHA-256 match"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(verify())
    except VerificationError as exc:
        print(f"Deals asset verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
