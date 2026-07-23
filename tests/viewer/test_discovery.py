from __future__ import annotations

from pathlib import Path

import pytest

from clawbench.viewer import discovery as discovery_module
from clawbench.viewer.discovery import _safe_resolve, discover_corpus, fingerprint


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_discovers_amazon_and_three_explicit_legacy_adapters() -> None:
    index = discover_corpus(REPO_ROOT)
    assert [item["key"] for item in index.items] == [
        "offlineclone--amazon-shopping-mainline",
        "legacy--dev-115-freshdesk-invoice-dispute-ticket",
        "legacy--dev-117-greenhouse-codepath-application",
        "legacy--dev-118-idealist-dc-program-manager-apply",
    ]


def test_amazon_adapter_keeps_dataset_calibration_separate_from_agent_results() -> None:
    amazon = discover_corpus(REPO_ROOT).by_key(
        "offlineclone--amazon-shopping-mainline"
    )
    assert amazon is not None
    assert amazon["source_type"] == "offline_clone"
    assert amazon["lifecycle_stage"] == "ready"
    assert amazon["construction_status"] == "accepted"
    assert amazon["experiment_status"] == "not_started"
    assert amazon["counts"] == {
        "routes": 15,
        "journeys": 3,
        "checkpoints": 16,
        "seeds": None,
        "public_seeds": None,
        "hidden_test_families": None,
        "states": 79,
        "assets": 454,
    }
    assert amazon["official_runs"] == []
    assert amazon["latest_official_result"] is None
    assert len(amazon["showcase"]["visual_pairs"]) == 2
    assert amazon["showcase"]["calibration"]["stage"] == "ACCEPTED"


def test_amazon_adapter_uses_sanitized_summary_without_ignored_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = (
        REPO_ROOT / "materials" / "amazon" / "artifacts" / "offline-clone"
    ).resolve()
    read_json = discovery_module._read_json

    def without_generated_artifacts(path: Path) -> tuple[object | None, str | None]:
        resolved = path.resolve()
        if resolved == artifact_root or artifact_root in resolved.parents:
            return None, "simulated clean checkout without ignored artifacts"
        return read_json(path)

    monkeypatch.setattr(discovery_module, "_read_json", without_generated_artifacts)
    amazon = discover_corpus(REPO_ROOT).by_key(
        "offlineclone--amazon-shopping-mainline"
    )
    assert amazon is not None
    assert amazon["lifecycle_stage"] == "ready"
    assert amazon["visual_evidence"]["comparison_kind"] == (
        "source-to-offline-reference"
    )
    assert amazon["showcase"]["calibration"]["metrics"]["visual"][
        "checks_passed"
    ] == 26
    assert amazon["internal"]["acceptance_source"] == "viewer-public-summary"
    assert amazon["internal"]["viewer_public_summary_error"] is None


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


def test_public_index_contains_only_sanitized_amazon_showcase() -> None:
    value = discover_corpus(REPO_ROOT, profile="public").as_dict()
    assert [item["key"] for item in value["items"]] == [
        "offlineclone--amazon-shopping-mainline"
    ]
    assert value["summary"]["benchmark_site_count"] == 1
    assert value["summary"]["official_run_count"] == 0
    amazon = value["items"][0]
    assert amazon["visual_evidence"]["comparison_kind"] == (
        "source-to-offline-reference"
    )
    assert amazon["showcase"]["experiment_status"] == "not_started"
    assert "internal" not in amazon


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
