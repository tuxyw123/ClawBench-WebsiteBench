# CODEX Trajectory: Amazon Dev Pilot 136

> This is a retrospective record of the original pre-fusion implementation.
> The later commerce fusion intentionally supersedes its account and checkout
> boundary decisions; current behavior and canonical addresses are documented
> in `README.md` and `../runtime-manifest.json`.

## Checkpoint

- Record date: `2026-07-17`.
- Public observation timestamp:
  `2026-07-17T07:00:41.030980+00:00` (`15:00:41.030980` in `UTC+08:00`).
- Owned documentation: `README.md`, `SOURCE_EVIDENCE.md`,
  `ASSET_ATTRIBUTION.md`, `LIMITATIONS.md`, `VERIFICATION.md`, and
  `CODEX_TRAJECTORY.md` in this clone directory.
- Canonical local port: `8153`.
- Task: dev-only task `900136`, target rank `2`, ASIN `B0874XN4D8`, quantity
  `2`.

## Staged Trajectory

### 1. Task selection and provenance

The audited V2 corpus contained no Amazon.com source task. Its only instruction
mentioning Amazon belonged to Simplify Jobs, so no V2 Amazon identity was
reused. The work was scoped as a new dev pilot for a public retail journey:
discover the External Solid State Drive Best Sellers list, open its rank-two
Samsung T7 1 TB Gray item, select two, and add it to a local cart.

### 2. Source GET-only capture

Anonymous public observation used GET requests only. Browser routing aborted
non-GET requests before transmission; no source form was submitted. Cookie and
token values were omitted, and no account, source cart, payment, address, or
user data was used or retained.

Observed task-relevant URLs:

- `https://us.amazon.com/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011`
- `https://us.amazon.com/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8`
- `https://us.amazon.com/gp/aw/d/B0874XN4D8`
- `https://www.amazon.com/gp/cart/view.html`

The redacted machine inventory is
`source-fixtures/public-source-observation.json`, SHA-256
`b103e3dc9f09e5adaa9d78c849a35e4f465cc65d250dcd1b09b8b5c32c8363d0`.
Its exact capture totals are:

- 12 page/viewport states at `1365x900` and `390x844`;
- 298 response records: 232 stylesheets, 53 images, 6 documents, 6 scripts,
  and 1 fetch;
- 140 eligible resource hashes attempted, 137 successful, and 3 retained
  failures;
- 32 deduplicated media resources, all hashed, including 16 visible
  task-related uses;
- 1 blocked source telemetry POST, 0 page errors, and 1 console error.

All 298 observed responses used Amazon-controlled hosts. Raw HTML and source
screenshots remained in caller-owned temporary storage and were not committed.

### 3. Desktop and mobile source baselines

The capture retained live navigation/protection states and separate stable GET
response renders. The stable response and screenshot hashes recorded in
`SOURCE_EVIDENCE.md` are:

| State | Viewport | HTML SHA-256 | Screenshot SHA-256 |
|---|---|---|---|
| Best Sellers | desktop | `ac87f9fd9ba39aab70039822667c59e75d6036cdc42b578988c8c0fd36803dae` | `4f81ec89aead6271226520e02132368464743972c0d423d78172ad5f67069c29` |
| Samsung T7 | desktop | `730a2b0a6c30b32cbad7c468dbf78a7c085e1ed235a2ed634556f28ff7b5721d` | `0007f36c700346ab4b20596b6eba8f0443f2b55703a2164dc53100e624512f5b` |
| Empty cart | desktop | `954dc1b19d49f30198b83a89c026120ea6b603544fd974eb3757c67e8505818e` | `4e98f3b8e458467886efe7c2feb25995705836a31bb9fd8d4cb7c7fec36cb1f2` |
| Best Sellers | mobile | `3ea91fc539851c11ba0d74ae0a8560d0c0387b1b57ae4061fecb6b35e7a924ba` | `5084da371f0d30df6277423146fc15c45c29945d7e93f4dab5bc2c767bc4c7ba` |
| Samsung T7 | mobile | `1bdc09b4b58e2346e4a2e58d921b66a6c6b928f93c151134aeacb834415e5132` | `215ee58f8c383ceab250ecc68f7d42fb30a7d4934468f783f33a245a8f464fda` |
| Empty cart | mobile | `5c166d42220d32268db9d79994d0c6af13194a3c4bd2900c65de3b232cf6a1cd` | `ee800a31aff31e0647bcbb9f5ae57b845461531c2a5fa3a2917b58b2d986bb55` |

