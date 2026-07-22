# Clean Amazon benchmark clone

This is the replacement Amazon candidate created after the previous clone was
deleted. It is a new implementation built around the refreshed source facts
and the frozen WebsiteBench task contract; no deleted frontend/backend code was
restored. The current homepage milestone renders all 27 modules observed in the
2026-07-21 live homepage evidence, in their frozen source order, using local
resources.

The current milestone is a deterministic, visually grounded task-critical
slice. It is not yet a claim that every Amazon page or private account workflow
is reproduced 1:1.

## Run

Python 3.11+ is the only runtime dependency.

```bash
cd materials/amazon/clone
python server.py
```

For a zero-secret local SMTP setup, use:

```bash
cd materials/amazon/clone
python run_local.py
```

Add `--detach` to keep it running in the background; startup logs are written
under `runtime/`. Omit it while debugging so configuration errors remain visible.

This starts the storefront on `http://127.0.0.1:8153`, the admin listener on
`http://127.0.0.1:8154`, a catch-only SMTP listener on `127.0.0.1:18125`, and a
browser inbox on `http://127.0.0.1:8155`. Registration, password-recovery, and
order messages travel through the real SMTP client/server protocol and appear
in that inbox. The catcher binds only to loopback, never relays, and never sends
messages to the internet. Stop the foreground process to stop both services.

- Public storefront: `http://127.0.0.1:8153`
- Private benchmark admin: `http://127.0.0.1:8154`
- Default local admin header:
  `X-Bench-Admin-Token: local-amazon-bench`
- SQLite: `runtime/amazon.sqlite3`

Override the defaults with `PORT`, `AMAZON_ADMIN_PORT`, and
`AMAZON_ADMIN_TOKEN`, or use the corresponding command-line flags. Public
requests to `/__bench/*` always return 404; the admin listener also returns 404
when its token is absent or incorrect. The documented development token is
accepted only for a loopback admin bind; a non-loopback `AMAZON_ADMIN_HOST` or
`--admin-host` requires an explicitly configured token of at least 32 visible
characters.

### Optional SMTP delivery

Outbound mail is `LOCAL_ONLY` by default, so a fresh checkout never contacts a
mail server. In that mode, verification codes are available only through the
token-protected admin listener. Setting `AMAZON_CLONE_SMTP_HOST` enables one
shared asynchronous SMTP transport for registration verification, password
recovery verification, and order confirmation.

`run_local.py` supplies a complete loopback SMTP configuration and sets
`AMAZON_CLONE_REQUIRE_SMTP=1`; `SMTP_SENT` in that profile means the local
catcher accepted the message, not that an external mailbox received it. To use
a real provider, set the variables below from a process environment or secret
store. `.env.example` is a names-only template; the application intentionally
does not load credentials from a committed file.

| Environment variable | Meaning |
|---|---|
| `AMAZON_CLONE_SMTP_HOST` | SMTP host; its presence enables SMTP mode. |
| `AMAZON_CLONE_SMTP_PORT` | Optional port; defaults to 587, or 465 for implicit SSL. |
| `AMAZON_CLONE_SMTP_TLS` | `starttls` (default), `ssl`, or `none`; `none` is restricted to loopback without authentication. |
| `AMAZON_CLONE_SMTP_FROM` | Required sender mailbox; a display name is allowed. |
| `AMAZON_CLONE_SMTP_USERNAME` | Optional authentication username. |
| `AMAZON_CLONE_SMTP_PASSWORD` | Optional authentication password; must be set with the username. |
| `AMAZON_CLONE_SMTP_TIMEOUT_SECONDS` | Connection timeout from 1 through 60 seconds; default 10. |
| `AMAZON_CLONE_REQUIRE_SMTP` | Set to `1` in deployments that require real delivery; startup fails unless the complete SMTP configuration is valid. |

