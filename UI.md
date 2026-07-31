# XAR Web UI — 机构级产业链投研终端 + 前沿探索

前端是一套 **React + TypeScript + Tailwind** 的单页应用（源码在 `web/`），由若干**平级模块**组成、共用同一套
设计令牌与同一份部署产物。模块注册表 `web/src/lib/modules.ts` 是单一事实源（顶栏页签、外壳、改名都从这里取数），
全局常驻顶栏 `components/shell/GlobalTopBar.tsx` 渲染一次、切模块不重挂：

1. **Chathy（对话分析，`/`）** —— 默认首页，对话式分析师。
2. **Andy（宏观指标，`/andy/*`）· Genny（研究终端，`/genny`，含 `/genny/segment/:id`·`/genny/company/:id`·`/genny/dataroom`）·
   Phanny（季报交易，`/phanny/*`）· Fenny（结构化票据，`/fenny/*`）· Romy（前沿探索，`/romy`·`/romy/:sectionId`）**
   —— 研究侧模块，顶栏按此顺序排页签。Genny 即本文下面详述的产业链投研链路
   **Theme → Segment → Company → Signal → Decision**。
3. **Jarvy（后台管理，`/jarvy/*`）** —— 后端管理控制平面，独立 admin 外壳、顶栏右侧分隔线之后弱化显示，
   **11 个子页**：总览 · **Monitor 任务监控** · Fetchy 抓取工人 · 本体 · 覆盖度 · 数据源 · 数据湖 · 另类数据 ·
   模型 · 连接器 · 技能。

> 2026-07-11 更名：旧前缀 `/ops/*` 与 `/explore/*` 现在只是兼容重定向（`App.tsx` 的 `LegacyPrefixRedirect`，
> 大小写不敏感，保留深路径与 query），实际路由是 `/jarvy/*` 与 `/romy`；`/segment/:id`·`/company/:id` 同理重定向到 `/genny/*`。
> 本文余下章节仍按「研究终端 / 运营控制台 / 前沿探索」的旧称叙述其内部结构，对应 Genny / Jarvy / Romy。

FastAPI 直接服务其编译产物，旧版原生 JS 界面保留在 `/legacy`。

## 打开方式

- 本机：<http://localhost:8000>（新 React 终端）· <http://localhost:8000/legacy>（旧 vanilla UI）
- 远程（SSH 隧道，安全不暴露端口；另有可选 `XAR_API_TOKEN`——默认关，开启后变更类 `/api/*` 调用须带 `X-API-Token` 头）：客户端机器上
  ```bash
  ssh -N -L 8000:localhost:8000 <user>@<server-or-tailscale-ip>
  ```
  然后本地浏览器开 <http://localhost:8000>。
- OpenAPI 文档：`/docs`。
- **Dagster 运维界面**（独立 ops surface，非本 SPA 的一部分）：<http://localhost:3001> —— 每日自动采集（`xar daily` / `run_daily`）的运行历史、分片、调度与重试，由 docker-compose 的 dagster 服务承载（`orchestration/definitions.py`）。

## 技术栈与构建

| | |
|---|---|
| 框架 | Vite + React 18 + **TypeScript**（strict） |
| 样式 | Tailwind CSS v3；设计令牌是 `web/src/styles/theme.css` 的 CSS 变量，`web/tailwind.config.js` 只负责把它们映射成色名 |
| 图标 / 字体 | lucide-react（无 emoji）· Inter（`@fontsource`，打包内置、无运行时 CDN） |
| 图表 | 手绘 SVG（迷你走势 / 热力单元格 / 评分条 / 动能条），无重型图表依赖 |
| 路由 | react-router v6：`/`（Chathy）· `/genny`(+`segment/:id`·`company/:id`·`dataroom`) · `/andy/*` · `/phanny/*` · `/fenny/*` · `/romy`(+`/:sectionId`) · `/jarvy`(+11 子页)；`/ops/*`·`/explore/*` 为兼容重定向。Andy/Fenny/Phanny 走 `lazy()` 分包 |
| 数据 | **真实后端** `web/src/lib/api.ts` → `/api/ui/*`、`web/src/lib/exploration.ts` → `/api/exploration/*`（由后端从真实库计算，无 mock） |

**开发**：`cd web && npm install && npm run dev` → :5173（`/api` 代理到 :8000）。
**构建**：`npm run build`（`tsc` 严格检查 + `vite build` → `web/dist`）。

## 集成与服务（交钥匙）

- **多阶段 Dockerfile**：stage 1 `node:20` 构建 `web/` → stage 2 python 拷贝 `dist` 到 `/app/webdist`，
  `ENV XAR_WEB_DIST=/app/webdist`。`docker compose up --build` 仍是一条命令起全栈。
