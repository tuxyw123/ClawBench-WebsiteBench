"""Password, opaque-token, email, and safe-redirect helpers."""

from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
UPPER_PATTERN = re.compile(r"[A-Z]")
LOWER_PATTERN = re.compile(r"[a-z]")
DIGIT_PATTERN = re.compile(r"[0-9]")


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def valid_email(email: str) -> bool:
    normalized = normalize_email(email)
    return len(normalized) <= 254 and EMAIL_PATTERN.fullmatch(normalized) is not None


def password_error(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if len(password) > 200:
        return "Password must be at most 200 characters."
    if not UPPER_PATTERN.search(password) or not LOWER_PATTERN.search(password) or not DIGIT_PATTERN.search(
        password
    ):
        return "Password must include an uppercase letter, a lowercase letter, and a digit."
    return None


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_next_path(value: str | None, default: str = "/") -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return default
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or "\\" in value or "\x00" in value:
        return default
    return value

