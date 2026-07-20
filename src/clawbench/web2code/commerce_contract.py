"""Shared account-and-order interface for WebsiteBench commerce runtimes.

Compiled white-label sites use ``PersistentCommerce`` while the Amazon
calibration site uses a SQLite adapter around its legacy request engine.  They
intentionally keep different catalog and cart implementations, but expose one
account lifecycle and order ownership seam to their presentation adapters.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class AccountOrderCommerce(Protocol):
    """Behavior shared by JSON and SQLite commerce implementations."""

    def register(self, email: str, password: str, confirm: str) -> str: ...

    def verify(self, token: str) -> None: ...

    def forgot_password(self, email: str) -> str | None: ...

    def reset_password(self, token: str, password: str, confirm: str) -> None: ...

    def login(self, email: str, password: str, *, device: str) -> str: ...

    def logout(self, token: str | None, *, device: str | None = None) -> None: ...

    def user_for_session(self, token: str | None) -> Mapping[str, Any] | None: ...

    def orders_for(self, user_id: str) -> list[dict[str, Any]]: ...

    def order_for(self, number: str, user_id: str) -> dict[str, Any]: ...

    def cancel(self, number: str, user_id: str) -> dict[str, Any]: ...


def require_account_order_commerce(value: object) -> AccountOrderCommerce:
    """Fail fast when a presentation adapter is wired to an incomplete runtime."""

    if not isinstance(value, AccountOrderCommerce):
        raise TypeError("commerce implementation does not satisfy AccountOrderCommerce")
    return value
