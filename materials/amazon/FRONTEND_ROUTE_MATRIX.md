# Amazon 前端路由、状态与资源矩阵

> **历史规划/待办清单，不是当前验收源。** 当前有界事实以 `clone.yaml`、
> `scope/*`、`source-assets/offline-clone-manifest.json` 和 harness 生成报告为准；
> 本文中的旧资源数与阶段判断只用于解释迭代过程。

更新日期：2026-07-22

## 目的与阶段边界

本文档把“先完成源站资源采集，再完成全部冻结范围前端，最后才开始后端”固化为可检查的工作顺序。这里的“全部”只指本文档明确冻结的路由、UI 状态和视口组合，不等于已经穷举 Amazon 全站。任何新增页面族或状态都必须按文末的扩展规则先进入矩阵。

阶段顺序不可倒置：

1. **A0 — 冻结观察环境。** 固定 `amazon.com`、匿名会话、Singapore、USD、English、无源站写操作，以及五个标准视口。
2. **A1 — 采集源站状态。** 对矩阵内每个状态保存 viewport/full-page 截图、最终 URL、DOM/可访问树、关键几何、computed styles 和 Network 资源清单。
3. **A2 — 下载并本地化资源。** 下载所有在冻结状态中实际可见或交互后可见的图片、SVG、sprite、字体、视频 poster/帧和其他视觉资源，记录来源与 SHA-256。资源清单 `missing=0`、`hash_mismatch=0`、`remote_runtime=0` 后，该状态才是 `asset-ready`。
4. **F0 — 搭建共享前端。** 只实现全局壳层、设计 token、响应式规则与通用组件，不接业务后端。
5. **F1 — 搭建页面和纯前端状态。** 使用冻结 fixture 和客户端状态完成页面、抽屉、弹层、选项、表单校验与购物车视觉占位；不得用后端进度掩盖前端缺口。
6. **V1 — 视觉 loop。** 每个状态按 source capture → local capture → landmark/region diff → 修正 → 重测循环，直到满足本文件的前端完成条件。
7. **B0 — 后端交接。** 只有冻结范围内所有非例外行都达到 `frontend-done`，并对 `inferred`/`unavailable` 行有明确人工决定后，才开始注册、登录、订单、购物车、付款、邮件等后端功能。

现有 `clone/` 中已经出现的后端代码或测试，不构成跳过 A0–V1 的理由；后续应暂停扩展后端，先关闭本矩阵的资源和前端缺口。

## 证据等级

| 等级 | 含义 | 能否作为 1:1 golden |
|---|---|---|
| `current direct` | 同一当前匿名 Singapore/USD 会话下，目标状态被直接渲染并保存了截图/DOM/几何 | 可以，但只覆盖实际采集的状态与视口 |
| `historical` | 2026-07-18 Gate 1 或更早的直接记录；可能是 Germany/EUR、保护页、未 hydrate 或旧商品数据 | 只能用于结构和几何参考，必须重新采集后才能成为当前 golden |
| `inferred` | 源站需要写操作、登录或隐私数据，当前本地状态只能依据可见相邻状态推导 | 可以做清楚标记的视觉占位，不能宣称源站 1:1 |
| `unavailable` | 没有可用的完整渲染证据，或现有证据只有保护/空白响应 | 阻塞实现与 1:1 宣称；应先补采或取得人工范围例外 |

若一行同时有多个等级，必须按视口和子状态分别记录，不能用较强的桌面证据覆盖移动端缺失。

## 标准视口

| 代号 | CSS 视口 | 用途 |
|---|---:|---|
| `D` | 1365×900 | 当前 desktop 主 golden |
| `DC` | 1024×768 | desktop compact |
| `T` | 768×1024 | tablet |
| `M` | 390×844 | mobile 主 golden |
| `MS` | 320×568 | mobile small |

P0 行最终应在五个视口都有当前直证与 clone 对照。P1/P2 也使用同一组视口；若源站在某视口确实不可获取，应保持 `unavailable`，不能自动降级为推断后算作通过。

## 资源包代号

