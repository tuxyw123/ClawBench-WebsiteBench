# Verification

> Historical record: the results below bind the pre-commerce-fusion runtime.
> `materials/amazon/runtime-manifest.json` now defines the runtime fingerprint,
> and the Viewer marks all retained Gates stale until new reports carry that
> fingerprint and Gate 4 receives fresh approval.

## Gate 4 BrowserUse Live Trajectory Run

- Date: `2026-07-18`
- BrowserUse: exact `0.12.6`, Browser + Tools
- Controller: current Codex session; additional LLM calls: `0`
- Paired source/clone trajectories: `5/5` passed
- Browser action traces/actions: `6/45`
- Source requests observed: `1,109`
- Source GET continued: `1,027`
- Source non-GET blocked before send: `82`
- Source non-GET transmitted / source mutations: `0/0`
- Clone unique requests / suppressed duplicate CDP events: `100/100`
- Clone external requests: `0`
- Clone POSTs / valid terminal POSTs: `1/1`
- Exact clone terminal: quantity `2`, subtotal `$439.98`
- Empty-cart checkout affordance: absent
- Original-resolution screenshots manually reviewed: `15/15`

The final owner-only, gitignored bundle is
`materials/amazon/verification/gate4/`. Its `report.json` SHA-256 is
`1619a6375d7a24d96825cd36be5168945d80527ef1185cd552fbebecb3bc62ed`;
the tracked contract SHA-256 is
`cd8ff44ec1017a62a9556f5b5da9bdc655f1f299eb710d574b458c4cd0d884a2`.
Every retained source URL is sanitized, all files are mode `0600`, and the
directory is mode `0700`. The privacy audit found no session-ID pattern,
Cookie, Set-Cookie, Authorization value, unsafe query key, long opaque path,
guard error, or screenshot hash mismatch. `GATE4_REVIEW.md` deliberately keeps
the capture-time state at `pending-human-approval`. Explicit human approval was
received on `2026-07-18` and is recorded separately in the private bundle as
`GATE4_APPROVAL.md`, preserving the approved report hash.

The difference loop added the source-observed Computers & Accessories search
refinement, removed checkout from an anonymous empty cart, used a real Android
mobile user agent, de-duplicated root/target CDP events by Chromium request ID,
and clipped gallery zoom paint to prevent it covering desktop PDP copy.

## Gate 3 Frozen-Baseline Fidelity Run

- Date: `2026-07-18`
- Frozen source snapshot: `amazon-en-us-new-york-20260718T060828Z`
- Frozen source report SHA-256:
  `a721b3e8d1a8ddf971fde6e7dcee00e5b22eaed64aef8ad00c0c4e32cf6828e0`
- Matrix: `100/100` states (`20` scenes × `5` viewports)
- Clone viewport and full-page screenshots: `100/100` each
- Semantic checks: `100/100`
- Two-frame stability: `100/100`
- Eligible direct-visual checks: `24/24`
- Structural diagnostic comparisons: `44`
- Explicitly unavailable source comparisons: `32`
- External requests, request failures, page errors, horizontal overflows:
  `0/0/0/0`

`phase3-fidelity.json` declares the mapping and thresholds before capture.
`tools/verify_phase3.py` refuses a source report whose snapshot ID or SHA-256
differs, starts the clone with an isolated temporary SQLite database, captures
the matrix in Chromium, and never opens a source URL. Direct visual states use
equal-size screenshot SSIM, edge F1, color-histogram similarity, normalized
MAE, and a weighted composite. Structural states retain DOM-shape and image
diagnostics without claiming pixel identity. HTTP 202/protected,
expected-error, and near-uniform source screenshots cannot be scored.

The accepted private bundle is
`materials/amazon/verification/gate3/` (private research evidence). Its
`GATE3_REVIEW.md` links six side-by-side source/clone views and records the
high-impact Phase 3 correction: `/account` presented the source-shaped
anonymous 12-card **Your Account** dashboard instead of a single sign-in card.
That historical boundary was later superseded by the local account/order
Adapter; real identity, payment, email, and external service actions still stop
locally.

## Gate 2 Regression Run

