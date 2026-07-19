from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


CAPTURE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture_public_source.py"
SPEC = importlib.util.spec_from_file_location("amazon_source_capture", CAPTURE_PATH)
assert SPEC and SPEC.loader
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)


def test_capture_matrix_is_the_frozen_phase_one_scope() -> None:
    assert list(capture.VIEWPORTS) == [
        "desktop",
        "desktop-compact",
        "tablet",
        "mobile",
        "mobile-small",
    ]
    assert len(capture.PAGES) == 19
    assert len({page.name for page in capture.PAGES}) == len(capture.PAGES)
    assert {page.state for page in capture.PAGES} == {"loaded", "menu", "autocomplete"}


def test_evidence_store_is_private_and_content_addressed(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    store = capture.EvidenceStore(root)
    first = store.put(b"same source bytes")
    second = store.put(b"same source bytes")

    assert first == second
    assert store.unique_bytes == len(b"same source bytes")
    assert len(store.unique_objects) == 1
    object_path = root / first["objectPath"]
    assert object_path.read_bytes() == b"same source bytes"
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(object_path).st_mode & 0o777 == 0o600


def test_url_and_report_redaction_reject_sensitive_values() -> None:
    sanitized = capture.sanitize_url(
        "https://www.amazon.com/s?k=portable+ssd&session-id=secret&ref=abc",
        keep_public_query=True,
    )
    assert "portable+ssd" in sanitized
    assert "secret" not in sanitized
    assert "%3Credacted%3E" in sanitized

    capture.assert_redacted({"safe": sanitized})
    with pytest.raises(AssertionError, match="sensitive source token"):
        capture.assert_redacted({"unsafe": "ak_bmsc=secret"})


def test_response_storage_summary_separates_visual_resources() -> None:
    rows = [
        {
            "method": "GET",
            "resourceType": "image",
            "bodyStored": True,
            "bodyStorage": "private-content-addressed-object",
        },
        {
            "method": "GET",
            "resourceType": "script",
            "bodyStored": False,
            "bodyStorage": "unavailable",
        },
        {
            "method": "POST",
            "resourceType": "fetch",
            "bodyStored": False,
            "bodyStorage": "not-in-capture-scope",
        },
    ]
    assert capture.response_storage_summary(rows) == {
        "attempted": 2,
        "stored": 1,
        "failed": 1,
        "overExplicitSizeLimit": 0,
        "notFinishedAtCapture": 0,
        "visualResponses": 1,
        "visualStored": 1,
    }


def test_finalize_response_rows_closes_late_callback_boundary() -> None:
    rows = [
        {
            "method": "GET",
            "resourceType": "stylesheet",
            "bodyStorage": None,
            "bodyHashError": None,
            "_requestFinished": True,
            "_requestFailure": None,
        }
    ]
    capture.finalize_response_rows(rows)

    assert rows == [
        {
            "method": "GET",
            "resourceType": "stylesheet",
            "bodyStorage": "not-finished-at-capture",
            "bodyHashError": "response-observed-after-body-capture",
        }
    ]


def test_consistency_check_detects_tampered_object(tmp_path: Path) -> None:
    store = capture.EvidenceStore(tmp_path)
    html = store.put(b"<html></html>")
    ax_tree = store.put_json({"nodes": []})
    screenshot_path = tmp_path / "source.png"
    screenshot_path.write_bytes(b"png")
    screenshot = {
        "file": screenshot_path.name,
        "bytes": 3,
        "sha256": capture.sha256(b"png"),
    }
    report = {
        "pages": [
            {
                "html": html,
                "accessibilityTree": ax_tree,
                "screenshot": screenshot,
                "fullPageScreenshot": screenshot,
            }
        ],
        "network": [],
    }
    capture.assert_evidence_consistent(report, tmp_path)
    (tmp_path / html["objectPath"]).write_bytes(b"tampered")
    with pytest.raises(AssertionError, match="size mismatch"):
        capture.assert_evidence_consistent(report, tmp_path)


def test_checkpoint_round_trip_supports_resumable_capture(tmp_path: Path) -> None:
    store = capture.EvidenceStore(tmp_path)
    page = {"page": "storefront-home-live", "viewport": "desktop"}
    network = [{"url": "https://www.amazon.com/", "bodyStored": False}]
    capture.write_checkpoint(store, page, network)

    assert capture.load_checkpoint(store, "desktop", "storefront-home-live") == (
        page,
        network,
    )
    checkpoint = capture.checkpoint_path(store, "desktop", "storefront-home-live")
    assert os.stat(checkpoint).st_mode & 0o777 == 0o600
