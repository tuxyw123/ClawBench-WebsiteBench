"""Run public and private reference listeners in one container."""

from __future__ import annotations

import asyncio
import os

import uvicorn

from .app import build_runtime, create_admin_app, create_public_app


async def serve() -> None:
    runtime = build_runtime()
    host = os.environ.get("HOST", "0.0.0.0")
    public_port = int(os.environ.get("PORT", "8080"))
    admin_port = int(os.environ.get("BENCH_ADMIN_PORT", "8081"))
    public_server = uvicorn.Server(
        uvicorn.Config(create_public_app(runtime), host=host, port=public_port, log_level="info")
    )
    admin_server = uvicorn.Server(
        uvicorn.Config(create_admin_app(runtime), host=host, port=admin_port, log_level="info")
    )
    await asyncio.gather(public_server.serve(), admin_server.serve())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()

