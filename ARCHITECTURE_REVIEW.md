# XAR 架构评审与改造计划（CTO / 首席架构师）

> 评审范围：全仓代码（`src/xar` ~10,860 LOC）+ schema + tests + CI + 部署。
> 评审视角：技术 / 用户 / 架构 / 第一性原理。
> 决策前提（已与项目方确认）：**目标部署形态 = 自用 / 单团队研究工具**；平台现为**三个并列顶层模块**——产业链投研主轴（研究终端 **Genny** `/genny`）、运维控制台（Operations Console `/ops/*`）、**前沿探索（Exploration `/explore`）**。Exploration 已从"早期范围漂移信号"转正为正式第三模块，并经独立 agent 审计 PASS。**前端现为四个并列 shell——默认首页 `/` 的对话式工具调用分析师 **Chathy**、宏观指标平台 **Andy** `/andy`（vendored `src/slx` siliconomics + 勾稽层，见演进 7）、研究终端 **Genny** `/genny`（原 Research Portal `/`；旧 `/segment/:id`·`/company/:id` 现 302 重定向到 `/genny`，App.tsx:53-67）、结构化票据/期权台 **Fenny** `/fenny`——四者共享同一数据+本体底座（详见演进 6/7）。**
> 文档用途：团队对齐基线，指导后续 P0/P1/P2 改造。证据均带 `file:line` 可追溯。

---

## 〇、总体裁决

**这是一份品味明显高于行业平均的工程实现**：~10,860 行代码撑起了 15+ 数据源（含新增 arXiv / journals 两个 frontier 源 + Finnhub/FMP 公司新闻）+ 双时态图谱 + **时间锚定的语义数据库** + 多 Agent 流水线 + **Dagster 日度自动 ingest** + 全栈四模块终端，"交钥匙 + 优雅降级"的工程纪律扎实，许可合规作为 CI 硬规则体现了机构级自觉。**不是屎山，信噪比很高。**

平台自上次评审以来发生了多处结构级演进，且都做得克制、对齐主轴纪律：

1. **投研主轴从 2 主题扩到 8 主题、全球化**：5 条产业链主题（`kind="chain"`，走上游→下游 tier 轴）`ai_optical` / `ai_chip` / `ai_software` / `space_exploration` / `humanoid_robotics`，外加 3 条**消费周期主题**（`kind="cycle"`，走经济周期轴而非供应链 tier）`internet` / `retail` / `restaurants`。THEMES 新增 `kind` 判别符（registry.py:19-31）。周期主题不复用 tier 轴，而是引入新本体维度 `src/xar/ontology/cycle.py`——5 态 `CyclePosition`（early/mid/late/defensive/counter_cyclical）+ `CYCLE_RANK`（兼作 segment tier，让热力图渲染为 "Cycle Map"）；前端 ChainHeatmap 对 cycle 主题改标为 Cycle Map。
2. **宇宙从 ~378 策展核扩到 947 公司**：`scripts/universe_build.py` 以 Finnhub 各交易所符号集为存在性闸 + 按 theme×region LLM 枚举 + 确定性 verify（存在性 + 去重 + US ≥$2B 市值闸 + 消费非美周期 blocklist + 名↔票同实体校验）生成 `src/xar/ingestion/universe.py`，`UNIVERSE` 追加进 `registry.COMPANIES`（registry.py:685）。覆盖 US + JP/KR/TW（+部分 CN），合计 **947 公司**。新增主题/宇宙严格走"加 theme + 加公司"的既定路径，架构未变形。
3. **语义数据库（本轮头条）**：一个时间戳化、可回测、本体锚定的语义层，承载结构化数值表（fundamentals/estimates/prices）**不**承载的内容——催化剂叙事、stance、因果、前瞻预期。设计决策为**加列复用既有三张双时态表而非另起平行表**：`kg_events`（加 theme/segment/narrative/time_orientation）+ `kg_edges`（新增 `causally_linked` EdgeType）+ `expert_insights`（加 as_of/theme/segment/time_orientation），由单一 SQL VIEW `semantic_facts`（UNION）统一（schema.sql:419）。抽取 `kg/extract.py` 现填 `time_orientation`（forward/backward）、grounded 叙事、drivers（因果实体→`causally_linked` 边）；检索 `graphrag.semantic()` 点查该视图并注入分析师 brief。
4. **前瞻断言解析生命周期（本轮唯一净新能力）**：`kg_events` 加 resolution/resolved_at/realizes_event_id；`src/xar/kg/resolve_claims.py` 的 `resolve_forward_claims()` 闭合"预期→兑现"环——一条 forward_looking 催化剂在窗口内出现同公司 backward 兑现事件即解析为 hit/miss，否则 stale（可复查），只改 forward 行，经 `semantic_facts.resolution` 暴露，CLI `xar resolve-claims`。
5. **Exploration 第三模块转正**：6 个前沿域（ai 优先打通 + physics/math/cs_systems/neuro/complex），融合 arXiv + 期刊（Quanta/Physics World RSS）+ X 专家声音，由强推理 tier LLM 蒸馏出**前瞻性研究前沿**（方向/成熟度/视野/意义/动能，附 grounded+validated 的 arXiv 引用），落 `frontier_fronts` + `frontier_domain_state` 两表。**复用既有栈（documents/embeddings/llm/db/同套 FastAPI+SPA chrome），是方向性研究而非交易**。该交付经独立 agent 审计 → **PASS**。
6. **前端三模块重构（该轮头条之二；后经演进 7 新增 Andy 扩为四模块）**：同一 React SPA 由 FastAPI 单体托管，前端拆为三个并列 shell + 一套共享 ModuleNav 切换器（`web/src/components/ModuleNav.tsx`），全部坐在既有数据+本体底座上（无平行后端）：
   - **XAR Chathy**（默认首页 `/`，App.tsx:53）——ChatGPT 式、SSE 流式、**工具调用**分析师：不走 HTTP 回环，直接**在进程内**调用与仪表盘同源的函数（语义事实 / 混合文档检索 / 仪表盘 / 供应链图 / 公司·segment 详情 / data-room 文档，见 `src/xar/chathy/tools.py` 的 code-as-truth 工具注册表 + `execute()`）。后端：`models/llm.complete_stream()`（SSE 流式 + function-calling，复用任务管理器的 fallback/计费；新增 `TaskClass.CHAT` → STRONG token，router.py:42/69）、`chathy/{tools,sessions,agent}.py`（≤8 迭代工具循环 `_MAX_ITERS=8` agent.py:22、Postgres `chat_sessions`/`chat_messages` schema.sql:483-500）、`api/chathy.py`（SSE `POST /api/chathy/sessions/{sid}/chat` + 会话 CRUD）。前端 `pages/chathy/ChathyPage.tsx` + `components/chathy/*`（react-markdown、工具活动 chips、会话栏、fetch+ReadableStream 的 stop/abort）。
   - **XAR Genny**（`/genny`，App.tsx:57）——既有研究终端更名迁移；新增 **Data Room**（`/genny/dataroom`）：上传 PDF/TXT/MD → 既有 Doc/objects/parse_pending 流水线，加列 `documents.theme`/`segment`（schema.sql:475-477）打标 theme·segment，chunk+embed，可浏览/下载、可被 Chathy 检索。后端 `api/dataroom.py` + `POST /api/genny/dataroom/upload`（app.py:517）；UI `pages/genny/DataRoomPage.tsx`。
   - **XAR Fenny**（`/fenny`，App.tsx:70，从 github.com/qzjacob/fenny 集成的**新**结构化票据 FCN/Phoenix/Snowball + 期权台）——**mount-first 供应商化**：`fcn` 包**原样 vendored 到 `src/fcn`**（见 `FENNY_UPSTREAM.md`），其 FastAPI 子应用**挂载**在 `/api/fenny`（app.py:559-566，Monte-Carlo Dupire 局部波动率定价 / greeks / 期权分析）；LLM 经 `route_via_xar` 标志走 XAR 任务管理器（fcn/service/llm.py:24），blotter 迁至 Postgres `fenny_blotter` 表（`xar/fenny/blotter_pg.py` 的 `PgBlotterStore`，经 `blotter_factory` 注入）。**依赖单向 `xar → fcn`——`fcn` 从不 import `xar`，保持独立可运行、未来 re-sync 机械化**。UI 4 个 lazy-loaded 工作区（Quotation Desk / Market Read / Underlying Finder / Options Desk，`pages/fenny/FennyApp.tsx`）带 plotly payoff 图——**plotly 隔离在 lazy `/fenny` chunk 内，主 bundle 保持精简**。
   - **暗色金融终端主题**：`web/src/styles/theme.css` 的 CSS 变量 token 系统（`--c-*`，如 `--c-canvas`/`--c-surface`/`--c-accent-*`），由 `tailwind.config.js` 以 `rgb(var(--c-*) / <alpha-value>)` 消费（tailwind.config.js:9）；Bloomberg 风、amber accent、dark-only。三 shell + Ops/Explore 卫星统一视觉语言。schema 全部加列/加表为 additive/幂等（`documents.theme/segment`、`chat_sessions`、`chat_messages`、`fenny_blotter`）；新增依赖 numpy/scipy/python-multipart（后端）、react-markdown/remark-gfm/plotly（前端）。全量已测（后端 49 tests + fenny 206 + ruff clean + docker build + live headless 验证）。