- `src/xar/api/app.py` 服务逻辑（纯增量，未改任何 `/api`）：
  - `GET /` → 有编译产物则服务 React SPA，否则回退旧 vanilla UI（保证纯 `pip install` 也能跑）；
  - `GET /legacy` → 旧 vanilla UI；`/assets/*` → 挂载 SPA 静态资源；
  - `GET /{path}` → SPA 客户端路由回退（`/api`、`/docs`、`/static`、`/assets`、`/legacy` 等正常落 404/各自处理），
    `/romy`、`/jarvy/x` 深链刷新可直达。

## 布局（AppShell）

```
┌────────────┬──────────────────────────────────────────────┬──────────────┐
│  Sidebar   │  TopBar  主题·周期·覆盖数·更新时间·市场筛选      │ Decision Rail│
│ (navy 导航)│──────────────────────────────────────────────│ (固定右栏)   │
│ Research   │  RegimeSummaryCard   产业链景气 / 综合分 / 动能 │  House View  │
│  Universe  │  ChainHeatmap        产业链热力矩阵 (中枢)      │  Top Opps    │
│  · 主题    │  ┌─────────────────────┬────────────────────┐  │  Top Risks   │
│  · 环节    │  │ SegmentRankingTable │ SignalFeed         │  │  Action Queue│
│  · 公司    │  │ CompanyWatchlist    │ CatalystCalendar   │  │              │
│ Workspace  │  └─────────────────────┴────────────────────┘  │              │
│ ↳ 资料室   │                                                │              │
│            │                                                │              │
└────────────┴──────────────────────────────────────────────┴──────────────┘
```
左侧深色侧栏（终端框架）| 中间 TopBar（固定）+ 可滚动主区 | 右侧固定 Decision Rail（<1280px 折叠为主区底部内联卡片）。
跳往 Romy / Jarvy 等其他模块走全局常驻顶栏的页签（`shell/GlobalTopBar.tsx`），侧栏内不再有模块切换器。

## 组件（`web/src/components/`）

| 组件 | 作用 |
|---|---|
| **AppShell / Layout** | 三栏结构外壳（纯布局）；`DataProvider` 经 `context.tsx` 一次拉取跨路由共享投研数据 |
| **Sidebar** | Research Universe（环节列表带景气点/动能 + 公司速跳）+ Workspace 导航（资料室）+ 覆盖度脚注；点环节联动选中。品牌区与模块切换已上移全局顶栏 |
| **TopBar** | **主题切换器**（从 `coverage.themes` 动态列出 8 条已激活主题，下拉切换重取数）· 周期(1W/1M/3M/YTD) · 覆盖公司/环节数 · 更新时间 · 市场筛选(全球/US/CN/JP/KR/HK) |
| **RegimeSummaryCard** | 链景气相位 + 综合分 + 趋势 + 广度 + 关键驱动 + Top 环节动能脉冲 |
| **ChainHeatmap** | **中枢**：上游→下游每环节一行，动能/盈利修正/估值/拥挤/供给/Alpha 热力矩阵 + 走势；点击行联动 |
| **SegmentRankingTable** | 环节机会排序（可按 Alpha/动能/Δ1M/估值/拥挤 排序），Alpha 评分条 |
| **SignalFeed** | 关键信号流：10 类催化剂 + 来源 chip（filing/公众号/预测市场/估计/内部人）+ 极性/置信/相对时间 |
| **CompanyWatchlist** | 重点公司：代码/名/环节/市场/市值/Δ价/营收增速/毛利/估计修正/conviction/信号徽章/走势 |
| **DecisionRail** | 决策台：House View + Top Opportunities（点击进环节）+ Top Risks + 可勾选 Action Queue |
| **CatalystCalendar** | 催化剂日历：按周分组，类型/极性/重要度/“in Nd” |
| **AdminLayout / AdminSidebar** | 运营控制台外壳（Admin 标），独立顶栏读 `/api/health`，不依赖终端数据上下文；侧栏 11 项导航，**Monitor 项带未解决 critical 告警的红点计数**（`MonitorBadge` 每 60s 轮询 `/api/ops/monitor/summary`，页面不可见时跳过；接口抖动静默失败，不让导航因监控接口报错）——红点管「人在 Jarvy 里干别的事」的情况，手机推送管「人不在电脑前」的情况 |
| **MonitorPage**（`pages/ops/MonitorPage.tsx`） | 任务监控页：顶部汇总条（正常/滞后/停摆/无信号/未配置/未解决告警 + 巡检心跳与耗时）· 常驻 worker 卡片 · Dagster 与宏观连接器表 · **13 个拉取源的「上次尝试 / SLA」与「上次产出 / SLA」双列** · 未解决告警流（确认/关闭）· 7 天状态时间线（纯 div 色带，零图表依赖）。**双列是这一页存在的全部理由**：只看「上次尝试」正是 wechat/futu/gangtise 静默哑火数周、Dagster 停摆 7 天却无人察觉的成因——源死透之后 cadence 戳照样是绿的。30s 轮询 |
| **ExplorationLayout** | 前沿探索外壳：`ExplorationSidebar` + 顶栏（面包屑 / preprints·articles·voices·fronts 计数 / `arXiv · Journals · X` chip / **Refresh** 触发再采集+再综合）+ `<Outlet/>`；数据由 `/api/exploration/overview` 驱动 |
| **ExplorationSidebar** | 前沿导航：Overview + 6 个前沿 Section（图标/中文名/各自 front 计数徽章）+ 底部 “← Research Terminal” 返回 |
| `ui/` | 基础件：Card · SectionHeader · Badge · DeltaTag · MetricPill · ScoreBar · Sparkline · MomentumBar |

