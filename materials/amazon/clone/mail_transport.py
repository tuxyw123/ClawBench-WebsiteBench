"""Reusable outbound SMTP transport for the local Amazon clone.

No network delivery is configured by default.  Supplying SMTP environment
variables switches registration, password-recovery, and order-confirmation
outboxes from ``LOCAL_ONLY`` to an asynchronous SMTP delivery attempt.
"""

from __future__ import annotations

import ipaddress
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Mapping
from urllib.parse import urlsplit


class SMTPConfigurationError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class SMTPConfig:
    host: str
    port: int
    security: str
    sender: str
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 10.0

    def __repr__(self) -> str:
        return (
            "SMTPConfig("
            f"host={self.host!r}, port={self.port!r}, security={self.security!r}, "
            f"sender={self.sender!r}, username={'<configured>' if self.username else None}, "
            "password=<redacted>, "
            f"timeout_seconds={self.timeout_seconds!r})"
        )


_SMTP_ENV_KEYS = (
    "AMAZON_CLONE_SMTP_HOST",
    "AMAZON_CLONE_SMTP_PORT",
    "AMAZON_CLONE_SMTP_TLS",
    "AMAZON_CLONE_SMTP_USERNAME",
    "AMAZON_CLONE_SMTP_PASSWORD",
    "AMAZON_CLONE_SMTP_FROM",
    "AMAZON_CLONE_SMTP_TIMEOUT_SECONDS",
)
_REQUIRE_SMTP_ENV_KEY = "AMAZON_CLONE_REQUIRE_SMTP"


def _clean_header(value: str, field: str, *, max_bytes: int = 512) -> str:
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned.encode("utf-8")) > max_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in cleaned)
    ):
        raise SMTPConfigurationError(f"{field} must be a non-empty single-line value")
    return cleaned


