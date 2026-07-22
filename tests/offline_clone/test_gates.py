from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import yaml

import clawbench.offline_clone.gates as gate_module
from clawbench.offline_clone.gates import GateError, run_gate
from clawbench.offline_clone.manifest import ManifestValidationError, load_manifest
from clawbench.offline_clone.report import status_report
from clawbench.offline_clone.state import effective_gate_statuses, load_state

from .helpers import add_closed_png_asset, configure_passing_gates, initialized_site


def test_gates_cannot_skip_and_reach_accepted_only_in_order(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    manifest = load_manifest(root)
    with pytest.raises(GateError, match="prerequisite"):
        run_gate(manifest, "assets")
    for gate in ("source", "assets", "frontend", "backend", "release"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"
    report = status_report(load_manifest(root))
    assert report["stage"] == "ACCEPTED"
    assert all(item["status"] == "passed" for item in report["gates"].values())


def test_manifest_change_invalidates_downstream_and_forces_source_gate(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    for gate in ("source", "assets", "frontend"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"
    path = root / "clone.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "# changed scope contract\n",
        encoding="utf-8",
    )
    with pytest.raises(GateError, match="source"):
        run_gate(load_manifest(root), "backend")
    report = status_report(load_manifest(root))
    assert report["stage"] == "INIT"
    assert report["gates"]["source"]["status"] == "stale"
    assert report["gates"]["frontend"]["status"] == "stale"
    assert run_gate(load_manifest(root), "source")["status"] == "passed"


def test_changed_gate_input_invalidates_that_gate_and_downstream(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    for gate in ("source", "assets", "frontend", "backend"):
        run_gate(load_manifest(root), gate)
    (root / "clone/backend/change.py").write_text("changed = True\n", encoding="utf-8")
    report = status_report(load_manifest(root))
    assert report["stage"] == "FRONTEND_READY"
    assert report["gates"]["backend"]["status"] == "stale"
    assert report["gates"]["release"]["status"] == "pending"


def test_unlisted_candidate_file_change_invalidates_an_accepted_release(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    for gate in ("source", "assets", "frontend", "backend", "release"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"

    (root / "clone/unlisted-production.js").write_text(
        "export const changed = true;\n", encoding="utf-8"
    )
    report = status_report(load_manifest(root))
    assert report["stage"] != "ACCEPTED"
    assert report["gates"]["backend"]["status"] == "stale"
    assert report["gates"]["backend"]["reason"] == "gate_inputs_changed"
    assert report["gates"]["release"]["status"] == "stale"


def test_changed_local_verifier_argv_file_invalidates_gate_without_manual_input(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    tools = root / "tools"
    tools.mkdir()
    verifier = tools / "verify_scope.py"
    verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")
    manifest_path = root / "clone.yaml"
    manifest_value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_value["gates"]["source"]["commands"] = [
        {
            "id": "verify-scope",
            "argv": ["{python}", "tools/verify_scope.py"],
            "cwd": ".",
        }
    ]
    assert "tools/verify_scope.py" not in manifest_value["gates"]["source"]["inputs"]
    manifest_path.write_text(
        yaml.safe_dump(manifest_value, sort_keys=False), encoding="utf-8"
    )

    manifest = load_manifest(root)
    assert run_gate(manifest, "source")["status"] == "passed"
    verifier.write_text("# changed verifier\nraise SystemExit(0)\n", encoding="utf-8")

    report = status_report(load_manifest(root))
    assert report["stage"] == "INIT"
    assert report["gates"]["source"]["status"] == "stale"
    assert report["gates"]["source"]["reason"] == "gate_inputs_changed"


def test_changed_coverage_ledger_invalidates_source_and_downstream(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    assert run_gate(load_manifest(root), "source")["status"] == "passed"

    path = root / "scope/coverage.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["dimensions"] = [
        {
            "id": "reachable",
            "label": "Reachable states",
            "unit": "route-state",
            "category": "reachability",
            "required_evidence_kinds": ["visual"],
            "required_items": ["home.default"],
            "satisfied_items": [],
        }
    ]
    path.write_text(json.dumps(value), encoding="utf-8")

    report = status_report(load_manifest(root))
    assert report["manifest_current"] is True
    assert report["stage"] == "INIT"
    assert report["gates"]["source"]["status"] == "stale"
    assert report["gates"]["source"]["reason"] == "gate_inputs_changed"


@pytest.mark.parametrize(
    "relative",
    [
        "scope/purpose.json",
        "scope/invariants.json",
        "scope/routes.json",
        "scope/journeys.json",
        "scope/checkpoints.json",
        "scope/claims.jsonl",
        "scope/coverage.json",
    ],
)
def test_each_scope_contract_change_invalidates_source(
    tmp_path: Path, relative: str
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    assert run_gate(load_manifest(root), "source")["status"] == "passed"

    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    report = status_report(load_manifest(root))
    assert report["stage"] == "INIT"
    assert report["gates"]["source"]["status"] == "stale"
    assert report["gates"]["source"]["reason"] == "gate_inputs_changed"


def test_asset_gate_fails_closed_without_complete_p0_assets(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    assert run_gate(load_manifest(root), "source")["status"] == "passed"
    result = run_gate(load_manifest(root), "assets")
    assert result["status"] == "failed"
    assert result["asset_closure"]["closure_status"] == "pending"
    with pytest.raises(GateError, match="assets"):
        run_gate(load_manifest(root), "frontend")


def test_source_gate_refuses_a_draft_coverage_ledger(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    coverage_path = root / "scope/coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["status"] = "draft"
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")

    manifest = load_manifest(root)
    with pytest.raises(ManifestValidationError, match="status 'frozen'"):
        run_gate(manifest, "source")
    assert "source" not in load_state(manifest)["gates"]


def test_gate_crash_cannot_leave_an_old_pass_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    manifest = load_manifest(root)
    assert run_gate(manifest, "source")["status"] == "passed"

    original_run_command = gate_module._run_command

    class SimulatedCrash(RuntimeError):
        pass

    def crash_after_durable_begin(*args: object, **kwargs: object) -> dict[str, object]:
        state = load_state(manifest)
        assert state["gates"]["source"]["status"] == "running"
        assert state["gates"]["source"]["attempts"][-1]["status"] == "running"
        raise SimulatedCrash("process terminated during command")

    monkeypatch.setattr(gate_module, "_run_command", crash_after_durable_begin)
    with pytest.raises(SimulatedCrash):
        run_gate(manifest, "source")

    state = load_state(manifest)
    assert state["gates"]["source"]["status"] == "running"
    statuses = effective_gate_statuses(manifest, state)
    assert statuses["source"]["status"] == "running"
    assert status_report(manifest)["stage"] == "INIT"

    monkeypatch.setattr(gate_module, "_run_command", original_run_command)
    assert run_gate(manifest, "source")["status"] == "passed"
    attempts = load_state(manifest)["gates"]["source"]["attempts"]
    assert attempts[-2]["status"] == "interrupted"


def test_slow_downstream_gate_cannot_overwrite_newer_upstream_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    for gate in ("source", "assets", "frontend", "backend"):
        assert run_gate(load_manifest(root), gate)["status"] == "passed"

    original_run_command = gate_module._run_command
    downstream_started = threading.Event()
    allow_downstream_to_finish = threading.Event()
    outcome: dict[str, object] = {}

    def controlled_command(*args: object, **kwargs: object) -> dict[str, object]:
        if threading.current_thread().name == "slow-backend-gate":
            downstream_started.set()
            assert allow_downstream_to_finish.wait(timeout=10)
        return original_run_command(*args, **kwargs)

    def rerun_backend() -> None:
        try:
            outcome["result"] = run_gate(load_manifest(root), "backend")
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            outcome["error"] = exc

    monkeypatch.setattr(gate_module, "_run_command", controlled_command)
    worker = threading.Thread(target=rerun_backend, name="slow-backend-gate")
    worker.start()
    assert downstream_started.wait(timeout=10)
    assert run_gate(load_manifest(root), "source")["status"] == "passed"
    allow_downstream_to_finish.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["result"]["status"] == "stale"  # type: ignore[index]

    report = status_report(load_manifest(root))
    assert report["gates"]["source"]["status"] == "passed"
    assert report["gates"]["assets"]["status"] == "stale"
    assert report["gates"]["backend"]["status"] == "stale"
