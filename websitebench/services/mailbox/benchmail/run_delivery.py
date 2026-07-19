"""Run the delivery-only mailbox listener."""

from __future__ import annotations

import os

import uvicorn

from .app import delivery_from_environment


def main() -> None:
    uvicorn.run(
        delivery_from_environment(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("MAILBOX_DELIVERY_PORT", "8027")),
    )


if __name__ == "__main__":
    main()
