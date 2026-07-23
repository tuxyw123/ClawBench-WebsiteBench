#!/usr/bin/env python3
"""Merge trusted JUnit and CTRF fragments into one deterministic CTRF report."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _junit_tests(path: Path) -> list[dict[str, Any]]:
    root = ET.parse(path).getroot()
    tests: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        node = None
        properties = case.find("properties")
        if properties is not None:
            for prop in properties.findall("property"):
                if prop.get("name") == "clawbench_node":
                    node = prop.get("value")
                    break
        if not node:
            raise ValueError(
                f"{path}: every JUnit testcase needs property clawbench_node"
            )
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        status = "failed" if failure is not None or error is not None else (
            "skipped" if skipped is not None else "passed"
        )
        message_element = failure or error or skipped
        entry: dict[str, Any] = {
            "name": node,
            "status": status,
            "duration": int(float(case.get("time", "0")) * 1000),
        }
        if message_element is not None:
            entry["message"] = (
                message_element.get("message")
                or (message_element.text or "").strip()
                or status
            )
        tests.append(entry)
    return tests


def _ctrf_tests(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    results = value["results"]
    tests = results["tests"]
    if not isinstance(tests, list):
        raise ValueError(f"{path}: CTRF tests must be an array")
    extra = results.get("extra", {})
    hard_failures = extra.get("hard_failures", []) if isinstance(extra, dict) else []
    if not isinstance(hard_failures, list):
        raise ValueError(f"{path}: hard_failures must be an array")
    return tests, hard_failures


def merge(junit: list[Path], ctrf: list[Path], output: Path) -> None:
    tests: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    for path in junit:
        tests.extend(_junit_tests(path))
    for path in ctrf:
        fragment_tests, fragment_hard_failures = _ctrf_tests(path)
        tests.extend(fragment_tests)
        hard_failures.extend(fragment_hard_failures)
    names = [test.get("name") for test in tests]
    if not tests or any(not isinstance(name, str) or not name for name in names):
        raise ValueError("merged CTRF contains no tests or an invalid name")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate test names: {duplicates}")
    statuses = [test.get("status") for test in tests]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "results": {
                    "tool": {"name": "clawbench-harbor-merge"},
                    "summary": {
                        "tests": len(tests),
                        "passed": statuses.count("passed"),
                        "failed": statuses.count("failed"),
                        "skipped": statuses.count("skipped"),
                    },
                    "tests": tests,
                    "extra": {"hard_failures": sorted(set(hard_failures))},
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, action="append", default=[])
    parser.add_argument("--ctrf", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    merge(args.junit, args.ctrf, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
