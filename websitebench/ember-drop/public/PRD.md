# Ember Drop

Reconstruct a limited-release store with one-unit guest-device and account-lifetime limits, non-renewing inventory reservations, ownership transfer on login, decline-safe checkout, expiry release, persistence, and final-sale orders.

## Observable business rules

- **Quantity**: `per_sku_limit` (account_scope=lifetime, guest_scope=device, limit=1).
- **Pricing**: `standard` (tax_basis_points=725).
- **Inventory**: `reservation` (decline_preserves_active=True, login_merge=transfer, repeated_action_extends_ttl=False, ttl_minutes=10).
- **Fulfillment**: `shipping` (free_threshold_cents=0, standard_cents=900).
- **Cancellation**: `final_sale` (cancel_allowed=False).
- **Token Lifetime**: `fixed` (reset_minutes=60, verification_minutes=30).

The controlled clock, validation errors, inventory state, totals, and cancellation availability are observable through normal browser flows.
