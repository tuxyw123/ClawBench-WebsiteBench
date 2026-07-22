# Amazon current search and commerce-card evidence — 2026-07-22

This directory records a bounded, anonymous browser observation of the current
`amazon.com` Singapore/USD search surface.  It was captured to unblock the
shopping-focused clone's filter, sort, and pagination semantics.

## Directly observed states

- default `portable ssd` results, including the result summary, left refinement
  groups, six sort choices, and the page-1 pager;
- the Samsung brand refinement applied through its real GET link;
- `Price: Low to High` applied through the real sort control;
- page 2 reached through the real `Next` link, including its Previous/page/Next
  navigation state.
- the anonymous `best sellers` search card surface and its Computers department
  search, from which 20 cards with a visible **Add to cart** control were
  retained across Books, Beauty, Home, Toys, and Computers.

The normalized facts and sample ASINs are in `observations.json`.  Volatile
tracking parameters (`qid`, `xpid`, and similar request tokens) are deliberately
not treated as product semantics.

## Evidence boundary

This remains a bounded DOM/card observation, not a complete source-site asset
bundle.  The control evidence does not establish the source site's mobile
filter drawer, history restoration, or every refinement value.  The later card
capture establishes only each retained card's title, ASIN, displayed default
USD price, rating/review display, image, department context, and the presence
of an **Add to cart** control.  It does **not** establish a full PDP, variants,
inventory depth, list price, seller identity, delivery promise, returns terms,
or any non-default offer.

The 20 normalized records and their locally verified JPEG hashes live in
`../../clone/fixtures/search-commerce-current-2026-07-22.json`; their binaries
live under
`../../clone/static/assets/source-current/2026-07-22/search-commerce/`.  The
clone therefore permits only an empty/default selection for these cards and
labels their evidence class as `direct-search-card`.