Partial or invalid SMTP configuration fails closed during startup. Credentials,
message bodies, raw recipients, and verification codes are not written to the
request journal or startup logs. Real external delivery requires the SMTP
variables above; `LOCAL_ONLY` is an explicit local outbox simulation and never
sends mail. Registration and the owning account's order page show the current
four-state result, offer a manual refresh while a job is pending, and offer a
bounded retry after a failure. Password recovery deliberately shows the same
neutral `QUEUED`/refresh surface for known and unknown identifiers until the
OTP proves ownership; pre-verification SMTP success, failure, and Retry are not
public. Exact recovery state remains available to the protected admin view and
to the verified flow. Public views accept no outbox id and never expose
recipients or provider error text. SMTP work is dispatched after the database
transaction so a slow mail server does not block the public response.

Delivery status has explicit meaning: `LOCAL_ONLY` has `is_simulation=true`;
`SMTP_PENDING`, `SMTP_SENT`, and `SMTP_FAILED` have `is_simulation=false`.
An authentication code is redacted from its outbox row after SMTP accepts it.
A failed attempt retains the still-valid code for up to two owner-triggered
transport retries (three total attempts); it does not mint a second usable
token. A legacy failed row whose code was already redacted is repaired by
atomically issuing one replacement and invalidating the old hash. A LOCAL_ONLY
restart converts unfinished legacy SMTP jobs back to the local outbox instead
of leaving them pending without a transport. The distinct
"Resend code" action remains rate-limited and replaces both the OTP and outbox
identity, so an older worker cannot mark a newer code as delivered. Each job is
atomically claimed, and claims plus startup replay enforce the same three-
attempt ceiling, so a crash cannot create an unbounded send loop. Duplicate
order submissions cannot send duplicate mail. On configured-SMTP startup,
eligible abandoned claims are released and pending jobs are replayed.
Registration or recovery mail whose code already expired is
instead marked `SMTP_FAILED` with the sanitized `ExpiredBeforeDelivery`
category and cannot be retried. This single-process queue therefore provides
at-least-once recovery across a crash (a crash after the SMTP server accepts a
message but before the local status commit can still produce a duplicate).

## Test

```bash
python -m unittest discover -s tests -v
```

The test suite exercises the real HTTP handler and independently exercises the
domain store. It proves:

- rank 2 is frozen to Samsung T7 / `B0874XN4D8`;
- Best Sellers → target PDP must occur in the same session and order;
- the terminal form body is exactly
  `ASIN=B0874XN4D8&quantity=2`;
- the accepted desktop POST returns 303 and creates one quantity-2 cart line;
- a duplicate POST cannot increase quantity or create a second completion;
- the first six search cards keep ASIN, canonical href, title, price, and PDP
  identity in sync even though the catalog is larger than the ranking;
- every non-T7 PDP is isolated from T7-only media, copy, and terminal forms;
- all 157 bare homepage `/dp/<ASIN>` links resolve locally without inventing
  SSD facts for books, beauty, toys, home goods, or other categories;
- the directly captured homepage PDP `B01M16WBW1` preserves its current
  source identity, Home & Kitchen taxonomy, offer, eight-image gallery, video
  affordance, overview, About copy, and geometry;
- the directly captured homepage PDP `B0BG6B2D4D` preserves its current Safari
  Ltd. Okapi identity, Toys & Games taxonomy, offer, six-image gallery, warning,
  About copy, top promotion, and geometry;
- the directly captured homepage PDP `B08HN37XC1` preserves its current SanDisk
  identity, Electronics taxonomy, offer, six-image gallery, video entry,
  style/capacity/color choices, overview, About copy, and three-column geometry;
- query-aware search matches the 157 frozen homepage products while keeping
  sparse evidence sparse, adds 20 bounded `direct-search-card` records only in
  their captured query/department scopes, and keeps the portable-SSD query's
  frozen nine-result contract before 27 homepage-evidence browse results;
