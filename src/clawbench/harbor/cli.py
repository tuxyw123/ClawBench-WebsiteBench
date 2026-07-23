"""Command-line interface for normalized Harbor benchmark authoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .manifest import (
    HarborManifestError,
    find_corpus_root,
    load_instance,
    load_site,
    resolve_inside,
)
from .materialize import materialize_instance
from .scaffold import initialize_instance, initialize_site


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _init_site(args: argparse.Namespace) -> int:
    manifest = initialize_site(
        args.site_dir,
        site_id=args.site_id,
        display_name=args.display_name,
    )
    _emit({"status": "initialized", "kind": "site", "manifest": str(manifest)})
    return 0


def _init_instance(args: argparse.Namespace) -> int:
    corpus_root = find_corpus_root(args.instance_dir.parent)
    load_site(
        resolve_inside(corpus_root, args.site_manifest, must_exist=True)
    )
    manifest = initialize_instance(
        args.instance_dir,
        instance_id=args.instance_id,
        site_manifest=args.site_manifest,
        author_name=args.author_name,
        author_email=args.author_email,
    )
    _emit({"status": "initialized", "kind": "instance", "manifest": str(manifest)})
    return 0


def _validate(args: argparse.Namespace) -> int:
    instance = load_instance(
        args.instance,
        corpus_root=args.corpus_root,
    )
    _emit(
        {
            "status": "valid",
            "instance_id": instance.data["instance_id"],
            "site_id": instance.site.data["site_id"],
            "instance_manifest_sha256": instance.sha256,
            "site_manifest_sha256": instance.site.sha256,
            "test_nodes": sum(len(nodes) for nodes in instance.data["tests"].values()),
        }
    )
    return 0


def _validate_corpus(args: argparse.Namespace) -> int:
    corpus_root = args.corpus_root.resolve()
    site_manifests = sorted((corpus_root / "sites").glob("*/site.yaml"))
    manifests = sorted((corpus_root / "instances").glob("*/instance.yaml"))
    if not site_manifests:
        raise HarborManifestError(
            [f"{corpus_root}: no sites/*/site.yaml manifests found"]
        )
    if not manifests:
        raise HarborManifestError(
            [f"{corpus_root}: no instances/*/instance.yaml manifests found"]
        )
    sites = [load_site(path) for path in site_manifests]
    instances = [
        load_instance(path, corpus_root=corpus_root)
        for path in manifests
    ]
    ids = [instance.data["instance_id"] for instance in instances]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise HarborManifestError(
            [f"duplicate instance_id in corpus: {item}" for item in duplicates]
        )
    site_ids = [site.data["site_id"] for site in sites]
    duplicate_sites = sorted(
        {item for item in site_ids if site_ids.count(item) > 1}
    )
    if duplicate_sites:
        raise HarborManifestError(
            [f"duplicate site_id in corpus: {item}" for item in duplicate_sites]
        )
    declared_site_paths = {site.path for site in sites}
    outside = [
        instance.site.path
        for instance in instances
        if instance.site.path not in declared_site_paths
    ]
    if outside:
        raise HarborManifestError(
            [f"instance references a non-corpus site manifest: {path}" for path in outside]
        )
    _emit(
        {
            "status": "valid",
            "instances": len(instances),
            "sites": len(sites),
        }
    )
    return 0


def _materialize(args: argparse.Namespace) -> int:
    output = materialize_instance(
        args.instance,
        args.out,
        corpus_root=args.corpus_root,
    )
    _emit(
        {
            "status": "materialized",
            "output": str(output),
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-harbor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_site = subparsers.add_parser("init-site", help="create a site authoring skeleton")
    init_site.add_argument("--site-dir", type=Path, required=True)
    init_site.add_argument("--site-id", required=True)
    init_site.add_argument("--display-name", required=True)
    init_site.set_defaults(function=_init_site)

    init_instance = subparsers.add_parser(
        "init-instance", help="create an instance authoring skeleton"
    )
    init_instance.add_argument("--instance-dir", type=Path, required=True)
    init_instance.add_argument("--instance-id", required=True)
    init_instance.add_argument(
        "--site-manifest",
        required=True,
        help="path relative to the Harbor authoring root, e.g. sites/shop/site.yaml",
    )
    init_instance.add_argument("--author-name", required=True)
    init_instance.add_argument("--author-email", required=True)
    init_instance.set_defaults(function=_init_instance)

    validate = subparsers.add_parser("validate", help="validate one instance")
    validate.add_argument("--instance", type=Path, required=True)
    validate.add_argument("--corpus-root", type=Path)
    validate.set_defaults(function=_validate)

    validate_corpus = subparsers.add_parser(
        "validate-corpus", help="validate every instance under one authoring root"
    )
    validate_corpus.add_argument("--corpus-root", type=Path, required=True)
    validate_corpus.set_defaults(function=_validate_corpus)

    materialize = subparsers.add_parser(
        "materialize", help="generate a self-contained Harbor bundle"
    )
    materialize.add_argument("--instance", type=Path, required=True)
    materialize.add_argument("--out", type=Path, required=True)
    materialize.add_argument("--corpus-root", type=Path)
    materialize.set_defaults(function=_materialize)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.function(args))
    except (
        FileExistsError,
        FileNotFoundError,
        HarborManifestError,
        OSError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
