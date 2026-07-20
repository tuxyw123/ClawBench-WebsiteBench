from __future__ import annotations

from pathlib import Path

import pytest

from clawbench.viewer.discovery import AMAZON_ITEM_KEY
from clawbench.viewer.reviews import DIMENSIONS, ReviewConflict, ReviewError, ReviewStore


REPO_ROOT = Path(__file__).resolve().parents[2]


def payload(*, gate: str = "approve", visibility: str = "internal") -> dict:
    return {
        "reviewer": "reviewer",
        "gate": gate,
        "visibility": visibility,
        "dimensions": {
            name: {"rating": "pass", "notes": "checked", "evidence_refs": []}
            for name in DIMENSIONS
        },
        "notes": "reviewed",
        "evidence_refs": [],
    }


def test_atomic_save_revision_conflict_and_restart_persistence(tmp_path: Path) -> None:
    root = tmp_path / "reviews"
    key = AMAZON_ITEM_KEY
    store = ReviewStore(root, REPO_ROOT)
    saved = store.save(
        key,
        payload(),
        expected_revision=0,
        artifact_fingerprint="a" * 64,
    )
    assert saved["revision"] == 1
    with pytest.raises(ReviewConflict):
        store.save(
            key,
            payload(),
            expected_revision=0,
            artifact_fingerprint="a" * 64,
        )
    restarted = ReviewStore(root, REPO_ROOT)
    assert restarted.load(key) == saved


def test_import_rejects_disabled_key_and_preserves_existing_review(tmp_path: Path) -> None:
    key = AMAZON_ITEM_KEY
    existing = ReviewStore(tmp_path / "reviews", REPO_ROOT)
    saved = existing.save(
        key, payload(), expected_revision=0, artifact_fingerprint="a" * 64
    )
    second_store = ReviewStore(tmp_path / "source", REPO_ROOT, allowed_keys=None)
    second = second_store.save(
        "legacy--dev-115-freshdesk-invoice-dispute-ticket",
        payload(),
        expected_revision=0,
        artifact_fingerprint="b" * 64,
    )
    bundle = {
        "schema_version": "websitebench.viewer-review-export.v1",
        "exported_at": saved["updated_at"],
        "reviews": [saved, second],
    }
    with pytest.raises(ReviewError, match="disabled item keys"):
        existing.import_batch(bundle)
    assert existing.load(key) == saved


def test_public_review_rejects_private_evidence_reference(tmp_path: Path) -> None:
    value = payload(visibility="public")
    value["evidence_refs"] = ["judge/fixtures/9101.json"]
    with pytest.raises(ValueError, match="private fixture"):
        ReviewStore(tmp_path, REPO_ROOT).save(
            AMAZON_ITEM_KEY,
            value,
            expected_revision=0,
            artifact_fingerprint="a" * 64,
        )
