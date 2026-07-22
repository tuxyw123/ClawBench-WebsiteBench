"""Session-owned state and validation for secondary Amazon clone surfaces.

These flows model local drafts and previews only. They never create a seller
account, public registry, gift-card balance, payment, or video entitlement.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


GIFT_CARDS_PATH = "/gift-cards/b/"
GIFT_CARD_PREVIEW_PATH = "/gift-cards/purchase-preview"
GIFT_CARD_BALANCE_PATH = "/gc/balance/"
GIFT_CARD_REDEEM_PATH = "/gc/redeem/"
SELL_PATH = "/b/"
SELL_DRAFT_PATH = "/b/sell/draft"
REGISTRY_PATH = "/gp/browse.html"
REGISTRY_SEARCH_PATH = "/registry/search"
REGISTRY_CREATE_PATH = "/registry/create"
REGISTRY_DETAIL_PATH = "/registry/detail"
PRIME_VIDEO_PATH = "/Amazon-Video/b/"

GIFT_DESIGNS = {
    "classic": "Classic smile",
    "birthday": "Birthday confetti",
    "thanks": "A little thanks",
}
GIFT_AMOUNTS_MINOR = (2500, 5000, 10000, 20000)
SELL_CATEGORIES = {
    "books": "Books",
    "electronics": "Electronics",
    "home": "Home & Kitchen",
    "toys": "Toys & Games",
    "beauty": "Beauty & Personal Care",
}
SELL_CONDITIONS = {
    "new": "New",
    "like-new": "Used - Like New",
    "good": "Used - Good",
}
REGISTRY_TYPES = {
    "wedding": "Wedding Registry",
    "baby": "Baby Registry",
    "gift": "Gift List",
}
_REDEMPTION_PEPPER = secrets.token_bytes(32)
_SAFE_CODE = re.compile(r"[A-Z0-9][A-Z0-9-]{7,31}")
_PRICE = re.compile(r"(?:[1-9][0-9]{0,3}|5000)(?:\.[0-9]{2})?")


class SpecialtyValidationError(ValueError):
    """A public specialty form did not satisfy its strict contract."""


def ensure_specialty_schema(store: Any) -> None:
    """Install isolated additive tables without changing the core schema."""

    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS specialty_gift_card_previews (
                preview_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_digest TEXT NOT NULL
                    REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
                design TEXT NOT NULL CHECK (design IN ('classic','birthday','thanks')),
                amount_minor INTEGER NOT NULL CHECK (amount_minor IN (2500,5000,10000,20000)),
                recipient_kind TEXT NOT NULL CHECK (recipient_kind IN ('self','gift')),
                status TEXT NOT NULL CHECK (status='LOCAL_PREVIEW'),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS specialty_gift_preview_session_idx
                ON specialty_gift_card_previews(session_digest,preview_id DESC);
            CREATE TABLE IF NOT EXISTS specialty_gift_redemption_attempts (
                redemption_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_digest TEXT NOT NULL
                    REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
                code_fingerprint TEXT NOT NULL CHECK (length(code_fingerprint)=64),
                status TEXT NOT NULL CHECK (status='NOT_APPLIED'),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS specialty_gift_redemption_session_idx
                ON specialty_gift_redemption_attempts(session_digest,redemption_id DESC);
            CREATE TABLE IF NOT EXISTS specialty_seller_drafts (
                draft_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_digest TEXT NOT NULL
                    REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
                title TEXT NOT NULL CHECK (length(title) BETWEEN 5 AND 100),
                category TEXT NOT NULL CHECK (category IN ('books','electronics','home','toys','beauty')),
                item_condition TEXT NOT NULL CHECK (item_condition IN ('new','like-new','good')),
                price_minor INTEGER NOT NULL CHECK (price_minor BETWEEN 100 AND 500000),
                quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                description TEXT NOT NULL CHECK (length(description)<=500),
                status TEXT NOT NULL CHECK (status='LOCAL_DRAFT'),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS specialty_seller_draft_session_idx
                ON specialty_seller_drafts(session_digest,draft_id DESC);
            CREATE TABLE IF NOT EXISTS specialty_registries (
                registry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_digest TEXT NOT NULL
                    REFERENCES browser_sessions(session_digest) ON DELETE CASCADE,
                registry_type TEXT NOT NULL CHECK (registry_type IN ('wedding','baby','gift')),
                owner_name TEXT NOT NULL CHECK (length(owner_name) BETWEEN 2 AND 80),
                registry_name TEXT NOT NULL CHECK (length(registry_name) BETWEEN 3 AND 100),
                event_date TEXT NOT NULL CHECK (length(event_date) IN (0,10)),
                status TEXT NOT NULL CHECK (status='LOCAL_PRIVATE_DRAFT'),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS specialty_registry_session_idx
                ON specialty_registries(session_digest,registry_id DESC);
            """
        )


