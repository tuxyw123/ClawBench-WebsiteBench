# Amazon 离线 Clone 案例复盘：从“测试通过”到目的驱动的可信复刻

## 文档目的与边界

这份案例复盘面向后续离线网站 clone 工程。它总结本轮 Amazon 重建中真正提高质量的动作、造成返工的动作、用户比实现者更早发现的问题，以及实现者在后期独立审计中才发现的问题，并将这些经验整理为可复用的阶段方法和 harness 约束。

它不是 Amazon 当前实现说明的重复，也不把当前候选描述成 Amazon 整站 1:1 复制。当前有界验收范围以 [clone.yaml](../materials/amazon/clone.yaml)、`materials/amazon/scope/*` 和 harness 生成报告为准；[Amazon README](../materials/amazon/README.md)、[重建上下文](../materials/amazon/REBUILD_CONTEXT.md) 和 [前端路由矩阵](../materials/amazon/FRONTEND_ROUTE_MATRIX.md) 保存更广的历史证据与 backlog，不能覆盖新 adapter 的证据等级或 gate 结论。

仓库没有保存早期逐轮 conversation、完整 agent message 或工具流；现有重建上下文明确说明旧 trajectory 是事后整理。因此，本案例结合本轮会话中用户给出的反馈顺序和仓库可验证事实进行复盘，而不是伪装成一份完整原始 transcript。后续 harness 必须从第一轮开始保存真实 trajectory。

## 一句话结论

本轮后半程能够收敛，关键不是实现了更多页面，而是把目标改成了：

> 先按网站原始目的冻结一组有价值的页面、状态和交互；下载这组范围的完整资源闭包；用确定性 fixture 完成全部前端；再用服务端不变量连接核心业务；最后通过视觉、功能、安全和迁移 loop 分别验收。

旧候选失败的顺序恰好相反：先围绕一个 Samsung T7 任务搭建后端和 verifier，再用合成商品扩大表面范围，最后用实现者自己设定的宽松指标证明实现者自己的结果“通过”。

## 1. 案例转折过程

### 1.1 窄任务 pilot 有价值，但不等于网站 clone

最初 pilot 严格实现了“进入 External SSD Best Sellers、打开排名第二的 Samsung T7、选择数量 2、加入购物车”这一条链路。会话隔离、严格 POST 合同、SQLite 持久化和幂等性都是真实价值。

问题不在于先做了一条 vertical slice，而在于后续把“单任务做得严谨”误当成了“网站主要目的已经建模”。当用户期待的是人类与 Amazon 的购物体验时，一条 T7 链路只能证明一个测试任务可完成。

### 1.2 Synthetic breadth 制造了虚假的完成感

旧候选随后扩成 200 个商品、10 个部门和 20 个分类，但商品只复用少量生成 sprite，信息密度、商品类别、筛选项和结果数量都与源站不同。代表性搜索甚至是源站 `1–24 of 467` 对 clone `1–6 of 6`。

旧 Gate 3 虽然报告 `100/100` 状态通过，实际只有 24 个状态参加 direct visual，44 个只是没有通过阈值的 structural diagnostics，32 个完全不可比较；关键阈值最低只有 SSIM `0.18`、edge F1 `0.08`。这证明“矩阵行数”和“测试通过数”都不是 fidelity 的代理指标。

### 1.3 删除旧实现是必要的止损

用户判断旧结果与源站差距过大后，要求删除已有生成内容，只保留对重建有帮助的部分。最终保留的是：

- GET-only 源站证据及保护/不可用边界；
- 脱敏、摘要、完整性检查和资源验证工具；
- 任务合同及其严格终止语义；
- 与 Amazon 无关的通用 WebsiteBench 基础设施。

旧前端、synthetic catalog、FastAPI 候选和 Gate 2–4 派生物没有继续作为新实现基线。这个决策比继续修补错误结构更节省总成本。

