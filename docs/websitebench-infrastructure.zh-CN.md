# WebsiteBench 基础设施架构说明

- **文档状态：** 仓库必读架构文档
- **版本：** 1.0
- **生效日期：** 2026-07-20
- **适用范围：** WebsiteBench 站点、Variant、运行器、评测器、批调度、HITL、轨迹与结果展示
- **规范依据：** `docs/benchmark-infrastructure-hitl-standard.md`

本文用中文说明 WebsiteBench 当前基础设施的组成、运行流程、信任边界和
Human-in-the-loop（HITL）能力边界。所有参与构建或修改 Benchmark 的人、
Agent、模型和自动化脚本都必须先阅读本文，并同时遵循英文规范中的
MUST/MUST NOT 条款。

## 1. 总体结论

当前 WebsiteBench 是一套由可信 Host 控制的单机 Benchmark 基础设施：

- Python Host Orchestrator 负责解析、运行、归因、评分和写结果；
- Docker Compose 负责启动 Reference、Agent、Browser Gateway、Builder、
  Mailbox、Model Proxy 和 Judge；
- rootless Docker daemon 负责隔离构建 Candidate；
- 最终 Candidate 使用独立、受限、无通用外网的运行沙箱；
- SQLite ledger 负责批任务、租约、attempt、重试和恢复；
- Variant DSL 负责确定性生成同一站点的公开与私有评测产物；
- 离线 trajectory bundle 负责安全导出 Agent、浏览器、人工、构建和评测轨迹；
- Viewer 是只读展示与人工证据审核层，不参与可信评分。

当前实现不是 Kubernetes，也不是跨机器分布式调度系统。需要分布式化时，
不得改变本文规定的 Registry 权威、Host 评分权威和网络/文件信任边界。

## 2. 总体架构图

```text
                             ┌─────────────────────────────┐
                             │ websitebench.registry.v1    │
                             │ site → driver / variant     │
                             │ family → split              │
                             └──────────────┬──────────────┘
                                            │ resolve
                                            ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Trusted Host                                                         │
│                                                                       │
│  SiteRegistry → ResolvedSite → trusted run manifest                  │
│        │                    │                                         │
│        │                    ├── secrets.env（Host only）              │
│        │                    └── task.v2 + public files（Agent 可见）  │
│        │                                                              │
│        ├── RunOrchestrator                                           │
│        ├── AttemptJournal / typed outcome                            │
│        ├── SQLite BatchLedger                                        │
│        └── Host scorer / result writer                               │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ Docker Compose
                                ▼
┌────────────────────────── Build Plane ────────────────────────────────┐
│                                                                       │
│  Agent ──agent-control──► Browser Gateway ──► Reference / Mailbox    │
│    │                                └────────► Candidate preview      │
│    │                                                                  │
│    ├──agent-control──► Candidate Builder ──► Rootless Docker daemon  │
│    └──model-egress───► Model Proxy ─────────► 指定模型 API           │
│                                                                       │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ final image + source/archive digest
                                ▼
┌──────────────────────── Evaluation Plane ─────────────────────────────┐
│                                                                       │
│  Host final Candidate sandbox ◄──── Judge ────► Reference            │
│               │                         │                             │
│               └── candidate-web ────────┘                             │
│                                         │                             │
│                                         └── facts.json only           │
└───────────────────────────────┬───────────────────────────────────────┘
                                ▼
                Host 校验 facts → 评分 → result.v1 / failure report
```

## 3. Corpus、Registry 与 Variant 层

### 3.1 Registry 是唯一入口

`websitebench/registry.yaml` 是站点与数据集划分的中央注册表，负责：

- `site_id → SiteDriver`；
- `site_id → Variant spec`；
- `family_id → train/validation/test split`；
- `site_id → family_id + variant_id`。

任何通用运行模块都不得通过拼接目录名、硬编码站点 ID 或固定 Compose 服务名
来选择站点。所有站点必须先经过 `SiteRegistry.resolve(site_id)`，得到经过校验
的 `ResolvedSite`。

