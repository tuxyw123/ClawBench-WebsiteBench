from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from clawbench.offline_clone.cli import main
from clawbench.offline_clone.manifest import (
    ManifestValidationError,
    initialize_site,
    load_manifest,
    resolve_inside,
)
from clawbench.offline_clone.report import status_report

from .helpers import initialized_site


def test_init_validate_status_and_report_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "new-site"
    assert main(
        [
            "init",
            "--site-dir",
            str(root),
            "--site-id",
            "demo-store",
            "--display-name",
            "Demo Store",
            "--source-url",
            "https://example.test/",
        ]
    ) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["stage"] == "INIT"
    assert (root / "clone/frontend").is_dir()
    assert (root / "scope/claims.jsonl").is_file()
    assert (root / "scope/purpose.json").is_file()
    assert (root / "scope/invariants.json").is_file()
    coverage_path = root / "scope/coverage.json"
    assert json.loads(coverage_path.read_text(encoding="utf-8")) == {
        "schema_version": "offline-clone.coverage.v1",
        "status": "draft",
        "dimensions": [],
    }
    clone_manifest = yaml.safe_load((root / "clone.yaml").read_text(encoding="utf-8"))
    assert clone_manifest["scope"]["coverage"] == "scope/coverage.json"
    assert clone_manifest["scope"]["purpose"] == "scope/purpose.json"
    assert clone_manifest["scope"]["invariants"] == "scope/invariants.json"
    assert len(clone_manifest["gates"]["release"]["evidence"]) == 6
    assert set(clone_manifest["gates"]["release"]["evidence"]) == {
        "visual",
        "browser",
        "network",
        "migration",
        "independent-audit",
        "full-suite",
    }
    assert "scope/purpose.json" in clone_manifest["gates"]["source"]["inputs"]
    assert "scope/invariants.json" in clone_manifest["gates"]["source"]["inputs"]
    assert "scope/coverage.json" in clone_manifest["gates"]["source"]["inputs"]

    assert main(["validate", "--site", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    assert main(["status", "--site", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["stage"] == "INIT"
    report_path = tmp_path / "report.json"
    assert main(["report", "--site", str(root), "--out", str(report_path)]) == 0
    capsys.readouterr()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["asset_closure"]["status"] == "failed"
    assert report["asset_closure"]["closure_status"] == "pending"
    assert report["runtime_remote_request_policy"] == "forbidden"
    assert "runtime_remote_requests" not in report


def test_init_rejects_bad_identity_before_creating_site(tmp_path: Path) -> None:
    root = tmp_path / "bad"
    with pytest.raises(ValueError, match="site_id"):
        initialize_site(
            root,
            site_id="Bad Site",
            display_name="Bad",
            source_url="https://example.test/",
        )
    assert not root.exists()


@pytest.mark.parametrize(
    "source_url, message",
    [
        ("https://user:password@example.test/", "userinfo"),
        ("https://example.test/#private-state", "fragment"),
        ("https://example.test/#", "fragment"),
        ("https://example.test/?access_token=secret", "credentials"),
        ("https://example.test/?page=1;token=secret", "credentials"),
        ("https://example.test/?X-Amz-Signature=secret", "credentials"),
        (
            "https://example.test/%61ccess_token%3Dnot-safe",
            "path or query value",
        ),
        (
            "https://example.test/%2561ccess_token%253Dnot-safe",
            "path or query value",
        ),
        (
            "https://example.test/?next=https%3A%2F%2Fother.test%2F%3Faccess_token%3Dnot-safe",
            "path or query value",
        ),
        (
            "https://example.test/?next=https%253A%252F%252Fother.test%252F%253Faccess_token%253Dnot-safe",
            "path or query value",
        ),
        ("https://example.test/%ZZ", "malformed percent escape"),
        ("https://example.test/%0Ahidden", "decoded control"),
        ("https://example.test/\nfoo", "ASCII control"),
        ("\x00https://example.test/", "ASCII control"),
        (" https://example.test/", "whitespace"),
        ("https://exa\tmple.test/", "ASCII control"),
        (
            "https://example.test/download/sk-abcdefghijklmnop",
            "path or query value",
        ),
    ],
)
def test_init_rejects_ambiguous_or_credential_bearing_source_urls_before_writing(
    tmp_path: Path, source_url: str, message: str
) -> None:
    root = tmp_path / "unsafe-url"
    with pytest.raises(ValueError, match=message):
        initialize_site(
            root,
            site_id="unsafe-url",
            display_name="Unsafe URL",
            source_url=source_url,
        )
    assert not root.exists()


def test_init_preflights_nonempty_destination_without_overwriting_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "partial-site"
    existing = root / "scope/routes.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("user-owned partial data\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="non-empty"):
        initialize_site(
            root,
            site_id="partial-site",
            display_name="Partial Site",
            source_url="https://example.test/",
        )

    assert existing.read_text(encoding="utf-8") == "user-owned partial data\n"
    assert not (root / "clone.yaml").exists()


def test_source_url_allows_benign_code_prose(tmp_path: Path) -> None:
    root = tmp_path / "benign-code"
    manifest = initialize_site(
        root,
        site_id="benign-code",
        display_name="Benign Code",
        source_url="https://example.test/docs/code-examples?topic=verification%20code",
    )
    assert manifest.data["site_id"] == "benign-code"


def test_manifest_source_origin_uses_sensitive_url_validation(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    manifest_path = root / "clone.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["origins"] = ["https://example.test/?api_key=secret"]
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="credentials"):
        load_manifest(root)


def test_manifest_and_scope_json_reject_duplicate_mapping_keys(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest_path = root / "clone.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + "\nsite_id: shadowed-site\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestValidationError, match="duplicate key"):
        load_manifest(root)

    root = initialized_site(tmp_path / "second")
    coverage_path = root / "scope/coverage.json"
    coverage_path.write_text(
        '{"schema_version":"offline-clone.coverage.v1","status":"draft",'
        '"status":"frozen","dimensions":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ManifestValidationError, match="duplicate JSON object key"):
        load_manifest(root)


def test_manifest_data_and_sha_are_derived_from_one_file_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = initialized_site(tmp_path)
    manifest_path = root / "clone.yaml"
    original_read_bytes = Path.read_bytes
    manifest_reads = 0

    def count_manifest_reads(path: Path) -> bytes:
        nonlocal manifest_reads
        if path == manifest_path:
            manifest_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_manifest_reads)
    manifest = load_manifest(root)
    assert manifest.data["site_id"] == "example-shop"
    assert len(manifest.sha256) == 64
    assert manifest_reads == 1


@pytest.mark.parametrize(
    "relative",
    [
        "../outside",
        r"..\outside",
        r"C:\outside\secret",
        r"C:drive-relative-secret",
        r"\\server\share\secret",
        "/etc/passwd",
    ],
)
def test_path_containment_rejects_posix_and_windows_escape(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError):
        resolve_inside(tmp_path, relative)


def test_manifest_rejects_a_windows_absolute_reference(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["paths"]["candidate_root"] = r"C:\outside\clone"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError):
        load_manifest(root)


@pytest.mark.parametrize("writable", [".", "clone"])
def test_writable_state_paths_must_name_regular_files(
    tmp_path: Path, writable: str
) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["paths"]["trajectory_file"] = writable
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="file|regular"):
        load_manifest(root)


def test_every_gate_requires_an_executable_command_including_assets(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["gates"]["assets"]["commands"] = []
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="non-empty"):
        load_manifest(root)


@pytest.mark.parametrize("gate", ["source", "assets", "frontend", "backend", "release"])
def test_every_gate_requires_at_least_one_fingerprinted_input(
    tmp_path: Path, gate: str
) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["gates"][gate]["inputs"] = []
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="non-empty"):
        load_manifest(root)


@pytest.mark.parametrize(
    "scope_name",
    ["purpose", "invariants", "routes", "journeys", "checkpoints", "claims", "coverage"],
)
def test_source_fingerprint_requires_every_scope_contract_input(
    tmp_path: Path, scope_name: str
) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["gates"]["source"]["inputs"].remove(value["scope"][scope_name])
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match=f"scope.{scope_name}"):
        load_manifest(root)