def _clean_body(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 64 * 1024:
        raise SMTPConfigurationError("message body must not exceed 64 KiB")
    if any(
        (ord(character) < 32 or ord(character) == 127)
        and character not in {"\t", "\n", "\r"}
        for character in value
    ):
        raise SMTPConfigurationError("message body contains a forbidden control character")
    return value


def _mailbox(value: str, field: str, *, allow_display_name: bool) -> str:
    cleaned = _clean_header(value, field)
    parsed_addresses = getaddresses([cleaned])
    if len(parsed_addresses) != 1:
        raise SMTPConfigurationError(f"{field} must contain one valid email address")
    display_name, address = parseaddr(cleaned)
    if (
        parsed_addresses[0] != (display_name, address)
        or not address
        or address.count("@") != 1
        or any(character.isspace() for character in address)
    ):
        raise SMTPConfigurationError(f"{field} must contain one valid email address")
    if not allow_display_name and (display_name or address != cleaned):
        raise SMTPConfigurationError(f"{field} must be a bare email address")
    return cleaned


def load_smtp_config(
    environ: Mapping[str, str] | None = None,
) -> SMTPConfig | None:
    """Load strict SMTP configuration, or return ``None`` for LOCAL_ONLY mode."""

    source = os.environ if environ is None else environ
    require_value = source.get(_REQUIRE_SMTP_ENV_KEY, "0").strip().lower()
    require_aliases = {
        "": False,
        "0": False,
        "false": False,
        "no": False,
        "1": True,
        "true": True,
        "yes": True,
    }
    if require_value not in require_aliases:
        raise SMTPConfigurationError(
            "AMAZON_CLONE_REQUIRE_SMTP must be 1 or 0"
        )
    require_smtp = require_aliases[require_value]
    host_value = source.get("AMAZON_CLONE_SMTP_HOST", "").strip()
    any_smtp_value = any(source.get(key, "").strip() for key in _SMTP_ENV_KEYS)
    if not host_value:
        if require_smtp:
            raise SMTPConfigurationError(
                "AMAZON_CLONE_REQUIRE_SMTP=1 requires a complete SMTP configuration"
            )
        if any_smtp_value:
            raise SMTPConfigurationError(
                "AMAZON_CLONE_SMTP_HOST is required when any SMTP setting is present"
            )
        return None

    host = _clean_header(host_value, "SMTP host")
    tls_value = source.get("AMAZON_CLONE_SMTP_TLS", "starttls").strip().lower()
    security_aliases = {
        "1": "starttls",
        "true": "starttls",
        "yes": "starttls",
        "starttls": "starttls",
        "ssl": "ssl",
        "implicit": "ssl",
        "0": "plain",
        "false": "plain",
        "no": "plain",
        "none": "plain",
        "plain": "plain",
    }
    security = security_aliases.get(tls_value)
    if security is None:
        raise SMTPConfigurationError(
            "AMAZON_CLONE_SMTP_TLS must be starttls, ssl, or none"
        )

    default_port = 465 if security == "ssl" else 587
    try:
        port = int(source.get("AMAZON_CLONE_SMTP_PORT", str(default_port)))
    except ValueError as exc:
        raise SMTPConfigurationError("SMTP port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SMTPConfigurationError("SMTP port must be between 1 and 65535")

    username = source.get("AMAZON_CLONE_SMTP_USERNAME", "").strip() or None
    password = source.get("AMAZON_CLONE_SMTP_PASSWORD", "") or None
    if (username is None) != (password is None):
        raise SMTPConfigurationError(
            "SMTP username and password must be configured together"
        )
    if username is not None:
        username = _clean_header(username, "SMTP username")
    if security == "plain":
        try:
            parsed_host = ipaddress.ip_address(host.strip("[]"))
            host_is_loopback = parsed_host.is_loopback
        except ValueError:
            host_is_loopback = host.casefold() == "localhost"
        if not host_is_loopback:
            raise SMTPConfigurationError(
                "unencrypted SMTP is permitted only for a loopback mail server"
            )
        if username is not None:
            raise SMTPConfigurationError(
                "unencrypted SMTP cannot be configured with authentication"
            )

    sender = _mailbox(
        source.get("AMAZON_CLONE_SMTP_FROM", ""),
        "AMAZON_CLONE_SMTP_FROM",
        allow_display_name=True,
    )
    try:
        timeout_seconds = float(
            source.get("AMAZON_CLONE_SMTP_TIMEOUT_SECONDS", "10")
        )
    except ValueError as exc:
        raise SMTPConfigurationError("SMTP timeout must be numeric") from exc
    if not 1 <= timeout_seconds <= 60:
        raise SMTPConfigurationError("SMTP timeout must be between 1 and 60 seconds")

    return SMTPConfig(
        host=host,
        port=port,
        security=security,
        sender=sender,
        username=username,
        password=password,
        timeout_seconds=timeout_seconds,
    )


def smtp_public_summary(config: SMTPConfig | None) -> dict[str, object]:
    if config is None:
        return {"mode": "LOCAL_ONLY"}
    return {
        "mode": "SMTP",
        "host": config.host,
        "port": config.port,
        "security": config.security,
        "authentication": bool(config.username),
        "sender": config.sender,
    }


def load_local_inbox_url(
    config: SMTPConfig | None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return a validated loopback inbox URL for the local capture profile."""

    source = os.environ if environ is None else environ
    raw = source.get("AMAZON_CLONE_LOCAL_INBOX_URL", "").strip()
    if not raw:
        return None
    if config is None:
        raise SMTPConfigurationError(
            "AMAZON_CLONE_LOCAL_INBOX_URL requires SMTP configuration"
        )
    try:
        config_host = ipaddress.ip_address(config.host.strip("[]"))
    except ValueError as exc:
        raise SMTPConfigurationError(
            "AMAZON_CLONE_LOCAL_INBOX_URL is allowed only with loopback SMTP"
        ) from exc
    if not config_host.is_loopback:
        raise SMTPConfigurationError(
            "AMAZON_CLONE_LOCAL_INBOX_URL is allowed only with loopback SMTP"
        )
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SMTPConfigurationError("local inbox URL has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SMTPConfigurationError(
            "AMAZON_CLONE_LOCAL_INBOX_URL must be a loopback HTTP origin"
        )
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{port}/"


def send_smtp_message(
    config: SMTPConfig,
    *,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    """Send one plain-text message without logging credentials or message content."""

    safe_recipient = _mailbox(recipient, "recipient", allow_display_name=False)
    safe_subject = _clean_header(subject, "subject")
    safe_body = _clean_body(body)
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = safe_recipient
    message["Subject"] = safe_subject
    message.set_content(safe_body)

    context = ssl.create_default_context()
    if config.security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            config.host,
            config.port,
            timeout=config.timeout_seconds,
            context=context,
        )
    else:
        client = smtplib.SMTP(
            config.host, config.port, timeout=config.timeout_seconds
        )
    with client:
        client.ehlo()
        if config.security == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if config.username is not None:
            assert config.password is not None
            client.login(config.username, config.password)
        client.send_message(message)


def smtp_error_summary(error: BaseException) -> str:
    """Return an admin-safe failure category without exception text or secrets."""

    name = type(error).__name__
    if isinstance(error, smtplib.SMTPResponseException):
        return f"{name}:smtp-{int(error.smtp_code)}"
    return name[:96] or "SMTPDeliveryError"


__all__ = [
    "SMTPConfig",
    "SMTPConfigurationError",
    "load_local_inbox_url",
    "load_smtp_config",
    "send_smtp_message",
    "smtp_error_summary",
    "smtp_public_summary",
]
