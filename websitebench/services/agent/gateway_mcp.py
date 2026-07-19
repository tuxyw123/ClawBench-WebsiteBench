"""Local stdio MCP bridge from Codex to controlled benchmark services."""

from __future__ import annotations

import base64
import os
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP, Image


mcp = FastMCP(
    "WebsiteBench controlled tools",
    instructions=(
        "Reference and mailbox exploration must use these tools. Raw HTTP, source, "
        "DOM HTML, network bodies, browser profiles, downloads, and DevTools are unavailable."
    ),
)
GATEWAY_URL = os.environ.get("BROWSER_GATEWAY_URL", "http://browser-gateway:7000").rstrip("/")
GATEWAY_TOKEN = os.environ.get("GATEWAY_TOKEN", "")
BUILDER_URL = os.environ.get("BUILDER_URL", "http://candidate-builder:7100").rstrip("/")
BUILDER_TOKEN = os.environ.get("BUILDER_TOKEN", "")


async def request(
    method: str,
    url: str,
    *,
    token: str,
    json_body: dict[str, Any] | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=json_body,
        )
    if response.status_code >= 400:
        return {"error": response.text, "http_status": response.status_code}
    if response.status_code == 204:
        return {"status": "closed"}
    return response.json()


@mcp.tool()
async def browser_create(
    target: Literal["reference", "mailbox", "candidate"] = "reference",
    path: str = "/",
) -> dict[str, Any]:
    """Create an isolated BrowserUse session on one allowed target and return its ID/state."""
    return await request(
        "POST",
        f"{GATEWAY_URL}/v1/sessions",
        token=GATEWAY_TOKEN,
        json_body={"target": target, "path": path},
    )


@mcp.tool()
async def browser_action(
    session_id: str,
    action: Literal[
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
    ],
    url: str | None = None,
    index: int | None = None,
    text: str | None = None,
    direction: Literal["up", "down"] | None = None,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Take one allowed BrowserUse action. Element actions use indices returned by state."""
    body = {"action": action}
    for key, value in {
        "url": url,
        "index": index,
        "text": text,
        "direction": direction,
        "seconds": seconds,
    }.items():
        if value is not None:
            body[key] = value
    return await request(
        "POST",
        f"{GATEWAY_URL}/v1/sessions/{session_id}/actions",
        token=GATEWAY_TOKEN,
        json_body=body,
    )


@mcp.tool()
async def browser_screenshot(session_id: str) -> Image | str:
    """Capture the current viewport through BrowserUse and return a PNG image."""
    result = await request(
        "POST",
        f"{GATEWAY_URL}/v1/sessions/{session_id}/actions",
        token=GATEWAY_TOKEN,
        json_body={"action": "screenshot"},
    )
    encoded = result.get("screenshot_base64")
    if not encoded:
        return str(result)
    return Image(data=base64.b64decode(encoded), format="png")


@mcp.tool()
async def browser_close(session_id: str) -> dict[str, Any]:
    """Close and destroy a controlled browser session."""
    return await request(
        "DELETE",
        f"{GATEWAY_URL}/v1/sessions/{session_id}",
        token=GATEWAY_TOKEN,
    )


@mcp.tool()
async def candidate_build() -> dict[str, Any]:
    """Validate and build the candidate in the isolated rootless BuildKit environment."""
    return await request(
        "POST",
        f"{BUILDER_URL}/v1/builds",
        token=BUILDER_TOKEN,
        timeout=660,
    )


@mcp.tool()
async def candidate_preview_status() -> dict[str, Any]:
    """Check whether the latest isolated candidate preview is ready for BrowserUse."""
    return await request(
        "GET",
        f"{BUILDER_URL}/v1/preview",
        token=BUILDER_TOKEN,
    )


if __name__ == "__main__":
    mcp.run()

