"""Deterministic batch planning and resumable SQLite scheduling."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .attempts import AttemptOutcome, AttemptStage, OutcomeKind, classify_failure
from .registry import SiteRegistry, sha256_value
from .run import prepare_run, repository_root, run_pilot


BATCH_VERSION = "websitebench.batch.v1"
SCHEDULER_STATES = frozenset({"queued", "running", "waiting_for_human", "retry_wait", "terminal"})
MAX_CONCURRENCY = 8
CORE_JOURNEYS = (
    "catalog_observability",
    "account_lifecycle",
    "cart_inventory",
    "checkout_concurrency",
    "orders_terminal",
)


def utc_now(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _tree_digest(root: Path) -> str:
    tracked = _git_value(root, "ls-files", "-co", "--exclude-standard").splitlines()
    entries = []
    for relative in sorted(set(tracked)):
        path = root / relative
        if path.is_file() and not path.is_symlink():
            entries.append((relative, __import__("hashlib").sha256(path.read_bytes()).hexdigest()))
    return sha256_value(entries)


def _image_inputs(root: Path, compose_path: Path) -> dict[str, Any]:
    """Freeze external image refs and local build recipes without a daemon."""

    document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    values: dict[str, Any] = {}
    for service_name, service in sorted(document.get("services", {}).items()):
        if "image" in service:
            values[service_name] = {"image": str(service["image"])}
            continue
        build = service.get("build")
        if build is None:
            continue
        definition = {"context": build} if isinstance(build, str) else dict(build)
        context = (compose_path.parent / definition.get("context", ".")).resolve()
        dockerfile = (context / definition.get("dockerfile", "Dockerfile")).resolve()
        if root.resolve() not in dockerfile.parents:
            raise ValueError(f"Compose Dockerfile escapes repository: {dockerfile}")
        if not dockerfile.is_file():
            raise ValueError(f"Compose Dockerfile does not exist: {dockerfile}")
        values[service_name] = {
            "context": context.relative_to(root).as_posix(),
            "dockerfile": dockerfile.relative_to(root).as_posix(),
            "dockerfile_sha256": __import__("hashlib").sha256(dockerfile.read_bytes()).hexdigest(),
        }
    return values


@dataclass(frozen=True)
class BatchPlan:
    value: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return str(self.value["digest"])

    @property
    def jobs(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.value["jobs"])

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.value))


def verify_frozen_inputs(plan: BatchPlan, registry: SiteRegistry) -> None:
    """Refuse execution when any immutable plan input has drifted."""

    frozen = plan.value["frozen_inputs"]
    selected = tuple(plan.value["selectors"]["resolved_site_ids"])
    root = repository_root()
    prompt_path = root / "websitebench" / "services" / "agent" / "AGENT_PROMPT.md"
    actual = {
        "registry_digest": registry.digest,
        "run_manifests": {
            site: registry.resolve(site).run_manifest()["digest"] for site in selected
        },
        "prompt_sha256": __import__("hashlib").sha256(prompt_path.read_bytes()).hexdigest(),
        "code_commit": _git_value(root, "rev-parse", "HEAD"),
        "source_tree_sha256": _tree_digest(root),
        "compose_inputs": {
            site: __import__("hashlib").sha256(
                registry.resolve(site).compose_path.read_bytes()
            ).hexdigest()
            for site in selected
        },
        "image_inputs": {
            site: _image_inputs(root, registry.resolve(site).compose_path)
            for site in selected
        },
        "public_inputs": {
            site: {
                item["path"]: item["sha256"]
                for item in registry.resolve(site).input_files
                if "/public/" in f"/{item['path']}"
            }
            for site in selected
        },
    }
    drift = [name for name, value in actual.items() if frozen.get(name) != value]
    if drift:
        raise ValueError(
            "batch plan frozen inputs drifted; create a new plan instead of resuming: "
            + ", ".join(sorted(drift))
        )


def create_plan(
    *,
    registry: SiteRegistry,
    site_ids: Iterable[str] | None = None,
    family_id: str | None = None,
    split: str | None = None,
    models: Iterable[str],
    thinking_levels: Iterable[str],
    tracks: Iterable[str],
    repetitions: int,
    concurrency: int = 1,
    budgets: Mapping[str, int] | None = None,
) -> BatchPlan:
    """Expand a frozen, digest-addressed registry matrix."""

    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be between 1 and {MAX_CONCURRENCY}")
    requested_sites = tuple(site_ids or ())
    selector_count = int(bool(requested_sites)) + int(family_id is not None) + int(split is not None)
    if selector_count != 1:
        raise ValueError("batch planning requires exactly one site, family, or split selector")
    models = tuple(sorted(set(models)))
    requested_thinking = set(thinking_levels)
    unknown_thinking = requested_thinking - {"xhigh", "high", "medium", "low"}
    if unknown_thinking:
        raise ValueError(f"unknown thinking levels: {sorted(unknown_thinking)}")
    thinking_levels = tuple(level for level in ("xhigh", "high", "medium", "low") if level in requested_thinking)
    requested_tracks = set(tracks)
    tracks = tuple(track for track in ("core", "hitl") if track in requested_tracks)
    if not models or not thinking_levels or not tracks:
        raise ValueError("model, thinking, and track dimensions cannot be empty")
    if requested_tracks - {"core", "hitl"}:
        raise ValueError(f"unknown tracks: {sorted(requested_tracks - {'core', 'hitl'})}")
    selected = registry.query(site_ids=requested_sites, family_id=family_id, split=split)
    budget = dict(
        budgets
        or {
            "wall_time_seconds": 7200,
            "token_budget": 400000,
            "browser_actions": 400,
            "candidate_builds": 10,
        }
    )
    if set(budget) != {"wall_time_seconds", "token_budget", "browser_actions", "candidate_builds"}:
        raise ValueError("batch budget must freeze all four canonical fields")
    root = repository_root()
    snapshots = {}
    public_inputs = {}
    for site_id in selected:
        resolved = registry.resolve(site_id)
        snapshots[site_id] = resolved.run_manifest()["digest"]
        public_inputs[site_id] = {
            item["path"]: item["sha256"]
            for item in resolved.input_files
            if "/public/" in f"/{item['path']}"
        }
        for track in tracks:
            if track not in resolved.manifest["tracks"] or not resolved.manifest["tracks"][track]["enabled"]:
                raise ValueError(f"site {site_id} does not enable track {track}")
    prompt_path = root / "websitebench" / "services" / "agent" / "AGENT_PROMPT.md"
    frozen = {
        "registry_digest": registry.digest,
        "run_manifests": snapshots,
        "prompt_sha256": __import__("hashlib").sha256(prompt_path.read_bytes()).hexdigest(),
        "code_commit": _git_value(root, "rev-parse", "HEAD"),
        "source_tree_sha256": _tree_digest(root),
        "compose_inputs": {
            site: __import__("hashlib").sha256(registry.resolve(site).compose_path.read_bytes()).hexdigest()
            for site in selected
        },
        "image_inputs": {
            site: _image_inputs(root, registry.resolve(site).compose_path)
            for site in selected
        },
        "budgets": budget,
        "public_inputs": public_inputs,
    }
    jobs = []
    ordinal = 0
    frozen_inputs_digest = sha256_value(frozen)
    for site in selected:
        resolved = registry.resolve(site)
        public_seed = int(resolved.execution_seeds["public"])
        hidden_seed = int(resolved.execution_seeds["hidden"])
        for model in models:
            for thinking in thinking_levels:
                for track in tracks:
                    for repetition in range(1, repetitions + 1):
                        identity = {
                            "site_id": site,
                            "model": model,
                            "thinking_level": thinking,
                            "track": track,
                            "repetition": repetition,
                        }
                        job_id = f"job-{sha256_value({**identity, 'frozen_inputs_digest': frozen_inputs_digest})[:20]}"
                        executions = [
                            {
                                "execution_id": f"{job_id}.{journey}.{seed}",
                                "journey_id": journey,
                                "seed": seed,
                                "visibility": visibility,
                                "max_score": 5,
                            }
                            for journey in CORE_JOURNEYS
                            for seed, visibility in ((public_seed, "public"), (hidden_seed, "hidden"))
                        ]
                        jobs.append({"job_id": job_id, "ordinal": ordinal, **identity, "executions": executions})
                        ordinal += 1
    body = {
        "schema_version": BATCH_VERSION,
        "selectors": {
            "site_ids": list(requested_sites),
            "family_id": family_id,
            "split": split,
            "resolved_site_ids": list(selected),
        },
        "dimensions": {
            "models": list(models),
            "thinking_levels": list(thinking_levels),
            "tracks": list(tracks),
            "repetitions": repetitions,
            "concurrency": concurrency,
        },
        "frozen_inputs": frozen,
        "jobs": jobs,
    }
    value = {**body, "digest": f"sha256:{sha256_value(body)}"}
    schema = json.loads((registry.corpus_root / "schemas" / "batch.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    return BatchPlan(value)


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    attempt_id: str
    attempt_number: int
    lease_owner: str
    lease_expires_at: str
    configuration: Mapping[str, Any]


class BatchLedger:
    """SQLite ledger with immutable plan identity and atomic leases."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS plans (
                    plan_digest TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL CHECK(schema_version = 'websitebench.batch.v1'),
                    plan_json TEXT NOT NULL,
                    concurrency INTEGER NOT NULL CHECK(concurrency BETWEEN 1 AND 8),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    plan_digest TEXT NOT NULL REFERENCES plans(plan_digest),
                    ordinal INTEGER NOT NULL,
                    configuration_json TEXT NOT NULL,
                    scheduler_state TEXT NOT NULL CHECK(scheduler_state IN ('queued','running','waiting_for_human','retry_wait','terminal')),
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    retry_not_before TEXT,
                    attempts_started INTEGER NOT NULL DEFAULT 0 CHECK(attempts_started >= 0),
                    outcome_kind TEXT,
                    result_ref TEXT,
                    terminal_at TEXT,
                    UNIQUE(plan_digest, ordinal)
                );
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    journey_id TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('public','hidden')),
                    max_score REAL NOT NULL CHECK(max_score = 5)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    attempt_number INTEGER NOT NULL,
                    lease_owner TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    outcome_json TEXT,
                    journal_ref TEXT,
                    UNIQUE(job_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT,
                    UNIQUE(attempt_id, kind, path)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    attempt_id TEXT,
                    kind TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS plans_immutable_update BEFORE UPDATE ON plans BEGIN SELECT RAISE(ABORT, 'batch plan is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS plans_immutable_delete BEFORE DELETE ON plans BEGIN SELECT RAISE(ABORT, 'batch plan is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS jobs_identity_immutable BEFORE UPDATE OF plan_digest, ordinal, configuration_json ON jobs BEGIN SELECT RAISE(ABORT, 'batch job identity is immutable'); END;
                """
            )

    def install_plan(self, plan: BatchPlan, *, now: datetime | None = None) -> None:
        value = plan.to_dict()
        if value.get("schema_version") != BATCH_VERSION:
            raise ValueError(f"unsupported batch plan version: {value.get('schema_version')}")
        digest = value.pop("digest")
        if f"sha256:{sha256_value(value)}" != digest:
            raise ValueError("batch plan digest is invalid")
        payload = json.dumps({**value, "digest": digest}, sort_keys=True, separators=(",", ":"))
        instant = utc_now(now)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT plan_json FROM plans WHERE plan_digest = ?", (digest,)).fetchone()
            if existing:
                if existing["plan_json"] != payload:
                    connection.rollback()
                    raise ValueError("stored plan digest has different immutable content")
                connection.commit()
                return
            connection.execute(
                "INSERT INTO plans(plan_digest,schema_version,plan_json,concurrency,created_at) VALUES(?,?,?,?,?)",
                (digest, BATCH_VERSION, payload, value["dimensions"]["concurrency"], instant),
            )
            for job in value["jobs"]:
                configuration = {key: item for key, item in job.items() if key not in {"job_id", "ordinal", "executions"}}
                connection.execute(
                    "INSERT INTO jobs(job_id,plan_digest,ordinal,configuration_json,scheduler_state) VALUES(?,?,?,?,?)",
                    (job["job_id"], digest, job["ordinal"], json.dumps(configuration, sort_keys=True), "queued"),
                )
                for execution in job["executions"]:
                    connection.execute(
                        "INSERT INTO executions(execution_id,job_id,journey_id,seed,visibility,max_score) VALUES(?,?,?,?,?,?)",
                        (execution["execution_id"], job["job_id"], execution["journey_id"], execution["seed"], execution["visibility"], execution["max_score"]),
                    )
            connection.commit()

    def claim(
        self,
        plan_digest: str,
        *,
        owner: str,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> ClaimedJob | None:
        moment = now or datetime.now(timezone.utc)
        instant = utc_now(moment)
        lease_expires = utc_now(moment + timedelta(seconds=lease_seconds))
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = connection.execute("SELECT concurrency FROM plans WHERE plan_digest = ?", (plan_digest,)).fetchone()
            if not plan:
                connection.rollback()
                raise ValueError(f"unknown plan: {plan_digest}")
            running = connection.execute("SELECT COUNT(*) AS count FROM jobs WHERE plan_digest = ? AND scheduler_state = 'running'", (plan_digest,)).fetchone()["count"]
            if running >= plan["concurrency"]:
                connection.commit()
                return None
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE plan_digest = ?
                  AND (scheduler_state = 'queued' OR (scheduler_state = 'retry_wait' AND retry_not_before <= ?))
                ORDER BY ordinal LIMIT 1
                """,
                (plan_digest, instant),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            number = row["attempts_started"] + 1
            attempt_id = f"{row['job_id']}.attempt-{number}"
            updated = connection.execute(
                """
                UPDATE jobs SET scheduler_state='running', lease_owner=?, lease_expires_at=?, retry_not_before=NULL, attempts_started=?
                WHERE job_id=? AND scheduler_state IN ('queued','retry_wait')
                """,
                (owner, lease_expires, number, row["job_id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                "INSERT INTO attempts(attempt_id,job_id,attempt_number,lease_owner,started_at) VALUES(?,?,?,?,?)",
                (attempt_id, row["job_id"], number, owner, instant),
            )
            connection.execute(
                "INSERT INTO events(job_id,attempt_id,kind,timestamp,payload_json) VALUES(?,?,?,?,?)",
                (row["job_id"], attempt_id, "claimed", instant, json.dumps({"owner": owner, "lease_expires_at": lease_expires}, sort_keys=True)),
            )
            connection.commit()
            return ClaimedJob(row["job_id"], attempt_id, number, owner, lease_expires, json.loads(row["configuration_json"]))

    def renew(self, claim: ClaimedJob, *, lease_seconds: int = 300, now: datetime | None = None) -> str:
        expires = utc_now((now or datetime.now(timezone.utc)) + timedelta(seconds=lease_seconds))
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE jobs SET lease_expires_at=? WHERE job_id=? AND scheduler_state='running' AND lease_owner=?",
                (expires, claim.job_id, claim.lease_owner),
            )
            if updated.rowcount != 1:
                raise ValueError("lease is no longer owned by this worker")
        return expires

    def finish(self, claim: ClaimedJob, outcome: AttemptOutcome, *, artifact_ref: str | None = None, now: datetime | None = None) -> None:
        if outcome.retry.retry_number != claim.attempt_number:
            raise ValueError("retry advice attempt number does not match claimed attempt")
        instant = utc_now(now)
        retry_at = outcome.retry.retry_at
        state = "retry_wait" if outcome.retry.retryable else "terminal"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (claim.job_id,)).fetchone()
            if not row or row["scheduler_state"] != "running" or row["lease_owner"] != claim.lease_owner:
                connection.rollback()
                raise ValueError("cannot finish an unowned or non-running job")
            connection.execute(
                "UPDATE attempts SET finished_at=?, outcome_json=?, journal_ref=? WHERE attempt_id=? AND finished_at IS NULL",
                (instant, json.dumps(outcome.to_dict(), sort_keys=True), artifact_ref, claim.attempt_id),
            )
            connection.execute(
                """
                UPDATE jobs SET scheduler_state=?, lease_owner=NULL, lease_expires_at=NULL,
                    retry_not_before=?, outcome_kind=?, result_ref=?, terminal_at=?
                WHERE job_id=?
                """,
                (state, retry_at, outcome.kind.value, outcome.result_ref, instant if state == "terminal" else None, claim.job_id),
            )
            connection.execute(
                "INSERT INTO events(job_id,attempt_id,kind,timestamp,payload_json) VALUES(?,?,?,?,?)",
                (claim.job_id, claim.attempt_id, "attempt_finished", instant, json.dumps(outcome.to_dict(), sort_keys=True)),
            )
            connection.commit()

    def recover_expired(self, plan_digest: str, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(timezone.utc)
        instant = utc_now(moment)
        recovered = 0
        while True:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM jobs WHERE plan_digest=? AND scheduler_state='running' AND lease_expires_at < ? ORDER BY ordinal LIMIT 1",
                    (plan_digest, instant),
                ).fetchone()
                if not row:
                    connection.commit()
                    break
                attempt = connection.execute("SELECT * FROM attempts WHERE job_id=? AND finished_at IS NULL ORDER BY attempt_number DESC LIMIT 1", (row["job_id"],)).fetchone()
                outcome = classify_failure(stage=AttemptStage.PREPARED, reason_code="LEASE_EXPIRED", message="worker lease expired before terminal outcome", attempt_number=attempt["attempt_number"], now=moment)
                state = "retry_wait" if outcome.retry.retryable else "terminal"
                connection.execute("UPDATE attempts SET finished_at=?, outcome_json=? WHERE attempt_id=?", (instant, json.dumps(outcome.to_dict(), sort_keys=True), attempt["attempt_id"]))
                connection.execute(
                    "UPDATE jobs SET scheduler_state=?,lease_owner=NULL,lease_expires_at=NULL,retry_not_before=?,outcome_kind=?,terminal_at=? WHERE job_id=?",
                    (state, outcome.retry.retry_at, outcome.kind.value, instant if state == "terminal" else None, row["job_id"]),
                )
                connection.execute(
                    "INSERT INTO events(job_id,attempt_id,kind,timestamp,payload_json) VALUES(?,?,?,?,?)",
                    (row["job_id"], attempt["attempt_id"], "lease_expired", instant, json.dumps(outcome.to_dict(), sort_keys=True)),
                )
                connection.commit()
                recovered += 1
        return recovered

    def add_artifact(self, attempt_id: str, *, kind: str, path: str, sha256: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute("INSERT INTO artifacts(attempt_id,kind,path,sha256) VALUES(?,?,?,?)", (attempt_id, kind, path, sha256))

    def summary(self, plan_digest: str) -> dict[str, Any]:
        with self.connect() as connection:
            plan = connection.execute("SELECT * FROM plans WHERE plan_digest=?", (plan_digest,)).fetchone()
            if not plan:
                raise ValueError(f"unknown plan: {plan_digest}")
            jobs = connection.execute("SELECT * FROM jobs WHERE plan_digest=? ORDER BY ordinal", (plan_digest,)).fetchall()
            attempts = connection.execute("SELECT a.* FROM attempts a JOIN jobs j ON j.job_id=a.job_id WHERE j.plan_digest=? ORDER BY j.ordinal,a.attempt_number", (plan_digest,)).fetchall()
            executions = connection.execute("SELECT COUNT(*) AS count FROM executions e JOIN jobs j ON j.job_id=e.job_id WHERE j.plan_digest=?", (plan_digest,)).fetchone()["count"]
            execution_rows = connection.execute(
                "SELECT e.job_id,e.journey_id,e.seed,e.visibility FROM executions e JOIN jobs j ON j.job_id=e.job_id WHERE j.plan_digest=? ORDER BY j.ordinal,e.execution_id",
                (plan_digest,),
            ).fetchall()
            artifacts = connection.execute("SELECT a.attempt_id,a.kind,a.path,a.sha256 FROM artifacts a JOIN attempts t ON t.attempt_id=a.attempt_id JOIN jobs j ON j.job_id=t.job_id WHERE j.plan_digest=? ORDER BY a.attempt_id,a.kind,a.path", (plan_digest,)).fetchall()
        scheduler_counts = {state: 0 for state in sorted(SCHEDULER_STATES)}
        attribution_counts = {kind.value: 0 for kind in OutcomeKind}
        scores = []
        exact_passed = 0
        exact_total = 0
        exact_by_visibility = {"public": {"passed": 0, "total": 0}, "hidden": {"passed": 0, "total": 0}}
        exact_by_thinking: dict[str, dict[str, int]] = {}
        exact_by_site: dict[str, dict[str, int]] = {}
        exact_by_site_thinking: dict[str, dict[str, dict[str, int]]] = {}
        candidate_failure_zeroes = 0
        unreadable_scored_results: list[dict[str, str]] = []
        durations: list[float] = []
        started_times: list[datetime] = []
        finished_times: list[datetime] = []
        for attempt in attempts:
            started = parse_time(attempt["started_at"])
            started_times.append(started)
            if attempt["finished_at"]:
                finished = parse_time(attempt["finished_at"])
                finished_times.append(finished)
                durations.append(max(0.0, (finished - started).total_seconds()))
        executions_by_job: dict[str, list[sqlite3.Row]] = {}
        visibility_by_job: dict[str, dict[int, str]] = {}
        for execution in execution_rows:
            executions_by_job.setdefault(execution["job_id"], []).append(execution)
            visibility_by_job.setdefault(execution["job_id"], {})[execution["seed"]] = execution["visibility"]

        def bucket(container: dict[str, dict[str, int]], key: str) -> dict[str, int]:
            return container.setdefault(key, {"passed": 0, "total": 0})

        def add_exact(row: sqlite3.Row, *, passed: bool, visibility: str) -> None:
            nonlocal exact_passed, exact_total
            configuration = json.loads(row["configuration_json"])
            site_id = str(configuration["site_id"])
            thinking = str(configuration["thinking_level"])
            exact_passed += int(passed)
            exact_total += 1
            exact_by_visibility[visibility]["passed"] += int(passed)
            exact_by_visibility[visibility]["total"] += 1
            for target in (
                bucket(exact_by_thinking, thinking),
                bucket(exact_by_site, site_id),
                bucket(exact_by_site_thinking.setdefault(site_id, {}), thinking),
            ):
                target["passed"] += int(passed)
                target["total"] += 1

        for row in jobs:
            scheduler_counts[row["scheduler_state"]] += 1
            if row["outcome_kind"]:
                attribution_counts[row["outcome_kind"]] += 1
            if row["outcome_kind"] == OutcomeKind.SCORED.value and row["result_ref"]:
                try:
                    result = json.loads(Path(row["result_ref"]).read_text(encoding="utf-8"))
                    scores.append(float(result["score"]))
                    for journey in result.get("journeys", []):
                        exact = bool(journey.get("terminal_passed")) and bool(journey.get("checkpoints")) and all(
                            checkpoint.get("passed") for checkpoint in journey["checkpoints"]
                        )
                        visibility = visibility_by_job.get(row["job_id"], {}).get(int(journey["seed"]))
                        if visibility not in exact_by_visibility:
                            raise ValueError(
                                f"result journey seed {journey.get('seed')} is not planned for {row['job_id']}"
                            )
                        add_exact(row, passed=exact, visibility=visibility)
                except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    unreadable_scored_results.append(
                        {"job_id": row["job_id"], "result_ref": row["result_ref"], "error": str(exc)}
                    )
            elif (
                row["scheduler_state"] == "terminal"
                and row["outcome_kind"] == OutcomeKind.CANDIDATE_FAILED.value
            ):
                for execution in executions_by_job.get(row["job_id"], []):
                    add_exact(row, passed=False, visibility=execution["visibility"])
                    candidate_failure_zeroes += 1

        def rates(values: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
            return {
                key: {
                    **counts,
                    "pass_rate": round(counts["passed"] / counts["total"], 6)
                    if counts["total"]
                    else None,
                }
                for key, counts in sorted(values.items())
            }

        thinking_rates = rates(exact_by_thinking)
        site_rates = rates(exact_by_site)
        site_thinking_rates = {
            site: rates(values) for site, values in sorted(exact_by_site_thinking.items())
        }
        configured_efforts = json.loads(plan["plan_json"])["dimensions"]["thinking_levels"]
        observed_effort_rates = [
            thinking_rates[effort]["pass_rate"]
            for effort in configured_efforts
            if effort in thinking_rates and thinking_rates[effort]["pass_rate"] is not None
        ]
        visibility_rates = {
            visibility: (
                counts["passed"] / counts["total"] if counts["total"] else None
            )
            for visibility, counts in exact_by_visibility.items()
        }
        effort_spread = (
            round((max(observed_effort_rates) - min(observed_effort_rates)) * 100, 4)
            if len(observed_effort_rates) == len(configured_efforts)
            else None
        )
        public_hidden_gap = (
            round(abs(visibility_rates["public"] - visibility_rates["hidden"]) * 100, 4)
            if visibility_rates["public"] is not None and visibility_rates["hidden"] is not None
            else None
        )
        analysis_complete = exact_total == executions and not unreadable_scored_results
        return {
            "schema_version": "websitebench.batch-summary.v1",
            "plan_digest": plan_digest,
            "dimensions": json.loads(plan["plan_json"])["dimensions"],
            "jobs": len(jobs),
            "journey_seed_executions": executions,
            "scheduler_counts": scheduler_counts,
            "attribution_counts": attribution_counts,
            "attempts": len(attempts),
            "retries": sum(int(row["attempt_number"]) > 1 for row in attempts),
            "scores": {"count": len(scores), "mean": round(sum(scores) / len(scores), 4) if scores else None},
            "timings": {
                "started_at": utc_now(min(started_times)) if started_times else None,
                "finished_at": utc_now(max(finished_times)) if finished_times else None,
                "completed_attempts": len(durations),
                "attempt_seconds": {
                    "total": round(sum(durations), 6),
                    "minimum": round(min(durations), 6) if durations else None,
                    "maximum": round(max(durations), 6) if durations else None,
                    "mean": round(sum(durations) / len(durations), 6) if durations else None,
                },
            },
            "exact_journey_seed": {
                "passed": exact_passed,
                "total": exact_total,
                "planned_total": executions,
                "pass_rate": round(exact_passed / exact_total, 6) if exact_total else None,
                "candidate_failure_zeroes": candidate_failure_zeroes,
                "unreadable_scored_results": unreadable_scored_results,
                "by_visibility": {
                    visibility: {
                        **counts,
                        "pass_rate": round(counts["passed"] / counts["total"], 6) if counts["total"] else None,
                    }
                    for visibility, counts in exact_by_visibility.items()
                },
                "by_thinking_level": thinking_rates,
                "by_site": site_rates,
                "by_site_and_thinking_level": site_thinking_rates,
                "decision_metrics": {
                    "analysis_complete": analysis_complete,
                    "xhigh_pass_rate": thinking_rates.get("xhigh", {}).get("pass_rate")
                    if analysis_complete
                    else None,
                    "effort_spread_percentage_points": effort_spread
                    if analysis_complete
                    else None,
                    "public_hidden_gap_percentage_points": public_hidden_gap
                    if analysis_complete
                    else None,
                    "all_zero_sites": [
                        site for site, counts in site_rates.items() if counts["pass_rate"] == 0
                    ]
                    if analysis_complete
                    else [],
                    "all_perfect_sites": [
                        site for site, counts in site_rates.items() if counts["pass_rate"] == 1
                    ]
                    if analysis_complete
                    else [],
                },
            },
            "attempt_history": [
                {"attempt_id": row["attempt_id"], "job_id": row["job_id"], "attempt_number": row["attempt_number"], "started_at": row["started_at"], "finished_at": row["finished_at"], "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None}
                for row in attempts
            ],
            "artifacts": [dict(row) for row in artifacts],
        }


Runner = Callable[[ClaimedJob], AttemptOutcome]


def run_workers(
    ledger: BatchLedger,
    plan_digest: str,
    *,
    runner: Runner,
    owner_prefix: str | None = None,
    lease_seconds: int = 300,
    renewal_interval_seconds: float | None = None,
) -> dict[str, Any]:
    """Run available jobs up to the immutable plan concurrency."""

    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    renewal_interval = (
        renewal_interval_seconds
        if renewal_interval_seconds is not None
        else min(60.0, lease_seconds / 3)
    )
    if renewal_interval <= 0 or renewal_interval >= lease_seconds:
        raise ValueError("lease renewal interval must be positive and shorter than the lease")
    ledger.recover_expired(plan_digest)
    with ledger.connect() as connection:
        concurrency = connection.execute("SELECT concurrency FROM plans WHERE plan_digest=?", (plan_digest,)).fetchone()["concurrency"]
    prefix = owner_prefix or f"{socket.gethostname()}-{os.getpid()}"
    lock = threading.Lock()

    def work(slot: int) -> int:
        completed = 0
        owner = f"{prefix}-{slot}-{uuid.uuid4().hex[:8]}"
        while True:
            with lock:
                claim = ledger.claim(
                    plan_digest,
                    owner=owner,
                    lease_seconds=lease_seconds,
                )
            if claim is None:
                return completed
            stop_renewal = threading.Event()
            renewal_errors: list[Exception] = []

            def renew_lease() -> None:
                while not stop_renewal.wait(renewal_interval):
                    try:
                        ledger.renew(claim, lease_seconds=lease_seconds)
                    except Exception as exc:  # preserve lease loss for the worker
                        renewal_errors.append(exc)
                        return

            heartbeat = threading.Thread(
                target=renew_lease,
                name=f"websitebench-lease-{claim.job_id}",
                daemon=True,
            )
            heartbeat.start()
            try:
                outcome = runner(claim)
            finally:
                stop_renewal.set()
                heartbeat.join()
            if renewal_errors:
                raise RuntimeError(
                    f"lost lease while running {claim.job_id}: {renewal_errors[0]}"
                ) from renewal_errors[0]
            ledger.finish(claim, outcome)
            if outcome.result_ref:
                result_path = Path(outcome.result_ref)
                digest = None
                if result_path.is_file():
                    digest = __import__("hashlib").sha256(result_path.read_bytes()).hexdigest()
                ledger.add_artifact(
                    claim.attempt_id,
                    kind="result",
                    path=str(result_path),
                    sha256=digest,
                )
            completed += 1

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(work, slot) for slot in range(concurrency)]
        for future in as_completed(futures):
            future.result()
    return ledger.summary(plan_digest)


def orchestrator_runner(
    output_root: Path,
    budgets: Mapping[str, int],
    expected_manifests: Mapping[str, str],
) -> Runner:
    def run(claim: ClaimedJob) -> AttemptOutcome:
        config = claim.configuration
        run_dir = prepare_run(
            site=config["site_id"], track=config["track"], model=config["model"],
            thinking_level=config["thinking_level"], output_root=output_root,
            budget_override=budgets,
            run_id=claim.attempt_id,
            job_id=claim.job_id,
            attempt_id=claim.attempt_id,
            attempt_number=claim.attempt_number,
        )
        metadata = json.loads((run_dir / "run-meta.json").read_text(encoding="utf-8"))
        if metadata["run_manifest_digest"] != expected_manifests[config["site_id"]]:
            raise ValueError(
                f"prepared manifest for {config['site_id']} differs from immutable batch plan"
            )
        run_pilot(run_dir)
        journal_paths = sorted((run_dir / "attempts").glob("*.json"))
        journal = json.loads(journal_paths[-1].read_text(encoding="utf-8"))
        outcome = journal["outcome"]
        from .attempts import RetryAdvice
        persisted_retry = outcome["retry"]
        return AttemptOutcome(
            OutcomeKind(outcome["kind"]), outcome["reason_code"], AttemptStage(outcome["stage"]), outcome["message"],
            RetryAdvice(
                persisted_retry["retryable"],
                persisted_retry["maximum_attempts"],
                persisted_retry["retry_number"],
                persisted_retry["delay_seconds"],
                persisted_retry["retry_at"],
            ),
            str(run_dir / outcome["result_ref"]) if outcome["result_ref"] else None, outcome["facts_valid"],
        )
    return run


def _load_plan(path: Path) -> BatchPlan:
    value = json.loads(path.read_text(encoding="utf-8"))
    return BatchPlan(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawbench-batch")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--ledger", type=Path, required=True)
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--site", action="append")
    plan.add_argument("--family")
    plan.add_argument("--split", choices=("train", "validation", "test"))
    plan.add_argument("--model", action="append", required=True)
    plan.add_argument("--thinking-level", action="append", required=True)
    plan.add_argument("--track", action="append", default=[])
    plan.add_argument("--repetitions", type=int, default=1)
    plan.add_argument("--concurrency", type=int, default=1)
    run = subparsers.add_parser("run")
    run.add_argument("--ledger", type=Path, required=True)
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument(
        "--output-root", type=Path, default=Path("artifacts/websitebench/runs")
    )
    resume = subparsers.add_parser("resume")
    resume.add_argument("--ledger", type=Path, required=True)
    resume.add_argument("--plan", type=Path, required=True)
    resume.add_argument(
        "--output-root", type=Path, default=Path("artifacts/websitebench/runs")
    )
    summary = subparsers.add_parser("summary")
    summary.add_argument("--ledger", type=Path, required=True)
    summary.add_argument("--plan", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ledger = BatchLedger(args.ledger)
    if args.command == "plan":
        plan = create_plan(
            registry=SiteRegistry.default(), site_ids=args.site, family_id=args.family, split=args.split,
            models=args.model, thinking_levels=args.thinking_level, tracks=args.track or ["core"],
            repetitions=args.repetitions, concurrency=args.concurrency,
        )
        ledger.install_plan(plan)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(ledger.summary(plan.digest), indent=2, sort_keys=True))
        return 0
    plan = _load_plan(args.plan)
    ledger.install_plan(plan)
    if args.command == "summary":
        print(json.dumps(ledger.summary(plan.digest), indent=2, sort_keys=True))
        return 0
    registry = SiteRegistry.default()
    verify_frozen_inputs(plan, registry)
    budgets = plan.value["frozen_inputs"]["budgets"]
    summary = run_workers(
        ledger,
        plan.digest,
        runner=orchestrator_runner(
            args.output_root,
            budgets,
            plan.value["frozen_inputs"]["run_manifests"],
        ),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["scheduler_counts"]["terminal"] == summary["jobs"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