| 代号 | 资源类型 |
|---|---|
| `G` | 全局导航/页脚 logo、sprite、图标、国旗、搜索/购物车/语言控件、返回顶部、字体与共享背景 |
| `H` | 首页、Deals、分类页的 hero/banner 及响应式裁切版本 |
| `C` | 分类 tile、圆形入口、carousel/rail 图、品牌图和促销卡背景 |
| `P` | 商品主图、缩略图、颜色/容量 swatch、评分/徽章、offer 图及不同分辨率版本 |
| `O` | drawer、autocomplete、location、delivery、zoom、video/360 等 overlay 的图标、蒙层和状态素材 |
| `Q` | 推荐、recently viewed、cross-sell rail 的商品图和徽章 |
| `E` | 空购物车、404、保护/挑战边界等插画与错误态素材 |
| `A` | sign-in/register/account/orders/lists/checkout 的品牌、表单、步骤、状态与空态素材 |
| `S` | 该状态的 DOM、computed styles、关键几何、文案、颜色和响应式 token 参考；不等于照搬源站脚本 |

视觉资源可以在权利允许的私有研究范围内本地化；Amazon 的脚本、账户数据、支付凭据和遥测不是 clone 运行时资源。交互逻辑应独立实现，clone 运行时不得请求 Amazon 或第三方域名。

## P0：资源采集与前端第一闭环

