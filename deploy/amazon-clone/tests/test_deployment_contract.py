from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
CLONE_ROOT = REPO_ROOT / "materials" / "amazon" / "clone"
if str(CLONE_ROOT) not in sys.path:
    sys.path.insert(0, str(CLONE_ROOT))

from server import PublicHandler, ReusableThreadingHTTPServer, parse_args  # noqa: E402
from store import Store  # noqa: E402


class DeploymentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.auth_environment = patch.dict(
            os.environ,
            {
                "AMAZON_BASIC_AUTH_USERNAME": "",
                "AMAZON_BASIC_AUTH_PASSWORD": "",
            },
        )
        self.auth_environment.start()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = Store(
            Path(self.tempdir.name) / "amazon.sqlite3",
            CLONE_ROOT / "schema.sql",
            CLONE_ROOT / "fixtures",
        )
        self.store.reset()
        PublicHandler.store = self.store
        self.server = ReusableThreadingHTTPServer(("127.0.0.1", 0), PublicHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()
        self.auth_environment.stop()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, list[str]], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        headers: dict[str, list[str]] = {}
        for name, value in response.getheaders():
            headers.setdefault(name.casefold(), []).append(value)
        body = response.read()
        connection.close()
        return response.status, headers, body

    def test_health_check_is_read_only_and_does_not_create_a_session(self) -> None:
        status, headers, body = self.request("GET", "/healthz")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertNotIn("set-cookie", headers)
        self.assertEqual(self.store.journal(), [])

        status, headers, body = self.request("HEAD", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertNotIn("set-cookie", headers)
        self.assertEqual(self.store.journal(), [])

    def test_public_deployment_flags_secure_cookies_and_response_headers(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AMAZON_COOKIE_SECURE": "1",
                "AMAZON_HSTS": "true",
                "AMAZON_NOINDEX": "yes",
            },
        ):
            status, headers, _ = self.request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn("Secure", headers["set-cookie"][0].split("; "))
        self.assertEqual(
            headers["strict-transport-security"], ["max-age=31536000"]
        )
        self.assertEqual(
            headers["x-robots-tag"], ["noindex, nofollow, noarchive"]
        )

    def test_public_basic_auth_protects_everything_except_health(self) -> None:
        password = "synthetic-演示-secret"
        credentials = base64.b64encode(
            f"bench:{password}".encode("utf-8")
        ).decode("ascii")
        with patch.dict(
            os.environ,
            {
                "AMAZON_BASIC_AUTH_USERNAME": "bench",
                "AMAZON_BASIC_AUTH_PASSWORD": password,
            },
        ):
            status, headers, _ = self.request("GET", "/")
            self.assertEqual(status, 401)
            self.assertNotIn("set-cookie", headers)
            self.assertEqual(
                headers["www-authenticate"],
                ['Basic realm="WebsiteBench Amazon", charset="UTF-8"'],
            )

            status, _, _ = self.request(
                "GET",
                "/static/styles.css",
                headers={"Authorization": "Basic not-valid-base64"},
            )
            self.assertEqual(status, 401)

            status, headers, _ = self.request(
                "GET",
                "/",
                headers={"Authorization": f"Basic {credentials}"},
            )
            self.assertEqual(status, 200)
            self.assertIn("set-cookie", headers)

            status, headers, body = self.request("GET", "/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"ok": True})
            self.assertNotIn("set-cookie", headers)

    def test_database_path_can_be_supplied_by_the_host_environment(self) -> None:
        expected = Path(self.tempdir.name) / "persistent" / "amazon.sqlite3"
        with (
            patch.dict(os.environ, {"AMAZON_DB_PATH": str(expected)}),
            patch.object(sys, "argv", ["server.py"]),
        ):
            args = parse_args()

        self.assertEqual(args.db, expected)
        self.assertEqual(args.admin_host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