**八条平行主题**：系统现覆盖 **8 条主题 / 1062 家公司 / 59 细分 / 33 条技术路线**（公司数为 `registry.COMPANIES` 去重口径、2026-07-31 实测；README/SHOWCASE 里的 947 是 universe 构建当时的旧值。技术路线中 8 条为本体增强补入的扩展路线，见 `ingestion/registry.py` 的 `TECH_ROUTES`，如 `tr_cybersec`/`tr_ddic`/`tr_power_semi`/`tr_cv`）。主题按 `THEMES` 的
`kind` 分两轴：**5 条产业链**（`chain`，上游→下游 tier 轴）+ **3 条消费周期**（`cycle`，经济周期轴）—— 公司带
`themes[]` 与 per-theme 段位（`meta.segments`），共享巨头（如 NVIDIA/Broadcom/Marvell）同属多链，故下表各主题公司数之和大于去重后的总数；TopBar 切主题即按
`?theme=` 重取 overview/companies/signals/catalysts，dashboard/环节/公司页全部随主题切换：

| 主题 | id | kind | 细分 | 公司 | 链路 / 周期轴 |
|---|---|---|---|---|---|
| **AI 光互连产业链** | `ai_optical` | chain | 4 | 69 | 上游器件→…→下游 |
| **AI 算力芯片产业链** | `ai_chip` | chain | 9 | 223 | 晶圆厂设备(WFE)→材料/EDA→晶圆代工→存储/HBM·GPU·CPU→先进封装→PCB |
| **AI 软件普及链** | `ai_software` | chain | 9 | 246 | 段位 = 企业 **AI 采用浪潮**（开发/基础设施与可观测性先受益，如 JFrog/Datadog；CRM/Salesforce 较后），每段附中文 `thesisCn` |
| **太空探索产业链** | `space_exploration` | chain | 8 | 164 | 发射→推进→卫星→**太空数据中心 / 在轨算力**（SpaceX 主导，非地面 DC）→地面站→部组件→应用→国防 |
| **人形机器人产业链** | `humanoid_robotics` | chain | 8 | 304 | 执行器/谐波减速器/滚柱丝杠→电机→传感器→算力·AI 大脑→电池→灵巧手→材料→整机(OEM) |
| **互联网平台** | `internet` | cycle | 8 | 54 | 周期轴（非上下游） |
| **美国零售** | `retail` | cycle | 7 | 61 | 周期轴（非上下游） |
| **餐饮服务** | `restaurants` | cycle | 6 | 25 | 周期轴（非上下游） |

全球票池（US/CN/JP/KR/EU/TW/HK/SG/SE），dashboard 内做 FX 归一；市值 >$2B 核验（US 用 Finnhub，其余 Yahoo+FX）。
KG 抽取**按主题取景**：`kg/extract.py` 的 `_focus_for(company)` 依主题选用对应行业框架（修掉了抽取 prompt 曾被硬编码成光互连的潜在 bug）。

**点击即跳转**：每个模块都连到具体页面 —— 点环节（侧栏 / 热力图行 / 排序表行 / 机会卡 / 公司详情的环节徽章）
→ `/genny/segment/:id`；点公司（重点公司行 / 信号行 / 侧栏 Top Names / 环节页成员）→ `/genny/company/:id`。市场筛选在
dashboard 过滤公司与信号。所有点击目标都是真实可导航实体（信号/催化剂均归属到 basket 内公司）。

## 前沿探索 / Exploration（第三模块）

