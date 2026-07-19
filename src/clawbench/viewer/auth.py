"""Single-user authentication, signed sessions, CSRF, and login throttling."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


SESSION_COOKIE = "clawbench_viewer_session"
LOGIN_CSRF_COOKIE = "clawbench_viewer_login_csrf"


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _secret_env(name: str) -> str:
    direct = os.environ.get(name)
    if direct is not None:
        return direct.strip()
    filename = os.environ.get(f"{name}_FILE")
    if filename:
        return open(filename, encoding="utf-8").read().strip()
    return ""


@dataclass(frozen=True)
class AuthSettings:
    username: str
    password_hash: str
    session_secret: str
    cookie_secure: bool = True
    session_seconds: int = 8 * 60 * 60
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    login_attempts: int = 5
    login_window_seconds: int = 300

    @classmethod
    def from_env(cls) -> "AuthSettings":
        username = _secret_env("CLAWBENCH_VIEWER_USERNAME")
        password_hash = _secret_env("CLAWBENCH_VIEWER_PASSWORD_HASH")
        secret = _secret_env("CLAWBENCH_VIEWER_SESSION_SECRET")
        if not username or not password_hash or len(secret) < 32:
            raise ValueError(
                "CLAWBENCH_VIEWER_USERNAME, CLAWBENCH_VIEWER_PASSWORD_HASH, and a "
                "32+ character CLAWBENCH_VIEWER_SESSION_SECRET are required"
            )
        hosts_value = _secret_env("CLAWBENCH_VIEWER_TRUSTED_HOSTS") or (
            "localhost,127.0.0.1"
        )
        hosts = tuple(
            host.strip()
            for host in hosts_value.split(",")
            if host.strip()
        )
        return cls(
            username=username,
            password_hash=password_hash,
            session_secret=secret,
            cookie_secure=_bool_env("CLAWBENCH_VIEWER_COOKIE_SECURE", True),
            trusted_hosts=hosts,
        )


class TokenSigner:
    def __init__(self, secret: str) -> None:
        self.key = secret.encode("utf-8")

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def dumps(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(self.key, body, hashlib.sha256).digest()
        return f"{self._encode(body)}.{self._encode(signature)}"

    def loads(self, token: str, *, purpose: str) -> dict[str, Any] | None:
        try:
            body_part, signature_part = token.split(".", 1)
            body = self._decode(body_part)
            signature = self._decode(signature_part)
            expected = hmac.new(self.key, body, hashlib.sha256).digest()
            payload = json.loads(body)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
        if not hmac.compare_digest(signature, expected):
            return None
        if payload.get("purpose") != purpose or payload.get("exp", 0) < int(time.time()):
            return None
        return payload


class AuthManager:
    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings
        self.signer = TokenSigner(settings.session_secret)
        self.hasher = PasswordHasher()

    def verify_password(self, username: str, password: str) -> bool:
        if not hmac.compare_digest(username, self.settings.username):
            return False
        try:
            return self.hasher.verify(self.settings.password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def login_csrf(self) -> str:
        return self.signer.dumps(
            {
                "purpose": "login-csrf",
                "nonce": secrets.token_urlsafe(24),
                "exp": int(time.time()) + 15 * 60,
            }
        )

    def verify_login_csrf(self, form_token: str, cookie_token: str | None) -> bool:
        return bool(
            cookie_token
            and hmac.compare_digest(form_token, cookie_token)
            and self.signer.loads(form_token, purpose="login-csrf")
        )

    def session_token(self) -> str:
        return self.signer.dumps(
            {
                "purpose": "session",
                "username": self.settings.username,
                "csrf": secrets.token_urlsafe(32),
                "exp": int(time.time()) + self.settings.session_seconds,
            }
        )

    def session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        payload = self.signer.loads(token, purpose="session")
        if payload and hmac.compare_digest(
            str(payload.get("username", "")), self.settings.username
        ):
            return payload
        return None

    @staticmethod
    def csrf_matches(session: dict[str, Any], supplied: str | None) -> bool:
        expected = session.get("csrf")
        return bool(expected and supplied and hmac.compare_digest(str(expected), supplied))


class LoginLimiter:
    def __init__(self, attempts: int, window_seconds: int) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        values = self._failures[key]
        while values and values[0] <= now - self.window_seconds:
            values.popleft()
        return values

    def allowed(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) < self.attempts

    def failure(self, key: str) -> None:
        with self._lock:
            self._prune(key, time.monotonic()).append(time.monotonic())

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    return PasswordHasher().hash(password)
