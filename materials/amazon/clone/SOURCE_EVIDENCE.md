# Amazon Public Source Evidence

## Scope and provenance

This is a new ClawBench-Pro `dev` task. The audited V2 corpus contains no
Amazon.com source task; the only V2 instruction mentioning Amazon belongs to
Simplify Jobs. The local task therefore does not claim an upstream V2 identity.
The quantity-two Samsung T7 journey is now treated as a mandatory regression
inside a broader public retail-site model, not as the boundary of the modeled
website.

Anonymous public source observation was performed on 2026-07-17. Source access
was GET-only. Browser routing aborted non-GET requests before transmission. No
account, source cart, form submission, Cookie, token, payment, address, or user
data was used or retained.

Task-related sources:

- `https://us.amazon.com/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011`
- `https://us.amazon.com/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8`
- `https://us.amazon.com/gp/aw/d/B0874XN4D8`
- `https://www.amazon.com/gp/cart/view.html`

Website-level sources additionally observed by anonymous GET:

- `https://www.amazon.com/`
- `https://www.amazon.com/Best-Sellers/zgbs`
- `https://www.amazon.com/s?k=portable+ssd`
- `https://www.amazon.com/computers-pc-hardware-accessories-add-ons/b/?node=541966`
- `https://www.amazon.com/gp/goldbox/`
- `https://www.amazon.com/gp/css/homepage.html`
- `https://www.amazon.com/gp/css/order-history`
- `https://www.amazon.com/hz/wishlist/ls`

## Website-level capture

`source-fixtures/public-site-observation.json` is the broadened, redacted
machine inventory captured at `2026-07-17T13:21:22.881048+00:00`. Its SHA-256
is `d8f84d9f446de5adbd5d47ed8b794c8315d10f354f1e74159539265cc1ff5626`.
It records 24 desktop/mobile states at `1365x900` and `390x844`, 1,428 Network
responses, 759 media/font response occurrences, and 522 deduplicated media
inventory rows. Resource hashing attempted 670 Amazon-controlled public
resources: 669 succeeded and one failed without being used by the clone.

The capture includes the storefront entry, all-departments Best Sellers,
External SSD Best Sellers, a populated portable-SSD SERP, Computers &
Accessories, Today's Deals, the T7 PDP, empty cart, account entry, orders entry,
and Lists entry. It records raw response status, final public URL, final DOM,
controls, forms, selected computed styles, original-resolution screenshot
hashes, resource initiators, cache metadata, and first-party classification.
Raw HTML and screenshots remain outside git.

The browser aborted 127 non-GET source requests before transmission. The 12
page errors and 132 console errors in the report are retained evidence of
source hydration and telemetry failing under that deliberate GET-only policy;
they are not hidden, retried with mutation access, or treated as clone runtime
acceptance. Of 1,428 responses, 1,424 were Amazon-controlled. Four external
responses are inventoried but are not localized or allowed at clone runtime.

### Observed website families

- The Best Sellers root uses the same dark global navigation as the PDP, a
  Best Sellers/New Releases tab row, a long department hierarchy, and multiple
  ranked category rails. Desktop shows four dense products per visible rail;
  mobile presents horizontally clipped two-column ranked rails.
- The portable-SSD SERP exposes result count, sort, a dense refinement column,
  result media, rating, price, delivery, and Add to cart. Mobile replaces the
  desktop sidebar with quick-filter chips and horizontally scrollable controls.
- Computers & Accessories exposes a category sub-navigation row, a store
  hierarchy, a wide category heading, and product rails. Mobile keeps the same
  hierarchy and intentionally clips horizontal rails rather than converting
  them to unrelated cards.
- Today's Deals exposes category chips, a department/brand/rating/price filter
  column, discount badges, offer cards, and quick-add controls. Mobile uses a
  two-column grid and a Filters entry.
- Lists is a distinct public entry page. Desktop presents Lists & Registries,
  list benefits, and a sign-in boundary; mobile uses a Lists hero followed by
  Shopping List, Wish List, price-change, and deal-notification semantics.
- Account and Orders naturally resolve to public account/sign-in states when
  anonymous. Their private interiors were not observed or inferred as source
  facts. The storefront home returned an empty HTTP 202 protection response in
  both viewports, so no current loaded-home pixel claim is made.

Every implemented fact is classified as one of: `observed` in the two machine
reports, `documented` in public Amazon help/editorial material, or `inferred`
for a deterministic local state that cannot be produced without a source
mutation. Populated cart, local list mutations, search history, synthetic
identity, and checkout stopping behavior are explicit local inferences; they
are never described as anonymously observed source states.

