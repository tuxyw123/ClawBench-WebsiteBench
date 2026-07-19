"""Auditable Human+Agent intervention log with fixed message/time limits."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_CATEGORIES = {
    "product-understanding",
    "exploration-strategy",
    "frontend-layout",
    "backend-modeling",
    "debug-direction",
    "test-suggestion",
    "missing-feature",
    "memory-correction",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class HumanInterventionLog:
    def __init__(self, path: Path | str, *, max_messages: int = 12, max_minutes: int = 90) -> None:
        self.path = Path(path)
        self.max_messages = max_messages
        self.max_minutes = max_minutes

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def append(self, *, category: str, message: str, final: bool = False) -> dict[str, Any]:
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"unknown intervention category: {category}")
        message = message.strip()
        if not message or len(message) > 4000:
            raise ValueError("message must contain 1 to 4000 characters")
        records = self.records()
        if len(records) >= self.max_messages:
            raise ValueError("HITL message budget exhausted")
        now = _now()
        first = datetime.fromisoformat(records[0]["timestamp"].replace("Z", "+00:00")) if records else now
        elapsed_minutes = (now - first).total_seconds() / 60
        if elapsed_minutes > self.max_minutes:
            raise ValueError("HITL 90-minute window has closed")
        previous_hash = records[-1]["hash"] if records else "0" * 64
        record = {
            "sequence": len(records) + 1,
            "timestamp": _timestamp(now),
            "elapsed_minutes": round(elapsed_minutes, 3),
            "category": category,
            "message": message,
            "final": final,
            "previous_hash": previous_hash,
        }
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        record["hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def validate(self) -> None:
        records = self.records()
        if len(records) > self.max_messages:
            raise ValueError("HITL message budget exceeded")
        previous_hash = "0" * 64
        for index, record in enumerate(records, 1):
            if record["sequence"] != index or record["previous_hash"] != previous_hash:
                raise ValueError("HITL log sequence/hash chain is invalid")
            candidate = {key: value for key, value in record.items() if key != "hash"}
            payload = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
            expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if record["hash"] != expected:
                raise ValueError("HITL log record was modified")
            previous_hash = record["hash"]

