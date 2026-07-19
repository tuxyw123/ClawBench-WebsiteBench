#!/usr/bin/env python3
"""Audit the Amazon dev-136 clone through its public UI and persisted state."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import re
import select
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit

from jsonschema import Draft202012Validator
from PIL import Image
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[4]
CLONE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = CLONE_ROOT / "server.py"
TASK_PATH = REPO_ROOT / "tasks/clawbench/dev-136-amazon-t7-best-seller/task.json"
SCHEMA_PATH = REPO_ROOT / "schemas/task.schema.json"
SOURCE_EVIDENCE_PATH = CLONE_ROOT / "SOURCE_EVIDENCE.md"
REPORT_PATH = CLONE_ROOT / "verification-report.json"

HOST = "127.0.0.1"
CANONICAL_PORT = 8153
PORT = CANONICAL_PORT
BASE_URL = f"http://{HOST}:{PORT}"
SESSION_COOKIE = "amazon_local_session"
TARGET_ASIN = "B0874XN4D8"
WRONG_ASIN = "B08HN37XC1"
GENERIC_ASIN = "B0D4BOTTLE"
GENERIC_PRODUCT_PATH = "/Stainless-Steel-Water-Bottle/dp/B0D4BOTTLE"
BEST_SELLERS_PATH = "/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011"
PRODUCT_PATH = "/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8"
MOBILE_PRODUCT_PATH = "/gp/aw/d/B0874XN4D8"
CART_PATH = "/gp/cart/view.html"
TERMINAL_PATHS = {
    "desktop": "/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance",
    "mobile": "/cart/add-to-cart/ref=mw_dp_buy_crt",
}
VIEWPORTS = {
    "desktop": {"width": 1365, "height": 900},
    "mobile": {"width": 390, "height": 844},
}
SCREENSHOT_STAGES = (
    "empty-cart",
    "best-sellers",
    "target-product",
    "populated-cart",
    "search-results",
    "no-results",
    "boundary",
    "not-found",
)
EXPECTED_SCREENSHOT_COUNT = len(VIEWPORTS) * len(SCREENSHOT_STAGES)
REVIEW_ENV = "CLAWBENCH_SCREENSHOT_REVIEW"
REVIEW_ACK = "I REVIEWED ALL ORIGINAL SCREENSHOTS"
REVIEW_TIMEOUT_SECONDS = 300
RUN_DATE = "2026-07-18"
MAX_BODY_BYTES = 16 * 1024
EXPECTED_SUBTOTAL = 439.98

SOURCE_SCREENSHOT_HASHES = (
    "4f81ec89aead6271226520e02132368464743972c0d423d78172ad5f67069c29",
    "0007f36c700346ab4b20596b6eba8f0443f2b55703a2164dc53100e624512f5b",
    "4e98f3b8e458467886efe7c2feb25995705836a31bb9fd8d4cb7c7fec36cb1f2",
    "5084da371f0d30df6277423146fc15c45c29945d7e93f4dab5bc2c767bc4c7ba",
    "215ee58f8c383ceab250ecc68f7d42fb30a7d4934468f783f33a245a8f464fda",
    "ee800a31aff31e0647bcbb9f5ae57b845461531c2a5fa3a2917b58b2d986bb55",
)
SOURCE_BASELINE_STATES = (
    {
        "state": "Best Sellers",
        "viewport": "desktop",
        "clone_stage": "best-sellers",
        "sha256": SOURCE_SCREENSHOT_HASHES[0],
    },
    {
        "state": "Samsung T7",
        "viewport": "desktop",
        "clone_stage": "target-product",
        "sha256": SOURCE_SCREENSHOT_HASHES[1],
    },
    {
        "state": "Empty cart",
        "viewport": "desktop",
        "clone_stage": "empty-cart",
        "sha256": SOURCE_SCREENSHOT_HASHES[2],
    },
    {
        "state": "Best Sellers",
        "viewport": "mobile",
        "clone_stage": "best-sellers",
        "sha256": SOURCE_SCREENSHOT_HASHES[3],
    },
    {
        "state": "Samsung T7",
        "viewport": "mobile",
        "clone_stage": "target-product",
        "sha256": SOURCE_SCREENSHOT_HASHES[4],
    },
    {
        "state": "Empty cart",
        "viewport": "mobile",
        "clone_stage": "empty-cart",
        "sha256": SOURCE_SCREENSHOT_HASHES[5],
    },
)
REVIEW_CHECKLISTS = {
    "empty-cart": (
        "source layout hierarchy and responsive account controls",
        "empty-cart artwork, spacing, and footer composition",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "best-sellers": (
        "source category hierarchy, header, and responsive grid or list geometry",
        "rank-two Samsung T7 identity, rating, reviews, image, and price",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "target-product": (
        "source product gallery, facts, variants, price, and buy-box hierarchy",
        "quantity control and responsive Add to cart form remain reachable",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "populated-cart": (
        "target Samsung T7, quantity two, item count, and subtotal are visible",
        "update, delete, save, checkout, and responsive cart hierarchy are coherent",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "search-results": (
        "Samsung T7 result identity and result count are legible",
        "search and responsive navigation hierarchy remain coherent",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "no-results": (
        "empty-search result state and query feedback are explicit",
        "page rhythm and responsive navigation remain coherent",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "boundary": (
        "local checkout boundary is conspicuous and cart state remains visible",
        "return action and responsive dialog hierarchy are coherent",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
    "not-found": (
        "customer-facing 404 state is explicit and source-consistent",
        "navigation and recovery affordances remain coherent",
        "no clipped controls, overlap, broken media, or horizontal overflow",
    ),
}

EXPECTED_METADATA = {
    "task_id": 900136,
    "metaclass": "shopping",
    "class": "retail",
    "description": (
        "Browse Amazon Best Sellers for external SSDs and add two units of the "
        "number-two Samsung T7 1TB Gray drive to the local cart."
    ),
    "sites_involved": ["amazon.local"],
    "platform": "amazon",
    "clone_path": "materials/amazon/clone",
    "server_command": (
        "python3 materials/amazon/clone/server.py "
        "--host 127.0.0.1 --port 8153"
    ),
    "verify_command": (
        "python3 materials/amazon/clone/tools/verify_task.py"
    ),
    "dev_only": True,
}
EXPECTED_INSTRUCTION = (
    "Open http://host.docker.internal:8153/. On Amazon, browse Best Sellers in "
    "External Solid State Drives, open the #2 Samsung T7 Portable SSD 1TB in Gray, "
    "choose quantity 2, and add it to the cart."
)
EXPECTED_EVAL = {
    "url_pattern": (
        r"^http://(localhost|127\.0\.0\.1|host\.docker\.internal):8153/"
        r"(gp/product/handle-buy-box/ref=dp_start-bbf_1_glance|"
        r"cart/add-to-cart/ref=mw_dp_buy_crt)$"
    ),
    "method": "POST",
    "body": {"ASIN": TARGET_ASIN, "quantity": "2"},
}
EXPECTED_STEPS = [
    {
        "name": "open_external_ssd_best_sellers",
        "url_pattern": (
            r"^http://(localhost|127\.0\.0\.1|host\.docker\.internal):8153/"
            r"Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011$"
        ),
        "method": "GET",
    },
    {
        "name": "open_rank_two_samsung_t7",
        "url_pattern": (
            r"^http://(localhost|127\.0\.0\.1|host\.docker\.internal):8153/"
            r"SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8$"
        ),
        "method": "GET",
    },
]


class VerificationFailure(AssertionError):
    """A named audit assertion failed."""


class ScreenshotReviewIncomplete(RuntimeError):
    """The required original-resolution screenshot review was not acknowledged."""


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(
        self,
        category: str,
        name: str,
        condition: Any,
        detail: Any = None,
    ) -> None:
        passed = bool(condition)
        self.items.append({"category": category, "name": name, "passed": passed})
        if not passed:
            suffix = "" if detail is None else f": {detail}"
            raise VerificationFailure(f"{name}{suffix}")

    def counts(self, category: str) -> dict[str, int]:
        selected = [item for item in self.items if item["category"] == category]
        return {
            "passed": sum(bool(item["passed"]) for item in selected),
            "total": len(selected),
        }

    def overall(self) -> dict[str, int]:
        return {
            "passed": sum(bool(item["passed"]) for item in self.items),
            "total": len(self.items),
        }


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header(self, name: str) -> str | None:
        lowered = name.casefold()
        return next(
            (value for key, value in self.headers if key.casefold() == lowered),
            None,
        )

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HTTPClient:
    def __init__(self, cookie: str | None = None) -> None:
        self.cookie = cookie
        self.set_cookie_headers: list[str] = []

    @property
    def session_id(self) -> str:
        if not self.cookie or "=" not in self.cookie:
            raise VerificationFailure("HTTP client has no session cookie")
        name, value = self.cookie.split("=", 1)
        if name != SESSION_COOKIE or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise VerificationFailure("HTTP client session cookie is malformed")
        return value

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        request_headers = dict(headers or {})
        if self.cookie:
            request_headers.setdefault("Cookie", self.cookie)
        connection = http.client.HTTPConnection(HOST, PORT, timeout=4)
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            response_headers = tuple(response.getheaders())
            response_body = response.read()
        finally:
            connection.close()
        for key, value in response_headers:
            if key.casefold() == "set-cookie":
                self.set_cookie_headers.append(value)
                pair = value.split(";", 1)[0]
                if pair.startswith(f"{SESSION_COOKIE}="):
                    self.cookie = pair
        return HTTPResponse(response.status, response_headers, response_body)


def port_is_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((HOST, PORT)) != 0


def cleanup_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}-shm"), Path(f"{path}-wal")):
        candidate.unlink(missing_ok=True)


class ManagedServer:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="clawbench-amazon-136-verifier-"))
        self.db_path = self.root / "state.sqlite3"
        self.log_path = self.root / "server.log"
        self.browser_root = self.root / "browser"
        self.profile_root = self.browser_root / "profiles"
        self.artifact_root = self.root / "screenshots"
        self.process: subprocess.Popen[str] | None = None
        self.processes: list[subprocess.Popen[str]] = []
        self.log_handle: TextIO | None = None
        self.restart_count = 0
        self.release_proofs: list[bool] = []

    def start(self) -> None:
        if not port_is_free():
            raise VerificationFailure(f"canonical port {PORT} is occupied")
        cleanup_database(self.db_path)
        self.log_path.unlink(missing_ok=True)
        self.browser_root.mkdir(mode=0o700)
        self.profile_root.mkdir(mode=0o700)
        self.artifact_root.mkdir(mode=0o700)
        self._launch("w")

    def _launch(self, mode: str) -> None:
        self.log_handle = self.log_path.open(mode, encoding="utf-8")
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": str(self.browser_root),
            "TMP": str(self.browser_root),
            "TEMP": str(self.browser_root),
        }
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(SERVER_PATH),
                "--host",
                HOST,
                "--port",
                str(PORT),
                "--db",
                str(self.db_path),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        self.processes.append(self.process)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise VerificationFailure(
                    "managed server exited during startup: " + self.read_log()
                )
            try:
                response = HTTPClient().request("HEAD", "/")
                if response.status == 200:
                    return
            except OSError:
                time.sleep(0.1)
        raise VerificationFailure(
            "managed server did not become ready: " + self.read_log()
        )

    def read_log(self) -> str:
        if self.log_handle:
            self.log_handle.flush()
        if not self.log_path.exists():
            return "(no server log)"
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-3000:]

    def _stop_current(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not port_is_free():
            time.sleep(0.1)
        released = port_is_free()
        self.release_proofs.append(released)
        if process is not None and process.poll() is None:
            raise VerificationFailure("managed server process survived shutdown")
        if not released:
            raise VerificationFailure(f"canonical port {PORT} was not released")

    def restart(self) -> None:
        if self.process is None or self.process.poll() is not None:
            raise VerificationFailure("managed server is not running before restart")
        prior_pid = self.process.pid
        self._stop_current()
        if not self.db_path.is_file():
            raise VerificationFailure("same database disappeared before restart")
        self._launch("a")
        self.restart_count += 1
        if self.process.pid == prior_pid:
            raise VerificationFailure("restart did not create a new server process")

    def stop_and_clean(self) -> dict[str, bool]:
        if self.process is not None:
            self._stop_current()
        elif self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        cleanup_database(self.db_path)
        self.log_path.unlink(missing_ok=True)
        shutil.rmtree(self.browser_root, ignore_errors=True)
        shutil.rmtree(self.artifact_root, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)
        proof = {
            "owned_process_stopped": all(
                process.poll() is not None for process in self.processes
            ),
            "canonical_port_released": port_is_free()
            and bool(self.release_proofs)
            and all(self.release_proofs),
            "temporary_workspace_removed": not self.root.exists(),
            "database_artifacts_removed": not any(
                candidate.exists()
                for candidate in (
                    self.db_path,
                    Path(f"{self.db_path}-shm"),
                    Path(f"{self.db_path}-wal"),
                )
            ),
            "server_log_removed": not self.log_path.exists(),
            "browser_workspace_removed": not self.browser_root.exists(),
            "screenshot_staging_removed": not self.artifact_root.exists(),
        }
        if not all(proof.values()):
            raise VerificationFailure(f"managed cleanup proof failed: {proof}")
        return proof


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def form_body(asin: str = TARGET_ASIN, quantity: str = "2") -> bytes:
    return urlencode({"ASIN": asin, "quantity": quantity}).encode("ascii")


def request_form(
    client: HTTPClient,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> HTTPResponse:
    request_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    request_headers.update(headers or {})
    return client.request(
        "POST",
        path,
        body=form_body() if body is None else body,
        headers=request_headers,
    )


def database_counts(path: Path, session_id: str) -> dict[str, Any]:
    with closing(sqlite3.connect(path, timeout=5)) as db:
        return {
            "cart": db.execute(
                "SELECT asin, quantity FROM cart WHERE session_id = ? ORDER BY asin",
                (session_id,),
            ).fetchall(),
            "saved": db.execute(
                "SELECT asin, quantity FROM saved WHERE session_id = ? ORDER BY asin",
                (session_id,),
            ).fetchall(),
            "discovery": db.execute(
                "SELECT path, kind, asin FROM discovery WHERE session_id = ? ORDER BY path",
                (session_id,),
            ).fetchall(),
            "boundaries": db.execute(
                "SELECT COUNT(*) FROM boundaries WHERE session_id = ?", (session_id,)
            ).fetchone()[0],
            "journal": db.execute(
                "SELECT COUNT(*) FROM request_journal WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0],
        }


def request_journal_rows(path: Path, session_id: str) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in db.execute(
                """
                SELECT method, path, status, outcome, asin, quantity
                FROM request_journal
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            )
        ]