The desktop baselines supplied the ranked product cells, three-column detail
layout, buy box, and empty-cart composition. The mobile product and cart
baselines supplied the compact header, stacked detail flow, full-width actions,
and footer. The mobile Best Sellers response supplied only the shell and tabs;
rank cards did not hydrate under the GET-only boundary.

### 4. Local asset selection

Source photos and artwork were retained only as inventory metadata. Local
runtime assets were selected as follows:

| Local file | Decision | SHA-256 |
|---|---|---|
| `static/assets/ssd-sprite.png` | Locally generated six-product sprite; no source image or logo input. | `d8b4eb038e65a69cb6c8c4f0e45e09497cec160a430caa2ed26c73997647079d` |
| `static/assets/empty-cart.png` | Original local empty-cart illustration. | `171f270e4ed12ebffe611266151cb1e16330f57ba87ec7144555e0d0d0907e93` |
| `static/vendor/lucide.min.js` | Repository-local icon distribution. | `3411692820cb8d47543f69496aa25fd603a358f4498046f41c508a5a3342210e` |

Wordmarks are styled text, fonts use a local Arial-compatible system stack,
and source CSS and JavaScript were not copied.

### 5. Frontend desktop/mobile iteration

`static/index.html`, `static/styles.css`, and `static/app.js` implement a
same-origin responsive shell, local navigation, Best Sellers list, target
product details, empty/cart states, search, loading/error states, and explicit
safety dialogs. Desktop and mobile purchase forms use the two source-observed
responsive terminal paths. The mobile Best Sellers implementation combines the
observed mobile shell with desktop-observed ranked data in a one-column layout.

The first desktop Best Sellers version was too compressed. Source/clone review
showed the source category hierarchy occupied most of the area between the
hero and product cells, and the ranked products began near the bottom of the
`1365x900` viewport. The clone was revised to restore that hierarchy, spacing,
heading position, four-column geometry, and route-specific navigation. Product
and cart states were similarly aligned to the source three-column buy-box and
empty/populated cart compositions. Mobile states retained the compact two-row
header, delivery bar, one-column rank list, stacked product flow, source-like
title truncation, and full-width actions.

### 6. Difference loop and visual gate

Source and clone states were checked at `1365x900` and `390x844`. The final
manual review covered Best Sellers, target product, empty cart, populated cart,
search results, no results, `404`, and checkout boundary at both viewports.
Review found no blank state, broken image, horizontal overflow, incoherent
overlap, or clipped task control. The remaining visible differences were
explicitly accepted: generated product/cart artwork, fixed New York/USD data,
omitted advertising/tracking/recommendations, and the mobile ranking inference.

The comparison also exposed two implementation defects before acceptance:

- Real Chromium sent `Origin: null` for the form POST while the page used a
  `no-referrer` policy. The policy was changed to
  `strict-origin-when-cross-origin` so same-origin form provenance could be
  validated without weakening server checks.
- Successful cart and boundary `fetch()` calls were followed by navigation
  before their JSON bodies were consumed, producing `net::ERR_ABORTED` in the
  verifier. The client now reads each successful JSON response before state
  bootstrap or navigation. The final run recorded zero request failures.

A separate pre-commit contract audit then invalidated the first verifier report
and forced a fresh run. It found that the authored desktop terminal path did not
match the captured form action, reversed discovery order was accepted, repeated
quantity-two completion produced another success journal row, and SQLite lock
recovery was not exercised. The desktop path was corrected to
`/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance`; ordered discovery and
exactly-one task completion are now enforced; quantities one and three retain
ordinary cart behavior without task evidence; and a real lock/release/retry
test now gates structured storage recovery. The stale report was removed before
the replacement verification run.

### 7. SQLite backend and contract hardening

`server.py` now opens the selected SQLite database with WAL, foreign keys, and
per-request connections. It stores random private sessions, time-bounded
discovery evidence, cart rows, saved-for-later rows, boundary events, and an
append-only request journal. Cart update/delete/save/move operations, search,
refresh, same-database restart, and browser-session isolation are implemented.

The two exact source-observed form terminal paths require same-origin form
encoding, the target ASIN, quantity two, a recent same-session rank-two
discovery, and a later matching product/variant view. Quantity one and three
remain ordinary cart actions but are journaled as non-task outcomes. Direct,
malformed, stale, duplicate, reversed-order, cross-session, wrong-method,
wrong-path, wrong-query, wrong-content-type, wrong-ASIN, wrong-rank, and
wrong-variant requests cannot satisfy the task.
Checkout, Buy Now, identity, delivery, account, and list actions terminate at
local no-effect boundaries and collect no sensitive data.

