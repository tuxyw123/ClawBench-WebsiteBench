from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify_phase3.py"
SPEC = importlib.util.spec_from_file_location("amazon_phase3", TOOL)
assert SPEC and SPEC.loader
phase3 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = phase3
SPEC.loader.exec_module(phase3)


def test_phase3_contract_covers_frozen_matrix() -> None:
    config = json.loads((ROOT / "phase3-fidelity.json").read_text())
    assert config["format"] == "clawbench.amazon.phase3-fidelity.v1"
    assert len(config["scenes"]) == 20
    assert len(config["viewports"]) == 5
    assert {scene["mode"] for scene in config["scenes"]} == {
        "direct-visual",
        "structural",
        "unavailable",
    }
    assert len({scene["source"] for scene in config["scenes"]}) == 20
    assert config["sourceBaseline"]["networkPolicy"] == "frozen-evidence-only"


def test_source_quality_classification() -> None:
    assert phase3.source_quality({"navigationStatus": 202, "dom": {}}) == "protected-or-empty"
    assert phase3.source_quality({"navigationStatus": 404, "dom": {}}) == "expected-error"
    assert phase3.source_quality({"navigationStatus": 200, "dom": {"captureQuality": {"bodyTextLength": 999}}}) == "partial"
    assert phase3.source_quality({"navigationStatus": 200, "dom": {"captureQuality": {"bodyTextLength": 1000}}}) == "strong"


def test_visual_gate_requires_every_declared_threshold() -> None:
    thresholds = {
        "composite": 0.35,
        "ssim": 0.18,
        "edge_f1": 0.08,
        "color_histogram": 0.55,
        "normalized_mae_max": 0.5,
    }
    passing = {
        "ssim": 0.4,
        "edge_f1": 0.3,
        "color_histogram": 0.8,
        "normalized_mae": 0.2,
    }
    assert phase3.direct_visual_pass(passing, thresholds)
    for key, value in (
        ("ssim", 0.17),
        ("edge_f1", 0.07),
        ("color_histogram", 0.54),
        ("normalized_mae", 0.51),
    ):
        failing = {**passing, key: value}
        assert not phase3.direct_visual_pass(failing, thresholds)


def test_structural_similarity_is_bounded_and_rewards_matching_shape() -> None:
    source = {
        "counts": {"elements": 100, "links": 20, "forms": 1, "buttons": 5, "images": 10},
        "dimensions": {"documentHeight": 900},
        "headingsAndControls": [{"text": "Your Account"}, {"text": "Your Orders"}],
    }
    matching = {
        "counts": {"elements": 100, "links": 20, "forms": 1, "buttons": 5, "images": 10},
        "dimensions": {"documentHeight": 900},
        "headingsAndControls": ["Your Account", "Your Orders"],
    }
    sparse = {
        "counts": {"elements": 5, "links": 0, "forms": 0, "buttons": 0, "images": 0},
        "dimensions": {"documentHeight": 100},
        "headingsAndControls": ["Unknown"],
    }
    perfect = phase3.structural_similarity(source, matching)
    weak = phase3.structural_similarity(source, sparse)
    assert perfect["score"] == 1.0
    assert 0 <= weak["score"] < perfect["score"]


def test_load_inputs_rejects_mutated_source_report(tmp_path: Path) -> None:
    config = json.loads((ROOT / "phase3-fidelity.json").read_text())
    source_path = tmp_path / "report.json"
    source_path.write_text(
        json.dumps(
            {
                "format": phase3.SOURCE_FORMAT,
                "snapshotId": config["sourceBaseline"]["snapshotId"],
                "pages": [],
            }
        )
    )
    with pytest.raises(ValueError, match="SHA-256"):
        phase3.load_inputs(ROOT / "phase3-fidelity.json", source_path)