**“人类知识的前沿”**：与研究终端、运营控制台平级的独立 SPA 外壳（`ExplorationLayout` + `ExplorationSidebar`）。
它复用 documents/embeddings/LLM/db 全栈，但**不做股票交易判断**——强调的是**长周期方向（direction）**而非买卖。

**6 个前沿域**（`src/xar/exploration/domains.py`，**展示顺序，AI 居首且端到端打通**）：

| Section | id | 图标 | 中文 | 范畴 |
|---|---|---|---|---|
| Artificial Intelligence | `ai` | brain | 人工智能前沿 | 智能体、推理、世界模型、后训练、效率、具身 |
| Physics | `physics` | atom | 物理学 | 量子信息、凝聚态、高能理论、引力 |
| Mathematics | `math` | sigma | 数学 | 数论/几何/组合/概率/最优化 + AI 辅助证明 |
| Computing & Systems | `cs_systems` | cpu | 计算与系统 | 体系结构、分布式系统、安全密码学、算法 |
| Neuro & Cognition | `neuro` | activity | 神经与认知 | 计算神经科学、认知、脑机接口 |
| Complex Systems & Society | `complex` | globe | 复杂系统与社会 | 经济物理、网络、群体行为、科技地缘 |

**接入的来源**（均落 `documents`，`meta.frontier=true` + `meta.domain`）：

- **arXiv 预印本**（`providers/arxiv.py`，公开 Atom API，按各域 `arxiv_cats` 拉取）。
- **顶刊 / 专业平台**（`providers/journals.py` —— Quanta Magazine + Physics World RSS）。
- **X 专家声音**（仅精选研究者 handle，回复已过滤；非 arXiv 的域更倚重 X）。

**LLM 综合“研究前沿（research fronts）”**：每个域产出多条前沿，字段含
`title` / `summary` / `direction`（前瞻性 thesis）/ `significance` / `maturity`（emerging|accelerating|maturing）/
`horizon`（near|mid|long）/ `momentum`（0–100）/ 经校验的 arXiv 引用（不允许臆造）。
重点是**长周期方向性**，不是仓位。

**存储**：`frontier_fronts` + `frontier_domain_state` 表（`storage/schema.sql`）。
**API**：`GET /api/exploration/overview` · `GET /api/exploration/section/{domain}`（未知域 404）· `POST /api/exploration/refresh`（后台再采集+再综合）。
**CLI**：`xar explore [domain]`（省略 domain 即全域；`--days` arXiv 回看窗 / `--voices` 拉 X / `--synthesize` 采集后综合）。
**代码**：`src/xar/exploration/`（`domains.py`、`ingest.py`、`synthesis.py`）+ `src/xar/api/exploration.py`；
前端 `web/src/pages/exploration/*`、`components/ExplorationLayout.tsx`+`ExplorationSidebar.tsx`、`lib/exploration.ts`、`types-exploration.ts`。
（交付已由独立审计 agent 复核 → PASS。）

**页面**：

| 路由 | 页面 | 内容（全部真实数据） |
|---|---|---|
| `/romy` | ExplorationOverviewPage | Frontier Overview：每个前沿域一张卡（图标/中英名/headline/momentum 条/Top fronts chip + fronts·preprints·articles·voices 计数），AI 卡带 “Live” 标；点击进 Section |
| `/romy/:sectionId` | ExplorationSectionPage | Section 详情：研究前沿列表（标题/maturity/horizon/summary/direction/significance/动能 + 引用的 arXiv 论文）+ 近期 preprints + 顶刊 articles + 专家 voices |

## 设计系统

**深色 Bloomberg 风**：近黑藏青画布 + 分层深色面板 + 细发丝边框；Inter 字体、数字 `tnum` 等宽对齐；克制、信息密集、
金融终端审美（少 emoji、无大面积渐变）。数值热力单元格由 `heat(value, scheme)` 统一着色（divergent / good-high / good-low 三种语义）。

**配色令牌**（`web/src/styles/theme.css`，经 `web/src/index.css` 引入）：所有颜色是**空格分隔的 RGB 三元组** CSS 变量，
`tailwind.config.js` 一律以 `rgb(var(--c-*) / <alpha-value>)` 消费 —— 这样换肤只改 `theme.css`，而 Tailwind 的透明度工具
（`bg-accent/25`）仍然可用。令牌名沿用改版前的 Tailwind 色名，故绝大多数 className 无需改动。

