"""Registry-driven WebsiteBench single-run preparation and orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .attempts import (
    AttemptJournal,
    AttemptOutcome,
    AttemptStage,
    OutcomeKind,
    classify_failure,
    retry_advice,
    validate_facts,
)
from .candidate import CandidateRuntime, CandidateRuntimeConfig, read_env, safe_name
from .hitl import ALLOWED_CATEGORIES, HumanInterventionLog
from .registry import RegistryValidationError, SiteRegistry, secret_environment, sha256_value, write_run_manifest
from .reporting import build_result, validate_result, write_reports
from .scoring import score_evaluation
from .topology import validate_compose_topology


def repository_root() -> Path:
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / "websitebench" / "registry.yaml").is_file():
        return checkout
    raise RuntimeError("clawbench-web2code requires a checkout containing websitebench/registry.yaml")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _variant_instruction(registry: SiteRegistry, site_id: str) -> str:
    for path in registry.variant_specs():
        value = __import__("yaml").safe_load(path.read_text(encoding="utf-8"))
        if value.get("site_id") == site_id:
            return str(value["instruction"])
    return "Reconstruct the complete browser-observable application and its documented persistent behavior."


def _assert_no_public_leak(
    run_dir: Path,
    *,
    private_paths: list[Path],
    private_seed_values: Mapping[str, int],
    secret_values: Mapping[str, str],
) -> None:
    public_files = [path for root in (run_dir / "public", run_dir / "schemas") for path in root.rglob("*") if path.is_file()]
    public_files.append(run_dir / "task.json")
    candidate_content_files = [
        path for path in public_files if path == run_dir / "task.json" or (run_dir / "public") in path.parents
    ]
    combined = b"\n".join(path.read_bytes() for path in candidate_content_files)
    combined_text = combined.decode("utf-8", errors="ignore")
    if "websitebench.run-manifest.v1" in combined_text or "run-manifest." in combined_text:
        raise RegistryValidationError("trusted run manifest leaked into candidate-visible export")
    for visibility, seed in private_seed_values.items():
        seed_reference = re.compile(
            rf"(?:\b{seed}\.json\b|[\"']?(?:seed|id)[\"']?\s*:\s*[\"']?{seed}(?!\d))",
            re.IGNORECASE,
        )
        if seed_reference.search(combined_text):
            raise RegistryValidationError(
                f"private {visibility} seed leaked into candidate-visible export"
            )
    for path in private_paths:
        if str(path) in combined_text:
            raise RegistryValidationError(f"private host path leaked into public export: {path}")
        private_files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        for private_file in private_files:
            payload = private_file.read_bytes()
            if len(payload) >= 16 and payload in combined:
                raise RegistryValidationError(
                    f"private input content leaked into public export: {private_file.name}"
                )
    for name, value in secret_values.items():
        if value and value in combined_text:
            raise RegistryValidationError(f"secret value for {name} leaked into public export")
    listing = {
        "schema_version": "websitebench.public-export.v1",
        "files": [
            {
                "path": path.relative_to(run_dir).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
            for path in sorted(public_files)
        ],
    }
    _write_json(run_dir / "public-export.json", listing)


def prepare_run(
    *,
    site: str,
    track: str,
    model: str,
    thinking_level: str,
    output_root: Path,
    budget_override: Mapping[str, int] | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    attempt_id: str | None = None,
    attempt_number: int = 1,
) -> Path:
    """Resolve one site and export only candidate-entitled task material."""

    root = repository_root()
    registry = SiteRegistry.default(root)
    resolved = registry.resolve(site)
    manifest = dict(resolved.manifest)
    if track not in manifest["tracks"] or not manifest["tracks"][track]["enabled"]:
        raise ValueError(f"track {track!r} is not enabled for {site}")
    model_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_api = urlsplit(model_base_url)
    if model_api.scheme not in {"http", "https"} or not model_api.hostname:
        raise ValueError("OPENAI_BASE_URL must be an absolute HTTP(S) URL")
    model_api_port = model_api.port or (443 if model_api.scheme == "https" else 80)
    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{site}-{track}-{stamp}-{secrets.token_hex(4)}"
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    job_id = job_id or run_id
    attempt_id = attempt_id or f"{run_id}.attempt-{attempt_number}"
    run_dir = (output_root / run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name in ("candidate", "agent", "browser", "builds", "eval", "attempts"):
        (run_dir / name).mkdir(mode=0o777)
    shutil.copytree(resolved.manifest_path.parent, run_dir / "public")
    shutil.copytree(root / "websitebench" / "schemas", run_dir / "schemas")
    run_manifest_path = write_run_manifest(resolved, run_dir / "trusted")
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))

    budget = dict(manifest["agent_budget"])
    if budget_override:
        unknown = set(budget_override) - set(budget)
        if unknown:
            raise ValueError(f"unknown budget fields: {sorted(unknown)}")
        budget.update({key: int(value) for key, value in budget_override.items()})
    driver = run_manifest["driver"]
    agent = {**manifest["pilot_agent"], "model": model, "thinking_level": thinking_level}
    task = {
        "schema_version": "websitebench.task.v2",
        "run_id": run_id,
        "task_id": site,
        "site_id": resolved.site_id,
        "site_version": resolved.site_version,
        "family_id": resolved.family_id,
        "variant_id": resolved.variant_id,
        "instruction": _variant_instruction(registry, site),
        "track": track,
        "target_url": driver["urls"]["target"],
        "mailbox_url": driver["urls"]["mailbox"],
        "public_files": {
            "manifest": "/task/public/manifest.yaml",
            "prd": "/task/public/PRD.md",
            "candidate_contract": "/task/public/candidate-contract.md",
            "smoke_cases": "/task/public/public-smoke-cases.json",
        },
        "budget": budget,
        "browser_gateway": {
            "url": driver["urls"]["browser_gateway"],
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
        "site_id": resolved.site_id,
        "family_id": resolved.family_id,
        "variant_id": resolved.variant_id,
        "split": resolved.split,
        "site_version": resolved.site_version,
        "track": track,
        "model": model,
        "thinking_level": thinking_level,
        "budget": budget,
        "run_manifest_digest": run_manifest["digest"],
        "run_manifest_ref": run_manifest_path.relative_to(run_dir).as_posix(),
        "created_at": utc_now(),
        "status": "prepared",
        "job_id": job_id,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
    }
    _write_json(run_dir / "run-meta.json", metadata)

    mounts_by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for mount in driver["mounts"]:
        mounts_by_kind.setdefault(mount["kind"], []).append(mount)

    def mounted_source(kind: str, *, required: bool = True) -> str:
        matches = mounts_by_kind.get(kind, [])
        if not matches:
            if required:
                raise RegistryValidationError(f"driver requires one {kind} mount")
            return ""
        if len(matches) != 1:
            raise RegistryValidationError(f"driver must declare exactly one {kind} mount")
        return str((resolved.corpus_root / matches[0]["source"]).resolve())

    generated_secrets = {
        "RUN_ID": run_id,
        "RUN_DIR": str(run_dir),
        "BENCH_ADMIN_TOKEN": secrets.token_urlsafe(32),
        "MAILBOX_DELIVERY_TOKEN": secrets.token_urlsafe(32),
        "GATEWAY_TOKEN": secrets.token_urlsafe(32),
        "BUILDER_TOKEN": secrets.token_urlsafe(32),
        "OPENAI_BASE_URL": model_base_url,
        "MODEL_API_HOST": model_api.hostname,
        "MODEL_API_PORT": str(model_api_port),
        "MODEL_NAME": model,
        "THINKING_LEVEL": thinking_level,
        "PUBLIC_SEED": str(resolved.execution_seeds["public"]),
        "PRIVATE_FIXTURE_DIR": mounted_source("private_fixture"),
        "BROWSER_ACTION_BUDGET": str(budget["browser_actions"]),
        "CANDIDATE_BUILD_BUDGET": str(budget["candidate_builds"]),
    }
    optional_mount_environment = {
        "ASSERTIONS_PATH": mounted_source("private_assertions", required=False),
        "REFERENCE_SOURCE_DIR": mounted_source("private_reference", required=False),
        "EVALUATOR_SOURCE_PATH": mounted_source("private_evaluator", required=False),
        "VARIANT_SPEC_PATH": mounted_source("private_variant", required=False),
    }
    generated_secrets.update(
        {name: value for name, value in optional_mount_environment.items() if value}
    )
    generated_secrets.update(secret_environment(resolved))
    generated_secrets.setdefault("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    for key, value in generated_secrets.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"environment value {key} contains a newline")
    secrets_path = run_dir / "secrets.env"
    secrets_path.write_text("".join(f"{key}={value}\n" for key, value in sorted(generated_secrets.items())), encoding="utf-8")
    secrets_path.chmod(0o600)
    (run_dir / "human-interventions.jsonl").touch(mode=0o644)
    AttemptJournal.create(
        run_dir / "attempts" / f"{attempt_id}.json",
        attempt_id=attempt_id,
        run_id=run_id,
        job_id=job_id,
        attempt_number=attempt_number,
    )
    private_paths = [resolved.corpus_root / mount["source"] for mount in driver["mounts"] if mount["kind"].startswith("private_")]
    sensitive_values = {
        key: value
        for key, value in generated_secrets.items()
        if key.endswith("_TOKEN") or key.endswith("_API_KEY")
    }
    _assert_no_public_leak(
        run_dir,
        private_paths=private_paths,
        private_seed_values={
            visibility: seed
            for visibility, seed in resolved.execution_seeds.items()
            if visibility != "public"
        },
        secret_values=sensitive_values,
    )
    return run_dir


def _trusted_manifest(run_dir: Path) -> dict[str, Any]:
    metadata = json.loads((run_dir / "run-meta.json").read_text(encoding="utf-8"))
    path = run_dir / metadata["run_manifest_ref"]
    value = json.loads(path.read_text(encoding="utf-8"))
    digest = value.pop("digest")
    actual = f"sha256:{sha256_value(value)}"
    value["digest"] = digest
    if digest != actual or digest != metadata["run_manifest_digest"]:
        raise RegistryValidationError("trusted run manifest digest mismatch")
    corpus = (repository_root() / "websitebench").resolve()

    def contains_symlink(relative: str) -> bool:
        current = corpus
        for part in Path(relative).parts:
            current /= part
            if current.is_symlink():
                return True
        return False

    recorded_paths = {item["path"] for item in value["inputs"]}
    for item in value["inputs"]:
        path = (corpus / item["path"]).resolve(strict=False)
        if contains_symlink(item["path"]) or corpus not in path.parents or not path.is_file():
            raise RegistryValidationError(
                f"trusted run input is unavailable or escaped its corpus root: {item['path']}"
            )
        payload = path.read_bytes()
        if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise RegistryValidationError(
                f"trusted run input drifted after preparation: {item['path']}"
            )
    for mount in value["driver"].get("mounts", []):
        if contains_symlink(mount["source"]):
            raise RegistryValidationError(
                f"trusted mount gained a symlink after preparation: {mount['source']}"
            )
        source = (corpus / mount["source"]).resolve(strict=False)
        if source.is_dir():
            for child in source.rglob("*"):
                if child.is_symlink():
                    raise RegistryValidationError(
                        f"trusted mount gained a symlink after preparation: {child}"
                    )
                if (
                    not child.is_file()
                    or "__pycache__" in child.parts
                    or child.suffix in {".pyc", ".pyo"}
                    or child.name == ".DS_Store"
                ):
                    continue
                relative = child.relative_to(corpus).as_posix()
                if relative not in recorded_paths:
                    raise RegistryValidationError(
                        f"trusted mount gained an unrecorded input after preparation: {relative}"
                    )
    return value


def _attempt(run_dir: Path) -> AttemptJournal:
    paths = sorted((run_dir / "attempts").glob("*.json"))
    if not paths:
        metadata = json.loads((run_dir / "run-meta.json").read_text())
        identifier = metadata.get("attempt_id", f"{metadata['run_id']}.attempt-1")
        return AttemptJournal.create(
            run_dir / "attempts" / f"{identifier}.json",
            attempt_id=identifier,
            run_id=metadata["run_id"],
            job_id=metadata.get("job_id", metadata["run_id"]),
            attempt_number=int(metadata.get("attempt_number", 1)),
        )
    return AttemptJournal(paths[-1])


def _attempt_number(run_dir: Path) -> int:
    return int(_attempt(run_dir).read()["attempt_number"])


def _host_artifact_path(run_dir: Path, container_path: str) -> Path:
    path = Path(container_path)
    try:
        relative = path.relative_to("/artifacts")
    except ValueError as exc:
        raise RegistryValidationError(
            f"driver evaluator artifact path must be below /artifacts: {container_path}"
        ) from exc
    if not relative.parts or ".." in relative.parts:
        raise RegistryValidationError(f"invalid evaluator artifact path: {container_path}")
    return run_dir / relative


def _compose_command(run_dir: Path) -> list[str]:
    value = _trusted_manifest(run_dir)
    compose = repository_root() / "websitebench" / value["driver"]["compose"]
    return [
        "docker", "compose", "--project-name", safe_name(run_dir.name),
        "--env-file", str(run_dir / "secrets.env"), "-f", str(compose),
    ]


def docker_ready() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "Docker CLI is not installed"
    try:
        daemon = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=15, check=False)
        if daemon.returncode:
            return False, f"Docker daemon is unavailable: {(daemon.stderr or daemon.stdout).strip()}"
        compose = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Docker preflight failed: {exc}"
    if compose.returncode:
        return False, f"Docker Compose v2 is unavailable: {(compose.stderr or compose.stdout).strip()}"
    return True, f"Docker {daemon.stdout.strip()}, Compose {compose.stdout.strip()}"


def finalize_evaluation(run_dir: Path) -> dict[str, Any]:
    """Validate facts, then score and report exclusively on the host."""

    trusted = _trusted_manifest(run_dir)
    driver = trusted["driver"]
    corpus = repository_root() / "websitebench"
    eval_dir = run_dir / "eval"
    facts = json.loads((eval_dir / "facts.json").read_text(encoding="utf-8"))
    valid, failures = validate_facts(facts, corpus / driver["scoring"]["facts_schema"])
    if not valid:
        raise ValueError("facts validation failed: " + "; ".join(failures))
    resource_path = eval_dir / "resource-facts.json"
    if resource_path.exists():
        resource_facts = json.loads(resource_path.read_text())
        latency = facts.get("efficiency", {}).get("p95_latency_ms_at_10_concurrent")
        if latency is not None:
            resource_facts["resources"]["p95_latency_ms"] = latency
        facts["resources"] = resource_facts["resources"]
        facts["efficiency"] = {
            **facts.get("efficiency", {}),
            **resource_facts["efficiency"],
        }
    facts["usage"] = _usage_facts(run_dir)
    scoring = json.loads((corpus / driver["scoring"]["policy"]).read_text(encoding="utf-8"))
    scored = score_evaluation(facts, scoring)
    metadata = json.loads((run_dir / "run-meta.json").read_text())
    result = build_result(
        run={
            "run_id": metadata["run_id"], "site_id": metadata["site_id"],
            "site_version": metadata["site_version"], "track": metadata["track"],
            "started_at": metadata["created_at"], "finished_at": utc_now(),
        },
        scored=scored,
        facts=facts,
    )
    validate_result(result, corpus / driver["scoring"]["result_schema"])
    write_reports(result, eval_dir)
    return result


def _usage_facts(run_dir: Path) -> dict[str, int]:
    agent_exit = run_dir / "agent" / "exit.json"
    agent = json.loads(agent_exit.read_text(encoding="utf-8")) if agent_exit.is_file() else {}
    input_tokens = int(agent.get("input_tokens", 0))
    output_tokens = int(agent.get("output_tokens", agent.get("tokens", 0)))
    actions_path = run_dir / "browser" / "actions.jsonl"
    browser_actions = (
        sum(bool(line.strip()) for line in actions_path.read_text(encoding="utf-8").splitlines())
        if actions_path.is_file()
        else 0
    )
    image_manifest = run_dir / "builds" / "final-image.json"
    image = (
        json.loads(image_manifest.read_text(encoding="utf-8"))
        if image_manifest.is_file()
        else {}
    )
    interventions = run_dir / "human-interventions.jsonl"
    human_messages = (
        sum(bool(line.strip()) for line in interventions.read_text(encoding="utf-8").splitlines())
        if interventions.is_file()
        else 0
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "browser_actions": browser_actions,
        "candidate_builds": int(image.get("builds_used", 0)),
        "human_messages": human_messages,
        "human_minutes": 0,
    }


def _empty_facts(code: str, message: str, *, infrastructure: bool) -> dict[str, Any]:
    return {
        "schema_version": "websitebench.facts.v1", "visual": [], "interactions": [],
        "journeys": [], "robustness": [], "efficiency": {},
        "hard_failures": [] if infrastructure else [{"code": code, "message": message, "evidence_ids": []}],
        "failures": [], "evidence": [], "seeds": [],
        "versions": {"protocol": "websitebench.facts.v1"},
    }


def write_hard_failure(
    run_dir: Path,
    code: str,
    message: str,
    *,
    stage: AttemptStage = AttemptStage.CANDIDATE_BUILD,
    process_exit: Mapping[str, Any] | None = None,
) -> None:
    metadata_path = run_dir / "run-meta.json"
    metadata = json.loads(metadata_path.read_text())
    facts = _empty_facts(code, message, infrastructure=False)
    (run_dir / "eval" / "facts.json").write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n")
    result = finalize_evaluation(run_dir)
    classified = classify_failure(
        stage=stage,
        reason_code=code,
        message=message,
        attempt_number=_attempt_number(run_dir),
    )
    outcome = AttemptOutcome(
        kind=classified.kind,
        reason_code=classified.reason_code,
        stage=classified.stage,
        message=classified.message,
        retry=classified.retry,
        result_ref="eval/evaluation-result.json",
        facts_valid=True,
    )
    journal = _attempt(run_dir)
    journal.finish(
        outcome,
        evidence={"message": message},
        process_exit=process_exit,
        facts_validation={"valid": True, "errors": []},
    )
    metadata["status"] = outcome.kind.value
    metadata["result_ref"] = "eval/evaluation-result.json"
    _write_json(metadata_path, metadata)
    del result


def write_infrastructure_error(run_dir: Path, code: str, message: str) -> None:
    """Persist attribution; retain a clearly non-candidate legacy diagnostic result."""

    metadata_path = run_dir / "run-meta.json"
    metadata = json.loads(metadata_path.read_text())
    journal = _attempt(run_dir)
    current_stage = AttemptStage(journal.read()["stage"])
    outcome = classify_failure(
        stage=current_stage,
        reason_code=code,
        message=message,
        attempt_number=_attempt_number(run_dir),
    )
    journal.finish(outcome, evidence={"message": message})
    # Historical result-v1 readers expect a diagnostic file for host preflight
    # failures.  It is explicitly infrastructure_error and batch summaries never
    # include it as a candidate score.
    trusted = _trusted_manifest(run_dir)
    driver = trusted["driver"]
    corpus = repository_root() / "websitebench"
    facts = _empty_facts(code, message, infrastructure=True)
    scoring = json.loads((corpus / driver["scoring"]["policy"]).read_text())
    scored = score_evaluation(facts, scoring)
    scored["status"] = "infrastructure_error"
    result = build_result(
        run={"run_id": metadata["run_id"], "site_id": metadata["site_id"], "site_version": metadata["site_version"], "track": metadata["track"], "started_at": metadata["created_at"], "finished_at": utc_now()},
        scored=scored,
        facts=facts,
    )
    validate_result(result, corpus / driver["scoring"]["result_schema"])
    write_reports(result, run_dir / "eval")
    metadata["status"] = outcome.kind.value
    metadata["infrastructure_error"] = {"code": code, "message": message}
    metadata["attempt_ref"] = _attempt(run_dir).path.relative_to(run_dir).as_posix()
    _write_json(metadata_path, metadata)


def _record_evaluator_failure(run_dir: Path, code: str, message: str, *, evaluator_exit: int | None) -> int:
    journal = _attempt(run_dir)
    value = journal.read()
    if AttemptStage(value["stage"]) is AttemptStage.EVALUATOR:
        journal.transition(
            AttemptStage.FACTS_VALIDATION,
            evidence={"valid": False, "reason": message},
            process_exit={"process": "evaluator", "exit_code": evaluator_exit},
            facts_validation={"valid": False, "errors": [message]},
        )
    outcome = classify_failure(
        stage=AttemptStage.FACTS_VALIDATION,
        reason_code=code,
        message=message,
        attempt_number=_attempt_number(run_dir),
    )
    journal.finish(
        outcome,
        facts_validation={"valid": False, "errors": [message]},
    )
    metadata_path = run_dir / "run-meta.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["status"] = outcome.kind.value
    metadata["evaluation_exit_code"] = evaluator_exit
    _write_json(metadata_path, metadata)
    return 3


def run_pilot(run_dir: Path, *, keep_containers: bool = False) -> int:
    trusted = _trusted_manifest(run_dir)
    driver = trusted["driver"]
    roles = driver["service_roles"]
    ready, detail = docker_ready()
    if not ready:
        write_infrastructure_error(run_dir, "CONTAINER_RUNTIME_UNAVAILABLE", detail)
        return 2
    if not os.environ.get("OPENAI_API_KEY"):
        write_infrastructure_error(run_dir, "MODEL_CREDENTIAL_UNAVAILABLE", "OPENAI_API_KEY is required for a non-dry run")
        return 2
    command = _compose_command(run_dir)

    def stop_compose() -> None:
        if not keep_containers:
            subprocess.run([*command, "down"], check=False)

    build_services = [roles[name] for name in ("reference", "mailbox_query", "mailbox_delivery", "build_daemon", "candidate_builder", "browser_gateway", "model_proxy")]
    started = subprocess.run([*command, "--profile", "build", "up", "--build", "--detach", "--wait", *build_services], check=False)
    if started.returncode:
        try:
            write_infrastructure_error(run_dir, "BENCHMARK_TOPOLOGY_START_FAILED", f"reference/build topology exited {started.returncode}")
        finally:
            stop_compose()
        return started.returncode
    journal = _attempt(run_dir)
    journal.transition(AttemptStage.AGENT)
    agent = subprocess.run([*command, "--profile", "build", "run", "--rm", roles["agent"]], check=False)
    if agent.returncode:
        try:
            write_hard_failure(
                run_dir,
                "AGENT_FAILED",
                f"candidate-building Agent exited {agent.returncode}",
                stage=AttemptStage.AGENT,
                process_exit={"process": "agent", "exit_code": agent.returncode},
            )
        finally:
            stop_compose()
        return agent.returncode
    journal.transition(AttemptStage.CANDIDATE_FINALIZE)
    finalize_script = """
