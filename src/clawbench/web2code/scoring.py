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
    aggregation_kind = journey_config.get("aggregation_kind", "sum-journeys-v1")
    journey_facts = facts.get("journeys", [])
    if aggregation_kind == "normalized-executions-v1" and not facts.get("hard_failures"):
        expected_count = int(
            journey_config.get(
                "execution_count",
                journey_config["execution_score_total"] / journey_config["journey_max_score"],
            )
        )
        if len(journey_facts) != expected_count:
            raise ValueError(
                f"normalized journey facts require {expected_count} executions, got {len(journey_facts)}"
            )
        identities = [(item.get("id"), int(item.get("seed", -1))) for item in journey_facts]
        if len(set(identities)) != len(identities):
            raise ValueError("normalized journey facts contain duplicate journey-seed executions")
        expected_journeys = set(journey_config.get("journeys", []))
        actual_journeys = {identifier for identifier, _seed in identities}
        if actual_journeys != expected_journeys:
            raise ValueError(
                f"normalized journey identities mismatch; expected={sorted(expected_journeys)}, "
                f"actual={sorted(actual_journeys)}"
            )
        for journey_id in expected_journeys:
            if sum(identifier == journey_id for identifier, _seed in identities) != 2:
                raise ValueError(f"normalized journey {journey_id} must run on exactly two seeds")
    journey_results: list[dict[str, Any]] = []
    journey_execution_score = 0.0
    journey_passed = 0
    for journey in journey_facts:
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
        journey_execution_score += score
        if terminal_passed and checkpoint_passed == len(checkpoints) and checkpoints:
            journey_passed += 1
        journey_results.append(
            {
                "id": journey["id"],
                "seed": int(journey["seed"]),
                "score": score,
                "max_score": journey_config["journey_max_score"],
                "terminal_passed": terminal_passed,
                "checkpoints": checkpoints,
            }
        )

    if aggregation_kind == "normalized-executions-v1":
        denominator = float(journey_config["execution_score_total"])
        journey_score = journey_execution_score / denominator * journey_config["max_score"]
    else:
        journey_score = journey_execution_score

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
        "visual": _dimension(
            visual_score, dimensions_config["visual"]["max_score"], visual_passed, len(visual_facts)
        ),
        "interactions": _dimension(
            interaction_score,
            dimensions_config["interactions"]["max_score"],
            interaction_passed,
            len(interactions),
        ),
        "journeys": _dimension(
            journey_score, journey_config["max_score"], journey_passed, len(journey_facts)
        ),
        "robustness": _dimension(
            robustness_score,
            dimensions_config["robustness"]["max_score"],
            robustness_passed,
            len(robustness),
        ),
        "efficiency": _dimension(
            efficiency_score,
            dimensions_config["efficiency"]["max_score"],
            efficiency_passed,
            len(targets),
        ),
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
