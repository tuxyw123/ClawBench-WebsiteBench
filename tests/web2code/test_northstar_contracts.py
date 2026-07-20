"""Gate W1 contract tests for the Northstar Market corpus item."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from clawbench.web2code.contracts import validate_site_contract


REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = REPO_ROOT / "websitebench" / "northstar-market"
PUBLIC_ROOT = SITE_ROOT / "public"
SCHEMA_ROOT = REPO_ROOT / "websitebench" / "schemas"


def _json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _validate_definition(schema_name: str, definition: str, value: object) -> None:
    schema = _json(SCHEMA_ROOT / schema_name)
    definition_schema = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    Draft202012Validator(definition_schema, format_checker=FormatChecker()).validate(value)


def test_w1_public_contract_is_internally_consistent() -> None:
    manifest = validate_site_contract(PUBLIC_ROOT / "manifest.yaml")

    assert manifest["site_id"] == "northstar-market"
    assert manifest["site_version"] == "1.0.0"
    assert manifest["family_id"] == "white-label-commerce-v1"
    assert manifest["difficulty"] == "hard"


def test_frozen_seed_and_track_policy() -> None:
    manifest = validate_site_contract(PUBLIC_ROOT / "manifest.yaml")

    assert [seed["id"] for seed in manifest["seeds"]["public"]] == [1101, 1102]
    assert set(manifest["seeds"]) == {"public"}
    assert "9101" not in (PUBLIC_ROOT / "manifest.yaml").read_text(encoding="utf-8")
    assert "9199" not in (PUBLIC_ROOT / "manifest.yaml").read_text(encoding="utf-8")
    assert manifest["tracks"]["core"]["human_messages"] == 0
    assert manifest["tracks"]["hitl"] == {
        "enabled": True,
        "human_messages": 12,
        "human_minutes": 90,
        "human_file_edits": False,
    }


def test_frozen_agent_budget_and_browser_policy() -> None:
    manifest = validate_site_contract(PUBLIC_ROOT / "manifest.yaml")

    assert manifest["agent_budget"] == {
        "wall_time_seconds": 8 * 60 * 60,
        "token_budget": 2_000_000,
        "browser_actions": 1_000,
        "candidate_builds": 20,
    }
    assert manifest["browser_policy"]["version"] == "0.12.6"
    assert manifest["browser_policy"]["reference_access"] == "continuous"
    denied = " ".join(manifest["browser_policy"]["denied"]).lower()
    for forbidden in ("raw html", "source map", "devtools", "cache", "download"):
        assert forbidden in denied


def test_admin_contract_examples_validate() -> None:
    _validate_definition(
        "admin-contract.schema.json",
        "reset_request",
        {
            "schema_version": 1,
            "run_id": "run-contract-test",
            "seed": 9101,
            "now": "2026-01-15T12:00:00Z",
            "fixture_path": "/bench-fixtures/9101.json",
        },
    )


def test_task_envelope_for_pilot_validates() -> None:
    manifest = validate_site_contract(PUBLIC_ROOT / "manifest.yaml")
    task_schema = _json(SCHEMA_ROOT / "task.schema.json")
    task = {
        "schema_version": "websitebench.task.v1",
        "run_id": "northstar-core-contract-test",
        "task_id": "northstar-market",
        "site_id": "northstar-market",
        "site_version": "1.0.0",
        "track": "core",
        "target_url": "http://reference.test:8080",
        "mailbox_url": "http://mailbox.test:8025",
        "public_files": {
            "manifest": "/task/public/manifest.yaml",
            "prd": "/task/public/PRD.md",
            "candidate_contract": "/task/public/candidate-contract.md",
            "smoke_cases": "/task/public/public-smoke-cases.json",
        },
        "budget": manifest["agent_budget"],
        "browser_gateway": {
            "url": "http://browser-gateway:7000",
            "tool_name": "controlled_browser",
            "reference_access": "continuous",
        },
        "candidate_workspace": "/workspace/candidate",
        "agent": manifest["pilot_agent"],
        "issued_at": "2026-01-15T12:00:00Z",
    }

    Draft202012Validator(task_schema, format_checker=FormatChecker()).validate(task)
    _validate_definition(
        "admin-contract.schema.json",
        "clock_advance_request",
        {"seconds": 300},
    )


def test_scoring_is_exactly_one_hundred_points() -> None:
    scoring = _json(PUBLIC_ROOT / "scoring.json")
    dimensions = scoring["dimensions"]

    assert sum(item["max_score"] for item in dimensions.values()) == 100
    assert len(dimensions["journeys"]["journeys"]) == 8
    assert dimensions["journeys"]["terminal_failure_cap"] == 2.5
    assert len(dimensions["robustness"]["groups"]) == 15
    assert len(dimensions["efficiency"]["targets"]) == 5
    assert scoring["hard_failure_score"] == 0


def test_visual_formula_and_required_viewports_are_frozen() -> None:
    visual = _json(PUBLIC_ROOT / "visual-checkpoints.json")

    assert visual["viewports"]["desktop"] == {
        "width": 1440,
        "height": 1000,
        "device_scale_factor": 1,
    }
    assert visual["viewports"]["mobile"] == {
        "width": 390,
        "height": 844,
        "device_scale_factor": 1,
    }
    assert sum(visual["comparison"].values()) == 1
    assert len(visual["checkpoints"]) == 12


def test_prd_declares_all_hidden_rule_families() -> None:
    prd = (PUBLIC_ROOT / "PRD.md").read_text(encoding="utf-8").lower()

    for declared_rule in (
        "five-minute",
        "30 minutes",
        "60 minutes",
        "24 hours",
        "8.25%",
        "idempotent",
        "atomic",
        "controlled clock",
        "cross-account",
        "404",
        "test mailbox",
    ):
        assert declared_rule in prd


def test_public_brand_contract_does_not_prompt_a_commercial_site_name() -> None:
    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PUBLIC_ROOT.iterdir()
        if path.suffix in {".md", ".yaml", ".json"}
    ).lower()

    assert "amazon" not in public_text
