"""Build portable, integrity-checked offline trajectory bundles.

Two evidence classes are deliberately distinct:

* a Web2Code run is a live capture assembled from recorded Agent, browser,
  human-intervention, build, candidate, and evaluation streams;
* a historical clone is a retrospective capture assembled from an authored
  ``CODEX_TRAJECTORY.md`` and its checked-in artifacts.

The latter is useful provenance, but must never be presented as a raw model
conversation.  The bundle manifest preserves that distinction.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from jsonschema import Draft202012Validator, FormatChecker


TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".verification-artifacts",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
SKIP_NAMES = {
    ".env",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.env",
    "human-interventions.lock",
}
SKIP_SUFFIXES = {
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".tar",
    ".tgz",
    ".zip",
}
SENSITIVE_KEYS = {
    "admin_token",
    "api_key",
    "authorization",
    "builder_token",
    "cookie",
    "credential",
    "delivery_token",
    "gateway_token",
    "openai_api_key",
    "password",
    "password_hash",
    "refresh_token",
    "secret",
    "session_secret",
    "session_token",
    "set_cookie",
    "token",
}
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|rk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
ENV_SECRET_PATTERN = re.compile(
    r"(?im)\b([A-Z][A-Z0-9_]*(?:API_KEY|PASSWORD|TOKEN|SECRET))\s*=\s*"
    r"(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s#]+)"
)
HEADING_PATTERN = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


class TrajectoryError(ValueError):
    """Raised when a source or exported bundle violates the protocol."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(value: Any, *, limit: int = 96) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).casefold())
    result = "-".join(part for part in normalized.split("-") if part)[:limit]
    return result or "trajectory"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_json_bytes(value))


