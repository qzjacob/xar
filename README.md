# XAR — 开源产业链投研 + 前沿探索平台 / Industry-Chain Research & Frontier-Exploration Platform

围绕多条并行主题（首发：**AI 光互连**，现已扩展至 **8 大主题**：5 条 AI 供应链 + 3 条消费经济周期链，覆盖 947 家公司）自动汇聚公司公告、财报、研报元数据、新闻、产品页与招聘信号 →
构建**双时态产业链知识图谱 + 时间戳化语义层（semantic_facts）+ RAG** → 经**多 Agent 流水线**产出**深度报告 / 跟踪摘要 / 投资启示**，每条结论可溯源引用，并由**日级自动增量采集**持续刷新。
平台前端由**四个同级模块**组成：**XAR Chathy**（对话分析，默认首页 `/`）、**XAR Andy**（宏观指标，`/andy`）、**XAR Genny**（投研终端 + 数据室，`/genny`）、**XAR Fenny**（结构化票据 + 期权台，`/fenny`），另有 **前沿探索**（`/explore`）与 **运维控制台**（`/ops`）两个卫星页；全部为同一 React SPA 内的外壳，共享同一 Postgres + 本体 + 语义层 + 文档/嵌入/LLM 栈。

> 交钥匙工程：填入一个 LLM Key 即可运行。设计蓝图见 [`DESIGN.md`](./DESIGN.md)，前端四大模块说明见 [`UI.md`](./UI.md)。

## 四大前端模块 — Chathy / Andy / Genny / Fenny

同一个由 FastAPI 托管的 **React SPA**（React + TS + Tailwind），顶栏 **ModuleNav** 在 Chathy | Andy | Genny | Fenny（+ Explore / Ops 卫星）间切换；全站统一**深色金融终端主题**（Bloomberg 风、琥珀强调，`web/src/styles/theme.css` 的 CSS 变量 token 经 `tailwind.config.js` 消费）。

- **XAR Chathy**（对话分析，默认首页 **`/`**）— ChatGPT 式流式、**工具调用**分析助手：以 SSE 流式 + function-calling 就地调用仪表盘同款的平台函数（语义事实、混合文档检索、仪表盘、供应链图谱、公司/环节详情、数据室文档）作答。后端 `models/llm.complete_stream()`（新增 `TaskClass.CHAT` = STRONG token）+ `src/xar/chathy/{tools,sessions,agent}.py`（code-as-truth 工具注册表、≤8 轮工具循环、会话入 Postgres `chat_sessions`/`chat_messages`）+ `api/chathy.py`（`/api/chathy/*`）。
- **XAR Andy**（宏观指标，**`/andy`**）— 理论锚定的宏观指标平台：vendored **`siliconomics` 硅基经济指标库**（`src/slx`，自 github.com/qzjacob/xar-andi，溯源/再同步见 [`ANDY_UPSTREAM.md`](./ANDY_UPSTREAM.md)）。**10 条理论锚**（A1–A8 + 2 META）×**43 个指标**（硬度分级 10 hard / 21 medium / 5 soft / 7 条不可量化「承重墙」），**双时态 point-in-time 库**（`valid_time`/`knowledge_time`/`vintage_date`，严格 `knowledge_time<=as_of` 防前视守卫），**18 个数据连接器**（零 key：sec_edgar/epoch_ai/fhfa/lbnl/indeed_hiring_lab/bls/stooq；带 key：FRED/BEA/EIA/EMBER/ACLED/Ticketmaster），**计量识别引擎**（DID + within-FE，真实 t 检验 p 值）与 **9 条过度宣称登记簿**（安全 AST DSL，判定 open/fixation_triggered/falsified/expired/inconclusive）。与主库**同一 Postgres、独立 `slx` schema**（search_path 隔离）；vendored FastAPI 挂载于 **`/api/andy`**。**勾稽层**：`ontology/macro_links.py`（code-as-truth，43/43 指标映射到 主题/环节/技术路线）+ `ingestion/macro_bridge.py` 把指标印字与判定跃迁蒸馏为 `kg_events(event_type='macro_print')` → 经 `semantic_facts` 零额外代码流入 Genny 信号流与 Chathy 工具（新增 `macro_indicators` 工具，识别水印逐字直通——soft 指标 = 未识别·勿作因果）。前端 5 个懒加载页（总览 / 指标库 / 指标审讯页 / 过度宣称登记簿 / 承重墙）+ 全局 `?as_of=` 防前视控制，teal 强调；Genny 侧配套 `MacroStrip` 反向勾稽 pill。
- **XAR Genny**（投研终端 + 数据室，**`/genny`**）— 原投研终端改名迁移至此（legacy `/segment/:id`、`/company/:id` 自动重定向）；新增 **数据室 Data Room**（`/genny/dataroom`）：上传 PDF/TXT/MD 研报 → 复用既有 Doc/解析/分块/嵌入管线，按 主题·环节 打标（新增 `documents.theme/segment` 列），可浏览/下载，并可被 Chathy 检索。后端 `api/dataroom.py`（`/api/genny/dataroom/*`）+ UI `pages/genny/DataRoomPage.tsx`。公司页升级为 **Company 360**：类型化**投资论点 Thesis 360**（立场/信念度/支柱带证据 chip + 证伪框 + 零 LLM 健康度，`ontology/thesis.py` + `research/thesis.py`，`POST /api/thesis/{cid}/build` 就地生成/重建）、**16 维 CoverageRing 覆盖度环**（`ontology/coverage360.py`）与 分析师预期 / 13F 机构持仓 / 前瞻日历 面板；Chathy 新增 `get_thesis` / `coverage_360` 工具。
- **XAR Fenny**（结构化票据 + 期权台，**`/fenny`**）— 新增 FCN / Phoenix / Snowball 结构化票据 + 期权工作台（自 github.com/qzjacob/fenny 集成）：`fcn` 包 vendored 于 `src/fcn`，其 FastAPI 子应用挂载于 **`/api/fenny`**（Monte-Carlo Dupire 局部波动率定价、greeks、期权分析），LLM 经 XAR 任务路由器（`route_via_xar`），blotter 入 Postgres（`fenny_blotter`）。前端 4 个懒加载工作区（报价台 / 市场解读 / 标的搜寻 / 期权台），plotly 收敛于与 Andy 共享的单一懒加载分片（`components/charts/PlotlyChart.tsx`），主包保持精简。

