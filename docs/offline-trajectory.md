# Human + Agent offline trajectory export

`clawbench-trajectory` converts clone-building evidence into a portable,
schema-valid bundle for offline analysis. It does not contact a reference site,
model API, or external storage while exporting.

## Evidence classes

The manifest keeps two evidence classes separate:

| Source | Manifest classification | Meaning |
| --- | --- | --- |
| Completed Web2Code run | `live` / `web2code-run` | Normalized recorded Agent, browser, human, build, candidate, and result streams |
| Checked-in `CODEX_TRAJECTORY.md` | `retrospective` / `clone-history` | Curated reconstruction from retained narrative and artifacts; not a raw conversation |

This distinction is mandatory. Historical Markdown must not be relabeled as
turn-level model output, even when it describes genuine human/Agent work.

## Bundle layout

```text
<bundle>/
  manifest.json                 # provenance, capture class, counts, exclusions
  events.jsonl                  # websitebench.trajectory-event.v1 records
  SHA256SUMS                    # every declared event/artifact payload
  files/
    task/                       # task and public contracts
    trajectory/                 # retained historical narrative, when present
    context/                    # provenance and verification records
    browser/screenshots/        # recorded browser evidence, when present
    builds/                     # sanitized build logs/metadata
    candidate/                  # final candidate source snapshot
    evaluation/                 # public result and failure report only
```

Events preserve actor (`human`, `agent`, `human-agent`, `tool`, `system`, or
`evaluator`), phase, kind, source stream, per-stream sequence, payload, and
artifact references. Cross-stream order is not invented when the source did
not record timestamps.

## Export the retained Amazon clone history

From the repository root:

```bash
clawbench-trajectory export-clone materials/amazon/clone \
  --task tasks/clawbench/dev-136-amazon-t7-best-seller/task.json \
  --repo-root . \
  --out artifacts/offline-trajectories/amazon-dev-136 \
  --archive

clawbench-trajectory validate \
  artifacts/offline-trajectories/amazon-dev-136
```

The default export includes the final clone source, tests, tools, generated
runtime assets, task, trajectory narrative, and verification records. It
excludes source-observation fixtures by default because their redistribution
must be reviewed independently. Add `--include-observations` only after that
review. Use `--without-code` for a provenance-only bundle.

## Export a future live Web2Code run

```bash
clawbench-trajectory export-run web2code-output/<run-id> \
  --repo-root . \
  --out artifacts/offline-trajectories/<run-id> \
  --archive
```

The live adapter consumes these existing streams when present:

- `agent/agent-messages.jsonl`;
- `browser/actions.jsonl` and browser screenshots;
- `human-interventions.jsonl`;
- sanitized build artifacts;
- the final Candidate source snapshot;
- `eval/evaluation-result.json` and `failure-report.md`.

An infrastructure-only preparation run remains a valid `partial` bundle, but
the manifest lists every missing or empty stream. It cannot be mistaken for a
successful Agent trajectory.

## Safety boundary

The exporter rejects or excludes:

- `secrets.env`, `.env`, private keys, credentials, and credential-shaped text;
- private reference source and hidden Judge fixtures;
- `eval/facts.json` and private evaluator internals;
- runtime databases, caches, symlinks, and container image archives;
- undeclared files that are not covered by the manifest and checksums;
- machine-specific absolute source roots.

The output directory and optional archive must not overlap an input source
tree, and overwrite mode refuses symbolic links and non-regular targets.

Structured secret fields and common API-key/Bearer formats are redacted before
hashing. `validate` then rechecks both schemas, event references, file sizes,
SHA-256 values, forbidden paths, symlinks, and residual credential patterns.
Because redaction happens before hashing, exported text artifacts are safe
offline snapshots rather than byte-identical backups of credential-bearing
inputs.
