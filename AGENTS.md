# Repository guidance

## WebsiteBench benchmark construction standard

Before creating, extending, or modifying any WebsiteBench benchmark, runtime,
SiteDriver, Variant, evaluator, batch workflow, or Human-in-the-loop behavior,
every Agent and model **must** read and follow
`docs/benchmark-infrastructure-hitl-standard.md` and the Chinese architecture
guide `docs/websitebench-infrastructure.zh-CN.md`. Their required invariants are
normative. Do not weaken a trust boundary or claim checkpointed/resumable batch
HITL support without explicit human approval and the required acceptance tests.

## Agent skills

### Issue tracker

Work items and PRDs are tracked as GitHub issues in this repository. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix` labels. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses a single-context domain-documentation layout. See `docs/agents/domain.md`.
