#!/usr/bin/env python3
"""Read-only, local checks for the Amazon offline-clone harness adapter.

This adapter deliberately validates a bounded shopping-mainline claim.  It does
not turn fixture or test counts into a claim of Amazon whole-site fidelity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageChops, ImageStat


STABLE_ID = re.compile(r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$")
PRIORITIES = {"p0", "p1", "p2", "omit"}
EVIDENCE_KINDS = {
    "current-direct",
    "current-partial",
    "historical",
    "inferred",
    "unavailable",
}
VERIFICATION_KINDS = {
    "fixture-validated",
    "local-tested",
    "manifest-verified",
    "scope-decision",
}
EXPECTED_COVERAGE = {
    "scoped-source-asset-records": 452,
    "required-runtime-asset-paths": 454,
    "p0-network-invariants": 1,
    "p0-browser-invariants": 3,
    "p0-migration-invariants": 1,
    "p0-full-suite-invariants": 1,
    "known-products": 191,
    "reachable-products": 191,
    "rich-pdp-products": 14,
    "purchasable-products": 49,
    "review-backed-products": 13,
    "comparable-products": 39,
    "source-direct-pdp-products": 11,
    "frontend-checkpoints": 16,
    "frontend-current-direct-checkpoints": 2,
    "frontend-current-partial-checkpoints": 7,
    "frontend-inferred-checkpoints": 6,
    "frontend-unavailable-checkpoints": 1,
    "core-journeys": 3,
}
FROZEN_INVARIANT_IDS = {
    "assets.local-only.closed",
    "variants.complete-quote.identity",
    "identity.verification.recovery",
    "checkout.retry.idempotent",
    "orders.lifecycle.monotone",
    "migration.copy-only",
    "evidence.denominators.separate",
    "scope.shopping-mainline.bounded",
}


class GateFailure(ValueError):
    pass


def _strict_json_value(text: str, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise GateFailure(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise GateFailure(f"invalid JSON in {label}: {exc}") from exc


def read_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateFailure(f"invalid JSON: {path}: {exc}") from exc
    value = _strict_json_value(text, str(path))
    if not isinstance(value, dict):
        raise GateFailure(f"JSON root must be an object: {path}")
    return value


def inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise GateFailure(f"invalid relative evidence path: {relative!r}")
    target = (root / relative.split("#", 1)[0]).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise GateFailure(f"evidence path escapes site root: {relative}")
    return target


def stable_id(value: object, label: str) -> str:
    if not isinstance(value, str) or STABLE_ID.fullmatch(value) is None:
        raise GateFailure(f"{label} is not a stable lowercase ID: {value!r}")
    return value


def validate_source(site_root: Path) -> None:
    scope_root = site_root / "scope"
    routes = read_object(scope_root / "routes.json")
    journeys = read_object(scope_root / "journeys.json")
    checkpoints = read_object(scope_root / "checkpoints.json")
    coverage = read_object(scope_root / "coverage.json")
    purpose = read_object(scope_root / "purpose.json")
    invariants = read_object(scope_root / "invariants.json")

    if purpose.get("schema_version") != "offline-clone.purpose.v1":
        raise GateFailure("purpose schema changed")
    if purpose.get("status") != "frozen":
        raise GateFailure("shopping-mainline purpose must be frozen")
    if purpose.get("purpose_id") != "amazon.shopping-mainline":
        raise GateFailure("shopping-mainline purpose changed")
    statement = purpose.get("statement")
    if not isinstance(statement, str) or "denies whole-marketplace or whole-site fidelity" not in statement:
        raise GateFailure("purpose must explicitly deny whole-site fidelity")
    if set(purpose.get("primary_actor_ids", [])) != {
        "shopper.anonymous",
        "shopper.verified-local",
    }:
        raise GateFailure("purpose actor set changed")
    if set(purpose.get("mainline_journey_ids", [])) != {
        "shopping.success",
        "shopping.failure",
        "shopping.recovery",
    }:
        raise GateFailure("purpose mainline journey set changed")
    if not isinstance(purpose.get("out_of_scope"), list) or len(purpose["out_of_scope"]) < 5:
        raise GateFailure("purpose out-of-scope boundary is unexpectedly shallow")

    if invariants.get("schema_version") != "offline-clone.invariants.v1":
        raise GateFailure("invariants schema changed")
    if invariants.get("status") != "frozen":
        raise GateFailure("shopping-mainline invariants must be frozen")
    invariant_rows = invariants.get("invariants")
    if not isinstance(invariant_rows, list) or len(invariant_rows) < 8:
        raise GateFailure("shopping-mainline invariants are unexpectedly shallow")
    invariant_ids: set[str] = set()
    for index, row in enumerate(invariant_rows):
        if not isinstance(row, dict):
            raise GateFailure(f"invariant {index} is not an object")
        invariant_id = stable_id(row.get("id"), f"invariant {index} id")
        if invariant_id in invariant_ids:
            raise GateFailure(f"duplicate invariant id: {invariant_id}")
        invariant_ids.add(invariant_id)
        if row.get("priority") not in {"p0", "p1", "p2"}:
            raise GateFailure(f"invariant {invariant_id} has invalid priority")
        if not isinstance(row.get("statement"), str) or len(row["statement"].strip()) < 30:
            raise GateFailure(f"invariant {invariant_id} has no substantive statement")
        if row.get("priority") == "p0":
            for field in ("positive_test_refs", "negative_test_refs"):
                references = row.get(field)
                if not isinstance(references, list) or not references:
                    raise GateFailure(f"P0 invariant {invariant_id} has no {field}")
                for reference in references:
                    if not isinstance(reference, str) or re.fullmatch(
                        r"clone/tests/test_[a-z0-9_]+\.py::[A-Za-z0-9_]+\.test_[a-z0-9_]+",
                        reference,
                    ) is None:
                        raise GateFailure(
                            f"P0 invariant {invariant_id} has malformed {field}: {reference!r}"
                        )
    if invariant_ids != FROZEN_INVARIANT_IDS:
        raise GateFailure("the exact frozen invariant ID set changed")

    if routes.get("schema_version") != "offline-clone.routes.v1":
        raise GateFailure("route schema changed")
    route_rows = routes.get("routes")
    if not isinstance(route_rows, list) or not route_rows:
        raise GateFailure("route matrix is empty")
    route_ids: set[str] = set()
    for index, row in enumerate(route_rows):
        if not isinstance(row, dict):
            raise GateFailure(f"route {index} is not an object")
        route_id = stable_id(row.get("id"), f"route {index} id")
        if route_id in route_ids:
            raise GateFailure(f"duplicate route id: {route_id}")
        route_ids.add(route_id)
        if row.get("priority") not in PRIORITIES:
            raise GateFailure(f"route {route_id} has no valid priority")
        if row.get("evidence_kind") not in EVIDENCE_KINDS:
            raise GateFailure(f"route {route_id} has no honest evidence kind")
        if row.get("verification_kind") not in VERIFICATION_KINDS:
            raise GateFailure(f"route {route_id} has no separate verification kind")
        if not isinstance(row.get("states"), list) or not row["states"]:
            raise GateFailure(f"route {route_id} has no state rows")
        if row.get("priority") in {"p0", "p1"} and not row.get("local_destination"):
            raise GateFailure(f"route {route_id} has no meaningful local destination")

    if journeys.get("schema_version") != "offline-clone.journeys.v1":
        raise GateFailure("journey schema changed")
    journey_rows = journeys.get("journeys")
    if not isinstance(journey_rows, list):
        raise GateFailure("journeys must be a list")
    by_kind = {
        row.get("kind"): row
        for row in journey_rows
        if isinstance(row, dict) and row.get("priority") == "p0"
    }
    if set(by_kind) != {"success", "failure", "recovery"}:
        raise GateFailure("P0 journeys must contain success, failure, and recovery")
    for kind, row in by_kind.items():
        stable_id(row.get("id"), f"{kind} journey id")
        if row.get("status") != "frozen":
            raise GateFailure(f"{kind} journey denominator is not frozen")
        steps = row.get("steps")
        if not isinstance(steps, list) or len(steps) < 3:
            raise GateFailure(f"{kind} journey is too shallow")

    if checkpoints.get("schema_version") != "offline-clone.checkpoints.v1":
        raise GateFailure("checkpoint schema changed")
    if checkpoints.get("status") != "frozen":
        raise GateFailure("checkpoint oracle must be frozen")
    viewports = checkpoints.get("viewports")
    if not isinstance(viewports, dict):
        raise GateFailure("viewports must be an object")
    for required in ("desktop", "mobile", "tablet"):
        viewport = viewports.get(required)
        if not isinstance(viewport, dict) or not all(
            isinstance(viewport.get(axis), int) and viewport[axis] > 0
            for axis in ("width", "height")
        ):
            raise GateFailure(f"missing or invalid {required} viewport")
    checkpoint_rows = checkpoints.get("checkpoints")
    if not isinstance(checkpoint_rows, list) or len(checkpoint_rows) != 16:
        raise GateFailure("the frozen frontend matrix must contain 16 checkpoints")
    checkpoint_ids: set[str] = set()
    visual_contract_ids: set[str] = set()
    for row in checkpoint_rows:
        if not isinstance(row, dict):
            raise GateFailure("checkpoint row must be an object")
        checkpoint_id = stable_id(row.get("id"), "checkpoint id")
        if checkpoint_id in checkpoint_ids:
            raise GateFailure(f"duplicate checkpoint id: {checkpoint_id}")
        checkpoint_ids.add(checkpoint_id)
        if row.get("route_id") not in route_ids:
            raise GateFailure(f"checkpoint {checkpoint_id} references an unknown route")
        if row.get("viewport") not in viewports:
            raise GateFailure(f"checkpoint {checkpoint_id} references an unknown viewport")
        if row.get("priority") not in {"p0", "p1", "p2"}:
            raise GateFailure(f"checkpoint {checkpoint_id} has an invalid priority")
        if row.get("evidence_kind") not in EVIDENCE_KINDS:
            raise GateFailure(f"checkpoint {checkpoint_id} has no honest evidence kind")
        if row.get("verification_kind") not in VERIFICATION_KINDS:
            raise GateFailure(
                f"checkpoint {checkpoint_id} has no separate verification kind"
            )
        visual_contract = row.get("visual_contract")
        if row.get("evidence_kind") != "current-direct":
            if visual_contract is not None:
                raise GateFailure(
                    f"non-direct checkpoint {checkpoint_id} must not define a visual oracle"
                )
            continue
        visual_contract_ids.add(checkpoint_id)
        if not isinstance(visual_contract, dict) or set(visual_contract) != {
            "source_artifact_path",
            "source_artifact_sha256",
            "viewport",
            "comparison_region",
            "metric",
            "threshold",
        }:
            raise GateFailure(f"checkpoint {checkpoint_id} has an invalid visual contract")
        source_relative = visual_contract.get("source_artifact_path")
        if not isinstance(source_relative, str) or not source_relative.startswith(
            "source-assets/"
        ):
            raise GateFailure(f"checkpoint {checkpoint_id} has an invalid source golden")
        source_path = inside(site_root, source_relative)
        if not source_path.is_file() or source_path.is_symlink():
            raise GateFailure(f"checkpoint {checkpoint_id} source golden is unavailable")
        source_sha256 = visual_contract.get("source_artifact_sha256")
        if not isinstance(source_sha256, str) or re.fullmatch(
            r"[a-f0-9]{64}", source_sha256
        ) is None:
            raise GateFailure(f"checkpoint {checkpoint_id} source digest is invalid")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != source_sha256:
            raise GateFailure(f"checkpoint {checkpoint_id} source digest changed")
        viewport = visual_contract.get("viewport")
        region = visual_contract.get("comparison_region")
        if (
            not isinstance(viewport, dict)
            or set(viewport) != {"width", "height"}
            or any(type(viewport.get(axis)) is not int or viewport[axis] < 1 for axis in viewport)
            or not isinstance(region, dict)
            or set(region) != {"x", "y", "width", "height"}
            or any(type(region.get(axis)) is not int for axis in region)
            or region["x"] < 0
            or region["y"] < 0
            or region["width"] < 1
            or region["height"] < 1
            or region["x"] + region["width"] > viewport["width"]
            or region["y"] + region["height"] > viewport["height"]
        ):
            raise GateFailure(f"checkpoint {checkpoint_id} visual geometry is invalid")
        try:
            with Image.open(source_path) as source_image:
                source_size = source_image.size
                source_image.verify()
        except Exception as exc:
            raise GateFailure(
                f"checkpoint {checkpoint_id} source golden is not a valid image: {exc}"
            ) from exc
        if source_size != (viewport["width"], viewport["height"]):
            raise GateFailure(
                f"checkpoint {checkpoint_id} source raster {source_size} does not match "
                f"its frozen viewport"
            )
        if visual_contract.get("metric") != "pixel-mae-similarity-v1":
            raise GateFailure(f"checkpoint {checkpoint_id} visual metric changed")
        threshold = visual_contract.get("threshold")
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not 0 < float(threshold) <= 1
        ):
            raise GateFailure(f"checkpoint {checkpoint_id} visual threshold is invalid")
    if visual_contract_ids != {"home.desktop.loaded", "search.desktop.filtered"}:
        raise GateFailure("the current-direct visual oracle set changed")

    if coverage.get("schema_version") != "offline-clone.coverage.v1":
        raise GateFailure("coverage schema changed")
    if coverage.get("status") != "frozen":
        raise GateFailure("coverage ledger must be frozen before implementation gates")
    dimensions = coverage.get("dimensions")
    if not isinstance(dimensions, list):
        raise GateFailure("coverage dimensions must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for row in dimensions:
        if not isinstance(row, dict):
            raise GateFailure("coverage dimension must be an object")
        dimension_id = stable_id(row.get("id"), "coverage dimension id")
        if dimension_id in by_id:
            raise GateFailure(f"duplicate coverage dimension: {dimension_id}")
        by_id[dimension_id] = row
    if set(by_id) != set(EXPECTED_COVERAGE):
        raise GateFailure("coverage denominators changed or were merged")
    for dimension_id, expected in EXPECTED_COVERAGE.items():
        row = by_id[dimension_id]
        required = row.get("required_items")
        satisfied = row.get("satisfied_items")
        if not isinstance(required, list) or not isinstance(satisfied, list):
            raise GateFailure(f"coverage {dimension_id} has invalid item lists")
        if len(required) != expected:
            raise GateFailure(
                f"coverage {dimension_id} must retain {expected} required items, "
                f"got {len(required)}"
            )
        if satisfied:
            raise GateFailure(
                f"source-stage coverage {dimension_id} must not contain implementation numerators"
            )
        for item in required:
            stable_id(item, f"coverage {dimension_id} item")
    if set(by_id["frontend-checkpoints"]["required_items"]) != checkpoint_ids:
        raise GateFailure("frontend coverage does not match the checkpoint matrix")
    checkpoint_buckets = {
        "frontend-current-direct-checkpoints": "current-direct",
        "frontend-current-partial-checkpoints": "current-partial",
        "frontend-inferred-checkpoints": "inferred",
        "frontend-unavailable-checkpoints": "unavailable",
    }
    for dimension_id, evidence_kind in checkpoint_buckets.items():
        expected_items = {
            row["id"]
            for row in checkpoint_rows
            if row.get("evidence_kind") == evidence_kind
        }
        if set(by_id[dimension_id]["required_items"]) != expected_items:
            raise GateFailure(
                f"{dimension_id} does not match checkpoint evidence classifications"
            )
    if set(by_id["core-journeys"]["required_items"]) != {
        row["id"] for row in journey_rows if row.get("priority") == "p0"
    }:
        raise GateFailure("journey coverage does not match P0 journeys")

    asset_manifest = read_object(
        site_root / "source-assets" / "offline-clone-manifest.json"
    )
    asset_rows = asset_manifest.get("assets")
    if not isinstance(asset_rows, list):
        raise GateFailure("asset manifest rows are unavailable to source coverage")
    scoped_asset_ids = {
        str(row.get("id"))
        for row in asset_rows
        if isinstance(row, dict)
        and not str(row.get("id", "")).startswith("runtime-alias.")
    }
    required_runtime_ids = {
        str(row.get("id"))
        for row in asset_rows
        if isinstance(row, dict) and row.get("required") is True
    }
    expected_asset_dimensions = {
        "scoped-source-asset-records": scoped_asset_ids,
        "required-runtime-asset-paths": required_runtime_ids,
    }
    for dimension_id, expected_items in expected_asset_dimensions.items():
        if set(by_id[dimension_id]["required_items"]) != expected_items:
            raise GateFailure(f"{dimension_id} does not match its machine inventory")
    p0_invariant_ids = {
        row["id"]
        for row in invariant_rows
        if isinstance(row, dict) and row.get("priority") == "p0"
    }
    covered_p0_invariants = set().union(
        *(
            set(by_id[dimension_id]["required_items"])
            for dimension_id in (
                "p0-network-invariants",
                "p0-browser-invariants",
                "p0-migration-invariants",
                "p0-full-suite-invariants",
            )
        )
    )
    if covered_p0_invariants != p0_invariant_ids:
        raise GateFailure("P0 invariant coverage does not match the frozen invariant ledger")

    claims_path = scope_root / "claims.jsonl"
    claim_ids: set[str] = set()
    line_count = 0
    for line_number, raw_line in enumerate(
        claims_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw_line.strip():
            continue
        line_count += 1
        claim = _strict_json_value(raw_line, f"claim line {line_number}")
        if not isinstance(claim, dict):
            raise GateFailure(f"claim line {line_number} is not an object")
        claim_id = stable_id(claim.get("id"), f"claim line {line_number} id")
        if claim_id in claim_ids:
            raise GateFailure(f"duplicate claim id: {claim_id}")
        claim_ids.add(claim_id)
        if claim.get("priority") not in PRIORITIES:
            raise GateFailure(f"claim {claim_id} has an invalid priority")
        if claim.get("evidence_kind") not in EVIDENCE_KINDS:
            raise GateFailure(f"claim {claim_id} has an invalid evidence kind")
        if claim.get("verification_kind") not in VERIFICATION_KINDS:
            raise GateFailure(f"claim {claim_id} has no separate verification kind")
        references = claim.get("evidence_refs")
        if not isinstance(references, list) or not references:
            raise GateFailure(f"claim {claim_id} has no evidence references")
        for reference in references:
            evidence_path = inside(site_root, reference)
            if not evidence_path.is_file():
                raise GateFailure(f"claim {claim_id} evidence is missing: {reference}")
        if claim.get("evidence_kind") != "current-direct" and not claim.get("limitations"):
            raise GateFailure(f"non-direct claim {claim_id} has no limitation")
    if line_count < 10:
        raise GateFailure("claim ledger is unexpectedly shallow")
    print(
        f"verified shopping-mainline scope: {len(route_ids)} routes, "
        f"{len(checkpoint_ids)} checkpoints, 3 P0 journeys, "
        f"{len(by_id)} separate denominators, and {line_count} evidence claims"
    )


def inspect_jpeg(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            mime = Image.MIME.get(image.format or "")
    except Exception as exc:
        raise GateFailure(f"invalid image {path}: {exc}") from exc
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": width,
        "height": height,
        "mime": mime,
    }


def verify_search_backfill(site_root: Path) -> None:
    fixture = read_object(
        site_root / "clone" / "fixtures" / "search-commerce-current-2026-07-22.json"
    )
    if fixture.get("schema") != "amazon-clone.search-commerce-cards.v1":
        raise GateFailure("search-card fixture schema changed")
    products = fixture.get("products")
    if not isinstance(products, list) or len(products) != 20:
        raise GateFailure("search-card fixture must contain 20 bounded cards")
    source_root = site_root / "source-assets" / "2026-07-22" / "search-commerce"
    runtime_root = (
        site_root
        / "clone"
        / "static"
        / "assets"
        / "source-current"
        / "2026-07-22"
        / "search-commerce"
    )
    expected_names: set[str] = set()
    for product in products:
        if not isinstance(product, dict):
            raise GateFailure("search-card product is not an object")
        asin = product.get("asin")
        asset = product.get("asset")
        if not isinstance(asin, str) or not isinstance(asset, dict):
            raise GateFailure("search-card asset identity is invalid")
        name = f"{asin}.jpg"
        expected_names.add(name)
        observed = []
        for side, root in (("source", source_root), ("runtime", runtime_root)):
            path = root / name
            if not path.is_file():
                raise GateFailure(f"{side} search-card mirror is missing: {name}")
            info = inspect_jpeg(path)
            expected = {
                "bytes": asset.get("bytes"),
                "sha256": asset.get("sha256"),
                "width": asset.get("width"),
                "height": asset.get("height"),
                "mime": asset.get("mime"),
            }
            if info != expected:
                raise GateFailure(f"{side} search-card metadata changed: {asin}")
            observed.append(path.read_bytes())
        if observed[0] != observed[1]:
            raise GateFailure(f"source/runtime search-card bytes differ: {asin}")
    for root in (source_root, runtime_root):
        actual = {path.name for path in root.glob("*.jpg") if path.is_file()}
        if actual != expected_names:
            raise GateFailure(f"search-card mirror file set changed: {root}")
    if not (source_root / "README.md").is_file():
        raise GateFailure("search-card storage-backfill boundary is undocumented")
    print(
        "verified 20 bounded search-card image mirrors against captured fixture "
        "bytes, SHA-256, MIME, and dimensions"
    )


RUNTIME_ASSET_REFERENCE = re.compile(
    r"/static/assets/[A-Za-z0-9_./+\-]+\.(?:avif|gif|jpe?g|png|svg|webp|woff2)",
    re.I,
)


def parser_proven_runtime_refs(site_root: Path) -> set[str]:
    """Return concrete runtime URL literals parsed only from production code."""

    clone_root = site_root / "clone"
    static_root = clone_root / "static"
    production_text = [
        *sorted(clone_root.glob("*.py")),
        *sorted(static_root.glob("*.css")),
        *sorted(static_root.glob("*.js")),
        *sorted(static_root.glob("*.html")),
    ]
    references: set[str] = set()
    for path in production_text:
        references.update(
            RUNTIME_ASSET_REFERENCE.findall(path.read_text(encoding="utf-8"))
        )
    return references


def audit_static(site_root: Path) -> None:
    static_root = site_root / "clone" / "static"
    violations: list[str] = []
    for path in sorted(static_root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {".css", ".js", ".html"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"(?:url\s*\(|src\s*=|href\s*=)\s*['\"]?https?://", text, re.I):
            violations.append(path.relative_to(site_root).as_posix())
    active_network_api = re.compile(
        r"\b(?:WebSocket|RTCPeerConnection|webkitRTCPeerConnection|getUserMedia)\b|"
        r"navigator\s*\.\s*mediaDevices",
        re.I,
    )
    network_api_sources = [
        *sorted((site_root / "clone").glob("*.py")),
        *sorted((site_root / "clone" / "fixtures").glob("*.json")),
        *sorted(static_root.glob("*.js")),
        *sorted(static_root.glob("*.html")),
    ]
    for path in network_api_sources:
        if active_network_api.search(path.read_text(encoding="utf-8")):
            violations.append(
                "active WebSocket/WebRTC API reference in "
                + path.relative_to(site_root).as_posix()
            )
    manifest = read_object(site_root / "source-assets" / "offline-clone-manifest.json")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise GateFailure("offline-clone asset manifest is invalid")
    declared_runtime: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("required") is not True:
            continue
        relative = asset.get("runtime_path")
        if not isinstance(relative, str) or not relative.startswith("clone/static/assets/"):
            raise GateFailure(f"required runtime asset path is invalid: {relative!r}")
        declared_runtime.add("/" + relative.removeprefix("clone/"))

    referenced_runtime = parser_proven_runtime_refs(site_root)
    undeclared = sorted(referenced_runtime - declared_runtime)
    if undeclared:
        violations.extend(f"undeclared runtime reference {value}" for value in undeclared)
    for public_path in referenced_runtime:
        local = site_root / "clone" / public_path.removeprefix("/")
        if not local.is_file():
            violations.append(f"missing referenced runtime file {public_path}")

    physical_root = static_root / "assets"
    physical_runtime = {
        "/static/assets/" + path.relative_to(physical_root).as_posix()
        for path in physical_root.rglob("*")
        if path.is_file()
    }
    missing_physical_declarations = physical_runtime - declared_runtime
    missing_runtime_files = declared_runtime - physical_runtime
    if len(assets) != 454 or len(declared_runtime) != 454:
        violations.append(
            f"required logical runtime closure is not 454 ({len(assets)} records, "
            f"{len(declared_runtime)} paths)"
        )
    if len(physical_runtime) != 454:
        violations.append(f"physical runtime asset count is not 454: {len(physical_runtime)}")
    if missing_physical_declarations:
        violations.append(
            "undeclared physical runtime files: "
            + ", ".join(sorted(missing_physical_declarations))
        )
    if missing_runtime_files:
        violations.append(
            "declared runtime files absent from physical closure: "
            + ", ".join(sorted(missing_runtime_files))
        )
    base_records = [
        asset for asset in assets if not str(asset.get("id", "")).startswith("runtime-alias.")
    ]
    if len(base_records) != 452:
        violations.append(f"scoped current/bounded source record count is {len(base_records)}, not 452")
    historical_alias = next(
        (asset for asset in assets if asset.get("id") == "runtime-alias.samsung-t7-main"),
        None,
    )
    if not isinstance(historical_alias, dict) or historical_alias.get("evidence_kind") != "historical":
        violations.append("Samsung T7 runtime alias is not explicitly historical")
    if violations:
        raise GateFailure("static closure violations: " + ", ".join(violations))
    print(
        f"verified local-only static references: {len(referenced_runtime)} concrete "
        "runtime URLs are declared; 454 required logical paths equal the 454-file "
        "physical runtime inventory with no undeclared or missing files; reverse "
        "runtime reachability is enforced by the browser census"
    )


ACCEPTANCE_ARTIFACT_PATHS = {
    "visual": "visual.json",
    "browser": "browser.json",
    "network": "network.json",
    "migration": "migration.json",
    "independent-audit": "independent-audit.json",
    "full-suite": "full-suite.json",
}


def _coverage_dimensions(site_root: Path) -> dict[str, list[str]]:
    coverage = read_object(site_root / "scope" / "coverage.json")
    if coverage.get("status") != "frozen":
        raise GateFailure("acceptance evidence requires a frozen coverage ledger")
    result: dict[str, list[str]] = {}
    rows = coverage.get("dimensions")
    if not isinstance(rows, list):
        raise GateFailure("acceptance evidence cannot read coverage dimensions")
    for row in rows:
        if not isinstance(row, dict):
            raise GateFailure("acceptance evidence found a malformed coverage dimension")
        dimension_id = stable_id(row.get("id"), "acceptance coverage dimension")
        required = row.get("required_items")
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise GateFailure(f"acceptance coverage {dimension_id} has invalid items")
        result[dimension_id] = list(required)
    return result


def _verified_coverage(
    site_root: Path,
    selections: dict[str, list[str] | tuple[str, ...] | None],
) -> list[dict[str, Any]]:
    dimensions = _coverage_dimensions(site_root)
    verified: list[dict[str, Any]] = []
    for dimension_id, selected in selections.items():
        if dimension_id not in dimensions:
            raise GateFailure(f"acceptance evidence names unknown dimension {dimension_id}")
        required = dimensions[dimension_id]
        items = required if selected is None else list(selected)
        unknown = sorted(set(items) - set(required))
        if unknown:
            raise GateFailure(
                f"acceptance evidence names unknown items for {dimension_id}: {unknown}"
            )
        if items:
            verified.append({"dimension_id": dimension_id, "items": items})
    return verified


def _write_acceptance_artifact(
    site_root: Path,
    *,
    kind: str,
    summary: str,
    metrics: dict[str, Any],
    boundaries: list[str],
    coverage: dict[str, list[str] | tuple[str, ...] | None],
    raw_artifacts: list[tuple[Path, str, str, str, list[str]]],
    extra_fields: dict[str, Any] | None = None,
) -> None:
    """Atomically emit one attempt-bound artifact when invoked by the harness."""

    binding_names = (
        "CLAWBENCH_OFFLINE_CLONE_ATTEMPT_ID",
        "CLAWBENCH_OFFLINE_CLONE_MANIFEST_SHA256",
        "CLAWBENCH_OFFLINE_CLONE_COMMAND_ID",
        "CLAWBENCH_OFFLINE_CLONE_SITE_DIR",
        "CLAWBENCH_OFFLINE_CLONE_MANIFEST",
    )
    present = {name: os.environ.get(name) for name in binding_names}
    if not any(present.values()):
        return
    missing = [name for name, value in present.items() if not value]
    if missing:
        raise GateFailure("incomplete acceptance artifact binding: " + ", ".join(missing))
    if kind not in ACCEPTANCE_ARTIFACT_PATHS:
        raise GateFailure(f"unsupported acceptance artifact kind: {kind}")

    attempt_id = str(present["CLAWBENCH_OFFLINE_CLONE_ATTEMPT_ID"])
    manifest_sha256 = str(present["CLAWBENCH_OFFLINE_CLONE_MANIFEST_SHA256"])
    producer = str(present["CLAWBENCH_OFFLINE_CLONE_COMMAND_ID"])
    if re.fullmatch(r"[a-f0-9]{32}", attempt_id) is None:
        raise GateFailure("acceptance attempt binding is malformed")
    if re.fullmatch(r"[a-f0-9]{64}", manifest_sha256) is None:
        raise GateFailure("acceptance manifest binding is malformed")
    stable_id(producer, "acceptance producer command")
    if Path(str(present["CLAWBENCH_OFFLINE_CLONE_SITE_DIR"])).resolve() != site_root:
        raise GateFailure("acceptance site binding does not match --site-root")
    expected_manifest = (site_root / "clone.yaml").resolve()
    if Path(str(present["CLAWBENCH_OFFLINE_CLONE_MANIFEST"])).resolve() != expected_manifest:
        raise GateFailure("acceptance manifest path binding does not match clone.yaml")
    if hashlib.sha256(expected_manifest.read_bytes()).hexdigest() != manifest_sha256:
        raise GateFailure("acceptance manifest bytes changed before artifact emission")

    artifact_root = (site_root / "artifacts" / "offline-clone").resolve()
    raw_rows: list[dict[str, Any]] = []
    for path, role, media_type, sanitization_method, subject_ids in raw_artifacts:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            raise GateFailure(f"acceptance raw artifact is missing or redirected: {path}")
        if artifact_root not in resolved.parents:
            raise GateFailure(f"acceptance raw artifact is outside artifact root: {path}")
        if (
            not subject_ids
            or any(not isinstance(item, str) or not item or any(char.isspace() for char in item) for item in subject_ids)
            or len(set(subject_ids)) != len(subject_ids)
        ):
            raise GateFailure(
                f"acceptance raw artifact has invalid subject bindings: {path}"
            )
        payload = resolved.read_bytes()
        raw_rows.append(
            {
                "path": resolved.relative_to(site_root).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "media_type": media_type,
                "role": role,
                "subject_ids": subject_ids,
                "contains_user_data": False,
                "sanitization_method": sanitization_method,
            }
        )
    if not raw_rows:
        raise GateFailure(f"acceptance artifact {kind} requires raw evidence")

    artifact = {
        "schema_version": "offline-clone.acceptance-evidence.v1",
        "kind": kind,
        "producer_command_id": producer,
        "gate_attempt_id": attempt_id,
        "manifest_sha256": manifest_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "passed",
        "summary": summary,
        "metrics": metrics,
        "boundaries": boundaries,
        "verified_coverage": _verified_coverage(site_root, coverage),
        "raw_artifacts": raw_rows,
    }
    if extra_fields:
        artifact.update(extra_fields)
    destination = (
        site_root
        / "artifacts"
        / "offline-clone"
        / "acceptance"
        / ACCEPTANCE_ARTIFACT_PATHS[kind]
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def _write_raw_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _workspace_python(site_root: Path) -> Path:
    workspace_root = site_root.parents[1]
    candidates = (
        workspace_root / ".venv" / "Scripts" / "python.exe",
        workspace_root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise GateFailure("workspace .venv Python is unavailable for Playwright smoke")


def run_browser_smoke(site_root: Path) -> None:
    """Re-exec the browser worker with the workspace Playwright environment."""

    command = [
        str(_workspace_python(site_root)),
        str(Path(__file__).resolve()),
        "browser-smoke-worker",
        "--site-root",
        str(site_root),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=site_root.parents[1],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("Playwright browser smoke exceeded 900 seconds") from exc
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise GateFailure(
            f"Playwright browser smoke worker exited {completed.returncode}"
        )


def run_visual_acceptance(site_root: Path) -> None:
    """Re-exec the visual worker with the workspace Playwright environment."""

    command = [
        str(_workspace_python(site_root)),
        str(Path(__file__).resolve()),
        "visual-acceptance-worker",
        "--site-root",
        str(site_root),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=site_root.parents[1],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure("Playwright visual acceptance exceeded 600 seconds") from exc
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise GateFailure(
            f"Playwright visual acceptance worker exited {completed.returncode}"
        )


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _browser_executable() -> Path:
    configured = os.environ.get("AMAZON_BROWSER_EXECUTABLE", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ]
    )
    for name in ("google-chrome", "google-chrome-stable", "chromium", "msedge"):
        discovered = shutil.which(name)
        if discovered:
            candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise GateFailure("no local Chrome or Edge executable is available")


def _admin_json(
    admin_base: str,
    admin_token: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    method = "GET"
    headers = {"X-Bench-Admin-Token": admin_token}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(admin_base + path, data=body, headers=headers, method=method)
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise GateFailure(f"admin {path} returned {response.status}")
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise GateFailure(f"admin {path} returned a non-object")
    return value


def _asset_route_candidates(asset_rows: list[dict[str, Any]]) -> list[str]:
    candidates: set[str] = set()
    for asset in asset_rows:
        for reference in asset.get("referenced_by", []):
            if not isinstance(reference, str) or not reference.startswith("/"):
                continue
            if any(character in reference for character in (",", "<", ">", "\n")):
                continue
            candidates.add(reference)
    return sorted(candidates, key=lambda value: (value != "/", value))


def _normalized_region_difference(
    source: Image.Image,
    candidate: Image.Image,
    box: tuple[int, int, int, int],
) -> float:
    difference = ImageChops.difference(
        source.convert("RGB").crop(box), candidate.convert("RGB").crop(box)
    )
    return sum(ImageStat.Stat(difference).mean) / (3.0 * 255.0)


def visual_acceptance_worker(site_root: Path) -> None:
    """Capture current-direct clone views and enforce a coarse visual contract."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise GateFailure("Playwright is unavailable in the workspace .venv") from exc

    clone_root = site_root / "clone"
    checkpoint_document = read_object(site_root / "scope" / "checkpoints.json")
    checkpoint_contracts = {
        str(row.get("id")): row.get("visual_contract")
        for row in checkpoint_document.get("checkpoints", [])
        if isinstance(row, dict) and isinstance(row.get("visual_contract"), dict)
    }
    cases = [
        {
            "id": "home.desktop.loaded",
            "path": "/",
            "landmarks": (
                (".site-header", 1),
                (".home-hero", 1),
                (".home-grid", 1),
                ("article.home-card", 4),
            ),
        },
        {
            "id": "search.desktop.filtered",
            "path": "/s?k=portable+ssd",
            "landmarks": (
                (".site-header", 1),
                ("#search-refinements", 1),
                (".search-results", 1),
                ("article.search-result", 2),
            ),
        },
    ]
    if set(checkpoint_contracts) != {str(case["id"]) for case in cases}:
        raise GateFailure("visual worker did not receive the exact frozen oracle set")
    public_port = _reserve_loopback_port()
    admin_port = _reserve_loopback_port()
    while admin_port == public_port:
        admin_port = _reserve_loopback_port()
    public_base = f"http://127.0.0.1:{public_port}"
    admin_token = "amazon-visual-local-admin"
    browser_path = _browser_executable()
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("AMAZON_CLONE_SMTP") or key == "AMAZON_CLONE_REQUIRE_SMTP":
            environment.pop(key, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    checks = 0
    similarity_scores: list[float] = []
    thresholds: list[float] = []
    negative_control_scores: list[float] = []
    source_hashes: list[str] = []
    capture_hashes: list[str] = []
    captured_pairs: list[
        tuple[str, Image.Image, Image.Image, float, tuple[int, int, int, int]]
    ] = []
    visual_records: list[dict[str, Any]] = []
    visual_raw_artifacts: list[tuple[Path, str, str, str, list[str]]] = []
    remote_requests: list[str] = []
    browser_errors: list[str] = []
    capture_root = site_root / "artifacts" / "offline-clone" / "acceptance" / "raw" / "visual"
    capture_root.mkdir(parents=True, exist_ok=True)

    def passed(condition: bool, message: str) -> None:
        nonlocal checks
        if not condition:
            raise GateFailure(message)
        checks += 1

    with tempfile.TemporaryDirectory(
        prefix="amazon-visual-acceptance-", ignore_cleanup_errors=True
    ) as temp_name:
        temp_root = Path(temp_name)
        stdout_path = temp_root / "server.stdout.log"
        stderr_path = temp_root / "server.stderr.log"
        with stdout_path.open("w+", encoding="utf-8") as server_stdout, stderr_path.open(
            "w+", encoding="utf-8"
        ) as server_stderr:
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(clone_root / "server.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(public_port),
                    "--admin-host",
                    "127.0.0.1",
                    "--admin-port",
                    str(admin_port),
                    "--admin-token",
                    admin_token,
                    "--db",
                    str(temp_root / "amazon.sqlite3"),
                ],
                cwd=clone_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=server_stdout,
                stderr=server_stderr,
                text=True,
            )
            try:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        break
                    try:
                        with urlopen(public_base + "/", timeout=2) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        pass
                    time.sleep(0.2)
                else:
                    raise GateFailure("isolated visual server did not become ready")
                if server.poll() is not None:
                    server_stderr.flush()
                    server_stderr.seek(0)
                    raise GateFailure(
                        "isolated visual server exited during startup: "
                        + server_stderr.read()[-2000:]
                    )

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=str(browser_path),
                        headless=True,
                        args=[
                            "--disable-background-networking",
                            "--disable-component-update",
                            "--disable-default-apps",
                            "--disable-extensions",
                            "--disable-sync",
                            "--metrics-recording-only",
                            "--no-first-run",
                            "--safebrowsing-disable-auto-update",
                        ],
                    )
                    context = browser.new_context(
                        viewport={"width": 1350, "height": 890},
                        locale="en-US",
                        timezone_id="Asia/Singapore",
                        service_workers="block",
                    )

                    def route_request(route: Any) -> None:
                        parsed = urlsplit(route.request.url)
                        port = parsed.port or (443 if parsed.scheme == "https" else 80)
                        if parsed.scheme in {"data", "blob", "about"}:
                            route.continue_()
                        elif (
                            parsed.scheme == "http"
                            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                            and port == public_port
                        ):
                            route.continue_()
                        else:
                            remote_requests.append(route.request.url)
                            route.abort()

                    context.route("**/*", route_request)
                    page = context.new_page()
                    page.on("pageerror", lambda error: browser_errors.append(str(error)))
                    page.on(
                        "console",
                        lambda message: browser_errors.append(message.text)
                        if message.type == "error"
                        else None,
                    )
                    for case in cases:
                        case_id = str(case["id"])
                        contract = checkpoint_contracts[case_id]
                        source_path = inside(
                            site_root, str(contract["source_artifact_path"])
                        )
                        passed(source_path.is_file(), f"visual source is missing: {source_path}")
                        source_bytes = source_path.read_bytes()
                        passed(
                            hashlib.sha256(source_bytes).hexdigest()
                            == contract["source_artifact_sha256"],
                            f"visual source digest changed: {case['id']}",
                        )
                        source_hashes.append(hashlib.sha256(source_bytes).hexdigest())
                        safe_case_id = str(case["id"]).replace(".", "-")
                        source_copy = capture_root / f"source-{safe_case_id}.png"
                        source_copy.write_bytes(source_bytes)
                        visual_raw_artifacts.append(
                            (
                                source_copy,
                                "source-screenshot",
                                "image/png",
                                "Anonymous public source capture; frozen digest checked and no authenticated or user-entered state retained.",
                                [case_id],
                            )
                        )
                        with Image.open(source_path) as source_image:
                            source = source_image.convert("RGB")
                        passed(
                            source.size
                            == (
                                int(contract["viewport"]["width"]),
                                int(contract["viewport"]["height"]),
                            ),
                            f"visual source raster changed: {case['id']} {source.size}",
                        )

                        response = page.goto(
                            public_base + str(case["path"]),
                            wait_until="networkidle",
                            timeout=30_000,
                        )
                        passed(
                            response is not None and int(response.status) == 200,
                            f"visual route failed: {case['id']}",
                        )
                        for selector, minimum in case["landmarks"]:
                            passed(
                                page.locator(selector).count() >= minimum,
                                f"visual landmark missing for {case['id']}: {selector}",
                            )
                        header_box = page.locator(".site-header").bounding_box()
                        passed(
                            isinstance(header_box, dict)
                            and 90 <= float(header_box["height"]) <= 112,
                            f"visual header geometry changed: {case['id']} {header_box}",
                        )

                        capture_path = capture_root / f"clone-{safe_case_id}.png"
                        page.screenshot(path=str(capture_path))
                        with Image.open(capture_path) as candidate_image:
                            candidate = candidate_image.convert("RGB")
                        capture_hashes.append(
                            hashlib.sha256(capture_path.read_bytes()).hexdigest()
                        )
                        visual_raw_artifacts.append(
                            (
                                capture_path,
                                "clone-screenshot",
                                "image/png",
                                "Captured from the isolated anonymous clone database with no external account or user-provided state.",
                                [case_id],
                            )
                        )
                        passed(
                            candidate.size == source.size,
                            f"visual clone raster does not match source raster: {case['id']}",
                        )
                        region_value = contract["comparison_region"]
                        comparison_box = (
                            int(region_value["x"]),
                            int(region_value["y"]),
                            int(region_value["x"]) + int(region_value["width"]),
                            int(region_value["y"]) + int(region_value["height"]),
                        )
                        difference = _normalized_region_difference(
                            source, candidate, comparison_box
                        )
                        similarity = 1.0 - difference
                        threshold = float(contract["threshold"])
                        similarity_scores.append(round(similarity, 9))
                        thresholds.append(threshold)
                        passed(
                            similarity >= threshold,
                            f"visual similarity {similarity:.4f} is below "
                            f"{threshold:.4f}: {case['id']}",
                        )
                        captured_pairs.append(
                            (
                                case_id,
                                source,
                                candidate,
                                threshold,
                                comparison_box,
                            )
                        )
                        visual_records.append(
                            {
                                "id": case_id,
                                "source_artifact_sha256": hashlib.sha256(
                                    source_copy.read_bytes()
                                ).hexdigest(),
                                "clone_artifact_sha256": hashlib.sha256(
                                    capture_path.read_bytes()
                                ).hexdigest(),
                                "score": similarity,
                                "threshold": threshold,
                                "passed": similarity >= threshold,
                                "metric": contract["metric"],
                                "viewport": dict(contract["viewport"]),
                                "comparison_region": dict(
                                    contract["comparison_region"]
                                ),
                            }
                        )
                    for index, (
                        case_id,
                        source,
                        _candidate,
                        threshold,
                        comparison_box,
                    ) in enumerate(
                        captured_pairs
                    ):
                        wrong_candidate = captured_pairs[1 - index][2]
                        wrong_difference = _normalized_region_difference(
                            source, wrong_candidate, comparison_box
                        )
                        wrong_similarity = 1.0 - wrong_difference
                        negative_control_scores.append(round(wrong_similarity, 9))
                        passed(
                            wrong_similarity < threshold,
                            f"visual threshold failed to reject wrong checkpoint for {case_id}: "
                            f"{wrong_similarity:.4f} >= {threshold:.4f}",
                        )
                    context.close()
                    browser.close()
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=10)

    passed(not remote_requests, "visual acceptance observed nonlocal browser requests")
    passed(not browser_errors, "visual acceptance observed browser errors")
    visual_subject_ids = sorted(record["id"] for record in visual_records)
    visual_diff_path = capture_root / "visual-diff.json"
    _write_raw_json(
        visual_diff_path,
        {
            "schema_version": "offline-clone.raw.visual-diff.v1",
            "subject_ids": visual_subject_ids,
            "checkpoints": visual_records,
            "wrong_checkpoint_similarity_scores": negative_control_scores,
        },
    )
    visual_raw_artifacts.append(
        (
            visual_diff_path,
            "visual-diff",
            "application/json",
            "The diff record contains frozen checkpoint identities, source/clone digests, full-raster scores, and a wrong-page negative control; no page text or user state is retained.",
            visual_subject_ids,
        )
    )
    print(
        "visual acceptance passed two current-direct frozen raster contracts; "
        f"pixel similarities={similarity_scores}, thresholds={thresholds}"
    )
    _write_acceptance_artifact(
        site_root,
        kind="visual",
        summary=(
            "Two current-direct desktop source captures were compared with clone captures "
            "using the source digests, raster geometry, pixel metric, and nonzero thresholds "
            "frozen in the checkpoint oracle."
        ),
        metrics={
            "checks_total": checks,
            "checks_passed": checks,
            "checks_failed": 0,
            "checkpoints_total": len(cases),
            "checkpoints_passed": len(cases),
            "checkpoints_failed": 0,
            "reference_views": len(cases),
            "browser_context_width": 1350,
            "browser_context_height": 890,
            "compared_raster_width": 1350,
            "compared_raster_height": 890,
            "pixel_similarity_scores": similarity_scores,
            "pixel_similarity_thresholds": thresholds,
            "wrong_checkpoint_similarity_scores": negative_control_scores,
            "source_reference_sha256": source_hashes,
            "clone_capture_sha256": capture_hashes,
            "nonlocal_requests": len(remote_requests),
            "browser_errors": len(browser_errors),
        },
        boundaries=[
            "The named desktop checkpoint remains 1365x900, while the frozen comparable source raster and isolated clone capture are both exactly 1350x890.",
            "Full-raster pixel MAE similarity is a coarse regression contract, not a claim of pixel identity; dynamic text and source notices may differ.",
            "Each frozen nonzero threshold must also reject the other checkpoint capture as an explicit wrong-page negative control.",
            "Only the two current-direct checkpoints are visually certified. Current-partial, inferred, and unavailable checkpoints are excluded from pixel comparison.",
            "The selected-filter interaction is functionally verified by browser evidence; its current-direct visual reference is the portable-SSD results surface captured before selection.",
        ],
        coverage={"frontend-current-direct-checkpoints": None},
        raw_artifacts=visual_raw_artifacts,
    )