7. **XAR Andy 宏观指标模块（第四前端模块，2026-07，分支 feat/chathy-andy-macro）**——原对话模块 "Andy" 更名 **Chathy** 后，"Andy" 名号让渡给**新的宏观指标平台**：`siliconomics` 硅基经济指标库自 `github.com/qzjacob/xar-andi` **原样 vendored 到 `src/slx`**（溯源/pin 见 `ANDY_UPSTREAM.md`），复刻 Fenny 的 **vendor + mount** 模式。要件：
   - **注册表 + PIT 纪律**：10 理论锚（A1–A8 + 2 META）× 43 指标（10 hard / 21 medium / 5 soft / 7 不可量化「承重墙」）+ 9 条过度宣称登记簿（安全 AST DSL，判定 open/fixation_triggered/falsified/expired/inconclusive）；双时态点时库 `valid_time`/`knowledge_time`/`vintage_date`，读取过严格 `knowledge_time<=as_of` 防前视守卫——与主库双时态/PIT 回测同一纪律。
   - **同一 Postgres、独立 `slx` schema**（search_path 隔离，零新增服务）；vendored FastAPI 挂 `/api/andy`（health/metrics/registry/overclaims），XAR 原生勾稽路由 `/api/andy/link/*` **注册于 mount 之上**（app.py:569-608 顺序不变量，先注册者遮蔽 mount 前缀）。18 个数据连接器（7 个零 key：sec_edgar/epoch_ai/fhfa/lbnl/indeed_hiring_lab/bls/stooq；keyed：FRED/BEA/EIA/EMBER/ACLED/Ticketmaster）；计量识别引擎（DID + within-FE，真实 t p 值），识别水印逐字直通（soft = 未识别·勿作因果）。上游 Streamlit/dagster/dbt/soda 裁除；调度 = `xar andy` CLI + 每日链 opt-in `macro` 源（daily.py:88-112）。
   - **勾稽（crosswalk）层**：code-as-truth `src/xar/ontology/macro_links.py`（**43/43** 指标映射 主题/环节/技术路线，scope `chain|platform`、`good_when rising|falling|None`、`rationale_zh`；+ 9 `OVERCLAIM_LINKS` 带判定极性）+ `src/xar/ingestion/macro_bridge.py`（指标印字与判定跃迁 → `kg_events(event_type='macro_print', license_tag='slx')`，逐关联主题一行、`dedup_key` 幂等）→ 经 `semantic_facts` **零额外代码**入 Genny SignalFeed（`macro_print` 记号）与 Chathy 新 `macro_indicators` 工具（tools.py:84/172）。
   - **前端**：懒加载 `/andy/*` 5 页（总览硬度 KPI + 9 灯判定墙 + 锚带 + 勾稽矩阵 / 指标库 / 审讯页 PIT plotly + 关联产业链 Genny 深链 / 过度宣称 / 承重墙）+ 全局 `?as_of=` 控制，teal 强调；plotly 与 Fenny **共享单一懒加载分片**（`components/charts/PlotlyChart.tsx`）；Genny `MacroStrip` 反向勾稽 pill。测试：`tests/andy`（vendored 28）+ `test_macro_links.py` + `test_macro_bridge.py`；`pyproject` packages 增 `src/slx`，新依赖 pyyaml/jsonschema/requests/fredapi。

但作为"机构级投研平台"而非"研究玩具"，仍存在 **5 个走向生产前应修复的硬伤**（API 零鉴权、无任务持久化、事务边界缺失、关键路径无测试、流水线不可恢复）。其中**护城河代码（双时态 / 实体消解 / 对账闸 / 证据闸）恰恰是测试覆盖最薄的地方**——这是最危险的不对称。

> **范围决策**：自用姿态下，多租户 / RBAC / RLS 不列入计划；零鉴权降级为"文档标注硬边界 + 可选 token"；任务持久化降级为"轻量幂等令牌"。真实 P0 收敛为 **数据完整性 + 流水线可恢复 + 护城河测试**。
>
> **范围更新**：上版裁决曾将 exploration 列为"冻结、避免范围漂移"。现已撤销冻结——它已正式记入 README/DESIGN/UI、有独立 API/CLI/schema、并通过独立审计，是平台明确的第三支柱。下文 P1-6 相应改写为"将 exploration 纳入主测试矩阵"。

---

## 一、技术审核（证据导向）

### 🔴 P0 正确性硬伤

#### 1. 全 API 零鉴权 —— "人审中断"护城河在 API 层被架空
- **证据**：`src/xar/api/app.py` 全部端点无 auth；`POST /api/report/{run_id}/approve`（app.py:127）无授权检查；新增的 `POST /api/exploration/refresh`（app.py:438）、`POST /api/ops/sources/{sid}/run`（app.py:337）同样无鉴权即可触发后台烧 LLM 钱的任务。
- **后果**：任何能访问端口者可批准任意报告、触发 ingest/pull/refresh/explore 烧 LLM 钱。唯一边界是"SSH 隧道不暴露端口"——运维约定而非产品边界。
- **自用姿态处置**：文档明确硬边界 + 加可选 `XAR_API_TOKEN` 中间件（5 行，默认关），`/approve` 与各 `*/run`、`/exploration/refresh` 即便自用也建议开（杜绝误触发）。→ 见 **P1-5**。

#### 2. 无事务边界 —— `db.query`/`db.execute` 每次调用 = 独立 autocommit 事务
- **证据**：`storage/db.py:85-95`；`kg/store.py:33-45`（check-then-insert TOCTOU）；`schema.sql:80-96`（`kg_edges` 无 UNIQUE 约束）。Exploration 的 `synthesis.synthesize` 也是"先 DELETE 再多次 INSERT"的多语句序列（synthesis.py:118-144），同样裸在 autocommit 下——并发刷新某域会产生窗口期空态。
- **后果**：并发或重跑会插重复边；多步 KG 写入无原子性；数据完整性地基松动。
- **修复**：见 **P0-1**。

#### 3. 后台任务非持久化 —— 进程崩了任务就没了
- **证据**：ingest/pull/report/exploration-refresh/ops-source-run 全用 `BackgroundTasks`（app.py:109/178/206/343/451 等）；无队列、无重试、无幂等令牌。
- **后果**：进程重启 = 在途任务丢失；连点两次"采集" / 两次"刷新前沿" = 双跑。
- **修复**：见 **P0-4**（轻量 pg-based 幂等令牌，零新依赖）。

