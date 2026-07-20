#!/usr/bin/env python3
"""Production local backend for the Amazon public-retail replica."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from clawbench.amazon_contract import (  # noqa: E402
    load_amazon_runtime_contract,
)


RUNTIME = load_amazon_runtime_contract(REPO_ROOT)["runtime"]
STATIC_ROOT = ROOT / "static"
SITE_CATALOG_PATH = STATIC_ROOT / "site-catalog.json"
DEFAULT_DB = ROOT / "amazon.sqlite3"
SESSION_COOKIE = "amazon_local_session"
ALLOWED_BIND_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}
BEST_SELLERS_PATH = "/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
PRODUCT_PATH = "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
MOBILE_PRODUCT_PATH = "/gp/aw/d/B0874XN4D8"
CART_PATH = "/gp/cart/view.html"
TARGET_ASIN = "B0874XN4D8"
TERMINAL_PATHS = {
    "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance",
    "/cart/add-to-cart/ref=mw_dp_buy_crt",
}
APP_ROUTES = {
    "/",
    BEST_SELLERS_PATH,
    "/Best-Sellers/zgbs",
    "/Best-Sellers/zgbs/",
    PRODUCT_PATH,
    MOBILE_PRODUCT_PATH,
    CART_PATH,
    "/s",
    "/gp/goldbox",
    "/gp/goldbox/",
    "/account",
    "/account/orders",
    "/hz/wishlist",
    "/hz/wishlist/ls",
    "/hz/history",
    "/checkout",
    "/checkout/payment",
    "/buy-now",
    "/local-boundary",
}
COMPUTERS_CATEGORY_PATHS = {
    "/b",
    "/b/",
    "/Computers-Accessories/b",
    "/Computers-Accessories/b/",
    "/computers-pc-hardware-accessories-add-ons/b",
    "/computers-pc-hardware-accessories-add-ons/b/",
}
DISCOVERY_TTL_SECONDS = 30 * 60
SEARCH_HISTORY_LIMIT = 10
RECENT_VIEWS_LIMIT = 10
SUGGESTION_LIMIT = 8
MAX_BODY_BYTES = 16 * 1024
SESSION_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
ASIN_RE = re.compile(r"[A-Z0-9]{10}\Z")
GENERIC_ASIN_RE = re.compile(r"[A-Z0-9]{10,11}\Z")
GENERIC_PDP_RE = re.compile(r"/[^/]+/dp/([A-Z0-9]{10,11})/?\Z")
FORM_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")

PRODUCTS: list[dict[str, Any]] = [
    {
        "rank": 1,
        "asin": "B08HN37XC1",
        "title": "SANDISK 2TB Extreme Portable SSD (Old Model) - Up to 1050MB/s, USB-C, USB 3.2 Gen 2, IP65 Water and Dust Resistance",
        "short_title": "SANDISK 2TB Extreme Portable SSD",
        "rating": 4.6,
        "reviews": 91118,
        "price": 269.75,
        "old_price": 299.99,
        "bought": "10K+ bought in past month",
        "capacity": "2 TB",
        "color": "Black",
        "interface": "USB 3.2 Gen 2",
        "connectivity": "USB-C",
        "bullets": ["Up to 1050MB/s", "IP65 water and dust resistance"],
        "sprite_index": 0,
    },
    {
        "rank": 2,
        "asin": "B0874XN4D8",
        "title": "Samsung T7 Portable SSD, 1TB External Solid State Drive, Speeds Up to 1,050MB/s, USB 3.2 Gen 2, Reliable Storage for Gaming, Students, Professionals, MU-PC1T0T/AM, Gray",
        "short_title": "Samsung T7 Portable SSD, 1TB External Solid State Drive",
        "rating": 4.7,
        "reviews": 38068,
        "price": 219.99,
        "old_price": 274.99,
        "bought": "5K+ bought in past month",
        "capacity": "1 TB",
        "color": "Titan Gray",
        "interface": "USB 3.0",
        "connectivity": "USB",
        "bullets": [
            "MADE FOR THE MAKERS: Create, explore, and store with fast, durable portable storage.",
            "SHARE IDEAS IN A FLASH: PCIe NVMe technology supports read and write speeds up to 1,050/1,000 MB/s.",
            "ALWAYS MAKE THE SAVE: Compact design with capacity for working files, photographs, and game data.",
            "ADAPTS TO EVERY NEED: Broad compatibility across computers, phones, cameras, and consoles.",
            "HI RESOLUTION VIDEO RECORDING: Record high-resolution video directly to portable storage on supported devices.",
        ],
        "sprite_index": 1,
    },
    {
        "rank": 3,
        "asin": "B0CHFSWM2P",
        "title": "Samsung T9 Portable SSD 1TB, USB 3.2 Gen 2x2 External Solid State Drive, up to 2,000MB/s",
        "short_title": "Samsung T9 Portable SSD 1TB",
        "rating": 4.6,
        "reviews": 2888,
        "price": 249.00,
        "old_price": 289.99,
        "bought": "2K+ bought in past month",
        "capacity": "1 TB",
        "color": "Black",
        "interface": "USB 3.2 Gen 2x2",
        "connectivity": "USB-C",
        "bullets": ["Up to 2,000MB/s", "Dynamic thermal guard"],
        "sprite_index": 2,
    },
    {
        "rank": 4,
        "asin": "B0C5JQ68FY",
        "title": "SANDISK 1TB Portable SSD - Up to 800MB/s, USB-C, USB 3.2 Gen 2",
        "short_title": "SANDISK 1TB Portable SSD",
        "rating": 4.6,
        "reviews": 13477,
        "price": 139.77,
        "old_price": 159.99,
        "bought": "3K+ bought in past month",
        "capacity": "1 TB",
        "color": "Black",
        "interface": "USB 3.2 Gen 2",
        "connectivity": "USB-C",
        "bullets": ["Up to 800MB/s", "Compact portable design"],
        "sprite_index": 3,
    },
    {
        "rank": 5,
        "asin": "B0BGKXX9TK",
        "title": "SSK Portable SSD 500GB External Solid State Drive, up to 1050MB/s USB-C",
        "short_title": "SSK Portable SSD 500GB",
        "rating": 4.5,
        "reviews": 4560,
        "price": 78.62,
        "old_price": 89.99,
        "bought": "1K+ bought in past month",
        "capacity": "500 GB",
        "color": "Black",
        "interface": "USB 3.2 Gen 2",
        "connectivity": "USB-C",
        "bullets": ["Up to 1050MB/s", "Phone and computer compatible"],
        "sprite_index": 4,
    },
    {
        "rank": 6,
        "asin": "B08GV9M64L",
        "title": "SANDISK 1TB Extreme PRO Portable SSD - Up to 2000MB/s, USB-C, IP65",
        "short_title": "SANDISK 1TB Extreme PRO Portable SSD",
        "rating": 4.5,
        "reviews": 9874,
        "price": 183.45,
        "old_price": 229.99,
        "bought": "1K+ bought in past month",
        "capacity": "1 TB",
        "color": "Black",
        "interface": "USB 3.2 Gen 2x2",
        "connectivity": "USB-C",
        "bullets": ["Up to 2000MB/s", "Forged aluminum chassis"],
        "sprite_index": 5,
    },
]

# Amazon exposes these ranked drives under Computers & Accessories even though
# the broader storefront navigation calls the owning department Electronics.
# Keep both facets so search/refinement trajectories do not lose the six task
# products after selecting the source-equivalent category.
for _task_product in PRODUCTS:
    _task_product.setdefault("department", "Electronics")
    _task_product.setdefault("category", "Computers & Accessories")


def load_site_catalog(path: Path = SITE_CATALOG_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not load site catalog: {path}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("site-catalog.json must contain an object")
    products = payload.get("products")
    trending = payload.get("trendingSearches")
    if not isinstance(products, list) or not isinstance(trending, list):
        raise RuntimeError(
            "site-catalog.json must contain products and trendingSearches lists"
        )
    required = {
        "asin",
        "slug",
        "title",
        "short_title",
        "brand",
        "department",
        "category",
        "price",
        "rating",
        "reviews",
        "bullets",
    }
    seen: set[str] = set()
    for product in products:
        if not isinstance(product, dict) or required.difference(product):
            raise RuntimeError("site-catalog.json contains an incomplete product")
        asin = product.get("asin")
        if (
            not isinstance(asin, str)
            or not GENERIC_ASIN_RE.fullmatch(asin)
            or asin in seen
        ):
            raise RuntimeError(
                "site-catalog.json product ASINs must be unique and valid"
            )
        if not isinstance(product.get("bullets"), list):
            raise RuntimeError(f"site-catalog.json product {asin} has invalid bullets")
        seen.add(asin)
    if any(not isinstance(term, str) or not term.strip() for term in trending):
        raise RuntimeError(
            "site-catalog.json trendingSearches must contain non-empty strings"
        )
    return payload


SITE_CATALOG = load_site_catalog()
GENERIC_PRODUCTS: list[dict[str, Any]] = SITE_CATALOG["products"]
TRENDING_SEARCHES: tuple[str, ...] = tuple(SITE_CATALOG["trendingSearches"])
TASK_PRODUCT_INDEX = {product["asin"]: product for product in PRODUCTS}
GENERIC_PRODUCT_INDEX = {product["asin"]: product for product in GENERIC_PRODUCTS}
PRODUCT_INDEX = {**GENERIC_PRODUCT_INDEX, **TASK_PRODUCT_INDEX}
ALL_PRODUCTS = PRODUCTS + [
    product for product in GENERIC_PRODUCTS if product["asin"] not in TASK_PRODUCT_INDEX
]
BOUNDARY_KINDS = {
    "account",
    "list",
    "checkout",
    "payment",
    "buy-now",
    "delivery",
    "language",
    "returns",
    "service",
}


class RequestError(ValueError):
    def __init__(
        self,
        status: HTTPStatus,
        message: str,
        outcome: str = "rejected",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.outcome = outcome


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA synchronous = FULL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                delivery_label TEXT NOT NULL DEFAULT 'New York 10001',
                currency TEXT NOT NULL DEFAULT 'USD',
                signed_in INTEGER NOT NULL DEFAULT 0 CHECK (signed_in IN (0, 1)),
                user_id TEXT,
                account_email TEXT,
                csrf_token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS discovery (
                session_id TEXT NOT NULL,
                path TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('best_sellers', 'product')),
                asin TEXT,
                viewed_at TEXT NOT NULL,
                viewed_at_epoch REAL NOT NULL,
                PRIMARY KEY (session_id, path),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS cart (
                session_id TEXT NOT NULL,
                asin TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 3),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, asin),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS saved (
                session_id TEXT NOT NULL,
                asin TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 3),
                saved_at TEXT NOT NULL,
                PRIMARY KEY (session_id, asin),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS boundaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS request_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                asin TEXT,
                quantity INTEGER,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                query TEXT NOT NULL,
                normalized_query TEXT NOT NULL,
                searched_at TEXT NOT NULL,
                searched_at_epoch REAL NOT NULL,
                UNIQUE (session_id, normalized_query),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recent_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                asin TEXT NOT NULL,
                path TEXT NOT NULL,
                viewed_at TEXT NOT NULL,
                viewed_at_epoch REAL NOT NULL,
                UNIQUE (session_id, asin),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS wishlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                asin TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_at_epoch REAL NOT NULL,
                UNIQUE (session_id, asin),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_discovery_session_time
                ON discovery(session_id, viewed_at_epoch);
            CREATE INDEX IF NOT EXISTS idx_boundaries_session
                ON boundaries(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_request_journal_session
                ON request_journal(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_search_history_session_time
                ON search_history(session_id, searched_at_epoch DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_recent_views_session_time
                ON recent_views(session_id, viewed_at_epoch DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_wishlist_session_time
                ON wishlist(session_id, added_at_epoch DESC, id DESC);
            """
        )
        session_columns = {
            str(row[1]) for row in db.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "user_id" not in session_columns:
            db.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
        if "account_email" not in session_columns:
            db.execute("ALTER TABLE sessions ADD COLUMN account_email TEXT")
        if "csrf_token" not in session_columns:
            db.execute("ALTER TABLE sessions ADD COLUMN csrf_token TEXT")
        db.execute(
            "UPDATE sessions SET csrf_token = lower(hex(randomblob(24))) "
            "WHERE csrf_token IS NULL OR csrf_token = ''"
        )
        result = db.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"SQLite integrity check failed: {result}")


