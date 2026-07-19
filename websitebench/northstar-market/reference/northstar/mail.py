"""Delivery-only adapter for the benchmark mailbox service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx


DeliverMail = Callable[[str, str, str], Awaitable[None]]


def http_mailer(api_url: str, token: str) -> DeliverMail:
    async def deliver(to: str, subject: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{api_url.rstrip('/')}/api/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"schema_version": 1, "to": to, "subject": subject, "text": text},
            )
            response.raise_for_status()

    return deliver