#### 4. 报告流水线宣称"可续跑"实际不可恢复
- **证据**：`agents/graph.py:20-49` 总从 scope 重跑；`state.py:68-78` `RunState.load` 无调用者；仅 `BudgetExceeded` 被捕获。
- **后果**：任何瞬时网络错误 kill 整个 run，已烧 token 全废；与 DESIGN §6 "检查点续跑"承诺不符。
- **修复**：见 **P0-2**。

#### 5. 关键路径零测试 —— 护城河代码是测试盲区
- **证据**：`tests/` 仅 ~200 行。未测：双时态 supersession 语义、实体消解模糊路径、debate、evidence_gate 阈值、dedup 并发、预算上限、schema 演进；新增的 exploration 合成/引用校验（synthesis.py:93/125 的 `valid_ids` grounding）亦无测试。
- **后果**：最该测的恰恰没测——把"机构级"招牌挂上去的最大风险。
- **修复**：见 **P0-3**。

### 🟡 P1 工程债

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| 6 | 分析师实为串行，与文档"并行分析师"矛盾 | `nodes.py:110-112` | P1-2 |
| 7 | 实体消解 SQL 与注释不符 + 缺 trigram 索引 | `resolve.py:43-47`；schema 无 `kg_nodes.name` gin 索引 | P1-1 |
| 8 | ~~LLM 价格表硬编码，预算上限会失真~~ **[本轮已解决]** | 价格表改由 `models/registry.py` 的 `_PRICES`（从 MODELS 派生）；计费感知预算：subscription 记 `usd=0`、回退到计量 key 时记真实成本 | ✅ 闭环 |
| 9 | 结构化输出失败静默吞掉 | `llm.py:184` `return schema()` | P0-5（计入 metrics） |
| 10 | CI lint 非阻塞 | `ci.yml:28` `ruff check src \|\| true` | P1-5 |
| 11 | 无 schema 迁移机制 | `schema.sql` 仅 `CREATE IF NOT EXISTS`（含新增 `frontier_*` 两表，schema.sql:324-353） | P1-4（alembic） |

### ✅ 已解决（带证据，保留以记录闭环）

- **[已修复] KG 抽取曾硬编码为"光模块产业"** —— 这是一处潜伏的正确性 bug：旧 prompt 把所有文档都框定到 optical-module industry，导致 chip/software/space/humanoid 的财报/公告抽取被系统性抑制（提取出错误或空的供应链事实）。**现已修复为 theme-aware**：`kg/extract.py` 新增 `_focus_for(company_id)`（extract.py:35-42）+ `_THEME_FOCUS` 映射（extract.py:18-31），按锚定公司的 theme 选择行业框架（软件文档出软件事实、航天文档出航天事实），`_system_for(focus)` 与 prompt 均参数化（extract.py:45-72）。**这是 8 主题扩张能真正落地的前提，价值很高。**
- **[已落地] 新增 arXiv + journals 两个 provider** —— `providers/arxiv.py`、`providers/journals.py`，已在 provider 套件注册（`providers/__init__.py:17,33-34`）并在运维控制台源注册表中以 `category="frontier"` 暴露、可手动触发（`api/ops.py:148-153`，`ops.run_source` 分派见 ops.py:239-241）。均为 green 许可、仅摘要/元数据、key-gated 优雅降级，与既有 13 源同纪律。
- **[已采用] "独立 agent 审计交付物"模式** —— Exploration 模块交付经一个独立 agent 审计 → PASS。该模式本身值得固化为新功能的验收闸之一。**本轮（语义 DB + 日度 ingest，以及随后的 LLM 任务管理器 + 本体富化）进一步固化：多个独立 multi-agent 审计 + 一轮 xhigh code-review 在本会话执行，其 P0/P1 发现均已修复**——LLM 任务管理器 + 本体富化经一轮独立**双重审计（36 agent）+ xhigh `/code-review`，裁决 GO**：全链完整（0 vocab 违规、5 条完整性不变量通过）、常识质量约 3% 错误率（LLM 供应商↔路线混淆 + 多主题过度，均 P1–P3，经确定性 `_CORRECTIONS` 修复）；`/code-review` 另捕获并修复 **P0 subscription 计费洞** + P1 retry-gating/ops null-bucket + P2 retryable-set 及 2 处测试卫生问题；勾稽核查（universe.py↔DB↔vocab）对账干净（43 pytest 全绿、ruff clean、docker 运行）。
- **[已落地] 语义数据库（时间锚定、可回测、本体锚定）** —— 复用既有三张双时态表加列、由 `semantic_facts` VIEW（schema.sql:419，UNION `kg_events` 非 expert 行 + `expert_insights`，insight 臂 LEFT JOIN `kg_events` 以浮出 resolution）统一。抽取侧 `kg/extract.py` 填 `time_orientation` + grounded 因果叙事 + drivers→`causally_linked` 边（extract.py:189；edges.py:41）；检索侧 `graphrag.semantic()` 点查视图，`agents/nodes.py` 注入分析师 brief。所有 schema 对象（加列 + 视图 + `ingest_runs` 表）均加在 `storage/schema.sql`、幂等、`init_schema()` 可重跑。
- **[已落地] 前瞻断言解析生命周期** —— `kg/resolve_claims.py:resolve_forward_claims()`（window_days/grace_days）把 forward_looking 催化剂解析为 hit/miss/stale，只改 forward 行，经 `semantic_facts.resolution` 暴露；CLI `xar resolve-claims`（cli.py:162）。这是评估 GLM-5.2 `SEMANTIC_DB_PLAN.md` 后唯一采纳的净新能力。
- **[已落地] Finnhub / FMP 公司新闻 ingestion（补真实源缺口）** —— `providers/finnhub.pull_news`(+`pull_general_news`)（finnhub.py:140/158）与 `providers/fmp.pull_news`（fmp.py:134）把公司新闻落进 `documents`（source='finnhub'/'fmp'、permission='grey'、仅摘要非全文、content-hash 去重）；`api/ops.py` 注册 `finnhub_news` 源（ops.py:146）+ `run_source` 分派（ops.py:259）；`kg/expert.ALT_SOURCES` 加入 finnhub/fmp（expert.py:29），使新闻同时进 build_kg 与 expert 层。
- **[已落地] 日度自动 ingest（曾列为待办，现真实部署）** —— `orchestration/daily.py:run_daily(stages=('pull','extract'))`：每源增量 PULL（按公司分片、隔离失败）→ parse/embed → build_kg → expert → signals → `resolve_forward_claims`（extract 阶段全局只跑一次而非每片；LLM 阶段预算上限封顶但廉价 DB 阶段照跑）。`storage/runlog.py` + 新 `ingest_runs` 表（schema.sql:443）= 运行日志 + 每源增量游标（last_success_ts）。CLI `xar daily`（cli.py:147）。content-hash + NOT-EXISTS 游标 → 幂等可续。
- **[已落地] Dagster sidecar（日度运行时，现已部署）** —— `orchestration/definitions.py`：`pull_shard`（静态分片、06:00 调度，cron `0 {hour}`）+ `extract_all`（单 run、单批预算、06:30 调度，cron `30 {hour}`，**刻意不分片**因其读全局队列）+ `core_daily`（按需）。`docker-compose.yml` 新增 dagster 服务，host 端口 **3001**（UI/run-history/retries）+ `dagster_home` 卷；仅 app 容器跑 `xar init`（schema owner）。
- **[已落地] LLM 任务管理器（取代旧两档 fast/strong 路由，补齐"无价格感知路由 / 无 fallback / 单厂商"的旧硬伤）** —— 不另起平行系统，扩展 `models/llm.py` + 新增两个文件：(1) **code-as-truth 模型库** `models/registry.py`（`Provider`/`ModelSpec` dataclass + `Billing`/`Capability`/`Status` 枚举；5 厂商(2026-07-19 起第 6 厂商 ollama 本地,见附录 G 追记) deepseek/anthropic/openai/zhipu(=GLM)/moonshot(=Kimi)，token 与 SUBSCRIPTION 两类计费；helper `candidates_for`/`preferred`/`get`/`by_litellm`/`provider_of`；`_PRICES` 由 MODELS 派生——`换代` = 改这一个文件）；(2) **任务路由** `models/router.py`（`TaskClass` 11 类 + `RoutePolicy`/`POLICIES` → `resolve(task)` 候选链；bulk/search 走 CHEAP_BULK + SUBSCRIPTION-first 让 947 公司夜间抽取不撞无界 token 账单，quality 走跨厂商 STRONG 兜底；优先级 `route_overrides` 表 > env `XAR_MODEL_*` > registry `preferred`；`tier=` 经 `as_task` 保留为回退别名，未迁移调用点不变）。`llm.py` 的 `complete()/complete_json()` 新增 `task=` + **fallback 执行器**（按候选取 `_endpoint` 的 api_base/key、跳过未配置厂商、预算感知跳过超额 token 候选并硬停 `BudgetExceeded`、`_retryable` 瞬时错误单次重试、失败/空响应轮转下一候选）；**计费感知成本**：真实包月调用记 `usd=0`（subscription bulk 不撞预算），而 subscription 回退到厂商计量 key 时记真实 per-token 成本（堵上账单洞）；`llm_usage` 加 provider/task_class/billing 列。`config.py` 加 glm/moonshot key + sub key/base + `model_bulk`；`schema.sql` 加 `route_overrides` 表（运行时换代）；`api/ops.py` 的 `/api/ops/llm` 浮出 registry 厂商/模型/路由表 + per-billing/provider/task 花费（旧行标 `legacy`、无 null 桶）+ `set_route()`；`api/app.py` 加 `POST /api/ops/llm/route`（不重部署换代）。`kg/extract.py`/`kg/expert.py` 已从 `tier="fast"` 迁移到 `task="kg_extract"`/`"expert"`。
- **[已落地] 本体富化至策展核深度（全 947 公司）** —— 基础本体（sector/industry/segment/chain_role）此前已 100% 完整；本轮为 569 家批量生成的"宇宙"公司补**深度**（多主题成员、技术路线暴露、更丰富别名、segment 精修）。`scripts/ontology_enrich.py` 经任务管理器走 `task="search_bulk"`（GLM 包月 + DeepSeek 兜底，528 公司约 $0.43），每家公司**严格按本体 vocab 白名单校验**后补充额外主题成员（+该主题 segment）/技术路线标签/别名/更优 primary segment，vocab 外一律丢弃；free-text `suggest_route` 浮出扩展候选；确定性 `_CORRECTIONS` 表编码 18 处审计确认修正；`generate()` 合并 cache+corrections → 重写 `src/xar/ingestion/universe.py`（Python repr）。`ingestion/registry.py` 的 `TECH_ROUTES` 25 → **33**（新增 8 条数据驱动 EXTENSION 路线：`tr_cybersec`/`tr_ddic`(display-driver IC)/`tr_power_semi`/`tr_cv`(computer vision)/`tr_med_imaging`/`tr_pneumatic`/`tr_industrial_gas`/`tr_ceramic_pkg`）。`kg/store.py:bootstrap_seed` 把富化技术路线落为 `uses_techroute` 边（`license_tag='enriched'`），且 `competes_in`(seed) + `uses_techroute`(enriched) 改为从 roster **delete-then-recreate**（store.py:183-191），让 corrections 在 reseed 时干净传播（幂等修复）。实测全 947：multi-theme 公司 80、tech-route 节点 33、`uses_techroute` 边 724（360 enriched）、`competes_in` 1024、`entity_aliases` 3623。

