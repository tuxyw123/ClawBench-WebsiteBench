from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clawbench.viewer.capture import (
    classify_blocked_request,
    classify_network_failure,
    classify_page_error,
    comparison_readiness,
    decide_capture_status as decide_side_capture_status,
    detect_access_gate,
    detect_soft_error,
    reviewability_for_status,
)
from clawbench.viewer.clone_process import CloneProcessManager
from clawbench.viewer.evidence import EvidenceStore, decide_capture_status, file_sha256
from clawbench.viewer.gateway import (
    clone_public_path,
    parse_clone_request,
    rewrite_clone_body,
    rewrite_location,
    rewrite_set_cookie,
)
from clawbench.viewer.metrics import compare_images
from clawbench.viewer.policy import request_decision


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_capture_status_keeps_partial_and_diagnostic_states_explicit() -> None:
    assert decide_capture_status(source_available=True, candidate_available=True) == (
        "captured",
        "reliable",
    )
    assert decide_capture_status(source_available=False, candidate_available=True) == (
        "partial",
        "caution",
    )
    assert decide_capture_status(
        source_available=True, candidate_available=True, comparable=False
    ) == ("not_comparable", "unavailable")


def test_evidence_resolver_rejects_unregistered_and_parent_paths(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    source = tmp_path / "source.png"
    pil.new("RGB", (20, 20), "white").save(source)
    store = EvidenceStore(tmp_path / "artifacts", REPO_ROOT)
    manifest = store.upsert(
        "legacy--dev-115-freshdesk-invoice-dispute-ticket", "home", "desktop", source_image=source
    )
    relative = manifest["captures"][0]["source_image"]
    resolved = store.resolve("legacy--dev-115-freshdesk-invoice-dispute-ticket", relative)
    assert file_sha256(resolved) == manifest["captures"][0]["source_sha256"]
    with pytest.raises(FileNotFoundError):
        store.resolve("legacy--dev-115-freshdesk-invoice-dispute-ticket", "../../source.png")


def test_visual_evidence_can_be_isolated_per_model_run(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    pil.new("RGB", (20, 20), "white").save(first)
    pil.new("RGB", (20, 20), "black").save(second)
    store = EvidenceStore(tmp_path / "artifacts", REPO_ROOT)
    one = store.upsert(
        "legacy--dev-115-freshdesk-invoice-dispute-ticket", "home", "desktop",
        run_id="model-one", source_image=first,
    )
    two = store.upsert(
        "legacy--dev-115-freshdesk-invoice-dispute-ticket", "home", "desktop",
        run_id="model-two", source_image=second,
    )
    assert one["run_id"] == "model-one"
    assert two["run_id"] == "model-two"
    assert store.manifest_path("legacy--dev-115-freshdesk-invoice-dispute-ticket", "model-one") != store.manifest_path(
        "legacy--dev-115-freshdesk-invoice-dispute-ticket", "model-two"
    )
    assert store.resolve(
        "legacy--dev-115-freshdesk-invoice-dispute-ticket", one["captures"][0]["source_image"], "model-one"
    ).is_file()


def test_image_diagnostics_are_not_named_as_official_score(tmp_path: Path) -> None:
    pil = pytest.importorskip("PIL.Image")
    pytest.importorskip("skimage")
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    heatmap = tmp_path / "heat.webp"
    pil.new("RGB", (40, 30), "white").save(first)
    pil.new("RGB", (40, 30), "white").save(second)
    metrics = compare_images(first, second, heatmap)
    assert metrics["ssim"] == 1.0
    assert "score" not in metrics
    assert heatmap.is_file()


def test_gateway_url_cookie_body_and_policy_rewrites() -> None:
    key = "legacy--demo"
    assert clone_public_path(key, "/login") == "/clone/legacy--demo/login"
    assert parse_clone_request("/clone/legacy--demo/api?q=1") == (
        key,
        "/api?q=1",
    )
    html = b'<html><head><script src="/static/app.js"></script></head><body><form action="/save"></form></body></html>'
    rewritten = rewrite_clone_body(html, "text/html; charset=utf-8", key).decode()
    assert 'src="/clone/legacy--demo/static/app.js"' in rewritten
    assert 'action="/clone/legacy--demo/save"' in rewritten
    assert "window.fetch = (input, init)" in rewritten
    nonced = rewrite_clone_body(
        html,
        "text/html; charset=utf-8",
        key,
        script_nonce="safe-nonce",
    ).decode()
    assert nonced.count('nonce="safe-nonce"') == 2
    payload = json.loads(
        rewrite_clone_body(
            b'{"image":"/static/item.png","route":"/cart"}',
            "application/json",
            key,
        )
    )
    assert payload["image"] == "/clone/legacy--demo/static/item.png"
    assert payload["route"] == "/cart"
    assert rewrite_location("/login", key) == "/clone/legacy--demo/login"
    assert rewrite_set_cookie("sid=x; Path=/; HttpOnly", key) == (
        "sid=x; Path=/clone/legacy--demo/; HttpOnly"
    )
    assert rewrite_set_cookie("sid=x; Domain=127.0.0.1; Path=/", key) == (
        "sid=x; Path=/clone/legacy--demo/"
    )
    assert not request_decision("POST", "https://example.com/write", {"example.com"}).allow


def test_capture_integrity_classifies_request_impact_and_error_shells() -> None:
    assert classify_blocked_request(
        "GET", "https://cdn.example.test/main.css", "stylesheet", "blocked_unlisted_host"
    ) == "visual"
    assert classify_blocked_request(
        "POST", "https://example.test/api", "other", "blocked_mutating_method"
    ) == "behavioral"
    assert classify_network_failure(
        "https://www.google-analytics.com/g/collect", "fetch"
    ) == "nonvisual"
    blocked = [{"url": "https://cdn.test/app.js", "impact": "behavioral"}]
    assert classify_page_error("Failed https://cdn.test/app.js", blocked) == "behavioral"
    assert detect_soft_error("Page not found", None) == "page not found"
    assert detect_access_gate("Just a moment", None, "Cloudflare") == (
        "cloudflare verification"
    )


def test_capture_integrity_distinguishes_blocked_failed_and_degraded() -> None:
    defaults = {
        "image_available": True,
        "soft_error": None,
        "access_gate": None,
        "blank_viewport": False,
        "screenshot_error": None,
        "navigation_error": None,
        "status_code": 200,
        "has_quality_issue": False,
    }
    assert decide_side_capture_status(side="source", **defaults) == "captured"
    assert decide_side_capture_status(
        side="source", **{**defaults, "access_gate": "access denied"}
    ) == "blocked"
    assert decide_side_capture_status(
        side="candidate", **{**defaults, "access_gate": "access denied"}
    ) == "failed"
    assert decide_side_capture_status(
        side="candidate", **{**defaults, "navigation_error": "timeout"}
    ) == "degraded"


def test_comparison_readiness_requires_two_usable_images() -> None:
    captured = {"status": "captured", "image": "capture.webp"}
    assert comparison_readiness(captured, captured) == ("captured", None)
    assert comparison_readiness(
        {"status": "blocked", "image": None, "http_status": 403}, captured
    ) == ("blocked", "source capture blocked: HTTP 403")
    assert comparison_readiness(captured, {"status": "failed", "image": None})[0] == (
        "failed"
    )
    assert reviewability_for_status("captured") == "reliable"
    assert reviewability_for_status("degraded") == "caution"


def test_clone_manager_does_not_proxy_an_unmanaged_process_on_declared_port() -> None:
    class ForeignHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"foreign-service")

        def log_message(self, format: str, *args: object) -> None:
            pass

    foreign = ThreadingHTTPServer(("127.0.0.1", 0), ForeignHandler)
    thread = threading.Thread(target=foreign.serve_forever, daemon=True)
    thread.start()
    key = "legacy--gateway-collision"
    clone_root = "website-clone/v2-086-job-search-hr-cv-autofill-greenhouse-meta"
    items = [
        {
            "key": key,
            "source_type": "legacy",
            "internal": {
                "clone_root": clone_root,
                "local_host": f"host.docker.internal:{foreign.server_port}",
                "server_command": f"python3 {clone_root}/server.py",
            },
        }
    ]
    manager = CloneProcessManager(REPO_ROOT, items, {key})
    try:
        base = manager.ensure(key)
        assert not base.endswith(f":{foreign.server_port}")
        with urllib.request.urlopen(base, timeout=3) as response:
            assert b"foreign-service" not in response.read()
    finally:
        manager.close()
        foreign.shutdown()
        foreign.server_close()
        thread.join(timeout=3)