当前 Registry 注册四个 commerce 站点：

| Site | Variant | Family | Split | Runtime |
| --- | --- | --- | --- | --- |
| Northstar Market | `northstar-standard` | `white-label-commerce-v1` | validation | Northstar 专用 Compose |
| Ember Drop | `ember-drop` | `white-label-commerce-v1` | validation | 共享 Commerce Runtime |
| Foundry Wholesale | `foundry-wholesale` | `white-label-commerce-v1` | validation | 共享 Commerce Runtime |
| Harbor Pickup | `harbor-pickup` | `white-label-commerce-v1` | validation | 共享 Commerce Runtime |

Amazon-136 是开发和 harness calibration 材料，不是注册站点，不进入正式
Commerce `/100` 评分和排行榜。

### 3.2 SiteDriver 的职责

每个 `websitebench.driver.v1` 必须声明：

- public Manifest；
- Compose 文件；
- Reference、Mailbox、Builder、Browser、Agent、Judge 等语义服务角色；
- agent-control、reference-web、candidate-web 等语义网络角色；
- Reference、Mailbox、Browser Gateway、Candidate URL；
- typed Candidate runtime environment；
- Host secret allowlist；
- public/private mounts 及其允许角色和只读属性；
- Candidate image/container/volume 命名和资源限制；
- Candidate health、admin、fixture/schema mount 约定；
- evaluator profile、argv、environment 和 facts path；
- scoring policy、facts schema 和 result schema。

Driver 可以使用不同的真实 Compose 服务名，但必须映射到相同的语义角色。
通用运行代码只读取语义角色，不读取 Northstar 等站点常量。

### 3.3 VariantCompiler 的职责

`white-label-commerce-v1` Variant DSL 使用严格、声明式数据描述：

- 品牌与目录；
- 注册、验证、登录和密码找回；
- session、token 和受控时钟；
- guest/account cart 与 merge 策略；
- 库存、reservation、购物车上限；
- 税率、运费门槛、pickup slot；
- checkout idempotency、订单和取消窗口；
- named journey 和 named assertion kinds。

Variant 中禁止动态代码、脚本、`eval`、动态 import、可执行模板和 split override。
Compiler 应确定性生成：

- public Manifest、PRD、candidate contract；
- public fixtures、smoke cases、visual checkpoints、scoring；
- private fixtures 和 Judge assertions；
- task v2；
- `variant.digest.json`。

`compile --check` 必须只检查漂移，不写文件。

## 4. Host 控制面

### 4.1 prepare_run

Host 准备一次运行时按以下顺序执行：

1. 通过 Registry 解析站点；
2. 校验 Driver、Manifest、family split、路径、mount、environment 和 Compose；
3. 创建权限为私有的 run 目录；
4. 复制 Agent 可见的 public 文件与 public schemas；
5. 生成 `websitebench.task.v2`；
6. 在 `trusted/` 下生成 digest-addressed `websitebench.run-manifest.v1`；
7. 生成 `secrets.env`，权限限制为 Host 可读写；
8. 创建 `AttemptJournal`；
9. 扫描 public export，拒绝隐藏 seed、私有内容、秘密值和 trusted manifest 泄漏；
10. 输出 `public-export.json`，记录公开文件的大小与 SHA-256。

可信 run manifest 不挂载给 Agent、Browser Gateway 或 Candidate Builder。执行前
Host 会重新计算 digest，并逐个验证冻结输入是否发生变化。

### 4.2 run 目录

一次标准运行的目录结构如下：

```text
<run>/
├── task.json                         # Agent 可见 task.v2
├── run-meta.json                     # Host 运行元数据
├── public-export.json                # 公开导出清单与摘要
├── secrets.env                       # Host-only secrets
├── human-interventions.jsonl         # HITL 消息哈希链
├── trusted/                          # Host-only run manifest
├── attempts/                         # typed attempt journal
├── public/                            # Agent 可见合同与 fixtures
├── schemas/                           # Agent 可见公开 schemas
├── candidate/                         # Agent 编写的最终源码
├── agent/                             # Codex JSONL 与 exit metadata
├── browser/                           # 受控动作和截图
├── builds/                            # 构建日志、镜像 manifest/archive
└── eval/                              # facts、资源事实、结果和失败报告
```