## 30 秒启动（Docker）

```bash
cp .env.example .env
# 编辑 .env，填任一 LLM Key 即可（经 LiteLLM + 任务路由器）。默认走 DeepSeek V4：
#   DEEPSEEK_API_KEY=sk-...          默认（v4-flash 抽取 / v4-pro 推理，开箱即用）
#   ANTHROPIC_API_KEY=sk-ant-...     质量任务跨厂回退（claude-opus-4-8 / claude-haiku-4-5）
#   OPENAI_API_KEY=sk-...
#   GLM_API_KEY=... / MOONSHOT_API_KEY=...  可选；批量/检索任务走 GLM/Kimi 订阅制（封顶账单）
docker compose up --build
```

打开 <http://localhost:8000> → 默认进入 **Chathy 对话分析**（`/`），直接提问即可（它会工具调用整个平台作答）。投研终端已移到 **Genny**（`/genny`）：点「采集全部公司」→ 选公司 + 报告类型 → 「生成报告」；宏观指标在 **Andy**（`/andy`，种子数据：`docker compose exec app xar andy ingest --seed`）；结构化票据/期权台在 **Fenny**（`/fenny`）。
顶栏 **ModuleNav** 另可进入 **/explore**（前沿探索）与 **/ops**（运维控制台）。内置 Web UI 说明见 [`UI.md`](./UI.md)。
`docker compose up` 同时起一个 **Dagster 边车**（日级自动采集运行时，UI / 运行历史 / 重试在 <http://localhost:3001>）；schema 仅由 `app` 容器 `xar init` 建表，Dagster 复用同一 Postgres。

