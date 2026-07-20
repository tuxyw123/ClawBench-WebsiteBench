"""SQLite commerce adapter for the Amazon-shaped calibration site.

The Amazon renderer and dev-136 request contract stay site-specific.  This
module supplies the account, cart ownership, checkout, inventory, and order
interface shared conceptually with WebsiteBench's compiled commerce runtime.
It deliberately stores no raw authentication, verification, reset, or payment
credential.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


CLONE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = CLONE_ROOT.parents[2] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clawbench.web2code.commerce_contract import AccountOrderCommerce  # noqa: E402


AUTH_COOKIE = "amazon_local_auth"
AUTH_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ORDER_RE = re.compile(r"AMZ-\d{6}\Z")
PASSWORD_MIN_LENGTH = 10
SESSION_SECONDS = 24 * 60 * 60
VERIFICATION_SECONDS = 30 * 60
RESET_SECONDS = 60 * 60
MAX_QUANTITY = 3
INITIAL_INVENTORY = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CommerceError(ValueError):
    """Stable domain failure exposed by both HTML forms and tests."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


@contextmanager
def _database(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=4, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 4000")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        # sqlite3.executescript() commits its surrounding transaction before
        # applying schema DDL. Domain operations keep the explicit transaction;
        # schema initialization may already be back in autocommit mode.
        if db.in_transaction:
            db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _password_hash(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode(
        "ascii"
    )


def _password_matches(password: str, salt: str, expected: str) -> bool:
    try:
        raw_salt = base64.b64decode(salt.encode("ascii"), validate=True)
        _, actual = _password_hash(password, raw_salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _price_cents(product: Mapping[str, Any]) -> int:
    return int(round(float(product["price"]) * 100))


class AmazonCommerceAdapter(AccountOrderCommerce):
    """Deep commerce module behind the Amazon presentation and route adapter."""

    def __init__(
        self, path: Path | str, products: Mapping[str, Mapping[str, Any]]
    ) -> None:
        self.path = Path(path).resolve()
        self.products = {str(key): dict(value) for key, value in products.items()}
        self._init_schema()

    def _init_schema(self) -> None:
        with _database(self.path, write=True) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
                    full_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_tokens (
                    token_hash TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('verification', 'reset')),
                    user_id TEXT NOT NULL,
                    issued_at_epoch INTEGER NOT NULL,
                    expires_at_epoch INTEGER NOT NULL,
                    used_at_epoch INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_session_id TEXT NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    expires_at_epoch INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (device_session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS account_cart (
                    user_id TEXT NOT NULL,
                    asin TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 3),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, asin),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS account_saved (
                    user_id TEXT NOT NULL,
                    asin TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 3),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, asin),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS account_wishlist (
                    user_id TEXT NOT NULL,
                    asin TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, asin),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS inventory (
                    asin TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (available >= 0),
                    initial_available INTEGER NOT NULL CHECK (initial_available >= 0)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    number TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('placed', 'cancelled')),
                    placed_at TEXT NOT NULL,
                    placed_at_epoch INTEGER NOT NULL,
                    cancelled_at TEXT,
                    subtotal_cents INTEGER NOT NULL,
                    tax_cents INTEGER NOT NULL,
                    shipping_cents INTEGER NOT NULL,
                    total_cents INTEGER NOT NULL,
                    full_name TEXT NOT NULL,
                    address_line TEXT NOT NULL,
                    city TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    resources_restored INTEGER NOT NULL DEFAULT 0 CHECK (resources_restored IN (0, 1)),
                    idempotency_key TEXT NOT NULL,
                    UNIQUE (user_id, idempotency_key),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    order_number TEXT NOT NULL,
                    asin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price_cents INTEGER NOT NULL,
                    PRIMARY KEY (order_number, asin),
                    FOREIGN KEY (order_number) REFERENCES orders(number) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS commerce_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );

                INSERT OR IGNORE INTO commerce_meta (key, value)
                VALUES ('order_sequence', 0);

                CREATE INDEX IF NOT EXISTS idx_auth_sessions_device
                    ON auth_sessions(device_session_id, expires_at_epoch);
                CREATE INDEX IF NOT EXISTS idx_orders_user_time
                    ON orders(user_id, placed_at_epoch DESC);
                """
            )
            session_columns = {
                str(row["name"]) for row in db.execute("PRAGMA table_info(sessions)")
            }
            if "user_id" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            if "account_email" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN account_email TEXT")
            if "csrf_token" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN csrf_token TEXT")
            db.execute(
                "UPDATE sessions SET csrf_token = lower(hex(randomblob(24))) WHERE csrf_token IS NULL"
            )
            for asin in self.products:
                db.execute(
                    """
                    INSERT OR IGNORE INTO inventory (asin, available, initial_available)
                    VALUES (?, ?, ?)
                    """,
                    (asin, INITIAL_INVENTORY, INITIAL_INVENTORY),
                )
            self._create_projection_triggers(db)

    @staticmethod
    def _create_projection_triggers(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS account_cart_insert
            AFTER INSERT ON cart
            WHEN (SELECT user_id FROM sessions WHERE id = NEW.session_id) IS NOT NULL
            BEGIN
                INSERT INTO account_cart (user_id, asin, quantity, updated_at)
                SELECT user_id, NEW.asin, NEW.quantity, NEW.updated_at
                FROM sessions WHERE id = NEW.session_id
                ON CONFLICT(user_id, asin) DO UPDATE SET
                    quantity = excluded.quantity,
                    updated_at = excluded.updated_at;
            END;

            CREATE TRIGGER IF NOT EXISTS account_cart_update
            AFTER UPDATE OF quantity, updated_at ON cart
            WHEN (SELECT user_id FROM sessions WHERE id = NEW.session_id) IS NOT NULL
            BEGIN
                INSERT INTO account_cart (user_id, asin, quantity, updated_at)
                SELECT user_id, NEW.asin, NEW.quantity, NEW.updated_at
                FROM sessions WHERE id = NEW.session_id
                ON CONFLICT(user_id, asin) DO UPDATE SET
                    quantity = excluded.quantity,
                    updated_at = excluded.updated_at;
            END;

            CREATE TRIGGER IF NOT EXISTS account_cart_delete
            AFTER DELETE ON cart
            WHEN (SELECT user_id FROM sessions WHERE id = OLD.session_id) IS NOT NULL
            BEGIN
                DELETE FROM account_cart
                WHERE user_id = (SELECT user_id FROM sessions WHERE id = OLD.session_id)
                  AND asin = OLD.asin;
            END;

            CREATE TRIGGER IF NOT EXISTS account_saved_insert
            AFTER INSERT ON saved
            WHEN (SELECT user_id FROM sessions WHERE id = NEW.session_id) IS NOT NULL
            BEGIN
                INSERT INTO account_saved (user_id, asin, quantity, updated_at)
                SELECT user_id, NEW.asin, NEW.quantity, NEW.saved_at
                FROM sessions WHERE id = NEW.session_id
                ON CONFLICT(user_id, asin) DO UPDATE SET
                    quantity = excluded.quantity,
                    updated_at = excluded.updated_at;
            END;

            CREATE TRIGGER IF NOT EXISTS account_saved_delete
            AFTER DELETE ON saved
            WHEN (SELECT user_id FROM sessions WHERE id = OLD.session_id) IS NOT NULL
            BEGIN
                DELETE FROM account_saved
                WHERE user_id = (SELECT user_id FROM sessions WHERE id = OLD.session_id)
                  AND asin = OLD.asin;
            END;

            CREATE TRIGGER IF NOT EXISTS account_wishlist_insert
            AFTER INSERT ON wishlist
            WHEN (SELECT user_id FROM sessions WHERE id = NEW.session_id) IS NOT NULL
            BEGIN
                INSERT INTO account_wishlist (user_id, asin, updated_at)
                SELECT user_id, NEW.asin, NEW.added_at
                FROM sessions WHERE id = NEW.session_id
                ON CONFLICT(user_id, asin) DO UPDATE SET updated_at = excluded.updated_at;
            END;

            CREATE TRIGGER IF NOT EXISTS account_wishlist_delete
            AFTER DELETE ON wishlist
            WHEN (SELECT user_id FROM sessions WHERE id = OLD.session_id) IS NOT NULL
            BEGIN
                DELETE FROM account_wishlist
                WHERE user_id = (SELECT user_id FROM sessions WHERE id = OLD.session_id)
                  AND asin = OLD.asin;
            END;
            """
        )

    @staticmethod
    def _normalized_email(email: str) -> str:
        normalized = email.strip().casefold()
        if len(normalized) > 254 or not EMAIL_RE.fullmatch(normalized):
            raise CommerceError("invalid_email", "Enter a valid email address.")
        return normalized

    @staticmethod
    def _validate_password(password: str, confirm: str) -> None:
        if len(password) < PASSWORD_MIN_LENGTH:
            raise CommerceError(
                "weak_password",
                f"Password must contain at least {PASSWORD_MIN_LENGTH} characters.",
            )
        if password != confirm:
            raise CommerceError("password_mismatch", "Passwords do not match.")

    def register(self, email: str, password: str, confirm: str) -> str:
        normalized = self._normalized_email(email)
        self._validate_password(password, confirm)
        user_id = f"user_{secrets.token_hex(12)}"
        salt, digest = _password_hash(password)
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        try:
            with _database(self.path, write=True) as db:
                db.execute(
                    """
                    INSERT INTO users
                        (id, email, password_salt, password_hash, verified, full_name, created_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        user_id,
                        normalized,
                        salt,
                        digest,
                        normalized.split("@", 1)[0],
                        utc_now(),
                    ),
                )
                db.execute(
                    """
                    INSERT INTO account_tokens
                        (token_hash, kind, user_id, issued_at_epoch, expires_at_epoch)
                    VALUES (?, 'verification', ?, ?, ?)
                    """,
                    (_token_hash(token), user_id, now, now + VERIFICATION_SECONDS),
                )
        except sqlite3.IntegrityError as error:
            raise CommerceError(
                "duplicate_email",
                "An account already exists for this email.",
                status=409,
            ) from error
        return token

    def _consume_token(self, token: str, kind: str) -> sqlite3.Row:
        if not AUTH_TOKEN_RE.fullmatch(token):
            raise CommerceError("invalid_token", "This link is invalid.")
        now = int(time.time())
        with _database(self.path, write=True) as db:
            row = db.execute(
                """
                SELECT token_hash, user_id, expires_at_epoch, used_at_epoch
                FROM account_tokens WHERE token_hash = ? AND kind = ?
                """,
                (_token_hash(token), kind),
            ).fetchone()
            if row is None or row["used_at_epoch"] is not None:
                raise CommerceError("invalid_token", "This link is invalid.")
            if int(row["expires_at_epoch"]) <= now:
                raise CommerceError("expired_token", "This link has expired.")
            db.execute(
                "UPDATE account_tokens SET used_at_epoch = ? WHERE token_hash = ?",
                (now, row["token_hash"]),
            )
            return row

    def verify(self, token: str) -> None:
        row = self._consume_token(token, "verification")
        with _database(self.path, write=True) as db:
            db.execute("UPDATE users SET verified = 1 WHERE id = ?", (row["user_id"],))

    def forgot_password(self, email: str) -> str | None:
        try:
            normalized = self._normalized_email(email)
        except CommerceError:
            return None
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        with _database(self.path, write=True) as db:
            user = db.execute(
                "SELECT id FROM users WHERE email = ?", (normalized,)
            ).fetchone()
            if user is None:
                return None
            db.execute(
                """
                INSERT INTO account_tokens
                    (token_hash, kind, user_id, issued_at_epoch, expires_at_epoch)
                VALUES (?, 'reset', ?, ?, ?)
                """,
                (_token_hash(token), user["id"], now, now + RESET_SECONDS),
            )
        return token

    def reset_password(self, token: str, password: str, confirm: str) -> None:
        self._validate_password(password, confirm)
        row = self._consume_token(token, "reset")
        salt, digest = _password_hash(password)
        with _database(self.path, write=True) as db:
            devices = db.execute(
                "SELECT device_session_id FROM auth_sessions WHERE user_id = ?",
                (row["user_id"],),
            ).fetchall()
            db.execute(
                "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                (salt, digest, row["user_id"]),
            )
            db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (row["user_id"],))
            for device in devices:
                self._detach_device(db, str(device["device_session_id"]))

    @staticmethod
    def _device_exists(db: sqlite3.Connection, device_session_id: str) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (device_session_id,)
            ).fetchone()
            is not None
        )

    def login(self, email: str, password: str, *, device: str) -> str:
        device_session_id = device
        normalized = self._normalized_email(email)
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        with _database(self.path, write=True) as db:
            if not self._device_exists(db, device_session_id):
                raise CommerceError(
                    "session_required", "Reload the page and try again.", status=403
                )
            user = db.execute(
                """
                SELECT id, email, password_salt, password_hash, verified, full_name
                FROM users WHERE email = ?
                """,
                (normalized,),
            ).fetchone()
            if user is None or not _password_matches(
                password, str(user["password_salt"]), str(user["password_hash"])
            ):
                raise CommerceError(
                    "invalid_login", "Email or password is incorrect.", status=401
                )
            if not bool(user["verified"]):
                raise CommerceError(
                    "unverified_login",
                    "Verify your email before signing in.",
                    status=403,
                )
            user_id = str(user["id"])
            self._merge_device_state(db, device_session_id, user_id)
            db.execute(
                """
                UPDATE sessions
                SET user_id = ?, account_email = ?, signed_in = 1
                WHERE id = ?
                """,
                (user_id, normalized, device_session_id),
            )
            db.execute(
                """
                INSERT INTO auth_sessions
                    (token_hash, user_id, device_session_id, created_at_epoch, expires_at_epoch)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(token),
                    user_id,
                    device_session_id,
                    now,
                    now + SESSION_SECONDS,
                ),
            )
        return token

    @staticmethod
    def _merge_device_state(
        db: sqlite3.Connection, device_session_id: str, user_id: str
    ) -> None:
        now = utc_now()
        guest = db.execute(
            "SELECT asin, quantity FROM cart WHERE session_id = ?",
            (device_session_id,),
        ).fetchall()
        for row in guest:
            existing = db.execute(
                "SELECT quantity FROM account_cart WHERE user_id = ? AND asin = ?",
                (user_id, row["asin"]),
            ).fetchone()
            quantity = min(
                MAX_QUANTITY,
                int(row["quantity"]) + (int(existing["quantity"]) if existing else 0),
            )
            db.execute(
                """
                INSERT INTO account_cart (user_id, asin, quantity, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, asin) DO UPDATE SET
                    quantity = excluded.quantity,
                    updated_at = excluded.updated_at
                """,
                (user_id, row["asin"], quantity, now),
            )
        db.execute("DELETE FROM cart WHERE session_id = ?", (device_session_id,))
        db.execute(
            """
            INSERT INTO cart (session_id, asin, quantity, updated_at)
            SELECT ?, asin, quantity, ? FROM account_cart WHERE user_id = ?
            """,
            (device_session_id, now, user_id),
        )

        for table, account_table, time_column in (
            ("saved", "account_saved", "saved_at"),
            ("wishlist", "account_wishlist", "added_at"),
        ):
            db.execute(
                f"DELETE FROM {table} WHERE session_id = ?", (device_session_id,)
            )
            if table == "saved":
                db.execute(
                    """
                    INSERT INTO saved (session_id, asin, quantity, saved_at)
                    SELECT ?, asin, quantity, ? FROM account_saved WHERE user_id = ?
                    """,
                    (device_session_id, now, user_id),
                )
            else:
                epoch = time.time()
                db.execute(
                    """
                    INSERT INTO wishlist
                        (session_id, asin, added_at, added_at_epoch)
                    SELECT ?, asin, ?, ? FROM account_wishlist WHERE user_id = ?
                    """,
                    (device_session_id, now, epoch, user_id),
                )

    def user_for_session(
        self, token: str | None, *, device_session_id: str | None = None
    ) -> dict[str, Any] | None:
        if not token or not AUTH_TOKEN_RE.fullmatch(token):
            return None
        now = int(time.time())
        with _database(self.path, write=False) as db:
            row = db.execute(
                """
                SELECT u.id, u.email, u.full_name, a.device_session_id
                FROM auth_sessions a JOIN users u ON u.id = a.user_id
                WHERE a.token_hash = ? AND a.expires_at_epoch > ?
                """,
                (_token_hash(token), now),
            ).fetchone()
            if row is None:
                return None
            if device_session_id and row["device_session_id"] != device_session_id:
                return None
            return {
                "id": str(row["id"]),
                "email": str(row["email"]),
                "full_name": str(row["full_name"]),
            }

    def reconcile_session(
        self, device_session_id: str | None, auth_token: str | None
    ) -> dict[str, Any] | None:
        if not device_session_id:
            return None
        user = self.user_for_session(auth_token, device_session_id=device_session_id)
        with _database(self.path, write=True) as db:
            session = db.execute(
                "SELECT user_id FROM sessions WHERE id = ?", (device_session_id,)
            ).fetchone()
            if session is None:
                return None
            if user is None and session["user_id"] is not None:
                self._detach_device(db, device_session_id)
            elif user is not None and session["user_id"] != user["id"]:
                self._merge_device_state(db, device_session_id, user["id"])
                db.execute(
                    """
                    UPDATE sessions
                    SET user_id = ?, account_email = ?, signed_in = 1
                    WHERE id = ?
                    """,
                    (user["id"], user["email"], device_session_id),
                )
        return user

    @staticmethod
    def _detach_device(db: sqlite3.Connection, device_session_id: str) -> None:
        db.execute(
            """
            UPDATE sessions
            SET user_id = NULL, account_email = NULL, signed_in = 0
            WHERE id = ?
            """,
            (device_session_id,),
        )
        db.execute("DELETE FROM cart WHERE session_id = ?", (device_session_id,))
        db.execute("DELETE FROM saved WHERE session_id = ?", (device_session_id,))
        db.execute("DELETE FROM wishlist WHERE session_id = ?", (device_session_id,))

    def logout(self, token: str | None, *, device: str | None = None) -> None:
        device_session_id = device
        if not device_session_id:
            return
        with _database(self.path, write=True) as db:
            if token and AUTH_TOKEN_RE.fullmatch(token):
                db.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ? AND device_session_id = ?",
                    (_token_hash(token), device_session_id),
                )
            self._detach_device(db, device_session_id)

    def csrf_token(self, device_session_id: str | None) -> str | None:
        if not device_session_id:
            return None
        with _database(self.path, write=True) as db:
            row = db.execute(
                "SELECT csrf_token FROM sessions WHERE id = ?", (device_session_id,)
            ).fetchone()
            if row is None:
                return None
            token = row["csrf_token"]
            if not token:
                token = secrets.token_hex(24)
                db.execute(
                    "UPDATE sessions SET csrf_token = ? WHERE id = ?",
                    (token, device_session_id),
                )
            return str(token)

    def require_csrf(self, device_session_id: str | None, supplied: str) -> None:
        expected = self.csrf_token(device_session_id)
        if not expected or not hmac.compare_digest(expected, supplied):
            raise CommerceError(
                "invalid_csrf", "Reload the page and try again.", status=403
            )

    def checkout(
        self,
        *,
        user: Mapping[str, Any] | None,
        device_session_id: str,
        idempotency_key: str,
        card_number: str,
        full_name: str,
        address_line: str,
        city: str,
        postal_code: str,
    ) -> dict[str, Any]:
        if user is None:
            raise CommerceError(
                "login_required", "Sign in before checkout.", status=401
            )
        values = {
            "full_name": full_name.strip(),
            "address_line": address_line.strip(),
            "city": city.strip(),
            "postal_code": postal_code.strip(),
        }
        if any(not value or len(value) > 160 for value in values.values()):
            raise CommerceError("invalid_address", "Complete the shipping address.")
        key = idempotency_key.strip()
        if not key or len(key) > 128:
            raise CommerceError("invalid_idempotency", "Reload checkout and try again.")
        normalized_card = re.sub(r"\s+", "", card_number)
        if not re.fullmatch(r"\d{12,19}", normalized_card):
            raise CommerceError(
                "invalid_payment", "Enter a valid local test card number."
            )
        if normalized_card.endswith("0002"):
            raise CommerceError(
                "payment_declined", "The local test payment was declined.", status=402
            )
        user_id = str(user["id"])
        now_epoch = int(time.time())
        with _database(self.path, write=True) as db:
            session = db.execute(
                "SELECT user_id FROM sessions WHERE id = ?", (device_session_id,)
            ).fetchone()
            if session is None or session["user_id"] != user_id:
                raise CommerceError(
                    "login_required", "Sign in before checkout.", status=401
                )
            existing = db.execute(
                "SELECT number FROM orders WHERE user_id = ? AND idempotency_key = ?",
                (user_id, key),
            ).fetchone()
            if existing:
                return self._order(db, str(existing["number"]), user_id)
            lines = db.execute(
                "SELECT asin, quantity FROM cart WHERE session_id = ? ORDER BY asin",
                (device_session_id,),
            ).fetchall()
            if not lines:
                raise CommerceError("empty_cart", "Your cart is empty.", status=409)
            subtotal = 0
            for line in lines:
                asin = str(line["asin"])
                product = self.products.get(asin)
                if product is None:
                    raise CommerceError(
                        "product_not_found", "A cart item is unavailable.", status=409
                    )
                stock = db.execute(
                    "SELECT available FROM inventory WHERE asin = ?", (asin,)
                ).fetchone()
                if stock is None or int(stock["available"]) < int(line["quantity"]):
                    raise CommerceError(
                        "insufficient_stock",
                        "Not enough inventory is available.",
                        status=409,
                    )
                subtotal += _price_cents(product) * int(line["quantity"])
            db.execute(
                "UPDATE commerce_meta SET value = value + 1 WHERE key = 'order_sequence'"
            )
            sequence = int(
                db.execute(
                    "SELECT value FROM commerce_meta WHERE key = 'order_sequence'"
                ).fetchone()["value"]
            )
            number = f"AMZ-{sequence:06d}"
            db.execute(
                """
                INSERT INTO orders
                    (number, user_id, status, placed_at, placed_at_epoch,
                     subtotal_cents, tax_cents, shipping_cents, total_cents,
                     full_name, address_line, city, postal_code, idempotency_key)
                VALUES (?, ?, 'placed', ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    number,
                    user_id,
                    utc_now(),
                    now_epoch,
                    subtotal,
                    subtotal,
                    values["full_name"],
                    values["address_line"],
                    values["city"],
                    values["postal_code"],
                    key,
                ),
            )
            for line in lines:
                asin = str(line["asin"])
                product = self.products[asin]
                quantity = int(line["quantity"])
                db.execute(
                    """
                    UPDATE inventory SET available = available - ?
                    WHERE asin = ? AND available >= ?
                    """,
                    (quantity, asin, quantity),
                )
                db.execute(
                    """
                    INSERT INTO order_items
                        (order_number, asin, title, quantity, unit_price_cents)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        number,
                        asin,
                        str(product.get("title", asin)),
                        quantity,
                        _price_cents(product),
                    ),
                )
            db.execute("DELETE FROM cart WHERE session_id = ?", (device_session_id,))
            db.execute("DELETE FROM account_cart WHERE user_id = ?", (user_id,))
            return self._order(db, number, user_id)

    @staticmethod
    def _order(db: sqlite3.Connection, number: str, user_id: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT * FROM orders WHERE number = ? AND user_id = ?",
            (number, user_id),
        ).fetchone()
        if row is None:
            raise CommerceError("order_not_found", "Order not found.", status=404)
        lines = db.execute(
            """
            SELECT asin, title, quantity, unit_price_cents
            FROM order_items WHERE order_number = ? ORDER BY rowid
            """,
            (number,),
        ).fetchall()
        return {
            "number": str(row["number"]),
            "user_id": str(row["user_id"]),
            "status": str(row["status"]),
            "placed_at": str(row["placed_at"]),
            "cancelled_at": row["cancelled_at"],
            "subtotal_cents": int(row["subtotal_cents"]),
            "tax_cents": int(row["tax_cents"]),
            "shipping_cents": int(row["shipping_cents"]),
            "total_cents": int(row["total_cents"]),
            "full_name": str(row["full_name"]),
            "address_line": str(row["address_line"]),
            "city": str(row["city"]),
            "postal_code": str(row["postal_code"]),
            "lines": [dict(line) for line in lines],
        }

    def orders_for(self, user_id: str) -> list[dict[str, Any]]:
        with _database(self.path, write=False) as db:
            numbers = db.execute(
                "SELECT number FROM orders WHERE user_id = ? ORDER BY placed_at_epoch DESC",
                (user_id,),
            ).fetchall()
            return [self._order(db, str(row["number"]), user_id) for row in numbers]

    def order_for(self, number: str, user_id: str) -> dict[str, Any]:
        if not ORDER_RE.fullmatch(number):
            raise CommerceError("order_not_found", "Order not found.", status=404)
        with _database(self.path, write=False) as db:
            return self._order(db, number, user_id)

    def cancel(self, number: str, user_id: str) -> dict[str, Any]:
        if not ORDER_RE.fullmatch(number):
            raise CommerceError("order_not_found", "Order not found.", status=404)
        now_epoch = int(time.time())
        with _database(self.path, write=True) as db:
            order = self._order(db, number, user_id)
            row = db.execute(
                "SELECT placed_at_epoch, resources_restored FROM orders WHERE number = ?",
                (number,),
            ).fetchone()
            if order["status"] == "cancelled":
                return order
            if now_epoch > int(row["placed_at_epoch"]) + 24 * 60 * 60:
                raise CommerceError(
                    "cancellation_closed",
                    "The cancellation window has closed.",
                    status=409,
                )
            if not bool(row["resources_restored"]):
                for line in order["lines"]:
                    db.execute(
                        "UPDATE inventory SET available = available + ? WHERE asin = ?",
                        (int(line["quantity"]), str(line["asin"])),
                    )
            db.execute(
                """
                UPDATE orders
                SET status = 'cancelled', cancelled_at = ?, resources_restored = 1
                WHERE number = ? AND user_id = ?
                """,
                (utc_now(), number, user_id),
            )
            return self._order(db, number, user_id)