def browser_smoke_worker(site_root: Path) -> None:
    """Run an isolated browser census and a real shopping/recovery journey."""

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise GateFailure("Playwright is unavailable in the workspace .venv") from exc

    clone_root = site_root / "clone"
    manifest = read_object(site_root / "source-assets" / "offline-clone-manifest.json")
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list):
        raise GateFailure("asset manifest is unavailable to browser smoke")
    asset_rows = [row for row in raw_assets if isinstance(row, dict)]
    declared_runtime = {
        "/" + str(row["runtime_path"]).removeprefix("clone/")
        for row in asset_rows
        if row.get("required") is True
    }
    required_runtime_asset_ids = sorted(
        stable_id(row.get("id"), "browser asset manifest id")
        for row in asset_rows
        if row.get("required") is True
    )
    runtime_subject_by_url = {
        "/" + str(row["runtime_path"]).removeprefix("clone/"): str(row["id"])
        for row in asset_rows
        if row.get("required") is True
    }
    if len(declared_runtime) != 454:
        raise GateFailure(
            f"browser smoke requires the frozen 454-path closure, got {len(declared_runtime)}"
        )
    parser_proven = parser_proven_runtime_refs(site_root)
    checkpoints = read_object(site_root / "scope" / "checkpoints.json")
    checkpoint_rows = checkpoints.get("checkpoints")
    if not isinstance(checkpoint_rows, list) or len(checkpoint_rows) != 16:
        raise GateFailure("browser smoke requires the 16 frozen checkpoints")
    checkpoint_ids = {
        str(row.get("id")) for row in checkpoint_rows if isinstance(row, dict)
    }

    public_port = _reserve_loopback_port()
    admin_port = _reserve_loopback_port()
    while admin_port == public_port:
        admin_port = _reserve_loopback_port()
    public_base = f"http://127.0.0.1:{public_port}"
    admin_base = f"http://127.0.0.1:{admin_port}"
    admin_token = "amazon-browser-smoke-local-admin"
    browser_path = _browser_executable()

    requested_assets: set[str] = set()
    request_events: list[dict[str, Any]] = []
    network_violations: list[str] = []
    websocket_events: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    static_failures: list[str] = []
    http_failures: list[str] = []
    broken_images: list[str] = []
    visits: list[dict[str, Any]] = []
    checkpoint_results: dict[str, dict[str, str]] = {}
    asset_route_failures: list[str] = []

    def mark(checkpoint_id: str, mode: str, url: str) -> None:
        if checkpoint_id not in checkpoint_ids:
            raise GateFailure(f"browser smoke marked unknown checkpoint {checkpoint_id}")
        checkpoint_results[checkpoint_id] = {"mode": mode, "url": url}

    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("AMAZON_CLONE_SMTP") or key == "AMAZON_CLONE_REQUIRE_SMTP":
            environment.pop(key, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    with tempfile.TemporaryDirectory(
        prefix="amazon-browser-smoke-", ignore_cleanup_errors=True
    ) as temp_name:
        temp_root = Path(temp_name)
        database = temp_root / "amazon.sqlite3"
        stdout_path = temp_root / "server.stdout.log"
        stderr_path = temp_root / "server.stderr.log"
        with stdout_path.open("w+", encoding="utf-8") as server_stdout, stderr_path.open(
            "w+", encoding="utf-8"
        ) as server_stderr:
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(clone_root / "server.py"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(public_port),
                    "--admin-host",
                    "127.0.0.1",
                    "--admin-port",
                    str(admin_port),
                    "--admin-token",
                    admin_token,
                    "--db",
                    str(database),
                ],
                cwd=clone_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=server_stdout,
                stderr=server_stderr,
                text=True,
            )
            try:
                deadline = time.monotonic() + 45
                startup_error: Exception | None = None
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        break
                    try:
                        with urlopen(public_base + "/", timeout=2) as response:
                            if response.status == 200:
                                startup_error = None
                                break
                    except Exception as exc:  # readiness polling only
                        startup_error = exc
                    time.sleep(0.2)
                else:
                    raise GateFailure(f"isolated server did not become ready: {startup_error}")
                if server.poll() is not None:
                    server_stderr.flush()
                    server_stderr.seek(0)
                    raise GateFailure(
                        "isolated server exited during startup: "
                        + server_stderr.read()[-2000:]
                    )

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=str(browser_path),
                        headless=True,
                        args=[
                            "--disable-background-networking",
                            "--disable-component-update",
                            "--disable-default-apps",
                            "--disable-extensions",
                            "--disable-sync",
                            "--metrics-recording-only",
                            "--no-first-run",
                            "--safebrowsing-disable-auto-update",
                        ],
                    )
                    context = browser.new_context(
                        viewport={"width": 1365, "height": 900},
                        locale="en-US",
                        timezone_id="Asia/Singapore",
                        service_workers="block",
                    )

                    def route_request(route: Any) -> None:
                        url = route.request.url
                        parsed = urlsplit(url)
                        if parsed.scheme in {"data", "blob", "about"}:
                            route.continue_()
                            return
                        port = parsed.port or (443 if parsed.scheme == "https" else 80)
                        if (
                            parsed.scheme == "http"
                            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                            and port == public_port
                        ):
                            route.continue_()
                            return
                        network_violations.append(url)
                        route.abort()

                    context.route("**/*", route_request)
                    page = context.new_page()
                    completed_request_ids: set[int] = set()

                    def on_console(message: Any) -> None:
                        if message.type == "error":
                            console_errors.append(f"{message.text} @ {message.location}")

                    def on_response(response: Any) -> None:
                        completed_request_ids.add(id(response.request))
                        parsed = urlsplit(response.url)
                        status = int(response.status)
                        local_path = unquote(parsed.path) or "/"
                        request_subjects = ["assets.local-only.closed"]
                        asset_subject = runtime_subject_by_url.get(local_path)
                        if asset_subject is not None:
                            request_subjects.append(asset_subject)
                            if status < 400:
                                requested_assets.add(local_path)
                        request_events.append(
                            {
                                "url": local_path,
                                "remote": False,
                                "failed": status >= 400,
                                "status": status,
                                "subject_ids": request_subjects,
                            }
                        )
                        if status >= 400:
                            http_failures.append(f"{status} {unquote(parsed.path)}")
                        if response.status >= 400 and parsed.path.startswith("/static/"):
                            static_failures.append(f"{response.status} {response.url}")

                    def on_request_failed(request: Any) -> None:
                        if id(request) in completed_request_ids:
                            return
                        parsed = urlsplit(request.url)
                        remote = not (
                            parsed.scheme == "http"
                            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                            and (parsed.port or 80) == public_port
                        )
                        if remote:
                            sanitized_url = (
                                f"{parsed.scheme}://{parsed.hostname or 'remote'}"
                                f"{unquote(parsed.path) or '/'}"
                            )
                        else:
                            sanitized_url = unquote(parsed.path) or "/"
                        request_subjects = ["assets.local-only.closed"]
                        asset_subject = runtime_subject_by_url.get(
                            unquote(parsed.path) or "/"
                        )
                        if asset_subject is not None:
                            request_subjects.append(asset_subject)
                        request_events.append(
                            {
                                "url": sanitized_url,
                                "remote": remote,
                                "failed": True,
                                "status": 599,
                                "subject_ids": request_subjects,
                            }
                        )
                        http_failures.append(f"599 {sanitized_url}")

                    page.on("console", on_console)
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.on("response", on_response)
                    page.on("requestfailed", on_request_failed)
                    page.on("websocket", lambda websocket: websocket_events.append(websocket.url))

                    def settle(label: str) -> None:
                        page.wait_for_load_state("networkidle", timeout=20_000)
                        broken = page.evaluate(
                            """
                            async () => {
                              const images = Array.from(document.images);
                              for (const image of images) image.loading = 'eager';
                              await Promise.race([
                                Promise.allSettled(images.map((image) => image.decode())),
                                new Promise((resolve) => setTimeout(resolve, 5000)),
                              ]);
                              return images
                                .filter((image) => image.complete && image.naturalWidth === 0)
                                .map((image) => image.currentSrc || image.src || '<empty-src>');
                            }
                            """
                        )
                        if isinstance(broken, list):
                            broken_images.extend(f"{label}: {value}" for value in broken)

                    def record_navigation(label: str, response: Any) -> None:
                        if response is None:
                            raise GateFailure(f"{label} produced no navigation response")
                        if int(response.status) >= 400:
                            raise GateFailure(
                                f"{label} returned {response.status}: {response.url}"
                            )
                        settle(label)
                        visits.append(
                            {
                                "label": label,
                                "status": int(response.status),
                                "url": page.url,
                            }
                        )

                    def visit(path: str, label: str) -> None:
                        response = page.goto(
                            public_base + path,
                            wait_until="domcontentloaded",
                            timeout=40_000,
                        )
                        record_navigation(label, response)

                    def submit(locator: Any, label: str) -> None:
                        with page.expect_navigation(
                            wait_until="domcontentloaded", timeout=40_000
                        ) as navigation:
                            locator.click()
                        record_navigation(label, navigation.value)

                    def choose_alternate_variant() -> None:
                        alternatives = page.locator(
                            '[data-product-option][aria-pressed="false"]:not([disabled])'
                        )
                        if alternatives.count() < 1:
                            raise GateFailure("T7 PDP has no reachable alternate option")
                        alternatives.first.click()
                        page.wait_for_timeout(100)
                        if (
                            page.locator('[data-product-quote-available="true"]').count()
                            != 1
                        ):
                            raise GateFailure("alternate T7 option did not produce a quote")

                    # Checkpoint-driven browser surfaces and responsive states.
                    visit("/", "checkpoint-home-desktop")
                    if page.locator("main").count() != 1:
                        raise GateFailure("home main landmark is missing")
                    mark("home.desktop.loaded", "browser-exercised", page.url)

                    page.set_viewport_size({"width": 390, "height": 844})
                    visit("/", "checkpoint-home-mobile")
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(100)
                    if page.evaluate("window.scrollY") <= 0:
                        raise GateFailure("mobile homepage did not use native vertical scroll")
                    mark("home.mobile.scroll", "browser-exercised", page.url)
                    page.set_viewport_size({"width": 1365, "height": 900})

                    visit(
                        "/s?k=portable+ssd&brand=SanDisk",
                        "checkpoint-search-filtered",
                    )
                    if page.locator("article.search-result").count() < 1:
                        raise GateFailure("filtered search returned no product cards")
                    if page.locator(".search-filter-link.is-selected").count() < 1:
                        raise GateFailure("filtered search did not expose selected filter state")
                    mark("search.desktop.filtered", "browser-exercised", page.url)

                    page.set_viewport_size({"width": 768, "height": 1024})
                    visit("/s?k=portable+ssd", "checkpoint-search-tablet-results")
                    if page.locator("article.search-result").count() < 1:
                        raise GateFailure("tablet search results are empty")
                    visit(
                        "/s?k=zzyzxxyy+qqqvvv",
                        "checkpoint-search-tablet-empty",
                    )
                    if page.locator(".search-results.search-no-results").count() != 1:
                        raise GateFailure("tablet no-results state is missing")
                    mark("search.tablet.results", "browser-exercised-partial-source", page.url)
                    page.set_viewport_size({"width": 1365, "height": 900})

                    visit("/dp/B0874XN4D8", "checkpoint-pdp-variant")
                    choose_alternate_variant()
                    mark("pdp.desktop.valid.variant", "browser-exercised", page.url)

                    page.set_viewport_size({"width": 390, "height": 844})
                    visit("/dp/B0874XN4D8", "checkpoint-pdp-mobile-dependency")
                    if (
                        page.locator(
                            '[data-product-option][disabled], [data-product-option][aria-disabled="true"], option:disabled'
                        ).count()
                        < 1
                    ):
                        raise GateFailure("mobile PDP exposes no disabled incompatible option")
                    mark("pdp.mobile.dependency.disabled", "browser-exercised", page.url)
                    page.set_viewport_size({"width": 1365, "height": 900})

                    visit("/dp/B07CRG94G3", "checkpoint-pdp-sparse")
                    if (
                        "Product offer details are not available"
                        not in page.locator("body").inner_text()
                    ):
                        raise GateFailure("sparse PDP does not disclose its offer boundary")
                    mark("pdp.desktop.sparse.boundary", "browser-exercised", page.url)

                    # Real interaction: add two sibling variants as distinct lines.
                    visit("/dp/B0874XN4D8", "journey-pdp-default")
                    submit(
                        page.locator(
                            'form.generic-cart-form:has([data-product-add-to-cart]) [data-product-add-to-cart]'
                        ).first,
                        "journey-add-default",
                    )
                    visit("/dp/B0874XN4D8", "journey-pdp-alternate")
                    choose_alternate_variant()
                    submit(
                        page.locator(
                            'form.generic-cart-form:has([data-product-add-to-cart]) [data-product-add-to-cart]'
                        ).first,
                        "journey-add-alternate",
                    )
                    if page.locator('.cart-line[data-asin="B0874XN4D8"]').count() != 2:
                        raise GateFailure("browser cart did not preserve sibling variants")
                    mark("cart.desktop.sibling.variants", "browser-exercised", page.url)

                    # Account hover, unknown-email registration, and email verification.
                    visit("/", "checkpoint-auth-hover")
                    page.locator("[data-account-menu-trigger]").hover()
                    page.wait_for_timeout(150)
                    if page.locator('[data-account-menu-panel][aria-hidden="false"]').count() != 1:
                        raise GateFailure("account hover flyout did not open")
                    submit(page.locator(".account-flyout-signin"), "journey-signin-entry")
                    page.locator("#ap-email").fill("browser-smoke@example.test")
                    submit(page.locator('form[action="/ap/signin"] button'), "journey-unknown-email")
                    if urlsplit(page.url).path != "/ap/register":
                        raise GateFailure("unknown email did not continue to registration")
                    page.locator("#ap-customer-name").fill("Browser Smoke")
                    page.locator("#ap-register-email").fill("browser-smoke@example.test")
                    page.locator("#ap-register-password").fill("BrowserSmoke123!")
                    page.locator("#ap-password-check").fill("BrowserSmoke123!")
                    submit(page.locator('form[action="/ap/register"] button'), "journey-register")
                    if urlsplit(page.url).path != "/ap/cvf/verify":
                        raise GateFailure("registration did not require email verification")
                    outbox = _admin_json(
                        admin_base,
                        admin_token,
                        "/__bench/auth/registration-outbox",
                    )
                    messages = outbox.get("messages")
                    if not isinstance(messages, list) or not messages:
                        raise GateFailure("local registration outbox is empty")
                    code = next(
                        (
                            str(message.get("verification_code"))
                            for message in reversed(messages)
                            if isinstance(message, dict)
                            and message.get("verification_code") is not None
                        ),
                        "",
                    )
                    if re.fullmatch(r"[0-9]{6}", code) is None:
                        raise GateFailure("local registration code is unavailable")
                    page.locator("#ap-code").fill(code)
                    submit(page.locator('form[action^="/ap/cvf/verify"] button.auth-primary'), "journey-verify")
                    mark("auth.desktop.hover.register.verify", "browser-exercised", page.url)

                    # Checkout decline/retry, order delivery, and return/refund recovery.
                    visit("/gp/cart/view.html", "journey-cart-signed-in")
                    submit(
                        page.locator(
                            'form[action="/gp/buy/spc/handlers/display.html"] button'
                        ),
                        "journey-checkout-start",
                    )
                    if urlsplit(page.url).path != "/gp/buy/addressselect/handlers/display.html":
                        raise GateFailure("signed-in checkout did not reach address selection")
                    address_values = {
                        "#checkout-new-address-full-name": "Browser Smoke",
                        "#checkout-new-address-line-1": "1 Offline Way",
                        "#checkout-new-address-city": "Singapore",
                        "#checkout-new-address-state": "Singapore",
                        "#checkout-new-address-postal": "018989",
                        "#checkout-new-address-phone": "+65 6123 4567",
                    }
                    for selector, value in address_values.items():
                        page.locator(selector).fill(value)
                    page.locator("#checkout-new-address-country").select_option("SG")
                    submit(
                        page.locator(
                            'form.checkout-address-form[action="/gp/buy/addressselect/handlers/display.html"] button'
                        ).last,
                        "journey-address",
                    )
                    page.locator('input[name="deliveryOption"][value="standard"]').check()
                    submit(
                        page.locator(
                            'form[action="/gp/buy/shipoptionselect/handlers/display.html"] button'
                        ),
                        "journey-delivery",
                    )
                    page.locator(
                        'input[name="paymentMethod"][value="sandbox-card-declined"]'
                    ).check()
                    submit(
                        page.locator(
                            'form[action="/gp/buy/payselect/handlers/display.html"] button'
                        ),
                        "journey-payment-decline",
                    )
                    if "declined" not in page.locator("body").inner_text().casefold():
                        raise GateFailure("declined card produced no retryable warning")
                    page.locator(
                        'input[name="paymentMethod"][value="sandbox-bank-approved"]'
                    ).check()
                    submit(
                        page.locator(
                            'form[action="/gp/buy/payselect/handlers/display.html"] button'
                        ),
                        "journey-payment-retry",
                    )
                    if urlsplit(page.url).path != "/gp/buy/spc/handlers/display.html":
                        raise GateFailure("approved bank retry did not reach order review")
                    mark("checkout.desktop.declined.retry", "browser-exercised", page.url)
                    submit(
                        page.locator('form[action="/gp/buy/place-order"] button'),
                        "journey-place-order",
                    )
                    order_values = parse_qs(urlsplit(page.url).query).get("orderID", [])
                    if len(order_values) != 1 or not order_values[0].isdigit():
                        raise GateFailure("order confirmation did not expose one orderID")
                    order_id = order_values[0]
                    _admin_json(
                        admin_base,
                        admin_token,
                        "/__bench/orders/advance",
                        {"orderID": order_id, "targetStatus": "SHIPPED"},
                    )
                    _admin_json(
                        admin_base,
                        admin_token,
                        "/__bench/orders/advance",
                        {"orderID": order_id, "targetStatus": "DELIVERED"},
                    )
                    visit(
                        f"/gp/your-account/order-details?orderID={order_id}",
                        "journey-order-delivered",
                    )
                    return_link = page.locator(
                        f'a[href^="/gp/your-account/returns/create?orderID={order_id}"]'
                    )
                    if return_link.count() != 1:
                        raise GateFailure("delivered order has no return entry")
                    submit(return_link, "journey-return-entry")
                    page.locator('select[name="reasonCode"]').select_option("DAMAGED")
                    page.locator('textarea[name="customerNote"]').fill(
                        "Repeatable browser smoke return"
                    )
                    submit(
                        page.locator(
                            'form[action="/gp/your-account/returns/create"] button'
                        ),
                        "journey-return-request",
                    )
                    return_values = parse_qs(urlsplit(page.url).query).get("returnID", [])
                    if len(return_values) != 1 or not return_values[0].isdigit():
                        raise GateFailure("return request did not expose one returnID")
                    return_id = return_values[0]
                    _admin_json(
                        admin_base,
                        admin_token,
                        "/__bench/returns/advance",
                        {"returnID": return_id, "targetStatus": "RECEIVED"},
                    )
                    _admin_json(
                        admin_base,
                        admin_token,
                        "/__bench/returns/advance",
                        {"returnID": return_id, "targetStatus": "REFUNDED"},
                    )
                    visit(
                        f"/gp/your-account/returns/details?returnID={return_id}",
                        "journey-return-refunded",
                    )
                    if "Return refunded" not in page.locator("body").inner_text():
                        raise GateFailure("return lifecycle did not reach refunded state")
                    mark("order.desktop.delivery.return", "browser-exercised", page.url)

                    visit("/gp/goldbox/", "checkpoint-deals")
                    page.locator('input[name="dealType"][value="limited-time"]').check()
                    submit(page.locator(".deals-apply-filter"), "checkpoint-deals-filtered")
                    if "dealType=limited-time" not in page.url:
                        raise GateFailure("Deals filter did not create a copyable URL")
                    mark("deals.desktop.filters", "browser-exercised-partial-source", page.url)

                    visit("/product-reviews/B0874XN4D8", "checkpoint-reviews")
                    if page.locator("main.product-reviews-main").count() != 1:
                        raise GateFailure("review route did not render source/local boundary")
                    mark("reviews.desktop.source.local", "browser-exercised", page.url)

                    visit("/dp/B0874XN4D8", "checkpoint-compare-default-pdp")
                    submit(
                        page.locator("form.compare-add-form button").first,
                        "checkpoint-compare-default-add",
                    )
                    if page.locator('.compare-main[data-compare-count="1"]').count() != 1:
                        raise GateFailure("comparison did not retain the default variant")
                    visit("/dp/B0874XN4D8", "checkpoint-compare-alternate-pdp")
                    choose_alternate_variant()
                    submit(
                        page.locator("form.compare-add-form button").first,
                        "checkpoint-compare-alternate-add",
                    )
                    if page.locator('.compare-main[data-compare-count="2"]').count() != 1:
                        raise GateFailure("comparison merged sibling variants")
                    if page.locator(".compare-table .compare-product-title").count() != 2:
                        raise GateFailure("comparison did not render two variant columns")
                    mark(
                        "compare.desktop.sibling.variants",
                        "browser-exercised-sibling-variant-columns",
                        page.url,
                    )

                    for path, label in (
                        ("/gift-cards/b/", "gift-cards"),
                        ("/b/", "sell"),
                        ("/gp/browse.html", "registry"),
                        ("/gp/help/customer/display.html?nodeId=508510", "help"),
                    ):
                        visit(path, f"checkpoint-navigation-{label}")
                    mark(
                        "navigation.desktop.local.destinations",
                        "browser-exercised",
                        page.url,
                    )

                    visit("/Amazon-Video/b/", "checkpoint-prime-placeholder")
                    if "outside this shopping clone" not in page.locator("body").inner_text():
                        raise GateFailure("Prime Video boundary is not explicit")
                    mark("prime.video.desktop.placeholder", "browser-exercised", page.url)

                    # Lists is asset-bearing but not one of the 16 selected checkpoints.
                    visit("/hz/wishlist/intro", "asset-surface-lists-intro")

                    # Prove that every required asset is actually servable through
                    # the isolated browser origin, including assets below the fold
                    # or behind stateful interactions.  Source ``referenced_by``
                    # values are capture provenance, not necessarily clone routes.
                    # A same-origin browser fetch therefore closes the runtime
                    # census before provenance is used as a best-effort fallback.
                    for offset in range(0, len(declared_runtime), 48):
                        batch = sorted(declared_runtime)[offset : offset + 48]
                        results = page.evaluate(
                            """
                            async (paths) => Promise.all(paths.map(async (path) => {
                              try {
                                const response = await fetch(path, {
                                  cache: 'no-store',
                                  credentials: 'same-origin',
                                });
                                await response.arrayBuffer();
                                return {path, status: response.status};
                              } catch (error) {
                                return {path, status: 599, error: String(error)};
                              }
                            }))
                            """,
                            batch,
                        )
                        failed_probes = [
                            row
                            for row in results
                            if not isinstance(row, dict)
                            or int(row.get("status", 599)) >= 400
                        ]
                        if failed_probes:
                            raise GateFailure(
                                "browser asset probes failed: "
                                + json.dumps(
                                    failed_probes, ensure_ascii=False, sort_keys=True
                                )[:4000]
                            )

                    # Only if a future browser fails to expose successful fetch
                    # responses do we use declared route provenance to drive more
                    # rendered surfaces.  Those visits never define support scope.
                    for path in _asset_route_candidates(asset_rows):
                        if not (declared_runtime - requested_assets):
                            break
                        try:
                            visit(path, "asset-census:" + path[:120])
                        except (GateFailure, PlaywrightError, PlaywrightTimeoutError) as exc:
                            asset_route_failures.append(f"{path}: {exc}")

                    context.close()
                    browser.close()

            except Exception as exc:
                if isinstance(exc, GateFailure):
                    failure = exc
                else:
                    failure = GateFailure(f"browser smoke execution failed: {exc}")
                raise failure
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=10)

        missing_checkpoints = sorted(checkpoint_ids - set(checkpoint_results))
        unexpected_checkpoints = sorted(set(checkpoint_results) - checkpoint_ids)
        if missing_checkpoints or unexpected_checkpoints:
            raise GateFailure(
                "checkpoint census mismatch: missing="
                + repr(missing_checkpoints)
                + ", unexpected="
                + repr(unexpected_checkpoints)
            )
        uncovered_assets = sorted(declared_runtime - requested_assets)
        undeclared_requests = sorted(requested_assets - declared_runtime)
        violations = {
            "network": network_violations,
            "websocket": websocket_events,
            "pageerror": page_errors,
            "console": console_errors,
            "static": static_failures,
            "http": http_failures,
            "broken_images": broken_images,
            "uncovered_declared_assets": uncovered_assets,
            "undeclared_requested_assets": undeclared_requests,
        }
        nonempty = {key: value for key, value in violations.items() if value}
        if nonempty:
            raise GateFailure(
                "browser/runtime closure violations: "
                + json.dumps(nonempty, ensure_ascii=False, sort_keys=True)[:12000]
            )
        census = {
            "schema_version": "amazon-offline-clone.browser-smoke.v1",
            "browser": str(browser_path),
            "checkpoint_results": checkpoint_results,
            "journey": [
                "two-sibling-variant-cart",
                "unknown-email-register-verify",
                "declined-card-approved-bank-retry",
                "place-ship-deliver-return-refund",
            ],
            "visited_pages": len(visits),
            "asset_census": {
                "declared_required": len(declared_runtime),
                "browser_requested": len(requested_assets),
                "production_literal_references": len(parser_proven),
                "covered_union": len(declared_runtime & requested_assets),
                "requested_urls": sorted(requested_assets),
                "census_sha256": hashlib.sha256(
                    "\n".join(sorted(requested_assets)).encode("utf-8")
                ).hexdigest(),
            },
            "asset_route_failures": asset_route_failures,
            "violations": {key: 0 for key in violations},
        }
        raw_root = (
            site_root
            / "artifacts"
            / "offline-clone"
            / "acceptance"
            / "raw"
        )
        browser_coverage = {
            "frontend-checkpoints": sorted(checkpoint_ids),
            "frontend-current-partial-checkpoints": None,
            "frontend-inferred-checkpoints": None,
            "frontend-unavailable-checkpoints": None,
            "core-journeys": None,
            "p0-browser-invariants": [
                "identity.verification.recovery",
                "checkout.retry.idempotent",
                "orders.lifecycle.monotone",
            ],
        }
        browser_subject_ids = sorted(
            {
                item
                for dimension in _verified_coverage(site_root, browser_coverage)
                for item in dimension["items"]
            }
        )
        mainline_journey_records = [
            {
                "id": "shopping.success",
                "status": "passed",
                "steps_total": 8,
                "steps_passed": 8,
            },
            {
                "id": "shopping.failure",
                "status": "passed",
                "steps_total": 4,
                "steps_passed": 4,
            },
            {
                "id": "shopping.recovery",
                "status": "passed",
                "steps_total": 8,
                "steps_passed": 8,
            },
        ]
        witnessed_ids = {record["id"] for record in mainline_journey_records}
        browser_journey_records = [
            *mainline_journey_records,
            *(
                {
                    "id": subject_id,
                    "status": "passed",
                    "steps_total": 1,
                    "steps_passed": 1,
                }
                for subject_id in browser_subject_ids
                if subject_id not in witnessed_ids
            ),
        ]
        if {record["id"] for record in browser_journey_records} != set(
            browser_subject_ids
        ):
            raise GateFailure("browser raw records do not witness every coverage subject")
        browser_trace_path = raw_root / "browser" / "trace.json"
        _write_raw_json(
            browser_trace_path,
            {
                "schema_version": "offline-clone.raw.browser-trace.v1",
                "subject_ids": browser_subject_ids,
                "checkpoint_results": {
                    checkpoint_id: {
                        "mode": result["mode"],
                        "path": urlsplit(result["url"]).path,
                    }
                    for checkpoint_id, result in sorted(checkpoint_results.items())
                },
                "journeys": browser_journey_records,
                "visited": [
                    {
                        "label": str(visit["label"]),
                        "status": int(visit["status"]),
                        "path": urlsplit(str(visit["url"])).path,
                    }
                    for visit in visits
                ],
                "browser_family": browser_path.name,
                "isolated_database": True,
            },
        )
        network_subject_ids = sorted(
            [*required_runtime_asset_ids, "assets.local-only.closed"]
        )
        network_log_path = raw_root / "network" / "network-log.json"
        _write_raw_json(
            network_log_path,
            {
                "schema_version": "offline-clone.raw.network-log.v1",
                "subject_ids": network_subject_ids,
                "requests": request_events,
                "asset_closure": {
                    "declared_required": sorted(declared_runtime),
                    "browser_requested": sorted(requested_assets),
                    "production_literal_references": sorted(parser_proven),
                    "covered_count": len(declared_runtime & requested_assets),
                },
                "violation_counts": {key: 0 for key in violations},
            },
        )
        _write_acceptance_artifact(
            site_root,
            kind="browser",
            summary=(
                "A local Chrome/Edge session exercised all 16 frozen route states and "
                "the shopping, identity, retry, delivery, return, and refund path on an "
                "isolated temporary database."
            ),
            metrics={
                "checks_total": len(checkpoint_results) + len(census["journey"]),
                "checks_passed": len(checkpoint_results) + len(census["journey"]),
                "checks_failed": 0,
                "checkpoints_total": len(checkpoint_results),
                "checkpoints_passed": len(checkpoint_results),
                "checkpoints_failed": 0,
                "journeys_total": len(browser_journey_records),
                "journeys_passed": len(browser_journey_records),
                "journeys_failed": 0,
                "checkpoint_routes_observed": len(checkpoint_results),
                "checkpoint_semantics_verified": len(checkpoint_results),
                "journey_segments": len(census["journey"]),
                "visited_pages": len(visits),
            },
            boundaries=[
                "The comparison checkpoint adds two complete sibling selections and requires two independently rendered comparison columns.",
                "Source-direct pixel similarity is assigned to the visual artifact rather than inferred from browser functional success.",
                "All identity, payment, delivery, and mail effects are deterministic local simulations on a disposable database.",
            ],
            coverage=browser_coverage,
            raw_artifacts=[
                (
                    browser_trace_path,
                    "browser-trace",
                    "application/json",
                    "Trace retains only local route paths, stable checkpoint labels, status codes, and aggregate journey outcomes; query values and entered identity data are omitted.",
                    browser_subject_ids,
                )
            ],
        )
        _write_acceptance_artifact(
            site_root,
            kind="network",
            summary=(
                "The release browser made no nonlocal request and the required runtime "
                "asset closure was fully covered by successful observed browser requests."
            ),
            metrics={
                "checks_total": len(violations),
                "checks_passed": len(violations),
                "checks_failed": 0,
                "requests_total": len(request_events),
                "forbidden_remote_requests": len(network_violations),
                "network_failures": len(http_failures),
                "declared_runtime_assets": len(declared_runtime),
                "browser_requested_assets": len(requested_assets),
                "parser_proven_assets": len(parser_proven),
                "covered_runtime_assets": len(declared_runtime & requested_assets),
                "nonlocal_requests": len(network_violations),
                "websocket_events": len(websocket_events),
                "static_failures": len(static_failures),
                "broken_images": len(broken_images),
            },
            boundaries=[
                "Only successful browser requests count toward the 454-path reverse-reachability numerator; production literals and manifest provenance never supplement it.",
                "HTTP(S) routing and browser WebSocket events are fail-closed; static production parsing also rejects WebSocket and WebRTC API references. OS-level UDP isolation is outside this process-level gate.",
                "The evaluated default release profile removes SMTP configuration and uses the local outbox. The opt-in external SMTP adapter is outside this browser/resource-network certification.",
                "The network gate covers the bounded 454-path shopping closure, not every Amazon-controlled resource in the source marketplace.",
            ],
            coverage={
                "required-runtime-asset-paths": None,
                "p0-network-invariants": ["assets.local-only.closed"],
            },
            raw_artifacts=[
                (
                    network_log_path,
                    "network-log",
                    "application/json",
                    "Network log contains only local response paths and status outcomes plus concrete asset-path closure; origins, headers, bodies, and query values are excluded.",
                    network_subject_ids,
                )
            ],
        )
        print(json.dumps(census, ensure_ascii=False, indent=2, sort_keys=True))


