# WebsiteBench Benchmark Infrastructure and HITL Standard

- **Status:** Required repository standard
- **Version:** 1.0
- **Effective date:** 2026-07-20
- **Applies to:** Every human, Agent, model, script, or automation that creates,
  extends, reviews, or operates a WebsiteBench benchmark

This document defines the required infrastructure and Human-in-the-loop (HITL)
contract for WebsiteBench. It records both the current implementation boundary
and the invariants that future implementations must preserve.

The terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative. An Agent
must not silently depart from a MUST/MUST NOT requirement. A proposed exception
requires explicit human approval, a documented rationale, and regression tests.

## 1. Benchmark infrastructure

### 1.1 Required architecture

WebsiteBench uses a host-controlled, Registry-driven execution chain. The
baseline implementation is a single trusted host running Python orchestration,
Docker Compose, an isolated rootless candidate builder, and a SQLite batch
ledger. It is not currently a Kubernetes or distributed scheduler.

```text
Registry + Variant DSL
          │ resolve
          ▼
Host RunOrchestrator ───── SQLite Batch Ledger
          │
          ├── trusted run manifest + secrets (host only)
          ├── task.v2 + public contracts (candidate-visible)
          │
          ▼
Docker Compose build plane
  Agent ──► Browser Gateway ──► Reference / Mailbox / Candidate preview
    │
    ├────► Candidate Builder ──► Rootless Docker daemon
    └────► Model Proxy ────────► Declared model API only
          │
          ▼
Remove builder, browser, preview, and model build-plane services
          │
          ▼
Host starts the immutable final Candidate sandbox
          │
          ▼
Judge compares Reference and Candidate and writes facts only
          │
          ▼
Host validates facts, scores, validates the result, and writes reports
```

### 1.2 Registry, SiteDriver, and Variant authority

1. `websitebench.registry.v1` MUST be the sole mapping for:

   - `site_id` to private SiteDriver;
   - `site_id` to Variant specification;
   - `family_id` to dataset split.

2. A SiteDriver MUST declare semantic service roles, network roles, Compose
   input, public Manifest, typed Candidate environment, mounts, Candidate
   runtime limits, evaluator invocation, facts schema, scoring policy, and
   result schema.

3. Generic orchestration MUST consume semantic roles from the resolved Driver.
   It MUST NOT depend on a site's concrete service names, paths, seeds, brand,
   or URLs.

4. Variant definitions MUST be strict data. They MUST NOT contain executable
   scripts, dynamic imports, executable templates, `eval`, or a split override.

5. A family MUST belong to exactly one of `train`, `validation`, or `test`.
   Generated public Manifests inherit that split from the Registry.

6. Public and hidden artifacts SHOULD be deterministically generated from the
   same semantic Variant definition. Repeated compilation MUST be byte-stable,
   and `compile --check` MUST detect drift without writing.

7. Amazon-136 remains development/calibration material. It MUST NOT be treated
   as a registered scored commerce site or added to the official leaderboard.

### 1.3 Trusted run preparation

For every run, the Host MUST:

1. resolve the selected site through SiteRegistry;
2. validate its public Manifest, Driver, family split, paths, mounts,
   environment declarations, and role-based topology;
3. write an immutable, digest-addressed `websitebench.run-manifest.v1` under a
   host-only `trusted/` directory;
4. generate per-run secrets with restrictive file permissions;
5. expose only task v2, public contracts, public fixtures, and public schemas to
   the Agent;
6. scan the public export for private paths, hidden seeds, private contents,
   secrets, and trusted-manifest leakage;
7. revalidate the trusted digest and every frozen input before execution.

The trusted run manifest, private Driver, private reference implementation,
hidden fixtures, Judge assertions, evaluator internals, and secrets MUST NOT be
mounted into the Agent or Browser Gateway.

### 1.4 Role and network trust boundaries

| Role | Required access | Prohibited access |
| --- | --- | --- |
| Host orchestrator | Registry, Driver, trusted manifest, secrets, Docker control, final scoring | Delegating scoring authority to the Judge |
| Agent | Candidate workspace, task/public files, controlled browser/build MCP, model proxy | Reference filesystem, Judge files, hidden fixtures, raw Docker API, host socket, arbitrary network |
| Browser Gateway | Controlled browser sessions for Reference, mailbox, and Candidate preview | Candidate/reference source mounts, raw page-source export, DevTools/profile/cache export |
| Candidate Builder | Candidate workspace and isolated rootless build daemon | Host Docker socket, Reference network, private reference/Judge inputs |
| Model Proxy | Declared model host and port | General-purpose outbound proxying |
| Reference | Private reference and deterministic fixtures on `reference_web` | Agent control or general internet |
| Final Candidate | Declared runtime environment, persistent `/data`, read-only evaluation fixtures/schemas, `candidate_web` | Build/model networks, general internet, Host filesystem |
| Evaluator/Judge | Reference and Candidate endpoints, read-only evaluation inputs, fact output directory | Agent control, model access, final score/result authority |

