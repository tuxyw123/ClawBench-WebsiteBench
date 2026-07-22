from __future__ import annotations

import http.client
import json
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as server_module  # noqa: E402
from mail_transport import SMTPConfig  # noqa: E402
from server import (  # noqa: E402
    AdminHandler,
    PublicHandler,
    ReusableThreadingHTTPServer,
    digest,
    resolve_admin_token,
)
from store import MAIL_SMTP_PENDING, PASSWORD_SCHEME, Store  # noqa: E402


class QuietPublicHandler(PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class AuthBackendTests(unittest.TestCase):
    password = "Correct-Horse-921"
    email = "buyer@example.test"

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        QuietPublicHandler.store = self.store
        QuietPublicHandler.smtp_config = None
        self.server = ReusableThreadingHTTPServer(("127.0.0.1", 0), QuietPublicHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def test_external_admin_binding_requires_an_explicit_strong_token(self) -> None:
        self.assertEqual(resolve_admin_token("127.0.0.1", None), "local-amazon-bench")
        self.assertEqual(resolve_admin_token("::1", None), "local-amazon-bench")
        for token in (None, "local-amazon-bench", "too-short"):
            with self.assertRaises(ValueError):
                resolve_admin_token("0.0.0.0", token)
        strong = "a-strong-synthetic-admin-token-1234567890"
        self.assertEqual(resolve_admin_token("0.0.0.0", strong), strong)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        fields: dict[str, str] | None = None,
        cookie: str = "",
    ) -> tuple[int, dict[str, list[str]], bytes]:
        body = urlencode(fields).encode("utf-8") if fields is not None else None
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Content-Length"] = str(len(body))
        if method == "POST":
            headers["Origin"] = f"http://{self.host}:{self.port}"
        if cookie:
            headers["Cookie"] = cookie
        connection = http.client.HTTPConnection(self.host, self.port, timeout=8)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_headers: dict[str, list[str]] = {}
        for name, value in response.getheaders():
            response_headers.setdefault(name.lower(), []).append(value)
        payload = response.read()
        connection.close()
        return response.status, response_headers, payload

    @staticmethod
    def cookie_from(headers: dict[str, list[str]]) -> str:
        return headers["set-cookie"][-1].split(";", 1)[0]

    @staticmethod
    def session_digest(cookie: str) -> str:
        return digest(cookie.split("=", 1)[1])

    def anonymous_cookie(self) -> str:
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        return self.cookie_from(headers)

    def wait_for_delivery_status(
        self,
        reader: Callable[[], list[dict[str, object]]],
        expected: str = "SMTP_SENT",
    ) -> dict[str, object]:
        deadline = time.monotonic() + 3
        messages: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            messages = reader()
            if messages and messages[0]["status"] == expected:
                return messages[0]
            time.sleep(0.01)
        self.fail(f"mail delivery did not reach {expected}: {messages!r}")

    def release_auth_mail_cooldown(self, purpose: str) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE auth_mail_rate_limits SET last_sent_at=0 WHERE purpose=?",
                (purpose,),
            )

    def register(
        self,
        cookie: str,
        *,
        email: str | None = None,
        return_to: str | None = None,
    ) -> tuple[int, dict[str, list[str]], bytes]:
        fields = {
            "customerName": "Example Buyer",
            "email": email or self.email,
            "password": self.password,
            "passwordCheck": self.password,
        }
        if return_to is not None:
            fields["openid.return_to"] = return_to
        response = self.request("POST", "/ap/register", fields=fields, cookie=cookie)
        if response[0] != 303 or response[1].get("location") != [
            "/ap/cvf/verify?purpose=registration"
        ]:
            return response
        messages = self.store.registration_outbox(self.session_digest(cookie))
        self.assertEqual(len(messages), 1)
        return self.request(
            "POST",
            "/ap/cvf/verify",
            fields={"code": messages[0]["verification_code"]},
            cookie=cookie,
        )

    def start_registration(
        self,
        cookie: str,
        *,
        email: str | None = None,
        return_to: str | None = None,
    ) -> tuple[int, dict[str, list[str]], bytes]:
        fields = {
            "customerName": "Example Buyer",
            "email": email or self.email,
            "password": self.password,
            "passwordCheck": self.password,
        }
        if return_to is not None:
            fields["openid.return_to"] = return_to
        return self.request("POST", "/ap/register", fields=fields, cookie=cookie)

    def begin_signin(
        self, cookie: str, email: str, return_to: str | None = None
    ) -> tuple[int, dict[str, list[str]], bytes]:
        fields = {"email": email}
        if return_to is not None:
            fields["openid.return_to"] = return_to
        return self.request("POST", "/ap/signin", fields=fields, cookie=cookie)

    def start_password_reset(
        self, cookie: str, email: str, return_to: str | None = None
    ) -> tuple[int, dict[str, list[str]], bytes]:
        fields = {"email": email}
        if return_to is not None:
            fields["openid.return_to"] = return_to
        return self.request(
            "POST", "/ap/forgotpassword", fields=fields, cookie=cookie
        )

    def test_registration_hashes_password_rotates_session_and_preserves_cart(self) -> None:
        cookie = self.anonymous_cookie()
        old_session_digest = self.session_digest(cookie)
        self.store.ensure_session(old_session_digest)
        status, headers, body = self.register(
            cookie,
            email="  Buyer@Example.Test  ",
            return_to="/gp/css/order-history",
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/gp/css/order-history"], b""))
        rotated_cookie = self.cookie_from(headers)
        new_session_digest = self.session_digest(rotated_cookie)
        self.assertNotEqual(new_session_digest, old_session_digest)
        account = self.store.account_for_session(new_session_digest)
        self.assertIsNotNone(account)
        assert account is not None
        self.assertEqual(account["email_normalized"], self.email)
        self.assertIsNone(self.store.account_for_session(old_session_digest))
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT password_salt,password_hash,password_scheme FROM accounts"
            ).fetchone()
        assert row is not None
        self.assertEqual(row["password_scheme"], PASSWORD_SCHEME)
        self.assertEqual(len(bytes(row["password_salt"])), 32)
        self.assertEqual(len(bytes(row["password_hash"])), 32)
        self.assertNotIn(self.password.encode("utf-8"), bytes(row["password_hash"]))

    def test_duplicate_registration_is_generic_and_does_not_bind_second_session(self) -> None:
        first = self.anonymous_cookie()
        self.assertEqual(self.register(first)[0], 303)
        second = self.anonymous_cookie()
        status, _, body = self.register(second, email="BUYER@example.test")
        self.assertEqual(status, 400)
        lowered = body.lower()
        self.assertNotIn(self.email.encode(), lowered)
        self.assertNotIn(self.password.lower().encode(), lowered)
        self.assertIn(b"check your details and try again", lowered)
        self.assertIsNone(self.store.account_for_session(self.session_digest(second)))
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)

    def test_registration_requires_bound_one_time_code_before_account_creation(self) -> None:
        cookie = self.anonymous_cookie()
        session_digest = self.session_digest(cookie)
        status, headers, body = self.start_registration(cookie)
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/cvf/verify?purpose=registration"], b""),
        )
        self.assertIsNone(self.store.account_for_session(session_digest))
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

        messages = self.store.registration_outbox(session_digest)
        self.assertEqual(len(messages), 1)
        code = messages[0]["verification_code"]
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertNotIn("recipient", messages[0])
        self.assertEqual(messages[0]["status"], "LOCAL_ONLY")
        self.assertTrue(messages[0]["is_simulation"])

        status, _, verify_page = self.request(
            "GET", "/ap/cvf/verify?purpose=registration", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertNotIn(code.encode(), verify_page)
        self.assertNotIn(self.email.encode(), verify_page.lower())

        other_session = self.anonymous_cookie()
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify",
                fields={"code": code},
                cookie=other_session,
            )[0],
            400,
        )
        self.assertIsNone(
            self.store.account_for_session(self.session_digest(other_session))
        )

        wrong_code = f"{(int(code) + 1) % 1_000_000:06d}"
        status, _, invalid_body = self.request(
            "POST", "/ap/cvf/verify", fields={"code": wrong_code}, cookie=cookie
        )
        self.assertEqual(status, 400)
        self.assertIn(b"not valid", invalid_body.lower())
        self.assertIsNone(self.store.account_for_session(session_digest))

        status, headers, body = self.request(
            "POST", "/ap/cvf/verify", fields={"code": code}, cookie=cookie
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))
        rotated_cookie = self.cookie_from(headers)
        self.assertIsNotNone(
            self.store.account_for_session(self.session_digest(rotated_cookie))
        )
        self.assertEqual(self.store.registration_outbox(session_digest), [])

        replay_status, _, _ = self.request(
            "POST", "/ap/cvf/verify", fields={"code": code}, cookie=cookie
        )
        self.assertEqual(replay_status, 400)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)

    def test_expired_and_replaced_registration_codes_cannot_be_used(self) -> None:
        cookie = self.anonymous_cookie()
        session_digest = self.session_digest(cookie)
        self.assertEqual(self.start_registration(cookie)[0], 303)
        old_code = self.store.registration_outbox(session_digest)[0][
            "verification_code"
        ]
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE auth_registration_flows SET expires_at=0 WHERE session_digest=?",
                (session_digest,),
            )
        status, _, body = self.request(
            "POST", "/ap/cvf/verify", fields={"code": old_code}, cookie=cookie
        )
        self.assertEqual(status, 410)
        self.assertIn(b"expired", body.lower())

        self.release_auth_mail_cooldown("registration")
        status, headers, body = self.request(
            "POST", "/ap/cvf/verify", fields={"action": "resend"}, cookie=cookie
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/cvf/verify?purpose=registration"], b""),
        )
        new_code = self.store.registration_outbox(session_digest)[0][
            "verification_code"
        ]
        self.assertNotEqual(new_code, old_code)
        self.assertEqual(
            self.request(
                "POST", "/ap/cvf/verify", fields={"code": old_code}, cookie=cookie
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                "POST", "/ap/cvf/verify", fields={"code": new_code}, cookie=cookie
            )[0],
            303,
        )

    def test_registration_outbox_is_not_exposed_on_public_server(self) -> None:
        cookie = self.anonymous_cookie()
        self.assertEqual(self.start_registration(cookie)[0], 303)
        status, _, body = self.request(
            "GET", "/__bench/auth/registration-outbox", cookie=cookie
        )
        self.assertEqual(status, 404)
        self.assertEqual(body, b"Not Found")

    def test_mail_outboxes_and_health_require_admin_token(self) -> None:
        cookie = self.anonymous_cookie()
        self.assertEqual(self.start_registration(cookie)[0], 303)
        expected_code = self.store.registration_outbox(self.session_digest(cookie))[0][
            "verification_code"
        ]

        owner = self.anonymous_cookie()
        self.assertEqual(
            self.register(owner, email="reset-admin@example.test")[0], 303
        )
        reset_cookie = self.anonymous_cookie()
        self.assertEqual(
            self.start_password_reset(reset_cookie, "reset-admin@example.test")[0],
            303,
        )
        reset_code = self.store.password_reset_outbox(
            self.session_digest(reset_cookie)
        )[0]["verification_code"]

        for public_path in (
            "/__bench/auth/password-reset-outbox",
            "/__bench/mail/outbox",
        ):
            status, _, body = self.request("GET", public_path, cookie=reset_cookie)
            self.assertEqual((status, body), (404, b"Not Found"))

        AdminHandler.store = self.store
        AdminHandler.admin_token = "synthetic-admin-token"
        AdminHandler.smtp_summary = {"mode": "LOCAL_ONLY"}
        admin_server = ReusableThreadingHTTPServer(("127.0.0.1", 0), AdminHandler)
        admin_thread = threading.Thread(target=admin_server.serve_forever, daemon=True)
        admin_thread.start()
        admin_host, admin_port = admin_server.server_address
        try:
            connection = http.client.HTTPConnection(admin_host, admin_port, timeout=8)
            connection.request("GET", "/__bench/auth/registration-outbox")
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
            connection.close()

            connection = http.client.HTTPConnection(admin_host, admin_port, timeout=8)
            connection.request(
                "GET",
                "/__bench/auth/registration-outbox",
                headers={"X-Bench-Admin-Token": "synthetic-admin-token"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["delivery"], "LOCAL_ONLY")
            self.assertEqual(payload["messages"][0]["verification_code"], expected_code)
            self.assertNotIn("recipient", payload["messages"][0])

            def admin_get(path: str) -> tuple[int, dict[str, object]]:
                connection = http.client.HTTPConnection(
                    admin_host, admin_port, timeout=8
                )
                connection.request(
                    "GET",
                    path,
                    headers={"X-Bench-Admin-Token": "synthetic-admin-token"},
                )
                response = connection.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                connection.close()
                return response.status, body

            status, reset_payload = admin_get(
                "/__bench/auth/password-reset-outbox"
            )
            self.assertEqual(status, 200)
            self.assertEqual(reset_payload["delivery"], "LOCAL_ONLY")
            self.assertEqual(
                reset_payload["messages"][0]["verification_code"], reset_code
            )
            self.assertNotIn("recipient", reset_payload["messages"][0])

            status, combined_payload = admin_get("/__bench/mail/outbox")
            self.assertEqual(status, 200)
            self.assertEqual(combined_payload["delivery"], "LOCAL_ONLY")
            self.assertEqual(
                {message["kind"] for message in combined_payload["messages"]},
                {"registration", "password-reset"},
            )

            status, health_payload = admin_get("/__bench/health")
            self.assertEqual(status, 200)
            self.assertEqual(health_payload["mail_transport"], {"mode": "LOCAL_ONLY"})
            self.assertEqual(
                health_payload["mail_delivery_status"]["LOCAL_ONLY"], 2
            )
        finally:
            admin_server.shutdown()
            admin_server.server_close()
            admin_thread.join(timeout=2)

    def test_two_stage_login_accepts_normalized_email_and_safe_return(self) -> None:
        owner = self.anonymous_cookie()
        owner_status, owner_headers, _ = self.register(owner)
        self.assertEqual(owner_status, 303)
        owner = self.cookie_from(owner_headers)
        login_cookie = self.anonymous_cookie()
        status, headers, body = self.begin_signin(
            login_cookie,
            " BUYER@EXAMPLE.TEST ",
            "/gp/css/order-history?ref=auth",
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/ap/signin?stage=password"], b""))
        status, headers, body = self.request(
            "POST", "/ap/signin", fields={"password": self.password}, cookie=login_cookie
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/gp/css/order-history?ref=auth"], b""))
        rotated_cookie = self.cookie_from(headers)
        self.assertNotEqual(rotated_cookie, login_cookie)
        self.assertIsNone(self.store.account_for_session(self.session_digest(login_cookie)))
        self.assertIsNotNone(self.store.account_for_session(self.session_digest(rotated_cookie)))

    def test_wrong_password_is_rejected_and_unknown_account_moves_to_prefilled_registration(self) -> None:
        owner = self.anonymous_cookie()
        owner_status, owner_headers, _ = self.register(owner)
        self.assertEqual(owner_status, 303)
        owner = self.cookie_from(owner_headers)
        cookie = self.anonymous_cookie()
        self.assertEqual(self.begin_signin(cookie, self.email)[0], 303)
        status, _, body = self.request(
            "POST", "/ap/signin", fields={"password": "Definitely-Wrong"}, cookie=cookie
        )
        self.assertEqual(status, 401)
        self.assertNotIn(self.email.encode(), body.lower())
        self.assertNotIn(b"definitely-wrong", body.lower())
        self.assertIsNone(self.store.account_for_session(self.session_digest(cookie)))

        unknown = self.anonymous_cookie()
        status, headers, body = self.begin_signin(
            unknown, " Unknown@Example.Test ", "/gp/css/order-history"
        )
        self.assertEqual(status, 303)
        self.assertEqual(body, b"")
        self.assertEqual(
            headers.get("location"),
            [
                "/ap/register?email=unknown%40example.test&"
                "openid.return_to=%2Fgp%2Fcss%2Forder-history"
            ],
        )
        status, _, registration_page = self.request(
            "GET", headers["location"][0], cookie=unknown
        )
        self.assertEqual(status, 200)
        self.assertIn(b'value="unknown@example.test"', registration_page.lower())
        self.assertIsNone(self.store.account_for_session(self.session_digest(unknown)))

    def test_signout_unbinds_session_and_protected_route_requires_login_again(self) -> None:
        cookie = self.anonymous_cookie()
        register_status, register_headers, _ = self.register(cookie)
        self.assertEqual(register_status, 303)
        cookie = self.cookie_from(register_headers)
        session_digest = self.session_digest(cookie)
        get_status, get_headers, _ = self.request("GET", "/ap/signout", cookie=cookie)
        self.assertEqual(get_status, 405)
        self.assertEqual(get_headers.get("allow"), ["POST"])
        self.assertIsNotNone(self.store.account_for_session(session_digest))
        status, headers, body = self.request("POST", "/ap/signout", fields={}, cookie=cookie)
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))
        self.assertTrue(any("Max-Age=0" in value for value in headers.get("set-cookie", [])))
        self.assertIsNone(self.store.account_for_session(session_digest))
        status, headers, _ = self.request("GET", "/gp/css/order-history", cookie=cookie)
        self.assertEqual(status, 303)
        self.assertTrue(headers["location"][0].startswith("/ap/signin?"))

    def test_signed_in_header_and_account_page_offer_post_only_signout(self) -> None:
        cookie = self.anonymous_cookie()
        status, headers, _ = self.register(cookie)
        self.assertEqual(status, 303)
        cookie = self.cookie_from(headers)

        status, _, home = self.request("GET", "/", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Hello, Example", home)
        self.assertNotIn(b"Hello, sign in", home)

        status, _, account_page = self.request(
            "GET", "/gp/css/homepage.html", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Hello, Example Buyer", account_page)
        self.assertIn(b'method="post" action="/ap/signout"', account_page)
        self.assertNotIn(b'href="/ap/signout"', account_page)

    def test_external_return_targets_are_rejected_for_register_and_login(self) -> None:
        register_cookie = self.anonymous_cookie()
        status, headers, _ = self.register(
            register_cookie,
            email="first@example.test",
            return_to="https://evil.example/collect",
        )
        self.assertEqual((status, headers.get("location")), (303, ["/"]))
        register_cookie = self.cookie_from(headers)

        login_cookie = self.anonymous_cookie()
        self.assertEqual(
            self.begin_signin(
                login_cookie, "first@example.test", "//evil.example/collect"
            )[0],
            303,
        )
        status, headers, _ = self.request(
            "POST", "/ap/signin", fields={"password": self.password}, cookie=login_cookie
        )
        self.assertEqual((status, headers.get("location")), (303, ["/"]))

    def test_authentication_never_places_credentials_in_request_journal(self) -> None:
        cookie = self.anonymous_cookie()
        register_status, register_headers, _ = self.register(cookie)
        self.assertEqual(register_status, 303)
        cookie = self.cookie_from(register_headers)
        self.assertEqual(
            self.request("POST", "/ap/signout", fields={}, cookie=cookie)[0], 303
        )
        login_cookie = self.anonymous_cookie()
        self.assertEqual(self.begin_signin(login_cookie, self.email)[0], 303)
        self.assertEqual(
            self.request(
                "POST", "/ap/signin", fields={"password": self.password}, cookie=login_cookie
            )[0],
            303,
        )
        journal = json.dumps(self.store.journal(), sort_keys=True).lower()
        self.assertNotIn(self.email, journal)
        self.assertNotIn(self.password.lower(), journal)
        self.assertEqual(self.store.journal(), [])

    def test_password_recovery_identifier_response_is_uniform_and_never_mails_unknown_address(self) -> None:
        owner = self.anonymous_cookie()
        status, headers, _ = self.register(owner)
        self.assertEqual(status, 303)
        owner = self.cookie_from(headers)

        known = self.anonymous_cookie()
        unknown = self.anonymous_cookie()
        known_response = self.start_password_reset(known, self.email)
        unknown_response = self.start_password_reset(
            unknown, "missing-account@example.test"
        )
        for status, headers, body in (known_response, unknown_response):
            self.assertEqual(status, 303)
            self.assertEqual(
                headers.get("location"),
                ["/ap/cvf/verify?purpose=password-reset"],
            )
            self.assertEqual(body, b"")

        known_messages = self.store.password_reset_outbox(
            self.session_digest(known)
        )
        self.assertEqual(len(known_messages), 1)
        self.assertRegex(known_messages[0]["verification_code"], r"^[0-9]{6}$")
        self.assertEqual(
            self.store.password_reset_outbox(self.session_digest(unknown)), []
        )
        _, _, known_page = self.request(
            "GET", "/ap/cvf/verify?purpose=password-reset", cookie=known
        )
        _, _, unknown_page = self.request(
            "GET", "/ap/cvf/verify?purpose=password-reset", cookie=unknown
        )
        self.assertEqual(known_page, unknown_page)
        self.assertNotIn(self.email.encode(), known_page.lower())
        self.assertIn(b"QUEUED", known_page)
        self.assertIn(b"Refresh delivery status", known_page)
        for private_state in (b"LOCAL_ONLY", b"SMTP_SENT", b"SMTP_FAILED"):
            self.assertNotIn(private_state, known_page)

    def test_password_reset_code_resend_expiry_attempt_limit_and_one_time_use(self) -> None:
        owner = self.anonymous_cookie()
        self.assertEqual(self.register(owner)[0], 303)

        cookie = self.anonymous_cookie()
        self.assertEqual(self.start_password_reset(cookie, self.email)[0], 303)
        session = self.session_digest(cookie)
        old_code = self.store.password_reset_outbox(session)[0][
            "verification_code"
        ]
        self.release_auth_mail_cooldown("password-reset")
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify?purpose=password-reset",
            fields={"action": "resend"},
            cookie=cookie,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/cvf/verify?purpose=password-reset"], b""),
        )
        new_code = self.store.password_reset_outbox(session)[0][
            "verification_code"
        ]
        self.assertNotEqual(old_code, new_code)
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": old_code},
                cookie=cookie,
            )[0],
            400,
        )
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify?purpose=password-reset",
            fields={"code": new_code},
            cookie=cookie,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/forgotpassword?stage=reset-password"], b""),
        )
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": new_code},
                cookie=cookie,
            )[0],
            400,
        )

        self.release_auth_mail_cooldown("password-reset")
        locked = self.anonymous_cookie()
        self.assertEqual(self.start_password_reset(locked, self.email)[0], 303)
        actual = self.store.password_reset_outbox(self.session_digest(locked))[0][
            "verification_code"
        ]
        wrong = "000000" if actual != "000000" else "111111"
        for attempt in range(5):
            response = self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": wrong},
                cookie=locked,
            )
            self.assertEqual(response[0], 400, attempt)
        self.assertIn(b"Too many incorrect attempts", response[2])
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": actual},
                cookie=locked,
            )[0],
            400,
        )

        self.release_auth_mail_cooldown("password-reset")
        expired = self.anonymous_cookie()
        self.assertEqual(self.start_password_reset(expired, self.email)[0], 303)
        expired_session = self.session_digest(expired)
        expired_code = self.store.password_reset_outbox(expired_session)[0][
            "verification_code"
        ]
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE auth_password_reset_flows SET expires_at=0 "
                "WHERE session_digest=?",
                (expired_session,),
            )
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": expired_code},
                cookie=expired,
            )[0],
            410,
        )

    def test_password_reset_revokes_other_sessions_and_old_password_without_leaking_secrets(self) -> None:
        first = self.anonymous_cookie()
        status, headers, _ = self.register(first)
        self.assertEqual(status, 303)
        first = self.cookie_from(headers)

        second = self.anonymous_cookie()
        self.assertEqual(self.begin_signin(second, self.email)[0], 303)
        status, headers, _ = self.request(
            "POST", "/ap/signin", fields={"password": self.password}, cookie=second
        )
        self.assertEqual(status, 303)
        second = self.cookie_from(headers)

        recovery = self.anonymous_cookie()
        self.assertEqual(
            self.start_password_reset(
                recovery, self.email, "https://evil.example/collect"
            )[0],
            303,
        )
        recovery_session = self.session_digest(recovery)
        code = self.store.password_reset_outbox(recovery_session)[0][
            "verification_code"
        ]
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": code},
                cookie=recovery,
            )[0],
            303,
        )
        new_password = "New-Correct-Horse-922"
        status, headers, body = self.request(
            "POST",
            "/ap/forgotpassword",
            fields={"password": new_password, "passwordCheck": new_password},
            cookie=recovery,
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))
        recovered = self.cookie_from(headers)
        self.assertIsNotNone(
            self.store.account_for_session(self.session_digest(recovered))
        )
        self.assertIsNone(self.store.account_for_session(self.session_digest(first)))
        self.assertIsNone(self.store.account_for_session(self.session_digest(second)))
        self.assertEqual(self.store.password_reset_outbox(), [])

        old_login = self.anonymous_cookie()
        self.assertEqual(self.begin_signin(old_login, self.email)[0], 303)
        self.assertEqual(
            self.request(
                "POST",
                "/ap/signin",
                fields={"password": self.password},
                cookie=old_login,
            )[0],
            401,
        )
        new_login = self.anonymous_cookie()
        self.assertEqual(self.begin_signin(new_login, self.email)[0], 303)
        self.assertEqual(
            self.request(
                "POST",
                "/ap/signin",
                fields={"password": new_password},
                cookie=new_login,
            )[0],
            303,
        )
        journal = json.dumps(self.store.journal(), sort_keys=True).lower()
        for secret in (self.email, self.password.lower(), new_password.lower(), code):
            self.assertNotIn(secret, journal)

    def test_smtp_auth_outboxes_hide_codes_and_record_admin_safe_delivery_status(self) -> None:
        registration = self.anonymous_cookie()
        registration_session = self.session_digest(registration)
        self.assertTrue(
            self.store.begin_registration(
                registration_session,
                "smtp-registration@example.test",
                "SMTP Registration",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        registration_outbox = self.store.registration_outbox(
            registration_session
        )
        self.assertIsNone(registration_outbox[0]["verification_code"])
        registration_delivery = self.store.registration_delivery(
            registration_session
        )
        self.assertIsNotNone(registration_delivery)
        assert registration_delivery is not None
        self.assertEqual(
            registration_delivery["recipient"],
            "smtp-registration@example.test",
        )
        registration_claim = self.store.claim_mail_delivery(
            "registration", registration_delivery["email_id"]
        )
        self.assertIsNotNone(registration_claim)
        assert registration_claim is not None
        self.assertTrue(
            self.store.mark_mail_delivery(
                "registration",
                registration_delivery["email_id"],
                claim_token=registration_claim,
                sent=False,
                error_summary="SMTPAuthenticationError:smtp-535",
            )
        )
        failed = self.store.registration_outbox(registration_session)[0]
        self.assertEqual(failed["status"], "SMTP_FAILED")
        self.assertFalse(failed["is_simulation"])
        self.assertEqual(failed["delivery_attempts"], 1)
        self.assertEqual(failed["last_error"], "SMTPAuthenticationError:smtp-535")
        self.assertIsNone(failed["verification_code"])

        owner = self.anonymous_cookie()
        self.assertEqual(self.register(owner)[0], 303)
        recovery = self.anonymous_cookie()
        recovery_session = self.session_digest(recovery)
        self.store.begin_password_reset(
            recovery_session,
            self.email,
            None,
            mail_mode=MAIL_SMTP_PENDING,
        )
        reset_outbox = self.store.password_reset_outbox(recovery_session)
        self.assertIsNone(reset_outbox[0]["verification_code"])
        reset_delivery = self.store.password_reset_delivery(recovery_session)
        self.assertIsNotNone(reset_delivery)
        assert reset_delivery is not None
        reset_claim = self.store.claim_mail_delivery(
            "password-reset", reset_delivery["email_id"]
        )
        self.assertIsNotNone(reset_claim)
        assert reset_claim is not None
        self.assertTrue(
            self.store.mark_mail_delivery(
                "password-reset",
                reset_delivery["email_id"],
                claim_token=reset_claim,
                sent=True,
            )
        )
        sent = self.store.password_reset_outbox(recovery_session)[0]
        self.assertEqual(sent["status"], "SMTP_SENT")
        self.assertEqual(sent["delivery_attempts"], 1)
        self.assertIsNotNone(sent["sent_at"])
        self.assertIsNone(sent["verification_code"])
        health = self.store.mail_delivery_health()
        self.assertEqual(health["SMTP_FAILED"], 1)
        self.assertEqual(health["SMTP_SENT"], 1)

    def test_configured_smtp_delivers_registration_and_recovery_codes(self) -> None:
        smtp_config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="Amazon Clone <no-reply@example.test>",
            username="smtp-user",
            password="smtp-secret",
        )
        QuietPublicHandler.smtp_config = smtp_config
        captured: list[dict[str, str]] = []
        delivery_started = threading.Event()

        def capture_delivery(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            captured.append(
                {
                    "config": repr(config),
                    "recipient": recipient,
                    "subject": subject,
                    "body": body,
                }
            )
            delivery_started.set()

        registration = self.anonymous_cookie()
        registration_session = self.session_digest(registration)
        with patch.object(
            server_module, "send_smtp_message", side_effect=capture_delivery
        ):
            status, headers, body = self.start_registration(
                registration, email="smtp-flow@example.test"
            )
            self.assertEqual(
                (status, headers.get("location"), body),
                (303, ["/ap/cvf/verify?purpose=registration"], b""),
            )
            self.assertTrue(delivery_started.wait(2))
            sent = self.wait_for_delivery_status(
                lambda: self.store.registration_outbox(registration_session)
            )
        self.assertFalse(sent["is_simulation"])
        self.assertIsNone(sent["verification_code"])
        self.assertEqual(captured[0]["recipient"], "smtp-flow@example.test")
        self.assertNotIn("smtp-secret", captured[0]["config"])
        code_match = re.search(r"\b([0-9]{6})\b", captured[0]["body"])
        self.assertIsNotNone(code_match)
        assert code_match is not None
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify?purpose=registration",
            fields={"code": code_match.group(1)},
            cookie=registration,
        )
        self.assertEqual((status, headers.get("location"), body), (303, ["/"], b""))

        captured.clear()
        delivery_started.clear()
        recovery = self.anonymous_cookie()
        recovery_session = self.session_digest(recovery)
        with patch.object(
            server_module, "send_smtp_message", side_effect=capture_delivery
        ):
            status, headers, body = self.start_password_reset(
                recovery, "smtp-flow@example.test"
            )
            self.assertEqual(
                (status, headers.get("location"), body),
                (303, ["/ap/cvf/verify?purpose=password-reset"], b""),
            )
            self.assertTrue(delivery_started.wait(2))
            sent = self.wait_for_delivery_status(
                lambda: self.store.password_reset_outbox(recovery_session)
            )
        self.assertFalse(sent["is_simulation"])
        self.assertIsNone(sent["verification_code"])
        self.assertEqual(captured[0]["recipient"], "smtp-flow@example.test")
        code_match = re.search(r"\b([0-9]{6})\b", captured[0]["body"])
        self.assertIsNotNone(code_match)
        assert code_match is not None
        status, headers, body = self.request(
            "POST",
            "/ap/cvf/verify?purpose=password-reset",
            fields={"code": code_match.group(1)},
            cookie=recovery,
        )
        self.assertEqual(
            (status, headers.get("location"), body),
            (303, ["/ap/forgotpassword?stage=reset-password"], b""),
        )

    def test_failed_registration_delivery_is_session_scoped_and_retryable(self) -> None:
        QuietPublicHandler.smtp_config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="no-reply@example.test",
        )
        attempts: list[str] = []
        retry_finished = threading.Event()

        def fail_then_send(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            del config, recipient, subject
            attempts.append(body)
            if len(attempts) == 1:
                raise RuntimeError("synthetic SMTP outage")
            retry_finished.set()

        owner = self.anonymous_cookie()
        owner_session = self.session_digest(owner)
        outsider = self.anonymous_cookie()
        with patch.object(
            server_module, "send_smtp_message", side_effect=fail_then_send
        ):
            self.assertEqual(
                self.start_registration(
                    owner, email="retry-registration@example.test"
                )[0],
                303,
            )
            failed = self.wait_for_delivery_status(
                lambda: self.store.registration_outbox(owner_session),
                "SMTP_FAILED",
            )
            self.assertEqual(failed["delivery_attempts"], 1)

            status, _, owner_page = self.request(
                "GET", "/ap/cvf/verify?purpose=registration", cookie=owner
            )
            self.assertEqual(status, 200)
            self.assertIn(b"SMTP_FAILED", owner_page)
            self.assertIn(b"Retry email delivery", owner_page)
            self.assertNotIn(b"synthetic SMTP outage", owner_page)

            _, _, outsider_page = self.request(
                "GET", "/ap/cvf/verify?purpose=registration", cookie=outsider
            )
            self.assertNotIn(b"SMTP_FAILED", outsider_page)
            self.assertNotIn(b"Retry email delivery", outsider_page)
            self.assertEqual(
                self.request(
                    "POST",
                    "/ap/cvf/verify?purpose=registration",
                    fields={"action": "retry-delivery"},
                    cookie=outsider,
                )[:1],
                (303,),
            )
            self.assertEqual(
                self.store.registration_outbox(owner_session)[0][
                    "delivery_attempts"
                ],
                1,
            )

            status, headers, body = self.request(
                "POST",
                "/ap/cvf/verify?purpose=registration",
                fields={"action": "retry-delivery"},
                cookie=owner,
            )
            self.assertEqual(
                (status, headers.get("location"), body),
                (303, ["/ap/cvf/verify?purpose=registration"], b""),
            )
            self.assertTrue(retry_finished.wait(2))
            sent = self.wait_for_delivery_status(
                lambda: self.store.registration_outbox(owner_session)
            )

        self.assertEqual(sent["delivery_attempts"], 2)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        _, _, sent_page = self.request(
            "GET", "/ap/cvf/verify?purpose=registration", cookie=owner
        )
        self.assertIn(b"SMTP_SENT", sent_page)
        self.assertNotIn(b"Retry email delivery", sent_page)
        code_match = re.search(r"\b([0-9]{6})\b", attempts[1])
        self.assertIsNotNone(code_match)
        assert code_match is not None
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=registration",
                fields={"code": code_match.group(1)},
                cookie=owner,
            )[0],
            303,
        )

    def test_password_reset_public_status_is_non_enumerating_and_private_retry_preserves_code(self) -> None:
        account_owner = self.anonymous_cookie()
        self.assertEqual(
            self.register(account_owner, email="retry-reset@example.test")[0],
            303,
        )
        QuietPublicHandler.smtp_config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="no-reply@example.test",
        )
        attempts: list[str] = []
        retry_finished = threading.Event()

        def fail_then_send(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            del config, recipient, subject
            attempts.append(body)
            if len(attempts) == 1:
                raise RuntimeError("synthetic SMTP outage")
            retry_finished.set()

        recovery = self.anonymous_cookie()
        recovery_session = self.session_digest(recovery)
        unknown = self.anonymous_cookie()
        with patch.object(
            server_module, "send_smtp_message", side_effect=fail_then_send
        ):
            known_response = self.start_password_reset(
                recovery, "retry-reset@example.test"
            )
            self.assertEqual(known_response[0], 303)
            self.wait_for_delivery_status(
                lambda: self.store.password_reset_outbox(recovery_session),
                "SMTP_FAILED",
            )
            _, _, failed_page = self.request(
                "GET", "/ap/cvf/verify?purpose=password-reset", cookie=recovery
            )

            unknown_response = self.start_password_reset(
                unknown, "missing-retry-reset@example.test"
            )
            self.assertEqual(
                (
                    known_response[0],
                    known_response[1].get("location"),
                    known_response[2],
                ),
                (
                    unknown_response[0],
                    unknown_response[1].get("location"),
                    unknown_response[2],
                ),
            )
            _, _, unknown_page = self.request(
                "GET", "/ap/cvf/verify?purpose=password-reset", cookie=unknown
            )
            self.assertEqual(failed_page, unknown_page)
            self.assertIn(b"QUEUED", failed_page)
            self.assertIn(b"Refresh delivery status", failed_page)
            for private_state in (b"SMTP_FAILED", b"SMTP_SENT", b"Retry email delivery"):
                self.assertNotIn(private_state, failed_page)

            status, headers, body = self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"action": "retry-delivery"},
                cookie=recovery,
            )
            self.assertEqual(
                (status, headers.get("location"), body),
                (303, ["/ap/cvf/verify?purpose=password-reset"], b""),
            )
            # A crafted pre-verification Retry is deliberately a no-op.
            still_failed = self.store.password_reset_outbox(recovery_session)[0]
            self.assertEqual(still_failed["status"], "SMTP_FAILED")
            self.assertEqual(still_failed["delivery_attempts"], 1)
            self.assertEqual(len(attempts), 1)

            # The protected domain operation can requeue the durable message;
            # its result remains hidden until the OTP proves ownership.
            self.assertTrue(self.store.retry_password_reset_mail(recovery_session))
            delivery = self.store.password_reset_delivery(recovery_session)
            self.assertIsNotNone(delivery)
            assert delivery is not None
            self.assertTrue(
                server_module.dispatch_mail_delivery(
                    self.store, QuietPublicHandler.smtp_config, delivery
                )
            )
            self.assertTrue(retry_finished.wait(2))
            sent = self.wait_for_delivery_status(
                lambda: self.store.password_reset_outbox(recovery_session)
            )

            _, _, sent_public_page = self.request(
                "GET", "/ap/cvf/verify?purpose=password-reset", cookie=recovery
            )
            _, _, unknown_public_page = self.request(
                "GET", "/ap/cvf/verify?purpose=password-reset", cookie=unknown
            )
            self.assertEqual(sent_public_page, unknown_public_page)
            self.assertNotIn(b"SMTP_SENT", sent_public_page)

        self.assertEqual(sent["delivery_attempts"], 2)
        self.assertEqual(attempts[0], attempts[1])
        code_match = re.search(r"\b([0-9]{6})\b", attempts[1])
        self.assertIsNotNone(code_match)
        assert code_match is not None
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=password-reset",
                fields={"code": code_match.group(1)},
                cookie=recovery,
            )[1].get("location"),
            ["/ap/forgotpassword?stage=reset-password"],
        )
        _, _, verified_owner_page = self.request(
            "GET", "/ap/cvf/verify?purpose=password-reset", cookie=recovery
        )
        self.assertIn(b"SMTP_SENT", verified_owner_page)
        self.assertEqual(
            self.store.verify_password_reset_code(
                recovery_session, code_match.group(1)
            ),
            "used",
        )

    def test_resend_replaces_the_job_without_allowing_an_old_worker_to_mark_it(self) -> None:
        QuietPublicHandler.smtp_config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="no-reply@example.test",
        )
        captured: list[str] = []
        first_started = threading.Event()
        second_finished = threading.Event()
        release_first = threading.Event()

        def delayed_delivery(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            del config, recipient, subject
            captured.append(body)
            if len(captured) == 1:
                first_started.set()
                release_first.wait(3)
            else:
                second_finished.set()

        cookie = self.anonymous_cookie()
        session_digest = self.session_digest(cookie)
        try:
            with patch.object(
                server_module, "send_smtp_message", side_effect=delayed_delivery
            ):
                self.assertEqual(
                    self.start_registration(
                        cookie, email="smtp-race@example.test"
                    )[0],
                    303,
                )
                self.assertTrue(first_started.wait(2))
                old_job = self.store.registration_outbox(session_digest)[0]
                old_email_id = old_job["email_id"]

                self.release_auth_mail_cooldown("registration")
                status, headers, body = self.request(
                    "POST",
                    "/ap/cvf/verify?purpose=registration",
                    fields={"action": "resend"},
                    cookie=cookie,
                )
                self.assertEqual(
                    (status, headers.get("location"), body),
                    (303, ["/ap/cvf/verify?purpose=registration"], b""),
                )
                self.assertTrue(second_finished.wait(2))
                new_job = self.wait_for_delivery_status(
                    lambda: self.store.registration_outbox(session_digest)
                )
                self.assertNotEqual(new_job["email_id"], old_email_id)
                release_first.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    stable_job = self.store.registration_outbox(session_digest)[0]
                    if stable_job["status"] == "SMTP_SENT":
                        break
                    time.sleep(0.01)
        finally:
            release_first.set()

        self.assertEqual(len(captured), 2)
        old_code_match = re.search(r"\b([0-9]{6})\b", captured[0])
        new_code_match = re.search(r"\b([0-9]{6})\b", captured[1])
        self.assertIsNotNone(old_code_match)
        self.assertIsNotNone(new_code_match)
        assert old_code_match is not None and new_code_match is not None
        self.assertNotEqual(old_code_match.group(1), new_code_match.group(1))
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=registration",
                fields={"code": old_code_match.group(1)},
                cookie=cookie,
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                "POST",
                "/ap/cvf/verify?purpose=registration",
                fields={"code": new_code_match.group(1)},
                cookie=cookie,
            )[0],
            303,
        )

    def test_auth_mail_cooldown_budget_and_lockout_persist_across_flows(self) -> None:
        owner = self.anonymous_cookie()
        self.assertEqual(self.register(owner)[0], 303)

        known = self.anonymous_cookie()
        unknown = self.anonymous_cookie()
        self.assertEqual(self.start_password_reset(known, self.email)[0], 303)
        self.assertEqual(
            self.start_password_reset(unknown, "unknown-budget@example.test")[0],
            303,
        )
        known_session = self.session_digest(known)
        original_job = self.store.password_reset_outbox(known_session)[0]

        known_response = self.request(
            "POST",
            "/ap/cvf/verify?purpose=password-reset",
            fields={"action": "resend"},
            cookie=known,
        )
        unknown_response = self.request(
            "POST",
            "/ap/cvf/verify?purpose=password-reset",
            fields={"action": "resend"},
            cookie=unknown,
        )
        # HTTP servers generate their own Date header, so requests crossing a
        # one-second boundary can differ there even when the public contract is
        # identical. Compare the stable, account-sensitive response surface.
        for response in (known_response, unknown_response):
            self.assertEqual(response[0], 303)
            self.assertEqual(
                response[1].get("location"),
                ["/ap/cvf/verify?purpose=password-reset"],
            )
            self.assertEqual(response[2], b"")
        self.assertEqual(
            self.store.password_reset_outbox(known_session)[0]["email_id"],
            original_job["email_id"],
        )

        # The initial send plus five permitted replacements consumes the
        # one-hour account/session/recipient budget.
        for _ in range(5):
            self.release_auth_mail_cooldown("password-reset")
            self.assertTrue(
                self.store.resend_password_reset_code(known_session)
            )
        budget_job = self.store.password_reset_outbox(known_session)[0]
        self.release_auth_mail_cooldown("password-reset")
        self.assertFalse(self.store.resend_password_reset_code(known_session))
        self.assertEqual(
            self.store.password_reset_outbox(known_session)[0]["email_id"],
            budget_job["email_id"],
        )

        # A new browser cannot bypass the account budget or invalidate the
        # still-active code issued to the original browser.
        attacker = self.anonymous_cookie()
        self.release_auth_mail_cooldown("password-reset")
        self.assertEqual(self.start_password_reset(attacker, self.email)[0], 303)
        self.assertEqual(
            self.store.password_reset_outbox(known_session)[0]["email_id"],
            budget_job["email_id"],
        )
        self.assertEqual(
            self.store.password_reset_outbox(self.session_digest(attacker)), []
        )

        locked_email = "locked-budget@example.test"
        locked_owner = self.anonymous_cookie()
        self.assertEqual(self.register(locked_owner, email=locked_email)[0], 303)
        locked = self.anonymous_cookie()
        self.assertEqual(self.start_password_reset(locked, locked_email)[0], 303)
        locked_session = self.session_digest(locked)
        actual = self.store.password_reset_outbox(locked_session)[0][
            "verification_code"
        ]
        wrong = "000000" if actual != "000000" else "111111"
        for _ in range(5):
            self.store.verify_password_reset_code(locked_session, wrong)
        self.release_auth_mail_cooldown("password-reset")
        self.assertFalse(self.store.resend_password_reset_code(locked_session))

    def test_startup_replay_reclaims_and_delivers_a_crashed_pending_job(self) -> None:
        session_digest = "c" * 64
        self.assertTrue(
            self.store.begin_registration(
                session_digest,
                "startup-replay@example.test",
                "Startup Replay",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        delivery = self.store.registration_delivery(session_digest)
        self.assertIsNotNone(delivery)
        assert delivery is not None
        abandoned_claim = self.store.claim_mail_delivery(
            "registration", delivery["email_id"]
        )
        self.assertIsNotNone(abandoned_claim)
        self.assertEqual(self.store.pending_mail_deliveries(), [])

        self.assertEqual(self.store.recover_pending_mail_claims(), 1)
        replay = self.store.pending_mail_deliveries()
        self.assertEqual(len(replay), 1)
        delivered = threading.Event()

        def capture_delivery(
            config: SMTPConfig,
            *,
            recipient: str,
            subject: str,
            body: str,
        ) -> None:
            del config, recipient, subject, body
            delivered.set()

        config = SMTPConfig(
            host="smtp.example.test",
            port=587,
            security="starttls",
            sender="no-reply@example.test",
        )
        with patch.object(
            server_module, "send_smtp_message", side_effect=capture_delivery
        ):
            self.assertTrue(
                server_module.dispatch_mail_delivery(self.store, config, replay[0])
            )
            self.assertTrue(delivered.wait(2))
            sent = self.wait_for_delivery_status(
                lambda: self.store.registration_outbox(session_digest)
            )
        self.assertEqual(sent["delivery_attempts"], 2)

    def test_local_only_startup_reconciles_legacy_pending_and_failed_auth_mail(self) -> None:
        registration_cookie = self.anonymous_cookie()
        registration_session = self.session_digest(registration_cookie)
        self.assertTrue(
            self.store.begin_registration(
                registration_session,
                "legacy-failed-local@example.test",
                "Legacy Failed Local",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        registration_delivery = self.store.registration_delivery(
            registration_session
        )
        self.assertIsNotNone(registration_delivery)
        assert registration_delivery is not None
        claim = self.store.claim_mail_delivery(
            "registration", registration_delivery["email_id"]
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertTrue(
            self.store.mark_mail_delivery(
                "registration",
                registration_delivery["email_id"],
                claim_token=claim,
                sent=False,
                error_summary="SMTPDeliveryError",
            )
        )
        # Simulate the older implementation that redacted failed OTP rows.
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE auth_registration_email_outbox SET verification_code='000000'"
            )

        self.assertTrue(
            self.store.register_account(
                "a" * 64,
                "legacy-pending-reset@example.test",
                "Legacy Pending Reset",
                self.password,
            )
        )
        reset_cookie = self.anonymous_cookie()
        reset_session = self.session_digest(reset_cookie)
        self.store.begin_password_reset(
            reset_session,
            "legacy-pending-reset@example.test",
            None,
            mail_mode=MAIL_SMTP_PENDING,
        )

        self.assertEqual(self.store.reconcile_mail_for_local_only(), 2)
        registration = self.store.registration_outbox(registration_session)[0]
        reset = self.store.password_reset_outbox(reset_session)[0]
        for message in (registration, reset):
            self.assertEqual(message["status"], "LOCAL_ONLY")
            self.assertTrue(message["is_simulation"])
            self.assertRegex(str(message["verification_code"]), r"^[0-9]{6}$")
        self.assertEqual(self.store.pending_mail_deliveries(), [])
        self.assertEqual(
            self.store.verify_registration_code(
                registration_session, registration["verification_code"]
            )[0],
            "verified",
        )
        self.assertEqual(
            self.store.verify_password_reset_code(
                reset_session, reset["verification_code"]
            ),
            "verified",
        )
        _, _, registration_page = self.request(
            "GET", "/ap/cvf/verify?purpose=registration", cookie=registration_cookie
        )
        self.assertIn(b"LOCAL_ONLY", registration_page)
        self.assertNotIn(b"configured SMTP service is processing", registration_page)
        self.assertNotIn(b"Retry email delivery", registration_page)

    def test_retry_visibility_matches_flow_lock_and_verification_guards(self) -> None:
        registration_session = "7" * 64
        self.assertTrue(
            self.store.begin_registration(
                registration_session,
                "locked-retry@example.test",
                "Locked Retry",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        registration_delivery = self.store.registration_delivery(
            registration_session
        )
        assert registration_delivery is not None
        claim = self.store.claim_mail_delivery(
            "registration", registration_delivery["email_id"]
        )
        assert claim is not None
        self.assertTrue(
            self.store.mark_mail_delivery(
                "registration",
                registration_delivery["email_id"],
                claim_token=claim,
                sent=False,
            )
        )
        self.assertTrue(
            self.store.registration_mail_status(registration_session)["can_retry"]
        )
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE auth_registration_flows SET attempts=5 WHERE session_digest=?",
                (registration_session,),
            )
        self.assertFalse(
            self.store.registration_mail_status(registration_session)["can_retry"]
        )
        self.assertFalse(self.store.retry_registration_mail(registration_session))

        self.assertTrue(
            self.store.register_account(
                "8" * 64,
                "verified-retry@example.test",
                "Verified Retry",
                self.password,
            )
        )
        reset_session = "9" * 64
        self.store.begin_password_reset(
            reset_session,
            "verified-retry@example.test",
            None,
            mail_mode=MAIL_SMTP_PENDING,
        )
        reset_delivery = self.store.password_reset_delivery(reset_session)
        assert reset_delivery is not None
        reset_claim = self.store.claim_mail_delivery(
            "password-reset", reset_delivery["email_id"]
        )
        assert reset_claim is not None
        self.assertTrue(
            self.store.mark_mail_delivery(
                "password-reset",
                reset_delivery["email_id"],
                claim_token=reset_claim,
                sent=False,
            )
        )
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE auth_password_reset_flows SET verified_at=? WHERE session_digest=?",
                (int(time.time()), reset_session),
            )
        self.assertFalse(
            self.store.password_reset_mail_status(reset_session)["can_retry"]
        )
        self.assertFalse(self.store.retry_password_reset_mail(reset_session))

    def test_mail_claim_is_single_winner_and_attempt_cap_blocks_startup_replay(self) -> None:
        concurrent_session = "4" * 64
        self.assertTrue(
            self.store.begin_registration(
                concurrent_session,
                "concurrent-claim@example.test",
                "Concurrent Claim",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        delivery = self.store.registration_delivery(concurrent_session)
        assert delivery is not None
        barrier = threading.Barrier(3)
        claims: list[str | None] = []

        def claim_once() -> None:
            barrier.wait()
            claims.append(
                self.store.claim_mail_delivery(
                    "registration", delivery["email_id"]
                )
            )

        workers = [threading.Thread(target=claim_once) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=2)
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        self.assertTrue(
            self.store.mark_mail_delivery(
                "registration",
                delivery["email_id"],
                claim_token=winners[0],
                sent=True,
            )
        )

        capped_session = "5" * 64
        self.assertTrue(
            self.store.begin_registration(
                capped_session,
                "attempt-cap@example.test",
                "Attempt Cap",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        capped = self.store.registration_delivery(capped_session)
        assert capped is not None
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE auth_registration_email_outbox
                SET delivery_attempts=3,claim_token='abandoned-at-cap'
                WHERE email_id=?
                """,
                (capped["email_id"],),
            )
        self.assertIsNone(self.store.registration_delivery(capped_session))
        self.assertIsNone(
            self.store.claim_mail_delivery("registration", capped["email_id"])
        )
        self.assertEqual(self.store.pending_mail_deliveries(), [])
        self.assertEqual(self.store.recover_pending_mail_claims(), 0)
        self.assertEqual(self.store.fail_exhausted_pending_mail(), 1)
        exhausted = self.store.registration_outbox(capped_session)[0]
        self.assertEqual(exhausted["status"], "SMTP_FAILED")
        self.assertEqual(exhausted["last_error"], "DeliveryAttemptsExhausted")
        self.assertFalse(
            self.store.registration_mail_status(capped_session)["can_retry"]
        )

    def test_startup_replay_fails_expired_auth_jobs_without_sending_them(self) -> None:
        registration_session = "d" * 64
        self.assertTrue(
            self.store.begin_registration(
                registration_session,
                "expired-registration@example.test",
                "Expired Registration",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        account_session = "e" * 64
        reset_email = "expired-reset@example.test"
        self.assertTrue(
            self.store.register_account(
                account_session, reset_email, "Expired Reset", self.password
            )
        )
        reset_session = "f" * 64
        self.store.begin_password_reset(
            reset_session,
            reset_email,
            None,
            mail_mode=MAIL_SMTP_PENDING,
        )
        with self.store.connect() as connection:
            connection.execute("UPDATE auth_registration_flows SET expires_at=0")
            connection.execute("UPDATE auth_password_reset_flows SET expires_at=0")

        self.assertEqual(self.store.expire_stale_pending_auth_mail(), 2)
        self.assertEqual(self.store.recover_pending_mail_claims(), 0)
        self.assertEqual(self.store.pending_mail_deliveries(), [])
        registration = self.store.registration_outbox(registration_session)[0]
        recovery = self.store.password_reset_outbox(reset_session)[0]
        for message in (registration, recovery):
            self.assertEqual(message["status"], "SMTP_FAILED")
            self.assertEqual(message["last_error"], "ExpiredBeforeDelivery")
            self.assertIsNone(message["verification_code"])

    def test_rate_limited_recovery_preserves_only_the_same_account_target(self) -> None:
        first_email = "target-a@example.test"
        second_email = "target-b@example.test"
        self.assertTrue(
            self.store.register_account(
                "1" * 64, first_email, "Target A", self.password
            )
        )
        self.assertTrue(
            self.store.register_account(
                "2" * 64, second_email, "Target B", self.password
            )
        )
        recovery_session = "3" * 64
        self.store.begin_password_reset(
            recovery_session, first_email, None
        )
        first_job = self.store.password_reset_outbox(recovery_session)[0]
        first_code = first_job["verification_code"]

        self.store.begin_password_reset(
            recovery_session, first_email, None
        )
        preserved = self.store.password_reset_outbox(recovery_session)[0]
        self.assertEqual(preserved["email_id"], first_job["email_id"])
        self.assertEqual(preserved["verification_code"], first_code)

        self.store.begin_password_reset(
            recovery_session, second_email, None
        )
        self.assertEqual(self.store.password_reset_outbox(recovery_session), [])
        with self.store.connect() as connection:
            flow = connection.execute(
                """
                SELECT account_id FROM auth_password_reset_flows
                WHERE session_digest=?
                """,
                (recovery_session,),
            ).fetchone()
        self.assertIsNotNone(flow)
        assert flow is not None
        self.assertIsNone(flow["account_id"])
        self.assertNotEqual(
            self.store.verify_password_reset_code(recovery_session, first_code),
            "verified",
        )

    def test_misspelled_auth_post_is_rejected_without_credential_journaling(self) -> None:
        cookie = self.anonymous_cookie()
        status, _, body = self.request(
            "POST",
            "/ap/signin/",
            fields={"email": self.email, "password": self.password},
            cookie=cookie,
        )
        self.assertEqual(status, 404)
        self.assertEqual(body, b"Not Found")
        self.assertEqual(self.store.journal(), [])

    def test_cross_origin_auth_post_is_forbidden(self) -> None:
        cookie = self.anonymous_cookie()
        body = urlencode({"email": self.email}).encode("utf-8")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=8)
        connection.request(
            "POST",
            "/ap/signin",
            body=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Cookie": cookie,
                "Origin": "https://evil.example",
            },
        )
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        self.assertEqual(response.status, 403)
        self.assertEqual(payload, b"Forbidden")
        self.assertEqual(self.store.journal(), [])

    def test_existing_browser_session_table_is_migrated_in_place(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy.sqlite3"
        legacy_conn = sqlite3.connect(legacy_path)
        try:
            legacy_conn.execute(
                "CREATE TABLE browser_sessions (session_digest TEXT PRIMARY KEY, reset_epoch INTEGER NOT NULL, created_at TEXT NOT NULL)"
            )
            legacy_conn.commit()
        finally:
            legacy_conn.close()
        migrated = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        with migrated.connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(browser_sessions)")}
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("account_id", columns)
        self.assertTrue(
            {
                "accounts",
                "auth_signin_flows",
                "auth_registration_flows",
                "auth_registration_email_outbox",
            }.issubset(tables)
        )

    def test_legacy_local_only_outboxes_are_migrated_without_losing_mail(self) -> None:
        legacy_path = Path(self.tempdir.name) / "legacy-mail.sqlite3"
        legacy = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        legacy.reset()
        old_session = "a" * 64
        self.assertTrue(
            legacy.begin_registration(
                old_session,
                "legacy-mail@example.test",
                "Legacy Mail",
                self.password,
                None,
            )
        )
        old_code = legacy.registration_outbox(old_session)[0][
            "verification_code"
        ]

        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                ALTER TABLE auth_registration_email_outbox
                    RENAME TO auth_registration_email_outbox_current;
                CREATE TABLE auth_registration_email_outbox (
                    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pending_id TEXT NOT NULL UNIQUE
                        REFERENCES auth_registration_flows(pending_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    template TEXT NOT NULL
                        CHECK (template='registration-verification'),
                    verification_code TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status='LOCAL_ONLY'),
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation=1),
                    created_at INTEGER NOT NULL
                );
                INSERT INTO auth_registration_email_outbox(
                    email_id,pending_id,recipient,template,verification_code,
                    status,is_simulation,created_at
                )
                SELECT email_id,pending_id,recipient,template,verification_code,
                       status,is_simulation,created_at
                FROM auth_registration_email_outbox_current;
                DROP TABLE auth_registration_email_outbox_current;

                ALTER TABLE auth_password_reset_email_outbox
                    RENAME TO auth_password_reset_email_outbox_current;
                CREATE TABLE auth_password_reset_email_outbox (
                    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reset_id TEXT NOT NULL UNIQUE
                        REFERENCES auth_password_reset_flows(reset_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    template TEXT NOT NULL
                        CHECK (template='password-reset-verification'),
                    verification_code TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status='LOCAL_ONLY'),
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation=1),
                    created_at INTEGER NOT NULL
                );
                INSERT INTO auth_password_reset_email_outbox(
                    email_id,reset_id,recipient,template,verification_code,
                    status,is_simulation,created_at
                )
                SELECT email_id,reset_id,recipient,template,verification_code,
                       status,is_simulation,created_at
                FROM auth_password_reset_email_outbox_current;
                DROP TABLE auth_password_reset_email_outbox_current;

                ALTER TABLE email_outbox RENAME TO email_outbox_current;
                CREATE TABLE email_outbox (
                    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL
                        REFERENCES accounts(account_id) ON DELETE CASCADE,
                    order_id INTEGER NOT NULL UNIQUE
                        REFERENCES orders(order_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    template TEXT NOT NULL CHECK (template='order-confirmation'),
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status='LOCAL_ONLY'),
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation=1),
                    created_at TEXT NOT NULL
                );
                INSERT INTO email_outbox(
                    email_id,account_id,order_id,recipient,template,subject,
                    payload_json,status,is_simulation,created_at
                )
                SELECT email_id,account_id,order_id,recipient,template,subject,
                       payload_json,status,is_simulation,created_at
                FROM email_outbox_current;
                DROP TABLE email_outbox_current;
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Store(legacy_path, ROOT / "schema.sql", ROOT / "fixtures")
        preserved = migrated.registration_outbox(old_session)
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0]["verification_code"], old_code)
        self.assertEqual(preserved[0]["status"], "LOCAL_ONLY")
        self.assertTrue(preserved[0]["is_simulation"])
        with migrated.connect() as connection:
            for table in (
                "auth_registration_email_outbox",
                "auth_password_reset_email_outbox",
                "email_outbox",
            ):
                columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                self.assertTrue(
                    {
                        "delivery_attempts",
                        "claim_token",
                        "last_error",
                        "attempted_at",
                        "sent_at",
                    }.issubset(columns)
                )

        smtp_session = "b" * 64
        self.assertTrue(
            migrated.begin_registration(
                smtp_session,
                "migrated-smtp@example.test",
                "Migrated SMTP",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )
        self.assertEqual(
            migrated.registration_outbox(smtp_session)[0]["status"],
            "SMTP_PENDING",
        )

    def test_preclaim_smtp_state_is_preserved_by_outbox_migration(self) -> None:
        database_path = Path(self.tempdir.name) / "preclaim-mail.sqlite3"
        preclaim = Store(database_path, ROOT / "schema.sql", ROOT / "fixtures")
        preclaim.reset()
        session_digest = "9" * 64
        self.assertTrue(
            preclaim.begin_registration(
                session_digest,
                "preclaim@example.test",
                "Preclaim SMTP",
                self.password,
                None,
                mail_mode=MAIL_SMTP_PENDING,
            )
        )

        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                ALTER TABLE auth_registration_email_outbox
                    RENAME TO auth_registration_email_outbox_current;
                CREATE TABLE auth_registration_email_outbox (
                    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pending_id TEXT NOT NULL UNIQUE
                        REFERENCES auth_registration_flows(pending_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    template TEXT NOT NULL,
                    verification_code TEXT NOT NULL,
                    status TEXT NOT NULL,
                    is_simulation INTEGER NOT NULL,
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    attempted_at INTEGER,
                    sent_at INTEGER,
                    created_at INTEGER NOT NULL
                );
                INSERT INTO auth_registration_email_outbox(
                    email_id,pending_id,recipient,template,verification_code,
                    status,is_simulation,delivery_attempts,last_error,
                    attempted_at,sent_at,created_at
                )
                SELECT email_id,pending_id,recipient,template,verification_code,
                       status,is_simulation,delivery_attempts,last_error,
                       attempted_at,sent_at,created_at
                FROM auth_registration_email_outbox_current;
                DROP TABLE auth_registration_email_outbox_current;
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = Store(database_path, ROOT / "schema.sql", ROOT / "fixtures")
        message = migrated.registration_outbox(session_digest)[0]
        self.assertEqual(message["status"], "SMTP_PENDING")
        self.assertFalse(message["is_simulation"])
        self.assertEqual(message["delivery_attempts"], 0)
        self.assertIsNotNone(migrated.registration_delivery(session_digest))


if __name__ == "__main__":
    unittest.main()
