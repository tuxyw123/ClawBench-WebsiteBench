# Amazon source evidence and rebuild context

This directory contains the evidence and the new clean-room Amazon benchmark
rebuild. The previous authored clone and its Gate 2–4 derived verification
artifacts were removed on 2026-07-20 after a review found that passing the old
checks did not imply close visual fidelity. The replacement in `clone/` was
then created from an empty directory; it does not restore the deleted code.

## Retained contents

- `REBUILD_CONTEXT.md`: compressed history of the previous attempt, why its
  gates produced a false sense of fidelity, and constraints for the next build.
- `clone/`: new runnable single-process frontend/backend, SQLite state,
  task-frozen fixture, private admin/reset surface, tests, and browser QA
  captures. This is an active candidate, not a completed 1:1 claim.
- `source-current/2026-07-20/`, `source-current/2026-07-21/`, and
  `source-current/2026-07-22/`: refreshed anonymous Singapore/USD page and
  bounded search-card evidence used by the clean rebuild. The 2026-07-22
  search-commerce fixture retains 20 current cards and their locally verified
  JPEGs without treating those cards as complete PDP captures.
- `source-assets/2026-07-21/`: the current direct-download resource set,
  provenance manifest, and integrity verifier. Runtime clone copies remain
  local and are checked against this manifest.
- `source-assets/2026-07-22/deals-current/`: immutable copies of the ten
  current Deals-card AVIF files, their dimensions/bytes/MIME/SHA-256 manifest,
  and a verifier that compares the source and runtime copies byte for byte.
- `source-assets/2026-07-22/lists-intro/`: the anonymous Lists intro evidence,
  12-image provenance manifest, and verifier for its source/runtime copies.
- `source-capture/`: final Gate 1 anonymous, public, GET-only snapshot. It
  contains screenshots, page/DOM records, sanitized metadata, and
  content-addressed public response objects.
- `source-capture/observations/`: smaller source and task-contract inventories
  migrated out of the removed clone.
- `source-capture/tools/`: GET-only capture and source-summary utilities.
- `source-capture/tests/`: tests for capture privacy, redaction, consistency,
  resumability, and source-summary behavior.
- `../../tasks/clawbench/dev-136-amazon-t7-best-seller/task.json`: preserved
  task semantics and terminal request contract used by the replacement
  implementation.

## Current clean rebuild milestone

The bounded source resource sets contain 452 records with no missing or
corrupt entries: the 2026-07-21 P0 manifest retains 410 files, the separate
2026-07-22 Deals manifest verifies ten additional AVIF source/runtime pairs,
the Lists intro manifest verifies 12 additional JPG/PNG pairs, and the bounded
search-commerce fixture verifies 20 current JPEGs by bytes, dimensions, MIME,
path, and SHA-256. Two separately evidenced runtime mappings cover the nav
sprite alias and historical Samsung T7 task image. The frozen closure therefore
has 454 required logical source/runtime pairs. Two unused legacy files were
removed, so `static/assets/` also contains 454 physical files. These are three
distinct denominators: 452 bounded source records, 454 required logical
mappings, and 454 runtime files. The Lists
`evidence.json` binds the anonymous `/hz/wishlist/ls` request to the observed
`/hz/wishlist/intro` final route, DOM facts, manifest, and verifier. Of the base
set, 321 remain assigned to the homepage, while the direct-
PDP subsets include 30 `pdp-home` files, two `pdp-computers` files, and one
`pdp-kitchen`, `pdp-toys`, `pdp-books`, and `pdp-beauty` file. The homepage
itself keeps the frozen 27-module sequence, 20
cards, and seven rails containing 157 unique item records.

Every bare homepage `/dp/<ASIN>` route now resolves locally. Eleven ASINs use
complete current direct PDP evidence and four homepage ASINs match products in
the existing task catalog; because SanDisk appears in both sets, this yields
14 unique rich-evidence PDPs and 143 deliberately sparse PDPs. Sparse pages
render only their observed homepage title and local card image; they do not
synthesize prices, ratings, offers, category-specific specifications, or
transaction behavior. The rich set now includes Samsung T7, Samsung T9,
Crucial X9, Sheets, Okapi, SanDisk, Books `168281808X`, and Beauty
`B074PVTPBW`, plus the Ailun iPad protector, Vault X card binder, and
upsimples picture frame, Instant Pot `B00FLYWNYQ`, JanSport backpack
`B07K74LDCH`, and Amazon Basics air filter `B088BZTYFP`.

