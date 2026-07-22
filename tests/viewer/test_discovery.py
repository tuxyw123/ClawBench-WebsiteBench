from __future__ import annotations

from pathlib import Path

import pytest

from clawbench.viewer.discovery import _safe_resolve, discover_corpus, fingerprint


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_discovers_three_explicit_legacy_adapters() -> None:
    index = discover_corpus(REPO_ROOT)
    assert [item["key"] for item in index.items] == [
        "legacy--dev-115-freshdesk-invoice-dispute-ticket",
        "legacy--dev-117-greenhouse-codepath-application",
        "legacy--dev-118-idealist-dc-program-manager-apply",
    ]


def test_legacy_adapter_does_not_infer_missing_taxonomy_or_official_score() -> None:
    legacy = discover_corpus(REPO_ROOT).by_key(
        "legacy--dev-117-greenhouse-codepath-application"
    )
    assert legacy is not None
    assert legacy["product_type"] is None
    assert legacy["difficulty"] is None
    assert legacy["roles"] == []
    assert legacy["counts"]["routes"] is None
    assert legacy["latest_official_result"] is None
    assert legacy["legacy_verification"] == {"passed": 91, "failed": 0, "total": 91}


def test_public_index_is_empty_when_allowlist_is_empty() -> None:
    value = discover_corpus(REPO_ROOT, profile="public").as_dict()
    assert value["items"] == []
    assert value["summary"]["item_count"] == 0


def test_path_resolver_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        _safe_resolve(tmp_path, tmp_path / ".." / "outside")


def test_artifact_fingerprint_changes_with_file_content(tmp_path: Path) -> None:
    first = tmp_path / "a.txt"
    first.write_text("one")
    before = fingerprint([first], tmp_path)
    first.write_text("two")
    after = fingerprint([first], tmp_path)
    assert before != after
