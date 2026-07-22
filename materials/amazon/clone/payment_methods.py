"""Safe, deterministic payment tools for the local checkout simulation.

These identifiers are scenarios, not payment credentials.  The public checkout
must never accept a PAN, expiry date, CVV, bank routing number, or similar
secret.  Keeping the outcome table server-owned makes decline and retry
behaviour reproducible without pretending that a real processor is involved.
"""

from __future__ import annotations

from dataclasses import dataclass


PAYMENT_APPROVED = "APPROVED"
PAYMENT_DECLINED = "DECLINED"

LEGACY_TEST_CARD = "test-card"
SANDBOX_CARD_APPROVED = "sandbox-card-approved"
SANDBOX_CARD_DECLINED = "sandbox-card-declined"
SANDBOX_BANK_APPROVED = "sandbox-bank-approved"
DEFAULT_PAYMENT_METHOD = SANDBOX_CARD_APPROVED


@dataclass(frozen=True, slots=True)
class SandboxPaymentMethod:
    identifier: str
    label: str
    description: str
    outcome: str
    decline_code: str | None = None


PAYMENT_METHODS: tuple[SandboxPaymentMethod, ...] = (
    SandboxPaymentMethod(
        identifier=SANDBOX_CARD_APPROVED,
        label="Sandbox card · ending in 4242",
        description="Simulated card authorization succeeds",
        outcome=PAYMENT_APPROVED,
    ),
    SandboxPaymentMethod(
        identifier=SANDBOX_CARD_DECLINED,
        label="Sandbox card · ending in 0002",
        description="Simulated issuer decline for testing retry",
        outcome=PAYMENT_DECLINED,
        decline_code="sandbox-card-declined",
    ),
    SandboxPaymentMethod(
        identifier=SANDBOX_BANK_APPROVED,
        label="Sandbox bank account",
        description="Simulated bank authorization succeeds",
        outcome=PAYMENT_APPROVED,
    ),
)

_BY_IDENTIFIER = {method.identifier: method for method in PAYMENT_METHODS}


def payment_method(identifier: str) -> SandboxPaymentMethod:
    """Return a supported public sandbox method or reject the identifier."""

    if not isinstance(identifier, str):
        raise ValueError("unsupported sandbox payment method")
    try:
        return _BY_IDENTIFIER[identifier]
    except KeyError as exc:
        raise ValueError("unsupported sandbox payment method") from exc


def payment_method_label(identifier: str) -> str:
    """Return a safe display label, including legacy persisted test attempts."""

    if identifier == LEGACY_TEST_CARD:
        return "Legacy sandbox test card"
    return payment_method(identifier).label


def public_payment_methods() -> tuple[dict[str, str], ...]:
    """Return the non-secret method metadata needed by the checkout renderer."""

    return tuple(
        {
            "identifier": method.identifier,
            "label": method.label,
            "description": method.description,
            "outcome": method.outcome,
        }
        for method in PAYMENT_METHODS
    )


__all__ = [
    "DEFAULT_PAYMENT_METHOD",
    "LEGACY_TEST_CARD",
    "PAYMENT_APPROVED",
    "PAYMENT_DECLINED",
    "PAYMENT_METHODS",
    "SANDBOX_BANK_APPROVED",
    "SANDBOX_CARD_APPROVED",
    "SANDBOX_CARD_DECLINED",
    "SandboxPaymentMethod",
    "payment_method",
    "payment_method_label",
    "public_payment_methods",
]