- Date: `2026-07-18`
- Result: `159/159` assertions passed
- Focused HTTP/backend checks: `53/53`
- Browser assertions: `85/85`
- Persistence assertions: `13/13`
- Lifecycle and cleanup assertions: `8/8`
- Desktop/mobile screenshots manually reviewed: `16/16`
- Viewports: `1365x900`, `390x844`
- Same-origin task terminal requests: `2`
- External runtime requests: `0`
- Request failures: `0`
- Page errors: `0`
- Unexpected HTTP errors: `0`
- Unexpected console errors: `0`
- Same-database restarts: `2` across `3` server starts
- SQLite quick check, restart persistence, and session isolation: passed

The durable machine report is `verification-report.json`. It contains only
structural evidence and hashes; it contains no temporary path, process ID,
browser profile, cookie, session token, SQLite row value, source response, or
original screenshot.

The separate Gate 2 browser run covers the frozen 14-journey contract in
`phase2-journeys.json`: `14/14` journeys and 31 assertions passed across the
200-product, 10-department, 20-category FastAPI SSR site, with zero external
requests, request failures, or page errors.

## Command

```bash
CLAWBENCH_SCREENSHOT_REVIEW=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONWARNINGS=error::ResourceWarning python3 -B \
  materials/amazon/clone/tools/verify_task.py
```

Review mode pauses after capture and prints a manifest containing dimensions,
byte sizes, SHA-256 values, and caller-owned paths. Acceptance requires the
exact acknowledgement printed by the verifier after all 16 original-resolution
images have been inspected. The screenshots are then deleted before the
durable report is written.

## Covered Journeys

- Independent website-level desktop/mobile sessions traverse 11 public route
  families: home, Best Sellers root, generic search, Computers, Deals, generic
  product, Lists, browsing history, account, orders, and cart.
- Generic product gallery/variants/offers/reviews, autocomplete keyboard state,
  list add/delete, recently viewed persistence, and ordinary non-terminal cart
  behavior are checked without generating task completion evidence.

- Desktop and mobile Best Sellers discovery, exact rank-two target opening,
  product option/quantity selection, exact terminal POST, and populated cart.
- Empty cart, quantity update, delete, save for later, move to cart, refresh,
  normal search, no-results search, real `404`, and local checkout boundary.
- Both source-observed terminal paths with exact method, query, content type,
  ASIN, quantity, redirect, persisted cart, request journal, and discovery
  evidence.
- Rejection/isolation of direct, malformed, wrong-path, wrong-query,
  wrong-content-type, wrong-ASIN, wrong-rank, wrong-variant, reversed-order,
  duplicate, and cross-session task terminal attempts; quantities one and
  three remain ordinary cart actions without task-completion evidence.
- Same-database restart persistence, separate browser-session isolation,
  SQLite integrity, request-size and origin controls, absence of public debug
  controls, host validation, and released canonical port.

## Visual Review

The final task-state manual review compared the clone against the captured public source
states and inspected these eight states at both viewports: Best Sellers,
target product, empty cart, populated cart, search results, no results, `404`,
and checkout boundary.

A separate website-level original-resolution matrix inspected home, Best
Sellers root/leaf, search, Computers, Deals, generic product, empty/populated
Lists, and history at `1365x900`, `1024x768`, `768x1024`, `390x844`, and
`320x568`. Its 65 route/viewport checks found no wrong status, broken image,
blank main region, or horizontal overflow. The canonical verifier retains the
desktop/mobile semantic subset of that matrix as a regression gate.

- Desktop Best Sellers preserves the source category hierarchy, large vertical
  rhythm, heading position, and four-column ranked-product geometry.
- Desktop product preserves the breadcrumb, thumbnail/media, product-content,
  option selectors, pricing, and buy-box columns at source-like scale.
- Desktop and mobile cart compositions preserve source-like item, quantity,
  subtotal, feedback, footer, and checkout-button hierarchy.
- Mobile retains the compact two-row navigation, delivery bar, one-column rank
  list, source-like title truncation, stacked product media/options, and
  task-reachable purchase controls.
- All reviewed images were nonblank and color-varied, with no broken images,
  incoherent overlaps, clipped task controls, or horizontal overflow found.

Allowed visible differences are the generated product/cart artwork,
deterministic USD/New York normalization, omitted ads/tracking and production
personalization, deterministic recommendation data, and the mobile ranked-list
inference documented in `LIMITATIONS.md`.

## Cleanup

The accepted run proved all owned resources were removed: server process,
canonical port, temporary workspace, SQLite files, server log, browser profile,
and screenshot staging directory. It did not stop unrelated observation
services.
