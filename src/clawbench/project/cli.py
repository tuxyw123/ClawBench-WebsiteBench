"""Command-line interface for repository project governance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .manifest import ProjectPlanError, load_project_plan, project_status


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _validate(args: argparse.Namespace) -> int:
    plan = load_project_plan(args.plan)
    _emit(
        {
            "status": "valid",
            "project_id": plan.data["project_id"],
            "updated_at": plan.data["updated_at"],
            "plan_sha256": plan.sha256,
        }
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    _emit(project_status(load_project_plan(args.plan)))
    return 0


def _check_release(args: argparse.Namespace) -> int:
    status = project_status(load_project_plan(args.plan))
    _emit(
        {
            "status": "ready" if status["release_ready"] else "not_ready",
            "project_id": status["project_id"],
            "release_gates": status["release_gates"],
            "blocked": status["blocked"],
        }
    )
    return 0 if status["release_ready"] else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-project")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="validate the project plan schema and cross-references",
    )
    validate.add_argument("--plan", type=Path, default=Path("project/plan.json"))
    validate.set_defaults(function=_validate)

    status = subparsers.add_parser(
        "status",
        help="summarize workstreams, milestones, backlog, gates, and blockers",
    )
    status.add_argument("--plan", type=Path, default=Path("project/plan.json"))
    status.set_defaults(function=_status)

    check_release = subparsers.add_parser(
        "check-release",
        help="return success only when every declared release gate has passed",
    )
    check_release.add_argument("--plan", type=Path, default=Path("project/plan.json"))
    check_release.set_defaults(function=_check_release)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.function(args))
    except (FileNotFoundError, OSError, ProjectPlanError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
