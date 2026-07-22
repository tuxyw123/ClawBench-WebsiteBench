# 离线网站 Clone Harness 使用手册

## 目标

`clawbench-offline-clone` 把离线 clone 从“写完一些页面后自行宣布完成”改造成一个可失效、可审计的阶段流程。它不包含 Amazon 业务代码，也不决定新网站应该复制多少页面；它负责强制保存范围、证据、资源、前端、后端和验收之间的依赖关系。

配套方法论见 [Amazon 案例复盘](offline-clone-amazon-case-study.md)。可自动调用的 Codex skill 安装在 `C:\Users\Administrator\.codex\skills\build-offline-site-clone\SKILL.md`。

## 生命周期

| 阶段 | 必须证明的事实 | 不能用什么替代 |
|---|---|---|
| `INIT` | 项目骨架已创建 | 空目录或口头计划 |
| `SOURCE_CAPTURED` | 网站目的、不变量、主线、capture context、证据等级、视觉 oracle 和独立分母已冻结；source truth 不读取 candidate | “大概把主要页面都做了” |
| `ASSETS_CLOSED` | 冻结范围内每个 required 资源均有来源副本、runtime 副本、引用、hash、MIME、尺寸和本地运行审计 | 截图、文件存在或浏览器缓存 |
| `FRONTEND_READY` | P0/P1 fixture 前端、视觉区域、滚动和交互状态通过 | 后端 API 已完成 |
| `BACKEND_READY` | 身份、作用域、状态机、失败/重试、幂等、迁移和外部服务边界通过 | happy path 或客户端校验 |
| `ACCEPTED` | visual、browser、network、migration、independent-audit、full-suite 六类当前证据全部通过 | 早期测试数字、退出码或单一相似度 |

阶段不能跳过。重跑某个上游 gate 会立即使它自身及全部下游 gate 失效。

## 快速开始

安装当前仓库后创建站点 adapter：

```powershell
clawbench-offline-clone init `
  --site-dir materials/example `
  --site-id example `
  --display-name "Example" `
  --source-url https://example.com/
```

生成结构：

```text
materials/example/
├── clone.yaml
├── scope/
│   ├── purpose.json
│   ├── invariants.json
│   ├── routes.json
│   ├── journeys.json
│   ├── checkpoints.json
│   ├── claims.jsonl
│   └── coverage.json
├── source-assets/manifest.json
├── clone/frontend/
├── clone/backend/
├── clone/static/assets/
├── artifacts/offline-clone/acceptance/  # release 时生成的结构化证据与 raw artifacts
└── .clone-harness/          # 本地状态与 trajectory；不提交
```

先编辑 `scope/*` 和 `clone.yaml` 中的 gate 命令，再依次运行：

```powershell
clawbench-offline-clone validate --site materials/example
clawbench-offline-clone gate source --site materials/example
clawbench-offline-clone gate assets --site materials/example
clawbench-offline-clone gate frontend --site materials/example
clawbench-offline-clone gate backend --site materials/example
clawbench-offline-clone gate release --site materials/example
clawbench-offline-clone status --site materials/example
clawbench-offline-clone report --site materials/example --out materials/example/artifacts/offline-clone/report.json
```

每个 gate 都必须配置至少一条真实命令。命令以 argv 数组运行，`shell=False`；可使用 `{python}`、`{site_dir}`、`{manifest}` 和 `{candidate_root}` 占位符。source gate 不得把 `candidate_root` 当作输入、cwd、脚本、模块或配置来源。站点内 verifier 脚本、`python -m` 包和 `--config=...` 文件会自动进入 gate fingerprint；显式站点外脚本被拒绝。

## Scope 与 coverage ledger

先写一句 `actor + core object + success verb`，再把目的拆成 entry、discover、decide、configure、commit、confirm、recover 和 aftercare 状态图。每个 selected mutation 都必须自动补齐 validation、duplicate、stale、foreign scope、refresh、retry 和 reversal/aftercare。

