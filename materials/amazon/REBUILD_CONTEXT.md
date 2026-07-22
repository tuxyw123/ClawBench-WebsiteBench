# Amazon clone 重建上下文

## 当前状态

2026-07-20 已重置旧 Amazon clone。随后从空目录创建了新的
`materials/amazon/clone/`；新候选没有恢复或复制被删除的旧实现。旧候选仍不可复用，
保留的历史输入包括：

- 2026-07-18 的 Gate 1 GET-only 源站快照；
- 两份较小的公开源站观察、一份任务合同观察；
- 源站抓取、脱敏、完整性检查与摘要工具及测试；
- `tasks/clawbench/dev-136-amazon-t7-best-seller/task.json` 中的任务语义；
- 与 Amazon 无关的 WebsiteBench / Viewer 通用基础设施。

截至 2026-07-22，新候选继续使用一个 Python 进程内的 public/admin 双监听与
SQLite，在 Singapore/USD 冻结基线上已覆盖首页、搜索、Best Sellers、五个来源
部门、富证据/稀疏 PDP、购物车、Lists、结账、订单售后、认证及密码找回。当前可验证的商品、
资源与邮件边界详见下方 2026-07-22 里程碑；它仍未完成 Amazon 整站 1:1 验收，
不可把“可运行”或某次回归通过写成“完全复刻”。

旧实现及旧 Gate 2–4 派生物均可从 Git 历史恢复，但不应作为新 clone 的代码或验收基线继续使用。

## 2026-07-21 历史阶段：初步成果与当时约定

本阶段明确采用 **先下载并索引源资源 → 完成前端页面族 → 实现后端流程 → 浏览器
loop 反复对照** 的顺序。以下内容只保留 2026-07-21 当时的阶段快照；其中“当前”、
`402` 个资源、五个富证据 PDP、认证 `501` 等表述均已被下一节的 2026-07-22
状态取代，不应再当作现状引用。

- 新增 `source-assets/2026-07-21/manifest.json`，目前 402 个 P0 本地资源均有
  URL、route/state、MIME、尺寸、字节数和 SHA-256；其中首页资源仍为 321 个，
  三个直接补采的首页商品 PDP `B01M16WBW1`、`B0BG6B2D4D` 与 `B08HN37XC1`
  合计提供 28 个
  `pdp-home` 资源，运行时禁止远程资源。
- 首页已按 2026-07-21 当前 live 证据渲染严格的 27 模块顺序：20 张内容卡与
  7 条商品 rail。7 条 rail 已完整冻结为 `25/19/26/17/26/28/16`，合计 157 个
  真实 ASIN、标题、链接、图片和顺序；Wireless Tech 四图、Top picks for
  Singapore 28 项也已单独补采。旧的第二浏览历史 rail、Sports rail、旅行用品
  替代、统一 14 项截断数组和额外登录推荐模块均已移除。站点级桌面四列主链接、
  4×7 服务目录与移动两列页尾继续保留。
- 1280×720 浏览器对照已把首两行、所有 rail 和下半页位置收敛到源站坐标：
  personalized rail 为 285px，普通 rail 为 281.5px。通过横向控件加载后，首页
  232 张 main 图片全部 complete，破图与远程运行时图片均为 0，桌面/390×844
  本地响应式视口均无控制台警告错误，手机布局无页面级横向溢出。390px 的源站
  证据是 desktop-UA 窄窗口（1000px 最小画布），并非 mobile-UA 首页，因此仍不
  把移动源站称为 1:1 验收通过。
- Samsung T7 当前 PDP 已按 1365×900 源证据重做首屏：7 槽缩略图轨、`3+`、
  `9 VIDEOS` 入口、主图、颜色/价格卡、容量按钮和 Buy Box；390×844 下提供
  上一张/下一张、索引、键盘和滑动切图。
- Samsung T9 已作为第一个数据驱动的非 T7 高保真首屏 PDP 接入：官方 6 图高清图库、
  `6+`、`10 VIDEOS` 入口、Black/Gray 与 1/2/4 TB 变体证据、当前价格/配送/
  库存/规格/About 和 Buy Box 均从 SQLite 中的商品专属 evidence payload 渲染。
  在 1365×900 官方同视口对照中，三列起点和宽度一致，标题框约 1–3px 差异，
  两个主购买按钮纵向约 7px 差异；390×844 下无横向溢出、破图或远程图片。