def successful_terminal_rows(path: Path, session_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in request_journal_rows(path, session_id)
        if row["status"] == 303 and row["outcome"] == "terminal_added"
    ]


def bootstrap(client: HTTPClient) -> dict[str, Any]:
    response = client.request("GET", "/api/bootstrap")
    if response.status != 200:
        raise VerificationFailure(f"bootstrap returned {response.status}")
    return response.json()


def assert_cart_payload(payload: dict[str, Any], quantity: int) -> bool:
    cart = payload.get("cart", {})
    items = cart.get("items", [])
    return (
        cart.get("total_quantity") == quantity
        and cart.get("subtotal") == round(219.99 * quantity, 2)
        and len(items) == 1
        and items[0].get("asin") == TARGET_ASIN
        and items[0].get("quantity") == quantity
    )


def inspect_terminal_state(
    db_path: Path,
    session_id: str,
    terminal_path: str,
    *,
    successful_count: int = 1,
) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        discovery = [
            dict(row)
            for row in db.execute(
                """
                SELECT path, kind, asin FROM discovery
                WHERE session_id = ? ORDER BY kind, path
                """,
                (session_id,),
            )
        ]
        cart = [
            dict(row)
            for row in db.execute(
                "SELECT asin, quantity FROM cart WHERE session_id = ? ORDER BY asin",
                (session_id,),
            )
        ]
        successful = [
            dict(row)
            for row in db.execute(
                """
                SELECT method, path, status, outcome, asin, quantity
                FROM request_journal
                WHERE session_id = ? AND status = 303 AND outcome = 'terminal_added'
                ORDER BY id
                """,
                (session_id,),
            )
        ]
    expected_discovery = {
        (BEST_SELLERS_PATH, "best_sellers", None),
        (PRODUCT_PATH, "product", TARGET_ASIN),
    }
    if integrity != "ok":
        raise VerificationFailure("SQLite quick_check did not return ok")
    if {
        (row["path"], row["kind"], row["asin"]) for row in discovery
    } != expected_discovery:
        raise VerificationFailure("same-session discovery evidence is not exact")
    if cart != [{"asin": TARGET_ASIN, "quantity": 2}]:
        raise VerificationFailure(
            "same-session cart evidence is not target quantity two"
        )
    expected_journal = {
        "method": "POST",
        "path": terminal_path,
        "status": 303,
        "outcome": "terminal_added",
        "asin": TARGET_ASIN,
        "quantity": 2,
    }
    if len(successful) != successful_count or any(
        row != expected_journal for row in successful
    ):
        raise VerificationFailure("successful terminal journal evidence is not exact")
    return {
        "integrity": integrity,
        "discovery_rows": len(discovery),
        "cart_rows": len(cart),
        "successful_terminal_rows": len(successful),
    }


def validate_task_contract(checks: Checks) -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    checks.add("focused", "schema: Draft 2020-12 schema is valid", True)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(task),
        key=lambda error: list(error.path),
    )
    checks.add(
        "focused",
        "task: task.json validates against schemas/task.schema.json",
        not errors,
        [error.message for error in errors],
    )
    checks.add(
        "focused",
        "task: top-level fields are exact",
        set(task)
        == {
            "$schema",
            "split",
            "metadata",
            "instruction",
            "eval_schema",
            "step_schema",
            "time_limit",
            "extra_info",
            "judge_context",
        },
    )
    checks.add(
        "focused",
        "task: schema reference and dev split are exact",
        task.get("$schema") == "../../../schemas/task.schema.json"
        and task.get("split") == "dev",
    )
    checks.add(
        "focused",
        "task: metadata and canonical commands are exact",
        task.get("metadata") == EXPECTED_METADATA,
    )
    checks.add(
        "focused",
        "task: instruction and time boundary are exact",
        task.get("instruction") == EXPECTED_INSTRUCTION
        and task.get("time_limit") == 20
        and task.get("extra_info") == [],
    )
    checks.add(
        "focused",
        "task: terminal matcher method and fields are exact",
        task.get("eval_schema") == EXPECTED_EVAL,
    )
    checks.add(
        "focused",
        "task: ordered discovery steps are exact",
        task.get("step_schema") == EXPECTED_STEPS,
    )
    matcher = re.compile(task["eval_schema"]["url_pattern"])
    accepted = [
        f"http://{host}:{CANONICAL_PORT}{path}"
        for host in ("localhost", "127.0.0.1", "host.docker.internal")
        for path in TERMINAL_PATHS.values()
    ]
    rejected = [
        f"https://localhost:{CANONICAL_PORT}{TERMINAL_PATHS['desktop']}",
        f"http://127.0.0.1:{CANONICAL_PORT + 1}{TERMINAL_PATHS['desktop']}",
        f"http://127.0.0.1:{CANONICAL_PORT}{TERMINAL_PATHS['desktop']}?x=1",
        f"http://amazon.com:{CANONICAL_PORT}{TERMINAL_PATHS['desktop']}",
        f"http://127.0.0.1:{CANONICAL_PORT}/cart/add-to-cart/ref=dp_start-bbf_1_glance",
        f"http://127.0.0.1:{CANONICAL_PORT}/gp/product/handle-buy-box/ref=mw_dp_buy_crt",
        f"http://127.0.0.1:{CANONICAL_PORT}/cart/add-to-cart/ref=wrong",
    ]
    checks.add(
        "focused",
        "task: terminal matcher accepts only canonical local variants",
        all(matcher.fullmatch(value) for value in accepted)
        and not any(matcher.fullmatch(value) for value in rejected),
    )
    source_hashes = source_screenshot_hashes()
    checks.add(
        "focused",
        "source: screenshot hashes match SOURCE_EVIDENCE",
        source_hashes == list(SOURCE_SCREENSHOT_HASHES),
    )
    return task


