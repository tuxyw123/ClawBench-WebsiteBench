"""Explicit-allowlist, on-demand lifecycle for the Amazon review clone."""

from __future__ import annotations

import atexit
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..amazon_contract import AMAZON_ITEM_KEY


class CloneGatewayError(RuntimeError):
    pass


class CloneProcessManager:
    def __init__(
        self,
        repo_root: Path,
        items: list[dict[str, Any]],
        allowlist: set[str],
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.items = {item["key"]: item for item in items}
        self.allowlist = allowlist
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._base_urls: dict[str, str] = {}
        self._temporary: dict[str, tempfile.TemporaryDirectory[str]] = {}
        self._lock = threading.Lock()
        atexit.register(self.close)

    def is_allowed(self, item_key: str) -> bool:
        item = self.items.get(item_key)
        return bool(
            item_key == AMAZON_ITEM_KEY
            and item_key in self.allowlist
            and item
            and item.get("source_type") == "benchmark"
            and item.get("internal", {}).get("server_command")
        )

    @staticmethod
    def _ready(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            return False

    @staticmethod
    def _available_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _managed_base(self, item_key: str) -> str | None:
        process = self._processes.get(item_key)
        base = self._base_urls.get(item_key)
        if not process or not base or process.poll() is not None:
            return None
        try:
            port = int(base.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None
        return base if self._ready(port) else None

    def ensure(self, item_key: str) -> str:
        if not self.is_allowed(item_key):
            raise CloneGatewayError("clone is not in the explicit gateway allowlist")
        item = self.items[item_key]
        host = item["internal"].get("local_host") or ""
        try:
            port = int(str(host).rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise CloneGatewayError("clone metadata has no explicit local port") from exc
        managed = self._managed_base(item_key)
        if managed:
            return managed
        with self._lock:
            managed = self._managed_base(item_key)
            if managed:
                return managed
            if item_key in self._processes:
                self._stop(item_key)
            if self._ready(port):
                port = self._available_port()
            command = shlex.split(item["internal"]["server_command"])
            if (
                len(command) < 2
                or Path(command[0]).name not in {"python", "python3", "python.exe"}
            ):
                raise CloneGatewayError("only declared Python clone servers may be started")
            command[0] = sys.executable
            clone_root = (self.repo_root / item["internal"]["clone_root"]).resolve()
            script = (self.repo_root / command[1]).resolve()
            if (
                not script.is_file()
                or clone_root not in script.parents
                or script.name != "server.py"
            ):
                raise CloneGatewayError("declared clone server must be inside its clone root")
            command[1] = str(script)
            for option, value in (("--host", "127.0.0.1"), ("--port", str(port))):
                if option in command:
                    command[command.index(option) + 1] = value
                else:
                    command.extend([option, value])
            temporary = tempfile.TemporaryDirectory(prefix=f"clawbench-viewer-{item_key}-")
            database = str(Path(temporary.name) / "state.sqlite3")
            if "--db" in command:
                command[command.index("--db") + 1] = database
            else:
                command.extend(["--db", database])
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            process = subprocess.Popen(
                command,
                cwd=self.repo_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                start_new_session=True,
            )
            self._processes[item_key] = process
            self._base_urls[item_key] = f"http://127.0.0.1:{port}"
            self._temporary[item_key] = temporary
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if self._ready(port):
                    return self._base_urls[item_key]
                time.sleep(0.1)
            self._stop(item_key)
            raise CloneGatewayError("clone did not become ready")

    def _stop(self, item_key: str) -> None:
        process = self._processes.pop(item_key, None)
        self._base_urls.pop(item_key, None)
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
        temporary = self._temporary.pop(item_key, None)
        if temporary:
            temporary.cleanup()

    def close(self) -> None:
        with self._lock:
            for key in list(self._processes):
                self._stop(key)
