from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from clawbench.viewer.discovery import (
    AMAZON_ITEM_KEY,
    _safe_resolve,
    aggregate_leaderboard,
    discover_corpus,
    fingerprint,
    public_leak_findings,
)
from clawbench.amazon_contract import amazon_runtime_fingerprint


REPO_ROOT = Path(__file__).resolve().parents[2]


def _fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    shutil.copytree(REPO_ROOT / "websitebench" / "schemas", root / "websitebench" / "schemas")
    manifest_relative = "materials/amazon/runtime-manifest.json"
    manifest = json.loads((REPO_ROOT / manifest_relative).read_text(encoding="utf-8"))
    files = [
        manifest_relative,
        "tasks/clawbench/dev-136-amazon-t7-best-seller/task.json",
        "materials/amazon/clone/verification-report.json",
        "materials/amazon/verification/gate2/report.json",
        "materials/amazon/verification/gate2/GATE2_REVIEW.md",
        "materials/amazon/verification/gate3/report.json",
        "materials/amazon/verification/gate3/GATE3_REVIEW.md",
        "materials/amazon/verification/gate4/report.json",
        "materials/amazon/verification/gate4/GATE4_REVIEW.md",
        "materials/amazon/verification/gate4/GATE4_APPROVAL.md",
    ]
    for relative in files:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    for relative in manifest["attestation"]["files"]:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    for relative in manifest["attestation"]["trees"]:
        shutil.copytree(REPO_ROOT / relative, root / relative)
    return root