唯一**必填**项是**一个 LLM Key**（任选其一）。数据库、向量库、嵌入模型（CPU，无需 GPU）、对象存储都已内置自动初始化。所有行情/另类数据 provider 的 Key 全部可选，缺失即自动跳过。Andy 宏观连接器同理：零 key 连接器（sec_edgar / epoch_ai / fhfa / lbnl / indeed_hiring_lab / bls / stooq）开箱即用；带 key 连接器（`FRED_API_KEY` / `BEA_API_KEY` / `EIA_API_KEY` / `EMBER_API_KEY` / `ACLED_API_KEY`+`ACLED_EMAIL` / `TICKETMASTER_API_KEY`，另有可选 `SLX_SLACK_WEBHOOK` 判定跃迁告警，见 `.env.example`）全部可选。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"           # 核心；免费行情(Yahoo)加 ".[market]"，中国数据源加 ".[cn]"，深度解析加 ".[parse-deep]"
# 需要一个 Postgres(pgvector)：docker run -d -p 5432:5432 \
#   -e POSTGRES_USER=xar -e POSTGRES_PASSWORD=xar -e POSTGRES_DB=xar pgvector/pgvector:pg16
cp .env.example .env              # 填 DEEPSEEK_API_KEY（或任一 LLM Key）
xar init                          # 建表 + 录入公司 + 知识图谱种子（含 Andy `slx` schema，守卫式）
xar ingest                        # 采集→解析→建图谱（全 basket；先跑单家用 `xar ingest nvidia`）
xar andy ingest --seed            # Andy 宏观指标：确定性种子（离线）；真实源 --connector sec_edgar 或 --all-real
xar report nvidia                 # 生成深度报告
xar explore ai                    # 前沿探索：采集 arXiv/期刊/X→综合 AI 研究前沿
xar serve                         # Web UI: http://localhost:8000
```

## 终端外壳与卫星模块（Genny 终端 · Ops · Explore）

除上文 Chathy / Andy / Genny / Fenny 外，同一 React SPA 还含两个经 **ModuleNav** 切换的卫星模块，共享同一套 Postgres + 文档/嵌入/LLM 栈：

1. **投研终端（Genny）**（`/genny`）— 投研主控台，主线为 **主题 → 环节 → 公司 → 信号 → 决策**；深色底 + 琥珀强调（`accent`），`Sidebar` + `TopBar` + `DecisionRail` + 全局 `DataProvider` context，并新增 **数据室**（`/genny/dataroom`）；legacy `/segment/:id`、`/company/:id` 自动重定向至此。
2. **运维控制台 Operations Console**（`/ops/*`）— 管理控制平面，琥珀色强调（`warn`），`AdminLayout`。9 个子页：总览、本体、**覆盖度**（947 公司 × 16 维 主题热力，`/ops/coverage`）、数据源、数据湖、另类数据、模型、连接器、技能（overview / ontology / coverage / sources / datalake / altdata / models / connectors / skills）。
3. **前沿探索 Exploration**（`/explore`、`/explore/:sectionId`）— **新增第三模块**，面向人类知识前沿。靛蓝色强调（`explore`），`ExplorationLayout` + `ExplorationSidebar`。详见下文。

## 8 大主题（947 家公司）

公司携带 `themes`（可同属多个主题）与按主题区分的 `seg`（环节 id）。新增赛道 = 在 `ingestion/registry.py` 加一个主题 + 公司，其余不变。`THEMES` 带一个 `kind` 判别轴：**`chain`** = 供应链环节（`tier` 自上游 → 下游）；**`cycle`** = 经济周期位置（不走上下游 tier 轴，见下）。前端 `ChainHeatmap` 对 cycle 主题改标为 **Cycle Map（周期图）**。

**5 条供应链主题（`kind=chain`，按 `tier` 自上游 → 下游排列）：**

| 主题 | 主线（上游 → 下游） |
|---|---|
| **ai_optical**（AI 光互连产业链） | 上游器件 → 光模块厂 → 代工制造 → 下游客户 |
| **ai_chip**（AI 算力芯片产业链） | 晶圆厂设备(WFE) → 材料/EDA → 晶圆代工 → 存储HBM/GPU/CPU → 先进封装 → PCB |
| **ai_software**（AI 软件普及链） | 按**企业 AI 采纳浪潮**分层：研发与AI基础设施/可观测先放量（JFrog/Datadog），CRM/ERP（Salesforce 类）较晚；每个环节带中文 `thesisCn` |
| **space_exploration**（太空探索产业链） | 发射 → 推进 → 卫星制造 → **太空数据中心 / 在轨算力**（以 SpaceX 为代表的天基算力，非地面 DC）→ 地面站 → 组件 → 应用 → 防务 |
| **humanoid_robotics**（人形机器人产业链） | 执行器/谐波减速器/滚柱丝杠 → 无框电机 → 传感器 → 域控/AI 大脑 → 电池 → 灵巧手 → 材料 → 整机 OEM |

**3 条消费经济周期主题（`kind=cycle`）：** `internet`（互联网平台）· `retail`（美国零售）· `restaurants`（餐饮服务）。这些不走供应链 tier 轴，而用新增的**经济周期本体**（`ontology/cycle.py`）：5 态 `CyclePosition`（early_cycle / mid_cycle / late_cycle / defensive / counter_cyclical），`CYCLE_RANK` 兼作 segment tier，于是热力图渲染为「Cycle Map（周期图）」。

**全宇宙构建**：`scripts/universe_build.py` 以 Finnhub 各交易所符号集为存在性闸 + 按主题×地区 LLM 枚举 + 确定性校验（存在性 / 去重 / 美股市值 > $2B 闸 / 消费主题非美区周期黑名单 / 名称↔代码同主体校验），生成 `ingestion/universe.py`（`UNIVERSE` 追加进 `registry.COMPANIES`）。覆盖 US + JP/KR/TW（+ 部分 CN）；地区分布约 US 356 / JP 223 / TW 143 / KR 134 / CN 77。仪表盘内做 FX 归一。市值校验 > $2B（美股用 Finnhub，其余用 Yahoo + 汇率）。
**本体纵深补全**：基础本体（sector/industry/segment/chain_role）已对全部 947 家公司 100% 覆盖；`scripts/ontology_enrich.py`（经任务路由器 `task=search_bulk`，GLM 订阅 + DeepSeek 回退）进一步把 569 家批量生成的「universe」公司补到与精选核心同等纵深——多主题成员、技术路线暴露（`uses_techroute`，`license_tag='enriched'`）、更丰富的别名、更准的主段；所有产出严格按本体词表白名单校验，词表外一律丢弃；技术路线另受**路线↔主题源不变量**约束——`registry.ROUTE_THEMES`（code-as-truth）为 33 条技术路线各声明其归属主题，`_valid()` 在补全源头即丢弃归属主题与公司主题零重叠的越域路线（如芯片公司被打上太空推进路线），把这类「供应商↔路线混淆」从事后 `_CORRECTIONS` 补丁表上提为源不变量（重跑补全不再复发该类错误；按零重叠丢弃为宽松设计，如 `tr_cv`=计算机视觉对 ai_software/humanoid/ai_chip 均有效）；另有一张确定性 `_CORRECTIONS` 表编码审计确认的修正，merge 后回写 `ingestion/universe.py`。**技术路线 25 → 33**（按反复出现的建议数据驱动新增 8 条扩展路线：`tr_cybersec` / `tr_ddic`（显示驱动 IC）/ `tr_power_semi` / `tr_cv`（计算机视觉）/ `tr_med_imaging` / `tr_pneumatic` / `tr_industrial_gas` / `tr_ceramic_pkg`）。

**知识图谱抽取按主题感知**：`kg/extract.py` 的 `_focus_for(company)` 依公司所属主题选取行业框架（修复了此前 prompt 被硬编码为光模块的潜在 bug）。

## 前沿探索模块（Exploration）

把投研流水线复用到**人类知识的前沿方向**：不做交易建议，而是综合**长周期、方向性**的研究前沿。

**6 个前沿领域**（显示顺序，AI 优先；`exploration/domains.py`）：
`ai`（人工智能前沿）· `physics`（物理学）· `math`（数学）· `cs_systems`（计算与系统）· `neuro`（神经与认知）· `complex`（复杂系统与社会；经济物理 + 科技地缘）。

**数据源**：
- **arXiv 预印本**（`providers/arxiv.py`，公开 Atom API，无需 key），各领域按 `arxiv_cats` 抓取；
- **顶级期刊 / 专业平台**（`providers/journals.py` — Quanta Magazine + Physics World RSS）；
- **X 专家之声**（仅订阅经精选的研究者 handle，过滤回复噪声）。

**LLM 综合**：按领域产出前瞻性的**研究前沿（research front）**，含 title、summary、direction（前瞻性主张）、significance、maturity（emerging | accelerating | maturing）、horizon（near | mid | long）、momentum（0–100），并附**经校验、不臆造**的 arXiv 引用。重心是**长周期方向**，而非个股交易。

**存储**：`frontier_fronts` + `frontier_domain_state` 表（`storage/schema.sql`），复用 documents / embeddings / LLM / db 栈。
**API**：`GET /api/exploration/overview` · `GET /api/exploration/section/{domain}`（未知领域返回 404）· `POST /api/exploration/refresh`。
**CLI**：`xar explore [domain]`。
**代码**：`src/xar/exploration/`（`domains.py` / `ingest.py` / `synthesis.py`）+ `src/xar/api/exploration.py`；前端 `web/src/pages/exploration/*`、`components/ExplorationLayout.tsx` + `ExplorationSidebar.tsx`、`lib/exploration.ts`、`types-exploration.ts`。
两个前沿源（arxiv、journals）也以 category=`frontier` 出现在运维控制台的数据源注册表中。

## 架构（实现 = `DESIGN.md` 的最优交钥匙路线）

| 层 | 实现 | 可热插拔为 |
|---|---|---|
| 存储 | **单 Postgres + pgvector**：向量 + 关系 + 双时态图谱 | Neo4j/Graphiti（图）、Qdrant（向量） |
| 采集（非结构化） | edgartools（美股 SEC，绿）· AKShare（A 股 cninfo，绿；研报仅元数据，红——评级/目标价确定性解析入 `analyst_ratings`）· trafilatura（新闻/产品页）· **精选行业 RSS**（`ingestion/feeds.py` **16 条人工核验源 × 8 主题**，公开无 key，`xar pull-rss`）· **微信公众号（we-mp-rss，灰）** · ATS 官方 API（招聘，绿） | Crawl4AI、Tushare Pro |
| 采集（结构化/另类） | **多 provider 套件**：Finnhub（基本面/估计/评级/内部交易 + **公司新闻** `pull_news`/`pull_general_news` + **财报日历→`event_calendar`**，速率感知全篮扫）· FMP（三大报表/分析师估计/目标价/日线 + **公司新闻** `pull_news`）· Polygon（深度日线 + vX 财报）· Yahoo/yfinance（免费全球价格+基本面 + **纵深**：全球评级/目标价/预期、空头持仓+流通盘（4 个新 CORE 指标键）、分红/拆股/财报日→日历、季度三表真实 `period_end` 含 capex/FCF；含 A 股，无 key）· **EDGAR 纵深**（`ingestion/xbrl.py` 8 季度 XBRL 财务时序 + `ingestion/holdings13f.py` 29 家管理人 13F→`holdings`）· **AIFINmarket / 万得**（CN A 股基本面+公告+资讯，MCP-over-HTTP）· Polymarket（预测市场，公开）· X/Reddit（社媒情绪）· Wind（CN-A 深度，需本地终端） | 任意 provider；均按需配置，缺 key 自动跳过 |
| 采集（前沿） | **arXiv**（预印本，公开）· **Journals**（Quanta / Physics World RSS）· X 专家之声 → 前沿探索模块 | 任意公开学术源 |
| 宏观指标（Andy） | **vendored `src/slx`（siliconomics）**：10 理论锚 × 43 指标注册表 · 双时态 PIT 库（独立 `slx` schema，`knowledge_time<=as_of` 防前视）· 18 连接器（7 个零 key）· DID/within-FE 识别引擎 · 9 条过度宣称登记簿；勾稽层 `ontology/macro_links.py` + `ingestion/macro_bridge.py` → `kg_events(macro_print)` → `semantic_facts` | 上游 xar-andi 独立运行（见 `ANDY_UPSTREAM.md`） |
| 解析 | pdfplumber + 分块 + **数值对账闸(tie-out)** | Docling（`.[parse-deep]`）、MinerU |
| 嵌入 | fastembed（CPU，默认 bge-small；可设 BGE-M3/Qwen3） | TEI/vLLM 服务 |
| 知识图谱 | 双时态 节点/边/事件 + **确定性实体消解** + **事件级去重** + **主题感知抽取** | Graphiti（`.[graph]`） |
| 语义层（semantic DB） | 时间戳化、可回测、本体锚定：复用 `kg_events`（+ theme/segment/narrative/time_orientation）+ `kg_edges`（`causally_linked`）+ `expert_insights`，由单一 SQL VIEW **`semantic_facts`** 统一（UNION + LEFT JOIN 浮现 resolution）；承载结构化数表没有的催化剂叙事 / 立场 / 因果 / 远期预期。抽取（`kg/extract.py`）填 `time_orientation`（forward/backward）、`narrative`、`drivers`（→ `causally_linked` 边）。检索 `graphrag.semantic()` 点查该视图并注入分析师 brief | — |
| 远期主张闭环 | `kg/resolve_claims.py` `resolve_forward_claims()`：forward_looking 催化剂遇同公司后续 realization 型 backward 事件 → 结算 hit/miss，否则 stale（可复查）；`kg_events` 增 resolution/resolved_at/realizes_event_id，经 `semantic_facts.resolution` 浮现 | — |
| 检索 | pgvector 稠密 + trigram 词法，RRF 融合 + GraphRAG | RAGFlow、LightRAG |
| 多 Agent | 可控 DAG：规划→图谱→分析师→多空辩论→风险→主编→**证据闸**→**人工审批** | LangGraph（同构） |
| 模型 | **LiteLLM + 任务路由器**：按 `TaskClass` 路由的可更新模型库（`models/registry.py`/`router.py`），计价感知（批量/检索→GLM/Kimi 订阅制，质量→token 跨厂回退）+ 计费感知成本追踪 + 单次预算上限 | 任意 LiteLLM provider / 本地开源 |
| 编排 | Dagster 资产化增量刷新（`.[orchestration]`）：`orchestration/definitions.py` 的 `pull_shard`（8 静态分区，06:00 调度）+ `extract_all`（单批，06:30）+ `core_daily`（按需），调度**默认 RUNNING**（起边车即自动夜跑）；compose 中 **dagster 边车** 暴露 <http://localhost:3001> | — |
| 日级自动采集 | `orchestration/daily.py` `run_daily(stages=('pull','extract'))`：按源增量 PULL（按公司分片、隔离失败）→ 解析/嵌入 → build_kg → expert → signals → `resolve_forward_claims`；`storage/runlog.py` + 新表 **`ingest_runs`** = 运行日志 + 增量游标（last_success_ts），内容哈希 + NOT-EXISTS 游标做幂等/可续跑。CLI `xar daily` | — |
| 评测 | 检索命中率 + 报告 rubric（LLM-judge）+ Phoenix（`.[eval]`） | — |
| 回测 | 催化剂→远期收益 信号有效性：驱动自 **`semantic_facts`**（非仅 kg_events），按 (category, polarity, kind, time_orientation) 键控，严格 PIT 进场 = `GREATEST(as_of, observed_at)` | — |

## 模型路由（LiteLLM + 任务路由器）

**任务路由器**取代旧的两档 fast/strong 路由：调用按 `TaskClass` 而非笼统的「快/强」分流，路由到一个**可更新的代码即真模型库**（`models/registry.py` 的 `Provider`/`ModelSpec`，含 `Billing`/`Capability`/`Status` 枚举）。`换代` = 编辑这一个文件（加 `ModelSpec`、置 `preferred=True`、把旧的 flip 成 `deprecated`）。默认 provider 仍为 **DeepSeek V4**（`config.py`，全部经 `XAR_*` 覆盖）。

- **`TaskClass`**（`models/router.py`，11 类：`kg_extract` / `expert` / `search_bulk` / `analyst` / `debate` / `editor` / `judge` / `synth` / `eval` / `adhoc_fast` / `adhoc_strong`）→ `resolve(task)` 给出有序候选回退链；`tier="fast|strong"` 经 `as_task` 保留为向后兼容别名，未迁移的调用点不变。
- **计价感知路由**：批量/检索类（`kg_extract`/`expert`/`search_bulk`）走 **CHEAP_BULK + 订阅制优先**——**GLM（智谱）/ Kimi（月之暗面）** 的 flat-rate 订阅，使夜间对 947 家公司全量抽取/枚举**不会跑出无上限的 token 账单**，其后再回退到预算内的廉价 DeepSeek token；质量类（`debate`/`editor`/`synth`）走 **STRONG token + 跨厂回退**。
- **解析优先级**：`route_overrides` 表（运维 API）> 环境变量（`XAR_MODEL_*`）> 库内 `preferred`。
- **回退执行器 + 计费感知成本**（`models/llm.py`）：逐候选取 api_base/key、跳过未配置 provider、按预算跳过超额 token 候选并 hard-stop `BudgetExceeded`、瞬时错误单次重试、失败/空响应轮换下一候选；真正命中 flat-plan 的调用记 `usd=0`（订阅批量不触预算上限），而订阅 spec 回退到计量 key 时则记**真实按 token 成本**（计费缺口已堵）。`llm_usage` 增 `provider`/`task_class`/`billing` 列。
- **运维 `换代`**（无需重发布）：`POST /api/ops/llm/route {key, model_id}` 在运行时把某能力/任务重定向到新模型；`/api/ops/llm` 面板浮现库内厂商/模型/路由表 + 按 billing/provider/task 的花费。

`complete()` / `complete_json()` 接受 `task=`；后者走 JSON-mode。providers 含 deepseek / anthropic / openai / **zhipu(=GLM)** / **moonshot(=Kimi)**，env key 分别为 `GLM_API_KEY`（亦认 `ZHIPU_API_KEY`）/ `MOONSHOT_API_KEY`（亦认 `KIMI_API_KEY`），均可选。每次运行有美元预算上限（`XAR_LLM_MAX_USD_PER_RUN`，默认 5）。

## CLI

```
xar init            建表 + 种子          xar report <id> --kind deep_report|tracking_summary|takeaways
xar ingest [id]     采集→解析→建图谱      xar pull [id]       结构化+另类数据→信号
xar ingest-wechat   微信公众号→本体        xar explore [domain] 前沿探索：arXiv/期刊/X→研究前沿
xar parse           仅解析+嵌入           xar backtest        催化剂→收益回测
xar build-kg        仅抽取图谱            xar eval            检索命中率
xar daily           日级增量采集全链      xar resolve-claims  结算远期主张 hit/miss/stale
xar providers-status provider 配置状态     xar status          各表行数
xar thesis <cmd>    投资论点：build [id|--theme|--all] · show · status
xar pull-rss [feed] 16 条精选行业 RSS→主题标注文档（--list 列源；夜批默认含 'rss'）
xar serve           Web UI + API
xar andy <cmd>      宏观指标：init · ingest[--seed|--connector NAME|--all-real] · identify · evaluate[--sync] · sync-events · status
```

**前端路由**：`/`（Chathy）· `/andy/*`（宏观指标，懒加载，全局 `?as_of=` 防前视）· `/genny`（+ `dataroom` · `segment/:id` · `company/:id`；legacy `/segment/:id`、`/company/:id` 重定向至此）· `/fenny/*`（懒加载）· `/explore`（+ `/:sectionId`）· `/ops` + 9 个子页（含 `/ops/coverage` 覆盖度热力）。
设计 token（**深色金融终端主题**，`web/src/styles/theme.css`）：`brand` · `accent`（琥珀，全站强调）· `warn`（运维）· `explore`（violet，探索）· `pos`/`neg`；Andy 页面用 teal 强调 ramp；plotly 收敛在 Andy/Fenny 共享的单一懒加载分片（`components/charts/PlotlyChart.tsx`）。

主要 API：`/api/providers` · `/api/pull` · `/api/fundamentals/{id}` · `/api/estimates/{id}` ·
`/api/prices/{id}` · `/api/prediction-markets` · `/api/social/{id}` · `/api/signals/{id}` ·
`POST /api/thesis/{cid}/build`（投资论点生成/刷新）· `/api/ops/coverage`（16 维覆盖度）·
`/api/chathy/*`（对话分析）· `/api/andy/*`（宏观指标，挂载 slx 子应用：`health` · `metrics[/{key}?as_of=]` · `registry/anchors` · `registry/metrics` · `overclaims[/evaluate]`；XAR 原生勾稽路由 `/api/andy/link/{themes, theme/{theme}?as_of, metric/{metric_key}, sync-events}` 注册于挂载之上）· `/api/genny/dataroom/*`（数据室）· `/api/fenny/*`（结构化票据/期权，挂载子应用）·
`/api/ui/*`（投研仪表盘）· `/api/ops/*`（运维控制台）· `/api/exploration/*`（前沿探索）。

## 结构化数据层 + 本体（Ontology）决策

**本体决策**：自建轻量领域本体（`NodeType`/`EdgeType`/`CatalystType`，code-as-truth），并**锚定**到两个
开源标准以保互操作——**FIBO**（金融业务本体，机构/股权/角色的规范 IRI）与 **schema.org**（Organization/
Product，便于 JSON-LD 导出）。不整体采用 FIBO：它穷尽刻画金融工具/合约，却没有「光模块二供」「CPO 技术路线」
这类垂直概念；而我们的垂直层用代码建模更快更可测。映射见 `ontology/standards.py`（`node_iri`/`edge_iri`）。

**结构化数据归一**：Finnhub/FMP/Polygon/Yahoo/Wind/AIFINmarket 对同一事实命名各异（`grossProfitRatio` vs
`grossMargin` vs `grossMargins`）。每个 provider 都归一到统一的 **canonical 财务指标词表**（`FinMetric`），
`fundamentals`/`estimates` 表因此只说一种语言，多来源按 `source` 共存、随 `as_of` 演进（双时态友好）。

**结构化→本体桥**（`kg/signals.py`）：估计上修/下修、内部人集中买入、预测市场概率，统一蒸馏进 filings 同源的
`kg_events` 催化剂流——于是检索、多空辩论、回测对「一致预期上修」「预测市场异动」与「公告催化剂」一视同仁；
社媒/研报文本镜像进 `documents`，照常走 RAG + LLM 抽取嵌入本体。映射保持在 10 类催化剂分类内
（`SIGNAL_TO_CATALYST`），具体信号子类记于事件 summary。

**专家 Agent 层**（`kg/expert.py`）：用 LLM 对另类数据（X / 微信公众号 / 新闻 / AIFINmarket / **Finnhub / FMP 公司新闻**）做相关性 / 立场 /
质量过滤 → `expert_insights` 表 + `kg_events(license=expert)`，作为信噪比放大器展示于 `/ops/altdata`。`ALT_SOURCES` 已含 finnhub/fmp，新闻同时流入 build_kg 与专家层。

**投资论点层（CompanyThesis）**（`ontology/thesis.py` + `research/thesis.py`）：论点不是自由文本而是**类型化对象**——3–6 个支柱（demand/moat/supply_chain/technology 等 8 类）的每条主张以类型化外键锚回平台事实（event/edge/chunk/insight/fundamental/estimate），带可证伪 falsifier 与 watch_metrics/watch_event_types；`validate_thesis` 纪律校验（证据 id 必须存在于 dossier、**总证据锚 <5 条时 conviction ≤3**），不过即拒绝入库。生成走 `TaskClass.THESIS`（订阅池优先、批量成本有界），版本化写入 `company_thesis`/`thesis_evidence`；**论点健康度零 LLM**：新事实按支柱 watch_event_types × 极性聚合，机器判 confirming/challenging/quiet。CLI `xar thesis`，API `POST /api/thesis/{cid}/build`，Chathy 工具 `get_thesis`。

**360° 覆盖度**（`ontology/coverage360.py`）：「一家公司我们知道多少」的机器可算口径——**16 个维度**（文档/催化剂/前瞻日历/财务快照/**财务时序(含 capex)**/预期/评级/行情/**13F 持仓**/内部人/供应链/社媒/专家/论点…）各带探针 SQL + 目标行数 + 权重，为 947 家公司批量算 0–1 加权覆盖分，喂 `/ops/coverage` 主题×维度热力看板、公司页 CoverageRing、采集优先级与论点的诚实 coverage_gaps。

