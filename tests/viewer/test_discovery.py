from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from clawbench.viewer.discovery import (
    _safe_resolve,
    discover_corpus,
    fingerprint,
    public_leak_findings,
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "websitebench").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    shutil.copytree(REPO_ROOT / "websitebench" / "schemas", root / "websitebench" / "schemas")
    shutil.copytree(
        REPO_ROOT / "websitebench" / "northstar-market",
        root / "websitebench" / "northstar-market",
    )
    return root


def _valid_result() -> dict:
    dimensions = {
        name: {"score": score, "max_score": maximum, "passed": 1, "total": 1}
        for name, score, maximum in (
            ("visual", 17, 20),
            ("interactions", 18, 20),
            ("journeys", 34, 40),
            ("robustness", 13, 15),
            ("efficiency", 5, 5),
        )
    }
    return {
        "schema_version": "websitebench.result.v1",
        "run_id": "candidate-001",
        "site_id": "northstar-market",
        "site_version": "1.0.0",
        "track": "core",
        "status": "passed",
        "score": 87,
        "dimensions": dimensions,
        "hard_failures": [],
        "journeys": [],
        "seeds": [],
        "resources": {
            "build_seconds": 1,
            "startup_seconds": 1,
            "image_bytes": 1,
            "source_bytes": 1,
            "peak_memory_bytes": 1,
            "p95_latency_ms": 1,
        },
        "network": {
            "runtime_requests": 1,
            "blocked_requests": 0,
            "reference_requests": 0,
            "internet_requests": 0,
        },
        "failures": [],
        "evidence": [],
        "versions": {"judge": "1"},
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "browser_actions": 1,
            "candidate_builds": 1,
            "human_messages": 0,
            "human_minutes": 0,
        },
        "started_at": "2026-07-18T00:00:00+00:00",
        "finished_at": "2026-07-18T00:01:00+00:00",
    }


def test_discovers_northstar_and_three_explicit_legacy_adapters() -> None:
    index = discover_corpus(REPO_ROOT)
    assert [item["key"] for item in index.items] == [
        "websitebench--northstar-market",
        "legacy--dev-115-freshdesk-invoice-dispute-ticket",
        "legacy--dev-117-greenhouse-codepath-application",
        "legacy--dev-118-idealist-dc-program-manager-apply",
    ]
    northstar = index.items[0]
    assert northstar["product_type"] == "ecommerce-marketplace"
    assert "persistent-cart" in northstar["capability_tags"]
    assert northstar["counts"] == {
        "routes": 13,
        "journeys": 5,
        "checkpoints": 12,
        "seeds": 8,
        "public_seeds": 2,
        "hidden_test_families": 2,
    }
    assert northstar["readiness_counts"]["invalid"] == 0


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


def test_public_index_is_allowlisted_and_recursively_clean() -> None:
    value = discover_corpus(REPO_ROOT, profile="public").as_dict()
    assert [item["key"] for item in value["items"]] == [
        "websitebench--northstar-market"
    ]
    assert public_leak_findings(value) == []
    serialized = json.dumps(value).lower().replace("\\", "/")
    assert "judge/" not in serialized
    assert "server_command" not in serialized
    assert "/mnt/" not in serialized


def test_corrupt_candidate_report_is_not_presented_as_official(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    report = root / "artifacts" / "websitebench" / "runs" / "broken" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text("{not json", encoding="utf-8")
    index = discover_corpus(root)
    assert index.runs == []
    assert len(index.invalid_runs) == 1
    assert index.items[0]["latest_official_result"] is None


def test_schema_valid_candidate_result_is_the_only_official_score(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    report = root / "artifacts" / "websitebench" / "runs" / "candidate-001" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(_valid_result()), encoding="utf-8")
    item = discover_corpus(root).items[0]
    assert item["latest_official_result"]["score"] == 87
    assert item["latest_official_result"]["dimensions"]["visual"]["score"] == 17
    assert item["legacy_verification"] is None
    assert item["readiness_counts"]["missing"] == 1  # companion evidence still absent


def test_public_result_excludes_hidden_fixtures_and_evidence_paths(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    (root / "websitebench" / "viewer-public-allowlist.json").write_text(
        json.dumps({"items": ["websitebench--northstar-market"]}), encoding="utf-8"
    )
    value = _valid_result()
    value["journeys"] = [
        {
            "id": "hidden-checkout",
            "seed": 9001,
            "score": 5,
            "max_score": 5,
            "terminal_passed": True,
            "checkpoints": [
                {
                    "id": "private-state",
                    "passed": True,
                    "expected": {"secret": "fixture-canary-do-not-publish"},
                    "actual": {"secret": "fixture-canary-do-not-publish"},
                    "evidence_ids": ["judge-state"],
                }
            ],
        }
    ]
    value["seeds"] = [
        {
            "seed": 9001,
            "purpose": "fixture-canary-do-not-publish",
            "reset_passed": True,
            "tests_passed": 1,
            "tests_total": 1,
        }
    ]
    value["evidence"] = [
        {
            "id": "judge-state",
            "kind": "state",
            "path": "judge/fixtures/private.json",
            "sha256": "a" * 64,
        }
    ]
    report = root / "artifacts" / "websitebench" / "runs" / "candidate-001" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(value), encoding="utf-8")

    public = discover_corpus(root, profile="public").as_dict()
    serialized = json.dumps(public)
    assert public["items"][0]["latest_official_result"]["score"] == 87
    assert public["items"][0]["latest_official_result"]["journeys"] == []
    assert "fixture-canary-do-not-publish" not in serialized
    assert "judge/fixtures" not in serialized


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
