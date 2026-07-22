from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest
import yaml

import clawbench.offline_clone.gates as gate_module
from clawbench.offline_clone.gates import run_gate
from clawbench.offline_clone.manifest import (
    ManifestValidationError,
    _validated_image_size,
    load_manifest,
    verify_acceptance_evidence,
)
from clawbench.offline_clone.report import full_report, status_report
from clawbench.offline_clone.state import load_state

from .helpers import add_closed_png_asset, configure_passing_gates, initialized_site


def _accepted_site(
    tmp_path: Path, *, state_model: str = "stateful"
) -> tuple[Path, str]:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    if state_model != "stateful":
        manifest_path = root / "clone.yaml"
        value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        value["state_model"] = state_model
        manifest_path.write_text(
            yaml.safe_dump(value, sort_keys=False), encoding="utf-8"
        )
    add_closed_png_asset(root)
    for gate in ("source", "assets", "frontend", "backend", "release"):
        result = run_gate(load_manifest(root), gate)
        assert result["status"] == "passed", result
    manifest = load_manifest(root)
    attempt_id = load_state(manifest)["gates"]["release"][
        "acceptance_evidence_attempt_id"
    ]
    return root, attempt_id


def _artifact(root: Path, kind: str) -> Path:
    manifest = load_manifest(root)
    return manifest.resolve(manifest.data["gates"]["release"]["evidence"][kind]["path"])


def _rewrite_json_raw(
    root: Path, kind: str, role: str, mutation: object
) -> None:
    summary_path = _artifact(root, kind)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    declaration = next(
        item for item in summary["raw_artifacts"] if item["role"] == role
    )
    raw_path = load_manifest(root).resolve(declaration["path"])
    document = json.loads(raw_path.read_text(encoding="utf-8"))
    mutation(document)  # type: ignore[operator]
    payload = (json.dumps(document) + "\n").encode("utf-8")
    raw_path.write_bytes(payload)
    import hashlib

    declaration["sha256"] = hashlib.sha256(payload).hexdigest()
    declaration["bytes"] = len(payload)
    declaration["subject_ids"] = document["subject_ids"]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")


def test_exit_zero_release_without_structured_evidence_cannot_accept(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    manifest_path = root / "clone.yaml"
    value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    value["gates"]["release"]["commands"] = [
        {"id": "pass", "argv": ["{python}", "-c", "raise SystemExit(0)"]},
        {
            "id": "pass-independent-audit",
            "argv": ["{python}", "-c", "raise SystemExit(0)", "audit"],
        },
    ]
    for kind, declaration in value["gates"]["release"]["evidence"].items():
        declaration["producer_command_id"] = (
            "pass-independent-audit" if kind == "independent-audit" else "pass"
        )
    manifest_path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    for gate in ("source", "assets", "frontend", "backend"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"
    result = run_gate(load_manifest(root), "release")
    assert result["status"] == "failed"
    assert "assigned evidence" in result["failure"]
    assert status_report(load_manifest(root))["stage"] == "BACKEND_READY"


def test_successful_release_embeds_only_bound_structured_evidence(
    tmp_path: Path,
) -> None:
    root, _ = _accepted_site(tmp_path)
    report = full_report(load_manifest(root))

    assert report["stage"] == "ACCEPTED"
    assert report["acceptance_evidence"]["status"] == "current"
    assert report["acceptance_evidence"]["artifact_count"] == 6
    assert len(report["acceptance_evidence"]["sha256"]) == 64
    assert {item["kind"] for item in report["acceptance_evidence"]["artifacts"]} == {
        "visual",
        "browser",
        "network",
        "migration",
        "independent-audit",
        "full-suite",
    }
    for item in report["acceptance_evidence"]["artifacts"]:
        assert {
            "kind",
            "path",
            "sha256",
            "producer_command_id",
            "gate_attempt_id",
            "status",
            "summary",
            "metrics",
            "boundaries",
            "verified_coverage",
            "raw_artifacts",
        }.issubset(item)
        if item["kind"] == "independent-audit":
            assert item["reviewer_method"] == "separate-release-command"
            assert item["independence_boundary"] == "distinct-command-id-and-argv"
        assert item["raw_artifacts"]
        assert "stdout" not in item
        assert not Path(item["path"]).is_absolute()
    dimension = report["coverage"]["dimensions"][0]
    assert dimension["declared_ratio"] == 0.0
    assert dimension["evidence_ratio"] == 1.0
    assert dimension["evidence_verified_items"] == ["home.default"]


@pytest.mark.parametrize(
    "kind, mutation, message",
    [
        (
            "visual",
            lambda value: value.update(kind="browser"),
            "expected 'visual'",
        ),
        (
            "browser",
            lambda value: value.update(producer_command_id="spoofed-command"),
            "producing release command",
        ),
        (
            "network",
            lambda value: value.update(manifest_sha256="0" * 64),
            "current manifest",
        ),
        (
            "full-suite",
            lambda value: value.update(gate_attempt_id="0" * 32),
            "current release attempt",
        ),
    ],
)
def test_wrong_kind_producer_manifest_or_attempt_is_rejected(
    tmp_path: Path,
    kind: str,
    mutation: object,
    message: str,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    path = _artifact(root, kind)
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)  # type: ignore[operator]
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match=message):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)
    report = status_report(load_manifest(root))
    assert report["stage"] == "BACKEND_READY"
    assert report["gates"]["release"]["status"] == "stale"


