# claw-bench-v2 live-site inventory 与离线 clone 执行提示

## 结论与口径

本仓库已将所需事实冻结在
`websitebench/corpora/claw-bench-v2/live-site-inventory.json`。它包含平台、
origin、129 个 task 的 instruction、终止请求 contract、时间限制和非敏感
`extra_info` 元数据，因此执行 Prompt 不需要另一份 `claw-bench-v2`
checkout。

该文件的 provenance 指向原始 `claw-bench-v2` 提交 `5fa5e74` 中由上游
`test-cases/v2` 生成的 `website-clone/task-inventory.json`，并记录：

- 129 个已发现 task；
- 61 个平台级 clone 单元；
- 62 个一方 site/host；
- 文档目标 130 题，因此保留 `-1` 的上游 artifact 缺口，不能生成占位题掩盖。

61 个平台与 62 个 host 的差别来自 Ticketmaster 同时使用
`ticketmaster.com` 和 `checkout.ticketmaster.com`。这里的 clone 单元按平台
合约聚合，不按 task 或 host 机械拆分。

原项目历史 `origin/agent/add-website-clones` 分支后来把 inventory 的
`verified_task_count` 写成 129，但这只是该分支自己的验证声明。它没有自动满足
本仓库当前 harness 要求的冻结 source truth、资源闭包、六类 release evidence、
Harbor NOP/oracle、隔离审计和人工浏览器验收，因此所有平台都应按当前流程重新
审计。

Amazon 不在这 129 个 V2 task 的平台清单内。它是本仓库的方法论 worked
example，不能被误加为第 62 个 V2 平台。

## 61 个平台

以下 batch 来自 inventory 的平台完整分组；batch 只是调度单元，不改变逐站
验收。

| Batch | 平台 key | 一方 site/host | Task 数 | Task IDs |
|---:|---|---|---:|---|
| 1 | capterra | capterra.com | 1 | 474 |
| 1 | edx | edx.org | 5 | 273, 1035, 1114, 1115, 1116 |
| 1 | etsy | etsy.com | 4 | 530, 531, 535, 536 |
| 1 | glassdoor | glassdoor.com | 1 | 468 |
| 1 | mailchimp | mailchimp.com | 1 | 486 |
| 1 | petfinder | petfinder.com | 3 | 815, 816, 817 |
| 1 | taskrabbit | taskrabbit.com | 1 | 47 |
| 1 | workable | workable.com | 1 | 571 |
| 2 | charity-village | charityvillage.com | 1 | 774 |
| 2 | eventbrite | eventbrite.com | 5 | 372, 560, 564, 1117, 1118 |
| 2 | goodreads | goodreads.com | 1 | 369 |
| 2 | habitica | habitica.com | 4 | 630, 631, 633, 634 |
| 2 | outschool | outschool.com | 1 | 608 |
| 2 | theordinary | theordinary.com | 3 | 1097, 1201, 1202 |
| 2 | todoist | todoist.com | 1 | 413 |
| 3 | coolors | coolors.co | 1 | 711 |
| 3 | greenhouse-codepath | greenhouse.com | 1 | 86 |
| 3 | imdb | imdb.com | 5 | 901, 902, 903, 904, 905 |
| 3 | ratemyprofessors | ratemyprofessors.com | 1 | 708 |
| 3 | target | target.com | 4 | 520, 521, 522, 523 |
| 3 | ticketmaster | ticketmaster.com; checkout.ticketmaster.com | 3 | 234, 1135, 1136 |
| 3 | tripadvisor | tripadvisor.com | 1 | 469 |
| 4 | eatthismuch | eatthismuch.com | 2 | 1095, 1113 |
| 4 | idealist | idealist.org | 1 | 776 |
| 4 | khanacademy | khanacademy.org | 5 | 1033, 1122, 1123, 1124, 1125 |
| 4 | semantic-scholar | semanticscholar.org | 1 | 247 |
| 4 | trakt | trakt.tv | 4 | 635, 638, 1137, 1138 |
| 4 | trustpilot | trustpilot.com | 1 | 470 |
| 4 | when2meet | when2meet.com | 2 | 274, 284 |
| 5 | airtable | airtable.com | 1 | 483 |
| 5 | coursera | coursera.org | 1 | 265 |
| 5 | github | github.com | 2 | 179, 180 |
| 5 | indeed | indeed.com | 1 | 91 |
| 5 | ravelry | ravelry.com | 5 | 600, 601, 602, 603, 1101 |
| 5 | simplify-jobs | simplify.jobs | 1 | 89 |
| 5 | tripit | tripit.com | 4 | 1093, 1139, 1140, 1141 |
| 5 | typeform | typeform.com | 1 | 487 |
| 6 | autoslash | autoslash.com | 1 | 763 |
| 6 | formswift | formswift.com | 1 | 598 |
| 6 | inmyarea | inmyarea.com | 1 | 533 |
| 6 | overleaf | overleaf.com | 2 | 215, 242 |
| 6 | redcross | redcross.org | 5 | 1130, 1131, 1132, 1133, 1134 |
| 6 | spirit-rock | spiritrock.org | 1 | 609 |
| 6 | untappd | untappd.com | 1 | 707 |
| 6 | weworkremotely | weworkremotely.com | 4 | 1045, 1144, 1145, 1146 |
| 7 | bark | bark.com | 1 | 735 |
| 7 | boardgamegeek | boardgamegeek.com | 3 | 607, 610, 1107 |
| 7 | freshdesk | freshdesk.com | 1 | 583 |
| 7 | handy | handy.com | 3 | 1065, 1120, 1121 |
| 7 | leetcode | leetcode.com | 1 | 266 |
| 7 | strava | strava.com | 5 | 596, 597, 1102, 1103, 1104 |
| 7 | styleseat | styleseat.com | 1 | 794 |
| 7 | vivino | vivino.com | 1 | 705 |
| 8 | beeradvocate | beeradvocate.com | 1 | 706 |
| 8 | change | change.org | 4 | 1088, 1108, 1111, 1112 |
| 8 | doodle | doodle.com | 4 | 500, 501, 502, 503 |
| 8 | g2 | g2.com | 1 | 475 |
| 8 | lowes | lowes.com | 1 | 737 |
| 8 | myrecipes | myrecipes.com | 3 | 1010, 1100, 1126 |
| 8 | substack | substack.com | 1 | 488 |
| 8 | webflow | webflow.com | 1 | 485 |

