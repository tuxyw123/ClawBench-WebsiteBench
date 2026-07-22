from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from deals_catalog import load_deals_default_card_offers
from payment_methods import (
    LEGACY_TEST_CARD,
    PAYMENT_APPROVED,
    PAYMENT_DECLINED,
    SANDBOX_CARD_APPROVED,
    payment_method,
    payment_method_label,
)
from product_options import (
    UNAVAILABLE_SELECTION_COPY,
    canonical_selection_key,
    default_selection,
    load_source_option_specs,
    load_source_transaction_quote_specs,
    normalize_complete_selection,
    resolve_transaction_quote,
)
from search_commerce import load_search_commerce_cards
from search_catalog import SOURCE_DEPARTMENTS
from review_store import (
    ReviewNotFound,
    install_schema as install_review_schema,
    list_reviews as list_local_reviews,
    register_review_product as register_local_review_product,
    reset_review_data,
    toggle_helpful_vote as toggle_local_review_helpful,
    upsert_review as upsert_local_review,
)


TARGET_ASIN = "B0874XN4D8"
TARGET_QUANTITY = "2"
TASK_ID = "900136"
BEST_SELLERS_PATH = "/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
PDP_PATH = "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
DESKTOP_TERMINAL_PATH = "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance"
MOBILE_TERMINAL_PATH = "/cart/add-to-cart/ref=mw_dp_buy_crt"
TERMINAL_PATHS = {DESKTOP_TERMINAL_PATH, MOBILE_TERMINAL_PATH}
HOME_PDP_EVIDENCE_FIXTURE = "home-pdp-evidence.json"
ACTIVE_CART_STATE = "ACTIVE"
SAVED_CART_STATE = "SAVED"
_CART_STATES = {ACTIVE_CART_STATE, SAVED_CART_STATE}
CART_LINE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
COMPARE_LINE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
COMPARE_LIMIT = 4
CHECKOUT_STATES = (
    "CART_READY",
    "ADDRESS_SELECTED",
    "DELIVERY_SELECTED",
    "PAYMENT_SELECTED",
    "PLACED",
)
CHECKOUT_MODE_CART = "CART"
CHECKOUT_MODE_BUY_NOW = "BUY_NOW"
DELIVERY_SHIPPING_MINOR = {"standard": 0, "expedited": 1299}
SUPPORTED_DELIVERY_COUNTRIES: tuple[tuple[str, str], ...] = (
    ("SG", "Singapore"),
    ("US", "United States"),
    ("CA", "Canada"),
    ("GB", "United Kingdom"),
    ("AU", "Australia"),
)
SUPPORTED_DELIVERY_COUNTRY_CODES = frozenset(
    code for code, _ in SUPPORTED_DELIVERY_COUNTRIES
)
# Kept as an import-compatible name for older callers.  The public checkout
# now exposes several explicit sandbox scenarios from payment_methods.py.
TEST_PAYMENT_METHOD = SANDBOX_CARD_APPROVED
ORDER_STATUSES = frozenset(
    {
        "PREPARING",
        "SHIPPED",
        "DELIVERED",
        "CANCELLED",
        "RETURN_REQUESTED",
        "RETURN_RECEIVED",
        "REFUNDED",
    }
)
SHIPMENT_STATUSES = frozenset({"PREPARING", "SHIPPED", "DELIVERED", "CANCELLED"})
RETURN_STATUSES = frozenset({"REQUESTED", "RECEIVED", "REFUNDED"})
RETURN_REASON_CODES = frozenset(
    {"DAMAGED", "DEFECTIVE", "NOT_AS_DESCRIBED", "WRONG_ITEM", "NO_LONGER_NEEDED"}
)
RETURN_NOTE_MAX_LENGTH = 500
LOCAL_SIMULATED_CARRIER = "Amazon Clone Local Carrier"
SIMULATION_NOTICE = (
    "Simulation only: no real card is charged, no carrier shipment is booked, "
    "and no real-world order is created."
)
REDACTED_REJECTED_POST_BODY = "<redacted rejected POST body>"

PASSWORD_SCHEME = "scrypt-v1"
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024
PASSWORD_SALT_BYTES = 32
REGISTRATION_CODE_BYTES = 32
REGISTRATION_CODE_TTL_SECONDS = 10 * 60
REGISTRATION_CODE_MAX_ATTEMPTS = 5
PASSWORD_RESET_CODE_TTL_SECONDS = 10 * 60
PASSWORD_RESET_CODE_MAX_ATTEMPTS = 5
AUTH_MAIL_COOLDOWN_SECONDS = 30
AUTH_MAIL_WINDOW_SECONDS = 60 * 60
AUTH_MAIL_MAX_SENDS_PER_WINDOW = 6
MAIL_LOCAL_ONLY = "LOCAL_ONLY"
MAIL_SMTP_PENDING = "SMTP_PENDING"
MAIL_SMTP_SENT = "SMTP_SENT"
MAIL_SMTP_FAILED = "SMTP_FAILED"
MAIL_DELIVERY_STATUSES = frozenset(
    {MAIL_LOCAL_ONLY, MAIL_SMTP_PENDING, MAIL_SMTP_SENT, MAIL_SMTP_FAILED}
)
MAIL_DELIVERY_MAX_ATTEMPTS = 3
MAIL_ERROR_SUMMARY_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9_]{0,95}(?::smtp-[1-5][0-9]{2})?"
)
SQLITE_INTEGER_MAX = (1 << 63) - 1
ORDER_ACTION_HMAC_KEY = hashlib.sha256(
    b"amazon-clone-local-order-action-v1"
).digest()
_DUMMY_SALT = bytes.fromhex(
    "7df1c7ca11974cad61c349713f974ba3f07f14b17e3c323cb3449f1560659fbb"
)


def normalize_email(value: str) -> str:
    """Canonical account identifier used consistently for registration and sign-in."""

    return unicodedata.normalize("NFKC", value).strip().casefold()


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )


def password_record(password: str) -> tuple[bytes, bytes, str]:
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    return salt, _scrypt(password, salt), PASSWORD_SCHEME


def verify_password(password: str, salt: bytes, expected_hash: bytes) -> bool:
    candidate = _scrypt(password, salt)
    return hmac.compare_digest(candidate, expected_hash)


_DUMMY_HASH = _scrypt("amazon-clone-invalid-account", _DUMMY_SALT)


class ContractError(ValueError):
    pass


