from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from home_catalog import load_home_product_catalog


if TYPE_CHECKING:
    from store import Store


DEFAULT_LIST_NAME = "Shopping List"
MAX_LIST_NAME_LENGTH = 100
_ASIN_RE = re.compile(r"[A-Z0-9]{10}\Z")


class WishlistError(Exception):
    """Base class for stable Wishlist service failures."""


class WishlistAuthenticationRequired(WishlistError):
    """The browser session is not bound to an account."""


class WishlistNotFound(WishlistError):
    """The requested account-owned list or item does not exist."""


class WishlistValidationError(WishlistError):
    """A caller supplied malformed or unsupported Wishlist input."""


class WishlistConflict(WishlistError):
    """The requested mutation conflicts with an account invariant."""


_WISHLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS wishlist_lists (
    list_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL
        REFERENCES accounts(account_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    name_key TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, name_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS wishlist_one_default_per_account_idx
    ON wishlist_lists(account_id)
    WHERE is_default = 1;

CREATE INDEX IF NOT EXISTS wishlist_lists_account_idx
    ON wishlist_lists(account_id, is_default DESC, list_id);

CREATE TABLE IF NOT EXISTS wishlist_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL
        REFERENCES wishlist_lists(list_id) ON DELETE CASCADE,
    asin TEXT NOT NULL CHECK (
        length(asin) = 10 AND asin NOT GLOB '*[^A-Z0-9]*'
    ),
    selection_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (list_id, asin, selection_json)
);

CREATE INDEX IF NOT EXISTS wishlist_items_list_idx
    ON wishlist_items(list_id, item_id DESC);
"""


def ensure_wishlist_schema(store: Store) -> None:
    """Install the additive Wishlist schema without changing the core schema file."""

    with store.connect() as connection:
        connection.executescript(_WISHLIST_SCHEMA)


def _canonical_identifier(value: int | str, label: str) -> int:
    if isinstance(value, bool):
        raise WishlistValidationError(f"{label} must be a positive integer")
    if isinstance(value, int):
        result = value
    elif (
        isinstance(value, str)
        and value
        and value.isascii()
        and value.isdecimal()
        and not (len(value) > 1 and value.startswith("0"))
    ):
        result = int(value)
    else:
        raise WishlistValidationError(f"{label} must be a positive integer")
    if result <= 0:
        raise WishlistValidationError(f"{label} must be a positive integer")
    return result


def _canonical_name(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise WishlistValidationError("list name must be text")
    normalized = unicodedata.normalize("NFKC", value)
    name = " ".join(normalized.split())
    if not name or len(name) > MAX_LIST_NAME_LENGTH:
        raise WishlistValidationError(
            f"list name must contain 1 to {MAX_LIST_NAME_LENGTH} characters"
        )
    if any(unicodedata.category(character).startswith("C") for character in name):
        raise WishlistValidationError("list name contains unsupported characters")
    return name, name.casefold()


def _canonical_asin(value: str) -> str:
    if not isinstance(value, str) or _ASIN_RE.fullmatch(value) is None:
        raise WishlistValidationError("ASIN must use the canonical 10-character form")
    return value


def _require_account_id(
    connection: sqlite3.Connection, session_digest: str
) -> int:
    row = connection.execute(
        "SELECT account_id FROM browser_sessions WHERE session_digest=?",
        (session_digest,),
    ).fetchone()
    if row is None or row["account_id"] is None:
        raise WishlistAuthenticationRequired("sign in to use Lists")
    return int(row["account_id"])


def _ensure_default_list(
    store: Store, connection: sqlite3.Connection, account_id: int
) -> int:
    rows = connection.execute(
        """
        SELECT list_id,is_default
        FROM wishlist_lists
        WHERE account_id=?
        ORDER BY is_default DESC,list_id
        """,
        (account_id,),
    ).fetchall()
    if not rows:
        now = store.now(connection)
        cursor = connection.execute(
            """
            INSERT INTO wishlist_lists(
                account_id,name,name_key,is_default,created_at,updated_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                account_id,
                DEFAULT_LIST_NAME,
                DEFAULT_LIST_NAME.casefold(),
                1,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    default_rows = [row for row in rows if bool(row["is_default"])]
    if default_rows:
        return int(default_rows[0]["list_id"])

    default_list_id = int(rows[0]["list_id"])
    connection.execute(
        "UPDATE wishlist_lists SET is_default=1 WHERE list_id=?",
        (default_list_id,),
    )
    return default_list_id


def _prepare_session(store: Store, session_digest: str) -> None:
    if not isinstance(session_digest, str) or not session_digest:
        raise WishlistAuthenticationRequired("sign in to use Lists")
    ensure_wishlist_schema(store)
    store.ensure_session(session_digest)


def _owned_list_row(
    connection: sqlite3.Connection, account_id: int, list_id: int
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT l.*,
               (SELECT COUNT(*) FROM wishlist_items i WHERE i.list_id=l.list_id)
                   AS item_count
        FROM wishlist_lists l
        WHERE l.list_id=? AND l.account_id=?
        """,
        (list_id, account_id),
    ).fetchone()
    if row is None:
        # Missing and foreign-owned identifiers deliberately share one result.
        raise WishlistNotFound("list not found")
    return row


def _list_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "list_id": int(row["list_id"]),
        "name": str(row["name"]),
        "is_default": bool(row["is_default"]),
        "item_count": int(row["item_count"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _decode_selection(raw_value: Any) -> dict[str, str]:
    try:
        payload = json.loads(str(raw_value))
    except (json.JSONDecodeError, TypeError) as exc:
        raise WishlistValidationError("stored Wishlist selection is corrupted") from exc
    if not isinstance(payload, dict) or any(
        not isinstance(label, str) or not isinstance(value, str)
        for label, value in payload.items()
    ):
        raise WishlistValidationError("stored Wishlist selection is corrupted")
    return payload


@lru_cache(maxsize=8)
def _home_catalog(fixture_root: str) -> dict[str, dict[str, Any]]:
    return load_home_product_catalog(Path(fixture_root))


def _trusted_product(store: Store, asin: str) -> dict[str, Any] | None:
    """Resolve display facts from server-owned fixtures and tables only."""

    home_product = _home_catalog(str(store.fixture_root)).get(asin)
    if home_product is not None and home_product.get("evidence_tier") == "pdp-direct":
        product = dict(home_product)
    else:
        offer = store.commerce_offer(asin)
        catalog_product = store.product(asin)
        if offer is not None:
            product = dict(offer)
        elif catalog_product is not None:
            product = dict(catalog_product)
        elif home_product is not None:
            product = dict(home_product)
        else:
            return None

    product["asin"] = asin
    product["canonical_path"] = str(
        product.get("canonical_path") or f"/dp/{asin}"
    )
    product["brand"] = str(product.get("brand") or "")
    return product


def _matching_quote(
    store: Store, asin: str, selected_options: Mapping[str, str]
) -> dict[str, Any] | None:
    if store.commerce_offer(asin) is None:
        return None
    for quote in store.product_option_quotes(asin):
        if (
            quote.get("selected_options") == dict(selected_options)
            and quote.get("availability") == "AVAILABLE"
        ):
            return quote
    return None


def _quote_available(
    store: Store, asin: str, selected_options: Mapping[str, str]
) -> bool:
    return _matching_quote(store, asin, selected_options) is not None


def _item_payload(store: Store, row: sqlite3.Row) -> dict[str, Any]:
    asin = str(row["asin"])
    selected_options = _decode_selection(row["selection_json"])
    product = _trusted_product(store, asin)
    if product is None:
        raise WishlistValidationError("stored Wishlist product is no longer trusted")
    quote = _matching_quote(store, asin, selected_options)
    available_to_cart = quote is not None
    payload: dict[str, Any] = {
        "item_id": int(row["item_id"]),
        "list_id": int(row["list_id"]),
        "asin": asin,
        "selected_options": selected_options,
        "created_at": str(row["created_at"]),
        "title": str(product["title"]),
        "brand": str(product.get("brand") or ""),
        "canonical_path": str(product["canonical_path"]),
        "image_path": str(
            quote.get("image_path")
            if quote is not None and quote.get("image_path")
            else product["image_path"]
        ),
        # Display prices are joined from the current server-owned offer. They
        # are never accepted from, or persisted on behalf of, the browser.
        "price_minor": int(quote["price_minor"]) if quote is not None else None,
        "currency": str(quote["currency"]) if quote is not None else None,
        "rating": str(product.get("rating") or ""),
        "reviews": int(product.get("reviews") or 0),
        "badge": str(product.get("badge") or ""),
        "available_to_cart": available_to_cart,
    }
    return payload


_ITEM_SELECT = """
SELECT i.item_id,i.list_id,i.asin,i.selection_json,i.created_at
FROM wishlist_items i
"""


def _item_row(
    connection: sqlite3.Connection,
    account_id: int,
    list_id: int,
    item_id: int,
) -> sqlite3.Row:
    row = connection.execute(
        _ITEM_SELECT
        + """
        JOIN wishlist_lists l ON l.list_id=i.list_id
        WHERE i.item_id=? AND i.list_id=? AND l.account_id=?
        """,
        (item_id, list_id, account_id),
    ).fetchone()
    if row is None:
        # Do not reveal whether either identifier belongs to another account.
        raise WishlistNotFound("item not found")
    return row


def _canonical_selection(
    store: Store, asin: str, selected_options: Mapping[str, Any]
) -> dict[str, str]:
    if not isinstance(selected_options, Mapping):
        raise WishlistValidationError("product option selection must be a mapping")

    if _trusted_product(store, asin) is None:
        raise WishlistNotFound("product not found")

    specification = store.product_option_spec(asin)
    expected_labels = {str(group["label"]) for group in specification}
    if set(selected_options) != expected_labels:
        raise WishlistValidationError(
            "product option selection is incomplete or unsupported"
        )

    canonical: dict[str, str] = {}
    for group in specification:
        label = str(group["label"])
        value = selected_options.get(label)
        allowed = tuple(str(option) for option in group["options"])
        if not isinstance(value, str) or value not in allowed:
            raise WishlistValidationError(
                f"unsupported observed option value for {label}"
            )
        canonical[label] = value

    # When an offer exists, reject observed option combinations that have no
    # transaction evidence. Browse-only products have no offer and can still
    # be saved, but they remain explicitly unavailable to cart.
    if store.commerce_offer(asin) is not None and not _quote_available(
        store, asin, canonical
    ):
        raise WishlistValidationError("no verified offer for this selection")
    return canonical


def product_selection_for_wishlist(
    store: Store, asin: str, selected_options: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return one trusted product and its canonical complete option selection.

    This is the read-only boundary used by the HTTP chooser.  It validates the
    same server-owned catalog and option contract as :func:`add_item` without
    mutating a list or accepting browser-supplied display facts.
    """

    canonical_asin = _canonical_asin(asin)
    canonical_options = _canonical_selection(
        store, canonical_asin, selected_options
    )
    product = _trusted_product(store, canonical_asin)
    if product is None:
        raise WishlistNotFound("product not found")
    quote = _matching_quote(store, canonical_asin, canonical_options)
    if quote is not None and quote.get("image_path"):
        product = {**product, "image_path": str(quote["image_path"])}
    return product, canonical_options


def lists_for_session(store: Store, session_digest: str) -> list[dict[str, Any]]:
    """Return account-owned lists, creating Shopping List on first access."""

    _prepare_session(store, session_digest)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        _ensure_default_list(store, connection, account_id)
        rows = connection.execute(
            """
            SELECT l.*,
                   (SELECT COUNT(*) FROM wishlist_items i WHERE i.list_id=l.list_id)
                       AS item_count
            FROM wishlist_lists l
            WHERE l.account_id=?
            ORDER BY l.is_default DESC,l.list_id
            """,
            (account_id,),
        ).fetchall()
        connection.commit()
    return [_list_payload(row) for row in rows]


def default_list_for_session(store: Store, session_digest: str) -> dict[str, Any]:
    lists = lists_for_session(store, session_digest)
    return next(wishlist for wishlist in lists if wishlist["is_default"])


def list_for_session(
    store: Store, session_digest: str, list_id: int | str
) -> dict[str, Any]:
    """Return one owned list with its current server-side product displays."""

    canonical_list_id = _canonical_identifier(list_id, "list id")
    _prepare_session(store, session_digest)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        _ensure_default_list(store, connection, account_id)
        list_row = _owned_list_row(connection, account_id, canonical_list_id)
        item_rows = connection.execute(
            _ITEM_SELECT
            + " WHERE i.list_id=? ORDER BY i.item_id DESC",
            (canonical_list_id,),
        ).fetchall()
        connection.commit()
    result = _list_payload(list_row)
    result["items"] = [_item_payload(store, row) for row in item_rows]
    return result


def create_list(
    store: Store, session_digest: str, name: str
) -> dict[str, Any]:
    canonical_name, name_key = _canonical_name(name)
    _prepare_session(store, session_digest)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        _ensure_default_list(store, connection, account_id)
        now = store.now(connection)
        try:
            cursor = connection.execute(
                """
                INSERT INTO wishlist_lists(
                    account_id,name,name_key,is_default,created_at,updated_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (account_id, canonical_name, name_key, 0, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise WishlistConflict("a list with that name already exists") from exc
        row = _owned_list_row(connection, account_id, int(cursor.lastrowid))
        connection.commit()
    return _list_payload(row)


def rename_list(
    store: Store,
    session_digest: str,
    list_id: int | str,
    name: str,
) -> dict[str, Any]:
    canonical_list_id = _canonical_identifier(list_id, "list id")
    canonical_name, name_key = _canonical_name(name)
    _prepare_session(store, session_digest)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        _ensure_default_list(store, connection, account_id)
        _owned_list_row(connection, account_id, canonical_list_id)
        try:
            connection.execute(
                """
                UPDATE wishlist_lists
                SET name=?,name_key=?,updated_at=?
                WHERE list_id=? AND account_id=?
                """,
                (
                    canonical_name,
                    name_key,
                    store.now(connection),
                    canonical_list_id,
                    account_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise WishlistConflict("a list with that name already exists") from exc
        row = _owned_list_row(connection, account_id, canonical_list_id)
        connection.commit()
    return _list_payload(row)


def delete_list(
    store: Store, session_digest: str, list_id: int | str
) -> dict[str, int]:
    """Delete one owned list while preserving exactly one account default."""

    canonical_list_id = _canonical_identifier(list_id, "list id")
    _prepare_session(store, session_digest)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        _ensure_default_list(store, connection, account_id)
        row = _owned_list_row(connection, account_id, canonical_list_id)
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM wishlist_lists WHERE account_id=?",
                (account_id,),
            ).fetchone()[0]
        )
        if count <= 1:
            raise WishlistConflict("an account must keep at least one list")

        connection.execute(
            "DELETE FROM wishlist_lists WHERE list_id=? AND account_id=?",
            (canonical_list_id, account_id),
        )
        if bool(row["is_default"]):
            replacement = connection.execute(
                """
                SELECT list_id FROM wishlist_lists
                WHERE account_id=? ORDER BY list_id LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if replacement is None:
                raise WishlistConflict("an account must keep at least one list")
            default_list_id = int(replacement["list_id"])
            connection.execute(
                "UPDATE wishlist_lists SET is_default=1 WHERE list_id=?",
                (default_list_id,),
            )
        else:
            default_row = connection.execute(
                """
                SELECT list_id FROM wishlist_lists
                WHERE account_id=? AND is_default=1
                """,
                (account_id,),
            ).fetchone()
            if default_row is None:
                default_list_id = _ensure_default_list(
                    store, connection, account_id
                )
            else:
                default_list_id = int(default_row["list_id"])
        connection.commit()
    return {
        "deleted_list_id": canonical_list_id,
        "default_list_id": default_list_id,
    }


def add_item(
    store: Store,
    session_digest: str,
    list_id: int | str,
    asin: str,
    selected_options: Mapping[str, Any],
) -> dict[str, Any]:
    """Idempotently add one fully specified server-verifiable product."""

    canonical_list_id = _canonical_identifier(list_id, "list id")
    canonical_asin = _canonical_asin(asin)
    _prepare_session(store, session_digest)

    # Authorize the opaque list id before validating product input so a foreign
    # list id cannot be used as a behavioral oracle.
    with store.connect() as connection:
        account_id = _require_account_id(connection, session_digest)
        _owned_list_row(connection, account_id, canonical_list_id)

    canonical_options = _canonical_selection(
        store, canonical_asin, selected_options
    )
    selection_json = json.dumps(
        canonical_options,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )

    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        _owned_list_row(connection, account_id, canonical_list_id)
        cursor = connection.execute(
            """
            INSERT INTO wishlist_items(list_id,asin,selection_json,created_at)
            VALUES (?,?,?,?)
            ON CONFLICT(list_id,asin,selection_json) DO NOTHING
            """,
            (
                canonical_list_id,
                canonical_asin,
                selection_json,
                store.now(connection),
            ),
        )
        created = cursor.rowcount == 1
        item_row = connection.execute(
            _ITEM_SELECT
            + """
            WHERE i.list_id=? AND i.asin=? AND i.selection_json=?
            """,
            (canonical_list_id, canonical_asin, selection_json),
        ).fetchone()
        if item_row is None:
            raise WishlistConflict("Wishlist item could not be persisted")
        connection.execute(
            "UPDATE wishlist_lists SET updated_at=? WHERE list_id=?",
            (store.now(connection), canonical_list_id),
        )
        connection.commit()
    return {"created": created, "item": _item_payload(store, item_row)}


def remove_item(
    store: Store,
    session_digest: str,
    list_id: int | str,
    item_id: int | str,
) -> dict[str, Any]:
    canonical_list_id = _canonical_identifier(list_id, "list id")
    canonical_item_id = _canonical_identifier(item_id, "item id")
    _prepare_session(store, session_digest)
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        account_id = _require_account_id(connection, session_digest)
        row = _item_row(
            connection, account_id, canonical_list_id, canonical_item_id
        )
        connection.execute(
            "DELETE FROM wishlist_items WHERE item_id=? AND list_id=?",
            (canonical_item_id, canonical_list_id),
        )
        connection.execute(
            "UPDATE wishlist_lists SET updated_at=? WHERE list_id=?",
            (store.now(connection), canonical_list_id),
        )
        connection.commit()
    return _item_payload(store, row)


def item_for_move_to_cart(
    store: Store,
    session_digest: str,
    list_id: int | str,
    item_id: int | str,
) -> dict[str, Any]:
    """Return only the trusted cart identity; leave deletion to the caller.

    The route can first add this identity to the account cart and only then call
    :func:`remove_item`, avoiding data loss when cart insertion fails.
    """

    canonical_list_id = _canonical_identifier(list_id, "list id")
    canonical_item_id = _canonical_identifier(item_id, "item id")
    _prepare_session(store, session_digest)
    with store.connect() as connection:
        account_id = _require_account_id(connection, session_digest)
        row = _item_row(
            connection, account_id, canonical_list_id, canonical_item_id
        )
    selected_options = _decode_selection(row["selection_json"])
    canonical_options = _canonical_selection(
        store, str(row["asin"]), selected_options
    )
    if not _quote_available(store, str(row["asin"]), canonical_options):
        raise WishlistConflict("this product has no verified offer for cart")
    return {
        "list_id": canonical_list_id,
        "item_id": canonical_item_id,
        "asin": str(row["asin"]),
        "selected_options": canonical_options,
    }
