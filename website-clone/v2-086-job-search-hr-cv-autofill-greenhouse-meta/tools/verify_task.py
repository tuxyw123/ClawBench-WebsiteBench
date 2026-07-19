#!/usr/bin/env python3
"""Playwright and persistence verifier for the local Greenhouse/CodePath task."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from playwright.async_api import BrowserContext, Page, async_playwright


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
TASK_PATH = REPO / "tasks/dev/dev-117-greenhouse-codepath-application/task.json"
SOURCE_PATH = REPO / "test-cases/v2/v2-086-job-search-hr-cv-autofill-greenhouse-meta/task.json"
SOURCE_ATTACHMENT = SOURCE_PATH.parent / "extra_info/job_links.json"
DEV_ATTACHMENT = TASK_PATH.parent / "extra_info/job_links.json"
SCHEMA_PATH = REPO / "claw-bench-v2/schemas/task.schema.json"
DEFAULT_DB = ROOT / "greenhouse.sqlite3"
DEFAULT_REPORT = ROOT / "verification-report.json"
ARTIFACTS = ROOT / ".verification-artifacts"
JOB_ID = "4526154007"
JOB_PATH = f"/codepath/jobs/{JOB_ID}"
TERMINAL_PATH = f"/v1/boards/codepath/jobs/{JOB_ID}"

EXPECTED = {
    "first_name": "Alex",
    "last_name": "Green",
    "preferred_name": "Alex",
    "email": "alex.green.uoft@clawbench.cc",
    "country": "Canada",
    "location": "Toronto, Ontario",
    "resume": {
        "file_name": "Alex_Green_Resume.pdf",
        "source": "assigned_profile",
        "parse_status": "complete",
    },
    "current_company": "Pinecrest Technologies Inc.",
    "current_title": "Senior Software Engineer",
    "highest_degree": "Doctor of Philosophy in Computer Science",
    "years_experience": "23+",
    "authorized_to_work": True,
    "requires_sponsorship": False,
    "future_opportunities": True,
    "consent": True,
}


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, passed: bool, detail: Any = None) -> None:
        item: dict[str, Any] = {"name": name, "passed": bool(passed)}
        if detail is not None and (not passed or isinstance(detail, (str, int, float))):
            item["detail"] = detail
        self.items.append(item)
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")


def http_json(url: str) -> tuple[int, dict[str, Any], dict[str, str]]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response), dict(response.headers)
    except HTTPError as error:
        try:
            payload = json.load(error)
        except Exception:
            payload = {}
        return error.code, payload, dict(error.headers)


def http_status(url: str) -> int:
    try:
        with urlopen(url, timeout=10) as response:
            return response.status
    except HTTPError as error:
        return error.code


async def fetch_json(page: Page, path: str, method: str = "GET", body: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return await page.evaluate(
        """async ({path, method, body, headers}) => {
          const response = await fetch(path, {
            method,
            headers: body === null ? (headers || {}) : {'Content-Type':'application/json', ...(headers || {})},
            body: body === null ? undefined : (typeof body === 'string' ? body : JSON.stringify(body)),
          });
          let data = {}; try { data = await response.json(); } catch (_) {}
          return {status: response.status, body: data};
        }""",
        {"path": path, "method": method, "body": body, "headers": headers or {}},
    )


def attach_observers(page: Page, clone: str, observations: dict[str, list[Any]]) -> None:
    expected_origin = urlparse(clone)

    def on_request(request: Any) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"http", "https"} and (parsed.scheme, parsed.netloc) != (
            expected_origin.scheme,
            expected_origin.netloc,
        ):
            observations["external_requests"].append(request.url)
        if parsed.path == TERMINAL_PATH and request.method == "POST":
            try:
                payload = request.post_data_json
            except Exception:
                payload = request.post_data
            observations["terminal_requests"].append(
                {
                    "url": request.url,
                    "method": request.method,
                    "headers": request.headers,
                    "body": payload,
                }
            )

    def on_response(response: Any) -> None:
        if urlparse(response.url).path == TERMINAL_PATH and response.request.method == "POST":
            observations["terminal_responses"].append(response.status)

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("pageerror", lambda error: observations["page_errors"].append(str(error)))
    page.on(
        "console",
        lambda message: observations["console_errors"].append(message.text)
        if message.type == "error"
        else None,
    )


async def check_overflow(checks: Checks, page: Page, name: str) -> None:
    result = await page.evaluate(
        """() => ({
          width: document.documentElement.scrollWidth,
          viewport: window.innerWidth,
          bodyWidth: document.body.scrollWidth,
          clipped: [...document.querySelectorAll('a,button,input,select')]
            .filter(el => {
              const s = getComputedStyle(el), r = el.getBoundingClientRect();
              return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0 &&
                (r.left < -1 || r.right > innerWidth + 1);
            }).map(el => (el.textContent || el.getAttribute('aria-label') || el.id || el.name).trim()).slice(0,10)
        })"""
    )
    checks.check(
        f"{name}: no horizontal document overflow",
        result["width"] <= result["viewport"] + 1 and result["bodyWidth"] <= result["viewport"] + 1,
        result,
    )
    checks.check(f"{name}: visible controls stay inside viewport", not result["clipped"], result)


async def check_assets(checks: Checks, page: Page, name: str) -> None:
    broken = await page.evaluate(
        """() => [...document.images].filter(img => !img.complete || img.naturalWidth === 0).map(img => img.src)"""
    )
    checks.check(f"{name}: same-origin images load", not broken, broken)


async def reset(page: Page) -> None:
    result = await fetch_json(
        page,
        "/api/testing/reset",
        "POST",
        {},
        {"X-Replica-Test": "1"},
    )
    if result["status"] != 200:
        raise RuntimeError(f"reset failed: {result}")


async def run_desktop(
    checks: Checks,
    context: BrowserContext,
    clone: str,
    observations: dict[str, list[Any]],
) -> tuple[dict[str, Any], str]:
    page = await context.new_page()
    attach_observers(page, clone, observations)
    await page.goto(f"{clone}/company", wait_until="networkidle")
    company_text = await page.locator("body").inner_text()
    checks.check(
        "desktop company page exposes mission and route to openings",
        "most diverse generation of software engineers" in company_text
        and await page.locator("a:has-text('Explore open roles')").is_visible(),
    )
    await check_assets(checks, page, "desktop company")
    await check_overflow(checks, page, "desktop company")
    await page.screenshot(path=ARTIFACTS / "greenhouse-desktop-company.png", full_page=True)

    await page.goto(f"{clone}{JOB_PATH}", wait_until="networkidle")
    await reset(page)
    await page.reload(wait_until="networkidle")
    checks.check("desktop listing has exact role and company", "Senior Software Engineer" in await page.locator("body").inner_text() and "CodePath" in await page.locator("body").inner_text())
    checks.check("desktop listing exposes exact job id", JOB_ID in await page.locator(".job-meta-panel").inner_text())
    checks.check("desktop listing shows remote regions and role details", "Canada or LATAM" in await page.locator("body").inner_text() and "What you'll do" in await page.locator("body").inner_text())
    checks.check("desktop listing has application action", await page.locator("#hero-apply").is_visible())
    await check_assets(checks, page, "desktop listing")
    await check_overflow(checks, page, "desktop listing")
    await page.screenshot(path=ARTIFACTS / "greenhouse-desktop-listing.png", full_page=True)

    await page.click("#hero-apply")
    await page.wait_for_selector("#application-form")
    checks.check("assigned resume is visible and parsed", "Alex_Green_Resume.pdf" in await page.locator(".resume-section").inner_text() and "Parsed" in await page.locator(".resume-section").inner_text())
    checks.check("resume identity is autofilled", await page.input_value("#first_name") == "Alex" and await page.input_value("#last_name") == "Green" and await page.input_value("#email") == EXPECTED["email"])
    checks.check("resume career fields are autofilled", await page.input_value("#current_company") == EXPECTED["current_company"] and await page.input_value("#highest_degree") == EXPECTED["highest_degree"])
    checks.check("required authorization answers are visible", await page.locator("input[name=authorized_to_work][value=true]").is_checked() and await page.locator("input[name=requires_sponsorship][value=false]").is_checked())
    preview = await context.new_page()
    attach_observers(preview, clone, observations)
    await preview.goto(f"{clone}/documents/alex-green-resume", wait_until="networkidle")
    preview_text = await preview.locator("body").inner_text()
    checks.check("same-origin resume preview exposes extracted fields", all(value in preview_text for value in ("Alex Green", "Pinecrest Technologies", "University of Toronto", "Python")))
    await check_assets(checks, preview, "resume preview")
    await preview.close()

    await page.fill("#preferred_name", "Alexandra")
    await page.fill("#preferred_name", "Alex")
    await page.wait_for_timeout(1000)
    checks.check(
        "autosave reports completed draft",
        await page.locator("#save-status").inner_text() == "Saved just now",
        await page.locator("#save-status").inner_text(),
    )
    await page.reload(wait_until="networkidle")
    checks.check("draft refresh restores visible application", await page.locator("#draft-restored").is_visible() and await page.input_value("#preferred_name") == "Alex")

    await page.fill("#first_name", "")
    await page.click("button:has-text('Save and review')")
    await page.wait_for_selector(".notice.error")
    checks.check("required field validation is visible", bool(await page.locator('[data-error="first_name"]').inner_text()))
    await page.fill("#first_name", "Alex")
    await page.click("button:has-text('Save and review')")
    await page.wait_for_url(f"**{JOB_PATH}/review")
    review_text = await page.locator("body").inner_text()
    checks.check("review shows identity, resume, and employer answers", all(value in review_text for value in (EXPECTED["email"], "Alex_Green_Resume.pdf", "Pinecrest Technologies", "Authorized to work in Canada", "Requires sponsorship")))
    await check_overflow(checks, page, "desktop review")
    await page.screenshot(path=ARTIFACTS / "greenhouse-desktop-review.png", full_page=True)

    await page.click("#submit-application")
    checks.check("review requires explicit consent", bool(await page.locator("#consent-error").inner_text()))
    await page.check("#consent")
    fail_setup = await fetch_json(page, "/api/testing/fail-next-terminal", "POST", {}, {"X-Replica-Test": "1"})
    checks.check("temporary terminal failure can be armed locally", fail_setup["status"] == 200, fail_setup)
    await page.click("#submit-application")
    await page.wait_for_selector("#retry-submit")
    checks.check("temporary submission failure exposes retry", "temporarily unavailable" in (await page.locator("#submit-alert").inner_text()).lower())
    await page.click("#retry-submit")
    await page.wait_for_url(f"**{JOB_PATH}/confirmation")
    confirmation_text = await page.locator("body").inner_text()
    confirmation_folded = confirmation_text.casefold()
    checks.check("confirmation is local and visibly submitted", "application received locally" in confirmation_folded and "submitted" in confirmation_folded and "no external delivery" in confirmation_folded)
    await check_overflow(checks, page, "desktop confirmation")
    await page.screenshot(path=ARTIFACTS / "greenhouse-desktop-confirmation.png", full_page=True)

    await page.reload(wait_until="networkidle")
    checks.check("confirmation persists after refresh", "application received locally" in (await page.locator("body").inner_text()).casefold())
    await page.click("a:has-text('View application status')")
    await page.wait_for_selector(".status-card")
    checks.check("status page agrees with submitted job", "Senior Software Engineer" in await page.locator(".status-card").inner_text() and "Alex_Green_Resume.pdf" in await page.locator(".status-card").inner_text())

    state = (await fetch_json(page, "/api/state"))["body"]
    token = state["sessionToken"]
    return state, token


async def prepare_mobile_review(page: Page, clone: str) -> None:
    await page.goto(f"{clone}{JOB_PATH}", wait_until="networkidle")
    await page.click("#hero-apply")
    await page.wait_for_selector("#application-form")
    await page.click("button:has-text('Save and review')")
    await page.wait_for_url(f"**{JOB_PATH}/review")


async def run_mobile(
    checks: Checks,
    context: BrowserContext,
    clone: str,
    observations: dict[str, list[Any]],
) -> None:
    page = await context.new_page()
    attach_observers(page, clone, observations)
    await page.goto(f"{clone}/codepath", wait_until="networkidle")
    checks.check("mobile board exposes exact listing", await page.locator(f'a[href="{JOB_PATH}"]').is_visible())
    await check_assets(checks, page, "mobile board")
    await check_overflow(checks, page, "mobile board")
    await page.screenshot(path=ARTIFACTS / "greenhouse-mobile-board.png", full_page=True)
    await page.click(f'a[href="{JOB_PATH}"]')
    await page.wait_for_selector(".apply-panel")
    checks.check("mobile listing keeps apply action visible", await page.locator(".apply-panel .button").is_visible())
    await check_overflow(checks, page, "mobile listing")
    await page.click(".apply-panel .button")
    await page.wait_for_selector("#application-form")
    checks.check("mobile application keeps resume and controls visible", await page.locator(".resume-file").is_visible() and await page.locator("button:has-text('Save and review')").is_visible())
    await check_overflow(checks, page, "mobile application")
    await page.screenshot(path=ARTIFACTS / "greenhouse-mobile-application.png", full_page=True)
    await page.click("button:has-text('Save and review')")
    await page.wait_for_url(f"**{JOB_PATH}/review")
    checks.check("mobile review keeps consent and submit visible", await page.locator("#consent").is_visible() and await page.locator("#submit-application").is_visible())
    await check_overflow(checks, page, "mobile review")
    await page.screenshot(path=ARTIFACTS / "greenhouse-mobile-review.png", full_page=True)


async def run_recovery_and_api_checks(
    checks: Checks,
    browser: Any,
    clone: str,
    observations: dict[str, list[Any]],
) -> None:
    context = await browser.new_context(viewport={"width": 390, "height": 844})
    page = await context.new_page()
    attach_observers(page, clone, observations)
    await page.goto(f"{clone}{JOB_PATH}", wait_until="networkidle")
    isolated = (await fetch_json(page, "/api/state"))["body"]
    checks.check("another browser session cannot read application", isolated.get("application") is None)
    checks.check("isolated state has no external effects", isolated.get("externalEffects") == [])

    direct = await fetch_json(page, TERMINAL_PATH, "POST", EXPECTED)
    checks.check("exact direct submit without reviewed draft is rejected", direct["status"] == 409, direct)
    wrong = dict(EXPECTED)
    wrong["first_name"] = "Not Alex"
    mismatch = await fetch_json(page, TERMINAL_PATH, "POST", wrong)
    checks.check("wrong identity terminal payload is rejected", mismatch["status"] == 422, mismatch)
    malformed = await fetch_json(page, TERMINAL_PATH, "POST", "{bad-json")
    checks.check("malformed terminal JSON is rejected", malformed["status"] == 400, malformed)
    unsupported = await page.evaluate(
        """async () => { const r = await fetch('/v1/boards/codepath/jobs/4526154007', {method:'POST',headers:{'Content-Type':'text/plain'},body:'x'}); return r.status; }"""
    )
    checks.check("unsupported terminal content type is rejected", unsupported == 415, unsupported)
    extra = dict(EXPECTED)
    extra["unexpected"] = True
    extra_result = await fetch_json(page, TERMINAL_PATH, "POST", extra)
    checks.check("unsupported terminal field is rejected", extra_result["status"] == 422, extra_result)

    missing_job = await fetch_json(page, "/api/boards/codepath/jobs/999999")
    checks.check("missing job API returns 404 recovery", missing_job["status"] == 404, missing_job)
    missing_api = await fetch_json(page, "/api/not-real")
    checks.check("missing API returns 404", missing_api["status"] == 404, missing_api)
    response = await page.goto(f"{clone}/not-a-real-greenhouse-page", wait_until="networkidle")
    checks.check("unknown page returns real 404", response is not None and response.status == 404, response.status if response else None)
    checks.check("404 page offers job-board recovery", "Return to current openings" in await page.locator("body").inner_text())
    await check_overflow(checks, page, "mobile 404")

    await page.goto(f"{clone}/local-boundary", wait_until="networkidle")
    await page.wait_for_selector(".boundary-main")
    checks.check("employer boundary remains same-origin and local", urlparse(page.url).netloc == urlparse(clone).netloc and "no external effect" in (await page.locator("body").inner_text()).lower())
    await check_overflow(checks, page, "mobile local boundary")
    await context.close()


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    checks = Checks()
    clone = args.clone.rstrip("/")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    task = json.loads(TASK_PATH.read_text())
    source = json.loads(SOURCE_PATH.read_text())
    source_attachment = json.loads(SOURCE_ATTACHMENT.read_text())
    dev_attachment = json.loads(DEV_ATTACHMENT.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())

    schema_errors = sorted(Draft202012Validator(schema).iter_errors(task), key=lambda error: list(error.path))
    checks.check("dev-117 validates against ClawBench-Pro task schema", not schema_errors, [error.message for error in schema_errors])
    checks.check("source task linkage is exact", task["metadata"]["source_task_id"] == 86 and task["metadata"]["source_task_path"].endswith("v2-086-job-search-hr-cv-autofill-greenhouse-meta"))
    checks.check("source attachment identifies exact target", source_attachment == {"job_url": "https://job-boards.greenhouse.io/codepath/jobs/4526154007", "job_title": "Senior Software Engineer", "company": "CodePath"})
    checks.check("dev attachment preserves job semantics on local origin", dev_attachment == {"job_url": "http://host.docker.internal:8134/codepath/jobs/4526154007", "job_title": source_attachment["job_title"], "company": source_attachment["company"]})
    checks.check("dev task references copied job attachment", task.get("extra_info") == source.get("extra_info"))

    matcher = task["eval_schema"]["url_pattern"]
    local_urls = [f"http://{host}:8134{TERMINAL_PATH}" for host in ("localhost", "127.0.0.1", "host.docker.internal")]
    checks.check("terminal matcher accepts all three local hosts", all(re.fullmatch(matcher, url) for url in local_urls), local_urls)
    checks.check("terminal matcher rejects external host", re.fullmatch(matcher, f"http://example.com:8134{TERMINAL_PATH}") is None)
    checks.check("terminal matcher pins port and exact path", re.fullmatch(matcher, f"http://127.0.0.1:8135{TERMINAL_PATH}") is None and re.fullmatch(matcher, f"http://127.0.0.1:8134/codepath/jobs/{JOB_ID}") is None)
    checks.check("terminal matcher body is exact expected payload", task["eval_schema"]["body"] == EXPECTED)

    health_status, health, headers = http_json(f"{clone}/health")
    checks.check("health endpoint reports canonical port and job", health_status == 200 and health.get("port") == 8134 and health.get("jobId") == JOB_ID, health)
    csp = next((value for key, value in headers.items() if key.casefold() == "content-security-policy"), "")
    checks.check("CSP restricts assets and connections to self", "default-src 'self'" in csp and "connect-src 'self'" in csp, csp)
    checks.check("static traversal is blocked", http_status(f"{clone}/static/%2e%2e/server.py") == 403)

    observations: dict[str, list[Any]] = {
        "external_requests": [],
        "terminal_requests": [],
        "terminal_responses": [],
        "page_errors": [],
        "console_errors": [],
    }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        desktop = await browser.new_context(viewport={"width": 1365, "height": 900})
        state, token = await run_desktop(checks, desktop, clone, observations)

        successful_requests = [request for request, status in zip(observations["terminal_requests"], observations["terminal_responses"], strict=False) if status == 201]
        checks.check("exactly one successful source-style terminal request", len(successful_requests) == 1, successful_requests)
        terminal = successful_requests[0] if successful_requests else None
        checks.check("successful terminal URL and method are exact", bool(terminal) and terminal["url"] == f"{clone}{TERMINAL_PATH}" and terminal["method"] == "POST", terminal)
        checks.check("successful terminal content type is JSON", bool(terminal) and "application/json" in terminal["headers"].get("content-type", ""), terminal)
        checks.check("successful terminal body is exact", bool(terminal) and terminal["body"] == EXPECTED, terminal["body"] if terminal else None)
        checks.check("retry path records one 503 then one 201", observations["terminal_responses"][:2] == [503, 201], observations["terminal_responses"])

        application = state.get("application") or {}
        checks.check("state stores exact submitted application", application.get("application") == EXPECTED and application.get("status") == "SUBMITTED_LOCAL", application)
        checks.check("state keeps all effects local", state.get("externalEffects") == [])
        checks.check("state records exact listing view", any(view.get("job_id") == JOB_ID and view.get("source_path") == JOB_PATH for view in state.get("listingViews", [])), state.get("listingViews"))
        success_journal = [row for row in state.get("journal", []) if row.get("endpoint") == TERMINAL_PATH and row.get("status_code") == 201]
        checks.check("journal has one exact successful terminal row", len(success_journal) == 1 and success_journal[0]["payload"] == EXPECTED, success_journal)
        checks.check("temporary failure is journaled before success", any(row.get("endpoint") == TERMINAL_PATH and row.get("status_code") == 503 for row in state.get("journal", [])))

        with sqlite3.connect(args.db) as db:
            row = db.execute("SELECT job_id, payload_json, status, confirmation_code FROM applications WHERE session_token=?", (token,)).fetchone()
            successful_count = db.execute("SELECT COUNT(*) FROM request_journal WHERE session_token=? AND endpoint=? AND status_code=201 AND terminal=1", (token, TERMINAL_PATH)).fetchone()[0]
            journal_count = db.execute("SELECT COUNT(*) FROM request_journal WHERE session_token=?", (token,)).fetchone()[0]
            draft_row = db.execute("SELECT job_id, step, payload_json FROM drafts WHERE session_token=?", (token,)).fetchone()
        checks.check("SQLite application agrees with exact payload", row is not None and row[0] == JOB_ID and json.loads(row[1]) == EXPECTED and row[2] == "SUBMITTED_LOCAL" and row[3].startswith("CP-"), row)
        checks.check("SQLite has one successful terminal journal row", successful_count == 1, successful_count)
        checks.check("SQLite journal includes workflow and recovery attempts", journal_count >= 6, journal_count)
        checks.check("SQLite reviewed draft agrees with terminal payload", draft_row is not None and draft_row[0] == JOB_ID and draft_row[1] == 3 and json.loads(draft_row[2]) == EXPECTED, draft_row)

        page = desktop.pages[0]
        duplicate = await fetch_json(page, TERMINAL_PATH, "POST", EXPECTED)
        checks.check("duplicate submission is rejected", duplicate["status"] == 409, duplicate)
        with sqlite3.connect(args.db) as db:
            application_count = db.execute("SELECT COUNT(*) FROM applications WHERE session_token=?", (token,)).fetchone()[0]
            success_after = db.execute("SELECT COUNT(*) FROM request_journal WHERE session_token=? AND endpoint=? AND status_code=201", (token, TERMINAL_PATH)).fetchone()[0]
        checks.check("duplicate leaves one application and one success", application_count == 1 and success_after == 1, {"applications": application_count, "successful_terminal": success_after})

        mobile = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        await run_mobile(checks, mobile, clone, observations)
        await run_recovery_and_api_checks(checks, browser, clone, observations)
        await mobile.close()
        await desktop.close()
        await browser.close()

    expected_console = re.compile(r"Failed to load resource: the server responded with a status of (4\d\d|503)")
    unexpected_console = [message for message in observations["console_errors"] if not expected_console.search(message)]
    checks.check("browser emitted no external runtime traffic", not observations["external_requests"], observations["external_requests"])
    checks.check("browser emitted no page errors", not observations["page_errors"], observations["page_errors"])
    checks.check("browser emitted no unexpected console errors", not unexpected_console, unexpected_console)

    passed = sum(item["passed"] for item in checks.items)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_task": 86,
        "dev_task": "dev-117",
        "clone": clone,
        "port": 8134,
        "result": "PASS" if passed == len(checks.items) else "FAIL",
        "passed": passed,
        "total": len(checks.items),
        "checks": checks.items,
        "terminal_requests": observations["terminal_requests"],
        "terminal_responses": observations["terminal_responses"],
        "external_requests": observations["external_requests"],
        "page_errors": observations["page_errors"],
        "console_errors": observations["console_errors"],
        "unexpected_console_errors": unexpected_console,
        "screenshots": sorted(str(path.relative_to(ROOT)) for path in ARTIFACTS.glob("*.png")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", default="http://127.0.0.1:8134")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        report = asyncio.run(verify(args))
    except Exception as exc:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_task": 86,
            "dev_task": "dev-117",
            "clone": args.clone,
            "port": 8134,
            "result": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(report["error"], file=sys.stderr)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {args.report}")
    if report.get("result") != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
