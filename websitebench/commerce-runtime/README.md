# WebsiteBench commerce runtimes

This directory contains the private reference presentation and Judge for
compiled `white-label-commerce-v1` variants. Business policy and deterministic
JSON persistence live in `src/clawbench/web2code/commerce_runtime.py`; the
FastAPI file here is a thin browser Adapter.

`AccountOrderCommerce` in
`src/clawbench/web2code/commerce_contract.py` is the shared Interface for
account lifecycle and order ownership. There are currently two Implementations:

- `PersistentCommerce`: JSON-backed, policy-driven reference runtime for
  Registry variants such as Foundry, Ember, and Harbor;
- `AmazonCommerceAdapter`: SQLite-backed extension of the Amazon calibration
  clone in `materials/amazon/clone`.

Amazon is intentionally not compiled from the white-label DSL and is not a
Registry site. Its source-shaped renderer, exact task-900136 terminal request,
catalog, and evidence remain Amazon-specific. Sharing the Interface lets the
Amazon site reuse the same account/order semantics without erasing that
benchmark identity.

The canonical Amazon paths, addresses, and attestation set live only in
`materials/amazon/runtime-manifest.json`.