The semantic networks are:

- `agent_control`: Agent to Browser Gateway and Candidate Builder control APIs;
- `reference_web`: Reference and mailbox query/delivery plane;
- `candidate_web`: Candidate preview/final runtime, mailbox delivery, and Judge;
- `model_egress`: Agent to the model proxy only;
- `build_egress`: isolated dependency/image build traffic;
- `internet_egress`: model proxy or explicitly approved infrastructure egress.

`agent_control`, `reference_web`, `candidate_web`, and `model_egress` MUST be
internal Docker networks. No service may use host networking, privileged mode,
or `/var/run/docker.sock`.

### 1.5 Candidate build and final runtime

1. Candidate builds MUST run through the budgeted Candidate Builder backed by a
   rootless Docker daemon.
2. Preview builds MAY use public fixtures only.
3. Finalization MUST record the successful image, source digest, archive digest,
   build count, and build duration.
4. The Host MUST reject source changes after the final successful build.
5. Before evaluation, the build/model/preview plane MUST be stopped and removed.
6. The final Candidate MUST start from the exported immutable image with:

   - no initial network;
   - connection only to its declared internal Candidate network;
   - read-only root filesystem;
   - all Linux capabilities dropped;
   - `no-new-privileges`;
   - PID, memory, CPU, and tmpfs limits;
   - persistent state only in its assigned `/data` volume;
   - fixtures and schemas mounted read-only.

7. Source-policy and anti-cheat checks MUST run before the final Candidate is
   evaluated.

### 1.6 Evaluation and scoring authority

1. A Judge MUST be a facts producer only.
2. Facts MUST validate against `websitebench.facts.v1` before scoring.
3. A schema-valid facts document MUST be scored even when the evaluator process
   exits non-zero.
4. Missing, unreadable, or invalid facts MUST be attributed to the evaluator,
   not converted into a zero-score Candidate result.
5. The Host is the sole authority that:

   - reads the scoring policy;
   - computes dimension and total scores;
   - validates `websitebench.result.v1`;
   - writes the canonical JSON result and human-readable failure report.

6. Attempt attribution MUST distinguish `candidate_failed`,
   `evaluator_failed`, and `infrastructure_error`. Scheduler state MUST remain
   separate from attempt attribution.

### 1.7 Batch execution

1. Batch plans MUST freeze Registry/run-manifest inputs, task prompt, code/tree
   digests, Compose/image inputs, budgets, selectors, models, thinking levels,
   tracks, repetitions, and concurrency.
2. A resume operation MUST refuse a plan whose frozen inputs have drifted.
3. SQLite is the source of truth for plans, jobs, journey-seed executions,
   leases, attempts, retry deadlines, outcomes, events, and artifact references.
4. Job claim and completion MUST be transactional and safe across workers.
5. Default concurrency is 1. Implementations MUST enforce a documented upper
   bound.
6. Candidate failures MUST NOT retry automatically. A transient evaluator
   failure may retry once. Only allowlisted infrastructure errors may retry
   twice, using the 5-second and 30-second backoffs.
7. Expired running leases MUST be closed as auditable infrastructure
   interruptions before a job is reclaimed.
8. Batch summaries MUST preserve attempt history and report scheduler counts,
   attribution counts, retries, scores, timing, journey-seed outcomes, and
   artifact references.

## 2. Human-in-the-loop standard

### 2.1 Scope

This section governs a Candidate run whose task track is `hitl`. It is distinct
from:

- corpus-construction review gates such as W1-W4;
- manual screenshot/evidence review in the Viewer;
- retrospective `human-agent` histories reconstructed from Markdown.

Those processes may provide evidence or approval, but they are not substitutes
for the runtime HITL protocol below.

### 2.2 Permitted human intervention

1. HITL MUST be explicitly enabled by the site's public track contract.
2. The default limit is 12 human messages within a 90-minute HITL window.
3. Human file edits are prohibited. The supported intervention channel is an
   auditable message only.
