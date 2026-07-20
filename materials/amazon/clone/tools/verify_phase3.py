#!/usr/bin/env python3
"""Compare the local Amazon clone with the frozen Gate 1 evidence matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clawbench.amazon_contract import (  # noqa: E402
    amazon_runtime_fingerprint,
    load_amazon_runtime_contract,
)
from clawbench.viewer.metrics import (  # noqa: E402
    analyze_visual_content,
    compare_images,
    compare_stability,
)


RUNTIME_MANIFEST = load_amazon_runtime_contract(REPO_ROOT)
FORMAT = "clawbench.amazon.phase3-fidelity-report.v1"
SOURCE_FORMAT = "clawbench-pro.public-source-observation.v2"
CONFIG = ROOT / "phase3-fidelity.json"
SERVER = REPO_ROOT / RUNTIME_MANIFEST["runtime"]["entrypoint"]
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a",
    "all",
    "amazon",
    "and",
    "for",
    "in",
    "of",
    "or",
    "the",
    "to",
    "your",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_inputs(config_path: Path, source_report_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = json.loads(source_report_path.read_text(encoding="utf-8"))
    if config.get("format") != "clawbench.amazon.phase3-fidelity.v1":
        raise ValueError(f"unsupported Phase 3 config: {config.get('format')!r}")
    if source.get("format") != SOURCE_FORMAT:
        raise ValueError(f"unsupported source report: {source.get('format')!r}")
    expected = config["sourceBaseline"]
    actual_sha = sha256_file(source_report_path)
    if source.get("snapshotId") != expected["snapshotId"]:
        raise ValueError("source snapshot ID differs from the frozen Gate 1 baseline")
    if actual_sha != expected["reportSha256"]:
        raise ValueError("source report SHA-256 differs from the frozen Gate 1 baseline")
    scenes = config.get("scenes", [])
    viewports = config.get("viewports", {})
    if len(scenes) != 20 or len(viewports) != 5:
        raise ValueError("Phase 3 requires exactly 20 scenes across five viewports")
    source_keys = {(page.get("page"), page.get("viewport")) for page in source["pages"]}
    missing = [
        f"{scene['source']}/{viewport}"
        for scene in scenes
        for viewport in viewports
        if (scene["source"], viewport) not in source_keys
    ]
    if missing:
        raise ValueError(f"source report is missing matrix entries: {', '.join(missing)}")
    return config, source


def source_quality(page: dict[str, Any]) -> str:
    status = page.get("navigationStatus")
    body_length = page.get("dom", {}).get("captureQuality", {}).get("bodyTextLength", 0)
    if isinstance(status, int) and status >= 400:
        return "expected-error"
    if status == 202 or not isinstance(body_length, int) or body_length <= 0:
        return "protected-or-empty"
    if body_length >= 1000:
        return "strong"
    return "partial"


def visual_composite(metrics: dict[str, float]) -> float:
    return round(
        0.4 * metrics["ssim"]
        + 0.2 * metrics["edge_f1"]
        + 0.4 * metrics["color_histogram"],
        4,
    )


def direct_visual_pass(metrics: dict[str, float], thresholds: dict[str, float]) -> bool:
    return (
        visual_composite(metrics) >= thresholds["composite"]
        and metrics["ssim"] >= thresholds["ssim"]
        and metrics["edge_f1"] >= thresholds["edge_f1"]
        and metrics["color_histogram"] >= thresholds["color_histogram"]
        and metrics["normalized_mae"] <= thresholds["normalized_mae_max"]
    )


def _ratio_similarity(left: int | float, right: int | float) -> float:
    left = max(float(left), 0.0)
    right = max(float(right), 0.0)
    if left == right == 0:
        return 1.0
    return min(left, right) / max(left, right, 1.0)


def _tokens(values: list[str]) -> set[str]:
    return {
        token
        for value in values
        for token in TOKEN_RE.findall(value.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def structural_similarity(source_dom: dict[str, Any], clone_dom: dict[str, Any]) -> dict[str, float]:
    source_counts = source_dom.get("counts", {})
    clone_counts = clone_dom.get("counts", {})
    count_keys = ("elements", "links", "forms", "buttons", "images")
    count_score = sum(
        _ratio_similarity(source_counts.get(key, 0), clone_counts.get(key, 0))
        for key in count_keys
    ) / len(count_keys)
    source_height = source_dom.get("dimensions", {}).get("documentHeight", 0)
    clone_height = clone_dom.get("dimensions", {}).get("documentHeight", 0)
    height_score = _ratio_similarity(source_height, clone_height)
    source_text = [
        str(item.get("text", ""))
        for item in source_dom.get("headingsAndControls", [])
        if isinstance(item, dict)
    ]
    clone_text = [str(value) for value in clone_dom.get("headingsAndControls", [])]
    source_tokens = _tokens(source_text)
    clone_tokens = _tokens(clone_text)
    heading_score = (
        len(source_tokens & clone_tokens) / len(source_tokens)
        if source_tokens
        else float(not clone_tokens)
    )
    score = 0.45 * count_score + 0.2 * height_score + 0.35 * heading_score
    return {
        "score": round(score, 4),
        "count_ratio": round(count_score, 4),
        "document_height_ratio": round(height_score, 4),
        "source_heading_token_recall": round(heading_score, 4),
    }


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_server(origin: str, process: subprocess.Popen[str]) -> None:
    import http.client

    parsed = urlsplit(origin)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"server exited during startup: {output}")
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        try:
            connection.request("HEAD", "/")
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return
        except OSError:
            time.sleep(0.05)
        finally:
            connection.close()
    raise TimeoutError("FastAPI SSR server did not start")


def settled(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => document.documentElement.dataset.render === 'fastapi-ssr'",
        timeout=10_000,
    )
    page.wait_for_timeout(350)


def apply_action(page: Page, action: str | None) -> None:
    if action == "open-menu":
        page.locator("[data-open-menu]:visible").first.click()
    elif action == "autocomplete":
        search = page.locator("[data-search-form]:visible input[type=search]").first
        search.fill("wireless")
        page.locator(".autocomplete-panel:visible [role=option]").first.wait_for()
    elif action:
        raise ValueError(f"unknown Phase 3 action: {action}")
    if action:
        page.wait_for_timeout(250)


def capture_dom(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const visibleText = (node) => {
            const rect = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
              ? (node.innerText || node.getAttribute('aria-label') || '').trim()
              : '';
          };
          const controls = [...document.querySelectorAll('h1,h2,h3,button,a,[role=option]')]
            .map(visibleText).filter(Boolean).slice(0, 120);
          return {
            title: document.title,
            dimensions: {
              viewportWidth: innerWidth,
              viewportHeight: innerHeight,
              documentWidth: document.documentElement.scrollWidth,
              documentHeight: document.documentElement.scrollHeight,
            },
            counts: {
              elements: document.querySelectorAll('*').length,
              links: document.links.length,
              forms: document.forms.length,
              buttons: document.querySelectorAll('button').length,
              images: document.images.length,
            },
            captureQuality: {
              bodyTextLength: (document.body.innerText || '').length,
              mainTextLength: (document.querySelector('main')?.innerText || '').length,
              mainPresent: Boolean(document.querySelector('main')),
            },
            headingsAndControls: controls,
          };
        }"""
    )


