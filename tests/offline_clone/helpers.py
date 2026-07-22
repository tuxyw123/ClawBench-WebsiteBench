from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from PIL import Image

from clawbench.offline_clone.manifest import initialize_site, load_manifest
from clawbench.offline_clone.state import initial_state, write_state


def initialized_site(tmp_path: Path) -> Path:
    root = tmp_path / "example-site"
    manifest = initialize_site(
        root,
        site_id="example-shop",
        display_name="Example Shop",
        source_url="https://example.test/",
    )
    write_state(manifest, initial_state(manifest))
    return root


def configure_passing_gates(root: Path) -> None:
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    passing = [{"id": "pass", "argv": ["{python}", "-c", "raise SystemExit(0)"]}]
    for name in ("source", "assets", "frontend", "backend"):
        value["gates"][name]["commands"] = passing
    release_command_id = "emit-release-evidence"
    audit_command_id = "emit-independent-audit"
    value["gates"]["release"]["commands"] = [
        {
            "id": release_command_id,
            "argv": ["{python}", "clone/emit_acceptance.py", "regular"],
        },
        {
            "id": audit_command_id,
            "argv": ["{python}", "clone/emit_acceptance.py", "independent-audit"],
        },
    ]
    for kind, declaration in value["gates"]["release"]["evidence"].items():
        declaration["producer_command_id"] = (
            audit_command_id if kind == "independent-audit" else release_command_id
        )
    source_checkpoint_relative = "source-assets/checkpoints/home.default.png"
    value["gates"]["source"]["inputs"].append(source_checkpoint_relative)
    source_checkpoint = root / source_checkpoint_relative
    source_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1, 1), "white").save(source_checkpoint)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    coverage = {
        "schema_version": "offline-clone.coverage.v1",
        "status": "frozen",
        "dimensions": [
            {
                "id": "reachable",
                "label": "Reachable states",
                "unit": "route-state",
                "category": "reachability",
                "required_evidence_kinds": ["visual"],
                "required_items": ["home.default"],
                "satisfied_items": [],
            }
        ],
    }
    (root / "scope/coverage.json").write_text(
        json.dumps(coverage, indent=2) + "\n", encoding="utf-8"
    )
    (root / "scope/purpose.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.purpose.v1",
                "status": "frozen",
                "purpose_id": "primary-purpose",
                "statement": "Let a visitor complete the representative mainline journey.",
                "primary_actor_ids": ["visitor"],
                "mainline_journey_ids": ["home-mainline"],
                "out_of_scope": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "scope/invariants.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.invariants.v1",
                "status": "frozen",
                "invariants": [
                    {
                        "id": "mainline-reachable",
                        "statement": "The representative mainline remains reachable.",
                        "priority": "p0",
                        "journey_ids": ["home-mainline"],
                        "positive_test_refs": ["test.example"],
                        "negative_test_refs": ["test.failure.example"],
                        "coverage_dimension_ids": ["reachable"],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "scope/journeys.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.journeys.v1",
                "journeys": [
                    {
                        "id": "home-mainline",
                        "kind": "success",
                        "priority": "p0",
                        "status": "frozen",
                        "actor": "visitor",
                        "steps": ["open the representative home state"],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    source_payload = source_checkpoint.read_bytes()
    (root / "scope/checkpoints.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.checkpoints.v1",
                "status": "frozen",
                "viewports": {},
                "checkpoints": [
                    {
                        "id": "home.default",
                        "visual_contract": {
                            "source_artifact_path": source_checkpoint_relative,
                            "source_artifact_sha256": hashlib.sha256(
                                source_payload
                            ).hexdigest(),
                            "viewport": {"width": 1, "height": 1},
                            "comparison_region": {
                                "x": 0,
                                "y": 0,
                                "width": 1,
                                "height": 1,
                            },
                            "metric": "pixel-mae-similarity-v1",
                            "threshold": 0.9,
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "clone/emit_acceptance.py").write_text(
        """from __future__ import annotations

import json
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

root = Path(os.environ["CLAWBENCH_OFFLINE_CLONE_SITE_DIR"])
manifest_path = Path(os.environ["CLAWBENCH_OFFLINE_CLONE_MANIFEST"])
manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
coverage_path = root / manifest["scope"]["coverage"]
coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
checkpoint_path = root / manifest["scope"]["checkpoints"]
checkpoint_scope = json.loads(checkpoint_path.read_text(encoding="utf-8"))
frozen_visual = checkpoint_scope["checkpoints"][0]["visual_contract"]
source_screenshot_payload = (root / frozen_visual["source_artifact_path"]).read_bytes()
verified = [
    {"dimension_id": dimension["id"], "items": dimension["required_items"]}
    for dimension in coverage["dimensions"]
    if dimension["required_items"] and "visual" in dimension["required_evidence_kinds"]
]
mode = sys.argv[1]
roles = {
    "visual": ["source-screenshot", "clone-screenshot", "visual-diff"],
    "browser": ["browser-trace"],
    "network": ["network-log"],
    "migration": ["pre-state", "post-state", "migration-log"],
    "independent-audit": ["audit-report"],
    "full-suite": ["test-result"],
}
specific_metrics = {
    "visual": {"checkpoints_total": 1, "checkpoints_passed": 1, "checkpoints_failed": 0},
    "browser": {"journeys_total": 1, "journeys_passed": 1, "journeys_failed": 0},
    "network": {"requests_total": 1, "forbidden_remote_requests": 0, "network_failures": 0},
    "migration": {
        "stateful": True,
        "migration_scenarios_total": 1,
        "migration_scenarios_passed": 1,
        "migration_scenarios_failed": 0,
        "copies_tested": 1,
        "schema_checks": 1,
        "data_checks": 1,
    },
    "independent-audit": {
        "findings_total": 0,
        "blocking_findings": 0,
        "reviewer_method": "separate-release-command",
        "independence_boundary": "distinct-command-id-and-argv",
    },
    "full-suite": {"tests_discovered": 2, "tests_passed": 2, "tests_failed": 0},
}
for kind, declaration in manifest["gates"]["release"]["evidence"].items():
    if (mode == "independent-audit") != (kind == "independent-audit"):
        continue
    status = "passed"
    metrics = {"checks_total": 1, "checks_passed": 1, "checks_failed": 0}
    metrics.update(specific_metrics[kind])
    kind_roles = roles[kind]
    reason = None
    if kind == "migration" and manifest["state_model"] == "stateless":
        status = "not_applicable"
        metrics = {
            "checks_total": 0,
            "checks_passed": 0,
            "checks_failed": 0,
            "stateful": False,
        }
        kind_roles = ["state-inventory"]
        reason = "The frozen manifest declares a stateless clone."
    raw_artifacts = []
    for role in kind_roles:
        if role in {"source-screenshot", "clone-screenshot"}:
            raw_payload = source_screenshot_payload
            media_type = "image/png"
            suffix = "png"
            subject_ids = ["home.default"]
        else:
            if role == "visual-diff":
                document = {
                    "schema_version": "offline-clone.raw.visual-diff.v1",
                    "subject_ids": ["home.default"],
                    "checkpoints": [{
                        "id": "home.default",
                        "source_artifact_sha256": hashlib.sha256(source_screenshot_payload).hexdigest(),
                        "clone_artifact_sha256": hashlib.sha256(source_screenshot_payload).hexdigest(),
                        "score": 1.0,
                        "threshold": frozen_visual["threshold"],
                        "passed": True,
                        "metric": frozen_visual["metric"],
                        "viewport": frozen_visual["viewport"],
                        "comparison_region": frozen_visual["comparison_region"],
                    }],
                }
                subject_ids = ["home.default"]
            elif role == "browser-trace":
                document = {
                    "schema_version": "offline-clone.raw.browser-trace.v1",
                    "subject_ids": ["home-mainline"],
                    "journeys": [{
                        "id": "home-mainline",
                        "status": "passed",
                        "steps_total": 1,
                        "steps_passed": 1,
                    }],
                }
                subject_ids = ["home-mainline"]
            elif role == "network-log":
                document = {
                    "schema_version": "offline-clone.raw.network-log.v1",
                    "subject_ids": ["runtime-offline"],
                    "requests": [{
                        "url": "http://127.0.0.1/",
                        "remote": False,
                        "failed": False,
                        "status": 200,
                        "subject_ids": ["runtime-offline"],
                    }],
                }
                subject_ids = ["runtime-offline"]
            elif role in {"pre-state", "post-state", "state-inventory"}:
                document = {
                    "schema_version": "offline-clone.raw.state-inventory.v1",
                    "subject_ids": ["migration-copy"],
                    "state_model": "stateless" if role == "state-inventory" else "stateful",
                    "persistence_surfaces": [] if role == "state-inventory" else ["fixture-db"],
                    "schema_fingerprint": "0" * 64,
                    "row_counts": {} if role == "state-inventory" else {"fixture": 1},
                }
                subject_ids = ["migration-copy"]
            elif role == "migration-log":
                document = {
                    "schema_version": "offline-clone.raw.migration-log.v1",
                    "subject_ids": ["migration-copy"],
                    "scenarios": [{
                        "id": "migration-copy",
                        "status": "passed",
                        "copies_tested": 1,
                        "schema_checks": 1,
                        "data_checks": 1,
                    }],
                }
                subject_ids = ["migration-copy"]
            elif role == "audit-report":
                document = {
                    "schema_version": "offline-clone.raw.audit-report.v1",
                    "subject_ids": ["release-audit"],
                    "reviewer_method": "separate-release-command",
                    "independence_boundary": "distinct-command-id-and-argv",
                    "checks": [{
                        "id": "audit-release",
                        "status": "passed",
                        "subject_ids": ["release-audit"],
                    }],
                    "findings": [],
                }
                subject_ids = ["release-audit"]
            else:
                document = {
                    "schema_version": "offline-clone.raw.test-result.v1",
                    "subject_ids": ["home-mainline"],
                    "tests": [
                        {
                            "id": "test.example",
                            "status": "passed",
                            "subject_ids": ["home-mainline"],
                        },
                        {
                            "id": "test.failure.example",
                            "status": "passed",
                            "subject_ids": ["home-mainline"],
                        },
                    ],
                }
                subject_ids = ["home-mainline"]
            raw_payload = (json.dumps(document) + "\\n").encode("utf-8")
            media_type = "application/json"
            suffix = "json"
        raw_path = f"artifacts/offline-clone/acceptance/raw/{kind}-{role}.{suffix}"
        raw_destination = root / raw_path
        raw_destination.parent.mkdir(parents=True, exist_ok=True)
        raw_destination.write_bytes(raw_payload)
        raw_artifacts.append({
            "path": raw_path,
            "sha256": hashlib.sha256(raw_payload).hexdigest(),
            "bytes": len(raw_payload),
            "media_type": media_type,
            "role": role,
            "subject_ids": subject_ids,
            "contains_user_data": False,
            "sanitization_method": "synthetic .test fixture without user data",
        })
    artifact = {
        "schema_version": "offline-clone.acceptance-evidence.v1",
        "kind": kind,
        "producer_command_id": declaration["producer_command_id"],
        "gate_attempt_id": os.environ["CLAWBENCH_OFFLINE_CLONE_ATTEMPT_ID"],
        "manifest_sha256": os.environ["CLAWBENCH_OFFLINE_CLONE_MANIFEST_SHA256"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "summary": f"The {kind} acceptance check passed.",
        "metrics": metrics,
        "boundaries": [],
        "verified_coverage": verified if kind == "visual" else [],
        "raw_artifacts": raw_artifacts,
    }
    if reason:
        artifact["reason"] = reason
    if kind == "independent-audit":
        artifact["reviewer_method"] = metrics["reviewer_method"]
        artifact["independence_boundary"] = metrics["independence_boundary"]
    destination = root / declaration["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(artifact, indent=2) + "\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )


def add_closed_png_asset(root: Path) -> None:
    source = root / "source-assets/images/logo.png"
    runtime = root / "clone/static/assets/logo.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 6), "#ff9900").save(source)
    runtime.write_bytes(source.read_bytes())
    data = source.read_bytes()
    manifest = {
        "schema_version": "offline-clone.assets.v1",
        "snapshot_id": "example-shop-20260722",
        "created_at": "2026-07-22T00:00:00Z",
        "remote_runtime_policy": "forbidden",
        "closure_status": "declared",
        "no_assets_reason": None,
        "assets": [
            {
                "id": "logo",
                "priority": "p0",
                "required": True,
                "source_path": "source-assets/images/logo.png",
                "runtime_path": "clone/static/assets/logo.png",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mime_type": "image/png",
                "dimensions": {"width": 8, "height": 6},
                "referenced_by": ["route:home/header"],
                "evidence_kind": "current-direct",
                "source_url": "https://example.test/logo.png",
                "capture_id": "home-desktop",
            }
        ],
    }
    (root / "source-assets/manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    load_manifest(root)