### 🟢 做得好的（保持）
- **tie-out 数值对账闸**（`parsing/tie_out.py`）—— 金融场景的锋利洞察，多数同类产品没有。
- **统一催化剂流**（`kg/signals.py`）—— 把结构化/另类/非结构化蒸馏进同一条 `kg_events`，抽象优雅。
- **theme-aware 抽取的克制设计** —— 一个 `_focus_for` + 一张映射表即把 8 主题的行业框架解耦，没有为每主题分叉 pipeline。
- **Exploration 复用既有栈** —— 不另起炉灶：复用 `documents`/embeddings/`models.llm`/`db` 与同套 FastAPI+SPA chrome，新增面仅两张表 + 一个 domain 注册表（见三、架构审核的专节）。
- **key-gated 优雅降级** —— 真正的交钥匙品味。
- **许可纪律作为 CI** —— 机构级自觉。
- 代码精简（~7.8k LOC 核心支撑四模块）、信噪比高，没有过度抽象。

---

## 二、用户 / 产品审核

**目标用户（已确认）**：自用 / 单团队研究工具。据此，多租户/RBAC/RLS 全部不列入计划。

**产品体验缺口：**
- DESIGN.md §11 自承：结构化/社媒/信号/预测市场端点已就绪但内置 UI 未全部呈现。React 终端漂亮但数据面覆盖不全。
- **KG 纠错回路缺失**（★★ 高价值）：用户发现错误图谱事实（实体消解错了、边过期了）无任何 UI/API 修正。人审应从"批准报告"扩展到"治理图谱"。→ **P1-3**。
- 软指标（crowding/conviction/house view）是 `dashboard.py` 的显式启发式，README 诚实标注"非杜撰"，但对终端用户可能被误读为"信号"。应在 UI 标注"derived heuristic"。
- **Exploration 的"方向性而非交易"姿态需在 UI 显性化**：研究前沿带 maturity/horizon/momentum/confidence，是长期方向判断，不应被误读为可交易信号；前沿 momentum 与投研主轴的 conviction 是两套语义，UI 需明确区隔（indigo shell 已在视觉上分流，但措辞仍需"forward-looking, not a trade"标注）。
- **新公司/新主题接入 = 改 `registry.py` 代码 + re-seed**（手工策展的 8 主题策展核 + 数百条 SEED_EDGES；947 公司中的扩展宇宙现已抽到生成的 `ingestion/universe.py` 并追加进 `COMPANIES`）。curated 垂直知识是真正护城河，但作为静态代码愈发不可持续。→ **P2-3**。

---

## 三、架构审核（可扩展性悬崖）

| 维度 | 现状 | 悬崖/何时到 | 解法 |
|---|---|---|---|
| 向量索引 | IVFFlat lists=100（`db.py:62`） | ~10–100 万 chunk 后召回/延迟劣化 | HNSW（pgvector 0.5+），`lists=sqrt(n)` 自适应 → P2-1 |
| 连接池 | max_size=8（`db.py:39`） | 真并行 ingest/检索即耗尽 | 提到 20–32；热点路径改单事务 |
| 图遍历深度 | graphrag 仅 1 跳（neighbors） | "谁二供 EML"多跳需求靠 prompt 伪造 | 递归 CTE 受限 2–3 跳 → P2-2 |
| 双时态完整性 | 仅 edges/events 双时态；nodes 自身不双时态 | 公司更名/TechRoute 属性随时间变化无法建模 | 现阶段可接受；长期再议 |
| 前沿前沿表完整性 | `frontier_fronts` 每次合成"先 DELETE 再 INSERT"全量替换（synthesis.py:117-118），非时序累积 | 无法追问"某前沿三个月前的判断是什么" | 自用姿态可接受；如需趋势回看再加快照表 |
| 多租户隔离 | 无 | DESIGN §10 自承"数据集范围≠安全边界" | 自用姿态排除，不计划 |

**对"单 PG 收敛"的判断：正确的押注，且被 Exploration 进一步验证。** 把 Neo4j+Qdrant+MinIO+Redis 合进一个 Postgres，运维成本骤降而能力等价，是本项目最聪明的设计决策之一。**应坚持**，不要因"图谱该上 Neo4j"的教条而回退。真正的悬崖只在向量规模和并发，都有渐进解。

