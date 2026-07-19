#!/usr/bin/env python3
"""Generate the private human-review summary for Amazon HITL Gate 2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_FORMAT = "clawbench.amazon.phase2-browser-verification.v1"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate(
    report: dict[str, Any], journeys: dict[str, Any], root: Path
) -> None:
    if report.get("format") != EXPECTED_FORMAT:
        raise ValueError("unsupported Gate 2 report format")
    if report.get("journeyCount") != 14 or len(report.get("journeys", [])) != 14:
        raise ValueError("Gate 2 requires fourteen browser journeys")
    if not all(item.get("passed") is True for item in report["journeys"]):
        raise ValueError("a Gate 2 browser journey did not pass")
    if journeys.get("format") != "clawbench.amazon.phase2-journeys.v1":
        raise ValueError("unsupported journey contract")
    expected_ids = [item["id"] for item in journeys.get("journeys", [])]
    actual_ids = [item["id"] for item in report["journeys"]]
    if expected_ids != actual_ids:
        raise ValueError("browser report does not match the journey contract")
    if report.get("externalRuntimeRequests") != 0:
        raise ValueError("Gate 2 browser run made an external request")
    runtime = report.get("runtime", {})
    if any(runtime.get(key) for key in ("externalRequests", "requestFailures", "pageErrors")):
        raise ValueError("Gate 2 browser run contains runtime failures")
    for screenshot in report.get("screenshots", []):
        path = root / screenshot["file"]
        body = path.read_bytes()
        if len(body) != screenshot["bytes"]:
            raise ValueError(f"screenshot size mismatch: {path.name}")
        if hashlib.sha256(body).hexdigest() != screenshot["sha256"]:
            raise ValueError(f"screenshot hash mismatch: {path.name}")


def render(report: dict[str, Any], legacy_report: dict[str, Any]) -> str:
    legacy_counts = legacy_report.get("verification", {}).get("overall_assertions", {})
    screenshots = {item["name"]: item for item in report["screenshots"]}
    visual_notes = (
        ("Desktop storefront", "desktop-home", "Dense ten-department storefront and product rails are readable."),
        ("Desktop search", "desktop-search", "Facets, sorting, sixteen-result page, prices, and actions remain legible."),
        ("Desktop task cart", "desktop-task-cart", "Quantity two and $439.98 subtotal are prominent."),
        ("Mobile storefront", "mobile-home", "Responsive modules become horizontal rails without broken controls."),
        ("Mobile category", "mobile-category", "Header, category rail, featured shops, and prices follow the source hierarchy."),
        ("Mobile task product", "mobile-task-product", "Gallery, variants, quantity, Add to cart, reviews, and footer remain reachable."),
    )
    lines = [
        "# Amazon clone — HITL Gate 2",
        "",
        "## Outcome",
        "",
        "- Public runtime: FastAPI SSR with progressively enhanced local JavaScript.",
        "- State engine: strict loopback-only request service backed by SQLite.",
        "- Catalog: 200 products, 10 departments, 20 categories.",
        "- Locale: en-US, USD, New York 10001.",
        f"- Browser journeys: {report['journeyCount']}/14 passed with {report['assertions']} assertions.",
        f"- Legacy task/security regression: {legacy_counts.get('passed', 0)}/{legacy_counts.get('total', 0)} passed.",
        "- Runtime audit: zero external requests, request failures, and page errors.",
        "",
        "## Architecture boundary",
        "",
        "`Browser → FastAPI SSR edge → loopback state engine → SQLite`",
        "",
        "FastAPI owns the exposed socket, SSR shell, static assets, and security headers. The internal engine preserves the exact benchmark terminal request, validation, journal, persistence, and isolation semantics. Checkout, identity, payment, delivery changes, and real orders remain local no-effect boundaries.",
        "",
        "## Fourteen journeys",
        "",
    ]
    for item in report["journeys"]:
        lines.append(
            f"- `{item['id']}` {item['name']}: passed ({item['assertions']} assertions)"
        )
    lines.extend(["", "## Representative visual review", ""])
    for label, name, note in visual_notes:
        screenshot = screenshots[name]
        lines.append(f"- [{label}]({screenshot['file']}): {note}")
    lines.extend(
        [
            "",
            "## Intentional boundaries",
            "",
            "- Raw Amazon HTML, media, and screenshots remain private Gate 1 evidence and are not runtime assets.",
            "- The 200-product catalog intentionally reuses twelve independently authored sprite cells; product identity is carried by title, brand, category, price, variants, and ASIN.",
            "- Account, orders, checkout, payment, Buy Now, and delivery changes remain visible safe stops.",
            "- BrowserUse comparison against the live source is reserved for the later interaction-parity phase; Gate 2 validates the clone itself.",
            "",
            "## Approval requested",
            "",
            "Approve Gate 2 to freeze the SSR/catalog implementation and proceed to the next fidelity phase.",
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
    parser.add_argument("--journeys", type=Path, required=True)
    parser.add_argument("--legacy-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = load_json(args.report)
    journeys = load_json(args.journeys)
    validate(report, journeys, args.report.parent)
    legacy_report = load_json(args.legacy_report)
    output = args.output or args.report.parent / "GATE2_REVIEW.md"
    private_write(output, render(report, legacy_report))
    print(json.dumps({"output": str(output), "journeys": report["journeyCount"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