- 首页 Home & Kitchen rail 的 `B01M16WBW1` 已成为首个完整补采的首页商品 PDP：
  冻结当前匿名 Singapore/USD 源站身份、Home & Kitchen 分类、价格/配送/库存、
  评分与评论数、8 图图库、`3+`、`7 VIDEOS`、规格、About 和 1280×720 首屏几何，
  并由商品专属 evidence payload 渲染。
- Toys rail 的 `B0BG6B2D4D` 也已按当前匿名 Singapore/USD 源站直接证据重建：
  Safari Ltd. Okapi 商品身份、Toys & Games 分类、价格/配送/库存、评分与评论数、
  6 图图库、警告、顶部推广、About 和桌面首屏几何均来自商品专属 evidence
  payload。
- Personalized rail 的 `B08HN37XC1` 已升级为当前直接证据 PDP：SanDisk 2 TB
  Extreme Portable SSD 的完整长标题、Electronics 分类、当前价格/配送/库存、
  评分与评论数、6 图图库、`8 VIDEOS`、Style/Capacity/Color 三组选择、10 项概览、
  About、卖家/履约事实和 1280×720 三列几何均由商品专属 evidence payload
  渲染。当前富证据 PDP 共五个：Samsung T7、Samsung T9、`B01M16WBW1`、
  `B0BG6B2D4D` 与 `B08HN37XC1`。
- 匿名认证前端已补齐独立的登录、密码、注册、找回/重置密码和验证码页面；受保护
  的账户、订单和 Lists 路由安全跳转到登录页。凭据提交仍明确返回 `501`，不写入
  journal，也不回显凭据，因此这不代表认证后端已完成。
- 搜索已支持对 157 个首页证据商品进行 query-aware 匹配；portable SSD 保持冻结
  的 9 项结果合同，稀疏证据结果不虚构价格或评分，并提供无结果态。当前仍未实现
  拼写纠错、分页和筛选交互。
- 非 T7 商品不再错误复用 T7 图库、Samsung Logo、Titan Gray 文案或 T7 terminal
  表单。157 个首页裸 `/dp/<ASIN>` 现在均可达：其中 3 个使用完整直接 PDP 证据，
  另外 3 个复用既有 task catalog，其余 151 个只渲染首页
  卡片已观察到的标题与本地图，并明确不虚构价格、评分、offer、类目规格或交易
  控件，等待逐 ASIN 采集完整图库/视频/A+ 与 offer 数据。
- 当前完整 discovery 共 46 项；合同、首页、搜索、认证前端、资源、PDP 可达性、
  证据分层和响应式回归全部通过，结果为 `OK`，无跳过项。
  桌面和手机搜索、T7/T9 PDP、SanDisk 2TB PDP 均已在真实浏览器复验，无横向
  溢出、破图或远程运行时图片；新首页也已完成上述桌面/手机复验。

后端仍只保留已验证的 T7 榜单 → PDP → quantity 2 → cart 严格任务链路。注册、
登录、结账、支付、订单和邮件不会在其余前端页面族及对应源资源冻结前提前扩张。
下一步先冻结 mobile-UA 首页，并继续把 151 个仅有 home-card evidence 的商品逐个
提升为源站直接 PDP 证据，同时补足搜索拼写纠错、分页、筛选交互、普通购物车、
结账、账户与其余前端页面族。当前所有新 rail 的 `/dp/<ASIN>` 已可达，但除 T7、
T9、`B01M16WBW1`、`B0BG6B2D4D`、`B08HN37XC1` 外不能据此宣称富证据 PDP
已完成；身份与交易
后端仍属于前端冻结后的后续阶段。

## 2026-07-22 当前里程碑：商品广度、规格、评论、账户恢复与购买链路

