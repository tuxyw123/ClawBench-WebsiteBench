from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawbench.trajectory.exporter import (
    TrajectoryError,
    export_clone_history,
    export_web2code_run,
    validate_bundle,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_retrospective_clone_export_is_explicit_sanitized_and_portable(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    clone = repository / "clone"
    clone.mkdir(parents=True)
    (repository / "pyproject.toml").write_text(
        "[project]\nname='fixture'\n", encoding="utf-8"
    )
    trajectory = clone / "CODEX_TRAJECTORY.md"
    trajectory.write_text(
        """# Build history

## Checkpoint

Human approved a synthetic, offline scope.

### 1. Source capture

The Agent observed the public page through a controlled browser.

### 2. Frontend implementation

Human review found a layout mismatch and the Agent revised the clone.

### 3. Verification gate

The final browser checks passed.
""",
        encoding="utf-8",
    )
    (clone / "README.md").write_text("Offline fixture clone.\n", encoding="utf-8")
    (clone / "server.py").write_text(
        'credential = "sk-abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8"
    )
    (clone / "Dockerfile").write_text(
        'ENV SERVICE_TOKEN="abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8"
    )
    (clone / "secrets.env").write_text(
        "OPENAI_API_KEY=do-not-export\n", encoding="utf-8"
    )
    (clone / ".env.local").write_text("SERVICE_TOKEN=do-not-export\n", encoding="utf-8")
    observations = clone / "source-fixtures"
    observations.mkdir()
    write_json(observations / "observation.json", {"private": "review first"})
    task = repository / "tasks" / "task.json"
    write_json(
        task,
        {
            "split": "dev",
            "instruction": "Build the local clone",
            "metadata": {"task_id": 42, "platform": "fixture"},
        },
    )

    result = export_clone_history(
        repository_root=repository,
        clone_dir=clone,
        task_path=task,
        output=tmp_path / "bundle",
        archive=True,
    )

    bundle = Path(result["bundle"])
    manifest = result["manifest"]
    assert manifest["capture"] == {
        "mode": "retrospective",
        "completeness": "curated",
        "source_kind": "clone-history",
        "ordering": "per-stream",
    }
    assert manifest["actors"] == ["human-agent"]
    assert manifest["event_count"] == 4
    assert Path(result["archive"]).is_file()
    assert not (bundle / "files/candidate/secrets.env").exists()
    assert not (bundle / "files/candidate/source-fixtures/observation.json").exists()
    source = (bundle / "files/candidate/server.py").read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in source
    assert "<redacted:credential>" in source
    dockerfile = (bundle / "files/candidate/Dockerfile").read_text(encoding="utf-8")
    assert "abcdefghijklmnopqrstuvwxyz" not in dockerfile
    assert "<redacted:credential>" in dockerfile
    assert not (bundle / "files/candidate/.env.local").exists()
    assert validate_bundle(bundle)["status"] == "valid"


def test_live_run_export_unifies_recorded_streams_and_excludes_private_state(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='fixture'\n", encoding="utf-8"
    )
    run = tmp_path / "run"
    run.mkdir()
    write_json(
        run / "task.json",
        {
            "run_id": "run-001",
            "task_id": "northstar-market",
            "site_id": "northstar-market",
            "site_version": "1.0.0",
            "track": "hitl",
            "instruction": "Reconstruct the site",
        },
    )
    write_json(
        run / "run-meta.json",
        {
            "run_id": "run-001",
            "created_at": "2026-07-19T00:00:00Z",
            "status": "evaluation_completed",
        },
    )
    write_json(run / "public" / "smoke.json", {"case": "home"})
    write_jsonl(
        run / "agent" / "agent-messages.jsonl",
        [
            {
                "type": "turn.completed",
                "timestamp": "2026-07-19T00:01:00Z",
                "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
                "message": f"workspace is {run}",
            }
        ],
    )
    write_jsonl(
        run / "browser" / "actions.jsonl",
        [
            {
                "sequence": 1,
                "timestamp": 1_753_056_060,
                "target": "reference",
                "action": "screenshot",
            }
        ],
    )
    screenshot = run / "browser" / "screenshots" / "0001-wb.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"not-a-real-png")
    write_jsonl(
        run / "human-interventions.jsonl",
        [
            {
                "timestamp": "2026-07-19T00:02:00Z",
                "category": "debug-direction",
                "message": "Recheck cart merge",
            }
        ],
    )
    candidate = run / "candidate"
    candidate.mkdir()
    (candidate / "app.py").write_text("print('candidate')\n", encoding="utf-8")
    builds = run / "builds"
    builds.mkdir()
    (builds / "build.log").write_text(
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8"
    )
    write_json(
        run / "eval" / "evaluation-result.json",
        {"status": "passed", "finished_at": "2026-07-19T00:03:00Z"},
    )
    write_json(run / "eval" / "facts.json", {"hidden_seed": 9199})
    (run / "secrets.env").write_text("OPENAI_API_KEY=never-export\n", encoding="utf-8")

    result = export_web2code_run(
        repository_root=repository,
        run_dir=run,
        output=tmp_path / "bundle",
    )

    bundle = Path(result["bundle"])
    manifest = result["manifest"]
    assert manifest["capture"]["mode"] == "live"
    assert manifest["capture"]["completeness"] == "normalized"
    assert set(manifest["actors"]) == {"agent", "evaluator", "human", "system", "tool"}
    assert not (bundle / "files/evaluation/facts.json").exists()
    assert not any(path.name == "secrets.env" for path in bundle.rglob("*"))
    all_events = (bundle / "events.jsonl").read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in all_events
    assert str(run) not in all_events
    assert "<redacted:credential>" in all_events
    build_log = (bundle / "files/builds/build.log").read_text(encoding="utf-8")
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in build_log
    assert validate_bundle(bundle)["status"] == "valid"


def test_infrastructure_only_run_is_explicitly_partial(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    write_json(run / "task.json", {"run_id": "run-partial", "task_id": "fixture"})
    write_json(
        run / "run-meta.json",
        {"run_id": "run-partial", "status": "infrastructure_error"},
    )

    result = export_web2code_run(
        repository_root=repository,
        run_dir=run,
        output=tmp_path / "bundle",
    )

    manifest = result["manifest"]
    assert manifest["capture"]["completeness"] == "partial"
    assert manifest["event_count"] == 1
    limitation = "\n".join(manifest["limitations"])
    assert "agent messages" in limitation
    assert "final candidate" in limitation
    assert "evaluation result" in limitation


def test_bundle_validation_detects_tampering(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    clone = repository / "clone"
    clone.mkdir(parents=True)
    (clone / "CODEX_TRAJECTORY.md").write_text(
        "## Checkpoint\n\nOne retained decision.\n", encoding="utf-8"
    )
    task = repository / "task.json"
    write_json(task, {"task_id": "fixture", "instruction": "Build"})
    result = export_clone_history(
        repository_root=repository,
        clone_dir=clone,
        task_path=task,
        output=tmp_path / "bundle",
        include_code=False,
    )
    bundle = Path(result["bundle"])
    with (bundle / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(TrajectoryError, match="integrity mismatch"):
        validate_bundle(bundle)


def test_bundle_validation_rejects_undeclared_files(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    clone = repository / "clone"
    clone.mkdir(parents=True)
    (clone / "CODEX_TRAJECTORY.md").write_text(
        "## Checkpoint\n\nOne retained decision.\n", encoding="utf-8"
    )
    task = repository / "task.json"
    write_json(task, {"task_id": "fixture", "instruction": "Build"})
    result = export_clone_history(
        repository_root=repository,
        clone_dir=clone,
        task_path=task,
        output=tmp_path / "bundle",
        include_code=False,
    )
    bundle = Path(result["bundle"])
    (bundle / "unhashed.txt").write_text("not declared\n", encoding="utf-8")
    with pytest.raises(TrajectoryError, match="undeclared file"):
        validate_bundle(bundle)


def test_export_rejects_output_overlapping_source(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    clone = repository / "clone"
    clone.mkdir(parents=True)
    (clone / "CODEX_TRAJECTORY.md").write_text(
        "## Checkpoint\n\nOne retained decision.\n", encoding="utf-8"
    )
    task = repository / "task.json"
    write_json(task, {"task_id": "fixture", "instruction": "Build"})
    with pytest.raises(TrajectoryError, match="must not overlap"):
        export_clone_history(
            repository_root=repository,
            clone_dir=clone,
            task_path=task,
            output=clone / "export",
        )
    with pytest.raises(TrajectoryError, match="must not overlap"):
        export_clone_history(
            repository_root=repository,
            clone_dir=clone,
            task_path=task,
            output=repository,
            overwrite=True,
        )
    assert (clone / "CODEX_TRAJECTORY.md").is_file()


def test_export_rejects_symlink_output(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    clone = repository / "clone"
    clone.mkdir(parents=True)
    (clone / "CODEX_TRAJECTORY.md").write_text(
        "## Checkpoint\n\nOne retained decision.\n", encoding="utf-8"
    )
    task = repository / "task.json"
    write_json(task, {"task_id": "fixture", "instruction": "Build"})
    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "bundle-link"
    output.symlink_to(target, target_is_directory=True)
    with pytest.raises(TrajectoryError, match="symlink output"):
        export_clone_history(
            repository_root=repository,
            clone_dir=clone,
            task_path=task,
            output=output,
            overwrite=True,
        )
    assert target.is_dir()
