#!/usr/bin/env python3
"""Offline, task-scoped Idealist replica for ClawBench V2 task 776."""

from __future__ import annotations

import argparse
import hashlib
import hmac
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
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
DEFAULT_DB = ROOT / "idealist.sqlite3"
SESSION_COOKIE = "idealist_local_session"
ACCOUNT_EMAIL = "alex.green.uoft@clawbench.cc"
TARGET_JOB_KEY = "dumbarton-arts-education-program-manager-washington-dc"
RESUME_FILE = "Alex_Green_Resume.pdf"
CANONICAL_PORT = 8135
EXPECTED_PROFILE = {
    "firstName": "Alex",
    "lastName": "Green",
    "email": ACCOUNT_EMAIL,
    "location": "Toronto, Ontario, Canada",
    "resumeFileName": RESUME_FILE,
    "intent": "COMPLETE_IDEALIST_PROFILE",
}
APP_PATHS = {
    "/",
    "/jobs",
    f"/en/nonprofit-job/{TARGET_JOB_KEY}",
    "/user/register",
    "/user/login",
    f"/application/{TARGET_JOB_KEY}",
    "/my-account",
    "/my-applications",
    "/local-boundary",
}

JOBS: list[dict[str, Any]] = [
    {
        "rank": 1,
        "key": TARGET_JOB_KEY,
        "title": "Program Manager",
        "organization": "Dumbarton Arts & Education",
        "location": "Washington, DC",
        "workMode": "On-site",
        "employment": "Full Time",
        "sector": "Nonprofit",
        "causeArea": "Education",
        "salary": "USD $50,000 - $60,000 / year",
        "published": "2026-07-08",
        "expires": "2026-08-28",
        "quickApply": True,
        "summary": "Lead arts education programs, partnerships, operations, and impact measurement for the DC community.",
        "description": [
            "Dumbarton Arts & Education is a nonprofit dedicated to high-quality arts experiences and education programs for the Washington, DC community.",
            "The Program Manager plays an operational and strategic role, ensuring programs run efficiently, partnerships are well managed, and impact is measured and communicated.",
            "This role supports arts-integrated learning, community engagement, education equity, and accessible lifelong learning.",
        ],
        "responsibilities": [
            "Manage program schedules, budgets, logistics, and day-to-day delivery.",
            "Build effective relationships with schools, teaching artists, families, and community partners.",
            "Track outcomes and translate program data into clear reports and improvements.",
            "Support an inclusive, accessible environment for learners and collaborators.",
        ],
        "qualifications": [
            "Three or more years of program or project management experience.",
            "Strong organization, written communication, and partnership skills.",
            "Commitment to arts access, education equity, and community-centered work.",
        ],
    },
    {
        "rank": 2,
        "key": "calgary-community-volunteer-engagement-coordinator",
        "title": "Program and Community Manager",
        "organization": "National Family Caregivers Association",
        "location": "Washington, DC",
        "workMode": "Hybrid",
        "employment": "Full Time",
        "sector": "Nonprofit",
        "causeArea": "Health & Medicine",
        "salary": "USD $75,000 - $85,000 / year",
        "published": "2026-06-20",
        "expires": "2026-08-04",
        "quickApply": True,
        "summary": "Manage caregiver programs and community partnerships across a national nonprofit network.",
        "description": ["A hybrid community program role with a national caregiver advocacy nonprofit."],
        "responsibilities": ["Coordinate programs, partnerships, and community communications."],
        "qualifications": ["Program management and stakeholder engagement experience."],
    },
    {
        "rank": 3,
        "key": "trellis-youth-program-coordinator-calgary",
        "title": "Operations & Program Associate",
        "organization": "Blue Moon Strategies",
        "location": "Washington, DC",
        "workMode": "Hybrid",
        "employment": "Full Time",
        "sector": "Consulting",
        "causeArea": "Civic Engagement",
        "salary": "USD $55,000 - $70,000 / year",
        "published": "2026-06-18",
        "expires": "2026-08-02",
        "quickApply": False,
        "summary": "Support operations and program delivery for mission-driven clients.",
        "description": ["An associate-level role supporting multiple program teams."],
        "responsibilities": ["Coordinate project operations and program deliverables."],
        "qualifications": ["Two years of operations or program support experience."],
    },
    {
        "rank": 4,
        "key": "edmonton-youth-volunteer-coordinator",
        "title": "Program Manager",
        "organization": "FrameWorks Institute",
        "location": "United States",
        "workMode": "Remote",
        "employment": "Full Time",
        "sector": "Nonprofit",
        "causeArea": "Research & Social Science",
        "salary": "USD $80,000 - $90,000 / year",
        "published": "2026-06-14",
        "expires": "2026-07-29",
        "quickApply": True,
        "summary": "Manage research translation projects for a remote nonprofit team.",
        "description": ["A remote US program management role outside the Washington, DC location filter."],
        "responsibilities": ["Manage research projects and partner deliverables."],
        "qualifications": ["Program management and research communication experience."],
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(value: Any, limit: int = 5000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:limit]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hash_password(password, salt).split("$", 1)[1]
    return hmac.compare_digest(candidate, expected)


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                account_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                postal_code TEXT NOT NULL,
                account_type TEXT NOT NULL,
                profile_complete INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profile_resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL UNIQUE,
                file_name TEXT NOT NULL,
                source TEXT NOT NULL,
                display_size TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token TEXT NOT NULL,
                keywords TEXT NOT NULL,
                location TEXT NOT NULL,
                employment TEXT NOT NULL,
                sector TEXT NOT NULL,
                result_keys TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token TEXT NOT NULL,
                job_key TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                session_token TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                job_key TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                session_token TEXT NOT NULL,
                job_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                delivery TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(account_id, job_key)
            );
            CREATE TABLE IF NOT EXISTS request_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token TEXT,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                content_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS boundary_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token TEXT NOT NULL,
                boundary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


class IdealistHandler(BaseHTTPRequestHandler):
    server_version = "IdealistLocal/1.0"

    @property
    def db_path(self) -> Path:
        return self.server.db_path  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/static/"):
            self.serve_static(path)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path in {"/health", "/api/health"}:
            self.send_json({"ok": True, "service": "idealist-local", "port": self.server.server_port})
            return
        if path == "/api/bootstrap":
            self.handle_bootstrap()
            return
        if path == "/api/jobs":
            self.handle_search(parse_qs(parsed.query, keep_blank_values=True))
            return
        if path.startswith("/api/jobs/"):
            self.handle_job(unquote(path.removeprefix("/api/jobs/")))
            return
        if path == "/api/state":
            self.handle_state()
            return
        if path in APP_PATHS or path.startswith("/en/nonprofit-job/") or path.startswith("/application/"):
            self.send_file(STATIC_ROOT / "index.html")
            return
        self.send_error_page(HTTPStatus.NOT_FOUND, "Page not found", "That local Idealist page does not exist.")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        routes = {
            "/api/reset": self.handle_reset,
            "/api/auth/register": self.handle_register,
            "/api/auth/sign-in": self.handle_sign_in,
            "/api/auth/logout": self.handle_logout,
            "/api/applications/draft": self.handle_draft,
            "/api/applications/submit": self.handle_submit,
            "/data/userdashboard/missing-info": self.handle_profile_completion,
            "/api/boundary": self.handle_boundary,
        }
        handler = routes.get(path)
        if handler:
            handler()
            return
        self.send_error_page(HTTPStatus.NOT_FOUND, "Endpoint not found", "That local Idealist endpoint does not exist.")

    def cookie_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel and re.fullmatch(r"[A-Za-z0-9_-]{32,80}", morsel.value) else None

    def ensure_session(self) -> tuple[str, int | None, bool]:
        token = self.cookie_token()
        with connect(self.db_path) as db:
            row = db.execute("SELECT account_id FROM sessions WHERE token=?", (token,)).fetchone() if token else None
            if row:
                return token or "", row["account_id"], False
            token = secrets.token_urlsafe(32)
            stamp = now_iso()
            db.execute(
                "INSERT INTO sessions(token, account_id, created_at, updated_at) VALUES (?, NULL, ?, ?)",
                (token, stamp, stamp),
            )
        return token, None, True

    def require_session(self) -> tuple[str, int | None]:
        token = self.cookie_token()
        if not token:
            return "", None
        with connect(self.db_path) as db:
            row = db.execute("SELECT account_id FROM sessions WHERE token=?", (token,)).fetchone()
        return (token, row["account_id"]) if row else ("", None)

    def read_body(self, *, allow_form: bool = False) -> tuple[dict[str, Any], str]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > 65_536:
            raise ValueError("Request body must be between 1 and 65536 bytes")
        raw = self.rfile.read(length)
        if content_type == "application/json":
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Request body must be valid JSON") from exc
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value, content_type
        if allow_form and content_type == "application/x-www-form-urlencoded":
            try:
                parsed = parse_qs(raw.decode(), keep_blank_values=True, strict_parsing=True)
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError("Request body must be valid form data") from exc
            return {key: values[-1] for key, values in parsed.items()}, content_type
        raise ValueError("Content-Type must be application/json" + (" or application/x-www-form-urlencoded" if allow_form else ""))

    def journal(self, token: str | None, endpoint: str, payload: dict[str, Any], status: int, content_type: str) -> None:
        with connect(self.db_path) as db:
            db.execute(
                "INSERT INTO request_journal(session_token, method, endpoint, content_type, payload, status, created_at) VALUES (?, 'POST', ?, ?, ?, ?, ?)",
                (token, endpoint, content_type, json.dumps(payload, sort_keys=True), status, now_iso()),
            )

    def handle_bootstrap(self) -> None:
        token, account_id, fresh = self.ensure_session()
        payload = self.state_payload(token, account_id)
        payload.update(
            {
                "jobs": JOBS,
                "targetJobKey": TARGET_JOB_KEY,
                "filters": {"employment": ["Full Time", "Part Time", "Contract"], "sector": ["Nonprofit", "Consulting"]},
                "safety": "Offline replica: no application, email, upload, or identity data is sent to a real employer or third party.",
            }
        )
        self.send_json(payload, cookie=token if fresh else None)

    def handle_search(self, query: dict[str, list[str]]) -> None:
        token, _, fresh = self.ensure_session()
        keywords = compact(query.get("keywords", [""])[-1], 120)
        location = compact(query.get("location", [""])[-1], 120)
        employment = compact(query.get("employment", [""])[-1], 80)
        sector = compact(query.get("sector", [""])[-1], 80)
        if keywords.casefold() == "offline":
            self.send_json({"error": "Search is temporarily unavailable. Please retry."}, status=HTTPStatus.SERVICE_UNAVAILABLE, cookie=token if fresh else None)
            return

        results: list[dict[str, Any]] = []
        for job in JOBS:
            haystack = f"{job['title']} {job['organization']} {job['summary']}".casefold()
            if keywords and keywords.casefold() not in haystack:
                continue
            if location and location.casefold() not in job["location"].casefold():
                continue
            if employment and employment != job["employment"]:
                continue
            if sector and sector != job["sector"]:
                continue
            results.append(job)
        results.sort(key=lambda item: (item["rank"], item["title"]))
        with connect(self.db_path) as db:
            db.execute(
                "INSERT INTO searches(session_token, keywords, location, employment, sector, result_keys, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (token, keywords, location, employment, sector, json.dumps([item["key"] for item in results]), now_iso()),
            )
        self.send_json(
            {"results": results, "count": len(results), "query": {"keywords": keywords, "location": location, "employment": employment, "sector": sector}},
            cookie=token if fresh else None,
        )

    def handle_job(self, key: str) -> None:
        job = next((item for item in JOBS if item["key"] == key), None)
        if not job:
            self.send_json({"error": "Job not found"}, status=HTTPStatus.NOT_FOUND)
            return
        token, _, fresh = self.ensure_session()
        with connect(self.db_path) as db:
            db.execute("INSERT INTO job_views(session_token, job_key, created_at) VALUES (?, ?, ?)", (token, key, now_iso()))
        self.send_json({"job": job}, cookie=token if fresh else None)

    def handle_register(self) -> None:
        token, account_id, fresh = self.ensure_session()
        try:
            body, content_type = self.read_body()
        except ValueError as exc:
            self.journal(token, "/api/auth/register", {}, HTTPStatus.BAD_REQUEST, self.headers.get("Content-Type", ""))
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST, cookie=token if fresh else None)
            return
        if account_id:
            self.journal(token, "/api/auth/register", body, HTTPStatus.CONFLICT, content_type)
            self.send_json({"error": "This session already has an account."}, status=HTTPStatus.CONFLICT)
            return

        expected_keys = {"accountType", "firstName", "lastName", "email", "postalCode", "password", "termsAccepted"}
        if set(body) != expected_keys:
            self.journal(token, "/api/auth/register", body, HTTPStatus.UNPROCESSABLE_ENTITY, content_type)
            self.send_json({"error": "Registration fields are incomplete or malformed."}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        email = compact(body.get("email"), 254).casefold()
        password = body.get("password") if isinstance(body.get("password"), str) else ""
        valid = (
            body.get("accountType") == "APPLICANT"
            and compact(body.get("firstName"), 80) == "Alex"
            and compact(body.get("lastName"), 80) == "Green"
            and email == ACCOUNT_EMAIL
            and compact(body.get("postalCode"), 20).upper() == "M5S 2H7"
            and len(password) >= 10
            and any(c.isupper() for c in password)
            and any(c.islower() for c in password)
            and any(c.isdigit() for c in password)
            and body.get("termsAccepted") is True
        )
        if not valid:
            safe_body = {**body, "password": "[REDACTED]"}
            self.journal(token, "/api/auth/register", safe_body, HTTPStatus.UNPROCESSABLE_ENTITY, content_type)
            self.send_json({"error": "Use the assigned Alex Green profile and a valid password."}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        try:
            with connect(self.db_path) as db:
                stamp = now_iso()
                cursor = db.execute(
                    "INSERT INTO accounts(email, password_hash, first_name, last_name, postal_code, account_type, created_at) VALUES (?, ?, 'Alex', 'Green', 'M5S 2H7', 'APPLICANT', ?)",
                    (ACCOUNT_EMAIL, hash_password(password), stamp),
                )
                new_account_id = int(cursor.lastrowid)
                db.execute(
                    "INSERT INTO profile_resumes(account_id, file_name, source, display_size, summary, created_at) VALUES (?, ?, 'ASSIGNED_PROFILE', '84 KB', ?, ?)",
                    (new_account_id, RESUME_FILE, "Alex Green - senior technology leader; local profile representation", stamp),
                )
        except sqlite3.IntegrityError:
            self.journal(token, "/api/auth/register", {**body, "password": "[REDACTED]"}, HTTPStatus.CONFLICT, content_type)
            self.send_json({"error": "An account already exists for this email. Sign in instead."}, status=HTTPStatus.CONFLICT)
            return
        self.journal(token, "/api/auth/register", {**body, "password": "[REDACTED]"}, HTTPStatus.CREATED, content_type)
        self.send_json({"ok": True, "email": ACCOUNT_EMAIL, "next": "/user/login"}, status=HTTPStatus.CREATED)

    def handle_sign_in(self) -> None:
        token, _, fresh = self.ensure_session()
        try:
            body, content_type = self.read_body(allow_form=True)
        except ValueError as exc:
            self.journal(token, "/api/auth/sign-in", {}, HTTPStatus.BAD_REQUEST, self.headers.get("Content-Type", ""))
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST, cookie=token if fresh else None)
            return
        allowed = {"email", "password", "callbackUrl", "csrfToken", "json"}
        email = compact(body.get("email"), 254).casefold()
        password = body.get("password") if isinstance(body.get("password"), str) else ""
        callback_url = compact(body.get("callbackUrl"), 300)
        csrf_token = compact(body.get("csrfToken"), 200)
        shape_ok = set(body) == allowed and callback_url.startswith("/application/") and len(csrf_token) >= 16 and body.get("json") == "true"
        with connect(self.db_path) as db:
            account = db.execute("SELECT id, password_hash FROM accounts WHERE email=?", (email,)).fetchone()
        safe_payload = {**body, "password": "[REDACTED]"}
        if not shape_ok or not account or not check_password(password, account["password_hash"]):
            self.journal(token, "/api/auth/sign-in", safe_payload, HTTPStatus.UNAUTHORIZED, content_type)
            self.send_json({"error": "Email or password is incorrect. Please try again."}, status=HTTPStatus.UNAUTHORIZED, cookie=token if fresh else None)
            return
        with connect(self.db_path) as db:
            db.execute("UPDATE sessions SET account_id=?, updated_at=? WHERE token=?", (account["id"], now_iso(), token))
        self.journal(token, "/api/auth/sign-in", safe_payload, HTTPStatus.OK, content_type)
        self.send_json({"ok": True, "url": callback_url, "account": {"email": ACCOUNT_EMAIL, "name": "Alex Green"}}, cookie=token if fresh else None)

    def handle_logout(self) -> None:
        token, account_id = self.require_session()
        if not token:
            self.send_json({"error": "No active session"}, status=HTTPStatus.UNAUTHORIZED)
            return
        with connect(self.db_path) as db:
            db.execute("UPDATE sessions SET account_id=NULL, updated_at=? WHERE token=?", (now_iso(), token))
        self.journal(token, "/api/auth/logout", {"accountId": account_id}, HTTPStatus.OK, "application/json")
        self.send_json({"ok": True})

    def validate_application(self, body: dict[str, Any]) -> str | None:
        if set(body) != {"jobKey", "applicant", "resume", "coverLetter", "accuracyConfirmed"}:
            return "Application payload is malformed."
        applicant = body.get("applicant")
        resume = body.get("resume")
        if body.get("jobKey") != TARGET_JOB_KEY:
            return "This application is for the wrong listing."
        if not isinstance(applicant, dict) or applicant != {
            "firstName": "Alex",
            "lastName": "Green",
            "email": ACCOUNT_EMAIL,
            "city": "Toronto",
            "province": "Ontario",
        }:
            return "Use the assigned Alex Green contact profile."
        if not isinstance(resume, dict) or resume != {"source": "ASSIGNED_PROFILE", "fileName": RESUME_FILE}:
            return "Select the assigned profile resume."
        cover = compact(body.get("coverLetter"), 5000)
        if len(cover) < 180 or "Dumbarton Arts & Education" not in cover or "Program Manager" not in cover:
            return "Write a substantive cover letter tailored to Dumbarton Arts & Education and the Program Manager role (at least 180 characters)."
        if body.get("accuracyConfirmed") is not True:
            return "Confirm that the application information is accurate."
        return None

    def handle_draft(self) -> None:
        token, account_id = self.require_session()
        if not account_id:
            self.send_json({"error": "Sign in to save your application.", "authRequired": True}, status=HTTPStatus.UNAUTHORIZED)
            return
        try:
            body, content_type = self.read_body()
        except ValueError as exc:
            self.journal(token, "/api/applications/draft", {}, HTTPStatus.BAD_REQUEST, self.headers.get("Content-Type", ""))
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        error = self.validate_application(body)
        if error:
            self.journal(token, "/api/applications/draft", body, HTTPStatus.UNPROCESSABLE_ENTITY, content_type)
            self.send_json({"error": error}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        with connect(self.db_path) as db:
            existing = db.execute("SELECT revision FROM drafts WHERE session_token=?", (token,)).fetchone()
            revision = (existing["revision"] if existing else 0) + 1
            db.execute(
                "INSERT INTO drafts(session_token, account_id, job_key, revision, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_token) DO UPDATE SET account_id=excluded.account_id, job_key=excluded.job_key, revision=excluded.revision, payload=excluded.payload, updated_at=excluded.updated_at",
                (token, account_id, TARGET_JOB_KEY, revision, json.dumps(body, sort_keys=True), now_iso()),
            )
        self.journal(token, "/api/applications/draft", {**body, "revision": revision}, HTTPStatus.OK, content_type)
        self.send_json({"ok": True, "revision": revision, "savedAt": now_iso()})

    def handle_submit(self) -> None:
        token, account_id = self.require_session()
        if not account_id:
            self.send_json({"error": "Your session expired. Sign in and retry.", "authRequired": True}, status=HTTPStatus.UNAUTHORIZED)
            return
        try:
            body, content_type = self.read_body()
        except ValueError as exc:
            self.journal(token, "/api/applications/submit", {}, HTTPStatus.BAD_REQUEST, self.headers.get("Content-Type", ""))
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        error = self.validate_application(body)
        if error:
            self.journal(token, "/api/applications/submit", body, HTTPStatus.UNPROCESSABLE_ENTITY, content_type)
            self.send_json({"error": error}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        with connect(self.db_path) as db:
            draft = db.execute("SELECT revision, payload FROM drafts WHERE session_token=? AND account_id=?", (token, account_id)).fetchone()
            previous = db.execute("SELECT id FROM applications WHERE account_id=? AND job_key=?", (account_id, TARGET_JOB_KEY)).fetchone()
            if previous:
                self.journal(token, "/api/applications/submit", body, HTTPStatus.CONFLICT, content_type)
                self.send_json({"error": "You already applied to this position.", "applicationId": previous["id"]}, status=HTTPStatus.CONFLICT)
                return
            if not draft or json.loads(draft["payload"]) != body:
                self.journal(token, "/api/applications/submit", body, HTTPStatus.CONFLICT, content_type)
                self.send_json({"error": "Review and save the current application before submitting."}, status=HTTPStatus.CONFLICT)
                return
            stamp = now_iso()
            cursor = db.execute(
                "INSERT INTO applications(account_id, session_token, job_key, payload, status, delivery, created_at) VALUES (?, ?, ?, ?, 'PENDING_PROFILE', 'NONE_LOCAL_REPLICA', ?)",
                (account_id, token, TARGET_JOB_KEY, json.dumps(body, sort_keys=True), stamp),
            )
            application_id = int(cursor.lastrowid)
        self.journal(token, "/api/applications/submit", {**body, "applicationId": application_id}, HTTPStatus.CREATED, content_type)
        self.send_json(
            {
                "ok": True,
                "applicationId": application_id,
                "status": "PENDING_PROFILE",
                "delivery": "NONE_LOCAL_REPLICA",
                "message": "Application staged locally. Complete the local Idealist profile to finish.",
            },
            status=HTTPStatus.CREATED,
        )

    def handle_profile_completion(self) -> None:
        token, account_id = self.require_session()
        endpoint = "/data/userdashboard/missing-info"
        if not account_id:
            self.journal(token or None, endpoint, {}, HTTPStatus.UNAUTHORIZED, self.headers.get("Content-Type", ""))
            self.send_json({"error": "Your session expired. Sign in and retry."}, status=HTTPStatus.UNAUTHORIZED)
            return
        try:
            body, content_type = self.read_body()
        except ValueError as exc:
            self.journal(token, endpoint, {}, HTTPStatus.BAD_REQUEST, self.headers.get("Content-Type", ""))
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if body != EXPECTED_PROFILE:
            self.journal(token, endpoint, body, HTTPStatus.UNPROCESSABLE_ENTITY, content_type)
            self.send_json({"error": "Profile payload does not match the assigned Alex Green profile."}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        with connect(self.db_path) as db:
            application = db.execute(
                "SELECT id, status FROM applications WHERE account_id=? AND job_key=?",
                (account_id, TARGET_JOB_KEY),
            ).fetchone()
            if not application:
                self.journal(token, endpoint, body, HTTPStatus.CONFLICT, content_type)
                self.send_json({"error": "Review and stage the application before completing the profile."}, status=HTTPStatus.CONFLICT)
                return
            if application["status"] == "SUBMITTED_LOCALLY":
                self.journal(token, endpoint, body, HTTPStatus.CONFLICT, content_type)
                self.send_json({"error": "This application was already completed.", "applicationId": application["id"]}, status=HTTPStatus.CONFLICT)
                return
            db.execute("UPDATE accounts SET profile_complete=1 WHERE id=?", (account_id,))
            db.execute("UPDATE applications SET status='SUBMITTED_LOCALLY' WHERE id=?", (application["id"],))
            db.execute("DELETE FROM drafts WHERE session_token=?", (token,))
        self.journal(token, endpoint, body, HTTPStatus.OK, content_type)
        self.send_json(
            {
                "ok": True,
                "profileComplete": True,
                "applicationId": application["id"],
                "applicationStatus": "SUBMITTED_LOCALLY",
                "delivery": "NONE_LOCAL_REPLICA",
                "message": "Profile and application completed locally. No employer was contacted.",
            }
        )

    def handle_boundary(self) -> None:
        token, _, fresh = self.ensure_session()
        try:
            body, content_type = self.read_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST, cookie=token if fresh else None)
            return
        boundary = compact(body.get("boundary"), 80)
        if boundary not in {"post-job", "learning", "resources", "support", "external-employer"}:
            self.send_json({"error": "Unknown local boundary"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        with connect(self.db_path) as db:
            db.execute("INSERT INTO boundary_events(session_token, boundary, created_at) VALUES (?, ?, ?)", (token, boundary, now_iso()))
        self.journal(token, "/api/boundary", {"boundary": boundary}, HTTPStatus.OK, content_type)
        self.send_json({"ok": True, "boundary": boundary, "localOnly": True}, cookie=token if fresh else None)

    def handle_reset(self) -> None:
        token, account_id = self.require_session()
        if not token:
            self.send_json({"ok": True, "reset": False})
            return
        with connect(self.db_path) as db:
            db.execute("DELETE FROM searches WHERE session_token=?", (token,))
            db.execute("DELETE FROM job_views WHERE session_token=?", (token,))
            db.execute("DELETE FROM drafts WHERE session_token=?", (token,))
            db.execute("DELETE FROM applications WHERE session_token=?", (token,))
            db.execute("DELETE FROM boundary_events WHERE session_token=?", (token,))
            db.execute("DELETE FROM request_journal WHERE session_token=?", (token,))
            db.execute("DELETE FROM sessions WHERE token=?", (token,))
            if account_id:
                remaining = db.execute("SELECT 1 FROM sessions WHERE account_id=?", (account_id,)).fetchone()
                if not remaining:
                    db.execute("DELETE FROM profile_resumes WHERE account_id=?", (account_id,))
                    db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        self.send_json({"ok": True, "reset": True}, clear_cookie=True)

    def handle_state(self) -> None:
        token, account_id, fresh = self.ensure_session()
        self.send_json(self.state_payload(token, account_id), cookie=token if fresh else None)

    def state_payload(self, token: str, account_id: int | None) -> dict[str, Any]:
        with connect(self.db_path) as db:
            account = None
            resume = None
            applications: list[dict[str, Any]] = []
            if account_id:
                row = db.execute("SELECT id, email, first_name, last_name, postal_code, account_type, profile_complete, created_at FROM accounts WHERE id=?", (account_id,)).fetchone()
                account = dict(row) if row else None
                resume_row = db.execute("SELECT file_name, source, display_size, summary, created_at FROM profile_resumes WHERE account_id=?", (account_id,)).fetchone()
                resume = dict(resume_row) if resume_row else None
                app_rows = db.execute("SELECT id, job_key, payload, status, delivery, created_at FROM applications WHERE account_id=? ORDER BY id", (account_id,)).fetchall()
                applications = [{**dict(row), "payload": json.loads(row["payload"])} for row in app_rows]
            draft_row = db.execute("SELECT job_key, revision, payload, updated_at FROM drafts WHERE session_token=?", (token,)).fetchone()
            searches = [dict(row) for row in db.execute("SELECT keywords, location, employment, sector, result_keys, created_at FROM searches WHERE session_token=? ORDER BY id", (token,)).fetchall()]
            for item in searches:
                item["result_keys"] = json.loads(item["result_keys"])
            journal_rows = db.execute("SELECT method, endpoint, content_type, payload, status, created_at FROM request_journal WHERE session_token=? ORDER BY id", (token,)).fetchall()
            journal = [{**dict(row), "payload": json.loads(row["payload"])} for row in journal_rows]
            views = [dict(row) for row in db.execute("SELECT job_key, created_at FROM job_views WHERE session_token=? ORDER BY id", (token,)).fetchall()]
        return {
            "authenticated": bool(account),
            "account": account,
            "profileResume": resume,
            "draft": {**dict(draft_row), "payload": json.loads(draft_row["payload"])} if draft_row else None,
            "applications": applications,
            "searches": searches,
            "jobViews": views,
            "journal": journal,
            "session": token[:10],
        }

    def serve_static(self, path: str) -> None:
        relative = unquote(path.removeprefix("/static/"))
        candidate = (STATIC_ROOT / relative).resolve()
        if not str(candidate).startswith(str(STATIC_ROOT.resolve()) + "/") or not candidate.is_file():
            self.send_error_page(HTTPStatus.NOT_FOUND, "Asset not found", "That local asset does not exist.")
            return
        self.send_file(candidate)

    def send_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error_page(HTTPStatus.NOT_FOUND, "File not found", "That local file does not exist.")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.security_headers()
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if path.name == "index.html" else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], *, status: int = HTTPStatus.OK, cookie: str | None = None, clear_cookie: bool = False) -> None:
        data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax")
        if clear_cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(data)

    def send_error_page(self, status: int, title: str, message: str) -> None:
        body = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{status} - {title}</title><link rel='stylesheet' href='/static/styles.css'></head>"
            "<body><div class='offline-banner'><strong>Offline training replica</strong>"
            "<span>No external destination was opened.</span></div><main id='main'>"
            f"<section class='status-page constrained'><div class='status-symbol'>{status}</div>"
            f"<p class='eyebrow'>Local Idealist</p><h1>{title}</h1><p>{message}</p>"
            "<a class='primary-button' href='/'>Return to jobs</a></section></main></body></html>"
        ).encode()
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def security_headers(self) -> None:
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=CANONICAL_PORT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db(args.db)
    server = ThreadingHTTPServer((args.host, args.port), IdealistHandler)
    server.db_path = args.db  # type: ignore[attr-defined]
    print(f"Idealist local replica listening on http://{args.host}:{args.port} (db={args.db})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
