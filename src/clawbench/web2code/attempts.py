"""Typed, auditable WebsiteBench attempt outcomes and retry advice."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


ATTEMPT_VERSION = "websitebench.attempt.v1"
FACTS_VERSION = "websitebench.facts.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AttemptStage(str, Enum):
    PREPARED = "prepared"
    AGENT = "agent"
    CANDIDATE_FINALIZE = "candidate_finalize"
    SOURCE_POLICY = "source_policy"
    CANDIDATE_BUILD = "candidate_build"
    CANDIDATE_START = "candidate_start"
    CANDIDATE_HEALTH = "candidate_health"
    EVALUATOR = "evaluator"
    FACTS_VALIDATION = "facts_validation"
    HOST_SCORING = "host_scoring"
    RESULT_VALIDATION = "result_validation"
    FINALIZED = "finalized"
    TERMINAL = "terminal"


class OutcomeKind(str, Enum):
    SCORED = "scored"
    CANDIDATE_FAILED = "candidate_failed"
    EVALUATOR_FAILED = "evaluator_failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


CANDIDATE_STAGES = frozenset(
    {
        AttemptStage.AGENT,
        AttemptStage.CANDIDATE_FINALIZE,
        AttemptStage.SOURCE_POLICY,
        AttemptStage.CANDIDATE_BUILD,
        AttemptStage.CANDIDATE_START,
        AttemptStage.CANDIDATE_HEALTH,
    }
)
INFRASTRUCTURE_REASON_ALLOWLIST = frozenset(
    {
        "CONTAINER_RUNTIME_UNAVAILABLE",
        "MODEL_CREDENTIAL_UNAVAILABLE",
        "BENCHMARK_TOPOLOGY_START_FAILED",
        "BUILD_PLANE_TEARDOWN_FAILED",
        "HOST_PROCESS_INTERRUPTED",
        "LEASE_EXPIRED",
        "HOST_STORAGE_UNAVAILABLE",
        "HOST_NETWORK_UNAVAILABLE",
    }
)
TRANSIENT_EVALUATOR_REASONS = frozenset(
    {"EVALUATOR_TIMEOUT", "EVALUATOR_PROCESS_INTERRUPTED", "EVALUATOR_SERVICE_UNAVAILABLE"}
)
_ALLOWED_TRANSITIONS: Mapping[AttemptStage, frozenset[AttemptStage]] = {
    AttemptStage.PREPARED: frozenset({AttemptStage.AGENT, AttemptStage.TERMINAL}),
    AttemptStage.AGENT: frozenset({AttemptStage.CANDIDATE_FINALIZE, AttemptStage.TERMINAL}),
    AttemptStage.CANDIDATE_FINALIZE: frozenset({AttemptStage.SOURCE_POLICY, AttemptStage.TERMINAL}),
    AttemptStage.SOURCE_POLICY: frozenset({AttemptStage.CANDIDATE_BUILD, AttemptStage.TERMINAL}),
    AttemptStage.CANDIDATE_BUILD: frozenset({AttemptStage.CANDIDATE_START, AttemptStage.TERMINAL}),
    AttemptStage.CANDIDATE_START: frozenset({AttemptStage.CANDIDATE_HEALTH, AttemptStage.TERMINAL}),
    AttemptStage.CANDIDATE_HEALTH: frozenset({AttemptStage.EVALUATOR, AttemptStage.TERMINAL}),
    AttemptStage.EVALUATOR: frozenset({AttemptStage.FACTS_VALIDATION, AttemptStage.TERMINAL}),
    AttemptStage.FACTS_VALIDATION: frozenset({AttemptStage.HOST_SCORING, AttemptStage.TERMINAL}),
    AttemptStage.HOST_SCORING: frozenset({AttemptStage.RESULT_VALIDATION, AttemptStage.TERMINAL}),
    AttemptStage.RESULT_VALIDATION: frozenset({AttemptStage.FINALIZED, AttemptStage.TERMINAL}),
    AttemptStage.FINALIZED: frozenset({AttemptStage.TERMINAL}),
    AttemptStage.TERMINAL: frozenset(),
}


@dataclass(frozen=True)
class RetryAdvice:
    retryable: bool
    maximum_attempts: int
    retry_number: int
    delay_seconds: int | None
    retry_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "retryable": self.retryable,
            "maximum_attempts": self.maximum_attempts,
            "retry_number": self.retry_number,
            "delay_seconds": self.delay_seconds,
            "retry_at": self.retry_at,
        }


@dataclass(frozen=True)
class AttemptOutcome:
    kind: OutcomeKind
    reason_code: str
    stage: AttemptStage
    message: str
    retry: RetryAdvice
    result_ref: str | None = None
    facts_valid: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason_code": self.reason_code,
            "stage": self.stage.value,
            "message": self.message,
            "retry": self.retry.to_dict(),
            "result_ref": self.result_ref,
            "facts_valid": self.facts_valid,
        }


def retry_advice(kind: OutcomeKind, reason_code: str, attempt_number: int, *, now: datetime | None = None) -> RetryAdvice:
    """Return the fixed retry policy consumed by the batch scheduler."""

    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    maximum = 1
    delays: tuple[int, ...] = ()
    if kind is OutcomeKind.EVALUATOR_FAILED and reason_code in TRANSIENT_EVALUATOR_REASONS:
        maximum, delays = 2, (5,)
    elif kind is OutcomeKind.INFRASTRUCTURE_ERROR:
        if reason_code not in INFRASTRUCTURE_REASON_ALLOWLIST:
            raise ValueError(f"infrastructure reason is not allowlisted: {reason_code}")
        maximum, delays = 3, (5, 30)
    retryable = attempt_number < maximum
    delay = delays[attempt_number - 1] if retryable else None
    retry_at = None
    if delay is not None:
        instant = (now or datetime.now(timezone.utc)) + timedelta(seconds=delay)
        retry_at = instant.isoformat().replace("+00:00", "Z")
    return RetryAdvice(retryable, maximum, attempt_number, delay, retry_at)


def classify_failure(
    *,
    stage: AttemptStage,
    reason_code: str,
    message: str,
    attempt_number: int,
    now: datetime | None = None,
) -> AttemptOutcome:
    if reason_code in INFRASTRUCTURE_REASON_ALLOWLIST:
        kind = OutcomeKind.INFRASTRUCTURE_ERROR
    elif stage in CANDIDATE_STAGES:
        kind = OutcomeKind.CANDIDATE_FAILED
    elif stage in {AttemptStage.EVALUATOR, AttemptStage.FACTS_VALIDATION}:
        kind = OutcomeKind.EVALUATOR_FAILED
    else:
        raise ValueError(
            f"unrecognized host failure {reason_code!r} at {stage.value}; preserve it for diagnosis"
        )
    return AttemptOutcome(
        kind=kind,
        reason_code=reason_code,
        stage=stage,
        message=message,
        retry=retry_advice(kind, reason_code, attempt_number, now=now),
        facts_valid=False if stage is AttemptStage.FACTS_VALIDATION else None,
    )


def validate_facts(value: Any, schema_path: Path | str) -> tuple[bool, tuple[str, ...]]:
    """Validate facts without scoring or mutating them."""

    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, (f"facts schema unavailable: {exc}",)
    failures = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    return not failures, tuple(
        f"{'.'.join(str(part) for part in failure.absolute_path) or '<root>'}: {failure.message}"
        for failure in failures
    )


class AttemptJournal:
    """Atomic JSON attempt journal with append-only stage history."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @classmethod
    def create(
        cls,
        path: Path | str,
        *,
        attempt_id: str,
        run_id: str,
        job_id: str,
        attempt_number: int,
    ) -> "AttemptJournal":
        journal = cls(path)
        if journal.path.exists():
            raise FileExistsError(journal.path)
        now = utc_now()
        journal._write(
            {
                "schema_version": ATTEMPT_VERSION,
                "attempt_id": attempt_id,
                "run_id": run_id,
                "job_id": job_id,
                "attempt_number": attempt_number,
                "stage": AttemptStage.PREPARED.value,
                "created_at": now,
                "updated_at": now,
                "events": [
                    {
                        "sequence": 1,
                        "stage": AttemptStage.PREPARED.value,
                        "timestamp": now,
                        "evidence": {},
                    }
                ],
                "process_exits": [],
                "facts_validation": None,
                "outcome": None,
            }
        )
        return journal

    def read(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != ATTEMPT_VERSION:
            raise ValueError(f"unsupported attempt journal: {value.get('schema_version')}")
        return value

    def transition(
        self,
        stage: AttemptStage,
        *,
        evidence: Mapping[str, Any] | None = None,
        process_exit: Mapping[str, Any] | None = None,
        facts_validation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = self.read()
        if value["outcome"] is not None:
            raise ValueError("terminal attempt cannot transition")
        current = AttemptStage(value["stage"])
        if stage not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"unsupported attempt transition: {current.value} -> {stage.value}")
        now = utc_now()
        value["stage"] = stage.value
        value["updated_at"] = now
        value["events"].append(
            {
                "sequence": len(value["events"]) + 1,
                "stage": stage.value,
                "timestamp": now,
                "evidence": dict(evidence or {}),
            }
        )
        if process_exit is not None:
            value["process_exits"].append(dict(process_exit))
        if facts_validation is not None:
            value["facts_validation"] = dict(facts_validation)
        self._write(value)
        return value

    def finish(
        self,
        outcome: AttemptOutcome,
        *,
        evidence: Mapping[str, Any] | None = None,
        process_exit: Mapping[str, Any] | None = None,
        facts_validation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = self.read()
        if value["outcome"] is not None:
            raise ValueError("terminal attempt outcome cannot be rewritten")
        current = AttemptStage(value["stage"])
        if AttemptStage.TERMINAL not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(f"attempt cannot finish from {current.value}")
        now = utc_now()
        value["stage"] = AttemptStage.TERMINAL.value
        value["updated_at"] = now
        value["events"].append(
            {
                "sequence": len(value["events"]) + 1,
                "stage": AttemptStage.TERMINAL.value,
                "timestamp": now,
                "evidence": {
                    "reason_code": outcome.reason_code,
                    "attribution": outcome.kind.value,
                    **dict(evidence or {}),
                },
            }
        )
        if process_exit is not None:
            value["process_exits"].append(dict(process_exit))
        if facts_validation is not None:
            value["facts_validation"] = dict(facts_validation)
        value["outcome"] = outcome.to_dict()
        self._write(value)
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        schema_path = (
            Path(__file__).resolve().parents[3]
            / "websitebench"
            / "schemas"
            / "attempt.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        failures = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda error: list(error.absolute_path),
        )
        if failures:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in failures
            )
            raise ValueError(f"invalid attempt journal: {details}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