def _valid_result(*, run_id: str = "candidate-001", score: float = 87) -> dict:
    dimensions = {
        name: {"score": value, "max_score": maximum, "passed": 1, "total": 1}
        for name, value, maximum in (
            ("visual", 17, 20),
            ("interactions", 18, 20),
            ("journeys", 34, 40),
            ("robustness", 13, 15),
            ("efficiency", 5, 5),
        )
    }
    return {
        "schema_version": "websitebench.result.v1",
        "run_id": run_id,
        "site_id": "amazon",
        "site_version": "1.0.0",
        "track": "core",
        "status": "passed",
        "score": score,
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


def _valid_calibration(effort: str) -> dict:
    return {
        "schema_version": "websitebench.calibration-result.v1",
        "calibration_id": f"amazon-{effort}-1",
        "benchmark_id": "amazon-136",
        "model": "gpt-5.5-codex",
        "reasoning_effort": effort,
        "track": "core",
        "time_limit_seconds": 1200,
        "status": "PASS",
        "mandatory_task_passed": True,
        "steps": {"passed": 8, "total": 8, "pass_rate": 1.0},
        "usage": {"input_tokens": 10, "output_tokens": 20, "browser_actions": 12},
        "elapsed_seconds": 42.5,
        "harness_error": None,
        "started_at": "2026-07-18T00:00:00Z",
        "finished_at": "2026-07-18T00:00:42Z",
    }


def _write_run(
    root: Path,
    report: dict,
    *,
    current_path: bool = True,
    metadata: dict | None = None,
) -> Path:
    run = root / "artifacts" / "websitebench" / "runs" / report.get("run_id", "broken")
    result = run / "eval" / "evaluation-result.json" if current_path else run / "report.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps(report), encoding="utf-8")
    if metadata is not None:
        (run / "run-meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    return run


def test_discovers_only_amazon_gate_reports_metrics_and_all_images() -> None:
    index = discover_corpus(REPO_ROOT)
    assert [item["key"] for item in index.items] == [AMAZON_ITEM_KEY]
    item = index.items[0]
    assert item["site_id"] == "amazon"
    assert [gate["number"] for gate in item["gates"]] == [2, 3, 4]
    assert item["metrics"] == {
        "regression_assertions": {"passed": 159, "total": 159},
        "browser_journeys": {"passed": 14, "total": 14},
        "semantic_checks": {"passed": 100, "total": 100},
        "stability_checks": {"passed": 100, "total": 100},
        "direct_visual_checks": {"passed": 24, "total": 24},
        "browseruse_trajectories": {"passed": 5, "total": 5},
        "gate4_status": "stale",
    }
    assert item["evidence"]["count"] == 295
    assert item["evidence"]["counts"]["gates"] == {"2": 6, "3": 274, "4": 15}
    assert item["instruction_zh"].startswith("打开 http://host.docker.internal:8153/")
    assert [gate["status"] for gate in item["gates"]] == ["stale", "stale", "stale"]
    assert item["gate4_approval"]["historical_status"] == "approved"
    assert item["readiness_counts"]["invalid"] == 2


def test_failed_gate_content_is_an_invalid_readiness_check(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    report_path = root / "materials" / "amazon" / "verification" / "gate2" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["journeys"][0]["passed"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")

    item = discover_corpus(root).items[0]
    assert item["gates"][0]["status"] == "invalid"
    gate2_readiness = next(
        check for check in item["readiness"] if check["id"] == "gate2_report"
    )
    assert gate2_readiness["status"] == "invalid"


def test_gate_approval_is_current_only_when_all_reports_match_runtime(
    tmp_path: Path,
) -> None:
    root = _fixture_repo(tmp_path)
    runtime_hash = amazon_runtime_fingerprint(root)
    report_paths = {
        "clone": root / "materials/amazon/clone/verification-report.json",
        "gate2": root / "materials/amazon/verification/gate2/report.json",
        "gate3": root / "materials/amazon/verification/gate3/report.json",
        "gate4": root / "materials/amazon/verification/gate4/report.json",
    }
    for name, path in report_paths.items():
        report = json.loads(path.read_text(encoding="utf-8"))
        if name == "clone":
            report["contract"]["runtime_structural_sha256"] = runtime_hash
        else:
            report["runtimeStructuralSha256"] = runtime_hash
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    approval_path = root / "materials/amazon/verification/gate4/GATE4_APPROVAL.md"
    approval = approval_path.read_text(encoding="utf-8")
    gate4_sha = hashlib.sha256(report_paths["gate4"].read_bytes()).hexdigest()
    approval = re.sub(
        r"(Approved report SHA-256:\s*`)[0-9a-f]{64}(`)",
        rf"\g<1>{gate4_sha}\2",
        approval,
    )
    approval_path.write_text(approval, encoding="utf-8")

    current = discover_corpus(root).items[0]
    assert [gate["status"] for gate in current["gates"]] == [
        "passed",
        "passed",
        "approved",
    ]
    assert current["metrics"]["gate4_status"] == "approved"

    app_js = root / "materials/amazon/clone/static/app.js"
    app_js.write_text(app_js.read_text(encoding="utf-8") + "\n// drift\n")
    stale = discover_corpus(root).items[0]
    assert [gate["status"] for gate in stale["gates"]] == [
        "stale",
        "stale",
        "stale",
    ]


def test_public_index_needs_no_allowlist_and_contains_no_internal_paths() -> None:
    value = discover_corpus(REPO_ROOT, profile="public").as_dict()
    assert [item["key"] for item in value["items"]] == [AMAZON_ITEM_KEY]
    assert public_leak_findings(value) == []
    serialized = json.dumps(value).lower().replace("\\", "/")
    assert "server_command" not in serialized
    assert "report_path" not in serialized
    assert "/mnt/" not in serialized


def test_current_and_compatibility_result_paths_publish_only_complete_metadata(
    tmp_path: Path,
) -> None:
    root = _fixture_repo(tmp_path)
    current = _valid_result(run_id="current")
    _write_run(
        root,
        current,
        metadata={
            "run_id": "current",
            "model": "model-a",
            "thinking_level": "high",
            "viewer_public": True,
        },
    )
    old = _valid_result(run_id="old", score=82)
    _write_run(
        root,
        old,
        current_path=False,
        metadata={
            "model": "model-b",
            "thinking_level": "medium",
            "viewer_public": False,
        },
    )
    missing = _valid_result(run_id="missing-meta", score=91)
    _write_run(root, missing, metadata={"viewer_public": True})

    internal = discover_corpus(root)
    public = discover_corpus(root, profile="public")
    assert {run["run_id"] for run in internal.runs} == {"current", "old", "missing-meta"}
    assert [run["run_id"] for run in public.runs] == ["current"]
    assert [row["model"] for row in public.leaderboard] == ["model-a"]


def test_current_evaluation_result_wins_over_compatibility_file(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    run = _write_run(
        root,
        _valid_result(run_id="same", score=92),
        metadata={
            "model": "model-a",
            "thinking_level": "high",
            "viewer_public": True,
        },
    )
    legacy = _valid_result(run_id="same", score=12)
    (run / "report.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert discover_corpus(root).runs[0]["score"] == 92


def test_invalid_schema_and_non_amazon_reports_are_admin_only_findings(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    broken = root / "artifacts" / "websitebench" / "runs" / "broken" / "report.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json", encoding="utf-8")
    other = _valid_result(run_id="other")
    other["site_id"] = "another-site"
    _write_run(root, other, current_path=False, metadata={})
    internal = discover_corpus(root)
    public = discover_corpus(root, profile="public")
    assert internal.runs == []
    assert len(internal.invalid_runs) == 2
    assert public.invalid_runs == []


def test_four_calibrations_are_public_unranked_and_never_official_runs(tmp_path: Path) -> None:
    root = _fixture_repo(tmp_path)
    for effort in ("low", "xhigh", "medium", "high"):
        destination = (
            root
            / "artifacts"
            / "websitebench"
            / "calibrations"
            / effort
            / "calibration-result.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(_valid_calibration(effort)), encoding="utf-8")

    public = discover_corpus(root, profile="public")
    item = public.items[0]
    assert [row["reasoning_effort"] for row in item["calibrations"]] == [
        "xhigh",
        "high",
        "medium",
        "low",
    ]
    assert all("score" not in row and "dimensions" not in row for row in item["calibrations"])
    assert item["official_runs"] == []
    assert public.leaderboard == []
    assert public.as_dict()["summary"] == {
        "item_count": 1,
        "official_run_count": 0,
        "published_run_count": 0,
        "calibration_count": 4,
        "invalid_run_count": 0,
        "evidence_count": 0,
        "gate4_status": "stale",
    }


def test_leaderboard_chooses_best_then_latest_and_has_stable_sort() -> None:
    runs = []
    for run_id, model, score, visual, finished in (
        ("a-old", "Alpha", 90, 15, "2026-07-18T00:01:00Z"),
        ("a-new", "Alpha", 90, 15, "2026-07-18T00:02:00Z"),
        ("beta", "Beta", 90, 19, "2026-07-18T00:01:00Z"),
        ("gamma", "Gamma", 91, 10, "2026-07-18T00:01:00Z"),
    ):
        run = _valid_result(run_id=run_id, score=score)
        run["dimensions"]["visual"]["score"] = visual
        run.update(
            {
                "model": model,
                "thinking_level": "high",
                "publishable": True,
                "finished_at": finished,
            }
        )
        runs.append(run)
    rows = aggregate_leaderboard(runs)
    assert [row["run_id"] for row in rows] == ["gamma", "beta", "a-new"]


def test_path_resolver_and_fingerprint_are_content_sensitive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        _safe_resolve(tmp_path, tmp_path / ".." / "outside")
    first = tmp_path / "a.txt"
    first.write_text("one")
    before = fingerprint([first], tmp_path)
    first.write_text("two")
    assert fingerprint([first], tmp_path) != before