| 令牌 | 角色 | 说明 |
|---|---|---|
| `canvas` / `surface` / `surface-2` / `line` | 表面层级 | 近黑藏青画布 → 卡片/面板 → 抬升态(hover/输入框) → 发丝边框 |
| `brand`（900→50） | **文字梯度**（非底色） | 深色主题下 `brand` 被改用为浅色文字坡道：900 最亮为正文，500 次要，200 为 chrome 上的弱文字 |
| `accent`（indigo） | 全项目唯一强调色 | 选中/链接/关键高亮。注意深色下的角色反转：`accent-700` 是**文字**档（须在 `-50` 面板上 ≥4.5:1），`-50`/`-75` 才是底色档 |
| `pos` / `neg` / `warn` | 正向 / 风险 / 警示 | 为深底提亮；`-50`/`-100` 是深色着色面板而非浅底 |
| `explore` / `andy` | 历史模块色，**已退役** | 按角色 alias 进 `accent` 坡道（亮文字档→亮 accent，深面板档→深 accent 面板），使既有 `bg-andy`/`text-explore-*` 零改动统一 |

`:root` 设 `color-scheme: dark`；动效令牌 `--dur-fast`/`--dur-med`/`--ease` 亦在此文件。

## 路由与页面

| 路由 | 页面 | 内容（全部真实数据） |
|---|---|---|
| `/genny` | DashboardPage | Regime + ChainHeatmap + 环节排序 + 重点公司 + 信号流 + 催化剂日历 + Decision Rail |
| `/genny/segment/:id` | SegmentPage | 环节头（景气/Alpha/动能…热力网格）+ 成员公司表 + 该环节信号流 |
| `/genny/company/:id` | CompanyPage | 公司头 + 指标条 + 价格走势 + 基本面表 + **供应链（上下游/技术路线/股权/单一来源风险，KG 边）** + 信号流 |
| `/romy` | ExplorationOverviewPage | **前沿首页**：6 个前沿域卡片（momentum/Top fronts/计数）+ 独立外壳（AI 居首带 Live） |
| `/romy/:sectionId` | ExplorationSectionPage | **前沿域详情**：研究前沿（direction/maturity/horizon/动能 + arXiv 引用）+ preprints + 顶刊 articles + 专家 voices |
| `/jarvy` | OpsOverviewPage | **控制台首页**：自检健康网格 + 各域统计卡 + 系统概览（独立 admin 外壳） |
| `/jarvy/monitor` | **MonitorPage** | **任务监控**（2026-07-29 新增）：汇总条 + 常驻 worker 卡片 + Dagster/宏观连接器表 + 13 个拉取源的「上次尝试 vs 上次产出」双列 + 告警流（确认/关闭）+ 7 天状态时间线；处置动作（重启 / 清 dagster 队列 / 立即拉取）走白名单 |
| `/jarvy/fetchy` | FetchyPage | **抓取工人管理**：glmworker 总开关 / 抽取 LLM 选型 / 数据源勾选 / 阶段勾选；配置写共享 DB（`glm_worker_state`），工人下一轮读取生效，无需重启容器 |
| `/jarvy/ontology` | OntologyPage | 本体：节点/边/催化剂类型 + FIBO/schema.org IRI + `FinMetric` 词表 + 信号→催化剂桥 + 实时 KG 计数 |
| `/jarvy/coverage` | CoveragePage | **360° 覆盖度**：主题 × 16 个覆盖维度的填充率热力表 + 每主题加权综合分（`ontology/coverage360.py`） |
| `/jarvy/sources` | SourcesPage | 17 个数据源（含新增 **finnhub_news** 公司新闻源）：可用性/姿态/行数/上次运行 + **一键运行** + **自检(selftest)** |
| `/jarvy/datalake` | DataLakePage | 非结构化数据湖：文档/分块/解析/抽取统计 + 文档浏览器(搜索/筛选/分页) + **处理 pending** |
| `/jarvy/altdata` | AltDataPage | **另类数据专家加工**：X/公众号/AIFINmarket 经 AI 专家智能体提炼为高信噪比观点(质量门)+ 一键处理 + Top 专家观点 |
| `/jarvy/models` | ModelsPage | 模型库(providers 含 GLM/Kimi/本地 ollama、models 带 token/subscription 计费) + 按 `TaskClass` 路由表 + 定价 + 用量(按 provider/billing/task 分桶) + 现行 `route_overrides` + **Test LLM** + **运行时换代**(`POST /api/ops/llm/route`) |
| `/jarvy/connectors` | ConnectorsPage | MCP & API 接口：出站连接器 + 入站 XAR API(MCP-ready) |
| `/jarvy/skills` | SkillsPage | Agent 技能：8 阶段报告 DAG + 平台能力(检索/消解/对账/抽取/信号桥/嵌入) |

