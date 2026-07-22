"""Discover canonical WebsiteBench items, candidate results, and legacy clones."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from .schema import validation_errors


READINESS_LABELS = {
    "manifest_schema": "Manifest schema",
    "required_artifacts": "Required public artifacts",
    "scoring_contract": "Scoring contract",
    "seed_reset": "Seed and reset",
    "controlled_time": "Controlled time",
    "journeys": "User journeys",
    "visual_checkpoints": "Visual checkpoints",
    "license": "License and assets",
    "candidate_report": "Official candidate report",
    "visual_evidence": "Visual evidence companion",
    "task_contract": "Task contract",
    "clone_artifact": "Clone artifact",
    "verification_report": "Legacy verifier report",
    "limitations": "Limitations record",
}
READINESS_STATES = {"present", "missing", "invalid", "not_applicable"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root(candidate: Path | None = None) -> Path:
    root = (candidate or Path.cwd()).resolve()
    if not (root / "pyproject.toml").is_file():
        raise ValueError(f"not a ClawBench repository root: {root}")
    return root


def _safe_resolve(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes repository root: {path}")
    return resolved


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"line {exc.lineno}, column {exc.colno}: {exc.msg}"


def _read_text(path: Path, limit: int = 120_000) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > limit:
        return text[:limit] + "\n\n[Viewer truncated this document.]"
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(paths: Iterable[Path], repo_root: Path) -> str:
    digest = hashlib.sha256()
    resolved = sorted({path.resolve() for path in paths if path.is_file()}, key=str)
    for path in resolved:
        path = _safe_resolve(repo_root, path)
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _status(identifier: str, state: str, detail: str = "") -> dict[str, str]:
    if state not in READINESS_STATES:
        raise ValueError(f"invalid readiness state: {state}")
    return {
        "id": identifier,
        "label": READINESS_LABELS[identifier],
        "status": state,
        "detail": detail,
    }


def _counts(readiness: list[dict[str, str]]) -> dict[str, int]:
    values = Counter(check["status"] for check in readiness)
    return {state: values.get(state, 0) for state in sorted(READINESS_STATES)}


def _recursive_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _recursive_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _recursive_strings(item)
    elif isinstance(value, str):
        yield value


def _result_summary(report: dict[str, Any]) -> dict[str, Any]:
    versions = report.get("versions", {})
    declared = report.get("candidate") or {}
    model_id = (
        declared.get("model_id")
        or versions.get("model")
        or versions.get("agent_model")
        or versions.get("model_id")
        or "unspecified"
    )
    candidate = {
        "model_id": model_id,
        "model_key": hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12],
        "display_name": declared.get("display_name") or model_id.replace("-", " ").title(),
        "provider": declared.get("provider"),
        "harness": declared.get("harness") or versions.get("harness"),
        "reasoning_effort": declared.get("reasoning_effort"),
    }
    return {
        "run_id": report["run_id"],
        "site_id": report["site_id"],
        "site_version": report.get("site_version"),
        "status": report["status"],
        "track": report["track"],
        "score": report["score"],
        "dimensions": report["dimensions"],
        "hard_failures": report["hard_failures"],
        "journeys": report["journeys"],
        "seeds": report["seeds"],
        "resources": report["resources"],
        "network": report["network"],
        "failures": report["failures"],
        "evidence": report["evidence"],
        "versions": versions,
        "candidate": candidate,
        "usage": report["usage"],
        "started_at": report["started_at"],
        "finished_at": report["finished_at"],
    }


def _discover_results(repo_root: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    by_site: dict[str, list[dict[str, Any]]] = {}
    invalid: list[dict[str, Any]] = []
    runs_root = repo_root / "artifacts" / "websitebench" / "runs"
    for path in sorted(runs_root.glob("*/report.json")) if runs_root.is_dir() else []:
        report, read_error = _read_json(path)
        if read_error or not isinstance(report, dict):
            invalid.append({"path": str(path.relative_to(repo_root)), "errors": [read_error or "not an object"]})
            continue
        errors = validation_errors(report, "result", repo_root)
        if errors:
            invalid.append({"path": str(path.relative_to(repo_root)), "errors": errors})
            continue
        summary = _result_summary(report)
        summary["report_path"] = str(path.relative_to(repo_root))
        by_site.setdefault(report["site_id"], []).append(summary)
    for runs in by_site.values():
        runs.sort(key=lambda run: (run["finished_at"], run["run_id"]), reverse=True)
    return by_site, invalid


def _load_visual_manifest(repo_root: Path, item_key: str) -> tuple[dict[str, Any] | None, list[str]]:
    path = repo_root / "artifacts" / "websitebench-viewer" / "visual" / item_key / "manifest.json"
    if not path.is_file():
        return None, []
    value, error = _read_json(path)
    if error or not isinstance(value, dict):
        return None, [error or "manifest is not an object"]
    errors = validation_errors(value, "visual_evidence", repo_root)
    if errors:
        return None, errors
    value["manifest_path"] = str(path.relative_to(repo_root))
    return value, []


def _canonical_item(
    repo_root: Path,
    manifest_path: Path,
    results: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    site_root = manifest_path.parent.parent
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        manifest = {}
        manifest_error = str(exc)
    else:
        manifest_error = ""
    site_id = manifest.get("site_id") or site_root.name
    key = f"websitebench--{site_id}"
    manifest_errors = ([manifest_error] if manifest_error else validation_errors(manifest, "site", repo_root))
    public = manifest.get("public", {}) if isinstance(manifest, dict) else {}
    referenced: dict[str, Path] = {}
    reference_errors: list[str] = []
    for name, relative in public.items():
        if not isinstance(relative, str):
            reference_errors.append(f"{name}: path is not a string")
            continue
        try:
            referenced[name] = _safe_resolve(repo_root, site_root / relative)
        except ValueError as exc:
            reference_errors.append(f"{name}: {exc}")
    missing = [name for name, path in referenced.items() if not path.is_file()]
    scoring, scoring_error = _read_json(referenced["scoring"]) if "scoring" in referenced else (None, "not declared")
    checkpoints, checkpoint_error = _read_json(referenced["visual_checkpoints"]) if "visual_checkpoints" in referenced else (None, "not declared")
    smoke, smoke_error = _read_json(referenced["smoke_cases"]) if "smoke_cases" in referenced else (None, "not declared")
    checkpoint_rows = checkpoints.get("checkpoints", []) if isinstance(checkpoints, dict) else []
    journey_rows = smoke.get("cases", []) if isinstance(smoke, dict) else []
    dimensions = scoring.get("dimensions", {}) if isinstance(scoring, dict) else {}
    scoring_valid = (
        not scoring_error
        and set(dimensions) == {"visual", "interactions", "journeys", "robustness", "efficiency"}
        and sum(value.get("max_score", 0) for value in dimensions.values()) == 100
    )
    item_runs = results.get(site_id, [])
    visual, visual_errors = _load_visual_manifest(repo_root, key)
    all_seed_rows = [seed for group in manifest.get("seeds", {}).values() for seed in group]
    scripts = site_root / "reference" / "scripts"
    license_data = manifest.get("license")
    readiness = [
        _status("manifest_schema", "invalid" if manifest_errors else "present", "; ".join(manifest_errors[:3])),
        _status(
            "required_artifacts",
            "invalid" if reference_errors else ("missing" if missing else "present"),
            "; ".join(reference_errors + [f"missing {name}" for name in missing]),
        ),
        _status("scoring_contract", "present" if scoring_valid else "invalid", scoring_error or "five dimensions total 100 points"),
        _status(
            "seed_reset",
            "present" if all_seed_rows and (scripts / "seed").is_file() and (scripts / "reset").is_file() else "missing",
            f"{len(all_seed_rows)} declared seeds; seed/reset scripts {'found' if scripts.is_dir() else 'missing'}",
        ),
        _status("controlled_time", "present" if isinstance(checkpoints, dict) and checkpoints.get("clock") else "missing"),
        _status("journeys", "present" if journey_rows and not smoke_error else ("invalid" if smoke_error and referenced.get("smoke_cases", Path()).is_file() else "missing"), f"{len(journey_rows)} public smoke journeys"),
        _status("visual_checkpoints", "present" if checkpoint_rows and not checkpoint_error else ("invalid" if checkpoint_error and referenced.get("visual_checkpoints", Path()).is_file() else "missing"), f"{len(checkpoint_rows)} checkpoints"),
        _status("license", "present" if isinstance(license_data, dict) and all(license_data.values()) else "missing"),
        _status("candidate_report", "present" if item_runs else "not_applicable", f"{len(item_runs)} valid official runs"),
        _status("visual_evidence", "invalid" if visual_errors else ("present" if visual else ("missing" if item_runs else "not_applicable")), "; ".join(visual_errors[:3])),
    ]
    readiness_counts = _counts(readiness)
    taxonomy = manifest.get("taxonomy") or {}
    documents = {
        "prd": _read_text(referenced["prd"]) if "prd" in referenced else None,
        "candidate_contract": _read_text(referenced["candidate_contract"]) if "candidate_contract" in referenced else None,
    }
    fingerprint_paths = [manifest_path, *referenced.values()]
    if visual:
        fingerprint_paths.append(repo_root / visual["manifest_path"])
    return {
        "key": key,
        "source_type": "websitebench",
        "site_id": site_id,
        "display_name": manifest.get("display_name", site_id),
        "description": manifest.get("description", ""),
        "family": manifest.get("family_id"),
        "product_type": taxonomy.get("product_type"),
        "difficulty": manifest.get("difficulty"),
        "split": manifest.get("split"),
        "site_version": manifest.get("site_version"),
        "capability_tags": taxonomy.get("capability_tags", []),
        "interaction_tags": taxonomy.get("interaction_tags", []),
        "roles": taxonomy.get("roles", []),
        "stateful_entities": taxonomy.get("stateful_entities", []),
        "counts": {
            "routes": len(manifest.get("routes", [])),
            "journeys": len(journey_rows),
            "checkpoints": len(checkpoint_rows),
            "seeds": len(all_seed_rows),
            "public_seeds": len(manifest.get("seeds", {}).get("public", [])),
            "hidden_test_families": sum(bool(manifest.get("seeds", {}).get(name)) for name in ("hidden", "concurrency")),
        },
        "protocol": {
            "public_artifacts": sorted(public),
            "browser_policy": manifest.get("browser_policy"),
            "tracks": manifest.get("tracks"),
            "services": manifest.get("services"),
            "license": license_data,
            "visual_viewports": sorted(
                (checkpoints.get("viewports") or {}).keys()
                if isinstance(checkpoints, dict)
                and isinstance(checkpoints.get("viewports"), dict)
                else set()
            ),
            "hard_failures": scoring.get("hard_failures", []) if isinstance(scoring, dict) else [],
            "scoring_dimensions": dimensions,
            "seeds": manifest.get("seeds", {}),
        },
        "readiness": readiness,
        "readiness_counts": readiness_counts,
        "lifecycle_stage": (
            "evaluated"
            if item_runs
            else "ready"
            if not readiness_counts["missing"] and not readiness_counts["invalid"]
            else "building"
        ),
        "official_runs": item_runs,
        "latest_official_result": item_runs[0] if item_runs else None,
        "legacy_verification": None,
        "visual_evidence": visual,
        "visual_evidence_errors": visual_errors,
        "documents": documents,
        "artifact_fingerprint": fingerprint(fingerprint_paths, repo_root),
        "internal": {
            "manifest_path": str(manifest_path.relative_to(repo_root)),
            "site_root": str(site_root.relative_to(repo_root)),
            "manifest_errors": manifest_errors,
            "reference_errors": reference_errors,
        },
    }


def _legacy_visual_paths(report: Any, clone_root: Path, repo_root: Path) -> list[Path]:
    output: list[Path] = []
    for value in _recursive_strings(report):
        if not value.lower().endswith((".png", ".webp", ".jpg", ".jpeg")):
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        try:
            candidate = _safe_resolve(clone_root, candidate)
        except ValueError:
            continue
        if candidate.is_file() and candidate not in output:
            output.append(candidate)
    artifact_root = clone_root / ".verification-artifacts"
    if artifact_root.is_dir():
        for candidate in sorted(artifact_root.iterdir()):
            if candidate.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"} and candidate not in output:
                output.append(candidate)
    return output


def _legacy_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks_value = report.get("checks")
    checks: list[Any] = checks_value if isinstance(checks_value, list) else []
    summary_value = report.get("summary")
    summary: dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
    passed = summary.get("passed", report.get("passed"))
    total = summary.get("total", report.get("total"))
    failed = summary.get("failed")
    if not isinstance(passed, int):
        passed = sum(check.get("passed") is True for check in checks if isinstance(check, dict))
    if not isinstance(total, int):
        total = len(checks)
    if not isinstance(failed, int):
        failed = max(total - passed, 0)
    return {"passed": passed, "failed": failed, "total": total}


def _legacy_item(repo_root: Path, task_path: Path) -> dict[str, Any] | None:
    task, task_error = _read_json(task_path)
    if not isinstance(task, dict):
        return None
    metadata_value = task.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    clone_relative = metadata.get("clone_path")
    if not isinstance(clone_relative, str):
        return None
    clone_root = _safe_resolve(repo_root, repo_root / clone_relative)
    key = f"legacy--{task_path.parent.name}"
    report_path = clone_root / "verification-report.json"
    report, report_error = _read_json(report_path)
    report_dict = report if isinstance(report, dict) else {}
    visual_paths = _legacy_visual_paths(report_dict, clone_root, repo_root)
    visual, visual_errors = _load_visual_manifest(repo_root, key)
    summary = _legacy_summary(report_dict) if report_dict else None
    attribution = clone_root / "ASSET_ATTRIBUTION.md"
    limitations = clone_root / "LIMITATIONS.md"
    readme = clone_root / "README.md"
    readiness = [
        _status("task_contract", "invalid" if task_error else "present", task_error or "legacy task.json adapter"),
        _status("clone_artifact", "present" if clone_root.is_dir() else "missing"),
        _status("verification_report", "invalid" if report_error and report_path.is_file() else ("present" if report_dict else "missing"), report_error or (f"{summary['passed']}/{summary['total']} checks passed" if summary else "")),
        _status("visual_checkpoints", "present" if visual_paths else "missing", f"{len(visual_paths)} explicit verifier screenshots"),
        _status("license", "present" if attribution.is_file() else "missing", "legacy asset attribution"),
        _status("limitations", "present" if limitations.is_file() else "missing"),
        _status("seed_reset", "not_applicable", "legacy compatibility adapter does not infer WebsiteBench controls"),
        _status("controlled_time", "not_applicable"),
        _status("journeys", "not_applicable", "not declared in legacy task metadata"),
        _status("candidate_report", "not_applicable", "legacy verifier evidence is not websitebench.result.v1"),
        _status("visual_evidence", "invalid" if visual_errors else ("present" if visual else "not_applicable"), "; ".join(visual_errors[:3])),
    ]
    metaclass = metadata.get("metaclass")
    class_name = metadata.get("class")
    documents = {
        "readme": _read_text(readme),
        "limitations": _read_text(limitations),
        "asset_attribution": _read_text(attribution),
        "verification_report": (
            json.dumps(report_dict, indent=2, ensure_ascii=False)
            if report_dict
            else None
        ),
        "instruction": task.get("instruction"),
    }
    paths = [task_path, report_path, readme, limitations, attribution, *visual_paths]
    if visual:
        paths.append(repo_root / visual["manifest_path"])
    local_port = None
    sites = metadata.get("sites_involved")
    if isinstance(sites, list) and sites and isinstance(sites[0], str):
        local_port = sites[0]
    return {
        "key": key,
        "source_type": "legacy",
        "site_id": str(metadata.get("task_id") or task_path.parent.name),
        "display_name": metadata.get("platform") or task_path.parent.name,
        "description": metadata.get("description") or "",
        "family": metaclass,
        "product_type": None,
        "difficulty": None,
        "split": "development" if metadata.get("dev_only") is True else None,
        "site_version": None,
        "capability_tags": [value for value in (metaclass, class_name) if isinstance(value, str)],
        "interaction_tags": [],
        "roles": [],
        "stateful_entities": [],
        "counts": {
            "routes": None,
            "journeys": None,
            "checkpoints": len(visual_paths),
            "seeds": None,
            "public_seeds": None,
            "hidden_test_families": None,
        },
        "protocol": {
            "metaclass": metaclass,
            "class": class_name,
            "eval_schema": task.get("eval_schema"),
            "time_limit": task.get("time_limit"),
            "license": _read_text(attribution),
        },
        "readiness": readiness,
        "readiness_counts": _counts(readiness),
        "lifecycle_stage": "legacy",
        "official_runs": [],
        "latest_official_result": None,
        "legacy_verification": summary,
        "visual_evidence": visual,
        "visual_evidence_errors": visual_errors,
        "legacy_screenshots": [str(path.relative_to(repo_root)) for path in visual_paths],
        "documents": documents,
        "artifact_fingerprint": fingerprint(paths, repo_root),
        "internal": {
            "task_path": str(task_path.relative_to(repo_root)),
            "clone_root": str(clone_root.relative_to(repo_root)),
            "report_path": str(report_path.relative_to(repo_root)),
            "server_command": metadata.get("server_command"),
            "local_host": local_port,
        },
    }


def _load_allowlist(repo_root: Path, path: Path | None) -> set[str]:
    allowlist_path = path or (repo_root / "websitebench" / "viewer-public-allowlist.json")
    value, error = _read_json(allowlist_path)
    if error:
        raise ValueError(f"public profile requires a valid allowlist: {error}")
    values = value.get("items") if isinstance(value, dict) else value
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError("public allowlist must be a JSON array or an object with an items array")
    return set(values)


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    """Publish aggregates without hidden-test or artifact-level details."""

    return {
        key: copy.deepcopy(run[key])
        for key in (
            "run_id",
            "site_id",
            "site_version",
            "status",
            "track",
            "score",
            "dimensions",
            "resources",
            "network",
            "usage",
            "candidate",
            "started_at",
            "finished_at",
        )
    } | {
        # The shared run template expects these keys. Detail stays internal
        # because it can contain hidden fixtures, reproduction steps, or paths.
        "hard_failures": [],
        "journeys": [],
        "seeds": [],
        "failures": [],
        "evidence": [],
        "versions": {},
        "details_withheld": True,
    }


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return only explicitly public, path-free fields for a corpus item."""

    result = {
        key: copy.deepcopy(item[key])
        for key in (
            "key", "source_type", "site_id", "display_name", "description", "family",
            "product_type", "difficulty", "split", "site_version", "capability_tags",
            "interaction_tags", "roles", "stateful_entities", "counts", "readiness",
            "readiness_counts", "official_runs", "latest_official_result",
            "legacy_verification", "artifact_fingerprint", "lifecycle_stage"
        )
    }
    result["counts"]["hidden_test_families"] = None
    result["official_runs"] = [_public_run(run) for run in item["official_runs"]]
    result["latest_official_result"] = (
        _public_run(item["latest_official_result"])
        if item["latest_official_result"]
        else None
    )
    protocol = item.get("protocol", {})
    result["protocol"] = {
        key: copy.deepcopy(protocol.get(key))
        for key in (
            "public_artifacts", "browser_policy", "tracks", "services", "license",
            "scoring_dimensions", "visual_viewports", "metaclass", "class", "time_limit"
        )
        if protocol.get(key) is not None
    }
    result["documents"] = {
        key: value
        for key, value in item.get("documents", {}).items()
        if key in {"prd", "candidate_contract", "readme", "limitations", "asset_attribution"}
    }
    result["visual_evidence"] = None
    result["visual_evidence_errors"] = []
    return result