- `/search/suggestions` returns at most ten catalog-derived, department-aware
  suggestions through strict server-owned parsing; the ARIA combobox supports
  keyboard and pointer selection plus Escape/outside close without claiming a
  current source autocomplete visual golden;
- search request parsing rejects ambiguous or malformed public state, while
  source-backed department/brand/price/rating/availability filters, four stable
  sorts, 16-item pagination, active-filter removal, and query-preserving links
  remain server-owned; unknown facts cannot satisfy a filter or outrank known
  price/rating values;
- department browsing exposes Books (32 cards / 7 purchasable), Home & Kitchen
  (22 / 6), Toys & Games (27 / 3), Computers (31 / 16), and Beauty & Personal
  Care (21 / 7), for 133 cards and 39 purchasable cards, without applying SSD-
  specific filters or copy outside the SSD department;
- the All directory preserves all seven frozen rail groups and all 157 unique
  products, while Today's Deals exposes 29 bounded USD offers: ten have current
  direct Deals capture and 19 retain separately labelled homepage/task evidence;
  every card has a live evidence-scoped PDP and server-owned add-to-cart quote;
- 49 ASINs resolve transactions through a strict server-owned source-quote
  allow-list: 19 prior offers, ten Deals-card defaults, and 20 current search-
  card defaults. Twelve PDPs expose option controls; values with no reachable
  quote are natively disabled, and selecting a valid disconnected quote
  minimally repairs dependent axes. Resolved options persist through cart,
  checkout, and order snapshots;
- all 191 server-known products expose a live local review destination, while
  only 13 expose source aggregate facts or an explicit source zero-review state.
  Local reviews remain separate, with one review per account/product,
  star filtering, recent/helpful sorting, identity-scoped Helpful voting, and
  server-derived Verified Purchase status from placed order items;
- normalized-email sign-in routes unknown addresses into a prefilled account
  creation flow, while existing accounts retain the password stage;
- registration stays pending until a session-bound six-digit code from the
  protected local mailbox or configured SMTP transport is verified; codes
  expire after ten minutes, allow five attempts, are replaced on resend, and
  are consumed once;
- password recovery gives known and unknown email addresses the same public
  redirect and verification page, sends only to an address loaded from an
  existing account, and binds its one-time code to the initiating session;
- authentication mail applies a 30-second cooldown and six-send, one-hour
  budgets across session and hashed-recipient scopes, plus account scope for
  recovery; a locked verification flow cannot reset its attempts by resending;
- password reset requires the verified recovery flow, rejects external return
  targets, replaces the password hash, revokes every prior authenticated
  session for the account, and rotates the recovering browser into a new one;
- authenticated session rotation, POST-only sign-out, and credential-free
  request journaling work through the local account store;
- evidence-backed offers support add, quantity update, delete, save-for-later,
  move-to-cart, anonymous isolation, and guest/account cart merge; opaque line
  IDs keep sibling selections of one ASIN independent while identical canonical
  selections merge;
- the public Lists intro uses its current source evidence and 12 verified local
  assets, while signed-in accounts own create/rename/delete list and add/remove
  item operations; Add to List retains complete option identity, browse-only
  products can be saved without becoming purchasable, Move to Cart re-quotes
  server-side, and foreign identifiers are not enumerable;
- authenticated checkout enforces address ownership and ordered
  address/delivery/payment transitions, with standard and expedited shipping
  restricted to the explicit SG/US/CA/GB/AU delivery-country allow-list;
- payment exposes three deterministic local sandbox scenarios—approved card,
  declined card, and approved bank account—without accepting or persisting PAN,
  expiry, or CVV data; a decline stays on the payment step and can be retried;
- idempotent order placement snapshots items, prices, and shipping address,
  clears only the active cart, and creates account-scoped order history/detail,
  a `PREPARING` shipment, and a local or SMTP order-confirmation record;
