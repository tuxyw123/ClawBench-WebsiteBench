from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
import yaml
from PIL import Image

from clawbench.offline_clone.manifest import (
    ManifestValidationError,
    load_manifest,
    require_frozen_coverage,
)
from clawbench.offline_clone.report import full_report

from .helpers import initialized_site


def _write_dimensions(root: Path, dimensions: list[dict[str, object]]) -> None:
    (root / "scope/coverage.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.coverage.v1",
                "status": "frozen",
                "dimensions": dimensions,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "scope/purpose.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.purpose.v1",
                "status": "frozen",
                "purpose_id": "coverage-test",
                "statement": "Verify independent frozen coverage dimensions.",
                "primary_actor_ids": ["tester"],
                "mainline_journey_ids": ["coverage-mainline"],
                "out_of_scope": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "scope/invariants.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.invariants.v1",
                "status": "frozen",
                "invariants": [
                    {
                        "id": "coverage-denominator-frozen",
                        "statement": "Frozen denominators remain explicit.",
                        "priority": "p0",
                        "journey_ids": ["coverage-mainline"],
                        "positive_test_refs": ["test.coverage.positive"],
                        "negative_test_refs": ["test.coverage.negative"],
                        "coverage_dimension_ids": [dimensions[0]["id"]] if dimensions else [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "scope/journeys.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.journeys.v1",
                "journeys": [
                    {
                        "id": "coverage-mainline",
                        "kind": "success",
                        "priority": "p0",
                        "status": "frozen",
                        "actor": "tester",
                        "steps": ["inspect the frozen denominator"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_relative = "source-assets/checkpoints/coverage.png"
    source_path = root / source_relative
    source_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1, 1), "white").save(source_path)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    visual_ids = sorted(
        {
            item
            for dimension in dimensions
            if "visual" in dimension.get("required_evidence_kinds", [])
            for item in dimension.get("required_items", [])
            if isinstance(item, str)
        }
    ) or ["coverage-placeholder"]
    (root / "scope/checkpoints.json").write_text(
        json.dumps(
            {
                "schema_version": "offline-clone.checkpoints.v1",
                "status": "frozen",
                "viewports": {},
                "checkpoints": [
                    {
                        "id": checkpoint_id,
                        "visual_contract": {
                            "source_artifact_path": source_relative,
                            "source_artifact_sha256": source_sha256,
                            "viewport": {"width": 1, "height": 1},
                            "comparison_region": {
                                "x": 0,
                                "y": 0,
                                "width": 1,
                                "height": 1,
                            },
                            "metric": "pixel-mae-similarity-v1",
                            "threshold": 0.9,
                        },
                    }
                    for checkpoint_id in visual_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = root / "clone.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if source_relative not in manifest["gates"]["source"]["inputs"]:
        manifest["gates"]["source"]["inputs"].append(source_relative)
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )


def _dimension(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "richly-rendered",
        "label": "Richly rendered entities",
        "unit": "entity",
        "category": "rendering",
        "rationale": "Keep rich rendering independent from reachability.",
        "required_evidence_kinds": ["visual"],
        "required_items": ["item.alpha", "item.beta", "item.gamma"],
        "satisfied_items": [],
    }
    value.update(overrides)
    if value["required_items"] == [] and "required_evidence_kinds" not in overrides:
        value["required_evidence_kinds"] = []
    return value


def test_coverage_rejects_satisfied_items_outside_required_set(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    _write_dimensions(
        root,
        [_dimension(satisfied_items=["item.alpha", "item.not-required"])],
    )
    with pytest.raises(
        ManifestValidationError, match="satisfied item is not required: item.not-required"
    ):
        load_manifest(root)


def test_source_coverage_must_be_explicitly_frozen_and_nonempty(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    manifest = load_manifest(root)
    with pytest.raises(ManifestValidationError, match="status 'frozen'"):
        require_frozen_coverage(manifest)

    path = root / "scope/coverage.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "frozen"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="non-empty"):
        load_manifest(root)


def test_empty_dimension_denominator_requires_explicit_na_rationale(
    tmp_path: Path,
) -> None:
    root = initialized_site(tmp_path)
    _write_dimensions(
        root,
        [
            _dimension(
                id="not-applicable",
                required_items=[],
                satisfied_items=[],
                rationale=None,
            )
        ],
    )
    with pytest.raises(ManifestValidationError, match="N/A rationale"):
        load_manifest(root)


def test_frozen_coverage_cannot_consist_only_of_na_dimensions(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    _write_dimensions(
        root,
        [
            _dimension(
                id="not-applicable",
                required_items=[],
                satisfied_items=[],
                rationale="N/A: this optional capability is outside the frozen purpose.",
            )
        ],
    )
    with pytest.raises(ManifestValidationError, match="non-empty denominator"):
        load_manifest(root)


@pytest.mark.parametrize(
    "dimensions, message",
    [
        (
            [_dimension(required_items=["item.alpha", "item.alpha"])],
            "duplicate item id: item.alpha",
        ),
        (
            [_dimension(), _dimension(label="Another label")],
            "duplicate dimension id: richly-rendered",
        ),
    ],
)
def test_coverage_rejects_duplicate_item_or_dimension_ids(
    tmp_path: Path,
    dimensions: list[dict[str, object]],
    message: str,
) -> None:
    root = initialized_site(tmp_path)
    _write_dimensions(root, dimensions)
    with pytest.raises(ManifestValidationError, match=message):
        load_manifest(root)


def test_report_computes_each_coverage_denominator_independently(tmp_path: Path) -> None:
    root = initialized_site(tmp_path)
    _write_dimensions(
        root,
        [
            _dimension(),
            _dimension(
                id="durably-verified",
                label="Durably verified mutations",
                unit="mutation",
                category="durability",
                rationale="A separate denominator for restart-safe writes.",
                required_items=["save.success", "save.retry"],
                satisfied_items=[],
            ),
            _dimension(
                id="not-applicable-yet",
                label="Unscoped optional capability",
                unit="capability",
                category="domain-specific",
                rationale="N/A: the frozen purpose has no optional capability.",
                required_items=[],
                satisfied_items=[],
            ),
        ],
    )

    coverage = full_report(load_manifest(root))["coverage"]
    assert "ratio" not in coverage
    assert "completeness" not in coverage
    assert coverage["dimensions"][0] == {
        "id": "richly-rendered",
        "label": "Richly rendered entities",
        "unit": "entity",
        "category": "rendering",
        "rationale": "Keep rich rendering independent from reachability.",
        "required_evidence_kinds": ["visual"],
        "required_items": ["item.alpha", "item.beta", "item.gamma"],
        "denominator": 3,
        "declared_satisfied_items": [],
        "declared_numerator": 0,
        "declared_remaining": 3,
        "declared_ratio": 0.0,
        "declared_remaining_items": ["item.alpha", "item.beta", "item.gamma"],
        "evidence_verified_items": [],
        "evidence_by_kind": {
            "visual": {
                "verified_items": [],
                "numerator": 0,
                "remaining": 3,
                "ratio": 0.0,
            }
        },
        "evidence_numerator": 0,
        "evidence_remaining": 3,
        "evidence_ratio": 0.0,
        "evidence_remaining_items": ["item.alpha", "item.beta", "item.gamma"],
    }
    assert coverage["dimensions"][1]["declared_numerator"] == 0
    assert coverage["dimensions"][1]["denominator"] == 2
    assert coverage["dimensions"][1]["declared_remaining"] == 2
    assert coverage["dimensions"][1]["declared_ratio"] == 0.0
    assert coverage["dimensions"][2]["declared_ratio"] is None
    assert coverage["dimensions"][2]["evidence_ratio"] is None
    assert coverage["status"] == "frozen"
    assert coverage["ledger"]["path"] == "scope/coverage.json"
    assert len(coverage["ledger"]["sha256"]) == 64
    assert require_frozen_coverage(load_manifest(root))["status"] == "frozen"
