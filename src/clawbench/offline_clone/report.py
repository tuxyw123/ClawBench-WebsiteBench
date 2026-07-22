"""Status and coverage reports for an offline-clone site."""

from __future__ import annotations

import hashlib
from typing import Any

from .assets import verify_asset_closure
from .manifest import (
    LoadedManifest,
    ManifestValidationError,
    acceptance_evidence_fingerprint,
    load_coverage_ledger,
    utc_now,
    verify_acceptance_evidence,
)
from .records import verify_trajectory_anchor
from .state import current_stage, effective_gate_statuses, load_state


def _status_report_from_snapshot(
    manifest: LoadedManifest,
    state: dict[str, Any],
    *,
    count: int,
    head: str | None,
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    statuses = effective_gate_statuses(manifest, state)
    if (
        statuses["release"]["status"] == "passed"
        and acceptance["status"] != "current"
    ):
        statuses["release"].update(
            {
                "status": "stale",
                "reason": "acceptance_evidence_changed_or_unavailable",
            }
        )
    return {
        "schema_version": "offline-clone.status.v1",
        "site_id": manifest.data["site_id"],
        "display_name": manifest.data["display_name"],
        "manifest": str(manifest.path),
        "manifest_sha256": manifest.sha256,
        "state_manifest_sha256": state.get("manifest_sha256"),
        "manifest_current": state.get("manifest_sha256") == manifest.sha256,
        "stage": current_stage(statuses),
        "gates": statuses,
        "acceptance_evidence": {
            "status": acceptance["status"],
            "artifact_count": acceptance["artifact_count"],
            "sha256": acceptance.get("sha256"),
        },
        "trajectory": {"count": count, "head_sha256": head},
    }


def status_report(manifest: LoadedManifest) -> dict[str, Any]:
    state = load_state(manifest)
    count, head = verify_trajectory_anchor(manifest, state)
    acceptance, _ = acceptance_evidence_report(manifest, state)
    return _status_report_from_snapshot(
        manifest,
        state,
        count=count,
        head=head,
        acceptance=acceptance,
    )


def acceptance_evidence_report(
    manifest: LoadedManifest, state: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return only validated, manifest/attempt-bound structured evidence."""

    state = state or load_state(manifest)
    release = state.get("gates", {}).get("release")
    if not isinstance(release, dict):
        return ({"status": "not-recorded", "artifact_count": 0, "artifacts": []}, [])
    attempt_id = release.get("acceptance_evidence_attempt_id")
    expected = release.get("acceptance_evidence_sha256")
    if not isinstance(attempt_id, str) or not isinstance(expected, str):
        return ({"status": "not-recorded", "artifact_count": 0, "artifacts": []}, [])
    try:
        evidence = verify_acceptance_evidence(
            manifest, gate_attempt_id=attempt_id
        )
    except (ManifestValidationError, OSError, ValueError):
        # Do not echo artifact contents, schema error values, paths outside the
        # declared relative names, or command output into the durable report.
        return (
            {
                "status": "invalid-or-missing",
                "artifact_count": 0,
                "artifacts": [],
            },
            [],
        )
    fingerprint = acceptance_evidence_fingerprint(evidence)
    if fingerprint != expected:
        return (
            {"status": "changed", "artifact_count": 0, "artifacts": []},
            [],
        )
    return (
        {
            "status": "current",
            "artifact_count": len(evidence),
            "sha256": fingerprint,
            "artifacts": evidence,
        },
        evidence,
    )


def coverage_report(
    manifest: LoadedManifest,
    acceptance_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Separate self-declared completion from release-evidence verification."""

    ledger = load_coverage_ledger(manifest.coverage_path)
    evidence_items: dict[str, dict[str, set[str]]] = {}
    for artifact in acceptance_evidence or []:
        for verified in artifact.get("verified_coverage", []):
            evidence_items.setdefault(artifact["kind"], {}).setdefault(
                verified["dimension_id"], set()
            ).update(verified["items"])
    dimensions: list[dict[str, Any]] = []
    for dimension in ledger["dimensions"]:
        required = dimension["required_items"]
        declared = set(dimension["satisfied_items"])
        required_kinds = list(dimension["required_evidence_kinds"])
        verified_by_kind = {
            kind: evidence_items.get(kind, {}).get(dimension["id"], set())
            for kind in required_kinds
        }
        verified = set(required)
        if required_kinds:
            for observed in verified_by_kind.values():
                verified.intersection_update(observed)
        else:
            verified.clear()
        declared_remaining = [item for item in required if item not in declared]
        evidence_remaining = [item for item in required if item not in verified]
        denominator = len(required)
        result = {
            key: dimension[key]
            for key in (
                "id",
                "label",
                "unit",
                "category",
                "required_evidence_kinds",
            )
        }
        if "rationale" in dimension:
            result["rationale"] = dimension["rationale"]
        result.update(
            {
                "required_items": list(required),
                "denominator": denominator,
                "declared_satisfied_items": list(dimension["satisfied_items"]),
                "declared_numerator": len(declared),
                "declared_remaining": len(declared_remaining),
                "declared_ratio": len(declared) / denominator if denominator else None,
                "declared_remaining_items": declared_remaining,
                "evidence_verified_items": [
                    item for item in required if item in verified
                ],
                "evidence_by_kind": {
                    kind: {
                        "verified_items": [
                            item for item in required if item in observed
                        ],
                        "numerator": len(observed),
                        "remaining": len(
                            [item for item in required if item not in observed]
                        ),
                        "ratio": len(observed) / denominator if denominator else None,
                    }
                    for kind, observed in verified_by_kind.items()
                },
                "evidence_numerator": len(verified),
                "evidence_remaining": len(evidence_remaining),
                "evidence_ratio": len(verified) / denominator if denominator else None,
                "evidence_remaining_items": evidence_remaining,
            }
        )
        dimensions.append(result)
    return {
        "schema_version": "offline-clone.coverage-report.v1",
        "status": ledger["status"],
        "ledger": {
            "path": manifest.data["scope"]["coverage"],
            "sha256": hashlib.sha256(manifest.coverage_path.read_bytes()).hexdigest(),
        },
        "dimensions": dimensions,
    }


def full_report(manifest: LoadedManifest) -> dict[str, Any]:
    state = load_state(manifest)
    count, head = verify_trajectory_anchor(manifest, state)
    acceptance, evidence = acceptance_evidence_report(manifest, state)
    status = _status_report_from_snapshot(
        manifest,
        state,
        count=count,
        head=head,
        acceptance=acceptance,
    )
    closure = verify_asset_closure(manifest)
    return {
        "schema_version": "offline-clone.report.v1",
        "generated_at": utc_now(),
        **{key: value for key, value in status.items() if key != "schema_version"},
        "source_baseline": manifest.data["source"]["baseline"],
        "runtime_remote_request_policy": manifest.data["source"]["capture_policy"][
            "runtime_remote_requests"
        ],
        "asset_closure": closure.as_dict(),
        "coverage": coverage_report(manifest, evidence),
        "acceptance_evidence": acceptance,
    }
