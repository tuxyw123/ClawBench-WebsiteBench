"""CLI for the manifest-driven offline-clone harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .gates import GateError, run_gate
from .manifest import (
    GATE_ORDER,
    ManifestValidationError,
    initialize_site,
    load_manifest,
)
from .records import RecordError, append_record, verify_trajectory_anchor
from .report import full_report, status_report
from .state import StateError, initial_state, load_state, write_state


def _emit(value: dict[str, Any], output: Path | None = None) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if output is None:
        print(payload, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")
    print(output)


def _init(args: argparse.Namespace) -> int:
    manifest = initialize_site(
        args.site_dir,
        site_id=args.site_id,
        display_name=args.display_name,
        source_url=args.source_url,
    )
    state = initial_state(manifest)
    write_state(manifest, state)
    _emit(
        {
            "status": "initialized",
            "site_id": manifest.data["site_id"],
            "manifest": str(manifest.path),
            "stage": "INIT",
        }
    )
    return 0


def _validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.site)
    state = load_state(manifest)
    count, head = verify_trajectory_anchor(manifest, state)
    _emit(
        {
            "status": "valid",
            "site_id": manifest.data["site_id"],
            "manifest": str(manifest.path),
            "manifest_sha256": manifest.sha256,
            "trajectory": {"count": count, "head_sha256": head},
        }
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    _emit(status_report(load_manifest(args.site)))
    return 0


def _gate(args: argparse.Namespace) -> int:
    result = run_gate(load_manifest(args.site), args.phase)
    _emit(result)
    return 0 if result["status"] == "passed" else 1


def _report(args: argparse.Namespace) -> int:
    _emit(full_report(load_manifest(args.site)), args.out)
    return 0


def _record(args: argparse.Namespace) -> int:
    message = sys.stdin.read(4001) if args.message_stdin else args.message
    record = append_record(
        load_manifest(args.site), kind=args.kind, message=message
    )
    _emit(
        {
            "status": "recorded",
            "sequence": record["sequence"],
            "record_sha256": record["record_sha256"],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-offline-clone")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a new offline-clone site skeleton")
    init.add_argument("--site-dir", type=Path, required=True)
    init.add_argument("--site-id", required=True)
    init.add_argument("--display-name", required=True)
    init.add_argument("--source-url", required=True)
    init.set_defaults(function=_init)

    for name, function in (
        ("validate", _validate),
        ("status", _status),
        ("report", _report),
    ):
        command = subparsers.add_parser(name)
        command.add_argument("--site", type=Path, required=True)
        if name == "report":
            command.add_argument("--out", type=Path)
        command.set_defaults(function=function)

    gate = subparsers.add_parser("gate", help="run one lifecycle gate")
    gate.add_argument("phase", choices=GATE_ORDER)
    gate.add_argument("--site", type=Path, required=True)
    gate.set_defaults(function=_gate)

    record = subparsers.add_parser("record", help="append a safe trajectory record")
    record.add_argument("--site", type=Path, required=True)
    record.add_argument("--kind", choices=sorted({
        "action", "correction", "decision", "feedback", "observation", "note"
    }), required=True)
    message = record.add_mutually_exclusive_group(required=True)
    message.add_argument("--message")
    message.add_argument(
        "--message-stdin",
        action="store_true",
        help="read the record message from stdin to avoid shell-history exposure",
    )
    record.set_defaults(function=_record)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.function(args))
    except (
        FileExistsError,
        GateError,
        ManifestValidationError,
        OSError,
        RecordError,
        StateError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
