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


def test_public_bilingual_evidence_and_admin_workflows_at_two_viewports(
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    from clawbench.viewer.app import create_app
    from clawbench.viewer.auth import AuthSettings, hash_password

    app = create_app(
        REPO_ROOT,
        settings=AuthSettings(
            username="reviewer",
            password_hash=hash_password("strong-password-123"),
            session_secret="browser-test-secret-" * 3,
            cookie_secure=False,
        ),
        review_root=tmp_path / "reviews",
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
            browser = runtime.chromium.launch(headless=True)
            revision = 0
            for width, height in ((1440, 1000), (390, 844)):
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.goto(base)
                assert page.get_by_text("159/159").is_visible()
                assert page.get_by_text("Revalidation required.", exact=True).is_visible()
                page.goto(base + "/leaderboard")
                assert page.get_by_text("No published runs yet", exact=True).is_visible()

                page.goto(base + "/evidence")
                assert page.locator("[data-evidence-card]").count() == 24
                page.select_option("select[name='gate']", "3")
                page.select_option("select[name='type']", "heatmap")
                page.select_option("select[name='viewport']", "mobile")
                page.get_by_role("button", name="Apply filters").click()
                page.wait_for_url("**/evidence?**")
                cards = page.locator("[data-evidence-card]")
                assert 0 < cards.count() <= 24
                assert all(cards.nth(index).get_attribute("data-gate") == "3" for index in range(cards.count()))
                assert all(cards.nth(index).get_attribute("data-type") == "heatmap" for index in range(cards.count()))
                assert all(cards.nth(index).get_attribute("data-viewport") == "mobile" for index in range(cards.count()))

                page.locator("[data-language-toggle]").click()
                assert page.locator("html").get_attribute("lang") == "zh"
                assert page.evaluate("localStorage.getItem('websitebench-language')") == "zh"
                page.goto(base + "/leaderboard")
                assert page.get_by_text("暂无已发布 Run", exact=True).is_visible()
                assert not page.get_by_text("No published runs yet", exact=True).is_visible()
                page.goto(base + "/methodology")
                assert page.locator("html").get_attribute("lang") == "zh"
                assert page.get_by_text("成绩、验证与证据始终分开。").is_visible()
                page.locator("[data-language-toggle]").click()

                for public_path in ("/", "/benchmark/amazon", "/leaderboard", "/evidence", "/methodology"):
                    page.goto(base + public_path)
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                    )
                    assert overflow is False

                page.goto(base + "/login")
                page.locator("input[name='username']").fill("reviewer")
                page.locator("input[name='password']").fill("strong-password-123")
                page.locator("form.stack-form button[type='submit']").click()
                page.wait_for_url(base + "/admin")
                page.locator("#review-form input[name='reviewer']").fill("reviewer")
                page.locator("#review-form select[name='gate']").select_option("approve")
                page.locator("#review-form button[type='submit']").click()
                revision += 1
                playwright.expect(page.locator("#review-status")).to_contain_text(
                    f"Saved revision {revision}"
                )
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                assert overflow is False
                with page.expect_download() as download:
                    page.get_by_role("link", name="Export").click()
                assert download.value.suggested_filename == "websitebench-amazon-review.json"
                page.get_by_role("button", name="Sign out").click()
                page.wait_for_url(base + "/")
                assert page.goto(base + "/admin").url.startswith(base + "/login")
                context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