私有 reference、hidden fixtures、Judge assertions 和 evaluator source 保持在
corpus 中，只通过 Driver 声明的只读 mount 提供给允许角色。

## 5. Compose 服务与信任边界

### 5.1 服务角色

| 语义角色 | 作用 | 可访问内容 | 明确禁止 |
| --- | --- | --- | --- |
| Reference | 运行私有目标网站 | 私有 reference、确定性 fixtures、reference DB | Agent control、通用外网 |
| Mailbox Query | 为浏览器提供本地邮件查询 | mailbox DB | Candidate/Agent 直接读取 DB |
| Mailbox Delivery | 接收 Reference/Candidate 邮件 | delivery token、mailbox DB | 通用外网 |
| Build Daemon | rootless Docker/BuildKit | public fixtures、schemas、构建网络 | Reference 网络、Host Docker socket |
| Candidate Builder | 校验、构建和启动 preview | Candidate workspace、Builder API | 私有 reference、Judge、Host socket |
| Browser Gateway | 受控探索 Reference/Mailbox/preview | 浏览器状态、可访问性信息、截图 | 源码 mount、DevTools/profile/cache/raw source 导出 |
| Model Proxy | 转发模型流量 | 声明的模型 host/port | 任意目的地代理 |
| Agent | 探索并构建 Candidate | task/public、workspace、Browser/Builder MCP | 私有输入、Docker API、Reference 直连、任意网络 |
| Evaluator/Judge | 比较 Reference 和 final Candidate | 两侧 HTTP/admin、只读 fixtures/assertions | Agent/model 网络、最终评分权 |

大多数服务使用：

- `read_only: true`；
- `cap_drop: [ALL]`；
- `no-new-privileges`；
- 独立 tmpfs；
- 显式内存和 CPU 限制；
- 禁止 privileged、host network 和 Host Docker socket。

rootless build daemon 因运行 dockerd 需要有限的 `SETUID/SETGID` 和放宽的
seccomp/apparmor，但仍不使用 privileged 模式或 Host Docker socket。

### 5.2 网络角色

| 网络 | 是否 internal | 连接角色 | 目的 |
| --- | --- | --- | --- |
| `agent-control` | 是 | Agent、Browser Gateway、Candidate Builder | Agent 调用受控工具 API |
| `reference-web` | 是 | Reference、Mailbox、Browser Gateway、Judge | 隔离 Reference 浏览与评测 |
| `candidate-web` | 是 | preview/final Candidate、Mailbox Delivery、Browser Gateway、Judge | Candidate 浏览和最终评测 |
| `model-egress` | 是 | Agent、Model Proxy | Agent 只能连接模型代理 |
| `build-egress` | 否 | rootless daemon、Builder | 构建依赖和镜像流量 |
| `internet-egress` | 否 | Model Proxy | 仅转发到声明的模型 API |

Agent 不能直接连接 Reference 或 final Candidate。Browser Gateway 是 Agent
浏览 Reference、Mailbox 和 Candidate preview 的唯一通道。

## 6. Candidate 构建与预览

Agent 在自己的容器内拥有 `/workspace/candidate` 的写权限，并通过 MCP 调用：

- `browser_create`、`browser_action`、`browser_screenshot`、`browser_close`；
- `candidate_build`；
- `candidate_preview_status`。

Candidate Builder 每次构建都会：

1. 检查必需目录、Dockerfile、Compose、seed/reset scripts；
2. 拒绝逃逸 symlink 和超出大小限制的源码；
3. 计算 Candidate source digest；
4. 通过 rootless daemon 构建镜像；
5. 使用 public fixture 启动只读 preview；
6. 检查 health 和 public admin reset；
7. 记录构建日志、耗时、剩余 build budget；
8. 在 finalization 时导出 image archive、source digest 和 archive digest。

