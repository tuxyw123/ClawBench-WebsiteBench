"""Build schema-valid JSON results and human-oriented failure reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def build_result(
    *,
    run: dict[str, Any],
    scored: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "websitebench.result.v1",
        "run_id": run["run_id"],
        "site_id": run["site_id"],
        "site_version": run["site_version"],
        "track": run["track"],
        "status": scored["status"],
        "score": scored["score"],
        "dimensions": scored["dimensions"],
        "hard_failures": scored["hard_failures"],
        "journeys": scored["journeys"],
        "seeds": facts.get("seeds", []),
        "resources": facts.get(
            "resources",
            {
                "build_seconds": 0,
                "startup_seconds": 0,
                "image_bytes": 0,
                "source_bytes": 0,
                "peak_memory_bytes": 0,
                "p95_latency_ms": 0,
            },
        ),
        "network": facts.get(
            "network",
            {
                "runtime_requests": 0,
                "blocked_requests": 0,
                "reference_requests": 0,
                "internet_requests": 0,
            },
        ),
        "failures": facts.get("failures", []),
        "evidence": facts.get("evidence", []),
        "versions": facts.get("versions", {}),
        "usage": facts.get(
            "usage",
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "browser_actions": 0,
                "candidate_builds": 0,
                "human_messages": 0,
                "human_minutes": 0,
            },
        ),
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
    }


def validate_result(result: dict[str, Any], schema_path: Path | str) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)


def failure_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Failure Report -- {result['run_id']}",
        "",
        f"- Status: **{result['status']}**",
        f"- Score: **{result['score']:.2f} / 100**",
        f"- Site: `{result['site_id']}@{result['site_version']}`",
        f"- Track: `{result['track']}`",
        "",
        "## Dimension scores",
        "",
        "| Dimension | Score | Passed |",
        "| --- | ---: | ---: |",
    ]
    for name, dimension in result["dimensions"].items():
        lines.append(
            f"| {name} | {dimension['score']:.2f} / {dimension['max_score']:.0f} | "
            f"{dimension['passed']} / {dimension['total']} |"
        )
    if result["hard_failures"]:
        lines += ["", "## Hard failures", ""]
        for failure in result["hard_failures"]:
            lines.append(f"- `{failure['code']}` -- {failure['message']}")
    lines += ["", "## Journey results", ""]
    for journey in result["journeys"]:
        marker = "PASS" if journey["terminal_passed"] and journey["score"] == 5 else "FAIL"
        lines.append(
            f"- **{marker}** `{journey['id']}`: {journey['score']:.2f} / 5 "
            f"(seed {journey['seed']})"
        )
        for checkpoint in journey["checkpoints"]:
            if not checkpoint["passed"]:
                lines.append(
                    f"  - `{checkpoint['id']}` expected `{checkpoint['expected']}`, "
                    f"actual `{checkpoint['actual']}`"
                )
    if result["failures"]:
        lines += ["", "## Actionable failures", ""]
        for failure in result["failures"]:
            lines += [
                f"### {failure['id']} -- {failure['summary']}",
                "",
                f"- Category: `{failure['category']}`",
                f"- Severity: `{failure['severity']}`",
                f"- Expected: `{failure['expected']}`",
                f"- Actual: `{failure['actual']}`",
                "- Reproduction:",
            ]
            lines.extend(f"  {index}. {step}" for index, step in enumerate(failure["reproduction"], 1))
            lines.append("")
    if not result["hard_failures"] and not result["failures"]:
        lines += ["", "No actionable failures were recorded.", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_reports(result: dict[str, Any], output_dir: Path | str) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "evaluation-result.json"
    markdown_path = output / "failure-report.md"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(failure_markdown(result), encoding="utf-8")
    return json_path, markdown_path