### 1.4 用户把工作顺序重新校正为资源、前端、后端、loop

用户明确要求：先下载源站需要的资源，搭好前端，再实现后端，最后反复 loop。此后真正有效的里程碑也按这个方向收敛：

1. 当前源站资源和内容事实先冻结；
2. 首页、搜索、PDP 和公共入口先完成布局及交互；
3. 后端逐步接通认证、购物车、结账、订单和邮件；
4. 浏览器对照、功能回归和独立审计不断修正。

需要保留一个重要限定：“下载完全”必须解释为**冻结范围内的资源闭包**，而不是试图下载整个动态网站。

## 2. 最终可验证事实及其正确解读

| 维度 | 最终事实 | 正确解读 |
|---|---:|---|
| 资源 | 四组有界来源清单 452 条；补入 2 条单独举证的 runtime 映射后为 454 个 required logical pairs；runtime 已清理为 454 个物理文件 | 来源记录、逻辑映射和物理文件必须分别计数；未引用文件不应成为正向分母 |
| 首页 | 27 个冻结模块、7 条 rail、157 个唯一商品记录 | 内容密度和顺序有来源依据，不代表每个商品都有完整 PDP |
| 商品身份 | 191 个服务端已知商品 | 表示路由和商品身份可达，不表示全部可购买 |
| 富证据 PDP | 14 个唯一 rich-evidence PDP；143 个 homepage 商品保持 sparse | sparse 页面不虚构价格、评分、offer 或购买能力 |
| 服务端报价 | 49 个 ASIN 具有严格报价 allow-list | 只有被报价的完整选择才可进入交易 |
| 部门浏览 | 五个部门共 133 张卡片，其中 39 张可购 | “多品类可浏览”与“多品类可购买”分开计数 |
| Deals | 29 个严格独立的 Deals 报价 | 普通可购商品不会自动被误标为 Deal |
| 评论 | 191 个商品均有本地评论路由；13 个有来源 aggregate 或明确零评论证据 | 本地评论能力不能伪装成来源评论事实 |
| Compare | 39 个 eligible ASIN，保留完整规格且限制同一来源 family | 数量来自报价与 taxonomy，不是固定白名单 5 项 |
| 支付 | approved card、declined card、approved bank 三种本地沙箱场景 | 不收集 PAN、expiry、CVV，也不代表真实支付连接 |
| 邮件 | `LOCAL_ONLY`、`SMTP_PENDING`、`SMTP_SENT`、`SMTP_FAILED` 四态 | 默认离线；只有显式完整配置才进行真实 SMTP 投递 |
| 回归 | 最终完整 discovery 为 `330/330` | 证明当前合同回归通过，不提升任何缺失的源站证据等级 |

这些分母保持分离，是当前版本比旧候选可信的核心原因。后续 clone 必须始终分别报告：known、reachable、rich、purchasable、comparable、source-verified 和 locally simulated。

## 3. 真正有效的动作

### 3.1 将证据等级作为一等数据

当前材料将事实区分为 `current-direct`、`historical`、`inferred`、`unavailable`，并进一步区分完整 PDP、card-only 和 default-offer 等边界。

例如，20 个 current search card 只证明卡片上的标题、ASIN、默认 USD 价格、显示型 rating/review 文案、图片、部门上下文及 Add to cart 控件；它们不证明完整 PDP、非默认规格、seller、inventory depth、delivery 或 returns。实现只开放这些卡片的默认空选择报价，没有从一个卡片推导完整商业世界。

这个模式比“尽量填满页面”更重要，应直接进入通用 evidence schema。

### 3.2 将资源下载做成可校验闭包

