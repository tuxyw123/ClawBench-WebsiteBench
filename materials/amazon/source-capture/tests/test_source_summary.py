from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


SUMMARY_PATH = Path(__file__).resolve().parents[1] / "tools" / "summarize_source_capture.py"
SPEC = importlib.util.spec_from_file_location("amazon_source_summary", SUMMARY_PATH)
assert SPEC and SPEC.loader
summary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = summary
SPEC.loader.exec_module(summary)


def capture(status: int, body_length: int) -> dict:
    return {
        "navigationStatus": status,
        "dom": {"captureQuality": {"bodyTextLength": body_length}},
    }


@pytest.mark.parametrize(
    ("status", "body_length", "expected"),
    [
        (200, 1000, "strong"),
        (200, 999, "partial"),
        (200, 0, "protected-or-empty"),
        (202, 5000, "protected-or-empty"),
        (404, 0, "expected-error"),
    ],
)
def test_capture_quality_classification(
    status: int, body_length: int, expected: str
) -> None:
    assert summary.classify_capture(capture(status, body_length)) == expected


def test_load_report_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"format": "wrong", "pages": [], "network": []}))
    with pytest.raises(ValueError, match="unsupported report format"):
        summary.load_report(path)


def test_review_is_complete_and_contains_gate_decisions() -> None:
    report = {
        "format": summary.REPORT_FORMAT,
        "snapshotId": "snapshot",
        "capturedAt": "2026-07-18T00:00:00Z",
        "captureMatrix": {
            "viewports": {"desktop": {"width": 100, "height": 100}},
            "pages": [{"name": "home"}],
            "getResponseRenders": [],
        },
        "source": {
            "requestedRegionalBaseline": {
                "locale": "en-US",
                "currency": "USD",
                "deliveryRegion": "New York 10001",
            },
            "observedRegionalSignals": [
                {
                    "deliveryText": "Deliver to Germany",
                    "currencySamples": ["EUR 1.00"],
                }
            ],
        },
        "pages": [
            {
                "page": "home",
                "viewport": "desktop",
                "navigationStatus": 200,
                "dom": {"captureQuality": {"bodyTextLength": 1200}},
                "screenshot": {"available": True},
                "fullPageScreenshot": {"available": True},
                "interactionState": {"applied": True},
            }
        ],
        "network": [
            {
                "url": "https://unit.token.awswaf.com/challenge.js",
                "amazonControlledHost": False,
            }
        ],
        "totals": {
            "responseBodyStorage": {"attempted": 1, "stored": 1},
            "evidenceStore": {"uniqueObjects": 1, "uniqueBytes": 5},
            "blockedNonGetRequests": 1,
        },
        "accessBoundary": {},
    }
    review = summary.build_review(report)
    markdown = summary.render_markdown(review)

    assert review["actualCaptureCount"] == review["expectedCaptureCount"] == 1
    assert review["qualities"] == {"strong": 1}
    assert "normalize the deterministic clone" in markdown
    assert "token.awswaf.com" in markdown
    assert "Phase 2 remains blocked" in markdown


def test_private_write_uses_owner_only_permissions(tmp_path: Path) -> None:
    output = tmp_path / "private" / "review.md"
    summary.private_write(output, "review")

    assert output.read_text() == "review"
    if os.name != "nt":
        assert os.stat(output.parent).st_mode & 0o777 == 0o700
        assert os.stat(output).st_mode & 0o777 == 0o600