def source_screenshot_hashes() -> list[str]:
    hashes: list[str] = []
    for line in SOURCE_EVIDENCE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().split("|")[1:-1]]
        if len(cells) == 4 and re.fullmatch(r"[0-9a-f]{64}", cells[3]):
            hashes.append(cells[3])
    return hashes


def run_focused_backend_suite(manager: ManagedServer, checks: Checks) -> None:
    ordinary = HTTPClient().request("GET", "/")
    csp = ordinary.header("Content-Security-Policy") or ""
    required_headers = {
        "Cache-Control": "no-store, max-age=0",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        ),
    }
    checks.add(
        "focused",
        "HTTP: root serves the application with no-store headers",
        ordinary.status == 200
        and ordinary.header("Content-Type") == "text/html; charset=utf-8"
        and all(
            ordinary.header(name) == value for name, value in required_headers.items()
        ),
    )
    checks.add(
        "focused",
        "HTTP: CSP confines scripts, forms, connections, frames, and objects",
        all(
            directive in csp
            for directive in (
                "default-src 'self'",
                "script-src 'self'",
                "connect-src 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                "frame-src 'none'",
                "object-src 'none'",
                "base-uri 'none'",
            )
        ),
        csp,
    )

    not_found = HTTPClient().request("GET", "/this-page-does-not-exist")
    checks.add(
        "focused",
        "HTTP: unknown application route is a genuine 404 document",
        not_found.status == 404
        and not_found.header("Content-Type").startswith("text/html")
        and b'id="app"' in not_found.body,
    )
    traversal = HTTPClient().request("GET", "/static/%2e%2e/server.py")
    checks.add(
        "focused",
        "HTTP: encoded static traversal cannot read server.py",
        traversal.status == 404
        and b"AmazonThreadingServer" not in traversal.body
        and b"Stage-two loopback" not in traversal.body,
    )

    public_controls = (
        "/api/state",
        "/api/reset",
        "/api/fault",
        "/api/failure",
        "/api/journal",
        "/api/testing",
        "/api/test",
        "/api/debug",
        "/api/control",
        "/api/dump",
        "/api/health",
    )
    control_statuses = []
    for path in public_controls:
        control_statuses.append(HTTPClient().request("GET", path).status)
        control_statuses.append(HTTPClient().request("POST", path).status)
    checks.add(
        "focused",
        "HTTP: no public state, reset, fault, journal, or testing controls exist",
        control_statuses == [404] * (len(public_controls) * 2),
        control_statuses,
    )

    first = HTTPClient()
    first_payload = bootstrap(first)
    cookie_header = first.set_cookie_headers[-1] if first.set_cookie_headers else ""
    checks.add(
        "focused",
        "session: cookie token and Path, Max-Age, HttpOnly, SameSite flags are exact",
        re.search(rf"^{SESSION_COOKIE}=[A-Za-z0-9_-]{{43}};", cookie_header) is not None
        and "; Path=/;" in cookie_header
        and "; Max-Age=31536000;" in cookie_header
        and "; HttpOnly;" in cookie_header
        and cookie_header.endswith("SameSite=Lax")
        and "Secure" not in cookie_header,
        cookie_header,
    )
    second = HTTPClient()
    second_payload = bootstrap(second)
    checks.add(
        "focused",
        "session: fresh clients receive isolated empty carts and distinct sessions",
        first.session_id != second.session_id
        and first_payload["cart"]["items"] == []
        and second_payload["cart"]["items"] == [],
    )

    direct = HTTPClient()
    direct_response = request_form(direct, TERMINAL_PATHS["desktop"])
    checks.add(
        "focused",
        "terminal: direct terminal request without discovery is rejected",
        direct_response.status == 403
        and direct_response.json().get("outcome") == "discovery_required"
        and database_counts(manager.db_path, direct.session_id)["cart"] == [],
    )

    missing_product = HTTPClient()
    missing_product.request("GET", BEST_SELLERS_PATH)
    missing_response = request_form(missing_product, TERMINAL_PATHS["desktop"])
    checks.add(
        "focused",
        "terminal: Best Sellers alone is insufficient discovery",
        missing_response.status == 403
        and missing_response.json().get("outcome") == "discovery_required"
        and database_counts(manager.db_path, missing_product.session_id)["cart"] == [],
    )

    reversed_order = HTTPClient()
    reversed_product = reversed_order.request("GET", PRODUCT_PATH)
    reversed_best = reversed_order.request("GET", BEST_SELLERS_PATH)
    reversed_response = request_form(reversed_order, TERMINAL_PATHS["desktop"])
    reversed_rows = request_journal_rows(manager.db_path, reversed_order.session_id)
    checks.add(
        "focused",
        "terminal: product-before-Best-Sellers discovery order is rejected",
        reversed_product.status == 200
        and reversed_best.status == 200
        and reversed_response.status == 403
        and reversed_response.json().get("outcome") == "discovery_order_invalid"
        and reversed_rows
        == [
            {
                "method": "POST",
                "path": TERMINAL_PATHS["desktop"],
                "status": 403,
                "outcome": "discovery_order_invalid",
                "asin": TARGET_ASIN,
                "quantity": 2,
            }
        ]
        and successful_terminal_rows(manager.db_path, reversed_order.session_id) == []
        and database_counts(manager.db_path, reversed_order.session_id)["cart"] == [],
        reversed_rows,
    )
    revisited_product = reversed_order.request("GET", PRODUCT_PATH)
    recovered_order = request_form(reversed_order, TERMINAL_PATHS["desktop"])
    checks.add(
        "focused",
        "terminal: revisiting the product after Best Sellers restores eligibility",
        revisited_product.status == 200
        and recovered_order.status == 303
        and recovered_order.header("Location") == CART_PATH
        and assert_cart_payload(bootstrap(reversed_order), 2)
        and len(successful_terminal_rows(manager.db_path, reversed_order.session_id))
        == 1,
    )

    stale = HTTPClient()
    stale.request("GET", BEST_SELLERS_PATH)
    stale.request("GET", PRODUCT_PATH)
    with closing(sqlite3.connect(manager.db_path, timeout=5)) as db:
        db.execute(
            "UPDATE discovery SET viewed_at_epoch = 0 WHERE session_id = ?",
            (stale.session_id,),
        )
        db.commit()
    stale_response = request_form(stale, TERMINAL_PATHS["desktop"])
    checks.add(
        "focused",
        "terminal: stale discovery cannot authorize a terminal request",
        stale_response.status == 403
        and stale_response.json().get("outcome") == "discovery_stale"
        and database_counts(manager.db_path, stale.session_id)["cart"] == [],
    )

    forged = HTTPClient(f"{SESSION_COOKIE}=" + "A" * 43)
    forged_response = request_form(forged, TERMINAL_PATHS["desktop"])
    checks.add(
        "focused",
        "session: a syntactically valid forged session cannot forge discovery",
        forged_response.status == 403
        and forged.session_id != "A" * 43
        and bool(forged.set_cookie_headers)
        and database_counts(manager.db_path, forged.session_id)["cart"] == [],
    )

    wrong_asin = HTTPClient()
    wrong_asin.request("GET", BEST_SELLERS_PATH)
    wrong_asin.request("GET", PRODUCT_PATH)
    wrong_asin_response = request_form(
        wrong_asin,
        TERMINAL_PATHS["desktop"],
        body=form_body(WRONG_ASIN, "2"),
    )
    checks.add(
        "focused",
        "terminal: another valid ASIN is rejected without cart effect",
        wrong_asin_response.status == 403
        and wrong_asin_response.json().get("outcome") == "undiscovered_product"
        and database_counts(manager.db_path, wrong_asin.session_id)["cart"] == [],
    )

    for quantity in (1, 3):
        non_terminal = HTTPClient()
        best_response = non_terminal.request("GET", BEST_SELLERS_PATH)
        product_response = non_terminal.request("GET", PRODUCT_PATH)
        add_response = request_form(
            non_terminal,
            TERMINAL_PATHS["desktop"],
            body=form_body(TARGET_ASIN, str(quantity)),
        )
        journal_rows = request_journal_rows(manager.db_path, non_terminal.session_id)
        checks.add(
            "focused",
            f"terminal: quantity {quantity} is an ordinary cart action, not task completion",
            best_response.status == 200
            and product_response.status == 200
            and add_response.status == 303
            and add_response.header("Location") == CART_PATH
            and assert_cart_payload(bootstrap(non_terminal), quantity)
            and journal_rows
            == [
                {
                    "method": "POST",
                    "path": TERMINAL_PATHS["desktop"],
                    "status": 303,
                    "outcome": "cart_added_non_task_quantity",
                    "asin": TARGET_ASIN,
                    "quantity": quantity,
                }
            ]
            and successful_terminal_rows(manager.db_path, non_terminal.session_id)
            == [],
            journal_rows,
        )

    invalid_quantity = request_form(
        HTTPClient(), TERMINAL_PATHS["desktop"], body=form_body(TARGET_ASIN, "4")
    )
    checks.add(
        "focused",
        "terminal: quantity outside one through three is rejected",
        invalid_quantity.status == 400
        and invalid_quantity.json().get("outcome") == "invalid_quantity",
    )
    wrong_content_type = HTTPClient().request(
        "POST",
        TERMINAL_PATHS["desktop"],
        body=json_body({"ASIN": TARGET_ASIN, "quantity": "2"}),
        headers={"Content-Type": "application/json"},
    )
    checks.add(
        "focused",
        "terminal: wrong content type is rejected",
        wrong_content_type.status == 415
        and wrong_content_type.json().get("outcome") == "unsupported_content_type",
    )
    duplicate_fields = request_form(
        HTTPClient(),
        TERMINAL_PATHS["desktop"],
        body=(f"ASIN={TARGET_ASIN}&ASIN={WRONG_ASIN}&quantity=2".encode("ascii")),
    )
    checks.add(
        "focused",
        "terminal: duplicate form fields are rejected",
        duplicate_fields.status == 400
        and duplicate_fields.json().get("outcome") == "duplicate_field",
    )
    query_suffix = request_form(HTTPClient(), TERMINAL_PATHS["desktop"] + "?quantity=2")
    checks.add(
        "focused",
        "terminal: query-suffixed terminal path is rejected",
        query_suffix.status == 404,
    )
    wrong_path = request_form(HTTPClient(), "/cart/add-to-cart/ref=unobserved-path")
    checks.add(
        "focused",
        "terminal: unobserved terminal path is rejected",
        wrong_path.status == 404,
    )
    bad_origin = request_form(
        HTTPClient(),
        TERMINAL_PATHS["desktop"],
        headers={"Origin": "https://www.amazon.com"},
    )
    checks.add(
        "focused",
        "terminal: cross-origin mutation is rejected",
        bad_origin.status == 403 and bad_origin.json().get("outcome") == "bad_origin",
    )
    oversize = request_form(
        HTTPClient(),
        TERMINAL_PATHS["desktop"],
        body=b"x" * (MAX_BODY_BYTES + 1),
    )
    checks.add(
        "focused",
        "terminal: oversized request body is rejected",
        oversize.status == 413 and oversize.json().get("outcome") == "body_too_large",
    )
    malformed_form = request_form(
        HTTPClient(),
        TERMINAL_PATHS["desktop"],
        body=f"ASIN={TARGET_ASIN}&quantity=%ZZ".encode("ascii"),
    )
    checks.add(
        "focused",
        "terminal: malformed form encoding is rejected",
        malformed_form.status == 400
        and malformed_form.json().get("outcome") == "malformed_form",
    )
    unknown_field = request_form(
        HTTPClient(),
        TERMINAL_PATHS["desktop"],
        body=f"ASIN={TARGET_ASIN}&quantity=2&debug=1".encode("ascii"),
    )
    checks.add(
        "focused",
        "terminal: unknown form fields are rejected",
        unknown_field.status == 400
        and unknown_field.json().get("outcome") == "unknown_field",
    )
    malformed_json = HTTPClient().request(
        "PATCH",
        f"/api/cart/{TARGET_ASIN}",
        body=b'{"quantity":',
        headers={"Content-Type": "application/json"},
    )
    checks.add(
        "focused",
        "cart: malformed JSON is rejected before mutation",
        malformed_json.status == 400
        and malformed_json.json().get("outcome") == "malformed_json",
    )
    duplicate_json = HTTPClient().request(
        "PATCH",
        f"/api/cart/{TARGET_ASIN}",
        body=b'{"quantity":2,"quantity":3}',
        headers={"Content-Type": "application/json"},
    )
    checks.add(
        "focused",
        "cart: duplicate JSON fields are rejected before mutation",
        duplicate_json.status == 400
        and duplicate_json.json().get("outcome") == "duplicate_field",
    )

    lock_client = HTTPClient()
    bootstrap(lock_client)
    before_lock = database_counts(manager.db_path, lock_client.session_id)
    lock_connection = sqlite3.connect(manager.db_path, timeout=5, isolation_level=None)
    try:
        lock_connection.execute("BEGIN IMMEDIATE")
        locked_mutation = lock_client.request(
            "POST",
            "/api/boundary",
            body=json_body({"kind": "checkout"}),
            headers={"Content-Type": "application/json"},
        )
    finally:
        if lock_connection.in_transaction:
            lock_connection.execute("ROLLBACK")
        lock_connection.close()
    after_lock_failure = database_counts(manager.db_path, lock_client.session_id)
    checks.add(
        "focused",
        "SQLite: transient write lock returns storage_unavailable without mutation",
        locked_mutation.status == 503
        and locked_mutation.json().get("outcome") == "storage_unavailable"
        and locked_mutation.header("Retry-After") == "1"
        and after_lock_failure == before_lock,
        {
            "status": locked_mutation.status,
            "body": locked_mutation.json(),
            "before": before_lock,
            "after": after_lock_failure,
        },
    )
    recovered_mutation = lock_client.request(
        "POST",
        "/api/boundary",
        body=json_body({"kind": "checkout"}),
        headers={"Content-Type": "application/json"},
    )
    after_lock_recovery = database_counts(manager.db_path, lock_client.session_id)
    checks.add(
        "persistence",
        "SQLite: mutation succeeds after lock release and server remains live",
        recovered_mutation.status == 200
        and recovered_mutation.json()
        == {"status": "local-no-effect", "kind": "checkout"}
        and after_lock_recovery["boundaries"] == before_lock["boundaries"] + 1
        and after_lock_recovery["journal"] == before_lock["journal"] + 1
        and manager.process is not None
        and manager.process.poll() is None,
        after_lock_recovery,
    )

    desktop = HTTPClient()
    desktop_best = desktop.request("GET", BEST_SELLERS_PATH)
    desktop_product = desktop.request("GET", PRODUCT_PATH)
    desktop_add = request_form(desktop, TERMINAL_PATHS["desktop"])
    desktop_payload = bootstrap(desktop)
    checks.add(
        "focused",
        "journey: exact desktop discovery and terminal request succeeds",
        desktop_best.status == 200
        and desktop_product.status == 200
        and desktop_add.status == 303
        and desktop_add.header("Location") == CART_PATH
        and assert_cart_payload(desktop_payload, 2),
    )
    desktop_evidence = inspect_terminal_state(
        manager.db_path, desktop.session_id, TERMINAL_PATHS["desktop"]
    )
    checks.add(
        "persistence",
        "SQLite: desktop discovery, cart, and successful journal row agree",
        desktop_evidence
        == {
            "integrity": "ok",
            "discovery_rows": 2,
            "cart_rows": 1,
            "successful_terminal_rows": 1,
        },
    )

    duplicate_terminal = request_form(desktop, TERMINAL_PATHS["desktop"])
    duplicate_terminal_rows = request_journal_rows(manager.db_path, desktop.session_id)
    checks.add(
        "focused",
        "terminal: repeated exact quantity-two completion is rejected as duplicate",
        duplicate_terminal.status == 409
        and duplicate_terminal.json().get("outcome") == "duplicate_terminal"
        and duplicate_terminal_rows[-1:]
        == [
            {
                "method": "POST",
                "path": TERMINAL_PATHS["desktop"],
                "status": 409,
                "outcome": "duplicate_terminal",
                "asin": TARGET_ASIN,
                "quantity": 2,
            }
        ]
        and len(successful_terminal_rows(manager.db_path, desktop.session_id)) == 1
        and assert_cart_payload(bootstrap(desktop), 2),
        duplicate_terminal_rows,
    )
    patch_three = desktop.request(
        "PATCH",
        f"/api/cart/{TARGET_ASIN}",
        body=json_body({"quantity": 3}),
        headers={"Content-Type": "application/json"},
    )
    checks.add(
        "focused",
        "cart: PATCH updates target quantity to three",
        patch_three.status == 200 and assert_cart_payload(bootstrap(desktop), 3),
    )
    patch_two = desktop.request(
        "PATCH",
        f"/api/cart/{TARGET_ASIN}",
        body=json_body({"quantity": 2}),
        headers={"Content-Type": "application/json"},
    )
    checks.add(
        "focused",
        "cart: PATCH restores target quantity to two",
        patch_two.status == 200 and assert_cart_payload(bootstrap(desktop), 2),
    )
    save = desktop.request("POST", f"/api/cart/{TARGET_ASIN}/save-for-later", body=b"")
    saved_payload = bootstrap(desktop)
    checks.add(
        "focused",
        "cart: save for later moves the exact item out of cart",
        save.status == 200
        and saved_payload["cart"]["items"] == []
        and [
            (item["asin"], item["quantity"])
            for item in saved_payload["saved_for_later"]
        ]
        == [(TARGET_ASIN, 2)],
    )
    save_again = desktop.request(
        "POST", f"/api/cart/{TARGET_ASIN}/save-for-later", body=b""
    )
    checks.add(
        "focused",
        "cart: repeated save is idempotent",
        save_again.status == 200
        and save_again.json().get("outcome") == "already_saved",
    )
    move = desktop.request("POST", f"/api/cart/{TARGET_ASIN}/move-to-cart", body=b"")
    checks.add(
        "focused",
        "cart: move to cart restores target quantity two",
        move.status == 200 and assert_cart_payload(bootstrap(desktop), 2),
    )
    move_again = desktop.request(
        "POST", f"/api/cart/{TARGET_ASIN}/move-to-cart", body=b""
    )
    checks.add(
        "focused",
        "cart: repeated move is idempotent",
        move_again.status == 200
        and move_again.json().get("outcome") == "already_in_cart",
    )

    before_boundary = database_counts(manager.db_path, desktop.session_id)
    boundary = desktop.request(
        "POST",
        "/api/boundary",
        body=json_body({"kind": "checkout"}),
        headers={"Content-Type": "application/json"},
    )
    after_boundary = database_counts(manager.db_path, desktop.session_id)
    checks.add(
        "focused",
        "boundary: checkout audit is local and has no cart, saved, or discovery effect",
        boundary.status == 200
        and boundary.json().get("status") == "local-no-effect"
        and before_boundary["cart"] == after_boundary["cart"]
        and before_boundary["saved"] == after_boundary["saved"]
        and before_boundary["discovery"] == after_boundary["discovery"]
        and after_boundary["boundaries"] == before_boundary["boundaries"] + 1,
    )
    delete = desktop.request("DELETE", f"/api/cart/{TARGET_ASIN}")
    checks.add(
        "focused",
        "cart: DELETE removes the target item",
        delete.status == 200 and bootstrap(desktop)["cart"]["items"] == [],
    )
    delete_again = desktop.request("DELETE", f"/api/cart/{TARGET_ASIN}")
    checks.add(
        "focused",
        "cart: repeated DELETE is an idempotent no-op",
        delete_again.status == 200
        and delete_again.json().get("outcome") == "cart_item_absent",
    )

    search_client = HTTPClient()
    search_result = search_client.request("GET", "/api/search?k=samsung+t7").json()
    empty_search = search_client.request("GET", "/api/search?k=").json()
    checks.add(
        "focused",
        "search: target query and normalized empty query are deterministic",
        search_result["count"] == 1
        and search_result["products"][0]["asin"] == TARGET_ASIN
        and empty_search["query"] == ""
        and empty_search["count"] == 6,
    )

    head_client = HTTPClient()
    bootstrap(head_client)
    before_head = database_counts(manager.db_path, head_client.session_id)
    head_best = head_client.request("HEAD", BEST_SELLERS_PATH)
    head_product = head_client.request("HEAD", PRODUCT_PATH)
    after_head = database_counts(manager.db_path, head_client.session_id)
    checks.add(
        "focused",
        "HTTP: HEAD returns metadata without discovery mutation",
        head_best.status == 200
        and head_product.status == 200
        and head_best.body == b""
        and head_product.body == b""
        and before_head["discovery"] == after_head["discovery"] == [],
    )

    mobile = HTTPClient()
    mobile_best = mobile.request("GET", BEST_SELLERS_PATH)
    mobile_product = mobile.request("GET", PRODUCT_PATH)
    mobile_add = request_form(mobile, TERMINAL_PATHS["mobile"])
    checks.add(
        "focused",
        "journey: exact mobile discovery and responsive terminal request succeeds",
        mobile_best.status == 200
        and mobile_product.status == 200
        and mobile_add.status == 303
        and mobile_add.header("Location") == CART_PATH
        and assert_cart_payload(bootstrap(mobile), 2),
    )
    mobile_evidence = inspect_terminal_state(
        manager.db_path, mobile.session_id, TERMINAL_PATHS["mobile"]
    )
    checks.add(
        "persistence",
        "SQLite: mobile discovery, cart, and successful journal row agree",
        mobile_evidence["successful_terminal_rows"] == 1,
    )

    manager.restart()
    checks.add(
        "persistence",
        "restart: same database preserves mobile cart quantity two",
        assert_cart_payload(bootstrap(mobile), 2),
    )
    checks.add(
        "persistence",
        "restart: deleted desktop cart remains empty",
        bootstrap(desktop)["cart"]["items"] == [],
    )
    isolated_after_restart = HTTPClient()
    checks.add(
        "persistence",
        "restart: fresh session remains isolated from persisted carts",
        bootstrap(isolated_after_restart)["cart"]["items"] == []
        and isolated_after_restart.session_id
        not in {desktop.session_id, mobile.session_id},
    )
    focused_counts = checks.counts("focused")
    checks.add(
        "focused",
        "suite: all dynamically counted focused backend checks passed",
        focused_counts["passed"] == focused_counts["total"],
        focused_counts,
    )


