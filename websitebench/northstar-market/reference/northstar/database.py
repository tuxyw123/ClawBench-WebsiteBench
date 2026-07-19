"""SQLite schema, controlled clock, fixture reset, and normalized state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .security import hash_password, normalize_email


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  image_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  brand TEXT NOT NULL,
  description TEXT NOT NULL,
  category_id TEXT NOT NULL REFERENCES categories(id),
  tags_json TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
  compare_at_cents INTEGER,
  inventory INTEGER NOT NULL CHECK(inventory >= 0),
  rating_basis_points INTEGER NOT NULL,
  review_count INTEGER NOT NULL,
  featured_rank INTEGER NOT NULL,
  image_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  email_normalized TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  verified INTEGER NOT NULL DEFAULT 0,
  full_name TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK(kind IN ('verification', 'reset')),
  token_hash TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  issued_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);
CREATE TABLE IF NOT EXISTS registration_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email_normalized TEXT NOT NULL,
  device_key TEXT NOT NULL,
  accepted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS registration_email_time
  ON registration_attempts(email_normalized, accepted_at);
CREATE INDEX IF NOT EXISTS registration_device_time
  ON registration_attempts(device_key, accepted_at);
CREATE TABLE IF NOT EXISTS carts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_type TEXT NOT NULL CHECK(owner_type IN ('guest', 'account')),
  owner_key TEXT NOT NULL,
  UNIQUE(owner_type, owner_key)
);
CREATE TABLE IF NOT EXISTS cart_lines (
  cart_id INTEGER NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 5),
  PRIMARY KEY(cart_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  order_number TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK(status IN ('placed', 'cancelled')),
  placed_at INTEGER NOT NULL,
  cancelled_at INTEGER,
  subtotal_cents INTEGER NOT NULL,
  shipping_cents INTEGER NOT NULL,
  tax_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL,
  shipping_method TEXT NOT NULL CHECK(shipping_method IN ('standard', 'express')),
  shipping_address_json TEXT NOT NULL,
  card_last_four TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_lines (
  order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  title TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  unit_price_cents INTEGER NOT NULL,
  PRIMARY KEY(order_id, product_id)
);
CREATE TABLE IF NOT EXISTS idempotency_records (
  user_id TEXT NOT NULL REFERENCES users(id),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  order_id TEXT NOT NULL REFERENCES orders(id),
  PRIMARY KEY(user_id, idempotency_key)
);
"""


