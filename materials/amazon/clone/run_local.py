"""Run the clone with its zero-secret loopback SMTP inbox."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "runtime"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Amazon Clone with a loopback-only SMTP browser inbox"
    )
    parser.add_argument("--port", type=int, default=8153)
    parser.add_argument("--admin-port", type=int, default=8154)
    parser.add_argument("--smtp-port", type=int, default=18125)
    parser.add_argument("--inbox-port", type=int, default=8155)
    parser.add_argument("--db", type=Path)
    parser.add_argument(
        "--detach",
        action="store_true",
        help="start in the background and write startup output under runtime/",
    )
    args = parser.parse_args()
    for name in ("port", "admin_port", "smtp_port", "inbox_port"):
        value = int(getattr(args, name))
        if not 1 <= value <= 65535:
            parser.error(f"--{name.replace('_', '-')} must be between 1 and 65535")
    if len({args.port, args.admin_port, args.smtp_port, args.inbox_port}) != 4:
        parser.error("storefront, admin, SMTP, and inbox ports must be distinct")
    return args


def _start_detached() -> int:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        *(argument for argument in sys.argv[1:] if argument != "--detach"),
    ]
    creationflags = 0
    start_new_session = os.name != "nt"
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    with (RUNTIME_ROOT / "local-stack.out.log").open("ab") as stdout_log, (
        RUNTIME_ROOT / "local-stack.err.log"
    ).open("ab") as stderr_log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout_log,
            stderr=stderr_log,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
    print(f"Amazon Clone local stack started as PID {process.pid}")
    return 0


def _wait_for_inbox(process: subprocess.Popen[bytes], url: str) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("local SMTP inbox exited before becoming healthy")
        try:
            with urlopen(url, timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload == {"ok": True, "mode": "LOCAL_SMTP_CAPTURE", "messages": 0}:
                return
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            time.sleep(0.1)
    raise RuntimeError("local SMTP inbox did not become healthy")


def main() -> int:
    args = parse_args()
    if args.detach:
        return _start_detached()
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    inbox_url = f"http://127.0.0.1:{args.inbox_port}/"
    inbox_command = [
        sys.executable,
        "-u",
        str(ROOT / "local_smtp_inbox.py"),
        "--smtp-host",
        "127.0.0.1",
        "--smtp-port",
        str(args.smtp_port),
        "--web-host",
        "127.0.0.1",
        "--web-port",
        str(args.inbox_port),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with (RUNTIME_ROOT / "local-smtp.out.log").open("ab") as stdout_log, (
        RUNTIME_ROOT / "local-smtp.err.log"
    ).open("ab") as stderr_log:
        inbox_process = subprocess.Popen(
            inbox_command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout_log,
            stderr=stderr_log,
            creationflags=creationflags,
        )
        try:
            _wait_for_inbox(inbox_process, inbox_url + "healthz")
            os.environ.update(
                {
                    "AMAZON_CLONE_SMTP_HOST": "127.0.0.1",
                    "AMAZON_CLONE_SMTP_PORT": str(args.smtp_port),
                    "AMAZON_CLONE_SMTP_TLS": "none",
                    "AMAZON_CLONE_SMTP_FROM": (
                        "Amazon Clone <no-reply@amazon-clone.local>"
                    ),
                    "AMAZON_CLONE_SMTP_TIMEOUT_SECONDS": "5",
                    "AMAZON_CLONE_REQUIRE_SMTP": "1",
                    "AMAZON_CLONE_LOCAL_INBOX_URL": inbox_url,
                }
            )
            os.environ.pop("AMAZON_CLONE_SMTP_USERNAME", None)
            os.environ.pop("AMAZON_CLONE_SMTP_PASSWORD", None)
            print(f"Storefront: http://127.0.0.1:{args.port}", flush=True)
            print(f"Local SMTP inbox: {inbox_url}", flush=True)
            print(
                "Delivery boundary: messages are captured locally and never sent to the internet.",
                flush=True,
            )
            original_argv = sys.argv
            sys.argv = [
                "server.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--admin-host",
                "127.0.0.1",
                "--admin-port",
                str(args.admin_port),
            ]
            if args.db is not None:
                sys.argv.extend(["--db", str(args.db)])
            try:
                from server import main as server_main

                return server_main()
            finally:
                sys.argv = original_argv
        finally:
            if inbox_process.poll() is None:
                inbox_process.terminate()
                try:
                    inbox_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    inbox_process.kill()
                    inbox_process.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
