"""Generic source-to-runtime asset closure verification."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .manifest import LoadedManifest, load_asset_manifest, resolve_inside


MIME_ALIASES = {
    "application/font-woff": "font/woff",
    "application/font-woff2": "font/woff2",
    "application/javascript": "text/javascript",
    "application/x-font-woff": "font/woff",
    "application/x-font-woff2": "font/woff2",
    "image/jpg": "image/jpeg",
}


@dataclass(frozen=True)
class AssetIssue:
    asset_id: str | None
    code: str
    message: str
    blocking: bool


@dataclass(frozen=True)
class AssetClosureReport:
    status: str
    closure_status: str
    declared: int
    required: int
    required_verified: int
    p0_required: int
    p0_verified: int
    verified: int
    referenced_assets: int
    reference_edges: int
    unreferenced_required_assets: int
    missing_assets: int
    missing_copies: int
    mismatched_assets: int
    required_closure: float
    p0_closure: float | None
    issues: tuple[AssetIssue, ...]

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["issues"] = [asdict(issue) for issue in self.issues]
        value["passed"] = self.passed
        return value

    # Keep the original Python attributes as unambiguous compatibility aliases.
    @property
    def referenced(self) -> int:
        return self.referenced_assets

    @property
    def missing(self) -> int:
        return self.missing_copies

    @property
    def mismatched(self) -> int:
        return self.mismatched_assets


def _normalized_mime(value: str) -> str:
    lowered = value.casefold().split(";", 1)[0].strip()
    return MIME_ALIASES.get(lowered, lowered)


def _avif_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 16 or data[4:8] != b"ftyp" or b"avif" not in data[8:32]:
        return None
    marker = 0
    while True:
        marker = data.find(b"ispe", marker)
        if marker < 0:
            return None
        if marker + 16 <= len(data):
            width = int.from_bytes(data[marker + 8 : marker + 12], "big")
            height = int.from_bytes(data[marker + 12 : marker + 16], "big")
            if width and height:
                return width, height
        marker += 4


_CSS_REFERENCE = re.compile(
    r"(?is)(?:url\(\s*|@import\s+(?:url\(\s*)?)['\"]?\s*"
    r"(?P<target>[^\s'\"\)]+)"
)
_ACTIVE_SVG_TAGS = {"script", "foreignobject", "iframe", "object", "embed"}


def _is_external_or_active_reference(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized.startswith("//"):
        return True
    scheme = re.match(r"^[a-z][a-z0-9+.-]*:", normalized)
    return bool(scheme and not normalized.startswith("data:"))


def _has_external_css_reference(value: str) -> bool:
    return any(
        _is_external_or_active_reference(match.group("target"))
        for match in _CSS_REFERENCE.finditer(value)
    )


def _svg_dimensions(data: bytes) -> tuple[int, int] | None:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"invalid SVG XML: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise ValueError("SVG content must have an svg root element")

    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag in _ACTIVE_SVG_TAGS:
            raise ValueError(f"active SVG element is forbidden: {tag}")
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].casefold()
            value = str(raw_value).strip()
            if name.startswith("on"):
                raise ValueError(f"active SVG event attribute is forbidden: {name}")
            if name in {"href", "src"} and _is_external_or_active_reference(value):
                raise ValueError("external or active SVG reference is forbidden")
            if name == "style" and _has_external_css_reference(value):
                raise ValueError("external SVG style reference is forbidden")
        if tag == "style" and _has_external_css_reference(element.text or ""):
            raise ValueError("external SVG stylesheet reference is forbidden")

    def number(value: str | None) -> int | None:
        if not value:
            return None
        cleaned = value.strip().casefold().removesuffix("px")
        try:
            parsed = float(cleaned)
        except ValueError:
            return None
        return int(parsed) if parsed > 0 and parsed.is_integer() else None

    width, height = number(root.get("width")), number(root.get("height"))
    if width and height:
        return width, height
    view_box = root.get("viewBox") or root.get("viewbox")
    if view_box:
        try:
            _, _, raw_width, raw_height = (
                float(part) for part in view_box.replace(",", " ").split()
            )
        except (TypeError, ValueError):
            return None
        if raw_width > 0 and raw_height > 0:
            return round(raw_width), round(raw_height)
    return None


def inspect_asset(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    mime: str | None = None
    dimensions: tuple[int, int] | None = None
    suffix = path.suffix.casefold()
    stripped = data.lstrip().lower()
    if suffix not in {".html", ".htm", ".xhtml"} and stripped.startswith(
        (b"<!doctype html", b"<html")
    ):
        raise ValueError("asset contains an HTML response shell")
    if data.startswith(b"wOF2"):
        mime = "font/woff2"
    elif data.startswith(b"wOFF"):
        mime = "font/woff"
    elif suffix in {".woff", ".woff2"}:
        raise ValueError(f"invalid {suffix.removeprefix('.').upper()} font magic")
    elif suffix == ".svg" or data.lstrip().lower().startswith(b"<svg"):
        dimensions = _svg_dimensions(data)
        mime = "image/svg+xml"
    else:
        avif_dimensions = _avif_dimensions(data)
        if avif_dimensions:
            mime = "image/avif"
            dimensions = avif_dimensions
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif"}:
            try:
                from PIL import Image

                with Image.open(path) as image:
                    dimensions = image.size
                    mime = Image.MIME.get(image.format or "")
                    image.verify()
            except Exception as exc:
                raise ValueError(f"invalid image content: {exc}") from exc
    guessed, _ = mimetypes.guess_type(path.name)
    mime = mime or guessed or "application/octet-stream"
    prefix = stripped
    if _normalized_mime(mime) not in {"text/html", "application/xhtml+xml"} and prefix.startswith(
        (b"<!doctype html", b"<html")
    ):
        raise ValueError("asset contains an HTML response shell")
    if suffix == ".css":
        try:
            css = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"CSS asset is not UTF-8: {exc}") from exc
        if _has_external_css_reference(css):
            raise ValueError("CSS asset contains an external runtime reference")
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mime_type": _normalized_mime(mime),
        "dimensions": (
            {"width": dimensions[0], "height": dimensions[1]} if dimensions else None
        ),
    }


def _is_link_or_reparse(path: Path) -> bool:
    """Inspect a lexical path entry without following it."""

    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if file_attributes & reparse_flag:
        return True
    return bool(hasattr(path, "is_junction") and path.is_junction())


def _asset_path(root: Path, relative: str) -> Path:
    resolved = resolve_inside(root, relative)
    # Walk the unresolved spelling as well: the resolved result alone hides an
    # intermediate symlink, Windows junction, or other reparse-point redirect.
    lexical = root
    raw = Path(relative)
    for component in raw.parts:
        lexical = lexical / component
        if _is_link_or_reparse(lexical):
            raise ValueError(
                f"asset path crosses a symbolic link, junction, or reparse point: {relative}"
            )
    return resolved


def _normalized_identity(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve(strict=False))))


def _same_physical_identity(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return _normalized_identity(left) == _normalized_identity(right)


def verify_asset_closure(manifest: LoadedManifest) -> AssetClosureReport:
    value = load_asset_manifest(manifest.asset_manifest_path)
    assets = value["assets"]
    closure_status = value["closure_status"]
    issues: list[AssetIssue] = []
    required = [asset for asset in assets if asset["required"]]
    p0_required = [asset for asset in required if asset["priority"] == "p0"]
    verified = 0
    required_verified = 0
    p0_verified = 0
    referenced_assets = 0
    reference_edges = 0
    unreferenced_required_assets = 0
    missing_asset_ids: set[str] = set()
    missing_copies = 0
    mismatched_asset_ids: set[str] = set()

    if closure_status == "pending":
        issues.append(
            AssetIssue(None, "ASSET_SCOPE_PENDING", "asset scope has not been frozen", True)
        )
    elif closure_status == "declared" and not required:
        issues.append(
            AssetIssue(
                None,
                "NO_REQUIRED_ASSETS",
                "declared asset scope must contain at least one required asset",
                True,
            )
        )

    resolved_paths: dict[str, dict[str, Path]] = {}
    path_records: list[tuple[str, str, Path, bool]] = []
    invalid_identity_assets: set[str] = set()

    for asset in assets:
        asset_id = asset["id"]
        blocking = bool(
            asset["required"] or asset["referenced_by"] or asset["priority"] == "p0"
        )
        if asset["referenced_by"]:
            referenced_assets += 1
            reference_edges += len(asset["referenced_by"])
        elif blocking:
            unreferenced_required_assets += 1
            issues.append(
                AssetIssue(
                    asset_id,
                    "UNREFERENCED_REQUIRED_ASSET",
                    "required asset has no component or checkpoint reference",
                    True,
                )
            )
        resolved_paths[asset_id] = {}
        for side in ("source", "runtime"):
            try:
                path = _asset_path(manifest.root, asset[f"{side}_path"])
            except ValueError as exc:
                issues.append(
                    AssetIssue(asset_id, "ASSET_PATH_INVALID", f"{side}: {exc}", blocking)
                )
                mismatched_asset_ids.add(asset_id)
                invalid_identity_assets.add(asset_id)
                continue
            resolved_paths[asset_id][side] = path
            path_records.append((asset_id, side, path, blocking))

    for asset in assets:
        asset_id = asset["id"]
        copies = resolved_paths[asset_id]
        if set(copies) != {"source", "runtime"}:
            continue
        if _same_physical_identity(copies["source"], copies["runtime"]):
            blocking = bool(
                asset["required"]
                or asset["referenced_by"]
                or asset["priority"] == "p0"
            )
            issues.append(
                AssetIssue(
                    asset_id,
                    "SOURCE_RUNTIME_IDENTITY_ALIAS",
                    "source and runtime copies resolve to the same physical identity",
                    blocking,
                )
            )
            invalid_identity_assets.add(asset_id)
            mismatched_asset_ids.add(asset_id)

    # One physical file must not satisfy two declared copies. Normalized paths
    # catch spelling/case aliases; samefile-compatible device/inode keys catch
    # hard links. Link and reparse redirections were rejected above.
    identity_owners: dict[tuple[Any, ...], tuple[str, str, Path, bool]] = {}
    for asset_id, side, path, blocking in path_records:
        if asset_id in invalid_identity_assets:
            continue
        keys: list[tuple[Any, ...]] = [("path", _normalized_identity(path))]
        try:
            metadata = path.stat(follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError, OSError):
            metadata = None
        if metadata is not None and getattr(metadata, "st_nlink", 1) != 1:
            issues.append(
                AssetIssue(
                    asset_id,
                    "ASSET_MULTIPLE_HARD_LINKS",
                    f"{side} copy must have exactly one hard link",
                    blocking,
                )
            )
            invalid_identity_assets.add(asset_id)
            mismatched_asset_ids.add(asset_id)
            continue
        if metadata is not None and metadata.st_ino:
            keys.append(("physical", metadata.st_dev, metadata.st_ino))
        owner = next((identity_owners[key] for key in keys if key in identity_owners), None)
        if owner is None:
            for key in keys:
                identity_owners[key] = (asset_id, side, path, blocking)
            continue
        owner_asset, owner_side, owner_path, owner_blocking = owner
        if not _same_physical_identity(path, owner_path):
            for key in keys:
                identity_owners.setdefault(key, (asset_id, side, path, blocking))
            continue
        if owner_asset == asset_id and owner_side != side:
            code = "SOURCE_RUNTIME_IDENTITY_ALIAS"
            message = "source and runtime copies resolve to the same physical identity"
        else:
            code = "DUPLICATE_ASSET_PATH_IDENTITY"
            message = (
                f"{side} copy reuses {owner_asset}.{owner_side} physical identity"
            )
        issues.append(
            AssetIssue(
                asset_id,
                code,
                message,
                blocking or owner_blocking,
            )
        )
        invalid_identity_assets.update({asset_id, owner_asset})
        mismatched_asset_ids.update({asset_id, owner_asset})

    for asset in assets:
        asset_id = asset["id"]
        blocking = bool(
            asset["required"] or asset["referenced_by"] or asset["priority"] == "p0"
        )
        is_required_p0 = bool(asset["required"] and asset["priority"] == "p0")
        observations: dict[str, dict[str, Any]] = {}
        failed = False
        for side in ("source", "runtime"):
            if side not in resolved_paths[asset_id]:
                failed = True
                continue
            try:
                path = resolved_paths[asset_id][side]
                if not path.is_file():
                    raise FileNotFoundError(str(path))
                observations[side] = inspect_asset(path)
            except (OSError, ValueError) as exc:
                code = "ASSET_MISSING" if isinstance(exc, FileNotFoundError) else "ASSET_INVALID"
                issues.append(AssetIssue(asset_id, code, f"{side}: {exc}", blocking))
                if isinstance(exc, FileNotFoundError):
                    missing_asset_ids.add(asset_id)
                    missing_copies += 1
                else:
                    mismatched_asset_ids.add(asset_id)
                failed = True
        if failed or asset_id in invalid_identity_assets:
            continue
        expected = {
            "bytes": asset["bytes"],
            "sha256": asset["sha256"],
            "mime_type": _normalized_mime(asset["mime_type"]),
            "dimensions": asset["dimensions"],
        }
        if expected["mime_type"].startswith("image/") and expected["dimensions"] is None:
            issues.append(
                AssetIssue(
                    asset_id,
                    "IMAGE_DIMENSIONS_UNDECLARED",
                    "image assets must declare intrinsic dimensions",
                    blocking,
                )
            )
            mismatched_asset_ids.add(asset_id)
            continue
        differences = []
        for side, observed in observations.items():
            for field, expected_value in expected.items():
                if observed[field] != expected_value:
                    differences.append(
                        f"{side}.{field}={observed[field]!r}, expected {expected_value!r}"
                    )
        if observations["source"]["sha256"] != observations["runtime"]["sha256"]:
            differences.append("source and runtime bytes differ")
        if differences:
            issues.append(
                AssetIssue(asset_id, "ASSET_MISMATCH", "; ".join(differences), blocking)
            )
            mismatched_asset_ids.add(asset_id)
            continue
        verified += 1
        if asset["required"]:
            required_verified += 1
        if is_required_p0:
            p0_verified += 1

    blocking_issues = [issue for issue in issues if issue.blocking]
    no_assets_pass = closure_status == "no-assets" and not assets and bool(
        value.get("no_assets_reason")
    )
    passed = (
        no_assets_pass
        or (bool(required) and required_verified == len(required))
    ) and not blocking_issues
    required_denominator = len(required)
    p0_denominator = len(p0_required)
    required_closure = (
        1.0
        if no_assets_pass
        else required_verified / required_denominator
        if required_denominator
        else 0.0
    )
    p0_closure = (
        1.0
        if no_assets_pass
        else p0_verified / p0_denominator
        if p0_denominator
        else None
    )
    return AssetClosureReport(
        status="passed" if passed else "failed",
        closure_status=closure_status,
        declared=len(assets),
        required=len(required),
        required_verified=required_verified,
        p0_required=p0_denominator,
        p0_verified=p0_verified,
        verified=verified,
        referenced_assets=referenced_assets,
        reference_edges=reference_edges,
        unreferenced_required_assets=unreferenced_required_assets,
        missing_assets=len(missing_asset_ids),
        missing_copies=missing_copies,
        mismatched_assets=len(mismatched_asset_ids),
        required_closure=round(required_closure, 6),
        p0_closure=round(p0_closure, 6) if p0_closure is not None else None,
        issues=tuple(issues),
    )