Search now performs query-aware matching across the 157 frozen homepage
evidence products and exposes 20 additional bounded `direct-search-card`
records where their captured query or department applies. The portable-SSD query retains its frozen nine-result
contract at the head of a 36-result set and adds 27 matching homepage-evidence
browse cards across three server-rendered 16-item pages. Search state is strict,
copyable, and composable: department, repeated-brand OR, price, 4+ rating,
explicit in-stock availability, four sort modes, applied-filter removal, and
Previous/page/Next links all preserve the active query. Missing evidence never
passes a price/rating/availability filter and sorts after known values. Five
source-rail departments are also first-class destinations after evidence-
scoped commerce supplements: Books (32 cards / 7 purchasable), Home & Kitchen
(22 / 6), Toys & Games (27 / 3), Computers & Accessories (31 / 16), and
Beauty & Personal Care (21 / 7), for 133 department cards and 39 purchasable
cards in total. Sparse records do not receive invented prices, ratings, or
cart controls. The live All directory exposes all seven
groups and 157 records. Today's Deals exposes 29 bounded USD offers: ten
default cards have current direct Deals capture from 2026-07-22, while the
other 19 strict offers retain separately labelled homepage/task evidence
instead of being upgraded to current Deals evidence. The ten current cards use
local AVIF assets; the page provides server-side copyable filters,
evidence-scoped product pages, and quick add. Ordinary commerce eligibility is
independent from this Deals evidence allow-list, so future verified offers will not be mislabeled as
deals. The primary header destinations resolve to populated or
meaningful local pages rather than dead placeholders.

This remains an active, evidence-scoped commerce milestone rather than a
whole-site 1:1 claim. The core local journey now includes unknown-email sign-in
handoff to prefilled registration, session-bound email verification, complete
email-code password recovery, one-time/expiring reset codes, password reset
with existing-session revocation, two-stage password sign-in, POST-only
sign-out, authenticated session rotation, guest cart merge, general cart
editing and save-for-later, an isolated Buy Now checkout, account-owned
addresses, standard/expedited delivery, three explicit local sandbox payment
scenarios (approved card, declined card, and approved bank account),
idempotent order placement, order history/detail, guarded local shipment/
cancellation, delivered-order return requests, and simulated refund records.
Registration, password recovery, and order confirmation share a configurable
SMTP transport with explicit `LOCAL_ONLY`, `SMTP_PENDING`, `SMTP_SENT`, and
`SMTP_FAILED` states. Registration-flow and order-owner pages can refresh status
and retry a bounded failed delivery without exposing an outbox identifier or
provider error. Password recovery instead keeps known and unknown identifiers
on the same neutral `QUEUED`/refresh surface until the OTP proves ownership; it
does not expose SMTP success, failure, or Retry before verification. Without
SMTP credentials messages remain safely visible only through the token-protected
`LOCAL_ONLY` admin mailbox; deployments that require external delivery set
`AMAZON_CLONE_REQUIRE_SMTP=1` and fail startup unless the SMTP configuration is
complete. The header also has an anonymous/authenticated
Account & Lists hover/focus menu and an Amazon-style All drawer with focus
containment, Escape/overlay close, and live local destinations. Gift Cards now
supports a local amount/design preview plus non-enumerating simulated balance
and redemption; Sell and Registry provide strict session-owned draft/search/
detail flows. Prime Video intentionally remains a static placeholder because it
is outside the requested shopping semantics. Product comparison dynamically
derives 39 eligible ASINs from current offer and taxonomy evidence, restricts a
set to one source-backed product family, preserves complete variant identity,
and allows up to four opaque comparison lines. Sparse homepage products stay
non-purchasable and non-comparable rather than receiving invented commerce data.

Lists now pair the public source-grounded intro with account-owned list and item
CRUD. Add to List preserves the complete observed variant selection, while
browse-only products may be saved but remain explicitly unavailable to cart.
Move to Cart accepts no client price, re-quotes the stored ASIN/selection on the
server, and removes the owned item only after cart insertion succeeds. Missing
and foreign list/item identifiers share the same non-enumerating result.

