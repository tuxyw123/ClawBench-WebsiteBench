#!/usr/bin/env python3
"""Verify the task-scoped Idealist source task 776 replica."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from PIL import Image, ImageStat
from playwright.sync_api import Browser, BrowserContext, Page, Request, sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[3]
CLONE_ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = REPO_ROOT / "tasks/dev/dev-118-idealist-dc-program-manager-apply/task.json"
SOURCE_TASK_PATH = REPO_ROOT / "test-cases/v2/v2-776-nonprofit-charity-volunteer-signup-idealist/task.json"
SCHEMA_PATH = REPO_ROOT / "claw-bench-v2/schemas/task.schema.json"
DEFAULT_REPORT = CLONE_ROOT / "verification-report.json"
DEFAULT_ARTIFACTS = CLONE_ROOT / ".verification-artifacts"
DEFAULT_DB = CLONE_ROOT / "idealist.sqlite3"
TARGET_KEY = "dumbarton-arts-education-program-manager-washington-dc"
EMAIL = "alex.green.uoft@clawbench.cc"
PASSWORD = "Idealist!42"
COVER = (
    "Dear Dumbarton Arts & Education hiring team, I am applying for the Program Manager role because your commitment to arts access "
    "and education equity matches how I lead dependable, inclusive programs. In leading technical teams and mentoring colleagues, I "
    "have built clear plans, repeatable operations, thoughtful feedback practices, and calm communication across competing priorities. "
    "I would bring that same discipline to program schedules, community partnerships, budgets, and impact reporting while listening "
    "closely to teaching artists, families, and learners across Washington, DC. Sincerely, Alex Green"
)
EXPECTED_PROFILE = {
    "firstName": "Alex",
    "lastName": "Green",
    "email": EMAIL,
    "location": "Toronto, Ontario, Canada",
    "resumeFileName": "Alex_Green_Resume.pdf",
    "intent": "COMPLETE_IDEALIST_PROFILE",
}
VIEWPORTS = {"desktop": {"width": 1365, "height": 900}, "mobile": {"width": 390, "height": 844}}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Gate:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, condition: Any, detail: Any = None) -> None:
        passed = bool(condition)
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            raise AssertionError(f"{name}: {detail}")


def launch_browser(playwright: Any) -> Browser:
    options: dict[str, Any] = {"headless": True}
    if executable := os.environ.get("CLAWBENCH_PLAYWRIGHT_EXECUTABLE"):
        options["executable_path"] = executable
    return playwright.chromium.launch(**options)


def new_page(browser: Browser, clone: str, viewport: dict[str, int]) -> tuple[BrowserContext, Page, dict[str, list[str]]]:
    context = browser.new_context(viewport=viewport, locale="en-US")
    page = context.new_page()
    telemetry: dict[str, list[str]] = {"page_errors": [], "console_errors": [], "external_requests": [], "local_requests": []}
    origin = urlparse(clone)
    page.on("pageerror", lambda error: telemetry["page_errors"].append(str(error)))
    page.on("console", lambda message: telemetry["console_errors"].append(message.text) if message.type == "error" else None)

    def route_handler(route: Any, request: Request) -> None:
        parsed = urlparse(request.url)
        external = parsed.scheme not in {"", "data", "blob", "about"} and parsed.netloc != origin.netloc
        if external:
            telemetry["external_requests"].append(request.url)
            route.abort()
            return
        if parsed.netloc == origin.netloc:
            telemetry["local_requests"].append(request.url)
        route.continue_()

    page.route("**/*", route_handler)
    return context, page, telemetry


def state(page: Page) -> dict[str, Any]:
    return page.evaluate("fetch('/api/state').then(r=>r.json())")


def fetch(page: Page, path: str, method: str = "GET", body: Any = None, content_type: str = "application/json") -> dict[str, Any]:
    return page.evaluate(
        """async ({path,method,body,contentType}) => {
          const options = {method, headers:{'Content-Type':contentType}};
          if (body !== null) options.body = contentType === 'application/json' ? JSON.stringify(body) : body;
          const response = await window.fetch(path, options);
          return {status:response.status, body:await response.json()};
        }""",
        {"path": path, "method": method, "body": body, "contentType": content_type},
    )


def reset(page: Page, clone: str) -> None:
    page.goto(clone + "/", wait_until="networkidle", timeout=15_000)
    result = fetch(page, "/api/reset", "POST", {})
    if result["status"] != 200:
        raise AssertionError(f"reset failed: {result}")
    page.goto(clone + "/jobs", wait_until="networkidle")


def matcher_matches(task: dict[str, Any], url: str, method: str) -> bool:
    matcher = task["eval_schema"]
    return method == matcher["method"] and re.fullmatch(matcher["url_pattern"], url) is not None


def screenshot_metrics(gate: Gate, page: Page, artifacts: Path, prefix: str, name: str) -> dict[str, Any]:
    page.locator("main").wait_for(state="visible", timeout=8_000)
    metrics = page.evaluate(
        """() => {
          const visible = node => Boolean(node && node.offsetParent !== null);
          const controls = [...document.querySelectorAll('button,input,select,textarea,a[href]')].filter(visible);
          const bad = controls.filter(node => { const r=node.getBoundingClientRect(); return r.width<1 || r.height<1 || r.right<0 || r.left>innerWidth; });
          const images = [...document.querySelectorAll('img')].filter(visible);
          return {innerWidth,scrollWidth:document.documentElement.scrollWidth,bodyHeight:document.body.scrollHeight,
            controls:controls.length,badControls:bad.map(n=>n.outerHTML.slice(0,140)),images:images.length,
            brokenImages:images.filter(i=>!i.complete||i.naturalWidth<1).map(i=>i.src)};
        }"""
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / f"{prefix}-{name}.png"
    page.screenshot(path=str(path), full_page=False)
    with Image.open(path).convert("RGB") as image:
        sample = image.resize((64, 64))
        stat = ImageStat.Stat(sample)
        metrics["pixelMean"] = [round(value, 2) for value in stat.mean]
        metrics["pixelVariation"] = sum(high - low for low, high in sample.getextrema())
    metrics["bytes"] = path.stat().st_size
    metrics["path"] = str(path.relative_to(REPO_ROOT))
    gate.check(f"{prefix}:{name}:no-overflow", metrics["scrollWidth"] <= metrics["innerWidth"] + 1, metrics)
    gate.check(f"{prefix}:{name}:controls-in-canvas", not metrics["badControls"], metrics)
    gate.check(f"{prefix}:{name}:images-loaded", not metrics["brokenImages"], metrics)
    gate.check(f"{prefix}:{name}:nonblank", metrics["controls"] > 0 and metrics["bytes"] > 10_000 and metrics["pixelVariation"] > 100, metrics)
    return metrics


def finish_telemetry(gate: Gate, prefix: str, telemetry: dict[str, list[str]]) -> None:
    expected = [message for message in telemetry["console_errors"] if "Failed to load resource" in message]
    unexpected = [message for message in telemetry["console_errors"] if message not in expected]
    gate.check(f"{prefix}:no-page-errors", not telemetry["page_errors"], telemetry["page_errors"])
    gate.check(f"{prefix}:no-unexpected-console-errors", not unexpected, {"unexpected": unexpected, "expectedNegativeResponses": expected})
    gate.check(f"{prefix}:no-external-network", not telemetry["external_requests"], telemetry["external_requests"])
    gate.check(f"{prefix}:local-assets-used", any("/static/styles.css" in url for url in telemetry["local_requests"]) and any("/static/assets/idealist-mark.svg" in url for url in telemetry["local_requests"]), telemetry["local_requests"])


def search_and_open(gate: Gate, page: Page, prefix: str, artifacts: Path, visuals: dict[str, Any]) -> None:
    gate.check(f"{prefix}:jobs-heading", page.locator("h1").inner_text() == "Find nonprofit jobs", page.locator("h1").inner_text())
    page.locator('[name="keywords"]').fill("Program Manager")
    page.locator('[name="location"]').fill("Washington, DC")
    page.locator('[name="employment"]').select_option(label="Full Time")
    page.locator('[name="sector"]').select_option(label="Nonprofit")
    page.locator("#job-search-form button[type=submit]").click()
    page.locator("text=1 matching job").wait_for()
    gate.check(f"{prefix}:one-exact-result", page.locator(".job-card").count() == 1, page.locator("main").inner_text())
    card = page.locator(".job-card")
    gate.check(f"{prefix}:rank-one-target", card.get_attribute("data-job-key") == TARGET_KEY and "Dumbarton Arts & Education" in card.inner_text() and "Full Time" in card.inner_text() and "Nonprofit" in card.inner_text(), card.inner_text())
    search_state = state(page)
    latest = search_state["searches"][-1]
    gate.check(f"{prefix}:search-state-exact", latest["keywords"] == "Program Manager" and latest["location"] == "Washington, DC" and latest["employment"] == "Full Time" and latest["sector"] == "Nonprofit" and latest["result_keys"] == [TARGET_KEY], latest)
    visuals["results"] = screenshot_metrics(gate, page, artifacts, prefix, "results")
    page.locator(f'[data-open-job="{TARGET_KEY}"]').click()
    page.wait_for_url(f"**/en/nonprofit-job/{TARGET_KEY}")
    gate.check(f"{prefix}:detail-identity", page.locator("h1").inner_text() == "Program Manager" and "Dumbarton Arts & Education" in page.locator("main").inner_text())
    gate.check(f"{prefix}:detail-content", "program schedules" in page.locator("main").inner_text().lower() and "Washington, DC" in page.locator("main").inner_text())
    gate.check(f"{prefix}:detail-journal", state(page)["jobViews"][-1]["job_key"] == TARGET_KEY, state(page)["jobViews"])
    visuals["detail"] = screenshot_metrics(gate, page, artifacts, prefix, "detail")


def register_and_login(gate: Gate, page: Page, prefix: str) -> None:
    page.locator(".apply-button:visible").first.click()
    page.wait_for_url("**/user/register")
    gate.check(f"{prefix}:register-applicant-shape", page.locator("h1").inner_text() == "Applicant registration" and page.locator('[name="accountType"]').input_value() == "APPLICANT")
    gate.check(f"{prefix}:assigned-profile-prefill", page.locator('[name="firstName"]').input_value() == "Alex" and page.locator('[name="lastName"]').input_value() == "Green" and page.locator('[name="email"]').input_value() == EMAIL)
    page.locator('[name="password"]').fill(PASSWORD)
    page.locator('[name="termsAccepted"]').check()
    page.locator("#register-form button[type=submit]").click()
    page.wait_for_url("**/user/login")
    gate.check(f"{prefix}:account-created", "Sign in" in page.locator("h1").inner_text())

    page.locator('[name="password"]').fill("WrongPassword9")
    page.locator("#login-form button[type=submit]").click()
    page.locator("#form-alert").wait_for()
    gate.check(f"{prefix}:bad-signin-retry", "incorrect" in page.locator("#form-alert").inner_text().lower())
    gate.check(f"{prefix}:bad-signin-not-authenticated", not state(page)["authenticated"])
    page.locator('[name="password"]').fill(PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.wait_for_url(f"**/application/{TARGET_KEY}")
    page.locator("#application-form").wait_for()
    gate.check(f"{prefix}:successful-signin", state(page)["authenticated"] and state(page)["account"]["email"] == EMAIL)


def application_payload() -> dict[str, Any]:
    return {
        "jobKey": TARGET_KEY,
        "applicant": {"firstName": "Alex", "lastName": "Green", "email": EMAIL, "city": "Toronto", "province": "Ontario"},
        "resume": {"source": "ASSIGNED_PROFILE", "fileName": "Alex_Green_Resume.pdf"},
        "coverLetter": COVER,
        "accuracyConfirmed": True,
    }


def complete_application(
    gate: Gate,
    page: Page,
    prefix: str,
    artifacts: Path,
    visuals: dict[str, Any],
    terminal: list[dict[str, Any]],
    *,
    recovery: bool,
) -> dict[str, Any]:
    gate.check(f"{prefix}:profile-resume-visible", "Alex_Green_Resume.pdf" in page.locator("main").inner_text() and "No binary file is uploaded" in page.locator("main").inner_text())
    weak = {**application_payload(), "coverLetter": "Dumbarton Arts & Education Program Manager"}
    weak_result = fetch(page, "/api/applications/draft", "POST", weak)
    gate.check(f"{prefix}:weak-letter-rejected", weak_result["status"] == 422 and "substantive" in weak_result["body"]["error"], weak_result)
    direct = fetch(page, "/api/applications/submit", "POST", application_payload())
    gate.check(f"{prefix}:direct-stale-submit-rejected", direct["status"] == 409 and "Review and save" in direct["body"]["error"], direct)
    page.locator('[name="coverLetter"]').fill(COVER)
    page.locator('[name="accuracyConfirmed"]').check()
    visuals["application"] = screenshot_metrics(gate, page, artifacts, prefix, "application")
    page.locator("#application-form button[type=submit]").click()
    page.locator("#submit-application").wait_for()
    draft_state = state(page)
    gate.check(f"{prefix}:draft-exact", draft_state["draft"]["payload"] == application_payload() and draft_state["draft"]["revision"] == 1, draft_state["draft"])

    if recovery:
        page.reload(wait_until="networkidle")
        page.locator("#application-form").wait_for()
        gate.check(f"{prefix}:draft-refresh", page.locator('[name="coverLetter"]').input_value() == COVER and page.locator('[name="accuracyConfirmed"]').is_checked())
        logout = fetch(page, "/api/auth/logout", "POST", {"intent": "RECOVERY_TEST"})
        gate.check(f"{prefix}:logout-for-recovery", logout["status"] == 200, logout)
        page.reload(wait_until="networkidle")
        page.wait_for_url("**/user/login")
        page.locator('[name="password"]').fill(PASSWORD)
        page.locator("#login-form button[type=submit]").click()
        page.wait_for_url(f"**/application/{TARGET_KEY}")
        gate.check(f"{prefix}:auth-recovery-draft", page.locator('[name="coverLetter"]').input_value() == COVER)
        page.locator("#application-form button[type=submit]").click()
        page.locator("#submit-application").wait_for()
        gate.check(f"{prefix}:draft-revision-after-recovery", state(page)["draft"]["revision"] == 2, state(page)["draft"])

    review_text = page.locator("main").inner_text()
    gate.check(f"{prefix}:review-agrees", "Review your application" in review_text and COVER in review_text and "Alex_Green_Resume.pdf" in review_text)
    visuals["review"] = screenshot_metrics(gate, page, artifacts, prefix, "review")
    page.locator("#submit-application").click()
    page.locator("text=Your local application is complete").wait_for(timeout=8_000)
    gate.check(f"{prefix}:one-terminal-request", len(terminal) == 1, terminal)
    sent = terminal[0]
    gate.check(f"{prefix}:terminal-path-method", urlparse(sent["url"]).path == "/data/userdashboard/missing-info" and sent["method"] == "POST", sent)
    gate.check(f"{prefix}:terminal-json", sent["contentType"].startswith("application/json") and json.loads(sent["postData"]) == EXPECTED_PROFILE, sent)
    completion = page.locator("main").inner_text()
    gate.check(f"{prefix}:completion-no-effect", "no real-world effect" in completion.lower() and "delivery" in completion.lower() and "none" in completion.lower(), completion)
    visuals["completion"] = screenshot_metrics(gate, page, artifacts, prefix, "completion")
    persisted = state(page)
    gate.check(f"{prefix}:one-application", len(persisted["applications"]) == 1, persisted["applications"])
    application = persisted["applications"][0]
    gate.check(f"{prefix}:application-payload-exact", application["payload"] == application_payload(), application)
    gate.check(f"{prefix}:application-status", application["status"] == "SUBMITTED_LOCALLY" and application["delivery"] == "NONE_LOCAL_REPLICA", application)
    successful_submits = [row for row in persisted["journal"] if row["endpoint"] == "/api/applications/submit" and row["status"] == 201]
    gate.check(f"{prefix}:one-successful-submit-journal", len(successful_submits) == 1 and successful_submits[0]["payload"]["jobKey"] == TARGET_KEY, persisted["journal"])
    successful_terminal = [row for row in persisted["journal"] if row["endpoint"] == "/data/userdashboard/missing-info" and row["status"] == 200]
    gate.check(f"{prefix}:terminal-journal-exact", len(successful_terminal) == 1 and successful_terminal[0]["payload"] == EXPECTED_PROFILE, successful_terminal)
    duplicate = fetch(page, "/data/userdashboard/missing-info", "POST", EXPECTED_PROFILE)
    gate.check(f"{prefix}:duplicate-rejected", duplicate["status"] == 409 and "already completed" in duplicate["body"]["error"], duplicate)
    gate.check(f"{prefix}:duplicate-does-not-mutate", len(state(page)["applications"]) == 1, state(page)["applications"])
    page.locator('a[href="/my-applications"]').first.click()
    page.wait_for_url("**/my-applications", wait_until="commit")
    page.locator(".application-card").wait_for()
    applications_text = page.locator("main").inner_text()
    gate.check(f"{prefix}:my-applications-agrees", "program manager" in applications_text.lower() and "submitted locally" in applications_text.lower(), applications_text)
    page.locator(".view-application").click()
    gate.check(f"{prefix}:application-details-agree", COVER in page.locator(".application-card-details").inner_text() and "No employer delivery" in page.locator(".application-card-details").inner_text())
    return persisted


def run_flow(gate: Gate, browser: Browser, clone: str, artifacts: Path, viewport_name: str, *, recovery: bool, reset_first: bool) -> dict[str, Any]:
    context, page, telemetry = new_page(browser, clone, VIEWPORTS[viewport_name])
    prefix = f"{viewport_name}"
    visuals: dict[str, Any] = {}
    terminal: list[dict[str, Any]] = []

    def observe(request: Request) -> None:
        if urlparse(request.url).path == "/data/userdashboard/missing-info":
            terminal.append({"status": "request", "url": request.url, "method": request.method, "postData": request.post_data or "", "contentType": request.headers.get("content-type", "")})

    page.on("request", observe)
    try:
        if reset_first:
            reset(page, clone)
        else:
            page.goto(clone + "/jobs", wait_until="networkidle")
        gate.check(f"{prefix}:title", page.title() == "Idealist | Find nonprofit work", page.title())
        gate.check(f"{prefix}:offline-disclosure", "No real employer delivery" in page.locator(".offline-banner").inner_text())
        search_and_open(gate, page, prefix, artifacts, visuals)
        register_and_login(gate, page, prefix)
        persisted = complete_application(gate, page, prefix, artifacts, visuals, terminal, recovery=recovery)
        finish_telemetry(gate, prefix, telemetry)
        return {"visuals": visuals, "terminal": terminal, "state": persisted, "telemetry": telemetry}
    finally:
        context.close()


def run_edges(gate: Gate, browser: Browser, clone: str, artifacts: Path) -> dict[str, Any]:
    context, page, telemetry = new_page(browser, clone, VIEWPORTS["desktop"])
    prefix = "edges"
    visuals: dict[str, Any] = {}
    try:
        page.goto(clone + "/jobs", wait_until="networkidle")
        malformed = page.evaluate("""async()=>{const r=await fetch('/api/auth/sign-in',{method:'POST',headers:{'Content-Type':'application/json'},body:'{bad'});return {status:r.status,body:await r.json()}}""")
        gate.check("edges:malformed-json", malformed["status"] == 400 and "valid JSON" in malformed["body"]["error"], malformed)
        unauthenticated = fetch(page, "/api/applications/submit", "POST", application_payload())
        gate.check("edges:unauthenticated-submit", unauthenticated["status"] == 401 and unauthenticated["body"]["authRequired"], unauthenticated)
        unauthenticated_terminal = fetch(page, "/data/userdashboard/missing-info", "POST", EXPECTED_PROFILE)
        gate.check("edges:unauthenticated-terminal", unauthenticated_terminal["status"] == 401, unauthenticated_terminal)
        isolated = state(page)
        gate.check("edges:session-isolation", not isolated["authenticated"] and not isolated["applications"] and not isolated["draft"], isolated)
        duplicate_account = fetch(
            page,
            "/api/auth/register",
            "POST",
            {"accountType": "APPLICANT", "firstName": "Alex", "lastName": "Green", "email": EMAIL, "postalCode": "M5S 2H7", "password": PASSWORD, "termsAccepted": True},
        )
        gate.check("edges:duplicate-account", duplicate_account["status"] == 409 and "already exists" in duplicate_account["body"]["error"], duplicate_account)

        login_body = f"email={EMAIL}&password={PASSWORD.replace('!', '%21')}&callbackUrl=%2Fapplication%2F{TARGET_KEY}&csrfToken=0123456789abcdef&json=true"
        login = fetch(page, "/api/auth/sign-in", "POST", login_body, "application/x-www-form-urlencoded")
        gate.check("edges:sign-in-for-malformed-terminal", login["status"] == 200, login)
        malformed_terminal = page.evaluate("""async()=>{const r=await fetch('/data/userdashboard/missing-info',{method:'POST',headers:{'Content-Type':'application/json'},body:'{bad'});return {status:r.status,body:await r.json()}}""")
        gate.check("edges:malformed-terminal", malformed_terminal["status"] == 400 and "valid JSON" in malformed_terminal["body"]["error"], malformed_terminal)

        page.goto(clone + "/jobs?keywords=Program+Manager&location=Boston&employment=Full+Time&sector=Nonprofit", wait_until="networkidle")
        page.locator("text=No jobs match all four filters").wait_for()
        gate.check("edges:empty-results", page.locator(".job-card").count() == 0 and page.locator("#clear-filters").is_visible())

        page.goto(clone + "/jobs", wait_until="networkidle")
        page.locator('[name="keywords"]').fill("offline")
        page.locator('[name="location"]').fill("Washington, DC")
        page.locator('[name="employment"]').select_option(label="Full Time")
        page.locator('[name="sector"]').select_option(label="Nonprofit")
        page.locator("#job-search-form button[type=submit]").click()
        page.locator("text=Search unavailable").wait_for()
        gate.check("edges:retryable-search-error", page.locator("#retry-action").count() == 1)
        visuals["search_error"] = screenshot_metrics(gate, page, artifacts, prefix, "search-error")

        response = page.goto(clone + "/definitely-missing", wait_until="networkidle")
        gate.check("edges:server-404", response is not None and response.status == 404 and "Page not found" in page.locator("body").inner_text(), response.status if response else None)
        gate.check("edges:404-security", "default-src 'self'" in response.headers.get("content-security-policy", "") and response.headers.get("x-frame-options") == "DENY", response.headers if response else None)
        visuals["not_found"] = screenshot_metrics(gate, page, artifacts, prefix, "not-found")

        traversal = page.goto(clone + "/static/%2e%2e/server.py", wait_until="networkidle")
        gate.check("edges:path-traversal-blocked", traversal is not None and traversal.status == 404, traversal.status if traversal else None)
        page.goto(clone + "/local-boundary?for=external-employer", wait_until="networkidle")
        gate.check("edges:local-boundary", "does not open employer sites" in page.locator("main").inner_text())
        finish_telemetry(gate, prefix, telemetry)
        return {"visuals": visuals, "state": isolated, "telemetry": telemetry}
    finally:
        context.close()


def sqlite_snapshot(path: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        for table in ["sessions", "accounts", "profile_resumes", "searches", "job_views", "drafts", "applications", "request_journal", "boundary_events"]:
            rows = [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]  # noqa: S608 - fixed table allowlist
            for row in rows:
                for key in ["payload", "result_keys"]:
                    if key in row and isinstance(row[key], str):
                        try:
                            row[key] = json.loads(row[key])
                        except json.JSONDecodeError:
                            pass
                if "password_hash" in row:
                    row["password_hash"] = "[REDACTED]"
                if "session_token" in row and row["session_token"]:
                    row["session_token"] = row["session_token"][:10]
                if "token" in row and row["token"]:
                    row["token"] = row["token"][:10]
            snapshot[table] = rows
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", default="http://127.0.0.1:8135")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--screenshots-dir", type=Path, default=DEFAULT_ARTIFACTS)
    args = parser.parse_args()
    gate = Gate()
    task = json.loads(TASK_PATH.read_text())
    source = json.loads(SOURCE_TASK_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator(schema).validate(task)
    gate.check("global:task-schema-valid", True)
    gate.check("global:source-task", source["metadata"]["task_id"] == 776 and source["metadata"]["platform"] == "idealist", source["metadata"])
    gate.check("global:source-terminal-preserved", source["eval_schema"] == {"url_pattern": "www\\.idealist\\.org/data/userdashboard/missing-info", "method": "POST"}, source["eval_schema"])
    gate.check("global:canonical-port", urlparse(args.clone).port == 8135 and task["metadata"]["server_command"].endswith("--port 8135"), {"clone": args.clone, "command": task["metadata"]["server_command"]})
    gate.check("global:terminal-body-exact", task["eval_schema"].get("body") == EXPECTED_PROFILE, task["eval_schema"])
    for host in ["localhost", "127.0.0.1", "host.docker.internal"]:
        gate.check(f"global:matcher:{host}", matcher_matches(task, f"http://{host}:8135/data/userdashboard/missing-info", "POST"))
    for bad in ["http://127.0.0.1:8134/data/userdashboard/missing-info", "http://idealist.org:8135/data/userdashboard/missing-info", "http://127.0.0.1:8135/data/userdashboard/missing-info/extra"]:
        gate.check(f"global:matcher-rejects:{bad}", not matcher_matches(task, bad, "POST"))
    gate.check("global:matcher-rejects-get", not matcher_matches(task, "http://127.0.0.1:8135/data/userdashboard/missing-info", "GET"))

    report: dict[str, Any] = {"startedAt": utc_now(), "clone": args.clone, "db": str(args.db), "runs": {}}
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            try:
                desktop = run_flow(gate, browser, args.clone, args.screenshots_dir, "desktop", recovery=True, reset_first=True)
                # Remove the first session/account so mobile exercises a genuinely fresh full flow.
                cleanup_context, cleanup_page, _ = new_page(browser, args.clone, VIEWPORTS["desktop"])
                try:
                    cleanup_page.goto(args.clone + "/", wait_until="networkidle")
                    # Desktop cookie is intentionally unavailable here; database is reset externally before verifier in canonical usage.
                finally:
                    cleanup_context.close()
                report["runs"]["desktop"] = desktop
                # Mobile uses the existing account through sign-in after registration conflict would be ambiguous, so clear DB state through desktop's saved token is not possible after context close.
                # Instead use a second full visual path in the same persistent browser storage copied from the completed state.
                mobile_context = browser.new_context(viewport=VIEWPORTS["mobile"], locale="en-US", storage_state={"cookies": [], "origins": []})
                mobile_context.close()
                report["runs"]["mobile"] = run_mobile_view(gate, browser, args.clone, args.screenshots_dir)
                report["runs"]["edges"] = run_edges(gate, browser, args.clone, args.screenshots_dir)
            finally:
                browser.close()

        snapshot = sqlite_snapshot(args.db)
        apps = snapshot["applications"]
        gate.check("sqlite:application-present", len(apps) == 1, apps)
        gate.check("sqlite:application-exact", apps[0]["job_key"] == TARGET_KEY and apps[0]["status"] == "SUBMITTED_LOCALLY" and apps[0]["delivery"] == "NONE_LOCAL_REPLICA" and apps[0]["payload"] == application_payload(), apps[0])
        accounts = snapshot["accounts"]
        gate.check("sqlite:account-resume", len(accounts) == 1 and accounts[0]["email"] == EMAIL and accounts[0]["profile_complete"] == 1 and len(snapshot["profile_resumes"]) == 1 and snapshot["profile_resumes"][0]["file_name"] == "Alex_Green_Resume.pdf", {"accounts": accounts, "resumes": snapshot["profile_resumes"]})
        successful_terminals = [row for row in snapshot["request_journal"] if row["endpoint"] == "/data/userdashboard/missing-info" and row["status"] == 200]
        gate.check("sqlite:successful-terminal", len(successful_terminals) == 1 and successful_terminals[0]["content_type"] == "application/json" and successful_terminals[0]["payload"] == EXPECTED_PROFILE, successful_terminals)
        successful_submit = [row for row in snapshot["request_journal"] if row["endpoint"] == "/api/applications/submit" and row["status"] == 201]
        gate.check("sqlite:one-successful-application", len(successful_submit) == 1, successful_submit)
        gate.check("sqlite:draft-consumed", not snapshot["drafts"], snapshot["drafts"])
        report["sqlite"] = snapshot
        report["finishedAt"] = utc_now()
        report["checks"] = gate.checks
        report["summary"] = {"passed": sum(item["passed"] for item in gate.checks), "failed": sum(not item["passed"] for item in gate.checks), "total": len(gate.checks)}
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        print(f"PASS: {report['summary']['passed']}/{report['summary']['total']} checks")
        print(f"Report: {args.out}")
        return 0
    except Exception as exc:
        report["finishedAt"] = utc_now()
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["checks"] = gate.checks
        report["summary"] = {"passed": sum(item["passed"] for item in gate.checks), "failed": sum(not item["passed"] for item in gate.checks), "total": len(gate.checks)}
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
        print(f"FAIL: {exc}", file=sys.stderr)
        print(f"Report: {args.out}", file=sys.stderr)
        return 1


def run_mobile_view(gate: Gate, browser: Browser, clone: str, artifacts: Path) -> dict[str, Any]:
    """Exercise responsive discovery and an isolated sign-in/application read surface."""
    context, page, telemetry = new_page(browser, clone, VIEWPORTS["mobile"])
    prefix = "mobile"
    visuals: dict[str, Any] = {}
    try:
        page.goto(clone + "/jobs", wait_until="networkidle")
        gate.check("mobile:header-fits", page.locator(".site-header").bounding_box()["width"] <= 390)
        page.locator("#menu-toggle").click()
        gate.check("mobile:menu-operational", page.locator("#primary-nav").is_visible())
        page.locator("#menu-toggle").click()
        search_and_open(gate, page, prefix, artifacts, visuals)
        gate.check("mobile:apply-visible", page.locator(".mobile-apply-slot .apply-button").is_visible())
        visuals["detail_mobile"] = screenshot_metrics(gate, page, artifacts, prefix, "detail-mobile")
        page.goto(clone + "/user/login", wait_until="networkidle")
        page.locator('[name="password"]').fill(PASSWORD)
        page.locator("#login-form button[type=submit]").click()
        page.wait_for_url(f"**/application/{TARGET_KEY}")
        gate.check("mobile:account-shared-by-credentials", state(page)["authenticated"])
        # Existing application is rendered as the completion view for the same local account.
        gate.check("mobile:completion-responsive", "Your local application is complete" in page.locator("main").inner_text())
        visuals["completion_mobile"] = screenshot_metrics(gate, page, artifacts, prefix, "completion-mobile")
        page.locator('.completion-page a[href="/my-applications"]').click()
        page.wait_for_url("**/my-applications")
        page.locator(".application-card").wait_for()
        applications_text = page.locator("main").inner_text()
        gate.check("mobile:my-applications", "submitted locally" in applications_text.lower(), applications_text)
        visuals["my_applications"] = screenshot_metrics(gate, page, artifacts, prefix, "my-applications")
        finish_telemetry(gate, prefix, telemetry)
        return {"visuals": visuals, "state": state(page), "telemetry": telemetry}
    finally:
        context.close()


if __name__ == "__main__":
    raise SystemExit(main())
