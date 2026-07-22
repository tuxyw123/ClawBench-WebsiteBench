# Current Amazon source baseline — 2026-07-20

This refresh was captured from anonymous `amazon.com` in one in-app browser
session. The fixed desktop viewport was `1365×900`, language was English,
delivery region was Singapore, currency was USD, cart count was zero, and the
source was kept GET-only.

## Durable captures

- `desktop-home-1365x900.png`: current Back-to-School home hero, four-card
  first row, desktop navigation, and the international delivery notice.
- `desktop-search-portable-ssd-1365x900.png`: current portable-SSD search,
  including the delivery notice, filters, first two SanDisk results, prices,
  ratings, and add-to-cart buttons. No source mutation was performed.

Amazon's live Best Sellers and PDP DOM were also inspected in this session.
Their heavy pages repeatedly exceeded the CDP screenshot deadline, so the
facts below are recorded as DOM observations rather than pretending an absent
viewport PNG is a direct golden.

## Current task facts

- External SSD Best Sellers route:
  `/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011`
- Rank `#1`: SanDisk Extreme Portable SSD 2TB, `$306.99`, rating `4.6`,
  `91,209` reviews.
- Rank `#2`: Samsung T7, ASIN `B0874XN4D8`, `$219.99`, rating `4.7`,
  `38,085` reviews.
- Rank `#3`: Samsung T9 1TB.
- Samsung T7 PDP route:
  `/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8`
- PDP state: `Amazon's Choice`, `4K+ bought in past month`, `-20%`, list price
  `$274.99`, `In Stock`, quantities 1–3, Add to cart, Buy Now, and five other
  seller offers.

## Evidence boundaries

The two PNGs above are current direct visual evidence. Historical Gate 1
records under `source-capture/` remain useful for geometry and mobile states,
but they mix Germany/EUR, protected pages, missing hydration, and stale dynamic
values. The rebuild therefore keeps current DOM facts, historical direct
visuals, and inferred responsive behavior visibly separate.
