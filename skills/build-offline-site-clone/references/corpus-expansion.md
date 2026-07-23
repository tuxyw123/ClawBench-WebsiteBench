# Inventory-driven corpus expansion

Use this reference when a request names many live sites or begins with an
upstream task inventory.

When this repository already contains a frozen inventory under
`websitebench/corpora/`, use that versioned artifact. A sibling source checkout
may be used for an explicit provenance audit, but it must not be an undeclared
runtime dependency of the prompt, clone, verifier, or release bundle.

## Normalize the unit of work

Build three separate ledgers:

1. task ledger: stable task ID, instruction family, terminal semantics, and
   source evidence;
2. platform ledger: normalized platform key, first-party origins, actors,
   shared purpose, and task IDs;
3. clone ledger: adapter path, lifecycle stage, current evidence, blockers, and
   release status.

One task is not automatically one clone. Multiple tasks that share the same
product, identity model, navigation shell, state store, and reset boundary
normally share one site adapter. Multiple unrelated products owned by one
company are not automatically one clone. Hostname equality is evidence, not the
only grouping rule.

Preserve all first-party origins used by the grouped platform. Initialize them
with repeated source arguments:

```powershell
clawbench-offline-clone init `
  --site-dir materials/<site-id> `
  --site-id <site-id> `
  --display-name "<display name>" `
  --source-url https://primary.example/ `
  --source-url https://checkout.example/
```

The origins authorize source analysis only. The accepted runtime remains fully
offline.

## Freeze a platform requirement card

Before capture, write a requirement card containing:

- source inventory provenance and count warnings;
- platform purpose and actors;
- all mapped task IDs and the capability each contributes;
- P0/P1 success, validation, duplicate, stale, unauthorized, foreign-scope,
  retry, reversal, and aftercare journeys where applicable;
- route/state/viewport/checkpoint matrix;
- backend invariants, reset seed, and local external-service adapters;
- explicit non-goals and evidence states;
- copyright, ownership, and redistribution boundary.

Do not copy Amazon-specific entities into another platform. Reuse the lifecycle,
evidence model, resource closure, frontend-first order, server-authoritative
invariants, deterministic reset, and acceptance evidence classes. Derive site
entities and denominators from the new platform's purpose.

## Work in bounded batches

Choose a batch whose platforms add meaningfully different journey and semantic
coverage. For each platform, complete or truthfully stop at:

```text
INIT -> SOURCE_CAPTURED -> ASSETS_CLOSED -> FRONTEND_READY
     -> BACKEND_READY -> ACCEPTED
```

Do not run all platforms through source capture and postpone every verifier.
Finish one small vertical batch, audit the method, then expand. Within a batch,
keep each site's state and evidence independent.

## Corpus-level checks

After each batch:

- reconcile task, platform, host, and clone counts;
- verify every task maps to exactly one platform adapter;
- verify every adapter maps back to at least one task;
- retain missing/duplicate inventory warnings;
- run every current per-site lifecycle check;
- run Harbor corpus validation for authored instances;
- report direct, structural-only, unavailable, and inferred evidence
  separately;
- keep release gates pending until real NOP/oracle, isolation, and human-review
  evidence exists.

An old branch's unit tests, a generated `verification.json`, or a
`verified_platform_count` field cannot substitute for current harness evidence.