def test_missing_or_non_regular_artifact_invalidates_a_prior_acceptance(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    path = _artifact(root, "network")
    path.unlink()
    path.mkdir()

    with pytest.raises(ManifestValidationError, match="regular file"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)
    assert status_report(load_manifest(root))["stage"] == "BACKEND_READY"


def test_duplicate_json_key_is_rejected_before_secret_scan_can_be_bypassed(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    path = _artifact(root, "visual")
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '  "summary":',
        '  "summary": "password=not-safe-to-persist",\n  "summary":',
        1,
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="duplicate JSON object key"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_release_requires_evidence_union_to_cover_frozen_denominators(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    path = _artifact(root, "visual")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["verified_coverage"] = []
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="does not cover frozen items"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_stateless_migration_na_is_explicit_and_cannot_verify_coverage(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path, state_model="stateless")
    path = _artifact(root, "migration")
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["status"] == "not_applicable"
    assert value["metrics"]["stateful"] is False
    evidence = verify_acceptance_evidence(
        load_manifest(root), gate_attempt_id=attempt_id
    )
    assert next(item for item in evidence if item["kind"] == "migration")[
        "status"
    ] == "not_applicable"

    value["verified_coverage"] = [
        {"dimension_id": "reachable", "items": ["home.default"]}
    ]
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="cannot verify coverage"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_raw_artifact_hash_tamper_invalidates_acceptance(tmp_path: Path) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    summary = json.loads(_artifact(root, "browser").read_text(encoding="utf-8"))
    raw = load_manifest(root).resolve(summary["raw_artifacts"][0]["path"])
    raw.write_text("tampered trace\n", encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="Expecting value|raw artifact"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)
    assert status_report(load_manifest(root))["stage"] == "BACKEND_READY"


def test_independent_audit_requires_distinct_producer_boundary(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["gates"]["release"]["evidence"]["independent-audit"][
        "producer_command_id"
    ] = "configure-release-evidence"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="produce only the audit"):
        load_manifest(root)


def test_coverage_cannot_be_proved_by_the_wrong_evidence_kind(tmp_path: Path) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    visual_path = _artifact(root, "visual")
    browser_path = _artifact(root, "browser")
    visual = json.loads(visual_path.read_text(encoding="utf-8"))
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    browser["verified_coverage"] = visual["verified_coverage"]
    visual["verified_coverage"] = []
    visual_path.write_text(json.dumps(visual), encoding="utf-8")
    browser_path.write_text(json.dumps(browser), encoding="utf-8")

    with pytest.raises(
        ManifestValidationError, match="not authorized|not bound|does not cover"
    ):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_release_evidence_path_must_stay_under_artifact_root(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["gates"]["release"]["evidence"]["visual"]["path"] = (
        "clone/looks-like-evidence.json"
    )
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="artifact_root"):
        load_manifest(root)


def test_evidence_changed_between_validation_and_state_commit_fails_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    for gate in ("source", "assets", "frontend", "backend"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"

    original_verify = gate_module.verify_acceptance_evidence
    calls = 0

    def mutate_before_second_verify(*args: object, **kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            path = _artifact(root, "visual")
            value = json.loads(path.read_text(encoding="utf-8"))
            value["summary"] = "This otherwise-valid evidence changed before commit."
            path.write_text(json.dumps(value), encoding="utf-8")
        return original_verify(*args, **kwargs)  # type: ignore[arg-type, return-value]

    monkeypatch.setattr(
        gate_module, "verify_acceptance_evidence", mutate_before_second_verify
    )
    result = run_gate(load_manifest(root), "release")
    assert result["status"] == "failed"
    assert "changed before commit" in result["failure"]
    assert status_report(load_manifest(root))["stage"] == "BACKEND_READY"


def test_one_release_command_cannot_write_another_producers_artifact(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["gates"]["release"]["commands"][0]["argv"] = [
        "{python}",
        "-c",
        (
            "import runpy,sys; "
            "sys.argv=['clone/emit_acceptance.py','regular']; "
            "runpy.run_path('clone/emit_acceptance.py',run_name='__main__'); "
            "sys.argv=['clone/emit_acceptance.py','independent-audit']; "
            "runpy.run_path('clone/emit_acceptance.py',run_name='__main__')"
        ),
    ]
    value["gates"]["release"]["commands"][1]["argv"] = [
        "{python}",
        "-c",
        "raise SystemExit(0)",
        "independent-noop",
    ]
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    for gate in ("source", "assets", "frontend", "backend"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"

    result = run_gate(load_manifest(root), "release")
    assert result["status"] == "failed"
    assert "assigned to another producer" in result["failure"]
    assert "independent-audit" in result["failure"]


def test_raw_evidence_declared_aggregate_budget_fails_before_large_reads(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    path = _artifact(root, "visual")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["raw_artifacts"][0]["bytes"] = 100 * 1024 * 1024
    value["raw_artifacts"][1]["bytes"] = 100 * 1024 * 1024
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="per-evidence budget"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_release_cannot_lower_a_frozen_visual_threshold(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)

    def lower_threshold(document: dict[str, object]) -> None:
        document["checkpoints"][0]["threshold"] = 0  # type: ignore[index]

    _rewrite_json_raw(root, "visual", "visual-diff", lower_threshold)
    with pytest.raises(ManifestValidationError, match="frozen contract"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_declared_subjects_without_witness_records_cannot_prove_coverage(
    tmp_path: Path,
) -> None:
    root, attempt_id = _accepted_site(tmp_path)
    coverage_path = root / "scope/coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["dimensions"][0]["required_items"] = ["fake.covered"]
    coverage["dimensions"][0]["required_evidence_kinds"] = ["browser"]
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    visual_path = _artifact(root, "visual")
    visual = json.loads(visual_path.read_text(encoding="utf-8"))
    visual["verified_coverage"] = []
    visual_path.write_text(json.dumps(visual), encoding="utf-8")
    browser_path = _artifact(root, "browser")
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    browser["verified_coverage"] = [
        {"dimension_id": "reachable", "items": ["fake.covered"]}
    ]
    browser_path.write_text(json.dumps(browser), encoding="utf-8")

    def add_unwitnessed_subject(document: dict[str, object]) -> None:
        document["subject_ids"] = ["home-mainline", "fake.covered"]

    _rewrite_json_raw(root, "browser", "browser-trace", add_unwitnessed_subject)
    with pytest.raises(ManifestValidationError, match="witnessed|not bound"):
        verify_acceptance_evidence(load_manifest(root), gate_attempt_id=attempt_id)


def test_compressed_image_dimensions_are_rejected_before_pixel_allocation() -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0)
    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    with pytest.raises(ValueError, match="unsafe|pixel budget"):
        _validated_image_size(payload)