class CheckoutReconciliationRequired(ContractError):
    """A final checkout write atomically invalidated stale browser state."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        message = {
            "cart-changed": (
                "the simulated payment is stale; select a sandbox payment method again"
            ),
            "unsupported-delivery-country": (
                "checkout delivery country is not supported"
            ),
        }.get(reason, "checkout state must be reviewed")
        super().__init__(message)


class AddressNotFound(ContractError):
    """The account does not own the requested active address."""


class AddressRevisionConflict(ContractError):
    """An address mutation was based on a stale form revision."""


class AddressInUse(ContractError):
    """An address cannot be deleted while an open checkout references it."""


class OrderNotFound(ContractError):
    """The account does not own the requested order, or the record is absent."""


class OrderStateConflict(ContractError):
    """An order lifecycle transition is not allowed from the current state."""


class ReturnNotFound(ContractError):
    """The requested local return record is absent."""


class OrderActionTokenInvalid(ContractError):
    """A customer order mutation did not carry its session-bound action token."""


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release Windows handles."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Store:
    def __init__(self, db_path: Path, schema_path: Path, fixture_root: Path) -> None:
        self.db_path = db_path
        self.schema_path = schema_path
        self.fixture_root = fixture_root.resolve()
        self._option_specs = load_source_option_specs(self.fixture_root)
        self._option_quote_specs = load_source_transaction_quote_specs(
            self.fixture_root
        )
        self._compare_profiles = self._load_compare_profiles()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _create_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(self.schema_path.read_text(encoding="utf-8"))
            # This additive schema must be installed before startup migrations
            # begin DML because sqlite3.executescript owns its transaction edge.
            install_review_schema(conn)
            self._migrate_mail_delivery_schema(conn)
            session_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(browser_sessions)")
            }
            if "account_id" not in session_columns:
                conn.execute(
                    "ALTER TABLE browser_sessions ADD COLUMN account_id INTEGER "
                    "REFERENCES accounts(account_id) ON DELETE SET NULL"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS browser_sessions_account_idx "
                "ON browser_sessions(account_id)"
            )
            # Existing installations predate the transaction offer registry and
            # stored cart lines against catalog_products. Seed every offer first,
            # then rebuild that small child table without losing cart contents.
            self._sync_commerce_offers(conn)
            self._migrate_option_selections(conn)
            # Removing an old composite primary/unique key requires a SQLite
            # table rebuild.  Do it with foreign-key enforcement temporarily
            # disabled, then validate the entire graph before startup returns.
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._migrate_cart_line_identity(conn)
                self._migrate_compare_item_identity(conn)
                self._migrate_checkout_order_variant_constraints(conn)
                self._migrate_payment_attempts_schema(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
            self._migrate_address_book_schema(conn)
            self._migrate_checkout_sessions(conn)
            self._migrate_order_lifecycle_schema(conn)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ContractError("database migration left invalid foreign keys")

    @staticmethod
    def _migrate_mail_delivery_schema(conn: sqlite3.Connection) -> None:
        """Upgrade pre-SMTP outboxes without discarding queued local messages."""

        registration_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(auth_registration_email_outbox)"
            )
        }
        registration_required = {
            "delivery_attempts",
            "claim_token",
            "last_error",
            "attempted_at",
            "sent_at",
        }
        registration_has_delivery_state = {
            "delivery_attempts",
            "last_error",
            "attempted_at",
            "sent_at",
        }.issubset(registration_columns)
        if not registration_required.issubset(registration_columns):
            conn.execute(
                "ALTER TABLE auth_registration_email_outbox "
                "RENAME TO auth_registration_email_outbox_legacy"
            )
            conn.execute(
                """
                CREATE TABLE auth_registration_email_outbox (
                    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pending_id TEXT NOT NULL UNIQUE
                        REFERENCES auth_registration_flows(pending_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    template TEXT NOT NULL
                        CHECK (template = 'registration-verification'),
                    verification_code TEXT NOT NULL CHECK (
                        length(verification_code) = 6
                        AND verification_code NOT GLOB '*[^0-9]*'
                    ),
                    status TEXT NOT NULL CHECK (status IN (
                        'LOCAL_ONLY','SMTP_PENDING','SMTP_SENT','SMTP_FAILED'
                    )),
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation IN (0,1)),
                    delivery_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK (delivery_attempts >= 0),
                    claim_token TEXT,
                    last_error TEXT,
                    attempted_at INTEGER,
                    sent_at INTEGER,
                    created_at INTEGER NOT NULL,
                    CHECK (
                        (status='LOCAL_ONLY' AND is_simulation=1)
                        OR (status<>'LOCAL_ONLY' AND is_simulation=0)
                    )
                )
                """
            )
            if registration_has_delivery_state:
                conn.execute(
                    """
                    INSERT INTO auth_registration_email_outbox(
                        email_id,pending_id,recipient,template,verification_code,
                        status,is_simulation,delivery_attempts,claim_token,
                        last_error,attempted_at,sent_at,created_at
                    )
                    SELECT email_id,pending_id,recipient,template,verification_code,
                           status,is_simulation,delivery_attempts,NULL,
                           last_error,attempted_at,sent_at,created_at
                    FROM auth_registration_email_outbox_legacy
                    """
                )
            else:
                conn.execute(
                    """
                    INSERT INTO auth_registration_email_outbox(
                        email_id,pending_id,recipient,template,verification_code,
                        status,is_simulation,delivery_attempts,claim_token,
                        last_error,attempted_at,sent_at,created_at
                    )
                    SELECT email_id,pending_id,recipient,template,verification_code,
                           'LOCAL_ONLY',1,0,NULL,NULL,NULL,NULL,created_at
                    FROM auth_registration_email_outbox_legacy
                    """
                )
            conn.execute("DROP TABLE auth_registration_email_outbox_legacy")

        reset_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(auth_password_reset_email_outbox)"
            )
        }
        reset_required = {
            "delivery_attempts",
            "claim_token",
            "last_error",
            "attempted_at",
            "sent_at",
        }
        reset_has_delivery_state = {
            "delivery_attempts",
            "last_error",
            "attempted_at",
            "sent_at",
        }.issubset(reset_columns)
        if not reset_required.issubset(reset_columns):
            conn.execute(
                "ALTER TABLE auth_password_reset_email_outbox "
                "RENAME TO auth_password_reset_email_outbox_legacy"
            )
            conn.execute(
                """
                CREATE TABLE auth_password_reset_email_outbox (
                    email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reset_id TEXT NOT NULL UNIQUE
                        REFERENCES auth_password_reset_flows(reset_id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    template TEXT NOT NULL
                        CHECK (template = 'password-reset-verification'),
                    verification_code TEXT NOT NULL CHECK (
                        length(verification_code) = 6
                        AND verification_code NOT GLOB '*[^0-9]*'
                    ),
                    status TEXT NOT NULL CHECK (status IN (
                        'LOCAL_ONLY','SMTP_PENDING','SMTP_SENT','SMTP_FAILED'
                    )),
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation IN (0,1)),
                    delivery_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK (delivery_attempts >= 0),
                    claim_token TEXT,
                    last_error TEXT,
                    attempted_at INTEGER,
                    sent_at INTEGER,
                    created_at INTEGER NOT NULL,
                    CHECK (
                        (status='LOCAL_ONLY' AND is_simulation=1)
                        OR (status<>'LOCAL_ONLY' AND is_simulation=0)
                    )
                )
                """
            )
            if reset_has_delivery_state:
                conn.execute(
                    """
                    INSERT INTO auth_password_reset_email_outbox(
                        email_id,reset_id,recipient,template,verification_code,
                        status,is_simulation,delivery_attempts,claim_token,last_error,
                        attempted_at,sent_at,created_at
                    )
                    SELECT email_id,reset_id,recipient,template,verification_code,
                           status,is_simulation,delivery_attempts,NULL,last_error,
                           attempted_at,sent_at,created_at
                    FROM auth_password_reset_email_outbox_legacy
                    """
                )
            else:
                conn.execute(
                    """
                    INSERT INTO auth_password_reset_email_outbox(
                        email_id,reset_id,recipient,template,verification_code,
                        status,is_simulation,delivery_attempts,claim_token,last_error,
                        attempted_at,sent_at,created_at
                    )
                    SELECT email_id,reset_id,recipient,template,verification_code,
                           'LOCAL_ONLY',1,0,NULL,NULL,NULL,NULL,created_at
                    FROM auth_password_reset_email_outbox_legacy
                    """
                )
            conn.execute("DROP TABLE auth_password_reset_email_outbox_legacy")

        order_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(email_outbox)")
        }
        order_required = {
            "delivery_attempts",
            "claim_token",
            "last_error",
            "attempted_at",
            "sent_at",
        }
        order_has_delivery_state = {
            "delivery_attempts",
            "last_error",
            "attempted_at",
            "sent_at",
        }.issubset(order_columns)
        if not order_required.issubset(order_columns):
            conn.execute("ALTER TABLE email_outbox RENAME TO email_outbox_legacy")
            conn.execute(
                """
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
                    status TEXT NOT NULL CHECK (status IN (
                        'LOCAL_ONLY','SMTP_PENDING','SMTP_SENT','SMTP_FAILED'
                    )),
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation IN (0,1)),
                    delivery_attempts INTEGER NOT NULL DEFAULT 0
                        CHECK (delivery_attempts >= 0),
                    claim_token TEXT,
                    last_error TEXT,
                    attempted_at INTEGER,
                    sent_at INTEGER,
                    created_at TEXT NOT NULL,
                    CHECK (
                        (status='LOCAL_ONLY' AND is_simulation=1)
                        OR (status<>'LOCAL_ONLY' AND is_simulation=0)
                    )
                )
                """
            )
            if order_has_delivery_state:
                conn.execute(
                    """
                    INSERT INTO email_outbox(
                        email_id,account_id,order_id,recipient,template,subject,
                        payload_json,status,is_simulation,delivery_attempts,
                        claim_token,last_error,attempted_at,sent_at,created_at
                    )
                    SELECT email_id,account_id,order_id,recipient,template,subject,
                           payload_json,status,is_simulation,delivery_attempts,
                           NULL,last_error,attempted_at,sent_at,created_at
                    FROM email_outbox_legacy
                    """
                )
            else:
                conn.execute(
                    """
                    INSERT INTO email_outbox(
                        email_id,account_id,order_id,recipient,template,subject,
                        payload_json,status,is_simulation,delivery_attempts,
                        claim_token,last_error,attempted_at,sent_at,created_at
                    )
                    SELECT email_id,account_id,order_id,recipient,template,subject,
                           payload_json,'LOCAL_ONLY',1,0,NULL,NULL,NULL,NULL,created_at
                    FROM email_outbox_legacy
                    """
                )
            conn.execute("DROP TABLE email_outbox_legacy")

    @staticmethod
    def _compare_label_key(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        normalized = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
        return " ".join(re.findall(r"[a-z0-9]+", normalized))

    @classmethod
    def _compare_family_key(cls, category_key: str, label: str) -> str:
        normalized = unicodedata.normalize("NFKD", label).encode(
            "ascii", "ignore"
        ).decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
        return f"{category_key}:{slug or category_key}"

    @classmethod
    def _compare_department(
        cls, candidates: list[Any]
    ) -> tuple[str, str] | None:
        aliases: dict[str, tuple[str, str]] = {}
        for department in SOURCE_DEPARTMENTS:
            slug = str(department["slug"])
            title = str(department["title"])
            for candidate in (
                slug,
                title,
                *tuple(department.get("aliases", ())),
            ):
                key = cls._compare_label_key(candidate)
                if key:
                    aliases[key] = (slug, title)
        for candidate in candidates:
            match = aliases.get(cls._compare_label_key(candidate))
            if match is not None:
                return match
        return None

    def _load_compare_profiles(self) -> dict[str, dict[str, Any]]:
        """Build comparison taxonomy only from retained source classifications."""

        profiles: dict[str, dict[str, Any]] = {}
        priorities: dict[str, int] = {}

        def install(
            asin: Any,
            category: tuple[str, str] | None,
            family_label: Any,
            *,
            priority: int,
            specs: Mapping[str, Any] | None = None,
            reviews_display: Any = None,
        ) -> None:
            if (
                not isinstance(asin, str)
                or not asin
                or category is None
                or priorities.get(asin, -1) > priority
            ):
                return
            category_key, category_label = category
            normalized_family_label = (
                " ".join(family_label.split())
                if isinstance(family_label, str) and family_label.strip()
                else category_label
            )
            clean_specs = {
                str(label): str(value)
                for label, value in (specs or {}).items()
                if isinstance(label, str)
                and label.strip()
                and isinstance(value, (str, int, float))
                and str(value).strip()
            }
            profiles[asin] = {
                "category_key": category_key,
                "category_label": category_label,
                "family_key": self._compare_family_key(
                    category_key, normalized_family_label
                ),
                "family_label": normalized_family_label,
                "specs": clean_specs,
                "reviews_display": (
                    reviews_display.strip()
                    if isinstance(reviews_display, str) and reviews_display.strip()
                    else None
                ),
            }
            priorities[asin] = priority

        # Broad search-card departments are explicit source fields and give
        # newly loaded purchasable cards dynamic eligibility without ASIN lists.
        for product in load_search_commerce_cards(self.fixture_root):
            raw_departments = product.get("department_slugs")
            candidates = (
                list(raw_departments)
                if isinstance(raw_departments, (list, tuple))
                else []
            )
            category = self._compare_department(candidates)
            department_label = category[1] if category is not None else ""
            raw_format = product.get("format")
            install(
                product.get("asin"),
                category,
                department_label,
                priority=10,
                specs={"Format": raw_format} if raw_format else None,
                reviews_display=product.get("reviews_display"),
            )

        # The frozen ranking itself is explicit External SSD family evidence;
        # every product in that source list is comparable without hardcoding it.
        frozen_path = (self.fixture_root / "task-frozen-900136-v1.json").resolve()
        try:
            frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("comparison ranking evidence is invalid") from exc
        ranking = frozen.get("ranking") if isinstance(frozen, dict) else None
        frozen_products = frozen.get("products") if isinstance(frozen, dict) else None
        if not isinstance(ranking, dict) or not isinstance(frozen_products, list):
            raise ContractError("comparison ranking evidence is invalid")
        ranking_title = str(ranking.get("title") or "External Solid State Drives")
        family_label = re.sub(
            r"^Best Sellers in\s+", "", ranking_title, flags=re.IGNORECASE
        )
        computer_category = self._compare_department(["computers"])
        for product in frozen_products:
            if isinstance(product, dict):
                install(
                    product.get("asin"),
                    computer_category,
                    family_label,
                    priority=20,
                )

        # Direct PDP breadcrumbs supply a narrower family and win broad card
        # taxonomy for overlapping ASINs.
        direct_path = (self.fixture_root / HOME_PDP_EVIDENCE_FIXTURE).resolve()
        try:
            direct = json.loads(direct_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("comparison PDP evidence is invalid") from exc
        direct_products = direct.get("products") if isinstance(direct, dict) else None
        if not isinstance(direct_products, list):
            raise ContractError("comparison PDP evidence is invalid")
        for product in direct_products:
            if not isinstance(product, dict):
                continue
            detail = product.get("pdp")
            if not isinstance(detail, dict):
                continue
            breadcrumb = detail.get("breadcrumb")
            breadcrumb_values = (
                [value for value in breadcrumb if isinstance(value, str)]
                if isinstance(breadcrumb, list)
                else []
            )
            category = self._compare_department(
                [*breadcrumb_values, detail.get("page_category")]
            )
            family = breadcrumb_values[-1] if len(breadcrumb_values) > 1 else (
                category[1] if category is not None else ""
            )
            install(
                product.get("asin"),
                category,
                family,
                priority=30,
            )
        return profiles

    def _load_direct_commerce_offers(self) -> list[dict[str, Any]]:
        candidate = (self.fixture_root / HOME_PDP_EVIDENCE_FIXTURE).resolve()
        if self.fixture_root not in candidate.parents or not candidate.is_file():
            raise ContractError("direct PDP evidence fixture does not exist")
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError("direct PDP evidence fixture is invalid") from exc
        if payload.get("schema") != "amazon-clone.home-pdp-evidence.v1":
            raise ContractError("unsupported direct PDP evidence schema")
        products = payload.get("products")
        if not isinstance(products, list) or not products:
            raise ContractError("direct PDP evidence must contain at least one offer")

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for product in products:
            if not isinstance(product, dict):
                raise ContractError("direct PDP offers must be objects")
            asin = product.get("asin")
            slug = product.get("slug")
            canonical_path = product.get("canonicalPath")
            image_path = product.get("image_path")
            price_minor = product.get("price_minor")
            list_price_minor = product.get("list_price_minor")
            reviews = product.get("reviews")
            if (
                not isinstance(asin, str)
                or len(asin) != 10
                or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for character in asin)
                or asin in seen
            ):
                raise ContractError("direct PDP offer has an invalid or duplicate ASIN")
            if not isinstance(slug, str) or not slug or "/" in slug:
                raise ContractError(f"direct PDP offer {asin} has an invalid slug")
            if (
                not isinstance(canonical_path, str)
                or not canonical_path.startswith("/")
                or not canonical_path.endswith(f"/dp/{asin}")
                or canonical_path.startswith("//")
            ):
                raise ContractError(f"direct PDP offer {asin} has an invalid canonical path")
            if (
                not isinstance(image_path, str)
                or not image_path.startswith("/static/")
                or "\\" in image_path
                or ".." in Path(image_path).parts
            ):
                raise ContractError(f"direct PDP offer {asin} has an invalid image")
            if isinstance(price_minor, bool) or not isinstance(price_minor, int) or price_minor < 0:
                raise ContractError(f"direct PDP offer {asin} has an invalid price")
            if (
                list_price_minor is not None
                and (
                    isinstance(list_price_minor, bool)
                    or not isinstance(list_price_minor, int)
                    or list_price_minor < 0
                )
            ):
                raise ContractError(f"direct PDP offer {asin} has an invalid list price")
            if isinstance(reviews, bool) or not isinstance(reviews, int) or reviews < 0:
                raise ContractError(f"direct PDP offer {asin} has invalid reviews")
            required_text = {
                "title": product.get("title"),
                "brand": product.get("brand"),
                "capacity": product.get("capacity"),
                "color": product.get("color"),
                "currency": product.get("currency"),
                "rating": product.get("rating"),
                "evidence_class": product.get("evidence_class"),
            }
            if any(not isinstance(value, str) for value in required_text.values()):
                raise ContractError(f"direct PDP offer {asin} has invalid text fields")

            seen.add(asin)
            normalized.append(
                {
                    "asin": asin,
                    "slug": slug,
                    "canonical_path": canonical_path,
                    "title": required_text["title"],
                    "brand": required_text["brand"],
                    "capacity": required_text["capacity"],
                    "color": required_text["color"],
                    "price_minor": price_minor,
                    "list_price_minor": list_price_minor,
                    "currency": required_text["currency"],
                    "rating": required_text["rating"],
                    "reviews": reviews,
                    "image_path": image_path,
                    "badge": str(product.get("badge") or ""),
                    "evidence_class": required_text["evidence_class"],
                }
            )
        return normalized

    @staticmethod
    def _upsert_commerce_offers(
        conn: sqlite3.Connection, products: list[dict[str, Any]], source: str
    ) -> None:
        rows = []
        for product in products:
            normalized = dict(product)
            normalized["canonical_path"] = normalized.get("canonical_path") or (
                f"/{normalized['slug']}/dp/{normalized['asin']}"
            )
            normalized["source"] = source
            rows.append(normalized)
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO commerce_offers(
                asin,slug,canonical_path,title,brand,capacity,color,price_minor,
                list_price_minor,currency,rating,reviews,image_path,badge,
                evidence_class,source
            ) VALUES (
                :asin,:slug,:canonical_path,:title,:brand,:capacity,:color,:price_minor,
                :list_price_minor,:currency,:rating,:reviews,:image_path,:badge,
                :evidence_class,:source
            )
            ON CONFLICT(asin) DO UPDATE SET
                slug=excluded.slug,
                canonical_path=excluded.canonical_path,
                title=excluded.title,
                brand=excluded.brand,
                capacity=excluded.capacity,
                color=excluded.color,
                price_minor=excluded.price_minor,
                list_price_minor=excluded.list_price_minor,
                currency=excluded.currency,
                rating=excluded.rating,
                reviews=excluded.reviews,
                image_path=excluded.image_path,
                badge=excluded.badge,
                evidence_class=excluded.evidence_class,
                source=excluded.source
            """,
            rows,
        )

    def _sync_commerce_offers(
        self,
        conn: sqlite3.Connection,
        task_products: list[dict[str, Any]] | None = None,
    ) -> None:
        if task_products is None:
            task_products = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT asin,slug,title,brand,capacity,color,price_minor,
                           list_price_minor,currency,rating,reviews,image_path,
                           badge,evidence_class
                    FROM catalog_products
                    ORDER BY asin
                    """
                )
            ]
        self._upsert_commerce_offers(conn, task_products, "task-fixture")
        # Direct PDP observations are the current transaction offer. They must
        # win the one overlapping ASIN without changing catalog_products.
        direct_offers = self._load_direct_commerce_offers()
        self._upsert_commerce_offers(conn, direct_offers, "direct-pdp")
        # Current Deals cards establish ten additional default USD offers.  The
        # legacy source column accepts ``direct-pdp`` for directly observed
        # offers; evidence_class keeps the narrower card-only boundary so the
        # renderer never invents PDP, rating, delivery, inventory, or options.
        deals_offers = list(load_deals_default_card_offers(self.fixture_root))
        self._upsert_commerce_offers(conn, deals_offers, "direct-pdp")
        # Current search cards establish only their visible default offer.
        # ``evidence_class`` retains that narrower boundary even though the
        # legacy source column groups all directly observed offers together.
        search_card_offers = list(load_search_commerce_cards(self.fixture_root))
        self._upsert_commerce_offers(conn, search_card_offers, "direct-pdp")
        if len(task_products) == 9:
            expected_asins = {
                str(product["asin"])
                for product in [
                    *task_products,
                    *direct_offers,
                    *deals_offers,
                    *search_card_offers,
                ]
            }
            offer_count = int(
                conn.execute("SELECT COUNT(*) FROM commerce_offers").fetchone()[0]
            )
            if offer_count != len(expected_asins):
                raise ContractError(
                    "commerce catalog offer count does not match current source evidence: "
                    f"expected {len(expected_asins)}, found {offer_count}"
                )

    @staticmethod
    def _new_cart_line_id() -> str:
        return secrets.token_urlsafe(24)

    def _canonical_stored_selection(
        self, asin: str, raw_value: Any
    ) -> tuple[str, str]:
        selected_options = self._stored_product_options(asin, raw_value)
        return (
            json.dumps(
                selected_options,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            canonical_selection_key(selected_options),
        )

    def _migrate_cart_line_identity(self, conn: sqlite3.Connection) -> None:
        """Give every cart row an opaque identity and a variant-aware key.

        SQLite cannot remove the legacy ``PRIMARY KEY(owner, asin)`` in place,
        so both guest and account tables are rebuilt transactionally. Existing
        selections are validated and canonicalized; a partially migrated table
        with duplicate canonical variants is folded without exceeding the
        existing quantity cap.
        """

        table_specs = (
            ("cart_lines", "cart_id", "carts", "cart_id"),
            ("account_cart_lines", "account_id", "accounts", "account_id"),
        )
        for table, owner_column, owner_table, owner_reference in table_specs:
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            foreign_tables = {
                row["table"]
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            }
            if {
                "line_id",
                "selection_json",
                "selection_key",
                "line_state",
            }.issubset(columns) and "commerce_offers" in foreign_tables:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_owner_state_idx "
                    f"ON {table}({owner_column},line_state)"
                )
                continue

            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY {owner_column},asin"
            ).fetchall()
            merged: dict[tuple[int, str, str], dict[str, Any]] = {}
            used_line_ids: set[str] = set()
            for row in rows:
                asin = str(row["asin"])
                selection_json, selection_key = self._canonical_stored_selection(
                    asin,
                    row["selection_json"] if "selection_json" in columns else "{}",
                )
                owner_id = int(row[owner_column])
                identity = (owner_id, asin, selection_key)
                state = (
                    SAVED_CART_STATE
                    if "line_state" in columns
                    and str(row["line_state"]) == SAVED_CART_STATE
                    else ACTIVE_CART_STATE
                )
                if identity in merged:
                    current = merged[identity]
                    current["quantity"] = min(
                        30, int(current["quantity"]) + int(row["quantity"])
                    )
                    if state == ACTIVE_CART_STATE:
                        current["line_state"] = ACTIVE_CART_STATE
                    continue
                candidate = (
                    str(row["line_id"])
                    if "line_id" in columns and row["line_id"] is not None
                    else ""
                )
                if (
                    CART_LINE_ID_PATTERN.fullmatch(candidate) is None
                    or candidate in used_line_ids
                ):
                    candidate = self._new_cart_line_id()
                    while candidate in used_line_ids:
                        candidate = self._new_cart_line_id()
                used_line_ids.add(candidate)
                merged[identity] = {
                    "line_id": candidate,
                    owner_column: owner_id,
                    "asin": asin,
                    "quantity": int(row["quantity"]),
                    "selection_json": selection_json,
                    "selection_key": selection_key,
                    "line_state": state,
                }

            replacement = f"{table}_line_identity_v2"
            conn.execute(f"DROP TABLE IF EXISTS {replacement}")
            conn.execute(
                f"""
                CREATE TABLE {replacement} (
                    line_id TEXT PRIMARY KEY NOT NULL,
                    {owner_column} INTEGER NOT NULL
                        REFERENCES {owner_table}({owner_reference}) ON DELETE CASCADE,
                    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{{}}',
                    selection_key TEXT NOT NULL,
                    line_state TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (line_state IN ('ACTIVE', 'SAVED')),
                    UNIQUE ({owner_column}, asin, selection_key)
                )
                """
            )
            if merged:
                conn.executemany(
                    f"""
                    INSERT INTO {replacement}(
                        line_id,{owner_column},asin,quantity,selection_json,
                        selection_key,line_state
                    ) VALUES (
                        :line_id,:{owner_column},:asin,:quantity,:selection_json,
                        :selection_key,:line_state
                    )
                    """,
                    list(merged.values()),
                )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {replacement} RENAME TO {table}")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_owner_state_idx "
                f"ON {table}({owner_column},line_state)"
            )

    @staticmethod
    def _new_compare_line_id() -> str:
        return secrets.token_urlsafe(24)

    def _migrate_compare_item_identity(self, conn: sqlite3.Connection) -> None:
        """Upgrade legacy ASIN-only comparisons without discarding selections.

        Compare rows now belong either to an anonymous browser session or to a
        durable account.  Each row has its own opaque mutation identity and a
        canonical option key, allowing two captured variants of one ASIN to
        coexist while keeping all removal operations owner-scoped.  A legacy
        list may contain products admitted by the old hard-coded registry but
        rejected by the current source-backed taxonomy.  Preserve source order
        while letting each owner's first eligible row choose the one family
        that survives migration.
        """

        offer_asins = {
            str(row["asin"])
            for row in conn.execute("SELECT asin FROM commerce_offers")
        }
        eligible_asins = offer_asins.intersection(self._compare_profiles)

        table_specs = (
            (
                "compare_items",
                "session_digest",
                "TEXT",
                "browser_sessions",
                "session_digest",
            ),
            (
                "account_compare_items",
                "account_id",
                "INTEGER",
                "accounts",
                "account_id",
            ),
        )
        for table, owner_column, owner_type, owner_table, owner_reference in table_specs:
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            foreign_tables = {
                row["table"]
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            }
            required = {
                "compare_line_id",
                owner_column,
                "asin",
                "selection_json",
                "selection_key",
                "position",
                "created_at",
            }
            if required.issubset(columns) and "commerce_offers" in foreign_tables:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_owner_idx "
                    f"ON {table}({owner_column},position)"
                )
                continue

            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY {owner_column},position,asin"
            ).fetchall()
            rebuilt: list[dict[str, Any]] = []
            used_line_ids: set[str] = set()
            owner_identities: dict[Any, set[tuple[str, str]]] = {}
            owner_family_keys: dict[Any, str] = {}
            owner_positions: dict[Any, int] = {}
            for row in rows:
                owner_id = row[owner_column]
                asin = str(row["asin"])
                profile = self._compare_profiles.get(asin)
                if asin not in eligible_asins or profile is None:
                    continue
                family_key = str(profile["family_key"])
                retained_family = owner_family_keys.get(owner_id)
                if retained_family is not None and retained_family != family_key:
                    continue
                selection_json, selection_key = self._canonical_stored_selection(
                    asin,
                    row["selection_json"] if "selection_json" in columns else "{}",
                )
                identities = owner_identities.setdefault(owner_id, set())
                identity = (asin, selection_key)
                if identity in identities or owner_positions.get(owner_id, 0) >= COMPARE_LIMIT:
                    continue
                candidate = (
                    str(row["compare_line_id"])
                    if "compare_line_id" in columns
                    and row["compare_line_id"] is not None
                    else ""
                )
                if (
                    COMPARE_LINE_ID_PATTERN.fullmatch(candidate) is None
                    or candidate in used_line_ids
                ):
                    candidate = self._new_compare_line_id()
                    while candidate in used_line_ids:
                        candidate = self._new_compare_line_id()
                used_line_ids.add(candidate)
                identities.add(identity)
                owner_family_keys.setdefault(owner_id, family_key)
                position = owner_positions.get(owner_id, 0) + 1
                owner_positions[owner_id] = position
                rebuilt.append(
                    {
                        "compare_line_id": candidate,
                        owner_column: owner_id,
                        "asin": asin,
                        "selection_json": selection_json,
                        "selection_key": selection_key,
                        "position": position,
                        "created_at": str(row["created_at"]),
                    }
                )

            replacement = f"{table}_variant_v2"
            conn.execute(f"DROP TABLE IF EXISTS {replacement}")
            conn.execute(
                f"""
                CREATE TABLE {replacement} (
                    compare_line_id TEXT PRIMARY KEY NOT NULL,
                    {owner_column} {owner_type} NOT NULL
                        REFERENCES {owner_table}({owner_reference}) ON DELETE CASCADE,
                    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
                    selection_json TEXT NOT NULL DEFAULT '{{}}',
                    selection_key TEXT NOT NULL,
                    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 4),
                    created_at TEXT NOT NULL,
                    UNIQUE ({owner_column}, asin, selection_key),
                    UNIQUE ({owner_column}, position)
                )
                """
            )
            if rebuilt:
                conn.executemany(
                    f"""
                    INSERT INTO {replacement}(
                        compare_line_id,{owner_column},asin,selection_json,
                        selection_key,position,created_at
                    ) VALUES (
                        :compare_line_id,:{owner_column},:asin,:selection_json,
                        :selection_key,:position,:created_at
                    )
                    """,
                    rebuilt,
                )
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {replacement} RENAME TO {table}")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_owner_idx "
                f"ON {table}({owner_column},position)"
            )

    @staticmethod
    def _migrate_option_selections(conn: sqlite3.Connection) -> None:
        """Add source-option snapshots without discarding pre-existing carts/orders."""

        for table in (
            "cart_lines",
            "account_cart_lines",
            "pending_buy_now",
            "checkout_lines",
            "order_items",
        ):
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if "selection_json" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN selection_json "
                    "TEXT NOT NULL DEFAULT '{}'"
                )

    @staticmethod
    def _has_unique_index(
        conn: sqlite3.Connection, table: str, columns: tuple[str, ...]
    ) -> bool:
        for index in conn.execute(f"PRAGMA index_list({table})"):
            if not bool(index["unique"]):
                continue
            indexed = tuple(
                str(row["name"])
                for row in conn.execute(f"PRAGMA index_info({index['name']})")
            )
            if indexed == columns:
                return True
        return False

    @classmethod
    def _migrate_checkout_order_variant_constraints(
        cls, conn: sqlite3.Connection
    ) -> None:
        """Remove legacy per-ASIN uniqueness from snapshots and order history."""

        checkout_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(checkout_lines)")
        }
        if "selection_json" not in checkout_columns or cls._has_unique_index(
            conn, "checkout_lines", ("checkout_id", "asin")
        ):
            conn.execute("DROP TABLE IF EXISTS checkout_lines_variant_v2")
            conn.execute(
                """
                CREATE TABLE checkout_lines_variant_v2 (
                    checkout_id INTEGER NOT NULL
                        REFERENCES checkout_sessions(checkout_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                    asin TEXT NOT NULL REFERENCES commerce_offers(asin),
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (checkout_id, ordinal)
                )
                """
            )
            selection_expression = (
                "selection_json" if "selection_json" in checkout_columns else "'{}'"
            )
            conn.execute(
                f"""
                INSERT INTO checkout_lines_variant_v2(
                    checkout_id,ordinal,asin,quantity,selection_json
                )
                SELECT checkout_id,ordinal,asin,quantity,{selection_expression}
                FROM checkout_lines
                """
            )
            conn.execute("DROP TABLE checkout_lines")
            conn.execute(
                "ALTER TABLE checkout_lines_variant_v2 RENAME TO checkout_lines"
            )

        order_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(order_items)")
        }
        if "selection_json" not in order_columns or cls._has_unique_index(
            conn, "order_items", ("order_id", "asin")
        ):
            # This child-table trigger names order_items in its body. SQLite
            # validates trigger SQL during ALTER TABLE, so recreate it later in
            # _migrate_order_lifecycle_schema after the replacement is in place.
            conn.execute("DROP TRIGGER IF EXISTS return_item_order_insert_guard")
            conn.execute("DROP TABLE IF EXISTS order_items_variant_v2")
            conn.execute(
                """
                CREATE TABLE order_items_variant_v2 (
                    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL
                        REFERENCES orders(order_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                    asin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 30),
                    selection_json TEXT NOT NULL DEFAULT '{}',
                    unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor >= 0),
                    line_total_minor INTEGER NOT NULL CHECK (
                        line_total_minor = unit_price_minor * quantity
                    ),
                    currency TEXT NOT NULL,
                    UNIQUE (order_id, ordinal)
                )
                """
            )
            selection_expression = (
                "selection_json" if "selection_json" in order_columns else "'{}'"
            )
            conn.execute(
                f"""
                INSERT INTO order_items_variant_v2(
                    order_item_id,order_id,ordinal,asin,title,image_path,quantity,
                    selection_json,unit_price_minor,line_total_minor,currency
                )
                SELECT order_item_id,order_id,ordinal,asin,title,image_path,quantity,
                       {selection_expression},unit_price_minor,line_total_minor,currency
                FROM order_items
                """
            )
            conn.execute("DROP TABLE order_items")
            conn.execute("ALTER TABLE order_items_variant_v2 RENAME TO order_items")

    @staticmethod
    def _migrate_payment_attempts_schema(conn: sqlite3.Connection) -> None:
        """Add deterministic decline/retry methods without losing old orders."""

        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='payment_attempts'"
        ).fetchone()
        if row is None:
            return
        table_sql = str(row["sql"] or "")
        if "sandbox-card-approved" in table_sql and "'DECLINED'" in table_sql:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    payment_attempts_one_approved_checkout_idx
                ON payment_attempts(checkout_id) WHERE status='APPROVED'
                """
            )
            return

        conn.execute(
            "DROP INDEX IF EXISTS payment_attempts_one_approved_checkout_idx"
        )
        conn.execute("DROP TABLE IF EXISTS payment_attempts_sandbox_v2")
        conn.execute(
            """
            CREATE TABLE payment_attempts_sandbox_v2 (
                payment_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                checkout_id INTEGER NOT NULL
                    REFERENCES checkout_sessions(checkout_id) ON DELETE CASCADE,
                account_id INTEGER NOT NULL
                    REFERENCES accounts(account_id) ON DELETE CASCADE,
                method TEXT NOT NULL CHECK (method IN (
                    'test-card','sandbox-card-approved','sandbox-card-declined',
                    'sandbox-bank-approved'
                )),
                status TEXT NOT NULL CHECK (
                    status IN ('APPROVED','DECLINED','SUPERSEDED')
                ),
                amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
                currency TEXT NOT NULL,
                cart_fingerprint TEXT NOT NULL,
                is_simulation INTEGER NOT NULL DEFAULT 1
                    CHECK (is_simulation = 1),
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO payment_attempts_sandbox_v2(
                payment_attempt_id,checkout_id,account_id,method,status,
                amount_minor,currency,cart_fingerprint,is_simulation,created_at
            )
            SELECT payment_attempt_id,checkout_id,account_id,method,status,
                   amount_minor,currency,cart_fingerprint,is_simulation,created_at
            FROM payment_attempts
            """
        )
        conn.execute("DROP TABLE payment_attempts")
        conn.execute(
            "ALTER TABLE payment_attempts_sandbox_v2 RENAME TO payment_attempts"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX payment_attempts_one_approved_checkout_idx
            ON payment_attempts(checkout_id) WHERE status='APPROVED'
            """
        )

    @staticmethod
    def _migrate_address_book_schema(conn: sqlite3.Connection) -> None:
        """Upgrade checkout-only addresses into a durable account address book."""

        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(addresses)")
        }
        if "is_default" not in columns:
            conn.execute(
                "ALTER TABLE addresses ADD COLUMN is_default INTEGER "
                "NOT NULL DEFAULT 0 CHECK (is_default IN (0,1))"
            )
        if "is_archived" not in columns:
            conn.execute(
                "ALTER TABLE addresses ADD COLUMN is_archived INTEGER "
                "NOT NULL DEFAULT 0 CHECK (is_archived IN (0,1))"
            )
        if "revision" not in columns:
            conn.execute(
                "ALTER TABLE addresses ADD COLUMN revision INTEGER "
                "NOT NULL DEFAULT 1 CHECK (revision > 0)"
            )

        conn.execute(
            "UPDATE addresses SET is_default=0 WHERE is_archived=1"
        )
        account_rows = conn.execute(
            "SELECT DISTINCT account_id FROM addresses ORDER BY account_id"
        ).fetchall()
        for account_row in account_rows:
            account_id = int(account_row["account_id"])
            rows = conn.execute(
                """
                SELECT address_id,is_default FROM addresses
                WHERE account_id=? AND is_archived=0
                ORDER BY is_default DESC,address_id
                """,
                (account_id,),
            ).fetchall()
            if not rows:
                continue
            chosen_id = int(rows[0]["address_id"])
            conn.execute(
                """
                UPDATE addresses SET is_default=CASE WHEN address_id=? THEN 1 ELSE 0 END
                WHERE account_id=? AND is_archived=0
                """,
                (chosen_id, account_id),
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS addresses_one_default_account_idx
            ON addresses(account_id) WHERE is_default=1 AND is_archived=0
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS addresses_account_active_idx
            ON addresses(account_id,is_archived,is_default,address_id)
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS addresses_archived_default_insert_guard
            BEFORE INSERT ON addresses
            WHEN NEW.is_archived=1 AND NEW.is_default=1
            BEGIN
                SELECT RAISE(ABORT,'archived address cannot be default');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS addresses_archived_default_update_guard
            BEFORE UPDATE OF is_archived,is_default ON addresses
            WHEN NEW.is_archived=1 AND NEW.is_default=1
            BEGIN
                SELECT RAISE(ABORT,'archived address cannot be default');
            END
            """
        )
        ownership_mismatch = conn.execute(
            """
            SELECT checkout.checkout_id
            FROM checkout_sessions AS checkout
            JOIN addresses AS address ON address.address_id=checkout.address_id
            WHERE checkout.account_id<>address.account_id
            LIMIT 1
            """
        ).fetchone()
        if ownership_mismatch is not None:
            raise ContractError(
                "database contains a checkout address owned by another account"
            )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS checkout_address_owner_insert_guard
            BEFORE INSERT ON checkout_sessions
            WHEN NEW.address_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM addresses
                WHERE address_id=NEW.address_id AND account_id=NEW.account_id
            )
            BEGIN
                SELECT RAISE(ABORT,'checkout address must belong to checkout account');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS checkout_address_owner_update_guard
            BEFORE UPDATE OF account_id,address_id ON checkout_sessions
            WHEN NEW.address_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM addresses
                WHERE address_id=NEW.address_id AND account_id=NEW.account_id
            )
            BEGIN
                SELECT RAISE(ABORT,'checkout address must belong to checkout account');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS address_checkout_owner_update_guard
            BEFORE UPDATE OF account_id ON addresses
            WHEN EXISTS (
                SELECT 1 FROM checkout_sessions
                WHERE address_id=OLD.address_id AND account_id<>NEW.account_id
            )
            BEGIN
                SELECT RAISE(ABORT,'address account must match referencing checkouts');
            END
            """
        )

    @staticmethod
    def _migrate_checkout_sessions(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(checkout_sessions)")
        }
        if "idempotency_key" not in columns:
            conn.execute(
                "ALTER TABLE checkout_sessions ADD COLUMN idempotency_key TEXT"
            )
        if "checkout_mode" not in columns:
            conn.execute(
                "ALTER TABLE checkout_sessions ADD COLUMN checkout_mode TEXT "
                "NOT NULL DEFAULT 'CART' "
                "CHECK (checkout_mode IN ('CART','BUY_NOW'))"
            )
        missing_rows = conn.execute(
            """
            SELECT checkout_id FROM checkout_sessions
            WHERE idempotency_key IS NULL OR idempotency_key=''
            ORDER BY checkout_id
            """
        ).fetchall()
        for row in missing_rows:
            conn.execute(
                "UPDATE checkout_sessions SET idempotency_key=? WHERE checkout_id=?",
                (secrets.token_urlsafe(24), int(row["checkout_id"])),
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS checkout_sessions_idempotency_key_idx
            ON checkout_sessions(idempotency_key)
            """
        )

    @staticmethod
    def _migrate_order_lifecycle_schema(conn: sqlite3.Connection) -> None:
        """Add orthogonal shipment/return/refund lifecycle state to legacy orders."""

        shipment_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(shipments)")
        }
        additions = {
            "lifecycle_status": (
                "TEXT NOT NULL DEFAULT 'PREPARING' CHECK ("
                "lifecycle_status IN ('PREPARING','SHIPPED','DELIVERED','CANCELLED'))"
            ),
            "revision": "INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0)",
            "shipped_at": "TEXT",
            "delivered_at": "TEXT",
            "cancelled_at": "TEXT",
        }
        migrated_legacy_shipments = "lifecycle_status" not in shipment_columns
        for column, declaration in additions.items():
            if column not in shipment_columns:
                conn.execute(
                    f"ALTER TABLE shipments ADD COLUMN {column} {declaration}"
                )
        if migrated_legacy_shipments:
            conn.execute(
                """
                UPDATE shipments SET lifecycle_status='PREPARING',revision=1,
                    shipped_at=NULL,delivered_at=NULL,cancelled_at=NULL,
                    tracking_code=NULL
                """
            )
        return_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(return_requests)")
        }
        if "revision" not in return_columns:
            conn.execute(
                "ALTER TABLE return_requests ADD COLUMN revision "
                "INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0)"
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO order_events(
                order_id,account_id,event_type,actor,from_status,to_status,
                idempotency_key,detail_json,created_at
            )
            SELECT order_id,account_id,'ORDER_PLACED','SYSTEM',NULL,'PREPARING',
                   idempotency_key,'{"source":"migration-backfill"}',created_at
            FROM orders
            """
        )

        lifecycle_error = conn.execute(
            """
            SELECT orders.order_id
            FROM orders
            LEFT JOIN shipments ON shipments.order_id=orders.order_id
            WHERE orders.status<>'PLACED'
               OR shipments.shipment_id IS NULL
               OR shipments.status<>'PREPARING'
               OR shipments.lifecycle_status NOT IN (
                    'PREPARING','SHIPPED','DELIVERED','CANCELLED'
               )
               OR (shipments.lifecycle_status IN ('PREPARING','CANCELLED')
                   AND shipments.tracking_code IS NOT NULL)
               OR (shipments.lifecycle_status IN ('SHIPPED','DELIVERED')
                   AND (
                       shipments.tracking_code IS NULL
                       OR length(shipments.tracking_code) NOT BETWEEN 8 AND 64
                       OR shipments.shipped_at IS NULL
                   ))
               OR (shipments.lifecycle_status='DELIVERED'
                   AND shipments.delivered_at IS NULL)
               OR (shipments.lifecycle_status='CANCELLED'
                   AND shipments.cancelled_at IS NULL)
            LIMIT 1
            """
        ).fetchone()
        if lifecycle_error is not None:
            raise ContractError("database contains inconsistent order shipment state")

        ownership_checks = (
            """
            SELECT event.order_event_id
            FROM order_events AS event JOIN orders ON orders.order_id=event.order_id
            WHERE event.account_id<>orders.account_id LIMIT 1
            """,
            """
            SELECT action.order_action_key_id
            FROM order_action_keys AS action JOIN orders ON orders.order_id=action.order_id
            WHERE action.account_id<>orders.account_id LIMIT 1
            """,
            """
            SELECT request.return_request_id
            FROM return_requests AS request JOIN orders ON orders.order_id=request.order_id
            WHERE request.account_id<>orders.account_id LIMIT 1
            """,
            """
            SELECT item.return_request_id
            FROM return_request_items AS item
            JOIN return_requests AS request USING(return_request_id)
            JOIN order_items AS order_item ON order_item.order_item_id=item.order_item_id
            WHERE request.order_id<>order_item.order_id
               OR item.quantity>order_item.quantity
            LIMIT 1
            """,
            """
            SELECT refund.refund_id
            FROM refunds AS refund JOIN orders ON orders.order_id=refund.order_id
            LEFT JOIN return_requests AS request
              ON request.return_request_id=refund.return_request_id
            WHERE refund.account_id<>orders.account_id
               OR refund.payment_attempt_id<>orders.payment_attempt_id
               OR refund.amount_minor<>orders.total_minor
               OR refund.currency<>orders.currency
               OR (refund.kind='RETURN' AND (
                    request.return_request_id IS NULL
                    OR request.order_id<>orders.order_id
                    OR request.account_id<>orders.account_id
               ))
            LIMIT 1
            """,
        )
        if any(
            conn.execute(statement).fetchone() is not None
            for statement in ownership_checks
        ):
            raise ContractError("database contains invalid order lifecycle ownership")

        return_state_error = conn.execute(
            """
            SELECT request.return_request_id
            FROM return_requests AS request
            JOIN shipments ON shipments.order_id=request.order_id
            LEFT JOIN refunds AS refund
              ON refund.return_request_id=request.return_request_id
             AND refund.kind='RETURN'
            WHERE shipments.lifecycle_status<>'DELIVERED'
               OR (request.status='REFUNDED' AND refund.refund_id IS NULL)
               OR (request.status<>'REFUNDED' AND refund.refund_id IS NOT NULL)
            LIMIT 1
            """
        ).fetchone()
        cancellation_error = conn.execute(
            """
            SELECT shipments.shipment_id
            FROM shipments
            LEFT JOIN refunds AS refund
              ON refund.order_id=shipments.order_id
             AND refund.kind='CANCELLATION'
            WHERE (shipments.lifecycle_status='CANCELLED'
                   AND refund.refund_id IS NULL)
               OR (shipments.lifecycle_status<>'CANCELLED'
                   AND refund.refund_id IS NOT NULL)
            LIMIT 1
            """
        ).fetchone()
        if return_state_error is not None or cancellation_error is not None:
            raise ContractError("database contains inconsistent return or refund state")

        trigger_statements = (
            """
            CREATE TRIGGER IF NOT EXISTS immutable_order_placement_status_guard
            BEFORE UPDATE OF status ON orders WHEN NEW.status<>OLD.status
            BEGIN SELECT RAISE(ABORT,'order placement status is immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS immutable_shipment_placement_status_guard
            BEFORE UPDATE OF status ON shipments WHEN NEW.status<>OLD.status
            BEGIN SELECT RAISE(ABORT,'shipment placement status is immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS shipment_lifecycle_transition_guard
            BEFORE UPDATE OF lifecycle_status ON shipments
            WHEN NOT (
                NEW.lifecycle_status=OLD.lifecycle_status
                OR (OLD.lifecycle_status='PREPARING'
                    AND NEW.lifecycle_status IN ('SHIPPED','CANCELLED'))
                OR (OLD.lifecycle_status='SHIPPED'
                    AND NEW.lifecycle_status='DELIVERED')
            )
            BEGIN SELECT RAISE(ABORT,'invalid shipment lifecycle transition'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS shipment_lifecycle_shape_guard
            BEFORE UPDATE OF lifecycle_status,tracking_code,shipped_at,delivered_at,cancelled_at
            ON shipments
            WHEN (NEW.lifecycle_status IN ('PREPARING','CANCELLED')
                    AND NEW.tracking_code IS NOT NULL)
              OR (NEW.lifecycle_status IN ('SHIPPED','DELIVERED')
                    AND (NEW.tracking_code IS NULL
                         OR length(NEW.tracking_code) NOT BETWEEN 8 AND 64
                         OR NEW.shipped_at IS NULL))
              OR (NEW.lifecycle_status='DELIVERED' AND NEW.delivered_at IS NULL)
              OR (NEW.lifecycle_status='CANCELLED' AND NEW.cancelled_at IS NULL)
            BEGIN SELECT RAISE(ABORT,'invalid shipment lifecycle fields'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS order_event_owner_insert_guard
            BEFORE INSERT ON order_events
            WHEN NOT EXISTS (
                SELECT 1 FROM orders
                WHERE order_id=NEW.order_id AND account_id=NEW.account_id
            )
            BEGIN SELECT RAISE(ABORT,'order event account mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS order_action_owner_insert_guard
            BEFORE INSERT ON order_action_keys
            WHEN NOT EXISTS (
                SELECT 1 FROM orders
                WHERE order_id=NEW.order_id AND account_id=NEW.account_id
            )
            BEGIN SELECT RAISE(ABORT,'order action account mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS return_request_owner_insert_guard
            BEFORE INSERT ON return_requests
            WHEN NOT EXISTS (
                SELECT 1 FROM orders
                WHERE order_id=NEW.order_id AND account_id=NEW.account_id
            )
              OR NOT EXISTS (
                SELECT 1 FROM shipments
                WHERE order_id=NEW.order_id AND lifecycle_status='DELIVERED'
            )
            BEGIN SELECT RAISE(ABORT,'return account mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS return_status_transition_guard
            BEFORE UPDATE OF status ON return_requests
            WHEN NOT (
                NEW.status=OLD.status
                OR (OLD.status='REQUESTED' AND NEW.status='RECEIVED')
                OR (OLD.status='RECEIVED' AND NEW.status='REFUNDED')
            )
            BEGIN SELECT RAISE(ABORT,'invalid return transition'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS return_delivered_insert_guard
            BEFORE INSERT ON return_requests
            WHEN NOT EXISTS (
                SELECT 1 FROM shipments
                WHERE order_id=NEW.order_id AND lifecycle_status='DELIVERED'
            )
            BEGIN SELECT RAISE(ABORT,'return requires delivered shipment'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS return_owner_update_guard
            BEFORE UPDATE OF account_id,order_id ON return_requests
            WHEN NEW.account_id<>OLD.account_id OR NEW.order_id<>OLD.order_id
            BEGIN SELECT RAISE(ABORT,'return ownership is immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS return_item_order_insert_guard
            BEFORE INSERT ON return_request_items
            WHEN NOT EXISTS (
                SELECT 1 FROM return_requests AS request
                JOIN order_items AS item ON item.order_item_id=NEW.order_item_id
                WHERE request.return_request_id=NEW.return_request_id
                  AND request.order_id=item.order_id
                  AND NEW.quantity<=item.quantity
            )
            BEGIN SELECT RAISE(ABORT,'return item order mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS refund_owner_insert_guard
            BEFORE INSERT ON refunds
            WHEN NOT EXISTS (
                SELECT 1 FROM orders
                WHERE order_id=NEW.order_id AND account_id=NEW.account_id
                  AND payment_attempt_id=NEW.payment_attempt_id
                  AND total_minor=NEW.amount_minor
                  AND currency=NEW.currency
            ) OR (
                NEW.kind='RETURN' AND NOT EXISTS (
                    SELECT 1 FROM return_requests
                    WHERE return_request_id=NEW.return_request_id
                      AND order_id=NEW.order_id AND account_id=NEW.account_id
                )
            )
            BEGIN SELECT RAISE(ABORT,'refund ownership mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS refund_state_insert_guard
            BEFORE INSERT ON refunds
            WHEN (
                NEW.kind='CANCELLATION' AND NOT EXISTS (
                    SELECT 1 FROM shipments
                    WHERE order_id=NEW.order_id
                      AND lifecycle_status='CANCELLED'
                )
            ) OR (
                NEW.kind='RETURN' AND NOT EXISTS (
                    SELECT 1 FROM return_requests
                    WHERE return_request_id=NEW.return_request_id
                      AND order_id=NEW.order_id AND status='REFUNDED'
                )
            )
            BEGIN SELECT RAISE(ABORT,'refund lifecycle mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS refund_identity_update_guard
            BEFORE UPDATE OF account_id,order_id,payment_attempt_id,
                             return_request_id,kind,amount_minor,currency
            ON refunds
            WHEN NEW.account_id<>OLD.account_id
              OR NEW.order_id<>OLD.order_id
              OR NEW.payment_attempt_id<>OLD.payment_attempt_id
              OR COALESCE(NEW.return_request_id,-1)<>COALESCE(OLD.return_request_id,-1)
              OR NEW.kind<>OLD.kind
              OR NEW.amount_minor<>OLD.amount_minor
              OR NEW.currency<>OLD.currency
            BEGIN SELECT RAISE(ABORT,'refund identity is immutable'); END
            """,
        )
        for statement in trigger_statements:
            conn.execute(statement)

    def ensure_seeded(self, fixture_name: str = "task-frozen-900136-v1.json") -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='snapshot_id'").fetchone()
        if row is None:
            self.reset(fixture_name)

    def _load_fixture(self, fixture_name: str) -> tuple[dict[str, Any], str]:
        candidate = (self.fixture_root / fixture_name).resolve()
        if self.fixture_root not in candidate.parents:
            raise ContractError("fixture path escapes fixture root")
        if candidate.suffix != ".json" or not candidate.is_file():
            raise ContractError("fixture does not exist")
        raw = candidate.read_bytes()
        fixture = json.loads(raw.decode("utf-8"))
        self._validate_fixture(fixture)
        return fixture, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _validate_fixture(fixture: dict[str, Any]) -> None:
        if fixture.get("schema") != "amazon-clone.fixture.v1":
            raise ContractError("unsupported fixture schema")
        if fixture.get("task_id") != TASK_ID:
            raise ContractError("fixture task id does not match")
        ranking = fixture.get("ranking") or {}
        if ranking.get("canonical_path") != BEST_SELLERS_PATH:
            raise ContractError("fixture ranking path does not match")
        rank_two = [item for item in ranking.get("items", []) if item.get("rank") == 2]
        if len(rank_two) != 1 or rank_two[0].get("asin") != TARGET_ASIN:
            raise ContractError("target ASIN must be frozen at rank 2")
        items = ranking.get("items") or []
        ranks = [item.get("rank") for item in items]
        asins = [item.get("asin") for item in items]
        if ranks != list(range(1, len(items) + 1)) or len(set(asins)) != len(asins):
            raise ContractError("ranking must use unique contiguous ranks")
        products = fixture.get("products") or []
        product_by_asin = {product.get("asin"): product for product in products}
        if len(product_by_asin) != len(products) or None in product_by_asin:
            raise ContractError("catalog ASINs must be present and unique")
        if not set(asins).issubset(product_by_asin):
            raise ContractError("every ranking ASIN must exist in the catalog")
        region = fixture.get("region") or {}
        if region.get("marketplace") != "amazon.com" or region.get("delivery_country") != "Singapore":
            raise ContractError("fixture marketplace and delivery region must match the observed source")
        if region.get("currency") != "USD":
            raise ContractError("fixture currency must be USD")
        if any(product.get("currency") != region["currency"] for product in products):
            raise ContractError("all catalog offers must use the fixture currency")
        for product in products:
            detail = product.get("pdp")
            if detail is not None and not isinstance(detail, dict):
                raise ContractError("product PDP evidence must be an object")
            if detail:
                gallery = detail.get("gallery") or []
                if not isinstance(gallery, list) or not gallery:
                    raise ContractError("detailed PDP evidence requires a gallery")
                local_paths = [detail.get("main_image"), *gallery]
                if any(not isinstance(path, str) or not path.startswith("/static/") for path in local_paths):
                    raise ContractError("PDP media paths must be local static resources")
        target = product_by_asin.get(TARGET_ASIN) or {}
        if (
            target.get("brand") != "Samsung"
            or target.get("capacity") != "1 TB"
            or target.get("color") != "Titan Gray"
            or target.get("slug") != "SAMSUNG-Portable-SSD-1TB-MU-PC1T0T"
        ):
            raise ContractError("target variant must be the Samsung T7 1 TB in Titan Gray")
        contract = fixture.get("terminal_contract") or {}
        expected = {
            "asin": TARGET_ASIN,
            "quantity": TARGET_QUANTITY,
            "desktop_path": DESKTOP_TERMINAL_PATH,
            "mobile_path": MOBILE_TERMINAL_PATH,
            "canonical_body": f"ASIN={TARGET_ASIN}&quantity={TARGET_QUANTITY}",
        }
        for key, value in expected.items():
            if contract.get(key) != value:
                raise ContractError(f"terminal contract mismatch: {key}")

    def reset(self, fixture_name: str = "task-frozen-900136-v1.json") -> dict[str, Any]:
        fixture, fixture_sha = self._load_fixture(fixture_name)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT value FROM meta WHERE key='reset_epoch'").fetchone()
            reset_epoch = int(previous[0]) + 1 if previous else 1
            reset_review_data(conn)
            for table in (
                "auth_password_reset_email_outbox",
                "auth_password_reset_flows",
                "auth_registration_email_outbox",
                "auth_registration_flows",
                "auth_mail_rate_limits",
                "refunds",
                "return_request_items",
                "return_requests",
                "order_action_keys",
                "order_events",
                "email_outbox",
                "shipments",
                "order_items",
                "orders",
                "payment_attempts",
                "checkout_lines",
                "checkout_sessions",
                "addresses",
                "task_completions",
                "request_journal",
                "task_progress",
                "navigation_events",
                "account_compare_items",
                "compare_items",
                "pending_buy_now",
                "account_cart_lines",
                "cart_lines",
                "carts",
                "auth_signin_flows",
                "browser_sessions",
                "accounts",
                "ranking_items",
                "ranking_lists",
                "product_details",
                "commerce_offers",
                "catalog_products",
                "meta",
            ):
                conn.execute(f"DELETE FROM {table}")

            products = fixture["products"]
            conn.executemany(
                """
                INSERT INTO catalog_products (
                    asin, slug, title, brand, capacity, color, price_minor,
                    list_price_minor, currency, rating, reviews, image_path,
                    badge, evidence_class
                ) VALUES (
                    :asin, :slug, :title, :brand, :capacity, :color, :price_minor,
                    :list_price_minor, :currency, :rating, :reviews, :image_path,
                    :badge, :evidence_class
                )
                """,
                products,
            )
            self._sync_commerce_offers(conn, products)
            detail_rows = [
                (
                    product["asin"],
                    json.dumps(product["pdp"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
                for product in products
                if product.get("pdp")
            ]
            if detail_rows:
                conn.executemany(
                    "INSERT INTO product_details(asin,payload_json) VALUES (?,?)",
                    detail_rows,
                )
            ranking = fixture["ranking"]
            conn.execute(
                "INSERT INTO ranking_lists(list_id,title,canonical_path) VALUES (?,?,?)",
                (ranking["list_id"], ranking["title"], ranking["canonical_path"]),
            )
            conn.executemany(
                "INSERT INTO ranking_items(list_id,rank,asin) VALUES (?,?,?)",
                [(ranking["list_id"], item["rank"], item["asin"]) for item in ranking["items"]],
            )
            metadata = {
                "schema": "amazon-clone.state.v1",
                "run_id": fixture["snapshot_id"],
                "seed": fixture["snapshot_id"],
                "controlled_now": fixture["controlled_now"],
                "reset_epoch": str(reset_epoch),
                "fixture_sha256": fixture_sha,
                "snapshot_id": fixture["snapshot_id"],
                "region": json.dumps(fixture["region"], sort_keys=True, separators=(",", ":")),
                "terminal_canonical_body": fixture["terminal_contract"]["canonical_body"],
            }
            conn.executemany("INSERT INTO meta(key,value) VALUES (?,?)", metadata.items())
            conn.commit()
        return self.normalized_state()

    def now(self, conn: sqlite3.Connection | None = None) -> str:
        owns_connection = conn is None
        db = conn or self.connect()
        try:
            row = db.execute("SELECT value FROM meta WHERE key='controlled_now'").fetchone()
            return row[0] if row else "2026-07-21T12:00:00Z"
        finally:
            if owns_connection:
                db.close()

    def meta(self) -> dict[str, str]:
        with self.connect() as conn:
            return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM meta")}

    def ensure_session(self, session_digest: str) -> int:
        with self.connect() as conn:
            epoch = int(conn.execute("SELECT value FROM meta WHERE key='reset_epoch'").fetchone()[0])
            now = self.now(conn)
            conn.execute(
                "INSERT OR IGNORE INTO browser_sessions(session_digest,reset_epoch,created_at) VALUES (?,?,?)",
                (session_digest, epoch, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO carts(session_digest,created_at) VALUES (?,?)",
                (session_digest, now),
            )
            cart_id = conn.execute(
                "SELECT cart_id FROM carts WHERE session_digest=?", (session_digest,)
            ).fetchone()[0]
            return int(cart_id)

    @staticmethod
    def _account_id_for_session(
        conn: sqlite3.Connection, session_digest: str
    ) -> int | None:
        row = conn.execute(
            "SELECT account_id FROM browser_sessions WHERE session_digest=?",
            (session_digest,),
        ).fetchone()
        if row is None or row["account_id"] is None:
            return None
        return int(row["account_id"])

    @staticmethod
    def _session_cart_id(conn: sqlite3.Connection, session_digest: str) -> int:
        row = conn.execute(
            "SELECT cart_id FROM carts WHERE session_digest=?", (session_digest,)
        ).fetchone()
        if row is None:
            raise ContractError("browser session has no anonymous cart")
        return int(row["cart_id"])

    def _merge_anonymous_cart_into_account(
        self,
        conn: sqlite3.Connection,
        session_digest: str,
        account_id: int,
    ) -> None:
        """Move guest lines into the durable account cart in the caller's transaction."""

        cart_id = self._session_cart_id(conn, session_digest)
        rows = conn.execute(
            """
            SELECT asin,quantity,selection_json,selection_key,line_state
            FROM cart_lines
            WHERE cart_id=?
            ORDER BY asin,selection_key,line_id
            """,
            (cart_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO account_cart_lines(
                    line_id,account_id,asin,quantity,selection_json,
                    selection_key,line_state
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(account_id,asin,selection_key) DO UPDATE SET
                    quantity=MIN(30,account_cart_lines.quantity+excluded.quantity),
                    line_state=CASE
                        WHEN account_cart_lines.line_state='ACTIVE'
                             OR excluded.line_state='ACTIVE'
                        THEN 'ACTIVE'
                        ELSE 'SAVED'
                    END
                """,
                (
                    self._new_cart_line_id(),
                    account_id,
                    row["asin"],
                    int(row["quantity"]),
                    row["selection_json"],
                    row["selection_key"],
                    row["line_state"],
                ),
            )
        conn.execute("DELETE FROM cart_lines WHERE cart_id=?", (cart_id,))

    def _merge_anonymous_compare_into_account(
        self,
        conn: sqlite3.Connection,
        session_digest: str,
        account_id: int,
    ) -> None:
        """Merge compatible guest comparisons behind one durable account.

        Account rows keep their ordering and win duplicates.  Guest rows get
        fresh opaque identities so a pre-authentication browser cannot replay
        an old line token after session rotation.
        """

        account_rows = conn.execute(
            """
            SELECT asin,selection_key,position
            FROM account_compare_items
            WHERE account_id=?
            ORDER BY position
            """,
            (account_id,),
        ).fetchall()
        family_keys = {
            str(profile["family_key"])
            for row in account_rows
            if (profile := self._compare_profiles.get(str(row["asin"]))) is not None
        }
        identities = {
            (str(row["asin"]), str(row["selection_key"]))
            for row in account_rows
        }
        position = len(account_rows)
        guest_rows = conn.execute(
            """
            SELECT asin,selection_json,selection_key,created_at
            FROM compare_items
            WHERE session_digest=?
            ORDER BY position
            """,
            (session_digest,),
        ).fetchall()
        for row in guest_rows:
            if position >= COMPARE_LIMIT:
                break
            asin = str(row["asin"])
            profile = self._compare_profiles.get(asin)
            identity = (asin, str(row["selection_key"]))
            if profile is None or identity in identities:
                continue
            family_key = str(profile["family_key"])
            if family_keys and family_keys != {family_key}:
                continue
            position += 1
            conn.execute(
                """
                INSERT INTO account_compare_items(
                    compare_line_id,account_id,asin,selection_json,
                    selection_key,position,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    self._new_compare_line_id(),
                    account_id,
                    asin,
                    row["selection_json"],
                    row["selection_key"],
                    position,
                    row["created_at"],
                ),
            )
            identities.add(identity)
            family_keys.add(family_key)
        conn.execute(
            "DELETE FROM compare_items WHERE session_digest=?", (session_digest,)
        )

    def begin_signin(
        self, session_digest: str, email: str, return_to: str | None
    ) -> None:
        """Persist only the pending identifier needed by the two-stage sign-in flow."""

        self.ensure_session(session_digest)
        email_normalized = normalize_email(email)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_signin_flows(
                    session_digest,email_normalized,return_to,updated_at
                ) VALUES (?,?,?,?)
                ON CONFLICT(session_digest) DO UPDATE SET
                    email_normalized=excluded.email_normalized,
                    return_to=excluded.return_to,
                    updated_at=excluded.updated_at
                """,
                (session_digest, email_normalized, return_to, self.now(conn)),
            )

    def account_exists(self, email: str) -> bool:
        email_normalized = normalize_email(email)
        with self.connect() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM accounts WHERE email_normalized=?",
                    (email_normalized,),
                ).fetchone()
            )

    @staticmethod
    def _registration_code_hash(code: str, salt: bytes) -> bytes:
        return hmac.new(salt, code.encode("ascii"), hashlib.sha256).digest()

    @staticmethod
    def _masked_email(email: str) -> str:
        local, separator, domain = email.partition("@")
        if not separator:
            return "***"
        visible = local[:1]
        return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"

    @staticmethod
    def _queued_mail_state(mail_mode: str) -> tuple[str, int]:
        if mail_mode == MAIL_LOCAL_ONLY:
            return MAIL_LOCAL_ONLY, 1
        if mail_mode == MAIL_SMTP_PENDING:
            return MAIL_SMTP_PENDING, 0
        raise ContractError("mail mode must be LOCAL_ONLY or SMTP_PENDING")

    @staticmethod
    def _recipient_rate_key(email_normalized: str) -> str:
        return hashlib.sha256(email_normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _consume_auth_mail_budget(
        conn: sqlite3.Connection,
        purpose: str,
        scopes: list[tuple[str, str]],
        current_time: int,
    ) -> bool:
        """Atomically enforce cooldown and a persistent rolling-window budget."""

        normalized_scopes = list(dict.fromkeys(scopes))
        rows: dict[tuple[str, str], sqlite3.Row | None] = {}
        for scope_type, scope_key in normalized_scopes:
            row = conn.execute(
                """
                SELECT window_started_at,send_count,last_sent_at
                FROM auth_mail_rate_limits
                WHERE purpose=? AND scope_type=? AND scope_key=?
                """,
                (purpose, scope_type, scope_key),
            ).fetchone()
            rows[(scope_type, scope_key)] = row
            if row is None:
                continue
            if current_time < int(row["last_sent_at"]) + AUTH_MAIL_COOLDOWN_SECONDS:
                return False
            window_is_current = (
                current_time
                < int(row["window_started_at"]) + AUTH_MAIL_WINDOW_SECONDS
            )
            if (
                window_is_current
                and int(row["send_count"]) >= AUTH_MAIL_MAX_SENDS_PER_WINDOW
            ):
                return False

        for scope_type, scope_key in normalized_scopes:
            row = rows[(scope_type, scope_key)]
            if row is None:
                conn.execute(
                    """
                    INSERT INTO auth_mail_rate_limits(
                        purpose,scope_type,scope_key,window_started_at,
                        send_count,last_sent_at
                    ) VALUES (?,?,?,?,1,?)
                    """,
                    (purpose, scope_type, scope_key, current_time, current_time),
                )
                continue
            window_is_current = (
                current_time
                < int(row["window_started_at"]) + AUTH_MAIL_WINDOW_SECONDS
            )
            conn.execute(
                """
                UPDATE auth_mail_rate_limits
                SET window_started_at=?,send_count=?,last_sent_at=?
                WHERE purpose=? AND scope_type=? AND scope_key=?
                """,
                (
                    int(row["window_started_at"])
                    if window_is_current
                    else current_time,
                    int(row["send_count"]) + 1 if window_is_current else 1,
                    current_time,
                    purpose,
                    scope_type,
                    scope_key,
                ),
            )
        return True

    def begin_registration(
        self,
        session_digest: str,
        email: str,
        display_name: str,
        password: str,
        return_to: str | None,
        *,
        mail_mode: str = MAIL_LOCAL_ONLY,
    ) -> bool:
        """Queue verification without creating an account until its OTP succeeds."""

        self.ensure_session(session_digest)
        mail_status, is_simulation = self._queued_mail_state(mail_mode)
        email_normalized = normalize_email(email)
        password_salt, password_hash, scheme = password_record(password)
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_salt = secrets.token_bytes(REGISTRATION_CODE_BYTES)
        code_hash = self._registration_code_hash(code, code_salt)
        pending_id = secrets.token_urlsafe(24)
        created_at = int(time.time())
        expires_at = created_at + REGISTRATION_CODE_TTL_SECONDS
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM accounts WHERE email_normalized=?",
                (email_normalized,),
            ).fetchone():
                conn.rollback()
                return False
            if not self._consume_auth_mail_budget(
                conn,
                "registration",
                [
                    ("session", session_digest),
                    ("recipient", self._recipient_rate_key(email_normalized)),
                ],
                created_at,
            ):
                conn.rollback()
                return False
            conn.execute(
                "DELETE FROM auth_registration_flows WHERE session_digest=?",
                (session_digest,),
            )
            conn.execute(
                """
                INSERT INTO auth_registration_flows(
                    pending_id,session_digest,email_normalized,display_name,
                    password_salt,password_hash,password_scheme,return_to,
                    code_salt,code_hash,expires_at,attempts,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pending_id,
                    session_digest,
                    email_normalized,
                    display_name,
                    password_salt,
                    password_hash,
                    scheme,
                    return_to,
                    code_salt,
                    code_hash,
                    expires_at,
                    0,
                    created_at,
                    created_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO auth_registration_email_outbox(
                    pending_id,recipient,template,verification_code,status,
                    is_simulation,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    pending_id,
                    email_normalized,
                    "registration-verification",
                    code,
                    mail_status,
                    is_simulation,
                    created_at,
                ),
            )
            conn.commit()
        return True

    def pending_registration(self, session_digest: str) -> dict[str, Any] | None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT email_normalized,expires_at,attempts
                FROM auth_registration_flows
                WHERE session_digest=?
                """,
                (session_digest,),
            ).fetchone()
        if row is None:
            return None
        return {
            "masked_email": self._masked_email(str(row["email_normalized"])),
            "expires_at": int(row["expires_at"]),
            "attempts_remaining": max(
                0, REGISTRATION_CODE_MAX_ATTEMPTS - int(row["attempts"])
            ),
        }

    def registration_outbox(
        self, session_digest: str | None = None
    ) -> list[dict[str, Any]]:
        """Read protected registration delivery state, exposing OTP only locally."""

        where = "WHERE f.session_digest=?" if session_digest else ""
        parameters: tuple[str, ...] = (session_digest,) if session_digest else ()
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT o.email_id,o.pending_id,o.recipient,o.template,
                       o.verification_code,o.status,o.is_simulation,o.created_at,
                       o.delivery_attempts,o.last_error,o.attempted_at,o.sent_at,
                       f.expires_at,f.attempts
                FROM auth_registration_email_outbox o
                JOIN auth_registration_flows f ON f.pending_id=o.pending_id
                {where}
                ORDER BY o.email_id
                """,
                parameters,
            ).fetchall()
        current_time = int(time.time())
        return [
            {
                "email_id": int(row["email_id"]),
                "pending_id": str(row["pending_id"]),
                "recipient_masked": self._masked_email(str(row["recipient"])),
                "template": str(row["template"]),
                "verification_code": (
                    str(row["verification_code"])
                    if str(row["status"]) == MAIL_LOCAL_ONLY
                    else None
                ),
                "status": str(row["status"]),
                "is_simulation": bool(row["is_simulation"]),
                "created_at": int(row["created_at"]),
                "expires_at": int(row["expires_at"]),
                "expired": current_time > int(row["expires_at"]),
                "attempts": int(row["attempts"]),
                "delivery_attempts": int(row["delivery_attempts"]),
                "last_error": row["last_error"],
                "attempted_at": row["attempted_at"],
                "sent_at": row["sent_at"],
            }
            for row in rows
        ]

    def resend_registration_code(
        self, session_digest: str, *, mail_mode: str = MAIL_LOCAL_ONLY
    ) -> bool:
        """Invalidate the previous code and queue one replacement delivery."""

        self.ensure_session(session_digest)
        mail_status, is_simulation = self._queued_mail_state(mail_mode)
        created_at = int(time.time())
        expires_at = created_at + REGISTRATION_CODE_TTL_SECONDS
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT f.pending_id,f.email_normalized,f.code_salt,f.code_hash,
                       f.attempts
                FROM auth_registration_flows f
                WHERE f.session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            if int(row["attempts"]) >= REGISTRATION_CODE_MAX_ATTEMPTS:
                conn.rollback()
                return False
            if not self._consume_auth_mail_budget(
                conn,
                "registration",
                [
                    ("session", session_digest),
                    (
                        "recipient",
                        self._recipient_rate_key(str(row["email_normalized"])),
                    ),
                ],
                created_at,
            ):
                conn.rollback()
                return False
            code = f"{secrets.randbelow(1_000_000):06d}"
            while hmac.compare_digest(
                self._registration_code_hash(code, bytes(row["code_salt"])),
                bytes(row["code_hash"]),
            ):
                code = f"{secrets.randbelow(1_000_000):06d}"
            code_salt = secrets.token_bytes(REGISTRATION_CODE_BYTES)
            code_hash = self._registration_code_hash(code, code_salt)
            conn.execute(
                """
                UPDATE auth_registration_flows
                SET code_salt=?,code_hash=?,expires_at=?,attempts=0,updated_at=?
                WHERE session_digest=?
                """,
                (code_salt, code_hash, expires_at, created_at, session_digest),
            )
            conn.execute(
                """
                DELETE FROM auth_registration_email_outbox WHERE pending_id=?
                """,
                (str(row["pending_id"]),),
            )
            conn.execute(
                """
                INSERT INTO auth_registration_email_outbox(
                    pending_id,recipient,template,verification_code,status,
                    is_simulation,created_at
                ) VALUES (?,?,'registration-verification',?,?,?,?)
                """,
                (
                    str(row["pending_id"]),
                    str(row["email_normalized"]),
                    code,
                    mail_status,
                    is_simulation,
                    created_at,
                ),
            )
            conn.commit()
        return True

    def verify_registration_code(
        self, session_digest: str, code: str
    ) -> tuple[str, str | None]:
        """Consume one pending OTP and create/bind the account atomically."""

        self.ensure_session(session_digest)
        current_time = int(time.time())
        try:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT * FROM auth_registration_flows
                    WHERE session_digest=?
                    """,
                    (session_digest,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return "missing", None
                attempts = int(row["attempts"])
                if current_time > int(row["expires_at"]):
                    conn.rollback()
                    return "expired", None
                if attempts >= REGISTRATION_CODE_MAX_ATTEMPTS:
                    conn.rollback()
                    return "locked", None
                candidate_hash = self._registration_code_hash(
                    code, bytes(row["code_salt"])
                )
                if not hmac.compare_digest(candidate_hash, bytes(row["code_hash"])):
                    attempts += 1
                    conn.execute(
                        """
                        UPDATE auth_registration_flows
                        SET attempts=?,updated_at=?
                        WHERE session_digest=?
                        """,
                        (attempts, current_time, session_digest),
                    )
                    conn.commit()
                    return (
                        "locked"
                        if attempts >= REGISTRATION_CODE_MAX_ATTEMPTS
                        else "invalid",
                        None,
                    )

                cursor = conn.execute(
                    """
                    INSERT INTO accounts(
                        email_normalized,display_name,password_salt,password_hash,
                        password_scheme,created_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        str(row["email_normalized"]),
                        str(row["display_name"]),
                        bytes(row["password_salt"]),
                        bytes(row["password_hash"]),
                        str(row["password_scheme"]),
                        self.now(conn),
                    ),
                )
                account_id = int(cursor.lastrowid)
                self._merge_anonymous_cart_into_account(
                    conn, session_digest, account_id
                )
                self._merge_anonymous_compare_into_account(
                    conn, session_digest, account_id
                )
                conn.execute(
                    "UPDATE browser_sessions SET account_id=? WHERE session_digest=?",
                    (account_id, session_digest),
                )
                conn.execute(
                    "DELETE FROM auth_signin_flows WHERE session_digest=?",
                    (session_digest,),
                )
                conn.execute(
                    "DELETE FROM auth_registration_flows WHERE session_digest=?",
                    (session_digest,),
                )
                return_to = str(row["return_to"]) if row["return_to"] else None
                conn.commit()
            return "verified", return_to
        except sqlite3.IntegrityError:
            with self.connect() as conn:
                conn.execute(
                    "DELETE FROM auth_registration_flows WHERE session_digest=?",
                    (session_digest,),
                )
            return "duplicate", None

    def begin_password_reset(
        self,
        session_digest: str,
        email: str,
        return_to: str | None,
        *,
        mail_mode: str = MAIL_LOCAL_ONLY,
    ) -> None:
        """Start an enumeration-resistant reset flow for known or unknown email."""

        self.ensure_session(session_digest)
        mail_status, is_simulation = self._queued_mail_state(mail_mode)
        email_normalized = normalize_email(email)
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_salt = secrets.token_bytes(REGISTRATION_CODE_BYTES)
        code_hash = self._registration_code_hash(code, code_salt)
        reset_id = secrets.token_urlsafe(24)
        created_at = int(time.time())
        expires_at = created_at + PASSWORD_RESET_CODE_TTL_SECONDS
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_row = conn.execute(
                """
                SELECT account_id,email_normalized
                FROM accounts WHERE email_normalized=?
                """,
                (email_normalized,),
            ).fetchone()
            account_id = (
                int(account_row["account_id"]) if account_row is not None else None
            )
            rate_scopes = [
                ("session", session_digest),
                ("recipient", self._recipient_rate_key(email_normalized)),
            ]
            if account_id is not None:
                rate_scopes.append(("account", str(account_id)))
            if not self._consume_auth_mail_budget(
                conn,
                "password-reset",
                rate_scopes,
                created_at,
            ):
                # Preserve an already-issued code only when this same browser
                # asked for the same account again. A different target replaces
                # the session flow with a decoy, preventing an old code for A
                # from being entered after the UI has requested account B.
                existing_session_flow = conn.execute(
                    """
                    SELECT f.account_id,a.email_normalized
                    FROM auth_password_reset_flows f
                    LEFT JOIN accounts a ON a.account_id=f.account_id
                    WHERE f.session_digest=?
                    """,
                    (session_digest,),
                ).fetchone()
                preserve_existing = bool(
                    existing_session_flow is not None
                    and existing_session_flow["account_id"] is not None
                    and str(existing_session_flow["email_normalized"])
                    == email_normalized
                )
                if not preserve_existing:
                    conn.execute(
                        "DELETE FROM auth_password_reset_flows WHERE session_digest=?",
                        (session_digest,),
                    )
                    conn.execute(
                        """
                        INSERT INTO auth_password_reset_flows(
                            reset_id,session_digest,account_id,return_to,code_salt,
                            code_hash,expires_at,attempts,verified_at,created_at,
                            updated_at
                        ) VALUES (?,?,NULL,NULL,?,?,?,0,NULL,?,?)
                        """,
                        (
                            reset_id,
                            session_digest,
                            code_salt,
                            code_hash,
                            expires_at,
                            created_at,
                            created_at,
                        ),
                    )
                conn.commit()
                return
            conn.execute(
                "DELETE FROM auth_password_reset_flows WHERE session_digest=?",
                (session_digest,),
            )
            if account_id is not None:
                # One active reset code per account: a request from another
                # browser replaces every older code for that account.
                conn.execute(
                    "DELETE FROM auth_password_reset_flows WHERE account_id=?",
                    (account_id,),
                )
            conn.execute(
                """
                INSERT INTO auth_password_reset_flows(
                    reset_id,session_digest,account_id,return_to,code_salt,
                    code_hash,expires_at,attempts,verified_at,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,0,NULL,?,?)
                """,
                (
                    reset_id,
                    session_digest,
                    account_id,
                    return_to,
                    code_salt,
                    code_hash,
                    expires_at,
                    created_at,
                    created_at,
                ),
            )
            if account_row is not None:
                # The recipient is loaded from the account row, never copied
                # from an arbitrary form field after lookup.
                conn.execute(
                    """
                    INSERT INTO auth_password_reset_email_outbox(
                        reset_id,recipient,template,verification_code,status,
                        is_simulation,created_at
                    ) VALUES (?,?,'password-reset-verification',?,?,?,?)
                    """,
                    (
                        reset_id,
                        str(account_row["email_normalized"]),
                        code,
                        mail_status,
                        is_simulation,
                        created_at,
                    ),
                )
            conn.commit()

    def pending_password_reset(
        self, session_digest: str
    ) -> dict[str, Any] | None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT expires_at,attempts,verified_at
                FROM auth_password_reset_flows WHERE session_digest=?
                """,
                (session_digest,),
            ).fetchone()
        if row is None:
            return None
        return {
            "expires_at": int(row["expires_at"]),
            "attempts_remaining": max(
                0, PASSWORD_RESET_CODE_MAX_ATTEMPTS - int(row["attempts"])
            ),
            "verified": row["verified_at"] is not None,
        }

    def password_reset_outbox(
        self, session_digest: str | None = None
    ) -> list[dict[str, Any]]:
        """Read protected reset delivery state, exposing OTP only locally."""

        where = "WHERE f.session_digest=?" if session_digest else ""
        parameters: tuple[str, ...] = (session_digest,) if session_digest else ()
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT o.email_id,o.reset_id,o.recipient,o.template,
                       o.verification_code,o.status,o.is_simulation,o.created_at,
                       o.delivery_attempts,o.last_error,o.attempted_at,o.sent_at,
                       f.expires_at,f.attempts,f.verified_at
                FROM auth_password_reset_email_outbox o
                JOIN auth_password_reset_flows f ON f.reset_id=o.reset_id
                {where}
                ORDER BY o.email_id
                """,
                parameters,
            ).fetchall()
        current_time = int(time.time())
        return [
            {
                "email_id": int(row["email_id"]),
                "reset_id": str(row["reset_id"]),
                "recipient_masked": self._masked_email(str(row["recipient"])),
                "template": str(row["template"]),
                "verification_code": (
                    str(row["verification_code"])
                    if str(row["status"]) == MAIL_LOCAL_ONLY
                    else None
                ),
                "status": str(row["status"]),
                "is_simulation": bool(row["is_simulation"]),
                "created_at": int(row["created_at"]),
                "expires_at": int(row["expires_at"]),
                "expired": current_time > int(row["expires_at"]),
                "attempts": int(row["attempts"]),
                "verified": row["verified_at"] is not None,
                "delivery_attempts": int(row["delivery_attempts"]),
                "last_error": row["last_error"],
                "attempted_at": row["attempted_at"],
                "sent_at": row["sent_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _public_mail_status(
        message: dict[str, Any], *, flow_attempt_limit: int
    ) -> dict[str, Any]:
        """Return only the delivery fields safe for a flow-owning browser."""

        status = str(message["status"])
        delivery_attempts = int(message["delivery_attempts"])
        flow_attempts = int(message.get("attempts", flow_attempt_limit))
        expired = bool(message.get("expired", False))
        verified = bool(message.get("verified", False))
        return {
            "status": status,
            "delivery_attempts": delivery_attempts,
            "can_retry": (
                status == MAIL_SMTP_FAILED
                and delivery_attempts < MAIL_DELIVERY_MAX_ATTEMPTS
                and flow_attempts < flow_attempt_limit
                and not expired
                and not verified
            ),
        }

    def registration_mail_status(
        self, session_digest: str
    ) -> dict[str, Any] | None:
        """Read mail state only for this browser's active registration flow."""

        self.ensure_session(session_digest)
        messages = self.registration_outbox(session_digest)
        return (
            self._public_mail_status(
                messages[0], flow_attempt_limit=REGISTRATION_CODE_MAX_ATTEMPTS
            )
            if messages
            else None
        )

    def password_reset_mail_status(
        self, session_digest: str
    ) -> dict[str, Any] | None:
        """Read mail state only for this browser's active recovery flow."""

        self.ensure_session(session_digest)
        messages = self.password_reset_outbox(session_digest)
        if not messages:
            return None
        return self._public_mail_status(
            messages[0], flow_attempt_limit=PASSWORD_RESET_CODE_MAX_ATTEMPTS
        )

    def resend_password_reset_code(
        self, session_digest: str, *, mail_mode: str = MAIL_LOCAL_ONLY
    ) -> bool:
        """Replace the current reset OTP without revealing account existence."""

        self.ensure_session(session_digest)
        mail_status, is_simulation = self._queued_mail_state(mail_mode)
        created_at = int(time.time())
        expires_at = created_at + PASSWORD_RESET_CODE_TTL_SECONDS
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT f.reset_id,f.account_id,f.code_salt,f.code_hash,
                       f.attempts,a.email_normalized
                FROM auth_password_reset_flows f
                LEFT JOIN accounts a ON a.account_id=f.account_id
                WHERE f.session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            if int(row["attempts"]) >= PASSWORD_RESET_CODE_MAX_ATTEMPTS:
                conn.rollback()
                return False
            rate_scopes = [("session", session_digest)]
            if row["account_id"] is not None:
                rate_scopes.extend(
                    [
                        ("account", str(row["account_id"])),
                        (
                            "recipient",
                            self._recipient_rate_key(str(row["email_normalized"])),
                        ),
                    ]
                )
            if not self._consume_auth_mail_budget(
                conn,
                "password-reset",
                rate_scopes,
                created_at,
            ):
                conn.rollback()
                return False
            code = f"{secrets.randbelow(1_000_000):06d}"
            while hmac.compare_digest(
                self._registration_code_hash(code, bytes(row["code_salt"])),
                bytes(row["code_hash"]),
            ):
                code = f"{secrets.randbelow(1_000_000):06d}"
            code_salt = secrets.token_bytes(REGISTRATION_CODE_BYTES)
            code_hash = self._registration_code_hash(code, code_salt)
            conn.execute(
                """
                UPDATE auth_password_reset_flows
                SET code_salt=?,code_hash=?,expires_at=?,attempts=0,
                    verified_at=NULL,updated_at=?
                WHERE session_digest=?
                """,
                (code_salt, code_hash, expires_at, created_at, session_digest),
            )
            if row["account_id"] is not None:
                conn.execute(
                    """
                    DELETE FROM auth_password_reset_email_outbox WHERE reset_id=?
                    """,
                    (str(row["reset_id"]),),
                )
                conn.execute(
                    """
                    INSERT INTO auth_password_reset_email_outbox(
                        reset_id,recipient,template,verification_code,status,
                        is_simulation,created_at
                    ) VALUES (?,?,'password-reset-verification',?,?,?,?)
                    """,
                    (
                        str(row["reset_id"]),
                        str(row["email_normalized"]),
                        code,
                        mail_status,
                        is_simulation,
                        created_at,
                    ),
                )
            conn.commit()
        return True

    def verify_password_reset_code(
        self, session_digest: str, code: str
    ) -> str:
        """Consume one reset OTP and unlock only this session's password form."""

        self.ensure_session(session_digest)
        current_time = int(time.time())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM auth_password_reset_flows WHERE session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return "missing"
            if row["verified_at"] is not None:
                conn.rollback()
                return "used"
            if current_time > int(row["expires_at"]):
                conn.rollback()
                return "expired"
            attempts = int(row["attempts"])
            if attempts >= PASSWORD_RESET_CODE_MAX_ATTEMPTS:
                conn.rollback()
                return "locked"
            candidate_hash = self._registration_code_hash(
                code, bytes(row["code_salt"])
            )
            valid = hmac.compare_digest(candidate_hash, bytes(row["code_hash"]))
            if not valid or row["account_id"] is None:
                attempts += 1
                conn.execute(
                    """
                    UPDATE auth_password_reset_flows
                    SET attempts=?,updated_at=? WHERE session_digest=?
                    """,
                    (attempts, current_time, session_digest),
                )
                conn.commit()
                return (
                    "locked"
                    if attempts >= PASSWORD_RESET_CODE_MAX_ATTEMPTS
                    else "invalid"
                )
            conn.execute(
                """
                UPDATE auth_password_reset_flows
                SET verified_at=?,updated_at=?,code_salt=?,code_hash=?
                WHERE session_digest=?
                """,
                (
                    current_time,
                    current_time,
                    secrets.token_bytes(REGISTRATION_CODE_BYTES),
                    secrets.token_bytes(32),
                    session_digest,
                ),
            )
            conn.commit()
            return "verified"

    def complete_password_reset(
        self, session_digest: str, password: str
    ) -> tuple[str, str | None]:
        """Set the new password, revoke other sessions, and authenticate this one."""

        self.ensure_session(session_digest)
        current_time = int(time.time())
        password_salt, password_hash, scheme = password_record(password)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT f.*,a.email_normalized
                FROM auth_password_reset_flows f
                LEFT JOIN accounts a ON a.account_id=f.account_id
                WHERE f.session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            if row is None or row["account_id"] is None:
                conn.rollback()
                return "missing", None
            if current_time > int(row["expires_at"]):
                conn.rollback()
                return "expired", None
            if row["verified_at"] is None:
                conn.rollback()
                return "unverified", None

            account_id = int(row["account_id"])
            return_to = str(row["return_to"]) if row["return_to"] else None
            conn.execute(
                """
                UPDATE accounts
                SET password_salt=?,password_hash=?,password_scheme=?
                WHERE account_id=?
                """,
                (password_salt, password_hash, scheme, account_id),
            )
            # Password reset revokes every existing authenticated browser.  The
            # recovery session is rebound below and then rotated by the server.
            conn.execute(
                "UPDATE browser_sessions SET account_id=NULL WHERE account_id=?",
                (account_id,),
            )
            conn.execute(
                "DELETE FROM auth_signin_flows WHERE email_normalized=?",
                (str(row["email_normalized"]),),
            )
            conn.execute(
                "DELETE FROM auth_password_reset_flows WHERE account_id=?",
                (account_id,),
            )
            self._merge_anonymous_cart_into_account(
                conn, session_digest, account_id
            )
            self._merge_anonymous_compare_into_account(
                conn, session_digest, account_id
            )
            conn.execute(
                "UPDATE browser_sessions SET account_id=? WHERE session_digest=?",
                (account_id, session_digest),
            )
            conn.commit()
        return "reset", return_to

    def _retry_auth_mail(self, session_digest: str, kind: str) -> bool:
        """Requeue one failed OTP delivery without accepting a public mail id.

        The browser session selects the active flow.  Normally the original
        still-valid OTP is retained.  Older databases may contain failed jobs
        whose plaintext code was redacted; in that case a replacement code is
        generated atomically and the old hash is invalidated.
        """

        self.ensure_session(session_digest)
        if kind == "registration":
            outbox = "auth_registration_email_outbox"
            flow_table = "auth_registration_flows"
            flow_key = "pending_id"
            ttl_seconds = REGISTRATION_CODE_TTL_SECONDS
            verified_clause = ""
        elif kind == "password-reset":
            outbox = "auth_password_reset_email_outbox"
            flow_table = "auth_password_reset_flows"
            flow_key = "reset_id"
            ttl_seconds = PASSWORD_RESET_CODE_TTL_SECONDS
            verified_clause = "AND f.verified_at IS NULL"
        else:
            raise ContractError("unknown auth mail delivery kind")

        current_time = int(time.time())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""
                SELECT o.email_id,o.verification_code,o.delivery_attempts,
                       f.{flow_key} AS flow_id,f.code_salt,f.code_hash,
                       f.expires_at,f.attempts
                FROM {outbox} o
                JOIN {flow_table} f ON f.{flow_key}=o.{flow_key}
                WHERE f.session_digest=? AND o.status='SMTP_FAILED'
                  AND o.claim_token IS NULL
                  AND o.delivery_attempts < ?
                  AND f.expires_at >= ?
                  AND f.attempts < ?
                  {verified_clause}
                """,
                (
                    session_digest,
                    MAIL_DELIVERY_MAX_ATTEMPTS,
                    current_time,
                    (
                        REGISTRATION_CODE_MAX_ATTEMPTS
                        if kind == "registration"
                        else PASSWORD_RESET_CODE_MAX_ATTEMPTS
                    ),
                ),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False

            code = str(row["verification_code"])
            retained_code_is_valid = bool(
                re.fullmatch(r"[0-9]{6}", code)
                and hmac.compare_digest(
                    self._registration_code_hash(code, bytes(row["code_salt"])),
                    bytes(row["code_hash"]),
                )
            )
            if not retained_code_is_valid:
                code = f"{secrets.randbelow(1_000_000):06d}"
                code_salt = secrets.token_bytes(REGISTRATION_CODE_BYTES)
                code_hash = self._registration_code_hash(code, code_salt)
                expires_at = current_time + ttl_seconds
                conn.execute(
                    f"""
                    UPDATE {flow_table}
                    SET code_salt=?,code_hash=?,expires_at=?,attempts=0,updated_at=?
                    WHERE {flow_key}=?
                    """,
                    (
                        code_salt,
                        code_hash,
                        expires_at,
                        current_time,
                        str(row["flow_id"]),
                    ),
                )

            cursor = conn.execute(
                f"""
                UPDATE {outbox}
                SET verification_code=?,status='SMTP_PENDING',is_simulation=0,
                    claim_token=NULL,last_error=NULL,attempted_at=NULL,sent_at=NULL
                WHERE email_id=? AND status='SMTP_FAILED'
                  AND claim_token IS NULL
                """,
                (code, int(row["email_id"])),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
        return True

    def retry_registration_mail(self, session_digest: str) -> bool:
        return self._retry_auth_mail(session_digest, "registration")

    def retry_password_reset_mail(self, session_digest: str) -> bool:
        return self._retry_auth_mail(session_digest, "password-reset")

    def retry_order_mail(
        self, session_digest: str, order_id: int | str
    ) -> bool:
        """Requeue a failed confirmation only for the authenticated owner."""

        self.ensure_session(session_digest)
        normalized = self._normalized_order_id(order_id)
        if normalized is None:
            raise ContractError("order id must be a positive integer")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE email_outbox
                SET status='SMTP_PENDING',is_simulation=0,claim_token=NULL,
                    last_error=NULL,attempted_at=NULL,sent_at=NULL
                WHERE order_id=? AND status='SMTP_FAILED'
                  AND claim_token IS NULL
                  AND delivery_attempts < ?
                  AND account_id=(
                      SELECT account_id FROM browser_sessions
                      WHERE session_digest=? AND account_id IS NOT NULL
                  )
                """,
                (normalized, MAIL_DELIVERY_MAX_ATTEMPTS, session_digest),
            )
            return cursor.rowcount == 1

    def registration_delivery(
        self, session_digest: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT o.email_id,o.recipient,o.verification_code,f.expires_at
                FROM auth_registration_email_outbox o
                JOIN auth_registration_flows f ON f.pending_id=o.pending_id
                WHERE f.session_digest=? AND o.status='SMTP_PENDING'
                  AND o.claim_token IS NULL
                  AND o.delivery_attempts < ?
                """,
                (session_digest, MAIL_DELIVERY_MAX_ATTEMPTS),
            ).fetchone()
        if row is None:
            return None
        return {
            "kind": "registration",
            "email_id": int(row["email_id"]),
            "recipient": str(row["recipient"]),
            "subject": "Verify your Amazon Clone email address",
            "body": (
                "Your Amazon Clone verification code is "
                f"{row['verification_code']}. It expires in 10 minutes.\n\n"
                "If you did not create this account, you can ignore this message."
            ),
        }

    def password_reset_delivery(
        self, session_digest: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT o.email_id,o.recipient,o.verification_code
                FROM auth_password_reset_email_outbox o
                JOIN auth_password_reset_flows f ON f.reset_id=o.reset_id
                WHERE f.session_digest=? AND o.status='SMTP_PENDING'
                  AND o.claim_token IS NULL
                  AND o.delivery_attempts < ?
                """,
                (session_digest, MAIL_DELIVERY_MAX_ATTEMPTS),
            ).fetchone()
        if row is None:
            return None
        return {
            "kind": "password-reset",
            "email_id": int(row["email_id"]),
            "recipient": str(row["recipient"]),
            "subject": "Reset your Amazon Clone password",
            "body": (
                "Your Amazon Clone password reset code is "
                f"{row['verification_code']}. It expires in 10 minutes.\n\n"
                "If you did not request a password reset, you can ignore this message."
            ),
        }

    def order_mail_delivery(self, order_id: int | str) -> dict[str, Any] | None:
        normalized = self._normalized_order_id(order_id)
        if normalized is None:
            raise ContractError("order id must be a positive integer")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT email_id,recipient,subject,payload_json
                FROM email_outbox
                WHERE order_id=? AND status='SMTP_PENDING'
                  AND claim_token IS NULL
                  AND delivery_attempts < ?
                """,
                (normalized, MAIL_DELIVERY_MAX_ATTEMPTS),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        item_lines = [
            f"- {item['asin']} × {int(item['quantity'])}"
            for item in payload.get("items", [])
        ]
        return {
            "kind": "order-confirmation",
            "email_id": int(row["email_id"]),
            "recipient": str(row["recipient"]),
            "subject": str(row["subject"]),
            "body": (
                f"Your Amazon Clone order #{normalized} was placed.\n\n"
                + ("\n".join(item_lines) or "No item summary is available.")
                + "\n\n"
                + f"Total: {int(payload['total_minor']) / 100:.2f} "
                + str(payload["currency"])
                + "\n\nThis message confirms a local simulated order; no card was charged."
            ),
        }

    @staticmethod
    def _mail_delivery_target(kind: str) -> tuple[str, str]:
        targets = {
            "registration": ("auth_registration_email_outbox", "email_id"),
            "password-reset": ("auth_password_reset_email_outbox", "email_id"),
            "order-confirmation": ("email_outbox", "email_id"),
        }
        target = targets.get(kind)
        if target is None:
            raise ContractError("unknown mail delivery kind")
        return target

    def claim_mail_delivery(self, kind: str, email_id: int) -> str | None:
        """Claim one pending job so concurrent requests cannot send it twice."""

        if isinstance(email_id, bool) or not isinstance(email_id, int) or email_id <= 0:
            raise ContractError("mail delivery id must be a positive integer")
        table, id_column = self._mail_delivery_target(kind)
        claim_token = secrets.token_urlsafe(24)
        attempted_at = int(time.time())
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE {table}
                SET claim_token=?,attempted_at=?,
                    delivery_attempts=delivery_attempts+1
                WHERE {id_column}=? AND status='SMTP_PENDING'
                  AND claim_token IS NULL
                  AND delivery_attempts < ?
                """,
                (
                    claim_token,
                    attempted_at,
                    email_id,
                    MAIL_DELIVERY_MAX_ATTEMPTS,
                ),
            )
        return claim_token if cursor.rowcount == 1 else None

    def recover_pending_mail_claims(self) -> int:
        """Release claims left by a previous single-process server instance."""

        recovered = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in (
                "auth_registration_email_outbox",
                "auth_password_reset_email_outbox",
                "email_outbox",
            ):
                cursor = conn.execute(
                    f"""
                    UPDATE {table} SET claim_token=NULL
                    WHERE status='SMTP_PENDING' AND claim_token IS NOT NULL
                      AND delivery_attempts < ?
                    """,
                    (MAIL_DELIVERY_MAX_ATTEMPTS,),
                )
                recovered += cursor.rowcount
            conn.commit()
        return recovered

    def fail_exhausted_pending_mail(self) -> int:
        """Close jobs that already consumed the bounded SMTP attempt budget."""

        failed = 0
        attempted_at = int(time.time())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in (
                "auth_registration_email_outbox",
                "auth_password_reset_email_outbox",
                "email_outbox",
            ):
                cursor = conn.execute(
                    f"""
                    UPDATE {table}
                    SET status='SMTP_FAILED',is_simulation=0,claim_token=NULL,
                        last_error='DeliveryAttemptsExhausted',
                        attempted_at=COALESCE(attempted_at,?),sent_at=NULL
                    WHERE status='SMTP_PENDING' AND delivery_attempts >= ?
                    """,
                    (attempted_at, MAIL_DELIVERY_MAX_ATTEMPTS),
                )
                failed += cursor.rowcount
            conn.commit()
        return failed

    def reconcile_mail_for_local_only(self) -> int:
        """Convert unfinished SMTP jobs when this startup has no transport.

        Auth flows keep a usable local OTP.  A legacy failed row may contain a
        redacted code, so repair its code/hash atomically before exposing it in
        the protected LOCAL_ONLY outbox.
        """

        current_time = int(time.time())
        localized = 0
        auth_targets = (
            (
                "auth_registration_email_outbox",
                "auth_registration_flows",
                "pending_id",
                REGISTRATION_CODE_TTL_SECONDS,
                "NULL AS verified_at",
            ),
            (
                "auth_password_reset_email_outbox",
                "auth_password_reset_flows",
                "reset_id",
                PASSWORD_RESET_CODE_TTL_SECONDS,
                "f.verified_at AS verified_at",
            ),
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for (
                outbox,
                flow_table,
                flow_key,
                ttl_seconds,
                verified_column,
            ) in auth_targets:
                rows = conn.execute(
                    f"""
                    SELECT o.email_id,o.verification_code,
                           f.{flow_key} AS flow_id,f.code_salt,f.code_hash,
                           f.expires_at,{verified_column}
                    FROM {outbox} o
                    JOIN {flow_table} f ON f.{flow_key}=o.{flow_key}
                    WHERE o.status IN ('SMTP_PENDING','SMTP_FAILED')
                    """
                ).fetchall()
                for row in rows:
                    code = str(row["verification_code"])
                    usable = bool(
                        re.fullmatch(r"[0-9]{6}", code)
                        and hmac.compare_digest(
                            self._registration_code_hash(
                                code, bytes(row["code_salt"])
                            ),
                            bytes(row["code_hash"]),
                        )
                        and int(row["expires_at"]) >= current_time
                    )
                    if not usable and row["verified_at"] is None:
                        code = f"{secrets.randbelow(1_000_000):06d}"
                        code_salt = secrets.token_bytes(REGISTRATION_CODE_BYTES)
                        conn.execute(
                            f"""
                            UPDATE {flow_table}
                            SET code_salt=?,code_hash=?,expires_at=?,attempts=0,
                                updated_at=?
                            WHERE {flow_key}=?
                            """,
                            (
                                code_salt,
                                self._registration_code_hash(code, code_salt),
                                current_time + ttl_seconds,
                                current_time,
                                str(row["flow_id"]),
                            ),
                        )
                    cursor = conn.execute(
                        f"""
                        UPDATE {outbox}
                        SET verification_code=?,status='LOCAL_ONLY',
                            is_simulation=1,claim_token=NULL,last_error=NULL,
                            attempted_at=NULL,sent_at=NULL
                        WHERE email_id=?
                          AND status IN ('SMTP_PENDING','SMTP_FAILED')
                        """,
                        (code, int(row["email_id"])),
                    )
                    localized += cursor.rowcount

            cursor = conn.execute(
                """
                UPDATE email_outbox
                SET status='LOCAL_ONLY',is_simulation=1,claim_token=NULL,
                    last_error=NULL,attempted_at=NULL,sent_at=NULL
                WHERE status IN ('SMTP_PENDING','SMTP_FAILED')
                """
            )
            localized += cursor.rowcount
            conn.commit()
        return localized

    def expire_stale_pending_auth_mail(self) -> int:
        """Fail pending OTP mail that can no longer arrive before code expiry."""

        current_time = int(time.time())
        expired = 0
        targets = (
            (
                "auth_registration_email_outbox",
                "pending_id",
                "auth_registration_flows",
                "pending_id",
            ),
            (
                "auth_password_reset_email_outbox",
                "reset_id",
                "auth_password_reset_flows",
                "reset_id",
            ),
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for outbox, outbox_key, flow_table, flow_key in targets:
                cursor = conn.execute(
                    f"""
                    UPDATE {outbox}
                    SET status='SMTP_FAILED',is_simulation=0,claim_token=NULL,
                        last_error='ExpiredBeforeDelivery',
                        attempted_at=COALESCE(attempted_at,?),
                        verification_code='000000'
                    WHERE status='SMTP_PENDING' AND {outbox_key} IN (
                        SELECT {flow_key} FROM {flow_table} WHERE expires_at < ?
                    )
                    """,
                    (current_time, current_time),
                )
                expired += cursor.rowcount
            conn.commit()
        return expired

    def pending_mail_deliveries(self) -> list[dict[str, Any]]:
        """Build every unclaimed SMTP job for startup replay."""

        with self.connect() as conn:
            registration_sessions = [
                str(row["session_digest"])
                for row in conn.execute(
                    """
                    SELECT f.session_digest
                    FROM auth_registration_email_outbox o
                    JOIN auth_registration_flows f ON f.pending_id=o.pending_id
                    WHERE o.status='SMTP_PENDING' AND o.claim_token IS NULL
                      AND o.delivery_attempts < ?
                    ORDER BY o.email_id
                    """,
                    (MAIL_DELIVERY_MAX_ATTEMPTS,),
                )
            ]
            reset_sessions = [
                str(row["session_digest"])
                for row in conn.execute(
                    """
                    SELECT f.session_digest
                    FROM auth_password_reset_email_outbox o
                    JOIN auth_password_reset_flows f ON f.reset_id=o.reset_id
                    WHERE o.status='SMTP_PENDING' AND o.claim_token IS NULL
                      AND o.delivery_attempts < ?
                    ORDER BY o.email_id
                    """,
                    (MAIL_DELIVERY_MAX_ATTEMPTS,),
                )
            ]
            order_ids = [
                int(row["order_id"])
                for row in conn.execute(
                    """
                    SELECT order_id FROM email_outbox
                    WHERE status='SMTP_PENDING' AND claim_token IS NULL
                      AND delivery_attempts < ?
                    ORDER BY email_id
                    """,
                    (MAIL_DELIVERY_MAX_ATTEMPTS,),
                )
            ]
        deliveries: list[dict[str, Any]] = []
        for session_digest in registration_sessions:
            delivery = self.registration_delivery(session_digest)
            if delivery is not None:
                deliveries.append(delivery)
        for session_digest in reset_sessions:
            delivery = self.password_reset_delivery(session_digest)
            if delivery is not None:
                deliveries.append(delivery)
        for order_id in order_ids:
            delivery = self.order_mail_delivery(order_id)
            if delivery is not None:
                deliveries.append(delivery)
        return deliveries

    def mark_mail_delivery(
        self,
        kind: str,
        email_id: int,
        *,
        claim_token: str,
        sent: bool,
        error_summary: str | None = None,
    ) -> bool:
        """Record SMTP outcome without accepting message bodies or credentials."""

        if isinstance(email_id, bool) or not isinstance(email_id, int) or email_id <= 0:
            raise ContractError("mail delivery id must be a positive integer")
        if (
            not isinstance(claim_token, str)
            or not 20 <= len(claim_token) <= 128
            or any(ord(character) < 33 or ord(character) > 126 for character in claim_token)
        ):
            raise ContractError("mail delivery claim token is invalid")
        safe_error = None
        if not sent:
            safe_error = (error_summary or "SMTPDeliveryError").strip()
            if MAIL_ERROR_SUMMARY_PATTERN.fullmatch(safe_error) is None:
                safe_error = "SMTPDeliveryError"
        table, id_column = self._mail_delivery_target(kind)
        # A failed transport attempt must retain the still-valid OTP so the
        # flow owner can retry the same durable job.  Redact it only after the
        # SMTP service accepted the message.
        redact_secret = (
            ",verification_code='000000'"
            if sent and kind in {"registration", "password-reset"}
            else ""
        )
        completed_at = int(time.time())
        status = MAIL_SMTP_SENT if sent else MAIL_SMTP_FAILED
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE {table}
                SET status=?,is_simulation=0,claim_token=NULL,
                    last_error=?,sent_at=?{redact_secret}
                WHERE {id_column}=? AND status='SMTP_PENDING'
                  AND claim_token=?
                """,
                (
                    status,
                    safe_error,
                    completed_at if sent else None,
                    email_id,
                    claim_token,
                ),
            )
            return cursor.rowcount == 1

    def mail_delivery_health(self) -> dict[str, int]:
        counts = {status: 0 for status in sorted(MAIL_DELIVERY_STATUSES)}
        with self.connect() as conn:
            for table in (
                "auth_registration_email_outbox",
                "auth_password_reset_email_outbox",
                "email_outbox",
            ):
                for row in conn.execute(
                    f"SELECT status,COUNT(*) AS count FROM {table} GROUP BY status"
                ):
                    counts[str(row["status"])] += int(row["count"])
        return counts

    def mail_delivery_outbox(self) -> list[dict[str, Any]]:
        """Return an admin-safe combined view with recipients always masked."""

        messages: list[dict[str, Any]] = []
        for message in self.registration_outbox():
            messages.append({"kind": "registration", **message})
        for message in self.password_reset_outbox():
            messages.append({"kind": "password-reset", **message})
        for message in self.mail_outbox():
            messages.append(
                {
                    "kind": "order-confirmation",
                    "email_id": int(message["email_id"]),
                    "recipient_masked": self._masked_email(
                        str(message["recipient"])
                    ),
                    "template": str(message["template"]),
                    "status": str(message["status"]),
                    "is_simulation": bool(message["is_simulation"]),
                    "delivery_attempts": int(message["delivery_attempts"]),
                    "last_error": message["last_error"],
                    "attempted_at": message["attempted_at"],
                    "sent_at": message["sent_at"],
                    "created_at": message["created_at"],
                    "order_id": int(message["order_id"]),
                }
            )
        return sorted(messages, key=lambda message: (str(message["kind"]), int(message["email_id"])))

    def register_account(
        self,
        session_digest: str,
        email: str,
        display_name: str,
        password: str,
    ) -> bool:
        """Create an account and bind the existing anonymous browser session atomically."""

        self.ensure_session(session_digest)
        email_normalized = normalize_email(email)
        salt, password_hash, scheme = password_record(password)
        try:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """
                    INSERT INTO accounts(
                        email_normalized,display_name,password_salt,password_hash,
                        password_scheme,created_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        email_normalized,
                        display_name,
                        salt,
                        password_hash,
                        scheme,
                        self.now(conn),
                    ),
                )
                account_id = int(cursor.lastrowid)
                self._merge_anonymous_cart_into_account(
                    conn, session_digest, account_id
                )
                self._merge_anonymous_compare_into_account(
                    conn, session_digest, account_id
                )
                conn.execute(
                    "UPDATE browser_sessions SET account_id=? WHERE session_digest=?",
                    (account_id, session_digest),
                )
                conn.execute(
                    "DELETE FROM auth_signin_flows WHERE session_digest=?",
                    (session_digest,),
                )
                conn.execute(
                    "DELETE FROM auth_registration_flows WHERE session_digest=?",
                    (session_digest,),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def authenticate_session(
        self, session_digest: str, password: str
    ) -> tuple[bool, str | None]:
        """Verify a pending sign-in without disclosing whether its account exists."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT f.email_normalized,f.return_to,a.account_id,
                       a.password_salt,a.password_hash,a.password_scheme
                FROM auth_signin_flows f
                LEFT JOIN accounts a ON a.email_normalized=f.email_normalized
                WHERE f.session_digest=?
                """,
                (session_digest,),
            ).fetchone()

        salt = bytes(row["password_salt"]) if row and row["password_salt"] is not None else _DUMMY_SALT
        expected_hash = (
            bytes(row["password_hash"])
            if row and row["password_hash"] is not None
            else _DUMMY_HASH
        )
        scheme_ok = bool(row and row["password_scheme"] == PASSWORD_SCHEME)
        password_ok = verify_password(password, salt, expected_hash)
        if not row or row["account_id"] is None or not scheme_ok or not password_ok:
            return False, None

        account_id = int(row["account_id"])
        return_to = row["return_to"]
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT a.account_id
                FROM auth_signin_flows f
                JOIN accounts a ON a.email_normalized=f.email_normalized
                WHERE f.session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            if current is None or int(current["account_id"]) != account_id:
                conn.rollback()
                return False, None
            self._merge_anonymous_cart_into_account(
                conn, session_digest, account_id
            )
            self._merge_anonymous_compare_into_account(
                conn, session_digest, account_id
            )
            conn.execute(
                "UPDATE browser_sessions SET account_id=? WHERE session_digest=?",
                (account_id, session_digest),
            )
            conn.execute(
                "DELETE FROM auth_signin_flows WHERE session_digest=?",
                (session_digest,),
            )
            conn.commit()
        return True, str(return_to) if return_to else None

    def account_for_session(self, session_digest: str) -> dict[str, Any] | None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.account_id,a.email_normalized,a.display_name,a.created_at
                FROM browser_sessions s
                JOIN accounts a ON a.account_id=s.account_id
                WHERE s.session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            return dict(row) if row else None

    def reviews_for_session(
        self,
        session_digest: str,
        asin: str,
        *,
        star: int | None = None,
        sort: str = "recent",
    ) -> list[dict[str, Any]]:
        """Return local reviews without accepting a client-supplied identity."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            return list_local_reviews(
                conn,
                asin,
                star=star,
                sort=sort,
                viewer_session_digest=session_digest,
            )

    def register_review_product(self, asin: str, source_scope: str) -> str:
        """Register a product identity proven by a server-owned catalog."""

        with self.connect() as conn:
            normalized_asin = register_local_review_product(
                conn, asin=asin, source_scope=source_scope
            )
            conn.commit()
            return normalized_asin

    def upsert_review(
        self,
        session_digest: str,
        asin: str,
        rating: int,
        headline: str,
        body: str,
    ) -> dict[str, Any]:
        """Create/update the account review resolved from ``session_digest``."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            review = upsert_local_review(
                conn,
                session_digest=session_digest,
                asin=asin,
                rating=rating,
                headline=headline,
                body=body,
                at=self.now(conn),
            )
            conn.commit()
            return review

    def toggle_review_helpful(
        self, session_digest: str, asin: str, review_id: int | str
    ) -> dict[str, Any]:
        """Toggle the helpful identity resolved from ``session_digest``."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            review_row = conn.execute(
                "SELECT 1 FROM product_reviews WHERE review_id=? AND asin=?",
                (review_id, asin),
            ).fetchone()
            if review_row is None:
                raise ReviewNotFound("review does not belong to this product")
            result = toggle_local_review_helpful(
                conn,
                session_digest=session_digest,
                review_id=review_id,
                at=self.now(conn),
            )
            conn.commit()
            return result

    def rotate_authenticated_session(
        self, old_session_digest: str, new_session_digest: str
    ) -> None:
        """Move authenticated state and the active cart onto a fresh browser session.

        Navigation history stays attached to the anonymous session that produced it,
        while account access and cart contents are removed from the old identifier.
        """

        if old_session_digest == new_session_digest:
            raise ContractError("session rotation requires a fresh identifier")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old_session = conn.execute(
                """
                SELECT reset_epoch,account_id
                FROM browser_sessions
                WHERE session_digest=?
                """,
                (old_session_digest,),
            ).fetchone()
            if old_session is None or old_session["account_id"] is None:
                conn.rollback()
                raise ContractError("only authenticated sessions can be rotated")
            if conn.execute(
                "SELECT 1 FROM browser_sessions WHERE session_digest=?",
                (new_session_digest,),
            ).fetchone():
                conn.rollback()
                raise ContractError("fresh session identifier already exists")

            account_id = int(old_session["account_id"])
            # A legacy or concurrently prepared guest line must not be stranded
            # when the raw session cookie rotates after authentication.
            self._merge_anonymous_cart_into_account(
                conn, old_session_digest, account_id
            )
            self._merge_anonymous_compare_into_account(
                conn, old_session_digest, account_id
            )
            now = self.now(conn)
            conn.execute(
                """
                INSERT INTO browser_sessions(
                    session_digest,reset_epoch,created_at,account_id
                ) VALUES (?,?,?,?)
                """,
                (
                    new_session_digest,
                    int(old_session["reset_epoch"]),
                    now,
                    account_id,
                ),
            )
            conn.execute(
                "INSERT INTO carts(session_digest,created_at) VALUES (?,?)",
                (new_session_digest, now),
            )
            conn.execute(
                "UPDATE pending_buy_now SET session_digest=? WHERE session_digest=?",
                (new_session_digest, old_session_digest),
            )

            # Helpful votes cast before sign-in belong to the same person as
            # the authenticated account after rotation.  Remove votes that
            # would become self-votes, migrate the remaining guest identity,
            # then discard rows ignored because the account had already voted
            # for the same review in another session.
            conn.execute(
                """
                DELETE FROM review_helpful_votes
                WHERE voter_account_id IS NULL
                  AND voter_session_digest=?
                  AND EXISTS (
                      SELECT 1
                      FROM product_reviews r
                      WHERE r.review_id=review_helpful_votes.review_id
                        AND r.account_id=?
                  )
                """,
                (old_session_digest, account_id),
            )
            conn.execute(
                """
                UPDATE OR IGNORE review_helpful_votes
                SET voter_account_id=?, voter_session_digest=NULL
                WHERE voter_account_id IS NULL AND voter_session_digest=?
                """,
                (account_id, old_session_digest),
            )
            conn.execute(
                """
                DELETE FROM review_helpful_votes
                WHERE voter_account_id IS NULL AND voter_session_digest=?
                """,
                (old_session_digest,),
            )

            conn.execute(
                "UPDATE browser_sessions SET account_id=NULL WHERE session_digest=?",
                (old_session_digest,),
            )
            conn.execute(
                "DELETE FROM auth_signin_flows WHERE session_digest=?",
                (old_session_digest,),
            )
            conn.execute(
                "DELETE FROM auth_registration_flows WHERE session_digest=?",
                (old_session_digest,),
            )
            conn.commit()

    def sign_out(self, session_digest: str) -> None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE browser_sessions SET account_id=NULL WHERE session_digest=?",
                (session_digest,),
            )
            conn.execute(
                "DELETE FROM auth_signin_flows WHERE session_digest=?",
                (session_digest,),
            )
            conn.execute(
                "DELETE FROM auth_registration_flows WHERE session_digest=?",
                (session_digest,),
            )
            conn.commit()

    def compare_eligible_asins(self) -> frozenset[str]:
        """Return purchasable products with source-backed comparison taxonomy."""

        with self.connect() as conn:
            offer_asins = {
                str(row["asin"])
                for row in conn.execute("SELECT asin FROM commerce_offers")
            }
        return frozenset(offer_asins.intersection(self._compare_profiles))

    def compare_profile(self, asin: str) -> dict[str, Any] | None:
        profile = self._compare_profiles.get(asin)
        if profile is None:
            return None
        with self.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM commerce_offers WHERE asin=?", (asin,)
            ).fetchone() is None:
                return None
        return {
            **profile,
            "specs": dict(profile.get("specs") or {}),
        }

    def _compare_owner(
        self, conn: sqlite3.Connection, session_digest: str
    ) -> tuple[str, str, str | int]:
        account_id = self._account_id_for_session(conn, session_digest)
        if account_id is not None:
            return "account_compare_items", "account_id", account_id
        return "compare_items", "session_digest", session_digest

    @staticmethod
    def _require_compare_line_id(value: str) -> str:
        if (
            not isinstance(value, str)
            or COMPARE_LINE_ID_PATTERN.fullmatch(value) is None
        ):
            raise ContractError("compare line identity is invalid")
        return value

    def _unused_compare_line_id(self, conn: sqlite3.Connection) -> str:
        while True:
            candidate = self._new_compare_line_id()
            exists = conn.execute(
                """
                SELECT 1 FROM compare_items WHERE compare_line_id=?
                UNION ALL
                SELECT 1 FROM account_compare_items WHERE compare_line_id=?
                LIMIT 1
                """,
                (candidate, candidate),
            ).fetchone()
            if exists is None:
                return candidate

    def compare_items(self, session_digest: str) -> list[dict[str, Any]]:
        """Resolve each stored selection against the current server quote."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            table, owner_column, owner_id = self._compare_owner(
                conn, session_digest
            )
            rows = conn.execute(
                f"""
                SELECT offer.*,line.compare_line_id,line.selection_json,
                       line.selection_key,line.position,line.created_at
                FROM {table} AS line
                JOIN commerce_offers AS offer ON offer.asin=line.asin
                WHERE line.{owner_column}=?
                ORDER BY line.position
                """,
                (owner_id,),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                asin = str(item["asin"])
                selected_options = self._stored_product_options(
                    asin, item.pop("selection_json", "{}")
                )
                self._apply_transaction_quote(item, selected_options)
                profile = self._compare_profiles.get(asin)
                if profile is not None:
                    # Search-card captures preserve abbreviated aggregate copy
                    # such as ``19.7K`` without inventing an exact integer.  In
                    # that evidence class the legacy zero is only a database
                    # sentinel, never a customer-facing review count.
                    if item.get("evidence_class") == "direct-search-card":
                        reviews_display = profile.get("reviews_display")
                        item["reviews"] = (
                            reviews_display
                            if isinstance(reviews_display, str)
                            and reviews_display
                            else None
                        )
                    item["compare"] = {
                        "category": str(profile["category_label"]),
                        "category_key": str(profile["category_key"]),
                        "family": str(profile["family_label"]),
                        "family_key": str(profile["family_key"]),
                        "selected_options": dict(item["selected_options"]),
                        "specs": dict(profile.get("specs") or {}),
                    }
                item["availability"] = str(item["display_availability"])
                items.append(item)
            return items

    def compare_asins(self, session_digest: str) -> list[str]:
        """Compatibility projection for callers that only need column ASINs."""

        return [str(item["asin"]) for item in self.compare_items(session_digest)]

    def add_compare(
        self,
        session_digest: str,
        asin: str,
        selected_options: Mapping[str, Any] | None = None,
    ) -> str:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            profile = self._compare_profiles.get(asin)
            offer_row = conn.execute(
                "SELECT * FROM commerce_offers WHERE asin=?", (asin,)
            ).fetchone()
            if profile is None or offer_row is None:
                conn.rollback()
                raise ContractError("product is not eligible for comparison")
            quote = self._transaction_quote(
                asin, selected_options, dict(offer_row)
            )
            selection_json = json.dumps(
                quote["selected_options"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            selection_key = str(quote["selection_key"])
            table, owner_column, owner_id = self._compare_owner(
                conn, session_digest
            )
            rows = conn.execute(
                f"""
                SELECT asin,selection_key FROM {table}
                WHERE {owner_column}=?
                ORDER BY position
                """,
                (owner_id,),
            ).fetchall()
            if any(
                str(row["asin"]) == asin
                and str(row["selection_key"]) == selection_key
                for row in rows
            ):
                conn.commit()
                return "duplicate"
            current_families = {
                str(existing_profile["family_key"])
                for row in rows
                if (
                    existing_profile := self._compare_profiles.get(
                        str(row["asin"])
                    )
                )
                is not None
            }
            if any(str(row["asin"]) not in self._compare_profiles for row in rows):
                conn.commit()
                return "incompatible"
            if current_families and current_families != {str(profile["family_key"])}:
                conn.commit()
                return "incompatible"
            if len(rows) >= COMPARE_LIMIT:
                conn.commit()
                return "full"
            conn.execute(
                f"""
                INSERT INTO {table}(
                    compare_line_id,{owner_column},asin,selection_json,
                    selection_key,position,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    self._unused_compare_line_id(conn),
                    owner_id,
                    asin,
                    selection_json,
                    selection_key,
                    len(rows) + 1,
                    self.now(conn),
                ),
            )
            conn.commit()
            return "added"

    def remove_compare(self, session_digest: str, compare_line_id: str) -> bool:
        normalized_line_id = self._require_compare_line_id(compare_line_id)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            table, owner_column, owner_id = self._compare_owner(
                conn, session_digest
            )
            rows = conn.execute(
                f"""
                SELECT compare_line_id,asin,selection_json,selection_key,created_at
                FROM {table}
                WHERE {owner_column}=?
                ORDER BY position
                """,
                (owner_id,),
            ).fetchall()
            if not any(
                str(row["compare_line_id"]) == normalized_line_id for row in rows
            ):
                conn.commit()
                return False
            remaining = [
                row
                for row in rows
                if str(row["compare_line_id"]) != normalized_line_id
            ]
            conn.execute(
                f"DELETE FROM {table} WHERE {owner_column}=?", (owner_id,)
            )
            conn.executemany(
                f"""
                INSERT INTO {table}(
                    compare_line_id,{owner_column},asin,selection_json,
                    selection_key,position,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                [
                    (
                        row["compare_line_id"],
                        owner_id,
                        row["asin"],
                        row["selection_json"],
                        row["selection_key"],
                        position,
                        row["created_at"],
                    )
                    for position, row in enumerate(remaining, start=1)
                ],
            )
            conn.commit()
            return True

    def clear_compare(self, session_digest: str) -> None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            table, owner_column, owner_id = self._compare_owner(
                conn, session_digest
            )
            conn.execute(
                f"DELETE FROM {table} WHERE {owner_column}=?", (owner_id,)
            )

    @staticmethod
    def _quantity(value: int | str) -> int:
        if isinstance(value, bool):
            raise ContractError("cart quantity must be an integer from 1 to 30")
        if isinstance(value, int):
            quantity = value
        elif isinstance(value, str) and value and value.isascii() and value.isdecimal():
            quantity = int(value)
            if str(quantity) != value:
                raise ContractError("cart quantity must use canonical decimal form")
        else:
            raise ContractError("cart quantity must be an integer from 1 to 30")
        if quantity < 1 or quantity > 30:
            raise ContractError("cart quantity must be between 1 and 30")
        return quantity

    def _cart_line_owner(
        self, conn: sqlite3.Connection, session_digest: str
    ) -> tuple[str, str, int]:
        account_id = self._account_id_for_session(conn, session_digest)
        if account_id is not None:
            return "account_cart_lines", "account_id", account_id
        return "cart_lines", "cart_id", self._session_cart_id(conn, session_digest)

    @staticmethod
    def _require_cart_line_id(value: str) -> str:
        if not isinstance(value, str) or CART_LINE_ID_PATTERN.fullmatch(value) is None:
            raise ContractError("cart line identity is invalid")
        return value

    def commerce_offer(self, asin: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM commerce_offers WHERE asin=?", (asin,)
            ).fetchone()
            return dict(row) if row else None

    def product_option_spec(self, asin: str) -> tuple[dict[str, Any], ...]:
        """Return a detached copy of the captured option allow-list for one ASIN."""

        return tuple(
            {
                "label": str(group["label"]),
                "default": str(group["default"]),
                "options": tuple(str(value) for value in group["options"]),
            }
            for group in self._option_specs.get(asin, ())
        )

    def default_product_options(self, asin: str) -> dict[str, str]:
        return default_selection(self._option_specs.get(asin))

    def _complete_product_options(
        self, asin: str, selected_options: Mapping[str, Any] | None
    ) -> dict[str, str]:
        """Normalize a new request while distinguishing omitted and empty maps."""

        try:
            return normalize_complete_selection(
                self._option_specs.get(asin), selected_options
            )
        except ValueError as exc:
            raise ContractError(str(exc)) from exc

    def _transaction_quote(
        self,
        asin: str,
        selected_options: Mapping[str, Any] | None,
        base_offer: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            quote = resolve_transaction_quote(
                asin,
                selected_options,
                option_specs=self._option_specs,
                quote_specs=self._option_quote_specs,
                base_offer=base_offer,
            )
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        if quote is None:
            raise ContractError(UNAVAILABLE_SELECTION_COPY)
        return quote

    def product_option_quotes(self, asin: str) -> list[dict[str, Any]]:
        """Return the server-resolved quote matrix safe to embed in one PDP."""

        offer = self.commerce_offer(asin)
        if offer is None:
            return []
        quotes: list[dict[str, Any]] = []
        for rule in self._option_quote_specs.get(asin, ()):
            selected_options = rule.get("selected_options")
            if not isinstance(selected_options, Mapping):
                raise ContractError("product option quote evidence is corrupted")
            quotes.append(self._transaction_quote(asin, selected_options, offer))
        return quotes

    @staticmethod
    def _selection_payload(raw_value: Any) -> dict[str, str]:
        try:
            payload = json.loads(str(raw_value or "{}"))
        except json.JSONDecodeError as exc:
            raise ContractError("cart option selection is corrupted") from exc
        if not isinstance(payload, dict) or any(
            not isinstance(label, str) or not isinstance(value, str)
            for label, value in payload.items()
        ):
            raise ContractError("cart option selection is corrupted")
        return payload

    def _stored_product_options(self, asin: str, raw_value: Any) -> dict[str, str]:
        """Read a stored selection, backfilling only legacy empty option maps."""

        payload = self._selection_payload(raw_value)
        if not payload and self._option_specs.get(asin):
            return self.default_product_options(asin)
        return self._complete_product_options(asin, payload)

    def _apply_transaction_quote(
        self, payload: dict[str, Any], selected_options: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Overlay a server quote on a commerce-offer/cart payload in place."""

        asin = str(payload.get("asin") or "")
        quote = self._transaction_quote(asin, selected_options, payload)
        payload["selected_options"] = dict(quote["selected_options"])
        payload["price_minor"] = int(quote["price_minor"])
        payload["currency"] = str(quote["currency"])
        payload["image_path"] = str(quote["image_path"])
        payload["display_availability"] = str(quote["display_availability"])
        payload["transaction_target"] = dict(quote["transaction_target"])
        payload["selection_key"] = str(quote["selection_key"])
        payload["transaction_quote"] = quote
        return payload

    def _cart_lines(
        self, session_digest: str, line_state: str
    ) -> list[dict[str, Any]]:
        if line_state not in _CART_STATES:
            raise ContractError("invalid cart line state")
        self.ensure_session(session_digest)
        with self.connect() as conn:
            table, owner_column, owner_id = self._cart_line_owner(
                conn, session_digest
            )
            rows = conn.execute(
                f"""
                SELECT offer.*,line.line_id,line.quantity,line.selection_json,
                       line.selection_key,line.line_state
                FROM {table} AS line
                JOIN commerce_offers AS offer ON offer.asin=line.asin
                WHERE line.{owner_column}=? AND line.line_state=?
                ORDER BY offer.asin,line.selection_key,line.line_id
                """,
                (owner_id, line_state),
            ).fetchall()
            lines: list[dict[str, Any]] = []
            for row in rows:
                line = dict(row)
                selected_options = self._stored_product_options(
                    str(line["asin"]), line.pop("selection_json", "{}")
                )
                lines.append(self._apply_transaction_quote(line, selected_options))
            return lines

    def cart(self, session_digest: str) -> list[dict[str, Any]]:
        return self._cart_lines(session_digest, ACTIVE_CART_STATE)

    def saved_cart(self, session_digest: str) -> list[dict[str, Any]]:
        return self._cart_lines(session_digest, SAVED_CART_STATE)

    def cart_count(self, session_digest: str) -> int:
        return sum(int(line["quantity"]) for line in self.cart(session_digest))

    def add_cart_item(
        self,
        session_digest: str,
        asin: str,
        quantity: int | str,
        selected_options: Mapping[str, Any] | None = None,
    ) -> int:
        requested = self._quantity(quantity)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            offer_row = conn.execute(
                "SELECT * FROM commerce_offers WHERE asin=?", (asin,)
            ).fetchone()
            if offer_row is None:
                conn.rollback()
                raise ContractError("product does not have a current commerce offer")
            quote = self._transaction_quote(asin, selected_options, dict(offer_row))
            selection_json = json.dumps(
                quote["selected_options"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            selection_key = str(quote["selection_key"])
            table, owner_column, owner_id = self._cart_line_owner(
                conn, session_digest
            )
            existing = conn.execute(
                f"SELECT line_id,quantity FROM {table} "
                f"WHERE {owner_column}=? AND asin=? AND selection_key=?",
                (owner_id, asin, selection_key),
            ).fetchone()
            resulting_quantity = (
                min(30, int(existing["quantity"]) + requested)
                if existing
                else requested
            )
            if existing:
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET quantity=?,line_state='ACTIVE'
                    WHERE {owner_column}=? AND line_id=?
                    """,
                    (resulting_quantity, owner_id, str(existing["line_id"])),
                )
            else:
                conn.execute(
                    f"""
                    INSERT INTO {table}(
                        line_id,{owner_column},asin,quantity,selection_json,
                        selection_key,line_state
                    ) VALUES (?,?,?,?,?,?,'ACTIVE')
                    """,
                    (
                        self._new_cart_line_id(),
                        owner_id,
                        asin,
                        resulting_quantity,
                        selection_json,
                        selection_key,
                    ),
                )
            conn.commit()
            return resulting_quantity

    def set_cart_quantity(
        self, session_digest: str, line_id: str, quantity: int | str
    ) -> bool:
        normalized_line_id = self._require_cart_line_id(line_id)
        normalized_quantity = self._quantity(quantity)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            table, owner_column, owner_id = self._cart_line_owner(
                conn, session_digest
            )
            cursor = conn.execute(
                f"""
                UPDATE {table} SET quantity=?
                WHERE {owner_column}=? AND line_id=? AND line_state='ACTIVE'
                """,
                (normalized_quantity, owner_id, normalized_line_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _cancel_empty_cart_checkout(
        conn: sqlite3.Connection, table: str, owner_id: int
    ) -> None:
        """Release checkout-owned state when an account cart has no active lines."""

        if table != "account_cart_lines":
            return
        has_active_line = conn.execute(
            """
            SELECT 1 FROM account_cart_lines
            WHERE account_id=? AND line_state='ACTIVE' LIMIT 1
            """,
            (owner_id,),
        ).fetchone()
        if has_active_line is None:
            # A Buy Now checkout owns checkout_lines and must remain independent
            # from ordinary cart edits.  An empty CART checkout, however, can no
            # longer advance and must release its selected address/payment state.
            conn.execute(
                """
                DELETE FROM checkout_sessions
                WHERE account_id=? AND checkout_mode='CART' AND status<>'PLACED'
                """,
                (owner_id,),
            )

    def delete_cart_item(self, session_digest: str, line_id: str) -> bool:
        normalized_line_id = self._require_cart_line_id(line_id)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            table, owner_column, owner_id = self._cart_line_owner(
                conn, session_digest
            )
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {owner_column}=? AND line_id=?",
                (owner_id, normalized_line_id),
            )
            self._cancel_empty_cart_checkout(conn, table, owner_id)
            conn.commit()
            return cursor.rowcount == 1

    def save_for_later(self, session_digest: str, line_id: str) -> bool:
        normalized_line_id = self._require_cart_line_id(line_id)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            table, owner_column, owner_id = self._cart_line_owner(
                conn, session_digest
            )
            cursor = conn.execute(
                f"""
                UPDATE {table} SET line_state='SAVED'
                WHERE {owner_column}=? AND line_id=? AND line_state='ACTIVE'
                """,
                (owner_id, normalized_line_id),
            )
            self._cancel_empty_cart_checkout(conn, table, owner_id)
            conn.commit()
            return cursor.rowcount == 1

    def move_to_cart(self, session_digest: str, line_id: str) -> bool:
        normalized_line_id = self._require_cart_line_id(line_id)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            table, owner_column, owner_id = self._cart_line_owner(
                conn, session_digest
            )
            cursor = conn.execute(
                f"""
                UPDATE {table} SET line_state='ACTIVE'
                WHERE {owner_column}=? AND line_id=? AND line_state='SAVED'
                """,
                (owner_id, normalized_line_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _require_account_id(
        conn: sqlite3.Connection, session_digest: str
    ) -> int:
        account_id = Store._account_id_for_session(conn, session_digest)
        if account_id is None:
            raise ContractError("checkout requires a signed-in account")
        return account_id

    @staticmethod
    def _validated_address_fields(fields: dict[str, Any]) -> dict[str, str]:
        if not isinstance(fields, dict):
            raise ContractError("delivery address fields must be an object")
        limits = {
            "full_name": (128, True),
            "address_line1": (200, True),
            "address_line2": (200, False),
            "city": (100, True),
            "state_region": (100, True),
            "postal_code": (32, True),
            "country_code": (2, True),
            "phone": (32, False),
        }
        unknown = set(fields) - set(limits)
        if unknown:
            raise ContractError(f"unsupported delivery address fields: {sorted(unknown)}")
        normalized: dict[str, str] = {}
        for name, (limit, required) in limits.items():
            value = fields.get(name, "")
            if not isinstance(value, str):
                raise ContractError(f"delivery address field {name} must be text")
            value = value.strip()
            if required and not value:
                raise ContractError(f"delivery address field {name} is required")
            if len(value) > limit or any(
                ord(character) < 32 or ord(character) == 127 for character in value
            ):
                raise ContractError(f"delivery address field {name} is invalid")
            normalized[name] = value
        country_code = normalized["country_code"].upper()
        if country_code not in SUPPORTED_DELIVERY_COUNTRY_CODES:
            raise ContractError("delivery country is not supported")
        normalized["country_code"] = country_code
        return normalized

    @staticmethod
    def _require_supported_delivery_address(
        address: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        if address is None:
            raise ContractError("checkout delivery address is unavailable")
        country_code = str(address.get("country_code") or "").upper()
        if country_code not in SUPPORTED_DELIVERY_COUNTRY_CODES:
            raise ContractError("checkout delivery country is not supported")
        return address

    @staticmethod
    def _normalized_address_number(value: int | str) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 0 < value <= SQLITE_INTEGER_MAX else None
        if (
            isinstance(value, str)
            and 0 < len(value) <= 19
            and value.isascii()
            and value.isdecimal()
        ):
            try:
                normalized = int(value)
            except ValueError:
                return None
            return (
                normalized
                if str(normalized) == value
                and 0 < normalized <= SQLITE_INTEGER_MAX
                else None
            )
        return None

    @staticmethod
    def _address_row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "address_id": int(row["address_id"]),
            "full_name": str(row["full_name"]),
            "address_line1": str(row["address_line1"]),
            "address_line2": str(row["address_line2"]),
            "city": str(row["city"]),
            "state_region": str(row["state_region"]),
            "postal_code": str(row["postal_code"]),
            "country_code": str(row["country_code"]),
            "phone": str(row["phone"]),
            "is_default": bool(row["is_default"]),
            "revision": int(row["revision"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _active_address_rows(
        conn: sqlite3.Connection, account_id: int
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT address_id,full_name,address_line1,address_line2,city,
                   state_region,postal_code,country_code,phone,is_default,
                   revision,created_at,updated_at
            FROM addresses
            WHERE account_id=? AND is_archived=0
            ORDER BY is_default DESC,address_id
            """,
            (account_id,),
        ).fetchall()

    def addresses_for_session(self, session_digest: str) -> list[dict[str, Any]]:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            account_id = self._require_account_id(conn, session_digest)
            return [
                self._address_row_payload(row)
                for row in self._active_address_rows(conn, account_id)
            ]

    def address_for_session(
        self, session_digest: str, address_id: int | str
    ) -> dict[str, Any] | None:
        normalized_id = self._normalized_address_number(address_id)
        if normalized_id is None:
            return None
        self.ensure_session(session_digest)
        with self.connect() as conn:
            account_id = self._require_account_id(conn, session_digest)
            row = conn.execute(
                """
                SELECT address_id,full_name,address_line1,address_line2,city,
                       state_region,postal_code,country_code,phone,is_default,
                       revision,created_at,updated_at
                FROM addresses
                WHERE address_id=? AND account_id=? AND is_archived=0
                """,
                (normalized_id, account_id),
            ).fetchone()
            return self._address_row_payload(row) if row else None

    @staticmethod
    def _validate_make_default(value: bool) -> bool:
        if not isinstance(value, bool):
            raise ContractError("make_default must be a boolean")
        return value

    def _insert_account_address(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        address: dict[str, str],
        *,
        make_default: bool,
    ) -> sqlite3.Row:
        now = self.now(conn)
        has_active = bool(
            conn.execute(
                "SELECT 1 FROM addresses WHERE account_id=? AND is_archived=0 LIMIT 1",
                (account_id,),
            ).fetchone()
        )
        should_default = make_default or not has_active
        if should_default:
            conn.execute(
                """
                UPDATE addresses SET is_default=0,revision=revision+1,updated_at=?
                WHERE account_id=? AND is_archived=0 AND is_default=1
                """,
                (now, account_id),
            )
        cursor = conn.execute(
            """
            INSERT INTO addresses(
                account_id,full_name,address_line1,address_line2,city,
                state_region,postal_code,country_code,phone,is_default,
                is_archived,revision,created_at,updated_at
            ) VALUES (
                :account_id,:full_name,:address_line1,:address_line2,:city,
                :state_region,:postal_code,:country_code,:phone,:is_default,
                0,1,:created_at,:updated_at
            )
            """,
            {
                **address,
                "account_id": account_id,
                "is_default": 1 if should_default else 0,
                "created_at": now,
                "updated_at": now,
            },
        )
        row = conn.execute(
            """
            SELECT address_id,full_name,address_line1,address_line2,city,
                   state_region,postal_code,country_code,phone,is_default,
                   revision,created_at,updated_at
            FROM addresses WHERE address_id=?
            """,
            (int(cursor.lastrowid),),
        ).fetchone()
        assert row is not None
        return row

    def create_address(
        self,
        session_digest: str,
        fields: dict[str, Any],
        *,
        make_default: bool = False,
    ) -> dict[str, Any]:
        address = self._validated_address_fields(fields)
        make_default = self._validate_make_default(make_default)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            row = self._insert_account_address(
                conn, account_id, address, make_default=make_default
            )
            payload = self._address_row_payload(row)
            conn.commit()
            return payload

    def _owned_active_address(
        self, conn: sqlite3.Connection, account_id: int, address_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM addresses WHERE address_id=? AND account_id=? AND is_archived=0",
            (address_id, account_id),
        ).fetchone()
        if row is None:
            raise AddressNotFound("address is unavailable")
        return row

    @staticmethod
    def _require_address_revision(row: sqlite3.Row, revision: int) -> None:
        if int(row["revision"]) != revision:
            raise AddressRevisionConflict("address was changed in another request")

    def _invalidate_checkout_after_address_edit(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        address_id: int,
        now: str,
    ) -> None:
        checkout = conn.execute(
            """
            SELECT checkout_id FROM checkout_sessions
            WHERE account_id=? AND address_id=? AND status<>'PLACED'
            """,
            (account_id, address_id),
        ).fetchone()
        if checkout is None:
            return
        checkout_id = int(checkout["checkout_id"])
        self._supersede_checkout_payments(conn, checkout_id)
        conn.execute(
            """
            UPDATE checkout_sessions SET
                status='ADDRESS_SELECTED',delivery_method=NULL,
                shipping_minor=NULL,updated_at=?
            WHERE checkout_id=?
            """,
            (now, checkout_id),
        )

    def update_address(
        self,
        session_digest: str,
        address_id: int | str,
        revision: int | str,
        fields: dict[str, Any],
        *,
        make_default: bool = False,
    ) -> dict[str, Any]:
        normalized_id = self._normalized_address_number(address_id)
        normalized_revision = self._normalized_address_number(revision)
        if normalized_id is None:
            raise AddressNotFound("address is unavailable")
        if normalized_revision is None:
            raise AddressRevisionConflict("address revision is invalid")
        address = self._validated_address_fields(fields)
        make_default = self._validate_make_default(make_default)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            current = self._owned_active_address(conn, account_id, normalized_id)
            self._require_address_revision(current, normalized_revision)
            now = self.now(conn)
            conn.execute(
                """
                UPDATE addresses SET
                    full_name=:full_name,address_line1=:address_line1,
                    address_line2=:address_line2,city=:city,
                    state_region=:state_region,postal_code=:postal_code,
                    country_code=:country_code,phone=:phone,
                    revision=revision+1,updated_at=:updated_at
                WHERE address_id=:address_id AND account_id=:account_id
                  AND is_archived=0 AND revision=:revision
                """,
                {
                    **address,
                    "address_id": normalized_id,
                    "account_id": account_id,
                    "revision": normalized_revision,
                    "updated_at": now,
                },
            )
            if make_default and not bool(current["is_default"]):
                conn.execute(
                    """
                    UPDATE addresses SET is_default=0,revision=revision+1,updated_at=?
                    WHERE account_id=? AND is_archived=0 AND is_default=1
                    """,
                    (now, account_id),
                )
                conn.execute(
                    "UPDATE addresses SET is_default=1 WHERE address_id=?",
                    (normalized_id,),
                )
            self._invalidate_checkout_after_address_edit(
                conn, account_id, normalized_id, now
            )
            updated = self._owned_active_address(conn, account_id, normalized_id)
            payload = self._address_row_payload(updated)
            conn.commit()
            return payload

    def set_default_address(
        self,
        session_digest: str,
        address_id: int | str,
        revision: int | str,
    ) -> dict[str, Any]:
        normalized_id = self._normalized_address_number(address_id)
        normalized_revision = self._normalized_address_number(revision)
        if normalized_id is None:
            raise AddressNotFound("address is unavailable")
        if normalized_revision is None:
            raise AddressRevisionConflict("address revision is invalid")
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            target = self._owned_active_address(conn, account_id, normalized_id)
            self._require_address_revision(target, normalized_revision)
            if not bool(target["is_default"]):
                now = self.now(conn)
                conn.execute(
                    """
                    UPDATE addresses SET is_default=0,revision=revision+1,updated_at=?
                    WHERE account_id=? AND is_archived=0 AND is_default=1
                    """,
                    (now, account_id),
                )
                conn.execute(
                    """
                    UPDATE addresses SET is_default=1,revision=revision+1,updated_at=?
                    WHERE address_id=? AND account_id=? AND is_archived=0
                    """,
                    (now, normalized_id, account_id),
                )
            updated = self._owned_active_address(conn, account_id, normalized_id)
            payload = self._address_row_payload(updated)
            conn.commit()
            return payload

    def delete_address(
        self,
        session_digest: str,
        address_id: int | str,
        revision: int | str,
    ) -> None:
        normalized_id = self._normalized_address_number(address_id)
        normalized_revision = self._normalized_address_number(revision)
        if normalized_id is None:
            raise AddressNotFound("address is unavailable")
        if normalized_revision is None:
            raise AddressRevisionConflict("address revision is invalid")
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            target = self._owned_active_address(conn, account_id, normalized_id)
            self._require_address_revision(target, normalized_revision)
            if conn.execute(
                """
                SELECT 1 FROM checkout_sessions
                WHERE account_id=? AND address_id=? AND status<>'PLACED'
                """,
                (account_id, normalized_id),
            ).fetchone():
                raise AddressInUse("address is selected by an active checkout")
            now = self.now(conn)
            conn.execute(
                """
                UPDATE addresses SET is_default=0,is_archived=1,
                    revision=revision+1,updated_at=?
                WHERE address_id=? AND account_id=? AND is_archived=0
                """,
                (now, normalized_id, account_id),
            )
            if bool(target["is_default"]):
                replacement = conn.execute(
                    """
                    SELECT address_id FROM addresses
                    WHERE account_id=? AND is_archived=0
                    ORDER BY address_id LIMIT 1
                    """,
                    (account_id,),
                ).fetchone()
                if replacement is not None:
                    conn.execute(
                        """
                        UPDATE addresses SET is_default=1,revision=revision+1,updated_at=?
                        WHERE address_id=?
                        """,
                        (now, int(replacement["address_id"])),
                    )
            conn.commit()

    def _account_cart_snapshot(
        self, conn: sqlite3.Connection, account_id: int
    ) -> tuple[list[dict[str, Any]], int, str, str]:
        rows = conn.execute(
            """
            SELECT offer.*,line.line_id,line.quantity,line.selection_json,
                   line.selection_key
            FROM account_cart_lines AS line
            JOIN commerce_offers AS offer ON offer.asin=line.asin
            WHERE line.account_id=? AND line.line_state='ACTIVE'
            ORDER BY offer.asin,line.selection_key,line.line_id
            """,
            (account_id,),
        ).fetchall()
        return self._quoted_checkout_snapshot(rows)

    def _buy_now_checkout_snapshot(
        self, conn: sqlite3.Connection, checkout_id: int
    ) -> tuple[list[dict[str, Any]], int, str, str]:
        rows = conn.execute(
            """
            SELECT offer.*,line.quantity,line.selection_json
            FROM checkout_lines AS line
            JOIN commerce_offers AS offer ON offer.asin=line.asin
            WHERE line.checkout_id=?
            ORDER BY line.ordinal
            """,
            (checkout_id,),
        ).fetchall()
        return self._quoted_checkout_snapshot(rows)

    def _quoted_checkout_snapshot(
        self, rows: list[sqlite3.Row]
    ) -> tuple[list[dict[str, Any]], int, str, str]:
        """Resolve current source-backed quotes for a cart or Buy Now line set."""

        items: list[dict[str, Any]] = []
        currencies: set[str] = set()
        fingerprint_items: list[dict[str, Any]] = []
        subtotal_minor = 0
        for row in rows:
            item = dict(row)
            selected_options = self._stored_product_options(
                str(item["asin"]), item.pop("selection_json", "{}")
            )
            self._apply_transaction_quote(item, selected_options)
            quantity = int(item["quantity"])
            price_minor = int(item["price_minor"])
            line_total_minor = price_minor * quantity
            item["line_total_minor"] = line_total_minor
            items.append(item)
            subtotal_minor += line_total_minor
            currency = str(item["currency"])
            currencies.add(currency)
            fingerprint_items.append(
                {
                    "asin": item["asin"],
                    "currency": currency,
                    "price_minor": price_minor,
                    "quantity": quantity,
                    "selection_key": item["selection_key"],
                    "selected_options": item["selected_options"],
                }
            )
        if len(currencies) > 1:
            raise ContractError("checkout cannot mix currencies")
        currency = next(iter(currencies), "USD")
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_items,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        return items, subtotal_minor, currency, fingerprint

    def _checkout_snapshot(
        self,
        conn: sqlite3.Connection,
        checkout_row: sqlite3.Row,
        account_id: int,
    ) -> tuple[list[dict[str, Any]], int, str, str]:
        if str(checkout_row["checkout_mode"]) == CHECKOUT_MODE_BUY_NOW:
            return self._buy_now_checkout_snapshot(
                conn, int(checkout_row["checkout_id"])
            )
        return self._account_cart_snapshot(conn, account_id)

    @staticmethod
    def _open_checkout_row(
        conn: sqlite3.Connection, account_id: int
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM checkout_sessions
            WHERE account_id=? AND status<>'PLACED'
            ORDER BY checkout_id DESC LIMIT 1
            """,
            (account_id,),
        ).fetchone()

    @staticmethod
    def _latest_checkout_row(
        conn: sqlite3.Connection, account_id: int
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM checkout_sessions
            WHERE account_id=?
            ORDER BY (status<>'PLACED') DESC,checkout_id DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()

    @staticmethod
    def _supersede_checkout_payments(
        conn: sqlite3.Connection, checkout_id: int
    ) -> None:
        conn.execute(
            """
            UPDATE payment_attempts SET status='SUPERSEDED'
            WHERE checkout_id=? AND status='APPROVED'
            """,
            (checkout_id,),
        )

    @classmethod
    def _address_payload(
        cls, conn: sqlite3.Connection, address_id: int | None, account_id: int
    ) -> dict[str, Any] | None:
        if address_id is None:
            return None
        row = conn.execute(
            """
            SELECT address_id,full_name,address_line1,address_line2,city,
                   state_region,postal_code,country_code,phone,is_default,
                   revision,created_at,updated_at
            FROM addresses
            WHERE address_id=? AND account_id=?
            """,
            (address_id, account_id),
        ).fetchone()
        if row is None:
            raise ContractError("checkout delivery address is unavailable")
        return cls._address_row_payload(row)

    @staticmethod
    def _approved_payment_payload(
        conn: sqlite3.Connection, checkout_id: int
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT payment_attempt_id,method,status,amount_minor,currency,
                   cart_fingerprint,is_simulation,created_at
            FROM payment_attempts
            WHERE checkout_id=? AND status='APPROVED'
            ORDER BY payment_attempt_id DESC LIMIT 1
            """,
            (checkout_id,),
        ).fetchone()
        if row is None:
            return None
        payment = dict(row)
        payment["is_simulation"] = bool(payment["is_simulation"])
        payment["method_label"] = payment_method_label(str(payment["method"]))
        payment["simulation_notice"] = SIMULATION_NOTICE
        return payment

    def _checkout_payload(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        account_id: int,
    ) -> dict[str, Any]:
        if row["status"] == "PLACED":
            order = conn.execute(
                "SELECT * FROM orders WHERE checkout_id=? AND account_id=?",
                (int(row["checkout_id"]), account_id),
            ).fetchone()
            if order is None:
                raise ContractError("placed checkout has no order")
            payload = self._order_payload(conn, order)
            payload["checkout_id"] = int(row["checkout_id"])
            return payload

        items, subtotal_minor, currency, fingerprint = self._checkout_snapshot(
            conn, row, account_id
        )
        shipping_minor = (
            int(row["shipping_minor"]) if row["shipping_minor"] is not None else 0
        )
        payment = self._approved_payment_payload(conn, int(row["checkout_id"]))
        if payment is not None:
            payment["cart_matches"] = bool(
                payment["cart_fingerprint"] == fingerprint
                and payment["currency"] == currency
                and int(payment["amount_minor"]) == subtotal_minor + shipping_minor
            )
        return {
            "checkout_id": int(row["checkout_id"]),
            "idempotency_key": row["idempotency_key"],
            "checkout_mode": str(row["checkout_mode"]),
            "status": row["status"],
            "items": items,
            "address": self._address_payload(
                conn,
                int(row["address_id"]) if row["address_id"] is not None else None,
                account_id,
            ),
            "saved_addresses": [
                self._address_row_payload(address_row)
                for address_row in self._active_address_rows(conn, account_id)
                if str(address_row["country_code"])
                in SUPPORTED_DELIVERY_COUNTRY_CODES
            ],
            "delivery_method": row["delivery_method"],
            "items_subtotal_minor": subtotal_minor,
            "shipping_minor": shipping_minor,
            "total_minor": subtotal_minor + shipping_minor,
            "currency": currency,
            "payment": payment,
            "is_simulation": True,
            "simulation_notice": SIMULATION_NOTICE,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _reconcile_open_checkout(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        account_id: int,
    ) -> tuple[sqlite3.Row, str | None]:
        """Demote an open checkout when address or approved totals are stale."""

        checkout_id = int(row["checkout_id"])
        now = self.now(conn)
        if row["address_id"] is not None:
            address = self._address_payload(
                conn, int(row["address_id"]), account_id
            )
            if (
                address is None
                or str(address.get("country_code") or "").upper()
                not in SUPPORTED_DELIVERY_COUNTRY_CODES
            ):
                self._supersede_checkout_payments(conn, checkout_id)
                conn.execute(
                    """
                    UPDATE checkout_sessions SET
                        status='CART_READY',address_id=NULL,
                        delivery_method=NULL,shipping_minor=NULL,updated_at=?
                    WHERE checkout_id=?
                    """,
                    (now, checkout_id),
                )
                updated = self._open_checkout_row(conn, account_id)
                assert updated is not None
                return updated, "unsupported-delivery-country"

        if row["status"] == "PAYMENT_SELECTED":
            _, subtotal_minor, currency, fingerprint = self._checkout_snapshot(
                conn, row, account_id
            )
            payment = self._approved_payment_payload(conn, checkout_id)
            shipping_minor = int(row["shipping_minor"] or 0)
            payment_current = bool(
                payment
                and payment["cart_fingerprint"] == fingerprint
                and payment["currency"] == currency
                and int(payment["amount_minor"])
                == subtotal_minor + shipping_minor
            )
            if not payment_current:
                self._supersede_checkout_payments(conn, checkout_id)
                conn.execute(
                    """
                    UPDATE checkout_sessions SET
                        status='DELIVERY_SELECTED',currency=?,updated_at=?
                    WHERE checkout_id=?
                    """,
                    (currency, now, checkout_id),
                )
                updated = self._open_checkout_row(conn, account_id)
                assert updated is not None
                return updated, "cart-changed"
        return row, None

    def _replace_with_buy_now_checkout(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        asin: str,
        quantity: int,
        selected_options: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        offer_row = conn.execute(
            "SELECT * FROM commerce_offers WHERE asin=?", (asin,)
        ).fetchone()
        if offer_row is None:
            raise ContractError("product does not have a current commerce offer")
        quote = self._transaction_quote(asin, selected_options, dict(offer_row))
        selection_json = json.dumps(
            quote["selected_options"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            "DELETE FROM checkout_sessions WHERE account_id=? AND status<>'PLACED'",
            (account_id,),
        )
        now = self.now(conn)
        cursor = conn.execute(
            """
            INSERT INTO checkout_sessions(
                account_id,idempotency_key,checkout_mode,status,currency,
                created_at,updated_at
            ) VALUES (?,?,'BUY_NOW','CART_READY',?,?,?)
            """,
            (
                account_id,
                secrets.token_urlsafe(24),
                str(quote["currency"]),
                now,
                now,
            ),
        )
        checkout_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO checkout_lines(
                checkout_id,ordinal,asin,quantity,selection_json
            ) VALUES (?,1,?,?,?)
            """,
            (checkout_id, asin, quantity, selection_json),
        )
        row = self._open_checkout_row(conn, account_id)
        assert row is not None
        return self._checkout_payload(conn, row, account_id)

    def begin_buy_now(
        self,
        session_digest: str,
        asin: str,
        quantity: int | str,
        selected_options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start an isolated Buy Now checkout or persist it across authentication."""

        requested = self._quantity(quantity)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            offer_row = conn.execute(
                "SELECT * FROM commerce_offers WHERE asin=?", (asin,)
            ).fetchone()
            if offer_row is None:
                raise ContractError("product does not have a current commerce offer")
            quote = self._transaction_quote(
                asin, selected_options, dict(offer_row)
            )
            normalized_options = dict(quote["selected_options"])
            account_id = self._account_id_for_session(conn, session_digest)
            if account_id is None:
                conn.execute(
                    """
                    INSERT INTO pending_buy_now(
                        session_digest,asin,quantity,selection_json,created_at
                    ) VALUES (?,?,?,?,?)
                    ON CONFLICT(session_digest) DO UPDATE SET
                        asin=excluded.asin,
                        quantity=excluded.quantity,
                        selection_json=excluded.selection_json,
                        created_at=excluded.created_at
                    """,
                    (
                        session_digest,
                        asin,
                        requested,
                        json.dumps(
                            normalized_options,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        self.now(conn),
                    ),
                )
                conn.commit()
                return {
                    "requires_authentication": True,
                    "asin": asin,
                    "quantity": requested,
                    "selected_options": normalized_options,
                }

            payload = self._replace_with_buy_now_checkout(
                conn,
                account_id,
                asin,
                requested,
                normalized_options,
            )
            conn.execute(
                "DELETE FROM pending_buy_now WHERE session_digest=?",
                (session_digest,),
            )
            conn.commit()
            payload["requires_authentication"] = False
            return payload

    def resume_buy_now(self, session_digest: str) -> dict[str, Any] | None:
        """Consume the authenticated session's one pending Buy Now intent."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            pending = conn.execute(
                """
                SELECT asin,quantity,selection_json
                FROM pending_buy_now WHERE session_digest=?
                """,
                (session_digest,),
            ).fetchone()
            if pending is None:
                conn.commit()
                return None
            selected_options = self._selection_payload(pending["selection_json"])
            payload = self._replace_with_buy_now_checkout(
                conn,
                account_id,
                str(pending["asin"]),
                int(pending["quantity"]),
                selected_options,
            )
            conn.execute(
                "DELETE FROM pending_buy_now WHERE session_digest=?",
                (session_digest,),
            )
            conn.commit()
            payload["requires_authentication"] = False
            return payload

    def start_checkout(self, session_digest: str) -> dict[str, Any]:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            items, _, currency, _ = self._account_cart_snapshot(
                conn, account_id
            )
            if not items:
                raise ContractError("checkout requires at least one active cart item")
            row = self._open_checkout_row(conn, account_id)
            now = self.now(conn)
            reconciliation_reason: str | None = None
            if row is not None and str(row["checkout_mode"]) != CHECKOUT_MODE_CART:
                conn.execute(
                    "DELETE FROM checkout_sessions WHERE checkout_id=?",
                    (int(row["checkout_id"]),),
                )
                row = None
            if row is None:
                conn.execute(
                    """
                    INSERT INTO checkout_sessions(
                        account_id,idempotency_key,checkout_mode,status,currency,
                        created_at,updated_at
                    ) VALUES (?,?,'CART','CART_READY',?,?,?)
                    """,
                    (
                        account_id,
                        secrets.token_urlsafe(24),
                        currency,
                        now,
                        now,
                    ),
                )
                row = self._open_checkout_row(conn, account_id)
            elif row["currency"] != currency:
                self._supersede_checkout_payments(conn, int(row["checkout_id"]))
                conn.execute(
                    """
                    UPDATE checkout_sessions SET
                        status='CART_READY',address_id=NULL,delivery_method=NULL,
                        shipping_minor=NULL,currency=?,updated_at=?
                    WHERE checkout_id=?
                    """,
                    (currency, now, int(row["checkout_id"])),
                )
                row = self._open_checkout_row(conn, account_id)
                reconciliation_reason = "cart-changed"
            assert row is not None
            row, stale_reason = self._reconcile_open_checkout(
                conn, row, account_id
            )
            reconciliation_reason = stale_reason or reconciliation_reason
            payload = self._checkout_payload(conn, row, account_id)
            if reconciliation_reason is not None:
                payload["reconciliation_reason"] = reconciliation_reason
            conn.commit()
            return payload

    def checkout(self, session_digest: str) -> dict[str, Any] | None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            account_id = self._require_account_id(conn, session_digest)
            row = self._latest_checkout_row(conn, account_id)
            return self._checkout_payload(conn, row, account_id) if row else None

    def reconcile_checkout(
        self, session_digest: str
    ) -> dict[str, Any] | None:
        """Return the latest checkout after atomically invalidating stale state."""

        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            row = self._latest_checkout_row(conn, account_id)
            if row is None:
                conn.commit()
                return None
            reason: str | None = None
            if row["status"] != "PLACED":
                row, reason = self._reconcile_open_checkout(
                    conn, row, account_id
                )
            payload = self._checkout_payload(conn, row, account_id)
            if reason is not None:
                payload["reconciliation_reason"] = reason
            conn.commit()
            return payload

    def _apply_checkout_address(
        self,
        conn: sqlite3.Connection,
        checkout_row: sqlite3.Row,
        account_id: int,
        address_id: int,
        currency: str,
    ) -> dict[str, Any]:
        now = self.now(conn)
        self._supersede_checkout_payments(conn, int(checkout_row["checkout_id"]))
        conn.execute(
            """
            UPDATE checkout_sessions SET
                status='ADDRESS_SELECTED',address_id=?,delivery_method=NULL,
                shipping_minor=NULL,currency=?,updated_at=?
            WHERE checkout_id=?
            """,
            (
                address_id,
                currency,
                now,
                int(checkout_row["checkout_id"]),
            ),
        )
        updated = self._open_checkout_row(conn, account_id)
        assert updated is not None
        return self._checkout_payload(conn, updated, account_id)

    def save_checkout_address(
        self,
        session_digest: str,
        fields: dict[str, Any],
        *,
        make_default: bool = False,
    ) -> dict[str, Any]:
        address = self._validated_address_fields(fields)
        make_default = self._validate_make_default(make_default)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            row = self._open_checkout_row(conn, account_id)
            if row is None:
                raise ContractError("start checkout before selecting an address")
            items, _, currency, _ = self._checkout_snapshot(conn, row, account_id)
            if not items:
                raise ContractError("checkout requires at least one active cart item")
            address_row = self._insert_account_address(
                conn,
                account_id,
                address,
                make_default=make_default,
            )
            payload = self._apply_checkout_address(
                conn,
                row,
                account_id,
                int(address_row["address_id"]),
                currency,
            )
            conn.commit()
            return payload

    def select_checkout_address(
        self,
        session_digest: str,
        address_id: int | str,
        revision: int | str,
    ) -> dict[str, Any]:
        normalized_id = self._normalized_address_number(address_id)
        normalized_revision = self._normalized_address_number(revision)
        if normalized_id is None:
            raise AddressNotFound("address is unavailable")
        if normalized_revision is None:
            raise AddressRevisionConflict("address revision is invalid")
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            checkout_row = self._open_checkout_row(conn, account_id)
            if checkout_row is None:
                raise ContractError("start checkout before selecting an address")
            items, _, currency, _ = self._checkout_snapshot(
                conn, checkout_row, account_id
            )
            if not items:
                raise ContractError("checkout requires at least one active cart item")
            address_row = self._owned_active_address(
                conn, account_id, normalized_id
            )
            self._require_address_revision(address_row, normalized_revision)
            self._require_supported_delivery_address(dict(address_row))
            payload = self._apply_checkout_address(
                conn, checkout_row, account_id, normalized_id, currency
            )
            conn.commit()
            return payload

    def select_delivery(
        self, session_digest: str, delivery_method: str
    ) -> dict[str, Any]:
        if delivery_method not in DELIVERY_SHIPPING_MINOR:
            raise ContractError("delivery method must be standard or expedited")
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            row = self._open_checkout_row(conn, account_id)
            if row is None or row["status"] not in {
                "ADDRESS_SELECTED",
                "DELIVERY_SELECTED",
                "PAYMENT_SELECTED",
            }:
                raise ContractError("select an address before choosing delivery")
            items, _, currency, _ = self._checkout_snapshot(conn, row, account_id)
            if not items:
                raise ContractError("checkout requires at least one active cart item")
            address = self._address_payload(
                conn, int(row["address_id"]), account_id
            )
            self._require_supported_delivery_address(address)
            self._supersede_checkout_payments(conn, int(row["checkout_id"]))
            now = self.now(conn)
            conn.execute(
                """
                UPDATE checkout_sessions SET
                    status='DELIVERY_SELECTED',delivery_method=?,shipping_minor=?,
                    currency=?,updated_at=?
                WHERE checkout_id=?
                """,
                (
                    delivery_method,
                    DELIVERY_SHIPPING_MINOR[delivery_method],
                    currency,
                    now,
                    int(row["checkout_id"]),
                ),
            )
            updated = self._open_checkout_row(conn, account_id)
            assert updated is not None
            payload = self._checkout_payload(conn, updated, account_id)
            conn.commit()
            return payload

    def select_test_payment(
        self, session_digest: str, method: str
    ) -> dict[str, Any]:
        try:
            scenario = payment_method(method)
        except ValueError as exc:
            raise ContractError("unsupported sandbox payment method") from exc
        payment_status = scenario.outcome
        decline_code = scenario.decline_code
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            row = self._open_checkout_row(conn, account_id)
            if row is None or row["status"] not in {
                "DELIVERY_SELECTED",
                "PAYMENT_SELECTED",
            }:
                raise ContractError("select delivery before choosing payment")
            items, subtotal_minor, currency, fingerprint = self._checkout_snapshot(
                conn, row, account_id
            )
            if not items:
                raise ContractError("checkout requires at least one active cart item")
            address = self._address_payload(
                conn, int(row["address_id"]), account_id
            )
            self._require_supported_delivery_address(address)
            shipping_minor = int(row["shipping_minor"])
            self._supersede_checkout_payments(conn, int(row["checkout_id"]))
            now = self.now(conn)
            cursor = conn.execute(
                """
                INSERT INTO payment_attempts(
                    checkout_id,account_id,method,status,amount_minor,currency,
                    cart_fingerprint,is_simulation,created_at
                ) VALUES (?,?,?,?,?,?,?,1,?)
                """,
                (
                    int(row["checkout_id"]),
                    account_id,
                    method,
                    payment_status,
                    subtotal_minor + shipping_minor,
                    currency,
                    fingerprint,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE checkout_sessions SET
                    status=?,currency=?,updated_at=?
                WHERE checkout_id=?
                """,
                (
                    "PAYMENT_SELECTED"
                    if payment_status == PAYMENT_APPROVED
                    else "DELIVERY_SELECTED",
                    currency,
                    now,
                    int(row["checkout_id"]),
                ),
            )
            updated = self._open_checkout_row(conn, account_id)
            assert updated is not None
            payload = self._checkout_payload(conn, updated, account_id)
            payload["payment_attempt"] = {
                "payment_attempt_id": int(cursor.lastrowid),
                "method": method,
                "method_label": payment_method_label(method),
                "status": payment_status,
                "decline_code": decline_code,
                "is_simulation": True,
            }
            conn.commit()
            return payload

    @staticmethod
    def _validated_idempotency_key(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or value.strip() != value
            or any(ord(character) < 33 or ord(character) == 127 for character in value)
        ):
            raise ContractError("idempotency key must be 1 to 128 printable characters")
        return value

    @staticmethod
    def _validated_action_idempotency_key(value: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{20,128}", value) is None
        ):
            raise ContractError(
                "order action idempotency key must use 20 to 128 safe characters"
            )
        return value

    @staticmethod
    def _order_action_token(
        session_digest: str, action: str, order_id: int, *, purpose: str = "csrf"
    ) -> str:
        if action not in {"cancel", "return"}:
            raise ContractError("unsupported order action token")
        message = f"{purpose}:{session_digest}:{action}:{order_id}".encode("utf-8")
        return hmac.new(ORDER_ACTION_HMAC_KEY, message, hashlib.sha256).hexdigest()

    @classmethod
    def _attach_order_action_tokens(
        cls, payload: dict[str, Any], session_digest: str
    ) -> dict[str, Any]:
        order_id = int(payload["order_id"])
        payload["action_tokens"] = {
            action: cls._order_action_token(session_digest, action, order_id)
            for action in ("cancel", "return")
        }
        payload["action_idempotency_keys"] = {
            action: cls._order_action_token(
                session_digest, action, order_id, purpose="idempotency"
            )
            for action in ("cancel", "return")
        }
        return payload

    @classmethod
    def _require_order_action_token(
        cls,
        session_digest: str,
        action: str,
        order_id: int,
        candidate: str,
    ) -> None:
        expected = cls._order_action_token(session_digest, action, order_id)
        if not isinstance(candidate, str) or not hmac.compare_digest(
            candidate, expected
        ):
            raise OrderActionTokenInvalid("order action token is invalid")

    def _order_payload(
        self, conn: sqlite3.Connection, order_row: sqlite3.Row
    ) -> dict[str, Any]:
        order_id = int(order_row["order_id"])
        checkout_row = conn.execute(
            "SELECT checkout_mode FROM checkout_sessions WHERE checkout_id=?",
            (int(order_row["checkout_id"]),),
        ).fetchone()
        if checkout_row is None:
            raise ContractError("order checkout record is unavailable")
        items = [
            dict(row)
            for row in conn.execute(
                """
                SELECT order_item_id,asin,title,image_path,quantity,selection_json,
                       unit_price_minor AS price_minor,line_total_minor,currency
                FROM order_items
                WHERE order_id=?
                ORDER BY ordinal
                """,
                (order_id,),
            )
        ]
        for item in items:
            item["selected_options"] = self._stored_product_options(
                str(item["asin"]), item.pop("selection_json", "{}")
            )
        payment_row = conn.execute(
            """
            SELECT payment_attempt_id,method,status,amount_minor,currency,
                   is_simulation,created_at
            FROM payment_attempts
            WHERE payment_attempt_id=?
            """,
            (int(order_row["payment_attempt_id"]),),
        ).fetchone()
        if payment_row is None:
            raise ContractError("order payment record is unavailable")
        payment = dict(payment_row)
        payment["is_simulation"] = bool(payment["is_simulation"])
        payment["method_label"] = payment_method_label(str(payment["method"]))
        payment["simulation_notice"] = SIMULATION_NOTICE

        shipment_row = conn.execute(
            """
            SELECT shipment_id,status,lifecycle_status,revision,delivery_method,
                   shipping_minor,carrier,tracking_code,is_simulation,created_at,
                   updated_at,shipped_at,delivered_at,cancelled_at
            FROM shipments WHERE order_id=?
            """,
            (order_id,),
        ).fetchone()
        if shipment_row is None:
            raise ContractError("order shipment record is unavailable")
        shipment = dict(shipment_row)
        shipment["is_simulation"] = bool(shipment["is_simulation"])
        shipment["simulation_notice"] = SIMULATION_NOTICE

        email_row = conn.execute(
            """
            SELECT email_id,recipient,template,status,is_simulation,
                   delivery_attempts,attempted_at,sent_at,created_at
            FROM email_outbox WHERE order_id=?
            """,
            (order_id,),
        ).fetchone()
        email = dict(email_row) if email_row else None
        if email is not None:
            email["is_simulation"] = bool(email["is_simulation"])
            email["can_retry"] = bool(
                email["status"] == MAIL_SMTP_FAILED
                and int(email["delivery_attempts"]) < MAIL_DELIVERY_MAX_ATTEMPTS
            )
            email["simulation_notice"] = SIMULATION_NOTICE

        return_row = conn.execute(
            """
            SELECT * FROM return_requests WHERE order_id=?
            """,
            (order_id,),
        ).fetchone()
        return_request: dict[str, Any] | None = None
        if return_row is not None:
            return_request = dict(return_row)
            return_request["is_simulation"] = bool(return_request["is_simulation"])
            return_request["items"] = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT item.order_item_id,item.quantity,order_item.asin,
                           order_item.title
                    FROM return_request_items AS item
                    JOIN order_items AS order_item
                      ON order_item.order_item_id=item.order_item_id
                    WHERE item.return_request_id=?
                    ORDER BY order_item.ordinal
                    """,
                    (int(return_row["return_request_id"]),),
                )
            ]
            return_request["simulation_notice"] = SIMULATION_NOTICE

        refunds: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT * FROM refunds WHERE order_id=? ORDER BY refund_id", (order_id,)
        ):
            refund = dict(row)
            refund["is_simulation"] = bool(refund["is_simulation"])
            refund["simulation_notice"] = SIMULATION_NOTICE
            refunds.append(refund)
        events = [
            {
                **dict(row),
                "detail": json.loads(str(row["detail_json"] or "{}")),
            }
            for row in conn.execute(
                """
                SELECT order_event_id,event_type,actor,from_status,to_status,
                       idempotency_key,detail_json,created_at
                FROM order_events WHERE order_id=? ORDER BY order_event_id
                """,
                (order_id,),
            )
        ]
        for event in events:
            event.pop("detail_json", None)

        lifecycle_status = str(shipment["lifecycle_status"])
        if lifecycle_status == "CANCELLED":
            display_status = "CANCELLED"
        elif return_request is not None:
            display_status = f"RETURN_{return_request['status']}"
        else:
            display_status = lifecycle_status

        return {
            "order_id": order_id,
            "checkout_id": int(order_row["checkout_id"]),
            "checkout_mode": str(checkout_row["checkout_mode"]),
            "placement_status": order_row["status"],
            "status": display_status,
            "items": items,
            "address": json.loads(order_row["shipping_address_json"]),
            "delivery_method": order_row["delivery_method"],
            "items_subtotal_minor": int(order_row["items_subtotal_minor"]),
            "shipping_minor": int(order_row["shipping_minor"]),
            "total_minor": int(order_row["total_minor"]),
            "currency": order_row["currency"],
            "payment": payment,
            "shipment": shipment,
            "return_request": return_request,
            "refunds": refunds,
            "events": events,
            "can_cancel": lifecycle_status == "PREPARING",
            "can_return": lifecycle_status == "DELIVERED" and return_request is None,
            "email": email,
            "idempotency_key": order_row["idempotency_key"],
            "is_simulation": bool(order_row["is_simulation"]),
            "simulation_notice": SIMULATION_NOTICE,
            "created_at": order_row["created_at"],
        }

    def place_order(
        self,
        session_digest: str,
        idempotency_key: str,
        *,
        mail_mode: str = MAIL_LOCAL_ONLY,
    ) -> dict[str, Any]:
        key = self._validated_idempotency_key(idempotency_key)
        mail_status, mail_is_simulation = self._queued_mail_state(mail_mode)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            existing = conn.execute(
                """
                SELECT * FROM orders
                WHERE account_id=? AND idempotency_key=?
                """,
                (account_id, key),
            ).fetchone()
            if existing is not None:
                payload = self._order_payload(conn, existing)
                conn.commit()
                return self._attach_order_action_tokens(payload, session_digest)

            checkout_row = self._open_checkout_row(conn, account_id)
            if checkout_row is None:
                raise ContractError("complete checkout payment before placing the order")
            checkout_row, reconciliation_reason = self._reconcile_open_checkout(
                conn, checkout_row, account_id
            )
            if reconciliation_reason is not None:
                # Commit the demotion before surfacing the redirect instruction.
                # BEGIN IMMEDIATE keeps a second cart write from racing between
                # this final reconciliation and order snapshot creation.
                conn.commit()
                raise CheckoutReconciliationRequired(reconciliation_reason)
            if checkout_row["status"] != "PAYMENT_SELECTED":
                raise ContractError("complete checkout payment before placing the order")
            items, subtotal_minor, currency, fingerprint = self._checkout_snapshot(
                conn, checkout_row, account_id
            )
            if not items:
                raise ContractError("checkout requires at least one active cart item")
            address = self._address_payload(
                conn, int(checkout_row["address_id"]), account_id
            )
            self._require_supported_delivery_address(address)
            assert address is not None
            shipping_minor = int(checkout_row["shipping_minor"])
            total_minor = subtotal_minor + shipping_minor
            payment_row = conn.execute(
                """
                SELECT * FROM payment_attempts
                WHERE checkout_id=? AND account_id=? AND status='APPROVED'
                ORDER BY payment_attempt_id DESC LIMIT 1
                """,
                (int(checkout_row["checkout_id"]), account_id),
            ).fetchone()
            payment_method_is_approved = False
            if payment_row is not None:
                persisted_method = str(payment_row["method"])
                if persisted_method == LEGACY_TEST_CARD:
                    payment_method_is_approved = True
                else:
                    try:
                        payment_method_is_approved = (
                            payment_method(persisted_method).outcome
                            == PAYMENT_APPROVED
                        )
                    except ValueError:
                        payment_method_is_approved = False
            payment_valid = bool(
                payment_row
                and payment_method_is_approved
                and int(payment_row["is_simulation"]) == 1
                and payment_row["currency"] == currency
                and int(payment_row["amount_minor"]) == total_minor
                and payment_row["cart_fingerprint"] == fingerprint
            )
            if not payment_valid:
                raise ContractError(
                    "the simulated payment is stale; select a sandbox payment method again"
                )
            assert payment_row is not None
            now = self.now(conn)
            shipping_address = {
                key: address[key]
                for key in (
                    "address_id",
                    "full_name",
                    "address_line1",
                    "address_line2",
                    "city",
                    "state_region",
                    "postal_code",
                    "country_code",
                    "phone",
                )
            }
            address_json = json.dumps(
                shipping_address,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            order_cursor = conn.execute(
                """
                INSERT INTO orders(
                    account_id,checkout_id,payment_attempt_id,idempotency_key,status,
                    items_subtotal_minor,shipping_minor,total_minor,currency,
                    delivery_method,shipping_address_json,is_simulation,created_at
                ) VALUES (?,?,?,?,'PLACED',?,?,?,?,?,?,1,?)
                """,
                (
                    account_id,
                    int(checkout_row["checkout_id"]),
                    int(payment_row["payment_attempt_id"]),
                    key,
                    subtotal_minor,
                    shipping_minor,
                    total_minor,
                    currency,
                    checkout_row["delivery_method"],
                    address_json,
                    now,
                ),
            )
            order_id = int(order_cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO order_events(
                    order_id,account_id,event_type,actor,from_status,to_status,
                    idempotency_key,detail_json,created_at
                ) VALUES (?,?,'ORDER_PLACED','CUSTOMER',NULL,'PREPARING',?,?,?)
                """,
                (
                    order_id,
                    account_id,
                    key,
                    json.dumps(
                        {"checkout_mode": str(checkout_row["checkout_mode"])},
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO order_items(
                    order_id,ordinal,asin,title,image_path,quantity,selection_json,
                    unit_price_minor,line_total_minor,currency
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        order_id,
                        ordinal,
                        item["asin"],
                        item["title"],
                        item["image_path"],
                        int(item["quantity"]),
                        json.dumps(
                            item.get("selected_options", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        int(item["price_minor"]),
                        int(item["line_total_minor"]),
                        item["currency"],
                    )
                    for ordinal, item in enumerate(items, 1)
                ],
            )
            conn.execute(
                """
                INSERT INTO shipments(
                    order_id,status,delivery_method,shipping_minor,carrier,
                    tracking_code,is_simulation,created_at,updated_at
                ) VALUES (?,'PREPARING',?,?,?,NULL,1,?,?)
                """,
                (
                    order_id,
                    checkout_row["delivery_method"],
                    shipping_minor,
                    "Amazon Clone Simulation",
                    now,
                    now,
                ),
            )
            account_row = conn.execute(
                """
                SELECT email_normalized FROM accounts WHERE account_id=?
                """,
                (account_id,),
            ).fetchone()
            if account_row is None:
                raise ContractError("order account is unavailable")
            email_payload = json.dumps(
                {
                    "currency": currency,
                    "delivery_method": checkout_row["delivery_method"],
                    "item_count": sum(int(item["quantity"]) for item in items),
                    "items": [
                        {
                            "asin": item["asin"],
                            "quantity": int(item["quantity"]),
                            "selected_options": item.get("selected_options", {}),
                        }
                        for item in items
                    ],
                    "order_id": order_id,
                    "total_minor": total_minor,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                """
                INSERT INTO email_outbox(
                    account_id,order_id,recipient,template,subject,payload_json,
                    status,is_simulation,created_at
                ) VALUES (?,? ,?,'order-confirmation',?,?,?,?,?)
                """,
                (
                    account_id,
                    order_id,
                    account_row["email_normalized"],
                    f"Amazon Clone simulated order #{order_id}",
                    email_payload,
                    mail_status,
                    mail_is_simulation,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE checkout_sessions SET status='PLACED',placed_at=?,updated_at=?
                WHERE checkout_id=?
                """,
                (now, now, int(checkout_row["checkout_id"])),
            )
            if str(checkout_row["checkout_mode"]) == CHECKOUT_MODE_CART:
                conn.execute(
                    """
                    DELETE FROM account_cart_lines
                    WHERE account_id=? AND line_state='ACTIVE'
                    """,
                    (account_id,),
                )
            order_row = conn.execute(
                "SELECT * FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
            assert order_row is not None
            payload = self._order_payload(conn, order_row)
            conn.commit()
            return self._attach_order_action_tokens(payload, session_digest)

    @staticmethod
    def _normalized_order_id(order_id: int | str) -> int | None:
        if isinstance(order_id, bool):
            return None
        if isinstance(order_id, int):
            return order_id if 0 < order_id <= SQLITE_INTEGER_MAX else None
        if (
            isinstance(order_id, str)
            and 0 < len(order_id) <= 19
            and order_id.isascii()
            and order_id.isdecimal()
        ):
            try:
                normalized = int(order_id)
            except ValueError:
                return None
            return (
                normalized
                if str(normalized) == order_id
                and 0 < normalized <= SQLITE_INTEGER_MAX
                else None
            )
        return None

    def orders_for_session(self, session_digest: str) -> list[dict[str, Any]]:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            account_id = self._require_account_id(conn, session_digest)
            rows = conn.execute(
                """
                SELECT * FROM orders
                WHERE account_id=? ORDER BY order_id DESC
                """,
                (account_id,),
            ).fetchall()
            return [
                self._attach_order_action_tokens(
                    self._order_payload(conn, row), session_digest
                )
                for row in rows
            ]

    def order_for_session(
        self, session_digest: str, order_id: int | str
    ) -> dict[str, Any] | None:
        normalized_order_id = self._normalized_order_id(order_id)
        if normalized_order_id is None:
            return None
        self.ensure_session(session_digest)
        with self.connect() as conn:
            account_id = self._require_account_id(conn, session_digest)
            row = conn.execute(
                """
                SELECT * FROM orders WHERE order_id=? AND account_id=?
                """,
                (normalized_order_id, account_id),
            ).fetchone()
            return (
                self._attach_order_action_tokens(
                    self._order_payload(conn, row), session_digest
                )
                if row
                else None
            )

    @staticmethod
    def _owned_order_row(
        conn: sqlite3.Connection, account_id: int, order_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id=? AND account_id=?",
            (order_id, account_id),
        ).fetchone()
        if row is None:
            raise OrderNotFound("order is unavailable")
        return row

    @staticmethod
    def _order_shipment_row(
        conn: sqlite3.Connection, order_id: int
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM shipments WHERE order_id=?", (order_id,)
        ).fetchone()
        if row is None:
            raise OrderStateConflict("order shipment is unavailable")
        return row

    @staticmethod
    def _insert_order_event(
        conn: sqlite3.Connection,
        *,
        order_id: int,
        account_id: int,
        event_type: str,
        actor: str,
        from_status: str,
        to_status: str,
        now: str,
        idempotency_key: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO order_events(
                order_id,account_id,event_type,actor,from_status,to_status,
                idempotency_key,detail_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                order_id,
                account_id,
                event_type,
                actor,
                from_status,
                to_status,
                idempotency_key,
                json.dumps(
                    detail or {},
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
            ),
        )

    def cancel_order(
        self,
        session_digest: str,
        order_id: int | str,
        idempotency_key: str,
        action_token: str,
    ) -> dict[str, Any]:
        normalized_id = self._normalized_order_id(order_id)
        if normalized_id is None:
            raise OrderNotFound("order is unavailable")
        key = self._validated_action_idempotency_key(idempotency_key)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            order = self._owned_order_row(conn, account_id, normalized_id)
            self._require_order_action_token(
                session_digest, "cancel", normalized_id, action_token
            )
            key_owner = conn.execute(
                """
                SELECT * FROM order_action_keys
                WHERE account_id=? AND idempotency_key=?
                """,
                (account_id, key),
            ).fetchone()
            if key_owner is not None and (
                str(key_owner["action_type"]) != "CANCEL"
                or int(key_owner["order_id"]) != normalized_id
            ):
                raise OrderStateConflict(
                    "idempotency key belongs to another order action"
                )
            replay = key_owner
            existing_action = conn.execute(
                """
                SELECT * FROM order_action_keys
                WHERE order_id=? AND action_type='CANCEL'
                """,
                (normalized_id,),
            ).fetchone()
            shipment = self._order_shipment_row(conn, normalized_id)
            if existing_action is not None or replay is not None:
                if str(shipment["lifecycle_status"]) != "CANCELLED":
                    raise OrderStateConflict("cancel action record is inconsistent")
                payload = self._order_payload(conn, order)
                conn.commit()
                return self._attach_order_action_tokens(payload, session_digest)
            if str(shipment["lifecycle_status"]) != "PREPARING":
                raise OrderStateConflict("only a preparing order can be cancelled")

            now = self.now(conn)
            action_cursor = conn.execute(
                """
                INSERT INTO order_action_keys(
                    account_id,order_id,action_type,idempotency_key,created_at
                ) VALUES (?,?,'CANCEL',?,?)
                """,
                (account_id, normalized_id, key, now),
            )
            updated = conn.execute(
                """
                UPDATE shipments SET lifecycle_status='CANCELLED',
                    revision=revision+1,tracking_code=NULL,cancelled_at=?,updated_at=?
                WHERE shipment_id=? AND lifecycle_status='PREPARING' AND revision=?
                """,
                (
                    now,
                    now,
                    int(shipment["shipment_id"]),
                    int(shipment["revision"]),
                ),
            )
            if updated.rowcount != 1:
                raise OrderStateConflict("shipment changed during cancellation")
            refund_cursor = conn.execute(
                """
                INSERT INTO refunds(
                    account_id,order_id,payment_attempt_id,return_request_id,
                    kind,status,amount_minor,currency,idempotency_key,
                    is_simulation,created_at
                ) VALUES (?,?,?,NULL,'CANCELLATION','COMPLETED',?,?,?,1,?)
                """,
                (
                    account_id,
                    normalized_id,
                    int(order["payment_attempt_id"]),
                    int(order["total_minor"]),
                    str(order["currency"]),
                    key,
                    now,
                ),
            )
            conn.execute(
                "UPDATE order_action_keys SET result_reference=? WHERE order_action_key_id=?",
                (int(refund_cursor.lastrowid), int(action_cursor.lastrowid)),
            )
            self._insert_order_event(
                conn,
                order_id=normalized_id,
                account_id=account_id,
                event_type="ORDER_CANCELLED",
                actor="CUSTOMER",
                from_status="PREPARING",
                to_status="CANCELLED",
                idempotency_key=key,
                detail={"refund_kind": "CANCELLATION", "is_simulation": True},
                now=now,
            )
            payload = self._order_payload(conn, order)
            conn.commit()
            return self._attach_order_action_tokens(payload, session_digest)

    def advance_order_shipment(
        self, order_id: int | str, target_status: str
    ) -> dict[str, Any]:
        normalized_id = self._normalized_order_id(order_id)
        if normalized_id is None:
            raise OrderNotFound("order is unavailable")
        if target_status not in {"SHIPPED", "DELIVERED"}:
            raise ContractError("shipment target must be SHIPPED or DELIVERED")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute(
                "SELECT * FROM orders WHERE order_id=?", (normalized_id,)
            ).fetchone()
            if order is None:
                raise OrderNotFound("order is unavailable")
            shipment = self._order_shipment_row(conn, normalized_id)
            current = str(shipment["lifecycle_status"])
            if current == target_status:
                payload = self._order_payload(conn, order)
                conn.commit()
                return payload
            expected = "PREPARING" if target_status == "SHIPPED" else "SHIPPED"
            if current != expected:
                raise OrderStateConflict(
                    f"shipment cannot transition from {current} to {target_status}"
                )
            now = self.now(conn)
            if target_status == "SHIPPED":
                tracking_code = (
                    f"ACL-{normalized_id:08d}-{secrets.token_hex(5).upper()}"
                )
                event_type = "SHIPMENT_SHIPPED"
                cursor = conn.execute(
                    """
                    UPDATE shipments SET lifecycle_status='SHIPPED',
                        revision=revision+1,carrier=?,tracking_code=?,shipped_at=?,
                        updated_at=?
                    WHERE shipment_id=? AND lifecycle_status='PREPARING' AND revision=?
                    """,
                    (
                        LOCAL_SIMULATED_CARRIER,
                        tracking_code,
                        now,
                        now,
                        int(shipment["shipment_id"]),
                        int(shipment["revision"]),
                    ),
                )
            else:
                event_type = "SHIPMENT_DELIVERED"
                cursor = conn.execute(
                    """
                    UPDATE shipments SET lifecycle_status='DELIVERED',
                        revision=revision+1,delivered_at=?,updated_at=?
                    WHERE shipment_id=? AND lifecycle_status='SHIPPED' AND revision=?
                    """,
                    (
                        now,
                        now,
                        int(shipment["shipment_id"]),
                        int(shipment["revision"]),
                    ),
                )
            if cursor.rowcount != 1:
                raise OrderStateConflict("shipment changed during admin transition")
            self._insert_order_event(
                conn,
                order_id=normalized_id,
                account_id=int(order["account_id"]),
                event_type=event_type,
                actor="ADMIN",
                from_status=current,
                to_status=target_status,
                detail={"is_simulation": True},
                now=now,
            )
            payload = self._order_payload(conn, order)
            conn.commit()
            return payload

    @staticmethod
    def _validated_return_fields(
        reason_code: str, customer_note: str
    ) -> tuple[str, str]:
        if reason_code not in RETURN_REASON_CODES:
            raise ContractError("return reason is unsupported")
        if not isinstance(customer_note, str):
            raise ContractError("return note must be text")
        note = customer_note.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(note) > RETURN_NOTE_MAX_LENGTH or any(
            (ord(character) < 32 and character not in {"\n", "\t"})
            or ord(character) == 127
            for character in note
        ):
            raise ContractError("return note is invalid")
        return reason_code, note

    def create_return_request(
        self,
        session_digest: str,
        order_id: int | str,
        reason_code: str,
        customer_note: str,
        idempotency_key: str,
        action_token: str,
    ) -> dict[str, Any]:
        normalized_id = self._normalized_order_id(order_id)
        if normalized_id is None:
            raise OrderNotFound("order is unavailable")
        reason, note = self._validated_return_fields(reason_code, customer_note)
        key = self._validated_action_idempotency_key(idempotency_key)
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_id = self._require_account_id(conn, session_digest)
            order = self._owned_order_row(conn, account_id, normalized_id)
            self._require_order_action_token(
                session_digest, "return", normalized_id, action_token
            )
            key_owner = conn.execute(
                """
                SELECT * FROM order_action_keys
                WHERE account_id=? AND idempotency_key=?
                """,
                (account_id, key),
            ).fetchone()
            if key_owner is not None and (
                str(key_owner["action_type"]) != "RETURN_REQUEST"
                or int(key_owner["order_id"]) != normalized_id
            ):
                raise OrderStateConflict(
                    "idempotency key belongs to another order action"
                )
            replay = key_owner
            existing = conn.execute(
                "SELECT * FROM return_requests WHERE order_id=?",
                (normalized_id,),
            ).fetchone()
            if existing is not None:
                if replay is None:
                    raise OrderStateConflict("this order already has a return request")
                payload = self._order_payload(conn, order)
                conn.commit()
                return self._attach_order_action_tokens(payload, session_digest)
            shipment = self._order_shipment_row(conn, normalized_id)
            if str(shipment["lifecycle_status"]) != "DELIVERED":
                raise OrderStateConflict("returns can be requested only after delivery")
            item_rows = conn.execute(
                "SELECT order_item_id,quantity FROM order_items WHERE order_id=? ORDER BY ordinal",
                (normalized_id,),
            ).fetchall()
            if not item_rows:
                raise OrderStateConflict("order has no returnable items")
            now = self.now(conn)
            action_cursor = conn.execute(
                """
                INSERT INTO order_action_keys(
                    account_id,order_id,action_type,idempotency_key,created_at
                ) VALUES (?,?,'RETURN_REQUEST',?,?)
                """,
                (account_id, normalized_id, key, now),
            )
            request_cursor = conn.execute(
                """
                INSERT INTO return_requests(
                    account_id,order_id,idempotency_key,reason_code,customer_note,
                    status,revision,is_simulation,created_at,updated_at
                ) VALUES (?,?,?,?,?,'REQUESTED',1,1,?,?)
                """,
                (account_id, normalized_id, key, reason, note, now, now),
            )
            return_id = int(request_cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO return_request_items(
                    return_request_id,order_item_id,quantity
                ) VALUES (?,?,?)
                """,
                [
                    (return_id, int(row["order_item_id"]), int(row["quantity"]))
                    for row in item_rows
                ],
            )
            conn.execute(
                "UPDATE order_action_keys SET result_reference=? WHERE order_action_key_id=?",
                (return_id, int(action_cursor.lastrowid)),
            )
            self._insert_order_event(
                conn,
                order_id=normalized_id,
                account_id=account_id,
                event_type="RETURN_REQUESTED",
                actor="CUSTOMER",
                from_status="DELIVERED",
                to_status="RETURN_REQUESTED",
                idempotency_key=key,
                detail={"reason_code": reason, "is_simulation": True},
                now=now,
            )
            payload = self._order_payload(conn, order)
            conn.commit()
            return self._attach_order_action_tokens(payload, session_digest)

    def return_for_session(
        self, session_digest: str, return_request_id: int | str
    ) -> dict[str, Any] | None:
        normalized_id = self._normalized_order_id(return_request_id)
        if normalized_id is None:
            return None
        self.ensure_session(session_digest)
        with self.connect() as conn:
            account_id = self._require_account_id(conn, session_digest)
            row = conn.execute(
                """
                SELECT orders.* FROM return_requests AS request
                JOIN orders ON orders.order_id=request.order_id
                WHERE request.return_request_id=? AND request.account_id=?
                """,
                (normalized_id, account_id),
            ).fetchone()
            return (
                self._attach_order_action_tokens(
                    self._order_payload(conn, row), session_digest
                )
                if row
                else None
            )

    def advance_return_request(
        self, return_request_id: int | str, target_status: str
    ) -> dict[str, Any]:
        normalized_id = self._normalized_order_id(return_request_id)
        if normalized_id is None:
            raise ReturnNotFound("return request is unavailable")
        if target_status not in {"RECEIVED", "REFUNDED"}:
            raise ContractError("return target must be RECEIVED or REFUNDED")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = conn.execute(
                "SELECT * FROM return_requests WHERE return_request_id=?",
                (normalized_id,),
            ).fetchone()
            if request is None:
                raise ReturnNotFound("return request is unavailable")
            order = conn.execute(
                "SELECT * FROM orders WHERE order_id=?", (int(request["order_id"]),)
            ).fetchone()
            if order is None:
                raise OrderStateConflict("return order is unavailable")
            shipment = self._order_shipment_row(conn, int(order["order_id"]))
            if str(shipment["lifecycle_status"]) != "DELIVERED":
                raise OrderStateConflict("return shipment is not delivered")
            current = str(request["status"])
            if current == target_status:
                payload = self._order_payload(conn, order)
                conn.commit()
                return payload
            expected = "REQUESTED" if target_status == "RECEIVED" else "RECEIVED"
            if current != expected:
                raise OrderStateConflict(
                    f"return cannot transition from {current} to {target_status}"
                )
            now = self.now(conn)
            cursor = conn.execute(
                """
                UPDATE return_requests SET status=?,revision=revision+1,updated_at=?
                WHERE return_request_id=? AND status=? AND revision=?
                """,
                (
                    target_status,
                    now,
                    normalized_id,
                    current,
                    int(request["revision"]),
                ),
            )
            if cursor.rowcount != 1:
                raise OrderStateConflict("return changed during admin transition")
            event_type = (
                "RETURN_RECEIVED" if target_status == "RECEIVED" else "RETURN_REFUNDED"
            )
            if target_status == "REFUNDED":
                conn.execute(
                    """
                    INSERT INTO refunds(
                        account_id,order_id,payment_attempt_id,return_request_id,
                        kind,status,amount_minor,currency,idempotency_key,
                        is_simulation,created_at
                    ) VALUES (?,?,?,?,'RETURN','COMPLETED',?,?,?,1,?)
                    """,
                    (
                        int(order["account_id"]),
                        int(order["order_id"]),
                        int(order["payment_attempt_id"]),
                        normalized_id,
                        int(order["total_minor"]),
                        str(order["currency"]),
                        f"return-{normalized_id}-simulated-refund",
                        now,
                    ),
                )
            self._insert_order_event(
                conn,
                order_id=int(order["order_id"]),
                account_id=int(order["account_id"]),
                event_type=event_type,
                actor="ADMIN",
                from_status=f"RETURN_{current}",
                to_status=f"RETURN_{target_status}",
                detail={"is_simulation": True},
                now=now,
            )
            payload = self._order_payload(conn, order)
            conn.commit()
            return payload

    def mail_outbox(self, account: int | None = None) -> list[dict[str, Any]]:
        if account is not None and (
            isinstance(account, bool) or not isinstance(account, int) or account <= 0
        ):
            raise ContractError("mail outbox account filter must be an account id")
        with self.connect() as conn:
            if account is None:
                rows = conn.execute(
                    "SELECT * FROM email_outbox ORDER BY email_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM email_outbox
                    WHERE account_id=? ORDER BY email_id
                    """,
                    (account,),
                ).fetchall()
            messages: list[dict[str, Any]] = []
            for row in rows:
                message = dict(row)
                message["payload"] = json.loads(message.pop("payload_json"))
                message["is_simulation"] = bool(message["is_simulation"])
                message["simulation_notice"] = SIMULATION_NOTICE
                messages.append(message)
            return messages

    def products(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, d.payload_json AS pdp_json
                FROM catalog_products p
                LEFT JOIN product_details d ON d.asin=p.asin
                ORDER BY p.asin
                """
            ).fetchall()
            return [self._product_row(row) for row in rows]

    def product(self, asin: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, d.payload_json AS pdp_json
                FROM catalog_products p
                LEFT JOIN product_details d ON d.asin=p.asin
                WHERE p.asin=?
                """,
                (asin,),
            ).fetchone()
            return self._product_row(row) if row else None

    @staticmethod
    def _product_row(row: sqlite3.Row) -> dict[str, Any]:
        product = dict(row)
        raw_detail = product.pop("pdp_json", None)
        product["pdp"] = json.loads(raw_detail) if raw_detail else None
        return product

    def ranking(self, list_id: str = "external-ssd") -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT ri.rank, p.*, d.payload_json AS pdp_json
                FROM ranking_items ri
                JOIN catalog_products p ON p.asin=ri.asin
                LEFT JOIN product_details d ON d.asin=p.asin
                WHERE ri.list_id=?
                ORDER BY ri.rank
                """,
                (list_id,),
            ).fetchall()
            return [self._product_row(row) for row in rows]

    def _record_navigation(
        self,
        conn: sqlite3.Connection,
        session_digest: str,
        method: str,
        route_key: str,
        path: str,
        referer: str,
        status: int,
        asin: str | None = None,
        rank: int | None = None,
    ) -> int:
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM navigation_events WHERE session_digest=?",
            (session_digest,),
        ).fetchone()[0]
        cursor = conn.execute(
            """
            INSERT INTO navigation_events(
                session_digest,sequence,method,route_key,path,referer,status,asin,rank,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (session_digest, sequence, method, route_key, path, referer, status, asin, rank, self.now(conn)),
        )
        return int(cursor.lastrowid)

    def record_best_sellers(self, session_digest: str, path: str, referer: str) -> None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event_id = self._record_navigation(
                conn, session_digest, "GET", "BEST_SELLERS", path, referer, 200, TARGET_ASIN, 2
            )
            epoch = int(conn.execute("SELECT value FROM meta WHERE key='reset_epoch'").fetchone()[0])
            conn.execute(
                """
                INSERT INTO task_progress(
                    session_digest,task_id,stage,best_sellers_event_id,reset_epoch,updated_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(session_digest) DO UPDATE SET
                    task_id=excluded.task_id,
                    stage='BEST_SELLERS_SEEN',
                    best_sellers_event_id=excluded.best_sellers_event_id,
                    pdp_event_id=NULL,
                    flow_capability_digest=NULL,
                    capability_consumed=0,
                    reset_epoch=excluded.reset_epoch,
                    updated_at=excluded.updated_at
                """,
                (session_digest, TASK_ID, "BEST_SELLERS_SEEN", event_id, epoch, self.now(conn)),
            )
            conn.commit()

    def record_pdp(
        self,
        session_digest: str,
        path: str,
        referer: str,
        capability_digest: str,
        referer_is_canonical: bool | None = None,
    ) -> bool:
        self.ensure_session(session_digest)
        ref_path = urlparse(referer).path if referer else ""
        canonical_source = ref_path == BEST_SELLERS_PATH if referer_is_canonical is None else referer_is_canonical
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event_id = self._record_navigation(
                conn, session_digest, "GET", "TARGET_PDP", path, referer, 200, TARGET_ASIN, None
            )
            progress = conn.execute(
                "SELECT * FROM task_progress WHERE session_digest=?", (session_digest,)
            ).fetchone()
            eligible = bool(
                progress
                and progress["stage"] == "BEST_SELLERS_SEEN"
                and progress["capability_consumed"] == 0
                and path == PDP_PATH
                and canonical_source
            )
            if eligible:
                conn.execute(
                    """
                    UPDATE task_progress SET
                        stage='TARGET_PDP_SEEN',
                        pdp_event_id=?,
                        flow_capability_digest=?,
                        capability_consumed=0,
                        updated_at=?
                    WHERE session_digest=?
                    """,
                    (event_id, capability_digest, self.now(conn), session_digest),
                )
            conn.commit()
            return eligible

    def record_read_route(
        self, session_digest: str, route_key: str, path: str, referer: str, status: int = 200
    ) -> None:
        self.ensure_session(session_digest)
        with self.connect() as conn:
            self._record_navigation(conn, session_digest, "GET", route_key, path, referer, status)

    def record_rejected_post(
        self,
        session_digest: str,
        path: str,
        media_type: str,
        raw_body: bytes,
    ) -> None:
        """Journal a rejected boundary POST without retaining attacker data.

        Rejected routes can carry passwords, one-time codes, or arbitrary
        attacker-chosen bytes.  Even hashing the original body would make a
        six-digit code cheaply enumerable, so both persisted body fields use a
        fixed sentinel.  The route, media type, status, and outcome retain the
        useful boundary audit signal.
        """
        self.ensure_session(session_digest)
        redacted = REDACTED_REJECTED_POST_BODY
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO request_journal(
                    session_digest,method,path,media_type,raw_body_sha256,
                    canonical_form,status,outcome,contract_id,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_digest,
                    "POST",
                    path,
                    media_type,
                    hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
                    redacted,
                    404,
                    "rejected-nonterminal",
                    "amazon-public-boundary-v1",
                    self.now(conn),
                ),
            )

    def terminal_request(
        self,
        session_digest: str,
        path: str,
        media_type: str,
        raw_body: bytes,
        fields: list[tuple[str, str]],
        capability_digest: str,
        source_is_canonical: bool = True,
    ) -> tuple[int, str]:
        self.ensure_session(session_digest)
        expected_fields = [("ASIN", TARGET_ASIN), ("quantity", TARGET_QUANTITY)]
        expected_body = (
            f"ASIN={TARGET_ASIN}&quantity={TARGET_QUANTITY}".encode("ascii")
        )
        body_is_safe_terminal_contract = bool(
            path in TERMINAL_PATHS
            and media_type.lower() == "application/x-www-form-urlencoded"
            and raw_body == expected_body
            and fields == expected_fields
        )
        if body_is_safe_terminal_contract:
            raw_sha = hashlib.sha256(raw_body).hexdigest()
            canonical_form = (
                f"ASIN={TARGET_ASIN}&quantity={TARGET_QUANTITY}"
            )
        else:
            canonical_form = REDACTED_REJECTED_POST_BODY
            raw_sha = hashlib.sha256(canonical_form.encode("utf-8")).hexdigest()
        preliminary_error = None
        if path not in TERMINAL_PATHS:
            preliminary_error = "wrong-terminal-path"
        elif media_type.lower() != "application/x-www-form-urlencoded":
            preliminary_error = "wrong-media-type"
        elif raw_body != expected_body:
            preliminary_error = "wrong-form-body"
        elif fields != expected_fields:
            preliminary_error = "wrong-form-body"
        elif not source_is_canonical:
            preliminary_error = "invalid-terminal-source"

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            progress = conn.execute(
                "SELECT * FROM task_progress WHERE session_digest=?", (session_digest,)
            ).fetchone()
            latest_navigation = conn.execute(
                "SELECT event_id FROM navigation_events WHERE session_digest=? ORDER BY sequence DESC LIMIT 1",
                (session_digest,),
            ).fetchone()
            error = preliminary_error
            if error is None and not progress:
                error = "missing-navigation-sequence"
            elif error is None and progress["stage"] != "TARGET_PDP_SEEN":
                error = "wrong-navigation-stage"
            elif error is None and progress["capability_consumed"]:
                error = "capability-already-consumed"
            elif error is None and progress["flow_capability_digest"] != capability_digest:
                error = "invalid-flow-capability"
            elif error is None and (
                latest_navigation is None or latest_navigation["event_id"] != progress["pdp_event_id"]
            ):
                error = "stale-navigation-sequence"

            cart_table, cart_owner_column, cart_owner_id = self._cart_line_owner(
                conn, session_digest
            )
            target_options = self.default_product_options(TARGET_ASIN)
            target_selection_key = canonical_selection_key(target_options)
            existing = conn.execute(
                f"SELECT quantity FROM {cart_table} "
                f"WHERE {cart_owner_column}=? AND asin=? AND selection_key=?",
                (cart_owner_id, TARGET_ASIN, target_selection_key),
            ).fetchone()
            if error is None and existing:
                error = "target-line-already-exists"

            status = 303 if error is None else 409
            outcome = "accepted" if error is None else error
            cursor = conn.execute(
                """
                INSERT INTO request_journal(
                    session_digest,method,path,media_type,raw_body_sha256,
                    canonical_form,status,outcome,contract_id,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_digest,
                    "POST",
                    path,
                    media_type,
                    raw_sha,
                    canonical_form,
                    status,
                    outcome,
                    "amazon-task-900136-terminal-v1",
                    self.now(conn),
                ),
            )
            request_id = int(cursor.lastrowid)
            if error is None:
                selection_json = json.dumps(
                    target_options,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                conn.execute(
                    f"""
                    INSERT INTO {cart_table}(
                        line_id,{cart_owner_column},asin,quantity,selection_json,
                        selection_key,line_state
                    ) VALUES (?,?,?,?,?,?,'ACTIVE')
                    """,
                    (
                        self._new_cart_line_id(),
                        cart_owner_id,
                        TARGET_ASIN,
                        int(TARGET_QUANTITY),
                        selection_json,
                        target_selection_key,
                    ),
                )
                conn.execute(
                    """
                    UPDATE task_progress SET
                        stage='COMPLETE', capability_consumed=1, updated_at=?
                    WHERE session_digest=?
                    """,
                    (self.now(conn), session_digest),
                )
                run_id = conn.execute("SELECT value FROM meta WHERE key='run_id'").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO task_completions(
                        run_id,session_digest,task_id,terminal_path,request_id,completed_at
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (run_id, session_digest, TASK_ID, path, request_id, self.now(conn)),
                )
            conn.commit()
            return status, outcome

    def advance_clock(self, seconds: int) -> str:
        if seconds < 0 or seconds > 31_536_000:
            raise ContractError("clock advance is out of range")
        from datetime import datetime, timedelta, timezone

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.now(conn).replace("Z", "+00:00")
            value = datetime.fromisoformat(current).astimezone(timezone.utc) + timedelta(seconds=seconds)
            result = value.isoformat().replace("+00:00", "Z")
            conn.execute("UPDATE meta SET value=? WHERE key='controlled_now'", (result,))
            conn.commit()
            return result

    def normalized_state(self) -> dict[str, Any]:
        with self.connect() as conn:
            meta = {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM meta")}
            carts = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT c.session_digest,p.asin,p.title,cl.quantity,
                           cl.selection_json,p.price_minor,p.currency,p.image_path
                    FROM carts c
                    JOIN cart_lines cl ON cl.cart_id=c.cart_id
                    JOIN commerce_offers p ON p.asin=cl.asin
                    WHERE cl.line_state='ACTIVE'
                    ORDER BY c.session_digest,p.asin,cl.selection_key,cl.line_id
                    """
                )
            ]
            for cart in carts:
                selected_options = self._stored_product_options(
                    str(cart["asin"]), cart.pop("selection_json", "{}")
                )
                self._apply_transaction_quote(cart, selected_options)
            progress = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT session_digest,task_id,stage,capability_consumed,reset_epoch
                    FROM task_progress ORDER BY session_digest
                    """
                )
            ]
            completions = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT run_id,session_digest,task_id,terminal_path,completed_at
                    FROM task_completions ORDER BY completion_id
                    """
                )
            ]
            return {
                "schema": "amazon-clone.state.v1",
                "snapshot_id": meta.get("snapshot_id"),
                "fixture_sha256": meta.get("fixture_sha256"),
                "controlled_now": meta.get("controlled_now"),
                "reset_epoch": int(meta.get("reset_epoch", "0")),
                "carts": carts,
                "task_progress": progress,
                "task_completions": completions,
            }

    def journal(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id,session_digest,method,path,media_type,raw_body_sha256,
                       canonical_form,status,outcome,contract_id,created_at
                FROM request_journal ORDER BY request_id
                """
            ).fetchall()
            return [dict(row) for row in rows]
