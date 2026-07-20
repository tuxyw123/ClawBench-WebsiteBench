"""Persistent optimistic-concurrency review storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Iterator

from ..amazon_contract import AMAZON_ITEM_KEY
from .schema import validation_errors

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


DIMENSIONS = (
    "protocol_observability",
    "behavioral_depth",
    "visual_interaction_coverage",
    "reproducibility",
    "isolation_asset_compliance",
    "diversity_non_reskin",
)
ITEM_KEY_RE = re.compile(r"^[a-z0-9]+(?:--[a-z0-9-]+)+$")
DEFAULT_REVIEW_KEYS = frozenset({AMAZON_ITEM_KEY})


class ReviewError(ValueError):
    pass


class ReviewConflict(ReviewError):
    def __init__(self, item_key: str, expected: int, current: int) -> None:
        self.item_key = item_key
        self.expected = expected
        self.current = current
        super().__init__(
            f"review revision conflict for {item_key}: expected {expected}, current {current}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_review(item_key: str, artifact_fingerprint: str) -> dict[str, Any]:
    return {
        "schema_version": "websitebench.viewer-review.v1",
        "item_key": item_key,
        "artifact_fingerprint": artifact_fingerprint,
        "revision": 0,
        "reviewer": "",
        "gate": "unreviewed",
        "visibility": "internal",
        "dimensions": {
            name: {"rating": "unreviewed", "notes": "", "evidence_refs": []}
            for name in DIMENSIONS
        },
        "notes": "",
        "evidence_refs": [],
        "created_at": None,
        "updated_at": None,
    }


def _public_content_errors(review: dict[str, Any]) -> list[str]:
    errors = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
        elif isinstance(value, str):
            normalized = value.lower().replace("\\", "/")
            if "judge/" in normalized:
                errors.append(f"{path}: private fixture path")
            if normalized.startswith(("/mnt/", "/home/", "/root/", "c:/")):
                errors.append(f"{path}: absolute internal path")

    visit(review, "$")
    return errors


class ReviewStore:
    def __init__(
        self,
        root: Path,
        repo_root: Path,
        *,
        allowed_keys: Collection[str] | None = DEFAULT_REVIEW_KEYS,
    ) -> None:
        self.root = root.resolve()
        self.repo_root = repo_root.resolve()
        self.allowed_keys = frozenset(allowed_keys) if allowed_keys is not None else None
        self._thread_lock = threading.RLock()

    def _path(self, item_key: str) -> Path:
        if not ITEM_KEY_RE.fullmatch(item_key):
            raise ReviewError(f"invalid review item key: {item_key}")
        if self.allowed_keys is not None and item_key not in self.allowed_keys:
            raise ReviewError(f"review item is not enabled: {item_key}")
        return self.root / f"{item_key}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            lock_path = self.root / ".reviews.lock"
            with lock_path.open("a+b") as handle:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self, item_key: str) -> dict[str, Any] | None:
        path = self._path(item_key)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReviewError(f"cannot read review {item_key}: {exc}") from exc
        errors = validation_errors(value, "review", self.repo_root)
        if errors:
            raise ReviewError(f"invalid stored review {item_key}: {'; '.join(errors)}")
        return value

    def list(self, *, public_only: bool = False) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        reviews = []
        paths = (
            [self.root / f"{key}.json" for key in sorted(self.allowed_keys)]
            if self.allowed_keys is not None
            else sorted(self.root.glob("*.json"))
        )
        for path in paths:
            if not path.is_file():
                continue
            review = self.load(path.stem)
            if review is None:
                continue
            if public_only and not (
                review["visibility"] == "public" and review["gate"] == "approve"
            ):
                continue
            reviews.append(review)
        return reviews

    @staticmethod
    def _atomic_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def save(
        self,
        item_key: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        artifact_fingerprint: str,
        default_reviewer: str = "",
    ) -> dict[str, Any]:
        if expected_revision < 0:
            raise ReviewError("expected_revision must be non-negative")
        with self._locked():
            current = self.load(item_key)
            current_revision = current["revision"] if current else 0
            if expected_revision != current_revision:
                raise ReviewConflict(item_key, expected_revision, current_revision)
            now = _now()
            review = {
                "schema_version": "websitebench.viewer-review.v1",
                "item_key": item_key,
                "artifact_fingerprint": artifact_fingerprint,
                "revision": current_revision + 1,
                "reviewer": payload.get("reviewer") or default_reviewer,
                "gate": payload.get("gate", "unreviewed"),
                "visibility": payload.get("visibility", "internal"),
                "dimensions": payload.get("dimensions", {}),
                "notes": payload.get("notes", ""),
                "evidence_refs": payload.get("evidence_refs", []),
                "created_at": current["created_at"] if current else now,
                "updated_at": now,
            }
            errors = validation_errors(review, "review", self.repo_root)
            if review["visibility"] == "public":
                errors.extend(_public_content_errors(review))
            if errors:
                raise ReviewError("; ".join(errors))
            self._atomic_write(self._path(item_key), review)
            return review

    def export(self, *, public_only: bool = False) -> dict[str, Any]:
        return {
            "schema_version": "websitebench.viewer-review-export.v1",
            "exported_at": _now(),
            "reviews": self.list(public_only=public_only),
        }

    def import_batch(self, bundle: dict[str, Any]) -> list[dict[str, Any]]:
        errors = validation_errors(bundle, "review_export", self.repo_root)
        if errors:
            raise ReviewError("; ".join(errors))
        reviews = bundle["reviews"]
        keys = [review["item_key"] for review in reviews]
        if len(keys) != len(set(keys)):
            raise ReviewError("review import contains duplicate item keys")
        if self.allowed_keys is not None:
            unknown = sorted(set(keys) - self.allowed_keys)
            if unknown:
                raise ReviewError(
                    "review import contains disabled item keys: " + ", ".join(unknown)
                )
        public_errors = [
            error
            for review in reviews
            if review["visibility"] == "public"
            for error in _public_content_errors(review)
        ]
        if public_errors:
            raise ReviewError("; ".join(public_errors))
        with self._locked():
            for incoming in reviews:
                current = self.load(incoming["item_key"])
                current_revision = current["revision"] if current else 0
                if current and incoming["revision"] != current_revision + 1:
                    raise ReviewConflict(
                        incoming["item_key"], incoming["revision"] - 1, current_revision
                    )
            staged: list[tuple[Path, Path]] = []
            try:
                for review in reviews:
                    destination = self._path(review["item_key"])
                    payload = json.dumps(review, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
                    fd, temporary_name = tempfile.mkstemp(
                        prefix=f".{destination.name}.import.", dir=self.root
                    )
                    temporary = Path(temporary_name)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    staged.append((temporary, destination))
                for temporary, destination in staged:
                    os.replace(temporary, destination)
            finally:
                for temporary, _ in staged:
                    temporary.unlink(missing_ok=True)
        return reviews
