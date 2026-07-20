"""Amazon-only discovery for the WebsiteBench Viewer.

The benchmark repository still contains the synthetic WebsiteBench corpus and
legacy compatibility clones, but the Viewer deliberately does not scan them.
Its public and internal profiles are two views over one fixed Amazon adapter.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..amazon_contract import (
    AMAZON_ITEM_KEY,
    AMAZON_RUNTIME_MANIFEST,
    AMAZON_SITE_ID,
    AmazonContractError,
    amazon_runtime_fingerprint,
    amazon_runtime_paths,
    load_amazon_runtime_contract,
    reported_runtime_fingerprints,
)
from .evidence import AmazonEvidenceRegistry
from .schema import validation_errors


READINESS_STATES = {"present", "missing", "invalid", "not_applicable"}
REPORT_FILES = {
    "clone-verification": Path("materials/amazon/clone/verification-report.json"),
    "gate2-report": Path("materials/amazon/verification/gate2/report.json"),
    "gate2-review": Path("materials/amazon/verification/gate2/GATE2_REVIEW.md"),
    "gate3-report": Path("materials/amazon/verification/gate3/report.json"),
    "gate3-review": Path("materials/amazon/verification/gate3/GATE3_REVIEW.md"),
    "gate4-report": Path("materials/amazon/verification/gate4/report.json"),
    "gate4-review": Path("materials/amazon/verification/gate4/GATE4_REVIEW.md"),
    "gate4-approval": Path("materials/amazon/verification/gate4/GATE4_APPROVAL.md"),
}


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


def _read_text(path: Path, limit: int = 160_000) -> str | None:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(value) > limit:
        return value[:limit] + "\n\n[Viewer truncated this document.]"
    return value


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


def _status(identifier: str, label: str, state: str, detail: str = "") -> dict[str, str]:
    if state not in READINESS_STATES:
        raise ValueError(f"invalid readiness state: {state}")
    return {"id": identifier, "label": label, "status": state, "detail": detail}


def _counts(readiness: list[dict[str, str]]) -> dict[str, int]:
    values = Counter(check["status"] for check in readiness)
    return {state: values.get(state, 0) for state in sorted(READINESS_STATES)}


def _load_object(path: Path) -> tuple[dict[str, Any], str | None]:
    value, error = _read_json(path)
    if error:
        return {}, error
    if not isinstance(value, dict):
        return {}, "JSON root is not an object"
    return value, None


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result_summary(
    report: dict[str, Any],
    *,
    report_path: Path,
    repo_root: Path,
    metadata: dict[str, Any],
    metadata_errors: list[str],
) -> dict[str, Any]:
    model = metadata.get("model")
    thinking_level = metadata.get("thinking_level")
    if not isinstance(model, str) or not model.strip():
        model = None
        metadata_errors.append("run-meta.json requires a non-empty model")
    else:
        model = model.strip()
    if not isinstance(thinking_level, str) or not thinking_level.strip():
        thinking_level = None
        metadata_errors.append("run-meta.json requires a non-empty thinking_level")
    else:
        thinking_level = thinking_level.strip()
    if metadata.get("run_id") not in {None, report["run_id"]}:
        metadata_errors.append("run-meta.json run_id does not match the report")
    viewer_public = metadata.get("viewer_public") is True
    publication_errors = list(metadata_errors)
    if not viewer_public:
        publication_errors.append("viewer_public is not true")
    summary = copy.deepcopy(report)
    summary.update(
        {
            "model": model,
            "thinking_level": thinking_level,
            "viewer_public": viewer_public,
            "publishable": bool(viewer_public and model and thinking_level and not metadata_errors),
            "publication_errors": publication_errors,
            "report_path": report_path.relative_to(repo_root).as_posix(),
            "run_directory": report_path.parent.parent.relative_to(repo_root).as_posix()
            if report_path.parent.name == "eval"
            else report_path.parent.relative_to(repo_root).as_posix(),
        }
    )
    return summary


def _discover_results(
    repo_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load Amazon result reports from the current and compatibility paths."""

    runs: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    runs_root = repo_root / "artifacts" / "websitebench" / "runs"
    if not runs_root.is_dir():
        return runs, invalid
    for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        current = run_dir / "eval" / "evaluation-result.json"
        compatibility = run_dir / "report.json"
        report_path = current if current.is_file() else compatibility
        if not report_path.is_file():
            continue
        report, read_error = _read_json(report_path)
        relative = report_path.relative_to(repo_root).as_posix()
        if read_error or not isinstance(report, dict):
            invalid.append(
                {
                    "run_directory": run_dir.relative_to(repo_root).as_posix(),
                    "path": relative,
                    "errors": [read_error or "report is not an object"],
                }
            )
            continue
        errors = validation_errors(report, "result", repo_root)
        if report.get("site_id") != AMAZON_SITE_ID:
            errors.append("site_id: only amazon results are accepted by this Viewer")
        if errors:
            invalid.append(
                {
                    "run_id": report.get("run_id", run_dir.name),
                    "run_directory": run_dir.relative_to(repo_root).as_posix(),
                    "path": relative,
                    "errors": errors,
                }
            )
            continue
        metadata_path = run_dir / "run-meta.json"
        metadata_errors: list[str] = []
        metadata, metadata_error = _read_json(metadata_path)
        if metadata_error:
            metadata = {}
            metadata_errors.append(f"run-meta.json: {metadata_error}")
        elif not isinstance(metadata, dict):
            metadata = {}
            metadata_errors.append("run-meta.json is not an object")
        runs.append(
            _result_summary(
                report,
                report_path=report_path,
                repo_root=repo_root,
                metadata=metadata,
                metadata_errors=metadata_errors,
            )
        )
    runs.sort(
        key=lambda run: (_parse_datetime(run["finished_at"]), run["run_id"]),
        reverse=True,
    )
    return runs, invalid