**三套独立外壳**（同一 SPA，同一部署）：
- **研究终端 / Genny**（`/genny`·`/genny/segment`·`/genny/company`）：`components/Layout.tsx` = `Sidebar` + `TopBar` + `DecisionRail`，
  全局数据经 `context.tsx`（`DataProvider`/`useData`）一次拉取跨路由共享。
- **前沿探索 / Romy**（`/romy`·`/romy/:sectionId`）：`components/ExplorationLayout.tsx` = `ExplorationSidebar`(Frontier 标) +
  探索顶栏（preprints/articles/voices/fronts 计数 + Refresh）+ `<Outlet/>`，数据经 `lib/exploration.ts`，**不依赖**终端数据上下文。
- **运营控制台 / Jarvy**（`/jarvy`·`/jarvy/*`）：`components/AdminLayout.tsx` = 独立 `AdminSidebar`(Admin 标) +
  admin 顶栏（实时 `/api/health`：LLM/providers/姿态）+ `<Outlet/>`，**不依赖**终端数据上下文。

**互相连接**：模块间跳转统一由全局常驻顶栏的页签承担（`shell/GlobalTopBar.tsx`，读 `lib/modules.ts`），Jarvy 页签在分隔线之后、
idle 态弱化——各模块外壳内不再各自维护切换入口。
BrowserRouter 深链由 FastAPI 的 SPA 回退支持，`/romy/:id`、`/jarvy/x` 刷新可直达。

## 数据：真实后端（无 mock）

`web/src/lib/api.ts` 直接请求 **`/api/ui/*`**，由 `src/xar/api/dashboard.py` 从真实库
（`companies` / `prices` / `fundamentals` / `kg_events` / `kg_edges` / `prediction_markets`）计算出前端 domain 形状；
`web/src/lib/exploration.ts` 请求 **`/api/exploration/*`**，由 `src/xar/api/exploration.py` 从
`frontier_fronts` / `frontier_domain_state` / `documents` 计算前沿形状：

| 前端方法 | 端点 | 真实来源与派生 |
|---|---|---|
| `getOverview` | `GET /api/ui/overview` | regime/segments/decision/coverage：环节=`chain_role` 聚合；动能/Δ/走势=`prices`；估值分位=`fundamentals` PE/PS；驱动/风险=`kg_events`+单一来源边 |
| `getCompanies` | `GET /api/ui/companies` | 市值/营收增速/毛利=Yahoo `fundamentals`；Δ价/走势=`prices`；信号徽章/估计修正=`kg_events` |
| `getSignals` | `GET /api/ui/signals` | `kg_events`（催化剂+信号）联 `documents` 推断来源（filing/公众号/预测市场/估计/内部人），仅 basket 公司（保证可跳转） |
| `getCatalysts` | `GET /api/ui/catalysts` | 有日期的 `kg_events`，按 confidence 定重要度 |
| `getCompany` | `GET /api/ui/company/:id` | 价格序列 + 基本面 + 信号 + `graphrag.supply_chain`（上下游/技术路线/股权/风险） |
| `getSegment` | `GET /api/ui/segment/:id` | 环节聚合 + 成员公司 + 成员信号 |
| `exploration.overview` | `GET /api/exploration/overview` | 每域一卡：`frontier_domain_state`(headline/momentum/front_count) + `documents` 实时 preprints/articles/voices 计数 |
| `exploration.section` | `GET /api/exploration/section/:domain` | 该域 fronts（含校验过的 arXiv 引用）+ 近期 preprints + 顶刊 articles + 专家 voices（未知域 404） |
| `exploration.refresh` | `POST /api/exploration/refresh` | 后台触发该域(或全域)再采集 + 再综合 |

> 数据填充：`xar pull`（Yahoo 免费行情，全球含 A 股）补 `prices`/`fundamentals`；`xar ingest` / `ingest-wechat`
> 充实 `kg_events`/`kg_edges`；`xar explore [domain]` 拉前沿 preprints/voices 并综合研究前沿。无数据时端点优雅返回空骨架。
> 无第一方来源的软指标（如 crowding）为基于真实信号（事件极性/密度、估值分位）的**显式派生**，非杜撰，公式见 `dashboard.py`。
> 旧 `/legacy` 仍提供采集→报告→人审主流程。

## 运营控制台 / Operations（后端管理平台）

顶栏 Jarvy 页签进入的 11 个控制台页，由 `src/xar/api/ops.py` 的 `/api/ops/*` 实时自省与操作真实平台状态
（无 mock）。这是“数据 ontology / 数据源 / MCP&API / LLM vendor&model / agent skills / 非结构化数据湖 / 另类数据专家加工 / 任务监控”的统一管理面。

