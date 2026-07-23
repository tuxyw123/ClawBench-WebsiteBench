"""Materialize one authoring instance into a self-contained Harbor bundle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from .manifest import LoadedInstance, load_instance, safe_regular_file, safe_tree_files


TEMPLATE_PACKAGE = "clawbench.harbor.templates"
COMMON_TEMPLATES = (
    "environment/Dockerfile",
    "tests/Dockerfile",
    "tests/test.sh",
    "tests/browser_lib.py",
    "tests/service_lib.py",
    "tests/compute_reward.py",
    "tests/merge_ctrf.py",
    "tests/verifier_contract.json",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _copy_regular(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    shutil.copymode(source, destination)


def _copy_tree(
    source_root: Path,
    relative: str,
    destination: Path,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source, child_relative in safe_tree_files(source_root, relative):
        _copy_regular(source, destination / child_relative)


def _copy_template(relative: str, destination: Path) -> None:
    resource = files(TEMPLATE_PACKAGE).joinpath(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with resource.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    if relative.endswith(".sh"):
        destination.chmod(0o755)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _task_toml(instance: LoadedInstance) -> str:
    metadata = instance.data["metadata"]
    budgets = instance.data["budgets"]
    tags = metadata.get("tags", [])
    keywords = ", ".join(_toml_string(item) for item in tags)
    task_name = f"clawbench/{instance.data['instance_id']}"
    return (
        'schema_version = "1.4"\n'
        'artifacts = ["/app/repo"]\n\n'
        "[task]\n"
        f"name = {_toml_string(task_name)}\n"
        'version = "1.0.0"\n'
        f"description = {_toml_string('Reconstruct the browser-only offline site ' + instance.site.data['display_name'])}\n"
        f"authors = [{{ name = {_toml_string(metadata['author_name'])}, "
        f"email = {_toml_string(metadata['author_email'])} }}]\n"
        f"keywords = [{keywords}]\n\n"
        "[metadata]\n"
        f"difficulty = {_toml_string(metadata['difficulty'])}\n"
        'category = "web-development"\n'
        'task_type = "fullstack-reconstruction"\n'
        'language = "web"\n\n'
        "[verifier]\n"
        'environment_mode = "separate"\n'
        f"timeout_sec = {float(budgets['verifier_timeout_sec']):.1f}\n\n"
        "[verifier.environment]\n"
        'network_mode = "no-network"\n'
        f"cpus = {budgets['cpus']}\n"
        f"memory_mb = {budgets['memory_mb']}\n"
        f"storage_mb = {budgets['storage_mb']}\n\n"
        "[agent]\n"
        f"timeout_sec = {float(budgets['agent_timeout_sec']):.1f}\n\n"
        "[environment]\n"
        'network_mode = "allowlist"\n'
        'allowed_hosts = ["reference"]\n'
        'os = "linux"\n'
        f"build_timeout_sec = {float(budgets['build_timeout_sec']):.1f}\n"
        f"cpus = {budgets['cpus']}\n"
        f"memory_mb = {budgets['memory_mb']}\n"
        f"storage_mb = {budgets['storage_mb']}\n"
    )


def _classification(relative: str) -> str:
    if relative.startswith("environment/reference/"):
        return "reference-sidecar-only"
    if relative.startswith("environment/seed/"):
        return "agent-public"
    if relative.startswith("environment/"):
        return "build-control"
    if relative.startswith("solution/"):
        return "oracle-only"
    if relative.startswith("tests/") or relative.startswith("authoring/"):
        return "verifier-only"
    if relative in {"instruction.md", "task.toml"}:
        return "agent-public"
    return "bundle-metadata"


def _bundle_manifest(root: Path, instance: LoadedInstance) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "bundle-manifest.json":
            continue
        payload = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "visibility": _classification(relative),
            }
        )
    return {
        "schema_version": "clawbench.harbor.bundle.v1",
        "instance_id": instance.data["instance_id"],
        "site_id": instance.site.data["site_id"],
        "authoring": {
            "instance_manifest_sha256": instance.sha256,
            "site_manifest_sha256": instance.site.sha256,
        },
        "files": entries,
    }


def _required_nodes(instance: LoadedInstance) -> dict[str, Any]:
    groups = instance.data["tests"]
    return {
        "schema_version": "clawbench.harbor.required-nodes.v1",
        "exact_node_set": True,
        "groups": groups,
        "nodes": [node for group in groups.values() for node in group],
        "dimension_max_points": instance.site.data["scoring"]["dimensions"],
    }


def _runtime_contract(instance: LoadedInstance) -> dict[str, Any]:
    return {
        "schema_version": "clawbench.harbor.runtime-contract.v1",
        "site_id": instance.site.data["site_id"],
        "instance_id": instance.data["instance_id"],
        "runtime": instance.site.data["runtime"],
        "paths": {
            "candidate_root": "/app/repo",
            "reference_root": "/tests/reference",
            "output_root": "/run/verifier-final",
        },
        "rules": {
            "agent_reference_access": "browser-only",
            "agent_exploration_driver": "browser-use-cli",
            "formal_ui_driver": "playwright",
            "formal_api_checks": "direct-http",
            "reference_source_in_agent_image": False,
            "candidate_and_reference_reset_before_scenario": True,
            "verifier_launches_fresh_reference_and_candidate": True,
            "live_reference_and_untrusted_candidate_must_not_overlap": True,
        },
    }


def _agent_contract(instance: LoadedInstance) -> dict[str, Any]:
    runtime = instance.site.data["runtime"]
    return {
        "schema_version": "clawbench.harbor.agent-browser-contract.v1",
        "site_id": instance.site.data["site_id"],
        "instance_id": instance.data["instance_id"],
        "reference_access": "browser-only",
        "exploration_driver": "browser-use-cli",
        "reference_url_env": runtime["reference_url_env"],
        "candidate_url_env": runtime["candidate_url_env"],
        "candidate_port": runtime["candidate_port"],
        "candidate_ready_path": runtime["ready_path"],
        "candidate_start": "/app/repo/run.sh",
        "candidate_data_dir_env": "CLAWBENCH_DATA_DIR",
        "rules": [
            "Do not attempt to locate or read the reference implementation.",
            "Use Browser Use CLI to inspect behavior and rendered states.",
            "Implement the candidate in /app/repo and self-check through the browser.",
            "Formal scoring is performed by trusted Playwright and direct HTTP checks.",
        ],
    }


def _docker_compose(instance: LoadedInstance) -> dict[str, Any]:
    runtime = instance.site.data["runtime"]
    return {
        "services": {
            "main": {
                "depends_on": {
                    "reference": {
                        "condition": "service_healthy",
                    }
                },
                "environment": {
                    runtime["reference_url_env"]: (
                        f"http://reference:{runtime['reference_port']}"
                    ),
                    runtime["candidate_url_env"]: (
                        f"http://127.0.0.1:{runtime['candidate_port']}"
                    ),
                },
            },
            "reference": {
                "build": {
                    "context": "./reference",
                },
                "environment": {
                    "PORT": str(runtime["reference_port"]),
                },
                "expose": [str(runtime["reference_port"])],
            },
        }
    }


def _populate(root: Path, instance: LoadedInstance) -> None:
    for relative in COMMON_TEMPLATES:
        _copy_template(relative, root / relative)

    site_paths = instance.site.data["paths"]
    instance_paths = instance.data["paths"]
    _copy_tree(
        instance.site.root,
        site_paths["public"],
        root / "environment/seed/.clawbench/site",
    )
    _copy_tree(
        instance.root,
        instance_paths["public"],
        root / "environment/seed",
    )
    _copy_tree(
        instance.site.root,
        site_paths["reference"],
        root / "environment/reference",
    )
    _copy_tree(
        instance.site.root,
        site_paths["reference"],
        root / "tests/reference",
    )
    (root / "environment/docker-compose.yaml").write_text(
        yaml.safe_dump(_docker_compose(instance), sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    _copy_tree(instance.site.root, site_paths["verifier"], root / "tests/site")
    _copy_tree(instance.root, instance_paths["verifier"], root / "tests/instance")
    _copy_tree(
        instance.site.root,
        site_paths["hidden_fixtures"],
        root / "tests/fixtures/site",
    )
    _copy_tree(
        instance.root,
        instance_paths["hidden_fixtures"],
        root / "tests/fixtures/instance",
    )
    oracle = site_paths.get("oracle")
    if oracle:
        _copy_tree(instance.site.root, oracle, root / "solution/site")
    _copy_regular(
        safe_regular_file(instance.root, instance_paths["oracle_solution"]),
        root / "solution/solve.sh",
    )
    (root / "solution/solve.sh").chmod(0o755)

    _copy_regular(
        safe_regular_file(instance.root, instance_paths["instruction"]),
        root / "instruction.md",
    )
    (root / "task.toml").write_text(
        _task_toml(instance), encoding="utf-8", newline="\n"
    )
    _write_json(root / "tests/required-nodes.json", _required_nodes(instance))
    _write_json(root / "tests/runtime-contract.json", _runtime_contract(instance))
    _write_json(
        root / "environment/seed/.clawbench/browser-contract.json",
        _agent_contract(instance),
    )
    _write_json(root / "authoring/site.normalized.json", instance.site.data)
    _write_json(root / "authoring/instance.normalized.json", instance.data)

    _write_json(
        root / "bundle-manifest.json",
        _bundle_manifest(root, instance),
    )


def materialize_instance(
    instance_path: Path | str,
    destination: Path | str,
    *,
    corpus_root: Path | None = None,
) -> Path:
    """Create one bundle atomically and never overwrite an existing destination."""

    instance = load_instance(instance_path, corpus_root=corpus_root)
    output = Path(destination).resolve()
    if output.exists():
        raise FileExistsError(f"destination already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent))
    ).resolve()
    try:
        _populate(temporary, instance)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output
