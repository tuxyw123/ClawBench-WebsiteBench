"""Host-side Web2Code2Web pilot preparation and orchestration CLI."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from .candidate import CandidateRuntime, safe_name
from .contracts import validate_site_contract
from .hitl import ALLOWED_CATEGORIES, HumanInterventionLog
from .reporting import build_result, validate_result, write_reports
from .scoring import score_evaluation
from .topology import validate_compose_topology


def repository_root() -> Path:
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / "websitebench").is_dir():
        return checkout
    raise RuntimeError("clawbench-web2code must currently run from a source checkout")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_run(
    *,
    site: str,
    track: str,
    model: str,
    thinking_level: str,
    output_root: Path,
) -> Path:
    root = repository_root()
    site_root = root / "websitebench" / site
    manifest_path = site_root / "public" / "manifest.yaml"
    manifest = validate_site_contract(manifest_path, require_fixtures=True)
    if track not in manifest["tracks"]:
        raise ValueError(f"unknown track: {track}")
    model_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_api = urlsplit(model_base_url)
    if model_api.scheme not in {"http", "https"} or not model_api.hostname:
        raise ValueError("OPENAI_BASE_URL must be an absolute HTTP(S) URL")
    model_api_port = model_api.port or (443 if model_api.scheme == "https" else 80)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{site}-{track}-{stamp}-{secrets.token_hex(4)}"
    run_dir = (output_root / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name in ("candidate", "agent", "browser", "builds", "eval"):
        path = run_dir / name
        path.mkdir(mode=0o777)
    shutil.copytree(site_root / "public", run_dir / "public")
    shutil.copytree(root / "websitebench" / "schemas", run_dir / "schemas")
    agent = {**manifest["pilot_agent"], "model": model, "thinking_level": thinking_level}
    task = {
        "schema_version": "websitebench.task.v1",
        "run_id": run_id,
        "task_id": site,
        "site_id": manifest["site_id"],
        "site_version": manifest["site_version"],
        "track": track,
        "target_url": "http://reference-app:8080",
        "mailbox_url": "http://mailbox:8025",
        "public_files": {
            "manifest": "/task/public/manifest.yaml",
            "prd": "/task/public/PRD.md",
            "candidate_contract": "/task/public/candidate-contract.md",
            "smoke_cases": "/task/public/public-smoke-cases.json",
        },
        "budget": manifest["agent_budget"],
        "browser_gateway": {
            "url": "http://browser-gateway:7000",
            "tool_name": "controlled_browser",
            "reference_access": "continuous",
        },
        "candidate_workspace": "/workspace/candidate",
        "agent": agent,
        "issued_at": utc_now(),
    }
    task_schema = json.loads((root / "websitebench" / "schemas" / "task.schema.json").read_text())
    Draft202012Validator(task_schema, format_checker=FormatChecker()).validate(task)
    _write_json(run_dir / "task.json", task)
    metadata = {
        "schema_version": "websitebench.run.v1",
        "run_id": run_id,
        "site": site,
        "site_version": manifest["site_version"],
        "track": track,
        "model": model,
        "thinking_level": thinking_level,
        "created_at": utc_now(),
        "status": "prepared",
    }
    _write_json(run_dir / "run-meta.json", metadata)
    secrets_values = {
        "RUN_ID": run_id,
        "RUN_DIR": str(run_dir),
        "BENCH_ADMIN_TOKEN": secrets.token_urlsafe(32),
        "MAILBOX_DELIVERY_TOKEN": secrets.token_urlsafe(32),
        "GATEWAY_TOKEN": secrets.token_urlsafe(32),
        "BUILDER_TOKEN": secrets.token_urlsafe(32),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "OPENAI_BASE_URL": model_base_url,
        "MODEL_API_HOST": model_api.hostname,
        "MODEL_API_PORT": str(model_api_port),
        "MODEL_NAME": model,
        "THINKING_LEVEL": thinking_level,
    }
    secrets_path = run_dir / "secrets.env"
    secrets_path.write_text(
        "".join(f"{key}={value}\n" for key, value in secrets_values.items()), encoding="utf-8"
    )
    secrets_path.chmod(0o600)
    (run_dir / "human-interventions.jsonl").touch(mode=0o644)
    return run_dir


def _compose_command(run_dir: Path) -> list[str]:
    root = repository_root()
    return [
        "docker",
        "compose",
        "--project-name",
        safe_name(run_dir.name),
        "--env-file",
        str(run_dir / "secrets.env"),
        "-f",
        str(root / "websitebench" / "northstar-market" / "docker-compose.yml"),
    ]


def docker_ready() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "Docker CLI is not installed"
    try:
        daemon = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if daemon.returncode:
            return False, f"Docker daemon is unavailable: {(daemon.stderr or daemon.stdout).strip()}"
        compose = subprocess.run(
            ["docker", "compose", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Docker preflight failed: {exc}"
    if compose.returncode:
        return False, f"Docker Compose v2 is unavailable: {(compose.stderr or compose.stdout).strip()}"
    return True, f"Docker {daemon.stdout.strip()}, Compose {compose.stdout.strip()}"


def finalize_evaluation(run_dir: Path) -> dict[str, Any]:
    root = repository_root()
    eval_dir = run_dir / "eval"
    facts = json.loads((eval_dir / "facts.json").read_text())
    resource_path = eval_dir / "resource-facts.json"
    if resource_path.exists():
        resource_facts = json.loads(resource_path.read_text())
        facts["resources"] = resource_facts["resources"]
        facts["efficiency"] = resource_facts["efficiency"]
    scoring = json.loads(
        (root / "websitebench" / "northstar-market" / "public" / "scoring.json").read_text()
    )
    scored = score_evaluation(facts, scoring)
    metadata = json.loads((run_dir / "run-meta.json").read_text())
    result = build_result(
        run={
            "run_id": metadata["run_id"],
            "site_id": "northstar-market",
            "site_version": metadata["site_version"],
            "track": metadata["track"],
            "started_at": metadata["created_at"],
            "finished_at": utc_now(),
        },
        scored=scored,
        facts=facts,
    )
    validate_result(result, root / "websitebench" / "schemas" / "report.schema.json")
    write_reports(result, eval_dir)
    return result


def write_hard_failure(run_dir: Path, code: str, message: str) -> None:
    metadata_path = run_dir / "run-meta.json"
    metadata = json.loads(metadata_path.read_text())
    scoring = json.loads((run_dir / "public" / "scoring.json").read_text())
    facts = {
        "visual": [],
        "interactions": [],
        "journeys": [],
        "robustness": [],
        "efficiency": {},
        "hard_failures": [{"code": code, "message": message, "evidence_ids": []}],
        "failures": [],
        "evidence": [],
        "seeds": [],
        "versions": {"protocol": "websitebench.result.v1"},
    }
    result = build_result(
        run={
            "run_id": metadata["run_id"],
            "site_id": "northstar-market",
            "site_version": metadata["site_version"],
            "track": metadata["track"],
            "started_at": metadata["created_at"],
            "finished_at": utc_now(),
        },
        scored=score_evaluation(facts, scoring),
        facts=facts,
    )
    validate_result(result, run_dir / "schemas" / "report.schema.json")
    write_reports(result, run_dir / "eval")
    metadata["status"] = "hard_failed"
    metadata["hard_failure"] = {"code": code, "message": message}
    _write_json(metadata_path, metadata)


def write_infrastructure_error(run_dir: Path, code: str, message: str) -> None:
    """Write a terminal report without attributing host failure to a candidate."""

    metadata_path = run_dir / "run-meta.json"
    metadata = json.loads(metadata_path.read_text())
    scoring = json.loads((run_dir / "public" / "scoring.json").read_text())
    facts = {
        "visual": [],
        "interactions": [],
        "journeys": [],
        "robustness": [],
        "efficiency": {},
        "hard_failures": [],
        "failures": [
            {
                "id": code.casefold().replace("_", "-"),
                "category": "startup",
                "severity": "critical",
                "summary": "Benchmark infrastructure prevented the run",
                "expected": "Docker Engine and Compose v2 can start the benchmark topology",
                "actual": message,
                "reproduction": ["Run clawbench-web2code pilot on the same host"],
                "evidence_ids": [],
            }
        ],
        "evidence": [],
        "seeds": [],
        "versions": {"protocol": "websitebench.result.v1"},
    }
    scored = score_evaluation(facts, scoring)
    scored["status"] = "infrastructure_error"
    result = build_result(
        run={
            "run_id": metadata["run_id"],
            "site_id": "northstar-market",
            "site_version": metadata["site_version"],
            "track": metadata["track"],
            "started_at": metadata["created_at"],
            "finished_at": utc_now(),
        },
        scored=scored,
        facts=facts,
    )
    validate_result(result, run_dir / "schemas" / "report.schema.json")
    write_reports(result, run_dir / "eval")
    metadata["status"] = "infrastructure_error"
    metadata["infrastructure_error"] = {"code": code, "message": message}
    _write_json(metadata_path, metadata)


def run_pilot(run_dir: Path, *, keep_containers: bool = False) -> int:
    ready, detail = docker_ready()
    if not ready:
        write_infrastructure_error(run_dir, "CONTAINER_RUNTIME_UNAVAILABLE", detail)
        return 2
    if not os.environ.get("OPENAI_API_KEY"):
        write_infrastructure_error(
            run_dir,
            "MODEL_CREDENTIAL_UNAVAILABLE",
            "OPENAI_API_KEY is required for a non-dry pilot",
        )
        return 2
    command = _compose_command(run_dir)
    result = subprocess.run(
        [
            *command,
            "--profile",
            "build",
            "up",
            "--build",
            "--detach",
            "--wait",
            "reference-app",
            "mailbox",
            "mailbox-delivery",
            "rootless-buildkit",
            "candidate-builder",
            "browser-gateway",
            "model-proxy",
        ],
        check=False,
    )
    if result.returncode:
        write_infrastructure_error(
            run_dir,
            "BENCHMARK_TOPOLOGY_START_FAILED",
            f"reference/build topology exited {result.returncode}",
        )
        return result.returncode
    result = subprocess.run(
        [*command, "--profile", "build", "run", "--rm", "agent"], check=False
    )
    metadata_path = run_dir / "run-meta.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["status"] = "agent_completed" if result.returncode == 0 else "agent_failed"
    metadata["agent_exit_code"] = result.returncode
    _write_json(metadata_path, metadata)
    if result.returncode:
        write_hard_failure(run_dir, "AGENT_FAILED", f"candidate-building Agent exited {result.returncode}")
        return result.returncode
    finalize_script = """
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:7100/v1/finalize",
    data=b"{}",
    headers={"Authorization": f"Bearer {os.environ['BUILDER_TOKEN']}"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=700) as response:
    result = json.load(response)
