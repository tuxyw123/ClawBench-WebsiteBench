"""Fail-closed, ordered gate execution for the offline-clone lifecycle."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from typing import Any

from .assets import verify_asset_closure
from .locking import site_mutation_lock
from .manifest import (
    GATE_ORDER,
    LoadedManifest,
    ManifestValidationError,
    acceptance_evidence_path_snapshot,
    acceptance_evidence_fingerprint,
    manifest_fingerprint,
    require_frozen_coverage,
    utc_now,
    verify_acceptance_evidence,
    verify_acceptance_evidence_artifact,
)
from .records import sensitive_findings, verify_trajectory_anchor_unlocked
from .state import (
    effective_gate_statuses,
    gate_input_fingerprint,
    invalidate_from,
    load_state,
    rebase_manifest,
    write_state,
)


class GateError(RuntimeError):
    pass


def _expand_argument(argument: str, manifest: LoadedManifest) -> str:
    values = {
        "{python}": sys.executable,
        "{site_dir}": str(manifest.root),
        "{manifest}": str(manifest.path),
        "{candidate_root}": str(
            manifest.resolve(manifest.data["paths"]["candidate_root"])
        ),
    }
    expanded = argument
    for marker, value in values.items():
        expanded = expanded.replace(marker, value)
    return expanded


def _run_command(
    manifest: LoadedManifest,
    command: dict[str, Any],
    *,
    gate_name: str,
    attempt_id: str,
) -> dict[str, Any]:
    argv = [_expand_argument(value, manifest) for value in command["argv"]]
    findings = sensitive_findings(" ".join(argv))
    if findings:
        raise GateError(
            f"gate command {command['id']} contains inline sensitive content: "
            + ", ".join(findings)
        )
    cwd = manifest.resolve(command.get("cwd", "."), must_exist=True)
    if not cwd.is_dir():
        raise GateError(f"gate command cwd is not a directory: {cwd}")
    timeout = int(command.get("timeout_seconds", 600))
    environment = os.environ.copy()
    environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    environment.update(
        {
            "CLAWBENCH_OFFLINE_CLONE_GATE": gate_name,
            "CLAWBENCH_OFFLINE_CLONE_ATTEMPT_ID": attempt_id,
            "CLAWBENCH_OFFLINE_CLONE_COMMAND_ID": command["id"],
            "CLAWBENCH_OFFLINE_CLONE_MANIFEST": str(manifest.path),
            "CLAWBENCH_OFFLINE_CLONE_MANIFEST_SHA256": manifest.sha256,
            "CLAWBENCH_OFFLINE_CLONE_SITE_DIR": str(manifest.root),
        }
    )
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return_code = result.returncode
        stdout_bytes = len(result.stdout)
        stderr_bytes = len(result.stderr)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        stdout_bytes = len(exc.stdout or b"")
        stderr_bytes = len(exc.stderr or b"")
        timed_out = True
    except OSError as exc:
        return_code = 127
        stdout_bytes = 0
        stderr_bytes = len(str(exc).encode("utf-8"))
        timed_out = False
    return {
        "id": command["id"],
        "return_code": return_code,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "timed_out": timed_out,
    }


def run_gate(manifest: LoadedManifest, gate_name: str) -> dict[str, Any]:
    if gate_name not in GATE_ORDER:
        raise GateError(f"unknown gate: {gate_name}")
    definition = manifest.data["gates"][gate_name]
    if not definition["commands"]:
        raise GateError(f"{gate_name} gate must declare at least one command")
    # Phase 1 is a durable begin transaction.  The old pass and every
    # downstream pass become stale *before* any command can run.  A crash then
    # leaves an explicit running attempt, never an incorrectly current pass.
    with site_mutation_lock(manifest.state_path):
        state = load_state(manifest)
        verify_trajectory_anchor_unlocked(manifest, state)
        manifest_changed = state.get("manifest_sha256") != manifest.sha256
        if manifest_changed:
            rebase_manifest(state, manifest)
        statuses = effective_gate_statuses(manifest, state)
        gate_index = GATE_ORDER.index(gate_name)
        missing_prerequisites = [
            name
            for name in GATE_ORDER[:gate_index]
            if statuses[name]["status"] != "passed"
        ]
        if missing_prerequisites:
            raise GateError(
                f"cannot run {gate_name}; prerequisite gates are not current: "
                + ", ".join(missing_prerequisites)
            )
        if gate_name == "source":
            require_frozen_coverage(manifest)
        try:
            starting_input_sha256 = gate_input_fingerprint(manifest, gate_name)
        except (OSError, ValueError) as exc:
            raise GateError(f"could not fingerprint gate inputs: {exc}") from exc

        invalidate_from(state, gate_name, "upstream_gate_rerun")
        gate_state = state["gates"].setdefault(gate_name, {"attempts": []})
        attempts = gate_state.setdefault("attempts", [])
        attempt_id = uuid.uuid4().hex
        attempt: dict[str, Any] = {
            "attempt_id": attempt_id,
            "sequence": len(attempts) + 1,
            "started_at": utc_now(),
            "status": "running",
            "manifest_sha256": manifest.sha256,
            "input_sha256": starting_input_sha256,
            "commands": [],
            "asset_closure": None,
            "acceptance_evidence": None,
            "acceptance_evidence_sha256": None,
            "failure": None,
        }
        attempts.append(attempt)
        gate_state.update(
            {
                "status": "running",
                "active_attempt_id": attempt_id,
                "started_at": attempt["started_at"],
                "manifest_sha256": manifest.sha256,
                "input_sha256": starting_input_sha256,
            }
        )
        gate_state.pop("completed_at", None)
        gate_state.pop("stale_reason", None)
        write_state(manifest, state)

    failure: str | None = None
    for command in definition["commands"]:
        before_evidence: dict[str, str | None] | None = None
        if gate_name == "release":
            try:
                before_evidence = acceptance_evidence_path_snapshot(manifest)
            except (ManifestValidationError, OSError, ValueError) as exc:
                failure = f"cannot snapshot release evidence before {command['id']}: {exc}"
                break
        try:
            result = _run_command(
                manifest,
                command,
                gate_name=gate_name,
                attempt_id=attempt_id,
            )
        except GateError as exc:
            failure = str(exc)
            break
        attempt["commands"].append(result)
        if result["return_code"] != 0:
            failure = f"command {result['id']} exited {result['return_code']}"
            break
        if gate_name == "release" and before_evidence is not None:
            try:
                after_evidence = acceptance_evidence_path_snapshot(manifest)
            except (ManifestValidationError, OSError, ValueError) as exc:
                failure = f"cannot snapshot release evidence after {command['id']}: {exc}"
                break
            assigned = sorted(
                kind
                for kind, declaration in definition["evidence"].items()
                if declaration["producer_command_id"] == command["id"]
            )
            changed = {
                kind
                for kind in before_evidence
                if before_evidence[kind] != after_evidence[kind]
            }
            missing_changes = sorted(set(assigned) - changed)
            unexpected_changes = sorted(changed - set(assigned))
            result["produced_evidence_kinds"] = assigned
            if missing_changes:
                failure = (
                    f"release command {command['id']} did not create or change its assigned "
                    "evidence: " + ", ".join(missing_changes)
                )
                break
            if unexpected_changes:
                failure = (
                    f"release command {command['id']} changed evidence assigned to another "
                    "producer: " + ", ".join(unexpected_changes)
                )
                break
            try:
                for kind in assigned:
                    produced = verify_acceptance_evidence_artifact(
                        manifest, kind=kind, gate_attempt_id=attempt_id
                    )
                    if produced["sha256"] != after_evidence[kind]:
                        raise GateError(
                            f"release evidence {kind} changed during immediate validation"
                        )
            except (GateError, ManifestValidationError, OSError, ValueError) as exc:
                failure = (
                    f"release command {command['id']} produced invalid evidence: {exc}"
                )
                break
    if failure is None and gate_name == "assets":
        closure = verify_asset_closure(manifest)
        attempt["asset_closure"] = closure.as_dict()
        if not closure.passed:
            failure = "required asset closure is incomplete"
    if failure is None and gate_name == "release":
        try:
            evidence = verify_acceptance_evidence(
                manifest, gate_attempt_id=attempt_id
            )
        except (ManifestValidationError, OSError, ValueError) as exc:
            failure = f"release acceptance evidence is invalid: {exc}"
        else:
            attempt["acceptance_evidence"] = evidence
            attempt["acceptance_evidence_sha256"] = acceptance_evidence_fingerprint(
                evidence
            )

    # Phase 2 reloads state and uses the attempt id as a compare-and-swap
    # token.  An upstream rerun can therefore invalidate this attempt while its
    # slow command is executing, and this completion cannot resurrect it.
    with site_mutation_lock(manifest.state_path):
        current_state = load_state(manifest)
        verify_trajectory_anchor_unlocked(manifest, current_state)
        current_gate = current_state.get("gates", {}).get(gate_name)
        if not isinstance(current_gate, dict):  # pragma: no cover - corrupt state guard
            raise GateError(f"running {gate_name} attempt disappeared from state")
        stored_attempt = next(
            (
                item
                for item in current_gate.get("attempts", [])
                if isinstance(item, dict) and item.get("attempt_id") == attempt_id
            ),
            None,
        )
        if stored_attempt is None:  # pragma: no cover - corrupt state guard
            raise GateError(f"running {gate_name} attempt disappeared from history")

        completed_at = utc_now()
        stored_attempt.update(
            {
                "commands": attempt["commands"],
                "asset_closure": attempt["asset_closure"],
                "acceptance_evidence": attempt["acceptance_evidence"],
                "acceptance_evidence_sha256": attempt[
                    "acceptance_evidence_sha256"
                ],
                "completed_at": completed_at,
            }
        )
        result_status = "failed" if failure else "passed"
        stale_reason: str | None = None
        if (
            current_gate.get("status") != "running"
            or current_gate.get("active_attempt_id") != attempt_id
        ):
            stale_reason = "gate_attempt_was_superseded"
        elif current_state.get("manifest_sha256") != manifest.sha256:
            stale_reason = "manifest_changed_while_gate_was_running"
        else:
            try:
                live_manifest_sha256 = manifest_fingerprint(manifest.path)
            except OSError:
                stale_reason = "manifest_unavailable_after_gate_run"
            else:
                if live_manifest_sha256 != manifest.sha256:
                    stale_reason = "manifest_changed_while_gate_was_running"
            try:
                completed_input_sha256 = gate_input_fingerprint(manifest, gate_name)
            except (OSError, ValueError):
                completed_input_sha256 = None
                stale_reason = stale_reason or "gate_inputs_unavailable_after_run"
            else:
                if completed_input_sha256 != starting_input_sha256:
                    stale_reason = "gate_inputs_changed_while_gate_was_running"

            if stale_reason is None:
                current_statuses = effective_gate_statuses(manifest, current_state)
                missing_after_run = [
                    name
                    for name in GATE_ORDER[:gate_index]
                    if current_statuses[name]["status"] != "passed"
                ]
                if missing_after_run:
                    stale_reason = "prerequisite_changed_while_gate_was_running"

            if stale_reason is None and failure is None and gate_name == "release":
                try:
                    latest_evidence = verify_acceptance_evidence(
                        manifest, gate_attempt_id=attempt_id
                    )
                    latest_evidence_sha256 = acceptance_evidence_fingerprint(
                        latest_evidence
                    )
                except (ManifestValidationError, OSError, ValueError) as exc:
                    failure = (
                        "release acceptance evidence changed before commit: "
                        f"{exc}"
                    )
                    result_status = "failed"
                else:
                    if latest_evidence_sha256 != attempt[
                        "acceptance_evidence_sha256"
                    ]:
                        failure = "release acceptance evidence changed before commit"
                        result_status = "failed"

        if stale_reason is not None:
            result_status = "stale"
            failure = stale_reason
            stored_attempt.update({"status": "superseded", "failure": stale_reason})
            # Only mutate the gate summary if this is still its active attempt;
            # otherwise preserve the newer process's running/current result.
            if current_gate.get("active_attempt_id") == attempt_id:
                current_gate.update(
                    {
                        "status": "stale",
                        "stale_reason": stale_reason,
                        "completed_at": completed_at,
                    }
                )
                current_gate.pop("active_attempt_id", None)
        else:
            stored_attempt.update({"status": result_status, "failure": failure})
            current_gate.update(
                {
                    "status": result_status,
                    "completed_at": completed_at,
                    "manifest_sha256": manifest.sha256,
                    "input_sha256": starting_input_sha256,
                }
            )
            if gate_name == "release":
                current_gate["acceptance_evidence"] = attempt[
                    "acceptance_evidence"
                ]
                current_gate["acceptance_evidence_sha256"] = attempt[
                    "acceptance_evidence_sha256"
                ]
                current_gate["acceptance_evidence_attempt_id"] = attempt_id
            current_gate.pop("active_attempt_id", None)
            current_gate.pop("stale_reason", None)
        write_state(manifest, current_state)

    return {
        "site_id": manifest.data["site_id"],
        "gate": gate_name,
        "status": result_status,
        "failure": failure,
        "attempt": attempt["sequence"],
        "commands": attempt["commands"],
        "asset_closure": attempt["asset_closure"],
        "acceptance_evidence": attempt["acceptance_evidence"],
        "acceptance_evidence_sha256": attempt["acceptance_evidence_sha256"],
        "manifest_changed": manifest_changed,
    }
