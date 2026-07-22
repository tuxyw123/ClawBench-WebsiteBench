from __future__ import annotations

import smtplib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mail_transport import (  # noqa: E402
    SMTPConfigurationError,
    load_local_inbox_url,
    load_smtp_config,
    send_smtp_message,
    smtp_error_summary,
    smtp_public_summary,
)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_count = 0
        self.starttls_called = False
        self.login_credentials: tuple[str, str] | None = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def ehlo(self) -> None:
        self.ehlo_count += 1

    def starttls(self, *, context: object) -> None:
        self.starttls_called = context is not None

    def login(self, username: str, password: str) -> None:
        self.login_credentials = (username, password)

    def send_message(self, message: object) -> None:
        self.message = message


class MailTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTP.instances.clear()

    def smtp_environment(self) -> dict[str, str]:
        return {
            "AMAZON_CLONE_SMTP_HOST": "smtp.example.test",
            "AMAZON_CLONE_SMTP_PORT": "2525",
            "AMAZON_CLONE_SMTP_TLS": "starttls",
            "AMAZON_CLONE_SMTP_USERNAME": "smtp-user",
            "AMAZON_CLONE_SMTP_PASSWORD": "smtp-secret",
            "AMAZON_CLONE_SMTP_FROM": "Amazon Clone <no-reply@example.test>",
            "AMAZON_CLONE_SMTP_TIMEOUT_SECONDS": "7",
        }

    def test_missing_configuration_is_local_only_and_partial_configuration_fails_closed(self) -> None:
        self.assertIsNone(load_smtp_config({}))
        self.assertIsNone(load_smtp_config({"AMAZON_CLONE_REQUIRE_SMTP": "0"}))
        self.assertEqual(smtp_public_summary(None), {"mode": "LOCAL_ONLY"})
        with self.assertRaisesRegex(
            SMTPConfigurationError, "requires a complete SMTP configuration"
        ):
            load_smtp_config({"AMAZON_CLONE_REQUIRE_SMTP": "1"})
        with self.assertRaisesRegex(
            SMTPConfigurationError, "must be 1 or 0"
        ):
            load_smtp_config({"AMAZON_CLONE_REQUIRE_SMTP": "sometimes"})
        with self.assertRaises(SMTPConfigurationError):
            load_smtp_config({"AMAZON_CLONE_SMTP_FROM": "x@example.test"})
        with self.assertRaises(SMTPConfigurationError):
            load_smtp_config(
                {
                    "AMAZON_CLONE_SMTP_HOST": "smtp.example.test",
                    "AMAZON_CLONE_SMTP_FROM": "x@example.test",
                    "AMAZON_CLONE_SMTP_USERNAME": "only-user",
                }
            )
        with self.assertRaises(SMTPConfigurationError):
            load_smtp_config(
                {
                    "AMAZON_CLONE_SMTP_HOST": "smtp.example.test",
                    "AMAZON_CLONE_SMTP_FROM": "safe@example.test\r\nBcc: bad@example.test",
                }
            )
        with self.assertRaises(SMTPConfigurationError):
            load_smtp_config(
                {
                    "AMAZON_CLONE_SMTP_HOST": "smtp.example.test",
                    "AMAZON_CLONE_SMTP_FROM": (
                        "safe@example.test, second@example.test"
                    ),
                }
            )

    def test_configuration_summary_and_repr_never_include_password(self) -> None:
        config = load_smtp_config(
            {
                **self.smtp_environment(),
                "AMAZON_CLONE_REQUIRE_SMTP": "1",
            }
        )
        self.assertIsNotNone(config)
        assert config is not None
        self.assertNotIn("smtp-secret", repr(config))
        summary = smtp_public_summary(config)
        self.assertEqual(summary["mode"], "SMTP")
        self.assertEqual(summary["security"], "starttls")
        self.assertTrue(summary["authentication"])
        self.assertNotIn("password", summary)
        self.assertNotIn("username", summary)

    def test_unencrypted_smtp_is_loopback_only_and_cannot_send_credentials(self) -> None:
        loopback = {
            "AMAZON_CLONE_SMTP_HOST": "127.0.0.1",
            "AMAZON_CLONE_SMTP_TLS": "none",
            "AMAZON_CLONE_SMTP_FROM": "no-reply@example.test",
        }
        config = load_smtp_config(loopback)
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.security, "plain")
        self.assertIsNone(config.username)

        with self.assertRaises(SMTPConfigurationError):
            load_smtp_config(
                {
                    **loopback,
                    "AMAZON_CLONE_SMTP_HOST": "smtp.example.test",
                }
            )
        with self.assertRaises(SMTPConfigurationError):
            load_smtp_config(
                {
                    **loopback,
                    "AMAZON_CLONE_SMTP_USERNAME": "smtp-user",
                    "AMAZON_CLONE_SMTP_PASSWORD": "smtp-secret",
                }
            )

    def test_local_inbox_link_requires_the_loopback_capture_profile(self) -> None:
        local_environment = {
            "AMAZON_CLONE_SMTP_HOST": "127.0.0.1",
            "AMAZON_CLONE_SMTP_PORT": "1025",
            "AMAZON_CLONE_SMTP_TLS": "none",
            "AMAZON_CLONE_SMTP_FROM": "no-reply@amazon-clone.local",
            "AMAZON_CLONE_LOCAL_INBOX_URL": "http://127.0.0.1:8155/",
        }
        local_config = load_smtp_config(local_environment)
        self.assertEqual(
            load_local_inbox_url(local_config, local_environment),
            "http://127.0.0.1:8155/",
        )
        for unsafe_url in (
            "https://127.0.0.1:8155/",
            "http://example.test:8155/",
            "http://127.0.0.1:8155/path",
            "http://user@127.0.0.1:8155/",
        ):
            with self.subTest(unsafe_url=unsafe_url):
                with self.assertRaises(SMTPConfigurationError):
                    load_local_inbox_url(
                        local_config,
                        {
                            **local_environment,
                            "AMAZON_CLONE_LOCAL_INBOX_URL": unsafe_url,
                        },
                    )
        external_config = load_smtp_config(self.smtp_environment())
        with self.assertRaises(SMTPConfigurationError):
            load_local_inbox_url(
                external_config,
                {"AMAZON_CLONE_LOCAL_INBOX_URL": "http://127.0.0.1:8155/"},
            )

    def test_starttls_auth_and_message_delivery_use_stdlib_smtp(self) -> None:
        config = load_smtp_config(self.smtp_environment())
        assert config is not None
        with patch("mail_transport.smtplib.SMTP", FakeSMTP):
            send_smtp_message(
                config,
                recipient="buyer@example.test",
                subject="Safe subject",
                body="Synthetic message body",
            )
        self.assertEqual(len(FakeSMTP.instances), 1)
        client = FakeSMTP.instances[0]
        self.assertEqual((client.host, client.port, client.timeout), ("smtp.example.test", 2525, 7.0))
        self.assertEqual(client.ehlo_count, 2)
        self.assertTrue(client.starttls_called)
        self.assertEqual(client.login_credentials, ("smtp-user", "smtp-secret"))
        self.assertEqual(client.message["To"], "buyer@example.test")
        self.assertEqual(client.message["Subject"], "Safe subject")

    def test_recipient_header_injection_is_rejected_and_errors_are_sanitized(self) -> None:
        config = load_smtp_config(self.smtp_environment())
        assert config is not None
        with self.assertRaises(SMTPConfigurationError):
            send_smtp_message(
                config,
                recipient="buyer@example.test\nBcc: attacker@example.test",
                subject="Safe",
                body="Body",
            )
        error = smtplib.SMTPAuthenticationError(535, b"secret recipient@example.test")
        summary = smtp_error_summary(error)
        self.assertEqual(summary, "SMTPAuthenticationError:smtp-535")
        self.assertNotIn("secret", summary)
        self.assertNotIn("recipient", summary)

    def test_subject_and_body_limits_reject_control_characters_and_oversize(self) -> None:
        config = load_smtp_config(self.smtp_environment())
        assert config is not None
        invalid_messages = (
            ("unsafe\x00subject", "Body"),
            ("x" * 513, "Body"),
            ("Safe subject", "unsafe\x00body"),
            ("Safe subject", "x" * (64 * 1024 + 1)),
        )
        with patch("mail_transport.smtplib.SMTP", FakeSMTP):
            for subject, body in invalid_messages:
                with self.subTest(subject_length=len(subject), body_length=len(body)):
                    with self.assertRaises(SMTPConfigurationError):
                        send_smtp_message(
                            config,
                            recipient="buyer@example.test",
                            subject=subject,
                            body=body,
                        )
        self.assertEqual(FakeSMTP.instances, [])


if __name__ == "__main__":
    unittest.main()