class RuntimeAudit:
    def __init__(self, page: Page) -> None:
        self.external_requests: list[str] = []
        self.failed_requests: list[dict[str, str]] = []
        self.page_errors: list[str] = []
        self.console_errors: list[str] = []
        self.expected_console_errors: list[str] = []
        self.unexpected_http_errors: list[tuple[int, str]] = []
        self.expected_document_404s: list[str] = []
        self.terminal_requests: list[dict[str, Any]] = []
        page.on("request", self._on_request)
        page.on("requestfailed", self._on_request_failed)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on("console", self._on_console)
        page.on("response", self._on_response)

    def _on_request(self, request: Any) -> None:
        parsed = urlsplit(request.url)
        if parsed.scheme in {"http", "https"} and (
            parsed.scheme != "http" or parsed.hostname != HOST or parsed.port != PORT
        ):
            self.external_requests.append(request.url)
        if request.method == "POST" and parsed.path in set(TERMINAL_PATHS.values()):
            post_data = request.post_data or ""
            self.terminal_requests.append(
                {
                    "method": request.method,
                    "path": parsed.path,
                    "query": parsed.query,
                    "content_type": request.headers.get("content-type", ""),
                    "pairs": parse_qsl(
                        post_data,
                        keep_blank_values=True,
                        strict_parsing=True,
                        encoding="utf-8",
                        errors="strict",
                    ),
                }
            )

    def _on_request_failed(self, request: Any) -> None:
        self.failed_requests.append(
            {
                "method": request.method,
                "url": request.url,
                "failure": request.failure or "unknown",
            }
        )

    def _on_console(self, message: Any) -> None:
        if message.type == "error":
            if message.text == (
                "Failed to load resource: the server responded with a status of "
                "404 (Not Found)"
            ):
                self.expected_console_errors.append(message.text)
            else:
                self.console_errors.append(message.text)

    def _on_response(self, response: Any) -> None:
        if response.status < 400:
            return
        parsed = urlsplit(response.url)
        if (
            response.status == 404
            and parsed.path == "/genuine-missing-page"
            and response.request.resource_type == "document"
        ):
            self.expected_document_404s.append(response.url)
            return
        self.unexpected_http_errors.append((response.status, response.url))