- 2026-07-21 P0 本地资源清单仍为 `410/410`；2026-07-22 Deals 清单另含 10 张
  AVIF，Lists intro 清单另含 12 张 JPG/PNG，search-commerce fixture 另校验 20 张
  当前 JPEG，总计 452 个有证据校验的本地文件。各组逐项覆盖字节、MIME、尺寸、路径
  与 SHA-256，运行时继续禁止远程图片。`clone/static/assets/` 物理上共有 456 个文件；
  该物理数量不能反向写成 456 个 evidence-verified 资源。
  Lists 的 `source-assets/2026-07-22/lists-intro/evidence.json` 记录匿名请求
  `/hz/wishlist/ls` 最终进入 `/hz/wishlist/intro` 的 DOM 事实与历史几何引用，并与
  `manifest.json`、`verify_assets.py` 互相绑定。
- All 目录仍以七条来源 rail 的 157 个唯一商品为边界；其中 14 个唯一商品使用 rich
  PDP，143 个仍是只呈现已观察标题与本地图的 sparse PDP。14 个 rich PDP 中有 11 个
  来自逐商品的直接源站 PDP 补采；sparse PDP 不虚构价格、评分、offer、评论、类目
  规格或购买能力。加上 task catalog、Deals 与 search-card 范围后，服务端当前共有
  191 个可达商品身份，全部具有本地评论路由；这不把 card-only 商品提升成 rich PDP。
- 顶栏与 All 目录已把五个来源部门做成可达的独立浏览面：Books 32（7 可购）、
  Home & Kitchen 22（6 可购）、Toys & Games 27（3 可购）、Computers 31（16 可购）、
  Beauty & Personal Care 21（7 可购），合计 133 张部门卡片、39 张可购卡片。各部门
  保持来源商品标题、ASIN、图片与顺序，不再把硬盘筛选项或文案套到非硬盘类目。
- 当前证据层共有 49 个具备严格服务端交易报价的 ASIN：既有 19 个商品报价、10 个
  2026-07-22 Deals 默认卡片报价，以及 20 个当前 `direct-search-card` 默认报价；列表、PDP、规格选择、快速加购、
  结账和订单快照均使用同一服务端报价，不根据基础价格猜测未采集的变体价格。portable SSD
  搜索仍保留冻结的前 9 个合同结果，并在其后展示匹配的首页证据商品。
- 20 个 search-card 记录来自匿名 Singapore/USD 的 `best sellers` 与 Computers 搜索，
  只证明卡片显示的默认空选择 USD 报价、图片、显示型 rating/review 文案、部门上下文和
  Add to cart 控件；它们不证明完整 PDP、非默认规格、seller、inventory depth、list price、
  delivery promise 或 returns terms，也不会被自动纳入 Deals。
- Books 直接 PDP `168281808X` 冻结为 *Threshing Day (Wing and Claw Collection)
  (The Empyrean)*：可购买实体规格为 Hardcover，当前价 `$17.49`、List Price
  `$24.99`；Publisher 为 Entangled: Red Tower Books，Publication date 为
  2026-09-29，Language 为 English，Print length 为 224 pages。来源显示 Kindle
  `$11.99`，但它不是本 clone 的实体购买报价；来源为 0 条 customer reviews，
  因此页面不虚构聚合评分或评论。
- Beauty 直接 PDP `B074PVTPBW` 冻结为 Mighty Patch Original Nighttime Acne
  Pimple Patches：`36 Count (Pack of 1)` 为 `$12.99`（`$0.36/count`），`75 Count`
  为 `$18.29`、List Price `$22.90`，两组均为已验证且 In Stock 的规格报价；来源
  聚合为 4.6 分、184,921 条 reviews，商品尺寸为 `5.5 × 3.25 × 0.75 inches`、
  重量 `0.8 ounces`。页面只保留这些聚合事实，不复制或虚构单条来源评论。
- Computers 的 Ailun `B0BJPXXM7D` 冻结 11 个可独立报价的 iPad 型号，当前默认
  `$7.98`；Toys 的 Vault X `B071V91LGC` 冻结 Black/Green/Pink/Purple/Red 五个
  颜色报价与各自库存文案；Home 的 upsimples `B0BQR2BQYZ` 冻结 19 个 `11x14`
  颜色报价，并以原生下拉框呈现已捕获到名称的 79 个尺寸（来源快照总计数为 80）。尺寸不是 `11x14` 时，
  因缺少任何可达的完整颜色×尺寸报价而使用原生 disabled/ARIA-disabled 状态，不能先选中
  无效组合，也不能借用默认价格。
