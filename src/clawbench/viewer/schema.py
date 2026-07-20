"""Schema loading and validation helpers for viewer contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMA_FILES = {
    "site": "site-manifest.schema.json",
    "result": "report.schema.json",
    "review": "viewer-review.schema.json",
    "review_export": "viewer-review-export.schema.json",
    "visual_evidence": "visual-evidence.schema.json",
    "calibration": "calibration-result.schema.json",
}


def schema_directory(repo_root: Path | None = None) -> Path:
    if repo_root is not None:
        canonical = repo_root / "websitebench" / "schemas"
        if canonical.is_dir():
            return canonical
    bundled = Path(__file__).resolve().parent / "_schemas"
    if bundled.is_dir():
        return bundled
    raise FileNotFoundError("WebsiteBench schemas are not available")


def load_schema(name: str, repo_root: Path | None = None) -> dict[str, Any]:
    try:
        filename = SCHEMA_FILES[name]
    except KeyError as exc:
        raise KeyError(f"unknown WebsiteBench schema: {name}") from exc
    return json.loads((schema_directory(repo_root) / filename).read_text(encoding="utf-8"))


def _registry(repo_root: Path | None = None) -> Registry:
    registry = Registry()
    directory = schema_directory(repo_root)
    for filename in SCHEMA_FILES.values():
        path = directory / filename
        if not path.is_file():
            continue
        contents = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(contents)
        registry = registry.with_resource(contents["$id"], resource)
        registry = registry.with_resource(filename, resource)
    return registry


def validation_errors(
    instance: Any,
    schema_name: str,
    repo_root: Path | None = None,
) -> list[str]:
    validator = Draft202012Validator(
        load_schema(schema_name, repo_root),
        registry=_registry(repo_root),
        format_checker=FormatChecker(),
    )
    errors = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(f"{path}: {error.message}")
    return errors


def validate(instance: Any, schema_name: str, repo_root: Path | None = None) -> None:
    errors = validation_errors(instance, schema_name, repo_root)
    if errors:
        raise ValueError("; ".join(errors))
