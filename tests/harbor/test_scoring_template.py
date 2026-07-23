from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from clawbench.harbor.materialize import materialize_instance
from clawbench.harbor.scaffold import initialize_instance, initialize_site


def _bundle(tmp_path: Path) -> Path:
    corpus = tmp_path / "harbor"
    (corpus / "instances").mkdir(parents=True)
    initialize_site(
        corpus / "sites" / "demo",
        site_id="demo",
        display_name="Demo",
    )
    instance = initialize_instance(
        corpus / "instances" / "demo-rebuild",
        instance_id="demo-rebuild",
        site_manifest="sites/demo/site.yaml",
        author_name="Benchmark Team",
        author_email="bench@example.test",
    )
    return materialize_instance(instance, tmp_path / "bundle")


def _report(required: dict[str, object]) -> dict[str, object]:
    scores = {
        "api::core/write-path": 0.5,
        "visual::primary/reference-checkpoint": 0.8,
        "robustness::refresh-and-retry": 0.0,
    }
    tests = []
    for node in required["nodes"]:
        score = scores.get(node, 1.0)
        tests.append(
            {
                "name": node,
                "status": "passed" if score == 1 else "failed",
                "extra": {"clawbench_score": score},
            }
        )
    return {
        "results": {
            "tool": {"name": "unit-test"},
            "summary": {"tests": len(tests)},
            "tests": tests,
            "extra": {"hard_failures": []},
        }
    }


def test_fractional_dimension_scoring_and_exact_set_validation(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    required_path = bundle / "tests/required-nodes.json"
    required = json.loads(required_path.read_text(encoding="utf-8"))
    report_path = tmp_path / "ctrf.json"
    report_path.write_text(
        json.dumps(_report(required)),
        encoding="utf-8",
    )
    output = tmp_path / "score"
    output.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            str(bundle / "tests/compute_reward.py"),
            str(report_path),
            str(required_path),
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    score = json.loads((output / "scorecard.json").read_text(encoding="utf-8"))
    assert not (output / "reward.json").exists()
    assert float((output / "reward.txt").read_text(encoding="utf-8")) == 0.77
    assert score["score"] == 77
    assert score["reward"] == 0.77
    assert score["dimensions"]["visual"]["score"] == 12

    invalid = _report(required)
    invalid["results"]["tests"].pop()
    report_path.write_text(json.dumps(invalid), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(bundle / "tests/compute_reward.py"),
            str(report_path),
            str(required_path),
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert not (output / "reward.txt").exists()
    verdict = json.loads((output / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["valid"] is False
    assert "EXACT_SET_MISMATCH" in verdict["reason"]


def test_hard_failure_zeroes_an_otherwise_passing_run(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    required_path = bundle / "tests/required-nodes.json"
    required = json.loads(required_path.read_text(encoding="utf-8"))
    report = _report(required)
    for test in report["results"]["tests"]:
        test["status"] = "passed"
        test["extra"]["clawbench_score"] = 1
    report["results"]["extra"]["hard_failures"] = ["REFERENCE_RESET_DIVERGED"]
    report_path = tmp_path / "ctrf.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    output = tmp_path / "score"
    output.mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            str(bundle / "tests/compute_reward.py"),
            str(report_path),
            str(required_path),
            str(output),
        ],
        check=False,
    )

    assert completed.returncode == 0
    score = json.loads((output / "scorecard.json").read_text(encoding="utf-8"))
    assert score["score"] == 0
    assert score["reward"] == 0
