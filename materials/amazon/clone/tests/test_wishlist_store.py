from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from home_catalog import load_home_product_catalog  # noqa: E402
from product_options import TARGET_ASIN  # noqa: E402
from store import Store  # noqa: E402
from wishlist_store import (  # noqa: E402
    WishlistAuthenticationRequired,
    WishlistConflict,
    WishlistNotFound,
    WishlistValidationError,
    add_item,
    create_list,
    default_list_for_session,
    delete_list,
    ensure_wishlist_schema,
    item_for_move_to_cart,
    list_for_session,
    lists_for_session,
    remove_item,
    rename_list,
)
from wishlist_views import (  # noqa: E402
    wishlist_add_chooser_page,
    wishlist_detail_page,
    wishlist_entry_form,
    wishlist_index_page,
    wishlist_intro_page,
)


PASSWORD = "Correct-Horse-921"
OWNER_SESSION = "wishlist-owner-session"
SECOND_OWNER_SESSION = "wishlist-owner-second-session"
OTHER_SESSION = "wishlist-other-session"
DEALS_ASIN = "B01LYNW421"
UNKNOWN_ASIN = "B000000000"


class WishlistStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()
        ensure_wishlist_schema(self.store)
        home_catalog = load_home_product_catalog(ROOT / "fixtures")
        self.home_only_asin = next(
            asin
            for asin in home_catalog
            if self.store.commerce_offer(asin) is None
        )
        self.assertTrue(
            self.store.register_account(
                OWNER_SESSION,
                "owner@example.test",
                "List Owner",
                PASSWORD,
            )
        )
        self.assertTrue(
            self.store.register_account(
                OTHER_SESSION,
                "other@example.test",
                "Other Shopper",
                PASSWORD,
            )
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_anonymous_is_rejected_and_first_access_creates_one_default_list(self) -> None:
        with self.assertRaises(WishlistAuthenticationRequired):
            lists_for_session(self.store, "anonymous-session")

        first = lists_for_session(self.store, OWNER_SESSION)
        second = lists_for_session(self.store, OWNER_SESSION)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["name"], "Shopping List")
        self.assertTrue(first[0]["is_default"])
        self.assertEqual(first[0]["item_count"], 0)
        self.assertEqual(default_list_for_session(self.store, OWNER_SESSION), first[0])

    def test_lists_are_created_renamed_deleted_and_never_reduced_to_zero(self) -> None:
        default_list = default_list_for_session(self.store, OWNER_SESSION)
        birthday = create_list(
            self.store, OWNER_SESSION, "  Birthday    Ideas  "
        )
        self.assertEqual(birthday["name"], "Birthday Ideas")
        self.assertFalse(birthday["is_default"])

        with self.assertRaises(WishlistConflict):
            create_list(self.store, OWNER_SESSION, "birthday ideas")

        renamed = rename_list(
            self.store, OWNER_SESSION, birthday["list_id"], "Books to read"
        )
        self.assertEqual(renamed["name"], "Books to read")

        deletion = delete_list(
            self.store, OWNER_SESSION, default_list["list_id"]
        )
        self.assertEqual(deletion["deleted_list_id"], default_list["list_id"])
        self.assertEqual(deletion["default_list_id"], birthday["list_id"])
        remaining = lists_for_session(self.store, OWNER_SESSION)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["list_id"], birthday["list_id"])
        self.assertTrue(remaining[0]["is_default"])

        with self.assertRaises(WishlistConflict):
            delete_list(self.store, OWNER_SESSION, birthday["list_id"])

    def test_item_identity_is_list_asin_and_complete_selection_only(self) -> None:
        wishlist = default_list_for_session(self.store, OWNER_SESSION)
        defaults = self.store.default_product_options(TARGET_ASIN)
        first = add_item(
            self.store,
            OWNER_SESSION,
            wishlist["list_id"],
            TARGET_ASIN,
            defaults,
        )
        duplicate = add_item(
            self.store,
            OWNER_SESSION,
            wishlist["list_id"],
            TARGET_ASIN,
            dict(reversed(tuple(defaults.items()))),
        )
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(first["item"]["item_id"], duplicate["item"]["item_id"])

        blue = dict(defaults)
        blue["Color"] = "Blue"
        blue_result = add_item(
            self.store,
            OWNER_SESSION,
            wishlist["list_id"],
            TARGET_ASIN,
            blue,
        )
        self.assertTrue(blue_result["created"])
        self.assertNotEqual(first["item"]["item_id"], blue_result["item"]["item_id"])

        detail = list_for_session(
            self.store, OWNER_SESSION, wishlist["list_id"]
        )
        self.assertEqual(detail["item_count"], 2)
        self.assertEqual(len(detail["items"]), 2)

        with self.store.connect() as connection:
            item_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(wishlist_items)")
            }
            stored = connection.execute(
                "SELECT selection_json FROM wishlist_items WHERE item_id=?",
                (first["item"]["item_id"],),
            ).fetchone()
        self.assertNotIn("price", item_columns)
        self.assertNotIn("price_minor", item_columns)
        self.assertEqual(
            stored["selection_json"],
            '{"Color":"Titan Gray","Memory Storage Capacity":"1 TB"}',
        )

    def test_incomplete_extra_unknown_and_unverified_selections_are_rejected(self) -> None:
        wishlist = default_list_for_session(self.store, OWNER_SESSION)
        list_id = wishlist["list_id"]
        with self.assertRaises(WishlistValidationError):
            add_item(
                self.store,
                OWNER_SESSION,
                list_id,
                TARGET_ASIN,
                {"Color": "Blue"},
            )
        with self.assertRaises(WishlistValidationError):
            add_item(
                self.store,
                OWNER_SESSION,
                list_id,
                TARGET_ASIN,
                {
                    **self.store.default_product_options(TARGET_ASIN),
                    "price_minor": "1",
                },
            )
        with self.assertRaises(WishlistValidationError):
            add_item(
                self.store,
                OWNER_SESSION,
                list_id,
                TARGET_ASIN,
                {"Color": "Blue", "Memory Storage Capacity": "2 TB"},
            )
        with self.assertRaises(WishlistNotFound):
            add_item(
                self.store, OWNER_SESSION, list_id, UNKNOWN_ASIN, {}
            )

        no_option_result = add_item(
            self.store, OWNER_SESSION, list_id, DEALS_ASIN, {}
        )
        self.assertTrue(no_option_result["created"])

        browse_only = add_item(
            self.store, OWNER_SESSION, list_id, self.home_only_asin, {}
        )
        self.assertTrue(browse_only["created"])
        self.assertFalse(browse_only["item"]["available_to_cart"])
        self.assertIsNone(browse_only["item"]["price_minor"])
        with self.assertRaises(WishlistConflict):
            item_for_move_to_cart(
                self.store,
                OWNER_SESSION,
                list_id,
                browse_only["item"]["item_id"],
            )

    def test_cross_account_ids_are_not_enumerable_and_move_payload_has_no_price(self) -> None:
        owner_list = default_list_for_session(self.store, OWNER_SESSION)
        item = add_item(
            self.store,
            OWNER_SESSION,
            owner_list["list_id"],
            TARGET_ASIN,
            self.store.default_product_options(TARGET_ASIN),
        )["item"]
        lists_for_session(self.store, OTHER_SESSION)

        foreign_operations = (
            lambda: list_for_session(
                self.store, OTHER_SESSION, owner_list["list_id"]
            ),
            lambda: rename_list(
                self.store, OTHER_SESSION, owner_list["list_id"], "Stolen"
            ),
            lambda: delete_list(
                self.store, OTHER_SESSION, owner_list["list_id"]
            ),
            lambda: add_item(
                self.store,
                OTHER_SESSION,
                owner_list["list_id"],
                TARGET_ASIN,
                self.store.default_product_options(TARGET_ASIN),
            ),
            lambda: remove_item(
                self.store,
                OTHER_SESSION,
                owner_list["list_id"],
                item["item_id"],
            ),
            lambda: item_for_move_to_cart(
                self.store,
                OTHER_SESSION,
                owner_list["list_id"],
                item["item_id"],
            ),
        )
        for operation in foreign_operations:
            with self.subTest(operation=operation):
                with self.assertRaises(WishlistNotFound):
                    operation()

        cart_identity = item_for_move_to_cart(
            self.store,
            OWNER_SESSION,
            owner_list["list_id"],
            item["item_id"],
        )
        self.assertEqual(
            set(cart_identity),
            {"list_id", "item_id", "asin", "selected_options"},
        )
        self.assertEqual(cart_identity["asin"], TARGET_ASIN)
        removed = remove_item(
            self.store,
            OWNER_SESSION,
            owner_list["list_id"],
            item["item_id"],
        )
        self.assertEqual(removed["item_id"], item["item_id"])
        with self.assertRaises(WishlistNotFound):
            remove_item(
                self.store,
                OWNER_SESSION,
                owner_list["list_id"],
                item["item_id"],
            )

    def test_lists_follow_the_account_across_authenticated_sessions_and_reset(self) -> None:
        created = create_list(self.store, OWNER_SESSION, "Long-term purchases")
        self.store.begin_signin(
            SECOND_OWNER_SESSION, "owner@example.test", None
        )
        authenticated, _ = self.store.authenticate_session(
            SECOND_OWNER_SESSION, PASSWORD
        )
        self.assertTrue(authenticated)
        second_session_lists = lists_for_session(
            self.store, SECOND_OWNER_SESSION
        )
        self.assertIn(
            created["list_id"],
            [wishlist["list_id"] for wishlist in second_session_lists],
        )

        # The additive tables are account-cascaded, so the existing core reset
        # remains safe even though it does not need to know their names.
        self.store.reset()
        with self.store.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM wishlist_lists").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM wishlist_items").fetchone()[0],
                0,
            )


