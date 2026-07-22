# Search-commerce image storage backfill

This dated directory is the immutable source-side byte mirror for the 20 image
records in
`materials/amazon/clone/fixtures/search-commerce-current-2026-07-22.json`.
The original handoff retained those downloaded bytes only under the runtime
static tree. Before each file was copied here, its byte length and SHA-256 were
checked against the frozen card fixture; after the copy, source and runtime
bytes were checked again. The harness manifest repeats the same hash, MIME, and
intrinsic dimensions and forbids overwriting this dated snapshot with newer
content.

This is a storage/provenance backfill, not new source capture. It does not
upgrade the evidence beyond the two anonymous Singapore/USD search result card
observations. Each record proves only the visible card identity, image,
displayed default USD price, aggregate rating/review copy, sponsored marker,
and co-observed Add to cart control. It does not prove a complete PDP, seller,
inventory depth, delivery date, returns terms, list price, or unobserved
variant.