### Snapshot drift

The original task snapshot remains frozen for scoring: rank `#2`, 5K+ recent
purchases, one Amazon offer, and the exact responsive quantity-two terminal
contract. The current public T7 GET has changed to rank `#3`, 8K+ recent
purchases, a larger gallery including video/360-degree affordances, Amazon and
used offers, other-seller modules, and a concurrent no-featured-offer summary.
Current-site fixtures and the frozen task fixture must therefore remain
separate; current source drift must not silently rewrite the benchmark target.

The ordinary product URL intermittently returned Amazon's public protection
boundary even with status 200. The complete desktop product HTML and the public
mobile `/gp/aw/d/` response were therefore captured separately and rendered
with JavaScript disabled. The same method produced stable Best Sellers and
empty-cart baselines while preserving the live challenge, lazy-load, and error
states as separate evidence.

## Source, DOM, styles, and Network

`source-fixtures/public-source-observation.json` contains the redacted machine
inventory. Its SHA-256 is
`b103e3dc9f09e5adaa9d78c849a35e4f465cc65d250dcd1b09b8b5c32c8363d0`.
The report records:

- 12 page/viewport states at `1365x900` and `390x844`;
- 298 response records: 232 stylesheets, 53 images, 6 documents, 6 scripts,
  and 1 fetch;
- request URL, method, status, resource type, content type, response size,
  initiator frame, cache directives, first-party classification, and hashes;
- 140 eligible resource hashes attempted, 137 successful, and 3 retained
  failures;
- 32 deduplicated media entries, all hashed, with 16 visible task-related
  uses;
- final DOM counts, headings, controls, form actions and field names, image
  dimensions, bounding boxes, and selected computed styles;
- 1 blocked source telemetry POST, 0 page errors, and 1 console error.

All 298 observed responses came from Amazon-controlled hosts. Raw HTML and raw
source screenshots stay in caller-owned temporary storage and are not
committed. Cookie and token values are omitted; source form field names remain
because they are public interaction semantics.

## Visual baseline

### Desktop Best Sellers

The source uses a 99 px dark two-row header, a teal Best Sellers hero, a narrow
category hierarchy at left, and a square-edged multi-column ranked product
grid. The task target is rank `#2`, ASIN `B0874XN4D8`, with a gray Samsung T7
image, blue title, orange stars, `4.7`, `(38,068)`, and a red price. Product
cells use thin gray dividers and orange rank ribbons rather than decorative
cards.

### Desktop product

The stable response render has a three-column product layout: thumbnail/main
gallery, dense product facts, and a bordered buy box. The source title is
“Samsung T7 Portable SSD, 1TB External Solid State Drive, Speeds Up to
1,050MB/s, USB 3.2 Gen 2, Reliable Storage for Gaming, Students, Professionals,
MU-PC1T0T/AM, Gray.” It exposes rating/review links, Amazon's Choice, recent
sales copy, specifications, About this item, quantity `1` through `3`, yellow
Add to cart, orange Buy Now, delivery, seller/returns, and Add to List.

### Mobile

Mobile uses a dark compact header, icon actions, a full-width search row and a
separate location row. The product response places branding and facts above a
large image well, then Titan Gray and 1 TB variants, price, delivery, quantity,
and a full-width buy box below the first viewport. The public mobile Best
Sellers response exposed its heading/tabs but did not hydrate rank cards under
the GET-only boundary. The clone retains this mobile shell while rendering
the same source-observed ranked data in a one-column task-completable list; this
is an explicit inference, not claimed as a pixel-observed source state.

The empty cart is a wide gray desktop canvas with the illustration and account
actions, while mobile uses stacked full-width yellow/outlined controls followed
by a two-column footer.

## Response snapshot hashes

