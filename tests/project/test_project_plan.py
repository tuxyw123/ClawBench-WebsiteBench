from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawbench.project.cli import main
from clawbench.project.manifest import (
    ProjectPlanError,
    load_project_plan,
    project_status,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = REPOSITORY_ROOT / "project" / "plan.json"


def _plan_data() -> dict[str, object]:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def test_repository_plan_is_valid_and_not_falsely_release_ready() -> None:
    plan = load_project_plan(PLAN_PATH)
    status = project_status(plan)

    assert plan.data["schema_version"] == "clawbench.project.v1"
    assert status["project_id"] == "clawbench-websitebench"
    assert status["workstreams"]["complete"] >= 2
    assert status["release_gates"]["passed"] == 1
    assert status["release_ready"] is False
    assert any(item["priority"] == "P0" for item in status["next_actions"])
    assert any(item["id"] == "viewer-integration" for item in status["blocked"])


def test_cli_validate_status_and_release_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["validate", "--plan", str(PLAN_PATH)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"

    assert main(["status", "--plan", str(PLAN_PATH)]) == 0
    assert json.loads(capsys.readouterr().out)["release_ready"] is False

    assert main(["check-release", "--plan", str(PLAN_PATH)]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "not_ready"


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "plan.json"
    target.write_text(
        '{"schema_version":"clawbench.project.v1","schema_version":"duplicate"}',
        encoding="utf-8",
    )

    with pytest.raises(ProjectPlanError, match="duplicate key"):
        load_project_plan(target)


def test_complete_work_requires_evidence(tmp_path: Path) -> None:
    data = _plan_data()
    workstreams = data["workstreams"]
    assert isinstance(workstreams, list)
    workstreams[0]["evidence"] = []
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ProjectPlanError, match="complete work requires evidence"):
        load_project_plan(target)


def test_dependencies_and_owners_must_resolve(tmp_path: Path) -> None:
    data = _plan_data()
    backlog = data["backlog"]
    assert isinstance(backlog, list)
    backlog[0]["depends_on"] = ["missing-work-item"]
    backlog[0]["owner"] = "missing-role"
    target = tmp_path / "plan.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ProjectPlanError) as error:
        load_project_plan(target)

    message = str(error.value)
    assert "unknown dependency 'missing-work-item'" in message
    assert "unknown owner 'missing-role'" in message