基础、Deals、Lists 和 search-commerce 四组有界来源清单分别验证 410、10、12、20 项，共 452 条。将通用 harness 反套到真实 runtime 后，又发现顶栏 `nav-sprite.png` 的逻辑别名和任务 fixture 使用的 historical T7 主图没有进入统一闭包；补入这 2 条单独举证的映射后，required logical pairs 为 454。反查还发现 2 个未引用 legacy runtime 文件，清理后物理 runtime 同样为 454。这个过程说明“已下载来源文件”“运行时需要的逻辑路径”和“目录里的物理文件”绝不能共用一个数字。

资源验证不仅检查文件存在，还检查 bytes、MIME、尺寸、路径、SHA-256、source/runtime 镜像一致性、物理身份别名、未入 manifest 的引用及 no-remote-runtime 规则。`referenced_by` 只是声明；最终仍需浏览器 network census 证明实际页面资源请求闭包。

资源闭包应满足：

```text
required = downloaded = verified = referenced
missing = 0
hash_mismatch = 0
remote_runtime = 0
```

这比仅保存截图或仅检查图片能加载更可靠，也能避免后期 CSS 已完成却因漏下载 hover、variant、overlay 或 lazy-load 资源而返工。

### 3.3 先用 fixture 完成前端状态，不让后端决定布局

首页最终按来源顺序冻结 27 个模块和 157 个商品记录；PDP、搜索、Deals、抽屉、hover 菜单、autocomplete、dialog 和 cart recommendation 都先解决页面结构和交互状态，再接业务数据。

用户指出窄窗口应像真实浏览器一样出现原生横向滚动，而不是被 clone 自动重排后，当前实现明确保留 1000px desktop canvas，并用回归测试禁止 `overflow:hidden` 或 viewport resize 偷换布局模型。这说明 viewport、UA、canvas 最小宽度和原生滚动本身也是来源语义。

### 3.4 将用户反馈转换为领域不变量

后期最有效的修正都不是一次性 UI patch，而是明确了跨页面不变量：

- 购物车行身份为 owner-scoped opaque `line_id` 加 canonical `selection_key`；
- 同一 ASIN 的 sibling variants 独立，完全相同选择才合并；
- option axes 不是笛卡尔积，完整 quote tuple 才是有效交易状态；
- 无可达报价的 option 原生禁用，跨连通报价组时最小化修复其他轴；
- cart、wishlist、compare、checkout 和 order 都重新使用同一服务端 quote；
- price、Verified Purchase、refund amount 和交易状态不接受客户端自报。

一旦这些规则成为数据库约束、服务端校验和测试合同，多个页面会同时正确，而不需要分别补丁。

### 3.5 将网站目的用于范围取舍

用户强调 Amazon 的目标是购物，因此发现、浏览、搜索、筛选、商品评估、规格、购物车、身份、配送、支付、订单及售后被视为主线；Prime Video 与该目的关系较弱，只保留静态占位。

这是正确的 tradeoff。离线 clone 的目标不是实现源站组织中的每一个业务，而是在有限预算内保留人类能感知的主要目的和关键失败语义。

### 3.6 独立审计晚于实现，但显著提升了可靠性

后期独立审计发现了 happy-path 测试没有覆盖的问题：legacy 支付入口、邮件状态账户枚举、LOCAL_ONLY 重启错误、SMTP replay 上限以及 rejected POST 日志泄密。每一项都被转化为负向回归。

这一经验应固化为流程：最终全量测试之前，必须有一个不了解实现意图的 adversarial audit，分别检查身份、所有权、迁移、重试、日志和外部服务边界。

## 4. 无效或边际收益过低的动作

### 4.1 在可靠视觉基线之前建设复杂架构

旧候选先建设双服务、SQLite、复杂 verifier 和大目录，再解决首页、商品密度和 PDP 几何。结果是后端越稳定，视觉重建成本越高。

首个版本只应有可重置 fixture server 和最少持久状态。只有前端矩阵冻结后，才应扩业务数据库和状态机。

### 4.2 用 synthetic catalog 假装广度

大量生成商品和少量复用图片能快速增加 route 数量，却会让卡片比例、标题长度、分类密度、价格格式和筛选分布全部偏离来源。它还会诱导后端为虚假商品实现无证据的 variants、reviews 和 offers。

