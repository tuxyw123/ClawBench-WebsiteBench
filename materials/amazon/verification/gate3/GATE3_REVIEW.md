# Amazon frozen-baseline fidelity — HITL Gate 3

- Captured: `2026-07-18T10:15:51.900023+00:00`
- Frozen source snapshot: `amazon-en-us-new-york-20260718T060828Z`
- Frozen source report SHA-256: `a721b3e8d1a8ddf971fde6e7dcee00e5b22eaed64aef8ad00c0c4e32cf6828e0`
- Source network policy: `frozen-evidence-only` (no live-source requests in Phase 3)
- Clone locale: `en-US` / `USD` / `New York 10001`

## Strict result

- State matrix: 100/100
- Semantic checks: 100/100
- Two-frame stable: 100/100
- Direct visual checks: 24/24
- Structural comparisons: 44
- Explicitly unavailable source comparisons: 32
- External requests / request failures / page errors: 0 / 0 / 0
- Horizontal overflows: 0

**Gate 3 automated checks pass.**

## Comparison policy

- `direct-visual`: equal-size frozen source and clone viewports are scored with SSIM, edge F1, color-histogram similarity, normalized MAE, and a declared composite.
- `structural`: image metrics and DOM-shape diagnostics are retained for review, but do not claim pixel identity when source layout, region, catalog, or access boundary differs.
- `unavailable`: HTTP 202/protected, expected-error, or near-uniform source screenshots are never scored as visual truth.
- Runtime assets are independently authored and same-origin; source media is not copied into the clone.

Declared direct thresholds: composite ≥ 0.35, SSIM ≥ 0.18, edge F1 ≥ 0.08, histogram ≥ 0.55, normalized MAE ≤ 0.5.

## Direct visual results

| State | Viewport | SSIM | Edge F1 | Histogram | NMAE | Composite |
|---|---|---:|---:|---:|---:|---:|
| `all-departments-best-sellers-live` | `desktop` | 0.6978 | 0.2387 | 0.9821 | 0.0865 | 0.7197 |
| `portable-ssd-filtered-search-live` | `desktop` | 0.8049 | 0.1938 | 0.9678 | 0.0660 | 0.7478 |
| `computers-category-live` | `desktop` | 0.6699 | 0.2378 | 0.9270 | 0.1383 | 0.6863 |
| `empty-cart-live` | `desktop` | 0.7983 | 0.2679 | 0.9874 | 0.0568 | 0.7679 |
| `account-entry-live` | `desktop` | 0.7201 | 0.3653 | 0.9932 | 0.0397 | 0.7584 |
| `all-departments-best-sellers-live` | `desktop-compact` | 0.7317 | 0.2444 | 0.9751 | 0.0749 | 0.7316 |
| `portable-ssd-filtered-search-live` | `desktop-compact` | 0.6308 | 0.3076 | 0.9637 | 0.0996 | 0.6993 |
| `computers-category-live` | `desktop-compact` | 0.6253 | 0.2501 | 0.9402 | 0.1446 | 0.6762 |
| `empty-cart-live` | `desktop-compact` | 0.7074 | 0.2613 | 0.9950 | 0.1020 | 0.7332 |
| `account-entry-live` | `desktop-compact` | 0.6215 | 0.4055 | 0.9896 | 0.0490 | 0.7255 |
| `all-departments-best-sellers-live` | `tablet` | 0.6816 | 0.1884 | 0.9764 | 0.0948 | 0.7009 |
| `portable-ssd-filtered-search-live` | `tablet` | 0.7490 | 0.1447 | 0.9448 | 0.0894 | 0.7065 |
| `computers-category-live` | `tablet` | 0.6083 | 0.2294 | 0.9511 | 0.1558 | 0.6696 |
| `empty-cart-live` | `tablet` | 0.7166 | 0.2163 | 0.9840 | 0.0845 | 0.7235 |
| `account-entry-live` | `tablet` | 0.5908 | 0.1978 | 0.9898 | 0.0584 | 0.6718 |
| `all-departments-best-sellers-live` | `mobile` | 0.5259 | 0.2607 | 0.9839 | 0.1492 | 0.6561 |
| `portable-ssd-filtered-search-live` | `mobile` | 0.4765 | 0.3451 | 0.9511 | 0.1403 | 0.6401 |
| `computers-category-live` | `mobile` | 0.5956 | 0.3229 | 0.9078 | 0.1363 | 0.6659 |
| `account-entry-live` | `mobile` | 0.4725 | 0.1744 | 0.8660 | 0.2822 | 0.5703 |
| `all-departments-best-sellers-live` | `mobile-small` | 0.5601 | 0.3422 | 0.9890 | 0.1056 | 0.6881 |
| `portable-ssd-filtered-search-live` | `mobile-small` | 0.6341 | 0.1933 | 0.9428 | 0.0846 | 0.6694 |
| `computers-category-live` | `mobile-small` | 0.6985 | 0.2963 | 0.9507 | 0.0986 | 0.7189 |
| `empty-cart-live` | `mobile-small` | 0.6243 | 0.2739 | 0.9823 | 0.1333 | 0.6974 |
| `account-entry-live` | `mobile-small` | 0.4204 | 0.2461 | 0.8543 | 0.3174 | 0.5591 |

## Structural diagnostics

- Comparable states: 44
- Diagnostic score range: 0.2289–0.6087; median 0.4354
- This score combines DOM count ratios, document-height ratio, and source heading-token recall. It is diagnostic only and has no Gate 3 pass threshold.

## Representative side-by-side review

- [all departments best sellers live desktop](review-pairs/source-clone-all-departments-best-sellers-live-desktop.jpg)
- [portable ssd filtered search live desktop compact](review-pairs/source-clone-portable-ssd-filtered-search-live-desktop-compact.jpg)
- [computers category live mobile](review-pairs/source-clone-computers-category-live-mobile.jpg)
- [empty cart live desktop](review-pairs/source-clone-empty-cart-live-desktop.jpg)
- [account entry live desktop](review-pairs/source-clone-account-entry-live-desktop.jpg)
- [samsung t7 product response render mobile](review-pairs/source-clone-samsung-t7-product-response-render-mobile.jpg)

## High-impact correction made in Phase 3

The frozen source `/account` viewport showed the anonymous **Your Account** dashboard, while the Gate 2 clone showed a single sign-in panel. The clone now renders the source-shaped 12-card dashboard and lower preference grids at all five viewports. Account, payment, address, and service mutations still stop at explicit local no-effect boundaries; no credential, address, or payment data is requested.

## Limits carried forward

- Germany/EUR in source evidence is an observed source-region artifact; deterministic clone behavior remains New York 10001/USD as approved at Gate 1.
- Independently authored product artwork and the 200-item synthetic catalog preserve shopping semantics but are not source media copies.
- Protected/blank source states cannot support visual equivalence claims.
- BrowserUse live-source/clone trajectory comparison has not run yet; it remains Phase 4 and is blocked until Gate 3 approval.

## Approval requested

Approve Gate 3 to authorize the final BrowserUse original-vs-clone trajectory phase. Without approval, no live-source BrowserUse session will run.
