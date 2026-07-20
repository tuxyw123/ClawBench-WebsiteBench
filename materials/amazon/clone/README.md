# Amazon Local Replica (Dev Pilot 136)

This directory contains a deterministic, loopback-only Amazon retail site
model. It covers publicly observable first-party daily-use shopping semantics;
dev task `900136` is a mandatory regression inside that broader surface, not
the implementation boundary. The audited V2 corpus has no Amazon source task,
so this pilot does not claim an upstream V2 identity.

The implementation is independently authored from anonymous, GET-only public
observations frozen at HITL Gate 1 on 2026-07-18. Its public entry is FastAPI
server-side rendering with progressive local JavaScript enhancement; a strict
loopback state engine and SQLite preserve task validation and session state.
The authored catalog contains exactly 200 products across 10 departments and
20 categories. It models the storefront, department
navigation, autocomplete, catalog search/filter/sort/pagination, Best Sellers,
category and deal discovery, generic and task product details, galleries,
variants, offers, reviews, recently viewed items, lists, a multi-product cart,
local registration and verification, sign-in, guest-cart migration, checkout,
order history, cancellation, password reset, recovery states, responsive
behavior, and local safety boundaries. Benchmark
task routes and bodies remain exact. It does not redistribute source HTML, CSS,
JavaScript, fonts, screenshots, or product photos and is not Amazon's
production service.

## Run

From the repository root:

```bash
python3 materials/amazon/clone/server.py \
  --host 127.0.0.1 --port 8153
```

These values come from the canonical
[`../runtime-manifest.json`](../runtime-manifest.json). The task adapter,
verification tools, and authenticated Viewer gateway all read that manifest;
change it instead of adding another Amazon path or port elsewhere.

The default database is
`materials/amazon/clone/amazon.sqlite3`. Use `--db PATH`
for an isolated run. Open `http://127.0.0.1:8153/`; benchmark containers use
`http://host.docker.internal:8153/`.

The clone has no preset username or password. Choose **Create account**, enter
local test credentials, and follow the one-time verification link rendered on
the page. Nothing is emailed or sent outside the process.

The verified task journey is:

1. Open `/` and follow Best Sellers.
2. Open `/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011`.
3. Open rank `#2`, the Samsung T7 1 TB Gray product.
4. Select quantity `2` and submit Add to cart.
5. Confirm quantity two and subtotal `$439.98` in the local cart.
6. Stop before checkout.

## Routes and Semantics

| Boundary | Local behavior |
|---|---|
| `/` | Dense storefront with source-shaped desktop/mobile navigation, departments, modules, rails, and recently viewed items. |
| `/Best-Sellers/zgbs` | All-departments Best Sellers root with category rails. |
| `/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011` | Six-item deterministic ranked list and task discovery route. |
| `/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8` | Desktop target product route. |
| `/gp/aw/d/B0874XN4D8` | Responsive mobile source-path variant. |
| `/<slug>/dp/<asin>` | Generic product with gallery, variants, facts, reviews, related items, sellers, list, and ordinary cart actions. |
| `/Computers-Accessories/b/` and `/gp/goldbox/` | Department and deal discovery pages. |
| `/gp/cart/view.html` | Persistent cart, quantity update, delete, save for later, and move to cart. |
| `/s?k=<query>` | Cross-catalog search with autocomplete/history, correction, facets, sorting, pagination, populated and no-results states. |
| `/hz/wishlist/ls` and `/hz/history` | Session-local list CRUD and browsing history. |
| `/register`, `/verify`, `/login`, `/logout`, `/forgot-password`, and `/reset-password` | Local account lifecycle with hashed passwords, digest-only tokens, CSRF protection, and session revocation. Verification/reset links are displayed locally; no email is sent. |
| `/account` and `/account/orders` | Signed-in local account and account-isolated order history; anonymous requests receive a sign-in surface. |
| `/checkout` and `/checkout/success/<order>` | Local test checkout, inventory-backed idempotent order creation, and confirmation. Card data is validated in memory and never persisted. |
| `/account/orders/<order>` and cancellation | Account-isolated order detail and bounded, idempotent local cancellation with inventory restoration. |
| Unknown product/page routes | Source-shaped `404` recovery presentation. |
| Buy Now, real payment/email/delivery, and external order controls | Visible same-origin local no-effect boundary; no external effect. |

State APIs provide session bootstrap, catalog search/suggestions, ordinary
multi-product cart actions, list CRUD, preferences, cart update/delete,
save-for-later/move-to-cart, and local boundary transitions. The Amazon
presentation delegates account and order ownership to `AmazonCommerceAdapter`,
the SQLite Implementation of the shared `AccountOrderCommerce` Interface also
used by the white-label runtime. SQLite stores
random private sessions, bounded search history, recently viewed products,
lists, discovery evidence, cart and saved items, account projections,
inventory, orders, boundary events, and an
append-only request journal. State survives refresh and restart with the same
database, while browser sessions remain isolated. The deterministic regional
state is en-US, USD, and New York 10001.

