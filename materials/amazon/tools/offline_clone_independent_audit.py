#!/usr/bin/env python3
"""Independent stdlib-only release auditor for the Amazon worked adapter.

This file intentionally does not import ``offline_clone_gate`` or candidate
modules.  It provides implementation/process separation, not third-party or
organizational independence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STABLE_ID = re.compile(r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class AuditFailure(ValueError):
    pass


def strict_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise AuditFailure(f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditFailure(f"cannot read strict JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditFailure(f"JSON root is not an object: {path}")
    return value


def inside(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or relative.startswith("/"):
        raise AuditFailure(f"unsafe relative path: {relative!r}")
    path_part = relative.split("#", 1)[0]
    resolved = (root / path_part).resolve()
    if resolved != root and root not in resolved.parents:
        raise AuditFailure(f"path escapes site root: {relative}")
    return resolved


def digest_file(path: Path) -> tuple[int, str]:
    if not path.is_file() or path.is_symlink():
        raise AuditFailure(f"required regular file is missing or redirected: {path}")
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise AuditFailure(f"file must be a single-link regular file: {path}")
    digest = hashlib.sha256()
    observed = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            observed += len(chunk)
            digest.update(chunk)
    return observed, digest.hexdigest()


def raster_size(path: Path) -> tuple[int, int]:
    """Read a PNG or JPEG raster size without trusting its legacy suffix."""

    with path.open("rb") as stream:
        header = stream.read(24)
        if (
            len(header) == 24
            and header[:8] == b"\x89PNG\r\n\x1a\n"
            and header[12:16] == b"IHDR"
        ):
            return struct.unpack(">II", header[16:24])
        if header[:2] != b"\xff\xd8":
            raise AuditFailure(f"visual source is not a PNG or JPEG raster: {path}")

        stream.seek(2)
        start_of_frame = {
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
        while True:
            marker_prefix = stream.read(1)
            while marker_prefix and marker_prefix != b"\xff":
                marker_prefix = stream.read(1)
            if not marker_prefix:
                break
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker:
                break
            marker_value = marker[0]
            if marker_value in {0x01, *range(0xD0, 0xDA)}:
                continue
            length_bytes = stream.read(2)
            if len(length_bytes) != 2:
                break
            segment_length = struct.unpack(">H", length_bytes)[0]
            if segment_length < 2:
                break
            if marker_value in start_of_frame:
                frame = stream.read(5)
                if len(frame) != 5 or segment_length < 7:
                    break
                height, width = struct.unpack(">HH", frame[1:5])
                if width > 0 and height > 0:
                    return width, height
                break
            stream.seek(segment_length - 2, os.SEEK_CUR)
    raise AuditFailure(f"visual source has no valid raster dimensions: {path}")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def audit(site_root: Path) -> None:
    site_root = site_root.resolve()
    if not site_root.is_dir():
        raise AuditFailure(f"site root is unavailable: {site_root}")

    purpose = strict_json(site_root / "scope" / "purpose.json")
    invariants = strict_json(site_root / "scope" / "invariants.json")
    checkpoints = strict_json(site_root / "scope" / "checkpoints.json")
    coverage = strict_json(site_root / "scope" / "coverage.json")
    if purpose.get("status") != "frozen" or purpose.get("purpose_id") != "amazon.shopping-mainline":
        raise AuditFailure("shopping-mainline purpose is not frozen")
    if invariants.get("status") != "frozen" or checkpoints.get("status") != "frozen":
        raise AuditFailure("invariant/checkpoint contracts are not frozen")
    if coverage.get("status") != "frozen":
        raise AuditFailure("coverage denominator is not frozen")

    dimensions = coverage.get("dimensions")
    if not isinstance(dimensions, list):
        raise AuditFailure("coverage dimensions are unavailable")
    coverage_by_id: dict[str, list[str]] = {}
    for row in dimensions:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise AuditFailure("coverage dimension is malformed")
        required = row.get("required_items")
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise AuditFailure(f"coverage items are malformed: {row.get('id')}")
        if row["id"] in coverage_by_id or len(required) != len(set(required)):
            raise AuditFailure(f"coverage dimension is duplicated: {row['id']}")
        coverage_by_id[row["id"]] = required

    checkpoint_rows = checkpoints.get("checkpoints")
    if not isinstance(checkpoint_rows, list):
        raise AuditFailure("checkpoint rows are unavailable")
    direct_oracles = [
        row
        for row in checkpoint_rows
        if isinstance(row, dict) and row.get("evidence_kind") == "current-direct"
    ]
    if {row.get("id") for row in direct_oracles} != {
        "home.desktop.loaded",
        "search.desktop.filtered",
    }:
        raise AuditFailure("current-direct visual oracle set changed")
    for row in direct_oracles:
        contract = row.get("visual_contract")
        if not isinstance(contract, dict) or contract.get("metric") != "pixel-mae-similarity-v1":
            raise AuditFailure(f"visual contract is malformed: {row.get('id')}")
        threshold = contract.get("threshold")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 < threshold <= 1:
            raise AuditFailure(f"visual threshold is invalid: {row.get('id')}")
        source_path = inside(site_root, str(contract.get("source_artifact_path", "")))
        size, observed_sha256 = digest_file(source_path)
        del size
        if observed_sha256 != contract.get("source_artifact_sha256"):
            raise AuditFailure(f"visual source digest changed: {row.get('id')}")
        viewport = contract.get("viewport")
        region = contract.get("comparison_region")
        if not isinstance(viewport, dict) or raster_size(source_path) != (
            viewport.get("width"),
            viewport.get("height"),
        ):
            raise AuditFailure(f"visual source raster changed: {row.get('id')}")
        if (
            not isinstance(region, dict)
            or any(type(region.get(key)) is not int for key in ("x", "y", "width", "height"))
            or region["x"] < 0
            or region["y"] < 0
            or region["width"] < 1
            or region["height"] < 1
            or region["x"] + region["width"] > viewport["width"]
            or region["y"] + region["height"] > viewport["height"]
        ):
            raise AuditFailure(f"visual comparison region is invalid: {row.get('id')}")

    asset_manifest = strict_json(site_root / "source-assets" / "offline-clone-manifest.json")
    if (
        asset_manifest.get("schema_version") != "offline-clone.assets.v1"
        or asset_manifest.get("remote_runtime_policy") != "forbidden"
        or asset_manifest.get("closure_status") != "declared"
    ):
        raise AuditFailure("asset manifest closure policy changed")
    assets = asset_manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 454:
        raise AuditFailure("asset manifest must contain exactly 454 rows")

    ids: set[str] = set()
    source_paths: set[str] = set()
    runtime_paths: set[str] = set()
    physical_identities: dict[tuple[int, int], str] = {}
    base_ids: list[str] = []
    direct_pdp_ids: set[str] = set()
    for index, row in enumerate(assets):
        if not isinstance(row, dict):
            raise AuditFailure(f"asset row {index} is not an object")
        asset_id = row.get("id")
        if not isinstance(asset_id, str) or STABLE_ID.fullmatch(asset_id) is None or asset_id in ids:
            raise AuditFailure(f"asset id is invalid or duplicated at row {index}")
        ids.add(asset_id)
        if not asset_id.startswith("runtime-alias."):
            base_ids.append(asset_id)
        if row.get("required") is not True:
            raise AuditFailure(f"asset is not required: {asset_id}")
        expected_bytes = row.get("bytes")
        expected_sha256 = row.get("sha256")
        if type(expected_bytes) is not int or expected_bytes < 1 or not isinstance(expected_sha256, str) or SHA256.fullmatch(expected_sha256) is None:
            raise AuditFailure(f"asset byte/hash contract is invalid: {asset_id}")
        source_relative = row.get("source_path")
        runtime_relative = row.get("runtime_path")
        if (
            not isinstance(source_relative, str)
            or not source_relative.startswith("source-assets/")
            or source_relative in source_paths
            or not isinstance(runtime_relative, str)
            or not runtime_relative.startswith("clone/static/assets/")
            or runtime_relative in runtime_paths
        ):
            raise AuditFailure(f"asset path is invalid or duplicated: {asset_id}")
        source_paths.add(source_relative)
        runtime_paths.add(runtime_relative)
        source_info = digest_file(inside(site_root, source_relative))
        runtime_info = digest_file(inside(site_root, runtime_relative))
        if source_info != (expected_bytes, expected_sha256) or runtime_info != source_info:
            raise AuditFailure(f"source/runtime bytes diverge: {asset_id}")
        source_file = inside(site_root, source_relative)
        runtime_file = inside(site_root, runtime_relative)
        if os.path.samefile(source_file, runtime_file):
            raise AuditFailure(f"source/runtime share one physical file: {asset_id}")
        for label, path in (
            (f"{asset_id}:source", source_file),
            (f"{asset_id}:runtime", runtime_file),
        ):
            metadata = path.stat(follow_symlinks=False)
            identity = (int(metadata.st_dev), int(metadata.st_ino))
            previous = physical_identities.setdefault(identity, label)
            if previous != label:
                raise AuditFailure(
                    f"physical file identity is reused by {previous} and {label}"
                )
        match = re.search(
            r"pdp-(?:home|books|beauty|computers|kitchen|toys)/([^/]+)/",
            source_relative,
            re.I,
        )
        if match:
            direct_pdp_ids.add("asin." + match.group(1).casefold())

    if len(base_ids) != 452 or len(ids) - len(base_ids) != 2:
        raise AuditFailure("452 scoped records plus two runtime aliases are required")
    if set(coverage_by_id.get("scoped-source-asset-records", [])) != set(base_ids):
        raise AuditFailure("source-asset denominator does not match independent inventory")
    if set(coverage_by_id.get("required-runtime-asset-paths", [])) != ids:
        raise AuditFailure("runtime-asset denominator does not match independent inventory")
    if set(coverage_by_id.get("source-direct-pdp-products", [])) != direct_pdp_ids:
        raise AuditFailure("source-direct PDP denominator does not match source provenance")

    physical_root = site_root / "clone" / "static" / "assets"
    physical_runtime = {
        "clone/static/assets/" + path.relative_to(physical_root).as_posix()
        for path in physical_root.rglob("*")
        if path.is_file()
    }
    if physical_runtime != runtime_paths or len(physical_runtime) != 454:
        raise AuditFailure("physical runtime closure differs from the 454-path ledger")

    check_rows = [
        {
            "id": "audit.scope-contracts",
            "status": "passed",
            "subject_ids": ["scope.shopping-mainline.bounded"],
        },
        {
            "id": "audit.visual-oracles",
            "status": "passed",
            "subject_ids": sorted(str(row["id"]) for row in direct_oracles),
        },
        {
            "id": "audit.asset-ledger",
            "status": "passed",
            "subject_ids": sorted(ids),
        },
        {
            "id": "audit.byte-pairs",
            "status": "passed",
            "subject_ids": sorted(ids),
        },
        {
            "id": "audit.physical-closure",
            "status": "passed",
            "subject_ids": sorted(ids),
        },
        {
            "id": "audit.direct-pdp-provenance",
            "status": "passed",
            "subject_ids": sorted(direct_pdp_ids),
        },
    ]
    raw_subject_ids = sorted(
        {
            subject_id
            for check in check_rows
            for subject_id in check["subject_ids"]
        }
    )
    reviewer_method = "separate-stdlib-process-independent-reimplementation"
    independence_boundary = (
        "This separate stdlib-only command does not import the primary adapter or candidate "
        "modules. Independence is implementation/process-level, not third-party or organizational."
    )
    raw_path = (
        site_root
        / "artifacts"
        / "offline-clone"
        / "acceptance"
        / "raw"
        / "independent-audit"
        / "audit-report.json"
    )
    atomic_json(
        raw_path,
        {
            "schema_version": "offline-clone.raw.audit-report.v1",
            "subject_ids": raw_subject_ids,
            "reviewer_method": reviewer_method,
            "independence_boundary": independence_boundary,
            "checks": check_rows,
            "findings": [],
            "inventory": {
                "asset_rows": len(assets),
                "scoped_source_records": len(base_ids),
                "runtime_aliases": len(ids) - len(base_ids),
                "physical_runtime_files": len(physical_runtime),
                "source_direct_pdp_products": len(direct_pdp_ids),
                "visual_oracles": len(direct_oracles),
            },
        },
    )

    bindings = {
        name: os.environ.get(name)
        for name in (
            "CLAWBENCH_OFFLINE_CLONE_ATTEMPT_ID",
            "CLAWBENCH_OFFLINE_CLONE_MANIFEST_SHA256",
            "CLAWBENCH_OFFLINE_CLONE_COMMAND_ID",
            "CLAWBENCH_OFFLINE_CLONE_SITE_DIR",
            "CLAWBENCH_OFFLINE_CLONE_MANIFEST",
        )
    }
    if not all(bindings.values()):
        raise AuditFailure("independent audit requires complete release-attempt bindings")
    attempt_id = str(bindings["CLAWBENCH_OFFLINE_CLONE_ATTEMPT_ID"])
    manifest_sha256 = str(bindings["CLAWBENCH_OFFLINE_CLONE_MANIFEST_SHA256"])
    producer = str(bindings["CLAWBENCH_OFFLINE_CLONE_COMMAND_ID"])
    manifest_path = (site_root / "clone.yaml").resolve()
    if (
        re.fullmatch(r"[a-f0-9]{32}", attempt_id) is None
        or SHA256.fullmatch(manifest_sha256) is None
        or producer != "independent-audit"
        or Path(str(bindings["CLAWBENCH_OFFLINE_CLONE_SITE_DIR"])).resolve() != site_root
        or Path(str(bindings["CLAWBENCH_OFFLINE_CLONE_MANIFEST"])).resolve() != manifest_path
        or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != manifest_sha256
    ):
        raise AuditFailure("release-attempt binding does not match the audited manifest")

    raw_bytes, raw_sha256 = digest_file(raw_path)
    summary_path = (
        site_root / "artifacts" / "offline-clone" / "acceptance" / "independent-audit.json"
    )
    atomic_json(
        summary_path,
        {
            "schema_version": "offline-clone.acceptance-evidence.v1",
            "kind": "independent-audit",
            "producer_command_id": producer,
            "gate_attempt_id": attempt_id,
            "manifest_sha256": manifest_sha256,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": "passed",
            "summary": (
                "A separate stdlib implementation revalidated frozen scope/oracles, 454 "
                "source/runtime byte pairs, 454 physical files, and direct-PDP provenance."
            ),
            "metrics": {
                "checks_total": len(check_rows),
                "checks_passed": len(check_rows),
                "checks_failed": 0,
                "findings_total": 0,
                "blocking_findings": 0,
                "asset_pairs_verified": len(assets),
                "scoped_source_records": len(base_ids),
                "runtime_aliases": len(ids) - len(base_ids),
                "physical_runtime_files": len(physical_runtime),
                "source_direct_pdp_products": len(direct_pdp_ids),
                "reviewer_method": reviewer_method,
                "independence_boundary": independence_boundary,
            },
            "reviewer_method": reviewer_method,
            "independence_boundary": independence_boundary,
            "boundaries": [
                "This is independent implementation/process-level reinspection, not third-party or organizational audit assurance.",
                "Browser runtime reachability and semantic behavior remain assigned to their typed network/browser/full-suite evidence.",
                "The two runtime aliases are excluded from the 452-record source numerator.",
            ],
            "verified_coverage": [
                {"dimension_id": "scoped-source-asset-records", "items": sorted(base_ids)},
                {"dimension_id": "source-direct-pdp-products", "items": sorted(direct_pdp_ids)},
            ],
            "raw_artifacts": [
                {
                    "path": raw_path.relative_to(site_root).as_posix(),
                    "sha256": raw_sha256,
                    "bytes": raw_bytes,
                    "media_type": "application/json",
                    "role": "audit-report",
                    "subject_ids": raw_subject_ids,
                    "contains_user_data": False,
                    "sanitization_method": "Only stable contract IDs, aggregate counts, and explicit independence boundaries are retained; no runtime state or user data is read.",
                }
            ],
        },
    )
    print(
        "independent stdlib audit passed: 452 scoped records + 2 aliases = "
        "454 source/runtime pairs and 454 physical runtime files"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        audit(args.site_root)
    except (AuditFailure, OSError) as exc:
        print(f"independent audit failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
