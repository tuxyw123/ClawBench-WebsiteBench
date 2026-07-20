"""Visual evidence registries and safe artifact resolution.

The public Viewer never accepts a filesystem path from a request.  Amazon's
retained gate images are indexed once, assigned stable identifiers, and served
only through :class:`AmazonEvidenceRegistry`.  The older ``EvidenceStore`` is
kept for maintainers who create private, diagnostic capture companions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .metrics import compare_images
from .schema import validation_errors


SAFE_COMPONENT = re.compile(r"[^a-zA-Z0-9._-]+")
IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}
AMAZON_EVIDENCE_ROOT = Path("materials/amazon/verification")
VIEWPORT_ZH = {
    "desktop": "桌面",
    "desktop-compact": "紧凑桌面",
    "tablet": "平板",
    "mobile": "移动",
    "mobile-small": "小屏移动",
    "unspecified": "未指定",
}
SCENE_ZH = {
    "Account Entry Live": "账户入口（实时）",
    "All Departments Best Sellers Live": "全部部门畅销榜（实时）",
    "Best Sellers External Ssd Live": "外置固态硬盘畅销榜（实时）",
    "Books Category Live": "图书分类（实时）",
    "Catalog No Results Live": "目录无结果（实时）",
    "Computers Category Live": "电脑分类（实时）",
    "Desktop B01 Leaf": "桌面端 B01 叶级页面",
    "Desktop B01 Product": "桌面端 B01 商品页",
    "Desktop B01 Root": "桌面端 B01 根页面",
    "Desktop B02 Search": "桌面端 B02 搜索",
    "Desktop B03 Account": "桌面端 B03 账户",
    "Desktop B04 Cart": "桌面端 B04 购物车",
    "Desktop Home": "桌面端首页",
    "Desktop Search": "桌面端搜索",
    "Desktop Secondary B02 Refined Search": "桌面端补充 B02 精细搜索",
    "Desktop Secondary B03 Orders Boundary": "桌面端补充 B03 订单边界",
    "Desktop Task Cart": "桌面端任务购物车",
    "Electronics Category Live": "电子产品分类（实时）",
    "Empty Cart B04 Empty Cart": "空购物车 B04",
    "Empty Cart Live": "空购物车（实时）",
    "Home Kitchen Category Live": "家居与厨房分类（实时）",
    "Lists Entry Live": "清单入口（实时）",
    "Mobile B05 Product": "移动端 B05 商品页",
    "Mobile B05 Ranking": "移动端 B05 排名页",
    "Mobile Category": "移动端分类页",
    "Mobile Home": "移动端首页",
    "Mobile Task Product": "移动端任务商品页",
    "Not Found Live": "未找到页面（实时）",
    "Orders Entry Live": "订单入口（实时）",
    "Portable Ssd Filtered Search Live": "便携式固态硬盘筛选搜索（实时）",
    "Portable Ssd Search Live": "便携式固态硬盘搜索（实时）",
    "Samsung T7 Product Live Boundary": "Samsung T7 商品边界状态",
    "Samsung T7 Product Response Render": "Samsung T7 商品响应渲染",
    "Storefront Department Drawer Live": "店面部门抽屉（实时）",
    "Storefront Home Live": "店面首页（实时）",
    "Storefront Search Autocomplete Live": "店面搜索自动补全（实时）",
    "Task B01 Cart": "任务 B01 购物车",
    "Task B01 Leaf": "任务 B01 叶级页面",
    "Task B01 Ready To Add": "任务 B01 待加入购物车",
    "Todays Deals Live": "今日优惠（实时）",
}


def _is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _has_symlink_component(path: Path, root: Path) -> bool:
    """Return whether ``path`` traverses a symlink below ``root``."""

    current = path
    while current != root:
        if current.is_symlink():
            return True
        if root not in current.parents:
            return True
        current = current.parent
    return root.is_symlink()


def _evidence_slug(relative: Path) -> str:
    value = relative.with_suffix("").as_posix().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _viewport_from_name(name: str) -> tuple[str, str]:
    lowered = name.lower()
    for marker, group in (
        ("mobile-small", "mobile"),
        ("desktop-compact", "desktop"),
        ("mobile", "mobile"),
        ("tablet", "tablet"),
        ("desktop", "desktop"),
    ):
        if marker in lowered:
            return group, marker
    return "unspecified", "unspecified"


def _evidence_kind(relative: Path) -> str:
    parts = relative.parts
    name = relative.name.lower()
    if "full-page" in parts:
        return "full-page"
    if "heatmaps" in parts:
        return "heatmap"
    if "review-pairs" in parts:
        return "pair"
    if name.startswith("source-"):
        return "source"
    return "clone"


def _scene_from_name(relative: Path) -> str:
    value = relative.stem.lower()
    for prefix in ("source-clone-", "difference-", "source-", "clone-"):
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    value = re.sub(
        r"-(desktop-compact|mobile-small|desktop|mobile|tablet)(-full)?$", "", value
    )
    return value.replace("-", " ").strip().title()


class AmazonEvidenceRegistry:
    """Read-only registry for the retained Amazon Gate 2/3/4 images."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.rejected: list[str] = []
        self._records: dict[str, dict[str, Any]] = {}
        configured_root = self.repo_root / AMAZON_EVIDENCE_ROOT
        self._root_safe = not _has_symlink_component(configured_root, self.repo_root)
        self.root = configured_root.resolve()
        if not self._root_safe:
            self.rejected.append(f"{AMAZON_EVIDENCE_ROOT.as_posix()}: symlinked root")
        self._metadata = self._load_report_metadata()
        self._build()

    def _load_report_metadata(self) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}

        def visit(value: Any, gate_root: Path) -> None:
            if isinstance(value, dict):
                candidates = [value.get(key) for key in ("file", "path")]
                for candidate in candidates:
                    if not isinstance(candidate, str):
                        continue
                    relative = Path(candidate)
                    if relative.suffix.lower() not in IMAGE_SUFFIXES:
                        continue
                    path = gate_root / relative
                    try:
                        key = path.relative_to(self.root).as_posix()
                    except ValueError:
                        continue
                    metadata[key] = {
                        field: value[field]
                        for field in ("bytes", "width", "height", "sha256")
                        if field in value
                    }
                for child in value.values():
                    visit(child, gate_root)
            elif isinstance(value, list):
                for child in value:
                    visit(child, gate_root)

        if not self._root_safe or not self.root.is_dir():
            return metadata
        for gate in (2, 3, 4):
            report_path = self.root / f"gate{gate}" / "report.json"
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            visit(report, report_path.parent)
        return metadata

    def _build(self) -> None:
        if not self._root_safe or not self.root.is_dir():
            return
        paths = sorted(
            path
            for path in self.root.rglob("*")
            if path.suffix.lower() in IMAGE_SUFFIXES
        )
        for path in paths:
            try:
                relative = path.relative_to(self.root)
            except ValueError:
                self.rejected.append(str(path))
                continue
            resolved = path.resolve()
            if (
                not path.is_file()
                or not _is_relative_to(resolved, self.root)
                or _has_symlink_component(path, self.root)
            ):
                self.rejected.append(relative.as_posix())
                continue
            match = re.match(r"gate([234])$", relative.parts[0])
            if match is None:
                self.rejected.append(relative.as_posix())
                continue
            evidence_id = _evidence_slug(relative)
            if evidence_id in self._records:
                evidence_id = f"{evidence_id}-{hashlib.sha256(relative.as_posix().encode()).hexdigest()[:8]}"
            viewport, viewport_variant = _viewport_from_name(relative.stem)
            if viewport == "unspecified" and relative.parts[0] == "gate4":
                viewport, viewport_variant = "desktop", "desktop"
            kind = _evidence_kind(relative)
            scene = _scene_from_name(relative)
            scene_zh = SCENE_ZH.get(scene, scene)
            report_metadata = self._metadata.get(relative.as_posix(), {})
            stat = path.stat()
            width = report_metadata.get("width")
            height = report_metadata.get("height")
            if width is None or height is None:
                try:
                    with Image.open(path) as image:
                        width, height = image.size
                except OSError:
                    width, height = None, None
            self._records[evidence_id] = {
                "id": evidence_id,
                "gate": int(match.group(1)),
                "type": kind,
                "type_zh": self._kind_zh(kind),
                "viewport": viewport,
                "viewport_variant": viewport_variant,
                "viewport_zh": VIEWPORT_ZH.get(viewport, viewport),
                "viewport_variant_zh": VIEWPORT_ZH.get(
                    viewport_variant, viewport_variant
                ),
                "scene": scene,
                "scene_zh": scene_zh,
                "caption": f"Gate {match.group(1)} · {kind.replace('-', ' ').title()} · {scene}",
                "caption_zh": f"Gate {match.group(1)} · {self._kind_zh(kind)} · {scene_zh}",
                "bytes": int(report_metadata.get("bytes", stat.st_size)),
                "width": width,
                "height": height,
                "sha256": report_metadata.get("sha256") or file_sha256(path),
                "contains_third_party_evidence": kind in {"source", "pair", "heatmap"},
                "url": f"/evidence/{evidence_id}",
                "_path": path,
            }

    @staticmethod
    def _kind_zh(kind: str) -> str:
        return {
            "source": "源站",
            "clone": "克隆站",
            "pair": "配对图",
            "heatmap": "热图",
            "full-page": "全页截图",
        }.get(kind, kind)

    @property
    def records(self) -> list[dict[str, Any]]:
        return [self.public_record(value) for value in self._records.values()]

    @property
    def paths(self) -> list[Path]:
        return [value["_path"] for value in self._records.values()]

    @property
    def count(self) -> int:
        return len(self._records)

    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if not key.startswith("_")}

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        record = self._records.get(evidence_id)
        return self.public_record(record) if record else None

    def resolve(self, evidence_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", evidence_id):
            raise FileNotFoundError(evidence_id)
        record = self._records.get(evidence_id)
        if record is None:
            raise FileNotFoundError(evidence_id)
        path: Path = record["_path"]
        resolved = path.resolve()
        if (
            not path.is_file()
            or not _is_relative_to(resolved, self.root)
            or _has_symlink_component(path, self.root)
        ):
            raise FileNotFoundError(evidence_id)
        return path

    def filter(
        self,
        *,
        gate: int | None = None,
        kind: str | None = None,
        viewport: str | None = None,
    ) -> list[dict[str, Any]]:
        output = self.records
        if gate is not None:
            output = [row for row in output if row["gate"] == gate]
        if kind:
            output = [row for row in output if row["type"] == kind]
        if viewport:
            output = [row for row in output if row["viewport"] == viewport]
        return output

    def counts(self) -> dict[str, dict[str, int]]:
        return {
            "gates": dict(sorted(Counter(str(row["gate"]) for row in self.records).items())),
            "types": dict(sorted(Counter(row["type"] for row in self.records).items())),
            "viewports": dict(
                sorted(Counter(row["viewport"] for row in self.records).items())
            ),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decide_capture_status(
    *,
    source_available: bool,
    candidate_available: bool,
    blocked: bool = False,
    failed: bool = False,
    comparable: bool = True,
) -> tuple[str, str]:
    if not comparable:
        return "not_comparable", "unavailable"
    if failed:
        return "failed", "unavailable"
    if blocked:
        return "blocked", "caution" if source_available or candidate_available else "unavailable"
    if source_available and candidate_available:
        return "captured", "reliable"
    if source_available or candidate_available:
        return "partial", "caution"
    return "pending", "unavailable"


class EvidenceStore:
    def __init__(self, root: Path, repo_root: Path) -> None:
        self.root = root.resolve()
        self.repo_root = repo_root.resolve()

    def item_root(self, item_key: str) -> Path:
        if not re.fullmatch(r"[a-z0-9]+(?:--[a-z0-9-]+)+", item_key):
            raise ValueError(f"invalid item key: {item_key}")
        return self.root / item_key

    def manifest_path(self, item_key: str) -> Path:
        return self.item_root(item_key) / "manifest.json"

    def load(self, item_key: str) -> dict[str, Any] | None:
        path = self.manifest_path(item_key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid visual evidence manifest: {exc}") from exc
        errors = validation_errors(value, "visual_evidence", self.repo_root)
        if errors:
            raise ValueError("invalid visual evidence manifest: " + "; ".join(errors))
        return value

    def _atomic_write(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _component(value: str) -> str:
        safe = SAFE_COMPONENT.sub("-", value).strip("-.")
        if not safe:
            raise ValueError("empty artifact path component")
        return safe

    def _copy_image(
        self,
        item_key: str,
        checkpoint: str,
        viewport: str,
        side: str,
        source: Path | None,
    ) -> tuple[str | None, str | None]:
        if source is None:
            return None, None
        source = source.resolve()
        if not source.is_file() or source.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"capture image is missing or unsupported: {source}")
        relative = Path("captures") / self._component(checkpoint) / self._component(viewport) / f"{side}{source.suffix.lower()}"
        destination = self.item_root(item_key) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return relative.as_posix(), file_sha256(destination)

    def upsert(
        self,
        item_key: str,
        checkpoint: str,
        viewport: str,
        *,
        source_image: Path | None = None,
        candidate_image: Path | None = None,
        ignore_regions: list[dict[str, int]] | None = None,
        comparable: bool = True,
    ) -> dict[str, Any]:
        source_relative, source_sha = self._copy_image(
            item_key, checkpoint, viewport, "source", source_image
        )
        candidate_relative, candidate_sha = self._copy_image(
            item_key, checkpoint, viewport, "candidate", candidate_image
        )
        status, reliability = decide_capture_status(
            source_available=source_relative is not None,
            candidate_available=candidate_relative is not None,
            comparable=comparable,
        )
        metrics = None
        heatmap_relative = None
        if source_relative and candidate_relative and comparable:
            heatmap_relative = (
                Path("captures")
                / self._component(checkpoint)
                / self._component(viewport)
                / "heatmap.webp"
            ).as_posix()
            try:
                metrics = compare_images(
                    self.item_root(item_key) / source_relative,
                    self.item_root(item_key) / candidate_relative,
                    self.item_root(item_key) / heatmap_relative,
                    ignore_regions=ignore_regions or [],
                )
            except (RuntimeError, ValueError):
                heatmap_relative = None
                reliability = "caution"
        capture = {
            "checkpoint": checkpoint,
            "viewport": viewport,
            "source_image": source_relative,
            "candidate_image": candidate_relative,
            "heatmap": heatmap_relative,
            "ignore_regions": ignore_regions or [],
            "source_sha256": source_sha,
            "candidate_sha256": candidate_sha,
            "capture_status": status,
            "evidence_reliability": reliability,
            "diagnostic_metrics": metrics,
        }
        manifest = self.load(item_key) or {
            "schema_version": "websitebench.visual-evidence.v1",
            "item_key": item_key,
            "generated_at": _now(),
            "captures": [],
        }
        manifest["generated_at"] = _now()
        manifest["captures"] = [
            row
            for row in manifest["captures"]
            if (row["checkpoint"], row["viewport"]) != (checkpoint, viewport)
        ]
        manifest["captures"].append(capture)
        manifest["captures"].sort(key=lambda row: (row["checkpoint"], row["viewport"]))
        errors = validation_errors(manifest, "visual_evidence", self.repo_root)
        if errors:
            raise ValueError("; ".join(errors))
        self._atomic_write(self.manifest_path(item_key), manifest)
        return manifest

    def resolve(self, item_key: str, relative_path: str) -> Path:
        manifest = self.load(item_key)
        if manifest is None:
            raise FileNotFoundError(relative_path)
        allowed = {
            capture[field]
            for capture in manifest["captures"]
            for field in ("source_image", "candidate_image", "heatmap")
            if capture[field]
        }
        if relative_path not in allowed:
            raise FileNotFoundError(relative_path)
        root = self.item_root(item_key).resolve()
        path = (root / relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(relative_path)
        return path
