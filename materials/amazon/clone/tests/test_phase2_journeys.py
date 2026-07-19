from __future__ import annotations

import json
from pathlib import Path


CLONE_ROOT = Path(__file__).resolve().parents[1]
JOURNEYS_PATH = CLONE_ROOT / "phase2-journeys.json"


def test_phase_two_has_fourteen_bounded_journeys() -> None:
    payload = json.loads(JOURNEYS_PATH.read_text(encoding="utf-8"))
    journeys = payload["journeys"]

    assert payload["format"] == "clawbench.amazon.phase2-journeys.v1"
    assert payload["gate"] == 2
    assert len(journeys) == 14
    assert [journey["id"] for journey in journeys] == [
        f"J{index:02d}" for index in range(1, 15)
    ]
    assert len({journey["name"] for journey in journeys}) == 14
    for journey in journeys:
        assert journey["start"].startswith("/")
        assert len(journey["trajectory"]) >= 2
        assert journey["terminal"]