`purpose.json` 冻结目的与主线 journey，`invariants.json` 把 P0/P1 不变量绑定到 journey 和 coverage dimension；`routes.json`、`journeys.json`、`checkpoints.json` 和 `claims.jsonl` 保留站点特定事实。harness 不把电商字段强加给 SaaS、内容、预订或社区站点。

`coverage.json` 只冻结多组独立分母。每组列出完整 `required_items` 和允许背书它的 `required_evidence_kinds`；source 阶段的 `satisfied_items` 保持为空。release 后，由六类验收 artifact 的 `verified_coverage` 提供证据 numerator。某维若要求多种 evidence，最终 numerator 是各类证据集合的交集，而不是宽松并集。报告分别输出 declared 与 evidence-backed 的 numerator、denominator、remaining 和 ratio，禁止生成一个“整站完成率”。

至少应维护这些概念分母：

- source-direct、partial、historical、inferred 和 unavailable 状态；
- P0 与 P1 frontend rows；
- visually comparable rows；
- selected success、failure/retry 和 recovery journeys；
- applicable backend invariants；
- viewable、authorized-mutable 和 durably verified entities；
- 站点特定分母，例如 purchasable、bookable、publishable、review-backed、comparable 或 role × action × state。

## 资源闭包

`source-assets/manifest.json` 的每个记录包含稳定 ID、priority、required、来源路径、runtime 路径、bytes、SHA-256、MIME、图片尺寸、引用它的组件/状态、证据等级和可选 source URL。

内建 verifier 会检查：

- 相对路径 containment，拒绝绝对路径、盘符相对路径、UNC、`..` 和 symlink；
- source/runtime 路径不同且字节相同；
- 声明 bytes、hash、MIME 和 dimensions 与两份文件一致；
- required 资源具有至少一个 `referenced_by`；
- 所有 `required:true` 资源都进入阻塞分母，不因 P1/P2 而被忽略。
- 字体 magic、图片解码和 SVG 根元素真实有效；伪装 HTML、active SVG、外部 SVG/CSS URL 会被拒绝。

仍需在 assets gate 中配置站点 adapter 命令，观察 runtime network 并扫描 HTML/CSS/JS 动态引用。内建 byte verifier 不能替代浏览器的 `remote_runtime=0` 证据。真正的退出式为：

```text
required = downloaded = verified = referenced
missing = corrupt = hash_mismatch = remote_runtime = 0
```

纯文本站点可以使用 `closure_status: no-assets`，但必须写明原因；空 manifest 或 `pending` 不能通过。

## 前端先于后端

资源 gate 通过后，只使用确定性 fixture server 构建选定前端：shared shell、内容密度、模块顺序、字体、crop、canvas、原生滚动、breakpoint、hover/focus、keyboard、Escape、Back/Forward、refresh，以及 loading/empty/error/disabled/success。确认弹窗和提交后的持久成功回执是两个状态。

前端 gate 之前可以做一个最小、可丢弃的 auth/RBAC contract spike，以避免多角色 fixture 失真；不得在此阶段扩展成持久业务后端。

视觉验收按 header、primary content、decision/action region、overlay 和 footer/full height 分区。聚合 SSIM 或大面积白背景不能掩盖关键局部差距。

## 后端与外部系统

后端按不变量而不是页面顺序实现：canonical identity → session/tenant/role scope → server authority → explicit state machine → idempotency/concurrency → persistence/migration → local external adapters。

每个 P0 mutation 至少覆盖 valid、invalid、duplicate、stale、foreign owner/tenant、unauthorized role、restart 和 migration。协作型网站还要覆盖 visibility、query totals/facets、attachments 和 exactly-once audit causality。

邮件、支付、物流、对象存储等默认处于显式本地模式。真实集成必须完整配置、主动启用并 fail closed；本地成功不能显示成真实外部投递或资金流。

`runtime_remote_requests: forbidden` 描述的是被 release 验收的离线运行 profile。若另行启用 SMTP 等真实 adapter，那是新的外部效果 profile，必须有独立配置与证据，不能继续引用默认 `network remote=0` 的离线验收结论。

