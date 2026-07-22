# Amazon current source evidence — 2026-07-21

This directory contains direct browser observations from the anonymous
`amazon.com` Singapore/USD storefront at a 1365×900 CSS viewport. The
international shopping transition alert was expanded in all three screenshots.

## Direct artifacts

- `desktop-ranking-external-ssd-1365x900.png` — hydrated External Solid State
  Drives Best Sellers page. The first six product cards, desktop navigation,
  hero, category tree, and first two grid rows are visibly rendered.
- `desktop-pdp-t7-1365x900.png` — hydrated Samsung T7 1 TB Titan Gray product
  detail page. Gallery, product facts, variants, buy box, quantity control, and
  Add to cart are visibly rendered.
- `desktop-empty-cart-after-pdp-1365x900.png` — empty cart fetched after first
  visiting the T7 PDP in the same anonymous Singapore/USD session. No
  source-site Add to cart POST was issued. The expanded delivery overlay, empty
  state, recently viewed T7 panel, recommendation rail, and footer are visible.
- `observations.json` — bounded DOM geometry and the current first-six ranking
  facts used by the local fixture, plus the directly observed empty-cart state.

These artifacts prove only the captured desktop state. They do not prove a
hydrated mobile layout. A populated quantity-2 source cart after a real Amazon
POST remains unavailable and is therefore inferred rather than evidenced.
