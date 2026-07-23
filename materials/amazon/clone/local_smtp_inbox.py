"""Loopback-only SMTP catcher and browser inbox for the offline clone.

This is a development transport, not an internet mail relay.  It accepts one
recipient per message, keeps a bounded in-memory inbox, and exposes that inbox
only on the same loopback interface.
"""

from __future__ import annotations

import argparse
import hmac
import html
import json
import re
import secrets
import signal
import socket
import socketserver
import threading
import time
from collections import deque
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
MAX_COMMAND_LINE = 1_000
MAX_COMMANDS = 100
MAX_MESSAGE_BYTES = 128 * 1024
MAX_MESSAGES = 100
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_HTTP_BODY = 2_048
CONNECTION_TIMEOUT_SECONDS = 30
MAILBOX_PATTERN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")


@dataclass(frozen=True)
class CapturedMessage:
    message_id: int
    received_at: int
    envelope_from: str
    recipient: str
    subject: str
    body: str
    raw_size: int


class InboxStore:
    def __init__(
        self,
        *,
        max_messages: int = MAX_MESSAGES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> None:
        if max_messages < 1 or max_total_bytes < 1:
            raise ValueError("inbox limits must be positive")
        self.max_messages = max_messages
        self.max_total_bytes = max_total_bytes
        self._messages: deque[CapturedMessage] = deque()
        self._total_bytes = 0
        self._next_id = 1
        self._lock = threading.Lock()

    def add(
        self,
        *,
        envelope_from: str,
        recipient: str,
        subject: str,
        body: str,
        raw_size: int,
    ) -> CapturedMessage:
        if raw_size > self.max_total_bytes:
            raise ValueError("message exceeds inbox capacity")
        with self._lock:
            while self._messages and (
                len(self._messages) >= self.max_messages
                or self._total_bytes + raw_size > self.max_total_bytes
            ):
                removed = self._messages.popleft()
                self._total_bytes -= removed.raw_size
            message = CapturedMessage(
                message_id=self._next_id,
                received_at=int(time.time()),
                envelope_from=envelope_from,
                recipient=recipient,
                subject=subject,
                body=body,
                raw_size=raw_size,
            )
            self._next_id += 1
            self._messages.append(message)
            self._total_bytes += raw_size
            return message

    def list_newest(self) -> list[CapturedMessage]:
        with self._lock:
            return list(reversed(self._messages))

    def get(self, message_id: int) -> CapturedMessage | None:
        with self._lock:
            return next(
                (message for message in self._messages if message.message_id == message_id),
                None,
            )

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()
            self._total_bytes = 0


def _safe_envelope_mailbox(value: str) -> str | None:
    if len(value) > 320 or MAILBOX_PATTERN.fullmatch(value) is None:
        return None
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return None
    return value


def _has_forbidden_controls(value: str, *, allow_line_breaks: bool) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_line_breaks else set()
    return any((ord(character) < 32 or ord(character) == 127) and character not in allowed for character in value)


def _parse_message(
    raw: bytes,
    envelope_from: str,
    recipient: str,
) -> tuple[str, str] | None:
    if b"\x00" in raw or len(raw) > MAX_MESSAGE_BYTES:
        return None
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if message.defects or message.is_multipart():
        return None
    for required in ("From", "To", "Subject"):
        if len(message.get_all(required, [])) != 1:
            return None
    if any(message.get_all(name, []) for name in ("Bcc", "Cc", "Resent-To", "Resent-From")):
        return None
    from_addresses = getaddresses([str(message["From"])])
    to_addresses = getaddresses([str(message["To"])])
    if len(from_addresses) != 1 or len(to_addresses) != 1:
        return None
    if from_addresses[0][1].casefold() != envelope_from.casefold():
        return None
    if to_addresses[0][1].casefold() != recipient.casefold():
        return None
    subject = str(message["Subject"])
    if (
        not subject
        or len(subject.encode("utf-8")) > 512
        or _has_forbidden_controls(subject, allow_line_breaks=False)
    ):
        return None
    if message.get_content_type() != "text/plain":
        return None
    try:
        body = message.get_content()
    except (LookupError, UnicodeError, ValueError):
        return None
    if not isinstance(body, str) or _has_forbidden_controls(body, allow_line_breaks=True):
        return None
    if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
        return None
    return subject, body


class LocalSMTPHandler(socketserver.StreamRequestHandler):
    server: "LocalSMTPServer"

    def _reply(self, line: str) -> None:
        self.wfile.write(line.encode("ascii") + b"\r\n")
        self.wfile.flush()

    def _line(self) -> bytes | None:
        line = self.rfile.readline(MAX_COMMAND_LINE + 3)
        if not line:
            return None
        if len(line) > MAX_COMMAND_LINE + 2 or not line.endswith(b"\r\n"):
            self._reply("500 command line too long or malformed")
            return None
        if b"\x00" in line:
            self._reply("500 NUL is forbidden")
            return None
        return line[:-2]

    def _data(self) -> tuple[bytes | None, bool]:
        parts: list[bytes] = []
        total = 0
        too_large = False
        while True:
            line = self.rfile.readline(MAX_COMMAND_LINE + 3)
            if not line:
                return None, False
            if len(line) > MAX_COMMAND_LINE + 2 or not line.endswith(b"\r\n"):
                return None, False
            if line == b".\r\n":
                break
            if line.startswith(b".."):
                line = line[1:]
            total += len(line)
            if total > MAX_MESSAGE_BYTES:
                too_large = True
            elif not too_large:
                parts.append(line)
        return (None if too_large else b"".join(parts)), too_large

    def handle(self) -> None:
        self.connection.settimeout(CONNECTION_TIMEOUT_SECONDS)
        self._reply("220 amazon-clone.local ESMTP local-capture")
        greeted = False
        envelope_from: str | None = None
        recipient: str | None = None
        commands = 0
        while commands < MAX_COMMANDS:
            try:
                raw_line = self._line()
            except (OSError, TimeoutError):
                return
            if raw_line is None:
                return
            commands += 1
            try:
                text = raw_line.decode("ascii")
            except UnicodeDecodeError:
                self._reply("500 commands must be ASCII")
                continue
            command, _, argument = text.partition(" ")
            command = command.upper()
            argument = argument.strip()

            if command in {"EHLO", "HELO"}:
                if not argument:
                    self._reply("501 hostname required")
                    continue
                greeted = True
                envelope_from = None
                recipient = None
                if command == "EHLO":
                    self._reply("250-amazon-clone.local")
                    self._reply(f"250 SIZE {MAX_MESSAGE_BYTES}")
                else:
                    self._reply("250 amazon-clone.local")
                continue
            if command == "NOOP":
                self._reply("250 OK")
                continue
            if command == "RSET":
                envelope_from = None
                recipient = None
                self._reply("250 reset")
                continue
            if command == "QUIT":
                self._reply("221 closing connection")
                return
            if command in {"AUTH", "STARTTLS", "VRFY", "EXPN", "ETRN"}:
                self._reply("502 command disabled in local capture mode")
                continue
            if command == "MAIL":
                if not greeted:
                    self._reply("503 send EHLO or HELO first")
                    continue
                match = re.fullmatch(r"FROM:<([^<>]+)>(?:\s+SIZE=(\d+))?", argument, re.IGNORECASE)
                mailbox = _safe_envelope_mailbox(match.group(1)) if match else None
                if mailbox is None:
                    self._reply("501 invalid sender")
                    continue
                if match and match.group(2) and int(match.group(2)) > MAX_MESSAGE_BYTES:
                    self._reply("552 message too large")
                    continue
                envelope_from = mailbox
                recipient = None
                self._reply("250 sender accepted")
                continue
            if command == "RCPT":
                if envelope_from is None:
                    self._reply("503 send MAIL first")
                    continue
                if recipient is not None:
                    self._reply("452 only one recipient is accepted")
                    continue
                match = re.fullmatch(r"TO:<([^<>]+)>", argument, re.IGNORECASE)
                mailbox = _safe_envelope_mailbox(match.group(1)) if match else None
                if mailbox is None:
                    self._reply("501 invalid recipient")
                    continue
                recipient = mailbox
                self._reply("250 recipient captured locally")
                continue
            if command == "DATA":
                if argument or envelope_from is None or recipient is None:
                    self._reply("503 sender and recipient required before DATA")
                    continue
                self._reply("354 end with <CRLF>.<CRLF>")
                try:
                    raw, too_large = self._data()
                except (OSError, TimeoutError):
                    return
                if raw is None:
                    if too_large:
                        self._reply("552 message too large")
                        envelope_from = None
                        recipient = None
                        continue
                    return
                parsed = _parse_message(raw, envelope_from, recipient)
                if parsed is None:
                    self._reply("554 malformed or unsafe message")
                else:
                    subject, body = parsed
                    try:
                        captured = self.server.inbox.add(
                            envelope_from=envelope_from,
                            recipient=recipient,
                            subject=subject,
                            body=body,
                            raw_size=len(raw),
                        )
                    except ValueError:
                        self._reply("452 inbox capacity exceeded")
                    else:
                        self._reply(f"250 captured as local message {captured.message_id}")
                envelope_from = None
                recipient = None
                continue
            self._reply("502 command not implemented")
        self._reply("421 command limit exceeded")


class LocalSMTPServer(socketserver.ThreadingTCPServer):
    # Fail closed when another inbox already owns the SMTP endpoint.  On
    # Windows SO_REUSEADDR can otherwise allow two listeners for one port and
    # route verification mail to the wrong browser inbox.
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], inbox: InboxStore) -> None:
        self.inbox = inbox
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(address, LocalSMTPHandler)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"421 local SMTP connection limit reached\r\n")
            finally:
                request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class LocalSMTPServerV6(LocalSMTPServer):
    address_family = socket.AF_INET6