Agent 没有 rootless daemon 的直接访问权，只能调用带 token、带预算的 Builder
API。Preview 使用公开 fixture，不允许加载 hidden fixtures。

## 7. 最终 Candidate 运行沙箱

Agent 成功结束后，Host：

1. 要求 Builder 导出最后一次成功 preview 对应的镜像；
2. 验证 Candidate 源码未在最后成功构建后发生变化；
3. 停止并删除 Builder、Browser Gateway、Build Daemon 和 Model Proxy；
4. 执行 source-policy 和 anti-cheat 扫描；
5. 验证 image archive SHA-256；
6. 将镜像加载到 Host Docker；
7. 以 `--network none` 创建 Candidate；
8. 仅将 Candidate 接入声明的内部 `candidate-web`；
9. 启用 read-only root、drop all capabilities、no-new-privileges、PID/CPU/
   memory/tmpfs limits；
10. 将 `/data` 挂载为独立持久化 volume；
11. 将 evaluation fixtures 和 schemas 只读挂载；
12. 等待 health 成功并启动资源监控。

最终 Candidate 不再拥有 build/model egress，也无法访问 Reference filesystem。

## 8. Judge、Host scoring 与失败归因

### 8.1 Judge 只产生事实

Judge 可以访问 Reference 与 final Candidate 的公开/admin 端点，并运行：

- visual checkpoints；
- interaction assertions；
- end-to-end journeys；
- robustness/boundary tests；
- concurrency 和 persistence tests；
- network/resource observations。

Judge 只写 `websitebench.facts.v1` 的 `facts.json`，不得计算最终 `/100` 分数，
不得写 canonical result。

### 8.2 Host 是唯一评分者

Host 必须：

1. 校验 facts schema；
2. 合并 Host 采集的 build、startup、image、source、memory 和 latency facts；
3. 读取该 SiteDriver 声明的 scoring policy；
4. 计算 Visual、Interactions、Journeys、Robustness、Efficiency；
5. 校验 `websitebench.result.v1`；
6. 写 `evaluation-result.json` 和 `failure-report.md`。

如果 evaluator 非零退出但已经写出合法 facts，Host 仍然评分。只有缺失、无法
读取或 schema/评分语义无效的 facts 才归因于 evaluator failure。

### 8.3 AttemptOutcome

Attempt journal 按阶段记录：

```text
prepared
  → agent
  → candidate_finalize
  → source_policy
  → candidate_build
  → candidate_start
  → candidate_health
  → evaluator
  → facts_validation
  → host_scoring
  → result_validation
  → finalized
```

终态归因分为：

- `scored`；
- `candidate_failed`；
- `evaluator_failed`；
- `infrastructure_error`。

归因和 scheduler state 是两套不同维度，不能混用。

## 9. SQLite 批调度

Batch 支持按以下维度确定性展开：

- site、family 或 split 查询；
- model；
- thinking level；
- track；
- repetitions；
- concurrency。

Plan 会冻结：

- Registry digest；
- 每站点 run-manifest digest；
- Agent prompt digest；
- Git commit 和 source tree digest；
- Compose 与 Dockerfile/image inputs；
- public inputs；
- budgets；
- 完整 job matrix。

resume 时只要任一冻结输入变化，就必须拒绝继续，并要求创建新 plan。

SQLite ledger 保存：

- plans；
- jobs；
- journey-seed executions；
- leases 和 owner；
- attempts；
- retry deadlines；
- outcome；
- events；
- artifact references。

Scheduler state 为：

- `queued`；
- `running`；
- `waiting_for_human`；
- `retry_wait`；
- `terminal`。

默认并发为 1，当前上限为 8。Worker 定期续租；失效租约在恢复前先关闭为可
审计的 infrastructure interruption。

重试规则：

- Candidate failure：0 次；
- transient evaluator failure：最多 1 次；
- allowlisted infrastructure error：最多 2 次；
- backoff：5 秒、30 秒。

