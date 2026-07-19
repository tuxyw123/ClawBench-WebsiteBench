#!/usr/bin/env python3
"""Task-scoped, local-only Greenhouse/CodePath replica for V2 task 86."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
DEFAULT_DB = ROOT / "greenhouse.sqlite3"
SESSION_COOKIE = "greenhouse_local_session"
BOARD_TOKEN = "codepath"
JOB_ID = "4526154007"
TERMINAL_PATH = f"/v1/boards/{BOARD_TOKEN}/jobs/{JOB_ID}"
RESUME_FILE = "Alex_Green_Resume.pdf"

JOB = {
    "id": JOB_ID,
    "title": "Senior Software Engineer",
    "company": "CodePath",
    "department": "Engineering",
    "location": "Remote, United States, Europe, Canada or LATAM",
    "workplace": "Remote",
    "type": "Full-Time",
    "boardToken": BOARD_TOKEN,
    "summary": (
        "Build and scale the learning platform that helps college students become "
        "industry-ready software engineers."
    ),
}

PROFILE = {
    "first_name": "Alex",
    "last_name": "Green",
    "preferred_name": "Alex",
    "email": "alex.green.uoft@clawbench.cc",
    "country": "Canada",
    "location": "Toronto, Ontario",
    "resume": {
        "file_name": RESUME_FILE,
        "source": "assigned_profile",
        "parse_status": "complete",
    },
    "current_company": "Pinecrest Technologies Inc.",
    "current_title": "Senior Software Engineer",
    "highest_degree": "Doctor of Philosophy in Computer Science",
    "years_experience": "23+",
}

EXPECTED_PAYLOAD = {
    **PROFILE,
    "authorized_to_work": True,
    "requires_sponsorship": False,
    "future_opportunities": True,
    "consent": True,
}

DRAFT_KEYS = set(EXPECTED_PAYLOAD)
APP_PATHS = {
    "/",
    "/company",
    "/codepath",
    f"/codepath/jobs/{JOB_ID}",
    f"/codepath/jobs/{JOB_ID}/apply",
    f"/codepath/jobs/{JOB_ID}/review",
    f"/codepath/jobs/{JOB_ID}/confirmation",
    "/my-application",
    "/privacy",
    "/local-boundary",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(value: Any, limit: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              fail_next_terminal INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listing_views (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_token TEXT NOT NULL,
              job_id TEXT NOT NULL,
              source_path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_token) REFERENCES sessions(token)
            );
            CREATE TABLE IF NOT EXISTS drafts (
              session_token TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              step INTEGER NOT NULL CHECK(step BETWEEN 1 AND 3),
              payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(session_token) REFERENCES sessions(token)
            );
            CREATE TABLE IF NOT EXISTS applications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_token TEXT NOT NULL UNIQUE,
              job_id TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status = 'SUBMITTED_LOCAL'),
              confirmation_code TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_token) REFERENCES sessions(token)
            );
            CREATE TABLE IF NOT EXISTS request_journal (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_token TEXT,
              method TEXT NOT NULL,
              endpoint TEXT NOT NULL,
              content_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status_code INTEGER NOT NULL,
              terminal INTEGER NOT NULL DEFAULT 0,
              outcome TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS boundary_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_token TEXT NOT NULL,
              kind TEXT NOT NULL,
              detail TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_token) REFERENCES sessions(token)
            );
            """
        )


class ReplicaServer(ThreadingHTTPServer):
    db_path: Path


