#!/usr/bin/env python3
"""Build a compact human-review gate from a private source-capture report."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


REPORT_FORMAT = "clawbench-pro.public-source-observation.v2"
VIEWPORT_LABELS = {
    "desktop": "D",
    "desktop-compact": "DC",
    "tablet": "T",
    "mobile": "M",
    "mobile-small": "MS",
}
QUALITY_LABELS = {
    "strong": "strong",
    "partial": "partial",
    "protected-or-empty": "protected/empty",
    "expected-error": "expected error",
}

REPRESENTATIVE_SCREENSHOTS = (
    (
        "Best Sellers desktop",
        "source-all-departments-best-sellers-live-desktop.png",
        "Strong list/ranking baseline.",
    ),
    (
        "Filtered search desktop compact",
        "source-portable-ssd-filtered-search-live-desktop-compact.png",
        "Strong search, filter, result-card, and sort baseline.",
    ),
    (
        "Computers mobile",
        "source-computers-category-live-mobile.png",
        "Strong responsive header, category, carousel, and price baseline.",
    ),
    (
        "Stable product response render mobile",
        "source-samsung-t7-product-response-render-mobile.png",
        "Complete product evidence; the source response retains a desktop-like dense layout.",
    ),
    (
        "Empty cart desktop",
        "source-empty-cart-live-desktop.png",
        "Strong anonymous empty-cart baseline.",
    ),
    (
        "Account desktop",
        "source-account-entry-live-desktop.png",
        "Strong anonymous account-entry baseline.",
    ),
    (
        "Storefront home desktop",
        "source-storefront-home-live-desktop.png",
        "Source protection boundary: HTTP 202 and blank viewport, not a clone target.",
    ),
    (
        "External SSD Best Sellers desktop",
        "source-best-sellers-external-ssd-live-desktop.png",
        "Rich DOM evidence but incomplete viewport paint; use DOM/full-page evidence cautiously.",
    ),
)


def load_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("format") != REPORT_FORMAT:
        raise ValueError(f"unsupported report format: {report.get('format')!r}")
    if not isinstance(report.get("pages"), list):
        raise ValueError("report pages must be a list")
    if not isinstance(report.get("network"), list):
        raise ValueError("report network must be a list")
    return report


def classify_capture(page: dict[str, Any]) -> str:
    status = page.get("navigationStatus")
    body_length = page.get("dom", {}).get("captureQuality", {}).get("bodyTextLength", 0)
    if isinstance(status, int) and status >= 400:
        return "expected-error"
    if status == 202 or not isinstance(body_length, int) or body_length <= 0:
        return "protected-or-empty"
    if body_length >= 1000:
        return "strong"
    return "partial"


def _matrix_expected_count(report: dict[str, Any]) -> int:
    matrix = report.get("captureMatrix", {})
    return len(matrix.get("viewports", {})) * (
        len(matrix.get("pages", [])) + len(matrix.get("getResponseRenders", []))
    )


def _regional_summary(report: dict[str, Any]) -> dict[str, Any]:
    requested = report.get("source", {}).get("requestedRegionalBaseline", {})
    signals = report.get("source", {}).get("observedRegionalSignals", [])
    delivery = Counter(
        signal.get("deliveryText")
        for signal in signals
        if isinstance(signal.get("deliveryText"), str) and signal.get("deliveryText")
    )
    currencies: Counter[str] = Counter()
    for signal in signals:
        for sample in signal.get("currencySamples", []):
            if isinstance(sample, str):
                prefix = sample.strip().split(maxsplit=1)[0]
                if prefix:
                    currencies[prefix] += 1
    return {
        "requested": requested,
        "delivery": delivery.most_common(3),
        "currencies": currencies.most_common(5),
    }


def _external_hosts(report: dict[str, Any]) -> Counter[str]:
    hosts: Counter[str] = Counter()
    for response in report.get("network", []):
        if response.get("amazonControlledHost") is False:
            host = urlsplit(response.get("url", "")).hostname
            if host:
                hosts[host] += 1
    return hosts


def build_review(report: dict[str, Any]) -> dict[str, Any]:
    pages = report["pages"]
    qualities = Counter(classify_capture(page) for page in pages)
    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    screenshots = 0
    full_page_screenshots = 0
    interaction_failures: list[dict[str, str]] = []

    for page in pages:
        page_name = str(page.get("page", "unknown"))
        viewport = str(page.get("viewport", "unknown"))
        grouped[page_name][viewport] = classify_capture(page)
        screenshots += int(page.get("screenshot", {}).get("available") is True)
        full_page_screenshots += int(page.get("fullPageScreenshot", {}).get("available") is True)
        interaction = page.get("interactionState", {})
        if interaction.get("applied") is False:
            interaction_failures.append(
                {
                    "page": page_name,
                    "viewport": viewport,
                    "detail": str(interaction.get("detail", "unavailable")),
                }
            )

    representative = []
    root = Path(".")
    for title, filename, note in REPRESENTATIVE_SCREENSHOTS:
        representative.append(
            {"title": title, "file": str(root / filename), "note": note}
        )

    return {
        "snapshotId": report.get("snapshotId"),
        "capturedAt": report.get("capturedAt"),
        "actualCaptureCount": len(pages),
        "expectedCaptureCount": _matrix_expected_count(report),
        "qualities": dict(qualities),
        "groupedQualities": dict(grouped),
        "screenshots": screenshots,
        "fullPageScreenshots": full_page_screenshots,
        "interactionFailures": interaction_failures,
        "regional": _regional_summary(report),
        "externalHosts": dict(_external_hosts(report)),
        "totals": report.get("totals", {}),
        "accessBoundary": report.get("accessBoundary", {}),
        "representativeScreenshots": representative,
    }


def render_markdown(review: dict[str, Any]) -> str:
    totals = review["totals"]
    storage = totals.get("responseBodyStorage", {})
    evidence = totals.get("evidenceStore", {})
    regional = review["regional"]
    requested = regional.get("requested", {})
    requested_text = "/".join(
        str(requested.get(key, "unknown"))
        for key in ("locale", "currency", "deliveryRegion")
    )
    observed_delivery = ", ".join(
        f"{value} ({count})" for value, count in regional.get("delivery", [])
    ) or "none"
    observed_currencies = ", ".join(
        f"{value} ({count})" for value, count in regional.get("currencies", [])
    ) or "none"

    lines = [
        "# Amazon source evidence — HITL Gate 1",
        "",
        f"- Snapshot: `{review['snapshotId']}`",
        f"- Captured: `{review['capturedAt']}`",
        (
            "- Matrix: "
            f"{review['actualCaptureCount']}/{review['expectedCaptureCount']} page/viewport states"
        ),
        (
            "- Screenshots: "
            f"{review['screenshots']} viewport and {review['fullPageScreenshots']} full-page"
        ),
        (
            "- Network bodies: "
            f"{storage.get('stored', 0)}/{storage.get('attempted', 0)} stored; "
            f"{storage.get('failed', 0)} failed; "
            f"{storage.get('notFinishedAtCapture', 0)} unfinished at capture"
        ),
        (
            "- Evidence store: "
            f"{evidence.get('uniqueObjects', 0)} unique objects, "
            f"{evidence.get('uniqueBytes', 0):,} bytes"
        ),
        f"- Blocked source writes: {totals.get('blockedNonGetRequests', 0)} non-GET requests",
        "",
        "## Gate decisions",
        "",
        (
            f"1. Requested clone baseline is `{requested_text}`; observed public source "
            f"delivery is `{observed_delivery}` and observed currency prefixes are "
            f"`{observed_currencies}`. Phase 2 should normalize the deterministic clone "
            "to New York/USD while treating Germany/EUR only as source evidence."
        ),
        (
            "2. HTTP 202/blank and other sparse protection states are availability "
            "boundaries, not visual targets. Rich states and the stable product response "
            "render are the implementation baselines."
        ),
        (
            "3. Raw HTML, media, response bodies, and screenshots remain private and "
            "gitignored. Clone runtime assets must be independently authored."
        ),
        "",
        "## Quality summary",
        "",
    ]
    for key in ("strong", "partial", "protected-or-empty", "expected-error"):
        lines.append(f"- {QUALITY_LABELS[key]}: {review['qualities'].get(key, 0)}")

    lines.extend(
        [
            "",
            "`D` desktop, `DC` desktop compact, `T` tablet, `M` mobile, `MS` mobile small.",
            "",
            "| State | D | DC | T | M | MS |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for page_name, viewport_map in review["groupedQualities"].items():
        cells = []
        for viewport in VIEWPORT_LABELS:
            quality = viewport_map.get(viewport, "missing")
            cells.append(QUALITY_LABELS.get(quality, quality))
        lines.append(f"| `{page_name}` | " + " | ".join(cells) + " |")

    lines.extend(["", "## Representative visual review", ""])
    for item in review["representativeScreenshots"]:
        lines.append(f"- [{item['title']}]({item['file']}): {item['note']}")

    lines.extend(["", "## Interaction and network boundaries", ""])
    if review["interactionFailures"]:
        for item in review["interactionFailures"]:
            lines.append(
                f"- `{item['page']}` / `{item['viewport']}`: {item['detail']}"
            )
    else:
        lines.append("- All requested interaction states were applied.")

    external_hosts = review["externalHosts"]
    if external_hosts:
        lines.append("")
        lines.append(
            "- External-to-Amazon-domain responses: "
            + ", ".join(f"`{host}` ({count})" for host, count in external_hosts.items())
            + ". These are source AWS WAF challenge infrastructure, not clone dependencies."
        )

    lines.extend(
        [
            "",
            "## Approval requested",
            "",
            "Approve Gate 1 only if the three gate decisions above are acceptable. "
            "Phase 2 remains blocked until explicit human approval.",
            "",
        ]
    )
    return "\n".join(lines)


def private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Private capture report.json")
    parser.add_argument(
        "--output",
        type=Path,
        help="Review Markdown path (default: GATE1_REVIEW.md beside report)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = load_report(args.report)
    review = build_review(report)
    if review["actualCaptureCount"] != review["expectedCaptureCount"]:
        raise ValueError(
            "incomplete capture matrix: "
            f"{review['actualCaptureCount']}/{review['expectedCaptureCount']}"
        )
    output = args.output or args.report.parent / "GATE1_REVIEW.md"
    private_write(output, render_markdown(review))
    print(json.dumps({"output": str(output), "qualities": review["qualities"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