正确加速方式不是造 200 条相似记录，而是按 layout signature 选择代表样本，并让没有完整证据的商品明确保持 sparse。

### 4.3 对受保护状态机械进行大矩阵采集

旧 Gate 1 对 20 个场景乘 5 个 viewport 进行采集，但首页、drawer 和 autocomplete 在所有 viewport 都受保护或空白。大量抓取没有转化成可用 golden。

后续工具应先 probe source availability，再将状态分类为 rich、partial、protected 或 expected error；只有 rich/partial 状态进入昂贵的全视口、full-page 和资源闭包阶段。

### 4.4 使用自指的通过标准

旧 verifier 由实现者同时定义，实现者可以通过降低阈值或把不可比较状态放入总体分母获得绿灯。后期一次 Deals 改进也曾让旧测试因品牌从不可点击文本变成有效链接而失败，说明部分测试锁定的是旧 markup，而非用户语义。

视觉 fidelity、DOM/interaction、业务不变量和安全边界必须是四类独立 gate；任何一类都不能替代其他类。

### 4.5 在长尾入口上过早实现深持久语义

Gift Cards、Sell、Registry 从空白入口升级为有意义本地页面是必要的，但完整 session-owned draft、搜索和私有详情的边际收益低于先修可购商品比例、规格依赖和 checkout。

长尾入口应先达到“非空、语义明确、返回路径可用”的 P2 标准，只有它进入网站主线或用户明确要求时才升级为深状态机。

### 4.6 在代码未冻结前反复执行全量验收

一次完整 discovery 运行时间明显高于 focused tests；其中一次在邮件安全审计发现新问题后不得不中止。合理顺序是静态检查、feature-focused tests、独立审计、code freeze、一次 full discovery、最后真实浏览器 smoke。

### 4.7 手工同步多份状态文档

商品数、可购数、资源数、邮件状态和测试总数曾在多份文档中反复同步。未来这些数据应来自唯一 machine-readable coverage ledger，并由报告生成器输出，避免文档比代码更早“完成”。

## 5. 用户先发现的问题与我们后发现的问题