| ID | 页面族 / UI 状态 | canonical route 或状态入口 | 关键 UI 状态 | 视口证据 | 所需资源 | `frontend-done` 条件 |
|---|---|---|---|---|---|---|
| P0-00 | 共享 storefront shell | 所有公开 storefront route | 两行 desktop header；mobile 顶栏+搜索+location；国际配送提示；footer；sticky/scroll 状态 | D=`current direct`；M=`historical`；DC/T/MS=`historical` | G,O,S | 五视口 header/footer、搜索框、location、cart badge、国际提示的尺寸/断点/焦点态与源站一致；所有共享资源已本地化；无远程请求 |
| P0-01 | Home | `/` | 默认首屏、hero carousel、首排四卡、后续 rails、页脚 | D=`current direct`（2026-07-20）；M=`unavailable`（历史为保护页）；DC/T/MS=`historical protected` | G,H,C,Q,S | 先补齐五视口当前 loaded 证据；所有模块顺序、裁切、重叠、carousel 指示器与 full-page 高度一致；保护页不得冒充 loaded home |
| P0-02 | Department drawer | `/` 上触发 `#nav-hamburger-menu`；本身不是独立 route | closed、一级 open、二级部门、back、scroll、outside/ESC close、focus trap | D/DC/T/M/MS=`unavailable`（历史点击均被保护页阻断） | G,C,O,S | 补采每层开合和移动端状态；桌面宽度、遮罩、滚动与移动端全屏层一致；键盘和触屏路径可重复 |
| P0-03 | Search autocomplete | `/` 或任意带全局搜索框页面，输入 `portable ssd`；clone JSON entry 为 `/search/suggestions?q=portable+ssd&i=computers` | empty focus、typing、suggestions、keyboard highlight、clear、submit、outside/ESC close | D/DC/T/M/MS=`unavailable`（历史交互均未应用） | G,O,S | clone 已实现严格、最多 10 条、catalog-derived、department-aware 的建议及 ARIA combobox/键盘/鼠标路径；仍须补采桌面/移动源站 suggestion 数据、行高、图标和 overlay 边界，未取得 golden 前不提升 direct evidence 等级 |
| P0-04 | Generic search results | `/s?k=portable+ssd`；表单 canonical action `/s/ref=nb_sb_noss` | 结果计数、sort、desktop refinement、mobile chips、cards、价格/评分/配送/Add to cart 视觉、pagination | D 首屏=`current direct`（2026-07-20）；D page-2/pager DOM=`current direct bounded`（2026-07-22，无完整截图/资产闭包）；DC/T=`historical partial`；M/MS=`unavailable`（历史保护/空） | G,P,Q,S | clone 已实现 36 条证据结果、16 条分页、Previous/page/Next 与报价商品 quick-add；仍须补齐五视口当前直证和 full-page 密度/裁切 golden |
| P0-05 | Filtered/sorted search | clone canonical `/s?k=portable+ssd&i=computers&brand=Samsung&sort=rating-desc`；源站观察 URL 使用 `rh`/`s` | 已选 department/filter、review sort、remove-filter；移动 Filters panel 仅作为后续 mobile-UA 候选 | D Samsung 与 price-sort 控件/DOM=`current direct bounded`（2026-07-22，无完整截图/资产闭包）；DC/T/M/MS=`historical`（DC/M 较强，其余 partial） | G,P,O,S | clone 已实现 GET department/brand/price/rating/availability、四种排序、chips/Clear 与严格状态保留；当前 1000px 固定桌面画布在窄窗口暴露原生横向滚动，抽屉代码预留但不宣称为当前可见能力；仍须当前补采 Back/Forward、mobile-UA 抽屉和五视口 visual golden |
| P0-06 | No-results search | `/s?k=clawbench-impossible-product-9f3a8c` | query 回显、0-result 文案、建议/改写、无商品 card、搜索重试 | D/T/M/MS=`historical partial`；DC=`unavailable`（历史保护） | G,E,S | 补采 current direct；无结果与网络错误分开；页面高度、建议块和搜索交互一致 |
| P0-07 | Generic Best Sellers | `/Best-Sellers/zgbs` | Best Sellers/New Releases tabs、department tree、多个 ranked rails、横向 clip/scroll | D/DC/T/M/MS=`historical strong` | G,C,P,S | 更新为当前五视口直证；rank badge、rail 间距、四列 desktop 与移动横向裁切/滚动行为一致 |
| P0-08 | Category Best Sellers：External SSD | `/Best-Sellers-External-Solid-State-Drives/zgbs/pc/3015429011` | teal hero、category hierarchy、ranked grid、前六商品、第二名目标链接 | D=`current direct`（2026-07-21）；DC/T=`historical strong`；M/MS=`historical partial` 且 cards 未稳定 hydrate | G,P,S | 下载当前可见排名商品全部分辨率图片；补齐五视口 hydrated 证据；rank、ASIN、标题、价格、评分、reviews、card 几何和链接完全匹配冻结快照 |
| P0-09 | PDP default：Samsung T7 | `/SAMSUNG-Portable-SSD-1TB-MU-PC1T0T/dp/B0874XN4D8?th=1`；mobile alias `/gp/aw/d/B0874XN4D8` | gallery、title/rating/badge、facts、variants、price、buy box、quantity、offers、About、recommendations | D=`current direct`（2026-07-21）；M/DC/T/MS=`historical`（稳定 response render） | G,P,Q,S | 当前五视口重采；desktop 三列和 mobile 内容顺序、gallery 主图、title、buy box、quantity 与长页模块位置一致；所有商品/推荐图本地化 |
| P0-10 | PDP gallery / zoom / media overlay | P0-09 内点击 thumbnail、主图、video/360 affordance | thumbnail selected、主图切换、zoom、modal close、video poster/360 fallback、键盘焦点 | default gallery D=`current direct`；交互 overlay D/M=`unavailable` | P,O,S | 每个可达 overlay 状态先补采；主图 intrinsic ratio、缩放边界、遮罩、close、焦点恢复和移动端手势一致；视频不可获取时明确禁用而非伪造 |
| P0-11 | PDP variants | P0-09 内选择 `Titan Gray`、`1 TB` 及其他可见选项 | selected/hover/native-disabled、依赖轴修复、图片/价格/title/availability 更新、URL query/history | 默认 Titan Gray/1 TB D=`current direct`；其他切换=`unavailable`/`historical` | P,O,S | clone 以 server quote matrix 投影依赖：无可达报价值 disabled，有效断开报价按最少轴变化修复；仍须采集每个实现的 option 组合，未采集组合不得宣称同源 |
| P0-12 | Delivery/location overlay | global location 入口；PDP delivery 入口 | international transition alert expanded、country/ZIP form、cancel/apply、validation | expanded desktop ranking/PDP/cart=`current direct`；mobile=`historical`/`unavailable` | G,O,S | 同一 Singapore/USD 基线下补采 closed/open/error/applied；overlay 层级、尺寸、背景滚动锁定、文案和焦点行为一致；前端阶段不提交真实地址 |
| P0-13 | Empty cart | `/gp/cart/view.html` | empty illustration、anonymous copy、recently viewed T7、recommendation rail、legal/footer | D=`current direct`（2026-07-21）；DC/T=`historical strong`；M/MS=`historical partial` | G,E,P,Q,S | 五视口 current direct；empty canvas、插画、recent panel、rail、footer y-position 与 source 相符；cart count 为 0；不得显示伪 checkout 状态 |
| P0-14 | Populated cart quantity 2 | `/gp/cart/view.html`，由本地交易状态形成 T7×2 | line item、quantity 2、同 ASIN sibling variants、subtotal、delete/save、proceed-to-checkout visual、recommendations；empty↔populated transition | D/DC/T/M/MS=`inferred`；源站写操作未执行 | G,P,Q,A,S | 本地状态以 opaque `line_id` + canonical `selection_key` 保留同 ASIN 不同规格，完全相同选择才合并；五视口布局内部自洽且操作可逆，但不得计入 source-direct 1:1 分母 |
| P0-15 | 404 | `/clawbench-local-replica-source-evidence-not-found`；未知 route 同 family | 404 status、Amazon error art、home link、responsive empty space | D/DC/T/M/MS=`historical`（expected error） | G,E,S | current 重采后匹配 status、插画、文案、间距和返回链接；未知路由不能被宽泛 route matcher 误渲染为 search/PDP |
| P0-16 | Protection / access boundary | 无稳定独立 canonical route；在原请求 route 上出现 HTTP 202/200 challenge/blank | challenge、HTTP 202 blank、retry/link；必须与正常 loaded state 分离 | `/` 五视口=`historical direct`；PDP live boundary=`historical` | E,S | 作为独立诊断状态建模，不作为 Home/PDP golden；只有拿到可见 challenge 才实现其 shell；blank 202 保持 evidence boundary，不用自创内容填充 |