def public_leak_findings(value: Any) -> list[str]:
    """Recursively find path/command/private-fixture markers in a public index."""

    findings: list[str] = []
    blocked_keys = {"server_command", "verify_command", "internal", "report_path", "manifest_path", "task_path", "clone_root"}

    def visit(item: Any, location: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                here = f"{location}.{key}"
                if key in blocked_keys:
                    findings.append(f"{here}: blocked internal key")
                visit(child, here)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{location}[{index}]")
        elif isinstance(item, str):
            lowered = item.lower().replace("\\", "/")
            if "judge/" in lowered:
                findings.append(f"{location}: private fixture marker")
            if lowered.startswith(("/mnt/", "/home/", "/root/", "c:/")):
                findings.append(f"{location}: absolute filesystem path")

    visit(value, "$")
    return findings


@dataclass
class CorpusIndex:
    repo_root: Path
    profile: str
    items: list[dict[str, Any]]
    invalid_runs: list[dict[str, Any]]

    def by_key(self, key: str) -> dict[str, Any] | None:
        return next((item for item in self.items if item["key"] == key), None)

    @property
    def runs(self) -> list[dict[str, Any]]:
        return [run for item in self.items for run in item["official_runs"]]

    def run_by_id(self, run_id: str) -> dict[str, Any] | None:
        return next((run for run in self.runs if run["run_id"] == run_id), None)

    @property
    def models(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for run in self.runs:
            grouped.setdefault(run["candidate"]["model_key"], []).append(run)
        output = []
        for key, runs in grouped.items():
            candidate = runs[0]["candidate"]
            dimensions = {
                name: round(sum(run["dimensions"][name]["score"] for run in runs) / len(runs), 2)
                for name in ("visual", "interactions", "journeys", "robustness", "efficiency")
            }
            output.append({
                **copy.deepcopy(candidate),
                "model_key": key,
                "run_count": len(runs),
                "site_count": len({run["site_id"] for run in runs}),
                "average_score": round(sum(run["score"] for run in runs) / len(runs), 2),
                "passed_count": sum(run["status"] == "passed" for run in runs),
                "latest_finished_at": max(run["finished_at"] for run in runs),
                "dimensions": dimensions,
                "runs": sorted(runs, key=lambda run: (run["site_id"], run["finished_at"])),
            })
        return sorted(output, key=lambda model: (-model["average_score"], model["display_name"].lower()))

    def model_by_key(self, model_key: str) -> dict[str, Any] | None:
        return next((model for model in self.models if model["model_key"] == model_key), None)

    @property
    def categories(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in self.items:
            if item["source_type"] != "websitebench":
                continue
            grouped.setdefault(item.get("product_type") or "uncategorized", []).append(item)
        output = []
        for category, items in grouped.items():
            output.append({
                "id": category,
                "label": category.replace("-", " ").title(),
                "site_count": len(items),
                "ready_count": sum(item["lifecycle_stage"] in {"ready", "evaluated"} for item in items),
                "evaluated_count": sum(item["lifecycle_stage"] == "evaluated" for item in items),
                "run_count": sum(len(item["official_runs"]) for item in items),
                "model_count": len({
                    run["candidate"]["model_key"]
                    for item in items
                    for run in item["official_runs"]
                }),
                "sites": items,
            })
        return sorted(output, key=lambda category: category["label"].lower())

    @property
    def evaluation_matrix(self) -> list[dict[str, Any]]:
        models = self.models
        rows = []
        for item in (item for item in self.items if item["source_type"] == "websitebench"):
            cells = []
            for model in models:
                runs = [
                    run
                    for run in item["official_runs"]
                    if run["candidate"]["model_key"] == model["model_key"]
                ]
                latest = max(runs, key=lambda run: run["finished_at"]) if runs else None
                cells.append({"model_key": model["model_key"], "run": latest})
            rows.append({"item": item, "cells": cells})
        return rows

    def as_dict(self) -> dict[str, Any]:
        distributions = {}
        for field in ("source_type", "family", "product_type", "difficulty", "split"):
            counter = Counter(item.get(field) or "pending" for item in self.items)
            distributions[field] = dict(sorted(counter.items()))
        readiness = Counter(
            check["status"] for item in self.items for check in item["readiness"]
        )
        models = self.models
        categories = self.categories
        benchmark_sites = [item for item in self.items if item["source_type"] == "websitebench"]
        evaluated_pairs = {
            (run["site_id"], run["candidate"]["model_key"])
            for run in self.runs
        }
        return {
            "schema_version": "websitebench.viewer-index.v1",
            "generated_at": utc_now(),
            "profile": self.profile,
            "summary": {
                "item_count": len(self.items),
                "websitebench_count": sum(item["source_type"] == "websitebench" for item in self.items),
                "legacy_count": sum(item["source_type"] == "legacy" for item in self.items),
                "official_run_count": len(self.runs),
                "invalid_run_count": len(self.invalid_runs),
                "category_count": len(categories),
                "model_count": len(models),
                "evaluated_pair_count": len(evaluated_pairs),
                "possible_pair_count": len(benchmark_sites) * len(models),
                "readiness": {state: readiness.get(state, 0) for state in sorted(READINESS_STATES)},
                "distributions": distributions,
            },
            "items": self.items,
            "models": models,
            "categories": categories,
            "evaluation_matrix": self.evaluation_matrix,
            "invalid_runs": self.invalid_runs if self.profile == "internal" else [],
        }


def discover_corpus(
    repo_root: Path | None = None,
    *,
    profile: str = "internal",
    public_allowlist: Path | None = None,
) -> CorpusIndex:
    root = _repo_root(repo_root)
    if profile not in {"internal", "public"}:
        raise ValueError("profile must be internal or public")
    results, invalid_runs = _discover_results(root)
    items = [
        _canonical_item(root, path, results)
        for path in sorted((root / "websitebench").glob("*/public/manifest.yaml"))
    ]
    for path in sorted((root / "tasks" / "dev").glob("*/task.json")):
        item = _legacy_item(root, path)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: (item["source_type"] != "websitebench", item["display_name"].lower()))
    if profile == "public":
        allowlist = _load_allowlist(root, public_allowlist)
        items = [public_item(item) for item in items if item["key"] in allowlist]
        invalid_runs = []
    index = CorpusIndex(root, profile, items, invalid_runs)
    if profile == "public":
        findings = public_leak_findings(index.as_dict())
        if findings:
            raise ValueError("public index leak check failed: " + "; ".join(findings[:10]))
    return index
