# WebsiteBench shared contracts

This directory now contains cross-site schemas used by the offline-clone
harness, portable corpus inventories, and corpus Viewer. Site implementations
and their source evidence live outside this directory (for example, under
`materials/amazon/`).

The schemas keep purpose, resource closure, route/state coverage, semantic
invariants, visual checkpoints, acceptance evidence, result reports, and human
review records machine-readable. They do not define a particular storefront or
reference implementation.

`corpora/claw-bench-v2/live-site-inventory.json` is the self-contained input for
the live-site expansion prompt. It preserves task instructions and terminal
request contracts, but intentionally omits referenced personal-data/file
contents.