- 新增的 Instant Pot `B00FLYWNYQ`、JanSport 背包 `B07K74LDCH` 与 Amazon Basics
  空气滤网 `B088BZTYFP` 均使用匿名 Singapore/USD 直接证据。Instant Pot 只开放
  两个有报价的尺寸；JanSport 只呈现 61 个来源颜色中已捕获名称与报价的 13 个，不
  推测其余 48 个标签；空气滤网呈现 9 个尺寸和 3 个 MERV 样式，但只有
  `16x20x1` 配 Merv 8 或 Merv 5 的组合可交易。
- 49 个 ASIN 的交易报价均由服务端 allow-list 约束，其中 12 个 PDP 具有规格选择；新增
  10 个 Deals 与 20 个 search-card 商品只允许卡片中已捕获的默认空选择报价。
  规格由服务端按“完整组合”解析；已验证组合会更新价格、主图、库存
  与交易目标，并把选择写入购物车、结账和订单快照。选项依赖直接由 quote matrix
  投影：没有任何可达报价的值原生禁用；选择有效但位于另一连通报价组的值时，前端按
  稳定报价顺序最小化其他轴变化，而不是先建立不可购买的中间组合。既有非默认报价包括 Samsung T7 Blue/1 TB、
  SanDisk Old Model/2 TB 的 Black、Monterey 与 Sky Blue、上述 Beauty 两种
  count 规格，以及 Ailun/Vault X/upsimples 已明确捕获的报价；不从默认报价推断任意笛卡尔积。
- 购物车以服务端生成的 opaque `line_id` 和 canonical `selection_key` 标识行：同一 ASIN
  的不同已报价规格保持独立，重复添加完全相同的选择才合并；改量、删除、Save for later
  与 Move to Cart 均只作用于当前 owner 的目标行，登录合并也保留规格身份。
- 191 个服务端已知商品均提供 `/product-reviews/<ASIN>` 评论面；只有 13 个商品具备冻结
  的来源聚合评分/数量或明确的 0-review 状态，其余显示中性的 source aggregate
  unavailable，而不虚构来源事实。本地用户评论与来源聚合分开标注；本地账户可对每个 ASIN 创建或更新一条评论、按星级筛选、按时间或 Helpful
  排序并投票。Verified Purchase 只由未取消订单的 `order_items` 动态推导，客户端无法
  自报；没有来源评论卡证据时，不虚构来源作者、标题或摘录。Books 明确显示 0 条
  来源评论且无假星级；Instant Pot、JanSport 与空气滤网也只保留已捕获聚合事实，
  不生成来源评论卡。
- Compare 从当前报价和保留的 department/ranking/breadcrumb taxonomy 动态导出 39 个
  eligible ASIN；服务端按完整规格重新报价，允许同一 ASIN 的 sibling variants 分列，
  但一个比较集只允许同一 source-backed family、最多四条 opaque line，删除也只接受行 ID。
- PDP 的 Buy Now 已从无行为按钮升级为独立购买入口：服务端严格校验来源报价、规格
  和数量，以 `BUY_NOW` checkout 快照只购买当前选择，不会把普通购物车一并结账或
  清空；访客可经登录或注册后续接，随后复用地址、配送、三种本地沙箱支付场景、幂等下单、邮件
  和订单历史状态机。
- checkout 提供 approved card、declined card 与 approved bank account 三种确定性沙箱
  场景，不采集 PAN、expiry 或 CVV。拒付停留在 payment step，不创建订单或资金动作，
  可改选另一沙箱方式重试。最终直接 place-order POST 是原子重校验边界：若沙箱支付批准后购物车又
  被新增、改量或删除，服务端在同一 `BEGIN IMMEDIATE` 事务内废止旧支付、把流程降回
  配送完成态并引导重新选择沙箱支付，不创建订单。配送国家只允许 Singapore (`SG`)、
  United States (`US`)、Canada (`CA`)、United Kingdom (`GB`) 与 Australia (`AU`)；
  地址创建/选择、配送选择和最终下单都重新验证，旧数据或被篡改为其他国家时会清除
  地址与支付选择、返回地址步骤且不落单。