| 端点 | 作用 | 真实来源 / 动作 |
|---|---|---|
| `GET /api/ops/ontology` | 本体 | `NodeType/EdgeType/CatalystType/FinMetric` + FIBO/schema.org IRI + 各类型在 KG 的实时计数 |
| `GET /api/ops/sources` | 数据源注册表 | **16 源**（采集 + provider + 2 个 frontier）：`available()`/姿态/行数/上次运行 |
| `POST /api/ops/sources/{id}/run` | **运行数据源** | 后台跑 ingestion/pull/explore→解析→建图谱（不可运行源被网关拒绝） |
| `GET /api/ops/llm` · `POST /api/ops/llm/test` | LLM | 模型库(providers 含 GLM/Kimi/本地 ollama、models 带 `billing=token\|subscription`)、按 `TaskClass` 的路由表(每任务的候选链)、`llm_usage` 用量（按 provider/billing/task_class 分桶，旧行标 `legacy`）、`route_overrides` 现行覆盖；test=真实廉价往返 |
| `POST /api/ops/llm/route` | **运行时换代** | 把某个 capability 或 task_class 重新指向另一个 registry 模型 id（写入 `route_overrides`，免重启即生效；空 `model_id` 清除覆盖） |
| `GET /api/ops/connectors` | MCP&API | 出站连接器(EDGAR/we-mp-rss/Finnhub/FMP/Polygon/Yahoo/Wind/Polymarket/X/Reddit/arXiv/Journals) + 入站 XAR API |
| `GET /api/ops/skills` | Agent 技能 | 8 阶段报告 DAG(scope→graph→5 分析师→多空辩论→风险→主编→证据闸→人审) + 平台能力 |
| `GET /api/ops/datalake` · `/documents` · `POST /process` | 数据湖 | documents/chunks/解析/抽取统计 + 分页浏览(搜索/筛选) + 触发解析+建图谱 |
| `GET /api/ops/altdata` · `POST /altdata/process` | 另类数据专家加工 | X/公众号/AIFINmarket → AI 专家提炼为 high-SNR `kg_events(license=expert)`；统计/keep-rate/Top 观点 + 触发处理 |
| `GET /api/ops/selftest` | **跑通自检** | 逐一探测每个本体类型 + 每个数据源，返回 ok/degraded/unconfigured/fail |
| `GET /api/ops/monitor` | **任务监控总览** | 22 个任务的状态快照 + 未解决告警 + 推送通道状态；`?fresh=1` 才现探 |
| `GET /api/ops/monitor/summary` | 监控摘要 | 侧栏红点徽章与主机 deadman 脚本用的轻量端点（不含 tasks 数组） |
| `GET /api/ops/monitor/alerts` | 告警列表 | `?scope=open\|all`、`?limit=` |
| `GET /api/ops/monitor/history` | 状态历史 | `task_status_history` 的状态跃迁行，`?task=`、`?hours=`（默认 168，即时间线的 7 天） |
| `POST /api/ops/monitor/alerts/{id}/ack` · `/resolve` | 确认 / 关闭告警 | 确认=不再推送但仍未解决；关闭=写 `resolved`（去重的部分唯一索引正建在 `state<>'resolved'` 上） |
| `POST /api/ops/monitor/actions` | **处置动作** | `restart:<svc>` / `dagster:unstick` / `pull:<source>`；**白名单**只认 catalog 里某个任务显式声明过的 action id，避免端点退化成任意开关 |
| `PUT /api/ops/monitor/mute` | 维护静音 | 只压 Telegram 推送，历史与告警台账照记（否则静音期间的停摆会彻底消失在记录里）；`hours=0` 解除 |

> **`/api/ops/monitor*` 这 8 个端点不在 `ops.py`**：处理逻辑在 `src/xar/api/monitor.py`（路由在 `app.py` 内联注册），
> 落在 `/api/ops/*` 前缀下只是为了与控制台其余端点同宗。读端点一律取 sweep 持久化的快照而非现探——
> 这样接口 <10ms，且**页面看到的与告警判定看到的是同一份数据**；二者不一致是最难查的一类问题。
> 巡检本身是 app 容器内的后台线程（每 120s 一轮，22 个任务）；app 整个挂掉时进程内报警会一起陪葬，
> 因此另有带外兜底 `deploy/monitor/deadman.sh` 挂在宿主 crontab 每 10 分钟打 `/api/ops/monitor/summary`。
> runbook：`deploy/monitor/README.md`。

