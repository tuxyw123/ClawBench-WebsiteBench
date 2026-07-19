# Amazon clone — HITL Gate 2

## Outcome

- Public runtime: FastAPI SSR with progressively enhanced local JavaScript.
- State engine: strict loopback-only request service backed by SQLite.
- Catalog: 200 products, 10 departments, 20 categories.
- Locale: en-US, USD, New York 10001.
- Browser journeys: 14/14 passed with 31 assertions.
- Legacy task/security regression: 159/159 passed.
- Runtime audit: zero external requests, request failures, and page errors.

## Architecture boundary

`Browser → FastAPI SSR edge → loopback state engine → SQLite`

FastAPI owns the exposed socket, SSR shell, static assets, and security headers. The internal engine preserves the exact benchmark terminal request, validation, journal, persistence, and isolation semantics. Checkout, identity, payment, delivery changes, and real orders remain local no-effect boundaries.

## Fourteen journeys

- `J01` Storefront discovery: passed (3 assertions)
- `J02` Department drawer: passed (2 assertions)
- `J03` Search autocomplete: passed (2 assertions)
- `J04` Populated search: passed (2 assertions)
- `J05` Search refine sort paginate: passed (3 assertions)
- `J06` No-results recovery: passed (2 assertions)
- `J07` Best Sellers discovery: passed (3 assertions)
- `J08` Task product gallery and variants: passed (2 assertions)
- `J09` Exact task add to cart: passed (2 assertions)
- `J10` Generic product cart: passed (2 assertions)
- `J11` Save for later: passed (1 assertions)
- `J12` Session-local list: passed (1 assertions)
- `J13` History and deals rediscovery: passed (2 assertions)
- `J14` Safe account and purchase boundaries: passed (4 assertions)

## Representative visual review

- [Desktop storefront](desktop-home.png): Dense ten-department storefront and product rails are readable.
- [Desktop search](desktop-search.png): Facets, sorting, sixteen-result page, prices, and actions remain legible.
- [Desktop task cart](desktop-task-cart.png): Quantity two and $439.98 subtotal are prominent.
- [Mobile storefront](mobile-home.png): Responsive modules become horizontal rails without broken controls.
- [Mobile category](mobile-category.png): Header, category rail, featured shops, and prices follow the source hierarchy.
- [Mobile task product](mobile-task-product.png): Gallery, variants, quantity, Add to cart, reviews, and footer remain reachable.

## Intentional boundaries

- Raw Amazon HTML, media, and screenshots remain private Gate 1 evidence and are not runtime assets.
- The 200-product catalog intentionally reuses twelve independently authored sprite cells; product identity is carried by title, brand, category, price, variants, and ASIN.
- Account, orders, checkout, payment, Buy Now, and delivery changes remain visible safe stops.
- BrowserUse comparison against the live source is reserved for the later interaction-parity phase; Gate 2 validates the clone itself.

## Approval requested

Approve Gate 2 to freeze the SSR/catalog implementation and proceed to the next fidelity phase.