def _retired_shared_self_audit(site_root: Path) -> None:
    """Legacy same-implementation pass retained only for historical readability."""

    raise GateFailure(
        "shared self-audit is retired; release uses offline_clone_independent_audit.py"
    )

    validate_source(site_root)
    verify_search_backfill(site_root)
    audit_static(site_root)
    manifest = read_object(site_root / "source-assets" / "offline-clone-manifest.json")
    if manifest.get("remote_runtime_policy") != "forbidden":
        raise GateFailure("independent audit requires forbidden remote runtime requests")
    if manifest.get("closure_status") != "declared":
        raise GateFailure("independent audit requires a declared asset closure")
    rows = manifest.get("assets")
    if not isinstance(rows, list) or len(rows) != 454:
        raise GateFailure("independent audit requires exactly 454 asset rows")
    seen_ids: set[str] = set()
    seen_source_paths: set[str] = set()
    seen_runtime_paths: set[str] = set()
    base_asset_ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise GateFailure(f"asset row {index} is not an object")
        asset_id = stable_id(row.get("id"), f"asset row {index} id")
        if asset_id in seen_ids:
            raise GateFailure(f"duplicate asset id in independent audit: {asset_id}")
        seen_ids.add(asset_id)
        if not asset_id.startswith("runtime-alias."):
            base_asset_ids.append(asset_id)
        if row.get("required") is not True:
            raise GateFailure(f"asset row is not required: {asset_id}")
        expected_bytes = row.get("bytes")
        expected_sha256 = row.get("sha256")
        if type(expected_bytes) is not int or expected_bytes < 1:
            raise GateFailure(f"asset byte count is invalid: {asset_id}")
        if not isinstance(expected_sha256, str) or re.fullmatch(
            r"[a-f0-9]{64}", expected_sha256
        ) is None:
            raise GateFailure(f"asset digest is invalid: {asset_id}")
        for field, prefix, seen in (
            ("source_path", "source-assets/", seen_source_paths),
            ("runtime_path", "clone/static/assets/", seen_runtime_paths),
        ):
            relative = row.get(field)
            if not isinstance(relative, str) or not relative.startswith(prefix):
                raise GateFailure(f"asset {asset_id} has invalid {field}")
            if relative in seen:
                raise GateFailure(f"asset {asset_id} duplicates {field}: {relative}")
            seen.add(relative)
            path = inside(site_root, relative)
            if not path.is_file() or path.is_symlink():
                raise GateFailure(f"asset {asset_id} has missing or redirected {field}")
            if path.stat().st_size != expected_bytes:
                raise GateFailure(f"asset {asset_id} changed byte count at {field}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                raise GateFailure(f"asset {asset_id} changed digest at {field}")
    if len(base_asset_ids) != 452:
        raise GateFailure(
            f"independent audit expected 452 scoped source records, got {len(base_asset_ids)}"
        )
    direct_pdp_ids: set[str] = set()
    for row in rows:
        source_path = str(row.get("source_path", ""))
        match = re.search(
            r"pdp-(?:home|books|beauty|computers|kitchen|toys)/([^/]+)/",
            source_path,
            re.I,
        )
        if match:
            direct_pdp_ids.add("asin." + match.group(1).casefold())
    required_direct_pdp_ids = set(
        _coverage_dimensions(site_root)["source-direct-pdp-products"]
    )
    if direct_pdp_ids != required_direct_pdp_ids:
        raise GateFailure(
            "source-direct PDP denominator does not match source asset provenance: "
            f"observed={sorted(direct_pdp_ids)}, required={sorted(required_direct_pdp_ids)}"
        )
    print(
        "independent release audit verified frozen scope plus 454 source/runtime "
        "byte pairs, including 452 scoped source records and two documented aliases"
    )
    reviewer_method = "separate-release-command-byte-and-contract-reinspection"
    independence_boundary = (
        "The command repeats source, search-backfill, static, and byte-pair checks "
        "without consuming visual, browser, migration, or full-suite summaries."
    )
    audit_report_path = (
        site_root
        / "artifacts"
        / "offline-clone"
        / "acceptance"
        / "raw"
        / "independent-audit"
        / "audit-report.json"
    )
    _write_raw_json(
        audit_report_path,
        {
            "schema_version": "amazon-offline-clone.independent-audit.v1",
            "reviewer_method": reviewer_method,
            "independence_boundary": independence_boundary,
            "checks": [
                {"id": "frozen-scope", "status": "passed"},
                {"id": "search-backfill", "status": "passed"},
                {"id": "static-closure", "status": "passed"},
                {
                    "id": "source-runtime-byte-pairs",
                    "status": "passed",
                    "pairs": len(rows),
                    "scoped_source_records": len(base_asset_ids),
                    "runtime_aliases": len(rows) - len(base_asset_ids),
                    "source_direct_pdp_products": len(direct_pdp_ids),
                },
            ],
            "findings": [],
        },
    )
    _write_acceptance_artifact(
        site_root,
        kind="independent-audit",
        summary=(
            "The release attempt independently revalidated the frozen shopping scope, "
            "search backfill, static closure, and every source/runtime byte pair."
        ),
        metrics={
            "checks_total": len(rows) + 3,
            "checks_passed": len(rows) + 3,
            "checks_failed": 0,
            "asset_pairs_verified": len(rows),
            "scoped_source_records": len(base_asset_ids),
            "runtime_aliases": len(rows) - len(base_asset_ids),
            "source_direct_pdp_products": len(direct_pdp_ids),
            "findings_total": 0,
            "blocking_findings": 0,
            "reviewer_method": reviewer_method,
            "independence_boundary": independence_boundary,
        },
        boundaries=[
            "This is an independent contract and byte-closure audit, not a browser, visual, or deep-semantic test.",
            "The two runtime aliases remain separately documented and do not inflate the 452-record source denominator.",
            "The archived unreferenced image is outside runtime closure and outside every coverage numerator.",
        ],
        coverage={
            "scoped-source-asset-records": None,
            "source-direct-pdp-products": None,
        },
        raw_artifacts=[
            (
                audit_report_path,
                "audit-report",
                "application/json",
                "Report contains only stable check identifiers, counts, and release-command independence boundaries; no runtime state or request content is included.",
            )
        ],
        extra_fields={
            "reviewer_method": reviewer_method,
            "independence_boundary": independence_boundary,
        },
    )


def _run_unittest_command(
    site_root: Path,
    arguments: list[str],
    *,
    timeout_seconds: int,
    extra_python_path: Path | None = None,
) -> tuple[int, str]:
    clone_root = site_root / "clone"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_python_path is not None:
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(extra_python_path) + (
            os.pathsep + existing if existing else ""
        )
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", *arguments],
            cwd=clone_root,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateFailure(
            f"unittest acceptance command exceeded {timeout_seconds} seconds"
        ) from exc
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise GateFailure(f"unittest acceptance command exited {completed.returncode}")
    match = re.search(r"\bRan (\d+) tests?\b", completed.stdout + completed.stderr)
    if match is None:
        raise GateFailure("unittest acceptance output did not report a test count")
    return int(match.group(1)), completed.stdout + completed.stderr


def _canonical_test_ref(qualified_name: str) -> str:
    """Convert unittest's dotted identity to the frozen source-reference form."""

    parts = qualified_name.split(".")
    if len(parts) < 3 or not parts[-1].startswith("test_"):
        raise GateFailure(f"cannot canonicalize unittest identity: {qualified_name!r}")
    method = parts[-1]
    class_name = parts[-2]
    module_parts = parts[:-2]
    if module_parts[0] == "tests":
        module_parts = module_parts[1:]
    if not module_parts or any(not part.isidentifier() for part in module_parts):
        raise GateFailure(f"cannot canonicalize unittest module: {qualified_name!r}")
    relative_module = "/".join(module_parts)
    return f"clone/tests/{relative_module}.py::{class_name}.{method}"


def _passed_test_refs(runner_output: str) -> list[str]:
    refs: list[str] = []
    pattern = re.compile(r"^(test_[A-Za-z0-9_]+) \(([^)]+)\) \.\.\. ok$")
    for line in runner_output.splitlines():
        match = pattern.fullmatch(line.strip())
        if match is None:
            continue
        qualified = match.group(2)
        if qualified.rsplit(".", 1)[-1] != match.group(1):
            raise GateFailure(f"unittest output identity is inconsistent: {line!r}")
        refs.append(_canonical_test_ref(qualified))
    if len(set(refs)) != len(refs):
        raise GateFailure("unittest output contains duplicate canonical test identities")
    return refs


def _asin_subjects(asins: set[str] | frozenset[str]) -> list[str]:
    return sorted("asin." + asin.casefold() for asin in asins)


def _derive_full_suite_coverage(site_root: Path) -> dict[str, list[str]]:
    """Enumerate candidate facts after the suite rather than echoing scope rows."""

    clone_root = (site_root / "clone").resolve()
    if str(clone_root) not in sys.path:
        sys.path.insert(0, str(clone_root))
    try:
        import render as clone_render
        import review_catalog as clone_review_catalog
        import server as clone_server
        from product_options import load_source_transaction_quote_specs
        from store import Store
    except Exception as exc:
        raise GateFailure(f"cannot import candidate coverage registries: {exc}") from exc

    dimensions = _coverage_dimensions(site_root)
    with tempfile.TemporaryDirectory(
        prefix="amazon-full-suite-coverage-", ignore_cleanup_errors=True
    ) as temp_name:
        store = Store(
            Path(temp_name) / "amazon.sqlite3",
            clone_root / "schema.sql",
            clone_root / "fixtures",
        )
        store.reset()
        with store.connect() as connection:
            database_asins = {
                str(row[0])
                for row in connection.execute(
                    "SELECT asin FROM catalog_products UNION SELECT asin FROM commerce_offers"
                )
            }
        known_asins = set(clone_server.HOME_PRODUCT_CATALOG) | database_asins
        reachable_asins = {
            asin
            for asin in known_asins
            if clone_server.product_for_pdp(store, asin) is not None
        }
        quote_asins = set(
            load_source_transaction_quote_specs(clone_root / "fixtures")
        )
        review_asins = set(clone_review_catalog.supported_review_asins())
        comparable_asins = set(store.compare_eligible_asins())

        # Richness is a frozen selection, so prove each member against the
        # actual candidate renderer instead of deriving it from that selection.
        rich_expected = {
            item.removeprefix("asin.").upper()
            for item in dimensions["rich-pdp-products"]
        }
        rich_asins: set[str] = set()
        for asin in sorted(rich_expected):
            product = clone_server.product_for_pdp(store, asin)
            if product is None:
                raise GateFailure(f"rich-PDP probe cannot resolve {asin}")
            rendered = clone_render.product_page(product, 0, False)
            required_landmarks = (
                f'data-asin="{asin}"',
                'id="productTitle"',
                "data-product-price",
                "data-product-add-to-cart",
            )
            if any(landmark not in rendered for landmark in required_landmarks):
                raise GateFailure(f"rich-PDP probe is missing purchase landmarks: {asin}")
            rich_asins.add(asin)

    observed = {
        "known-products": _asin_subjects(known_asins),
        "reachable-products": _asin_subjects(reachable_asins),
        "rich-pdp-products": _asin_subjects(rich_asins),
        "purchasable-products": _asin_subjects(quote_asins),
        "review-backed-products": _asin_subjects(review_asins),
        "comparable-products": _asin_subjects(comparable_asins),
    }
    for dimension_id, actual in observed.items():
        required = sorted(dimensions[dimension_id])
        if actual != required:
            raise GateFailure(
                f"candidate enumeration does not match frozen {dimension_id}: "
                f"observed={len(actual)}, required={len(required)}"
            )
    return observed


def run_migration_copy_gate(site_root: Path) -> None:
    """Run only existing legacy-schema tests, all of which create disposable copies."""

    test_names = [
        "tests.test_auth_backend.AuthBackendTests.test_existing_browser_session_table_is_migrated_in_place",
        "tests.test_auth_backend.AuthBackendTests.test_legacy_local_only_outboxes_are_migrated_without_losing_mail",
        "tests.test_cart_backend.CartBackendTests.test_pre_option_database_is_migrated_without_rebuilding_user_tables",
        "tests.test_cart_backend.CartBackendTests.test_legacy_line_identity_constraints_migrate_without_cart_data_loss",
        "tests.test_checkout_backend.CheckoutBackendTests.test_checkout_only_address_schema_is_migrated_and_backfills_one_default",
        "tests.test_checkout_backend.CheckoutBackendTests.test_payment_schema_migration_preserves_legacy_order_and_adds_declines",
        "test_order_lifecycle.OrderLifecycleTests.test_legacy_placed_order_migrates_without_rewriting_placement_facts",
    ]
    count, runner_output = _run_unittest_command(
        site_root,
        ["-v", *test_names],
        timeout_seconds=1200,
        extra_python_path=site_root / "clone" / "tests",
    )
    if count != len(test_names):
        raise GateFailure(
            f"migration-copy gate expected {len(test_names)} tests, got {count}"
        )
    passed_refs = _passed_test_refs(runner_output)
    if len(passed_refs) != count:
        raise GateFailure(
            f"migration-copy gate expected {count} canonical passed tests, got "
            f"{len(passed_refs)}"
        )
    migration_subject_ids = sorted(["migration.copy-only", *passed_refs])
    migration_scenarios = [
        {
            "id": test_ref,
            "status": "passed",
            "copies_tested": 1,
            "schema_checks": 1,
            "data_checks": 1,
        }
        for test_ref in passed_refs
    ]
    migration_scenarios.append(
        {
            "id": "migration.copy-only",
            "status": "passed",
            "copies_tested": 0,
            "schema_checks": 0,
            "data_checks": 0,
        }
    )
    legacy_contract_digest = hashlib.sha256()
    for test_ref in passed_refs:
        relative_file = test_ref.split("::", 1)[0]
        test_path = inside(site_root, relative_file)
        if not test_path.is_file():
            raise GateFailure(f"migration test source is unavailable: {relative_file}")
        legacy_contract_digest.update(test_ref.encode("utf-8"))
        legacy_contract_digest.update(b"\0")
        legacy_contract_digest.update(test_path.read_bytes())
        legacy_contract_digest.update(b"\0")
    post_schema_sha256 = hashlib.sha256(
        (site_root / "clone" / "schema.sql").read_bytes()
    ).hexdigest()
    raw_root = (
        site_root
        / "artifacts"
        / "offline-clone"
        / "acceptance"
        / "raw"
        / "migration"
    )
    pre_state_path = raw_root / "pre-state.json"
    post_state_path = raw_root / "post-state.json"
    migration_log_path = raw_root / "migration-log.json"
    _write_raw_json(
        pre_state_path,
        {
            "schema_version": "offline-clone.raw.state-inventory.v1",
            "subject_ids": passed_refs,
            "state_model": "stateful",
            "persistence_surfaces": ["test-owned-temporary-sqlite-copies"],
            "schema_fingerprint": legacy_contract_digest.hexdigest(),
            "row_counts": {"legacy_scenarios": count},
            "served_runtime_access": "forbidden",
        },
    )
    _write_raw_json(
        post_state_path,
        {
            "schema_version": "offline-clone.raw.state-inventory.v1",
            "subject_ids": passed_refs,
            "state_model": "stateful",
            "persistence_surfaces": ["test-owned-temporary-sqlite-copies"],
            "schema_fingerprint": post_schema_sha256,
            "row_counts": {"passed_scenarios": count, "failed_scenarios": 0},
            "temporary_copies_disposed_by_tests": True,
            "served_runtime_access": "none",
        },
    )
    _write_raw_json(
        migration_log_path,
        {
            "schema_version": "offline-clone.raw.migration-log.v1",
            "subject_ids": migration_subject_ids,
            "runner": "python-unittest-selected-existing-migration-tests",
            "runner_output_sha256": hashlib.sha256(
                runner_output.encode("utf-8")
            ).hexdigest(),
            "scenarios": migration_scenarios,
        },
    )
    _write_acceptance_artifact(
        site_root,
        kind="migration",
        summary=(
            "Seven existing legacy-schema tests migrated disposable database copies "
            "without data loss or immutable-order rewrites."
        ),
        metrics={
            "checks_total": count,
            "checks_passed": count,
            "checks_failed": 0,
            "stateful": True,
            "disposable_database_tests": count,
            "migration_scenarios_total": len(migration_scenarios),
            "migration_scenarios_passed": len(migration_scenarios),
            "migration_scenarios_failed": 0,
            "copies_tested": count,
            "schema_checks": count,
            "data_checks": count,
        },
        boundaries=[
            "The migration gate creates legacy databases only inside test-owned temporary directories.",
            "The served clone/runtime and clone/.runtime databases are excluded from release inputs and are never opened, copied, fingerprinted, or mutated by this gate.",
            "This gate proves the enumerated legacy schemas, not arbitrary future schemas.",
        ],
        coverage={"p0-migration-invariants": ["migration.copy-only"]},
        raw_artifacts=[
            (
                pre_state_path,
                "pre-state",
                "application/json",
                "Pre-state enumerates only test identifiers and the disposable-copy boundary; it does not inspect or identify any served runtime database.",
                passed_refs,
            ),
            (
                post_state_path,
                "post-state",
                "application/json",
                "Post-state retains only aggregate pass counts and confirms test-owned temporary disposal; no database rows or identity data are retained.",
                passed_refs,
            ),
            (
                migration_log_path,
                "migration-log",
                "application/json",
                "The migration log retains canonical test identities, aggregate checks, and only a digest of runner output; request payloads, database rows, and entered values are absent.",
                migration_subject_ids,
            ),
        ],
    )


def run_full_suite(site_root: Path) -> None:
    count, runner_output = _run_unittest_command(
        site_root,
        ["discover", "-s", "tests", "-v"],
        timeout_seconds=3600,
    )
    if count != 328:
        raise GateFailure(f"full Amazon discovery expected 328 tests, got {count}")
    passed_test_refs = _passed_test_refs(runner_output)
    if len(passed_test_refs) != count:
        raise GateFailure(
            f"full-suite verbose result expected {count} passed test rows, "
            f"got {len(passed_test_refs)}"
        )
    invariants = read_object(site_root / "scope" / "invariants.json")
    required_p0_test_refs = {
        test_ref
        for invariant in invariants.get("invariants", [])
        if isinstance(invariant, dict) and invariant.get("priority") == "p0"
        for field in ("positive_test_refs", "negative_test_refs")
        for test_ref in invariant.get(field, [])
        if isinstance(test_ref, str)
    }
    missing_p0_tests = sorted(required_p0_test_refs - set(passed_test_refs))
    if missing_p0_tests:
        raise GateFailure(
            "full-suite output is missing frozen P0 test refs: "
            + ", ".join(missing_p0_tests)
        )
    full_suite_coverage = _derive_full_suite_coverage(site_root)
    full_suite_coverage["p0-full-suite-invariants"] = [
        "variants.complete-quote.identity"
    ]
    coverage_witness_tests = {
        "clone/tests/test_review_integration.py::ReviewIntegrationTests.test_all_known_product_review_destinations_are_live": [
            *full_suite_coverage["known-products"],
            *full_suite_coverage["reachable-products"],
            *full_suite_coverage["review-backed-products"],
        ],
        "clone/tests/test_home_frontend.py::HomeFrontendTests.test_every_home_rail_asin_has_a_bare_reachable_pdp_without_invented_ssd_copy": full_suite_coverage[
            "rich-pdp-products"
        ],
        "clone/tests/test_product_options.py::ProductTransactionQuoteTests.test_quote_catalog_contains_only_evidence_backed_complete_combinations": full_suite_coverage[
            "purchasable-products"
        ],
        "clone/tests/test_compare_backend.py::CompareBackendTests.test_eligibility_malformed_forms_and_same_origin_are_enforced": full_suite_coverage[
            "comparable-products"
        ],
        "clone/tests/test_checkout_backend.py::CheckoutBackendTests.test_checkout_and_order_preserve_two_variants_of_the_same_asin": full_suite_coverage[
            "p0-full-suite-invariants"
        ],
    }
    missing_witness_tests = sorted(set(coverage_witness_tests) - set(passed_test_refs))
    if missing_witness_tests:
        raise GateFailure(
            "full-suite coverage witness tests did not run: "
            + ", ".join(missing_witness_tests)
        )
    coverage_subject_ids = {
        item for items in full_suite_coverage.values() for item in items
    }
    full_suite_subject_ids = sorted(coverage_subject_ids | set(passed_test_refs))
    test_result_path = (
        site_root
        / "artifacts"
        / "offline-clone"
        / "acceptance"
        / "raw"
        / "full-suite"
        / "test-result.json"
    )
    _write_raw_json(
        test_result_path,
        {
            "schema_version": "offline-clone.raw.test-result.v1",
            "subject_ids": full_suite_subject_ids,
            "runner": "python-unittest-discovery",
            "tests": [
                {
                    "id": test_ref,
                    "status": "passed",
                    "subject_ids": sorted(
                        {test_ref, *coverage_witness_tests.get(test_ref, [])}
                    ),
                }
                for test_ref in passed_test_refs
            ],
            "coverage_probes": [
                {
                    "dimension_id": dimension_id,
                    "subject_ids": items,
                    "status": "passed",
                }
                for dimension_id, items in sorted(full_suite_coverage.items())
            ],
            "runner_output_sha256": hashlib.sha256(
                runner_output.encode("utf-8")
            ).hexdigest(),
        },
    )
    _write_acceptance_artifact(
        site_root,
        kind="full-suite",
        summary=(
            "The complete 328-test Amazon clone discovery suite passed, covering "
            "catalog breadth, variants, cart identity, comparison, identity, reviews, "
            "checkout, payment simulation, mail adapters, and order lifecycle."
        ),
        metrics={
            "checks_total": count,
            "checks_passed": count,
            "checks_failed": 0,
            "discovered_tests": count,
            "tests_discovered": count,
            "tests_passed": count,
            "tests_failed": 0,
        },
        boundaries=[
            "Unit and integration tests prove the frozen local semantics; they do not prove source-site pixel fidelity or unrestricted marketplace breadth.",
            "External mail, payment, seller, and fulfillment systems remain adapters or deterministic local simulations.",
            "Test databases are disposable; release acceptance excludes served runtime state.",
        ],
        coverage=full_suite_coverage,
        raw_artifacts=[
            (
                test_result_path,
                "test-result",
                "application/json",
                "The report retains canonical unittest identifiers, independently enumerated candidate coverage subjects, aggregate outcomes, and a digest of runner output; test-owned state and request values are excluded.",
                full_suite_subject_ids,
            )
        ],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "source",
            "search-backfill",
            "static-offline",
            "visual-acceptance",
            "visual-acceptance-worker",
            "browser-smoke",
            "browser-smoke-worker",
            "migration-copy",
            "full-suite",
        ),
    )
    parser.add_argument("--site-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        site_root = args.site_root.resolve(strict=True)
        if args.command == "source":
            validate_source(site_root)
        elif args.command == "search-backfill":
            verify_search_backfill(site_root)
        elif args.command == "static-offline":
            audit_static(site_root)
        elif args.command == "visual-acceptance":
            run_visual_acceptance(site_root)
        elif args.command == "visual-acceptance-worker":
            visual_acceptance_worker(site_root)
        elif args.command == "browser-smoke":
            run_browser_smoke(site_root)
        elif args.command == "browser-smoke-worker":
            browser_smoke_worker(site_root)
        elif args.command == "migration-copy":
            run_migration_copy_gate(site_root)
        else:
            run_full_suite(site_root)
    except (GateFailure, OSError) as exc:
        print(f"Amazon offline-clone gate failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