- a direct place-order POST atomically reconciles stale cart/payment and
  delivery-country state before snapshot creation, superseding invalid payment
  approval and returning to the required step without creating an order;
- post-order state is modeled orthogonally: the immutable placement fact stays
  `orders.status=PLACED`, while local simulated shipment, cancellation, return,
  and refund records follow guarded, idempotent transitions; no carrier, bank,
  card network, return label, or real money movement is implied;
- comparison derives 39 eligible ASINs from current purchasable offers plus retained
  search-department, ranking-family, or PDP-breadcrumb evidence; it re-quotes
  complete option selections server-side, allows sibling variants of one ASIN,
  enforces one source-backed product family and four lines, and merges durable
  account state across authentication without trusting client price/title data;
- public access to the admin surface is denied.

The final full discovery run passes `328/328` tests, including current
cart/variant, comparison migration, commerce, review, payment, mail privacy,
request-journal redaction, and shopping-entrypoint regressions. Re-run
the command above after later evidence-scoped surfaces are added.

The in-app browser QA additionally exercised the mobile terminal action
`/cart/add-to-cart/ref=mw_dp_buy_crt` at `390×844` and the desktop action
`/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance` at `1365×900`.

## Task-critical journey

1. Open
   `/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011`.
2. Follow rank `#2` to
   `/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8`.
3. Select quantity `2` and choose **Add to cart**.
4. The canonical desktop PDP submits the accepted desktop POST path and
   redirects to `/gp/cart/view.html`; the explicit `/gp/aw/d/<ASIN>` alias uses
   the accepted mobile path without changing behavior merely because a window
   was resized.

The browser session cookie, one-time flow capability, request journal, cart
row, task progress, and task completion are committed through one SQLite
transaction. Session and flow cookies are opaque; only their SHA-256 digests
are persisted.

## Admin contract

- `GET /__bench/health`
- `POST /__bench/reset`
- `POST /__bench/clock/advance` with `{"seconds": N}`
- `POST /__bench/orders/advance` with exactly
  `{"orderID": N, "targetStatus": "SHIPPED"|"DELIVERED"}`
- `POST /__bench/returns/advance` with exactly
  `{"returnID": N, "targetStatus": "RECEIVED"|"REFUNDED"}`
- `GET /__bench/state`
- `GET /__bench/journal`
- `GET /__bench/auth/registration-outbox`
- `GET /__bench/auth/password-reset-outbox`
- `GET /__bench/mail/outbox`

`GET /__bench/health` includes the credential-free transport summary and
counts for `LOCAL_ONLY`, `SMTP_PENDING`, `SMTP_SENT`, and `SMTP_FAILED`.
The two auth outboxes expose a verification code only while its message remains
`LOCAL_ONLY`; SMTP-mode views expose status and sanitized failure metadata but
not the code. The combined mail outbox masks recipients. None of these routes
exist on the public listener.

Shipment and return advancement is intentionally admin-only so tests can move
the local state machine without pretending that a real carrier or warehouse
event occurred. Transitions cannot skip or move backward; replaying the current
target is idempotent. Missing or incorrect admin tokens receive the same 404 as
an unknown route.

`reset` validates that the fixture stays inside `fixtures/`, validates the
frozen rank and terminal contract before mutation, clears all mutable browser,
account, cart, checkout, order, comparison, and journal state, reloads the
catalog and ranking, and increments the reset epoch. The candidate state schema
is the clone-specific `amazon-clone.state.v1`.

## Implemented public surface

- a 1000px minimum desktop storefront canvas with browser-native horizontal and
  vertical page scrolling in narrower windows, plus independently scrollable
  product rails;
- live global navigation and footer, including an Amazon-style All drawer with
  overlay/Escape close, focus containment and live destinations, the
  157-product All directory, populated Deals page, and meaningful shallow pages
  for the principal header destinations;
