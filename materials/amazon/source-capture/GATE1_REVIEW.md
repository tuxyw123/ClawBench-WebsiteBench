# Amazon source evidence — HITL Gate 1

- Snapshot: `amazon-en-us-new-york-20260718T060828Z`
- Captured: `2026-07-18T06:08:28.771923+00:00`
- Matrix: 100/100 page/viewport states
- Screenshots: 100 viewport and 100 full-page
- Network bodies: 1643/1700 stored; 17 failed; 40 unfinished at capture
- Evidence store: 746 unique objects, 136,181,727 bytes
- Blocked source writes: 51 non-GET requests

## Gate decisions

1. Requested clone baseline is `en-US/USD/New York 10001`; observed public source delivery is `Deliver to Germany (41), Deliver to Germany Deliver to Germany Deliver to (5)` and observed currency prefixes are `EUR (362), EUR20 (7), EUR206 (7), EUR240.65 (7), EUR786 (5)`. Phase 2 should normalize the deterministic clone to New York/USD while treating Germany/EUR only as source evidence.
2. HTTP 202/blank and other sparse protection states are availability boundaries, not visual targets. Rich states and the stable product response render are the implementation baselines.
3. Raw HTML, media, response bodies, and screenshots remain private and gitignored. Clone runtime assets must be independently authored.

## Quality summary

- strong: 40
- partial: 37
- protected/empty: 18
- expected error: 5

`D` desktop, `DC` desktop compact, `T` tablet, `M` mobile, `MS` mobile small.

| State | D | DC | T | M | MS |
|---|---:|---:|---:|---:|---:|
| `storefront-home-live` | protected/empty | protected/empty | protected/empty | protected/empty | protected/empty |
| `storefront-department-drawer-live` | protected/empty | protected/empty | protected/empty | protected/empty | protected/empty |
| `storefront-search-autocomplete-live` | protected/empty | protected/empty | protected/empty | protected/empty | protected/empty |
| `all-departments-best-sellers-live` | strong | strong | strong | strong | strong |
| `best-sellers-external-ssd-live` | strong | strong | strong | partial | partial |
| `portable-ssd-search-live` | partial | partial | partial | protected/empty | protected/empty |
| `portable-ssd-filtered-search-live` | partial | strong | partial | strong | partial |
| `catalog-no-results-live` | partial | protected/empty | partial | partial | partial |
| `computers-category-live` | strong | strong | strong | strong | strong |
| `electronics-category-live` | strong | strong | strong | strong | strong |
| `home-kitchen-category-live` | strong | strong | strong | strong | strong |
| `books-category-live` | partial | strong | partial | partial | partial |
| `todays-deals-live` | partial | partial | partial | partial | partial |
| `samsung-t7-product-live-boundary` | partial | partial | partial | partial | partial |
| `empty-cart-live` | strong | strong | strong | partial | partial |
| `account-entry-live` | strong | strong | strong | partial | partial |
| `orders-entry-live` | partial | partial | partial | partial | partial |
| `lists-entry-live` | strong | strong | strong | partial | partial |
| `not-found-live` | expected error | expected error | expected error | expected error | expected error |
| `samsung-t7-product-response-render` | strong | strong | strong | strong | strong |

## Representative visual review

- [Best Sellers desktop](source-all-departments-best-sellers-live-desktop.png): Strong list/ranking baseline.
- [Filtered search desktop compact](source-portable-ssd-filtered-search-live-desktop-compact.png): Strong search, filter, result-card, and sort baseline.
- [Computers mobile](source-computers-category-live-mobile.png): Strong responsive header, category, carousel, and price baseline.
- [Stable product response render mobile](source-samsung-t7-product-response-render-mobile.png): Complete product evidence; the source response retains a desktop-like dense layout.
- [Empty cart desktop](source-empty-cart-live-desktop.png): Strong anonymous empty-cart baseline.
- [Account desktop](source-account-entry-live-desktop.png): Strong anonymous account-entry baseline.
- [Storefront home desktop](source-storefront-home-live-desktop.png): Source protection boundary: HTTP 202 and blank viewport, not a clone target.
- [External SSD Best Sellers desktop](source-best-sellers-external-ssd-live-desktop.png): Rich DOM evidence but incomplete viewport paint; use DOM/full-page evidence cautiously.

## Interaction and network boundaries

- `storefront-department-drawer-live` / `desktop`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#nav-hamburger-menu, [data-action='a-dropdown-button'], button[aria-label*='menu' i]").first to be visible
- `storefront-search-autocomplete-live` / `desktop`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#twotabsearchtextbox, input[type='search']").first to be visible
- `storefront-department-drawer-live` / `desktop-compact`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#nav-hamburger-menu, [data-action='a-dropdown-button'], button[aria-label*='menu' i]").first to be visible
- `storefront-search-autocomplete-live` / `desktop-compact`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#twotabsearchtextbox, input[type='search']").first to be visible
- `storefront-department-drawer-live` / `tablet`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#nav-hamburger-menu, [data-action='a-dropdown-button'], button[aria-label*='menu' i]").first to be visible
- `storefront-search-autocomplete-live` / `tablet`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#twotabsearchtextbox, input[type='search']").first to be visible
- `storefront-department-drawer-live` / `mobile`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#nav-hamburger-menu, [data-action='a-dropdown-button'], button[aria-label*='menu' i]").first to be visible
- `storefront-search-autocomplete-live` / `mobile`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#twotabsearchtextbox, input[type='search']").first to be visible
- `storefront-department-drawer-live` / `mobile-small`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#nav-hamburger-menu, [data-action='a-dropdown-button'], button[aria-label*='menu' i]").first to be visible
- `storefront-search-autocomplete-live` / `mobile-small`: TimeoutError: Locator.wait_for: Timeout 4000ms exceeded. Call log: - waiting for locator("#twotabsearchtextbox, input[type='search']").first to be visible

- External-to-Amazon-domain responses: `1c5c1ecf7303.7a63328c.eu-central-1.token.awswaf.com` (18). These are source AWS WAF challenge infrastructure, not clone dependencies.

## Approval requested

Approve Gate 1 only if the three gate decisions above are acceptable. Phase 2 remains blocked until explicit human approval.