## P1：网站主要页面族

| ID | 页面族 / UI 状态 | canonical route 或状态入口 | 关键 UI 状态 | 视口证据 | 所需资源 | `frontend-done` 条件 |
|---|---|---|---|---|---|---|
| P1-01 | Today's Deals | `/gp/goldbox/` | category chips、filters、discount badges、offer grid、quick-add、mobile Filters | D/DC/T/M/MS=`historical partial` | G,H,C,P,O,S | 当前五视口直证、全部首屏 offer 素材和 filter 状态下载完成；desktop sidebar 与 mobile 两列 grid/Filters 一致 |
| P1-02 | Computers & Accessories | `/computers-pc-hardware-accessories-add-ons/b/?node=541966` | category subnav、store hierarchy、wide heading、tiles/rails、carousel | D/DC/T/M/MS=`historical strong` | G,H,C,P,S | 当前重采并下载所有 visible + lazy-loaded rail 素材；移动端保持源站横向裁切而非擅自改卡片 |
| P1-03 | Electronics | `/electronics-store/b/?node=172282` | category subnav、hero、department tiles、product/promo rails | D/DC/T/M/MS=`historical strong` | G,H,C,P,S | 当前五视口 source/clone 全页对照通过；所有 breakpoints 的 hero/tile 资源本地化 |
| P1-04 | Home & Kitchen | `/home-garden-kitchen-furniture-bedding/b/?node=1055398` | store hero、category hierarchy、room/category tiles、rails | D/DC/T/M/MS=`historical strong` | G,H,C,P,S | 同 P1-03，并验证长标题、不同图片比例与 carousel 边界 |
| P1-05 | Books | `/books-used-books-textbooks/b/?node=283155`；历史最终页曾漂移到 `/amz-books/store?...` | books hero、format/category entry、book covers、rails、redirect/canonicalization | DC=`historical strong`；D/T/M/MS=`historical partial` | G,H,C,P,S | 先确认 current canonical/final URL；五视口 current direct；book cover 比例、作者/format/price 信息密度一致 |
| P1-06 | Sign in | `/ap/signin`（Orders 匿名入口历史重定向到带 OpenID query 的此 route） | email/phone、continue、password step、forgot password、validation、legal links | D/DC/T/M/MS=`historical partial`；独立 current sign-in capture=`unavailable` | G,A,S | 不保留 OpenID/token query 值；补采匿名两步流程和错误态；前端阶段只做本地字段校验/step 切换，不认证、不发邮件 |
| P1-07 | Register | `/ap/register`（provisional，必须以 current final URL 确认） | name、email/phone、password、verification-code shell、validation、sign-in link | D/DC/T/M/MS=`unavailable` | G,A,S | 先 current direct 采集全部步骤；验证码页面仅视觉 fixture；未采集前不得按记忆自创或宣称一致 |
| P1-08 | Account | `/gp/css/homepage.html` | anonymous sign-in boundary；signed-in account cards/sections 视觉 fixture | D/DC/T=`historical strong`；M/MS=`historical partial`；private interior=`unavailable` | G,A,S | anonymous state current 重采；private interior 若不能合法观察则标记 inferred/范围例外；前端 cards、empty/loading/error 状态独立于后端 |
| P1-09 | Orders | `/gp/css/order-history`，匿名最终到 `/ap/signin?...` | anonymous redirect shell；signed-in tabs、search、empty/list/detail visual fixtures | D/DC/T/M/MS=`historical partial`；private states=`unavailable` | G,A,P,E,S | 先完成当前匿名 redirect golden；private empty/populated/detail 不得算 source direct；前端 route/back/tab/search 状态可重复 |
| P1-10 | Lists | 请求 `/hz/wishlist/ls`；匿名 current final `/hz/wishlist/intro` | intro/benefits/sign-in；账户私有 list create/rename/delete；variant-preserving add/remove；browse-only save；server re-quote move-to-cart | requested/final route 与 DOM facts=`current observation`（非完整 golden，2026-07-22）；D/DC/T/M/MS geometry=`historical`；private list=`inferred` | G,A,P,E,S | 补齐 intro 五视口当前直证；私有 CRUD/variant/browse-only/move-to-cart 按 inferred 功能验收单列，不能计入 source-direct 视觉分母；跨账户 ID 不可枚举 |
| P1-11 | Product reviews | PDP `#customerReviews`；`/product-reviews/{ASIN}`；Helpful POST 同 product route 下 | source aggregate/explicit zero/unavailable、local compose/update、star filter、recent/helpful sort、Helpful toggle、Verified Purchase | 13 个 source aggregate/zero facts=`current direct bounded`；完整来源评论卡=`unavailable`；本地 authored state=`inferred` | G,A,P,E,S | 191 个已知商品路由均 live；只有 13 个展示来源 aggregate，其余为 neutral unavailable；不虚构来源作者/摘录，Verified Purchase 只由订单推导 |
| P1-12 | Product comparison | GET `/gp/compare`；POST `/gp/compare/add`、`/gp/compare/remove`、`/gp/compare/clear` | empty/single/2–4 lines、same-ASIN variants、same-family reject、opaque-line remove、guest→account merge | D/DC/T/M/MS=`unavailable`/`inferred` | G,A,P,E,S | 从 39 个报价+taxonomy eligible ASIN 动态生成；完整规格由服务端 re-quote，一个集合只含同一 source-backed family，最多四行；功能通过不提升 source-direct 视觉等级 |