当前 C001 正式矩阵为 Foundry、Ember、Harbor × xhigh/high/medium/low，Core
track、repetition 1、concurrency 2，共 12 个 jobs 和 120 个 journey-seed
executions。

## 10. Human-in-the-loop

### 10.1 当前单次运行机制

HITL 必须由 public track contract 显式启用。当前机制是：

```text
codex exec 首轮
      │ thread.started / turn.completed
      ▼
Agent 容器轮询 human-interventions.jsonl
      │
Human 执行 hitl-message
      │
      ├── category + message + final
      └── sequence + timestamp + previous_hash + hash
      ▼
codex exec resume <thread_id> "Human intervention (...)"
      │
      └── 重复，最多 12 条 / 90 分钟
```

允许的 intervention categories：

- `product-understanding`；
- `exploration-strategy`；
- `frontend-layout`；
- `backend-modeling`；
- `debug-direction`；
- `test-suggestion`；
- `missing-feature`；
- `memory-correction`。

每条消息长度为 1-4000 字符。`--final` 表示该消息对应的 resumed turn 完成后
结束 HITL 等待。协议不允许人工直接编辑 Candidate 文件。

### 10.2 当前边界

当前 HITL 是同一 Agent 容器内的轮询/resume，而不是 checkpoint scheduler：

- 等待人工时 Agent 容器仍然存活；
- build plane 和 batch worker 仍被占用；
- 人工等待计入 Agent wall time；
- 每轮结束后没有 durable checkpoint；
- Agent/container 崩溃后不能可靠地从上一轮恢复；
- `waiting_for_human` 只在 ledger/schema 中预留，尚未连接原子唤醒流程；
- 当前 `human_minutes` 仍记录为 0；
- 正式 experiment 必须保持 `hitl.generate_jobs: false`。

任何 Agent、文档或 UI 都不得将当前能力描述为：

- checkpointed HITL；
- worker-releasing HITL；
- crash-resumable HITL；
- human wait 不占 active time 的 HITL。

### 10.3 正式批量 HITL 的启用条件

正式启用前必须完成：

1. 每轮保存 thread ID、job/attempt ID、token、active time、sequence、digest；
2. `running → waiting_for_human` 原子状态转换；
3. 安全停止 build plane 并释放 worker lease；
4. `hitl-message` 原子 append 且只唤醒一个 job；
5. 使用 `codex exec resume` 恢复同一 thread；
6. human wait 不消耗 Agent active time，但仍受 90 分钟窗口限制；
7. checkpoint 丢失/损坏、resume 失败、窗口过期、scheduler crash 的 typed outcome；
8. Core/HITL 混跑和重启恢复测试；
9. 一次真人端到端 smoke。

## 11. 结果、轨迹与 Viewer

一次 run 的公开结果包括：

- `evaluation-result.json`；
- `failure-report.md`；
- 允许公开的 aggregate resource/network/usage 数据。

私有 `facts.json`、hidden seeds、Judge internals 和私有 reproduction 不得进入
公开 Viewer 或离线 public bundle。

离线 trajectory exporter 可以读取：

- Agent JSONL；
- Browser actions 与 screenshots；
- Human interventions；
- sanitized build artifacts；
- final Candidate snapshot；
- public evaluation result。

Exporter 必须扫描并排除 secrets、私有 reference、hidden fixtures、私有 facts、
runtime DB、cache 和 image archive，并为所有导出文件生成 SHA-256。

Viewer 是下游展示层。它可以读取通过 schema 校验且明确标记为 public 的结果，
但不得参与 Judge 执行、facts 生成或 Host scoring。

## 12. 常用命令

### 校验 Registry 与全部站点

```bash
PYTHONPATH=src python -m clawbench.web2code.run validate --all
```

### 准备单次 dry run

```bash
PYTHONPATH=src python -m clawbench.web2code.run pilot \
  --site foundry-wholesale \
  --track core \
  --model gpt-5.5-codex \
  --thinking-level xhigh \
  --dry-run
```

### 编译全部 Variant

