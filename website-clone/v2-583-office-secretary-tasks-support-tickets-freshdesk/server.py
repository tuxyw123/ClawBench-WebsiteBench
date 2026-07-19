#!/usr/bin/env python3
"""Task-scoped, local-only Freshdesk replica for ClawBench V2 task 583."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
SESSION_COOKIE = "freshdesk_replica_session"
LOCAL_CODE = "246810"
FIXTURE_EMAIL = "alex.green.uoft@clawbench.cc"
FIXTURE_NAME = "Alex Green"
REQUESTER_ID = 1001
TEST_AGENT_ID = 2002
SUPPORT_GROUP_ID = 3001
TERMINAL_FIELDS = {
    "requester_id",
    "subject",
    "description",
    "status",
    "priority",
    "source",
    "group_id",
    "responder_id",
    "type",
}
DRAFT_FIELDS = {
    "requester_id",
    "subject",
    "description",
    "status",
    "priority",
    "source",
    "group_id",
    "responder_id",
    "type",
}
EXPECTED_SUBJECT = "Invoice Dispute - Order #10042"
EXPECTED_DESCRIPTION = (
    "The invoice for Order #10042 includes a duplicate $125.00 service charge. "
    "Please review the billing discrepancy and issue a corrected invoice."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              session_id TEXT PRIMARY KEY,
              authenticated INTEGER NOT NULL DEFAULT 0,
              verified INTEGER NOT NULL DEFAULT 0,
              fail_next_terminal INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signup_drafts (
              session_id TEXT PRIMARY KEY,
              full_name TEXT NOT NULL DEFAULT '',
              email TEXT NOT NULL DEFAULT '',
              workspace_name TEXT NOT NULL DEFAULT '',
              workspace_domain TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
              session_id TEXT PRIMARY KEY,
              full_name TEXT NOT NULL,
              email TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              verified INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspaces (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              domain TEXT NOT NULL,
              plan TEXT NOT NULL CHECK(plan = 'Sprout'),
              completed INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ticket_drafts (
              session_id TEXT PRIMARY KEY,
              requester_id INTEGER NOT NULL DEFAULT 1001,
              subject TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '',
              status INTEGER NOT NULL DEFAULT 2,
              priority INTEGER NOT NULL DEFAULT 1,
              source INTEGER NOT NULL DEFAULT 3,
              group_id INTEGER NOT NULL DEFAULT 3001,
              responder_id INTEGER,
              type TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              workspace_id INTEGER NOT NULL,
              requester_id INTEGER NOT NULL,
              subject TEXT NOT NULL,
              description TEXT NOT NULL,
              status INTEGER NOT NULL CHECK(status IN (2, 3, 4, 5)),
              priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 4),
              source INTEGER NOT NULL,
              group_id INTEGER NOT NULL,
              responder_id INTEGER NOT NULL,
              type TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(session_id, subject),
              FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
            );
            CREATE TABLE IF NOT EXISTS ticket_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ticket_id INTEGER NOT NULL,
              session_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              detail_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS request_journal (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              method TEXT NOT NULL,
              content_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status_code INTEGER NOT NULL,
              terminal INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS boundary_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              detail TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )


class ReplicaServer(ThreadingHTTPServer):
    db_path: Path


class Handler(BaseHTTPRequestHandler):
    server_version = "FreshdeskReplica/1.0"

    @property
    def db_path(self) -> Path:
        return self.server.db_path  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def session(self) -> tuple[str, bool]:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        if morsel and re.fullmatch(r"[a-f0-9]{32}", morsel.value):
            session_id = morsel.value
            created = False
        else:
            session_id = secrets.token_hex(16)
            created = True
        now = utc_now()
        with connect(self.db_path) as db:
            db.execute(
                """INSERT INTO sessions (session_id, created_at, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at""",
                (session_id, now, now),
            )
        return session_id, created

    def send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = HTTPStatus.OK,
        session: tuple[str, bool] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'",
        )
        if session and session[1]:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={session[0]}; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()
        self.wfile.write(body)

    def send_json(
        self,
        payload: Any,
        status: int = HTTPStatus.OK,
        session: tuple[str, bool] | None = None,
    ) -> None:
        body = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        self.send_bytes(body, "application/json; charset=utf-8", status, session)

    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 1 or length > 30_000:
            raise ValueError("Expected a request body under 30 KB")
        return self.rfile.read(length)

    def read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise TypeError("Content-Type must be application/json")
        raw = self.read_body()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Malformed JSON request body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def ensure_draft(self, db: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM ticket_drafts WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row:
            return row
        db.execute(
            "INSERT INTO ticket_drafts (session_id, updated_at) VALUES (?, ?)",
            (session_id, utc_now()),
        )
        return db.execute(
            "SELECT * FROM ticket_drafts WHERE session_id = ?", (session_id,)
        ).fetchone()

    def account_ready(self, db: sqlite3.Connection, session_id: str) -> bool:
        row = db.execute(
            """SELECT s.authenticated, s.verified, w.completed
               FROM sessions s LEFT JOIN workspaces w ON w.session_id = s.session_id
               WHERE s.session_id = ?""",
            (session_id,),
        ).fetchone()
        return bool(row and row["authenticated"] and row["verified"] and row["completed"])

    def journal(
        self,
        db: sqlite3.Connection,
        session_id: str,
        payload: Any,
        status: int,
        terminal: bool,
    ) -> None:
        db.execute(
            """INSERT INTO request_journal
               (session_id, endpoint, method, content_type, payload_json,
                status_code, terminal, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                urlparse(self.path).path,
                self.command,
                self.headers.get("Content-Type", ""),
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                status,
                int(terminal),
                utc_now(),
            ),
        )

    def boundary(
        self, db: sqlite3.Connection, session_id: str, kind: str, detail: str
    ) -> None:
        db.execute(
            """INSERT INTO boundary_events (session_id, kind, detail, created_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, kind, detail, utc_now()),
        )

    def state(self, session_id: str) -> dict[str, Any]:
        with connect(self.db_path) as db:
            session_row = db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            account_row = db.execute(
                "SELECT * FROM accounts WHERE session_id = ?", (session_id,)
            ).fetchone()
            workspace_row = db.execute(
                "SELECT * FROM workspaces WHERE session_id = ?", (session_id,)
            ).fetchone()
            signup_row = db.execute(
                "SELECT * FROM signup_drafts WHERE session_id = ?", (session_id,)
            ).fetchone()
            draft = dict(self.ensure_draft(db, session_id))
            tickets = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM tickets WHERE session_id = ? ORDER BY id DESC",
                    (session_id,),
                )
            ]
            events = []
            for row in db.execute(
                """SELECT e.* FROM ticket_events e JOIN tickets t ON t.id = e.ticket_id
                   WHERE t.session_id = ? ORDER BY e.id""",
                (session_id,),
            ):
                item = dict(row)
                item["detail"] = json.loads(item.pop("detail_json"))
                events.append(item)
            journal = []
            for row in db.execute(
                "SELECT * FROM request_journal WHERE session_id = ? ORDER BY id",
                (session_id,),
            ):
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                item["terminal"] = bool(item["terminal"])
                journal.append(item)
            boundaries = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM boundary_events WHERE session_id = ? ORDER BY id",
                    (session_id,),
                )
            ]
        session_data = dict(session_row) if session_row else {}
        for key in ("authenticated", "verified", "fail_next_terminal"):
            if key in session_data:
                session_data[key] = bool(session_data[key])
        account = dict(account_row) if account_row else None
        if account:
            account.pop("password_hash", None)
            account["verified"] = bool(account["verified"])
        workspace = dict(workspace_row) if workspace_row else None
        if workspace:
            workspace["completed"] = bool(workspace["completed"])
        return {
            "session": session_data,
            "account": account,
            "workspace": workspace,
            "signupDraft": dict(signup_row) if signup_row else None,
            "ticketDraft": draft,
            "tickets": tickets,
            "events": events,
            "journal": journal,
            "boundaries": boundaries,
            "fixtures": {
                "requester": {
                    "id": REQUESTER_ID,
                    "name": FIXTURE_NAME,
                    "email": FIXTURE_EMAIL,
                    "company": "Pinecrest Technologies Inc.",
                },
                "agents": [
                    {"id": TEST_AGENT_ID, "name": "Test Agent", "role": "Agent"}
                ],
                "groups": [{"id": SUPPORT_GROUP_ID, "name": "Support"}],
            },
            "counts": {
                "tickets": len(tickets),
                "successfulTerminalRequests": sum(
                    1
                    for item in journal
                    if item["terminal"] and item["status_code"] == 201
                ),
                "terminalAttempts": sum(1 for item in journal if item["terminal"]),
                "boundaries": len(boundaries),
            },
        }

    def serve_static(self, relative: str, session: tuple[str, bool]) -> None:
        try:
            candidate = (STATIC_ROOT / unquote(relative)).resolve()
            candidate.relative_to(STATIC_ROOT.resolve())
        except (ValueError, OSError):
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND, session)
            return
        if not candidate.is_file():
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND, session)
            return
        content_type, _ = mimetypes.guess_type(candidate)
        self.send_bytes(
            candidate.read_bytes(), content_type or "application/octet-stream", session=session
        )

    def do_GET(self) -> None:  # noqa: N802
        session = self.session()
        session_id = session[0]
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self.send_json(
                {"ok": True, "service": "freshdesk-replica", "port": 8132},
                session=session,
            )
            return
        if path in {"/api/bootstrap", "/api/state"}:
            self.send_json(self.state(session_id), session=session)
            return
        if path == "/api/_/tickets":
            with connect(self.db_path) as db:
                if not self.account_ready(db, session_id):
                    self.send_json(
                        {"error": "Authentication required", "code": "unauthenticated"},
                        HTTPStatus.UNAUTHORIZED,
                        session,
                    )
                    return
                if "simulate_error=1" in parsed.query:
                    self.send_json(
                        {"error": "Tickets are temporarily unavailable", "retryable": True},
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        session,
                    )
                    return
                tickets = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM tickets WHERE session_id = ? ORDER BY id DESC",
                        (session_id,),
                    )
                ]
            self.send_json({"tickets": tickets}, session=session)
            return
        match = re.fullmatch(r"/api/_/tickets/(\d+)", path)
        if match:
            with connect(self.db_path) as db:
                if not self.account_ready(db, session_id):
                    self.send_json(
                        {"error": "Authentication required", "code": "unauthenticated"},
                        HTTPStatus.UNAUTHORIZED,
                        session,
                    )
                    return
                row = db.execute(
                    "SELECT * FROM tickets WHERE id = ? AND session_id = ?",
                    (int(match.group(1)), session_id),
                ).fetchone()
            if not row:
                self.send_json({"error": "Ticket not found"}, HTTPStatus.NOT_FOUND, session)
                return
            self.send_json({"ticket": dict(row)}, session=session)
            return
        if path.startswith("/static/"):
            self.serve_static(path.removeprefix("/static/"), session)
            return
        if path == "/favicon.ico":
            self.serve_static("assets/freshdesk-mark.svg", session)
            return
        if not path.startswith("/api/"):
            self.serve_static("index.html", session)
            return
        self.send_json({"error": "API route not found"}, HTTPStatus.NOT_FOUND, session)

    def do_POST(self) -> None:  # noqa: N802
        session = self.session()
        session_id = session[0]
        path = urlparse(self.path).path
        if path == "/api/_/tickets":
            self.create_ticket(session)
            return
        try:
            payload = self.read_json()
        except TypeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE, session)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST, session)
            return

        if path == "/api/signup/draft":
            allowed = {"full_name", "email", "workspace_name", "workspace_domain"}
            if set(payload) - allowed:
                self.send_json({"error": "Unsupported draft fields"}, HTTPStatus.BAD_REQUEST, session)
                return
            now = utc_now()
            values = {key: str(payload.get(key, "")).strip() for key in allowed}
            with connect(self.db_path) as db:
                db.execute(
                    """INSERT INTO signup_drafts
                       (session_id, full_name, email, workspace_name, workspace_domain, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(session_id) DO UPDATE SET
                       full_name=excluded.full_name, email=excluded.email,
                       workspace_name=excluded.workspace_name,
                       workspace_domain=excluded.workspace_domain, updated_at=excluded.updated_at""",
                    (
                        session_id,
                        values["full_name"],
                        values["email"],
                        values["workspace_name"],
                        values["workspace_domain"],
                        now,
                    ),
                )
            self.send_json({"saved": True, "updated_at": now}, session=session)
            return

        if path == "/api/auth/register":
            expected = {"full_name", "email", "password", "accepted_terms"}
            errors: dict[str, str] = {}
            if set(payload) != expected:
                errors["form"] = "Use the complete registration form"
            if str(payload.get("full_name", "")).strip() != FIXTURE_NAME:
                errors["full_name"] = f"Use the assigned name {FIXTURE_NAME}"
            if str(payload.get("email", "")).strip().lower() != FIXTURE_EMAIL:
                errors["email"] = f"Use the assigned email {FIXTURE_EMAIL}"
            password = str(payload.get("password", ""))
            if len(password) < 8:
                errors["password"] = "Password must be at least 8 characters"
            if payload.get("accepted_terms") is not True:
                errors["accepted_terms"] = "Accept the local terms to continue"
            if errors:
                self.send_json({"error": "Check the highlighted fields", "fields": errors}, 422, session)
                return
            now = utc_now()
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            with connect(self.db_path) as db:
                existing = db.execute(
                    "SELECT 1 FROM accounts WHERE session_id = ?", (session_id,)
                ).fetchone()
                if existing:
                    self.send_json({"error": "Account already registered"}, HTTPStatus.CONFLICT, session)
                    return
                db.execute(
                    """INSERT INTO accounts
                       (session_id, full_name, email, password_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (session_id, FIXTURE_NAME, FIXTURE_EMAIL, password_hash, now, now),
                )
                self.boundary(
                    db,
                    session_id,
                    "email_verification",
                    "Verification code displayed locally; no email was sent.",
                )
            self.send_json(
                {
                    "registered": True,
                    "verification_required": True,
                    "local_code_hint": LOCAL_CODE,
                },
                HTTPStatus.CREATED,
                session,
            )
            return

        if path == "/api/auth/verify":
            if set(payload) != {"code"} or payload.get("code") != LOCAL_CODE:
                self.send_json({"error": "That verification code is not valid"}, 422, session)
                return
            now = utc_now()
            with connect(self.db_path) as db:
                account = db.execute(
                    "SELECT 1 FROM accounts WHERE session_id = ?", (session_id,)
                ).fetchone()
                if not account:
                    self.send_json({"error": "Register first"}, HTTPStatus.CONFLICT, session)
                    return
                db.execute(
                    "UPDATE accounts SET verified = 1, updated_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
                db.execute(
                    """UPDATE sessions SET verified = 1, authenticated = 1, updated_at = ?
                       WHERE session_id = ?""",
                    (now, session_id),
                )
            self.send_json({"verified": True}, session=session)
            return

        if path == "/api/workspaces":
            expected = {"name", "domain", "plan"}
            name = str(payload.get("name", "")).strip()
            domain = str(payload.get("domain", "")).strip().lower()
            errors = {}
            if set(payload) != expected:
                errors["form"] = "Use the complete workspace form"
            if len(name) < 3:
                errors["name"] = "Workspace name must be at least 3 characters"
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])?", domain):
                errors["domain"] = "Use 3-30 lowercase letters, numbers, or hyphens"
            if payload.get("plan") != "Sprout":
                errors["plan"] = "This replica supports the free Sprout plan only"
            with connect(self.db_path) as db:
                account = db.execute(
                    "SELECT verified FROM accounts WHERE session_id = ?", (session_id,)
                ).fetchone()
                if not account or not account["verified"]:
                    self.send_json({"error": "Verify the account first"}, HTTPStatus.UNAUTHORIZED, session)
                    return
                if errors:
                    self.send_json({"error": "Check the workspace details", "fields": errors}, 422, session)
                    return
                if db.execute(
                    "SELECT 1 FROM workspaces WHERE session_id = ?", (session_id,)
                ).fetchone():
                    self.send_json({"error": "Workspace already exists"}, HTTPStatus.CONFLICT, session)
                    return
                now = utc_now()
                cursor = db.execute(
                    """INSERT INTO workspaces
                       (session_id, name, domain, plan, created_at, updated_at)
                       VALUES (?, ?, ?, 'Sprout', ?, ?)""",
                    (session_id, name, domain, now, now),
                )
                self.boundary(
                    db,
                    session_id,
                    "workspace_provisioning",
                    "Workspace and Freshdesk subdomain were created only in local SQLite.",
                )
            self.send_json(
                {"workspace_id": cursor.lastrowid, "name": name, "domain": domain, "plan": "Sprout"},
                HTTPStatus.CREATED,
                session,
            )
            return

        if path == "/api/auth/login":
            if set(payload) != {"email", "password"}:
                self.send_json({"error": "Email and password are required"}, 422, session)
                return
            password_hash = hashlib.sha256(str(payload.get("password", "")).encode()).hexdigest()
            with connect(self.db_path) as db:
                row = db.execute(
                    "SELECT email, password_hash, verified FROM accounts WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if (
                    not row
                    or str(payload.get("email", "")).lower() != row["email"].lower()
                    or password_hash != row["password_hash"]
                ):
                    self.send_json({"error": "Email or password is incorrect"}, 422, session)
                    return
                db.execute(
                    "UPDATE sessions SET authenticated = 1, verified = ?, updated_at = ? WHERE session_id = ?",
                    (row["verified"], utc_now(), session_id),
                )
            self.send_json({"authenticated": True}, session=session)
            return

        if path == "/api/auth/logout":
            with connect(self.db_path) as db:
                db.execute(
                    "UPDATE sessions SET authenticated = 0, updated_at = ? WHERE session_id = ?",
                    (utc_now(), session_id),
                )
            self.send_json({"authenticated": False}, session=session)
            return

        if path == "/api/ticket-draft":
            if set(payload) != DRAFT_FIELDS:
                self.send_json({"error": "Draft fields do not match the ticket form"}, 400, session)
                return
            normalized = self.normalize_ticket(payload)
            if normalized is None:
                self.send_json({"error": "Draft contains invalid ticket values"}, 422, session)
                return
            now = utc_now()
            with connect(self.db_path) as db:
                if not self.account_ready(db, session_id):
                    self.send_json({"error": "Authentication required"}, 401, session)
                    return
                db.execute(
                    """INSERT INTO ticket_drafts
                       (session_id, requester_id, subject, description, status, priority,
                        source, group_id, responder_id, type, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(session_id) DO UPDATE SET
                       requester_id=excluded.requester_id, subject=excluded.subject,
                       description=excluded.description, status=excluded.status,
                       priority=excluded.priority, source=excluded.source,
                       group_id=excluded.group_id, responder_id=excluded.responder_id,
                       type=excluded.type, updated_at=excluded.updated_at""",
                    (
                        session_id,
                        normalized["requester_id"],
                        normalized["subject"],
                        normalized["description"],
                        normalized["status"],
                        normalized["priority"],
                        normalized["source"],
                        normalized["group_id"],
                        normalized["responder_id"],
                        normalized["type"],
                        now,
                    ),
                )
            self.send_json({"saved": True, "updated_at": now}, session=session)
            return

        reopen = re.fullmatch(r"/api/_/tickets/(\d+)/reopen", path)
        if reopen:
            ticket_id = int(reopen.group(1))
            with connect(self.db_path) as db:
                if not self.account_ready(db, session_id):
                    self.send_json({"error": "Authentication required"}, 401, session)
                    return
                ticket = db.execute(
                    "SELECT * FROM tickets WHERE id = ? AND session_id = ?",
                    (ticket_id, session_id),
                ).fetchone()
                if not ticket:
                    self.send_json({"error": "Ticket not found"}, 404, session)
                    return
                if ticket["status"] not in (4, 5):
                    self.send_json({"error": "Only resolved or closed tickets can be reopened"}, 409, session)
                    return
                now = utc_now()
                db.execute("UPDATE tickets SET status = 2, updated_at = ? WHERE id = ?", (now, ticket_id))
                db.execute(
                    """INSERT INTO ticket_events
                       (ticket_id, session_id, event_type, detail_json, created_at)
                       VALUES (?, ?, 'reopened', ?, ?)""",
                    (ticket_id, session_id, '{"status":2}', now),
                )
            self.send_json({"ticket_id": ticket_id, "status": 2}, session=session)
            return

        if path == "/api/boundary":
            allowed = {"identity", "email", "customer", "team", "integration"}
            kind = str(payload.get("kind", ""))
            if set(payload) != {"kind", "detail"} or kind not in allowed:
                self.send_json({"error": "Unknown local boundary"}, 422, session)
                return
            with connect(self.db_path) as db:
                self.boundary(db, session_id, kind, str(payload["detail"])[:500])
            self.send_json({"recorded": True, "local_only": True}, session=session)
            return

        if path == "/api/testing/fail-next-terminal":
            with connect(self.db_path) as db:
                if not self.account_ready(db, session_id):
                    self.send_json({"error": "Authentication required"}, 401, session)
                    return
                db.execute(
                    "UPDATE sessions SET fail_next_terminal = 1 WHERE session_id = ?",
                    (session_id,),
                )
            self.send_json({"armed": True}, session=session)
            return

        if path == "/api/reset":
            if self.headers.get("X-Replica-Reset") != "freshdesk-local-reset":
                self.send_json({"error": "Local verifier reset header required"}, 403, session)
                return
            with connect(self.db_path) as db:
                for table in (
                    "ticket_events",
                    "tickets",
                    "ticket_drafts",
                    "request_journal",
                    "boundary_events",
                    "workspaces",
                    "accounts",
                    "signup_drafts",
                    "sessions",
                ):
                    db.execute(f"DELETE FROM {table}")
                db.execute("DELETE FROM sqlite_sequence")
            self.send_json({"reset": True}, session=session)
            return

        self.send_json({"error": "API route not found"}, HTTPStatus.NOT_FOUND, session)

    def normalize_ticket(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            normalized = {
                "requester_id": int(payload["requester_id"]),
                "subject": str(payload["subject"]).strip(),
                "description": str(payload["description"]).strip(),
                "status": int(payload["status"]),
                "priority": int(payload["priority"]),
                "source": int(payload["source"]),
                "group_id": int(payload["group_id"]),
                "responder_id": int(payload["responder_id"]),
                "type": str(payload["type"]).strip(),
            }
        except (KeyError, TypeError, ValueError):
            return None
        if normalized["requester_id"] != REQUESTER_ID:
            return None
        if normalized["status"] not in (2, 3, 4, 5):
            return None
        if normalized["priority"] not in (1, 2, 3, 4):
            return None
        if normalized["source"] != 3 or normalized["group_id"] != SUPPORT_GROUP_ID:
            return None
        if normalized["responder_id"] != TEST_AGENT_ID:
            return None
        if normalized["type"] not in {"Billing", "Question", "Problem", "Feature Request"}:
            return None
        return normalized

    def validate_terminal(self, payload: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        if set(payload) != TERMINAL_FIELDS:
            errors["body"] = "Terminal body must contain exactly the nine ticket fields"
            return errors
        normalized = self.normalize_ticket(payload)
        if normalized is None:
            errors["properties"] = "Requester, source, status, group, agent, type, or priority is invalid"
            return errors
        if normalized["subject"] != EXPECTED_SUBJECT:
            errors["subject"] = f'Subject must be exactly "{EXPECTED_SUBJECT}"'
        description = normalized["description"]
        if description != EXPECTED_DESCRIPTION:
            errors["description"] = "Use the complete specified billing discrepancy description"
        if normalized["status"] != 2:
            errors["status"] = "New ticket status must be Open"
        if normalized["priority"] != 3:
            errors["priority"] = "Priority must be High"
        if normalized["type"] != "Billing":
            errors["type"] = "Type must be Billing"
        return errors

    def create_ticket(self, session: tuple[str, bool]) -> None:
        session_id = session[0]
        try:
            payload = self.read_json()
        except TypeError as exc:
            with connect(self.db_path) as db:
                self.journal(db, session_id, {"_error": str(exc)}, 415, True)
            self.send_json({"error": str(exc)}, 415, session)
            return
        except ValueError as exc:
            with connect(self.db_path) as db:
                self.journal(db, session_id, {"_error": str(exc)}, 400, True)
            self.send_json({"error": str(exc)}, 400, session)
            return
        with connect(self.db_path) as db:
            if not self.account_ready(db, session_id):
                self.journal(db, session_id, payload, 401, True)
                self.send_json(
                    {"error": "Sign in and finish workspace setup before creating tickets"},
                    401,
                    session,
                )
                return
            errors = self.validate_terminal(payload)
            if errors:
                self.journal(db, session_id, payload, 422, True)
                self.send_json({"error": "Ticket validation failed", "fields": errors}, 422, session)
                return
            draft = dict(self.ensure_draft(db, session_id))
            comparable = {key: draft[key] for key in DRAFT_FIELDS}
            if comparable != payload:
                self.journal(db, session_id, payload, 409, True)
                self.send_json(
                    {"error": "Submitted ticket does not match the saved visible draft"},
                    409,
                    session,
                )
                return
            if db.execute(
                "SELECT 1 FROM tickets WHERE session_id = ? AND subject = ?",
                (session_id, EXPECTED_SUBJECT),
            ).fetchone():
                self.journal(db, session_id, payload, 409, True)
                self.send_json({"error": "A ticket with this subject already exists"}, 409, session)
                return
            fail_row = db.execute(
                "SELECT fail_next_terminal FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if fail_row and fail_row["fail_next_terminal"]:
                db.execute(
                    "UPDATE sessions SET fail_next_terminal = 0 WHERE session_id = ?",
                    (session_id,),
                )
                self.journal(db, session_id, payload, 503, True)
                self.send_json(
                    {"error": "Ticket service is temporarily unavailable", "retryable": True},
                    503,
                    session,
                )
                return
            workspace = db.execute(
                "SELECT id FROM workspaces WHERE session_id = ?", (session_id,)
            ).fetchone()
            now = utc_now()
            cursor = db.execute(
                """INSERT INTO tickets
                   (session_id, workspace_id, requester_id, subject, description, status,
                    priority, source, group_id, responder_id, type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    workspace["id"],
                    payload["requester_id"],
                    payload["subject"],
                    payload["description"],
                    payload["status"],
                    payload["priority"],
                    payload["source"],
                    payload["group_id"],
                    payload["responder_id"],
                    payload["type"],
                    now,
                    now,
                ),
            )
            ticket_id = int(cursor.lastrowid)
            db.execute(
                """INSERT INTO ticket_events
                   (ticket_id, session_id, event_type, detail_json, created_at)
                   VALUES (?, ?, 'created', ?, ?)""",
                (ticket_id, session_id, json.dumps(payload, sort_keys=True), now),
            )
            self.journal(db, session_id, payload, 201, True)
        self.send_json(
            {
                "id": ticket_id,
                "subject": payload["subject"],
                "status": 2,
                "priority": 3,
                "responder_id": TEST_AGENT_ID,
            },
            HTTPStatus.CREATED,
            session,
        )

    def do_PATCH(self) -> None:  # noqa: N802
        session = self.session()
        session_id = session[0]
        match = re.fullmatch(r"/api/_/tickets/(\d+)", urlparse(self.path).path)
        if not match:
            self.send_json({"error": "API route not found"}, 404, session)
            return
        try:
            payload = self.read_json()
        except TypeError as exc:
            self.send_json({"error": str(exc)}, 415, session)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400, session)
            return
        allowed = {"subject", "description", "status", "priority", "group_id", "responder_id", "type"}
        if not payload or set(payload) - allowed:
            self.send_json({"error": "Unsupported ticket update fields"}, 422, session)
            return
        ticket_id = int(match.group(1))
        with connect(self.db_path) as db:
            if not self.account_ready(db, session_id):
                self.send_json({"error": "Authentication required"}, 401, session)
                return
            row = db.execute(
                "SELECT * FROM tickets WHERE id = ? AND session_id = ?", (ticket_id, session_id)
            ).fetchone()
            if not row:
                self.send_json({"error": "Ticket not found"}, 404, session)
                return
            merged = dict(row)
            merged.update(payload)
            normalized = self.normalize_ticket(merged)
            if normalized is None or not normalized["subject"] or len(normalized["description"]) < 20:
                self.send_json({"error": "Ticket update is invalid"}, 422, session)
                return
            now = utc_now()
            db.execute(
                """UPDATE tickets SET subject=?, description=?, status=?, priority=?, group_id=?,
                   responder_id=?, type=?, updated_at=? WHERE id=?""",
                (
                    normalized["subject"],
                    normalized["description"],
                    normalized["status"],
                    normalized["priority"],
                    normalized["group_id"],
                    normalized["responder_id"],
                    normalized["type"],
                    now,
                    ticket_id,
                ),
            )
            db.execute(
                """INSERT INTO ticket_events
                   (ticket_id, session_id, event_type, detail_json, created_at)
                   VALUES (?, ?, 'updated', ?, ?)""",
                (ticket_id, session_id, json.dumps(payload, sort_keys=True), now),
            )
            self.journal(db, session_id, payload, 200, False)
        self.send_json({"ticket_id": ticket_id, "updated": True}, session=session)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8132)
    parser.add_argument("--db", type=Path, default=ROOT / "freshdesk.sqlite3")
    args = parser.parse_args()
    initialize(args.db)
    server = ReplicaServer((args.host, args.port), Handler)
    server.db_path = args.db.resolve()
    print(f"Freshdesk replica listening on http://{args.host}:{args.port} using {server.db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