## P2：受保护流程的前端壳层

| ID | 页面族 / UI 状态 | canonical route 或状态入口 | 关键 UI 状态 | 视口证据 | 所需资源 | `frontend-done` 条件 |
|---|---|---|---|---|---|---|
| P2-01 | Checkout visual shell | provisional `/gp/buy/spc/handlers/display.html`；payment entry `/gp/buy/payselect/handlers/display.html`；实际 source entry/final route 尚未观察 | sign-in gate、shipping address、delivery option、approved-card/declined-card/approved-bank sandbox、decline retry、review order、success/failure shell | D/DC/T/M/MS=`unavailable`；只能 `inferred` | G,A,P,O,S | 不收集 PAN/expiry/CVV，不调用付款；三种确定性沙箱场景中拒付停留 payment step 且可重试；每一步不计入 source-direct 1:1 |
| P2-02 | Auth/account protected errors and mail delivery | P1-06～P1-12 内的 expired session、invalid code、empty/error/loading state；订单 retry `/gp/your-account/order-email/retry` | inline error、banner、session-expired return；`LOCAL_ONLY`/`SMTP_PENDING`/`SMTP_SENT`/`SMTP_FAILED`、refresh、bounded retry | D/DC/T/M/MS=`unavailable`/`inferred` | A,E,O,S | 状态仅由当前 auth flow 或订单 owner 可见，不接收公开 outbox ID；失败可安全重试但不泄漏 provider error/凭据/code，功能状态不提升源站证据等级 |

## 2026-07-22 实现附记（不改变证据等级）

- 三组 scoped manifest 加 search-commerce fixture 已验证
  `410 + 10 + 12 + 20 = 452` 个本地资源；`clone/static/assets/` 物理上共有 456 个文件，
  但不能把物理数写成 456 个 evidence-verified 资源。前三组分别为基础 P0、Deals 与
  Lists intro，第四组为 20 张有 bytes/MIME/dimensions/path/SHA-256 的当前搜索卡 JPEG。Lists 的
  `source-assets/2026-07-22/lists-intro/evidence.json`、`manifest.json` 和
  `verify_assets.py` 绑定当前匿名 requested/final route、DOM 事实、12 个源/运行时副本
  及其字节、MIME、尺寸、路径和 SHA-256；历史五视口截图仍只作为 geometry 参考。
