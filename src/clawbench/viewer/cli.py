"""Command-line entry point for the Amazon WebsiteBench Viewer."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from .auth import AuthSettings, hash_password
from .discovery import AMAZON_ITEM_KEY, discover_corpus, public_leak_findings
from .evidence import EvidenceStore
from .reviews import ReviewStore


def _write_or_print(value: dict[str, Any], output: str | None) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        print(path)
    else:
        print(payload, end="")


def _root(args: argparse.Namespace) -> Path:
    return Path(args.repo_root).resolve()


def _validate(args: argparse.Namespace) -> int:
    try:
        index = discover_corpus(_root(args), profile=args.profile)
        value = index.as_dict()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    invalid = [
        {"item_key": item["key"], "checks": [check for check in item["readiness"] if check["status"] == "invalid"]}
        for item in index.items
        if any(check["status"] == "invalid" for check in item["readiness"])
    ]
    leaks = public_leak_findings(value) if args.profile == "public" else []
    invalid_runs = index.invalid_runs if args.profile == "internal" else []
    failed = bool(invalid or leaks or invalid_runs)
    result = {
        "status": "invalid" if failed else "valid",
        "profile": args.profile,
        "items": len(index.items),
        "official_runs": len(index.runs),
        "invalid_items": invalid,
        "invalid_runs": invalid_runs,
        "public_leaks": leaks,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if failed else 0


def _index(args: argparse.Namespace) -> int:
    try:
        index = discover_corpus(_root(args), profile=args.profile)
        value = index.as_dict()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _write_or_print(value, args.out)
    return 0


def _declared_checkpoints(root: Path, item: dict[str, Any]) -> list[tuple[str, str]]:
    del root, item
    return []


def _capture(args: argparse.Namespace) -> int:
    root = _root(args)
    try:
        index = discover_corpus(root)
        item = index.by_key(args.item)
        if item is None:
            raise ValueError(f"unknown corpus item: {args.item}")
        store = EvidenceStore(
            Path(args.artifacts).resolve()
            if args.artifacts
            else root / "artifacts" / "websitebench-viewer" / "visual",
            root,
        )
        ignore_regions = json.loads(args.ignore_regions) if args.ignore_regions else []
        if args.source_image or args.candidate_image:
            if not args.checkpoint or not args.viewport:
                raise ValueError("--checkpoint and --viewport are required with image inputs")
            manifest = store.upsert(
                args.item,
                args.checkpoint,
                args.viewport,
                source_image=Path(args.source_image) if args.source_image else None,
                candidate_image=Path(args.candidate_image) if args.candidate_image else None,
                ignore_regions=ignore_regions,
                comparable=not args.not_comparable,
            )
        else:
            raise ValueError(
                "Amazon public evidence is read from its fixed gate registry; "
                "provide explicit source/candidate images for a private companion"
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({
        "item_key": args.item,
        "captures": len(manifest["captures"]),
        "manifest": str(store.manifest_path(args.item)),
        "note": "diagnostic_metrics are viewer diagnostics, not official visual scores",
    }, indent=2))
    return 0


def _serve(args: argparse.Namespace) -> int:
    try:
        settings = AuthSettings.from_env() if args.profile == "internal" else None
        from .app import create_app
        import uvicorn

        application = create_app(
            _root(args),
            profile=args.profile,
            settings=settings,
            review_root=Path(args.reviews).resolve() if args.reviews else None,
            evidence_root=Path(args.artifacts).resolve() if args.artifacts else None,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    uvicorn.run(application, host=args.host, port=args.port, proxy_headers=True)
    return 0


def _hash_password(args: argparse.Namespace) -> int:
    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Password: ")
    confirmation = password if args.password_stdin else getpass.getpass("Confirm password: ")
    if not password or password != confirmation:
        print("passwords do not match", file=sys.stderr)
        return 2
    try:
        print(hash_password(password))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _export_reviews(args: argparse.Namespace) -> int:
    root = _root(args)
    store = ReviewStore(
        Path(args.reviews).resolve()
        if args.reviews
        else root / "artifacts" / "websitebench-viewer" / "reviews",
        root,
        allowed_keys={AMAZON_ITEM_KEY},
    )
    try:
        value = store.export(public_only=args.public_only)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _write_or_print(value, args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-viewer")
    parser.add_argument("--repo-root", default=".", help="ClawBench repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, function in (("validate", _validate), ("index", _index)):
        command = subparsers.add_parser(name)
        command.add_argument("--repo-root", default=argparse.SUPPRESS)
        command.add_argument("--profile", choices=("internal", "public"), default="internal")
        if name == "index":
            command.add_argument("--out")
        command.set_defaults(function=function)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--repo-root", default=argparse.SUPPRESS)
    capture.add_argument("--item", required=True)
    capture.add_argument("--checkpoint")
    capture.add_argument("--viewport", choices=("desktop", "mobile"))
    capture.add_argument("--source-image")
    capture.add_argument("--candidate-image")
    capture.add_argument("--ignore-regions", help="JSON array of x/y/width/height masks")
    capture.add_argument("--not-comparable", action="store_true")
    capture.add_argument("--artifacts")
    capture.set_defaults(function=_capture)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--repo-root", default=argparse.SUPPRESS)
    serve.add_argument("--profile", choices=("internal", "public"), default="internal")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--reviews")
    serve.add_argument("--artifacts")
    serve.set_defaults(function=_serve)

    password = subparsers.add_parser("hash-password")
    password.add_argument("--repo-root", default=argparse.SUPPRESS)
    password.add_argument("--password-stdin", action="store_true")
    password.set_defaults(function=_hash_password)

    export = subparsers.add_parser("export-reviews")
    export.add_argument("--repo-root", default=argparse.SUPPRESS)
    export.add_argument("--reviews")
    export.add_argument("--out")
    export.add_argument("--public-only", action="store_true")
    export.set_defaults(function=_export_reviews)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