def _plain_text(value: str, *, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise SpecialtyValidationError(f"{field} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not minimum <= len(normalized) <= maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in normalized
    ):
        raise SpecialtyValidationError(f"{field} is invalid")
    return normalized


def _optional_text(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise SpecialtyValidationError(f"{field} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if len(normalized) > maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in normalized
    ):
        raise SpecialtyValidationError(f"{field} is invalid")
    return normalized


def _positive_id(value: int | str) -> int | None:
    if isinstance(value, bool):
        return None
    raw = str(value)
    if re.fullmatch(r"[1-9][0-9]{0,18}", raw) is None:
        return None
    parsed = int(raw)
    return parsed if parsed <= (1 << 63) - 1 else None


def create_gift_card_preview(
    store: Any, session_digest: str, design: str, amount: str, recipient_kind: str
) -> dict[str, Any]:
    if design not in GIFT_DESIGNS or recipient_kind not in {"self", "gift"}:
        raise SpecialtyValidationError("gift card selection is invalid")
    if re.fullmatch(r"(?:25|50|100|200)", amount or "") is None:
        raise SpecialtyValidationError("gift card amount is invalid")
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO specialty_gift_card_previews(
                session_digest,design,amount_minor,recipient_kind,status,created_at
            ) VALUES (?,?,?,?,'LOCAL_PREVIEW',?)
            """,
            (
                session_digest,
                design,
                int(amount) * 100,
                recipient_kind,
                store.now(connection),
            ),
        )
        preview_id = int(cursor.lastrowid)
        connection.commit()
    return gift_card_preview(store, session_digest, preview_id) or {}


def gift_card_preview(
    store: Any, session_digest: str, preview_id: int | str
) -> dict[str, Any] | None:
    normalized = _positive_id(preview_id)
    if normalized is None:
        return None
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT preview_id,design,amount_minor,recipient_kind,status,created_at
            FROM specialty_gift_card_previews
            WHERE preview_id=? AND session_digest=?
            """,
            (normalized, session_digest),
        ).fetchone()
    return dict(row) if row else None


def redeem_gift_card(store: Any, session_digest: str, claim_code: str) -> dict[str, Any]:
    normalized = unicodedata.normalize("NFKC", claim_code).strip().upper()
    if _SAFE_CODE.fullmatch(normalized) is None:
        raise SpecialtyValidationError(
            "claim code must use 8 to 32 letters, numbers, or hyphens"
        )
    fingerprint = hmac.new(
        _REDEMPTION_PEPPER, normalized.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO specialty_gift_redemption_attempts(
                session_digest,code_fingerprint,status,created_at
            ) VALUES (?,?,'NOT_APPLIED',?)
            """,
            (session_digest, fingerprint, store.now(connection)),
        )
        redemption_id = int(cursor.lastrowid)
        connection.commit()
    return {"redemption_id": redemption_id, "status": "NOT_APPLIED"}


def gift_card_balance(store: Any, session_digest: str) -> dict[str, int]:
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        attempts = int(
            connection.execute(
                "SELECT COUNT(*) FROM specialty_gift_redemption_attempts WHERE session_digest=?",
                (session_digest,),
            ).fetchone()[0]
        )
    return {"balance_minor": 0, "redemption_attempts": attempts}


def save_seller_draft(
    store: Any, session_digest: str, fields: dict[str, str]
) -> dict[str, Any]:
    title = _plain_text(
        fields.get("title", ""), field="title", minimum=5, maximum=100
    )
    category = fields.get("category", "")
    condition = fields.get("condition", "")
    if category not in SELL_CATEGORIES or condition not in SELL_CONDITIONS:
        raise SpecialtyValidationError("listing category or condition is invalid")
    raw_price = fields.get("price", "")
    if _PRICE.fullmatch(raw_price) is None:
        raise SpecialtyValidationError("price must be between $1.00 and $5,000.00")
    try:
        price_minor = int(Decimal(raw_price) * 100)
    except (InvalidOperation, ValueError):
        raise SpecialtyValidationError("price is invalid") from None
    if not 100 <= price_minor <= 500_000:
        raise SpecialtyValidationError("price must be between $1.00 and $5,000.00")
    raw_quantity = fields.get("quantity", "")
    if re.fullmatch(r"(?:[1-9]|[12][0-9]|30)", raw_quantity) is None:
        raise SpecialtyValidationError("quantity must be 1 through 30")
    description = _optional_text(
        fields.get("description", ""), field="description", maximum=500
    )
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        now = store.now(connection)
        cursor = connection.execute(
            """
            INSERT INTO specialty_seller_drafts(
                session_digest,title,category,item_condition,price_minor,quantity,
                description,status,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,'LOCAL_DRAFT',?,?)
            """,
            (
                session_digest,
                title,
                category,
                condition,
                price_minor,
                int(raw_quantity),
                description,
                now,
                now,
            ),
        )
        draft_id = int(cursor.lastrowid)
        connection.commit()
    return seller_draft(store, session_digest, draft_id) or {}


def seller_draft(
    store: Any, session_digest: str, draft_id: int | str
) -> dict[str, Any] | None:
    normalized = _positive_id(draft_id)
    if normalized is None:
        return None
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT draft_id,title,category,item_condition,price_minor,quantity,
                   description,status,created_at,updated_at
            FROM specialty_seller_drafts
            WHERE draft_id=? AND session_digest=?
            """,
            (normalized, session_digest),
        ).fetchone()
    return dict(row) if row else None


def seller_drafts(store: Any, session_digest: str) -> list[dict[str, Any]]:
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT draft_id,title,category,item_condition,price_minor,quantity,
                   description,status,created_at,updated_at
            FROM specialty_seller_drafts WHERE session_digest=?
            ORDER BY draft_id DESC LIMIT 12
            """,
            (session_digest,),
        ).fetchall()
    return [dict(row) for row in rows]


def _validated_event_date(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw) is None:
        raise SpecialtyValidationError("event date is invalid")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise SpecialtyValidationError("event date is invalid") from None
    if not 2000 <= parsed.year <= 2100:
        raise SpecialtyValidationError("event date is invalid")
    return raw


def create_registry(
    store: Any, session_digest: str, fields: dict[str, str]
) -> dict[str, Any]:
    registry_type = fields.get("registryType", "")
    if registry_type not in REGISTRY_TYPES:
        raise SpecialtyValidationError("registry type is invalid")
    owner_name = _plain_text(
        fields.get("ownerName", ""), field="owner name", minimum=2, maximum=80
    )
    registry_name = _plain_text(
        fields.get("registryName", ""),
        field="registry name",
        minimum=3,
        maximum=100,
    )
    event_date = _validated_event_date(fields.get("eventDate", ""))
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO specialty_registries(
                session_digest,registry_type,owner_name,registry_name,event_date,
                status,created_at
            ) VALUES (?,?,?,?,?,'LOCAL_PRIVATE_DRAFT',?)
            """,
            (
                session_digest,
                registry_type,
                owner_name,
                registry_name,
                event_date,
                store.now(connection),
            ),
        )
        registry_id = int(cursor.lastrowid)
        connection.commit()
    return registry(store, session_digest, registry_id) or {}


