from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAWBENCH_RUN_VIEWER_PLAYWRIGHT") != "1",
    reason="set CLAWBENCH_RUN_VIEWER_PLAYWRIGHT=1 for browser acceptance tests",
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_login_filter_compare_visual_export_logout_at_two_viewports(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    pil = pytest.importorskip("PIL.Image")
    import uvicorn

    from clawbench.viewer.app import create_app
    from clawbench.viewer.auth import AuthSettings, hash_password
    from clawbench.viewer.evidence import EvidenceStore

    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    pil.new("RGB", (640, 420), "#f2f6f4").save(source)
    pil.new("RGB", (640, 420), "#e8f5ef").save(candidate)
    showcase_key = "offlineclone--amazon-shopping-mainline"
    EvidenceStore(tmp_path / "visual", REPO_ROOT).upsert(
        showcase_key,
        "home-desktop",
        "desktop",
        source_image=source,
        candidate_image=candidate,
    )
    app = create_app(
        REPO_ROOT,
        settings=AuthSettings(
            username="reviewer",
            password_hash=hash_password("strong-password-123"),
            session_secret="browser-test-secret-" * 3,
            cookie_secure=False,
        ),
        review_root=tmp_path / "reviews",
        evidence_root=tmp_path / "visual",
    )
    port = _port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/healthz", timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    else:
        pytest.fail("viewer server did not start")

    try:
        with playwright.sync_playwright() as runtime:
            launch_options = {"headless": True}
            if executable_path := os.environ.get("CLAWBENCH_BROWSER_EXECUTABLE"):
                launch_options["executable_path"] = executable_path
            browser = runtime.chromium.launch(**launch_options)
            for width, height in ((1440, 1000), (390, 844)):
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.goto(base)
                page.get_by_label("Username").fill("reviewer")
                page.get_by_label("Password").fill("strong-password-123")
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url(base + "/")
                page.goto(base + "/tasks")
                page.locator("#task-search").fill("freshdesk")
                assert page.locator("[data-task-row]:visible").count() == 1
                page.locator("#task-search").fill("")
                page.goto(base + "/compare")
                page.locator(".compare-picker select").select_option(index=[0, 1])
                page.get_by_role("button", name="Compare selection").click()
                page.wait_for_url("**/compare?**")
                assert page.get_by_text("Official WebsiteBench score").is_visible()
                page.goto(f"{base}/tasks/{showcase_key}")
                page.get_by_role("button", name="Overlay").click()
                assert page.locator("[data-capture='0']").get_attribute("data-mode") == "overlay"
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                assert overflow is False
                with page.expect_download() as download:
                    page.get_by_role("link", name="Export").click()
                assert download.value.suggested_filename == "websitebench-reviews.json"
                page.get_by_role("button", name="Sign out").click()
                page.wait_for_url("**/login")
                context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
