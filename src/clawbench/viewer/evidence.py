"""Visual evidence companion manifests and safe artifact resolution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import compare_images
from .schema import validation_errors


SAFE_COMPONENT = re.compile(r"[^a-zA-Z0-9._-]+")
IMAGE_SUFFIXES = {".png", ".webp", ".jpg", ".jpeg"}


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

    def artifact_root(self, item_key: str, run_id: str | None = None) -> Path:
        root = self.item_root(item_key)
        return root / "runs" / self._component(run_id) if run_id else root

    def manifest_path(self, item_key: str, run_id: str | None = None) -> Path:
        return self.artifact_root(item_key, run_id) / "manifest.json"

    def load(self, item_key: str, run_id: str | None = None) -> dict[str, Any] | None:
        path = self.manifest_path(item_key, run_id)
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
        run_id: str | None,
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
        destination = self.artifact_root(item_key, run_id) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return relative.as_posix(), file_sha256(destination)

    def upsert(
        self,
        item_key: str,
        checkpoint: str,
        viewport: str,
        *,
        run_id: str | None = None,
        source_image: Path | None = None,
        candidate_image: Path | None = None,
        ignore_regions: list[dict[str, int]] | None = None,
        comparable: bool = True,
    ) -> dict[str, Any]:
        source_relative, source_sha = self._copy_image(
            item_key, run_id, checkpoint, viewport, "source", source_image
        )
        candidate_relative, candidate_sha = self._copy_image(
            item_key, run_id, checkpoint, viewport, "candidate", candidate_image
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
                    self.artifact_root(item_key, run_id) / source_relative,
                    self.artifact_root(item_key, run_id) / candidate_relative,
                    self.artifact_root(item_key, run_id) / heatmap_relative,
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
        manifest = self.load(item_key, run_id) or {
            "schema_version": "websitebench.visual-evidence.v1",
            "item_key": item_key,
            "generated_at": _now(),
            "captures": [],
        }
        if run_id:
            manifest["run_id"] = run_id
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
        self._atomic_write(self.manifest_path(item_key, run_id), manifest)
        return manifest

    def resolve(
        self, item_key: str, relative_path: str, run_id: str | None = None
    ) -> Path:
        manifest = self.load(item_key, run_id)
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
        root = self.artifact_root(item_key, run_id).resolve()
        path = (root / relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(relative_path)
        return path