| 谁先发现 | 问题 | 深层原因 | 最终纠偏 |
|---|---|---|---|
| 用户 | 首页文字、图片排版混乱且内容不完整 | 先用合成内容填页面，没有冻结真实模块顺序和密度 | 冻结 27 模块、157 商品记录及来源资源，按原尺寸反复对照 |
| 用户 | 窄窗口体验不像真实浏览器 | 把“响应式”当成普遍正确，忽略来源的固定 canvas | 保留 1000px canvas 和浏览器原生滚动 |
| 用户 | 商品太少且几乎只有硬盘 | 把一条 benchmark journey 当成网站目的 | 扩到五个部门并单列浏览/可购分母 |
| 用户 | 顶栏、Gift Cards、Sell 等入口为空 | route 存在被误计为页面完成 | 主要入口变成 live 或 meaningful shallow page，低价值生态明确 placeholder |
| 用户 | 登录、注册、验证码和 hover 语义缺失 | 只实现可见壳层，没有流程图和交互状态矩阵 | 两阶段登录、未知邮箱注册 handoff、验证、恢复和 hover/focus menu |
| 用户 | PDP 不能选择规格且没有评论 | 商品身份模型只有 ASIN，没有完整选择和评估阶段 | quote matrix、来源 aggregate 与本地评论分离 |
| 用户 | 同 ASIN 不同规格在购物车互相覆盖 | 数据表以 ASIN 作为行身份 | opaque line ID 和 canonical selection key 贯穿所有交易快照 |
| 用户 | 页面先允许无效规格组合再显示不可购买 | 将 option axes 错当成可自由组合 | 从 complete quote tuples 投影可达性并最小修复 |
| 用户 | Compare 少且不区分规格 | 固定 5 项名单和 ASIN-only identity | 从报价和 taxonomy 动态导出 39 项并保留规格 |
| 用户 | 只有单一成功卡，没有拒付、重试和多方式 | 只为 happy path 建状态 | 三种确定性沙箱场景和 decline/retry 状态机 |
| 我们后发现 | legacy `test-card` 仍能创建新尝试 | 为迁移保留旧枚举时，没有区分 read compatibility 与 public write contract | 旧值只可读取，公开新写入强制经过当前 allow-list |
| 我们后发现 | 密码找回状态可枚举邮箱 | 已知邮箱暴露真实 SMTP 状态，未知邮箱显示伪 queued | OTP 验证前已知/未知页面字节级一致且 crafted retry 无副作用 |
| 我们后发现 | LOCAL_ONLY 重启会留下失真的 pending/failed | mail transport mode 与持久状态迁移分离设计 | 启动时原子 reconcile，并修复仍需验证的本地 OTP |
| 我们后发现 | SMTP claim/replay 可突破重试预算 | retry、claim 和 startup replay 各自检查不同条件 | 所有入口统一三次 delivery ceiling，耗尽后稳定失败 |
| 我们后发现 | rejected POST journal 保存 secret 或其可枚举 hash | 认为 hash 等于脱敏，忽略低熵 OTP/密码字典攻击 | 非精确白名单合同统一保存固定 sentinel 及 sentinel hash |
| 我们后发现 | 文档和测试可能固定错误实现 | 把当前输出当作 oracle，而不是来源和用户语义 | 报告数据生成化，并区分行为测试与 markup 诊断 |
| 我们后发现 | 452 个来源记录、454 个 runtime 映射和 456 个物理文件曾被混成“资源数” | 没有区分来源证据、逻辑路径和目录库存 | harness 反向闭包、补 2 条映射、清 2 个死文件，并分别报告三个分母 |
| 我们后发现 | 主 buy box 完成时，配送、退货、其他卖家和购物车推荐仍可能很浅 | 以核心 CTA 代替完整页面解剖 | 对 decision/action 周边建立独立 checkpoint；按 P0/P1 价值决定 live、shallow 或明确 omit |
| 我们后发现 | unittest release 可以被错误解释成真实浏览器验收 | gate 只看退出码，没有要求验收证据种类 | release 必须产出 visual、browser、network、migration、independent-audit 和 full-suite 结构化证据 |
| 我们后发现 | source coverage 一度从 candidate fixture 反推 numerator | “已经实现”污染了“源站证明过什么” | source gate 禁止读取 candidate；只冻结空 numerator 的分母，release typed evidence 再填 numerator |
| 我们后发现 | visual producer 可以在看到结果后把 threshold 降到 0 | 视觉 oracle 没在 source 阶段冻结 | checkpoint 预先绑定 source path/SHA、viewport、region、metric 和非零 threshold，harness 从图像重算 |
| 我们后发现 | artifact 可自报另一个 producer、伪造 counts 或用无关 raw 文件背书 coverage | 将结构化 JSON 本身误当成独立证据 | 每条 release command 前后做路径 snapshot；即时校验因果、raw hash、record counts 和 witnessed subject IDs |
| 我们后发现 | 手写 release inputs 漏掉一个生产文件后，旧 ACCEPTED 仍可能保持 current | 把输入枚举当成生产闭包 | backend/release 自动 fingerprint 全 candidate，仅允许显式审查过的可变 runtime exclusions |
| 我们后发现 | “独立审计”仍调用主 adapter 的相同 helper | 只分了 command ID，没有分验证实现 | Amazon 新增 stdlib-only 独立审计器；边界诚实标为进程/实现级，不冒充第三方审计 |
| 我们后发现 | 扩展名、字节相同或文件存在不足以证明资源闭包 | 字体/图片/SVG 内容和 hardlink identity 未纳入语义 | 校验 magic/解码/SVG active content、CSS/SVG 外链、single-link physical identity，并让身份变化使 gate stale |
| 我们后发现 | 默认离线 network=0 与可选真实 SMTP 被写成同一个 runtime claim | 没区分验收 profile 与外部效果 profile | release 只验收 LOCAL_ONLY；真实 SMTP 必须显式另启配置和证据，不能借用离线结论 |

