"""Safe process and report primitives for a sequential differential verifier."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx


class ServiceStartError(RuntimeError):
    """A service did not become ready within its declared budget."""


_ACTIVE_SERVICE: "ManagedService | None" = None


@dataclass
class ManagedService:
    """Run one service and guarantee process-group cleanup.

    Reference and candidate services must be used sequentially. Candidate
    processes run as uid/gid 10001 with a scrubbed environment and cannot read
    `/tests` or `/run/verifier-final`.
    """

    name: str
    argv: list[str]
    cwd: Path
    base_url: str
    ready_path: str
    log_path: Path
    env: dict[str, str] = field(default_factory=dict)
    untrusted: bool = False
    process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    log_handle: Any = field(default=None, init=False)

    def start(self, *, timeout_sec: float = 60) -> None:
        global _ACTIVE_SERVICE
        if self.process is not None:
            raise RuntimeError(f"{self.name} is already started")
        if _ACTIVE_SERVICE is not None:
            raise RuntimeError(
                f"service {_ACTIVE_SERVICE.name} is still active; reference and "
                "candidate must run sequentially"
            )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        command = list(self.argv)
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            **self.env,
        }
        if self.untrusted:
            command = [
                "setpriv",
                "--reuid",
                "10001",
                "--regid",
                "10001",
                "--clear-groups",
                "--no-new-privs",
                *command,
            ]
            environment.update(
                {
                    "HOME": "/run/verifier-untrusted",
                    "TMPDIR": "/run/verifier-untrusted",
                    "CLAWBENCH_DATA_DIR": "/run/verifier-untrusted",
                }
            )
        self.log_handle = self.log_path.open("wb")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            self.log_handle.close()
            self.log_handle = None
            raise
        _ACTIVE_SERVICE = self
        deadline = time.monotonic() + timeout_sec
        ready_url = urljoin(self.base_url.rstrip("/") + "/", self.ready_path.lstrip("/"))
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                message = (
                    f"{self.name} exited before ready with code {self.process.returncode}"
                )
                self.stop()
                raise ServiceStartError(message)
            try:
                if httpx.get(ready_url, timeout=2).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        self.stop()
        raise ServiceStartError(f"{self.name} did not become ready: {ready_url}")

    def stop(self) -> None:
        global _ACTIVE_SERVICE
        process = self.process
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            if process.poll() is None:
                process.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.wait(timeout=5)
        if self.log_handle is not None:
            self.log_handle.close()
        self.process = None
        if _ACTIVE_SERVICE is self:
            _ACTIVE_SERVICE = None

    def __enter__(self) -> "ManagedService":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()


def configure_local_urls(contract: dict[str, Any]) -> dict[str, str]:
    """Set verifier-only URL variables to loopback ports from the contract."""

    runtime = contract["runtime"]
    reference = f"http://127.0.0.1:{runtime['verifier_reference_port']}"
    candidate = f"http://127.0.0.1:{runtime['verifier_candidate_port']}"
    values = {
        runtime["reference_url_env"]: reference,
        runtime["reference_admin_url_env"]: reference,
        runtime["candidate_url_env"]: candidate,
        runtime["candidate_admin_url_env"]: candidate,
    }
    os.environ.update(values)
    return values


def write_all_failed_ctrf(
    required_path: Path,
    output_path: Path,
    reason: str,
) -> None:
    """Represent a candidate failure as a valid exact-node CTRF report."""

    required = json.loads(required_path.read_text(encoding="utf-8"))
    nodes = required["nodes"]
    tests = [
        {
            "name": node,
            "status": "failed",
            "message": reason,
            "extra": {"clawbench_score": 0.0},
        }
        for node in nodes
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "results": {
                    "tool": {"name": "clawbench-service-fallback"},
                    "summary": {
                        "tests": len(tests),
                        "passed": 0,
                        "failed": len(tests),
                        "skipped": 0,
                    },
                    "tests": tests,
                    "extra": {"hard_failures": []},
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