- an anonymous/authenticated **Account & Lists** flyout with hover, focus,
  ArrowDown, Escape-to-close, ARIA state, live account/list/order links, and
  POST-only sign-out;
- storefront home with the current 27-module source sequence: 20 content cards
  and seven product rails containing `25/19/26/17/26/28/16` frozen items;
  every rail item preserves its captured ASIN, title, order, local image, and
  canonical product-link intent;
- `/Best-Sellers/zgbs` and the External SSD ranking;
- `/s` and `/s/ref=nb_sb_noss` query-aware search across the 157 frozen homepage
  evidence products plus 20 scope-bounded current search cards, with a no-
  results state and without inventing prices or
  ratings for sparse records; the portable-SSD query keeps its frozen first
  nine contract results and appends 27 matching homepage-evidence browse cards
  over three 16-item pages, with GET filters/sorts, applied chips,
  Previous/page/Next navigation, and server-owned quick add for the quoted
  results only; drawer behavior is staged for a future mobile-UA surface but is
  intentionally not exposed by the current fixed 1000px desktop canvas;
- `/search/suggestions` plus a progressively enhanced ARIA combobox with
  department-aware catalog suggestions, keyboard/pointer choice, live status,
  and Escape/outside close;
- source-scoped department pages for Books (32 products), Home & Kitchen (22),
  Toys & Games (27), Computers (31), and Beauty & Personal Care (21), each with
  department-appropriate headings and filters; the five pages contain 133 card
  placements, 39 of which have server-owned purchase quotes;
- a seven-group, 157-product All directory and a populated Today's Deals page
  containing 29 bounded USD offers, with live PDP and add-to-cart
  destinations, server-side theme/department/brand/rating/price/discount/deal-
  type filters, and ten locally retained current-card AVIF assets;
- a Gift Cards flow with a local amount/design preview, simulated zero balance,
  and non-enumerating redemption attempts that store only keyed fingerprints;
- session-owned Sell listing drafts and Registry create/search/detail flows with
  strict form and same-origin validation; Prime Video intentionally stays a
  static, state-free placeholder rather than modeling non-shopping semantics;
- Samsung T7 desktop/mobile PDP with a seven-slot gallery rail, mobile swipe
  controls, color/price cards, video dialog shell, and source-aligned Buy Box;
- Samsung T9 evidence-driven desktop/mobile PDP with six official hi-res gallery
  images, `6+` and `10 VIDEOS` slots, source-backed variants/specs, and a
  source-aligned Buy Box;
- CGK Unlimited Queen sheet-set PDP `B01M16WBW1`, driven by current direct
  homepage-to-PDP evidence with eight gallery images, `3+`, `7 VIDEOS`, current
  price/delivery/social proof, Home & Kitchen taxonomy, specs, and About copy;
- Safari Ltd. Okapi figure PDP `B0BG6B2D4D`, driven by current direct
  homepage-to-PDP evidence with six gallery images, current price/delivery/
  social proof, Toys & Games taxonomy, warning, top promotion, and About copy;
- SanDisk Extreme Portable SSD PDP `B08HN37XC1`, driven by current direct
  homepage-to-PDP evidence with six gallery images, `8 VIDEOS`, current
  price/delivery/social proof, three option families, ten overview facts, and a
  source-aligned Buy Box;
- Rebecca Yarros's *Threshing Day* hardcover PDP `168281808X`, with the
  directly captured book identity, $17.49 physical offer, main image, book
  metadata, format control, and explicit zero-customer-review source state;
- Mighty Patch PDP `B074PVTPBW`, with the directly captured Beauty & Personal
  Care identity, main image, 4.6/184,921 aggregate review facts, and verified
  36-count ($12.99) and 75-count ($18.29) size offers;
- Ailun iPad screen-protector PDP `B0BJPXXM7D`, with its direct Computers &
  Accessories identity, local 1481×1500 main image, 4.6/143,321 aggregate,
  product facts, and 11 independently quoted model choices;