Additional hardening covers bounded request bodies, allowlisted bind hosts,
same-origin validation, `303` redirects, idempotent quantity replacement,
closed database handles, private `HttpOnly; SameSite=Lax` sessions, CSP, and the
absence of public reset/state-dump/fault-injection endpoints. SQLite lock/I/O
failures receive a bounded `503 storage_unavailable` response and can be retried
after storage recovers.

### 8. Final verifier, screenshot review, and cleanup

An intermediate full run was rejected during manual review because CSS smooth
scrolling had not settled before several screenshots were taken. The verifier
was changed to force and assert a top-left scroll position before every capture;
the rejected run produced no durable report.

The accepted command was:

```bash
CLAWBENCH_SCREENSHOT_REVIEW=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONWARNINGS=error::ResourceWarning python3 -B \
  materials/amazon/clone/tools/verify_task.py
```

The replacement 2026-07-17 run passed `145/145` assertions: `53/53` focused
backend, `71/71` browser, `13/13` persistence, and `8/8` lifecycle/cleanup. It observed
two same-origin task terminal requests, zero external requests, zero request
failures, zero page errors, zero unexpected HTTP errors, and zero unexpected
console errors. SQLite quick check, two same-database restarts across three
server starts, and browser-session isolation passed.

All 16 original-resolution screenshots were inspected before the exact verifier
acknowledgement was entered. Their durable hashes are:

| State | Desktop `1365x900` SHA-256 | Mobile `390x844` SHA-256 |
|---|---|---|
| Best Sellers | `e26d36dbf891413af97ea073fb9ea86434ab02c31ac076b68c758a04c1533ff5` | `be385bed76170ba2f70c28b563809dc5f947ccc8ea8c03aadea30a0fc75e6810` |
| Target product | `7646668189b6489b272f188b86a2f166b83c95dc9c2ce3dd390a9d5fd4ae24e3` | `870c56c7dc3885849926930ce9885a52d09ac510572112abf01d93ff698fea41` |
| Empty cart | `b378ae460c2d97b4a379a65cda7c07aa316550e83bc924de382bf065ace3e65a` | `4b945bc45315859f6f7e560041ab9010780b9390a4d0d54c6a0756455e306d38` |
| Populated cart | `456f62fe405918278ce4a1c72e20bfdf86332df70b15deea4d7e2fa4c1ae3116` | `9ca3a746d07d0dcf3dae4b43838df2e5b956c89907071ebd6d969e09f6768b04` |
| Search results | `d3a9fcb8da2b03767fd49bff3dba122e6f49c2c37fac0088a046fb46c8b8d142` | `8af2261d91d3670a38bff8eb7eb47d205acfa78dbd9cb23e89f519a633ad6b52` |
| No results | `b2d1e2343820141b1897301d1b1e097fb58e137fb646e86bf957d09db20f44fa` | `954eeb33ee75c1d4ce25fa5e0de373ae8b28114fa72c2caa2dce94e9267575ae` |
| `404` | `152d8c5c520cfa011625f6e5dc3785c20782a0913f98ad6148f73e385d109d54` | `851a523629101586756af6ac09e6d1f48f2ac38e25a80c221e6e1dee40e4c68f` |
| Checkout boundary | `f29b8e09992b0d60a15cd6e30f926209b43657abb94a836f75ecbde3e81f707a` | `d3366db22373949cb99134752c8073920e4a3d7157d836a0d40eb52be8fd13a9` |

The verifier then stopped its owned process and removed its canonical port,
temporary workspace, database files, log, browser workspace, and screenshot
staging directory before writing `verification-report.json`.

### 9. Website-level expansion after task-pilot review

The task-complete pilot was explicitly rejected as the final scope because its
deep semantics ended at the Samsung T7 journey. A second GET-only capture
expanded source evidence to home, Best Sellers root/leaf, search, Computers,
Deals, product, cart, account, orders, and Lists across desktop and mobile. It
recorded 24 page/viewport states, 1,428 responses, 759 media/font occurrences,
669 hashed eligible resources, and 127 blocked non-GET attempts. The homepage
returned a public `202` protection state; observed, documented, and inferred
facts remain separated in `SOURCE_EVIDENCE.md`.

