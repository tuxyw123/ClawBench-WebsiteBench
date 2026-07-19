"""W3 tests for topology, BrowserUse policy, builder budget, and run preparation."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from clawbench.web2code.policy import scan_candidate
from clawbench.web2code.candidate import CandidateRuntime, safe_name
from clawbench.web2code.hitl import HumanInterventionLog
from clawbench.web2code.run import docker_ready, prepare_run, run_pilot
from clawbench.web2code.topology import TopologyValidationError, validate_compose_topology


REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = REPO_ROOT / "websitebench" / "northstar-market"
GATEWAY_ROOT = REPO_ROOT / "websitebench" / "services" / "browser-gateway"
BUILDER_ROOT = REPO_ROOT / "websitebench" / "services" / "builder"
MODEL_PROXY_ROOT = REPO_ROOT / "websitebench" / "services" / "model-proxy"
for import_root in (GATEWAY_ROOT, BUILDER_ROOT, MODEL_PROXY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from benchbrowser.app import BrowserGateway, GatewayConfig  # noqa: E402
from benchbuilder.app import BuilderConfig, CandidateBuilder, REQUIRED_PATHS  # noqa: E402
from model_proxy import ModelProxy  # noqa: E402


def test_compose_trust_boundaries_are_machine_enforced() -> None:
    compose = validate_compose_topology(SITE_ROOT / "docker-compose.yml")

    agent = compose["services"]["agent"]
    assert set(agent["networks"]) == {"agent-control", "model-egress"}
    assert "reference-web" not in agent["networks"]
    assert "candidate-web" not in agent["networks"]
    assert compose["networks"]["model-egress"]["internal"] is True
    assert compose["networks"]["reference-web"]["internal"] is True
    assert compose["networks"]["candidate-web"]["internal"] is True
    assert set(compose["services"]["model-proxy"]["networks"]) == {
        "model-egress",
        "internet-egress",
    }
    assert set(compose["services"]["mailbox"]["networks"]) == {"reference-web"}
    assert set(compose["services"]["mailbox-delivery"]["networks"]) == {
        "reference-web",
        "candidate-web",
    }
    agent_mounts = json.dumps(agent["volumes"])
    assert "secrets.env" not in agent_mounts
    assert "required}:/artifacts" not in agent_mounts
    assert all(
        "/var/run/docker.sock" not in json.dumps(service)
        for service in compose["services"].values()
    )
    evaluator_mounts = json.dumps(compose["services"]["evaluator"]["volumes"])
    assert "/bench-fixtures/1101.json" in evaluator_mounts
    assert "/bench-fixtures/1102.json" in evaluator_mounts


def test_topology_validator_rejects_reference_access_from_agent(tmp_path: Path) -> None:
    source = (SITE_ROOT / "docker-compose.yml").read_text()
    invalid = source.replace("      model-egress: {}\n    deploy:", "      model-egress: {}\n      reference-web: {}\n    deploy:")
    path = tmp_path / "compose.yml"
    path.write_text(invalid)

    with pytest.raises(TopologyValidationError, match="services.agent.networks"):
        validate_compose_topology(path)


def test_candidate_policy_detects_proxy_iframe_socket_and_private_copy(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    private = tmp_path / "private"
    candidate.mkdir()
    private.mkdir()
    for relative in REQUIRED_PATHS:
        path = candidate / relative
        if "." not in Path(relative).name:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder")
    copied = "private implementation marker\n" * 30
    (private / "secret.py").write_text(copied)
    (candidate / "backend" / "copied.py").write_text(copied)
    (candidate / "frontend" / "index.html").write_text(
        '<iframe src="http://reference-app:8080"></iframe>'
    )
    (candidate / "docker-compose.yml").write_text(
        "services:\n  app:\n    privileged: true\n    volumes: [/var/run/docker.sock:/x]\n"
    )

    findings = scan_candidate(candidate, private_reference=private)
    codes = {finding.code for finding in findings}
    assert {"IFRAME", "REFERENCE_ORIGIN", "PRIVILEGED_CONTAINER", "DOCKER_SOCKET", "PRIVATE_FILE_COPY"} <= codes


def test_browser_gateway_allows_only_bound_origins_and_budgeted_actions(tmp_path: Path) -> None:
    class FakeGateway(BrowserGateway):
        def __init__(self, config: GatewayConfig) -> None:
            super().__init__(config)
            self.commands: list[tuple[str, ...]] = []

        async def run(self, *arguments: str, timeout: float = 45) -> str:
            del timeout
            self.commands.append(arguments)
            if arguments[-2:] == ("get", "url"):
                return "Current URL: http://reference-app:8080/\n"
            if "screenshot" in arguments:
                Path(arguments[-1]).write_bytes(b"png")
            return "[0] link 'Shop all'\n"

    gateway = FakeGateway(
        GatewayConfig(
            token="token",
            reference_url="http://reference-app:8080",
            mailbox_url="http://mailbox:8025",
            candidate_url="http://candidate:8080",
            action_budget=2,
            artifact_dir=tmp_path,
        )
    )

    async def scenario() -> None:
        assert gateway.allowed_url("http://reference-app:8080/search?q=x", "reference")
        assert not gateway.allowed_url("http://reference-app.evil:8080/", "reference")
        assert not gateway.allowed_url("http://mailbox:8025/", "reference")
        created = await gateway.create_session("reference", "/")
        result = await gateway.act(created["session_id"], {"action": "state"})
        assert result["remaining_actions"] == 0
        with pytest.raises(Exception) as exhausted:
            await gateway.act(created["session_id"], {"action": "state"})
        assert getattr(exhausted.value, "status_code", None) == 429

    asyncio.run(scenario())


def test_builder_validates_required_layout_without_host_socket(tmp_path: Path) -> None:
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    builder = CandidateBuilder(
        BuilderConfig(
            token="token",
            workspace=workspace,
            artifacts=tmp_path / "artifacts",
            docker_host="tcp://rootless-buildkit:2375",
            max_builds=20,
            run_id="test-run",
            admin_token="admin",
        )
    )
    assert len(builder.validate_workspace()) == len(REQUIRED_PATHS)
    for relative in REQUIRED_PATHS:
        path = workspace / relative
        if relative in {"frontend", "backend"}:
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder")
            if relative.startswith("scripts/"):
                path.chmod(0o755)
    assert builder.validate_workspace() == []
    assert "docker.sock" not in builder.config.docker_host


def test_builder_waits_for_health_and_resets_preview_to_public_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "1101.json"
    fixture.write_text(json.dumps({"now": "2026-01-15T12:00:00Z"}))
    builder = CandidateBuilder(
        BuilderConfig(
            token="token",
            workspace=tmp_path / "candidate",
            artifacts=tmp_path / "artifacts",
            docker_host="tcp://rootless-buildkit:2375",
            max_builds=20,
            run_id="preview-test",
            admin_token="admin",
            preview_fixture_source=fixture,
        )
    )
    observed: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str):
            observed["health_url"] = url
            return type("Response", (), {"status_code": 200})()

        async def post(self, url: str, *, headers: dict, json: dict):
            observed.update({"reset_url": url, "headers": headers, "body": json})
            return type("Response", (), {"status_code": 200, "text": ""})()

    monkeypatch.setattr(
        "benchbuilder.app.httpx.AsyncClient", lambda **kwargs: FakeClient()
    )

    ready, detail = asyncio.run(builder.prepare_preview())

    assert ready is True
    assert "public seed 1101" in detail
    assert observed["health_url"] == "http://rootless-buildkit:18080/healthz"
    assert observed["body"] == {
        "schema_version": 1,
        "run_id": "preview-preview-test",
        "seed": 1101,
        "now": "2026-01-15T12:00:00Z",
        "fixture_path": "/bench-fixtures/1101.json",
    }


def test_builder_exports_exact_last_successful_source(tmp_path: Path) -> None:
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    for relative in REQUIRED_PATHS:
        path = workspace / relative
        if relative in {"frontend", "backend"}:
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder")
            if relative.startswith("scripts/"):
                path.chmod(0o755)

    class FakeBuilder(CandidateBuilder):
        async def command(self, *args: str, timeout: float = 600) -> tuple[int, str]:
            del timeout
            if args[0] == "save":
                Path(args[2]).write_bytes(b"isolated-image-archive")
            return 0, "ok"

    builder = FakeBuilder(
        BuilderConfig(
            token="token",
            workspace=workspace,
            artifacts=tmp_path / "artifacts",
            docker_host="tcp://rootless-buildkit:2375",
            max_builds=20,
            run_id="finalize-test",
            admin_token="admin",
        )
    )
    builder.last_image = "websitebench-candidate:finalize-test-1"
    builder.last_source_digest = builder.source_digest()
    builder.last_build_seconds = 12.5

    manifest = asyncio.run(builder.finalize())

    assert manifest["status"] == "exported"
    assert manifest["source_sha256"] == builder.source_digest()
    assert len(manifest["archive_sha256"]) == 64
    (workspace / "README.md").write_text("changed after preview")
    changed = asyncio.run(builder.finalize())
    assert changed["status"] == "source_changed_after_preview"


def test_model_proxy_allows_only_exact_configured_host_and_port() -> None:
    proxy = ModelProxy("api.openai.com", 443)

    assert proxy.allowed("api.openai.com", 443)
    assert proxy.allowed("API.OPENAI.COM.", 443)
    assert not proxy.allowed("api.openai.com.evil.test", 443)
    assert not proxy.allowed("api.openai.com", 80)


def test_prepare_run_exports_only_public_task_material(tmp_path: Path) -> None:
    run_dir = prepare_run(
        site="northstar-market",
        track="core",
        model="gpt-5.6-sol",
        thinking_level="xhigh",
        output_root=tmp_path,
    )
    task = json.loads((run_dir / "task.json").read_text())

    assert task["budget"]["browser_actions"] == 1000
    assert task["budget"]["candidate_builds"] == 20
    assert task["agent"]["model"] == "gpt-5.6-sol"
    assert (run_dir / "public" / "PRD.md").is_file()
    assert (run_dir / "schemas" / "fixture.schema.json").is_file()
    assert not (run_dir / "reference").exists()
    assert not (run_dir / "judge").exists()
    assert (run_dir / "secrets.env").stat().st_mode & 0o077 == 0
    assert (run_dir / "human-interventions.jsonl").is_file()
    assert "MODEL_API_HOST=api.openai.com" in (run_dir / "secrets.env").read_text()


def test_candidate_runtime_resource_parsing_and_scoped_names() -> None:
    assert CandidateRuntime.parse_memory("12.5MiB / 1GiB") == int(12.5 * 1024 * 1024)
    assert CandidateRuntime.parse_memory("800kB / 1GB") == 800_000
    assert safe_name("Northstar Core Run_01") == "northstar-core-run-01"


def test_candidate_runtime_blocks_every_hard_source_policy_finding(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    candidate = run_dir / "candidate"
    candidate.mkdir(parents=True)
    (run_dir / "eval").mkdir()
    for relative in REQUIRED_PATHS:
        path = candidate / relative
        if relative in {"frontend", "backend"}:
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder")
    (candidate / "frontend" / "index.html").write_text(
        '<iframe src="http://reference-app:8080"></iframe>'
    )
    runtime = CandidateRuntime(
        run_dir=run_dir,
        repository_root=REPO_ROOT,
        project="policy-test",
    )

    with pytest.raises(RuntimeError, match="candidate source policy failed"):
        runtime.build_and_start()

    findings = json.loads((run_dir / "eval" / "source-policy.json").read_text())
    assert {finding["code"] for finding in findings} >= {"IFRAME", "REFERENCE_ORIGIN"}


def test_docker_preflight_requires_daemon_and_compose_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clawbench.web2code.run.shutil.which", lambda _: "/usr/bin/docker")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="28.0.4\n", stderr=""),
            subprocess.CompletedProcess([], 1, stdout="", stderr="unknown command: compose"),
        ]
    )
    monkeypatch.setattr("clawbench.web2code.run.subprocess.run", lambda *args, **kwargs: next(responses))

    ready, detail = docker_ready()

    assert ready is False
    assert "Compose v2" in detail


def test_unavailable_container_runtime_writes_infrastructure_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = prepare_run(
        site="northstar-market",
        track="core",
        model="gpt-5.6-sol",
        thinking_level="xhigh",
        output_root=tmp_path,
    )
    monkeypatch.setattr(
        "clawbench.web2code.run.docker_ready",
        lambda: (False, "Docker daemon is unavailable for test"),
    )

    assert run_pilot(run_dir) == 2
    result = json.loads((run_dir / "eval" / "evaluation-result.json").read_text())
    metadata = json.loads((run_dir / "run-meta.json").read_text())
    assert result["status"] == "infrastructure_error"
    assert result["hard_failures"] == []
    assert metadata["status"] == "infrastructure_error"
    assert (run_dir / "eval" / "failure-report.md").is_file()


def test_hitl_log_is_hash_chained_and_limited_to_messages(tmp_path: Path) -> None:
    log = HumanInterventionLog(tmp_path / "human-interventions.jsonl", max_messages=2)
    first = log.append(category="debug-direction", message="Inspect cart merge after login")
    second = log.append(
        category="missing-feature", message="Check reset-token reuse", final=True
    )
    log.validate()
    assert second["previous_hash"] == first["hash"]
    with pytest.raises(ValueError, match="budget exhausted"):
        log.append(category="test-suggestion", message="One message too many")
