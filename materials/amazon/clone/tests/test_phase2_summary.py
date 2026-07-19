from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


SUMMARY_PATH = Path(__file__).resolve().parents[1] / "tools" / "summarize_phase2.py"
SPEC = importlib.util.spec_from_file_location("amazon_phase2_summary", SUMMARY_PATH)
assert SPEC and SPEC.loader
summary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = summary
SPEC.loader.exec_module(summary)


def test_validation_rejects_incomplete_journeys(tmp_path: Path) -> None:
    report = {
        "format": summary.EXPECTED_FORMAT,
        "journeyCount": 13,
        "journeys": [],
        "externalRuntimeRequests": 0,
        "runtime": {},
        "screenshots": [],
    }
    with pytest.raises(ValueError, match="fourteen"):
        summary.validate(report, {"format": "x"}, tmp_path)


def test_private_write_is_owner_only(tmp_path: Path) -> None:
    output = tmp_path / "gate" / "review.md"
    summary.private_write(output, "review")
    assert output.read_text() == "review"
    assert os.stat(output.parent).st_mode & 0o777 == 0o700
    assert os.stat(output).st_mode & 0o777 == 0o600