## 6. 返工的根因

### 6.1 没有先定义网站目的图

如果一开始就把 Amazon 主线画成：

```text
发现 → 搜索/筛选 → 商品评估 → 有效规格 → 购物车/Buy Now
     → 登录/注册 → 地址 → 配送 → 支付 → 订单 → 售后
```

就能更早识别 variants、reviews、cart identity、delivery 和 decline retry 是核心，而 Prime Video、广告或深层 Sell 工作流不是第一阶段目标。

### 6.2 没有先定义跨页面领域身份

ASIN 不是交易行的完整身份。商品、完整选择、报价、卖家或履约上下文共同决定交易对象。因为这一模型建立过晚，cart、wishlist、compare、checkout 和 order 都经历了迁移或补丁。

任何新网站在写持久表之前，都必须回答：什么字段组合才是用户看到的“同一个东西”？

### 6.3 Evidence、UI 和业务事实曾混在一起

来源卡片、完整 PDP、可购买报价、本地评论和本地支付模拟曾容易被视为同一类“商品数据”。最终将它们拆开后，虚构事实和错误传播显著减少。

### 6.4 验收分母不透明

旧版 `100/100` 隐藏了 direct、structural 和 unavailable 的差别。任何覆盖报告都必须展示各自分母，不能只给一个总百分比。

### 6.5 缺少早期威胁模型和独立审计

邮件“能发”、支付“能成功”、journal“有记录”都不等于语义正确或安全。账户枚举、低熵 hash、owner scope、legacy write 和 crash replay 只有在恶意或异常视角下才暴露。

### 6.6 没有保存真实 trajectory

无法可靠回放哪一次用户反馈导致哪一项设计变化，也难以计算真正浪费在哪个阶段。后续 harness 必须持久记录人类反馈、scope 决策、browser actions、commands、diff、测试和 gate 结论。

## 7. 后续统一流程：范围 → 资源 → 前端 → 后端 → Loop

### 阶段 S：冻结目的和范围

这一步只做决策，不写业务实现。

1. 用一句话写出网站原始目的。
2. 定义 1–3 条核心用户主线及成功、失败、重试状态。
3. 建立 route × state × viewport 矩阵。
4. 给每一行标注 P0/P1/P2、证据等级、资源需求、frontend-done 和 backend invariant。
5. 明确 placeholder、omit 和不得声称完成的部分。

退出条件：范围有冻结分母，未知或不可观察状态没有被伪装成实现要求。

### 阶段 R：下载冻结范围的完整资源闭包

1. 先 probe 可用性和区域状态，再进行正式采集。
2. 固定时间、市场、币种、配送区域、认证状态、viewport 和 UA。
3. 下载 initial、full-page lazy-load、carousel、hover、focus、drawer、variant、gallery 和 overlay 会使用的资源。
4. 建立 immutable manifest 和 source-to-runtime 映射。
5. 校验文件内容、尺寸、MIME、hash、引用和无远程依赖。

退出条件：每个 required frontend row 都有已验证资源，或有明确且不冒充 direct fidelity 的例外。

### 阶段 F：用 fixture 完成所有计划前端

1. 使用只读 fixture 提供 loaded、empty、loading、error、hover、focus、dialog 和 selected 等状态。
2. 先实现 shared shell，再按主线实现页面族。
3. 匹配内容密度、模块顺序、图片 crop、字体、间距、原生 overflow 和完整页面高度。
4. 检查 click、keyboard、touch、Escape、Back/Forward、refresh 和焦点恢复。
5. 每个页面按相同 viewport 保存 source/clone、landmark geometry、region diff 和人工结论。