def parse_utc(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include UTC timezone")
    return int(parsed.astimezone(timezone.utc).timestamp())


def format_utc(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).isoformat().replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def meta(self, connection: sqlite3.Connection, key: str, default: str | None = None) -> str:
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            if default is None:
                raise RuntimeError(f"database has not been reset: missing {key}")
            return default
        return str(row["value"])

    def now(self, connection: sqlite3.Connection | None = None) -> int:
        if connection is not None:
            return int(self.meta(connection, "now"))
        with self.connect() as owned:
            return int(self.meta(owned, "now"))

    def reset(self, fixture: dict[str, Any], *, run_id: str, seed: int, now: str) -> None:
        if fixture["seed"] != seed:
            raise ValueError("fixture seed does not match reset seed")
        clock = parse_utc(now)
        with self.transaction(immediate=True) as connection:
            for table in (
                "idempotency_records",
                "order_lines",
                "orders",
                "cart_lines",
                "carts",
                "registration_attempts",
                "auth_tokens",
                "sessions",
                "users",
                "products",
                "categories",
                "meta",
            ):
                connection.execute(f"DELETE FROM {table}")
            connection.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                (
                    ("run_id", run_id),
                    ("seed", str(seed)),
                    ("now", str(clock)),
                    ("fixture_id", fixture["fixture_id"]),
                ),
            )
            for category in fixture["catalog"]["categories"]:
                connection.execute(
                    "INSERT INTO categories VALUES (?, ?, ?, ?, ?)",
                    (
                        category["id"],
                        category["slug"],
                        category["name"],
                        category["description"],
                        json.dumps(category["image"], sort_keys=True),
                    ),
                )
            for product in fixture["catalog"]["products"]:
                connection.execute(
                    """INSERT INTO products(
                        id, sku, slug, title, brand, description, category_id,
                        tags_json, price_cents, compare_at_cents, inventory,
                        rating_basis_points, review_count, featured_rank, image_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        product["id"],
                        product["sku"],
                        product["slug"],
                        product["title"],
                        product["brand"],
                        product["description"],
                        product["category_id"],
                        json.dumps(product["tags"], sort_keys=True),
                        product["price_cents"],
                        product["compare_at_cents"],
                        product["inventory"],
                        product["rating_basis_points"],
                        product["review_count"],
                        product["featured_rank"],
                        json.dumps(product["image"], sort_keys=True),
                    ),
                )
            for account in fixture["accounts"]:
                connection.execute(
                    """INSERT INTO users(
                        id, email, email_normalized, password_hash, verified, full_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        account["id"],
                        normalize_email(account["email"]),
                        normalize_email(account["email"]),
                        hash_password(account["password"]),
                        int(account["verified"]),
                        account["full_name"],
                        clock,
                    ),
                )

    def advance_clock(self, seconds: int) -> int:
        if not 0 <= seconds <= 2_678_400:
            raise ValueError("seconds must be between 0 and 2678400")
        with self.transaction(immediate=True) as connection:
            new_now = self.now(connection) + seconds
            connection.execute("UPDATE meta SET value = ? WHERE key = 'now'", (str(new_now),))
            return new_now

    def normalized_state(self) -> dict[str, Any]:
        with self.connect() as connection:
            users = [
                {
                    "id": row["id"],
                    "email": row["email_normalized"],
                    "verified": bool(row["verified"]),
                    "full_name": row["full_name"],
                }
                for row in connection.execute(
                    "SELECT id, email_normalized, verified, full_name FROM users ORDER BY id"
                )
            ]
            products = [
                {
                    "id": row["id"],
                    "sku": row["sku"],
                    "price_cents": row["price_cents"],
                    "inventory": row["inventory"],
                }
                for row in connection.execute(
                    "SELECT id, sku, price_cents, inventory FROM products ORDER BY id"
                )
            ]
            guest_carts: list[dict[str, Any]] = []
            account_carts: list[dict[str, Any]] = []
            for cart in connection.execute("SELECT id, owner_type, owner_key FROM carts ORDER BY owner_key"):
                lines = [
                    {"product_id": row["product_id"], "quantity": row["quantity"]}
                    for row in connection.execute(
                        "SELECT product_id, quantity FROM cart_lines WHERE cart_id = ? ORDER BY product_id",
                        (cart["id"],),
                    )
                ]
                if not lines:
                    continue
                if cart["owner_type"] == "guest":
                    guest_carts.append(
                        {
                            "device_key_digest": hashlib.sha256(
                                cart["owner_key"].encode("utf-8")
                            ).hexdigest(),
                            "lines": lines,
                        }
                    )
                else:
                    account_carts.append({"user_id": cart["owner_key"], "lines": lines})
            orders: list[dict[str, Any]] = []
            for order in connection.execute("SELECT * FROM orders ORDER BY placed_at, order_number"):
                lines = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT product_id, sku, title, quantity, unit_price_cents
                        FROM order_lines WHERE order_id = ? ORDER BY product_id""",
                        (order["id"],),
                    )
                ]
                orders.append(
                    {
                        "id": order["id"],
                        "order_number": order["order_number"],
                        "user_id": order["user_id"],
                        "status": order["status"],
                        "placed_at": format_utc(order["placed_at"]),
                        "cancelled_at": (
                            format_utc(order["cancelled_at"])
                            if order["cancelled_at"] is not None
                            else None
                        ),
                        "subtotal_cents": order["subtotal_cents"],
                        "shipping_cents": order["shipping_cents"],
                        "tax_cents": order["tax_cents"],
                        "total_cents": order["total_cents"],
                        "shipping_method": order["shipping_method"],
                        "shipping_address": json.loads(order["shipping_address_json"]),
                        "card_last_four": order["card_last_four"],
                        "lines": lines,
                    }
                )
            counters = {
                "sessions": connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "verification_tokens": connection.execute(
                    "SELECT COUNT(*) FROM auth_tokens WHERE kind = 'verification'"
                ).fetchone()[0],
                "reset_tokens": connection.execute(
                    "SELECT COUNT(*) FROM auth_tokens WHERE kind = 'reset'"
                ).fetchone()[0],
                "idempotency_records": connection.execute(
                    "SELECT COUNT(*) FROM idempotency_records"
                ).fetchone()[0],
            }
            return {
                "schema_version": "websitebench.state.v1",
                "run_id": self.meta(connection, "run_id"),
                "seed": int(self.meta(connection, "seed")),
                "now": format_utc(self.now(connection)),
                "users": users,
                "products": products,
                "guest_carts": guest_carts,
                "account_carts": account_carts,
                "orders": orders,
                "counters": counters,
            }