def _discover_calibrations(
    repo_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    """Load unranked Amazon-136 harness calibrations separately from results."""

    root = repo_root / "artifacts" / "websitebench" / "calibrations"
    if not root.is_dir():
        return [], [], []
    records: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    paths: list[Path] = []
    for path in sorted(root.rglob("calibration-result.json")):
        value, error = _read_json(path)
        relative = path.relative_to(repo_root).as_posix()
        errors = [error] if error else []
        if not errors and isinstance(value, dict):
            errors.extend(validation_errors(value, "calibration", repo_root))
            steps = value.get("steps", {})
            if isinstance(steps, dict) and steps.get("total"):
                expected = steps.get("passed", 0) / steps["total"]
                if abs(float(steps.get("pass_rate", -1)) - expected) > 1e-9:
                    errors.append("steps.pass_rate: must equal passed / total")
        elif not errors:
            errors.append("calibration result is not an object")
        if errors:
            invalid.append({"kind": "calibration", "path": relative, "errors": errors})
            continue
        records.append(copy.deepcopy(value))
        paths.append(path)
    order = {"xhigh": 0, "high": 1, "medium": 2, "low": 3}
    records.sort(key=lambda item: (order.get(item["reasoning_effort"], 99), item["calibration_id"]))
    return records, invalid, paths


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    """Expose aggregate run data without hidden journeys or artifact paths."""

    return {
        key: copy.deepcopy(run[key])
        for key in (
            "run_id",
            "site_id",
            "site_version",
            "track",
            "status",
            "score",
            "dimensions",
            "resources",
            "network",
            "usage",
            "started_at",
            "finished_at",
            "model",
            "thinking_level",
        )
    } | {"details_withheld": True}


def aggregate_leaderboard(
    runs: Iterable[dict[str, Any]], *, public_only: bool = True
) -> list[dict[str, Any]]:
    """Select the best run for each model/configuration/track tuple."""

    candidates = [
        run
        for run in runs
        if (run.get("publishable") if public_only else run.get("model") and run.get("thinking_level"))
    ]
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for run in candidates:
        key = (str(run["model"]), str(run["thinking_level"]), str(run["track"]))
        current = best.get(key)
        challenger = (float(run["score"]), _parse_datetime(run["finished_at"]), run["run_id"])
        incumbent = (
            float(current["score"]),
            _parse_datetime(current["finished_at"]),
            current["run_id"],
        ) if current else None
        if incumbent is None or challenger > incumbent:
            best[key] = run
    rows = []
    for run in best.values():
        public = _public_run(run)
        public.update(
            {
                "visual": run["dimensions"]["visual"]["score"],
                "interactions": run["dimensions"]["interactions"]["score"],
                "journeys_score": run["dimensions"]["journeys"]["score"],
            }
        )
        rows.append(public)
    rows.sort(
        key=lambda row: (
            -float(row["score"]),
            -float(row["visual"]),
            row["model"].casefold(),
            row["thinking_level"].casefold(),
            row["track"],
        )
    )
    return rows


def _approval_status(text: str | None, report_path: Path, report: dict[str, Any]) -> tuple[str, str]:
    if not text:
        return "pending", "Gate 4 approval record is missing"
    status_match = re.search(r"Effective status:\s*`([^`]+)`", text)
    sha_match = re.search(r"Approved report SHA-256:\s*`([0-9a-f]{64})`", text)
    contract_match = re.search(r"Approved contract SHA-256:\s*`([0-9a-f]{64})`", text)
    if not status_match or status_match.group(1) != "approved":
        return "pending", "Gate 4 has no effective approved status"
    if not sha_match or not report_path.is_file() or sha_match.group(1) != _sha256(report_path):
        return "invalid", "Gate 4 approval report hash does not match"
    if not contract_match:
        return "invalid", "Gate 4 approval contract hash is missing"
    if report.get("contractSha256") != contract_match.group(1):
        return "invalid", "Gate 4 approval contract hash does not match"
    return "approved", "Explicit human approval recorded on 2026-07-18"


def _amazon_item(
    repo_root: Path,
    runs: list[dict[str, Any]],
    calibrations: list[dict[str, Any]],
    calibration_paths: list[Path],
    evidence: AmazonEvidenceRegistry,
) -> dict[str, Any]:
    runtime_manifest: dict[str, Any] = {}
    runtime: dict[str, Any] = {}
    runtime_error: str | None = None
    runtime_attested_paths: list[Path] = []
    current_runtime_fingerprint: str | None = None
    try:
        runtime_manifest = load_amazon_runtime_contract(repo_root)
        runtime = runtime_manifest["runtime"]
        runtime_attested_paths = amazon_runtime_paths(repo_root, runtime_manifest)
        current_runtime_fingerprint = amazon_runtime_fingerprint(
            repo_root, runtime_manifest
        )
    except AmazonContractError as exc:
        runtime_error = str(exc)

    task_relative = runtime.get("task_path")
    task_path = repo_root / task_relative if task_relative else repo_root / "__missing_amazon_task__"
    task, task_error = _load_object(task_path)
    metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
    metadata_errors: list[str] = []
    for key, expected in (
        ("benchmark_key", AMAZON_ITEM_KEY),
        ("site_id", AMAZON_SITE_ID),
        ("runtime_manifest", AMAZON_RUNTIME_MANIFEST.as_posix()),
    ):
        if metadata.get(key) != expected:
            metadata_errors.append(f"metadata.{key} must equal {expected!r}")
    task_contract_error = "; ".join(
        error for error in (runtime_error, task_error, *metadata_errors) if error
    ) or None
    loaded_reports: dict[str, dict[str, Any]] = {}
    report_errors: dict[str, str | None] = {}
    for identifier, relative in REPORT_FILES.items():
        if relative.suffix != ".json":
            continue
        loaded_reports[identifier], report_errors[identifier] = _load_object(repo_root / relative)

    clone = loaded_reports.get("clone-verification", {})
    gate2 = loaded_reports.get("gate2-report", {})
    gate3 = loaded_reports.get("gate3-report", {})
    gate4 = loaded_reports.get("gate4-report", {})
    approval_path = repo_root / REPORT_FILES["gate4-approval"]
    approval_text = _read_text(approval_path)
    effective_gate4, approval_detail = _approval_status(
        approval_text, repo_root / REPORT_FILES["gate4-report"], gate4
    )
    reported_fingerprints = reported_runtime_fingerprints(
        (clone, gate2, gate3, gate4)
    )
    runtime_fresh = bool(
        current_runtime_fingerprint
        and all(
            value == current_runtime_fingerprint
            for value in reported_fingerprints
        )
    )
    if runtime_error:
        attestation_detail = runtime_error
    elif runtime_fresh:
        attestation_detail = "All clone and Gate reports match the current runtime"
    else:
        matching = sum(
            value == current_runtime_fingerprint for value in reported_fingerprints
        )
        attestation_detail = (
            f"{matching}/4 reports match the current runtime; rerun clone verification "
            "and Gates 2–4 after the commerce fusion"
        )

    clone_overall = clone.get("verification", {}).get("overall_assertions", {})
    gate2_journeys = gate2.get("journeys", []) if isinstance(gate2.get("journeys"), list) else []
    gate2_total = int(gate2.get("journeyCount", len(gate2_journeys)) or 0)
    gate2_passed = sum(
        row.get("passed") is True for row in gate2_journeys if isinstance(row, dict)
    )
    gate3_summary = gate3.get("summary", {}) if isinstance(gate3.get("summary"), dict) else {}
    gate4_summary = gate4.get("summary", {}) if isinstance(gate4.get("summary"), dict) else {}
    metrics = {
        "regression_assertions": {
            "passed": int(clone_overall.get("passed", 0) or 0),
            "total": int(clone_overall.get("total", 0) or 0),
        },
        "browser_journeys": {"passed": gate2_passed, "total": gate2_total},
        "semantic_checks": {
            "passed": int(gate3_summary.get("semanticPassed", 0) or 0),
            "total": int(gate3_summary.get("expectedCaptureCount", 0) or 0),
        },
        "stability_checks": {
            "passed": int(gate3_summary.get("stable", 0) or 0),
            "total": int(gate3_summary.get("expectedCaptureCount", 0) or 0),
        },
        "direct_visual_checks": {
            "passed": int(gate3_summary.get("directVisualPassed", 0) or 0),
            "total": int(gate3_summary.get("directVisualEligible", 0) or 0),
        },
        "browseruse_trajectories": {
            "passed": int(gate4_summary.get("trajectoriesPassed", 0) or 0),
            "total": int(gate4_summary.get("trajectoryCount", 0) or 0),
        },
    }
    regression_total = metrics["regression_assertions"]["total"]
    clone_ok = bool(
        regression_total
        and metrics["regression_assertions"]["passed"] == regression_total
    )
    gate2_ok = gate2_total > 0 and gate2_passed == gate2_total
    semantic_total = metrics["semantic_checks"]["total"]
    stability_total = metrics["stability_checks"]["total"]
    direct_visual_total = metrics["direct_visual_checks"]["total"]
    gate3_ok = bool(
        semantic_total
        and metrics["semantic_checks"]["passed"] == semantic_total
        and stability_total == semantic_total
        and metrics["stability_checks"]["passed"] == stability_total
        and direct_visual_total
        and metrics["direct_visual_checks"]["passed"] == direct_visual_total
    )
    gate4_trajectory_ok = bool(
        metrics["browseruse_trajectories"]["total"]
        and metrics["browseruse_trajectories"]["passed"]
        == metrics["browseruse_trajectories"]["total"]
    )
    gate2_status = "invalid" if not gate2_ok else "passed" if runtime_fresh else "stale"
    gate3_status = "invalid" if not gate3_ok else "passed" if runtime_fresh else "stale"
    gate4_ok = (
        gate4_trajectory_ok and effective_gate4 == "approved" and runtime_fresh
    )
    gate4_status = (
        "approved"
        if gate4_ok
        else "stale"
        if gate4_trajectory_ok and effective_gate4 == "approved" and not runtime_fresh
        else effective_gate4
        if gate4_trajectory_ok
        else "invalid"
    )
    metrics["gate4_status"] = gate4_status
    historical_note = (
        ""
        if runtime_fresh
        else "; historical result — current commerce runtime requires revalidation"
    )
    historical_note_zh = (
        "" if runtime_fresh else "；历史结果——当前融合后的商业运行时需要重新验证"
    )
    gates = [
        {
            "number": 2,
            "date": str(clone.get("date", "2026-07-18")),
            "status": gate2_status,
            "status_zh": (
                "通过" if gate2_status == "passed" else "需重验" if gate2_status == "stale" else "无效"
            ),
            "title": "Behavioral regression",
            "title_zh": "行为回归",
            "summary": f"{gate2_passed}/{gate2_total} browser journeys passed{historical_note}",
            "summary_zh": f"{gate2_passed}/{gate2_total} 条浏览器旅程通过{historical_note_zh}",
            "report_id": "gate2-report",
        },
        {
            "number": 3,
            "date": str(gate3.get("capturedAt", "2026-07-18"))[:10],
            "status": gate3_status,
            "status_zh": (
                "通过" if gate3_status == "passed" else "需重验" if gate3_status == "stale" else "无效"
            ),
            "title": "Fidelity matrix",
            "title_zh": "保真度矩阵",
            "summary": (
                f"{metrics['semantic_checks']['passed']}/{semantic_total} semantic and "
                f"{metrics['direct_visual_checks']['passed']}/{metrics['direct_visual_checks']['total']} direct visual checks passed"
                f"{historical_note}"
            ),
            "summary_zh": (
                f"{metrics['semantic_checks']['passed']}/{semantic_total} 项语义检查与 "
                f"{metrics['direct_visual_checks']['passed']}/{metrics['direct_visual_checks']['total']} 项直接视觉检查通过"
                f"{historical_note_zh}"
            ),
            "report_id": "gate3-report",
        },
        {
            "number": 4,
            "date": str(gate4.get("capturedAt", "2026-07-18"))[:10],
            "status": gate4_status,
            "status_zh": (
                "已批准"
                if gate4_status == "approved"
                else "需重验"
                if gate4_status == "stale"
                else "无效"
                if gate4_status == "invalid"
                else "待批准"
            ),
            "title": "Paired BrowserUse review",
            "title_zh": "BrowserUse 配对审核",
            "summary": (
                f"{metrics['browseruse_trajectories']['passed']}/{metrics['browseruse_trajectories']['total']} trajectories passed; {approval_detail}{historical_note}"
            ),
            "summary_zh": (
                f"{metrics['browseruse_trajectories']['passed']}/{metrics['browseruse_trajectories']['total']} 条轨迹通过；已记录明确人工批准{historical_note_zh}"
            ),
            "report_id": "gate4-report",
        },
    ]

    readiness = [
        _status(
            "task_contract",
            "Amazon task contract",
            "invalid" if task_contract_error else "present",
            task_contract_error
            or f"Task {metadata.get('task_id')} on {runtime.get('container_url')}",
        ),
        _status(
            "clone_verification",
            "Clone verification report",
            "invalid"
            if report_errors.get("clone-verification") or not clone_ok
            else "present",
            report_errors.get("clone-verification") or f"{metrics['regression_assertions']['passed']}/{metrics['regression_assertions']['total']} assertions",
        ),
        *[
            _status(
                f"gate{number}_report",
                f"Gate {number} report",
                "invalid"
                if report_errors.get(f"gate{number}-report")
                or not {
                    2: gate2_ok,
                    3: gate3_ok,
                    4: gate4_trajectory_ok,
                }[number]
                else "present",
                report_errors.get(f"gate{number}-report") or next(gate["summary"] for gate in gates if gate["number"] == number),
            )
            for number in (2, 3, 4)
        ],
        _status(
            "runtime_attestation",
            "Current runtime attestation",
            "present" if runtime_fresh else "invalid",
            attestation_detail,
        ),
        _status(
            "gate4_approval",
            "Gate 4 approval",
            "present" if gate4_status == "approved" else "invalid",
            approval_detail if runtime_fresh else f"{approval_detail}; {attestation_detail}",
        ),
        _status(
            "evidence_registry",
            "Public evidence registry",
            "present" if evidence.count else "missing",
            f"{evidence.count} registered images; {len(evidence.rejected)} rejected paths",
        ),
    ]
    contract = clone.get("contract", {}) if isinstance(clone.get("contract"), dict) else {}
    port = runtime.get("canonical_port")
    fingerprint_paths = [
        task_path,
        repo_root / AMAZON_RUNTIME_MANIFEST,
        *runtime_attested_paths,
        *(repo_root / relative for relative in REPORT_FILES.values()),
        *evidence.paths,
        *calibration_paths,
    ]
    return {
        "key": AMAZON_ITEM_KEY,
        "source_type": "benchmark",
        "site_id": AMAZON_SITE_ID,
        "display_name": "Amazon",
        "description": metadata.get("description", "Amazon retail reconstruction benchmark"),
        "split": task.get("split", "dev"),
        "task_id": metadata.get("task_id", 900136),
        "instruction": task.get("instruction", ""),
        "instruction_zh": (
            f"打开 {runtime.get('container_url', 'Amazon 本地站点')}。在 Amazon 中浏览外置固态硬盘畅销榜，"
            "打开排名第 2 的 Samsung T7 Portable SSD 1TB 灰色款，将数量设为 2，并加入购物车。"
        ),
        "task_contract": {
            "method": task.get("eval_schema", {}).get("method"),
            "terminal_paths": contract.get("terminal_paths", []),
            "target": contract.get("target", {}),
            "canonical_port": port,
            "time_limit": task.get("time_limit"),
        },
        "metrics": metrics,
        "gates": gates,
        "gate4_approval": {
            "status": gate4_status,
            "historical_status": effective_gate4,
            "detail": approval_detail if runtime_fresh else attestation_detail,
            "approved_on": "2026-07-18" if effective_gate4 == "approved" else None,
        },
        "evidence": {"count": evidence.count, "counts": evidence.counts()},
        "readiness": readiness,
        "readiness_counts": _counts(readiness),
        "official_runs": runs,
        "latest_official_result": runs[0] if runs else None,
        "leaderboard": aggregate_leaderboard(runs),
        "calibrations": calibrations,
        "artifact_fingerprint": fingerprint(fingerprint_paths, repo_root),
        "internal": {
            "runtime_manifest": AMAZON_RUNTIME_MANIFEST.as_posix(),
            "task_path": runtime.get("task_path"),
            "clone_root": runtime.get("clone_root"),
            "server_command": runtime.get("server_command"),
            "local_url": runtime.get("local_url"),
            "container_url": runtime.get("container_url"),
            "viewer_path": runtime.get("viewer_path"),
            "local_host": f"127.0.0.1:{port}" if port else None,
            "runtime_fingerprint": current_runtime_fingerprint,
            "report_files": {
                identifier: relative.as_posix() for identifier, relative in REPORT_FILES.items()
            },
        },
    }


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(item[key])
        for key in (
            "key",
            "source_type",
            "site_id",
            "display_name",
            "description",
            "split",
            "task_id",
            "instruction",
            "instruction_zh",
            "task_contract",
            "metrics",
            "gates",
            "gate4_approval",
            "evidence",
            "readiness",
            "readiness_counts",
            "artifact_fingerprint",
            "calibrations",
        )
    }
    published = [_public_run(run) for run in item["official_runs"] if run.get("publishable")]
    published.sort(
        key=lambda run: (_parse_datetime(run["finished_at"]), run["run_id"]), reverse=True
    )
    result["official_runs"] = published
    result["latest_official_result"] = published[0] if published else None
    result["leaderboard"] = copy.deepcopy(item["leaderboard"])
    return result


