"""W4 tests for visual metrics, weighted scoring, and failure reports."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from clawbench.web2code.reporting import build_result, failure_markdown, validate_result
from clawbench.web2code.scoring import score_evaluation
from clawbench.web2code.visual import checkpoint_similarity


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = REPO_ROOT / "websitebench" / "northstar-market" / "public"
SCHEMA_ROOT = REPO_ROOT / "websitebench" / "schemas"


def scoring_config() -> dict:
    return json.loads((PUBLIC_ROOT / "scoring.json").read_text())


def checkpoint(identifier: str, passed: bool = True) -> dict:
    return {
        "id": identifier,
        "passed": passed,
        "expected": "expected-state",
        "actual": "expected-state" if passed else "different-state",
        "evidence_ids": [],
    }


def perfect_facts() -> dict:
    return {
        "visual": [{"id": f"visual-{index}", "similarity": 1.0} for index in range(12)],
        "interactions": [{"id": f"interaction-{index}", "passed": True} for index in range(20)],
        "journeys": [
            {
                "id": identifier,
                "seed": 9101 + index % 5,
                "terminal_passed": True,
                "checkpoints": [checkpoint(f"{identifier}-{step}") for step in range(4)],
            }
            for index, identifier in enumerate(scoring_config()["dimensions"]["journeys"]["journeys"])
        ],
        "robustness": [
            {"id": identifier, "passed": True}
            for identifier in scoring_config()["dimensions"]["robustness"]["groups"]
        ],
        "efficiency": {
            "clean_build_seconds": 120,
            "image_bytes": 500_000_000,
            "peak_memory_bytes": 500_000_000,
            "p95_latency_ms_at_10_concurrent": 200,
            "source_bytes": 2_000_000,
        },
        "hard_failures": [],
    }


def test_identical_visual_inputs_score_one() -> None:
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    image[20:60, 30:70] = [32, 110, 78]
    geometry = [
        {"role": "button", "name": "Add to cart", "x": 0.3, "y": 0.5, "width": 0.2, "height": 0.1}
    ]
    result = checkpoint_similarity(
        image,
        image.copy(),
        reference_text="Northstar Add to cart $12.99",
        candidate_text="Northstar Add to cart $12.99",
        reference_geometry=geometry,
        candidate_geometry=geometry,
    )

    assert result["similarity"] == 1
    assert all(value == 1 for key, value in result.items() if key != "similarity")


def test_perfect_facts_score_exactly_one_hundred() -> None:
    scored = score_evaluation(perfect_facts(), scoring_config())

    assert scored["score"] == 100
    assert scored["status"] == "passed"
    assert {name: value["score"] for name, value in scored["dimensions"].items()} == {
        "visual": 20,
        "interactions": 20,
        "journeys": 40,
        "robustness": 15,
        "efficiency": 5,
    }


def test_terminal_journey_failure_caps_partial_score_at_two_point_five() -> None:
    facts = perfect_facts()
    facts["journeys"][0]["terminal_passed"] = False
    scored = score_evaluation(facts, scoring_config())

    assert scored["journeys"][0]["score"] == 2.5
    assert scored["dimensions"]["journeys"]["score"] == 37.5


def test_any_hard_failure_forces_zero_but_keeps_diagnostics() -> None:
    facts = perfect_facts()
    facts["hard_failures"] = [
        {"code": "RUNTIME_REFERENCE_REQUEST", "message": "candidate requested target", "evidence_ids": []}
    ]
    scored = score_evaluation(facts, scoring_config())

    assert scored["score"] == 0
    assert scored["status"] == "hard_failed"
    assert scored["dimensions"]["visual"]["score"] == 20


def test_result_schema_and_markdown_failure_report() -> None:
    facts = perfect_facts()
    facts.update(
        {
            "seeds": [
                {"seed": 9101, "purpose": "functional", "reset_passed": True, "tests_passed": 1, "tests_total": 1}
            ],
            "failures": [
                {
                    "id": "cart-refresh",
                    "category": "cart",
                    "severity": "major",
                    "summary": "Guest cart disappeared after refresh",
                    "expected": {"quantity": 2},
                    "actual": {"quantity": 0},
                    "reproduction": ["Reset seed 9101", "Add two units", "Refresh the page"],
                    "evidence_ids": [],
                }
            ],
            "evidence": [],
            "versions": {"browser-use": "0.12.6"},
        }
    )
    scored = score_evaluation(facts, scoring_config())
    result = build_result(
        run={
            "run_id": "report-test",
            "site_id": "northstar-market",
            "site_version": "1.0.0",
            "track": "core",
            "started_at": "2026-01-15T12:00:00Z",
            "finished_at": "2026-01-15T13:00:00Z",
        },
        scored=scored,
        facts=facts,
    )
    validate_result(result, SCHEMA_ROOT / "report.schema.json")
    markdown = failure_markdown(result)

    assert "Guest cart disappeared" in markdown
    assert "100.00 / 100" in markdown


def test_failure_corpus_has_one_controlled_mutation_per_failure_family() -> None:
    manifest = json.loads(
        (
            REPO_ROOT
            / "websitebench"
            / "northstar-market"
            / "judge"
            / "failure-corpus"
            / "manifest.json"
        ).read_text()
    )
    mutations = manifest["mutations"]

    assert len(mutations) == 12
    assert len({item["id"] for item in mutations}) == len(mutations)
    assert {"persistence", "time", "authentication", "session", "cart", "checkout", "inventory", "order", "security", "concurrency", "visual"} <= {
        item["category"] for item in mutations
    }