### Exploration 模块架构评估（新增专节）

Exploration 是第三个顶层模块（App.tsx:42-45 的 `/explore` 路由 + 独立 indigo shell），但**它在架构上几乎没有增加新的承重墙**——这正是它做对的地方：

- **零新存储 / 零新依赖的复用**：前沿文献与专家声音直接落进既有 `documents` 表，仅以 `meta.frontier=true` + `meta.domain` 打标（ingest.py:41-47/70-75/92-96），因此天然进入既有 embeddings / data-lake / 许可纪律体系。合成结果只新增 `frontier_fronts` + `frontier_domain_state` 两张窄表（schema.sql:324-353）。LLM 走既有 `models.llm` 任务管理器（synthesis 仍以 `tier="strong"` 调用，经 `router.as_task` 回退别名映射到 STRONG 候选链，synthesis.py:113；未迁移调用点无需改动）。`exploration/__init__.py` 明确自述"deliberately reuses the existing stack"——这是与主轴正交但不另起炉灶的范本。
- **`frontier_fronts` 数据模型评价**：以 `(domain, title)` 派生主键 `domain:slug`（synthesis.py:130），字段 direction/significance/maturity(emerging|accelerating|maturing)/horizon(near|mid|long)/momentum(0-100)/confidence(0-1) + `key_papers/key_terms/key_voices` 三个 TEXT[]。模型刻意为"方向性判断"而设，而非交易标的。**值得肯定的两点**：(1) `key_papers` 在写入前用 `valid_ids` 过滤，只保留确实出现在喂入清单里的 arXiv id（synthesis.py:93/125），即引用 grounding + validation，杜绝 LLM 幻觉引用；(2) 合成是"先 DELETE 该域再 INSERT"的全量替换语义（synthesis.py:117-118）——把一次合成视为"当前前沿快照"，符合"前沿是会移动的"这一第一性认知。代价是丢失历史（见上表"前沿表完整性"），自用姿态下可接受。
- **前瞻性而非交易的姿态（架构层强制，而非仅靠文案）**：system prompt 显式要求"Emphasize long-horizon DIRECTION and second-order implications, NOT near-term trades"（synthesis.py:45-51），且整个模块不写 `kg_events`、不进 `signals`、不触碰报告流水线——数据通路上就与交易侧隔离。这是把产品立场固化进数据流而非仅靠 UI 措辞的好做法。
- **源广度与分层**：每个 domain 三层源——arXiv 预印本（前沿原始信号，arxiv.py，public 无 key）、curated 期刊/专业文章（Quanta / Physics World RSS 等编辑层，journals.py）、X 专家声音（handles-only、reply-filtered，只保留策展研究者，ingest.py:60-78）。三层在 prompt 中分块喂入（synthesis.py:100-112），层次清楚。6 域中 `ai` 端到端打通，其余域配置就绪、随源接入逐步点亮（domains.py）。两个 frontier 源已进运维控制台源注册表（ops.py:148-153），与主轴源同一治理面。
- **可改进点（非 P0）**：(1) 合成的全量替换 + 多语句裸 autocommit 在并发刷新同域时有空窗（接 P0-1 的 `transaction()` 即可消除）；(2) grounding 只校验 arXiv id，未校验 journal/voice 引用；(3) 无 momentum/confidence 的回归测试（接 P0-3 / P1-6）。

---

## 四、第一性原理：真正的产品与护城河

剥掉所有"管道商品"（provider 套件、UI、RAG、单 PG），**XAR 真正在卖的只有三样东西**：

1. **编码进代码与图谱的垂直领域知识**（本体 + 10 类催化剂分类法 + cycle 周期本体 + SEED_EDGES + 技术路线节点 + 8 主题 947 公司的策展产业链/周期图）——唯一无法被复制的部分，护城河的河床。**本轮新增的语义层（catalyst 叙事 / stance / 因果 / forward 期望 + 前瞻断言解析）正是这条河床里"结构化数值表装不下"的那一半。** Exploration 的 6 域前沿映射是这条河床向"知识前沿"的自然延伸。
2. **金融场景的信任纪律**（双时态 + 实体消解 + 数值对账闸 + 证据闸 + 人审 + 引用 grounding）——把"LLM 研报"升级为"机构级研报"的工程化方法论。Exploration 的 `valid_ids` 引用校验是同一信任纪律在前沿研究上的复刻。
3. **可控可审计的多 Agent 编排哲学**（确定性 DAG + 一个受限自治岛 + 独立 agent 审计交付物）——与 swarm 套壳的根本分野。

**判定每个新功能的标准：它是否让"垂直知识"更准、或让"信任纪律"更硬？否，则不建。**

**关于 Exploration 的复盘（修正上版判断）**：上一版评审把 `exploration/` 视为"与主轴正交、未记载、范围漂移早期信号"并建议冻结。**现实走向相反且更好**：它被做成了一个有独立 API/CLI/schema/UI、复用既有栈、姿态清晰（方向性非交易）、并经独立审计 PASS 的正式第三模块。它通过了上面的双门槛中的第二条（信任纪律的复刻），且与主轴**共享同一河床**（垂直知识 → 知识前沿）。**结论：撤销冻结，转为正式支柱，纳入测试矩阵与维护承诺。**

---

## 五、可执行改造计划

### 优先级总览

| 档 | 主题 | 任务数 | 周期 | 理由 |
|---|---|---|---|---|
| **P0** | 数据完整性 + 流水线可恢复 + 护城河测试 | 5 | 1–2 周 | 自用姿态下，图谱腐坏与 run 浪费是真实损失；"机构级"招牌需测试背书 |
| **P1** | 工程债 + KG 纠错回路 + exploration 纳入测试矩阵 | 6 | 2–3 周 | 自用用户体验与可演进性 |
| **P2** | 可扩展性前瞻 | 4 | 按需 | 监控触发，不提前 |

---

### P0 —— 必修（数据完整性与可信度地基）

#### P0-1　事务边界 + `kg_edges` 去重唯一性
- **问题证据**：`storage/db.py:85-95`；`kg/store.py:33-45`；`schema.sql:80-96`；exploration 合成的多语句序列 `synthesis.py:117-144`。
- **做法**：
  1. `db.py` 新增 `transaction()` 上下文管理器（一个连接跑多语句、原子提交）。
  2. `schema.sql` 增部分唯一索引：`CREATE UNIQUE INDEX IF NOT EXISTS uq_edges_active ON kg_edges(src_id,dst_id,rel_type) WHERE invalidated_at IS NULL;`
  3. `store.add_edge` 改单事务 `INSERT ... ON CONFLICT DO NOTHING`（去掉前置 SELECT）。
  4. `store.add_event` 去掉冗余前置 SELECT（`ON CONFLICT(dedup_key)` 已兜底）。
  5. `synthesis.synthesize` 的"DELETE 旧 fronts + INSERT 新 fronts"包进同一 `transaction()`，消除并发刷新空窗。
- **验收**：新增 `tests/test_kg_integrity.py`：并发（线程池 N=8）重复 `add_edge` 同一三元组 → 恰好 1 条；重复 `add_event` 同一 dedup → 恰好 1 条；`add_edge` 幂等跨重启；并发 `synthesize(domain)` 不出现空态窗口。
- **工时**：0.5 天　**依赖**：无

#### P0-2　报告 run 真正可 resume
- **问题证据**：`agents/graph.py:20-49`；`state.py:68-78`。
- **做法**：
  1. `run_report` catch 通用异常（除 BudgetExceeded）→ `checkpoint(status='paused')` 记录 `last_node`。
  2. 新增 `resume(run_id)`：`RunState.load` → 从 `last_node` 之后续跑；节点幂等守卫 `if state.has(node_key): return`。
  3. `report_runs.status` 增 `paused` 枚举；CLI/API 暴露 `xar resume <run_id>` / `POST /api/report/{id}/resume`。
