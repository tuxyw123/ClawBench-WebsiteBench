# Amazon Asset Attribution

## Source inventory

`source-fixtures/public-source-observation.json` inventories every image, font,
and other media response seen during the twelve frozen task GET captures. Each entry
records its public URL, type, content type, byte size, SHA-256 when readable,
Amazon-controlled host classification, rendered dimensions/use, task relevance,
and local replacement decision.

`source-fixtures/public-site-observation.json` adds the website-level capture:
24 desktop/mobile states, 1,428 response records, and 522 deduplicated media
inventory rows spanning Best Sellers, SERP, Computers, Deals, PDP, cart,
account, orders, and Lists entry surfaces. The source files remain evidence
only and are not redistributed.

The inventory contains 32 deduplicated media resources and all 32 have hashes.
Sixteen were visible in task-related source states. The source files and raw
screenshots are evidence only and are not redistributed in this repository.

## Local asset policy

- Product photographs: replaced by locally authored or generated catalog
  renders with comparable silhouette, crop, background, and visual weight.
- Amazon and Samsung wordmarks: represented with styled text in the interface;
  no source logo bitmap is copied.
- Header, cart, location, account, search, star, chevron, menu, and list icons:
  implemented as local code-native shapes or the repository's local icon set.
- Empty-cart illustration: replaced by an original local illustration with the
  same size and informational role.
- Fonts: use the local Arial-compatible system stack observed in computed
  styles; no source font file is redistributed.
- Source CSS and JavaScript: recorded by URL/type/hash metadata where available
  but not copied. The clone implementation is authored locally from observed
  layout and behavior.
- Ads, tracking pixels, telemetry, unrelated recommendations, video streams,
  and challenge artwork: omitted from the task runtime.

The selected generated/local assets and their final SHA-256 values are listed
below. All runtime references are same-origin; the accepted verifier observed
zero external runtime requests.

## Selected local assets

| Local path | Purpose | Origin | SHA-256 |
|---|---|---|---|
| `static/assets/ssd-sprite.png` | Six source-weighted product renders in a 3x2 CSS sprite | Generated locally with the built-in image generation tool; no source image or logo input | `d8b4eb038e65a69cb6c8c4f0e45e09497cec160a430caa2ed26c73997647079d` |
| `static/assets/marketplace-sprite.png` | Twelve category-spanning product renders in a 4x3 CSS sprite | Generated locally with the built-in image generation tool; no source image, seller media, logo, or packaging input | `b08546c07c18136aefb3d0fd9d8c4a95ff35c10faed6eefe5d93e934cb5c982c` |
| `static/assets/storefront-hero.png` | Wide multi-category storefront hero with left-side copy space | Generated locally with the built-in image generation tool; no source image, logo, product trademark, text, packaging, or person input | `2293d54bfef17d799f1f8018de934a7973ae6eaf36ba70cab7e63631e15369ff` |
| `static/assets/empty-cart.png` | Original empty-cart illustration | Generated locally with the built-in image generation tool; no source image or logo input | `171f270e4ed12ebffe611266151cb1e16330f57ba87ec7144555e0d0d0907e93` |
| `static/vendor/lucide.min.js` | Local interface icons | Existing repository-local Lucide distribution reused from the Target replica | `3411692820cb8d47543f69496aa25fd603a358f4498046f41c508a5a3342210e` |

The product sprite prompt specified six compact portable SSD silhouettes,
source-like relative crops, white marketplace backgrounds, and no text, logo,
packaging, people, or watermark. The empty-cart prompt specified a centered
yellow empty cart, pale-blue circular backdrop, small trees/clouds, a white
page background, and no brand mark or text.

The marketplace sprite prompt specified an exact 4x3 contact sheet containing
a tumbler, earbuds, throw, air fryer, book, running shoe, serum, building
blocks, headphones, lamp, bottle, and backpack. Every equal cell uses a pure
white catalog background, centered object, consistent padding, and no brand,
trademark, readable text, packaging, person, or watermark. These objects are
synthetic category fixtures, not copies of the products in the source capture.

The storefront hero prompt used an airy summer living-room scene with the same
generic fixture categories, a bright natural commercial treatment, products
grouped to the right, and clean negative space for HTML copy on the left. It
contains no source seller media, brand, trademark, readable text, packaging,
person, watermark, or dark overlay. Because the anonymous home GET returned an
HTTP 202 protection state, this is an explicit authored home fixture rather
than a claim of a captured Amazon home campaign.
