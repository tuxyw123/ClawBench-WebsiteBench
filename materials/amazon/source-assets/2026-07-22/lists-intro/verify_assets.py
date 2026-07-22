from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA = "amazon-lists-intro-asset-manifest.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "materials" / "amazon" / "clone").is_dir():
            return candidate
    raise VerificationError("repository root not found")


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise VerificationError(f"invalid relative path: {relative!r}")
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise VerificationError(f"path escapes root: {relative}")
    return target


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise VerificationError("asset is not a valid PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise VerificationError("asset is not a valid JPEG")
    offset = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            if width > 0 and height > 0:
                return width, height
        offset += segment_length
    raise VerificationError("JPEG dimensions were not found")


def _verify_file(path: Path, entry: dict[str, Any], side: str) -> bytes:
    if not path.is_file():
        raise VerificationError(f"{side} asset is missing: {path}")
    data = path.read_bytes()
    name = str(entry.get("filename") or "")
    if len(data) != entry.get("bytes"):
        raise VerificationError(f"{side} byte count changed: {name}")
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise VerificationError(f"{side} SHA-256 changed: {name}")
    mime = entry.get("mime_type")
    dimensions = (
        _png_dimensions(data) if mime == "image/png"
        else _jpeg_dimensions(data) if mime == "image/jpeg"
        else None
    )
    if dimensions is None:
        raise VerificationError(f"unsupported manifest MIME: {name}")
    if dimensions != (entry.get("width"), entry.get("height")):
        raise VerificationError(f"{side} dimensions changed: {name}")
    return data


def verify() -> int:
    capture_root = Path(__file__).resolve().parent
    repo_root = _repo_root()
    manifest = _read_object(capture_root / "manifest.json")
    evidence = _read_object(capture_root / "evidence.json")
    if manifest.get("schema") != EXPECTED_SCHEMA:
        raise VerificationError("unsupported manifest schema")
    if evidence.get("asset_manifest") != "manifest.json" or evidence.get("asset_verifier") != "verify_assets.py":
        raise VerificationError("evidence does not bind the manifest and verifier")
    entries = manifest.get("assets")
    if manifest.get("asset_count") != 12 or not isinstance(entries, list) or len(entries) != 12:
        raise VerificationError("asset count does not match")

    expected_names: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise VerificationError("manifest asset entry must be an object")
        entry = raw_entry
        filename = entry.get("filename")
        digest = entry.get("sha256")
        if (
            not isinstance(filename, str)
            or not filename
            or filename in expected_names
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise VerificationError("invalid manifest asset identity")
        source_path = _inside(capture_root, str(entry.get("source_asset_path") or ""))
        runtime_relative = str(entry.get("runtime_asset_path") or "")
        if not runtime_relative.startswith("/static/"):
            raise VerificationError(f"runtime path is not local static: {filename}")
        runtime_path = _inside(
            repo_root / "materials" / "amazon" / "clone" / "static",
            runtime_relative.removeprefix("/static/"),
        )
        source_data = _verify_file(source_path, entry, "source")
        runtime_data = _verify_file(runtime_path, entry, "runtime")
        if source_data != runtime_data:
            raise VerificationError(f"source/runtime bytes differ: {filename}")
        expected_names.add(filename)

    source_names = {
        path.name for path in capture_root.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    }
    runtime_root = (
        repo_root / "materials" / "amazon" / "clone" / "static" / "assets"
        / "source-current" / "2026-07-22" / "lists-intro"
    )
    runtime_names = {
        path.name for path in runtime_root.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    }
    if source_names != expected_names or runtime_names != expected_names:
        raise VerificationError("source/runtime asset filename sets differ")
    print(
        "verified 12 Lists assets: source/runtime bytes, MIME, dimensions, "
        "paths, and SHA-256 match"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(verify())
    except VerificationError as exc:
        print(f"Lists asset verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