| State | Viewport | HTML SHA-256 | Screenshot SHA-256 |
|---|---|---|---|
| Best Sellers | desktop | `ac87f9fd9ba39aab70039822667c59e75d6036cdc42b578988c8c0fd36803dae` | `4f81ec89aead6271226520e02132368464743972c0d423d78172ad5f67069c29` |
| Samsung T7 | desktop | `730a2b0a6c30b32cbad7c468dbf78a7c085e1ed235a2ed634556f28ff7b5721d` | `0007f36c700346ab4b20596b6eba8f0443f2b55703a2164dc53100e624512f5b` |
| Empty cart | desktop | `954dc1b19d49f30198b83a89c026120ea6b603544fd974eb3757c67e8505818e` | `4e98f3b8e458467886efe7c2feb25995705836a31bb9fd8d4cb7c7fec36cb1f2` |
| Best Sellers | mobile | `3ea91fc539851c11ba0d74ae0a8560d0c0387b1b57ae4061fecb6b35e7a924ba` | `5084da371f0d30df6277423146fc15c45c29945d7e93f4dab5bc2c767bc4c7ba` |
| Samsung T7 | mobile | `1bdc09b4b58e2346e4a2e58d921b66a6c6b928f93c151134aeacb834415e5132` | `215ee58f8c383ceab250ecc68f7d42fb30a7d4934468f783f33a245a8f464fda` |
| Empty cart | mobile | `5c166d42220d32268db9d79994d0c6af13194a3c4bd2900c65de3b232cf6a1cd` | `ee800a31aff31e0647bcbb9f5ae57b845461531c2a5fa3a2917b58b2d986bb55` |

## Interaction contract

The desktop Add to cart control publicly exposes form action
`/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance`; mobile exposes
`/cart/add-to-cart/ref=mw_dp_buy_crt`. Both are form-encoded POSTs carrying
ASIN and quantity. The task accepts only these responsive variants and requires
ASIN `B0874XN4D8`, quantity `2`, prior Best Sellers discovery, and the rank-two
detail view. No source POST was sent to validate the behavior after submission.

## Normalization and boundaries

Anonymous source responses varied by access path; the expanded frozen Gate 1
capture observed Germany/EUR while the requested clone baseline was New York.
The local environment uses deterministic `New York 10001`/USD state while
retaining the observed coordinates, hierarchy, controls, and source copy.
Advertisements, telemetry, personalization, recommendations unrelated to the
task, real identity, checkout, Buy Now, payment, delivery, returns, and account
effects are excluded or represented as conspicuous local no-effect boundaries.

## Frozen Gate 1 matrix and Gate 3 use

The final Gate 1 private snapshot is
`amazon-en-us-new-york-20260718T060828Z`, captured at
`2026-07-18T06:08:28.771923+00:00`. Its report SHA-256 is
`a721b3e8d1a8ddf971fde6e7dcee00e5b22eaed64aef8ad00c0c4e32cf6828e0`.
It contains 100 page/viewport captures across five viewports, 1,700 network
responses, 608 media resources, 1,643 stored response bodies, 100 viewport and
100 full-page screenshots, and 51 non-GET requests aborted before
transmission. Raw bodies and media remain private and gitignored.

Gate 3 consumed this snapshot entirely offline. The tracked
`phase3-fidelity.json` binds the exact report hash and maps 20 source states to
local routes at `1365×900`, `1024×768`, `768×1024`, `390×844`, and `320×568`.
The accepted run scored 24 direct visual states, retained 44 structural states
for diagnosis, and marked 32 protected, expected-error, or near-uniform source
states unavailable. A source screenshot must contain visual content before it
can be scored even when its DOM contains text. No Phase 3 request was sent to
Amazon.

## Gate 4 live read-only interaction evidence

After explicit Gate 3 approval, the final r8 run used exact
`browser-use==0.12.6` Browser + Tools for five paired trajectories. The current
Codex session deterministically controlled the browser; no BrowserUse Agent was
constructed and no additional LLM call was made. A CDP Fetch guard continued
only GET requests and failed every HEAD, OPTIONS, or POST request with
`BlockedByClient` before transmission. The run observed 1,109 source requests:
1,027 GETs continued and 82 non-GETs blocked. It executed no source mutation,
identity submission, cart write, or checkout action.

The source evidence retained in
`materials/amazon/verification/gate4/report.json` is derived and sanitized. Query
parameters are limited to `i`, `k`, `language`, `page`, and `s`; Amazon session
paths and long opaque segments are redacted; headers, response bodies, cookies,
credentials, and form values are not retained. The bundle is gitignored and
owner-only. Its 15 screenshots were manually reviewed. Gate 4 received
explicit human approval on `2026-07-18`; `GATE4_APPROVAL.md` records that event
without modifying the immutable run report.

The live anonymous source displayed Los Angeles 90060 and current prices. The
clone intentionally keeps the Gate 1 task fixture of New York 10001/USD and the
frozen target price. The source mobile ranking shell did not hydrate cards
under GET-only enforcement, and one desktop PDP click required a direct GET
retry; neither protection was bypassed.