class Handler(BaseHTTPRequestHandler):
    server_version = "GreenhouseCodePathLocal/1.0"

    @property
    def db_path(self) -> Path:
        return self.server.db_path  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(
            f"{self.address_string()} - - [{self.log_date_time_string()}] {fmt % args}\n"
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/static/"):
            self.serve_static(path)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path == "/health":
            self.send_json(
                {
                    "ok": True,
                    "service": "greenhouse-codepath-local",
                    "port": self.server.server_port,
                    "jobId": JOB_ID,
                }
            )
            return
        if path == "/api/bootstrap":
            self.handle_bootstrap()
            return
        if path == "/api/boards/codepath/jobs":
            self.handle_board()
            return
        if path == f"/api/boards/codepath/jobs/{JOB_ID}":
            self.handle_job()
            return
        if path.startswith("/api/boards/codepath/jobs/"):
            self.send_json({"error": "This job is no longer available."}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/state":
            self.handle_state()
            return
        if path == "/documents/alex-green-resume":
            self.handle_resume()
            return
        if path in APP_PATHS:
            if path == f"/codepath/jobs/{JOB_ID}":
                token, created = self.ensure_session()
                with connect(self.db_path) as db:
                    db.execute(
                        "INSERT INTO listing_views(session_token, job_id, source_path, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (token, JOB_ID, path, now_iso()),
                    )
                self.send_file(STATIC_ROOT / "index.html", token if created else None)
                return
            self.send_file(STATIC_ROOT / "index.html")
            return
        self.send_error_page(
            HTTPStatus.NOT_FOUND,
            "Page not found",
            "The Greenhouse page you requested is not available in this local board.",
        )

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == f"/api/drafts/{JOB_ID}":
            self.handle_draft()
            return
        if path == TERMINAL_PATH:
            self.handle_submit()
            return
        if path == "/api/boundary":
            self.handle_boundary()
            return
        if path == "/api/testing/fail-next-terminal":
            self.handle_fail_next()
            return
        if path == "/api/testing/reset":
            self.handle_reset()
            return
        self.send_json({"error": "Endpoint not found."}, HTTPStatus.NOT_FOUND)

    def cookie_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        if morsel and re.fullmatch(r"[A-Za-z0-9_-]{32,80}", morsel.value):
            return morsel.value
        return None

    def ensure_session(self) -> tuple[str, bool]:
        token = self.cookie_token()
        with connect(self.db_path) as db:
            row = (
                db.execute("SELECT token FROM sessions WHERE token=?", (token,)).fetchone()
                if token
                else None
            )
            if row:
                db.execute(
                    "UPDATE sessions SET updated_at=? WHERE token=?", (now_iso(), token)
                )
                return token or "", False
            token = secrets.token_urlsafe(32)
            stamp = now_iso()
            db.execute(
                "INSERT INTO sessions(token, created_at, updated_at) VALUES (?, ?, ?)",
                (token, stamp, stamp),
            )
        return token, True

    def require_session(self) -> str | None:
        token = self.cookie_token()
        if not token:
            return None
        with connect(self.db_path) as db:
            row = db.execute("SELECT token FROM sessions WHERE token=?", (token,)).fetchone()
        return token if row else None

    def read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise TypeError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 1 or length > 32_768:
            raise ValueError("Request body must be between 1 and 32768 bytes")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def read_json_or_error(self, token: str | None, endpoint: str) -> dict[str, Any] | None:
        try:
            return self.read_json()
        except TypeError as exc:
            self.journal(token, endpoint, {}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
            self.send_json({"error": str(exc)}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        except ValueError as exc:
            self.journal(token, endpoint, {}, HTTPStatus.BAD_REQUEST, str(exc))
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return None

    def journal(
        self,
        token: str | None,
        endpoint: str,
        payload: dict[str, Any],
        status: int,
        outcome: str,
        *,
        terminal: bool = False,
        db: sqlite3.Connection | None = None,
    ) -> None:
        values = (
            token,
            "POST",
            endpoint,
            self.headers.get("Content-Type", ""),
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            int(status),
            int(terminal),
            outcome,
            now_iso(),
        )
        sql = (
            "INSERT INTO request_journal(session_token, method, endpoint, content_type, "
            "payload_json, status_code, terminal, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        if db is not None:
            db.execute(sql, values)
            return
        with connect(self.db_path) as own_db:
            own_db.execute(sql, values)

    def get_draft(self, token: str) -> dict[str, Any] | None:
        with connect(self.db_path) as db:
            row = db.execute(
                "SELECT job_id, step, payload_json, updated_at FROM drafts WHERE session_token=?",
                (token,),
            ).fetchone()
        if not row:
            return None
        return {
            "jobId": row["job_id"],
            "step": row["step"],
            "application": json.loads(row["payload_json"]),
            "updatedAt": row["updated_at"],
        }

    def get_application(self, token: str) -> dict[str, Any] | None:
        with connect(self.db_path) as db:
            row = db.execute(
                "SELECT id, job_id, payload_json, status, confirmation_code, created_at "
                "FROM applications WHERE session_token=?",
                (token,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "jobId": row["job_id"],
            "application": json.loads(row["payload_json"]),
            "status": row["status"],
            "confirmationCode": row["confirmation_code"],
            "createdAt": row["created_at"],
        }

    def handle_bootstrap(self) -> None:
        token, created = self.ensure_session()
        self.send_json(
            {
                "board": {"token": BOARD_TOKEN, "name": "CodePath"},
                "job": JOB,
                "profile": PROFILE,
                "resumeDocument": "/documents/alex-green-resume",
                "draft": self.get_draft(token),
                "application": self.get_application(token),
                "localOnly": True,
            },
            cookie_token=token if created else None,
        )

    def handle_board(self) -> None:
        token, created = self.ensure_session()
        jobs = [
            JOB,
            {
                "id": "4402039007",
                "title": "Staff Software Engineer",
                "company": "CodePath",
                "department": "Engineering",
                "location": JOB["location"],
                "type": "Full-Time",
            },
            {
                "id": "4410991007",
                "title": "Senior Product Manager, Learner Experience",
                "company": "CodePath",
                "department": "Product",
                "location": "Remote, United States",
                "type": "Full-Time",
            },
            {
                "id": "4398102007",
                "title": "General Application",
                "company": "CodePath",
                "department": "General",
                "location": "Remote, United States",
                "type": "Full-Time",
            },
        ]
        self.send_json({"jobs": jobs, "count": len(jobs)}, cookie_token=token if created else None)

    def handle_job(self) -> None:
        token, created = self.ensure_session()
        self.send_json({"job": JOB}, cookie_token=token if created else None)

    def handle_resume(self) -> None:
        body = f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{RESUME_FILE}</title><link rel=\"stylesheet\" href=\"/static/styles.css\"></head>
<body class=\"document-body\"><main class=\"resume-sheet\"><header><h1>Alex Green</h1>
<p>Senior Software Engineer · Toronto, Ontario, Canada</p><p>{PROFILE['email']}</p></header>
<section><h2>Summary</h2><p>Senior Software Engineer with 23+ years of experience in full-stack development, distributed systems, and cloud infrastructure. PhD in Computer Science from the University of Toronto.</p></section>
<section><h2>Experience</h2><h3>Senior Software Engineer · Pinecrest Technologies Inc.</h3><p>2019-present · Toronto, ON</p><ul><li>Lead a backend team building distributed data pipelines.</li><li>Design APIs serving more than 2M daily requests.</li><li>Mentor engineers and improve delivery practices.</li></ul></section>
<section><h2>Education</h2><p><strong>PhD, Computer Science</strong> · University of Toronto · 2010</p><p>MSc, Computer Science · University of Toronto · 2004</p><p>BSc, Computer Science · University of Toronto · 2002</p></section>
<section><h2>Skills</h2><p>Python, Java, TypeScript, Go, PostgreSQL, AWS, Docker, Kubernetes, Terraform, React, Node.js</p></section></main></body></html>"""
        self.send_bytes(body.encode(), "text/html; charset=utf-8")

    def validate_draft(self, payload: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        unknown = set(payload) - DRAFT_KEYS
        if unknown:
            errors["fields"] = f"Unsupported fields: {', '.join(sorted(unknown))}"
        required_text = (
            "first_name",
            "last_name",
            "preferred_name",
            "email",
            "country",
            "location",
            "current_company",
            "current_title",
            "highest_degree",
            "years_experience",
        )
        for field in required_text:
            if not compact(payload.get(field), 500):
                errors[field] = "This field is required."
        if compact(payload.get("email"), 300).casefold() != PROFILE["email"]:
            errors["email"] = "Use the email from the assigned resume."
        resume = payload.get("resume")
        if resume != PROFILE["resume"]:
            errors["resume"] = "Attach the assigned Alex Green resume."
        for field in ("authorized_to_work", "requires_sponsorship", "future_opportunities", "consent"):
            if not isinstance(payload.get(field), bool):
                errors[field] = "Select an answer."
        for field, value in PROFILE.items():
            if payload.get(field) != value:
                errors[field] = "This value must match the assigned resume."
        if payload.get("authorized_to_work") is not True:
            errors["authorized_to_work"] = "Confirm Canadian work authorization."
        if payload.get("requires_sponsorship") is not False:
            errors["requires_sponsorship"] = "Use the assigned profile answer."
        return errors

    def handle_draft(self) -> None:
        token = self.require_session()
        if not token:
            self.send_json({"error": "Application session expired. Reload the job."}, HTTPStatus.UNAUTHORIZED)
            return
        body = self.read_json_or_error(token, f"/api/drafts/{JOB_ID}")
        if body is None:
            return
        if set(body) != {"job_id", "step", "application"}:
            self.journal(token, f"/api/drafts/{JOB_ID}", body, 422, "invalid envelope")
            self.send_json({"error": "Draft envelope is invalid."}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        step = body.get("step")
        application = body.get("application")
        if body.get("job_id") != JOB_ID or step not in (1, 2, 3) or not isinstance(application, dict):
            self.journal(token, f"/api/drafts/{JOB_ID}", body, 422, "invalid draft")
            self.send_json({"error": "Draft job, step, or application is invalid."}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        errors = self.validate_draft(application)
        if errors:
            self.journal(token, f"/api/drafts/{JOB_ID}", body, 422, "validation failed")
            self.send_json({"error": "Check the required application fields.", "fields": errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        stamp = now_iso()
        with connect(self.db_path) as db:
            db.execute(
                "INSERT INTO drafts(session_token, job_id, step, payload_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_token) DO UPDATE SET "
                "job_id=excluded.job_id, step=excluded.step, payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (token, JOB_ID, step, json.dumps(application, sort_keys=True), stamp),
            )
        self.journal(token, f"/api/drafts/{JOB_ID}", body, 200, "saved")
        self.send_json({"ok": True, "savedAt": stamp, "step": step})

    def handle_submit(self) -> None:
        token = self.require_session()
        if not token:
            self.send_json({"error": "Application session expired. Reload the job."}, HTTPStatus.UNAUTHORIZED)
            return
        body = self.read_json_or_error(token, TERMINAL_PATH)
        if body is None:
            return
        if body != EXPECTED_PAYLOAD:
            errors = self.validate_draft(body)
            if set(body) != set(EXPECTED_PAYLOAD):
                errors["fields"] = "The terminal payload must contain exactly the reviewed fields."
            if body.get("consent") is not True:
                errors["consent"] = "Consent is required before submission."
            self.journal(token, TERMINAL_PATH, body, 422, "payload mismatch", terminal=True)
            self.send_json({"error": "Application does not match the reviewed profile.", "fields": errors}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        with connect(self.db_path) as db:
            session = db.execute(
                "SELECT fail_next_terminal FROM sessions WHERE token=?", (token,)
            ).fetchone()
            if session and session["fail_next_terminal"]:
                db.execute("UPDATE sessions SET fail_next_terminal=0 WHERE token=?", (token,))
                self.journal(token, TERMINAL_PATH, body, 503, "temporary failure", terminal=True, db=db)
                self.send_json({"error": "Greenhouse is temporarily unavailable. Your reviewed draft is safe."}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            draft = db.execute(
                "SELECT step, payload_json FROM drafts WHERE session_token=? AND job_id=?",
                (token, JOB_ID),
            ).fetchone()
            if not draft or draft["step"] != 3 or json.loads(draft["payload_json"]) != EXPECTED_PAYLOAD:
                self.journal(token, TERMINAL_PATH, body, 409, "unreviewed or stale draft", terminal=True, db=db)
                self.send_json({"error": "Review the current application before submitting."}, HTTPStatus.CONFLICT)
                return
            if db.execute("SELECT 1 FROM applications WHERE session_token=?", (token,)).fetchone():
                self.journal(token, TERMINAL_PATH, body, 409, "duplicate", terminal=True, db=db)
                self.send_json({"error": "This application was already submitted locally."}, HTTPStatus.CONFLICT)
                return
            code = f"CP-{JOB_ID[-4:]}-{secrets.token_hex(3).upper()}"
            stamp = now_iso()
            db.execute(
                "INSERT INTO applications(session_token, job_id, payload_json, status, confirmation_code, created_at) "
                "VALUES (?, ?, ?, 'SUBMITTED_LOCAL', ?, ?)",
                (token, JOB_ID, json.dumps(body, sort_keys=True), code, stamp),
            )
            self.journal(token, TERMINAL_PATH, body, 201, "submitted locally", terminal=True, db=db)
        self.send_json(
            {
                "ok": True,
                "status": "SUBMITTED_LOCAL",
                "confirmationCode": code,
                "jobId": JOB_ID,
                "externalEffects": [],
            },
            HTTPStatus.CREATED,
        )

    def handle_boundary(self) -> None:
        token, created = self.ensure_session()
        body = self.read_json_or_error(token, "/api/boundary")
        if body is None:
            return
        kind = compact(body.get("kind"), 60)
        detail = compact(body.get("detail"), 300)
        if kind not in {"mygreenhouse", "job_alert", "privacy", "employer", "email"}:
            self.send_json({"error": "Unknown local boundary."}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        with connect(self.db_path) as db:
            db.execute(
                "INSERT INTO boundary_events(session_token, kind, detail, created_at) VALUES (?, ?, ?, ?)",
                (token, kind, detail, now_iso()),
            )
        self.send_json(
            {"ok": True, "localOnly": True, "externalEffects": []},
            cookie_token=token if created else None,
        )

    def handle_fail_next(self) -> None:
        token = self.require_session()
        if not token or self.headers.get("X-Replica-Test") != "1":
            self.send_json({"error": "Not available."}, HTTPStatus.NOT_FOUND)
            return
        with connect(self.db_path) as db:
            db.execute("UPDATE sessions SET fail_next_terminal=1 WHERE token=?", (token,))
        self.send_json({"ok": True})

    def handle_reset(self) -> None:
        token = self.require_session()
        if not token or self.headers.get("X-Replica-Test") != "1":
            self.send_json({"error": "Not available."}, HTTPStatus.NOT_FOUND)
            return
        with connect(self.db_path) as db:
            for table in ("applications", "drafts", "listing_views", "request_journal", "boundary_events"):
                db.execute(f"DELETE FROM {table} WHERE session_token=?", (token,))
            db.execute("UPDATE sessions SET fail_next_terminal=0 WHERE token=?", (token,))
        self.send_json({"ok": True})

    def handle_state(self) -> None:
        token, created = self.ensure_session()
        with connect(self.db_path) as db:
            views = [dict(row) for row in db.execute(
                "SELECT job_id, source_path, created_at FROM listing_views WHERE session_token=? ORDER BY id",
                (token,),
            )]
            journal = []
            for row in db.execute(
                "SELECT method, endpoint, content_type, payload_json, status_code, terminal, outcome, created_at "
                "FROM request_journal WHERE session_token=? ORDER BY id",
                (token,),
            ):
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                journal.append(item)
            boundaries = [dict(row) for row in db.execute(
                "SELECT kind, detail, created_at FROM boundary_events WHERE session_token=? ORDER BY id",
                (token,),
            )]
        self.send_json(
            {
                "sessionToken": token,
                "job": JOB,
                "draft": self.get_draft(token),
                "application": self.get_application(token),
                "listingViews": views,
                "journal": journal,
                "boundaryEvents": boundaries,
                "externalEffects": [],
            },
            cookie_token=token if created else None,
        )

    def send_json(
        self,
        payload: Any,
        status: int = HTTPStatus.OK,
        cookie_token: str | None = None,
    ) -> None:
        body = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        self.send_bytes(body, "application/json; charset=utf-8", status, cookie_token)

    def send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = HTTPStatus.OK,
        cookie_token: str | None = None,
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
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'",
        )
        if cookie_token:
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={cookie_token}; Path=/; HttpOnly; SameSite=Lax",
            )
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, cookie_token: str | None = None) -> None:
        if not path.is_file():
            self.send_error_page(HTTPStatus.NOT_FOUND, "Asset not found", "The requested local asset does not exist.")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif path.suffix in {".html", ".css", ".svg"}:
            content_type += "; charset=utf-8"
        self.send_bytes(path.read_bytes(), content_type, cookie_token=cookie_token)

    def serve_static(self, request_path: str) -> None:
        relative = unquote(request_path.removeprefix("/static/"))
        if not relative or ".." in Path(relative).parts:
            self.send_json({"error": "Static path is invalid."}, HTTPStatus.FORBIDDEN)
            return
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_json({"error": "Static path is invalid."}, HTTPStatus.FORBIDDEN)
            return
        self.send_file(candidate)

    def send_error_page(self, status: int, title: str, detail: str) -> None:
        body = f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title>
<link rel=\"stylesheet\" href=\"/static/styles.css\"></head><body><main class=\"error-page\">
<img src=\"/static/assets/codepath-mark.svg\" alt=\"CodePath\"><p class=\"error-code\">{int(status)}</p>
<h1>{title}</h1><p>{detail}</p><a class=\"button primary\" href=\"/codepath\">Return to current openings</a>
</main></body></html>"""
        self.send_bytes(body.encode(), "text/html; charset=utf-8", status)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8134)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    initialize(args.db)
    server = ReplicaServer((args.host, args.port), Handler)
    server.db_path = args.db
    print(f"Greenhouse/CodePath local replica: http://127.0.0.1:{args.port}", flush=True)
    print(f"SQLite: {args.db}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