退出条件：计划内前端矩阵全部完成，0 broken image、0 remote runtime、0 未解释 console error，且后端尚未迫使前端改变布局。

### 阶段 B：按领域不变量接后端

后端不按页面顺序实现，而按依赖顺序实现：

1. canonical entity identity 和完整 variant relation；
2. session/account/owner scope；
3. server-owned quote、库存或可操作 allow-list；
4. mutation、idempotency 和状态机；
5. retry、expiry、lockout 和 failure semantics；
6. persistence、migration、backup 和 restart；
7. LOCAL_ONLY 外部服务 adapter 及显式 fail-closed 配置。

退出条件：客户端无法伪造金额、身份、来源状态或跨 owner 对象，核心状态机的失败和重试与成功路径同等完整。

### 阶段 L：集成 Loop 和最终 Gate

每条核心主线至少覆盖：

- happy path；
- invalid transition；
- duplicate、retry 和 idempotency；
- cross-session、foreign ID 和 account merge；
- refresh、restart 和 legacy migration；
- desktop、narrow canvas 和真实交互；
- network、console、broken image 和 remote request；
- evidence claim audit；
- privacy/security adversarial audit。

执行顺序应是 focused regression、独立审计、code freeze、完整测试、真实运行服务 smoke。若审计发现 P0，必须返回相应后端或前端阶段，而不是只补最终测试。

## 8. P0 / P1 / P2 反模式与纠偏规则

### P0：数据、所有权、安全或核心主线错误

典型反模式：

- 用 product ID 代替完整规格身份；
- 将 option axes 展开成任意笛卡尔积；
- 接受客户端价格、seller、Verified Purchase 或 refund amount；
- 将 browseable 商品默认为 purchasable；
- auth/mail 响应泄漏账户存在性；
- journal 保存密码、OTP、支付数据或低熵 secret hash；
- retry/replay 无统一上限，mutation 不幂等；
- 为读取 legacy 数据而继续开放 legacy public writes。

纠偏规则：完整 selection key、server quote allow-list、opaque owner-scoped IDs、统一状态机、allowlist logging、负向测试和 migration test 必须在发布前全部关闭。P0 不能以 placeholder 或文档说明豁免。

### P1：最大体验差距和主要辅助语义

典型反模式：

- 用 synthetic catalog 填充视觉密度；
- 顶栏、筛选、评论、Compare、配送或退货入口为空；
- 混用市场、币种、登录态和 viewport；
- 不区分 source-direct、historical 和 inferred；
- 用整体相似度掩盖 header、商品卡或 buy box 的局部错位；
- 擅自将固定 canvas 改成自认为更好的响应式布局；
- 只测 click，不测 hover、focus、keyboard 和 scroll。

纠偏规则：purpose-driven route matrix、当前统一 capture、代表性 layout signature、区域级 visual gate、meaningful local navigation 及透明分母。P1 按“最大人类可感知差距”排序，而不是按开发者最容易实现的功能排序。

### P2：长尾、效率和维护性问题

典型反模式：

- 为所有长尾入口实现深后端；
- 在代码冻结前反复跑完整 suite；
- 手工将同一数字同步到多份文档；
- 对已知 WAF 空白状态做完整采集矩阵；
- 把某个站点的大型业务实现直接复制成通用 infra；
- 不保存真实 conversation 和决策轨迹。

纠偏规则：使用 shallow-live/placeholder 合同、focused-test registry、机器生成 coverage 报告、probe 后自适应采集、adapter 分层和 append-only trajectory log。P2 可以明确延期，但必须可见，不能伪装成已完成。

## 9. 时间与 token 的 Tradeoff

优先级不应由页面数量决定，而应同时考虑：