**数据源注册表（17 源，6 类）**：filing（edgar/cninfo）· web（news/jobs/wechat/**finnhub_news**）· market（yahoo/finnhub/fmp/polygon/wind/aifinmarket）·
prediction（polymarket）· social（twitter/reddit）· **frontier（arxiv/journals）** —— 后两者即前沿探索的来源，现已并入控制台源注册表的
`category="frontier"`。`providers.status()` 暴露 **14 个 provider**（fmp/finnhub/polygon/yahoo/wind/aifinmarket/polymarket/twitter/reddit/arxiv/journals/alphapai/futu/gangtise），
全部 key-gated、未配置时降级为 no-op。

> ⚠️ **FMP 已上游死亡，不是「未配置」**：v3/v4 全部端点返回 HTTP 403 `Legacy Endpoint … no longer supported`
> ——**不是** 402/429 那类配额问题。它已配置、已武装、每晚每分片空耗约 45 分钟，写的 6 张表**有史以来零行**。
> 因此控制台里它显示为「可用」并不代表能拿到数据，**买 key 也点不亮**；把它当作可用源来读会导致错误的采购决定。

**专家智能体层**（`kg/expert.py`）：对另类数据（X/公众号/news/finnhub/fmp/AIFINmarket）做 LLM 相关性/立场/质量过滤
→ `expert_insights` 表 + `kg_events(license=expert)`，信噪比放大在 `/jarvy/altdata` 展示。

> **公司新闻源**：`finnhub_news`（运行经 `providers/finnhub.pull_news`，落 `documents` source='finnhub'，permission='grey'，仅摘要、按 content-hash 去重）使公司新闻进入 `kg_events`，公司详情页（`/genny/company/:id`）信号流因此新增**新闻派生的催化剂**。

**LLM 任务管理器**（`models/registry.py` + `models/router.py`，经 LiteLLM 执行）：取代旧的 fast/strong 两档路由 ——
**模型库**（`registry.py`，code-as-truth）以 `Provider`/`ModelSpec` 描述各家模型与能力（`Capability`: fast/strong/reasoning/long_context/cheap_bulk）、计费方式（`Billing`: token/subscription）、状态（active/preview/deprecated），providers 含 deepseek/anthropic/openai、订阅制的 zhipu(GLM)/moonshot(Kimi) 与**本地 ollama**(minis RTX 3090,零成本仅钉扎 spec,现役 `qwen3-14b-local`)；**换代 = 改这一个文件**（加 `ModelSpec`、置 `preferred=True`、把旧的标 deprecated；本地头另可经 `XAR_GLM_WORKER_LOCAL_MODEL` env 切换）。
**任务路由**（`router.py`）以 `TaskClass`（11 类：`kg_extract`/`expert`/`search_bulk`/`analyst`/`debate`/`editor`/`judge`/`synth`/`eval`/`adhoc_fast`/`adhoc_strong`）经 `POLICIES`+`resolve()` 解析出有序候选链：批量/检索类（kg_extract/expert/search_bulk）走 `cheap_bulk` + **订阅制优先**（GLM/Kimi 包月，夜间全量抽取不被 token 账单冲爆），再回落到预算内的廉价 DeepSeek token 模型——**glmworker 钉扎路径 2026-07-19 起本地优先**（ollama 零成本，云订阅回落）；质量类（debate/editor/synth）走 `strong` token 并跨家回落。
解析优先级：`route_overrides` 表（ops API）> 环境变量 `XAR_MODEL_*` > registry `preferred`；`tier="fast|strong"` 作为向后兼容别名保留（`as_task`），未迁移的调用点不变。
`llm.py` 的 `complete()`/`complete_json()` 新增 `task=` 参数与逐候选**回落执行器**（按候选取 api_base/key、跳过未配置的 provider、对超预算的 token 候选做预算感知跳过并在硬上限触发 `BudgetExceeded`、瞬时错误单次重试、失败/空响应轮换到下一候选）；**计费感知成本**：真正命中订阅平台记 `usd=0`，订阅 spec 回落到按量计费 key 时记真实 per-token 成本（`llm_usage` 新增 provider/task_class/billing 列）。每次运行/每批仍有 USD 预算上限。

> 自检实测：本体 4 节点/8 边/10 催化剂/29 指标全部就绪；源中可用且有真实数据者
> （edgar · wechat · yahoo · polymarket · news/jobs/reddit · arxiv/journals 就绪），keyed/extra 源（polygon/wind/twitter/cninfo/aifinmarket）
> 诚实标注为待配置（填 key 即点亮）。**`fmp` 不属于这一类**：它已配置且在跑，但上游 v3/v4 端点整体 403 下线，填 key 无效（见上）。
> LLM 实测 DeepSeek 往返 `XAR-OK`。控制台页支持**一键运行数据源 / 处理数据湖 / 处理另类数据 / 测试 LLM**。