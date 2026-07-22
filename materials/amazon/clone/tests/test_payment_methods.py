from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_methods import (  # noqa: E402
    DEFAULT_PAYMENT_METHOD,
    LEGACY_TEST_CARD,
    PAYMENT_APPROVED,
    PAYMENT_DECLINED,
    PAYMENT_METHODS,
    SANDBOX_BANK_APPROVED,
    SANDBOX_CARD_APPROVED,
    SANDBOX_CARD_DECLINED,
    payment_method,
    payment_method_label,
    public_payment_methods,
)


class PaymentMethodTests(unittest.TestCase):
    def test_public_scenarios_cover_card_bank_success_and_card_decline(self) -> None:
        methods = {method.identifier: method for method in PAYMENT_METHODS}
        self.assertEqual(DEFAULT_PAYMENT_METHOD, SANDBOX_CARD_APPROVED)
        self.assertEqual(methods[SANDBOX_CARD_APPROVED].outcome, PAYMENT_APPROVED)
        self.assertEqual(methods[SANDBOX_BANK_APPROVED].outcome, PAYMENT_APPROVED)
        self.assertEqual(methods[SANDBOX_CARD_DECLINED].outcome, PAYMENT_DECLINED)
        self.assertEqual(
            methods[SANDBOX_CARD_DECLINED].decline_code,
            "sandbox-card-declined",
        )

    def test_only_opaque_scenario_identifiers_and_non_secret_metadata_are_public(self) -> None:
        payload = public_payment_methods()
        self.assertEqual(len(payload), 3)
        self.assertEqual(
            {frozenset(method) for method in payload},
            {frozenset({"identifier", "label", "description", "outcome"})},
        )
        flattened = repr(payload).casefold()
        for forbidden in ("pan", "cvv", "security code", "routing number", "expiry"):
            self.assertNotIn(forbidden, flattened)

    def test_unknown_or_legacy_identifiers_cannot_start_new_attempts(self) -> None:
        for identifier in ("", "visa", LEGACY_TEST_CARD, "4111111111111111"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    payment_method(identifier)

    def test_legacy_attempts_still_have_a_safe_display_label(self) -> None:
        self.assertEqual(
            payment_method_label(LEGACY_TEST_CARD),
            "Legacy sandbox test card",
        )
        self.assertEqual(
            payment_method_label(SANDBOX_BANK_APPROVED),
            "Sandbox bank account",
        )


if __name__ == "__main__":
    unittest.main()