print(json.dumps(result, sort_keys=True))
if result.get("status") != "exported":
    raise SystemExit(1)
"""
    finalized = subprocess.run(
        [*command, "exec", "--no-TTY", "candidate-builder", "python", "-c", finalize_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if finalized.returncode:
        detail = (finalized.stdout + finalized.stderr)[-12_000:]
        write_hard_failure(run_dir, "CANDIDATE_FINALIZE_FAILED", detail or "finalize failed")
        return finalized.returncode
    build_plane = [
        "candidate-builder",
        "browser-gateway",
        "rootless-buildkit",
        "model-proxy",
    ]
    stopped = subprocess.run(
        [*command, "stop", "--timeout", "20", *build_plane], check=False
    )
    removed = subprocess.run(
        [*command, "rm", "--force", *build_plane], check=False
    )
    if stopped.returncode or removed.returncode:
        write_infrastructure_error(
            run_dir,
            "BUILD_PLANE_TEARDOWN_FAILED",
            "could not remove preview/build/model services before final evaluation",
        )
        return stopped.returncode or removed.returncode
    runtime = CandidateRuntime(
        run_dir=run_dir,
        repository_root=repository_root(),
        project=safe_name(run_dir.name),
    )
    try:
        try:
            runtime.build_and_start()
        except RuntimeError as exc:
            write_hard_failure(run_dir, "CANDIDATE_BUILD_OR_START_FAILED", str(exc))
            return 1
        runtime.start_resource_monitor()
        evaluation = subprocess.run(
            [*command, "--profile", "judge", "run", "--rm", "evaluator"], check=False
        )
        runtime.finish_resource_monitor()
        facts_path = run_dir / "eval" / "facts.json"
        if not facts_path.exists():
            write_infrastructure_error(
                run_dir,
                "EVALUATOR_DID_NOT_PRODUCE_FACTS",
                f"evaluator exited {evaluation.returncode} without facts.json",
            )
            return evaluation.returncode or 2
        final_result = finalize_evaluation(run_dir)
        metadata = json.loads(metadata_path.read_text())
        metadata["status"] = "evaluation_completed" if evaluation.returncode == 0 else "evaluation_failed"
        metadata["evaluation_exit_code"] = evaluation.returncode
        _write_json(metadata_path, metadata)
        return 0 if final_result and final_result["status"] == "passed" else (evaluation.returncode or 1)
    finally:
        if not keep_containers:
            runtime.stop()
            subprocess.run([*command, "down"], check=False)


def validate_command(site: str) -> None:
    root = repository_root()
    site_root = root / "websitebench" / site
    validate_site_contract(site_root / "public" / "manifest.yaml", require_fixtures=True)
    validate_compose_topology(site_root / "docker-compose.yml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-web2code")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate public contracts and isolation topology")
    validate.add_argument("--site", default="northstar-market")
    pilot = subparsers.add_parser("pilot", help="prepare and optionally run one candidate build pilot")
    pilot.add_argument("--site", default="northstar-market")
    pilot.add_argument("--track", choices=("core", "hitl"), default="core")
    pilot.add_argument("--model", default="gpt-5.6-sol")
    pilot.add_argument("--thinking-level", default="xhigh")
    pilot.add_argument("--output-root", type=Path, default=Path("web2code-output"))
    pilot.add_argument("--dry-run", action="store_true", help="prepare and validate without starting containers")
    pilot.add_argument("--keep-containers", action="store_true", help="leave corpus and candidate containers running")
    hitl = subparsers.add_parser("hitl-message", help="append one auditable intervention to a running HITL track")
    hitl.add_argument("run_dir", type=Path)
    hitl.add_argument("--category", choices=sorted(ALLOWED_CATEGORIES), required=True)
    hitl.add_argument("--message", required=True)
    hitl.add_argument("--final", action="store_true", help="end the HITL wait after this intervention")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            validate_command(args.site)
            print(f"valid Web2Code2Web site: {args.site}")
            return 0
        if args.command == "hitl-message":
            metadata = json.loads((args.run_dir / "run-meta.json").read_text())
            if metadata["track"] != "hitl":
                raise ValueError("run is not in the HITL track")
            log = HumanInterventionLog(args.run_dir / "human-interventions.jsonl")
            record = log.append(category=args.category, message=args.message, final=args.final)
            print(f"recorded HITL intervention {record['sequence']}/12")
            return 0
        validate_command(args.site)
        run_dir = prepare_run(
            site=args.site,
            track=args.track,
            model=args.model,
            thinking_level=args.thinking_level,
            output_root=args.output_root,
        )
        print(f"prepared run: {run_dir}")
        if args.dry_run:
            return 0
        return run_pilot(run_dir, keep_containers=args.keep_containers)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
