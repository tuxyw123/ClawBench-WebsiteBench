from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urlencode


CLONE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = CLONE_ROOT / "server.py"
CATALOG_PATH = CLONE_ROOT / "static" / "site-catalog.json"
SESSION_COOKIE = "amazon_local_session"
TARGET_ASIN = "B0874XN4D8"
GENERIC_ASIN = "B0D4BOTTLE"
GENERIC_PATH = "/Stainless-Steel-Water-Bottle/dp/B0D4BOTTLE"
BEST_SELLERS_PATH = "/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
PRODUCT_PATH = "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
TERMINAL_PATH = "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance"


@dataclass(frozen=True)
class Response:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))


class Client:
    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie: str | None = None

    @property
    def session_id(self) -> str:
        if not self.cookie:
            raise AssertionError("client has no session cookie")
        return self.cookie.split("=", 1)[1]

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        request_headers = dict(headers or {})
        if self.cookie:
            request_headers.setdefault("Cookie", self.cookie)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            raw = connection.getresponse()
            response = Response(raw.status, tuple(raw.getheaders()), raw.read())
        finally:
            connection.close()
        for name, value in response.headers:
            if name.casefold() == "set-cookie" and value.startswith(
                f"{SESSION_COOKIE}="
            ):
                self.cookie = value.split(";", 1)[0]
        return response

    def json_request(
        self,
        method: str,
        path: str,
        payload: object,
        *,
        origin: str | None = None,
        content_type: str = "application/json",
    ) -> Response:
        headers = {"Content-Type": content_type}
        if origin is not None:
            headers["Origin"] = origin
        return self.request(
            method,
            path,
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
        )


class ManagedServer:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="amazon-site-model-")
        self.db_path = Path(self.temporary.name) / "amazon.sqlite3"
        self.port = self._reserve_port()
        self.process: subprocess.Popen[str] | None = None
        try:
            self.start()
        except BaseException:
            self.stop()
            self.temporary.cleanup()
            raise

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def start(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(SERVER_PATH),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--db",
                str(self.db_path),
            ],
            cwd=CLONE_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                output = self.process.stdout.read() if self.process.stdout else ""
                raise RuntimeError(f"server exited during startup: {output}")
            try:
                response = Client(self.port).request("HEAD", "/")
            except OSError:
                time.sleep(0.03)
                continue
            if response.status == 200:
                return
        self.stop()
        raise RuntimeError("server did not accept loopback connections")

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        if process.stdout:
            process.stdout.close()

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        self.stop()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) != 0:
                    break
            time.sleep(0.02)
        else:
            raise AssertionError("server socket remained open after process cleanup")
        self.temporary.cleanup()


class SiteModelIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ManagedServer()

    def tearDown(self) -> None:
        self.server.close()

    def bootstrap(self, client: Client) -> dict[str, object]:
        response = client.request("GET", "/api/bootstrap")
        self.assertEqual(response.status, 200)
        payload = response.json()
        self.assertIsInstance(payload, dict)
        return payload  # type: ignore[return-value]

    def db_rows(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> list[sqlite3.Row]:
        with sqlite3.connect(self.server.db_path, timeout=4) as db:
            db.row_factory = sqlite3.Row
            return db.execute(sql, parameters).fetchall()

    def test_document_routes_and_head_are_non_mutating(self) -> None:
        client = Client(self.server.port)
        self.bootstrap(client)
        routes = (
            "/Best-Sellers/zgbs",
            "/Best-Sellers/zgbs/",
            "/gp/goldbox",
            "/gp/goldbox/",
            "/b",
            "/Computers-Accessories/b/?node=541966",
            "/computers-pc-hardware-accessories-add-ons/b/?node=541966",
            GENERIC_PATH,
            "/hz/wishlist/ls",
            "/hz/history",
            "/account",
            "/account/orders",
        )
        before = self.db_rows(
            "SELECT * FROM recent_views WHERE session_id = ?", (client.session_id,)
        )
        for route in routes:
            with self.subTest(route=route):
                response = client.request("HEAD", route)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.body, b"")
        after = self.db_rows(
            "SELECT * FROM recent_views WHERE session_id = ?", (client.session_id,)
        )
        self.assertEqual(before, after)

        self.assertEqual(client.request("GET", GENERIC_PATH).status, 200)
        recent = self.bootstrap(client)["recent_views"]
        self.assertEqual([item["asin"] for item in recent], [GENERIC_ASIN])
        self.assertEqual(client.request("GET", "/unknown/page").status, 404)
        self.assertEqual(client.request("GET", "/unknown/b/").status, 404)
        self.assertEqual(
            client.request("GET", "/Unknown-Product/dp/B000000000").status,
            404,
        )

    def test_search_suggestions_and_bounded_deduplicated_history(self) -> None:
        client = Client(self.server.port)
        initial = self.bootstrap(client)
        self.assertEqual(len(initial["products"]), 6)
        self.assertEqual(initial["products"][1]["asin"], TARGET_ASIN)

        search = client.request("GET", "/api/search?k=water+bottle")
        self.assertEqual(search.status, 200)
        payload = search.json()
        self.assertEqual(set(payload), {"query", "products", "count"})
        self.assertEqual(payload["count"], 1)
        product = payload["products"][0]
        self.assertEqual(product["asin"], GENERIC_ASIN)
        self.assertEqual(product["brand"], "Trail & Tide")
        self.assertIn("specs", product)

        task_search = client.request("GET", "/api/search?k=samsung+t7").json()
        self.assertEqual(task_search["count"], 1)
        self.assertEqual(task_search["products"][0]["asin"], TARGET_ASIN)
        empty = client.request("GET", "/api/search?k=").json()
        self.assertEqual(empty["count"], 6)

        client.request("GET", "/api/search?k=Water%20Bottle")
        for index in range(12):
            client.request(
                "GET", f"/api/search?k={quote_plus(f'unique query {index}')}"
            )
        client.request("GET", "/api/search?k=water+bottle")
        history = self.bootstrap(client)["search_history"]
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0]["query"], "water bottle")
        self.assertEqual(
            len({item["query"].casefold() for item in history}), len(history)
        )

        suggestions = client.request("GET", "/api/suggestions?q=water").json()
        self.assertEqual(set(suggestions), {"suggestions"})
        self.assertEqual(suggestions["suggestions"][0], "water bottle")
        self.assertIn(
            "Stainless Steel Water Bottle with Leakproof Cap, 24 oz, Teal",
            suggestions["suggestions"],
        )
        self.assertEqual(client.request("GET", "/api/suggestions").status, 400)
        self.assertEqual(
            client.request("GET", "/api/suggestions?q=one&q=two").status,
            400,
        )
        self.assertEqual(
            client.request("GET", f"/api/suggestions?q={'x' * 81}").status,
            400,
        )

    def test_generic_cart_and_wishlist_lifecycle_is_idempotent(self) -> None:
        client = Client(self.server.port)
        add = client.json_request(
            "POST",
            "/api/cart/add",
            {"asin": GENERIC_ASIN, "quantity": 2},
            origin=client.origin,
        )
        self.assertEqual(add.status, 200)
        self.assertEqual(add.json()["outcome"], "generic_cart_upserted")
        repeat = client.json_request(
            "POST",
            "/api/cart/add",
            {"asin": GENERIC_ASIN, "quantity": 2},
            origin=client.origin,
        )
        self.assertEqual(repeat.status, 200)
        cart = self.bootstrap(client)["cart"]
        self.assertEqual(cart["total_quantity"], 2)
        self.assertEqual(len(cart["items"]), 1)
        self.assertEqual(cart["items"][0]["product"]["brand"], "Trail & Tide")

        saved = client.request(
            "POST", f"/api/cart/{GENERIC_ASIN}/save-for-later", body=b""
        )
        self.assertEqual(saved.status, 200)
        self.assertEqual(self.bootstrap(client)["cart"]["items"], [])
        client.json_request(
            "POST",
            "/api/cart/add",
            {"asin": GENERIC_ASIN, "quantity": 3},
            origin=client.origin,
        )
        refreshed = self.bootstrap(client)
        self.assertEqual(refreshed["saved_for_later"], [])
        self.assertEqual(refreshed["cart"]["total_quantity"], 3)

        first_list = client.json_request(
            "POST", "/api/list", {"asin": GENERIC_ASIN}, origin=client.origin
        )
        second_list = client.json_request(
            "POST", "/api/list", {"asin": GENERIC_ASIN}, origin=client.origin
        )
        self.assertEqual(first_list.json()["outcome"], "wishlist_added")
        self.assertEqual(second_list.json()["outcome"], "wishlist_unchanged")
        listed = client.request("GET", "/api/list").json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["items"][0]["asin"], GENERIC_ASIN)
        self.assertEqual(len(self.bootstrap(client)["wishlist"]), 1)

        deleted = client.request("DELETE", f"/api/list/{GENERIC_ASIN}", body=b"")
        absent = client.request("DELETE", f"/api/list/{GENERIC_ASIN}", body=b"")
        self.assertEqual(deleted.json()["outcome"], "wishlist_deleted")
        self.assertEqual(absent.json()["outcome"], "wishlist_absent")
        self.assertEqual(client.request("GET", "/api/list").json()["items"], [])
        self.assertEqual(
            client.request("DELETE", f"/api/cart/{GENERIC_ASIN}", body=b"").status,
            200,
        )
        self.assertEqual(self.bootstrap(client)["cart"]["items"], [])

    def test_generic_mutation_validation_methods_and_preferences(self) -> None:
        client = Client(self.server.port)
        self.assertEqual(
            client.json_request(
                "POST",
                "/api/cart/add",
                {"asin": GENERIC_ASIN, "quantity": 1},
                origin="https://www.amazon.com",
            ).status,
            403,
        )
        self.assertEqual(
            client.json_request(
                "POST",
                "/api/cart/add",
                {"asin": GENERIC_ASIN, "quantity": 1},
                content_type="text/plain",
            ).status,
            415,
        )
        invalid_payloads = (
            {"asin": GENERIC_ASIN},
            {"asin": GENERIC_ASIN, "quantity": True},
            {"asin": GENERIC_ASIN, "quantity": 4},
            {"asin": "lowercase1", "quantity": 1},
            {"asin": GENERIC_ASIN, "quantity": 1, "extra": "no"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(
                    client.json_request("POST", "/api/cart/add", payload).status,
                    400,
                )
        task_product_add = client.json_request(
            "POST", "/api/cart/add", {"asin": TARGET_ASIN, "quantity": 2}
        )
        self.assertEqual(task_product_add.status, 200)
        self.assertEqual(task_product_add.json()["item"]["asin"], TARGET_ASIN)
        self.assertEqual(client.request("POST", "/api/suggestions?q=x").status, 404)
        self.assertEqual(client.request("PATCH", "/api/list").status, 404)
        self.assertEqual(client.request("PUT", "/api/cart/add").status, 405)
        for kind, expected in (("delivery", "New York 10001"), ("language", "en-US")):
            response = client.json_request(
                "POST",
                "/api/session/preferences",
                {"kind": kind},
                origin=client.origin,
            )
            self.assertEqual(response.status, 200)
            result = response.json()
            self.assertEqual(result["value"], expected)
            self.assertEqual(result["status"], "local-no-effect")
            self.assertFalse(
                {"credential", "address", "payment", "email"}.intersection(result)
            )
        self.assertEqual(
            client.json_request(
                "POST", "/api/session/preferences", {"kind": "payment"}
            ).status,
            400,
        )
        self.assertEqual(
            client.json_request(
                "POST",
                "/api/session/preferences",
                {"kind": "delivery", "address": "forbidden"},
            ).status,
            400,
        )

    def test_restart_persistence_and_session_isolation(self) -> None:
        first = Client(self.server.port)
        second = Client(self.server.port)
        first.json_request(
            "POST",
            "/api/cart/add",
            {"asin": GENERIC_ASIN, "quantity": 2},
            origin=first.origin,
        )
        first.json_request("POST", "/api/list", {"asin": GENERIC_ASIN})
        first.request("GET", "/api/search?k=water+bottle")
        first.request("GET", GENERIC_PATH)
        isolated = self.bootstrap(second)
        self.assertEqual(isolated["cart"]["items"], [])
        self.assertEqual(isolated["wishlist"], [])
        self.assertEqual(isolated["recent_views"], [])
        self.assertEqual(isolated["search_history"], [])
        self.assertNotEqual(first.session_id, second.session_id)

        self.server.restart()
        persisted = self.bootstrap(first)
        self.assertEqual(persisted["cart"]["total_quantity"], 2)
        self.assertEqual(persisted["wishlist"][0]["asin"], GENERIC_ASIN)
        self.assertEqual(persisted["recent_views"][0]["asin"], GENERIC_ASIN)
        self.assertEqual(persisted["search_history"][0]["query"], "water bottle")
        self.assertEqual(self.bootstrap(second)["cart"]["items"], [])

    def test_recent_views_are_bounded_and_deduplicated(self) -> None:
        client = Client(self.server.port)
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        products = catalog["products"]
        for product in products:
            path = f"/{product['slug']}/dp/{product['asin']}"
            self.assertEqual(client.request("GET", path).status, 200)
        recent = self.bootstrap(client)["recent_views"]
        self.assertEqual(len(recent), 10)
        first = products[0]
        first_path = f"/{first['slug']}/dp/{first['asin']}"
        client.request("GET", first_path)
        updated = self.bootstrap(client)["recent_views"]
        self.assertEqual(len(updated), 10)
        self.assertEqual(updated[0]["asin"], first["asin"])
        rows = self.db_rows(
            "SELECT asin FROM recent_views WHERE session_id = ?", (client.session_id,)
        )
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({row["asin"] for row in rows}), 10)

    def test_generic_actions_cannot_create_terminal_outcomes(self) -> None:
        client = Client(self.server.port)
        client.json_request(
            "POST",
            "/api/cart/add",
            {"asin": GENERIC_ASIN, "quantity": 2},
            origin=client.origin,
        )
        client.json_request("POST", "/api/list", {"asin": GENERIC_ASIN})
        client.request("GET", GENERIC_PATH)
        terminal_without_discovery = client.request(
            "POST",
            TERMINAL_PATH,
            body=urlencode({"ASIN": TARGET_ASIN, "quantity": "2"}).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(terminal_without_discovery.status, 403)
        self.assertEqual(
            terminal_without_discovery.json()["outcome"], "discovery_required"
        )
        terminal_rows = self.db_rows(
            """
            SELECT outcome FROM request_journal
            WHERE session_id = ? AND outcome = 'terminal_added'
            """,
            (client.session_id,),
        )
        self.assertEqual(terminal_rows, [])

        client.request("GET", BEST_SELLERS_PATH)
        client.request("GET", PRODUCT_PATH)
        completed = client.request(
            "POST",
            TERMINAL_PATH,
            body=urlencode({"ASIN": TARGET_ASIN, "quantity": "2"}).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(completed.status, 303)
        terminal_rows = self.db_rows(
            """
            SELECT path, asin, quantity FROM request_journal
            WHERE session_id = ? AND outcome = 'terminal_added'
            """,
            (client.session_id,),
        )
        self.assertEqual(len(terminal_rows), 1)
        self.assertEqual(terminal_rows[0]["path"], TERMINAL_PATH)
        self.assertEqual(terminal_rows[0]["asin"], TARGET_ASIN)
        self.assertEqual(terminal_rows[0]["quantity"], 2)

    def test_sqlite_integrity_foreign_keys_indexes_and_hidden_state(self) -> None:
        client = Client(self.server.port)
        self.bootstrap(client)
        bootstrap = self.bootstrap(client)
        self.assertFalse(
            {"request_journal", "terminal_added", "terminal_eligible"}.intersection(
                bootstrap
            )
        )
        with sqlite3.connect(self.server.db_path, timeout=4) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table in ("search_history", "recent_views", "wishlist"):
                foreign_keys = db.execute(
                    f"PRAGMA foreign_key_list({table})"
                ).fetchall()
                self.assertTrue(any(row[2] == "sessions" for row in foreign_keys))
                indexes = db.execute(f"PRAGMA index_list({table})").fetchall()
                self.assertGreaterEqual(len(indexes), 2)


if __name__ == "__main__":
    unittest.main()
