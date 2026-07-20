# Amazon source-to-clone research material

This directory preserves the evidence that preceded the synthetic Northstar
Market benchmark. It is useful for studying source observation, clone fidelity,
browser trajectories, and task-contract verification, but it is not mounted
into WebsiteBench Agent or Candidate containers.

## Contents

- `source-capture/`: final Gate 1 anonymous, public, GET-only capture. It
  contains screenshots, accessibility/DOM records, sanitized URL metadata, and
  content-addressed public response objects.
- `clone/`: independently authored offline Amazon-shaped local replica and its
  verification tools. Runtime assets are local/generated; see
  `clone/ASSET_ATTRIBUTION.md`.
- `runtime-manifest.json`: canonical Amazon task path, clone entrypoint,
  addresses, Viewer gateway path, and runtime-attestation file set.
- `verification/gate2/`: browser regression review evidence.
- `verification/gate3/`: final source-offline fidelity matrix, screenshots, and
  heatmaps.
- `verification/gate4/`: historically approved BrowserUse source/clone
  trajectory evidence; the commerce fusion requires a fresh Gate sequence.
- `../../tasks/clawbench/dev-136-amazon-t7-best-seller/`: associated ClawBench
  development task.

Only the final useful iteration of each gate is retained. Earlier duplicate
Gate 1, Gate 3, and Gate 4 runs and the unrelated ClawBench V2 corpus were
intentionally excluded.

## Privacy and redistribution

The capture report records `cookiesHeadersAndTokensOmitted=true`, anonymous
public GET access, and blocking of non-GET source mutations. Repository audit
finds no structured Cookie, Authorization, password, or API-key fields.

The raw capture nevertheless contains source-site screenshots, markup, and
public media. Those materials remain the property of their respective owners
and are retained for private research/reproducibility. Review licensing and
site terms before changing repository visibility or redistributing them.

The authored clone itself contains attribution and a zero-external-request
runtime policy. It combines the Amazon presentation/task engine with the
shared commerce account/order Interface through a SQLite Adapter. Start the
single canonical runtime from the repository root with:

```bash
python materials/amazon/clone/server.py --host 127.0.0.1 --port 8153
```

Then open `http://127.0.0.1:8153/`. In an authenticated internal Viewer, the
same runtime is exposed at `/clone/benchmark--amazon/`. Both addresses and the
command are declared in `runtime-manifest.json`.
