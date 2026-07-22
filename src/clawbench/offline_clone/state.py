"""Atomic stage state and input fingerprints for offline-clone gates."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .manifest import GATE_ORDER, LoadedManifest, load_asset_manifest, utc_now


STATE_SCHEMA_VERSION = "offline-clone.state.v1"
STAGE_LABELS = {
    None: "INIT",
    "source": "SOURCE_CAPTURED",
    "assets": "ASSETS_CLOSED",
    "frontend": "FRONTEND_READY",
    "backend": "BACKEND_READY",
    "release": "ACCEPTED",
}


class StateError(ValueError):
    pass


def initial_state(manifest: LoadedManifest) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "site_id": manifest.data["site_id"],
        "manifest_sha256": manifest.sha256,
        "created_at": now,
        "updated_at": now,
        "gates": {},
        "trajectory": {"count": 0, "head_sha256": None},
    }


def load_state(manifest: LoadedManifest) -> dict[str, Any]:
    path = manifest.state_path
    if not path.exists():
        return initial_state(manifest)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"invalid harness state: {exc}") from exc
    if not isinstance(value, dict):
        raise StateError("invalid harness state: root must be an object")
    if value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise StateError("invalid harness state schema_version")
    if value.get("site_id") != manifest.data["site_id"]:
        raise StateError("harness state belongs to another site")
    if not isinstance(value.get("gates"), dict):
        raise StateError("invalid harness state gates")
    trajectory = value.get("trajectory")
    if not isinstance(trajectory, dict):
        raise StateError("invalid harness trajectory anchor")
    return value


def write_state(manifest: LoadedManifest, state: dict[str, Any]) -> None:
    path = manifest.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # fsyncing the file before replace is insufficient on POSIX: a power
        # loss may otherwise forget the directory entry update.  Windows does
        # not allow opening directories this way, so durability there relies
        # on FlushFileBuffers above plus the atomic replace.
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            pass
        else:
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _digest_file(digest: Any, path: Path, root: Path) -> None:
    if _is_link_or_reparse(path):
        raise StateError(f"gate input must not be a link/reparse point: {path}")
    metadata = path.stat(follow_symlinks=False)
    relative = path.relative_to(root).as_posix().encode("utf-8")
    digest.update(b"F")
    digest.update(len(relative).to_bytes(4, "big"))
    digest.update(relative)
    # Content alone cannot distinguish two required copies from one hard-linked
    # physical identity. Bind identity and link count so aliasing or an atomic
    # same-byte replacement forces the semantic verifier to run again.
    for value in (
        metadata.st_size,
        getattr(metadata, "st_dev", 0),
        getattr(metadata, "st_ino", 0),
        getattr(metadata, "st_nlink", 1),
    ):
        digest.update(int(value).to_bytes(16, "big", signed=False))
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise StateError(f"cannot inspect gate input {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag) or bool(
        hasattr(path, "is_junction") and path.is_junction()
    )


def _safe_lexical_input(manifest: LoadedManifest, relative: str) -> Path:
    resolved = manifest.resolve(relative, must_exist=True)
    lexical = manifest.root
    for component in Path(relative).parts:
        lexical = lexical / component
        if _is_link_or_reparse(lexical):
            raise StateError(
                f"gate input crosses a link/reparse point: {relative}"
            )
    return resolved


_IGNORED_DIRECTORY_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
_IGNORED_DIRECTORY_SUFFIXES = frozenset({".pyc", ".pyo"})


def _directory_input_files(
    path: Path, *, excluded_paths: tuple[Path, ...] = ()
) -> list[Path]:
    output: list[Path] = []
    for child in path.rglob("*"):
        if _is_link_or_reparse(child):
            raise StateError(f"gate input directory contains a link/reparse point: {child}")
        resolved_child = child.resolve()
        if any(
            resolved_child == excluded or excluded in resolved_child.parents
            for excluded in excluded_paths
        ):
            continue
        relative_parts = child.relative_to(path).parts
        if any(part in _IGNORED_DIRECTORY_PARTS for part in relative_parts):
            continue
        if child.is_file() and child.suffix.casefold() not in _IGNORED_DIRECTORY_SUFFIXES:
            output.append(child)
    return output


def _candidate_exclusion_paths(manifest: LoadedManifest) -> tuple[Path, ...]:
    candidate_root = manifest.resolve(manifest.data["paths"]["candidate_root"])
    return tuple(
        (candidate_root / relative).resolve()
        for relative in manifest.data["paths"].get("candidate_excludes", [])
    )


def _command_file_arguments(
    manifest: LoadedManifest, definition: dict[str, Any]
) -> set[Path]:
    """Return cwd-relative regular files named directly in command argv.

    Gate adapters frequently execute a local verifier script.  Requiring every
    author to repeat that script in ``inputs`` is error-prone: the command can
    stay green after its own verifier changes.  Exact file arguments are safe
    to infer without recursively fingerprinting argument directories such as
    ``tests`` (which may contain volatile caches or runtime data).
    """

    output: set[Path] = set()
    root = manifest.root.resolve()
    markers = {
        "{site_dir}": str(root),
        "{manifest}": str(manifest.path),
        "{candidate_root}": str(
            manifest.resolve(manifest.data["paths"]["candidate_root"])
        ),
    }
    for command in definition.get("commands", []):
        if not isinstance(command, dict):
            continue
        cwd = manifest.resolve(command.get("cwd", "."), must_exist=True)
        argv = command.get("argv", [])
        if not isinstance(argv, list):
            continue

        # ``python -m local_package`` names code through import syntax rather
        # than a file path.  Fingerprint any matching site-local module/package
        # so editing the verifier cannot leave an old gate green.  Installed
        # standard/third-party modules have no site-local match and remain an
        # environment dependency.
        for index, argument in enumerate(argv[:-1]):
            if argument != "-m" or not isinstance(argv[index + 1], str):
                continue
            module_name = argv[index + 1]
            if not module_name or any(
                not component.isidentifier() for component in module_name.split(".")
            ):
                continue
            module_path = Path(*module_name.split("."))
            search_roots = {cwd, root, root / "src"}
            for search_root in search_roots:
                module_file = search_root / module_path.with_suffix(".py")
                package = search_root / module_path
                if module_file.is_file():
                    relative = module_file.relative_to(root).as_posix()
                    output.add(_safe_lexical_input(manifest, relative))
                if package.is_dir():
                    for child in _directory_input_files(package):
                        if child.is_file():
                            output.add(child)

        for index, raw_argument in enumerate(argv):
            if not isinstance(raw_argument, str) or not raw_argument:
                continue
            argument = raw_argument
            expanded = argument
            for marker, replacement in markers.items():
                expanded = expanded.replace(marker, replacement)
            if "{python}" in expanded or "{" in expanded or "}" in expanded:
                continue
            if expanded.startswith("-"):
                # Option values such as ``--config=policy.json`` are verifier
                # inputs just as much as a following positional path.  Flags
                # without an equals value are handled when their next argv
                # element is visited.
                if "=" not in expanded:
                    continue
                expanded = expanded.split("=", 1)[1]
                if not expanded:
                    continue
            raw = Path(expanded)
            lexical = raw if raw.is_absolute() else cwd / raw
            if not lexical.exists() or lexical.is_dir():
                continue
            candidate = lexical.resolve()
            if candidate != root and root not in candidate.parents:
                if index == 0:
                    # Bare PATH-resolved programs are environment dependencies.
                    # An explicit path, however, is mutable project input and
                    # must never escape the bounded site directory.  Authors
                    # can use ``{python}`` for the harness interpreter.
                    explicit_path = raw.is_absolute() or any(
                        separator in expanded for separator in ("/", "\\")
                    ) or expanded.startswith(".")
                    if not explicit_path:
                        continue
                raise StateError(
                    f"gate command file argument escapes the site: {raw_argument}"
                )
            relative = lexical.relative_to(root).as_posix()
            output.add(_safe_lexical_input(manifest, relative))
    return output


def gate_input_fingerprint(manifest: LoadedManifest, gate_name: str) -> str:
    if gate_name not in GATE_ORDER:
        raise StateError(f"unknown gate: {gate_name}")
    digest = hashlib.sha256()
    digest.update(gate_name.encode("ascii"))
    definition = manifest.data["gates"][gate_name]
    digest.update(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    paths: set[Path] = set()
    candidate_exclusions = (
        _candidate_exclusion_paths(manifest)
        if gate_name in {"backend", "release"}
        else ()
    )
    for relative in definition["inputs"]:
        path = _safe_lexical_input(manifest, relative)
        if path.is_dir():
            children = _directory_input_files(
                path, excluded_paths=candidate_exclusions
            )
            if not children:
                marker = f"EMPTY:{path.relative_to(manifest.root).as_posix()}".encode()
                digest.update(marker)
            paths.update(children)
        else:
            paths.add(path)
    paths.update(_command_file_arguments(manifest, definition))
    if gate_name == "source":
        # Every scope ledger is source-of-truth. Include all of them even if a
        # hand-written manifest accidentally omits one from the input list.
        for relative in manifest.data["scope"].values():
            paths.add(_safe_lexical_input(manifest, relative))
    if gate_name == "assets":
        paths.add(manifest.asset_manifest_path)
        asset_manifest = load_asset_manifest(manifest.asset_manifest_path)
        for asset in asset_manifest["assets"]:
            for field in ("source_path", "runtime_path"):
                candidate = manifest.resolve(asset[field])
                if candidate.exists():
                    paths.add(candidate)
                else:
                    digest.update(
                        f"MISSING:{asset[field]}".encode("utf-8", errors="strict")
                    )
    if gate_name in {"backend", "release"}:
        # A hand-maintained input list is not a production closure. Always bind
        # these late gates to every candidate file except explicit, reviewable
        # mutable-state exclusions and built-in cache products.
        candidate_root = _safe_lexical_input(
            manifest, manifest.data["paths"]["candidate_root"]
        )
        if not candidate_root.is_dir():
            raise StateError("paths.candidate_root must be a directory")
        candidate_files = _directory_input_files(
            candidate_root, excluded_paths=candidate_exclusions
        )
        digest.update(b"CANDIDATE_PRODUCTION_CLOSURE_V1")
        if not candidate_files:
            digest.update(b"EMPTY_CANDIDATE_ROOT")
        paths.update(candidate_files)
    for path in sorted(
        paths, key=lambda item: item.relative_to(manifest.root).as_posix()
    ):
        _digest_file(digest, path, manifest.root)
    return digest.hexdigest()


def effective_gate_statuses(
    manifest: LoadedManifest, state: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    manifest_matches = state.get("manifest_sha256") == manifest.sha256
    chain_open = True
    for gate_name in GATE_ORDER:
        stored = state.get("gates", {}).get(gate_name)
        status = "pending"
        reason: str | None = None
        if isinstance(stored, dict):
            status = str(stored.get("status", "pending"))
            if not manifest_matches:
                status, reason = "stale", "manifest_sha256_changed"
            elif status == "passed":
                try:
                    current_input = gate_input_fingerprint(manifest, gate_name)
                except (OSError, ValueError) as exc:
                    status, reason = "stale", f"input_unavailable:{exc}"
                else:
                    if stored.get("input_sha256") != current_input:
                        status, reason = "stale", "gate_inputs_changed"
            if not chain_open and status != "pending":
                status, reason = "stale", "prerequisite_not_current"
        if status != "passed":
            chain_open = False
        output[gate_name] = {
            "status": status,
            "reason": reason,
            "attempts": len(stored.get("attempts", []))
            if isinstance(stored, dict)
            else 0,
            "completed_at": stored.get("completed_at")
            if isinstance(stored, dict)
            else None,
        }
    return output


def current_stage(statuses: dict[str, dict[str, Any]]) -> str:
    completed: str | None = None
    for gate_name in GATE_ORDER:
        if statuses[gate_name]["status"] != "passed":
            break
        completed = gate_name
    return STAGE_LABELS[completed]


def rebase_manifest(state: dict[str, Any], manifest: LoadedManifest) -> None:
    if state.get("manifest_sha256") == manifest.sha256:
        return
    state["manifest_sha256"] = manifest.sha256
    for gate in state.get("gates", {}).values():
        if isinstance(gate, dict) and gate.get("status") in {"passed", "failed"}:
            gate["status"] = "stale"
            gate["stale_reason"] = "manifest_sha256_changed"


def invalidate_from(state: dict[str, Any], gate_name: str, reason: str) -> None:
    start = GATE_ORDER.index(gate_name)
    for name in GATE_ORDER[start:]:
        gate = state.get("gates", {}).get(name)
        if isinstance(gate, dict) and gate.get("status") != "pending":
            active_attempt_id = gate.get("active_attempt_id")
            if gate.get("status") == "running" and active_attempt_id:
                for attempt in reversed(gate.get("attempts", [])):
                    if (
                        isinstance(attempt, dict)
                        and attempt.get("attempt_id") == active_attempt_id
                        and attempt.get("status") == "running"
                    ):
                        attempt.update(
                            {
                                "status": "interrupted",
                                "completed_at": utc_now(),
                                "failure": reason,
                            }
                        )
                        break
            gate["status"] = "stale"
            gate["stale_reason"] = reason
            gate.pop("active_attempt_id", None)