def screenshot_record(page: Page, path: Path, *, full_page: bool) -> dict[str, Any]:
    body = page.screenshot(path=str(path), full_page=full_page, animations="disabled")
    os.chmod(path, 0o600)
    with Image.open(path) as image:
        width, height = image.size
    return {
        "file": path.name,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "width": width,
        "height": height,
    }


def compose_review(source_path: Path, clone_path: Path, output_path: Path) -> None:
    with Image.open(source_path) as source_image, Image.open(clone_path) as clone_image:
        source = source_image.convert("RGB")
        clone = clone_image.convert("RGB")
        width = source.width + clone.width
        height = max(source.height, clone.height) + 34
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(source, (0, 34))
        canvas.paste(clone, (source.width, 34))
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 10), "FROZEN SOURCE", fill="#111111")
        draw.text((source.width + 12, 10), "LOCAL CLONE", fill="#111111")
        canvas.save(output_path, "JPEG", quality=88, optimize=True)
    os.chmod(output_path, 0o600)


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def run_capture(
    config: dict[str, Any],
    source_report: dict[str, Any],
    source_root: Path,
    output: Path,
) -> dict[str, Any]:
    private_directory(output)
    private_directory(output / "screenshots")
    private_directory(output / "full-page")
    private_directory(output / "heatmaps")
    private_directory(output / "review-pairs")
    source_index = {
        (page["page"], page["viewport"]): page for page in source_report["pages"]
    }
    port = reserve_port()
    origin = f"http://127.0.0.1:{port}"
    db_dir = tempfile.TemporaryDirectory(prefix="amazon-phase3-state-")
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--db",
            str(Path(db_dir.name) / "state.sqlite3"),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    captures: list[dict[str, Any]] = []
    try:
        wait_for_server(origin, process)
        with tempfile.TemporaryDirectory(prefix="amazon-phase3-frames-") as frame_dir_name:
            frame_dir = Path(frame_dir_name)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                for viewport_name, viewport in config["viewports"].items():
                    mobile = viewport["width"] <= 390
                    context = browser.new_context(
                        viewport=viewport,
                        locale="en-US",
                        timezone_id="America/New_York",
                        user_agent=(
                            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
                            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
                            if mobile
                            else "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/136 Safari/537.36"
                        ),
                        is_mobile=mobile,
                        has_touch=mobile,
                    )
                    for scene in config["scenes"]:
                        page = context.new_page()
                        external: list[str] = []
                        failures: list[str] = []
                        page_errors: list[str] = []
                        page.on(
                            "request",
                            lambda request, bucket=external: bucket.append(request.url)
                            if urlsplit(request.url).hostname not in {"127.0.0.1", "localhost"}
                            else None,
                        )
                        page.on("requestfailed", lambda request, bucket=failures: bucket.append(request.url))
                        page.on("pageerror", lambda error, bucket=page_errors: bucket.append(str(error)))
                        response = page.goto(origin + scene["clonePath"], wait_until="domcontentloaded")
                        settled(page)
                        apply_action(page, scene.get("action"))
                        ready_count = page.locator(scene["ready"]).count()
                        page.evaluate("scrollTo(0, 0)")
                        page.wait_for_timeout(100)
                        slug = f"{scene['source']}-{viewport_name}"
                        frame_a = frame_dir / f"{slug}-a.png"
                        frame_b = frame_dir / f"{slug}-b.png"
                        page.screenshot(path=str(frame_a), animations="disabled")
                        page.wait_for_timeout(500)
                        page.screenshot(path=str(frame_b), animations="disabled")
                        stability = compare_stability(frame_a, frame_b)
                        screenshot_path = output / "screenshots" / f"clone-{slug}.png"
                        full_path = output / "full-page" / f"clone-{slug}-full.png"
                        screenshot = screenshot_record(page, screenshot_path, full_page=False)
                        full_page = screenshot_record(page, full_path, full_page=True)
                        visual_content = analyze_visual_content(screenshot_path)
                        clone_dom = capture_dom(page)
                        source_page = source_index[(scene["source"], viewport_name)]
                        quality = source_quality(source_page)
                        source_screenshot = source_root / source_page["screenshot"]["file"]
                        source_visual_content = (
                            analyze_visual_content(source_screenshot)
                            if source_screenshot.is_file()
                            else None
                        )
                        comparable = (
                            scene["mode"] != "unavailable"
                            and quality not in {"protected-or-empty", "expected-error"}
                            and source_screenshot.is_file()
                            and source_visual_content is not None
                            and not source_visual_content["near_uniform"]
                        )
                        metrics: dict[str, float] | None = None
                        direct_pass: bool | None = None
                        heatmap_file: str | None = None
                        if comparable:
                            heatmap_path = output / "heatmaps" / f"difference-{slug}.webp"
                            metrics = compare_images(source_screenshot, screenshot_path, heatmap_path)
                            metrics["composite"] = visual_composite(metrics)
                            heatmap_file = _relative(heatmap_path, output)
                            if scene["mode"] == "direct-visual":
                                direct_pass = direct_visual_pass(
                                    metrics, config["directVisualThresholds"]
                                )
                        structure = structural_similarity(source_page["dom"], clone_dom)
                        expected_status = int(scene.get("expectedStatus", 200))
                        actual_status = response.status if response else None
                        horizontal_overflow = (
                            clone_dom["dimensions"]["documentWidth"]
                            > clone_dom["dimensions"]["viewportWidth"] + 2
                        )
                        semantic_pass = (
                            actual_status == expected_status
                            and ready_count >= int(scene["minimum"])
                            and not external
                            and not failures
                            and not page_errors
                            and not visual_content["near_uniform"]
                            and not horizontal_overflow
                        )
                        captures.append(
                            {
                                "scene": scene["source"],
                                "viewport": viewport_name,
                                "mode": scene["mode"],
                                "reason": scene.get("reason"),
                                "clonePath": scene["clonePath"],
                                "httpStatus": actual_status,
                                "expectedStatus": expected_status,
                                "readySelector": scene["ready"],
                                "readyCount": ready_count,
                                "minimumReadyCount": scene["minimum"],
                                "sourceQuality": quality,
                                "sourceVisualContent": source_visual_content,
                                "sourceScreenshot": source_page["screenshot"]["file"],
                                "comparable": comparable,
                                "screenshot": {**screenshot, "file": _relative(screenshot_path, output)},
                                "fullPageScreenshot": {**full_page, "file": _relative(full_path, output)},
                                "heatmap": heatmap_file,
                                "visualMetrics": metrics,
                                "directVisualPass": direct_pass,
                                "structuralMetrics": structure,
                                "stability": stability,
                                "visualContent": visual_content,
                                "horizontalOverflow": horizontal_overflow,
                                "semanticPass": semantic_pass,
                                "externalRequests": external,
                                "requestFailures": failures,
                                "pageErrors": page_errors,
                                "cloneDom": clone_dom,
                            }
                        )
                        page.close()
                    context.close()
                browser.close()
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        db_dir.cleanup()

    review_keys = (
        ("all-departments-best-sellers-live", "desktop"),
        ("portable-ssd-filtered-search-live", "desktop-compact"),
        ("computers-category-live", "mobile"),
        ("empty-cart-live", "desktop"),
        ("account-entry-live", "desktop"),
        ("samsung-t7-product-response-render", "mobile"),
    )
    capture_index = {(item["scene"], item["viewport"]): item for item in captures}
    review_pairs = []
    for scene_name, viewport_name in review_keys:
        item = capture_index[(scene_name, viewport_name)]
        source_path = source_root / item["sourceScreenshot"]
        clone_path = output / item["screenshot"]["file"]
        pair_path = output / "review-pairs" / f"source-clone-{scene_name}-{viewport_name}.jpg"
        compose_review(source_path, clone_path, pair_path)
        review_pairs.append(_relative(pair_path, output))

    summary = {
        "captureCount": len(captures),
        "expectedCaptureCount": len(config["scenes"]) * len(config["viewports"]),
        "semanticPassed": sum(item["semanticPass"] for item in captures),
        "stable": sum(item["stability"]["stable"] for item in captures),
        "directVisualEligible": sum(item["directVisualPass"] is not None for item in captures),
        "directVisualPassed": sum(item["directVisualPass"] is True for item in captures),
        "directVisualFailed": sum(item["directVisualPass"] is False for item in captures),
        "structuralComparable": sum(
            item["mode"] == "structural" and item["comparable"] for item in captures
        ),
        "sourceUnavailable": sum(not item["comparable"] for item in captures),
        "externalRequests": sum(len(item["externalRequests"]) for item in captures),
        "requestFailures": sum(len(item["requestFailures"]) for item in captures),
        "pageErrors": sum(len(item["pageErrors"]) for item in captures),
        "horizontalOverflows": sum(item["horizontalOverflow"] for item in captures),
    }
    report = {
        "format": FORMAT,
        "gate": 3,
        "runtimeStructuralSha256": amazon_runtime_fingerprint(
            REPO_ROOT, RUNTIME_MANIFEST
        ),
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "sourceBaseline": {
            **config["sourceBaseline"],
            "reportPath": str(source_root / "report.json"),
        },
        "cloneBaseline": config["cloneBaseline"],
        "directVisualThresholds": config["directVisualThresholds"],
        "summary": summary,
        "reviewPairs": review_pairs,
        "captures": captures,
    }
    private_json(output / "report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config, source = load_inputs(args.config, args.source_report)
    report = run_capture(config, source, args.source_report.parent, args.output_dir)
    print(json.dumps(report["summary"], indent=2))
    summary = report["summary"]
    strict_pass = (
        summary["captureCount"] == summary["expectedCaptureCount"]
        and summary["semanticPassed"] == summary["captureCount"]
        and summary["stable"] == summary["captureCount"]
        and summary["directVisualFailed"] == 0
        and summary["externalRequests"] == 0
        and summary["requestFailures"] == 0
        and summary["pageErrors"] == 0
        and summary["horizontalOverflows"] == 0
    )
    return 0 if strict_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