def _inside(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise TrajectoryError(f"path escapes source root: {path}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return _inside(root, path).relative_to(root.resolve()).as_posix()


def _schema_directory() -> Path:
    source_root = Path(__file__).resolve().parents[3]
    source_schemas = source_root / "websitebench" / "schemas"
    if source_schemas.is_dir():
        return source_schemas
    installed = Path(__file__).resolve().parents[1] / "viewer" / "_schemas"
    if installed.is_dir():
        return installed
    raise TrajectoryError("WebsiteBench schemas are unavailable")


def _validate_json(value: Any, schema_name: str) -> None:
    schema = json.loads((_schema_directory() / schema_name).read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in errors[:12]
        )
        raise TrajectoryError(f"{schema_name} validation failed: {details}")


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
    if normalized in SENSITIVE_KEYS:
        return True
    return normalized.endswith(
        (
            "_api_key",
            "_authorization",
            "_cookie",
            "_credential",
            "_password",
            "_secret",
            "_token",
        )
    )


def redact_text(text: str, roots: Iterable[Path] = ()) -> tuple[str, int]:
    """Redact credential-shaped values and machine-specific source roots."""

    redactions = 0
    result = text
    for root in sorted({str(path.resolve()) for path in roots}, key=len, reverse=True):
        if root and root in result:
            occurrences = result.count(root)
            result = result.replace(root, "<workspace>")
            redactions += occurrences
    for pattern in SECRET_PATTERNS:
        result, count = pattern.subn("<redacted:credential>", result)
        redactions += count

    def replace_env(match: re.Match[str]) -> str:
        return f"{match.group(1)}=<redacted:credential>"

    result, count = ENV_SECRET_PATTERN.subn(replace_env, result)
    redactions += count
    return result, redactions


def redact_value(value: Any, roots: Iterable[Path] = ()) -> tuple[Any, int]:
    """Recursively redact structured event payloads without dropping shape."""

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        count = 0
        for key, child in value.items():
            if _is_sensitive_key(key) and child not in (None, "", 0, False):
                output[str(key)] = "<redacted:credential>"
                count += 1
            else:
                output[str(key)], child_count = redact_value(child, roots)
                count += child_count
        return output, count
    if isinstance(value, list):
        output_list = []
        count = 0
        for child in value:
            sanitized, child_count = redact_value(child, roots)
            output_list.append(sanitized)
            count += child_count
        return output_list, count
    if isinstance(value, str):
        return redact_text(value, roots)
    return value, 0


def _secret_findings(text: str) -> list[str]:
    findings = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(pattern.pattern)
    if any(
        match.group(2).strip("\"'") != "<redacted:credential>"
        for match in ENV_SECRET_PATTERN.finditer(text)
    ):
        findings.append("credential environment assignment")
    return findings


def _media_type(path: Path) -> str:
    if path.suffix.casefold() == ".md":
        return "text/markdown"
    if path.suffix.casefold() == ".jsonl":
        return "application/x-ndjson"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _git_state(root: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"commit": None, "dirty": None}
    value = commit.stdout.strip()
    return {
        "commit": value
        if commit.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value)
        else None,
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
    }


def _absolute_path(path: Path) -> Path:
    """Return a lexical absolute path without following its final symlink."""

    return path.expanduser().absolute()


def _is_forbidden_file(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name in SKIP_NAMES
        or name.startswith(".env.")
        or name.endswith(".env")
        or path.suffix.casefold() in SKIP_SUFFIXES
        or name.endswith((".tar.gz", ".tar.zst"))
    )


def _prepare_output(output: Path, *, overwrite: bool) -> Path:
    output = _absolute_path(output)
    if output.is_symlink():
        raise TrajectoryError(f"refusing to use symlink output: {output}")
    resolved_output = output.resolve()
    if output.exists():
        if not overwrite:
            raise TrajectoryError(f"output already exists: {output}")
        if not output.is_dir():
            raise TrajectoryError(f"refusing to replace non-directory output: {output}")
        if (
            resolved_output == Path(resolved_output.anchor)
            or len(resolved_output.parts) < 3
        ):
            raise TrajectoryError(f"refusing to replace broad output path: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True, mode=0o755)
    return output.resolve()


class BundleWriter:
    def __init__(
        self, output: Path, *, source_roots: Iterable[Path], overwrite: bool
    ) -> None:
        self.output = _prepare_output(output, overwrite=overwrite)
        self.source_roots = tuple(path.resolve() for path in source_roots)
        self.artifacts: list[dict[str, Any]] = []

    def _destination(self, relative: str) -> Path:
        if not relative.startswith("files/") or ".." in Path(relative).parts:
            raise TrajectoryError(f"invalid bundle artifact path: {relative}")
        destination = self.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def add_bytes(
        self,
        data: bytes,
        relative: str,
        *,
        role: str,
        media_type: str,
        origin: str | None,
        redactions: int = 0,
    ) -> str:
        destination = self._destination(relative)
        destination.write_bytes(data)
        self.artifacts.append(
            {
                "path": relative,
                "role": role,
                "media_type": media_type,
                "bytes": len(data),
                "sha256": _sha256_bytes(data),
                "origin": origin,
                "redactions": redactions,
            }
        )
        return relative

    def add_json(
        self, value: Any, relative: str, *, role: str, origin: str | None
    ) -> str:
        sanitized, redactions = redact_value(value, self.source_roots)
        return self.add_bytes(
            _json_bytes(sanitized),
            relative,
            role=role,
            media_type="application/json",
            origin=origin,
            redactions=redactions,
        )

    def add_file(
        self,
        source: Path,
        relative: str,
        *,
        role: str,
        origin: str | None,
        sanitize_text: bool = True,
    ) -> str:
        if source.is_symlink() or not source.is_file():
            raise TrajectoryError(f"artifact must be a regular file: {source}")
        data = source.read_bytes()
        redactions = 0
        if sanitize_text:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                text, redactions = redact_text(text, self.source_roots)
                data = text.encode("utf-8")
        return self.add_bytes(
            data,
            relative,
            role=role,
            media_type=_media_type(source),
            origin=origin,
            redactions=redactions,
        )

    def write_events(
        self, episode_id: str, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        normalized = []
        redactions = 0
        for sequence, event in enumerate(events, 1):
            event = dict(event)
            event.update(
                {
                    "schema_version": "websitebench.trajectory-event.v1",
                    "episode_id": episode_id,
                    "event_id": f"{episode_id}:{sequence:06d}",
                    "sequence": sequence,
                }
            )
            event, count = redact_value(event, self.source_roots)
            redactions += count
            _validate_json(event, "trajectory-event.schema.json")
            normalized.append(event)
        data = b"".join(
            (json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n").encode(
                "utf-8"
            )
            for event in normalized
        )
        path = self.output / "events.jsonl"
        path.write_bytes(data)
        self.artifacts.append(
            {
                "path": "events.jsonl",
                "role": "normalized-events",
                "media_type": "application/x-ndjson",
                "bytes": len(data),
                "sha256": _sha256_bytes(data),
                "origin": None,
                "redactions": redactions,
            }
        )
        return normalized

    def finish(self, manifest: dict[str, Any]) -> dict[str, Any]:
        manifest = {
            **manifest,
            "artifacts": sorted(self.artifacts, key=lambda item: item["path"]),
        }
        manifest, _ = redact_value(manifest, self.source_roots)
        _validate_json(manifest, "trajectory-bundle.schema.json")
        _write_json(self.output / "manifest.json", manifest)
        checksum_lines = [
            f"{artifact['sha256']}  {artifact['path']}\n"
            for artifact in manifest["artifacts"]
        ]
        (self.output / "SHA256SUMS").write_text(
            "".join(checksum_lines), encoding="utf-8"
        )
        validate_bundle(self.output)
        return manifest


def _event(
    *,
    timestamp: str | None,
    actor: str,
    kind: str,
    phase: str,
    summary: str,
    payload: dict[str, Any],
    artifact_refs: Iterable[str],
    capture: str,
    stream: str,
    source_sequence: int,
    source_path: str | None,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "actor": actor,
        "kind": kind,
        "phase": phase,
        "summary": summary[:1000] or kind,
        "payload": payload,
        "artifact_refs": sorted(set(artifact_refs)),
        "source": {
            "capture": capture,
            "stream": stream,
            "source_sequence": source_sequence,
            "source_path": source_path,
        },
    }


def _timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        try:
            return (
                datetime.fromtimestamp(value, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def _read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        return
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = {"raw": line, "parse_error": True}
        if not isinstance(value, dict):
            value = {"value": value}
        yield number, value


def _markdown_sections(text: str) -> list[tuple[str, str, int]]:
    sections: list[tuple[str, str, int]] = []
    heading: str | None = None
    start_line = 0
    body: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = HEADING_PATTERN.match(line)
        if match:
            if heading is not None and "\n".join(body).strip():
                sections.append((heading, "\n".join(body).strip(), start_line))
            heading = match.group(2).strip()
            start_line = line_number
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None and "\n".join(body).strip():
        sections.append((heading, "\n".join(body).strip(), start_line))
    return sections


def _retrospective_classification(heading: str) -> tuple[str, str]:
    value = heading.casefold()
    if any(token in value for token in ("source", "baseline", "capture", "provenance")):
        return "source-observation", "observation"
    if any(
        token in value
        for token in ("frontend", "backend", "sqlite", "implementation", "expansion")
    ):
        return "implementation", "code_change"
    if any(
        token in value
        for token in ("verify", "verification", "review", "gate", "difference")
    ):
        return "verification", "test"
    if "decision" in value:
        return "handoff", "decision"
    if any(token in value for token in ("audit", "index", "checkpoint")):
        return "handoff", "checkpoint"
    return "implementation", "message"


def _iter_snapshot_files(root: Path, *, include_observations: bool) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if not include_observations and "source-fixtures" in relative.parts:
            continue
        if _is_forbidden_file(path):
            continue
        if path.stat().st_size > 50 * 1024 * 1024:
            continue
        yield path


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return {
        "task_id": task.get("task_id")
        or metadata.get("task_id")
        or task.get("site_id")
        or "unknown",
        "site_id": task.get("site_id") or metadata.get("platform"),
        "site_version": task.get("site_version"),
        "track": task.get("track") or task.get("split"),
        "instruction": task.get("instruction"),
        "task_artifact": "files/task/task.json",
    }


def _artifact_role(relative: Path) -> str:
    if "source-fixtures" in relative.parts:
        return "source-observation"
    if relative.name.startswith("test_") or "tests" in relative.parts:
        return "candidate-test"
    if "tools" in relative.parts:
        return "trajectory-tool"
    if relative.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        return "candidate-asset"
    if relative.suffix.casefold() in TEXT_SUFFIXES:
        return "candidate-source"
    return "candidate-artifact"


def _archive_bundle(bundle: Path) -> Path:
    archive = bundle.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(bundle, arcname=bundle.name, recursive=True)
    return archive


def _check_export_targets(
    source_roots: Iterable[Path], output: Path, *, archive: bool, overwrite: bool
) -> None:
    output_path = _absolute_path(output)
    if output_path.is_symlink():
        raise TrajectoryError(f"refusing to use symlink output: {output_path}")
    resolved_output = output_path.resolve()
    for source_root in source_roots:
        resolved_source = source_root.resolve()
        if (
            resolved_output == resolved_source
            or resolved_source in resolved_output.parents
            or resolved_output in resolved_source.parents
        ):
            raise TrajectoryError("output must not overlap the exported source tree")
    if not archive:
        return
    archive_path = output_path.with_suffix(".tar.gz")
    if archive_path.exists() and not overwrite:
        raise TrajectoryError(f"archive already exists: {archive_path}")
    if archive_path.is_symlink() or (
        archive_path.exists() and not archive_path.is_file()
    ):
        raise TrajectoryError(
            f"refusing to replace unsafe archive target: {archive_path}"
        )


def export_clone_history(
    *,
    repository_root: Path,
    clone_dir: Path,
    task_path: Path,
    output: Path,
    include_code: bool = True,
    include_observations: bool = False,
    archive: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export a checked-in clone history as a curated retrospective bundle."""

    repository_root = repository_root.resolve()
    clone_dir = _inside(repository_root, clone_dir)
    task_path = _inside(repository_root, task_path)
    trajectory_path = clone_dir / "CODEX_TRAJECTORY.md"
    if not trajectory_path.is_file():
        raise TrajectoryError(f"missing retrospective trajectory: {trajectory_path}")
    _check_export_targets(
        (clone_dir, task_path), output, archive=archive, overwrite=overwrite
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(task, dict):
        raise TrajectoryError("task must contain a JSON object")
    identity = _task_summary(task)
    digest = hashlib.sha256(
        task_path.read_bytes() + trajectory_path.read_bytes()
    ).hexdigest()
    episode_id = _slug(f"clone-{identity['task_id']}-{digest[:12]}")
    writer = BundleWriter(output, source_roots=(repository_root,), overwrite=overwrite)
    task_ref = writer.add_json(
        task,
        "files/task/task.json",
        role="task-contract",
        origin=_relative(repository_root, task_path),
    )
    trajectory_ref = writer.add_file(
        trajectory_path,
        "files/trajectory/CODEX_TRAJECTORY.md",
        role="retrospective-trajectory",
        origin=_relative(repository_root, trajectory_path),
    )
    copied_sources = {task_path.resolve(), trajectory_path.resolve()}
    for name in (
        "README.md",
        "SOURCE_EVIDENCE.md",
        "ASSET_ATTRIBUTION.md",
        "LIMITATIONS.md",
        "VERIFICATION.md",
        "verification-report.json",
    ):
        source = clone_dir / name
        if source.is_file():
            writer.add_file(
                source,
                f"files/context/{name}",
                role="verification"
                if "VERIFICATION" in name.upper() or name.endswith("report.json")
                else "provenance",
                origin=_relative(repository_root, source),
            )
            copied_sources.add(source.resolve())
    for source in sorted(clone_dir.glob("phase*.json")):
        writer.add_file(
            source,
            f"files/context/{source.name}",
            role="verification-contract",
            origin=_relative(repository_root, source),
        )
        copied_sources.add(source.resolve())
    if include_code:
        for source in _iter_snapshot_files(
            clone_dir, include_observations=include_observations
        ):
            if source.resolve() in copied_sources:
                continue
            relative = source.relative_to(clone_dir)
            writer.add_file(
                source,
                f"files/candidate/{relative.as_posix()}",
                role=_artifact_role(relative),
                origin=_relative(repository_root, source),
            )
    events = []
    trajectory_text = trajectory_path.read_text(encoding="utf-8")
    for source_sequence, (heading, content, line_number) in enumerate(
        _markdown_sections(trajectory_text), 1
    ):
        phase, kind = _retrospective_classification(heading)
        events.append(
            _event(
                timestamp=None,
                actor="human-agent",
                kind=kind,
                phase=phase,
                summary=heading,
                payload={"content": content, "source_line": line_number},
                artifact_refs=(trajectory_ref,),
                capture="retrospective",
                stream="curated-markdown",
                source_sequence=source_sequence,
                source_path="files/trajectory/CODEX_TRAJECTORY.md",
            )
        )
    report_path = clone_dir / "verification-report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        events.append(
            _event(
                timestamp=None,
                actor="evaluator",
                kind="evaluation",
                phase="evaluation",
                summary="Checked-in clone verification result",
                payload={"summary": summary},
                artifact_refs=("files/context/verification-report.json",),
                capture="imported",
                stream="verification-report",
                source_sequence=1,
                source_path="files/context/verification-report.json",
            )
        )
    normalized = writer.write_events(episode_id, events)
    counts = Counter(event["kind"] for event in normalized)
    excluded = [
        "credentials and environment files",
        "runtime databases, caches, and temporary verification artifacts",
        "raw model conversation, shell transcript, and code diffs because they were not retained",
    ]
    if not include_observations:
        excluded.append(
            "source-fixtures observations (enable explicitly after redistribution review)"
        )
    if not include_code:
        excluded.append("final candidate source snapshot (disabled by export option)")
    manifest = writer.finish(
        {
            "schema_version": "websitebench.trajectory-bundle.v1",
            "bundle_id": episode_id,
            "episode_id": episode_id,
            "created_at": utc_now(),
            "capture": {
                "mode": "retrospective",
                "completeness": "curated",
                "source_kind": "clone-history",
                "ordering": "per-stream",
            },
            "task": {**identity, "task_artifact": task_ref},
            "repository": _git_state(repository_root),
            "actors": sorted({event["actor"] for event in normalized}),
            "event_count": len(normalized),
            "event_counts": dict(sorted(counts.items())),
            "integrity": {"algorithm": "sha256", "checksum_file": "SHA256SUMS"},
            "safety": {"secret_scan": "passed", "excluded": excluded},
            "limitations": [
                "Narrative sections are retrospective human/Agent provenance, not raw turn-level messages.",
                "Section order is preserved, but individual actions do not have reliable timestamps.",
                "The final code snapshot does not reconstruct intermediate diffs that were not retained.",
            ],
        }
    )
    archive_path = _archive_bundle(writer.output) if archive else None
    return {
        "bundle": str(writer.output),
        "archive": str(archive_path) if archive_path else None,
        "manifest": manifest,
    }


def _copy_tree(
    writer: BundleWriter,
    *,
    source_root: Path,
    destination_root: str,
    role: str,
    origin_root: Path,
    include_observations: bool = True,
) -> list[str]:
    refs = []
    if not source_root.is_dir():
        return refs
    for source in _iter_snapshot_files(
        source_root, include_observations=include_observations
    ):
        relative = source.relative_to(source_root)
        refs.append(
            writer.add_file(
                source,
                f"{destination_root}/{relative.as_posix()}",
                role=role if role != "candidate" else _artifact_role(relative),
                origin=_relative(origin_root, source),
            )
        )
    return refs


def export_web2code_run(
    *,
    repository_root: Path,
    run_dir: Path,
    output: Path,
    include_code: bool = True,
    archive: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Normalize one Web2Code run directory into a safe offline bundle."""

    repository_root = repository_root.resolve()
    run_dir = run_dir.resolve()
    task_path = run_dir / "task.json"
    metadata_path = run_dir / "run-meta.json"
    if not task_path.is_file() or not metadata_path.is_file():
        raise TrajectoryError("run directory must contain task.json and run-meta.json")
    _check_export_targets((run_dir,), output, archive=archive, overwrite=overwrite)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(task, dict) or not isinstance(metadata, dict):
        raise TrajectoryError("run task and metadata must contain JSON objects")
    episode_id = _slug(metadata.get("run_id") or task.get("run_id") or run_dir.name)
    writer = BundleWriter(
        output,
        source_roots=(repository_root, run_dir),
        overwrite=overwrite,
    )
    task_ref = writer.add_json(
        task, "files/task/task.json", role="task-contract", origin="task.json"
    )
    writer.add_json(
        metadata,
        "files/context/run-meta.json",
        role="run-metadata",
        origin="run-meta.json",
    )
    _copy_tree(
        writer,
        source_root=run_dir / "public",
        destination_root="files/task/public",
        role="public-contract",
        origin_root=run_dir,
    )
    _copy_tree(
        writer,
        source_root=run_dir / "schemas",
        destination_root="files/task/schemas",
        role="protocol-schema",
        origin_root=run_dir,
    )
    candidate_root = run_dir / "candidate"
    candidate_available = candidate_root.is_dir() and any(
        _iter_snapshot_files(candidate_root, include_observations=True)
    )
    candidate_refs = []
    if include_code:
        candidate_refs = _copy_tree(
            writer,
            source_root=candidate_root,
            destination_root="files/candidate",
            role="candidate",
            origin_root=run_dir,
        )
    screenshot_refs = _copy_tree(
        writer,
        source_root=run_dir / "browser" / "screenshots",
        destination_root="files/browser/screenshots",
        role="browser-screenshot",
        origin_root=run_dir,
    )
    build_refs = _copy_tree(
        writer,
        source_root=run_dir / "builds",
        destination_root="files/builds",
        role="build-artifact",
        origin_root=run_dir,
    )
    evaluation_refs = []
    for name in ("evaluation-result.json", "failure-report.md", "source-policy.json"):
        source = run_dir / "eval" / name
        if source.is_file():
            evaluation_refs.append(
                writer.add_file(
                    source,
                    f"files/evaluation/{name}",
                    role="evaluation-result",
                    origin=f"eval/{name}",
                )
            )
    events = [
        _event(
            timestamp=_timestamp(metadata.get("created_at") or task.get("issued_at")),
            actor="system",
            kind="checkpoint",
            phase="intake",
            summary="Web2Code run started",
            payload={"run": metadata},
            artifact_refs=("files/context/run-meta.json", task_ref),
            capture="imported",
            stream="run-metadata",
            source_sequence=1,
            source_path="files/context/run-meta.json",
        )
    ]
    agent_path = run_dir / "agent" / "agent-messages.jsonl"
    for source_sequence, record in _read_jsonl(agent_path):
        label = str(record.get("type") or record.get("event") or "agent message")
        events.append(
            _event(
                timestamp=_timestamp(
                    record.get("timestamp") or record.get("created_at")
                ),
                actor="agent",
                kind="message",
                phase="implementation",
                summary=label,
                payload={"record": record},
                artifact_refs=(),
                capture="recorded",
                stream="agent-messages",
                source_sequence=source_sequence,
                source_path="agent/agent-messages.jsonl",
            )
        )
    browser_path = run_dir / "browser" / "actions.jsonl"
    for source_sequence, record in _read_jsonl(browser_path):
        target = str(record.get("target") or "")
        phase = (
            "source-observation"
            if target in {"reference", "mailbox"}
            else "implementation"
        )
        refs = []
        if str(record.get("action")) == "screenshot":
            try:
                screenshot_sequence = int(record.get("sequence", source_sequence))
            except (TypeError, ValueError):
                screenshot_sequence = source_sequence
            prefix = f"files/browser/screenshots/{screenshot_sequence:04d}-"
            refs = [path for path in screenshot_refs if path.startswith(prefix)]
        events.append(
            _event(
                timestamp=_timestamp(record.get("timestamp")),
                actor="tool",
                kind="browser_action",
                phase=phase,
                summary=f"{target or 'browser'}: {record.get('action', 'action')}",
                payload={"record": record},
                artifact_refs=refs,
                capture="recorded",
                stream="browser-actions",
                source_sequence=source_sequence,
                source_path="browser/actions.jsonl",
            )
        )
    human_path = run_dir / "human-interventions.jsonl"
    for source_sequence, record in _read_jsonl(human_path):
        events.append(
            _event(
                timestamp=_timestamp(record.get("timestamp")),
                actor="human",
                kind="intervention",
                phase="implementation",
                summary=str(record.get("category") or "human intervention"),
                payload={"record": record},
                artifact_refs=(),
                capture="recorded",
                stream="human-interventions",
                source_sequence=source_sequence,
                source_path="human-interventions.jsonl",
            )
        )
    for source_sequence, reference in enumerate(build_refs, 1):
        events.append(
            _event(
                timestamp=None,
                actor="tool",
                kind="build",
                phase="implementation",
                summary=f"Candidate build artifact: {Path(reference).name}",
                payload={},
                artifact_refs=(reference,),
                capture="imported",
                stream="build-artifacts",
                source_sequence=source_sequence,
                source_path=reference,
            )
        )
    if candidate_refs:
        events.append(
            _event(
                timestamp=None,
                actor="system",
                kind="artifact",
                phase="handoff",
                summary="Final candidate source snapshot",
                payload={"file_count": len(candidate_refs)},
                artifact_refs=candidate_refs,
                capture="imported",
                stream="candidate-snapshot",
                source_sequence=1,
                source_path="files/candidate",
            )
        )
    result_path = run_dir / "eval" / "evaluation-result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        events.append(
            _event(
                timestamp=_timestamp(result.get("finished_at"))
                if isinstance(result, dict)
                else None,
                actor="evaluator",
                kind="evaluation",
                phase="evaluation",
                summary="WebsiteBench evaluation result",
                payload={"result": result},
                artifact_refs=evaluation_refs,
                capture="imported",
                stream="evaluation",
                source_sequence=1,
                source_path="files/evaluation/evaluation-result.json",
            )
        )
    normalized = writer.write_events(episode_id, events)
    counts = Counter(event["kind"] for event in normalized)
    missing_streams = []
    for label, path in (
        ("agent messages", agent_path),
        ("browser actions", browser_path),
        ("human interventions", human_path),
    ):
        if not path.is_file() or path.stat().st_size == 0:
            missing_streams.append(label)
    if not candidate_available:
        missing_streams.append("final candidate")
    if not result_path.is_file():
        missing_streams.append("evaluation result")
    completeness = "normalized" if not missing_streams else "partial"
    limitations = [
        "Each stream preserves its own order; recorded timestamps are the only cross-stream temporal evidence.",
        "Private fixtures, evaluator facts, credentials, runtime databases, and image archives are excluded.",
    ]
    if not include_code:
        limitations.append(
            "The final candidate source snapshot was intentionally omitted by the export option."
        )
    if missing_streams:
        limitations.append(
            "Missing or empty source streams: " + ", ".join(missing_streams) + "."
        )
    manifest = writer.finish(
        {
            "schema_version": "websitebench.trajectory-bundle.v1",
            "bundle_id": episode_id,
            "episode_id": episode_id,
            "created_at": utc_now(),
            "capture": {
                "mode": "live",
                "completeness": completeness,
                "source_kind": "web2code-run",
                "ordering": "per-stream",
            },
            "task": {**_task_summary(task), "task_artifact": task_ref},
            "repository": _git_state(repository_root),
            "actors": sorted({event["actor"] for event in normalized}),
            "event_count": len(normalized),
            "event_counts": dict(sorted(counts.items())),
            "integrity": {"algorithm": "sha256", "checksum_file": "SHA256SUMS"},
            "safety": {
                "secret_scan": "passed",
                "excluded": [
                    "secrets.env and credential-bearing environment files",
                    "private reference source and hidden judge fixtures",
                    "eval/facts.json and other private evaluator internals",
                    "runtime databases, container image archives, and caches",
                ],
            },
            "limitations": limitations,
        }
    )
    archive_path = _archive_bundle(writer.output) if archive else None
    return {
        "bundle": str(writer.output),
        "archive": str(archive_path) if archive_path else None,
        "manifest": manifest,
    }


def validate_bundle(bundle: Path) -> dict[str, Any]:
    """Validate schemas, event references, hashes, and credential hygiene."""

    bundle = bundle.resolve()
    manifest_path = bundle / "manifest.json"
    events_path = bundle / "events.jsonl"
    checksums_path = bundle / "SHA256SUMS"
    if (
        not manifest_path.is_file()
        or not events_path.is_file()
        or not checksums_path.is_file()
    ):
        raise TrajectoryError(
            "bundle must contain manifest.json, events.jsonl, and SHA256SUMS"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_json(manifest, "trajectory-bundle.schema.json")
    manifest_findings = _secret_findings(manifest_path.read_text(encoding="utf-8"))
    if manifest_findings:
        raise TrajectoryError(
            f"credential-shaped content remains in manifest.json: {manifest_findings[0]}"
        )
    artifacts = {artifact["path"]: artifact for artifact in manifest["artifacts"]}
    if len(artifacts) != len(manifest["artifacts"]):
        raise TrajectoryError("manifest contains duplicate artifact paths")
    expected_lines = []
    for relative, artifact in sorted(artifacts.items()):
        path = _inside(bundle, bundle / relative)
        if path.is_symlink() or not path.is_file():
            raise TrajectoryError(f"missing or unsafe artifact: {relative}")
        digest = _sha256_file(path)
        if digest != artifact["sha256"] or path.stat().st_size != artifact["bytes"]:
            raise TrajectoryError(f"artifact integrity mismatch: {relative}")
        expected_lines.append(f"{digest}  {relative}\n")
        try:
            artifact_text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            artifact_text = ""
        if artifact_text:
            findings = _secret_findings(artifact_text)
            if findings:
                raise TrajectoryError(
                    f"credential-shaped content remains in {relative}: {findings[0]}"
                )
    if checksums_path.read_text(encoding="utf-8") != "".join(expected_lines):
        raise TrajectoryError("SHA256SUMS does not match the manifest")
    events = []
    for number, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrajectoryError(
                f"events.jsonl line {number} is invalid: {exc.msg}"
            ) from exc
        _validate_json(event, "trajectory-event.schema.json")
        if event["episode_id"] != manifest["episode_id"] or event["sequence"] != number:
            raise TrajectoryError(f"event identity/order mismatch at line {number}")
        for reference in event["artifact_refs"]:
            if reference not in artifacts:
                raise TrajectoryError(
                    f"event references undeclared artifact: {reference}"
                )
        events.append(event)
    if not events or len(events) != manifest["event_count"]:
        raise TrajectoryError("manifest event_count does not match events.jsonl")
    counts = dict(sorted(Counter(event["kind"] for event in events).items()))
    if counts != manifest["event_counts"]:
        raise TrajectoryError("manifest event_counts does not match events.jsonl")
    declared_files = {"manifest.json", "SHA256SUMS", *artifacts}
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise TrajectoryError(
                f"bundle contains a symlink: {path.relative_to(bundle)}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(bundle).as_posix()
        if _is_forbidden_file(path):
            raise TrajectoryError(
                f"bundle contains a forbidden file: {path.relative_to(bundle)}"
            )
        if relative not in declared_files:
            raise TrajectoryError(f"bundle contains an undeclared file: {relative}")
    return {
        "status": "valid",
        "bundle_id": manifest["bundle_id"],
        "capture": manifest["capture"],
        "events": len(events),
        "artifacts": len(artifacts),
    }
