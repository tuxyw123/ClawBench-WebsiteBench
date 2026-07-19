# Northstar Market benchmark

Northstar Market is ClawBench's first Web2Code2Web corpus item: a private,
synthetic white-label commerce reference and a controlled environment in which
an Agent must reconstruct a candidate from browser-visible behavior and public
contracts only.

It is independent of the earlier Amazon exploration clone. It contains no
Amazon source, media, product data, trademarks, or runtime dependency.

## What is implemented

- a persistent reference shop with registration, local mailbox verification,
  login/logout, forgot/reset password, guest/account cart merge, checkout,
  orders, atomic inventory, cancellation, and controlled time;
- deterministic public seeds `1101` and `1102`, hidden variants `9101` through
  `9105`, and the stock-one concurrency seed `9199`;
- public PRD, manifest, candidate contract, schemas, smoke cases, visual
  checkpoints, scoring rules, Dockerfiles, Compose topology, seed/reset scripts,
  and public/private health endpoints;
- an isolated Codex Agent, controlled BrowserUse gateway, rootless remote build
  service, final no-egress candidate sandbox, private evaluator, and HITL audit
  channel;
- weighted `20/20/40/15/5` scoring and schema-valid JSON plus Markdown failure
  reports.

The Agent can only observe the running reference and mailbox through the
BrowserUse gateway. The private reference, hidden fixtures, evaluator source,
browser profile, raw HTTP data, per-run secrets, and host container socket are
not mounted into the Agent. Its model network is internal and reaches only the
configured model API through an exact host/port allowlist proxy; it has no
general internet route.

## Validate and prepare a run

From the repository root:

```bash
PYTHONPATH=src python -m clawbench.web2code.run validate \
  --site northstar-market

PYTHONPATH=src python -m clawbench.web2code.run pilot \
  --site northstar-market \
  --track core \
  --model gpt-5.6-sol \
  --thinking-level xhigh \
  --dry-run
```

The dry run validates contracts and network topology, creates a private run
directory below `web2code-output/`, copies only public task material, and emits
the task envelope and per-run secrets without starting containers.

## Run the full core track

Prerequisites are Docker Engine, the Docker Compose v2 plugin, and
`OPENAI_API_KEY`:

```bash
export OPENAI_API_KEY=...
PYTHONPATH=src python -m clawbench.web2code.run pilot \
  --site northstar-market \
  --track core \
  --model gpt-5.6-sol \
  --thinking-level xhigh
```

The command starts the reference, mailbox, BrowserUse gateway, remote rootless
builder, and Agent; builds the final candidate in a fresh offline sandbox; runs
the private multi-seed evaluator; then writes:

```text
web2code-output/<run-id>/
  candidate/                    # Agent-owned candidate source
  agent/agent-messages.jsonl    # Codex JSONL transcript
  browser/                      # controlled actions and screenshots
  builds/                       # build-service logs
  eval/facts.json               # private evaluator facts
  eval/evaluation-result.json   # websitebench.result.v1
  eval/failure-report.md        # human-oriented diagnosis
  run-meta.json
```

## Human-in-the-loop track

Start with `--track hitl`. While the Agent is waiting, append an auditable,
hash-chained intervention from a separate terminal:

```bash
PYTHONPATH=src python -m clawbench.web2code.run hitl-message \
  web2code-output/<run-id> \
  --category debug-direction \
  --message "Recheck guest-cart merge after login"
```

The track permits at most 12 messages and 90 minutes. Humans cannot directly
edit candidate files. Pass `--final` on the last message to end the wait.

## Local reference inspection

The reference is private benchmark infrastructure, not candidate input. A
corpus maintainer can run it with a generated `.env` and the `corpus` profile,
then connect a browser through an explicitly published port. The normal pilot
does not publish it to the host and intentionally exposes it only to the
controlled browser container.

## Verification status

The pure-Python contract, business-rule, isolation, scoring, and reporting
suite passes locally. Container-level release validation additionally requires
a working Docker daemon and Compose v2; see `GATE_W4.md` for the current host's
infrastructure status.
