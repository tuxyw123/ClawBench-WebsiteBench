#!/usr/bin/env python3
"""Validate an exact CTRF node set and emit a fractional Harbor reward."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {"passed", "failed", "skipped"}


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(
        path,
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _invalid(output: Path, reason: str) -> int:
    (output / "reward.txt").unlink(missing_ok=True)
    (output / "scorecard.json").unlink(missing_ok=True)
    _atomic_json(
        output / "verdict.json",
        {
            "schema_version": "clawbench.harbor.verdict.v1",
            "valid": False,
            "reason": reason,
        },
    )
    print(f"INVALID_RUN: {reason}", file=sys.stderr)
    return 2


def _load_required(path: Path) -> tuple[dict[str, list[str]], dict[str, int]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("exact_node_set") is not True:
        raise ValueError("required-nodes must enable exact_node_set")
    groups = value["groups"]
    weights = value["dimension_max_points"]
    if not isinstance(groups, dict) or not isinstance(weights, dict):
        raise ValueError("groups and dimension_max_points must be objects")
    if set(groups) != set(weights):
        raise ValueError("test groups and scoring dimensions differ")
    nodes = [node for group in groups.values() for node in group]
    if not nodes or not all(isinstance(node, str) and node for node in nodes):
        raise ValueError("required node set is empty or malformed")
    if len(nodes) != len(set(nodes)):
        raise ValueError("required node set contains duplicates")
    if (
        not all(isinstance(points, int) and not isinstance(points, bool) for points in weights.values())
        or sum(weights.values()) != 100
    ):
        raise ValueError("dimension_max_points must be integer weights summing to 100")
    return groups, weights


def _load_ctrf(path: Path) -> tuple[dict[str, tuple[str, float]], list[str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    results = value["results"]
    tests = results["tests"]
    if not isinstance(tests, list) or not tests:
        raise ValueError("CTRF contains zero tests")
    statuses: dict[str, tuple[str, float]] = {}
    for test in tests:
        if not isinstance(test, dict):
            raise ValueError("CTRF test entry is not an object")
        name = test.get("name")
        status = test.get("status")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("CTRF test name is empty")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"unknown CTRF status for {name}: {status}")
        if name in statuses:
            raise ValueError(f"duplicate CTRF test name: {name}")
        default_score = 1.0 if status == "passed" else 0.0
        extra = test.get("extra", {})
        if not isinstance(extra, dict):
            raise ValueError(f"CTRF extra must be an object: {name}")
        score = extra.get("clawbench_score", default_score)
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 1
        ):
            raise ValueError(f"invalid clawbench_score for {name}")
        statuses[name] = (status, float(score))
    results_extra = results.get("extra", {})
    if not isinstance(results_extra, dict):
        raise ValueError("CTRF results.extra must be an object")
    hard_failures = results_extra.get("hard_failures", [])
    if (
        not isinstance(hard_failures, list)
        or not all(isinstance(item, str) and item for item in hard_failures)
        or len(hard_failures) != len(set(hard_failures))
    ):
        raise ValueError("hard_failures must be a unique string list")
    return statuses, hard_failures


def score(report_path: Path, required_path: Path, output: Path) -> int:
    try:
        groups, weights = _load_required(required_path)
        statuses, hard_failures = _load_ctrf(report_path)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _invalid(output, f"SCORING_INPUT_INVALID:{type(exc).__name__}:{exc}")

    required = {node for nodes in groups.values() for node in nodes}
    if set(statuses) != required:
        missing = sorted(required - set(statuses))
        extra = sorted(set(statuses) - required)
        return _invalid(
            output,
            f"CTRF_EXACT_SET_MISMATCH:missing={missing}:extra={extra}",
        )

    dimensions: dict[str, dict[str, Any]] = {}
    total = 0.0
    for dimension, nodes in groups.items():
        maximum = weights[dimension]
        fraction = (
            sum(statuses[node][1] for node in nodes) / len(nodes)
            if nodes
            else 1.0
        )
        points = maximum * fraction
        dimensions[dimension] = {
            "max_score": maximum,
            "score": round(points, 6),
            "passed": sum(statuses[node][0] == "passed" for node in nodes),
            "total": len(nodes),
        }
        total += points
    if hard_failures:
        total = 0.0

    total = round(total, 6)
    reward = round(total / 100.0, 8)
    scorecard = {
        "schema_version": "clawbench.harbor.score.v1",
        "score": total,
        "max_score": 100,
        "reward": reward,
        "hard_failures": hard_failures,
        "dimensions": dimensions,
    }
    _atomic_json(output / "dimensions.json", scorecard)
    _atomic_json(output / "scorecard.json", scorecard)
    _atomic_text(output / "reward.txt", f"{reward:.8f}\n")
    _atomic_json(
        output / "verdict.json",
        {
            "schema_version": "clawbench.harbor.verdict.v1",
            "valid": True,
            "score": total,
            "max_score": 100,
            "reward": reward,
            "hard_failures": hard_failures,
        },
    )
    print(f"nodes={len(required)} score={total:.6f}/100 reward={reward:.8f}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: compute_reward.py CTRF REQUIRED_NODES OUTPUT_DIR", file=sys.stderr)
        return 2
    return score(Path(argv[0]), Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
