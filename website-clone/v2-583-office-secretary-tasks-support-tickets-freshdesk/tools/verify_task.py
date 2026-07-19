#!/usr/bin/env python3
"""Verify the offline Freshdesk V2 task 583 replica."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from jsonschema import Draft202012Validator
from playwright.sync_api import Browser, BrowserContext, Page, Request, sync_playwright


CLONE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_PATH = REPO_ROOT / "tasks/dev/dev-115-freshdesk-invoice-dispute-ticket/task.json"
SOURCE_TASK_PATH = (
    REPO_ROOT
    / "test-cases/v2/v2-583-office-secretary-tasks-support-tickets-freshdesk/task.json"
)
SCHEMA_PATH = REPO_ROOT / "claw-bench-v2/schemas/task.schema.json"
REPORT_PATH = CLONE_ROOT / "verification-report.json"
ARTIFACT_ROOT = CLONE_ROOT / ".verification-artifacts"
PASSWORD = "Freshdesk!42"
EXPECTED_BODY = {
    "requester_id": 1001,
    "subject": "Invoice Dispute - Order #10042",
    "description": (
        "The invoice for Order #10042 includes a duplicate $125.00 service charge. "
        "Please review the billing discrepancy and issue a corrected invoice."
    ),
    "status": 2,
    "priority": 3,
    "source": 3,
    "group_id": 3001,
    "responder_id": 2002,
    "type": "Billing",
}


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: Any = None) -> None:
        item = {"name": name, "passed": bool(passed)}
        if detail is not None:
            item["detail"] = detail
        self.items.append(item)

    def require(self, name: str, condition: bool, detail: Any = None) -> None:
        self.add(name, condition, detail)
        if not condition:
            raise AssertionError(f"{name}: {detail}")

    @property
    def passed(self) -> int:
        return sum(1 for item in self.items if item["passed"])

    @property
    def failed(self) -> int:
        return len(self.items) - self.passed


def reset_server(clone: str) -> None:
    request = UrlRequest(
        f"{clone}/api/reset",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Replica-Reset": "freshdesk-local-reset",
        },
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed local URL
        if response.status != 200:
            raise RuntimeError(f"Reset failed: {response.status}")


def matcher_matches(task: dict[str, Any], request: Request) -> bool:
    matcher = task["eval_schema"]
    return bool(
        request.method == matcher["method"]
        and re.fullmatch(matcher["url_pattern"], request.url)
        and request.post_data_json == matcher["body"]
    )


def attach_telemetry(
    context: BrowserContext,
    clone: str,
    page_errors: list[str],
    console_errors: list[str],
    external_requests: list[str],
) -> None:
    clone_host = urlparse(clone).hostname

    def on_request(request: Request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"http", "https"} and parsed.hostname != clone_host:
            external_requests.append(request.url)

    context.on("request", on_request)
    context.on("page", lambda page: page.on("pageerror", lambda error: page_errors.append(str(error))))
    context.on(
        "page",
        lambda page: page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            and "Failed to load resource: the server responded with a status of" not in message.text
            else None,
        ),
    )


def inspect_layout(page: Page, checks: Checks, label: str) -> dict[str, Any]:
    result = page.evaluate(
        """() => {
          const root = document.documentElement;
          const body = document.body;
          const overflow = Math.max(root.scrollWidth, body.scrollWidth) - innerWidth;
          const clipped = [];
          const textOverflow = [];
          for (const el of document.querySelectorAll('button,input,select,textarea,a.btn')) {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            if (style.display === 'none' || style.visibility === 'hidden' || rect.width < 1 || rect.height < 1) continue;
            const intersects = rect.bottom > 0 && rect.top < innerHeight;
            if (intersects && (rect.left < -1 || rect.right > innerWidth + 1)) {
              clipped.push({tag: el.tagName, id: el.id, text: (el.textContent || el.value || '').trim().slice(0, 50), left: rect.left, right: rect.right});
            }
            if (el.tagName === 'BUTTON' && el.scrollWidth > el.clientWidth + 2) {
              textOverflow.push({id: el.id, text: el.textContent.trim().slice(0, 50)});
            }
          }
          const brokenImages = [...document.images].filter(img => img.complete && img.naturalWidth === 0).map(img => img.src);
          return {overflow, clipped, textOverflow, brokenImages, width: innerWidth, height: innerHeight};
        }"""
    )
    checks.add(f"{label}: no horizontal document overflow", result["overflow"] <= 1, result)
    checks.add(f"{label}: visible controls inside viewport", not result["clipped"], result["clipped"])
    checks.add(f"{label}: button labels fit", not result["textOverflow"], result["textOverflow"])
    checks.add(f"{label}: same-origin images loaded", not result["brokenImages"], result["brokenImages"])
    return result


def screenshot(page: Page, checks: Checks, name: str) -> str:
    path = ARTIFACT_ROOT / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    checks.add(f"{name}: screenshot nonblank", path.stat().st_size > 8_000, path.stat().st_size)
    return str(path.relative_to(CLONE_ROOT))


def db_evidence(db_path: Path, expected_body: dict[str, Any]) -> dict[str, Any]:
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        account = dict(db.execute("SELECT * FROM accounts ORDER BY rowid DESC LIMIT 1").fetchone())
        account.pop("password_hash", None)
        workspace = dict(db.execute("SELECT * FROM workspaces ORDER BY id DESC LIMIT 1").fetchone())
        ticket = dict(db.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 1").fetchone())
        event = dict(
            db.execute(
                "SELECT * FROM ticket_events WHERE event_type = 'created' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        )
        terminal_rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM request_journal WHERE terminal = 1 ORDER BY id"
            )
        ]
    comparable = {key: ticket[key] for key in expected_body}
    return {
        "account": account,
        "workspace": workspace,
        "ticket": ticket,
        "ticket_matches_body": comparable == expected_body,
        "created_event": {**event, "detail": json.loads(event["detail_json"])},
        "terminal_statuses": [row["status_code"] for row in terminal_rows],
        "successful_terminal": [
            {**row, "payload": json.loads(row["payload_json"])}
            for row in terminal_rows
            if row["status_code"] == 201
        ],
    }


def complete_signup(page: Page, checks: Checks, viewport_name: str) -> dict[str, Any]:
    page.goto("/", wait_until="networkidle")
    checks.add(f"{viewport_name}: signup first screen", page.locator("#signup-form").is_visible())
    inspect_layout(page, checks, f"{viewport_name} signup")
    auth_artifact = screenshot(page, checks, f"{viewport_name}-signup")
    page.locator("#password").fill(PASSWORD)
    with page.expect_response("**/api/auth/register") as invalid_info:
        page.locator("#signup-form button[type=submit]").click()
    checks.add(f"{viewport_name}: registration validation", invalid_info.value.status == 422)
    checks.add(
        f"{viewport_name}: local terms error visible",
        "Accept" in page.locator('[data-error="accepted_terms"]').inner_text(),
    )
    page.locator("#accepted-terms").check()
    with page.expect_response("**/api/auth/register") as register_info:
        page.locator("#signup-form button[type=submit]").click()
    checks.require(f"{viewport_name}: registration created", register_info.value.status == 201)
    page.wait_for_url("**/signup/verify")
    page.locator("#verification-code").fill("111111")
    with page.expect_response("**/api/auth/verify") as wrong_code_info:
        page.locator("#verify-form button[type=submit]").click()
    checks.add(f"{viewport_name}: wrong code rejected", wrong_code_info.value.status == 422)
    checks.add(
        f"{viewport_name}: verification remains local",
        "No message was sent" in page.locator(".auth-form").inner_text(),
    )
    page.locator("#verification-code").fill("246810")
    with page.expect_response("**/api/auth/verify") as verify_info:
        page.locator("#verify-form button[type=submit]").click()
    checks.require(f"{viewport_name}: local email verified", verify_info.value.status == 200)
    page.wait_for_url("**/signup/workspace")
    page.locator("#workspace-domain").fill("Bad domain!")
    with page.expect_response("**/api/workspaces") as invalid_workspace_info:
        page.locator("#workspace-form button[type=submit]").click()
    checks.add(f"{viewport_name}: workspace validation", invalid_workspace_info.value.status == 422)
    page.locator("#workspace-name").fill("Pinecrest Support")
    page.locator("#workspace-domain").fill("pinecrest-support")
    with page.expect_response("**/api/workspaces") as workspace_info:
        page.locator("#workspace-form button[type=submit]").click()
    checks.require(f"{viewport_name}: Sprout workspace created", workspace_info.value.status == 201)
    page.wait_for_url("**/a/dashboard")
    checks.add(f"{viewport_name}: agent dashboard entered", page.locator("h1").inner_text() == "Good morning, Alex")
    return {
        "registration_status": register_info.value.status,
        "verification_status": verify_info.value.status,
        "workspace": workspace_info.value.json(),
        "artifact": auth_artifact,
    }


def create_ticket(
    page: Page, checks: Checks, task: dict[str, Any], viewport_name: str
) -> dict[str, Any]:
    page.locator('a[href="/a/tickets/new"]').first.click()
    page.wait_for_url("**/a/tickets/new")
    page.locator("#ticket-form button[type=submit]").click()
    checks.add(
        f"{viewport_name}: required ticket fields validated",
        "Complete the required" in page.locator("#form-alert").inner_text(),
    )
    page.locator("#ticket-subject").fill(EXPECTED_BODY["subject"])
    page.locator("#ticket-description").fill(EXPECTED_BODY["description"])
    page.locator("#ticket-priority").select_option("3")
    page.locator("#ticket-type").select_option("Billing")
    page.locator("#ticket-agent").select_option("2002")
    page.wait_for_timeout(700)
    state_before_reload = page.evaluate("async () => await (await fetch('/api/state')).json()")
    checks.add(
        f"{viewport_name}: exact ticket draft persisted",
        all(state_before_reload["ticketDraft"][key] == value for key, value in EXPECTED_BODY.items()),
        state_before_reload["ticketDraft"],
    )
    page.reload(wait_until="networkidle")
    checks.add(
        f"{viewport_name}: draft restored after refresh",
        page.locator("#ticket-subject").input_value() == EXPECTED_BODY["subject"]
        and page.locator("#ticket-description").input_value() == EXPECTED_BODY["description"]
        and page.locator("#ticket-priority").input_value() == "3"
        and page.locator("#ticket-agent").input_value() == "2002",
    )
    inspect_layout(page, checks, f"{viewport_name} ticket form")
    form_artifact = screenshot(page, checks, f"{viewport_name}-ticket-form")

    page.evaluate(
        "async () => await (await fetch('/api/testing/fail-next-terminal', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})).json()"
    )
    with (
        page.expect_request("**/api/_/tickets") as failed_request_info,
        page.expect_response("**/api/_/tickets") as failed_response_info,
    ):
        page.locator("#ticket-form button[type=submit]").click()
    failed_request = failed_request_info.value
    failed_response = failed_response_info.value
    checks.add(f"{viewport_name}: retryable terminal error", failed_response.status == 503)
    checks.add(
        f"{viewport_name}: failed attempt still exact",
        failed_request.post_data_json == EXPECTED_BODY,
        failed_request.post_data_json,
    )
    checks.add(f"{viewport_name}: retry control visible", page.locator("#form-alert .btn").is_visible())
    with (
        page.expect_request("**/api/_/tickets") as request_info,
        page.expect_response("**/api/_/tickets") as response_info,
    ):
        page.locator("#form-alert .btn").click()
    request = request_info.value
    response = response_info.value
    checks.require(f"{viewport_name}: terminal request returned 201", response.status == 201)
    checks.add(f"{viewport_name}: terminal method POST", request.method == "POST", request.method)
    checks.add(f"{viewport_name}: terminal path exact", urlparse(request.url).path == "/api/_/tickets", request.url)
    checks.add(f"{viewport_name}: terminal body exact", request.post_data_json == EXPECTED_BODY, request.post_data_json)
    checks.add(f"{viewport_name}: dev matcher accepts request", matcher_matches(task, request), request.url)
    page.wait_for_url(re.compile(r".*/a/tickets/\d+$"))
    ticket_id = int(urlparse(page.url).path.rsplit("/", 1)[1])
    page.locator("h1", has_text=EXPECTED_BODY["subject"]).wait_for()
    checks.add(f"{viewport_name}: detail subject visible", page.locator("h1").inner_text() == EXPECTED_BODY["subject"])
    checks.add(f"{viewport_name}: High visible", "High" in page.locator(".side-panel").inner_text())
    checks.add(f"{viewport_name}: Test Agent visible", "Test Agent" in page.locator(".side-panel").inner_text())
    page.reload(wait_until="networkidle")
    checks.add(
        f"{viewport_name}: completed ticket persists after refresh",
        page.locator("h1").inner_text() == EXPECTED_BODY["subject"]
        and EXPECTED_BODY["description"] in page.locator(".message-body").inner_text(),
    )
    return {
        "id": ticket_id,
        "url": request.url,
        "method": request.method,
        "body": request.post_data_json,
        "status": response.status,
        "ticket_id": ticket_id,
        "form_artifact": form_artifact,
    }


def exercise_lifecycle(page: Page, checks: Checks, viewport_name: str, ticket_id: int) -> dict[str, Any]:
    page.locator('a[href$="/edit"]').click()
    page.wait_for_url(f"**/a/tickets/{ticket_id}/edit")
    checks.add(
        f"{viewport_name}: edit surface populated",
        page.locator("#edit-subject").input_value() == EXPECTED_BODY["subject"],
    )
    page.locator(f'a[href="/a/tickets/{ticket_id}"]').click()
    page.locator("#resolve-ticket").click()
    page.locator("#reopen-ticket").wait_for()
    checks.add(f"{viewport_name}: resolve updates detail", "Resolved" in page.locator(".side-panel").inner_text())
    page.locator("#reopen-ticket").click()
    page.locator("#resolve-ticket").wait_for()
    checks.add(f"{viewport_name}: reopen restores Open", "Open" in page.locator(".side-panel").inner_text())
    page.locator("#logout-button").click()
    page.wait_for_url("**/login")
    page.locator("#login-password").fill("wrong-password")
    with page.expect_response("**/api/auth/login") as wrong_login_info:
        page.locator("#login-form button[type=submit]").click()
    checks.add(f"{viewport_name}: invalid login rejected", wrong_login_info.value.status == 422)
    page.locator("#login-password").fill(PASSWORD)
    with page.expect_response("**/api/auth/login") as login_info:
        page.locator("#login-form button[type=submit]").click()
    checks.add(f"{viewport_name}: login recovery succeeds", login_info.value.status == 200)
    page.wait_for_url("**/a/dashboard")
    page.goto(f"/a/tickets/{ticket_id}", wait_until="networkidle")
    checks.add(f"{viewport_name}: ticket survives login recovery", page.locator("h1").inner_text() == EXPECTED_BODY["subject"])
    return {"wrong_login": wrong_login_info.value.status, "login": login_info.value.status}


def exercise_adjacent_states(page: Page, checks: Checks, viewport_name: str, ticket_id: int) -> dict[str, Any]:
    page.goto("/a/tickets", wait_until="networkidle")
    page.locator("#ticket-search").fill("10042")
    checks.add(f"{viewport_name}: ticket search finds target", page.locator("#ticket-table-body tr").count() == 1)
    page.locator("#ticket-search").fill("no-such-ticket")
    checks.add(f"{viewport_name}: search empty recovery", "No matching tickets" in page.locator("#ticket-list").inner_text())
    page.goto("/a/tickets?api_error=1", wait_until="networkidle")
    checks.add(f"{viewport_name}: list error surface", "could not be loaded" in page.locator("#ticket-list").inner_text())
    page.locator("#retry-list").click()
    page.locator("#ticket-table-body tr").first.wait_for()
    checks.add(f"{viewport_name}: list retry succeeds", page.locator("#ticket-table-body tr").count() == 1)
    page.goto("/a/tickets/99999", wait_until="networkidle")
    checks.add(f"{viewport_name}: missing ticket recovery", "Ticket not found" in page.locator(".not-found").inner_text())
    page.goto("/a/not-a-real-page", wait_until="networkidle")
    checks.add(f"{viewport_name}: general 404 recovery", "Page not found" in page.locator(".not-found").inner_text())
    page.goto("/a/team", wait_until="networkidle")
    page.locator("#invite-agent").click()
    page.locator(".toast").wait_for()
    checks.add(f"{viewport_name}: team boundary visible", "No external request" in page.locator(".toast").inner_text())
    page.goto("/a/settings/integrations", wait_until="networkidle")
    page.locator('[data-connect="Slack"]').click()
    page.locator(".toast").last.wait_for()
    checks.add(f"{viewport_name}: integration boundary visible", "No external request" in page.locator(".toast").last.inner_text())
    page.goto(f"/a/tickets/{ticket_id}", wait_until="networkidle")
    return {"ticket_id": ticket_id, "final_path": urlparse(page.url).path}


def exercise_api_edges(browser: Browser, clone: str, checks: Checks) -> dict[str, Any]:
    context = browser.new_context(base_url=clone)
    page = context.new_page()
    page.goto("/", wait_until="networkidle")
    exact = json.dumps(EXPECTED_BODY, separators=(",", ":"))
    statuses = page.evaluate(
        """async ({body}) => {
          const results = {};
          results.unauthorized = (await fetch('/api/_/tickets', {method:'POST', headers:{'Content-Type':'application/json'}, body})).status;
          results.malformed = (await fetch('/api/_/tickets', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{'})).status;
          results.unsupported = (await fetch('/api/_/tickets', {method:'POST', headers:{'Content-Type':'text/plain'}, body})).status;
          results.missingApi = (await fetch('/api/not-real')).status;
          results.missingTicket = (await fetch('/api/_/tickets/99999')).status;
          const isolated = await (await fetch('/api/state')).json();
          results.isolatedTickets = isolated.counts.tickets;
          return results;
        }""",
        {"body": exact},
    )
    checks.add("edge: unauthenticated terminal rejected", statuses["unauthorized"] == 401, statuses)
    checks.add("edge: malformed terminal rejected", statuses["malformed"] == 400, statuses)
    checks.add("edge: unsupported content type rejected", statuses["unsupported"] == 415, statuses)
    checks.add("edge: missing API is 404", statuses["missingApi"] == 404, statuses)
    checks.add("edge: unauthenticated missing ticket is 401", statuses["missingTicket"] == 401, statuses)
    checks.add("edge: session state isolated", statuses["isolatedTickets"] == 0, statuses)
    context.close()
    return statuses


def run_viewport(
    browser: Browser,
    clone: str,
    db_path: Path,
    task: dict[str, Any],
    checks: Checks,
    name: str,
    viewport: dict[str, int],
) -> dict[str, Any]:
    reset_server(clone)
    page_errors: list[str] = []
    console_errors: list[str] = []
    external_requests: list[str] = []
    context = browser.new_context(base_url=clone, viewport=viewport)
    attach_telemetry(context, clone, page_errors, console_errors, external_requests)
    page = context.new_page()
    artifacts = []
    signup = complete_signup(page, checks, name)
    artifacts.append(signup["artifact"])
    inspect_layout(page, checks, f"{name} dashboard")
    artifacts.append(screenshot(page, checks, f"{name}-dashboard"))
    terminal = create_ticket(page, checks, task, name)
    artifacts.append(terminal["form_artifact"])
    inspect_layout(page, checks, f"{name} ticket detail")
    artifacts.append(screenshot(page, checks, f"{name}-ticket-detail"))
    database = db_evidence(db_path, EXPECTED_BODY)
    checks.add(f"{name}: SQLite ticket matches terminal", database["ticket_matches_body"], database["ticket"])
    checks.add(f"{name}: SQLite account is verified Alex", database["account"]["full_name"] == "Alex Green" and database["account"]["verified"] == 1, database["account"])
    checks.add(f"{name}: SQLite workspace is Sprout", database["workspace"]["plan"] == "Sprout" and database["workspace"]["completed"] == 1, database["workspace"])
    checks.add(f"{name}: one successful terminal journal", len(database["successful_terminal"]) == 1, database["terminal_statuses"])
    checks.add(f"{name}: successful journal body exact", database["successful_terminal"][0]["payload"] == EXPECTED_BODY, database["successful_terminal"][0]["payload"])
    checks.add(f"{name}: creation event body exact", database["created_event"]["detail"] == EXPECTED_BODY, database["created_event"]["detail"])
    lifecycle = exercise_lifecycle(page, checks, name, terminal["id"])

    duplicate = page.evaluate(
        """async (body) => {
          const duplicate = await fetch('/api/_/tickets', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
          const extra = await fetch('/api/_/tickets', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...body, unexpected:true})});
          return {duplicate:duplicate.status, extra:extra.status};
        }""",
        EXPECTED_BODY,
    )
    checks.add(f"{name}: duplicate terminal rejected", duplicate["duplicate"] == 409, duplicate)
    checks.add(f"{name}: unsupported terminal field rejected", duplicate["extra"] == 422, duplicate)
    adjacent = exercise_adjacent_states(page, checks, name, terminal["id"])
    inspect_layout(page, checks, f"{name} final detail")
    artifacts.append(screenshot(page, checks, f"{name}-final-detail"))
    state_after = page.evaluate("async () => await (await fetch('/api/state')).json()")
    checks.add(f"{name}: exactly one durable ticket", state_after["counts"]["tickets"] == 1, state_after["counts"])
    checks.add(f"{name}: explicit local boundaries journaled", state_after["counts"]["boundaries"] >= 4, state_after["boundaries"])
    checks.add(f"{name}: no external requests", not external_requests, external_requests)
    checks.add(f"{name}: no page errors", not page_errors, page_errors)
    checks.add(f"{name}: no unexpected console errors", not console_errors, console_errors)
    context.close()
    return {
        "viewport": viewport,
        "signup": signup,
        "terminal": terminal,
        "database": database,
        "lifecycle": lifecycle,
        "adjacent": adjacent,
        "final_state": state_after,
        "telemetry": {
            "page_errors": page_errors,
            "console_errors": console_errors,
            "external_requests": external_requests,
        },
        "artifacts": artifacts,
    }


def validate_contract(checks: Checks) -> tuple[dict[str, Any], dict[str, Any]]:
    task = json.loads(TASK_PATH.read_text())
    source = json.loads(SOURCE_TASK_PATH.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(task), key=lambda error: list(error.path))
    checks.add("contract: dev-115 validates against task schema", not errors, [error.message for error in errors])
    checks.add("contract: source task id is 583", source["metadata"]["task_id"] == 583)
    checks.add("contract: source terminal method preserved", source["eval_schema"]["method"] == task["eval_schema"]["method"] == "POST")
    checks.add("contract: source terminal suffix preserved", source["eval_schema"]["url_pattern"].endswith("/api/_/tickets") and task["eval_schema"]["url_pattern"].endswith("/api/_/tickets$"))
    checks.add("contract: exact body documented", task["eval_schema"]["body"] == EXPECTED_BODY, task["eval_schema"]["body"])
    checks.add("contract: canonical port 8132", ":8132/" in task["eval_schema"]["url_pattern"])
    for host in ("localhost", "127.0.0.1", "host.docker.internal"):
        url = f"http://{host}:8132/api/_/tickets"
        checks.add(f"contract: matcher accepts {host}", bool(re.fullmatch(task["eval_schema"]["url_pattern"], url)), url)
    for url in (
        "https://freshdesk.com/api/_/tickets",
        "http://127.0.0.1:8133/api/_/tickets",
        "http://example.com:8132/api/_/tickets",
        "http://127.0.0.1:8132/api/_/tickets/1",
    ):
        checks.add(f"contract: matcher rejects {url}", not re.fullmatch(task["eval_schema"]["url_pattern"], url))
    return task, source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", default="http://127.0.0.1:8132")
    parser.add_argument("--db", type=Path, default=CLONE_ROOT / "freshdesk.sqlite3")
    args = parser.parse_args()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    checks = Checks()
    task, source = validate_contract(checks)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clone": args.clone,
        "database": str(args.db.resolve()),
        "task": task,
        "source_task": source,
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            report["desktop"] = run_viewport(
                browser,
                args.clone,
                args.db,
                task,
                checks,
                "desktop",
                {"width": 1365, "height": 900},
            )
            report["mobile"] = run_viewport(
                browser,
                args.clone,
                args.db,
                task,
                checks,
                "mobile",
                {"width": 390, "height": 844},
            )
            report["api_edges"] = exercise_api_edges(browser, args.clone, checks)
            browser.close()
    except Exception as exc:  # noqa: BLE001 - verifier must persist partial evidence
        checks.add("verifier completed", False, f"{type(exc).__name__}: {exc}")
        report["exception"] = f"{type(exc).__name__}: {exc}"
    report["checks"] = checks.items
    report["summary"] = {
        "total": len(checks.items),
        "passed": checks.passed,
        "failed": checks.failed,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], indent=2))
    for item in checks.items:
        if not item["passed"]:
            print(f"FAIL: {item['name']}: {item.get('detail')}")
    print(f"Report: {REPORT_PATH}")
    raise SystemExit(1 if checks.failed else 0)


if __name__ == "__main__":
    main()