- Wishlist 已实现公开 intro 与账户私有 CRUD。Add to List 保留完整已观察规格；无报价的
  browse-only 商品可保存但不可加入购物车；Move to Cart 不接收客户端价格，而以存储的
  ASIN/规格在服务端重新报价，购物车成功后才删除列表项。不存在和跨账户 list/item ID
  返回同一 404，私有状态仍标为 `inferred`。
- 当前 commerce offer allow-list 为 49 个 ASIN：既有 19、Deals default 10、当前
  `direct-search-card` default 20。后 20 条仅证明搜索卡默认空选择报价，不证明完整 PDP、
  variants、seller、inventory、list price、delivery 或 returns，也不自动进入 Deals。
- 五个部门现有 Books 32/7、Home 22/6、Toys 27/3、Computers 31/16、Beauty 21/7
  （格式为 cards/purchasable），合计 133/39。Deals 仍严格保持独立 allow-list 的 29 项。
- 购物车使用 owner-scoped opaque `line_id` + canonical `selection_key`；同 ASIN sibling
  variants 不互相覆盖。PDP option compatibility 由 quote matrix 投影，无可达报价值禁用，
  有效断开报价以最少轴变化修复其余选择。
- 所有 191 个已知商品均有本地 review route，仅 13 个有 source aggregate/zero evidence；
  Compare 动态覆盖 39 个 eligible ASIN，保留规格、限制同 family 和四条 opaque line。
- checkout 的 direct place-order 会在同一写事务内再次校验 cart fingerprint、沙箱支付
  与配送地址。支付提供 approved card、declined card、approved bank 三场景；拒付不落单
  且可重试。支付后购物车变化时废止旧 approval 并返回支付步骤；配送仅允许 SG/US/CA/GB/AU，
  不支持或后续失效的地址在创建、选择配送及最终下单边界均被拒绝且不生成订单。P2-01
  仍因缺少可安全观察的源站私有直证而保持 `inferred`，不能据功能测试提升视觉证据等级。
- 注册 flow 与订单 owner 的邮件状态严格区分 `LOCAL_ONLY`、`SMTP_PENDING`、
  `SMTP_SENT`、`SMTP_FAILED` 并支持 scoped refresh 与 bounded retry；密码恢复在 OTP
  验证前对已知/未知邮箱统一显示 `QUEUED`/刷新，不公开 SMTP 成败或 Retry；部署设置
  `AMAZON_CLONE_REQUIRE_SMTP=1` 后，SMTP 配置不完整会 fail closed，而不是回退本地 outbox。
- autocomplete 已有本地 `/search/suggestions`、ARIA/键盘/鼠标/outside close 语义，但
  尚无当前源站 suggestion visual/data golden，不能从功能实现推导 direct fidelity。
- Prime Video 继续是无目录、播放、订阅或持久状态的静态占位，不计为已完成的页面族。

## 资源本地化清单要求

历史 `source-capture/` 有 100 个页面/视口状态和 746 个 content-addressed objects，但它们包含区域漂移、保护响应和历史资源，且原 Gate 1 的 localization decision 不是当前资源许可或当前 golden。当前 452 个 evidence-verified 文件只证明上述三个 scoped manifest 与 20-card fixture 的有限闭包；456 是运行时物理文件数，不等于每个矩阵 route/state 都已有 completeness manifest，因此全矩阵仍未通过 A2。

建议每次当前采集生成一条不可变 manifest 记录，至少包含：

- `capture_date`、marketplace、locale、currency、delivery region、auth/cart 状态；
- page family、canonical requested route、final route、UI state、viewport；
- source URL、initiator、resource type、content type、status、bytes、intrinsic width/height；
- SHA-256、响应式 `srcset`/media query、可见区域/用途、是否 lazy-loaded；
- 本地相对路径、下载结果、hash verification、引用该资源的组件；
- 权利/再分发限制、是否只允许私有研究使用；
- `required`、`downloaded`、`verified`、`referenced`、`missing_reason`。

每个状态的资源闭包必须满足：

```text
required = downloaded = verified = referenced
missing = 0
hash_mismatch = 0
remote_runtime = 0
```