Checkout treats direct place-order submission as a final atomic reconciliation
boundary. If the cart changes after simulated payment approval, the stale
payment is superseded and checkout returns to payment selection without
creating an order. A deterministic sandbox decline also remains on the payment
step, creates no order or money movement, and can be retried with another
sandbox method. Delivery is limited to Singapore, the United States, Canada,
the United Kingdom, and Australia; unsupported or subsequently corrupted
addresses are rejected at creation/selection, delivery, and final placement,
with checkout safely returned to address selection.

Forty-nine ASINs now have a strict, server-owned complete-combination quote
allow-list: the prior 19 offers, ten Deals-card defaults, and 20 current
`direct-search-card` defaults. Twelve PDPs expose source-backed product-choice
controls. Each search card establishes only its displayed empty/default
selection and does not establish a full PDP, variant matrix, seller, inventory
depth, delivery promise, returns terms, or list price. The ten new Deals-card
offers likewise omit uncaptured ratings, reviews, delivery, inventory,
departments, themes, and option matrices. Captured combinations update price,
image and availability and persist through cart, checkout and order snapshots.
Option dependencies come from the server-owned quote matrix: a value with no
reachable quote is natively disabled, while choosing a valid value in a
disconnected quote set minimally repairs the remaining axes instead of first
creating an invalid combination. The Instant Pot has two quoted
sizes, the JanSport page exposes only the 13 captured colors, and the air filter
allows transactions only for the captured `16x20x1` Merv 8/Merv 5 combinations.
All 191 server-known products have a live `/product-reviews/<ASIN>` surface for
local account-authored reviews. Only 13 products expose captured source
aggregate facts or an explicit zero-review state; all others render a neutral
source-aggregate-unavailable state rather than inventing ratings or excerpts.
Local reviews support filtering, sorting and Helpful votes, while Verified
Purchase is derived only from placed, non-cancelled order items. Books
accurately reports zero source reviews; Beauty preserves its captured aggregate
without copying individual source review excerpts.

Most homepage PDPs still need direct capture. Catalog-derived, department-aware
autocomplete is implemented through `/search/suggestions`, including ARIA
combobox state, keyboard/mouse selection, and outside close, but it lacks a
current source visual golden and is not claimed as direct source fidelity.
Search still lacks spelling correction, complete source-facet coverage, and
current mobile filter-drawer evidence. No external payment
processor is connected; SMTP delivery is real only when an operator supplies a
valid provider configuration. The storefront keeps a 1000px minimum desktop
canvas so narrow windows use browser-native horizontal scrolling rather than
changing the interaction model. Cart lines use opaque server-owned `line_id`
values plus canonical `selection_key` identity: sibling variants of one ASIN
remain independent, while repeated adds of the same selection merge. Source
review cards were not captured completely, so the clone does not invent source
authors or excerpts.

The final full discovery run passes `330/330` tests. This includes the current
cart/variant, comparison migration, commerce, review, payment, mail privacy,
request-journal redaction, and shopping-entrypoint regressions.
The base verifier passes `410/410`, the Deals verifier passes `10/10`, the Lists
verifier passes `12/12`, and the search-commerce loader verifies 20 card assets,
for 452 bounded source records plus two separately evidenced runtime mappings:
454 required logical pairs and 454 physical files under runtime `static/assets/`.

## Privacy and redistribution

The capture report records `cookiesHeadersAndTokensOmitted=true`, anonymous
public GET access, and blocking of non-GET source mutations. Repository audit
finds no structured Cookie, Authorization, password, or API-key fields.

The Gate 1 snapshot is historical (`2026-07-18`) and contains regional drift,
protection pages, source-site markup, screenshots, and media. Treat it as
private research evidence rather than current or complete golden truth. A new
clone should refresh the source baseline where permitted and must keep
observed, protected/unavailable, and inferred states separate. The new clone
freezes Singapore/USD for deterministic testing and records places where only
historical or inferred mobile evidence is available.

The source materials remain the property of their respective owners. Review
licensing and site terms before changing repository visibility, reusing media,
or redistributing the evidence.