- 桌面和移动 All 入口现在打开带遮罩的 Amazon 式侧抽屉，支持关闭按钮、点击遮罩、
  Escape、Tab 焦点循环、焦点恢复与背景滚动锁定；分类、Deals、Gift Cards、Sell、
  Registry、账户、语言和客服均为本地 live route，并保留 `/gp/site-directory` 无 JS 回退。
- Today's Deals 已按当前源站首屏语义补入横向主题 chips、Department/Brand/4+ rating/
  price/discount/deal-type 左筛选栏、紧凑跨品类网格和黄色快速加购；筛选通过可复制 GET
  query 在服务端组合执行并显示结果计数/清除入口。默认展示 29 个可交易报价，不再以
  9 个 SSD 占满首屏；新增 10 个商品不补造未捕获的评分、评论、配送、库存、部门、
  主题或规格。
- Search 已把原来的静态左栏/排序外观升级为严格、可复制的服务端状态：`k`/department、
  重复 brand（维度内 OR）、price、4+ rating、显式 In Stock、四种稳定排序和 page 可以组合；
  变更筛选/排序会回到第一页，chips/Clear 与 Previous/page/Next 保留其余状态。portable SSD
  由 9 个冻结报价结果加 27 个本地首页证据结果组成，按源站当前密度每页 16 条共三页；
  未知 price/rating/availability 不会通过对应筛选，排序时置于已知事实之后。移动 Filters
  有本地可访问抽屉，但其 current mobile golden 仍未补齐。
- `/search/suggestions` 已提供最多 10 条从冻结 catalog 派生、department-aware 的建议，
  全局搜索框具备 ARIA combobox、键盘/鼠标选择、Escape 与 outside close。当前源站
  autocomplete 的视觉与数据 golden 仍未取得，因此这里只声明本地功能，不声明 direct fidelity；
  spelling correction 仍未实现。
- Deals 展示资格已与普通 commerce 报价资格解耦：`deals-evidence.json` 是独立 allow-list，
  因而后续新增有直接报价证据的普通商品不会被误标成 Deal；当前 Deals 仍严格保持 29 项。
- Lists 已从匿名安全跳转壳层扩展为公开 `/hz/wishlist/intro` 与账户私有功能面。首次
  访问为账户建立唯一默认 Shopping List；账户可创建、重命名、删除列表（但不能删除
  最后一张），并添加/删除商品。Add to List 保存 ASIN 与完整已观察规格，因此同一
  ASIN 的不同已报价变体保持独立；无交易报价的 browse-only 商品也可收藏，但明确不可
  加入购物车。Move to Cart 不接受客户端价格，而是以存储的 ASIN/规格在服务端重新
  报价，购物车写入成功后才移除列表项；不存在与跨账户的 list/item ID 对外统一为同一
  404 边界，不能用于枚举。
- Gift Cards 已有金额/卡面/对象选择与本地预览、明确为 `$0` 的模拟余额及不枚举结果的
  虚构码兑换；不采集真实支付信息，兑换尝试仅存 keyed fingerprint。Sell 提供严格校验、
  会话私有的 listing draft/result；Registry 提供本地创建、demo+本人搜索和私有详情，
  跨会话不可见。Prime Video 按用户取舍仅保留静态占位，未建立目录、播放、订阅或状态表。
- 密码找回已由原先的 `501` 占位升级为完整的邮箱提交 → 验证码校验 → 设置新密码
  流程。公开响应对存在与不存在的邮箱保持通用文案，避免账号枚举；验证码绑定恢复
  flow、一次性使用并带有效期、尝试次数与重发替换语义，成功重置后旧密码失效并
  处理既有会话。验证码、密码和 SMTP 凭据不写入普通 journal。
