from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from clawbench.harbor.cli import main
from clawbench.harbor.manifest import HarborManifestError, load_instance, load_site
from clawbench.harbor.materialize import materialize_instance
from clawbench.harbor.scaffold import initialize_instance, initialize_site


def _authoring_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    corpus = tmp_path / "harbor"
    site_dir = corpus / "sites" / "demo-store"
    instance_dir = corpus / "instances" / "demo-store-rebuild"
    (corpus / "instances").mkdir(parents=True)
    site_manifest = initialize_site(
        site_dir,
        site_id="demo-store",
        display_name="Demo Store",
    )
    instance_manifest = initialize_instance(
        instance_dir,
        instance_id="demo-store-rebuild",
        site_manifest="sites/demo-store/site.yaml",
        author_name="Benchmark Team",
        author_email="bench@example.test",
    )
    return corpus, site_manifest, instance_manifest


def test_scaffolds_form_a_valid_fullstack_instance(tmp_path: Path) -> None:
    corpus, site_path, instance_path = _authoring_corpus(tmp_path)

    site = load_site(site_path)
    instance = load_instance(instance_path)

    assert instance.corpus_root == corpus.resolve()
    assert instance.site.path == site.path
    assert site.data["runtime"]["agent_browser"] == "browser-use-cli"
    assert site.data["runtime"]["formal_browser"] == "playwright"
    assert sum(site.data["scoring"]["dimensions"].values()) == 100
    assert len(instance.data["tests"]["api"]) >= 2
    assert len(instance.data["tests"]["ui"]) >= 2


def test_scaffold_identity_must_match_normalized_directory(tmp_path: Path) -> None:
    destination = tmp_path / "different-name"
    with pytest.raises(ValueError, match="directory name"):
        initialize_site(
            destination,
            site_id="demo-store",
            display_name="Demo Store",
        )
    assert not destination.exists()


def test_semantics_reject_visibility_overlap_bad_prefixes_and_weak_complexity(
    tmp_path: Path,
) -> None:
    _, site_path, instance_path = _authoring_corpus(tmp_path)
    site = yaml.safe_load(site_path.read_text(encoding="utf-8"))
    site["paths"]["verifier"] = "public/verifier"
    (site_path.parent / "public" / "verifier").mkdir()
    site_path.write_text(yaml.safe_dump(site, sort_keys=False), encoding="utf-8")
    with pytest.raises(HarborManifestError, match="overlap"):
        load_site(site_path)

    site["paths"]["verifier"] = "verifier"
    site_path.write_text(yaml.safe_dump(site, sort_keys=False), encoding="utf-8")
    instance = yaml.safe_load(instance_path.read_text(encoding="utf-8"))
    instance["tests"]["api"] = ["ui::wrong/prefix"]
    instance_path.write_text(
        yaml.safe_dump(instance, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(HarborManifestError, match="at least 2|must start with"):
        load_instance(instance_path)


def test_materialize_enforces_visibility_and_is_reproducible(
    tmp_path: Path,
) -> None:
    _, site_path, instance_path = _authoring_corpus(tmp_path)
    (site_path.parent / "public" / "api-contract.json").write_text(
        '{"public": true}\n',
        encoding="utf-8",
    )
    (site_path.parent / "verifier" / "secret-check.py").write_text(
        "REFERENCE_ONLY_EXPECTATION = True\n",
        encoding="utf-8",
    )
    (instance_path.parent / "public" / "starter.py").write_text(
        "STARTER = True\n",
        encoding="utf-8",
    )

    output = materialize_instance(
        instance_path,
        tmp_path / "dist" / "demo-store-rebuild",
    )

    assert (output / "environment/seed/starter.py").is_file()
    assert (
        output / "environment/seed/.clawbench/site/api-contract.json"
    ).is_file()
    assert not (
        output / "environment/seed/.clawbench/site/secret-check.py"
    ).exists()
    assert (output / "environment/reference/server.py").is_file()
    assert (output / "tests/reference/server.py").is_file()
    assert (output / "tests/site/secret-check.py").is_file()
    assert (output / "solution/solve.sh").is_file()
    task = tomllib.loads((output / "task.toml").read_text(encoding="utf-8"))
    assert task["schema_version"] == "1.4"
    assert task["artifacts"] == ["/app/repo"]
    assert task["verifier"]["environment_mode"] == "separate"
    assert task["metadata"]["task_type"] == "fullstack-reconstruction"
    assert task["environment"]["memory_mb"] == 8192
    compose = yaml.safe_load(
        (output / "environment/docker-compose.yaml").read_text(encoding="utf-8")
    )
    assert compose["services"]["main"]["depends_on"]["reference"]["condition"] == (
        "service_healthy"
    )
    assert (
        compose["services"]["main"]["environment"]["CLAWBENCH_REFERENCE_URL"]
        == "http://reference:8080"
    )

    bundle = json.loads((output / "bundle-manifest.json").read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in bundle["files"]}
    assert entries["environment/seed/starter.py"]["visibility"] == "agent-public"
    assert entries["environment/Dockerfile"]["visibility"] == "build-control"
    assert entries["tests/site/secret-check.py"]["visibility"] == "verifier-only"
    assert (
        entries["environment/reference/server.py"]["visibility"]
        == "reference-sidecar-only"
    )
    assert entries["solution/solve.sh"]["visibility"] == "oracle-only"
    assert all(len(entry["sha256"]) == 64 for entry in entries.values())

    with pytest.raises(FileExistsError, match="already exists"):
        materialize_instance(instance_path, output)


def test_cli_init_validate_materialize_and_corpus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus = tmp_path / "harbor"
    site = corpus / "sites" / "shop"
    instance = corpus / "instances" / "shop-rebuild"
    (corpus / "instances").mkdir(parents=True)

    assert main(
        [
            "init-site",
            "--site-dir",
            str(site),
            "--site-id",
            "shop",
            "--display-name",
            "Shop",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "init-instance",
            "--instance-dir",
            str(instance),
            "--instance-id",
            "shop-rebuild",
            "--site-manifest",
            "sites/shop/site.yaml",
            "--author-name",
            "Benchmark Team",
            "--author-email",
            "bench@example.test",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["validate", "--instance", str(instance)]) == 0
    assert json.loads(capsys.readouterr().out)["test_nodes"] == 8
    assert main(["validate-corpus", "--corpus-root", str(corpus)]) == 0
    assert json.loads(capsys.readouterr().out)["instances"] == 1
    output = tmp_path / "bundle"
    assert main(
        [
            "materialize",
            "--instance",
            str(instance),
            "--out",
            str(output),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "materialized"
