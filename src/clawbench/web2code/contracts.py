"""Load and validate public Web2Code2Web site contracts.

The validator deliberately operates only on public artifacts. Private fixtures
and judge code are validated by the evaluator in later delivery gates.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ContractProblem:
    """One actionable contract validation problem."""

    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


class ContractValidationError(ValueError):
    """Raised when a public site contract is internally inconsistent."""

    def __init__(self, problems: list[ContractProblem]) -> None:
        self.problems = tuple(problems)
        details = "\n".join(f"- {problem}" for problem in problems)
        super().__init__(f"site contract validation failed:\n{details}")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_jsonschema() -> tuple[Any, Any]:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:  # pragma: no cover - dependency is present in dev/test
        raise RuntimeError(
            "contract validation requires the project development dependencies; "
            "run it with `uv run --group dev`"
        ) from exc
    return Draft202012Validator, FormatChecker


def _schema_problems(instance: Any, schema: dict[str, Any], location: str) -> list[ContractProblem]:
    validator_cls, format_checker_cls = _load_jsonschema()
    validator_cls.check_schema(schema)
    validator = validator_cls(schema, format_checker=format_checker_cls())
    problems: list[ContractProblem] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        suffix = ".".join(str(part) for part in error.absolute_path)
        error_location = f"{location}.{suffix}" if suffix else location
        problems.append(ContractProblem(error_location, error.message))
    return problems


def _resolve_site_path(site_root: Path, relative_path: str) -> Path:
    resolved = (site_root / relative_path).resolve()
    corpus_root = site_root.parent.resolve()
    if resolved != corpus_root and corpus_root not in resolved.parents:
        raise ValueError(f"path escapes the websitebench corpus: {relative_path}")
    return resolved


def _duplicate_values(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    duplicates: set[Any] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def validate_site_contract(
    manifest_path: Path | str,
    *,
    require_fixtures: bool = False,
) -> dict[str, Any]:
    """Validate schemas, public files, scoring invariants, and optional fixtures.

    Args:
        manifest_path: Path to the public site manifest.
        require_fixtures: Require and validate every declared fixture. W1 uses
            ``False`` because fixture generation is a W2 deliverable.

    Returns:
        The parsed manifest when the contract is valid.

    Raises:
        ContractValidationError: One or more actionable problems were found.
    """

    manifest_path = Path(manifest_path).resolve()
    site_root = manifest_path.parent.parent
    corpus_root = site_root.parent
    schemas_root = corpus_root / "schemas"
    problems: list[ContractProblem] = []

    try:
        with manifest_path.open(encoding="utf-8") as stream:
            manifest = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ContractValidationError([ContractProblem(str(manifest_path), str(exc))]) from exc

    if not isinstance(manifest, dict):
        raise ContractValidationError(
            [ContractProblem(str(manifest_path), "manifest must contain a mapping")]
        )

    try:
        manifest_schema = _read_json(schemas_root / "site-manifest.schema.json")
        problems.extend(_schema_problems(manifest, manifest_schema, "manifest"))
    except (OSError, ValueError) as exc:
        problems.append(ContractProblem("site-manifest.schema.json", str(exc)))

    public = manifest.get("public", {})
    loaded_public: dict[str, dict[str, Any]] = {}
    for key in (
        "prd",
        "candidate_contract",
        "task_schema",
        "fixture_schema",
        "admin_contract_schema",
        "visual_checkpoints",
        "smoke_cases",
        "scoring",
        "report_schema",
    ):
        relative = public.get(key) if isinstance(public, dict) else None
        if not isinstance(relative, str):
            continue
        try:
            path = _resolve_site_path(site_root, relative)
        except ValueError as exc:
            problems.append(ContractProblem(f"manifest.public.{key}", str(exc)))
            continue
        if not path.is_file():
            problems.append(ContractProblem(f"manifest.public.{key}", f"missing file: {path}"))
            continue
        if path.suffix == ".json":
            try:
                loaded_public[key] = _read_json(path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                problems.append(ContractProblem(relative, str(exc)))

    for schema_key in ("task_schema", "fixture_schema", "admin_contract_schema", "report_schema"):
        schema = loaded_public.get(schema_key)
        if schema is None:
            continue
        try:
            validator_cls, _ = _load_jsonschema()
            validator_cls.check_schema(schema)
        except Exception as exc:  # jsonschema raises several schema error types
            problems.append(ContractProblem(schema_key, f"invalid JSON Schema: {exc}"))

    site_version = manifest.get("site_version")
    for key in ("visual_checkpoints", "smoke_cases", "scoring"):
        document = loaded_public.get(key)
        if document is not None and document.get("site_version") != site_version:
            problems.append(
                ContractProblem(key, f"site_version must equal manifest version {site_version!r}")
            )

    visual = loaded_public.get("visual_checkpoints", {})
    comparison = visual.get("comparison", {})
    if isinstance(comparison, dict) and comparison:
        comparison_total = sum(value for value in comparison.values() if isinstance(value, (int, float)))
        if abs(comparison_total - 1.0) > 1e-9:
            problems.append(ContractProblem("visual_checkpoints.comparison", "weights must sum to 1"))
    checkpoints = visual.get("checkpoints", [])
    if isinstance(checkpoints, list):
        checkpoint_ids = [item.get("id") for item in checkpoints if isinstance(item, dict)]
        duplicates = _duplicate_values(checkpoint_ids)
        if duplicates:
            problems.append(ContractProblem("visual_checkpoints", f"duplicate IDs: {duplicates}"))
        viewports = visual.get("viewports", {})
        for index, checkpoint in enumerate(checkpoints):
            if not isinstance(checkpoint, dict):
                problems.append(ContractProblem(f"visual_checkpoints.{index}", "must be an object"))
                continue
            if checkpoint.get("viewport") not in viewports:
                problems.append(
                    ContractProblem(
                        f"visual_checkpoints.{index}.viewport",
                        "must name a declared viewport",
                    )
                )

    scoring = loaded_public.get("scoring", {})
    dimensions = scoring.get("dimensions", {})
    if isinstance(dimensions, dict) and dimensions:
        maximum = sum(
            dimension.get("max_score", 0)
            for dimension in dimensions.values()
            if isinstance(dimension, dict)
        )
        if maximum != 100:
            problems.append(ContractProblem("scoring.dimensions", "max_score values must sum to 100"))
        visual_metrics = dimensions.get("visual", {}).get("checkpoint_metrics")
        if visual_metrics != comparison:
            problems.append(
                ContractProblem(
                    "scoring.dimensions.visual.checkpoint_metrics",
                    "must equal visual-checkpoints comparison weights",
                )
            )
        journey = dimensions.get("journeys", {})
        if len(journey.get("journeys", [])) * journey.get("journey_max_score", 0) != journey.get(
            "max_score"
        ):
            problems.append(ContractProblem("scoring.dimensions.journeys", "journey points do not add up"))
        robustness = dimensions.get("robustness", {})
        if len(robustness.get("groups", [])) != robustness.get("max_score"):
            problems.append(
                ContractProblem("scoring.dimensions.robustness", "one-point groups do not add up")
            )

    seed_ids: list[int] = []
    fixture_schema = loaded_public.get("fixture_schema")
    seeds = manifest.get("seeds", {})
    if isinstance(seeds, dict):
        for group_name, group in seeds.items():
            if not isinstance(group, list):
                continue
            for index, seed_entry in enumerate(group):
                if not isinstance(seed_entry, dict):
                    continue
                seed = seed_entry.get("id")
                if isinstance(seed, int):
                    seed_ids.append(seed)
                relative = seed_entry.get("fixture")
                if not require_fixtures or not isinstance(relative, str):
                    continue
                try:
                    fixture_path = _resolve_site_path(site_root, relative)
                    fixture = _read_json(fixture_path)
                    if fixture_schema is not None:
                        problems.extend(
                            _schema_problems(
                                fixture,
                                fixture_schema,
                                f"seeds.{group_name}.{index}.fixture",
                            )
                        )
                    if fixture.get("seed") != seed:
                        problems.append(
                            ContractProblem(
                                f"seeds.{group_name}.{index}.fixture.seed",
                                f"must equal declared seed {seed}",
                            )
                        )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    problems.append(ContractProblem(f"seeds.{group_name}.{index}.fixture", str(exc)))

    duplicate_seeds = _duplicate_values(seed_ids)
    if duplicate_seeds:
        problems.append(ContractProblem("manifest.seeds", f"duplicate seed IDs: {duplicate_seeds}"))

    if problems:
        raise ContractValidationError(problems)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Web2Code2Web public site contract")
    parser.add_argument("manifest", type=Path, help="path to public/manifest.yaml")
    parser.add_argument(
        "--require-fixtures",
        action="store_true",
        help="require and validate every public and private fixture declared by the manifest",
    )
    args = parser.parse_args(argv)
    try:
        manifest = validate_site_contract(args.manifest, require_fixtures=args.require_fixtures)
    except ContractValidationError as exc:
        print(exc)
        return 1
    print(
        f"valid {manifest['schema_version']} contract: "
        f"{manifest['site_id']}@{manifest['site_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
