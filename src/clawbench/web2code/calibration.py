"""Amazon-136 harness calibration records (never official benchmark scores)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


CALIBRATION_VERSION = "websitebench.calibration-result.v1"


def validate_calibration(value: Any, schema_path: Path | str) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    failures = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if failures:
        raise ValueError(
            "invalid calibration result: "
            + "; ".join(
                f"{'.'.join(str(part) for part in failure.absolute_path) or '<root>'}: {failure.message}"
                for failure in failures
            )
        )
    steps = value["steps"]
    expected = steps["passed"] / steps["total"]
    if abs(float(steps["pass_rate"]) - expected) > 1e-9:
        raise ValueError("invalid calibration result: steps.pass_rate does not equal passed / total")
    if "score" in value or "dimensions" in value:
        raise ValueError("calibration results cannot contain official score fields")


def write_calibration(value: dict[str, Any], output: Path | str, schema_path: Path | str) -> Path:
    validate_calibration(value, schema_path)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