资源下载范围包括首屏、full-page lazy-load 后、carousel/hover/focus、drawer、autocomplete、variant、gallery 和 overlay 中会出现的素材；不能只下载初始 viewport 的图片。不同尺寸的同图不是自动等价，应按源站实际 `srcset`/裁切保留需要的变体。

## 前端完成总条件

一行只有同时满足以下条件才可标记 `frontend-done`：

1. **证据闭包。** 每个要求视口都有 `current direct`，或有书面人工例外；`historical` 只能帮助定位，`inferred`/`unavailable` 不得混入 source-direct 通过率。
2. **资源闭包。** 该行 manifest 满足上面的等式，浏览器 Network 中除 localhost/data/blob 外无请求。
3. **几何和样式。** 关键 landmark 位置与尺寸控制在 ±1 CSS px；字体族、字号、字重、行高、颜色、border、shadow、图片 `object-fit`/crop 与源站 computed state 一致。平台字体栅格差异需在报告中单列，不能用大面积白底相似度掩盖。
4. **内容密度。** 标题长度、卡片数、图片比例、价格/评分/review 格式、module 顺序和 full-page 高度来自冻结证据；不得用重复商品或合成模块填空后声称一致。
5. **响应式行为。** 五视口无非源站的 overflow、重排或隐藏；若源站是水平裁切/scroll，clone 也保持相同行为。
6. **交互状态。** click、keyboard、touch、focus、hover、ESC、back/forward 和刷新恢复都可确定性重放；前端阶段使用 fixture，不依赖业务后端。
7. **视觉 loop。** 保存同尺寸 source/clone、landmark geometry、region diff 和人工 review 结论；所有 material mismatch 关闭后才通过。
8. **质量边界。** 无 console error、无 broken image、无累积 layout shift；404、保护、空、loading、error 与 loaded 状态分别测试。

前端总阶段只有在 `frontend_done / required_frontend_rows = 100%`、P0/P1/P2 的例外分母透明，并且不存在未说明的 `unavailable` 行时才允许进入 B0。视觉占位可帮助后续后端开发，但不提升 source-direct 覆盖率。

## 当前证据缺口摘要

- `source-current/2026-07-20/` 只有当前 desktop Home 与 portable-SSD search 两张直证。
- `source-current/2026-07-21/` 增加当前 desktop External SSD ranking、T7 PDP 与 after-PDP empty cart 三张直证。
- 当前 mobile、desktop compact、tablet 和 mobile-small 没有同一 Singapore/USD current golden。
- drawer、autocomplete、PDP gallery overlay、非默认 variants、register、private account/orders/lists、reviews/Compare 私有状态和 checkout 没有当前直接视觉证据；autocomplete 本地功能已实现，缺的是源站 suggestion visual/data golden；Lists intro 仅新增当前 requested/final route、DOM 与资源 provenance，五视口 geometry 仍为 historical。
- populated cart quantity 2 因未对源站发送 POST，只能是明确的 local inferred fixture。
- Gate 1 的历史 100-state 快照仍有参考价值，但不能消除上述缺口，也不能证明当前资源已全部下载。

## 不可穷举范围与扩展规则

Amazon 是动态、区域化并持续变化的网站；不能用本文档的有限矩阵宣称“全 Amazon 已穷举”。扩展按 layout signature 和状态空间执行：

1. 新 canonical route pattern、新页面模板、新 overlay、auth 状态、空/错误/loading 状态、区域/货币或断点行为出现时，先新增一行，不直接塞进已有模板。
2. 通配页面族（如 `/dp/{ASIN}`、`/s?...`、`/b/?node=...`）至少覆盖会改变布局的代表样本：短/长标题、单/多 offer、促销/无促销、in/out of stock、不同图片比例、不同筛选密度和空结果。只有 DOM/geometry signature 等价的样本才可共享组件验收。
3. 每个新增行重新执行 A0→A1→A2→F→V；不允许因为组件“看起来通用”而跳过资源清单或 current direct capture。
4. 源站无法安全观察的 private/mutating 状态保持 `inferred` 或 `unavailable`，并以功能等价为后续目标；不得把本地实现反向描述成源站证据。
5. 覆盖报告必须同时给出已知 required、current-direct、historical-only、inferred、unavailable 和未知待发现页面族数量。只有明确冻结版本的分母可以写“100%”，不能写“全站 100%”。