import json, os, urllib.request
request = urllib.request.Request('http://127.0.0.1:7100/v1/finalize', data=b'{}', headers={'Authorization': f\"Bearer {os.environ['BUILDER_TOKEN']}\"}, method='POST')
with urllib.request.urlopen(request, timeout=700) as response: result = json.load(response)
print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result.get('status') == 'exported' else 1)
"""
    finalized = subprocess.run([*command, "exec", "--no-TTY", roles["candidate_builder"], "python", "-c", finalize_script], capture_output=True, text=True, check=False)
    if finalized.returncode:
        try:
            write_hard_failure(
                run_dir,
                "CANDIDATE_FINALIZE_FAILED",
                (finalized.stdout + finalized.stderr)[-12000:] or "finalize failed",
                stage=AttemptStage.CANDIDATE_FINALIZE,
                process_exit={"process": "candidate_finalize", "exit_code": finalized.returncode},
            )
        finally:
            stop_compose()
        return finalized.returncode
    journal.transition(AttemptStage.SOURCE_POLICY)
    build_plane = [roles[name] for name in ("candidate_builder", "browser_gateway", "build_daemon", "model_proxy")]
    stopped = subprocess.run([*command, "stop", "--timeout", "20", *build_plane], check=False)
    removed = subprocess.run([*command, "rm", "--force", *build_plane], check=False)
    if stopped.returncode or removed.returncode:
        try:
            write_infrastructure_error(run_dir, "BUILD_PLANE_TEARDOWN_FAILED", "could not remove build/model preview services before evaluation")
        finally:
            stop_compose()
        return stopped.returncode or removed.returncode
    config = CandidateRuntimeConfig.from_run_manifest(trusted, corpus_root=repository_root() / "websitebench")
    runtime = CandidateRuntime(run_dir=run_dir, project=safe_name(run_dir.name), config=config)
    try:
        journal.transition(AttemptStage.CANDIDATE_BUILD)
        try:
            runtime.build_and_start()
        except RuntimeError as exc:
            write_hard_failure(run_dir, "CANDIDATE_BUILD_OR_START_FAILED", str(exc), stage=AttemptStage.CANDIDATE_BUILD)
            return 1
        journal.transition(AttemptStage.CANDIDATE_START)
        journal.transition(AttemptStage.CANDIDATE_HEALTH)
        runtime.start_resource_monitor()
        journal.transition(AttemptStage.EVALUATOR)
        evaluator_environment = os.environ.copy()
        runtime_values = read_env(run_dir / "secrets.env")
        evaluator_arguments = [
            *command,
            "--profile",
            driver["evaluator"]["profile"],
            "run",
            "--rm",
        ]
        for name, declared in sorted(driver["evaluator"]["environment"].items()):
            if declared == "runtime-secret":
                if name not in runtime_values:
                    raise RegistryValidationError(
                        f"evaluator runtime secret {name} is unavailable"
                    )
                evaluator_environment[name] = runtime_values[name]
            else:
                evaluator_environment[name] = str(declared)
            evaluator_arguments.extend(["--env", name])
        evaluator_arguments.append(roles["evaluator"])
        evaluator_arguments.extend(driver["evaluator"]["argv"])
        evaluation = subprocess.run(
            evaluator_arguments,
            env=evaluator_environment,
            check=False,
        )
        runtime.finish_resource_monitor()
        facts_path = _host_artifact_path(run_dir, driver["evaluator"]["facts_path"])
        if not facts_path.exists():
            return _record_evaluator_failure(run_dir, "EVALUATOR_FACTS_MISSING", f"evaluator exited {evaluation.returncode} without facts", evaluator_exit=evaluation.returncode)
        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _record_evaluator_failure(run_dir, "EVALUATOR_FACTS_UNREADABLE", str(exc), evaluator_exit=evaluation.returncode)
        valid, errors = validate_facts(facts, repository_root() / "websitebench" / driver["scoring"]["facts_schema"])
        if not valid:
            return _record_evaluator_failure(run_dir, "EVALUATOR_FACTS_INVALID", "; ".join(errors), evaluator_exit=evaluation.returncode)
        try:
            scoring_policy = json.loads(
                (
                    repository_root()
                    / "websitebench"
                    / driver["scoring"]["policy"]
                ).read_text(encoding="utf-8")
            )
            score_evaluation(facts, scoring_policy)
        except (KeyError, TypeError, ValueError) as exc:
            return _record_evaluator_failure(
                run_dir,
                "EVALUATOR_FACTS_INVALID",
                f"facts violate scoring semantics: {exc}",
                evaluator_exit=evaluation.returncode,
            )
        journal.transition(AttemptStage.FACTS_VALIDATION, evidence={"valid": True}, process_exit={"process": "evaluator", "exit_code": evaluation.returncode}, facts_validation={"valid": True, "errors": []})
        journal.transition(AttemptStage.HOST_SCORING)
        try:
            final_result = finalize_evaluation(run_dir)
        except (KeyError, OSError, TypeError, ValidationError, ValueError) as exc:
            return _record_evaluator_failure(
                run_dir,
                "EVALUATOR_FACTS_INVALID",
                f"facts cannot produce a valid result v1: {exc}",
                evaluator_exit=evaluation.returncode,
            )
        journal.transition(AttemptStage.RESULT_VALIDATION)
        journal.transition(AttemptStage.FINALIZED)
        attempt_number = _attempt_number(run_dir)
        outcome = AttemptOutcome(OutcomeKind.SCORED, "RESULT_FINALIZED", AttemptStage.FINALIZED, "schema-valid facts were scored by the host", retry_advice(OutcomeKind.SCORED, "RESULT_FINALIZED", attempt_number), "eval/evaluation-result.json", True)
        journal.finish(outcome)
        metadata_path = run_dir / "run-meta.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["status"] = "scored"
        metadata["evaluation_exit_code"] = evaluation.returncode
        metadata["result_ref"] = "eval/evaluation-result.json"
        _write_json(metadata_path, metadata)
        return 0 if final_result["status"] == "passed" else 1
    finally:
        if not keep_containers:
            runtime.stop()
            stop_compose()


def validate_command(site: str | None = None) -> dict[str, Any]:
    registry = SiteRegistry.default(repository_root())
    if site is None:
        for site_id in registry.site_ids:
            resolved = registry.resolve(site_id)
            validate_compose_topology(
                resolved.compose_path,
                roles=resolved.service_roles,
                network_roles=resolved.driver["networks"],
            )
        return registry.validate_corpus()
    resolved = registry.resolve(site)
    validate_compose_topology(
        resolved.compose_path,
        roles=resolved.service_roles,
        network_roles=resolved.driver["networks"],
    )
    return {"site_id": resolved.site_id, "family_id": resolved.family_id, "variant_id": resolved.variant_id, "split": resolved.split, "registry_digest": resolved.registry_digest}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-web2code")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="resolve a site and validate contracts/topology")
    validate.add_argument("--site")
    validate.add_argument("--all", action="store_true")
    pilot = subparsers.add_parser("pilot", help="prepare and optionally execute one run")
    pilot.add_argument("--site", required=True)
    pilot.add_argument("--track", choices=("core", "hitl"), default="core")
    pilot.add_argument("--model", default="gpt-5.5-codex")
    pilot.add_argument("--thinking-level", default="xhigh")
    pilot.add_argument("--output-root", type=Path, default=Path("web2code-output"))
    pilot.add_argument("--dry-run", action="store_true")
    pilot.add_argument("--keep-containers", action="store_true")
    hitl = subparsers.add_parser("hitl-message")
    hitl.add_argument("run_dir", type=Path)
    hitl.add_argument("--category", choices=sorted(ALLOWED_CATEGORIES), required=True)
    hitl.add_argument("--message", required=True)
    hitl.add_argument("--final", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        if bool(args.site) == bool(args.all):
            raise SystemExit("validate requires exactly one of --site or --all")
        print(json.dumps(validate_command(None if args.all else args.site), indent=2, sort_keys=True))
        return 0
    if args.command == "hitl-message":
        log = HumanInterventionLog(args.run_dir / "human-interventions.jsonl")
        print(json.dumps(log.append(category=args.category, message=args.message, final=args.final), indent=2))
        return 0
    run_dir = prepare_run(site=args.site, track=args.track, model=args.model, thinking_level=args.thinking_level, output_root=args.output_root)
    print(run_dir)
    return 0 if args.dry_run else run_pilot(run_dir, keep_containers=args.keep_containers)


if __name__ == "__main__":
    raise SystemExit(main())