- 注册验证、密码找回和订单确认共用可配置 SMTP transport，并区分 `LOCAL_ONLY`、
  `SMTP_PENDING`、`SMTP_SENT` 与 `SMTP_FAILED` 四态。注册 flow 与订单 owner
  可查看四态、刷新并在预算内重试；密码找回在 OTP 验证前则始终向已知/未知邮箱显示
  相同的 `QUEUED`/刷新面，不公开 SMTP 成功、失败或 Retry，证明所有权后才显示真实状态。
  只有显式配置 SMTP
  host/port/TLS/from（以及服务端需要时的 username/password）时才尝试外部投递；
  未配置时明确记录为受保护的 `LOCAL_ONLY` outbox，不谎称已发送到真实邮箱。SMTP
  失败以安全状态记录；公开页面不泄漏 outbox ID、内部错误、凭据或验证码；本地 outbox 与恢复
  调试接口仍受 admin 边界保护。对应环境变量为 `AMAZON_CLONE_SMTP_HOST`、
  `AMAZON_CLONE_SMTP_PORT`、`AMAZON_CLONE_SMTP_TLS`、`AMAZON_CLONE_SMTP_FROM`、
  `AMAZON_CLONE_SMTP_USERNAME`、`AMAZON_CLONE_SMTP_PASSWORD`、
  `AMAZON_CLONE_SMTP_TIMEOUT_SECONDS` 与 `AMAZON_CLONE_REQUIRE_SMTP`。后者设为 `1`
  时，缺少完整有效 SMTP 配置会使启动失败，不允许静默回退到 `LOCAL_ONLY`。
- 下单后的 `orders.status=PLACED` 保留为不可变事实；本地模拟物流独立执行
  `PREPARING → SHIPPED → DELIVERED` 或 `PREPARING → CANCELLED`。准备中的订单
  可由所属账户取消，送达后可申请退货，再由受保护的 admin 路由收货并完成模拟退款。
  取消、物流推进、退货与退款均校验账户/管理能力、同源请求、HMAC 动作令牌、严格状态
  顺序和幂等键；退款金额只从订单快照派生，不接受客户端金额，也不声称接入真实承运商、
  退货标签、银行或卡网络。

最终完整 discovery 已通过 `328/328` 项，覆盖当前购物车/规格、Compare 迁移、commerce、
评论、支付、邮件隐私、request journal 脱敏与购物入口回归。
当前基础、Deals、Lists、search-commerce 资源校验分别覆盖 `410`、`10`、`12`、`20`
项，合计 452 个 evidence-verified 资源，均为 0 缺失、0 损坏；运行时物理文件总数为
456。后续扩展仍应重新运行完整发现测试，不能沿用该数字替代新一轮验证。

## conversation 实际保留情况

仓库没有保存逐轮 Codex 对话、agent message JSONL、原始 prompt/response transcript 或完整工具流。旧 `CODEX_TRAJECTORY.md` 是事后整理的 retrospective reconstruction，不是原始 conversation。Gate 4 曾保留浏览器 action trace，但它由当前 Codex 会话确定性控制，声明额外 LLM 调用为 0，同样不能代表生成过程对话。

因此，下面只能总结代码库能够证明的决策轨迹，不能还原逐条聊天内容。

## 旧尝试时间线

1. **2026-07-17：窄任务 pilot。** 原 V2 corpus 没有 Amazon source task，于是新增 dev-only 任务 `900136`：进入 External SSD Best Sellers，打开排名第 2 的 Samsung T7（ASIN `B0874XN4D8`），选择数量 2 并加入本地购物车。
2. **单任务实现。** 先做响应式前端、本地 Python/SQLite 状态、严格 terminal POST 合同、会话隔离与持久化，并用大量功能断言保证这条路径可完成。
3. **网站级扩张。** 单任务版被认为范围不足，于是扩到首页、Best Sellers、搜索、分类、Deals、PDP、购物车、账户、订单和 Lists，并生成 200 个商品、10 个部门和 20 个分类。
4. **2026-07-18 Gate 1。** 对 20 个场景 × 5 个 viewport 做匿名 GET-only 采集，共 100 个页面/视口状态、1,700 个 response、100 张 viewport 图和 100 张 full-page 图；其中大量状态受 WAF、HTTP 202、空白或未 hydration 影响。
5. **Gate 2。** 运行时迁为 FastAPI SSR → loopback state engine → SQLite。14 条本地 journey 和旧 task/security 测试通过，但这主要证明功能、安全边界和无外部请求。
6. **Gate 3。** 100 个 clone 状态全部完成语义/稳定性检查，但只有 24 个状态参加 direct visual；44 个只做无门槛 structural diagnostics，32 个完全不可比较。视觉阈值最低仅 composite 0.35、SSIM 0.18、edge F1 0.08。
7. **Gate 4。** 只比较 5 条受限 source/clone 交互轨迹、45 个 action，并在 clone 侧完成一次 terminal POST。它验证任务流程，不验证整站视觉复刻。
8. **2026-07-19。** 上述内容被一次性导入新的 standalone Git 仓库，原开发 commit 历史没有保留。