- **验收**：`analysts` 节点 mock 抛 `ConnectionError` → run=paused；`resume` 后从 debate 续跑至 published；`llm_usage` 不重复计费已完成节点。
- **工时**：1.5 天　**依赖**：无

#### P0-3　护城河代码回归测试套件
- **问题证据**：`tests/` 仅 ~200 行，双时态/消解/证据闸/dedup/预算零覆盖。
- **做法**：新增 `tests/test_moat.py`（DB-gated、LLM-mocked，沿用 test_pipeline 风格）：
  1. **双时态 supersession**：`supersede_edge` 后 `graphrag.neighbors` 不返回旧边，但 `as_of=旧日期` 仍可查。
  2. **实体消解三层级联**：exact → fuzzy（trigram ≥0.55，learned 缓存）→ create（确定性 sha id 幂等）。
  3. **事件级 dedup**：同 company+type+date+magnitude+route 跨两次插入返回 False 且仅 1 行。
  4. **evidence_gate 阈值**：构造 coverage<0.55 或 risk≥0.5 的 mock → `passed=False` → status=awaiting_approval。
  5. **预算上限**：mock `_spent` 返回超限 → `complete()` 抛 `BudgetExceeded`。
  6. **theme-aware 抽取**：mock 一篇软件公司文档 → `_focus_for` 返回软件 focus（非 optical）；mock 航天公司 → 航天 focus（守住已修复的硬伤不回归）。
- **验收**：`pytest -q` 全绿；`kg/`、`agents/evidence_gate.py`、`models/llm.py` 行覆盖 ≥70%。
- **工时**：2 天　**依赖**：无（可与 P0-1/P0-2 并行）

#### P0-4　后台任务幂等令牌（轻量，不引队列）
- **问题证据**：`app.py:109/178/206/343/451` 等裸 `BackgroundTasks`（ingest/wechat/pull/ops-run/exploration-refresh）。
- **做法**：
  1. 新建 `jobs` 表：`(id, kind, target, status, token, started_at, finished_at, error)`。
  2. 提交时生成 token；存在同 kind+target 的 running/pending 则拒绝（返回 `already_running`）。
  3. `_job` 包 try/finally 写 `status` + `error`（覆盖 exploration refresh 与 ops source run 在内的所有后台入口）。
  4. `/api/jobs` 列出最近任务状态供 UI 可见。
- **验收**：连点两次"采集全部公司" / 两次"刷新全部前沿"第二次返回 `already_running`；模拟异常 → job 记 `failed` + error 可见。
- **工时**：1 天　**依赖**：P0-1 的 `transaction()`

#### P0-5　结构化输出失败可观测
- **问题证据**：`llm.py:184` `return schema()` 静默吞失败（报告流水线与 exploration 合成共用此路径）。
- **做法**：
  1. `complete_json` 失败时在 `RunState` 计 `structured_failures`（保留 warning log）。
  2. `evidence_gate` 把 `structured_failures` 纳入 metrics，报告头标注"抽取失败 N 次"。
- **验收**：mock `complete` 返回非法 JSON → 报告 metrics 含 `structured_failures≥1`，人审可见。
- **工时**：0.5 天　**依赖**：无

---

### P1 —— 工程债 + 闭环 + Exploration 纳入测试矩阵（2–3 周）

#### P1-1　实体消解索引 + alias 纳入匹配
- **证据**：`resolve.py:43-47` 注释/SQL 不符 + `schema.sql` 无 `kg_nodes.name` trigram 索引。
- **做法**：`CREATE INDEX ... USING gin (name gin_trgm_ops)`；SQL 改为 `UNION` 匹配 `name` 与 `entity_aliases`。
- **验收**：`EXPLAIN` 走索引；新增 fuzzy 命中测试。
- **工时**：0.5 天

#### P1-2　真并行分析师（+预算锁）
- **证据**：`nodes.py:110-112` 串行；`_spent` 非原子。
- **做法**：`ThreadPoolExecutor(max_workers=4)` 跑 5 分析师；`_spent` 检查加 `threading.Lock` + DB `SELECT ... FOR UPDATE` 或 in-memory 计数器。
- **验收**：单 run wall-time 下降 ~40%；并发下不超预算。
- **工时**：1 天

#### P1-3　KG 纠错回路（人审的真正闭环）★★
- **动机**：自用姿态下用户即分析师，发现错误图谱事实需修正——把 human-in-the-loop 从"批准报告"扩展到"治理图谱"。
- **做法**：
  1. API：`POST /api/kg/edge/{id}/supersede`、`POST /api/kg/event/{id}/invalidate`、`POST /api/kg/resolve-merge {from,to}`（合并重复节点并迁移边）。
  2. 全部记 `license_tag='human_fix'`、`source='analyst'`，双时态保留前值（不删）。
  3. UI：公司页供应链区每条边/事件加"标记过期/合并"按钮。
- **验收**：测试合并两节点后边迁移、旧边 supersede、`changes_since` 反映。
- **工时**：2 天

#### P1-4　schema 迁移机制（alembic）
- **动机**：在有真实数据前引入成本最低；`CREATE IF NOT EXISTS` 无法表达列变更（8 主题扩张 + `frontier_*` 两表 + 语义层加列/`semantic_facts` 视图/`ingest_runs` 表已让 schema 演进压力变大——本轮虽全走幂等 `ALTER ... ADD COLUMN IF NOT EXISTS`，但仍非正式迁移机制）。
- **做法**：引入 alembic，把 `schema.sql` 转为初始 migration；`xar init` 改 `alembic upgrade head`。
- **验收**：空库与已有库均能 `upgrade`；新增一个改列类型的测试 migration。
- **工时**：1.5 天

#### P1-5　CI lint 硬执行 + 可选 API token　✅ **2026-07-20 落地**（复评修复轮：ci.yml 去 `|| true`；`XAR_API_TOKEN` 中间件默认关，见 CODE_REVIEW 附录 I）
- **做法**：`ci.yml` 去 `|| true`；新增可选 `XAR_API_TOKEN` 中间件（自用默认关，`/api/report/*/approve`、`/api/exploration/refresh`、`/api/ops/sources/*/run` 建议开）。
- **验收**：lint 失败阻断 CI；设 token 后无 token 请求 401。
- **工时**：0.5 天

#### P1-6　Exploration 纳入主测试矩阵（撤销冻结后的对账）
- **背景**：上版本任务为"冻结 exploration"。现 exploration 已转正为第三模块，本任务改为**把它纳入与主轴同等的工程纪律**：
  1. 主仓 CI 跑 exploration 单测（domain 注册表完整性、`valid_ids` 引用 grounding 不放过幻觉引用、maturity/horizon 枚举回退、全量替换语义不残留旧 front）。
  2. arxiv/journals 两 frontier 源纳入 provider 健康自检（ops.py selftest），保持 key-gated 优雅降级断言。
  3. README/DESIGN/UI 已记载，复核三处描述与代码一致（6 域、`/explore` 路由、frontier 源类别）。
- **验收**：`pytest -q` 含 exploration 用例全绿；selftest 报告 arxiv/journals 状态。
- **工时**：0.5–1 天

---

### P2 —— 前瞻（监控触发，不提前）

#### P2-1　向量索引切 HNSW
- **触发**：`chunks` 向量数 > 100k 或检索 p95 > 200ms。
- **做法**：`CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`，保留 IVFFlat 兜底；`ensure_vector_index` 按行数自适应。
- **工时**：1 天

#### P2-2　多跳图遍历（受限递归 CTE）
- **动机**：实现 marketed 的"谁二供 EML"真图查询，而非 prompt 伪造。
- **做法**：`graphrag` 增 `multi_hop(node, rels[], max_depth=3)` 用 SQL 递归 CTE，深度硬上限防爆。
- **工时**：1 天

