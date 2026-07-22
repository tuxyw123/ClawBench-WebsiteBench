# Amazon source assets — 2026-07-21

This directory is the validated, current-direct offline asset set for the
initial Amazon clone P0 routes. `manifest.json` is the canonical inventory;
every listed file has its source route/state, exact source URL, byte length,
SHA-256 digest, decoded content type, dimensions, evidence level, and corruption
status recorded.

## A2/P0 status

| Route group | Expected | Downloaded | Missing | Corrupt |
| --- | ---: | ---: | ---: | ---: |
| `home` | 321 | 321 | 0 | 0 |
| `search` | 5 | 5 | 0 | 0 |
| `ranking` | 6 | 6 | 0 | 0 |
| `pdp-search` | 5 | 5 | 0 | 0 |
| `pdp-t9` | 9 | 9 | 0 | 0 |
| `pdp-t7` | 13 | 13 | 0 | 0 |
| `pdp-home` | 30 | 30 | 0 | 0 |
| `pdp-computers` | 2 | 2 | 0 | 0 |
| `pdp-kitchen` | 1 | 1 | 0 | 0 |
| `pdp-toys` | 1 | 1 | 0 | 0 |
| `pdp-books` | 1 | 1 | 0 | 0 |
| `pdp-beauty` | 1 | 1 | 0 | 0 |
| `auth` | 7 | 7 | 0 | 0 |
| `cart` | 8 | 8 | 0 | 0 |
| **Total** | **410** | **410** | **0** | **0** |

`remote_runtime_policy` is `forbidden`: clone pages must copy and serve these
resources locally and must not hotlink Amazon at runtime.

## Provenance

- `home/manifest.json` preserves the original browser asset-bundle record and
  now also indexes the verified full-page direct downloads. The accompanying
  `home/full-page-inventory.json` records all 139 added card and rail assets,
  including local filename, byte length, SHA-256 digest, decoded dimensions,
  and download status. The formerly failed `49eee639f994b488` fetch was
  recovered by direct download and is now represented as a validated success.
- `home/personalized-rails.json` preserves the 27-heading desktop module order,
  the one personalized rail present in that source variant, all 25 item-level
  ASIN/link/title/image and geometry records, and the explicit absence of the
  second history rail. Its companion narrow-window capture is labeled as a
  desktop-UA 1000px-minimum-canvas observation, not mobile-UA evidence. The 25
  exact item images use deterministic `home/personalized/<ASIN>.jpg` names and
  are mirrored into the clone's dated local asset tree.
- `home/wireless-tech.json` freezes the four-item Wireless Tech quad observed in
  the narrow desktop-UA layout, including its exact card geometry, labels,
  canonical search intents, CTA intent, source URLs, and 186×116 image
  dimensions. Its local images use semantic deterministic filenames under
  `home/wireless-tech/`.
- `home/top-picks-singapore.json` freezes the current 28-item Singapore rail in
  source order with item-level ASIN, canonical product path, title, image URL,
  natural dimensions, and card dimensions. The capture handoff did not expose
  an exact timestamp, so the evidence records `capturedAt: null` and only the
  known ordering after the timestamped Wireless Tech capture. Its images use
  deterministic `home/top-picks-singapore/<ASIN>.jpg` filenames.
- `home/remaining-standard-rails-capture.json` freezes five current source
  product rails and all 104 item records: Home & Kitchen, Toys, Computers &
  Accessories, Books, and Beauty & Personal Care. Their source and clone mirror
  images use deterministic `home/rails/<rail-slug>/<ASIN>.jpg` paths and retain
  each source URL, natural image dimensions, card dimensions, and item order.
- Search and ranking URLs are the live DOM `currentSrc` values used for the
  direct downloads.
- PDP gallery and support images came from the current Samsung T7 and T9 page
  asset inventories and were downloaded directly. T9 main/gallery images use
  the official high-resolution `colorImages.initial` URLs; its video thumbnail
  and Gray 2 TB variant image are separately tracked in the manifest.
- `pdp-home/B01M16WBW1/evidence.json` freezes the first directly captured PDP
  reached from a current homepage rail: the CGK Unlimited Queen sheet set under
  an anonymous Singapore/USD session. It records current identity, taxonomy,
  offer, social proof, product overview, About copy, desktop geometry, eight
  gallery images, and a video thumbnail. The nine downloaded media files are
  mirrored under the clone's dated `pdp-home/B01M16WBW1/` asset tree.
- `pdp-home/B0BG6B2D4D/evidence.json` freezes the directly captured Safari Ltd.
  Okapi figure PDP under the same anonymous Singapore/USD baseline. It records
  the observed Toys & Games taxonomy, identity, offer, social proof, delivery,
  warning, About copy, desktop geometry, six gallery images, and top promotion.
  Its seven downloaded media files are mirrored under the clone's dated
  `pdp-home/B0BG6B2D4D/` asset tree.
- `pdp-home/B08HN37XC1/evidence.json` freezes the directly captured SanDisk 2 TB
  Extreme Portable SSD PDP reached from the personalized homepage rail. It
  records the current long-form identity, Electronics taxonomy, offer, social
  proof, fulfillment, style/capacity/color choices, ten overview facts, About
  copy, and desktop three-column geometry. Its six gallery images, video
  thumbnail, brand logo, three color swatches, and top promotion are mirrored
  as 12 validated local files under `pdp-home/B08HN37XC1/`.
- `pdp-kitchen/B00FLYWNYQ/evidence.json` freezes the Instant Pot direct PDP,
  including its anonymous Singapore/USD identity, aggregate rating, fulfillment,
  product facts, and the two named sizes with captured quotes. The source
  reported one additional size without exposing its label, so the evidence does
  not invent it.
- `pdp-computers/B07K74LDCH/evidence.json` freezes the JanSport backpack direct
  PDP and 13 named color quotes. The source reported 61 colors, but the other 48
  labels were not captured and remain deliberately unrepresented.
- `pdp-home/B088BZTYFP/evidence.json` freezes the Amazon Basics air-filter direct
  PDP, its nine named dimensions, three MERV styles, aggregate rating, and one
  local main image. Only `16x20x1` with Merv 8 or Merv 5 has a captured
  transaction quote; no other Cartesian combination is inferred.
- Cart assets came from the current empty-cart-after-PDP state and were
  downloaded directly.
- Auth assets came from the current anonymous retail sign-in, registration, and
  password-recovery page families across desktop/mobile and US/Singapore
  evidence. The canonical set preserves both retail sprite densities, the
  Amazon Ember regular/medium/bold/italic faces, and the earlier logo sprite.

The scope is intentionally bounded to the current P0 visual routes. It is not a
claim that every resource used across all Amazon pages has been captured.

## Verification

Run from the repository root:

```powershell
python materials\amazon\source-assets\2026-07-21\verify_manifest.py
```

The verifier is read-only. It checks path safety and uniqueness, file presence,
bytes, SHA-256, MIME/format signatures, Pillow-decoded raster dimensions, SVG
XML/viewBox metadata, home provenance mapping, item-level evidence order and
geometry for personalized, Wireless Tech, Singapore, and the five standard
product rails, deterministic local paths, clone-mirror byte identity,
unmanifested resource files, group totals, missing/corrupt lists, and the
no-remote-runtime policy.