- Vault X trading-card binder PDP `B071V91LGC`, with its direct Toys & Games
  identity, local 1233×1500 main image, 4.8/8,730 aggregate, product facts, and
  five captured color offers with option-specific stock copy;
- upsimples picture-frame PDP `B0BQR2BQYZ`, with its direct Home & Kitchen
  identity, local 1199×1500 main image, 4.5/41,071 aggregate, 19 captured color
  offers, and a native selector for the 79 source-captured named sizes (the
  source snapshot reported 80 size entries in total);
- Instant Pot PDP `B00FLYWNYQ`, with its direct Home & Kitchen identity, local main
  image, 4.7/173,211 aggregate, and only the captured 3-quart and 6-quart quotes;
- JanSport backpack PDP `B07K74LDCH`, with its direct Computers & Accessories
  identity, local main image, 4.7/19,909 aggregate, and the 13 captured named
  color quotes rather than invented labels for the other 48 source options;
- Amazon Basics air-filter PDP `B088BZTYFP`, with its direct Home & Kitchen
  identity, local main image, 4.6/17,115 aggregate, nine captured dimensions,
  and transactions limited to `16x20x1` with Merv 8 or Merv 5;
- source-quote-backed option semantics: Samsung T7 supports the captured
  Titan Gray/1 TB and Blue/1 TB offers; SanDisk supports the captured Old
  Model/2 TB Black, Monterey and Sky Blue offers; Mighty Patch supports its two
  captured sizes; the book supports the captured Hardcover offer while Kindle
  remains a non-transactional visible format; T9 and the sheet set support only
  their captured default offers; Ailun supports 11 captured model offers,
  Vault X supports five captured color offers, upsimples supports its 19
  captured colors only while Size remains `11x14`, Instant Pot supports two
  captured sizes, JanSport supports 13 captured colors, and the air filter
  supports two complete dimension/style combinations. The quote matrix also
  owns dependencies: values that can never reach a quote are rendered with
  native disabled/ARIA-disabled state, while valid disconnected offers repair
  the fewest remaining axes in stable quote order instead of creating an
  invalid intermediate selection;
- live `/product-reviews/<ASIN>` surfaces for all 191 server-known products,
  with source aggregate summaries or explicit zero-review evidence limited to
  13 products. The deliberately separate local review repository supports create/update,
  1–5 stars, filters/sorts,
  Helpful toggling and order-derived Verified Purchase without rewriting the
  captured source aggregate;
- bare `/dp/<ASIN>` resolution for all 157 homepage rail products: 11 use
  complete current direct PDP evidence, three additional ASINs reuse matching
  task-catalog products, and the remaining 143 render
  an explicitly evidence-limited page using only the observed homepage title
  and local card image, with no invented price, rating, offer,
  category-specific specification, transaction controls, or comparison entry;
- evidence-scoped task-catalog PDPs that use each product's own identity and
  local media instead of borrowing Samsung T7 content;
- general anonymous/account cart with add, quantity update, delete,
  save-for-later, move-to-cart, and cart merge across registration or sign-in;
  opaque server-owned line identities keep distinct captured variants of one
  ASIN independent while repeated adds of the same canonical selection merge;
- a public source-grounded Lists intro plus account-owned list create, rename,
  delete and item add/remove flows. Add to List stores the complete observed
  option selection, including distinct variants of one ASIN; browse-only
  products may be saved but remain unavailable for cart, and Move to Cart
  re-quotes the stored ASIN/selection on the server before removing the owned
  list item. Missing and foreign list/item identifiers share the same 404;
- pending account registration with protected-mailbox or configured SMTP email
  verification,
  unknown-email sign-in handoff to a prefilled registration page,
  normalized-email password sign-in for existing accounts, session rotation,
  POST-only sign-out, and safe redirects for protected account, checkout, and
  order routes;
