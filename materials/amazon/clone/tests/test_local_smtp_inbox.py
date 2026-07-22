from __future__ import annotations

import http.client
import re
import smtplib
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_smtp_inbox import build_servers  # noqa: E402
from mail_transport import SMTPConfig, send_smtp_message  # noqa: E402
from server import PublicHandler, ReusableThreadingHTTPServer  # noqa: E402
from store import Store  # noqa: E402


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class LocalSMTPInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.smtp_server, self.web_server, self.inbox = build_servers(
            smtp_port=0,
            web_port=0,
        )
        self.threads = [
            threading.Thread(target=self.smtp_server.serve_forever, daemon=True),
            threading.Thread(target=self.web_server.serve_forever, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def tearDown(self) -> None:
        self.smtp_server.shutdown()
        self.web_server.shutdown()
        self.smtp_server.server_close()
        self.web_server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def config(self) -> SMTPConfig:
        return SMTPConfig(
            host="127.0.0.1",
            port=int(self.smtp_server.server_address[1]),
            security="plain",
            sender="Amazon Clone <no-reply@amazon-clone.local>",
            timeout_seconds=3,
        )

    def test_real_smtp_socket_captures_plain_text_message_and_dot_stuff(self) -> None:
        send_smtp_message(
            self.config(),
            recipient="reader@example.test",
            subject="Your verification code",
            body="Code: 123456\n.leading dot stays in the body",
        )
        messages = self.inbox.list_newest()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].recipient, "reader@example.test")
        self.assertEqual(messages[0].subject, "Your verification code")
        self.assertIn("123456", messages[0].body)
        self.assertIn(".leading dot", messages[0].body)

    def test_protocol_rejects_auth_out_of_order_and_multiple_recipients(self) -> None:
        with smtplib.SMTP(
            "127.0.0.1", int(self.smtp_server.server_address[1]), timeout=3
        ) as client:
            code, _ = client.docmd("AUTH", "PLAIN ignored")
            self.assertEqual(code, 502)
            client.ehlo()
            code, _ = client.docmd("RCPT", "TO:<first@example.test>")
            self.assertEqual(code, 503)
            self.assertEqual(client.mail("sender@example.test")[0], 250)
            self.assertEqual(client.rcpt("first@example.test")[0], 250)
            self.assertEqual(client.rcpt("second@example.test")[0], 452)
        self.assertEqual(self.inbox.list_newest(), [])

    def test_web_inbox_escapes_content_and_csrf_protects_clear(self) -> None:
        message = self.inbox.add(
            envelope_from="sender@example.test",
            recipient="reader@example.test",
            subject="<script>alert(1)</script>",
            body="<img src=x onerror=alert(2)>",
            raw_size=100,
        )
        host, port = self.web_server.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request("GET", f"/message/{message.message_id}")
        response = connection.getresponse()
        page = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", page)

        connection.request(
            "POST",
            "/clear",
            body="csrf=wrong",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        rejected = connection.getresponse()
        rejected.read()
        self.assertEqual(rejected.status, 403)
        self.assertEqual(len(self.inbox.list_newest()), 1)

        body = urlencode({"csrf": self.web_server.csrf_token})
        connection.request(
            "POST",
            "/clear",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        cleared = connection.getresponse()
        cleared.read()
        connection.close()
        self.assertEqual(cleared.status, 303)
        self.assertEqual(self.inbox.list_newest(), [])

    def test_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit loopback"):
            build_servers(smtp_host="0.0.0.0", smtp_port=0, web_port=0)


class RegistrationThroughLocalSMTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.smtp_server, self.web_server, self.inbox = build_servers(
            smtp_port=0,
            web_port=0,
        )
        self.smtp_thread = threading.Thread(
            target=self.smtp_server.serve_forever, daemon=True
        )
        self.web_thread = threading.Thread(
            target=self.web_server.serve_forever, daemon=True
        )
        self.smtp_thread.start()
        self.web_thread.start()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        QuietPublicHandler.store = self.store
        QuietPublicHandler.smtp_config = SMTPConfig(
            host="127.0.0.1",
            port=int(self.smtp_server.server_address[1]),
            security="plain",
            sender="Amazon Clone <no-reply@amazon-clone.local>",
            timeout_seconds=3,
        )
        QuietPublicHandler.local_inbox_url = (
            f"http://127.0.0.1:{self.web_server.server_address[1]}/"
        )
        self.public_server = ReusableThreadingHTTPServer(
            ("127.0.0.1", 0), QuietPublicHandler
        )
        self.public_thread = threading.Thread(
            target=self.public_server.serve_forever, daemon=True
        )
        self.public_thread.start()

    def tearDown(self) -> None:
        self.public_server.shutdown()
        self.smtp_server.shutdown()
        self.web_server.shutdown()
        self.public_server.server_close()
        self.smtp_server.server_close()
        self.web_server.server_close()
        self.public_thread.join(timeout=2)
        self.smtp_thread.join(timeout=2)
        self.web_thread.join(timeout=2)
        QuietPublicHandler.smtp_config = None
        QuietPublicHandler.local_inbox_url = None
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: str = "",
        cookie: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        host, port = self.public_server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=5)
        headers: dict[str, str] = {"Origin": f"http://{host}:{port}"}
        if body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, body=body.encode("utf-8"), headers=headers)
        response = connection.getresponse()
        payload = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, payload

    def test_registration_receives_code_over_tcp_and_verifies_account(self) -> None:
        registration = urlencode(
            {
                "customerName": "Local SMTP User",
                "email": "local-smtp-user@example.test",
                "password": "safe-password-123",
                "passwordCheck": "safe-password-123",
                "openid.return_to": "/",
            }
        )
        status, headers, _ = self.request("POST", "/ap/register", body=registration)
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("location"), "/ap/cvf/verify?purpose=registration")
        cookie = headers["set-cookie"].split(";", 1)[0]

        status, _, verification_page = self.request(
            "GET",
            "/ap/cvf/verify?purpose=registration",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Open local SMTP inbox", verification_page)
        self.assertIn(
            f"http://127.0.0.1:{self.web_server.server_address[1]}/".encode(),
            verification_page,
        )

        deadline = time.monotonic() + 3
        messages = []
        while time.monotonic() < deadline:
            messages = self.inbox.list_newest()
            if messages:
                break
            time.sleep(0.02)
        self.assertEqual(len(messages), 1)
        match = re.search(r"\b([0-9]{6})\b", messages[0].body)
        self.assertIsNotNone(match)
        code = match.group(1) if match else ""

        status, headers, _ = self.request(
            "POST",
            "/ap/cvf/verify?purpose=registration",
            body=urlencode({"code": code}),
            cookie=cookie,
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("location"), "/")
        self.assertTrue(self.store.account_exists("local-smtp-user@example.test"))


if __name__ == "__main__":
    unittest.main()
