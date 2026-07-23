"""Load, validate, and summarize the repository project plan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_SCHEMA = "project-plan.schema.json"
PROJECT_SCHEMA_VERSION = "clawbench.project.v1"
MAX_PLAN_BYTES = 2 * 1024 * 1024
WORK_STATUS = ("planned", "in_progress", "blocked", "complete")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


class ProjectPlanError(ValueError):
    """Raised when the project plan is invalid or internally inconsistent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__(
            "Project plan validation failed:\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedProjectPlan:
    path: Path
    data: dict[str, Any]
    sha256: str


def _schema_path() -> Path:
    source_root = Path(__file__).resolve().parents[3]
    source = source_root / "websitebench" / "schemas" / PROJECT_SCHEMA
    if source.is_file():
        return source
    bundled = Path(__file__).resolve().parents[1] / "viewer" / "_schemas" / PROJECT_SCHEMA
    if bundled.is_file():
        return bundled
    raise FileNotFoundError(f"Project schema is unavailable: {PROJECT_SCHEMA}")


def _load_schema() -> dict[str, Any]:
    value = json.loads(_schema_path().read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(f"duplicate key {key!r}")
        value[key] = item
    return value


def _schema_problems(value: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(
        _load_schema(),
        format_checker=FormatChecker(),
    )
    problems: list[str] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        suffix = ".".join(str(part) for part in error.absolute_path)
        problems.append(f"plan{'.' + suffix if suffix else ''}: {error.message}")
    return problems


def _duplicate_id_problems(value: dict[str, Any], collection: str) -> list[str]:
    identifiers = [
        item.get("id")
        for item in value.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    return [f"plan.{collection}: duplicate id {item!r}" for item in duplicates]


def _completion_problems(value: dict[str, Any], collection: str) -> list[str]:
    problems: list[str] = []
    for item in value.get(collection, []):
        if not isinstance(item, dict):
            continue
        identifier = item.get("id", "<unknown>")
        status = item.get("status")
        evidence = item.get("evidence")
        blockers = item.get("blocked_by")
        if status == "complete" and not evidence:
            problems.append(
                f"plan.{collection}.{identifier}: complete work requires evidence"
            )
        if status == "blocked" and not blockers:
            problems.append(
                f"plan.{collection}.{identifier}: blocked work requires blocked_by"
            )
    return problems


def _dependency_problems(
    value: dict[str, Any],
    collection: str,
    dependency_field: str = "depends_on",
) -> list[str]:
    items = [item for item in value.get(collection, []) if isinstance(item, dict)]
    identifiers = {
        item.get("id") for item in items if isinstance(item.get("id"), str)
    }
    problems: list[str] = []
    for item in items:
        identifier = item.get("id", "<unknown>")
        for dependency in item.get(dependency_field, []):
            if dependency == identifier:
                problems.append(
                    f"plan.{collection}.{identifier}: cannot depend on itself"
                )
            elif dependency not in identifiers:
                problems.append(
                    f"plan.{collection}.{identifier}: unknown dependency "
                    f"{dependency!r}"
                )
    return problems


def _semantic_problems(value: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for collection in (
        "roles",
        "workstreams",
        "milestones",
        "backlog",
        "risks",
        "decisions",
        "release_gates",
    ):
        problems.extend(_duplicate_id_problems(value, collection))
    for collection in ("workstreams", "milestones", "backlog"):
        problems.extend(_completion_problems(value, collection))
    problems.extend(_dependency_problems(value, "milestones"))
    problems.extend(_dependency_problems(value, "backlog"))
    role_ids = {
        role.get("id")
        for role in value.get("roles", [])
        if isinstance(role, dict) and isinstance(role.get("id"), str)
    }
    for collection in (
        "workstreams",
        "milestones",
        "backlog",
        "risks",
        "release_gates",
    ):
        for item in value.get(collection, []):
            if not isinstance(item, dict):
                continue
            if item.get("owner") not in role_ids:
                problems.append(
                    f"plan.{collection}.{item.get('id', '<unknown>')}: "
                    f"unknown owner {item.get('owner')!r}"
                )
    for gate in value.get("release_gates", []):
        if not isinstance(gate, dict):
            continue
        if gate.get("status") == "passed" and not gate.get("evidence"):
            problems.append(
                f"plan.release_gates.{gate.get('id', '<unknown>')}: "
                "passed gate requires evidence"
            )
        if gate.get("status") == "blocked" and not gate.get("blocked_by"):
            problems.append(
                f"plan.release_gates.{gate.get('id', '<unknown>')}: "
                "blocked gate requires blocked_by"
            )
    return problems


def load_project_plan(path: Path | str) -> LoadedProjectPlan:
    plan_path = Path(path).resolve()
    try:
        raw = plan_path.read_bytes()
    except OSError as exc:
        raise ProjectPlanError([f"{plan_path}: cannot read project plan: {exc}"]) from exc
    if len(raw) > MAX_PLAN_BYTES:
        raise ProjectPlanError(
            [f"{plan_path}: project plan exceeds {MAX_PLAN_BYTES} bytes"]
        )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise ProjectPlanError([f"{plan_path}: invalid JSON: {exc}"]) from exc
    if not isinstance(value, dict):
        raise ProjectPlanError([f"{plan_path}: project plan must contain an object"])
    problems = _schema_problems(value)
    if value.get("schema_version") != PROJECT_SCHEMA_VERSION:
        problems.append(
            "plan.schema_version: expected "
            f"{PROJECT_SCHEMA_VERSION!r}, got {value.get('schema_version')!r}"
        )
    problems.extend(_semantic_problems(value))
    if problems:
        raise ProjectPlanError(problems)
    return LoadedProjectPlan(
        path=plan_path,
        data=value,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in WORK_STATUS}
    for item in items:
        status = item.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def project_status(plan: LoadedProjectPlan) -> dict[str, Any]:
    data = plan.data
    workstreams = data["workstreams"]
    milestones = data["milestones"]
    backlog = data["backlog"]
    release_gates = data["release_gates"]
    next_actions = sorted(
        (
            {
                "id": item["id"],
                "priority": item["priority"],
                "status": item["status"],
                "title": item["title"],
                "owner": item["owner"],
            }
            for item in backlog
            if item["status"] != "complete"
        ),
        key=lambda item: (PRIORITY_ORDER[item["priority"]], item["id"]),
    )
    blocked = [
        {
            "kind": collection,
            "id": item["id"],
            "title": item["title"],
            "blocked_by": item["blocked_by"],
        }
        for collection, items in (
            ("workstream", workstreams),
            ("milestone", milestones),
            ("backlog", backlog),
        )
        for item in items
        if item["status"] == "blocked"
    ]
    gate_counts = {
        status: sum(gate["status"] == status for gate in release_gates)
        for status in ("pending", "passed", "failed", "blocked")
    }
    return {
        "status": "valid",
        "project_id": data["project_id"],
        "project_status": data["status"],
        "updated_at": data["updated_at"],
        "plan_sha256": plan.sha256,
        "workstreams": _status_counts(workstreams),
        "milestones": _status_counts(milestones),
        "backlog": _status_counts(backlog),
        "release_gates": gate_counts,
        "release_ready": bool(release_gates)
        and all(gate["status"] == "passed" for gate in release_gates),
        "next_actions": next_actions,
        "blocked": blocked,
    }
