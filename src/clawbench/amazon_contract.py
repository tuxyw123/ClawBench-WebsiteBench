"""Canonical runtime contract for the Amazon WebsiteBench calibration site.

The task, Viewer clone gateway, verification tools, and documentation all refer
to one checked-in manifest instead of carrying independent clone paths, ports,
and commands.  The same manifest defines the files covered by runtime
attestation so a historical validation report cannot silently approve changed
clone code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


AMAZON_ITEM_KEY = "benchmark--amazon"
AMAZON_SITE_ID = "amazon"
AMAZON_RUNTIME_MANIFEST = Path("materials/amazon/runtime-manifest.json")


class AmazonContractError(ValueError):
    """The canonical Amazon manifest is missing, unsafe, or inconsistent."""


def _inside_repo(repo_root: Path, relative: str, *, kind: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AmazonContractError(
            f"{kind} must be a non-empty repository-relative path"
        )
    root = repo_root.resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise AmazonContractError(f"{kind} escapes the repository: {relative}")
    return path


def load_amazon_runtime_contract(repo_root: Path) -> dict[str, Any]:
    """Read and minimally validate the single Amazon runtime manifest."""

    root = repo_root.resolve()
    path = root / AMAZON_RUNTIME_MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AmazonContractError(
            f"cannot read {AMAZON_RUNTIME_MANIFEST}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise AmazonContractError(
            f"invalid {AMAZON_RUNTIME_MANIFEST} at line {exc.lineno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise AmazonContractError("Amazon runtime manifest must be a JSON object")
    expected = {
        "schema_version": "websitebench.amazon-runtime.v1",
        "item_key": AMAZON_ITEM_KEY,
        "site_id": AMAZON_SITE_ID,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise AmazonContractError(f"{key} must equal {expected_value!r}")
    runtime = value.get("runtime")
    if not isinstance(runtime, dict):
        raise AmazonContractError("runtime must be an object")
    for key in (
        "task_path",
        "clone_root",
        "entrypoint",
        "server_command",
        "verify_command",
        "local_url",
        "container_url",
        "viewer_path",
    ):
        if not isinstance(runtime.get(key), str) or not runtime[key]:
            raise AmazonContractError(f"runtime.{key} must be a non-empty string")
    for key in ("task_path", "clone_root", "entrypoint"):
        _inside_repo(root, runtime[key], kind=f"runtime.{key}")
    if (
        not isinstance(runtime.get("canonical_port"), int)
        or not 1 <= runtime["canonical_port"] <= 65535
    ):
        raise AmazonContractError("runtime.canonical_port must be a valid TCP port")
    if runtime["viewer_path"] != f"/clone/{AMAZON_ITEM_KEY}/":
        raise AmazonContractError(
            "runtime.viewer_path must use the canonical Amazon item key"
        )
    attestation = value.get("attestation")
    if not isinstance(attestation, dict):
        raise AmazonContractError("attestation must be an object")
    for key in ("files", "trees"):
        rows = attestation.get(key)
        if not isinstance(rows, list) or not all(
            isinstance(row, str) and row for row in rows
        ):
            raise AmazonContractError(f"attestation.{key} must be a list of paths")
        for row in rows:
            _inside_repo(root, row, kind=f"attestation.{key}")
    return value


def amazon_runtime_paths(
    repo_root: Path, manifest: dict[str, Any] | None = None
) -> list[Path]:
    """Resolve the exact, deterministic file set covered by attestation."""

    root = repo_root.resolve()
    value = manifest or load_amazon_runtime_contract(root)
    paths: set[Path] = {root / AMAZON_RUNTIME_MANIFEST}
    attestation = value["attestation"]
    for relative in attestation["files"]:
        path = _inside_repo(root, relative, kind="attestation.files")
        if not path.is_file() or path.is_symlink():
            raise AmazonContractError(
                f"attested file is missing or symbolic: {relative}"
            )
        paths.add(path)
    for relative in attestation["trees"]:
        tree = _inside_repo(root, relative, kind="attestation.trees")
        if not tree.is_dir() or tree.is_symlink():
            raise AmazonContractError(
                f"attested tree is missing or symbolic: {relative}"
            )
        for path in tree.rglob("*"):
            if path.is_symlink():
                raise AmazonContractError(
                    f"symbolic path is forbidden in attested tree: {path.relative_to(root)}"
                )
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            ):
                paths.add(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def amazon_runtime_fingerprint(
    repo_root: Path, manifest: dict[str, Any] | None = None
) -> str:
    """Hash attested path names and bytes using one repository-wide algorithm."""

    root = repo_root.resolve()
    digest = hashlib.sha256()
    for path in amazon_runtime_paths(root, manifest):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def reported_runtime_fingerprints(
    reports: Iterable[dict[str, Any]],
) -> list[str | None]:
    """Read the normalized hash from clone and gate report shapes."""

    values: list[str | None] = []
    for report in reports:
        contract = (
            report.get("contract") if isinstance(report.get("contract"), dict) else {}
        )
        value = (
            report.get("runtimeStructuralSha256")
            or contract.get("runtime_structural_sha256")
            or contract.get("structural_sha256")
        )
        values.append(value if isinstance(value, str) else None)
    return values
