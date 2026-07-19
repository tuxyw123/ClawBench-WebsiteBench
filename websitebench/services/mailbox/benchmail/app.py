"""Delivery, browser inbox, and private administration applications."""

from __future__ import annotations

import hmac
import html
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse


URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class Mailbox:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS messages (
                  id TEXT PRIMARY KEY,
                  recipient TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  body_text TEXT NOT NULL,
                  body_html TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS message_recipient_time
                  ON messages(recipient, created_at DESC);
                """
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def normalize_recipient(value: str) -> str:
        return value.strip().casefold()

    def deliver(self, *, recipient: str, subject: str, text: str, body_html: str | None) -> dict[str, Any]:
        normalized = self.normalize_recipient(recipient)
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("invalid recipient")
        if not subject.strip() or len(subject) > 300:
            raise ValueError("invalid subject")
        if not text.strip() or len(text) > 100_000:
            raise ValueError("invalid text")
        if body_html is not None and len(body_html) > 100_000:
            raise ValueError("invalid html")
        message_id = f"msg_{secrets.token_hex(12)}"
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, normalized, subject.strip(), text, body_html, created_at),
            )
            connection.commit()
        return {"id": message_id, "recipient": normalized, "subject": subject.strip(), "created_at": created_at}

    def inbox(self, recipient: str) -> list[dict[str, Any]]:
        normalized = self.normalize_recipient(recipient)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE recipient = ? ORDER BY created_at DESC, id DESC",
                (normalized,),
            ).fetchall()
        return [self.serialize(row) for row in rows]

    def message(self, message_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self.serialize(row) if row else None

    @staticmethod
    def serialize(row: sqlite3.Row) -> dict[str, Any]:
        text = str(row["body_text"])
        return {
            "id": row["id"],
            "to": row["recipient"],
            "subject": row["subject"],
            "text": text,
            "links": URL_PATTERN.findall(text),
            "created_at": row["created_at"],
        }

    def reset(self) -> int:
        with self.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            connection.execute("DELETE FROM messages")
            connection.commit()
        return int(count)

    def count(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · Test Mailbox</title><style>
    :root{{font-family:Inter,system-ui,sans-serif;color:#1d2925;background:#edf2ef}}*{{box-sizing:border-box}}body{{margin:0}}header{{background:#173f36;color:white;padding:18px 0}}.shell{{width:min(920px,calc(100% - 30px));margin:auto}}a{{color:#175b4c}}main{{padding:40px 0}}.card{{background:white;border:1px solid #d4ddd8;border-radius:16px;padding:24px;margin:16px 0;box-shadow:0 10px 25px #173f3612}}h1{{margin:0;font-family:Georgia,serif}}h2{{margin:0 0 8px}}label{{font-weight:700}}input{{width:100%;padding:12px;border:1px solid #aebbb5;border-radius:8px;margin:7px 0 12px}}button,.button{{display:inline-block;background:#173f36;color:white;border:0;border-radius:999px;padding:11px 20px;text-decoration:none;font-weight:700}}pre{{white-space:pre-wrap;background:#f6f7f4;padding:18px;border-radius:10px}}.meta{{color:#65706c;font-size:.86rem}}.empty{{text-align:center;padding:60px}}ul{{padding-left:20px}}
    </style></head><body><header><div class="shell"><strong>✦ WebsiteBench Test Mailbox</strong></div></header><main class="shell">{body}</main></body></html>"""


def create_public_app(mailbox: Mailbox, delivery_token: str) -> FastAPI:
    app = FastAPI(title="WebsiteBench mailbox", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        mailbox.count()
        return {"status": "ok"}

    @app.post("/api/v1/messages", status_code=202)
    async def deliver(request: Request, authorization: str | None = Header(None)) -> dict[str, Any]:
        expected = f"Bearer {delivery_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=404)
        body = await request.json()
        allowed = {"schema_version", "to", "subject", "text", "html"}
        if not isinstance(body, dict) or not set(body) <= allowed or body.get("schema_version") != 1:
            raise HTTPException(status_code=422, detail="invalid message payload")
        try:
            message = mailbox.deliver(
                recipient=str(body.get("to", "")),
                subject=str(body.get("subject", "")),
                text=str(body.get("text", "")),
                body_html=str(body["html"]) if body.get("html") is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"schema_version": 1, "status": "accepted", **message}

    @app.get("/api/v1/inbox")
    async def inbox_api(recipient: str = Query("")) -> dict[str, Any]:
        return {"schema_version": 1, "recipient": mailbox.normalize_recipient(recipient), "messages": mailbox.inbox(recipient)}

    @app.get("/api/v1/messages/{message_id}")
    async def message_api(message_id: str) -> dict[str, Any]:
        message = mailbox.message(message_id)
        if message is None:
            raise HTTPException(status_code=404)
        return {"schema_version": 1, **message}

    @app.get("/", response_class=HTMLResponse)
    async def home(recipient: str = "") -> HTMLResponse:
        form = f"""<h1>Local test mailbox</h1><p>No message leaves this benchmark.</p><div class="card"><form action="/inbox" method="get"><label for="recipient">Recipient email</label><input id="recipient" name="recipient" type="email" value="{html.escape(recipient)}" placeholder="shopper@example.test" required><button>Open inbox</button></form></div>"""
        return HTMLResponse(_page("Mailbox", form))

    @app.get("/inbox", response_class=HTMLResponse)
    async def inbox_page(recipient: str = "") -> HTMLResponse:
        messages = mailbox.inbox(recipient)
        cards = "".join(
            f"""<article class="card"><p class="meta">{html.escape(message['created_at'])}</p><h2><a href="/messages/{message['id']}">{html.escape(message['subject'])}</a></h2><p>To: {html.escape(message['to'])}</p></article>"""
            for message in messages
        )
        if not cards:
            cards = '<div class="card empty"><h2>No messages yet</h2><p>Check the address or trigger a verification/reset email.</p></div>'
        body = f"""<a href="/">← Another inbox</a><h1>Inbox</h1><p>{html.escape(mailbox.normalize_recipient(recipient))}</p>{cards}"""
        return HTMLResponse(_page("Inbox", body))

    @app.get("/messages/{message_id}", response_class=HTMLResponse)
    async def message_page(message_id: str) -> HTMLResponse:
        message = mailbox.message(message_id)
        if message is None:
            raise HTTPException(status_code=404)
        links = "".join(
            f'<li><a class="button" href="{html.escape(link, quote=True)}">Open link</a></li>'
            for link in message["links"]
        )
        body = f"""<a href="/inbox?recipient={html.escape(message['to'], quote=True)}">← Inbox</a><article class="card"><p class="meta">To: {html.escape(message['to'])} · {html.escape(message['created_at'])}</p><h1>{html.escape(message['subject'])}</h1><pre>{html.escape(message['text'])}</pre>{f'<h2>Links</h2><ul>{links}</ul>' if links else ''}</article>"""
        return HTMLResponse(_page(message["subject"], body))

    return app


def create_admin_app(mailbox: Mailbox, admin_token: str) -> FastAPI:
    app = FastAPI(title="WebsiteBench mailbox admin", docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(value: str | None) -> None:
        if not value or not hmac.compare_digest(value, admin_token):
            raise HTTPException(status_code=404)

    @app.get("/__bench/health")
    async def health(x_bench_admin_token: str | None = Header(None)) -> dict[str, Any]:
        authorize(x_bench_admin_token)
        return {"schema_version": 1, "status": "ok"}

    @app.post("/__bench/reset")
    async def reset(x_bench_admin_token: str | None = Header(None)) -> dict[str, Any]:
        authorize(x_bench_admin_token)
        removed = mailbox.reset()
        return {"schema_version": 1, "status": "reset", "removed": removed}

    @app.get("/__bench/state")
    async def state(x_bench_admin_token: str | None = Header(None)) -> dict[str, Any]:
        authorize(x_bench_admin_token)
        return {"schema_version": 1, "messages": mailbox.count()}

    return app


def from_environment() -> tuple[FastAPI, FastAPI]:
    data_dir = Path(os.environ.get("MAILBOX_DATA_DIR", "/data"))
    mailbox = Mailbox(data_dir / "mailbox.sqlite3")
    delivery_token = os.environ.get("MAILBOX_DELIVERY_TOKEN", "development-delivery-token")
    admin_token = os.environ.get("BENCH_ADMIN_TOKEN", "development-admin-token")
    return create_public_app(mailbox, delivery_token), create_admin_app(mailbox, admin_token)


def create_delivery_app(mailbox: Mailbox, delivery_token: str) -> FastAPI:
    app = create_public_app(mailbox, delivery_token)

    @app.middleware("http")
    async def delivery_only(request: Request, call_next: Any) -> Response:
        allowed = request.url.path == "/healthz" or (
            request.method == "POST" and request.url.path == "/api/v1/messages"
        )
        if not allowed:
            return Response(status_code=404)
        return await call_next(request)

    return app


def delivery_from_environment() -> FastAPI:
    data_dir = Path(os.environ.get("MAILBOX_DATA_DIR", "/data"))
    mailbox = Mailbox(data_dir / "mailbox.sqlite3")
    delivery_token = os.environ.get("MAILBOX_DELIVERY_TOKEN", "development-delivery-token")
    return create_delivery_app(mailbox, delivery_token)
