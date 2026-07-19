"""Pure, reproducible implementation of the frozen 20/20/40/15/5 score."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _dimension(score: float, maximum: float, passed: int, total: int) -> dict[str, Any]:
    return {
        "score": round(max(0.0, min(maximum, score)), 4),
        "max_score": maximum,
        "passed": passed,
        "total": total,
    }


def score_evaluation(facts: dict[str, Any], scoring: dict[str, Any]) -> dict[str, Any]:
    """Return dimension scores, journey records, total, and hard-fail status."""

    dimensions_config = scoring["dimensions"]
    visual_facts = facts.get("visual", [])
    visual_values = [float(item.get("similarity", 0)) for item in visual_facts]
    visual_score = (
        sum(visual_values) / len(visual_values) * dimensions_config["visual"]["max_score"]
        if visual_values
        else 0.0
    )
    visual_passed = sum(value >= 0.8 for value in visual_values)

    interactions = facts.get("interactions", [])
    interaction_passed = sum(bool(item.get("passed")) for item in interactions)
    interaction_score = (
        interaction_passed / len(interactions) * dimensions_config["interactions"]["max_score"]
        if interactions
        else 0.0
    )

    journey_config = dimensions_config["journeys"]
    journey_results: list[dict[str, Any]] = []
    journey_score = 0.0
    journey_passed = 0
    for journey in facts.get("journeys", []):
        checkpoints = deepcopy(journey.get("checkpoints", []))
        checkpoint_passed = sum(bool(item.get("passed")) for item in checkpoints)
        raw_score = (
            checkpoint_passed / len(checkpoints) * journey_config["journey_max_score"]
            if checkpoints
            else 0.0
        )
        terminal_passed = bool(journey.get("terminal_passed"))
        score = raw_score if terminal_passed else min(raw_score, journey_config["terminal_failure_cap"])
        score = round(score, 4)
        journey_score += score
        if terminal_passed and checkpoint_passed == len(checkpoints) and checkpoints:
            journey_passed += 1
        journey_results.append(
            {
                "id": journey["id"],
                "seed": int(journey["seed"]),
                "score": score,
                "max_score": 5,
                "terminal_passed": terminal_passed,
                "checkpoints": checkpoints,
            }
        )

    robustness = facts.get("robustness", [])
    robustness_passed = sum(bool(item.get("passed")) for item in robustness)
    robustness_score = min(
        dimensions_config["robustness"]["max_score"], float(robustness_passed)
    )

    efficiency_metrics = facts.get("efficiency", {})
    targets = dimensions_config["efficiency"]["targets"]
    efficiency_passed = 0
    for key, target in targets.items():
        value = efficiency_metrics.get(key)
        if value is not None and target["operator"] == "<=" and value <= target["value"]:
            efficiency_passed += 1
    efficiency_score = float(efficiency_passed)

    dimensions = {
        "visual": _dimension(visual_score, 20, visual_passed, len(visual_facts)),
        "interactions": _dimension(
            interaction_score, 20, interaction_passed, len(interactions)
        ),
        "journeys": _dimension(
            journey_score, 40, journey_passed, len(facts.get("journeys", []))
        ),
        "robustness": _dimension(
            robustness_score, 15, robustness_passed, len(robustness)
        ),
        "efficiency": _dimension(efficiency_score, 5, efficiency_passed, len(targets)),
    }
    hard_failures = deepcopy(facts.get("hard_failures", []))
    total = round(sum(item["score"] for item in dimensions.values()), 4)
    if hard_failures:
        total = float(scoring.get("hard_failure_score", 0))
    return {
        "score": total,
        "dimensions": dimensions,
        "journeys": journey_results,
        "hard_failures": hard_failures,
        "status": "hard_failed" if hard_failures else ("passed" if total >= 70 else "failed"),
    }