The runtime was then expanded with a 12-product multi-department catalog,
generated same-origin marketplace imagery, dense home modules, department
navigation, autocomplete/history, Best Sellers root, Computers and Deals,
faceted/sorted/paginated search, generic product galleries/variants/offers/
reviews, session-local Lists and browsing history, and ordinary multi-product
cart actions. SQLite gained bounded search history, recent views, and wishlist
state. Account, orders, checkout, payment, delivery, and real identity remain
visible local boundaries.

Visual comparison exposed and corrected a misplaced desktop buy box, an
implicit CSS grid column that caused a 50-pixel generic-product overflow,
hidden mobile generic purchase buttons, and a suppressed mobile search-result
count. A five-viewport matrix covered 65 route/viewport combinations and the
original-resolution desktop/mobile screenshots were inspected. The canonical
verifier now runs separate website-level sessions through 11 route families;
ordinary list/cart actions are asserted not to create task terminal evidence.

The final integration loop also found that the new list and generic cart
clients did not consume successful JSON response bodies, so later navigation
produced `net::ERR_ABORTED` despite persisted state. The clients now consume
each response before bootstrap/navigation. Task quantity `2` after the ordered
discovery chain still uses the exact terminal form; quantities `1` and `3`, and
all ordinary catalog actions, use the non-terminal cart API.

## Source-to-Clone Decisions

| Source evidence | Clone decision | Audit boundary |
|---|---|---|
| No V2 Amazon source task exists. | Use a new dev-only task and task ID. | No upstream V2 identity is claimed. |
| Desktop Best Sellers exposed the hierarchy and ranks 1-6. | Preserve a deterministic six-item ranked list and exact rank-two target. | No live ranking or catalog refresh. |
| Mobile Best Sellers exposed heading/tabs but no hydrated cards. | Keep the shell and render desktop-observed ranks as a one-column list. | Explicit inference for task completion. |
| Desktop and mobile product responses exposed different responsive Add to cart paths. | Render and enforce both observed paths with ASIN, quantity, discovery, variant, session, redirect, and persistence semantics. | Only the local cart changes. |
| Anonymous responses varied across regional states. | Fix delivery to New York 10001 and currency to USD. | No geolocation lookup or currency switch. |
| Source photos, artwork, styles, scripts, and fonts were inventoried. | Use generated images, styled text, authored CSS/JS, system fonts, and local icons. | Copyrighted source media is not redistributed. |
| The ordinary product URL intermittently returned protection content. | Preserve protection/live states in evidence and serve a deterministic local detail. | The clone does not reproduce or bypass source protection. |
| Ads, telemetry, account, orders, Buy Now, payment, and checkout are outside the task. | Omit them or stop at same-origin no-effect dialogs. | No remote or real-world side effect. |

## Spot-Audit Index

- `tasks/clawbench/dev-136-amazon-t7-best-seller/task.json` - task identity,
  instruction, journey steps, terminal matcher, safety rubric, and reserved
  commands.
- `SOURCE_EVIDENCE.md` - public provenance, capture totals, visual observations,
  response/screenshot hashes, form semantics, and normalization boundaries.
- `ASSET_ATTRIBUTION.md` - source media policy and local asset hashes.
- `source-fixtures/public-source-observation.json` - redacted page, DOM, style,
  response, media, error, and hash inventory.
- `source-fixtures/public-site-observation.json` - broader storefront,
  department, search, deal, product, list, account, order, and cart observation.
- `source-fixtures/task-contract-observation.json` - target hierarchy, ranks,
  product semantics, responsive terminal paths, and accepted fields.
- `tools/capture_public_source.py` - GET-only capture, redaction, hashing, and
  caller-owned raw-artifact behavior.
- `server.py` - route, SQLite, session, terminal-contract, validation, CSP, and
  local boundary behavior.
- `static/index.html`, `static/styles.css`, and `static/app.js` - local runtime,
  responsive views, forms, and safety boundaries.
- `static/assets/ssd-sprite.png`, `static/assets/marketplace-sprite.png`,
  `static/assets/storefront-hero.png`, `static/assets/empty-cart.png`, and
  `static/vendor/lucide.min.js` - the complete selected local asset set.
- `tools/verify_task.py`, `VERIFICATION.md`, and `verification-report.json` -
  executable task/visual/backend verifier, reviewed evidence, and redacted
  accepted report.
- `phase3-fidelity.json`, `tools/verify_phase3.py`, and
  `tools/summarize_phase3.py` - SHA-locked frozen-source mapping, five-viewport
  differential capture, and the private Gate 3 review generator.