- non-enumerating password recovery with session-bound email verification,
  resend replacement, expiry and attempt limits, followed by one-time password
  reset and revocation of the account's other authenticated sessions;
- isolated Buy Now checkout that snapshots only the selected offer without
  consuming or clearing the ordinary cart and survives login/registration
  continuation;
- account-owned address entry, standard/expedited delivery selection, explicit
  approved-card, declined-card, and approved-bank sandbox payment scenarios,
  decline/retry, review, idempotent place-order, order
  confirmation/history/detail, guarded local shipment advancement and
  cancellation, delivered-order return requests, completed simulated refund
  records, and local or SMTP order-confirmation state. Delivery is intentionally
  limited to SG, US, CA, GB, and AU, and direct place-order atomically demotes
  stale cart/payment or unsupported-address state before any order is created;
- dynamic same-family comparison across 39 evidence-backed purchasable ASINs,
  including PDP-selected variants and default search-card offers, with opaque
  line-targeted removal and a four-line maximum;
- working registration verification with session-scoped four-state delivery
  feedback and bounded retry; non-enumerating password recovery keeps
  known/unknown status pages byte-identical until OTP verification and offers a
  rate-limited new-code resend; order confirmation has an owner-scoped
  status/retry loop, plus the general 404 page.

All runtime assets are local and CSP restricts images, scripts, styles,
connections, and form actions to self/data. No request from this candidate is
sent to Amazon, analytics, checkout, or payment services.

## Evidence and current limits

- Current Singapore/USD page evidence:
  `../source-current/2026-07-21/`
- P0 local resource manifest and verifier:
  `../source-assets/2026-07-21/manifest.json` and
  `../source-assets/2026-07-21/verify_manifest.py`
- Current Deals-card asset manifest and source/runtime verifier:
  `../source-assets/2026-07-22/deals-current/manifest.json` and
  `../source-assets/2026-07-22/deals-current/verify_assets.py`
- Current Lists intro evidence, 12-asset manifest, and source/runtime verifier:
  `../source-assets/2026-07-22/lists-intro/evidence.json`,
  `../source-assets/2026-07-22/lists-intro/manifest.json`, and
  `../source-assets/2026-07-22/lists-intro/verify_assets.py`
- Current search control/brand/sort/page-2 DOM observation:
  `../source-current/2026-07-22/observations.json` (bounded control evidence,
  not a full visual or asset-closure bundle)
- Current 20-card search-commerce fixture and integrity-checked runtime JPEGs:
  `fixtures/search-commerce-current-2026-07-22.json` and
  `static/assets/source-current/2026-07-22/search-commerce/`. Each record proves
  only its displayed default offer and is not a complete PDP capture.
- Historical Gate 1 capture and geometry:
  `../source-capture/`
- Browser QA is rerun from the current candidate rather than keeping stale
  derived screenshots in the repository.

Current desktop source facts are strong for the header, live home, task
ranking DOM, search, and the 14 unique rich-evidence PDPs. Three scoped resource
manifests plus the bounded search-card fixture contain 452 source records: 410
P0 files, ten Deals AVIF pairs, 12 Lists intro JPG/PNG pairs, and 20 search-card
JPEGs. Two separately evidenced runtime aliases close the nav sprite and
historical Samsung T7 task image. Thus the asset closure contains 454 required
logical pairs and `static/assets/` contains 454 physical files after two unused
legacy files were removed. The base capture subsets include 321 homepage resources,
30 `pdp-home` resources, two `pdp-computers` resources, and one each under
`pdp-kitchen`, `pdp-toys`, `pdp-books`, and `pdp-beauty`; the remaining base
entries cover the other frozen task surfaces. The Deals and Lists verifiers
also check bytes, dimensions, MIME, paths, SHA-256, and source/runtime byte
identity. Runtime resources are forbidden from loading remotely.
The homepage uses the frozen current 27-module order and a self-contained
seven-rail fixture with 157 real item-level records. Wireless Tech uses its
four captured source tiles; the obsolete second history rail, Sports rail,
travel substitute, truncated 14-item arrays, and extra sign-in module are no
longer rendered.