这些站点覆盖的主要能力不是简单换皮 CRUD，而包括电商/购物车、课程与练习、
活动报名、预约、求职申请、表单/内容创建、账户与生产力对象、评价/评分、收藏/
列表、社交发布、旅行计划、票务和文档协作。每个平台必须从其 task 集重新导出
目的、状态机和服务端不变量。

## 仅存在于实验归档的额外在线平台

`experiments/human-audit-2026-07-21/provenance.json` 还引用了不属于上述 129
个 V2 task 的旧/额外实验平台：Craigslist、PurelyMail、Trello、Zotero、
Plooto、Insureon、Rover，以及 ASPCA/Adopets。它们用于历史实验或额外 episode
分析，不应自动加入 V2 clone corpus。若目标是复现整个实验归档而非 129 题 V2
集合，应另建 inventory 并独立冻结范围。

## 可直接使用的完整 prompt

将下方 prompt 原样交给 Codex；只需在首行把 `<BATCH>` 改成一个 batch 编号或
`all`。建议先做一个 batch，并在方法审计后再扩展。

```text
你正在当前仓库根目录工作；该目录必须包含 pyproject.toml、PROJECT.md、
project/plan.json、skills/、websitebench/ 和 materials/。目标是使用本仓库
自带的 claw-bench-v2 live-site inventory，按照当前 Amazon worked example
的方法，重建严格离线、可重置、可审计的 platform-level site adapters。
处理范围：<BATCH>。

必须使用并完整遵循：
- skills/build-offline-site-clone/SKILL.md
- skills/build-offline-site-clone/references/corpus-expansion.md
- PROJECT.md 与 project/plan.json
- docs/offline-clone-harness.md
- docs/offline-clone-amazon-case-study.md
- materials/amazon/clone.yaml
- materials/amazon/scope/*
- materials/amazon/README.md 与 materials/amazon/REBUILD_CONTEXT.md
- websitebench/corpora/claw-bench-v2/live-site-inventory.json
- 如进入 Harbor：docs/harbor-fullstack-benchmark.md 和 skill 的
  references/harbor-benchmark.md

不得要求、搜索或假定存在同级 claw-bench-v2 目录。只有当用户另外提供上游
checkout 并明确要求 provenance 复核时，才可把它作为只读补充证据；默认执行
必须仅依赖当前仓库与允许访问的公开源站。

Amazon 只提供方法和证据边界，不提供新站点的领域模型。严禁把 ASIN、Amazon
catalog、quote matrix、购物车、SQLite 历史或 Amazon adapter 代码机械复制到
不适用的平台。必须复用的是：
1. 目的驱动的 scope freeze；
2. direct / partial / historical / inferred / unavailable 证据分级；
3. 冻结范围内的完整本地资源闭包；
4. fixture-first frontend，再实现必要 backend；
5. 服务端权威、确定性 reset、显式本地外部服务 adapter；
6. source、assets、frontend、backend、release 顺序 gate；
7. visual、browser、network、migration、independent-audit、full-suite 六类
   release evidence；
8. 不用静态测试冒充 Harbor 校准或人工浏览器验收。

一、建立事实源
1. 使用 Python 3.11+。若当前 checkout 尚未安装，先在仓库根目录运行：
   python -m pip install -e ".[dev]"
   然后运行：
   $env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
   python -m clawbench.project.cli validate
   python -m clawbench.project.cli status
2. 检查当前仓库的 git status，保留所有无关 dirty work，不覆盖并行改动。
3. inventory 只读取：
   websitebench/corpora/claw-bench-v2/live-site-inventory.json
   先运行：
   python -m pytest tests/offline_clone/test_live_site_inventory.py -q
   若它缺失、解析失败或计数不一致，停止 corpus 扩容并报告仓库不完整；不得去
   猜测另一个机器上的绝对路径。
4. 固定分母：documented=130、discovered=129、platforms=61、first-party
   sites/hosts=62、missing upstream artifact=1。不得发明第 130 题。
5. 以 docs/claw-bench-v2-live-site-clone-plan.md 的 8 个 batch 为调度清单。
   每个 task 必须映射到且只映射到一个 platform adapter。
6. inventory 只保留 `extra_info` 的路径/说明，不携带地址、凭据、个人资料或
   文件正文。需要这些值的 task 必须标记为 fixture-unavailable，使用明确的
   benchmark-owned synthetic seed，或等待获得经过授权且脱敏的 fixture；
   不得从 instruction 猜测个人信息。

二、按平台而不是按 task/host 建 clone
1. 同平台共享产品壳、身份/状态模型和 reset 边界的 task 合并到一个 adapter；
   task-specific 目标后续放到 Harbor instance overlay。
2. 一个公司下若是不同产品、不同身份或不同状态存储，不得只因同域名强行合并。
3. 同一平台跨多个一方 origin 时保留全部 origin。使用：
   clawbench-offline-clone init `
     --site-dir materials/<platform-key> `
     --site-id <platform-key> `
     --display-name "<platform display name> offline clone" `
     --source-url https://<primary-origin>/ `
     [--source-url https://<additional-first-party-origin>/]
   如果 console script 不可用，等价调用：
   python -m clawbench.offline_clone.cli init <相同参数>
4. 初始化不等于完成。立即把 placeholder gate 改成站点真实命令。

三、每个平台先写 requirement card 和冻结 scope
1. 在 project/plan.json 登记稳定 work item、owner、priority、acceptance、
   dependencies 和 evidence 路径。
2. 从该平台全部 task 导出：
   - actor + core object + success verb 的一句话目的；
   - task ID、instruction family、终止语义、可观察成功条件；
   - entry → discover → decide → configure → commit → confirm → recover →
     aftercare 状态图；
   - P0/P1 success、validation、duplicate、stale、foreign owner/tenant、
     unauthorized role、refresh、retry、reversal/aftercare；
   - route × state × viewport × auth/role matrix；
   - 服务端语义不变量、seed/reset、持久化与迁移；
   - 明确 non-goals、版权/再分发边界。
3. 为 scope/purpose.json、invariants.json、routes.json、journeys.json、
   checkpoints.json、claims.jsonl、coverage.json 写站点特定内容。
4. coverage 维护独立分母，不输出“整站完成率”。至少分开：
   source-direct/partial/historical/inferred/unavailable、P0/P1 frontend、
   visually comparable、success/failure/recovery journeys、backend invariants、
   viewable/mutable/durably verified entities，并补充站点特定分母。

四、冻结 source truth
1. 默认匿名、只读、GET/HEAD。不得执行真实付款、报名、提交、发帖、预约、
   申请、邮件发送或其他生产 mutation。
2. 记录 locale、currency、timezone、delivery region、auth、role、tenant、
   viewport、UA、feature flags、capture time 和重定向。
3. 对每个 task 做 source availability probe，再把状态标为 direct、partial、
   protected/unavailable 或 inferred；不可访问状态不得伪装成直接证据。
4. source verifier 只能读取 source/scope，不得读取 candidate_root、candidate
   fixture 或其模块。
5. checkpoint 在 source 阶段预先绑定 source image path/SHA、viewport、
   comparison region、metric 和非零 threshold。

五、关闭资源后再做视觉
1. 枚举冻结范围内所有图片、字体、icon、CSS、JS、响应和交互态资源。
2. 每个 required asset 记录 source/runtime path、bytes、SHA-256、MIME、
   dimensions、referenced_by、evidence level 和可选 source URL。
3. 本地保存允许再分发/研究使用的资源；敏感或不可再分发内容使用明确的替代
   策略并记录，不伪造来源。
4. 资源 gate 的退出条件必须是：
   required = downloaded = verified = referenced
   missing = corrupt = hash_mismatch = remote_runtime = 0
5. 浏览器 network census 必须证明 runtime 不访问远程图片、字体、API、遥测或
   源站。CSP 与静态扫描不能替代真实 network evidence。

六、fixture-first frontend
1. 先完成 shared shell，再按冻结 route/state/viewport 矩阵实现页面族。
2. 使用确定性 fixture server；除最小 auth/RBAC contract spike 外，不先建设
   持久业务后端。
3. 实现 loading、empty、validation、disabled、success、error、unauthorized、
   overlay、dialog、drawer、hover/focus、keyboard、Escape、Back/Forward、
   refresh、native scroll 和 breakpoint。
4. 对照时匹配 route、data、viewport、scroll 和 interaction state；先修几何/
   模块顺序/密度/crop/canvas，再修颜色与细节。
5. 每个关键页面分别检查 header、primary content、decision/action region、
   overlay、footer/full height，不能用大面积背景的总 SSIM 掩盖关键区域。

七、只实现冻结旅程需要的 backend
1. 按 canonical identity → session/tenant/role scope → server authority →
   explicit state machine → idempotency/concurrency → persistence/migration →
   local external adapters 的顺序实现。
2. 客户端不得自报价格、权限、所有权、状态或 verifier expectation。
3. 每个 P0 mutation 至少覆盖 valid、invalid、duplicate、stale、foreign
   scope、unauthorized、restart、migration；协作站补 visibility、query
   totals/facets、attachments 和 exactly-once audit causality。
4. 邮件、支付、对象存储、通知、物流等默认 local-only。真实集成必须显式开启、
   完整配置、fail closed，并使用独立验收 profile。
5. public 与 admin/reset 控制面分离；不要通过公共 endpoint 泄漏隐藏 fixture、
   verifier expectation 或 oracle。

八、逐 gate 验证，不跨站共享完成声明
对每个平台依次运行：
clawbench-offline-clone validate --site materials/<platform-key>
clawbench-offline-clone gate source --site materials/<platform-key>
clawbench-offline-clone gate assets --site materials/<platform-key>
clawbench-offline-clone gate frontend --site materials/<platform-key>
clawbench-offline-clone gate backend --site materials/<platform-key>
clawbench-offline-clone gate release --site materials/<platform-key>
clawbench-offline-clone status --site materials/<platform-key>
clawbench-offline-clone report --site materials/<platform-key> `
  --out materials/<platform-key>/artifacts/offline-clone/report.json

release 前先 focused tests，再独立审计，再 code freeze，再 full discovery。
release command 必须当场生成并绑定 visual、browser、network、migration、
independent-audit、full-suite artifact；raw records、hash、counts、producer
command 和 witnessed subject IDs 必须可重算。

九、Harbor 封装
1. 只有 site contract 稳定后才创建 harbor/sites/<site-id>。
2. 每个 task 作为独立 harbor/instances/<instance-id> overlay，复用 site，
   不复制 reference source。
3. Agent 只通过 Browser Use CLI 探索 reference；Playwright 与直接 HTTP 只在
   独立 verifier。
4. reference-first 顺序采集 expectation；candidate 检查时防止 proxy reference。
5. validate、validate-corpus、materialize 后，还必须真实执行 NOP、oracle、
   oracle repeat、隔离审计和人工浏览器 review。
6. 没有这些证据时，instance/site/corpus 不得标记 release-ready。

十、批次与最终交付
1. 一次只承诺一个有界 batch；每完成一个平台就更新 clone ledger 和
   project/plan.json，不能等全部结束后补写证据。
2. 平台若因登录、地区、反自动化、版权或源站漂移无法直接捕获，保留
   unavailable/partial 状态并继续可安全推进的部分；不要用 synthetic breadth
   填满。
3. 每个 batch 结束时核对：
   - task/platform/host/clone 数量；
   - 每个 task 恰好一个 adapter；
   - 每个 adapter 至少一个 task；
   - 所有 manifest/schema/gate 状态；
   - 当前测试、Harbor、人工 review 与 release gate 证据。
4. 最终报告必须列出：冻结范围、直接/结构性/不可用/推断证据、资源分母、
   frontend/backend 状态、reset、命令与测试、已知差异、版权边界、blocker、
   Harbor 校准、人工审核和 release readiness。
5. 若只完成代码或静态测试，明确写“未 release-ready”，不要把历史分支的
   verified 字段、旧 verification.json、截图或单元测试当作当前完成证据。
```