class WishlistViewContractTests(unittest.TestCase):
    def test_intro_contains_source_assets_and_separate_desktop_mobile_structures(self) -> None:
        page = wishlist_intro_page(0)
        self.assertIn('data-wishlist-page="intro"', page)
        self.assertIn('/static/wishlist.css?v=20260722-1', page)
        self.assertIn('class="wishlist-intro-desktop"', page)
        self.assertIn('class="wishlist-intro-mobile"', page)
        for asset in (
            "desktop-banner.jpg",
            "mobile-list-icon.png",
            "mobile-gift-icon.png",
            "mobile-baby-registry.png",
            "mobile-wedding-registry.png",
        ):
            self.assertIn(
                f"/static/assets/source-current/2026-07-22/lists-intro/{asset}",
                page,
            )
        self.assertIn(
            "/ap/signin?openid.return_to=%2Fhz%2Fwishlist%2Fls", page
        )

    def test_signed_in_forms_expose_stable_contract_and_never_post_price(self) -> None:
        wishlists = [
            {
                "list_id": 7,
                "name": "Shopping List",
                "is_default": True,
                "item_count": 0,
            }
        ]
        index = wishlist_index_page(wishlists, 0, "Ada")
        self.assertIn('action="/hz/wishlist/create"', index)
        self.assertIn('name="listName"', index)

        product = {
            "asin": TARGET_ASIN,
            "title": "Portable SSD",
            "image_path": "/static/example.jpg",
            "canonical_path": f"/dp/{TARGET_ASIN}",
            "price_minor": 999999,
        }
        chooser = wishlist_add_chooser_page(
            product,
            {"Color": "Blue", "Memory Storage Capacity": "1 TB"},
            wishlists,
            0,
            "Ada",
        )
        self.assertIn('action="/hz/wishlist/add-item"', chooser)
        self.assertIn('name="listID" value="7"', chooser)
        self.assertIn(f'name="ASIN" value="{TARGET_ASIN}"', chooser)
        self.assertIn('name="option.Color" value="Blue"', chooser)
        self.assertNotIn('name="price"', chooser)
        self.assertNotIn('name="price_minor"', chooser)

        entry = wishlist_entry_form(
            product,
            {"Color": "Blue", "Memory Storage Capacity": "1 TB"},
        )
        self.assertIn('method="get" action="/hz/wishlist/add"', entry)
        self.assertIn('data-product-option-field="Color"', entry)
        self.assertNotIn('name="price', entry)

        detail_payload = {
            **wishlists[0],
            "items": [],
        }
        detail = wishlist_detail_page(
            detail_payload, wishlists, 0, "Ada"
        )
        self.assertIn('data-wishlist-page="detail"', detail)
        self.assertIn('action="/hz/wishlist/rename"', detail)
        # The only list cannot be deleted in the UI, matching the store guard.
        self.assertNotIn('action="/hz/wishlist/delete"', detail)


if __name__ == "__main__":
    unittest.main()
