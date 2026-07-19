"""Expose a deliberately small subset of BrowserUse 0.12.6 commands."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request


ALLOWED_ACTIONS = {
    "navigate",
    "state",
    "click",
    "input",
    "type",
    "select",
    "hover",
    "keys",
    "scroll",
    "back",
    "forward",
    "reload",
    "wait",
    "screenshot",
}
INDEX_ACTIONS = {"click", "input", "select", "hover"}
TEXT_ACTIONS = {"input", "type", "select", "keys"}
SESSION_PATTERN = re.compile(r"^[a-z0-9_-]{8,80}$")


@dataclass
class GatewayConfig:
    token: str
    reference_url: str
    mailbox_url: str
    candidate_url: str
    action_budget: int
    artifact_dir: Path
    command: str = "browser-use"

    @property
    def targets(self) -> dict[str, str]:
        return {
            "reference": self.reference_url.rstrip("/"),
            "mailbox": self.mailbox_url.rstrip("/"),
            "candidate": self.candidate_url.rstrip("/"),
        }


@dataclass
class Session:
    id: str
    target: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BrowserGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.config.artifact_dir.mkdir(parents=True, exist_ok=True)
        (self.config.artifact_dir / "screenshots").mkdir(exist_ok=True)
        self.sessions: dict[str, Session] = {}
        self.action_count = 0
        self.budget_lock = asyncio.Lock()

    def authorized(self, authorization: str | None) -> None:
        expected = f"Bearer {self.config.token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=404)

    def allowed_url(self, value: str, target: str) -> bool:
        if target not in self.config.targets:
            return False
        try:
            candidate = urlsplit(value)
            allowed = urlsplit(self.config.targets[target])
        except ValueError:
            return False
        candidate_port = candidate.port or (443 if candidate.scheme == "https" else 80)
        allowed_port = allowed.port or (443 if allowed.scheme == "https" else 80)
        return (
            candidate.scheme in {"http", "https"}
            and candidate.scheme == allowed.scheme
            and candidate.hostname == allowed.hostname
            and candidate_port == allowed_port
            and candidate.username is None
            and candidate.password is None
        )

    async def run(self, *arguments: str, timeout: float = 45) -> str:
        process = await asyncio.create_subprocess_exec(
            self.config.command,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "NO_COLOR": "1"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise HTTPException(status_code=504, detail="browser command timed out")
        output = stdout.decode("utf-8", "replace")
        if process.returncode != 0:
            detail = stderr.decode("utf-8", "replace")[-4000:]
            raise HTTPException(status_code=502, detail=f"browser command failed: {detail}")
        return output[-65_536:]

    async def consume_budget(self) -> int:
        async with self.budget_lock:
            if self.action_count >= self.config.action_budget:
                raise HTTPException(status_code=429, detail="browser action budget exhausted")
            self.action_count += 1
            return self.action_count

    async def current_url(self, session: Session) -> str:
        output = await self.run("-s", session.id, "get", "url")
        urls = re.findall(r"https?://[^\s'\"]+", output)
        return urls[-1].rstrip(")],") if urls else output.strip().splitlines()[-1]

    async def ensure_location(self, session: Session) -> str:
        current = await self.current_url(session)
        if not self.allowed_url(current, session.target):
            await self.run("-s", session.id, "close")
            self.sessions.pop(session.id, None)
            raise HTTPException(status_code=403, detail="browser left its target origin; session closed")
        return current

    def log(self, record: dict[str, Any]) -> None:
        path = self.config.artifact_dir / "actions.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    async def create_session(self, target: str, path: str = "/") -> dict[str, Any]:
        if target not in self.config.targets:
            raise HTTPException(status_code=422, detail="unknown browser target")
        if not path.startswith("/") or path.startswith("//"):
            raise HTTPException(status_code=422, detail="path must be same-origin")
        session_id = f"wb_{secrets.token_hex(10)}"
        session = Session(id=session_id, target=target)
        url = f"{self.config.targets[target]}{path}"
        count = await self.consume_budget()
        output = await self.run("-s", session_id, "open", url)
        self.sessions[session_id] = session
        current = await self.ensure_location(session)
        self.log(
            {
                "sequence": count,
                "timestamp": time.time(),
                "session_id": session_id,
                "target": target,
                "action": "create",
                "url": current,
                "result": output[-4000:],
            }
        )
        return {"session_id": session_id, "target": target, "url": current, "result": output}

    async def act(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if not SESSION_PATTERN.fullmatch(session_id) or session_id not in self.sessions:
            raise HTTPException(status_code=404)
        action = body.get("action")
        if action not in ALLOWED_ACTIONS:
            raise HTTPException(status_code=422, detail="action is not allowed")
        session = self.sessions[session_id]
        async with session.lock:
            count = await self.consume_budget()
            arguments = ["-s", session_id]
            if action == "navigate":
                url = str(body.get("url", ""))
                if not self.allowed_url(url, session.target):
                    raise HTTPException(status_code=403, detail="navigation must remain on target origin")
                arguments += ["open", url]
            elif action in INDEX_ACTIONS:
                index = body.get("index")
                if not isinstance(index, int) or index < 0:
                    raise HTTPException(status_code=422, detail="non-negative element index required")
                arguments += [action, str(index)]
                if action in {"input", "select"}:
                    arguments.append(str(body.get("text", "")))
            elif action in TEXT_ACTIONS:
                arguments += [action, str(body.get("text", ""))]
            elif action == "scroll":
                direction = body.get("direction", "down")
                if direction not in {"up", "down"}:
                    raise HTTPException(status_code=422, detail="scroll direction must be up or down")
                arguments += ["scroll", direction]
            elif action == "wait":
                seconds = body.get("seconds", 1)
                if not isinstance(seconds, (int, float)) or not 0 <= seconds <= 10:
                    raise HTTPException(status_code=422, detail="wait must be between 0 and 10 seconds")
                await asyncio.sleep(seconds)
                arguments += ["state"]
            elif action == "screenshot":
                screenshot = self.config.artifact_dir / "screenshots" / f"{count:04d}-{session_id}.png"
                arguments += ["screenshot", str(screenshot)]
            else:
                arguments += [action]
            output = await self.run(*arguments)
            current = await self.ensure_location(session)
            result: dict[str, Any] = {
                "sequence": count,
                "session_id": session_id,
                "action": action,
                "url": current,
                "result": output,
                "remaining_actions": self.config.action_budget - self.action_count,
            }
            if action == "screenshot":
                result["screenshot_base64"] = base64.b64encode(screenshot.read_bytes()).decode("ascii")
            self.log({**result, "screenshot_base64": "<returned-to-agent>" if action == "screenshot" else None, "timestamp": time.time()})
            return result

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            raise HTTPException(status_code=404)
        async with session.lock:
            await self.run("-s", session_id, "close")


def create_app(gateway: BrowserGateway) -> FastAPI:
    app = FastAPI(title="Controlled BrowserUse Gateway", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine": "browser-use",
            "engine_version": "0.12.6",
            "actions_used": gateway.action_count,
            "actions_budget": gateway.config.action_budget,
        }

    @app.post("/v1/sessions")
    async def create_session(request: Request, authorization: str | None = Header(None)) -> dict[str, Any]:
        gateway.authorized(authorization)
        body = await request.json()
        if not isinstance(body, dict) or not set(body) <= {"target", "path"}:
            raise HTTPException(status_code=422, detail="invalid session request")
        return await gateway.create_session(str(body.get("target", "")), str(body.get("path", "/")))

    @app.post("/v1/sessions/{session_id}/actions")
    async def action(
        session_id: str, request: Request, authorization: str | None = Header(None)
    ) -> dict[str, Any]:
        gateway.authorized(authorization)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="invalid action request")
        return await gateway.act(session_id, body)

    @app.delete("/v1/sessions/{session_id}", status_code=204)
    async def close(session_id: str, authorization: str | None = Header(None)) -> None:
        gateway.authorized(authorization)
        await gateway.close(session_id)

    return app


def from_environment() -> FastAPI:
    config = GatewayConfig(
        token=os.environ.get("GATEWAY_TOKEN", "development-gateway-token"),
        reference_url=os.environ.get("REFERENCE_URL", "http://reference-app:8080"),
        mailbox_url=os.environ.get("PUBLIC_MAILBOX_URL", "http://mailbox:8025"),
        candidate_url=os.environ.get("CANDIDATE_URL", "http://rootless-buildkit:18080"),
        action_budget=int(os.environ.get("BROWSER_ACTION_BUDGET", "1000")),
        artifact_dir=Path(os.environ.get("GATEWAY_ARTIFACT_DIR", "/tmp/websitebench-browser")),
    )
    return create_app(BrowserGateway(config))


app = from_environment()
