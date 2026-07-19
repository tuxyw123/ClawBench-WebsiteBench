#!/usr/bin/env python3
"""Build the private HITL Gate 3 review from a Phase 3 fidelity report."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


REPORT_FORMAT = "clawbench.amazon.phase3-fidelity-report.v1"


def load_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("format") != REPORT_FORMAT:
        raise ValueError(f"unsupported Phase 3 report: {report.get('format')!r}")
    if not isinstance(report.get("captures"), list):
        raise ValueError("Phase 3 captures must be a list")
    return report


def strict_failures(report: dict[str, Any]) -> list[str]:
    summary = report["summary"]
    failures = []
    if summary["captureCount"] != summary["expectedCaptureCount"]:
        failures.append("incomplete capture matrix")
    if summary["semanticPassed"] != summary["captureCount"]:
        failures.append("semantic state failures")
    if summary["stable"] != summary["captureCount"]:
        failures.append("unstable clone frames")
    if summary["directVisualFailed"]:
        failures.append("direct visual threshold failures")
    for key in (
        "externalRequests",
        "requestFailures",
        "pageErrors",
        "horizontalOverflows",
    ):
        if summary[key]:
            failures.append(key)
    return failures


def build_review(report: dict[str, Any]) -> dict[str, Any]:
    direct = [
        capture
        for capture in report["captures"]
        if capture.get("directVisualPass") is not None
    ]
    structural = [
        capture
        for capture in report["captures"]
        if capture.get("mode") == "structural" and capture.get("comparable")
    ]
    unavailable = [capture for capture in report["captures"] if not capture.get("comparable")]
    structural_scores = [capture["structuralMetrics"]["score"] for capture in structural]
    quality_counts = Counter(capture.get("sourceQuality", "unknown") for capture in report["captures"])
    return {
        "capturedAt": report.get("capturedAt"),
        "sourceBaseline": report["sourceBaseline"],
        "cloneBaseline": report["cloneBaseline"],
        "thresholds": report["directVisualThresholds"],
        "summary": report["summary"],
        "failures": strict_failures(report),
        "qualityCounts": dict(quality_counts),
        "direct": direct,
        "structural": {
            "count": len(structural_scores),
            "minimum": round(min(structural_scores), 4) if structural_scores else None,
            "median": round(statistics.median(structural_scores), 4) if structural_scores else None,
            "maximum": round(max(structural_scores), 4) if structural_scores else None,
        },
        "unavailable": unavailable,
        "reviewPairs": report.get("reviewPairs", []),
    }


def render_markdown(review: dict[str, Any]) -> str:
    summary = review["summary"]
    baseline = review["sourceBaseline"]
    clone = review["cloneBaseline"]
    thresholds = review["thresholds"]
    lines = [
        "# Amazon frozen-baseline fidelity — HITL Gate 3",
        "",
        f"- Captured: `{review['capturedAt']}`",
        f"- Frozen source snapshot: `{baseline['snapshotId']}`",
        f"- Frozen source report SHA-256: `{baseline['reportSha256']}`",
        f"- Source network policy: `{baseline['networkPolicy']}` (no live-source requests in Phase 3)",
        f"- Clone locale: `{clone['locale']}` / `{clone['currency']}` / `{clone['deliveryRegion']}`",
        "",
        "## Strict result",
        "",
        f"- State matrix: {summary['captureCount']}/{summary['expectedCaptureCount']}",
        f"- Semantic checks: {summary['semanticPassed']}/{summary['captureCount']}",
        f"- Two-frame stable: {summary['stable']}/{summary['captureCount']}",
        f"- Direct visual checks: {summary['directVisualPassed']}/{summary['directVisualEligible']}",
        f"- Structural comparisons: {summary['structuralComparable']}",
        f"- Explicitly unavailable source comparisons: {summary['sourceUnavailable']}",
        f"- External requests / request failures / page errors: {summary['externalRequests']} / {summary['requestFailures']} / {summary['pageErrors']}",
        f"- Horizontal overflows: {summary['horizontalOverflows']}",
    ]
    if review["failures"]:
        lines.extend(["", "Blocking failures: " + ", ".join(review["failures"]) + "."])
    else:
        lines.extend(["", "**Gate 3 automated checks pass.**"])

    lines.extend(
        [
            "",
            "## Comparison policy",
            "",
            "- `direct-visual`: equal-size frozen source and clone viewports are scored with SSIM, edge F1, color-histogram similarity, normalized MAE, and a declared composite.",
            "- `structural`: image metrics and DOM-shape diagnostics are retained for review, but do not claim pixel identity when source layout, region, catalog, or access boundary differs.",
            "- `unavailable`: HTTP 202/protected, expected-error, or near-uniform source screenshots are never scored as visual truth.",
            "- Runtime assets are independently authored and same-origin; source media is not copied into the clone.",
            "",
            "Declared direct thresholds: "
            f"composite ≥ {thresholds['composite']}, SSIM ≥ {thresholds['ssim']}, "
            f"edge F1 ≥ {thresholds['edge_f1']}, histogram ≥ {thresholds['color_histogram']}, "
            f"normalized MAE ≤ {thresholds['normalized_mae_max']}.",
            "",
            "## Direct visual results",
            "",
            "| State | Viewport | SSIM | Edge F1 | Histogram | NMAE | Composite |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for capture in review["direct"]:
        metrics = capture["visualMetrics"]
        lines.append(
            f"| `{capture['scene']}` | `{capture['viewport']}` | "
            f"{metrics['ssim']:.4f} | {metrics['edge_f1']:.4f} | "
            f"{metrics['color_histogram']:.4f} | {metrics['normalized_mae']:.4f} | "
            f"{metrics['composite']:.4f} |"
        )

    structural = review["structural"]
    lines.extend(
        [
            "",
            "## Structural diagnostics",
            "",
            f"- Comparable states: {structural['count']}",
            f"- Diagnostic score range: {structural['minimum']}–{structural['maximum']}; median {structural['median']}",
            "- This score combines DOM count ratios, document-height ratio, and source heading-token recall. It is diagnostic only and has no Gate 3 pass threshold.",
            "",
            "## Representative side-by-side review",
            "",
        ]
    )
    for pair in review["reviewPairs"]:
        label = Path(pair).stem.removeprefix("source-clone-").replace("-", " ")
        lines.append(f"- [{label}]({pair})")

    lines.extend(
        [
            "",
            "## High-impact correction made in Phase 3",
            "",
            "The frozen source `/account` viewport showed the anonymous **Your Account** dashboard, while the Gate 2 clone showed a single sign-in panel. The clone now renders the source-shaped 12-card dashboard and lower preference grids at all five viewports. Account, payment, address, and service mutations still stop at explicit local no-effect boundaries; no credential, address, or payment data is requested.",
            "",
            "## Limits carried forward",
            "",
            "- Germany/EUR in source evidence is an observed source-region artifact; deterministic clone behavior remains New York 10001/USD as approved at Gate 1.",
            "- Independently authored product artwork and the 200-item synthetic catalog preserve shopping semantics but are not source media copies.",
            "- Protected/blank source states cannot support visual equivalence claims.",
            "- BrowserUse live-source/clone trajectory comparison has not run yet; it remains Phase 4 and is blocked until Gate 3 approval.",
            "",
            "## Approval requested",
            "",
            "Approve Gate 3 to authorize the final BrowserUse original-vs-clone trajectory phase. Without approval, no live-source BrowserUse session will run.",
            "",
        ]
    )
    return "\n".join(lines)


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = load_report(args.report)
    review = build_review(report)
    output = args.output or args.report.parent / "GATE3_REVIEW.md"
    private_write(output, render_markdown(review))
    print(json.dumps({"output": str(output), "failures": review["failures"]}))
    return 1 if review["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