4. Every message MUST use one of these categories:

   - `product-understanding`;
   - `exploration-strategy`;
   - `frontend-layout`;
   - `backend-modeling`;
   - `debug-direction`;
   - `test-suggestion`;
   - `missing-feature`;
   - `memory-correction`.

5. A message MUST contain 1-4000 non-whitespace characters.
6. `final: true` MUST end the human-intervention loop after that message is
   delivered and its resumed Agent turn completes.

### 2.3 Audit record

Each human message MUST be appended to `human-interventions.jsonl` with:

- monotonic sequence number;
- UTC timestamp;
- elapsed HITL minutes;
- category;
- message;
- final flag;
- previous-record hash;
- current-record SHA-256 hash.

The hash chain MUST validate before publication or offline export. Human
message count MUST be included in result usage and trajectory metadata. A
future implementation MUST also record real human waiting/active minutes rather
than leaving `human_minutes` at zero.

### 2.4 Current single-run HITL behavior

The current implementation performs HITL inside one long-running Agent
container:

1. execute the first `codex exec --json` turn;
2. capture the Codex thread ID and token usage;
3. poll the read-only mounted `human-interventions.jsonl` for new messages;
4. for each new record, execute `codex exec resume <thread_id>` with the human
   category and message;
5. accumulate token usage and enforce the remaining wall-time/token budget;
6. stop on Agent failure, budget exhaustion, the 12-message limit, the
   90-minute limit, or a final message;
7. write the final thread ID, usage, budget status, and exit code to the Agent
   artifact directory;
8. continue through normal Candidate finalization and evaluation.

This behavior is supported for a single run, but it has the following explicit
limitations:

- the Agent container remains alive while waiting;
- the build plane and worker lease remain occupied;
- human waiting consumes the run wall-time budget;
- there is no durable checkpoint after each turn;
- a process/container crash cannot reliably resume from the last completed
  turn;
- the batch ledger's `waiting_for_human` state is reserved but not yet wired to
  an atomic wake-up flow;
- official batch experiment definitions MUST keep HITL job generation disabled
  until checkpointed batch HITL is implemented and accepted.

Agents and documentation MUST NOT describe the current implementation as
checkpointed, worker-releasing, crash-resumable, or active-time-excluding HITL.

### 2.5 Requirements for checkpointed batch HITL

Before batch HITL may be enabled officially, all of the following MUST exist:

1. a durable checkpoint after every Agent turn containing job/attempt identity,
   Codex thread/resume ID, sequence, token counters, cumulative Agent active
   time, timestamp, and content digest;
2. an atomic transition from `running` to `waiting_for_human`;
3. safe build-plane teardown and worker-lease release while waiting;
4. an atomic `hitl-message` operation that appends exactly once and wakes
   exactly one waiting job;
5. resume through the persisted Codex thread without duplicating an attempt or
   human message;
6. human wait excluded from Agent active-time while the 90-minute HITL wall
   window remains enforced;
7. typed handling of expired windows, corrupt/missing checkpoints, resume
   failures, scheduler crashes, and teardown failures;
8. mixed Core/HITL scheduling and restart-recovery tests;
9. a real human smoke test covering wait, worker release, message, same-thread
   resume, valid facts, and Host-written result.

## 3. Mandatory Agent compliance checklist

Every Agent or model that constructs or changes a benchmark MUST perform and
report this checklist:

- [ ] Read this standard before editing benchmark code or contracts.
- [ ] Resolve all sites through Registry/Driver interfaces; introduce no
      site-specific constants into generic modules.
- [ ] Preserve public/private/trusted artifact separation.
- [ ] Validate role-based Compose topology and all schemas.
- [ ] Preserve rootless build isolation and final Candidate sandboxing.
- [ ] Keep Judge output facts-only and Host scoring authoritative.
- [ ] Preserve typed attempt attribution and retry limits.
- [ ] Keep Core and HITL scheduler semantics distinct.
- [ ] Do not claim unimplemented checkpoint/resume behavior.
- [ ] Run relevant contract, isolation, Registry/Variant, batch, scoring,
      trajectory, Viewer, and compatibility tests.
- [ ] Run a Docker-backed end-to-end smoke when changing Compose, networking,
      mounts, builder, Candidate runtime, evaluator, or HITL lifecycle behavior.
- [ ] Document any infrastructure limitation or skipped smoke explicitly.

## 4. Change control

Changes to this standard require explicit human approval when they alter a
trust boundary, scoring authority, retry policy, HITL budget, intervention
permissions, or checkpoint semantics. Update the version/effective date and add
or adjust acceptance tests in the same change.