@pytest.mark.parametrize(
    "mode",
    ["input", "cwd", "absolute-argv", "equals-config", "python-module"],
)
def test_source_verifier_cannot_consume_candidate_outputs(
    tmp_path: Path, mode: str
) -> None:
    root = initialized_site(tmp_path)
    verifier = root / "clone/verifier.py"
    verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    command = value["gates"]["source"]["commands"][0]
    if mode == "input":
        value["gates"]["source"]["inputs"].append("clone/verifier.py")
    elif mode == "cwd":
        command["cwd"] = "clone"
    elif mode == "absolute-argv":
        command["argv"] = ["{python}", str(verifier.resolve())]
    elif mode == "equals-config":
        command["argv"] = ["{python}", "-c", "raise SystemExit(0)", "--config=clone/verifier.py"]
    else:
        command["argv"] = ["{python}", "-m", "clone.verifier"]
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="candidate_root"):
        load_manifest(root)


def test_candidate_exclusion_cannot_hide_production_through_a_link(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    production = root / "clone/production"
    production.mkdir()
    (production / "app.py").write_text("APP = True\n", encoding="utf-8")
    alias = root / "clone/runtime-alias"
    try:
        alias.symlink_to(production, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["paths"]["candidate_excludes"] = ["runtime-alias"]
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="symbolic link|reparse"):
        load_manifest(root)


def test_optional_multi_actor_capture_context_is_schema_valid(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    path = root / "clone.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["source"]["baseline"].update(
        tenant="tenant-alpha",
        workspace="workspace-docs",
        role="editor",
        capabilities=["read", "write"],
        feature_flags=["version-history"],
        user_agent="OfflineCloneHarness/1.0",
        viewport={"width": 1440, "height": 900, "device_scale_factor": 1.0},
        capture_id="editor-desktop-default",
    )
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    assert load_manifest(root).data["source"]["baseline"]["role"] == "editor"


def test_manifest_bytes_change_marks_every_completed_gate_stale(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    state = json.loads(manifest.state_path.read_text(encoding="utf-8"))
    state["gates"]["source"] = {
        "status": "passed",
        "input_sha256": "not-current",
        "attempts": [],
        "completed_at": "2026-07-22T00:00:00Z",
    }
    manifest.state_path.write_text(json.dumps(state), encoding="utf-8")
    path = root / "clone.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# manifest edit\n", encoding="utf-8")
    report = status_report(load_manifest(root))
    assert report["manifest_current"] is False
    assert report["stage"] == "INIT"
    assert report["gates"]["source"]["status"] == "stale"