Desktop card and rail geometry follows the captured source coordinates,
including the 285px personalized rail and 281.5px standard rails. All referenced
main images are local. The storefront deliberately keeps a minimum 1000px desktop
canvas: a narrow browser exposes native page-level horizontal scrolling instead
of silently reflowing into a different interaction model. The sitewide
evidence-backed desktop and mobile footer directories are also present.

The Lists evidence directly records the current anonymous requested/final route
and DOM facts, and reuses the historical five-viewport captures for geometry.
Authenticated list contents and mutations remain private/inferred functional
states; their implementation is not presented as current source-direct proof.

This is still not a whole-site 1:1 acceptance claim. The 390px source evidence
is a narrow desktop-UA page with a 1000px minimum canvas, not a captured
mobile-UA homepage, so mobile-source fidelity remains a separate gate. All 157
homepage rail links are now reachable through `/dp/<ASIN>`, but reachability is
not treated as full PDP fidelity: 11 ASINs (`B01M16WBW1`, `B0BG6B2D4D`,
`B08HN37XC1`, `168281808X`, `B074PVTPBW`, `B0BJPXXM7D`, `B071V91LGC`, and
`B0BQR2BQYZ`, `B00FLYWNYQ`, `B07K74LDCH`, and `B088BZTYFP`) use complete
current direct PDP evidence, three other ASINs reuse existing task-catalog
products, and the remaining 143 deliberately expose only homepage-card
evidence. These two sets form the 14 unique current rich-evidence PDPs.
Search product main images have current direct-media provenance, while most
other products still lack source-backed full galleries, video, A+ modules,
offers, and delivery data. In particular, each of the 20 `direct-search-card`
records establishes only its captured default USD offer, image, displayed
rating/review text, department context, and Add to cart control—not variants,
seller identity, inventory depth, list price, delivery/returns terms, or a full
PDP. The 143 sparse homepage-card-only products are
therefore intentionally not purchasable or comparable. Search pagination,
server-side filters/sorts, applied-filter removal, state preservation, quoted-
result quick add, and local catalog-derived autocomplete are implemented.
Spelling correction, complete current source facets, a current autocomplete
visual golden, mobile drawer golden evidence, and the remaining PDP captures
are still incomplete.

Only complete source-quote combinations transact. The client projects the
server-owned quote matrix before committing a selection: values with no
reachable quote are disabled, while a valid value from a disconnected quote
set minimally repairs other axes. Captured color offers without a child ASIN
are represented by the base ASIN plus a canonical server selection key. The cart can therefore retain sibling selections as
separate lines, while matching ASIN/selection keys merge up to the quantity
cap; update, save, move, and delete operations target an owned opaque line ID.

All 191 server-known products have a live local review surface. Source evidence
includes aggregate review counts/ratings or an explicit zero-review state for
only 13 products, and no complete source review cards suitable for faithful
reproduction. The clone therefore renders a neutral unavailable aggregate for
the other products and does not invent source authors or excerpts; user-created
local reviews are visibly separated and never recompute the source aggregate.

The core local commerce loop is implemented, but payment remains deliberately
simulated through approved-card, declined-card, and approved-bank scenarios;
none contacts a payment processor or accepts real payment secrets. Email remains
`LOCAL_ONLY` unless an operator supplies the complete SMTP environment above;
configured SMTP performs real outbound delivery for registration, recovery,
and order confirmation but does not turn this clone into a production identity
or commerce service. Deployments that must never fall back to the local outbox
set `AMAZON_CLONE_REQUIRE_SMTP=1`. These limits keep the clone focused on
meaningful browse, account, cart, delivery, comparison, and order semantics
without claiming production Amazon services.
