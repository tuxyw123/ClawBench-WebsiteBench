# Foundry Wholesale

Reconstruct a wholesale storefront where authentication, case quantities, tiered line discounts, post-discount shipping thresholds, tax, token expiry, persistence, and cancellation rules are observable and enforced.

## Observable business rules

- **Quantity**: `wholesale_case` (case_size=3, login_required=True, maximum=24, minimum=3).
- **Pricing**: `tiered_line_discount` (tax_basis_points=650, tiers=[{'minimum': 6, 'percent': 5}, {'minimum': 12, 'percent': 10}]).
- **Inventory**: `checkout_decrement` (atomic=True).
- **Fulfillment**: `shipping` (free_threshold_cents=25000, standard_cents=1200, threshold_basis=discounted_subtotal).
- **Cancellation**: `window` (minutes=120).
- **Token Lifetime**: `fixed` (reset_minutes=90, verification_minutes=45).

The controlled clock, validation errors, inventory state, totals, and cancellation availability are observable through normal browser flows.
