# Harbor Pickup

Reconstruct a pickup-only store with mandatory store and slot selection, store-isolated inventory, shared slot capacity, atomic resource consumption, persistent orders, and cutoff-aware cancellation that restores both resources exactly once.

## Observable business rules

- **Quantity**: `store_stock` (maximum=12, minimum=1).
- **Pricing**: `standard` (tax_basis_points=500).
- **Inventory**: `store_isolated` (atomic_with_slot_capacity=True).
- **Fulfillment**: `pickup_slots` (shipping_allowed=False, slot_capacity=2, slot_required=True, store_required=True).
- **Cancellation**: `pickup_cutoff` (minimum_notice_minutes=60, restore_capacity_once=True, restore_inventory_once=True).
- **Token Lifetime**: `fixed` (reset_minutes=60, verification_minutes=30).

The controlled clock, validation errors, inventory state, totals, and cancellation availability are observable through normal browser flows.
