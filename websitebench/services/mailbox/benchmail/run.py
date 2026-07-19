"""Run mailbox public and admin listeners."""

from __future__ import annotations

import asyncio
import os

import uvicorn

from .app import from_environment


async def serve() -> None:
    public_app, admin_app = from_environment()
    host = os.environ.get("HOST", "0.0.0.0")
    public = uvicorn.Server(
        uvicorn.Config(public_app, host=host, port=int(os.environ.get("MAILBOX_PORT", "8025")))
    )
    admin = uvicorn.Server(
        uvicorn.Config(admin_app, host=host, port=int(os.environ.get("MAILBOX_ADMIN_PORT", "8026")))
    )
    await asyncio.gather(public.serve(), admin.serve())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

