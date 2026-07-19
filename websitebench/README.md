# ClawBench Web2Code2Web

This directory contains the corpus and protocol for the Web2Code2Web benchmark.
It is deliberately separate from the existing live-web task suites under
`test-cases/`.

The first corpus item is **Northstar Market**, a synthetic, white-label
commerce application. It is inspired by common commerce interaction patterns,
but it does not use Amazon source code, data, trademarks, or media.

## Trust boundary

- `northstar-market/public/` is visible to candidate-building agents.
- `northstar-market/reference/` will contain the private reference
  implementation and is never mounted into an agent or browser-gateway
  container.
- `northstar-market/judge/` will contain private evaluators and hidden fixtures.
- `schemas/` is public and defines the cross-site benchmark protocol.

An agent may explore a running reference only through the controlled browser
gateway. The agent may edit and build its candidate workspace, but it may not
read the reference filesystem, browser profile, response bodies, bundles,
source maps, cache, or hidden evaluator files.

## Human-in-the-loop gates

The Northstar pilot is delivered in four review gates:

1. **W1 Protocol freeze** -- PRD, manifest, fixture/reset/clock contract,
   candidate output contract, checkpoints, scoring, and report schema.
2. **W2 Reference behavior** -- reference app, mailbox, deterministic data,
   state machine, and business tests.
3. **W3 Isolation and builder** -- container networks, controlled BrowserUse
   gateway, Codex runner, build service, logging, and anti-cheat checks.
4. **W4 Evaluation and release** -- multi-seed hidden tests, concurrency,
   visual and functional scoring, failure reports, and end-to-end pilot runs.

Work after each gate normally requires explicit human approval. The Northstar
pilot's W1 gate was explicitly approved, and the user then authorized automatic
progress through W2--W4. Approved files are versioned: behavior changes require
a manifest version bump and a fresh gate.
