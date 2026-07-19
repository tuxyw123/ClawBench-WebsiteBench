# Gate W3 -- Isolation and candidate builder

Status: **implemented and auto-approved**

Delivered:

- Compose trust topology with separate internal reference, candidate,
  Agent-control, and model networks plus an independent build-egress network;
- a BrowserUse `0.12.6` gateway exposing only budgeted navigate, click, type,
  select, scroll, hover, history, visible-state, and screenshot actions;
- a Codex runner with explicit model/reasoning settings, JSONL capture,
  wall-clock/token budgets, and resumable HITL turns;
- a remote rootless build service with layout, build-count, source-size, and
  symlink checks and no host Docker socket;
- a final candidate runtime created with no initial network, then attached only
  to the internal candidate network with read-only root, dropped
  capabilities, resource limits, persistent `/data`, and hidden fixtures
  mounted read-only;
- static and runtime-request anti-cheat rules, including hard rejection of any
  iframe, reference origin, privileged mode, host networking, Docker socket,
  escaping symlink, remote screenshot service, or exact private-file copy;
- a hash-chained HITL log limited to 12 messages and 90 minutes, with no direct
  human file edits.
- narrowly scoped artifact mounts: the Agent cannot read per-run secrets,
  reference/evaluator files, browser artifacts, or arbitrary run-directory
  contents;
- an internal model network and exact host/port allowlist proxy: the Agent has
  no direct internet route, while the proxy alone can reach the configured
  model API.
- split mailbox trust planes: reference and Candidate applications can only
  reach the token-authenticated delivery listener, while inbox/query and reset
  endpoints live on a separate network unavailable to Candidate runtime.
- exact-image promotion: the isolated builder records a source digest and
  exports the last healthy public-seed preview; the host verifies both source
  and archive digests, loads that image, and never performs a second networked
  Candidate build.

Acceptance evidence:

- topology and policy validation is machine enforced by
  `tests/web2code/test_web2code_isolation.py`;
- run preparation proves that only public task material is copied into Agent
  scope;
- all 42 Web2Code2Web tests, Ruff checks, Python compilation, and the contract
  validator pass on the current host.