## 2026-07-18 Gate 2 deepening

The public runtime was migrated to FastAPI server-side rendering while the
strict request engine remained isolated on an ephemeral loopback port. The
catalog was expanded to 200 deterministic products, 10 departments, and 20
categories; all media remained independently authored and same-origin. A new
14-journey Chromium verifier covered storefront discovery, drawer and
autocomplete, search/refinement/recovery, Best Sellers, task and generic PDPs,
cart/list/history/deals, and safe external-effect boundaries. It passed 31/31
journey assertions with zero external runtime requests, request failures, or
page errors. The pre-existing task/security verifier also passed 159/159.

## 2026-07-18 Gate 3 frozen difference loop

After Gate 2 approval, the source snapshot was frozen by both snapshot ID and
report SHA-256. No Phase 3 source network access was permitted. A tracked
20-scene mapping paired the Gate 1 evidence with the corresponding clone
routes across desktop, compact desktop, tablet, mobile, and mobile-small
viewports. Each clone state was captured twice for stability, once at viewport
size and once full-page; external requests, failed requests, page errors,
semantic selectors, HTTP status, and horizontal overflow were recorded.

The first complete matrix exposed five contract failures caused by an invalid
local Home & Kitchen slug and one invalid visual comparison caused by a white
source mobile-cart frame whose DOM still contained text. The mapping was fixed
to use the real `home` department slug, and the evidence-integrity check was
strengthened to reject near-uniform source paint. Neither visual threshold was
relaxed. An earlier partial run also found that the mobile drawer action had
selected the hidden desktop button; the verifier now requires a visible
control.

Source/clone review identified the largest real frontend mismatch at
`/account`: the source showed a public anonymous **Your Account** dashboard,
while Gate 2 rendered one sign-in panel. The clone was revised to the same
12-card, three-column desktop hierarchy and responsive single-column mobile
flow, with lower preference groups. Login, address, payment, Prime, messages,
and service links remain explicit local no-effect boundaries; Orders retains a
credential-free sign-in boundary.

The accepted `r3` matrix passed 100/100 semantic checks, 100/100 two-frame
stability checks, and 24/24 eligible direct-visual thresholds. It retained 44
structural diagnostic comparisons, excluded 32 protected/error/near-uniform
source states, and recorded zero external requests, request failures, page
errors, or horizontal overflows. The owner-only, gitignored bundle contains 100
viewport screenshots, 100 full-page screenshots, heatmaps, six side-by-side
review pairs, `report.json`, and `GATE3_REVIEW.md`.

## 2026-07-18 Gate 4 BrowserUse difference loop

After explicit Gate 3 approval, exact `browser-use==0.12.6` Browser + Tools ran
five live-source/clone trajectories at desktop and mobile widths. The current
Codex session was the deterministic controller; no BrowserUse Agent or
additional LLM call was used. Source action capabilities were restricted to
navigation, link following, element inspection, page search, and waits. A CDP
Fetch guard failed all non-GET requests before transmission and was installed
on newly attached targets as well as the root session.

Early runs exposed a missing Computers & Accessories search refinement, an
incorrect checkout affordance on the empty cart, a desktop user agent at mobile
width, and duplicated clone network events emitted from root and page CDP
sessions. These were corrected without relaxing the trajectory contract. The
network observer now de-duplicates by Chromium request ID rather than session
ID and hard-fails unless the clone emits exactly one POST at the task terminal.
Manual r7 review then found gallery zoom paint covering the first few pixels of
desktop PDP copy; gallery paint is now clipped to its grid area.

The clean r8 run passed 5/5 comparisons and 45 recorded actions. It observed
1,027 continued source GETs and blocked 82 source non-GETs before send, with
zero source mutations. The clone recorded 100 unique requests, suppressed 100
duplicate CDP callbacks, made zero external requests, and emitted exactly one
valid terminal POST. The final cart contained quantity 2 at `$439.98`; the
fresh empty cart exposed no checkout control. All 15 r8 screenshots were
reviewed at original resolution, and the privacy/integrity audit had zero
failures. Explicit human approval was received on `2026-07-18`; a separate
approval record preserves the immutable report and its capture-time status.

## Git Delivery

The scoped implementation and inventory update were committed as `4263694`
(`feat(website-clone): add Amazon retail pilot`) and pushed successfully to
`origin/agent/add-website-clones` on 2026-07-17. The final verifier/report refresh
is kept in a follow-up evidence commit so this delivery record is itself covered
by the report's structural hash.