- 是否位于网站目的主线；
- 用户到达频率和失败严重度；
- 是否改变持久数据或后续状态；
- 是否有可靠来源证据；
- 是否能通过一个领域不变量同时修复多个页面；
- 捕获、实现和验证成本；
- 是否可以安全降级为 shallow page 或 placeholder。

推荐停止条件是：

1. P0 为零，核心主线成功、失败和重试均可完成；
2. 最大的 P1 人类可感知差距已经关闭；
3. 所有核心入口为 live 或明确、诚实的本地边界；
4. P2 有清晰 placeholder/omit 说明；
5. 资源、前端、后端和测试分母透明；
6. 不再以实现边际收益很低的长尾功能换取“页面更多”的数字。

Amazon 中 Prime Video 只占位就是正确例子；早期 200 个 synthetic 商品则是错误例子。

## 10. 对通用 Harness 和 Skill 的约束

后续 infra 应复用这次形成的**过程和不变量**，而不是复制 Amazon 业务代码。

### 10.1 应当复用的基础能力

- source availability probe 与 GET-only capture；
- route/state/viewport 矩阵 schema；
- evidence tier 与 provenance ledger；
- asset manifest、hash、dimensions、reference 和 no-remote verifier；
- fixture-state frontend server；
- exact-viewport screenshot、landmark 和 region diff runner；
- canonical entity/variant identity primitives；
- owner scope、opaque ID、idempotency 和状态机测试模板；
- LOCAL_ONLY 外部服务 adapter；
- focused、migration、security、browser 和 full-suite orchestration；
- machine-generated coverage report；
- append-only human/agent/browser/diff/test trajectory。

### 10.2 每个新站点必须重新提供的 adapter

- 网站目的和核心 journeys；
- canonical route family；
- source state extractor；
- 页面组件和布局 signature；
- 商品、内容或业务实体 schema；
- 有效选择、报价或状态转换关系；
- 站点特定 owner、权限和失败语义；
- P0/P1/P2 取舍。

### 10.3 不可复制的 Amazon 实现

当前 Amazon 候选已经是一个大型站点特定实现：`store.py` 约 7,400 行、`render.py` 约 4,400 行、`server.py` 约 4,000 行，完整 Python 业务代码约 23,000 行。它包含 Amazon ASIN、购物车、Deals、Lists、Compare、checkout、邮件及订单状态，不应成为下一个新闻站、社交站、SaaS、预订站或内容站的模板。

直接复制这些文件会同时复制：

- Amazon 的实体模型和错误假设；
- 为本轮历史迁移保留的兼容逻辑；
- 单进程 SQLite 的规模边界；
- 与 Amazon route 和页面结构耦合的 renderer；
- 本轮反馈驱动形成、但不一定适合其他站点的深度。

正确做法是提取通用 contract 和 verifier，将 Amazon 放在一个 reference case 或 adapter 中。新站点从空业务目录开始，只继承 harness，不继承 Amazon catalog、route、schema、renderer 或 store。

## 11. 最终工程戒律

1. “所有资源”始终是冻结 scope 的完整闭包，不是整个网站。
2. 先目的和矩阵，再资源；资源闭包后先前端，再后端。
3. known、reachable、rich、purchasable、comparable 和 source-verified 必须分别计数。
4. option 列表不是有效交易集合；有效集合是来源支持的完整 tuples。
5. 在建立任何持久表前定义用户所见对象的完整身份。
6. 前端视觉、功能正确性、安全性和源站证据是独立 gate。
7. 测试通过不能提升 evidence level。
8. 外部邮件、支付和物流默认安全本地模拟，真实连接必须显式启用并 fail closed。
9. 用户反馈应转化为矩阵项、领域不变量和负向回归，而不是一次性页面补丁。
10. 最终 full suite 之前必须进行独立 adversarial audit。
11. 保存真实 trajectory，禁止用事后叙事冒充原始 conversation。
12. 复用流程和 infra，不复制 Amazon 业务代码。