def registry(
    store: Any, session_digest: str, registry_id: int | str
) -> dict[str, Any] | None:
    normalized = _positive_id(registry_id)
    if normalized is None:
        return None
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        row = connection.execute(
            """
            SELECT registry_id,registry_type,owner_name,registry_name,event_date,
                   status,created_at
            FROM specialty_registries
            WHERE registry_id=? AND session_digest=?
            """,
            (normalized, session_digest),
        ).fetchone()
    return dict(row) if row else None


def registries(store: Any, session_digest: str) -> list[dict[str, Any]]:
    ensure_specialty_schema(store)
    store.ensure_session(session_digest)
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT registry_id,registry_type,owner_name,registry_name,event_date,
                   status,created_at
            FROM specialty_registries WHERE session_digest=?
            ORDER BY registry_id DESC LIMIT 12
            """,
            (session_digest,),
        ).fetchall()
    return [dict(row) for row in rows]


_DEMO_REGISTRIES = (
    {
        "demo_id": "demo-welcome",
        "registry_type": "gift",
        "owner_name": "Amazon Clone Demo",
        "registry_name": "Welcome Home Picks",
        "event_date": "",
        "status": "LOCAL_DEMO",
    },
    {
        "demo_id": "demo-reading",
        "registry_type": "gift",
        "owner_name": "Amazon Clone Demo",
        "registry_name": "A Reader's Shelf",
        "event_date": "",
        "status": "LOCAL_DEMO",
    },
)


def search_registries(
    store: Any, session_digest: str, query: str
) -> tuple[str, list[dict[str, Any]]]:
    normalized = _plain_text(
        query, field="registry search", minimum=2, maximum=60
    )
    needle = normalized.casefold()
    own = [
        {**item, "is_own": True}
        for item in registries(store, session_digest)
        if needle in f"{item['owner_name']} {item['registry_name']}".casefold()
    ]
    demos = [
        {**item, "is_own": False}
        for item in _DEMO_REGISTRIES
        if needle in f"{item['owner_name']} {item['registry_name']}".casefold()
    ]
    return normalized, own + demos