## 为什么“全部通过”仍然差距很大

- **目标错位。** 旧工作优先优化单一 Samsung T7 任务、持久化、安全边界和测试可通过性，而不是先把少数关键页面做得高度一致。
- **源证据不足。** 首页、drawer、autocomplete 在所有 viewport 都是保护/空白状态；移动 Best Sellers 没有 hydrate 商品卡；PDP 也间歇进入保护页。旧实现对这些部分进行了大面积推断和自创。
- **区域基线混用。** Gate 1 实际观察到 Germany/EUR，Gate 4 又出现 Los Angeles，而 clone 固定 New York 10001/USD。文案、价格、配送和布局证据不属于同一稳定状态。
- **合成内容改变了信息密度。** 200 个商品只复用少量生成 sprite，商品、品类、筛选项、结果数量和首页模块与源站不同。代表性搜索对比甚至是 source `1–24 of 467` 对 clone `1–6 of 6`。
- **视觉 gate 过宽。** 100 个状态中仅 24 个直接比较，允许很低的相似度阈值；关键 PDP 只算 structural。自动化“绿灯”不能支持像素级或体验级结论。
- **Gate 4 覆盖太窄。** 5 条 journey 能证明控件可达和终止请求正确，不能代表整站 fidelity。
- **架构先行、视觉滞后。** 双服务加 SQLite、复杂 verifier 和 200 商品 catalog 在视觉基线尚不可靠时就被固化，增加了返工成本。

## 下一轮必须遵守的重建原则

1. **先冻结可验证范围。** 第一里程碑只做有可靠源证据的少量 route/state，例如 Best Sellers、目标 PDP、搜索和空购物车；未观察到的首页或交互不要自创后宣称复刻完成。
2. **重新采集统一基线。** 同一时间、地区、货币、登录状态和 viewport 完成采集；把 `observed`、`protected/unavailable`、`inferred` 分开存储。历史快照只能作参考，不自动成为当前 golden truth。
3. **视觉优先于功能扩张。** 每个 route 先通过原尺寸 side-by-side 人工检查，再扩下一页。不得用结构检查替代关键页面的视觉比较。
4. **验收覆盖必须透明。** 报告同时列出 direct visual、structural、unavailable 的分母；关键 route/viewports 必须全部 direct visual，不能用总体 `100/100` 隐藏未比较状态。
5. **提高并分层阈值。** 对 header、首屏结构、商品卡、PDP buy box、字体/间距分别设指标与人工 gate；聚合色彩或大面积白底相似度不能单独判定通过。
6. **先真实内容密度，后扩大 catalog。** 商品数量、标题长度、筛选项、图片比例、价格/评分格式和模块顺序应来自冻结证据；不要先生成大 catalog 再让布局适配合成数据。
7. **保持 infra 简单。** 第一版优先一个可重置服务、明确 seed、SQLite（确有持久化需求时）和独立 verifier。候选实现与评测工具分目录，避免自验证逻辑决定自己的通过标准。
8. **保留真实 trajectory。** 下一轮从开始就记录 `agent-messages.jsonl`、浏览器 actions、shell/build/eval streams、代码 diff 和 human interventions；不要再用事后叙事冒充 conversation。
9. **素材与合规先决策。** 当前 source-capture 含 Amazon 页面、截图、HTML/response objects 和媒体。复用或公开前必须确认许可；若必须使用独立创作素材，就应承认这会限制视觉同一性并调整任务目标。

## 建议的新实现边界

新的候选目录仍可使用 `materials/amazon/clone/`，但应在正式重建开始时从空目录创建。任务合同继续要求：同一会话按顺序访问 External SSD Best Sellers 和排名第 2 的 Samsung T7，再以 quantity 2 发出一次正确的响应式 Add-to-cart POST。除此之外的整站范围必须重新确认，不能从旧 200 商品实现自动继承。
