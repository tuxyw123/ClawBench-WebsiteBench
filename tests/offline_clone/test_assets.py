from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from clawbench.offline_clone import assets as assets_module
from clawbench.offline_clone.assets import verify_asset_closure
from clawbench.offline_clone.manifest import ManifestValidationError, load_manifest

from .helpers import add_closed_png_asset, initialized_site


def test_asset_closure_verifies_source_runtime_bytes_mime_dimensions_and_reference(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    report = verify_asset_closure(load_manifest(root))
    assert report.passed
    assert report.p0_required == report.p0_verified == 1
    assert report.p0_closure == 1.0
    assert report.missing == report.mismatched == 0
    assert report.referenced_assets == 1
    assert report.reference_edges == 1
    assert report.missing_assets == report.missing_copies == 0
    assert report.mismatched_assets == 0
    serialized = report.as_dict()
    assert serialized["referenced_assets"] == 1
    assert serialized["missing_assets"] == serialized["missing_copies"] == 0
    assert "referenced" not in serialized
    assert "missing" not in serialized


def test_asset_closure_fails_when_runtime_copy_changes(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    (root / "clone/static/assets/logo.png").write_bytes(b"not an image")
    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert any(issue.blocking and issue.asset_id == "logo" for issue in report.issues)


def test_missing_counters_distinguish_assets_from_source_runtime_copies(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    (root / "source-assets/images/logo.png").unlink()
    (root / "clone/static/assets/logo.png").unlink()
    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert report.missing_assets == 1
    assert report.missing_copies == 2
    assert report.missing == 2  # Backward-compatible alias is copy-level.
    assert report.mismatched_assets == 0


def test_asset_closure_fails_when_required_asset_is_unreferenced(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["assets"][0]["referenced_by"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert {issue.code for issue in report.issues} >= {"UNREFERENCED_REQUIRED_ASSET"}
    assert report.referenced_assets == 0
    assert report.reference_edges == 0
    assert report.unreferenced_required_assets == 1


def test_empty_asset_scope_requires_explicit_no_assets_decision(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    pending = verify_asset_closure(manifest)
    assert not pending.passed
    assert pending.p0_closure is None
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(
        {
            "closure_status": "no-assets",
            "no_assets_reason": "The frozen text-only scope renders no external assets.",
        }
    )
    path.write_text(json.dumps(value), encoding="utf-8")
    report = verify_asset_closure(load_manifest(root))
    assert report.passed
    assert report.p0_closure == 1.0


@pytest.mark.parametrize("priority", ["p1", "p2"])
def test_every_required_priority_is_part_of_the_blocking_closure(
    tmp_path: Path, priority: str
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["assets"][0]["priority"] = priority
    path.write_text(json.dumps(value), encoding="utf-8")
    (root / "clone/static/assets/logo.png").unlink()
    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert report.p0_required == 0
    assert report.p0_closure is None
    assert report.required == 1
    assert report.required_verified == 0
    assert report.required_closure == 0.0


@pytest.mark.parametrize("priority", ["p0", "p1"])
def test_p0_or_referenced_asset_cannot_opt_out_of_required_closure(
    tmp_path: Path, priority: str
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["assets"][0]["priority"] = priority
    value["assets"][0]["required"] = False
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="True was expected"):
        load_manifest(root)


def test_source_and_runtime_hardlinks_do_not_count_as_two_copies(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    source = root / "source-assets/images/logo.png"
    runtime = root / "clone/static/assets/logo.png"
    runtime.unlink()
    os.link(source, runtime)

    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert report.mismatched_assets == 1
    assert {issue.code for issue in report.issues} >= {
        "SOURCE_RUNTIME_IDENTITY_ALIAS"
    }


def test_asset_copy_with_an_external_hardlink_is_not_an_independent_copy(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    source = root / "source-assets/images/logo.png"
    external_alias = tmp_path / "source-alias.png"
    os.link(source, external_alias)

    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert {issue.code for issue in report.issues} >= {
        "ASSET_MULTIPLE_HARD_LINKS"
    }


def test_normalized_physical_identity_is_deduplicated_across_assets(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    first = value["assets"][0]
    source = root / "source-assets/images/logo-copy.png"
    runtime = root / "clone/static/assets/logo-copy.png"
    os.link(root / first["source_path"], source)
    runtime.write_bytes((root / first["runtime_path"]).read_bytes())
    second = {
        **first,
        "id": "logo-copy",
        "source_path": "source-assets/images/logo-copy.png",
        "runtime_path": "clone/static/assets/logo-copy.png",
    }
    value["assets"].append(second)
    path.write_text(json.dumps(value), encoding="utf-8")

    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert report.mismatched_assets == 2
    assert {issue.code for issue in report.issues} & {
        "DUPLICATE_ASSET_PATH_IDENTITY",
        "ASSET_MULTIPLE_HARD_LINKS",
    }


def test_asset_path_rejects_an_intermediate_symlink_or_reparse_redirect(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    link = root / "source-assets/image-link"
    try:
        link.symlink_to(root / "source-assets/images", target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["assets"][0]["source_path"] = "source-assets/image-link/logo.png"
    path.write_text(json.dumps(value), encoding="utf-8")

    report = verify_asset_closure(load_manifest(root))
    assert not report.passed
    assert {issue.code for issue in report.issues} >= {"ASSET_PATH_INVALID"}


def test_windows_reparse_attribute_is_treated_as_a_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
    monkeypatch.setattr(Path, "lstat", lambda _path: metadata)
    assert assets_module._is_link_or_reparse(tmp_path / "junction")


def test_asset_source_url_rejects_recognized_credential_query_keys(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    add_closed_png_asset(root)
    path = root / "source-assets/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["assets"][0]["source_url"] = (
        "https://cdn.example.test/logo.png?X-Amz-Credential=secret"
    )
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="credentials"):
        load_manifest(root)


@pytest.mark.parametrize(
    "name,payload,message",
    [
        ("fake.woff2", b"not-a-font", "font magic"),
        ("padded.png", b" " * 4096 + b"<html>error</html>", "HTML response"),
        ("not-svg.svg", b'<root><svg width="1" height="1"/></root>', "svg root"),
        (
            "active.svg",
            b'<svg width="1" height="1"><script>bad()</script></svg>',
            "active SVG",
        ),
        (
            "remote.svg",
            b'<svg width="1" height="1"><image href="https://cdn.example.test/a.png"/></svg>',
            "reference",
        ),
        (
            "remote.css",
            b'.hero{background:url("https://cdn.example.test/a.png")}',
            "external runtime",
        ),
        (
            "file.css",
            b'.hero{background:url("file:///etc/passwd")}',
            "external runtime",
        ),
    ],
)
def test_asset_inspection_rejects_disguised_or_remote_active_content(
    tmp_path: Path, name: str, payload: bytes, message: str
) -> None:
    path = tmp_path / name
    path.write_bytes(payload)
    with pytest.raises(ValueError, match=message):
        assets_module.inspect_asset(path)