@dataclass
class BrowserJourney:
    viewport: str
    context: BrowserContext
    page: Page
    audit: RuntimeAudit
    session_id: str
    terminal_path: str
    storage_state: dict[str, Any] | None = None


def launch_browser(playwright: Any, manager: ManagedServer) -> Browser:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(manager.browser_root),
        "TMP": str(manager.browser_root),
        "TEMP": str(manager.browser_root),
    }
    kwargs: dict[str, Any] = {"headless": True, "env": env}
    executable = os.environ.get("CLAWBENCH_PLAYWRIGHT_EXECUTABLE")
    if executable:
        kwargs["executable_path"] = executable
    return playwright.chromium.launch(**kwargs)


def launch_browser_context(
    browser: Browser,
    playwright: Any,
    manager: ManagedServer,
    viewport_name: str,
    *,
    storage_state: dict[str, Any] | None = None,
) -> BrowserContext:
    dimensions = VIEWPORTS[viewport_name]
    kwargs: dict[str, Any] = {
        "viewport": dimensions,
        "locale": "en-US",
        "timezone_id": "America/Chicago",
        "color_scheme": "light",
        "reduced_motion": "reduce",
    }
    if storage_state is not None:
        kwargs["storage_state"] = storage_state
    if viewport_name == "mobile":
        kwargs.update(
            {
                "is_mobile": True,
                "has_touch": True,
                "user_agent": (
                    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Mobile Safari/537.36"
                ),
            }
        )
    return browser.new_context(**kwargs)


def goto_app(page: Page, path: str, *, status: int = 200) -> Any:
    response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle", timeout=15000)
    if response is None or response.status != status:
        actual = None if response is None else response.status
        raise VerificationFailure(
            f"navigation {path} returned {actual}, expected {status}"
        )
    page.wait_for_function(
        """() => {
          const main = document.querySelector('main');
          return main && !main.querySelector('.initial-loading, .route-loading') &&
            main.innerText.trim().length > 0;
        }""",
        timeout=8000,
    )
    return response


def visible_texts(page: Page, selector: str) -> list[str]:
    return page.locator(selector).evaluate_all(
        """elements => elements
          .filter(element => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' &&
              rect.width > 0 && rect.height > 0;
          })
          .map(element => (element.textContent || '').trim())"""
    )