class InboxHTTPHandler(BaseHTTPRequestHandler):
    server: "InboxHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args

    def _host_is_local(self) -> bool:
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("//" + host)
            return parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == self.server.server_address[1]
        except ValueError:
            return False

    def _headers(self, status: int, body: bytes, content_type: str, *, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _html(self, status: int, content: str, *, location: str | None = None) -> None:
        self._headers(status, content.encode("utf-8"), "text/html; charset=utf-8", location=location)

    def _page(self, title: str, content: str) -> str:
        return (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title></head><body><main><h1>{html.escape(title)}</h1>"
            '<p><strong>Local SMTP inbox.</strong> Messages never leave this computer.</p>'
            f"{content}</main></body></html>"
        )

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_is_local():
            self._headers(421, b"", "text/plain; charset=utf-8")
            return
        path = urlsplit(self.path).path
        if path == "/healthz":
            payload = json.dumps(
                {"ok": True, "mode": "LOCAL_SMTP_CAPTURE", "messages": len(self.server.inbox.list_newest())},
                separators=(",", ":"),
            ).encode("utf-8")
            self._headers(200, payload, "application/json")
            return
        if path == "/":
            rows = []
            for message in self.server.inbox.list_newest():
                rows.append(
                    "<li>"
                    f'<a href="/message/{message.message_id}">{html.escape(message.subject)}</a> '
                    f"to {html.escape(message.recipient)}"
                    "</li>"
                )
            listing = "<ol>" + "".join(rows) + "</ol>" if rows else "<p>No messages yet.</p>"
            clear = (
                '<form method="post" action="/clear">'
                f'<input type="hidden" name="csrf" value="{html.escape(self.server.csrf_token, quote=True)}">'
                '<button type="submit">Clear inbox</button></form>'
            )
            self._html(200, self._page("Amazon Clone local inbox", listing + clear))
            return
        match = re.fullmatch(r"/message/([1-9][0-9]*)", path)
        if match:
            message = self.server.inbox.get(int(match.group(1)))
            if message is None:
                self._html(404, self._page("Message not found", '<p><a href="/">Back to inbox</a></p>'))
                return
            content = (
                f"<p>To: {html.escape(message.recipient)}</p>"
                f"<p>Subject: {html.escape(message.subject)}</p>"
                f"<pre>{html.escape(message.body)}</pre>"
                '<p><a href="/">Back to inbox</a></p>'
            )
            self._html(200, self._page("Captured message", content))
            return
        self._html(404, self._page("Not found", '<p><a href="/">Back to inbox</a></p>'))

    def do_POST(self) -> None:
        if not self._host_is_local() or urlsplit(self.path).path != "/clear":
            self._headers(404, b"", "text/plain; charset=utf-8")
            return
        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlsplit(origin)
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed_origin.port != self.server.server_address[1]
            ):
                self._headers(403, b"", "text/plain; charset=utf-8")
                return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_HTTP_BODY:
            self._headers(413, b"", "text/plain; charset=utf-8")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            self._headers(415, b"", "text/plain; charset=utf-8")
            return
        try:
            values = parse_qs(self.rfile.read(length).decode("utf-8"), strict_parsing=True)
        except (UnicodeDecodeError, ValueError):
            values = {}
        tokens = values.get("csrf", [])
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self.server.csrf_token):
            self._headers(403, b"", "text/plain; charset=utf-8")
            return
        self.server.inbox.clear()
        self._headers(303, b"", "text/plain; charset=utf-8", location="/")


class InboxHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], inbox: InboxStore) -> None:
        self.inbox = inbox
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, InboxHTTPHandler)


class InboxHTTPServerV6(InboxHTTPServer):
    address_family = socket.AF_INET6


def build_servers(
    *,
    smtp_host: str = "127.0.0.1",
    smtp_port: int = 18125,
    web_host: str = "127.0.0.1",
    web_port: int = 8155,
    inbox: InboxStore | None = None,
) -> tuple[LocalSMTPServer, InboxHTTPServer, InboxStore]:
    if smtp_host not in LOOPBACK_HOSTS or web_host not in LOOPBACK_HOSTS:
        raise ValueError("local SMTP and inbox HTTP must bind to an explicit loopback IP")
    if not 0 <= smtp_port <= 65535 or not 0 <= web_port <= 65535:
        raise ValueError("ports must be between 0 and 65535")
    store = inbox or InboxStore()
    smtp_class = LocalSMTPServerV6 if smtp_host == "::1" else LocalSMTPServer
    web_class = InboxHTTPServerV6 if web_host == "::1" else InboxHTTPServer
    smtp_server = smtp_class((smtp_host, smtp_port), store)
    try:
        web_server = web_class((web_host, web_port), store)
    except Exception:
        smtp_server.server_close()
        raise
    return smtp_server, web_server, store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the loopback-only Amazon Clone SMTP inbox")
    parser.add_argument("--smtp-host", default="127.0.0.1", choices=sorted(LOOPBACK_HOSTS))
    parser.add_argument("--smtp-port", type=int, default=18125)
    parser.add_argument("--web-host", default="127.0.0.1", choices=sorted(LOOPBACK_HOSTS))
    parser.add_argument("--web-port", type=int, default=8155)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    smtp_server, web_server, _ = build_servers(
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        web_host=args.web_host,
        web_port=args.web_port,
    )
    smtp_thread = threading.Thread(target=smtp_server.serve_forever, name="local-smtp", daemon=True)
    smtp_thread.start()
    stopping = threading.Event()

    def stop(*_: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=web_server.shutdown, daemon=True).start()
        threading.Thread(target=smtp_server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop)
    print(
        json.dumps(
            {
                "event": "amazon-clone-local-smtp-started",
                "smtp": f"{args.smtp_host}:{smtp_server.server_address[1]}",
                "inbox": f"http://{args.web_host}:{web_server.server_address[1]}/",
                "delivery_boundary": "LOCAL_CAPTURE_ONLY",
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        web_server.serve_forever()
    finally:
        web_server.server_close()
        smtp_server.shutdown()
        smtp_server.server_close()
        smtp_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
