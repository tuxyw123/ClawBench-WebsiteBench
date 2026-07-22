from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server as server_module  # noqa: E402
import test_checkout_backend as checkout_support  # noqa: E402
from store import Store  # noqa: E402


ORDER_CANCEL_PATH = "/gp/your-account/order-cancel"
RETURN_CREATE_PATH = "/gp/your-account/returns/create"
RETURN_DETAIL_PATH = "/gp/your-account/returns/details"
ORDER_DETAIL_PATH = "/gp/your-account/order-details"
ADMIN_TOKEN = "test-only-order-lifecycle-admin-token"


class QuietLifecyclePublicHandler(server_module.PublicHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class QuietLifecycleAdminHandler(server_module.AdminHandler):
    def log_message(self, format_string: str, *args: object) -> None:
        del format_string, args


class OrderLifecycleTests(unittest.TestCase):
    """End-to-end contract for the clone's explicitly simulated order lifecycle."""

    same_origin = checkout_support.CheckoutBackendTests.same_origin
    request = checkout_support.CheckoutBackendTests.request
    anonymous_cookie = checkout_support.CheckoutBackendTests.anonymous_cookie
    register = checkout_support.CheckoutBackendTests.register
    add_item = checkout_support.CheckoutBackendTests.add_item
    signed_in_cart = checkout_support.CheckoutBackendTests.signed_in_cart
    start_checkout = checkout_support.CheckoutBackendTests.start_checkout
    submit_address = checkout_support.CheckoutBackendTests.submit_address
    select_delivery = checkout_support.CheckoutBackendTests.select_delivery
    select_payment = checkout_support.CheckoutBackendTests.select_payment
    place_order = checkout_support.CheckoutBackendTests.place_order
    complete_checkout = checkout_support.CheckoutBackendTests.complete_checkout

    @staticmethod
    def session_cookie(headers: dict[str, list[str]]) -> str:
        return checkout_support.CheckoutBackendTests.session_cookie(headers)

    @staticmethod
    def session_digest(cookie: str) -> str:
        return checkout_support.CheckoutBackendTests.session_digest(cookie)

    @staticmethod
    def address_fields(label: str = "Primary") -> dict[str, str]:
        return checkout_support.CheckoutBackendTests.address_fields(label)

    @staticmethod
    def assert_redirect(
        response: tuple[int, dict[str, list[str]], bytes], location: str
    ) -> None:
        checkout_support.CheckoutBackendTests.assert_redirect(response, location)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            ROOT / "schema.sql",
            ROOT / "fixtures",
        )
        self.store.reset()

        QuietLifecyclePublicHandler.store = self.store
        QuietLifecyclePublicHandler.smtp_config = None
        self.server = server_module.ReusableThreadingHTTPServer(
            ("127.0.0.1", 0), QuietLifecyclePublicHandler
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

        QuietLifecycleAdminHandler.store = self.store
        QuietLifecycleAdminHandler.admin_token = ADMIN_TOKEN
        QuietLifecycleAdminHandler.smtp_summary = {"mode": "LOCAL_ONLY"}
        self.admin_server = server_module.ReusableThreadingHTTPServer(
            ("127.0.0.1", 0), QuietLifecycleAdminHandler
        )
        self.admin_thread = threading.Thread(
            target=self.admin_server.serve_forever, daemon=True
        )
        self.admin_thread.start()
        self.admin_host, self.admin_port = self.admin_server.server_address

    def tearDown(self) -> None:
        self.admin_server.shutdown()
        self.admin_server.server_close()
        self.admin_thread.join(timeout=2)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def admin_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        token: str | None = ADMIN_TOKEN,
    ) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        if token is not None:
            headers["X-Bench-Admin-Token"] = token
        connection = http.client.HTTPConnection(
            self.admin_host, self.admin_port, timeout=8
        )
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        status = response.status
        connection.close()
        decoded = json.loads(response_body.decode("utf-8"))
        self.assertIsInstance(decoded, dict)
        return status, decoded

    def placed_order(
        self, email: str, *, key: str, quantity: int = 2
    ) -> tuple[str, str, int, dict[str, Any]]:
        cookie = self.signed_in_cart(email, quantity=quantity)
        order_id, _ = self.complete_checkout(
            cookie,
            delivery="standard",
            key=key,
        )
        session_digest = self.session_digest(cookie)
        order = self.store.order_for_session(session_digest, order_id)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order["placement_status"], "PLACED")
        self.assertEqual(order["status"], "PREPARING")
        return cookie, session_digest, order_id, order

    def assert_placement_fact(self, order_id: int) -> None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT status,is_simulation FROM orders WHERE order_id=?",
                (order_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual((row["status"], row["is_simulation"]), ("PLACED", 1))

    def test_legacy_placed_order_migrates_without_rewriting_placement_facts(self) -> None:
        _, session_digest, order_id, before = self.placed_order(
            "migration-owner@example.test",
            key="place-lifecycle-migration-0001",
            quantity=2,
        )
        db_path = self.store.db_path
        lifecycle_triggers = (
            "immutable_order_placement_status_guard",
            "immutable_shipment_placement_status_guard",
            "shipment_lifecycle_transition_guard",
            "shipment_lifecycle_shape_guard",
            "order_event_owner_insert_guard",
            "order_action_owner_insert_guard",
            "return_request_owner_insert_guard",
            "return_status_transition_guard",
            "return_delivered_insert_guard",
            "return_owner_update_guard",
            "return_item_order_insert_guard",
            "refund_owner_insert_guard",
            "refund_state_insert_guard",
            "refund_identity_update_guard",
        )
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            for trigger in lifecycle_triggers:
                connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
            for table in (
                "refunds",
                "return_request_items",
                "return_requests",
                "order_action_keys",
                "order_events",
            ):
                connection.execute(f'DROP TABLE IF EXISTS "{table}"')
            connection.executescript(
                """
                CREATE TABLE legacy_shipments (
                    shipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL UNIQUE
                        REFERENCES orders(order_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK (status='PREPARING'),
                    delivery_method TEXT NOT NULL CHECK (
                        delivery_method IN ('standard','expedited')
                    ),
                    shipping_minor INTEGER NOT NULL CHECK (
                        shipping_minor IN (0,1299)
                    ),
                    carrier TEXT NOT NULL,
                    tracking_code TEXT,
                    is_simulation INTEGER NOT NULL DEFAULT 1
                        CHECK (is_simulation=1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO legacy_shipments(
                    shipment_id,order_id,status,delivery_method,shipping_minor,
                    carrier,tracking_code,is_simulation,created_at,updated_at
                )
                SELECT shipment_id,order_id,status,delivery_method,shipping_minor,
                       carrier,NULL,is_simulation,created_at,updated_at
                FROM shipments;
                DROP TABLE shipments;
                ALTER TABLE legacy_shipments RENAME TO shipments;
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated_store = Store(db_path, ROOT / "schema.sql", ROOT / "fixtures")
        migrated = migrated_store.order_for_session(session_digest, order_id)
        self.assertIsNotNone(migrated)
        assert migrated is not None
        self.assertEqual(migrated["placement_status"], "PLACED")
        self.assertEqual(migrated["status"], "PREPARING")
        self.assertEqual(migrated["shipment"]["lifecycle_status"], "PREPARING")
        self.assertEqual(migrated["shipment"]["revision"], 1)
        self.assertIsNone(migrated["shipment"]["tracking_code"])
        self.assertEqual(migrated["items"], before["items"])
        self.assertEqual(migrated["address"], before["address"])
        self.assertEqual(migrated["payment"], before["payment"])
        with migrated_store.connect() as connection:
            event = connection.execute(
                """
                SELECT event_type,actor,to_status FROM order_events
                WHERE order_id=?
                """,
                (order_id,),
            ).fetchone()
            foreign_key_errors = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(tuple(event), ("ORDER_PLACED", "SYSTEM", "PREPARING"))
        self.assertEqual(foreign_key_errors, [])

    def test_cancel_is_account_scoped_origin_protected_and_exactly_once(self) -> None:
        cookie, session_digest, order_id, order = self.placed_order(
            "cancel-owner@example.test",
            key="place-cancel-lifecycle-0001",
            quantity=3,
        )
        fields = {
            "orderID": str(order_id),
            "idempotencyKey": order["action_idempotency_keys"]["cancel"],
            "actionToken": order["action_tokens"]["cancel"],
        }
        status, _, body = self.request(
            "GET", f"{ORDER_DETAIL_PATH}?orderID={order_id}", cookie=cookie
        )
        self.assertEqual(status, 200)
        initial_html = body.decode("utf-8")
        self.assertIn("Cancel order", initial_html)
        self.assertIn("Local shipment simulation", initial_html)
        self.assertNotIn(">Return items<", initial_html)

        attacker = self.register(
            self.anonymous_cookie(), "cancel-attacker@example.test"
        )
        status, _, _ = self.request(
            "POST", ORDER_CANCEL_PATH, fields=fields, cookie=attacker
        )
        self.assertEqual(status, 404)

        bad_token_fields = {**fields, "actionToken": "0" * 64}
        status, _, _ = self.request(
            "POST", ORDER_CANCEL_PATH, fields=bad_token_fields, cookie=cookie
        )
        self.assertEqual(status, 403)

        status, _, _ = self.request(
            "POST",
            ORDER_CANCEL_PATH,
            fields=fields,
            cookie=cookie,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        status, _, _ = self.request(
            "POST",
            ORDER_CANCEL_PATH,
            fields={**fields, "amountMinor": "1"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)

        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM refunds").fetchone()[0], 0)
            self.assertEqual(
                conn.execute(
                    "SELECT lifecycle_status FROM shipments WHERE order_id=?",
                    (order_id,),
                ).fetchone()[0],
                "PREPARING",
            )

        expected_location = f"{ORDER_DETAIL_PATH}?orderID={order_id}"
        self.assert_redirect(
            self.request("POST", ORDER_CANCEL_PATH, fields=fields, cookie=cookie),
            expected_location,
        )
        self.assert_redirect(
            self.request("POST", ORDER_CANCEL_PATH, fields=fields, cookie=cookie),
            expected_location,
        )

        cancelled = self.store.order_for_session(session_digest, order_id)
        self.assertIsNotNone(cancelled)
        assert cancelled is not None
        self.assertEqual(cancelled["placement_status"], "PLACED")
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(cancelled["shipment"]["lifecycle_status"], "CANCELLED")
        self.assertEqual(len(cancelled["refunds"]), 1)
        self.assertEqual(cancelled["refunds"][0]["kind"], "CANCELLATION")
        self.assertEqual(cancelled["refunds"][0]["status"], "COMPLETED")
        self.assertTrue(cancelled["refunds"][0]["is_simulation"])

        with self.store.connect() as conn:
            facts = conn.execute(
                """
                SELECT orders.status AS placement_status,orders.total_minor,
                       orders.currency,orders.payment_attempt_id,
                       checkout_sessions.status AS checkout_status,
                       payment_attempts.status AS payment_status,
                       shipments.status AS legacy_shipment_status,
                       shipments.lifecycle_status,shipments.cancelled_at
                FROM orders
                JOIN checkout_sessions USING (checkout_id)
                JOIN payment_attempts USING (payment_attempt_id)
                JOIN shipments USING (order_id)
                WHERE orders.order_id=?
                """,
                (order_id,),
            ).fetchone()
            refund_rows = conn.execute(
                "SELECT * FROM refunds WHERE order_id=?", (order_id,)
            ).fetchall()
            cancelled_events = conn.execute(
                """
                SELECT COUNT(*) FROM order_events
                WHERE order_id=? AND event_type='ORDER_CANCELLED'
                """,
                (order_id,),
            ).fetchone()[0]
            action_rows = conn.execute(
                """
                SELECT COUNT(*) FROM order_action_keys
                WHERE order_id=? AND action_type='CANCEL'
                """,
                (order_id,),
            ).fetchone()[0]
            email_rows = conn.execute(
                "SELECT COUNT(*) FROM email_outbox WHERE order_id=?", (order_id,)
            ).fetchone()[0]
        assert facts is not None
        self.assertEqual(facts["placement_status"], "PLACED")
        self.assertEqual(facts["checkout_status"], "PLACED")
        self.assertEqual(facts["payment_status"], "APPROVED")
        self.assertEqual(facts["legacy_shipment_status"], "PREPARING")
        self.assertEqual(facts["lifecycle_status"], "CANCELLED")
        self.assertIsNotNone(facts["cancelled_at"])
        self.assertEqual(len(refund_rows), 1)
        refund = refund_rows[0]
        self.assertEqual(refund["amount_minor"], facts["total_minor"])
        self.assertEqual(refund["currency"], facts["currency"])
        self.assertEqual(refund["payment_attempt_id"], facts["payment_attempt_id"])
        self.assertEqual(refund["is_simulation"], 1)
        self.assertEqual((cancelled_events, action_rows, email_rows), (1, 1, 1))
        self.assertEqual(self.store.cart(session_digest), [])
        status, _, body = self.request("GET", expected_location, cookie=cookie)
        self.assertEqual(status, 200)
        cancelled_html = body.decode("utf-8")
        self.assertIn("Cancelled", cancelled_html)
        self.assertIn("Cancellation refund", cancelled_html)
        self.assertIn("No money was moved", cancelled_html)
        self.assertNotIn("Cancel order</button>", cancelled_html)

    def test_admin_shipping_requires_capability_and_enforces_exact_sequence(self) -> None:
        cookie, _, order_id, order = self.placed_order(
            "shipping-owner@example.test", key="place-shipping-lifecycle-0001"
        )

        for token in (None, "wrong-admin-token"):
            with self.subTest(token=token):
                status, payload = self.admin_post(
                    "/__bench/orders/advance",
                    {"orderID": order_id, "targetStatus": "SHIPPED"},
                    token=token,
                )
                self.assertEqual((status, payload), (404, {"error": "not-found"}))

        status, payload = self.admin_post(
            "/__bench/orders/advance",
            {"orderID": order_id, "targetStatus": "DELIVERED"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "state-conflict")

        return_fields = {
            "orderID": str(order_id),
            "reasonCode": "DAMAGED",
            "customerNote": "Not yet delivered",
            "idempotencyKey": order["action_idempotency_keys"]["return"],
            "actionToken": order["action_tokens"]["return"],
        }
        status, _, _ = self.request(
            "POST", RETURN_CREATE_PATH, fields=return_fields, cookie=cookie
        )
        self.assertEqual(status, 409)

        status, shipped_payload = self.admin_post(
            "/__bench/orders/advance",
            {"orderID": order_id, "targetStatus": "SHIPPED"},
        )
        self.assertEqual(status, 200)
        shipped = shipped_payload["order"]
        self.assertEqual(shipped["placement_status"], "PLACED")
        self.assertEqual(shipped["status"], "SHIPPED")
        self.assertEqual(shipped["shipment"]["lifecycle_status"], "SHIPPED")
        self.assertEqual(shipped["shipment"]["carrier"], "Amazon Clone Local Carrier")
        self.assertRegex(shipped["shipment"]["tracking_code"], r"^ACL-")
        self.assertIsNotNone(shipped["shipment"]["shipped_at"])
        shipped_revision = shipped["shipment"]["revision"]

        status, repeated_shipped_payload = self.admin_post(
            "/__bench/orders/advance",
            {"orderID": order_id, "targetStatus": "SHIPPED"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            repeated_shipped_payload["order"]["shipment"]["revision"],
            shipped_revision,
        )
        self.assertEqual(
            repeated_shipped_payload["order"]["shipment"]["tracking_code"],
            shipped["shipment"]["tracking_code"],
        )

        cancel_fields = {
            "orderID": str(order_id),
            "idempotencyKey": order["action_idempotency_keys"]["cancel"],
            "actionToken": order["action_tokens"]["cancel"],
        }
        status, _, _ = self.request(
            "POST", ORDER_CANCEL_PATH, fields=cancel_fields, cookie=cookie
        )
        self.assertEqual(status, 409)

        status, delivered_payload = self.admin_post(
            "/__bench/orders/advance",
            {"orderID": order_id, "targetStatus": "DELIVERED"},
        )
        self.assertEqual(status, 200)
        delivered = delivered_payload["order"]
        self.assertEqual(delivered["placement_status"], "PLACED")
        self.assertEqual(delivered["status"], "DELIVERED")
        self.assertEqual(delivered["shipment"]["lifecycle_status"], "DELIVERED")
        self.assertIsNotNone(delivered["shipment"]["delivered_at"])
        delivered_revision = delivered["shipment"]["revision"]

        status, repeated_delivered_payload = self.admin_post(
            "/__bench/orders/advance",
            {"orderID": order_id, "targetStatus": "DELIVERED"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            repeated_delivered_payload["order"]["shipment"]["revision"],
            delivered_revision,
        )
        status, payload = self.admin_post(
            "/__bench/orders/advance",
            {"orderID": order_id, "targetStatus": "SHIPPED"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "state-conflict")

        with self.store.connect() as conn:
            event_counts = dict(
                conn.execute(
                    """
                    SELECT event_type,COUNT(*) AS event_count
                    FROM order_events WHERE order_id=?
                    GROUP BY event_type
                    """,
                    (order_id,),
                ).fetchall()
            )
            refund_count = conn.execute(
                "SELECT COUNT(*) FROM refunds WHERE order_id=?", (order_id,)
            ).fetchone()[0]
        self.assertEqual(event_counts["SHIPMENT_SHIPPED"], 1)
        self.assertEqual(event_counts["SHIPMENT_DELIVERED"], 1)
        self.assertNotIn("ORDER_CANCELLED", event_counts)
        self.assertEqual(refund_count, 0)
        self.assert_placement_fact(order_id)
        status, _, body = self.request(
            "GET", f"{ORDER_DETAIL_PATH}?orderID={order_id}", cookie=cookie
        )
        self.assertEqual(status, 200)
        delivered_html = body.decode("utf-8")
        self.assertIn("Delivered", delivered_html)
        self.assertIn(shipped["shipment"]["tracking_code"], delivered_html)
        self.assertIn(">Return items<", delivered_html)
        self.assertNotIn("Cancel order</button>", delivered_html)

    def test_idempotency_key_cannot_be_reused_across_order_action_types(self) -> None:
        cookie, session_digest, first_order_id, first_order = self.placed_order(
            "cross-action-key@example.test",
            key="place-cross-action-first-0001",
        )
        shared_key = "shared-cross-action-key-0001"
        cancel_fields = {
            "orderID": str(first_order_id),
            "idempotencyKey": shared_key,
            "actionToken": first_order["action_tokens"]["cancel"],
        }
        self.assert_redirect(
            self.request(
                "POST", ORDER_CANCEL_PATH, fields=cancel_fields, cookie=cookie
            ),
            f"{ORDER_DETAIL_PATH}?orderID={first_order_id}",
        )

        self.add_item(cookie, checkout_support.FIXTURE_ASIN)
        second_order_id, _ = self.complete_checkout(
            cookie,
            delivery="standard",
            key="place-cross-action-second-0001",
        )
        for target in ("SHIPPED", "DELIVERED"):
            status, _ = self.admin_post(
                "/__bench/orders/advance",
                {"orderID": second_order_id, "targetStatus": target},
            )
            self.assertEqual(status, 200)
        second_order = self.store.order_for_session(
            session_digest, second_order_id
        )
        self.assertIsNotNone(second_order)
        assert second_order is not None
        status, _, body = self.request(
            "POST",
            RETURN_CREATE_PATH,
            fields={
                "orderID": str(second_order_id),
                "reasonCode": "NO_LONGER_NEEDED",
                "customerNote": "",
                "idempotencyKey": shared_key,
                "actionToken": second_order["action_tokens"]["return"],
            },
            cookie=cookie,
        )
        self.assertEqual(status, 409, body)
        with self.store.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM return_requests WHERE order_id=?",
                    (second_order_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM refunds"
                ).fetchone()[0],
                1,
            )

    def test_delivered_return_validation_ownership_and_refund_are_exactly_once(self) -> None:
        cookie, session_digest, order_id, order = self.placed_order(
            "return-owner@example.test",
            key="place-return-lifecycle-0001",
            quantity=2,
        )
        for target in ("SHIPPED", "DELIVERED"):
            status, _ = self.admin_post(
                "/__bench/orders/advance",
                {"orderID": order_id, "targetStatus": target},
            )
            self.assertEqual(status, 200)

        base_fields = {
            "orderID": str(order_id),
            "reasonCode": "DEFECTIVE",
            "customerNote": "Device failed\r\nunder load.",
            "idempotencyKey": order["action_idempotency_keys"]["return"],
            "actionToken": order["action_tokens"]["return"],
        }
        status, _, body = self.request(
            "GET", f"{RETURN_CREATE_PATH}?orderID={order_id}", cookie=cookie
        )
        self.assertEqual(status, 200)
        return_form_html = body.decode("utf-8")
        self.assertIn(
            'action="/gp/your-account/returns/create"', return_form_html
        )
        self.assertIn('name="reasonCode"', return_form_html)
        self.assertIn('value="DEFECTIVE"', return_form_html)
        self.assertIn('maxlength="500"', return_form_html)
        self.assertIn(base_fields["actionToken"], return_form_html)
        attacker = self.register(
            self.anonymous_cookie(), "return-attacker@example.test"
        )
        status, _, _ = self.request(
            "POST", RETURN_CREATE_PATH, fields=base_fields, cookie=attacker
        )
        self.assertEqual(status, 404)

        status, _, _ = self.request(
            "POST",
            RETURN_CREATE_PATH,
            fields={**base_fields, "reasonCode": "CUSTOM_REASON"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            RETURN_CREATE_PATH,
            fields={**base_fields, "customerNote": "x" * 501},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        status, _, _ = self.request(
            "POST",
            RETURN_CREATE_PATH,
            fields={**base_fields, "customerNote": "bad\x00note"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)

        status, headers, body = self.request(
            "POST", RETURN_CREATE_PATH, fields=base_fields, cookie=cookie
        )
        self.assertEqual((status, body), (303, b""))
        location = urlsplit(headers["location"][0])
        self.assertEqual(location.path, RETURN_DETAIL_PATH)
        return_values = parse_qs(location.query).get("returnID", [])
        self.assertEqual(len(return_values), 1)
        return_id = int(return_values[0])

        self.assert_redirect(
            self.request(
                "POST", RETURN_CREATE_PATH, fields=base_fields, cookie=cookie
            ),
            headers["location"][0],
        )
        status, _, body = self.request(
            "GET", headers["location"][0], cookie=cookie
        )
        self.assertEqual(status, 200)
        requested_html = body.decode("utf-8")
        self.assertIn("Return requested", requested_html)
        self.assertIn("No real return or refund", requested_html)
        status, _, _ = self.request("GET", headers["location"][0], cookie=attacker)
        self.assertEqual(status, 404)

        with self.store.connect() as conn:
            request_row = conn.execute(
                "SELECT * FROM return_requests WHERE return_request_id=?",
                (return_id,),
            ).fetchone()
            return_item_totals = conn.execute(
                """
                SELECT COUNT(*) AS item_count,SUM(quantity) AS quantity
                FROM return_request_items WHERE return_request_id=?
                """,
                (return_id,),
            ).fetchone()
            order_item_totals = conn.execute(
                """
                SELECT COUNT(*) AS item_count,SUM(quantity) AS quantity
                FROM order_items WHERE order_id=?
                """,
                (order_id,),
            ).fetchone()
        assert request_row is not None
        self.assertEqual(request_row["status"], "REQUESTED")
        self.assertEqual(request_row["customer_note"], "Device failed\nunder load.")
        self.assertEqual(request_row["is_simulation"], 1)
        self.assertEqual(tuple(return_item_totals), tuple(order_item_totals))

        status, payload = self.admin_post(
            "/__bench/returns/advance",
            {"returnID": return_id, "targetStatus": "RECEIVED"},
            token=None,
        )
        self.assertEqual((status, payload), (404, {"error": "not-found"}))
        status, payload = self.admin_post(
            "/__bench/returns/advance",
            {"returnID": return_id, "targetStatus": "REFUNDED"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "state-conflict")

        status, received_payload = self.admin_post(
            "/__bench/returns/advance",
            {"returnID": return_id, "targetStatus": "RECEIVED"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(received_payload["order"]["status"], "RETURN_RECEIVED")
        received_revision = received_payload["order"]["return_request"]["revision"]
        status, repeated_received_payload = self.admin_post(
            "/__bench/returns/advance",
            {"returnID": return_id, "targetStatus": "RECEIVED"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            repeated_received_payload["order"]["return_request"]["revision"],
            received_revision,
        )

        status, refunded_payload = self.admin_post(
            "/__bench/returns/advance",
            {"returnID": return_id, "targetStatus": "REFUNDED"},
        )
        self.assertEqual(status, 200)
        refunded = refunded_payload["order"]
        self.assertEqual(refunded["placement_status"], "PLACED")
        self.assertEqual(refunded["status"], "RETURN_REFUNDED")
        self.assertEqual(refunded["return_request"]["status"], "REFUNDED")
        self.assertEqual(len(refunded["refunds"]), 1)
        refunded_revision = refunded["return_request"]["revision"]
        status, repeated_refunded_payload = self.admin_post(
            "/__bench/returns/advance",
            {"returnID": return_id, "targetStatus": "REFUNDED"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            repeated_refunded_payload["order"]["return_request"]["revision"],
            refunded_revision,
        )

        status, _, _ = self.request(
            "POST",
            RETURN_CREATE_PATH,
            fields={**base_fields, "idempotencyKey": "second-return-attempt-0001"},
            cookie=cookie,
        )
        self.assertEqual(status, 409)

        final_order = self.store.order_for_session(session_digest, order_id)
        self.assertIsNotNone(final_order)
        assert final_order is not None
        self.assertEqual(final_order["status"], "RETURN_REFUNDED")
        with self.store.connect() as conn:
            facts = conn.execute(
                """
                SELECT orders.status AS placement_status,orders.total_minor,
                       orders.currency,orders.payment_attempt_id,
                       checkout_sessions.status AS checkout_status,
                       payment_attempts.status AS payment_status,
                       shipments.lifecycle_status,return_requests.status AS return_status,
                       return_requests.revision AS return_revision
                FROM orders
                JOIN checkout_sessions USING (checkout_id)
                JOIN payment_attempts USING (payment_attempt_id)
                JOIN shipments USING (order_id)
                JOIN return_requests USING (order_id)
                WHERE orders.order_id=?
                """,
                (order_id,),
            ).fetchone()
            refund_rows = conn.execute(
                "SELECT * FROM refunds WHERE order_id=?", (order_id,)
            ).fetchall()
            event_counts = dict(
                conn.execute(
                    """
                    SELECT event_type,COUNT(*) AS event_count
                    FROM order_events WHERE order_id=?
                    GROUP BY event_type
                    """,
                    (order_id,),
                ).fetchall()
            )
            action_count = conn.execute(
                """
                SELECT COUNT(*) FROM order_action_keys
                WHERE order_id=? AND action_type='RETURN_REQUEST'
                """,
                (order_id,),
            ).fetchone()[0]
        assert facts is not None
        self.assertEqual(facts["placement_status"], "PLACED")
        self.assertEqual(facts["checkout_status"], "PLACED")
        self.assertEqual(facts["payment_status"], "APPROVED")
        self.assertEqual(facts["lifecycle_status"], "DELIVERED")
        self.assertEqual(facts["return_status"], "REFUNDED")
        self.assertEqual(facts["return_revision"], 3)
        self.assertEqual(len(refund_rows), 1)
        refund = refund_rows[0]
        self.assertEqual(refund["kind"], "RETURN")
        self.assertEqual(refund["status"], "COMPLETED")
        self.assertEqual(refund["amount_minor"], facts["total_minor"])
        self.assertEqual(refund["currency"], facts["currency"])
        self.assertEqual(refund["payment_attempt_id"], facts["payment_attempt_id"])
        self.assertEqual(refund["return_request_id"], return_id)
        self.assertEqual(refund["is_simulation"], 1)
        self.assertEqual(event_counts["RETURN_REQUESTED"], 1)
        self.assertEqual(event_counts["RETURN_RECEIVED"], 1)
        self.assertEqual(event_counts["RETURN_REFUNDED"], 1)
        self.assertEqual(action_count, 1)
        self.assert_placement_fact(order_id)


if __name__ == "__main__":
    unittest.main()