## 微信公众号接入（we-mp-rss → 本体）

公众号是国内产业链最快的非结构化情报源。XAR 通过自建的
[we-mp-rss](https://github.com/rachelos/we-mp-rss)（FastAPI，端口 8001，登录微信→抓取订阅公众号→
暴露**公开 feed 端点**）接入，连接器 `ingestion/wechat.py` 消费其 `{base}/feed/{id}.json|.rss` 与聚合
`{base}/rss`（**零鉴权、零新增依赖**，stdlib 解析 JSON/RSS/Atom）。每篇文章落为
`Doc(source="wechat", permission="grey")` 入 `documents`，与新闻/研报**同一条非结构化管线**：分块嵌入
（RAG）→ LLM 抽取 → 双时态本体 —— 于是公众号评论与公告、研报一样被检索、被挖出节点/边/催化剂事件。
按公司别名（含中文）或 `feed→company` 映射归属；姿态自用（存事实+原文链接做引用，不转载）。

```bash
docker compose --profile wechat up -d        # 起 we-mp-rss（首次需扫码登录订阅公众号）
# .env: WERSS_BASE_URL=http://werss:8001
xar ingest-wechat                            # 抓取→解析→入本体；或 API: POST /api/ingest/wechat
```

## 数据合规姿态（自用）

代码以 **Apache-2.0** 开源；抓取到的第三方数据**不对外分发**。每个文档落库带 `permission`(green/grey/red) 标签：

- **绿**：SEC EDGAR（美政府公共领域）、cninfo 法定披露、ATS 官方招聘 API、arXiv / 期刊公开摘要。
- **灰**：新闻/产品页礼貌抓取（尊重 robots/crawl-delay，存事实+引用而非转载全文），免费层 API（自用 R&D）。
- **红**：券商研报**仅入元数据**（标题/机构/评级/目标价），绝不入全文 PDF（版权）。

许可洁净由 CI 把关（`scripts/check_licenses.py` 阻断 AGPL/GPL/NC 进核心）。

## 关键设计点

- **双时态**：`t_valid_*`（世界为真区间）+ `observed_at`（获知时间）——后发文档不覆盖先前为真事实，「某日为真」可查。
- **数值对账闸**：表格类 chunk 的合计行须与列和对账，不过则标记，数值结论不在其上 grounding。
- **证据闸**：每份报告计算证据覆盖度/数值对账/幻觉风险（LLM-judge），低置信报告醒目标注交人工。
- **人工审批**：报告默认 `awaiting_approval`，经人审才发布；强制非投资建议免责声明。
- **成本控制**：计价感知的任务路由（批量走 GLM/Kimi 订阅制封顶账单、质量走 token 跨厂回退）+ 计费感知成本追踪 + 单次运行美元预算上限（`XAR_*` 可调）。

## 测试

```bash
pytest -q          # 单元测试始终跑；端到端测试在检测到 Postgres 时运行（LLM/嵌入已 mock，无需 API Key）
```

## License

Apache-2.0（代码）。各上游依赖以各自许可分发；可选 extras（cn/graph/crawl 等）按其许可自行评估，部分需隔离运行。