```bash
PYTHONPATH=src python -m clawbench.web2code.variants compile --all
PYTHONPATH=src python -m clawbench.web2code.variants compile --all --check
```

### 创建和运行 batch

```bash
PYTHONPATH=src python -m clawbench.web2code.batch plan \
  --ledger artifacts/websitebench/c001/batch.sqlite3 \
  --out artifacts/websitebench/c001/plan.json \
  --split validation \
  --model gpt-5.5-codex \
  --thinking-level xhigh \
  --track core \
  --repetitions 1 \
  --concurrency 1

PYTHONPATH=src python -m clawbench.web2code.batch run \
  --ledger artifacts/websitebench/c001/batch.sqlite3 \
  --plan artifacts/websitebench/c001/plan.json
```

### 发送 HITL 消息

```bash
PYTHONPATH=src python -m clawbench.web2code.run hitl-message \
  web2code-output/<run-id> \
  --category debug-direction \
  --message "重新检查登录后的 guest cart merge" \
  --final
```

## 13. 所有 Agent 的强制检查清单

任何 Agent 或模型新增/修改 Benchmark 时必须确认：

- [ ] 已阅读本文和 `docs/benchmark-infrastructure-hitl-standard.md`。
- [ ] 站点通过 Registry/Driver 解析，没有在通用模块加入站点常量。
- [ ] family split 只由 Registry 管理。
- [ ] public、private、trusted 和 secret 文件严格隔离。
- [ ] Agent 和 Browser Gateway 没有私有 source/fixture/Judge mounts。
- [ ] Compose 语义角色和网络拓扑验证通过。
- [ ] Builder 使用 rootless daemon，未使用 Host Docker socket。
- [ ] final Candidate 保持只读、最小权限、内部网络和资源限制。
- [ ] Judge 只写 facts，Host 独占评分和 result 写入权。
- [ ] candidate/evaluator/infrastructure attribution 与 scheduler state 分离。
- [ ] retry limits、backoff 和租约恢复语义未被弱化。
- [ ] 当前未将 checkpointed batch HITL 误报为已实现。
- [ ] 相关 contracts、Registry/Variant、isolation、scoring、batch、trajectory、
      Viewer 和兼容性测试通过。
- [ ] 修改 Compose、网络、mount、Builder、Candidate runtime、Judge 或 HITL
      lifecycle 时执行 Docker 端到端 smoke；无法执行时明确记录原因。

## 14. 关键源码入口

| 领域 | 路径 |
| --- | --- |
| Registry | `websitebench/registry.yaml`、`src/clawbench/web2code/registry.py` |
| Drivers | `websitebench/drivers/*.driver.yaml` |
| Variant compiler | `src/clawbench/web2code/variants.py` |
| Host orchestration | `src/clawbench/web2code/run.py` |
| Compose topology validation | `src/clawbench/web2code/topology.py` |
| Candidate final runtime | `src/clawbench/web2code/candidate.py` |
| Candidate builder | `websitebench/services/builder/benchbuilder/app.py` |
| Controlled browser MCP | `websitebench/services/agent/gateway_mcp.py` |
| Agent runner/HITL resume | `websitebench/services/agent/run_agent.py` |
| HITL audit log | `src/clawbench/web2code/hitl.py` |
| Attempt outcomes | `src/clawbench/web2code/attempts.py` |
| Batch ledger/scheduler | `src/clawbench/web2code/batch.py` |
| Judge facts schema | `websitebench/schemas/facts.schema.json` |
| Host scoring | `src/clawbench/web2code/scoring.py` |
| Offline trajectory | `src/clawbench/trajectory/exporter.py` |

## 15. 文档变更规则

如果未来修改信任边界、评分权威、retry policy、HITL budget、人工权限或
checkpoint semantics，必须：

1. 先取得明确的人工批准；
2. 同步更新本文与英文规范版本；
3. 同一变更中补齐 schemas、tests 和 migration/compatibility 说明；
4. 不得通过文档措辞掩盖尚未实现的能力。