#### P2-3　公司/主题录入数据化
- **动机**：手工策展的 `registry.py`（8 主题策展核）+ 生成的 `universe.py`（合计 947 公司）是护城河但已临近不可持续。
- **做法**：抽出 `companies`/`seed_edges`/`tech_routes` 为可版本化数据（YAML/SQL seed）+ "新增公司" API；保留代码作回退。同理可把 exploration 的 `domains.py` 一并数据化。
- **工时**：2 天

#### P2-4　可观测性（不自建）
- **做法**：结构化 JSON 日志 + OpenTelemetry trace + 接 Phoenix/Grafana 看成本/延迟；不自建仪表。
- **工时**：1.5 天

---

### 执行顺序与并行度

```
Week 1:  [P0-1 ─ P0-3 ─ P0-5] 并行  (P0-1 先出 transaction() 给 P0-4)
Week 2:  [P0-2 ─ P0-4] 串行依赖 P0-1  → P0 全部完成
Week 3:  [P1-1 ─ P1-2 ─ P1-5 ─ P1-6] 并行  + P1-3 启动
Week 4:  [P1-3 ─ P1-4] 完成 P1
之后:    P2 按 monitor 触发
```

**关键依赖**：P0-1 的 `transaction()` 是 P0-4 与 exploration 合成原子化的前置；P0-3 的测试可全程并行；P1-3（KG 纠错）与 P1-4（迁移）建议在 P0 测试就绪后再动，避免返工。

---

## 六、防屎山 / 防技术债红线（贯穿全程）

- ❌ 不抽象通用 KG/RAG 框架；✅ 保持单 PG 薄层。
- ❌ 不引重型图表库；✅ 手绘 SVG。
- ❌ 不自建多租户 / RBAC（自用姿态已排除）。
- ❌ 不回退 LangGraph；✅ 确定性 DAG + 一个自治岛。
- ❌ 不为 Exploration 另起存储/依赖；✅ 复用 documents/embeddings/llm/db 与同套 chrome。
- ⚠️ 新增数据源需进运维控制台源注册表 + key-gated 优雅降级（arxiv/journals、新增 `finnhub_news`/fmp 公司新闻均已遵循：仅摘要非全文、content-hash 去重、permission='grey'）；非必要不再扩源。
- ❌ **不为语义层另起平行表**；✅ 加列复用 `kg_events`/`kg_edges`/`expert_insights` + 单一 `semantic_facts` VIEW（见 ADR：reuse-not-new-table）。所有 schema 改动走幂等 `ALTER ... ADD COLUMN IF NOT EXISTS` / `CREATE ... IF NOT EXISTS`。
- ❌ 日度 ingest 不引重型队列；✅ `ingest_runs` 表 + content-hash + NOT-EXISTS 游标做幂等可续，由 Dagster sidecar 调度（不侵入 app 容器，仅 app 跑 `xar init`）。
- ❌ **LLM 路由不另起平行系统**；✅ 扩展既有 `models/llm.py` + code-as-truth `registry.py`（LiteLLM 已通 zhipu/moonshot，无新重依赖），`换代` 改一处 ModelSpec / 经 `route_overrides` 运行时改写（见 ADR：reuse-llm-not-parallel-router）。
- ❌ **本体富化不另起新层**；✅ 加深既有 roster——白名单校验 LLM delta 经 `_CORRECTIONS` 合并后写回 `ingestion/universe.py`，`bootstrap_seed` 从 roster delete-then-recreate 派生边（见 ADR：additive-ontology-writeback）。
- ✅ **可规则化的 LLM-output 纠错优先上升为 source 不变量，而非堆 hand-patch 表**：route↔theme 归属由 code-as-truth `registry.ROUTE_THEMES`（33 路线各声明其 home theme(s)）声明，`ontology_enrich._valid()` 在富化源头丢弃 home theme 与公司 theme 集**零交集**的越域路线（如芯片公司被打上航天推进路线），把"供应商↔路线混淆"这一可规则类别从事后 `_CORRECTIONS` 提升为不变量——重跑富化不再重生该错误类（仅守未来富化，非回溯改写；permissive：仅零交集才丢，如 `tr_cv` 对 ai_software/humanoid/ai_chip 均合法）（见 ADR：route-theme-invariant、Appendix H.5）。
- ✅ Exploration 保持"方向性而非交易"：不写 `kg_events`、不进 signals、不触报告流水线。
- ❌ **Fenny 不 fork-and-rewrite、不双向耦合**；✅ **vendor + mount，先挂后并**（`src/fcn` 原样 vendored + 子应用挂 `/api/fenny`），依赖单向 `xar → fcn`（`fcn` 从不 import `xar`），blotter/LLM 经注入点（`blotter_factory`/`route_via_xar`）改道，upstream re-sync 保持机械化（见 ADR：vendor-mount-merge-later、`FENNY_UPSTREAM.md`）。
- ❌ **Andy 不 fork-and-rewrite、不另起第二个数据库**；✅ 复刻 Fenny 的 **vendor + mount**（`src/slx` 原样 vendored + 子应用挂 `/api/andy`），**同一 Postgres 独立 `slx` schema**（search_path 隔离）；上游 Streamlit/dagster/dbt/soda 裁除（调度 = `xar andy` CLI + opt-in `macro` 日源）；勾稽走 code-as-truth `macro_links` + `macro_bridge` 的 `dedup_key` 幂等蒸馏（→`semantic_facts`，零平行信号面）；原生 `/api/andy/link/*` 注册于 mount 之上（顺序不变量）；PIT `knowledge_time<=as_of` 防前视与识别水印直通不可绕过（见 ADR：andy-vendor-slx-schema、`ANDY_UPSTREAM.md`）。
- ❌ **Chathy 不另起后端、不走 HTTP 回环**；✅ 在进程内直调仪表盘同源函数（`chathy/tools.py` code-as-truth 注册表），复用任务管理器的流式/fallback/计费（`TaskClass.CHAT`），≤8 迭代工具循环封顶。
- ❌ **不为 Fenny/Andy 把 plotly 打进主 bundle**；✅ plotly 隔离在懒加载分片（现为 Andy/Fenny 共享的单一分片 `components/charts/PlotlyChart.tsx`），主 bundle 保持精简。
- ✅ **暗色主题走 CSS 变量单一 token 源**（`theme.css` 的 `--c-*` 经 `rgb(var(--c-*))` 供 tailwind 消费），不散落硬编码色值、不引第二套主题机制。
- ✅ 每个新功能过"垂直知识更准 / 信任纪律更硬"双门槛；交付走"独立 agent 审计"（本轮语义 DB + 日度 ingest 经多个独立 multi-agent 审计 + xhigh code-review，P0/P1 发现已修复）。

---

## 七、决策记录（ADR-style）

