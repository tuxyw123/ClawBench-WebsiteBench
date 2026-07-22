from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from clawbench.offline_clone.gates import run_gate
from clawbench.offline_clone.manifest import load_manifest
from clawbench.offline_clone.report import status_report
from clawbench.offline_clone.state import StateError, gate_input_fingerprint

from .helpers import add_closed_png_asset, configure_passing_gates, initialized_site


def _set_source_command(root: Path, argv: list[str]) -> None:
    manifest_path = root / "clone.yaml"
    value = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    value["gates"]["source"]["commands"] = [
        {"id": "local-verifier", "argv": argv, "cwd": "."}
    ]
    manifest_path.write_text(
        yaml.safe_dump(value, sort_keys=False), encoding="utf-8"
    )


def test_python_module_verifier_is_fingerprinted(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    package = root / "verify_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    entry = package / "__main__.py"
    entry.write_text("raise SystemExit(0)\n", encoding="utf-8")
    _set_source_command(root, ["{python}", "-m", "verify_pkg"])

    assert run_gate(load_manifest(root), "source")["status"] == "passed"
    entry.write_text("# verifier changed\nraise SystemExit(0)\n", encoding="utf-8")

    report = status_report(load_manifest(root))
    assert report["gates"]["source"]["status"] == "stale"
    assert report["gates"]["source"]["reason"] == "gate_inputs_changed"


def test_equals_style_local_config_is_fingerprinted(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    config = root / "policy.json"
    config.write_text("{}\n", encoding="utf-8")
    _set_source_command(
        root,
        ["{python}", "-c", "raise SystemExit(0)", "--config=policy.json"],
    )

    assert run_gate(load_manifest(root), "source")["status"] == "passed"
    config.write_text('{"changed":true}\n', encoding="utf-8")

    report = status_report(load_manifest(root))
    assert report["gates"]["source"]["status"] == "stale"


def test_explicit_outside_command_path_is_rejected(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    outside = tmp_path / "outside_verify.py"
    outside.write_text("raise SystemExit(0)\n", encoding="utf-8")
    _set_source_command(root, ["../outside_verify.py"])

    with pytest.raises(StateError, match="escapes the site"):
        gate_input_fingerprint(load_manifest(root), "source")


def test_directory_input_rejects_nested_link_or_reparse_point(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    outside = tmp_path / "outside.py"
    outside.write_text("raise SystemExit(0)\n", encoding="utf-8")
    linked = root / "clone/frontend/redirected.py"
    linked.parent.mkdir(parents=True, exist_ok=True)
    try:
        linked.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(StateError, match="link/reparse"):
        gate_input_fingerprint(load_manifest(root), "frontend")


def test_volatile_python_cache_does_not_stale_directory_input(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    frontend = root / "clone/frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "app.py").write_text("APP = True\n", encoding="utf-8")
    before = gate_input_fingerprint(load_manifest(root), "frontend")

    cache = frontend / "__pycache__"
    cache.mkdir()
    (cache / "app.cpython-313.pyc").write_bytes(b"volatile cache")
    after = gate_input_fingerprint(load_manifest(root), "frontend")

    assert after == before


def test_explicit_candidate_runtime_exclusion_is_not_production_input(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    runtime = root / "clone/runtime"
    runtime.mkdir()
    database = runtime / "state.sqlite"
    database.write_bytes(b"state-v1")
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["paths"]["candidate_excludes"] = ["runtime"]
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    before = gate_input_fingerprint(load_manifest(root), "backend")
    database.write_bytes(b"state-v2")
    after = gate_input_fingerprint(load_manifest(root), "backend")
    assert after == before


def test_same_byte_hardlink_replacement_invalidates_asset_gate(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    configure_passing_gates(root)
    add_closed_png_asset(root)
    assert run_gate(load_manifest(root), "source")["status"] == "passed"
    assert run_gate(load_manifest(root), "assets")["status"] == "passed"

    source = root / "source-assets/images/logo.png"
    runtime = root / "clone/static/assets/logo.png"
    runtime.unlink()
    os.link(source, runtime)

    report = status_report(load_manifest(root))
    assert report["stage"] == "SOURCE_CAPTURED"
    assert report["gates"]["assets"]["status"] == "stale"
    assert report["gates"]["assets"]["reason"] == "gate_inputs_changed"