def assert_no_horizontal_overflow(
    page: Page, checks: Checks, viewport_name: str, stage: str
) -> None:
    metrics = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          html: document.documentElement.scrollWidth,
          body: document.body.scrollWidth
        })"""
    )
    checks.add(
        "browser",
        f"{viewport_name}: {stage} has no horizontal overflow",
        metrics["html"] <= metrics["viewport"]
        and metrics["body"] <= metrics["viewport"],
        metrics,
    )


def screenshot_metadata(
    path: Path, expected_dimensions: dict[str, int]
) -> dict[str, Any]:
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        size = image.size
        image_format = image.format
        sample = image.convert("RGB").resize((96, 96))
        extrema = sample.getextrema()
        colors = sample.getcolors(maxcolors=96 * 96) or []
    nonblank = any(high > low for low, high in extrema)
    varied_color = len(colors) >= 64
    if image_format != "PNG":
        raise VerificationFailure(f"screenshot is not PNG: {path.name}")
    if size != (expected_dimensions["width"], expected_dimensions["height"]):
        raise VerificationFailure(
            f"screenshot dimensions are not original viewport: {path.name}"
        )
    if len(raw) < 3000 or not nonblank or not varied_color:
        raise VerificationFailure(f"screenshot pixel integrity failed: {path.name}")
    return {
        "width": size[0],
        "height": size[1],
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pixel_checks": {"nonblank": nonblank, "varied_color": varied_color},
    }


def capture_screenshot(
    page: Page,
    manager: ManagedServer,
    checks: Checks,
    viewport_name: str,
    stage: str,
) -> dict[str, Any]:
    if stage not in SCREENSHOT_STAGES:
        raise VerificationFailure(f"unknown screenshot stage: {stage}")
    page.wait_for_function(
        "() => [...document.images].every(image => image.complete)", timeout=8000
    )
    page.evaluate(
        """() => {
          document.documentElement.style.scrollBehavior = 'auto';
          window.scrollTo({left: 0, top: 0, behavior: 'instant'});
        }"""
    )
    page.wait_for_function("() => window.scrollX === 0 && window.scrollY === 0")
    scroll_position = page.evaluate("() => ({x: window.scrollX, y: window.scrollY})")
    assert_no_horizontal_overflow(page, checks, viewport_name, stage)
    path = manager.artifact_root / f"amazon-{viewport_name}-{stage}.png"
    page.screenshot(
        path=str(path),
        full_page=False,
        animations="disabled",
        caret="hide",
    )
    metadata = screenshot_metadata(path, VIEWPORTS[viewport_name])
    checks.add(
        "browser",
        f"{viewport_name}: {stage} screenshot is top-left, original-size, nonblank, and varied",
        metadata["width"] == VIEWPORTS[viewport_name]["width"]
        and metadata["height"] == VIEWPORTS[viewport_name]["height"]
        and all(metadata["pixel_checks"].values())
        and scroll_position == {"x": 0, "y": 0},
        scroll_position,
    )
    return {
        "path": path,
        "viewport": viewport_name,
        "stage": stage,
        **metadata,
    }


def visible_locator(page: Page, selector: str) -> Any:
    locators = page.locator(selector)
    for index in range(locators.count()):
        candidate = locators.nth(index)
        if candidate.is_visible():
            return candidate
    raise VerificationFailure(f"no visible locator for {selector}")


def run_browser_journey(
    context: BrowserContext,
    manager: ManagedServer,
    checks: Checks,
    viewport_name: str,
) -> tuple[BrowserJourney, list[dict[str, Any]]]:
    page = context.pages[0] if context.pages else context.new_page()
    audit = RuntimeAudit(page)
    captures: list[dict[str, Any]] = []

    goto_app(page, CART_PATH)
    checks.add(
        "browser",
        f"{viewport_name}: starts with an empty isolated cart",
        "Your Amazon Cart is empty" in page.locator("main").inner_text()
        and visible_texts(page, ".cart-count") == ["0"],
    )
    desktop_visible = page.locator(".desktop-nav").is_visible()
    mobile_visible = page.locator(".mobile-nav").is_visible()
    checks.add(
        "browser",
        f"{viewport_name}: responsive navigation visibility is exact",
        (
            (desktop_visible and not mobile_visible)
            if viewport_name == "desktop"
            else (mobile_visible and not desktop_visible)
        ),
        {"desktop": desktop_visible, "mobile": mobile_visible},
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "empty-cart")
    )

    goto_app(page, BEST_SELLERS_PATH)
    target_card = page.locator(f'.ranked-product[data-asin="{TARGET_ASIN}"]')
    target_text = target_card.inner_text()
    checks.add(
        "browser",
        f"{viewport_name}: Best Sellers shows exact rank-two Samsung target evidence",
        target_card.count() == 1
        and "#2" in target_text
        and "Samsung T7 Portable SSD" in target_text
        and "4.7" in target_text
        and "38,068" in target_text
        and "External Solid State Drives" in page.locator("main").inner_text(),
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "best-sellers")
    )

    with page.expect_navigation(wait_until="networkidle", timeout=15000):
        target_card.locator(".ranked-title").click()
    page.locator(".product-page h1").wait_for(state="visible", timeout=8000)
    product_text = page.locator(".product-page").inner_text()
    hidden_asins = page.locator('form[data-add-form] input[name="ASIN"]').evaluate_all(
        "elements => elements.map(element => element.value)"
    )
    checks.add(
        "browser",
        f"{viewport_name}: product copy, ASIN, variants, rating, and reviews are exact",
        page.url == f"{BASE_URL}{PRODUCT_PATH}"
        and "Samsung T7 Portable SSD, 1TB External Solid State Drive" in product_text
        and "Gray" in product_text
        and "Titan Gray" in product_text
        and "1 TB" in product_text
        and "4.7" in product_text
        and "38,068" in product_text
        and hidden_asins == [TARGET_ASIN, TARGET_ASIN]
        and page.locator('[aria-label="Color"] .selected').inner_text() == "Titan Gray"
        and page.locator(
            '[aria-label="Memory Storage Capacity"] .selected'
        ).inner_text()
        == "1 TB",
    )
    desktop_form_visible = page.locator(".desktop-purchase").is_visible()
    mobile_form_visible = page.locator(".mobile-purchase").is_visible()
    expected_form = (
        page.locator(".desktop-purchase")
        if viewport_name == "desktop"
        else page.locator(".mobile-purchase")
    )
    checks.add(
        "browser",
        f"{viewport_name}: only the responsive terminal form is visible",
        (
            (desktop_form_visible and not mobile_form_visible)
            if viewport_name == "desktop"
            else (mobile_form_visible and not desktop_form_visible)
        ),
        {"desktop": desktop_form_visible, "mobile": mobile_form_visible},
    )
    checks.add(
        "browser",
        f"{viewport_name}: responsive form action is the exact source-observed terminal",
        expected_form.get_attribute("method").lower() == "post"
        and expected_form.get_attribute("action") == TERMINAL_PATHS[viewport_name],
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "target-product")
    )

    quantity_select = expected_form.locator('select[name="quantity"]')
    quantity_select.select_option("2")
    with page.expect_navigation(wait_until="networkidle", timeout=15000):
        expected_form.get_by_role("button", name="Add to cart").click()
    page.locator(f'[data-cart-item="{TARGET_ASIN}"]').wait_for(
        state="visible", timeout=8000
    )
    checks.add(
        "browser",
        f"{viewport_name}: exactly one terminal request has exact method, path, fields, and media type",
        len(audit.terminal_requests) == 1
        and audit.terminal_requests[0]
        == {
            "method": "POST",
            "path": TERMINAL_PATHS[viewport_name],
            "query": "",
            "content_type": "application/x-www-form-urlencoded",
            "pairs": [
                ("ASIN", TARGET_ASIN),
                ("quantity", "2"),
                ("submit.add-to-cart", "Add to Cart"),
            ],
        },
        audit.terminal_requests,
    )
    cart_text = page.locator(".cart-page").inner_text()
    checks.add(
        "browser",
        f"{viewport_name}: populated cart agrees on target, quantity, subtotal, and count",
        "Samsung T7 Portable SSD" in cart_text
        and "Subtotal (2 items): $439.98" in cart_text
        and page.locator(f'[data-cart-quantity="{TARGET_ASIN}"]').input_value() == "2"
        and visible_texts(page, ".cart-count") == ["2"],
        cart_text,
    )
    checks.add(
        "browser",
        f"{viewport_name}: cart exposes update, delete, and save controls",
        page.locator(f'[data-cart-quantity="{TARGET_ASIN}"]').is_visible()
        and page.locator(f'[data-remove="{TARGET_ASIN}"]').is_visible()
        and page.locator(f'[data-save="{TARGET_ASIN}"]').is_visible(),
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "populated-cart")
    )

    cart_quantity = page.locator(f'[data-cart-quantity="{TARGET_ASIN}"]')
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and urlsplit(response.url).path == f"/api/cart/{TARGET_ASIN}",
        timeout=8000,
    ):
        cart_quantity.select_option("3")
    page.wait_for_function(
        """asin => {
          const select = document.querySelector(`[data-cart-quantity="${asin}"]`);
          return select && select.value === '3' &&
            document.querySelector('.cart-page')?.textContent.includes('Subtotal (3 items): $659.97');
        }""",
        arg=TARGET_ASIN,
        timeout=8000,
    )
    checks.add(
        "browser",
        f"{viewport_name}: cart PATCH is reflected in UI",
        cart_quantity.input_value() == "3",
    )
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and urlsplit(response.url).path == f"/api/cart/{TARGET_ASIN}",
        timeout=8000,
    ):
        cart_quantity.select_option("2")
    page.wait_for_function(
        """asin => {
          const select = document.querySelector(`[data-cart-quantity="${asin}"]`);
          return select && select.value === '2' &&
            document.querySelector('.cart-page')?.textContent.includes('Subtotal (2 items): $439.98');
        }""",
        arg=TARGET_ASIN,
        timeout=8000,
    )

    with page.expect_response(
        lambda response: urlsplit(response.url).path
        == f"/api/cart/{TARGET_ASIN}/save-for-later",
        timeout=8000,
    ):
        page.locator(f'[data-save="{TARGET_ASIN}"]').click()
    page.locator(f'[data-move-to-cart="{TARGET_ASIN}"]').wait_for(
        state="visible", timeout=8000
    )
    checks.add(
        "browser",
        f"{viewport_name}: save for later exposes move-to-cart control",
        "Saved for later" in page.locator(".cart-page").inner_text()
        and page.locator(f'[data-move-to-cart="{TARGET_ASIN}"]').is_visible(),
    )
    with page.expect_response(
        lambda response: urlsplit(response.url).path
        == f"/api/cart/{TARGET_ASIN}/move-to-cart",
        timeout=8000,
    ):
        page.locator(f'[data-move-to-cart="{TARGET_ASIN}"]').click()
    page.locator(f'[data-cart-item="{TARGET_ASIN}"]').wait_for(
        state="visible", timeout=8000
    )
    checks.add(
        "browser",
        f"{viewport_name}: move to cart restores quantity two without another terminal",
        page.locator(f'[data-cart-quantity="{TARGET_ASIN}"]').input_value() == "2"
        and len(audit.terminal_requests) == 1,
    )

    goto_app(page, "/s?k=samsung+t7")
    search_text = page.locator(".search-page").inner_text()
    checks.add(
        "browser",
        f"{viewport_name}: search result finds the exact Samsung T7",
        "1 result" in search_text
        and "Samsung T7 Portable SSD" in search_text
        and page.locator(".search-result").count() == 1,
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "search-results")
    )

    goto_app(page, "/s?k=no-such-amazon-product-900136")
    checks.add(
        "browser",
        f"{viewport_name}: no-result search state is explicit",
        "No results for" in page.locator(".search-page").inner_text()
        and page.locator(".search-result").count() == 0,
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "no-results")
    )
    goto_app(page, "/s")
    checks.add(
        "browser",
        f"{viewport_name}: empty search asks for a term without API failure",
        "Enter a search term" in page.locator(".search-page").inner_text(),
    )

    goto_app(page, CART_PATH)
    cart_before_boundary = page.locator(".cart-page").inner_text()
    checkout_link = visible_locator(page, 'a[href="/checkout"]')
    with page.expect_navigation(wait_until="load", timeout=15000):
        checkout_link.click()
    page.locator("#boundary-dialog[open]").wait_for(state="visible", timeout=8000)
    checks.add(
        "browser",
        f"{viewport_name}: checkout opens a visible local no-effect boundary dialog",
        "Checkout stops here" in page.locator("#boundary-dialog").inner_text()
        and "Subtotal (2 items): $439.98" in page.locator(".cart-page").inner_text()
        and "Subtotal (2 items): $439.98" in cart_before_boundary,
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "boundary")
    )
    with page.expect_navigation(wait_until="load", timeout=15000):
        page.locator(".boundary-return").click()
    page.locator(f'[data-cart-item="{TARGET_ASIN}"]').wait_for(
        state="visible", timeout=8000
    )

    goto_app(page, "/genuine-missing-page", status=404)
    checks.add(
        "browser",
        f"{viewport_name}: genuine 404 renders a customer-facing not-found state",
        "Sorry, we couldn't find that page." in page.locator("main").inner_text()
        and page.title() == "Page Not Found - Amazon.com",
    )
    captures.append(
        capture_screenshot(page, manager, checks, viewport_name, "not-found")
    )

    goto_app(page, CART_PATH)
    page.reload(wait_until="networkidle", timeout=15000)
    page.locator(f'[data-cart-item="{TARGET_ASIN}"]').wait_for(
        state="visible", timeout=8000
    )
    checks.add(
        "persistence",
        f"{viewport_name}: refresh recovers target cart quantity two",
        page.locator(f'[data-cart-quantity="{TARGET_ASIN}"]').input_value() == "2"
        and "Subtotal (2 items): $439.98" in page.locator(".cart-page").inner_text(),
    )

    cookies = context.cookies([BASE_URL])
    session_cookie = next(
        (cookie for cookie in cookies if cookie["name"] == SESSION_COOKIE), None
    )
    checks.add(
        "browser",
        f"{viewport_name}: browser cookie remains HttpOnly, SameSite Lax, and non-secure on HTTP",
        session_cookie is not None
        and session_cookie["httpOnly"] is True
        and session_cookie["sameSite"] == "Lax"
        and session_cookie["secure"] is False,
    )
    if session_cookie is None:
        raise VerificationFailure("browser session cookie disappeared")
    inspect_terminal_state(
        manager.db_path,
        session_cookie["value"],
        TERMINAL_PATHS[viewport_name],
    )
    checks.add(
        "persistence",
        f"{viewport_name}: SQLite terminal evidence remains exact after UI controls",
        True,
    )
    return (
        BrowserJourney(
            viewport=viewport_name,
            context=context,
            page=page,
            audit=audit,
            session_id=session_cookie["value"],
            terminal_path=TERMINAL_PATHS[viewport_name],
        ),
        captures,
    )


def run_site_model_browser(
    playwright: Any,
    browser: Browser,
    manager: ManagedServer,
    checks: Checks,
    viewport_name: str,
) -> RuntimeAudit:
    context = launch_browser_context(browser, playwright, manager, viewport_name)
    page = context.pages[0] if context.pages else context.new_page()
    audit = RuntimeAudit(page)
    routes = (
        ("home", "/"),
        ("best sellers root", "/Best-Sellers/zgbs"),
        ("generic search", "/s?k=wireless+earbuds"),
        ("computers category", "/Computers-Accessories/b/?node=541966"),
        ("deals", "/gp/goldbox/"),
        ("generic product", GENERIC_PRODUCT_PATH),
        ("lists", "/hz/wishlist/ls"),
        ("browsing history", "/hz/history"),
        ("account boundary", "/account"),
        ("orders boundary", "/account/orders"),
        ("cart", CART_PATH),
    )
    try:
        route_results: list[dict[str, Any]] = []
        for label, path in routes:
            response = goto_app(page, path)
            main_text = page.locator("main").inner_text().strip()
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            route_results.append(
                {
                    "label": label,
                    "path": path,
                    "status": response.status,
                    "main_text_length": len(main_text),
                    "horizontal_overflow": overflow,
                }
            )
        checks.add(
            "browser",
            f"{viewport_name}: website-level daily-use routes render substantial overflow-free documents",
            len(route_results) == len(routes)
            and all(item["status"] == 200 for item in route_results)
            and all(item["main_text_length"] >= 40 for item in route_results)
            and all(item["horizontal_overflow"] <= 1 for item in route_results),
            route_results,
        )

        goto_app(page, "/")
        search_input = visible_locator(page, '[data-search-form] input[type="search"]')
        search_input.fill("wireless")
        page.wait_for_function(
            """input => {
              const panel = input.closest('form')?.querySelector('.autocomplete-panel.open');
              return panel && panel.querySelectorAll('[role=option]').length > 0;
            }""",
            arg=search_input.element_handle(),
            timeout=8000,
        )
        search_input.press("ArrowDown")
        checks.add(
            "browser",
            f"{viewport_name}: autocomplete is visible and keyboard navigable",
            search_input.get_attribute("data-active-suggestion") == "0",
        )

        goto_app(page, GENERIC_PRODUCT_PATH)
        checks.add(
            "browser",
            f"{viewport_name}: generic product exposes gallery, variants, ordinary cart, list, reviews, and seller offers",
            page.locator(".generic-gallery").is_visible()
            and visible_locator(page, f'[data-quick-add="{GENERIC_ASIN}"]').is_visible()
            and visible_locator(page, f'[data-list-add="{GENERIC_ASIN}"]').is_visible()
            and "Customer reviews" in page.locator("main").inner_text()
            and "Other sellers on Amazon" in page.locator("main").inner_text(),
        )
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and urlsplit(response.url).path == "/api/list",
            timeout=8000,
        ):
            visible_locator(page, f'[data-list-add="{GENERIC_ASIN}"]').click()
        page.wait_for_function(
            "() => document.querySelector('#toast.show')?.textContent === 'Added to List'",
            timeout=8000,
        )
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and urlsplit(response.url).path == "/api/cart/add",
            timeout=8000,
        ):
            visible_locator(page, f'[data-quick-add="{GENERIC_ASIN}"]').click()
        page.wait_for_function(
            "() => document.querySelector('#toast.show')?.textContent?.startsWith('Added 1 to Cart')",
            timeout=8000,
        )

        goto_app(page, "/hz/wishlist/ls")
        checks.add(
            "browser",
            f"{viewport_name}: local list exposes the persisted item and delete/cart controls",
            page.locator(f'[data-list-remove="{GENERIC_ASIN}"]').count() == 1
            and page.locator(f'[data-quick-add="{GENERIC_ASIN}"]').count() == 1,
        )
        goto_app(page, "/hz/history")
        checks.add(
            "browser",
            f"{viewport_name}: recently viewed product appears in browsing history",
            page.locator(".history-item").count() == 1
            and "Water Bottle" in page.locator(".history-page").inner_text(),
        )
        goto_app(page, CART_PATH)
        checks.add(
            "browser",
            f"{viewport_name}: ordinary catalog action creates a non-terminal persisted cart item",
            page.locator(f'[data-cart-item="{GENERIC_ASIN}"]').count() == 1
            and len(audit.terminal_requests) == 0,
            audit.terminal_requests,
        )
        page.wait_for_load_state("networkidle")
        checks.add(
            "browser",
            f"{viewport_name}: website-level journey completes without request failures",
            audit.failed_requests == [],
            audit.failed_requests,
        )
        return audit
    finally:
        context.close()


def verify_browser_restart(
    playwright: Any,
    browser: Browser,
    manager: ManagedServer,
    journeys: list[BrowserJourney],
    checks: Checks,
) -> list[RuntimeAudit]:
    manager.restart()
    restart_audits: list[RuntimeAudit] = []
    for journey in journeys:
        context = launch_browser_context(
            browser,
            playwright,
            manager,
            journey.viewport,
            storage_state=journey.storage_state,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            audit = RuntimeAudit(page)
            goto_app(page, CART_PATH)
            checks.add(
                "persistence",
                f"{journey.viewport}: same-database restart recovers quantity two",
                page.locator(f'[data-cart-quantity="{TARGET_ASIN}"]').input_value()
                == "2"
                and visible_texts(page, ".cart-count") == ["2"],
            )
            inspect_terminal_state(
                manager.db_path,
                journey.session_id,
                journey.terminal_path,
            )
            restart_audits.append(audit)
        finally:
            context.close()

    isolation_context = launch_browser_context(
        browser,
        playwright,
        manager,
        "desktop",
    )
    try:
        isolation_page = isolation_context.new_page()
        isolation_audit = RuntimeAudit(isolation_page)
        goto_app(isolation_page, CART_PATH)
        checks.add(
            "persistence",
            "browser isolation: fresh post-restart context has an empty cart",
            "Your Amazon Cart is empty" in isolation_page.locator("main").inner_text()
            and visible_texts(isolation_page, ".cart-count") == ["0"],
        )
    finally:
        isolation_context.close()
    restart_audits.append(isolation_audit)
    return restart_audits


def screenshot_review_manifest(captures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(captures) != EXPECTED_SCREENSHOT_COUNT:
        raise VerificationFailure(
            f"expected {EXPECTED_SCREENSHOT_COUNT} screenshots, found {len(captures)}"
        )
    identities = {(item["viewport"], item["stage"]) for item in captures}
    expected = {
        (viewport, stage) for viewport in VIEWPORTS for stage in SCREENSHOT_STAGES
    }
    if identities != expected:
        raise VerificationFailure("screenshot stage coverage is incomplete")
    source_baselines = {
        (item["viewport"], item["clone_stage"]): item for item in SOURCE_BASELINE_STATES
    }
    manifest: list[dict[str, Any]] = []
    for item in sorted(captures, key=lambda row: (row["viewport"], row["stage"])):
        path = Path(item["path"])
        metadata = screenshot_metadata(path, VIEWPORTS[item["viewport"]])
        if metadata["sha256"] != item["sha256"]:
            raise VerificationFailure(f"screenshot changed before review: {path.name}")
        source_baseline = source_baselines.get((item["viewport"], item["stage"]))
        manifest.append(
            {
                "path": str(path.resolve()),
                "capture_id": f"{item['viewport']}:{item['stage']}",
                "review_state": item["stage"],
                "source_baseline": (
                    {
                        "state": source_baseline["state"],
                        "viewport": source_baseline["viewport"],
                        "sha256": source_baseline["sha256"],
                    }
                    if source_baseline is not None
                    else None
                ),
                "comparison_mode": (
                    "direct_source_baseline"
                    if source_baseline is not None
                    else "source_informed_clone_state"
                ),
                "review_checklist": list(REVIEW_CHECKLISTS[item["stage"]]),
                "dimensions": {
                    "width": item["width"],
                    "height": item["height"],
                },
                "sha256": item["sha256"],
                "bytes": item["bytes"],
            }
        )
    return manifest


def source_to_clone_review_mapping() -> list[dict[str, Any]]:
    return [
        {
            "source_state": item["state"],
            "source_viewport": item["viewport"],
            "source_screenshot_sha256": item["sha256"],
            "clone_capture_id": f"{item['viewport']}:{item['clone_stage']}",
            "review_checklist": list(REVIEW_CHECKLISTS[item["clone_stage"]]),
        }
        for item in SOURCE_BASELINE_STATES
    ]


def await_screenshot_review(
    captures: list[dict[str, Any]],
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    timeout_seconds: int = REVIEW_TIMEOUT_SECONDS,
    selector: Callable[..., Any] = select.select,
) -> None:
    if os.environ.get(REVIEW_ENV) != "1":
        raise ScreenshotReviewIncomplete(f"{REVIEW_ENV}=1 is required")
    manifest = screenshot_review_manifest(captures)
    timeout = min(max(timeout_seconds, 0), REVIEW_TIMEOUT_SECONDS)
    output_stream.write(
        json.dumps(
            {
                "marker": "AMAZON_ORIGINAL_RESOLUTION_REVIEW_REQUIRED",
                "acknowledgement": REVIEW_ACK,
                "timeout_seconds": timeout,
                "source_baseline_screenshot_sha256": list(SOURCE_SCREENSHOT_HASHES),
                "source_to_clone_review": source_to_clone_review_mapping(),
                "screenshots": manifest,
            },
            ensure_ascii=True,
        )
        + "\nAMAZON_SCREENSHOT_REVIEW_ACK> "
    )
    output_stream.flush()
    ready, _, _ = selector([input_stream], [], [], timeout)
    if not ready:
        outcome = "timeout"
    else:
        response = input_stream.readline()
        if response == "":
            outcome = "eof"
        elif response.rstrip("\r\n") == REVIEW_ACK:
            outcome = "acknowledged"
        elif response.rstrip("\r\n") == "":
            outcome = "blank"
        else:
            outcome = "mismatch"
    output_stream.write(
        json.dumps({"acknowledged": outcome == "acknowledged", "outcome": outcome})
        + "\n"
    )
    output_stream.flush()
    if outcome != "acknowledged":
        raise ScreenshotReviewIncomplete(
            f"screenshot review was not accepted: {outcome}"
        )


def runtime_totals(audits: list[RuntimeAudit]) -> dict[str, int]:
    return {
        "same_origin_terminal_requests": sum(
            len(audit.terminal_requests) for audit in audits
        ),
        "external_runtime_requests": sum(
            len(audit.external_requests) for audit in audits
        ),
        "request_failures": sum(len(audit.failed_requests) for audit in audits),
        "page_errors": sum(len(audit.page_errors) for audit in audits),
        "unexpected_console_errors": sum(len(audit.console_errors) for audit in audits),
        "expected_console_404s": sum(
            len(audit.expected_console_errors) for audit in audits
        ),
        "unexpected_http_errors": sum(
            len(audit.unexpected_http_errors) for audit in audits
        ),
        "expected_document_404s": sum(
            len(audit.expected_document_404s) for audit in audits
        ),
    }


def structural_sha256() -> str:
    paths = {SCHEMA_PATH, SERVER_PATH}
    paths.update(path for path in CLONE_ROOT.glob("*.md") if path.is_file())
    for root in (
        TASK_PATH.parent,
        CLONE_ROOT / "source-fixtures",
        CLONE_ROOT / "static",
        CLONE_ROOT / "tools",
    ):
        paths.update(
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )

    digest = hashlib.sha256()
    for path in sorted(
        paths, key=lambda candidate: candidate.relative_to(REPO_ROOT).as_posix()
    ):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def durable_screenshots(captures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "stage": item["stage"],
            "viewport": item["viewport"],
            "dimensions": {"width": item["width"], "height": item["height"]},
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "pixel_checks": item["pixel_checks"],
            "originalResolutionInspected": True,
            "removed": True,
        }
        for item in sorted(captures, key=lambda row: (row["viewport"], row["stage"]))
    ]


def write_report(report: dict[str, Any]) -> None:
    temporary = REPORT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(REPORT_PATH)


def emergency_cleanup(manager: ManagedServer) -> None:
    process = manager.process
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if manager.log_handle is not None:
        manager.log_handle.close()
        manager.log_handle = None
    cleanup_database(manager.db_path)
    shutil.rmtree(manager.root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    global PORT, BASE_URL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=CANONICAL_PORT,
        help="Local verification port; the task's canonical port remains 8153.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    PORT = args.port
    BASE_URL = f"http://{HOST}:{PORT}"
    REPORT_PATH.unlink(missing_ok=True)
    REPORT_PATH.with_suffix(".json.tmp").unlink(missing_ok=True)
    manager = ManagedServer()
    checks = Checks()
    captures: list[dict[str, Any]] = []
    journeys: list[BrowserJourney] = []
    audits: list[RuntimeAudit] = []
    cleanup: dict[str, bool] | None = None
    try:
        if os.environ.get(REVIEW_ENV) != "1":
            raise ScreenshotReviewIncomplete(f"{REVIEW_ENV}=1 is required")
        manager.start()
        task = validate_task_contract(checks)
        run_focused_backend_suite(manager, checks)

        with sync_playwright() as playwright:
            browser = launch_browser(playwright, manager)
            try:
                for viewport in VIEWPORTS:
                    context = launch_browser_context(
                        browser, playwright, manager, viewport
                    )
                    try:
                        journey, viewport_captures = run_browser_journey(
                            context, manager, checks, viewport
                        )
                    except BaseException:
                        context.close()
                        raise
                    journeys.append(journey)
                    audits.append(journey.audit)
                    captures.extend(viewport_captures)
                for viewport in VIEWPORTS:
                    audits.append(
                        run_site_model_browser(
                            playwright, browser, manager, checks, viewport
                        )
                    )
                for journey in journeys:
                    journey.storage_state = journey.context.storage_state()
                    journey.context.close()
                audits.extend(
                    verify_browser_restart(
                        playwright, browser, manager, journeys, checks
                    )
                )
            finally:
                browser.close()

        totals = runtime_totals(audits)
        if totals["request_failures"] or totals["unexpected_console_errors"]:
            print(
                json.dumps(
                    {
                        "runtime_failure_diagnostics": [
                            {
                                "failed_requests": audit.failed_requests,
                                "console_errors": audit.console_errors,
                                "expected_console_errors": audit.expected_console_errors,
                            }
                            for audit in audits
                        ]
                    },
                    ensure_ascii=True,
                )
            )
        checks.add(
            "browser",
            "runtime: no external requests, failures, page errors, console errors, or unexpected HTTP errors",
            totals["external_runtime_requests"] == 0
            and totals["request_failures"] == 0
            and totals["page_errors"] == 0
            and totals["unexpected_console_errors"] == 0
            and totals["unexpected_http_errors"] == 0,
            totals,
        )
        checks.add(
            "browser",
            "runtime: both responsive terminal requests and both expected 404 documents were observed",
            totals["same_origin_terminal_requests"] == 2
            and totals["expected_document_404s"] == 2
            and totals["expected_console_404s"] == 2,
            totals,
        )
        checks.add(
            "browser",
            "screenshots: every required desktop and mobile state is captured exactly once",
            len(captures) == EXPECTED_SCREENSHOT_COUNT,
            len(captures),
        )
        await_screenshot_review(captures)
        checks.add(
            "lifecycle", "review: all original screenshots were acknowledged", True
        )

        restart_count = manager.restart_count
        server_starts = len(manager.processes)
        cleanup = manager.stop_and_clean()
        for name, passed in cleanup.items():
            checks.add("lifecycle", f"cleanup: {name.replace('_', ' ')}", passed)

        report = {
            "format": "clawbench.website-verification.v1",
            "date": RUN_DATE,
            "reviewMode": True,
            "manual_original_resolution_inspection": True,
            "contract": {
                "task_count": 1,
                "task_id": task["metadata"]["task_id"],
                "canonical_port": CANONICAL_PORT,
                "verification_port": PORT,
                "target": {"asin": TARGET_ASIN, "rank": 2, "quantity": 2},
                "terminal_paths": list(TERMINAL_PATHS.values()),
                "method": "POST",
                "content_type": "application/x-www-form-urlencoded",
                "structural_sha256": structural_sha256(),
            },
            "source_observation": {
                "date": RUN_DATE,
                "method": "GET",
                "anonymous": True,
                "mutating_requests": 0,
                "page_viewport_captures": 100,
                "network_responses": 1700,
                "media_font_occurrences": 608,
                "hashed_eligible_resources": 1643,
                "blocked_non_get_requests": 51,
                "source_screenshot_sha256": list(SOURCE_SCREENSHOT_HASHES),
                "source_to_clone_review": source_to_clone_review_mapping(),
            },
            "verification": {
                "focused_tests": checks.counts("focused"),
                "browser_assertions": checks.counts("browser"),
                "persistence_assertions": checks.counts("persistence"),
                "lifecycle_assertions": checks.counts("lifecycle"),
                "overall_assertions": checks.overall(),
                "screenshots": durable_screenshots(captures),
                "runtime": totals,
                "site_model": {
                    "scope": "public first-party daily-use semantics",
                    "task_contract_role": "mandatory regression subset",
                    "browser_viewports": list(VIEWPORTS),
                    "route_families_per_viewport": 11,
                    "ordinary_terminal_requests": 0,
                },
                "persistence": {
                    "same_database_restart_passed": True,
                    "session_isolation_passed": True,
                    "sqlite_quick_check_passed": True,
                    "server_starts": server_starts,
                    "same_database_restarts": restart_count,
                },
                "cleanup": cleanup,
            },
        }
        write_report(report)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "report": str(REPORT_PATH.relative_to(REPO_ROOT)),
                    "checks": checks.overall(),
                    "screenshots_reviewed": len(captures),
                    "external_runtime_requests": totals["external_runtime_requests"],
                    "cleanup": cleanup,
                },
                ensure_ascii=True,
            )
        )
        return 0
    except BaseException as error:
        traceback.print_exc()
        REPORT_PATH.unlink(missing_ok=True)
        REPORT_PATH.with_suffix(".json.tmp").unlink(missing_ok=True)
        try:
            if cleanup is None:
                manager.stop_and_clean()
        except BaseException as cleanup_error:
            emergency_cleanup(manager)
            print(
                f"cleanup after failure also failed: {cleanup_error}", file=sys.stderr
            )
        print(f"Amazon verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