@contextmanager
def database(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=2, isolation_level=None)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 2000")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def add_journal(
    db: sqlite3.Connection,
    session_id: str,
    method: str,
    path: str,
    status: int,
    outcome: str,
    asin: str | None = None,
    quantity: int | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO request_journal
            (session_id, method, path, status, outcome, asin, quantity, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, method, path, status, outcome, asin, quantity, utc_now()),
    )


class AmazonThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], db_path: Path) -> None:
        self.db_path = db_path
        super().__init__(address, AmazonHandler)


class AmazonHandler(BaseHTTPRequestHandler):
    server_version = "AmazonLocalEvaluation/1.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    _pending_cookie: str | None = None

    @property
    def db_path(self) -> Path:
        return self.server.db_path  # type: ignore[attr-defined]

    def handle_one_request(self) -> None:
        self._pending_cookie = None
        super().handle_one_request()

    def send_common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' "
            "'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "frame-src 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        if self._pending_cookie:
            self.send_header("Set-Cookie", self._pending_cookie)
        if self.close_connection:
            self.send_header("Connection", "close")

    def send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_common_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()

    def send_json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self.command in {"POST", "PATCH", "DELETE"}:
            self.close_connection = True
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
        self.send_bytes(body, "application/json; charset=utf-8", status, headers)

    def send_request_error(self, error: RequestError) -> None:
        self.send_json(
            {"error": str(error), "outcome": error.outcome},
            error.status,
        )

    def send_storage_error(self) -> None:
        self.close_connection = True
        self.send_json(
            {
                "error": "Local cart storage is temporarily unavailable. Try again.",
                "outcome": "storage_unavailable",
            },
            HTTPStatus.SERVICE_UNAVAILABLE,
            {"Retry-After": "1"},
        )

    def send_redirect(self, location: str) -> None:
        self.send_bytes(
            b"",
            "text/plain; charset=utf-8",
            HTTPStatus.SEE_OTHER,
            {"Location": location},
        )

    def _target(self) -> tuple[Any, str]:
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid request target.")
        try:
            path = unquote(parsed.path, errors="strict")
        except UnicodeDecodeError as error:
            raise RequestError(
                HTTPStatus.BAD_REQUEST, "Invalid request path."
            ) from error
        return parsed, path

    def _cookie_token(self) -> str | None:
        values = self.headers.get_all("Cookie", [])
        if not values:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load("; ".join(values))
        except CookieError:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        if morsel is None or not SESSION_TOKEN_RE.fullmatch(morsel.value):
            return None
        return morsel.value

    def _existing_session(self, db: sqlite3.Connection) -> str | None:
        token = self._cookie_token()
        if token is None:
            return None
        row = db.execute("SELECT id FROM sessions WHERE id = ?", (token,)).fetchone()
        return str(row["id"]) if row else None

    def _ensure_session(self, db: sqlite3.Connection) -> str:
        token = self._existing_session(db)
        now = utc_now()
        if token is not None:
            db.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
                (now, token),
            )
            return token

        for _ in range(4):
            token = secrets.token_urlsafe(32)
            try:
                db.execute(
                    """
                    INSERT INTO sessions (id, csrf_token, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (token, secrets.token_hex(24), now, now),
                )
            except sqlite3.IntegrityError:
                continue
            self._pending_cookie = (
                f"{SESSION_COOKIE}={token}; Path=/; Max-Age=31536000; "
                "HttpOnly; SameSite=Lax"
            )
            return token
        raise sqlite3.DatabaseError("Could not allocate a unique session")

    def _session_payload(
        self,
        db: sqlite3.Connection,
        session_id: str | None,
    ) -> dict[str, Any]:
        session = None
        if session_id is not None:
            session = db.execute(
                """
                SELECT delivery_label, currency, signed_in, account_email, csrf_token
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return {
            "delivery_label": (
                str(session["delivery_label"]) if session else "New York 10001"
            ),
            "currency": str(session["currency"]) if session else "USD",
            "signed_in": bool(session["signed_in"]) if session else False,
            "email": str(session["account_email"]) if session and session["account_email"] else None,
            "display_name": (
                str(session["account_email"]).split("@", 1)[0]
                if session and session["account_email"]
                else None
            ),
            "csrf_token": str(session["csrf_token"]) if session else None,
            "language": "en-US",
        }

    def _wishlist_payload(
        self,
        db: sqlite3.Connection,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        if session_id is None:
            return []
        rows = db.execute(
            """
            SELECT asin, added_at
            FROM wishlist
            WHERE session_id = ?
            ORDER BY added_at_epoch DESC, id DESC
            """,
            (session_id,),
        ).fetchall()
        return [
            {
                "asin": str(row["asin"]),
                "added_at": str(row["added_at"]),
                "product": PRODUCT_INDEX[str(row["asin"])],
            }
            for row in rows
            if str(row["asin"]) in PRODUCT_INDEX
        ]

    def _recent_views_payload(
        self,
        db: sqlite3.Connection,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        if session_id is None:
            return []
        rows = db.execute(
            """
            SELECT asin, path, viewed_at
            FROM recent_views
            WHERE session_id = ?
            ORDER BY viewed_at_epoch DESC, id DESC
            LIMIT ?
            """,
            (session_id, RECENT_VIEWS_LIMIT),
        ).fetchall()
        return [
            {
                "asin": str(row["asin"]),
                "path": str(row["path"]),
                "viewed_at": str(row["viewed_at"]),
                "product": GENERIC_PRODUCT_INDEX[str(row["asin"])],
            }
            for row in rows
            if str(row["asin"]) in GENERIC_PRODUCT_INDEX
        ]

    def _search_history_payload(
        self,
        db: sqlite3.Connection,
        session_id: str | None,
    ) -> list[dict[str, str]]:
        if session_id is None:
            return []
        rows = db.execute(
            """
            SELECT query, searched_at
            FROM search_history
            WHERE session_id = ?
            ORDER BY searched_at_epoch DESC, id DESC
            LIMIT ?
            """,
            (session_id, SEARCH_HISTORY_LIMIT),
        ).fetchall()
        return [
            {"query": str(row["query"]), "searched_at": str(row["searched_at"])}
            for row in rows
        ]

    def _bootstrap(
        self,
        db: sqlite3.Connection,
        session_id: str | None,
    ) -> dict[str, Any]:
        cart_rows = []
        saved_rows = []
        discovery_rows = []
        if session_id is not None:
            cart_rows = db.execute(
                "SELECT asin, quantity FROM cart WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            saved_rows = db.execute(
                "SELECT asin, quantity FROM saved WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            discovery_rows = db.execute(
                """
                SELECT path, kind, asin
                FROM discovery
                WHERE session_id = ? AND viewed_at_epoch >= ?
                """,
                (session_id, time.time() - DISCOVERY_TTL_SECONDS),
            ).fetchall()

        product_order = {
            product["asin"]: index for index, product in enumerate(ALL_PRODUCTS)
        }
        cart_rows = sorted(
            cart_rows,
            key=lambda row: product_order.get(row["asin"], len(product_order)),
        )
        saved_rows = sorted(
            saved_rows,
            key=lambda row: product_order.get(row["asin"], len(product_order)),
        )

        items = []
        for row in cart_rows:
            product = PRODUCT_INDEX.get(str(row["asin"]))
            if product is None:
                continue
            quantity = int(row["quantity"])
            items.append(
                {
                    "asin": product["asin"],
                    "quantity": quantity,
                    "subtotal": round(float(product["price"]) * quantity, 2),
                    "product": product,
                }
            )

        saved_items = []
        for row in saved_rows:
            product = PRODUCT_INDEX.get(str(row["asin"]))
            if product is not None:
                saved_items.append(
                    {
                        "asin": product["asin"],
                        "quantity": int(row["quantity"]),
                        "product": product,
                    }
                )

        viewed_asins = {
            str(row["asin"])
            for row in discovery_rows
            if row["kind"] == "product" and row["asin"] in PRODUCT_INDEX
        }
        product_views = [
            product["asin"] for product in PRODUCTS if product["asin"] in viewed_asins
        ]
        return {
            "session": self._session_payload(db, session_id),
            "products": PRODUCTS,
            "cart": {
                "items": items,
                "total_quantity": sum(item["quantity"] for item in items),
                "subtotal": round(sum(item["subtotal"] for item in items), 2),
            },
            "saved_for_later": saved_items,
            "discovery": {
                "best_sellers_viewed": any(
                    row["kind"] == "best_sellers" for row in discovery_rows
                ),
                "product_views": product_views,
            },
            "wishlist": self._wishlist_payload(db, session_id),
            "recent_views": self._recent_views_payload(db, session_id),
            "search_history": self._search_history_payload(db, session_id),
        }

    def _record_discovery(
        self,
        db: sqlite3.Connection,
        session_id: str,
        path: str,
    ) -> None:
        kind = "best_sellers" if path == BEST_SELLERS_PATH else "product"
        asin = TARGET_ASIN if kind == "product" else None
        now = utc_now()
        db.execute(
            """
            INSERT INTO discovery
                (session_id, path, kind, asin, viewed_at, viewed_at_epoch)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, path) DO UPDATE SET
                kind = excluded.kind,
                asin = excluded.asin,
                viewed_at = excluded.viewed_at,
                viewed_at_epoch = excluded.viewed_at_epoch
            """,
            (session_id, path, kind, asin, now, time.time()),
        )

    def _record_search_history(
        self,
        db: sqlite3.Connection,
        session_id: str,
        query: str,
    ) -> None:
        normalized = query.casefold()
        now = utc_now()
        now_epoch = time.time()
        db.execute(
            """
            INSERT INTO search_history
                (session_id, query, normalized_query, searched_at, searched_at_epoch)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, normalized_query) DO UPDATE SET
                query = excluded.query,
                searched_at = excluded.searched_at,
                searched_at_epoch = excluded.searched_at_epoch
            """,
            (session_id, query, normalized, now, now_epoch),
        )
        db.execute(
            """
            DELETE FROM search_history
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM search_history
                WHERE session_id = ?
                ORDER BY searched_at_epoch DESC, id DESC
                LIMIT ?
            )
            """,
            (session_id, session_id, SEARCH_HISTORY_LIMIT),
        )

    def _record_recent_view(
        self,
        db: sqlite3.Connection,
        session_id: str,
        asin: str,
        path: str,
    ) -> None:
        now = utc_now()
        now_epoch = time.time()
        db.execute(
            """
            INSERT INTO recent_views
                (session_id, asin, path, viewed_at, viewed_at_epoch)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, asin) DO UPDATE SET
                path = excluded.path,
                viewed_at = excluded.viewed_at,
                viewed_at_epoch = excluded.viewed_at_epoch
            """,
            (session_id, asin, path, now, now_epoch),
        )
        db.execute(
            """
            DELETE FROM recent_views
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM recent_views
                WHERE session_id = ?
                ORDER BY viewed_at_epoch DESC, id DESC
                LIMIT ?
            )
            """,
            (session_id, session_id, RECENT_VIEWS_LIMIT),
        )

    def _validate_origin(self) -> None:
        origins = self.headers.get_all("Origin", [])
        if not origins:
            return
        hosts = self.headers.get_all("Host", [])
        if len(origins) != 1 or len(hosts) != 1:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.FORBIDDEN, "Origin is not allowed.", "bad_origin"
            )
        try:
            origin = urlsplit(origins[0])
            host = urlsplit(f"//{hosts[0]}")
            origin_port = origin.port or 80
            host_port = host.port or 80
        except ValueError as error:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.FORBIDDEN,
                "Origin is not allowed.",
                "bad_origin",
            ) from error
        valid = (
            origin.scheme.lower() == "http"
            and bool(origin.hostname)
            and bool(host.hostname)
            and origin.username is None
            and origin.password is None
            and host.username is None
            and host.password is None
            and origin.path in {"", "/"}
            and not origin.query
            and not origin.fragment
            and origin.hostname.casefold() == host.hostname.casefold()
            and origin_port == host_port
        )
        if not valid:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.FORBIDDEN, "Origin is not allowed.", "bad_origin"
            )

    def _content_type(self, expected: str) -> None:
        values = self.headers.get_all("Content-Type", [])
        if len(values) != 1:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                f"Content-Type must be {expected}.",
                "unsupported_content_type",
            )
        pieces = [piece.strip() for piece in values[0].split(";")]
        if not pieces or pieces[0].lower() != expected:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                f"Content-Type must be {expected}.",
                "unsupported_content_type",
            )
        for parameter in pieces[1:]:
            if "=" not in parameter:
                self.close_connection = True
                raise RequestError(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Malformed Content-Type.",
                    "unsupported_content_type",
                )
            name, value = parameter.split("=", 1)
            if (
                name.strip().lower() != "charset"
                or value.strip(' \t"').lower() != "utf-8"
            ):
                self.close_connection = True
                raise RequestError(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "Only UTF-8 request bodies are supported.",
                    "unsupported_content_type",
                )

    def _read_body(self, *, required: bool) -> bytes:
        if self.headers.get_all("Transfer-Encoding", []):
            self.close_connection = True
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Transfer-Encoding is not supported.",
                "invalid_body",
            )
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            if required:
                raise RequestError(
                    HTTPStatus.LENGTH_REQUIRED,
                    "Content-Length is required.",
                    "invalid_body",
                )
            return b""
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0].strip()):
            self.close_connection = True
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Invalid Content-Length.",
                "invalid_body",
            )
        length = int(lengths[0])
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"Request body exceeds {MAX_BODY_BYTES} bytes.",
                "body_too_large",
            )
        if required and length == 0:
            raise RequestError(
                HTTPStatus.BAD_REQUEST, "Request body is required.", "invalid_body"
            )
        body = self.rfile.read(length)
        if len(body) != length:
            self.close_connection = True
            raise RequestError(
                HTTPStatus.BAD_REQUEST, "Incomplete request body.", "invalid_body"
            )
        return body

    def _read_json(self) -> dict[str, Any]:
        self._content_type("application/json")
        body = self._read_body(required=True)

        def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise RequestError(
                        HTTPStatus.BAD_REQUEST,
                        f"Duplicate JSON field: {key}.",
                        "duplicate_field",
                    )
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"Invalid JSON constant: {value}")

        try:
            payload = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=object_pairs,
                parse_constant=reject_constant,
            )
        except RequestError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Malformed JSON body.",
                "malformed_json",
            ) from error
        if not isinstance(payload, dict):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "JSON body must be an object.",
                "malformed_json",
            )
        return payload

    def _read_form(self) -> dict[str, str]:
        self._content_type("application/x-www-form-urlencoded")
        body = self._read_body(required=True)
        try:
            encoded = body.decode("ascii", errors="strict")
            if FORM_PERCENT_RE.search(encoded):
                raise ValueError("invalid percent escape")
            pairs = parse_qsl(
                encoded,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=16,
                separator="&",
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Malformed form body.",
                "malformed_form",
            ) from error
        fields: dict[str, str] = {}
        for name, value in pairs:
            if not name:
                raise RequestError(
                    HTTPStatus.BAD_REQUEST,
                    "Form field names may not be empty.",
                    "malformed_form",
                )
            if name in fields:
                raise RequestError(
                    HTTPStatus.BAD_REQUEST,
                    f"Duplicate form field: {name}.",
                    "duplicate_field",
                )
            fields[name] = value
        return fields

    def _require_empty_body(self) -> None:
        if self._read_body(required=False):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "This endpoint does not accept a request body.",
                "invalid_body",
            )

    def _single_query_parameter(
        self,
        parsed: Any,
        name: str,
        *,
        max_length: int,
    ) -> str:
        try:
            if FORM_PERCENT_RE.search(parsed.query):
                raise ValueError("invalid percent escape")
            pairs = parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=4,
                separator="&",
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Malformed query string.",
                "invalid_query",
            ) from error
        if len(pairs) != 1 or pairs[0][0] != name:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                f"Query string must contain exactly one {name} parameter.",
                "invalid_query",
            )
        value = pairs[0][1]
        if len(value) > max_length:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                f"Query parameter {name} must be at most {max_length} characters.",
                "invalid_query",
            )
        return " ".join(value.split())

    def _product_search_text(self, product: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("title", "short_title", "brand", "department", "category"):
            value = product.get(key)
            if isinstance(value, str):
                parts.append(value)
        bullets = product.get("bullets")
        if isinstance(bullets, list):
            parts.extend(value for value in bullets if isinstance(value, str))
        specs = product.get("specs")
        if isinstance(specs, dict):
            parts.extend(str(value) for value in specs.values())
        return " ".join(parts).casefold()

    def _suggestions(
        self,
        db: sqlite3.Connection,
        session_id: str | None,
        query: str,
    ) -> list[str]:
        folded = query.casefold()
        terms = folded.split()
        values: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            key = " ".join(value.split()).casefold()
            if key and key not in seen and len(values) < SUGGESTION_LIMIT:
                seen.add(key)
                values.append(value)

        if session_id is not None:
            rows = db.execute(
                """
                SELECT query
                FROM search_history
                WHERE session_id = ?
                ORDER BY searched_at_epoch DESC, id DESC
                LIMIT ?
                """,
                (session_id, SEARCH_HISTORY_LIMIT),
            ).fetchall()
            for row in rows:
                value = str(row["query"])
                if not folded or folded in value.casefold():
                    add(value)

        for value in TRENDING_SEARCHES:
            if not folded or folded in value.casefold():
                add(value)

        for product in ALL_PRODUCTS:
            if not terms or all(
                term in self._product_search_text(product) for term in terms
            ):
                add(str(product["title"]))
        return values

    def _generic_pdp_asin(self, path: str) -> str | None:
        match = GENERIC_PDP_RE.fullmatch(path)
        if not match or match.group(1) not in GENERIC_PRODUCT_INDEX:
            return None
        return match.group(1)

    def _send_index(self, status: HTTPStatus) -> None:
        try:
            body = (STATIC_ROOT / "index.html").read_bytes()
        except OSError:
            self.send_bytes(
                b"Application unavailable.\n",
                "text/plain; charset=utf-8",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self.send_bytes(body, "text/html; charset=utf-8", status)

    def _serve_static(self, path: str) -> None:
        relative = path.removeprefix("/static/")
        try:
            candidate = (STATIC_ROOT / relative).resolve()
            candidate.relative_to(STATIC_ROOT.resolve())
            if not candidate.is_file():
                raise FileNotFoundError
            body = candidate.read_bytes()
        except (OSError, ValueError):
            self.send_bytes(
                b"Not found.\n",
                "text/plain; charset=utf-8",
                HTTPStatus.NOT_FOUND,
            )
            return
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_bytes(body, mime)

    def _handle_get(self, *, mutate: bool) -> None:
        parsed, path = self._target()
        if path == "/api/bootstrap":
            if parsed.query:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.", "not_found")
            with database(self.db_path, write=mutate) as db:
                session_id = (
                    self._ensure_session(db) if mutate else self._existing_session(db)
                )
                payload = self._bootstrap(db, session_id)
            self.send_json(payload)
            return
        if path == "/api/suggestions":
            query = self._single_query_parameter(parsed, "q", max_length=80)
            with database(self.db_path, write=mutate) as db:
                session_id = (
                    self._ensure_session(db) if mutate else self._existing_session(db)
                )
                suggestions = self._suggestions(db, session_id, query)
            self.send_json({"suggestions": suggestions})
            return
        if path == "/api/search":
            try:
                params = parse_qs(
                    parsed.query,
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=8,
                    encoding="utf-8",
                    errors="strict",
                )
            except (UnicodeDecodeError, ValueError) as error:
                raise RequestError(
                    HTTPStatus.BAD_REQUEST,
                    "Malformed search query.",
                    "invalid_query",
                ) from error
            if set(params).difference({"k"}) or len(params.get("k", [])) > 1:
                raise RequestError(
                    HTTPStatus.BAD_REQUEST,
                    "Search accepts at most one k parameter.",
                    "invalid_query",
                )
            raw_query = params.get("k", [""])[0]
            query = " ".join(raw_query.split())[:160]
            terms = [term for term in query.casefold().split() if term]
            if terms:
                products = [
                    product
                    for product in ALL_PRODUCTS
                    if all(term in self._product_search_text(product) for term in terms)
                ]
            else:
                products = PRODUCTS
            if mutate:
                with database(self.db_path, write=True) as db:
                    session_id = self._ensure_session(db)
                    if query:
                        self._record_search_history(db, session_id, query)
            self.send_json(
                {"query": query, "products": products, "count": len(products)}
            )
            return
        if path == "/api/list":
            if parsed.query:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.", "not_found")
            with database(self.db_path, write=mutate) as db:
                session_id = (
                    self._ensure_session(db) if mutate else self._existing_session(db)
                )
                items = self._wishlist_payload(db, session_id)
            self.send_json({"items": items, "count": len(items)})
            return
        if path.startswith("/api/"):
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        if path.startswith("/static/"):
            self._serve_static(path)
            return
        if path in {BEST_SELLERS_PATH, PRODUCT_PATH, MOBILE_PRODUCT_PATH} and mutate:
            with database(self.db_path, write=True) as db:
                session_id = self._ensure_session(db)
                self._record_discovery(db, session_id, path)
        generic_asin = self._generic_pdp_asin(path)
        if generic_asin is not None and mutate:
            with database(self.db_path, write=True) as db:
                session_id = self._ensure_session(db)
                self._record_recent_view(db, session_id, generic_asin, path)
        if (
            path in APP_ROUTES
            or path in COMPUTERS_CATEGORY_PATHS
            or generic_asin is not None
        ):
            self._send_index(HTTPStatus.OK)
        else:
            self._send_index(HTTPStatus.NOT_FOUND)

    def _terminal_add(self, path: str) -> None:
        self._validate_origin()
        fields = self._read_form()
        allowed = {
            "ASIN",
            "quantity",
            "submit.add-to-cart",
            "offerListingID",
            "session-id",
        }
        unknown = set(fields).difference(allowed)
        if unknown:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                f"Unknown form field: {sorted(unknown)[0]}.",
                "unknown_field",
            )
        if "ASIN" not in fields or "quantity" not in fields:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "ASIN and quantity are required.",
                "missing_field",
            )
        asin = fields["ASIN"]
        quantity_text = fields["quantity"]
        if not ASIN_RE.fullmatch(asin) or asin not in TASK_PRODUCT_INDEX:
            raise RequestError(HTTPStatus.NOT_FOUND, "Unknown ASIN.", "unknown_asin")
        if not re.fullmatch(r"[1-3]", quantity_text):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Quantity must be an integer from 1 to 3.",
                "invalid_quantity",
            )
        quantity = int(quantity_text)
        status = HTTPStatus.SEE_OTHER
        outcome = "terminal_added" if quantity == 2 else "cart_added_non_task_quantity"
        now_epoch = time.time()
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            if asin != TARGET_ASIN:
                status = HTTPStatus.FORBIDDEN
                outcome = "undiscovered_product"
            else:
                rows = db.execute(
                    """
                    SELECT path, viewed_at_epoch
                    FROM discovery
                    WHERE session_id = ? AND path IN (?, ?, ?)
                    """,
                    (
                        session_id,
                        BEST_SELLERS_PATH,
                        PRODUCT_PATH,
                        MOBILE_PRODUCT_PATH,
                    ),
                ).fetchall()
                viewed = {
                    str(row["path"]): float(row["viewed_at_epoch"]) for row in rows
                }
                product_view_times = [
                    viewed[product_path]
                    for product_path in (PRODUCT_PATH, MOBILE_PRODUCT_PATH)
                    if product_path in viewed
                ]
                if BEST_SELLERS_PATH not in viewed or not product_view_times:
                    status = HTTPStatus.FORBIDDEN
                    outcome = "discovery_required"
                elif any(
                    timestamp < now_epoch - DISCOVERY_TTL_SECONDS
                    or timestamp > now_epoch + 1
                    for timestamp in (
                        viewed[BEST_SELLERS_PATH],
                        max(product_view_times),
                    )
                ):
                    status = HTTPStatus.FORBIDDEN
                    outcome = "discovery_stale"
                elif max(product_view_times) < viewed[BEST_SELLERS_PATH]:
                    status = HTTPStatus.FORBIDDEN
                    outcome = "discovery_order_invalid"

            if status == HTTPStatus.SEE_OTHER and quantity == 2:
                duplicate = db.execute(
                    """
                    SELECT 1 FROM request_journal
                    WHERE session_id = ? AND outcome = 'terminal_added'
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                if duplicate is not None:
                    status = HTTPStatus.CONFLICT
                    outcome = "duplicate_terminal"

            if status == HTTPStatus.SEE_OTHER:
                now = utc_now()
                db.execute(
                    """
                    INSERT INTO cart (session_id, asin, quantity, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, asin) DO UPDATE SET
                        quantity = excluded.quantity,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, asin, quantity, now),
                )
                db.execute(
                    "DELETE FROM saved WHERE session_id = ? AND asin = ?",
                    (session_id, asin),
                )
            add_journal(
                db,
                session_id,
                "POST",
                path,
                int(status),
                outcome,
                asin,
                quantity,
            )

        if status == HTTPStatus.SEE_OTHER:
            self.send_redirect(CART_PATH)
        else:
            self.send_request_error(
                RequestError(
                    status,
                    (
                        "This task completion was already recorded."
                        if outcome == "duplicate_terminal"
                        else "View Best Sellers, then the current target product, before adding it."
                    ),
                    outcome,
                )
            )

    def _catalog_asin(self, value: Any) -> str:
        if not isinstance(value, str) or not GENERIC_ASIN_RE.fullmatch(value):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "ASIN must be an uppercase catalog identifier.",
                "invalid_asin",
            )
        if value not in PRODUCT_INDEX:
            raise RequestError(HTTPStatus.NOT_FOUND, "Unknown ASIN.", "unknown_asin")
        return value

    def _generic_cart_add(self, path: str) -> None:
        self._validate_origin()
        payload = self._read_json()
        if set(payload) != {"asin", "quantity"}:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "JSON body must contain exactly asin and quantity.",
                "invalid_cart_add",
            )
        asin = self._catalog_asin(payload.get("asin"))
        quantity = payload.get("quantity")
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity not in {1, 2, 3}
        ):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Quantity must be an integer from 1 to 3.",
                "invalid_quantity",
            )
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            now = utc_now()
            db.execute(
                """
                INSERT INTO cart (session_id, asin, quantity, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, asin) DO UPDATE SET
                    quantity = excluded.quantity,
                    updated_at = excluded.updated_at
                """,
                (session_id, asin, quantity, now),
            )
            db.execute(
                "DELETE FROM saved WHERE session_id = ? AND asin = ?",
                (session_id, asin),
            )
            add_journal(
                db,
                session_id,
                "POST",
                path,
                200,
                "generic_cart_upserted",
                asin,
                quantity,
            )
        product = PRODUCT_INDEX[asin]
        self.send_json(
            {
                "status": "ok",
                "outcome": "generic_cart_upserted",
                "item": {
                    "asin": asin,
                    "quantity": quantity,
                    "subtotal": round(float(product["price"]) * quantity, 2),
                    "product": product,
                },
            }
        )

    def _wishlist_add(self, path: str) -> None:
        self._validate_origin()
        payload = self._read_json()
        if set(payload) != {"asin"}:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "JSON body must contain exactly one asin field.",
                "invalid_wishlist",
            )
        asin = self._catalog_asin(payload.get("asin"))
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            now = utc_now()
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO wishlist
                    (session_id, asin, added_at, added_at_epoch)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, asin, now, time.time()),
            )
            outcome = "wishlist_added" if cursor.rowcount else "wishlist_unchanged"
            add_journal(db, session_id, "POST", path, 200, outcome, asin)
        self.send_json({"status": "ok", "outcome": outcome, "asin": asin})

    def _wishlist_delete(self, path: str, asin: str) -> None:
        self._validate_origin()
        self._require_empty_body()
        asin = self._catalog_asin(asin)
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            cursor = db.execute(
                "DELETE FROM wishlist WHERE session_id = ? AND asin = ?",
                (session_id, asin),
            )
            outcome = "wishlist_deleted" if cursor.rowcount else "wishlist_absent"
            add_journal(db, session_id, "DELETE", path, 200, outcome, asin)
        self.send_json({"status": "ok", "outcome": outcome, "asin": asin})

    def _session_preferences(self, path: str) -> None:
        self._validate_origin()
        payload = self._read_json()
        if set(payload) != {"kind"} or not isinstance(payload.get("kind"), str):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Preference body must contain exactly one string kind.",
                "invalid_preference",
            )
        kind = payload["kind"]
        if kind not in {"delivery", "language"}:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Preference kind must be delivery or language.",
                "invalid_preference",
            )
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            db.execute(
                """
                INSERT INTO boundaries (session_id, kind, path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, kind, path, utc_now()),
            )
            add_journal(
                db,
                session_id,
                "POST",
                path,
                200,
                "preference_boundary_no_effect",
            )
            session = self._session_payload(db, session_id)
        value = session["delivery_label"] if kind == "delivery" else session["language"]
        self.send_json(
            {
                "status": "local-no-effect",
                "kind": kind,
                "value": value,
                "session": session,
            }
        )

    def _cart_save_or_move(self, path: str, asin: str, *, move: bool) -> None:
        self._validate_origin()
        self._require_empty_body()
        if asin not in PRODUCT_INDEX:
            raise RequestError(HTTPStatus.NOT_FOUND, "Unknown ASIN.", "unknown_asin")
        status = HTTPStatus.OK
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            source = "saved" if move else "cart"
            target = "cart" if move else "saved"
            row = db.execute(
                f"SELECT quantity FROM {source} WHERE session_id = ? AND asin = ?",
                (session_id, asin),
            ).fetchone()
            target_row = db.execute(
                f"SELECT quantity FROM {target} WHERE session_id = ? AND asin = ?",
                (session_id, asin),
            ).fetchone()
            if row is None and target_row is None:
                status = HTTPStatus.CONFLICT
                outcome = "saved_item_not_found" if move else "cart_item_not_found"
            elif row is None:
                outcome = "already_in_cart" if move else "already_saved"
            else:
                quantity = int(row["quantity"])
                timestamp_column = "updated_at" if move else "saved_at"
                db.execute(
                    f"""
                    INSERT INTO {target} (session_id, asin, quantity, {timestamp_column})
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id, asin) DO UPDATE SET
                        quantity = excluded.quantity,
                        {timestamp_column} = excluded.{timestamp_column}
                    """,
                    (session_id, asin, quantity, utc_now()),
                )
                db.execute(
                    f"DELETE FROM {source} WHERE session_id = ? AND asin = ?",
                    (session_id, asin),
                )
                outcome = "moved_to_cart" if move else "saved_for_later"
            add_journal(db, session_id, "POST", path, int(status), outcome, asin)
        if status == HTTPStatus.OK:
            self.send_json({"status": "ok", "outcome": outcome})
        else:
            self.send_request_error(
                RequestError(status, "Item is not available for this action.", outcome)
            )

    def _boundary(self, path: str) -> None:
        self._validate_origin()
        payload = self._read_json()
        if set(payload) != {"kind"} or not isinstance(payload.get("kind"), str):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Boundary body must contain exactly one string kind.",
                "invalid_boundary",
            )
        kind = payload["kind"]
        if kind not in BOUNDARY_KINDS:
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Unknown boundary kind.",
                "invalid_boundary",
            )
        with database(self.db_path, write=True) as db:
            session_id = self._ensure_session(db)
            now = utc_now()
            db.execute(
                "INSERT INTO boundaries (session_id, kind, path, created_at) VALUES (?, ?, ?, ?)",
                (session_id, kind, path, now),
            )
            add_journal(db, session_id, "POST", path, 200, "boundary_no_effect")
        self.send_json({"status": "local-no-effect", "kind": kind})

    def do_HEAD(self) -> None:
        try:
            self._handle_get(mutate=False)
        except RequestError as error:
            self.send_request_error(error)
        except sqlite3.Error:
            self.send_storage_error()

    def do_GET(self) -> None:
        try:
            self._handle_get(mutate=True)
        except RequestError as error:
            self.send_request_error(error)
        except sqlite3.Error:
            self.send_storage_error()

    def do_POST(self) -> None:
        try:
            parsed, path = self._target()
            if parsed.query:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.", "not_found")
            if path in TERMINAL_PATHS:
                self._terminal_add(path)
                return
            if path == "/api/cart/add":
                self._generic_cart_add(path)
                return
            if path == "/api/list":
                self._wishlist_add(path)
                return
            if path == "/api/session/preferences":
                self._session_preferences(path)
                return
            if path == "/api/boundary":
                self._boundary(path)
                return
            match = re.fullmatch(
                r"/api/cart/([A-Z0-9]{10,11})/(save-for-later|move-to-cart)",
                path,
            )
            if match:
                self._cart_save_or_move(
                    path,
                    match.group(1),
                    move=match.group(2) == "move-to-cart",
                )
                return
            raise RequestError(HTTPStatus.NOT_FOUND, "Not found.", "not_found")
        except RequestError as error:
            self.send_request_error(error)
        except sqlite3.Error:
            self.send_storage_error()

    def do_PATCH(self) -> None:
        try:
            parsed, path = self._target()
            match = re.fullmatch(r"/api/cart/([A-Z0-9]{10,11})", path)
            if parsed.query or not match:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.", "not_found")
            self._validate_origin()
            payload = self._read_json()
            if set(payload) != {"quantity"}:
                raise RequestError(
                    HTTPStatus.BAD_REQUEST,
                    "JSON body must contain exactly one quantity field.",
                    "invalid_quantity",
                )
            quantity = payload["quantity"]
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity not in {1, 2, 3}
            ):
                raise RequestError(
                    HTTPStatus.BAD_REQUEST,
                    "Quantity must be an integer from 1 to 3.",
                    "invalid_quantity",
                )
            asin = match.group(1)
            if asin not in PRODUCT_INDEX:
                raise RequestError(
                    HTTPStatus.NOT_FOUND, "Unknown ASIN.", "unknown_asin"
                )
            status = HTTPStatus.OK
            with database(self.db_path, write=True) as db:
                session_id = self._ensure_session(db)
                cursor = db.execute(
                    """
                    UPDATE cart SET quantity = ?, updated_at = ?
                    WHERE session_id = ? AND asin = ?
                    """,
                    (quantity, utc_now(), session_id, asin),
                )
                outcome = "cart_quantity_updated"
                if cursor.rowcount != 1:
                    status = HTTPStatus.NOT_FOUND
                    outcome = "cart_item_not_found"
                add_journal(
                    db,
                    session_id,
                    "PATCH",
                    path,
                    int(status),
                    outcome,
                    asin,
                    quantity,
                )
            if status == HTTPStatus.OK:
                self.send_json({"status": "ok", "outcome": outcome})
            else:
                self.send_request_error(
                    RequestError(status, "Cart item not found.", outcome)
                )
        except RequestError as error:
            self.send_request_error(error)
        except sqlite3.Error:
            self.send_storage_error()

    def do_DELETE(self) -> None:
        try:
            parsed, path = self._target()
            list_match = re.fullmatch(r"/api/list/([A-Z0-9]{10,11})", path)
            if list_match and not parsed.query:
                self._wishlist_delete(path, list_match.group(1))
                return
            match = re.fullmatch(r"/api/cart/([A-Z0-9]{10,11})", path)
            if parsed.query or not match:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.", "not_found")
            self._validate_origin()
            self._require_empty_body()
            asin = match.group(1)
            if asin not in PRODUCT_INDEX:
                raise RequestError(
                    HTTPStatus.NOT_FOUND, "Unknown ASIN.", "unknown_asin"
                )
            with database(self.db_path, write=True) as db:
                session_id = self._ensure_session(db)
                cursor = db.execute(
                    "DELETE FROM cart WHERE session_id = ? AND asin = ?",
                    (session_id, asin),
                )
                outcome = "cart_item_deleted" if cursor.rowcount else "cart_item_absent"
                add_journal(db, session_id, "DELETE", path, 200, outcome, asin)
            self.send_json({"status": "ok", "outcome": outcome})
        except RequestError as error:
            self.send_request_error(error)
        except sqlite3.Error:
            self.send_storage_error()

    def _method_not_allowed(self) -> None:
        self.send_json(
            {"error": "Method not allowed.", "outcome": "method_not_allowed"},
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"Allow": "GET, HEAD, POST, PATCH, DELETE"},
        )

    do_OPTIONS = _method_not_allowed
    do_PUT = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.client_address[0]} - {format % args}")


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Amazon task backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=port_number, default=RUNTIME["canonical_port"])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if args.host not in ALLOWED_BIND_HOSTS:
        parser.error("host must be 127.0.0.1, localhost, or 0.0.0.0")

    db_path = args.db.expanduser().resolve()
    try:
        import uvicorn

        from fastapi_app import create_app
    except ImportError as error:
        parser.error(
            "FastAPI site dependencies are required; install the project 'sites' extra"
        )
        raise AssertionError("unreachable") from error

    init_db(db_path)
    app = create_app(db_path, legacy=__import__(__name__))
    print(
        f"Amazon FastAPI SSR evaluation listening on http://{args.host}:{args.port}",
        flush=True,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
        server_header=False,
        date_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