| 决策 | 选择 | 理由 |
|---|---|---|
| 部署形态 | 自用 / 单团队 | 多租户/RBAC/RLS 不计划；零鉴权降级为文档+可选 token |
| 顶层模块 | 三模块并列：投研主轴 + Operations Console `/ops` + Exploration `/explore`；前端为 Chathy `/` + Andy `/andy` + Genny `/genny` + Fenny `/fenny` 四 shell（原 Research Portal `/` → Genny `/genny`） | 全部复用同一 PG/LLM/embeddings 栈，分立 shell |
| 前端四模块 | **同一 React SPA 拆为 Chathy `/` + Andy `/andy` + Genny `/genny` + Fenny `/fenny` 四并列 shell + 共享 ModuleNav，全部坐既有数据+本体底座** | 前端边界，无平行后端；schema 全加列/加表 additive/幂等（documents.theme/segment、chat_sessions/messages、fenny_blotter；Andy 走独立 `slx` schema） |
| Andy 集成（宏观指标） | **vendor + mount（`src/slx` siliconomics 挂 `/api/andy`），同一 Postgres 独立 `slx` schema（search_path 隔离）** | 复刻 Fenny 先挂后并模式（见 `ANDY_UPSTREAM.md`）；原生勾稽路由 `/api/andy/link/*` 注册于 mount 之上（顺序不变量）；code-as-truth `macro_links`（43/43 指标 + 9 OVERCLAIM_LINKS）+ `macro_bridge` `dedup_key` 幂等 → `semantic_facts` 零额外代码入 Genny/Chathy；PIT `knowledge_time<=as_of` 防前视 + 识别水印直通；上游 Streamlit/dagster/dbt/soda 裁除，调度 = `xar andy` CLI + opt-in `macro` 日源；plotly 与 Fenny 共享懒加载分片 |
| Chathy（对话分析师） | **工具调用 agent，进程内直调仪表盘同源函数（无 HTTP 回环）** | code-as-truth 工具注册表 `chathy/tools.py`；复用任务管理器流式/fallback/计费（新 `TaskClass.CHAT`→STRONG token）；≤8 迭代工具循环；会话落 Postgres |
| Fenny 集成 | **vendor + mount，先挂后并**（`src/fcn` 原样 vendored + 子应用挂 `/api/fenny`，非 fork-rewrite） | 依赖单向 `xar → fcn`（fcn 从不 import xar，保持独立可运行、re-sync 机械化）；LLM 经 `route_via_xar` 走任务管理器、blotter 经 `blotter_factory` 迁 `fenny_blotter` 表；plotly 隔离在懒加载分片保主 bundle 精简（现与 Andy 共享单一分片） |
| 前端主题 | **暗色金融终端：单一 CSS 变量 token 源 `theme.css`（`--c-*`）经 `rgb(var(--c-*))` 供 tailwind 消费** | Bloomberg 风、amber accent、dark-only；不散落硬编码色值、不引第二套主题机制 |
| Exploration 模块 | **转正为正式第三支柱（撤销上版"冻结"）** | 复用既有栈、姿态清晰（方向性非交易）、有独立 API/CLI/schema/UI、经独立 agent 审计 PASS |
| 投研主题 | 8 主题（5 产业链 `kind=chain`：光/芯/软件/航天/人形 + 3 消费周期 `kind=cycle`：互联网/零售/餐饮），947 公司 | "加 theme + 加公司"既定路径，架构未变形；周期主题走 `cycle.py` 周期轴而非 tier 轴；FX 归一 |
| 周期主题轴 | 新本体维度 `ontology/cycle.py`（5 态 `CyclePosition` + `CYCLE_RANK` 兼作 segment tier） | 消费股看经济周期位次而非供应链上下游；热力图渲染为 Cycle Map |
| 语义数据库存储 | **加列复用既有三张双时态表 + 单一 `semantic_facts` VIEW，不另起平行表** | 评估 GLM-5.2 `SEMANTIC_DB_PLAN.md` 后定；additive/幂等/可回测；catalyst 叙事/stance/因果/forward 期望落在数值表装不下的那一半 |
| 前瞻断言解析 | 采纳 `resolve_forward_claims`（hit/miss/stale，只改 forward 行） | 本轮唯一净新能力；闭合"预期→兑现"环，经 `semantic_facts.resolution` 暴露 |
| 日度 ingest 运行时 | Dagster sidecar（`pull_shard` 分片 06:00 + `extract_all` 单 run 06:30），host 端口 3001 | extract 全局只跑一次（不分片）；content-hash + `ingest_runs` 游标 → 幂等可续；仅 app 容器跑 `xar init` |
| KG 抽取框架选择 | **theme-aware `_focus_for`（修复 optical 硬编码 bug）** | 一表一函数解耦 8 主题行业框架，避免 pipeline 分叉 |
| LLM 路由 | **LLM 任务管理器（取代旧两档 fast/strong）**：code-as-truth 模型库 `models/registry.py`（Provider/ModelSpec + Billing/Capability/Status 枚举，5 厂商 deepseek/anthropic/openai/zhipu=GLM/moonshot=Kimi；2026-07-19 增第 6 厂商 ollama 本地）+ 任务路由 `models/router.py`（`TaskClass` 11 类 + `POLICIES` → `resolve()` 候选链）。bulk/search 任务（kg_extract/expert/search_bulk）SUBSCRIPTION 优先（GLM/Kimi 包月→廉价 DeepSeek token 兜底），quality 任务跨厂商 STRONG 兜底。优先级 `route_overrides` 表 > env `XAR_MODEL_*` > registry `preferred`；`tier="fast/strong"` 保留为回退别名 | 计费感知（token 计真实成本并守预算上限、subscription 记 `usd=0`、回退到计量 key 时记真实成本以堵账单洞）+ 跨厂商 fallback 执行器；`换代` = 改 `registry.py` 一处或经 `POST /api/ops/llm/route` 运行时改写，无需重部署 |
| LLM 富化写回（本体） | **加深既有 roster + 写回 `universe.py`，不另起本体层** | 白名单校验 LLM delta + `_CORRECTIONS` 合并 → `bootstrap_seed` 从 roster delete-then-recreate 派生 `competes_in`/`uses_techroute` 边；additive、corrections 干净传播 |
| 技术路线 vocab | `TECH_ROUTES` 25 → 33（+8 条数据驱动 EXTENSION：cybersec/ddic/power_semi/cv/med_imaging/pneumatic/industrial_gas/ceramic_pkg） | 由 `suggest_route` 复现频次驱动，覆盖原 optical/chip 中心集之外的专业化 |
| route↔theme 源不变量 | **code-as-truth `registry.ROUTE_THEMES`（33 路线各声明 home theme(s)）+ `_valid()` 越域门**：富化源头丢弃 home theme 与公司 theme 集零交集的路线 | 把可规则化的"供应商↔路线混淆"从事后 `_CORRECTIONS` 提升为 source 不变量，重跑富化不再重生该错误类；permissive（仅零交集才丢）、forward-only（不回溯改写）（GLM-5.2 follow-up Appendix H.5：lift corrections to invariants） |
| 存储 | 坚持 单 PG + pgvector | 运维成本骤降、能力等价；不回退 Neo4j |
| Agent 编排 | 坚持自建确定性 DAG | 可控可审计；不回退 LangGraph |
| 鉴权 | 可选 `XAR_API_TOKEN` | 自用默认关，`/approve`、`/exploration/refresh`、`*/run` 建议开 |
| 任务持久化 | 轻量 pg job 表 + 幂等令牌 | 零新依赖，够用 |
| schema 演进 | alembic | 在真实数据产生前引入成本最低；`frontier_*` 两表已增演进压力 |
| 测试优先级 | 押护城河代码 + theme-aware 抽取 + exploration grounding | 双时态/消解/对账/证据闸/引用校验是品牌背书 |
| 交付验收 | 引入"独立 agent 审计"模式 | Exploration 已实践且 PASS |

---

*评审基于 2026-06-25 代码快照（分支 `feat/semantic-db-daily-ingest`：8 主题 / 947 公司 / 59 segments / 33 tech-routes / 语义数据库 + 前瞻断言解析 / Finnhub-FMP 新闻 / Dagster 日度 ingest / **LLM 任务管理器（registry+router、计费感知、跨厂商 fallback、GLM+Kimi）** / **本体富化至全 947 公司深度**，43 pytest 全绿、ruff clean、docker 运行：app `:8000` / Dagster `:3001` / Postgres+pgvector）。**2026-07-03 增补**（分支 `feat/chathy-andy-macro`）：对话模块 Andy 更名 **Chathy**；新第四前端模块 **XAR Andy 宏观指标平台**（vendored `src/slx` siliconomics + 勾稽层 `macro_links`/`macro_bridge`，见演进 7）——前端为 **Chathy / Andy / Genny / Fenny 四大模块**。证据均带 `file:line` 可追溯；工时为单人乐观估算，含测试。本文件为活文档，每完成一个 P0/P1 任务即勾选并附 PR 链接。*
