from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1] / "tools" / "summarize_phase3.py"
SPEC = importlib.util.spec_from_file_location("amazon_phase3_summary", TOOL)
assert SPEC and SPEC.loader
summary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = summary
SPEC.loader.exec_module(summary)


def report() -> dict:
    return {
        "format": summary.REPORT_FORMAT,
        "capturedAt": "2026-07-18T00:00:00Z",
        "sourceBaseline": {
            "snapshotId": "snapshot",
            "reportSha256": "abc",
            "networkPolicy": "frozen-evidence-only",
        },
        "cloneBaseline": {
            "locale": "en-US",
            "currency": "USD",
            "deliveryRegion": "New York 10001",
        },
        "directVisualThresholds": {
            "composite": 0.35,
            "ssim": 0.18,
            "edge_f1": 0.08,
            "color_histogram": 0.55,
            "normalized_mae_max": 0.5,
        },
        "summary": {
            "captureCount": 1,
            "expectedCaptureCount": 1,
            "semanticPassed": 1,
            "stable": 1,
            "directVisualEligible": 1,
            "directVisualPassed": 1,
            "directVisualFailed": 0,
            "structuralComparable": 0,
            "sourceUnavailable": 0,
            "externalRequests": 0,
            "requestFailures": 0,
            "pageErrors": 0,
            "horizontalOverflows": 0,
        },
        "reviewPairs": ["review-pairs/pair.jpg"],
        "captures": [
            {
                "scene": "account-entry-live",
                "viewport": "desktop",
                "mode": "direct-visual",
                "comparable": True,
                "sourceQuality": "strong",
                "directVisualPass": True,
                "visualMetrics": {
                    "ssim": 0.7,
                    "edge_f1": 0.3,
                    "color_histogram": 0.9,
                    "normalized_mae": 0.1,
                    "composite": 0.7,
                },
                "structuralMetrics": {"score": 0.5},
            }
        ],
    }


def test_strict_review_passes_and_names_phase4_boundary() -> None:
    review = summary.build_review(report())
    markdown = summary.render_markdown(review)
    assert review["failures"] == []
    assert "Gate 3 automated checks pass" in markdown
    assert "BrowserUse" in markdown
    assert "12-card dashboard" in markdown


def test_strict_review_reports_visual_failure() -> None:
    value = report()
    value["summary"]["directVisualFailed"] = 1
    assert "direct visual threshold failures" in summary.strict_failures(value)


def test_private_write_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "private" / "review.md"
    summary.private_write(path, "review")
    assert path.read_text() == "review"
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