def public_leak_findings(value: Any) -> list[str]:
    """Recursively find internal paths or hidden result detail in public data."""

    findings: list[str] = []
    blocked_keys = {
        "internal",
        "report_path",
        "run_directory",
        "server_command",
        "clone_root",
        "task_path",
        "hard_failures",
        "seeds",
        "failures",
        "evidence_manifest",
        "versions",
    }

    def visit(item: Any, location: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                here = f"{location}.{key}"
                if key in blocked_keys or (key == "journeys" and not location.endswith(".dimensions")):
                    findings.append(f"{here}: blocked internal key")
                visit(child, here)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{location}[{index}]")
        elif isinstance(item, str):
            normalized = item.lower().replace("\\", "/")
            if "judge/" in normalized:
                findings.append(f"{location}: private fixture marker")
            if normalized.startswith(("/mnt/", "/home/", "/root/", "c:/")):
                findings.append(f"{location}: absolute filesystem path")

    visit(value, "$")
    return findings


@dataclass
class CorpusIndex:
    repo_root: Path
    profile: str
    items: list[dict[str, Any]]
    invalid_runs: list[dict[str, Any]]
    evidence_registry: AmazonEvidenceRegistry

    def by_key(self, key: str) -> dict[str, Any] | None:
        return next((item for item in self.items if item["key"] == key), None)

    @property
    def runs(self) -> list[dict[str, Any]]:
        return [run for item in self.items for run in item["official_runs"]]

    @property
    def public_runs(self) -> list[dict[str, Any]]:
        if self.profile == "public":
            return copy.deepcopy(self.runs)
        return [_public_run(run) for run in self.runs if run.get("publishable")]

    @property
    def leaderboard(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.items[0]["leaderboard"]) if self.items else []

    def run_by_id(self, run_id: str) -> dict[str, Any] | None:
        return next((run for run in self.runs if run["run_id"] == run_id), None)

    def public_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        return next((run for run in self.public_runs if run["run_id"] == run_id), None)

    def as_dict(self) -> dict[str, Any]:
        item = self.items[0] if self.items else None
        return {
            "schema_version": "websitebench.amazon-viewer-index.v1",
            "generated_at": utc_now(),
            "profile": self.profile,
            "summary": {
                "item_count": len(self.items),
                "official_run_count": len(self.runs),
                "published_run_count": len(self.public_runs),
                "calibration_count": len(item.get("calibrations", [])) if item else 0,
                "invalid_run_count": len(self.invalid_runs),
                "evidence_count": item["evidence"]["count"] if item else 0,
                "gate4_status": item["metrics"]["gate4_status"] if item else "missing",
            },
            "items": self.items,
            "leaderboard": self.leaderboard,
            "invalid_runs": self.invalid_runs if self.profile == "internal" else [],
        }


def discover_corpus(
    repo_root: Path | None = None,
    *,
    profile: str = "internal",
) -> CorpusIndex:
    root = _repo_root(repo_root)
    if profile not in {"internal", "public"}:
        raise ValueError("profile must be internal or public")
    evidence = AmazonEvidenceRegistry(root)
    runs, invalid_runs = _discover_results(root)
    calibrations, invalid_calibrations, calibration_paths = _discover_calibrations(root)
    item = _amazon_item(root, runs, calibrations, calibration_paths, evidence)
    items = [item if profile == "internal" else public_item(item)]
    index = CorpusIndex(
        repo_root=root,
        profile=profile,
        items=items,
        invalid_runs=(invalid_runs + invalid_calibrations) if profile == "internal" else [],
        evidence_registry=evidence,
    )
    if profile == "public":
        findings = public_leak_findings(index.as_dict())
        if findings:
            raise ValueError("public index leak check failed: " + "; ".join(findings[:10]))
    return index