The task terminal contract is exactly one form-encoded POST to either:

- `/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance`
- `/cart/add-to-cart/ref=mw_dp_buy_crt`

The task-completing body contains `ASIN=B0874XN4D8&quantity=2`, and the same
session must first discover rank `#2` and then open the matching
product/variant. Quantity one or three remains a normal local cart action but
is journaled as non-task activity. Wrong method, path, query, content type,
ASIN, rank, variant, order, direct/cross-session submission, and duplicate task
completion are rejected or isolated.

Registration, checkout, and orders extend the interactive benchmark surface;
they do not alter task `900136` or silently add scoring criteria. The scored
journey still stops after confirming the quantity-two cart state.

## Verify

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  materials/amazon/clone/tools/verify_phase2.py \
  --output-dir artifacts/amazon-gate2-review

PYTHONDONTWRITEBYTECODE=1 python3 \
  materials/amazon/clone/tools/verify_phase3.py \
  --source-report materials/amazon/source-capture/report.json \
  --output-dir artifacts/amazon-gate3-review

python3 materials/amazon/clone/tools/summarize_phase3.py \
  artifacts/amazon-gate3-review/report.json

CLAWBENCH_SCREENSHOT_REVIEW=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -B \
  materials/amazon/clone/tools/verify_task.py
```

The Gate 2 verifier exercises 14 daily-use journeys in Chromium and records
private desktop/mobile review screenshots. The regression verifier owns a
temporary database, server, browser contexts, logs, and
screenshots. In review mode it pauses before acceptance and requires the exact
acknowledgement printed by the tool after all original-resolution screenshots
have been inspected. The retained 2026-07-18 pre-fusion regression run passed `159/159`
assertions, reviewed 16 desktop/mobile task-state screenshots, exercised 11
additional daily-use route families in each viewport, observed two exact
same-origin terminal requests, made zero external runtime requests, and removed
every owned runtime artifact. See `VERIFICATION.md` and
`verification-report.json`.

The Gate 3 verifier is source-offline: it accepts only the Gate 1 report whose
snapshot ID and SHA-256 are frozen in `phase3-fidelity.json`. The accepted
`2026-07-18` run captured all `20 × 5 = 100` clone states and full pages. It
passed `100/100` semantic checks, `100/100` two-frame stability checks, and
`24/24` eligible direct-visual comparisons; retained 44 structural comparisons
for diagnosis; explicitly excluded 32 protected, expected-error, or
near-uniform source states; and observed zero external requests, request
failures, page errors, and horizontal overflows. The private evidence includes
heatmaps and six source/clone review pairs.

After Gate 3 approval, Gate 4 used exact `browser-use==0.12.6` Browser + Tools
under the current Codex controller with zero additional LLM calls. Five paired
live-source/clone trajectories passed: ranked discovery and exact cart
completion, search/filter/sort, anonymous account boundary, empty cart, and
mobile ranked discovery/PDP controls. Source browsing was anonymous and GET
only; CDP blocked 82 HEAD/OPTIONS/POST requests before transmission. The clone
made zero external requests and exactly one terminal POST, reaching quantity 2
and `$439.98`. All 15 screenshots in the owner-only r8 evidence bundle were
manually reviewed. Gate 4 received explicit human approval on `2026-07-18`;
the immutable run report retains its capture-time pending state and the bundle
contains a separate `GATE4_APPROVAL.md` record.

That approval is historical after the commerce fusion. The Viewer compares
the current manifest fingerprint with the clone and Gate reports and marks all
three Gates `stale` until the four verifiers are rerun and the new Gate 4
report receives explicit approval. Retained metrics remain provenance, not a
claim that the changed runtime is already approved.

## Runtime Boundary

All runtime code and assets are same-origin and locally bundled. The public
runtime architecture is `Browser → FastAPI SSR → loopback state engine →
SQLite commerce adapter`. CSP and
server-side validation prevent external forms, frames, media, connections, and
sensitive browser capabilities. Account, address, test payment, checkout, and
orders are local only. Passwords use salted scrypt hashes; verification, reset,
and authenticated-session tokens are stored only as digests; raw card numbers
are never stored. Delivery changes, email transmission, real payments, external
order placement, and remote publication are not connected. Public source
capture sent no mutation and retained no cookie, token, account, address, or
payment value.

See `SOURCE_EVIDENCE.md`, `ASSET_ATTRIBUTION.md`, `LIMITATIONS.md`, and
`CODEX_TRAJECTORY.md` for provenance, visual decisions, allowed differences,
and the four-stage implementation audit trail.
