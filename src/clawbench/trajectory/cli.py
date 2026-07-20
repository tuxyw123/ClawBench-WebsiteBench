"""Command-line interface for offline trajectory export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .exporter import (
    TrajectoryError,
    export_clone_history,
    export_web2code_run,
    validate_bundle,
)


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    manifest = result["manifest"]
    return {
        "status": "exported",
        "bundle": result["bundle"],
        "archive": result["archive"],
        "bundle_id": manifest["bundle_id"],
        "capture": manifest["capture"],
        "events": manifest["event_count"],
        "artifacts": len(manifest["artifacts"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-trajectory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone = subparsers.add_parser(
        "export-clone", help="export checked-in CODEX_TRAJECTORY.md provenance"
    )
    clone.add_argument("clone_dir", type=Path)
    clone.add_argument("--task", type=Path, required=True)
    clone.add_argument("--repo-root", type=Path, default=Path("."))
    clone.add_argument("--out", type=Path, required=True)
    clone.add_argument("--without-code", action="store_true")
    clone.add_argument("--include-observations", action="store_true")
    clone.add_argument("--archive", action="store_true")
    clone.add_argument("--overwrite", action="store_true")

    run = subparsers.add_parser("export-run", help="export a Web2Code run directory")
    run.add_argument("run_dir", type=Path)
    run.add_argument("--repo-root", type=Path, default=Path("."))
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--without-code", action="store_true")
    run.add_argument("--archive", action="store_true")
    run.add_argument("--overwrite", action="store_true")

    validate = subparsers.add_parser("validate", help="validate an exported bundle")
    validate.add_argument("bundle", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export-clone":
            result = export_clone_history(
                repository_root=args.repo_root,
                clone_dir=args.clone_dir,
                task_path=args.task,
                output=args.out,
                include_code=not args.without_code,
                include_observations=args.include_observations,
                archive=args.archive,
                overwrite=args.overwrite,
            )
            value = _summary(result)
        elif args.command == "export-run":
            result = export_web2code_run(
                repository_root=args.repo_root,
                run_dir=args.run_dir,
                output=args.out,
                include_code=not args.without_code,
                archive=args.archive,
                overwrite=args.overwrite,
            )
            value = _summary(result)
        else:
            value = validate_bundle(args.bundle)
    except (OSError, ValueError, json.JSONDecodeError, TrajectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