## 状态失效与 trajectory

`clone.yaml` 使用原始字节 SHA-256。任何 manifest 修改都会让已有 gate 全部变成 `stale`，必须从 source 重新跑。每个 gate 还会记录输入树 fingerprint；即使 manifest 不变，资源、fixture、前端或后端输入变化也会使该 gate 及下游失效。

`paths.candidate_excludes` 只能列明确的可变 runtime 状态目录。backend 与 release 会自动 fingerprint 整个 `candidate_root` 减去这些显式 exclusions，不能靠漏写某个生产文件来保留旧的 `BACKEND_READY`/`ACCEPTED`。目录 fingerprint 忽略 `__pycache__`、pytest/mypy/ruff cache 和 `.pyc/.pyo`，但遇到 symlink、junction 或 reparse point 会 fail closed。

trajectory 是 append-only JSONL hash chain，state 另外保存 count/head anchor。建议从第一轮就记录用户反馈、证据边界、scope 取舍、关键决定和 focused test 结论：

```powershell
"将可浏览与可提交分开计数" |
  clawbench-offline-clone record --site materials/example `
    --kind correction --message-stdin
```

harness 在写入内容或其 hash 之前拒绝密码、token、Cookie、OTP、支付卡、非保留域邮箱、地址、原始 request body 和低熵 secret hash，也会检查多层 percent-encoded 表示。命令输出只保存退出码、耗时和 stdout/stderr 字节数，不保存原文或原文 hash。state、trajectory、锁、pending intent、scope、gate inputs 与验收 artifact 的路径必须互不覆盖；trajectory 与锁文件拒绝 symlink/reparse/hardlink。

## 时间与 token 的默认分配

- 60%：从常见入口到目的成功的主线和视觉一致性；
- 20%：身份、权限、失败、重试和恢复；
- 10%：按 layout/entity/interaction signature 选出的跨模板广度；
- 10%：最终 accessibility、视觉收敛和完整回归。

先停止 P2 深挖，再削减 P1；P0 数据、安全或核心主线错误不能降级成 placeholder。不要用大量 synthetic records 增加路由数量，应复用 data-driven template，并让 sparse evidence 保持 sparse。

## 验收顺序

实现阶段只跑静态和 focused tests。功能完成后先安排不了解预期结论的独立审计，再 code freeze。release gate 要求六类证据：

- `visual`：冻结在 `checkpoints.json` 的 source path/SHA、viewport、comparison region、`pixel-mae-similarity-v1` 和非零 threshold；harness 从绑定图像重算分数；
- `browser`：真实 journey/step trace；
- `network`：请求日志、派生的 remote/local 判定、失败数和 required runtime census；
- `migration`：stateful 项目必须在副本上给出 pre/post inventory 与 scenario；只有 manifest 明确 `state_model: stateless` 才可 N/A；
- `independent-audit`：与其他 producer 不同的命令和实现边界，零未关闭 P0/P1；
- `full-suite`：本轮完整 discovery 的逐项结果，`passed == discovered` 且 `failed == 0`。

每个 artifact 都绑定当前 manifest SHA、release attempt 和 producer command。执行器在每条 release 命令前后做 snapshot：命令必须当场只创建/修改分配给自己的 artifact，随即校验，后续 producer 不能覆盖它。raw artifacts 必须位于 artifact root、是单链接普通文件，并绑定 bytes、SHA、MIME、subject IDs；summary counts 必须由 raw records 重算。报告必须包含本轮实际测试数、运行模式和剩余 P2/omit 边界，不能复用旧数字。

“独立”至少是不同命令、不同实现、无共享 helper 的进程级边界；它不等同于组织级第三方审计，交付时必须如实说明。

Amazon adapter 是一个 reference case，不是通用模板。新网站继承 harness、schema、invariant IDs 和流程；不继承 Amazon 的 ASIN、catalog、renderer、store、route 或 SQLite 迁移历史。
