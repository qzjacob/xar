# 开源产业链投研平台 — 设计蓝图

> 围绕一组并行的主题（首发：**AI 光模块 / 光互连产业链**，现已扩展为 **8 大主题**：5 条 AI **产业链**主题（`kind="chain"`，上下游 tier 轴）AI 光互连 · AI 算力芯片 · AI 软件普及 · 太空探索 · 人形机器人，加 3 条**消费周期**主题（`kind="cycle"`，经济周期位置轴）互联网 · 零售 · 餐饮服务），自动汇聚公司公告、财报、研报、新闻、产品页与招聘信息，构建**知识图谱 + RAG 检索系统 + 时间戳化语义数据库**，识别客户、供应商、技术路线、订单、催化剂叙事与前瞻预期变化，并由**多 Agent** 生成深度报告、跟踪摘要与投资启示。在投研主线之外，新增**前沿探索（Exploration）模块**，把同一套文档/嵌入/LLM/数据库栈对准**人类知识的前沿方向**（arXiv 预印本 + 顶刊 + 专家声音 → 前瞻性研究前沿）。

本文档是项目的总体设计与技术选型决策记录。所有选型基于对现有开源生态的检索调研（2026-06），原则是**最大化复用业界最佳开源方案，避免重造轮子**。

> **如何阅读**：§1–§8、§10 是**设计蓝图与决策理由**（保留原貌，供追溯"为什么"）；下面的 **§0 实现现状** 记录**实际交付的精简交钥匙栈**——当蓝图与实现不一致时，**以 §0 与代码为准**。§4/§5/§9/§11 已就已实现能力更新；§2 增补"三大顶层模块 + 前沿探索模块 + 四大前端模块（Chathy / Andy / Genny / Fenny，§2.3–§2.4）"。

---

## 0. 实现现状（As-Built，2026-06）

蓝图的目标是"复用业界最佳开源件"，但落地时为**交钥匙（填一个 API Key 即跑）**做了精简：把多进程重栈（Neo4j + RAGFlow + Graphiti + LangGraph + Dagster + Langfuse + Next.js）收敛为**单 Postgres + 自建薄层**，能力等价、运维成本骤降。蓝图的"护城河"（双时态可引用 KG、可控可审计的多 Agent 流水线、数值对账闸、实体消解、许可纪律）**全部保留**。前端从内置原生单页演进为 **React + TS + Tailwind 单页应用（SPA，由 FastAPI 托管）**，内含**三个对等顶层模块**（投研门户 / 运营控制台 / 前沿探索，见 §2）；**2026-07 再重构为命名前端模块**——默认首页 `/` 改为对话式工具调用分析师 **Chathy**，投研终端下移至 `/genny`（**Genny**，含新数据室），并新增结构化票据 + 期权台 **Fenny**（`/fenny`，vendored `fcn` 子应用），全部落深色金融终端主题；随后新增第四模块**宏观指标平台 XAR Andy**（`/andy`，vendored `src/slx` `siliconomics` 硅基经济指标库 + 勾稽层），前端遂为 **Chathy / Andy / Genny / Fenny 四大命名模块**（见 §2.3–§2.4）。

| 能力 | 蓝图选型 | **As-Built（实际实现）** |
|---|---|---|
| 存储 | Postgres(pgvector+AGE) + Neo4j + MinIO + Redis | **单 Postgres + pgvector**（向量+关系+双时态图谱一库）；对象存储=本地 FS/可选 S3 |
| RAG/解析 | RAGFlow + Docling + MinerU | **自建**：pdfplumber + 分块 + 数值对账闸；Docling 可选(`.[parse-deep]`) |
| 嵌入 | BGE-M3 / Qwen3（TEI/vLLM 服务） | **fastembed**（ONNX，CPU，默认 `bge-small-en-v1.5` 384d；可换 BGE-M3 1024d） |
| 知识图谱 | Graphiti → Neo4j | **自建双时态 KG**（`kg_nodes/edges/events`，supersession + 事件级去重 + 确定性实体消解） |
| 检索 | Graphiti + RAGFlow 混合 | **pgvector 稠密 + pg_trgm 词法，RRF(k=60) 融合 + GraphRAG 遍历** |
| 多 Agent | LangGraph DAG | **自建可控 DAG**：规划→图谱检索→5 分析师→多空辩论→风险→主编→证据闸→人审中断（检查点入 `report_runs.state`） |
| 模型网关 | LiteLLM → Claude | **LiteLLM + LLM 任务管理器**（code-as-truth 模型库 `models/registry.py` + 任务路由 `models/router.py` + `llm.py` 跨候选回退执行器）：按 `TaskClass`（11 类）路由到有序候选链，**计费感知**（token vs 订阅）+ 单次/单批美元预算上限 + 成本计费；bulk/search 任务（`kg_extract`/`expert`/`search_bulk`）订阅优先（GLM/Kimi 平价）、quality 任务（debate/editor/synth）强 token 跨厂回退；运行时换代经 `route_overrides` 表（`POST /api/ops/llm/route`）。**默认 DeepSeek V4**：`XAR_MODEL_FAST=deepseek/deepseek-v4-flash` + `XAR_MODEL_STRONG=deepseek/deepseek-v4-pro` + `XAR_MODEL_BULK`，`XAR_MODEL_EFFORT=high`；`complete_json` 走 JSON-mode 保障结构化输出；一行 env 或注册表 `preferred=True` 即切任意 LiteLLM 模型（`claude-opus-4-8` / `claude-haiku-4-5` / GLM / Kimi）。详见 §6.1 |
| 编排 | Dagster | **已部署**：`orchestration/daily.py run_daily` 每日增量链（CLI `xar daily`）+ Dagster 旁车（`orchestration/definitions.py`：`pull_shard` 分片 06:00 / `extract_all` 06:30，docker-compose 暴露 `:3001` UI/重试/run 历史） |
| 评测/追踪 | Phoenix + Langfuse | 自建检索命中率 + 报告 rubric(LLM-judge)；`llm_usage` 表做成本追踪；Phoenix 可选(`.[eval]`) |
| 前端 | Next.js + Vercel AI SDK | **React + TS + Tailwind SPA**（`web/`，FastAPI 托管编译产物；无构建时回退内置原生单页 `api/static/index.html`，见 `UI.md`） |
| 包/运行 | 多服务 compose | Python 包 `xar`（`pip install` / `docker compose up`），CLI 入口 `xar` |

**蓝图之外、新增实现的能力**（详见 §2/§4/§5）：

1. **结构化数据层** —— 八张表（`fundamentals/estimates/analyst_ratings/prices/insider_trades/prediction_markets/social_posts/holdings`，另有前瞻 `event_calendar`），多 provider 数据按 `source`+`as_of` 共存。
2. **多 provider 行情/另类数据套件（共 12 个 provider）** —— Finnhub · FMP · Polygon · Yahoo(yfinance) · Wind · AIFINmarket(万得 MCP-over-HTTP) · Polymarket · X · Reddit · arXiv · Journals · **RSS（16 条精选行业源，公开无 key）**，key-gated 的缺失自动跳过（`providers.status()` 一览）。
3. **本体标准锚定 + 规范财务词表** —— 领域本体锚定 FIBO/schema.org；`FinMetric` 规范词表统一各 provider 字段命名。
4. **结构化→本体信号桥** —— 估计修正/内部人集群/预测市场异动蒸馏为 `kg_events` 催化剂。
5. **微信公众号接入** —— 经 we-mp-rss 公开 feed 把公众号文章纳入非结构化→本体管线。
6. **专家智能体另类数据加工平台**（`kg/expert.py`）—— 对 X / 公众号 / 资讯 / AIFINmarket 运行领域专家 LLM，按相关性+立场+信号质量过滤，仅保留高信噪比观点（带 entity/stance/quality），写入 `expert_insights` 表 + `kg_events(license=expert)`，是原始召回式抽取之上的信噪比放大层（实测 80 篇公众号→3 条买方级观点，keep-rate 3.75%；在 `/ops/altdata` 呈现）。
7. **X(推特)专家信息源**（`providers/twitter.py`）—— 经 TwitterAPI.io `advanced_search`，按主题精选专家账号 + 领域关键词检索，落 `social_posts`+`documents(source=x)`，经专家加工进本体。
8. **AIFINmarket(万得终端)**（`providers/aifinmarket.py`）—— CN A 股专业源（**MCP-over-HTTP**）：A 股基本面(→`FinMetric`)+公告/资讯(→`documents`→本体)，gated；多市场行情仍由 Yahoo/Finnhub/FMP/Polygon 覆盖。
9. **八大主题（947 公司 / 59 细分）** —— 在 `ingestion/registry.py` 中由 `THEMES`/`SEGMENTS` 驱动，`THEMES[*].kind` 判别 `"chain"`（5 条 AI 产业链，上下游 `tier` 轴）vs `"cycle"`（3 条消费周期，经济周期位置轴，见 §5.5）：chain = `ai_optical`（AI 光互连，4 段）、`ai_chip`（AI 算力芯片，9 段，含跨主题巨头）、`ai_software`（AI 软件普及，9 段，tier=企业 AI 采用浪潮，每段带中文 `thesisCn`）、`space_exploration`（太空探索，8 段，含**太空数据中心/在轨算力**子段）、`humanoid_robotics`（人形机器人，8 段）；cycle = `internet`（互联网，8 段）、`retail`（零售，7 段）、`restaurants`（餐饮服务，6 段）。全球域（US/CN/JP/KR/EU/TW/HK/SG/SE），仪表盘做 FX 归一；市值阈 >$2B（美股 Finnhub，其余 Yahoo+FX 核验）。公司 `themes` TEXT[] 可跨主题，逐主题细分存 `meta.segments`。**Universe 由 ~378 名精选核心扩展到 947 司**（`scripts/universe_build.py` 生成 `ingestion/universe.py`：Finnhub 各交易所符号集作存在性闸 → 逐主题×地域 LLM 枚举 → 确定性核验（存在性 + 去重 + 美股 ≥$2B 市值闸 + 消费非美周期黑名单 + 名↔ticker 同实体校验）→ append 进 `registry.COMPANIES`；地域约 US 356 / JP 223 / TW 143 / KR 134 / CN 77）。
10. **主题感知的 KG 抽取**（`kg/extract.py` `_focus_for()`）—— 按锚点公司所属主题选取产业框架（光互连/芯片/软件/太空/人形），**修复了 prompt 曾硬编码为光模块的潜伏 bug**，使软件 filing 产出软件事实而非光学事实。
11. **前沿探索模块（Exploration，第三个顶层模块）** —— 6 个前沿领域（AI 优先），arXiv 预印本 + 顶刊 + X 专家声音 → LLM 合成前瞻性**研究前沿**；复用 documents/embeddings/LLM/db 栈，新增 `frontier_fronts` + `frontier_domain_state` 两表（详见 §2.2/§5.3/§9）。该交付经独立审计 Agent 复核 → **PASS**。
12. **语义数据库（headline，时间戳化、可回测、本体锚定的语义层）** —— 承载结构化数值表（基本面/估计/价格）**不**承载的内容：催化剂叙事、立场、因果、前瞻预期。设计决策=**加性复用既有三张双时态表**而非另立并行表：`kg_events`（新增列 theme/segment/narrative/time_orientation/resolution/…）+ `kg_edges`（新增 `causally_linked` EdgeType）+ `expert_insights`（新增 as_of/theme/segment/time_orientation），由单一 SQL `VIEW semantic_facts`（UNION）统一点查；抽取（`kg/extract.py`）填 `time_orientation`(forward/backward) + 因果/前瞻 `narrative` + `drivers`（因果实体→`causally_linked` 边）；`graphrag.semantic()` 点查该视图，`agents/nodes.py` 把语义流注入分析师 brief（详见 §5.6）。
13. **前瞻声明解析生命周期** —— `kg/resolve_claims.py resolve_forward_claims()` 闭合"预期→兑现"环：一条有方向的 `forward_looking` 催化剂在窗口内出现同公司 `backward_looking` 兑现型事件时解析为 **hit/miss**（极性一致=hit、相反=miss；按 `COALESCE(event_date, observed_at)` 定时），否则 **stale**（可复查）；**仅** mutate forward 行，经 `semantic_facts.resolution` 暴露；CLI `xar resolve-claims`（详见 §5.6）。
14. **每日自动增量链 + Finnhub/FMP 新闻源** —— `orchestration/daily.py run_daily(stages=('pull','extract'))`：按公司分片的逐源增量 PULL（隔离失败）→ 解析/嵌入 → `build_kg` → expert → signals → `resolve_forward_claims`（extract 全局只跑一次，LLM 阶段有预算上限、廉价 DB 阶段照常跑）；`storage/runlog.py` + 新表 `ingest_runs` = run 日志 + 逐源增量游标（`last_success_ts`），幂等可续（内容哈希 + NOT-EXISTS 游标）；CLI `xar daily`。`providers/finnhub.pull_news`(+`pull_general_news`)/`providers/fmp.pull_news` 把公司新闻落 `documents`（source=finnhub/fmp，permission=grey，自用摘要、内容哈希去重），`api/ops.py` 注册 `finnhub_news` 源，`kg/expert.ALT_SOURCES` 纳入 finnhub/fmp（新闻同入 build_kg 与专家层）。
15. **公司 360 / 投资论点层（2026-07）** —— 类型化 `CompanyThesis`（`ontology/thesis.py`，证据类型化外键 + `validate_thesis` 纪律 + conviction–证据耦合）+ 生成管线（`research/thesis.py`：dossier→build→版本化 `company_thesis`/`thesis_evidence`→零 LLM 健康度）+ **16 维覆盖度**（`ontology/coverage360.py`→`/ops/coverage`）+ **五路数据纵深**（Finnhub 财报日历/篮扫、Yahoo 纵深、EDGAR XBRL+13F、CN 补齐、RSS 框架）+ Company 360 前端；Dagster 调度改默认 RUNNING。详见 §5.9。
16. **微信多层级挖掘系统（2026-07）** —— 在深度抽取**之前**插入一层廉价、GLM 订阅钉扎的 **SNR triage 闸**（`mining/triage.py`），把微信「值不值得深抽」的判断前移：零 LLM 中文预筛（`ontology/cn_routing.py` 8 主题 + 33 tr_\* 中文关键词，补齐此前全英文关键词表的中文缺口）→ 一次 `WECHAT_TRIAGE` 短调用 → 可审计融合 + 小作文地板 + 新颖度救回 → `documents.triage_score`；两条 NULL 安全 WHERE 守卫（`build_kg`/`expert`）按 `triage_score >= 0.4` 门控（未 triage 照旧全流，向后兼容），把「每篇微信 2 次满额 GLM、保留率 3.75%、~96% 额度烧噪音」压到高信噪比文章才耗深度额度。含 T0 论点驱动目标化（`mining/targeting.py`）+ T1 策展名册（`mining/roster.py` + `wechat_accounts`，非关键词搜索）+ 中英嵌入升级（`xar reembed`→e5-large 1024d）。详见 §5.10。

---

## 1. 背景与目标（Context）

- **问题**：单一行业（如 AI 光模块）的投研信息高度碎片化——美股 10-K/8-K、A 股公告/财报、中外研报、产业新闻、厂商产品页、招聘动向分散在数十个来源，且**关系（谁供谁、走哪条技术路线、何时拿到订单）和时间（某事实在某日是否成立）**没有任何工具能结构化呈现。
- **目标产出**：一个自托管平台，把上述信息汇聚成一张**带时间维度、可溯源引用**的产业链知识图谱，并通过多 Agent 流水线产出三类可信制品：**深度报告 / 跟踪摘要 / 投资启示**——每一条结论都能点回到源文件或图谱事实。
- **预期成果**：6 周内交付 AI 光模块垂直切片（约 15 家公司）端到端可用；该 MVP 不是抛弃品，而是长期系统的 P0–P3。**实现现状已扩展到 8 大主题（5 产业链 + 3 消费周期）、947 家公司**，并把同一套引擎延伸到**前沿探索**（投研之外的"知识方向"层）。

### 1.1 关键范围决策（已与需求方确认）

| 维度 | 决策 | 对设计的影响 |
|---|---|---|
| **市场覆盖** | **美股为核心交易市场**；日股/韩股/A 股/港股/欧股/台股等作为**全球供应链互相印证**的投研环节 | SEC EDGAR 是数据脊柱；cninfo/JPX/KRX 等用于交叉验证产业链；全球域行情经 FX 归一 |
| **数据合规底线** | **激进抓取，仅自用**（不对外分发抓取到的数据） | 代码开源（宽松许可），数据私有。免费/非商用 API 层（Finnhub/FMP/yfinance）与研报/招聘的灰区抓取在**自用**前提下可用；研报**全文**仍受版权约束，倾向只入元数据 |
| **模型形态** | **可插拔，默认商业 API** | **默认 DeepSeek V4**（v4-flash 抽取 / v4-pro 推理，经 LiteLLM 网关）；一行 env 可切 Claude（Opus 4.8 推理 / Haiku 4.5 抽取）或任意 LiteLLM 模型；嵌入用 fastembed(bge-small) 默认、BGE-M3 / Qwen3-Embedding 可选，全部可换本地开源 |

> **"自用"的两条纪律**：(1) 开源的是**代码**，必须保持 AGPL/GPL/NC 洁净；(2) 抓取到的**第三方数据**（尤其研报 PDF、新闻全文）**不对外发布/转售**，落库存"事实 + 引用链接"而非原文。

---

## 2. 总体架构

```
                         ┌─────────────────────────────────────────────────────┐
                         │  多 Agent 报告流水线 (LangGraph DAG, 可控+人审)        │  ← 护城河 #2
                         │  规划→图谱检索→并行分析师→多空辩论→风险→主编→证据闸→人审 │
                         └───────────▲───────────────────────▲─────────────────┘
                                     │ 调用(工具)            │ 调用(工具)
                  ┌──────────────────┴──────┐      ┌─────────┴───────────────────┐
                  │  RAG 检索 (RAGFlow)      │      │  时序知识图谱 GraphRAG       │  ← 护城河 #1
                  │  深度解析+引用溯源+混合检索 │      │  Graphiti(双时态) → Neo4j     │
                  └──────────────────▲──────┘      └─────────▲───────────────────┘
                                     │                       │
                  ┌──────────────────┴───────────────────────┴───────────────────┐
                  │  采集与编排 (Dagster 资产化, 增量+血缘)                          │
                  │  EDGAR / cninfo / 研报元数据 / 新闻 / 产品页 / 招聘(ATS API)     │
                  └──────────────────▲────────────────────────────────────────────┘
                                     │
        ┌────────────────────────────┴───────────────────────────────────────────┐
        │  存储: PostgreSQL(pgvector+AGE) · Neo4j(图谱系统-of-record) · MinIO · Redis │
        │  模型网关: LiteLLM → Claude(默认) / 本地开源 ; 嵌入: BGE-M3 / Qwen3 (TEI)    │
        │  可信脊柱: Langfuse(追踪/成本) · Phoenix(离线评测) · TEDS 解析对账闸          │
        └──────────────────────────────────────────────────────────────────────────┘
```

**两个护城河**：(1) 带双时态、可引用的产业链知识图谱；(2) 可控、可审计、强溯源的多 Agent 报告流水线。其余为可复用的"管道商品"。

> **As-Built 提示**：上图描述蓝图层级关系；实际栈见 §0（单 Postgres + 自建薄层、LiteLLM 默认 DeepSeek V4、FastAPI + React SPA）。

### 2.1 三大顶层模块（As-Built，对等并列，各有独立 SPA 外壳）

React SPA（`web/`）由 FastAPI 托管，路由顶层划分为**三个对等模块**，各有自己的外壳、配色与导航；投研门户侧边栏含**模块切换按钮**跳转其余两个模块。

| 模块 | 路由 | 角色 | 外壳 / 配色 |
|---|---|---|---|
| **1. 投研门户（Research Portal ＝ Genny）** | `/genny` · `/genny/segment/:id` · `/genny/company/:id`（旧 `/`·`/segment/:id`·`/company/:id` 302 重定向；`/` 现为 Chathy，见 §2.3） | 投研终端：主题 → 细分 → 公司 → 信号 → 决策。全局 `DataProvider` 上下文供数据 | `Layout`(AppShell) + `Sidebar` + `TopBar` + `DecisionRail`；海军蓝 chrome(`brand`)、蓝色强调(`accent`) |
| **2. 运营控制台（Operations Console）** | `/ops` + 9 子页：overview · ontology · **coverage** · sources · datalake · altdata · models · connectors · skills | 管理控制面：本体/**覆盖度热力（947 × 16 维）**/数据源/数据湖/另类数据/模型/连接器/技能巡检 | `AdminLayout`；琥珀色强调(`warn`)；不挂终端数据上下文 |
| **3. 前沿探索（Exploration）** | `/explore` · `/explore/:sectionId` | **新增第三模块**——人类知识的前沿：6 领域研究前沿、预印本、专家声音 | `ExplorationLayout` + `ExplorationSidebar`；靛蓝"explore"强调(`explore`，token `#6D28D9`) |

设计令牌（`web/tailwind.config.js`）：`brand`(海军蓝) · `accent`(蓝，投研) · `warn`(琥珀，运营) · `explore`(靛蓝，探索) · `pos`/`neg`。

### 2.2 前沿探索模块设计（Exploration，`src/xar/exploration/` + `api/exploration.py`）

**意图**：投研主线追踪"产业链 → 公司 → 催化剂 → 决策"；探索模块把同一套**文档/嵌入/LLM/数据库**基础设施对准**人类知识的前沿方向**，强调**长周期方向（direction）而非个股交易**。它与投研门户的"主题"一一映射成"前沿领域（section）"。

**6 个前沿领域**（`exploration/domains.py`，AI 优先，列表顺序=展示顺序）：

| id | 名称 | 范围 | 主要 arXiv 类目 |
|---|---|---|---|
| `ai` | 人工智能前沿 | 智能体、推理、世界模型、后训练、效率、具身 | cs.AI/cs.LG/cs.CL/cs.CV/cs.MA/stat.ML |
| `physics` | 物理学 | 量子信息、凝聚态、高能理论、引力 | quant-ph/cond-mat.str-el/hep-th/gr-qc/physics.app-ph |
| `math` | 数学 | 数论/几何/组合/概率/最优化 + AI 辅助证明 | math.AG/math.NT/math.CO/math.PR/math.OC |
| `cs_systems` | 计算与系统 | 体系结构、分布式系统、安全密码学、算法 | cs.DC/cs.AR/cs.OS/cs.DS/cs.CR |
| `neuro` | 神经与认知 | 计算神经科学、认知、脑机接口 | q-bio.NC |
| `complex` | 复杂系统与社会 | 经济物理、网络、群体行为、科技地缘（econophysics + geopolitics） | physics.soc-ph/econ.GN/nlin.AO |

**信息源**（均经现有 provider 层，key/posture-gated）：
- **arXiv 预印本**（`providers/arxiv.py`，公开 Atom API，无 Key）——按领域类目拉近 21 天预印本，仅标题+摘要+元数据，落 `documents(source=arxiv, meta.frontier=true, meta.domain=…)`。
- **顶刊 / 专业平台**（`providers/journals.py`，公开 RSS）——Quanta Magazine + Physics World 等编审层文章，落 `documents(source=journal)`；逐领域 feed 映射，失败即返回 `[]` 不抛错。
- **X 专家声音**（`providers/twitter.py`，仅**精选研究者账号**、回复过滤）——只保留 curated handle 的高信号原帖，落 `documents(source=x, meta.expert=true)`。

**合成（`exploration/synthesis.py`，护城河层）**：读近期预印本 + 顶刊 + 专家声音，用**推理层 LLM（`tier="strong"`=v4-pro）**蒸馏每领域 5–7 条**研究前沿**，结构化字段：`title`、`summary`（当下发生什么）、`direction`（1–5+ 年前瞻性论点）、`significance`（二阶意义）、`maturity`（emerging|accelerating|maturing）、`horizon`（near|mid|long）、`momentum`(0–100)、`confidence`(0–1)、`key_terms`、`key_papers`（**只接受出现在所给清单内的 arXiv id，杜绝幻觉引用**）。每次合成代表前沿**当前态**——按领域 `DELETE` 后重写 `frontier_fronts`，并 upsert `frontier_domain_state`（headline + momentum + 计数）。

**存储**：`frontier_fronts`（domain:slug 主键，含 direction/maturity/horizon/momentum/key_papers[]…）+ `frontier_domain_state`（领域 rollup）；见 `storage/schema.sql`。两表**不绑定 company**，与投研侧解耦。

**API**（`api/exploration.py`，只读视图）：
- `GET /api/exploration/overview` —— 每领域一卡（AI 优先），含 headline/momentum/论文-声音-文章计数/Top 前沿。
- `GET /api/exploration/section/{domain}` —— 领域详情（带引用论文的前沿、近期论文、专家声音）；未知领域 **404**。
- `POST /api/exploration/refresh?domain=` —— 后台任务：拉最新预印本/声音 + 重新合成（省略 `domain` 即全量）。

**CLI**：`xar explore [domain] [--days N] [--voices/--no-voices] [--synthesize/--no-synthesize]`（省略 domain 即全部领域，AI 是首个端到端打通的领域）。

**前端**：`web/src/pages/exploration/*`（`ExplorationOverviewPage` / `ExplorationSectionPage` / `_shared`）、`components/ExplorationLayout.tsx` + `ExplorationSidebar.tsx`、`lib/exploration.ts`、`types-exploration.ts`。

**与运营控制台的衔接**：arXiv、journals 两个前沿源已纳入运营控制台**数据源注册表**，归类 `category="frontier"`（`api/ops.py` `SOURCES`，`runnable=true`，公开无 Key），可在 `/ops/sources` 触发拉取。

### 2.3 四大前端模块（As-Built，2026-07）—— Chathy / Andy / Genny / Fenny

在 §2.1 的"投研 / 运营 / 探索"三外壳之上，前端二次重构为**四个命名前端模块**（同一 React SPA、同一 FastAPI 托管、**共享数据 + 本体底座**：8 主题 / 947 公司 / 33 技术路线 / 语义数据库 / LLM 任务管理器不变）。**默认首页从投研终端改为对话式分析师**。共享 **ModuleNav 切换条**（`web/src/components/ModuleNav.tsx`：Chathy | Andy | Genny | Fenny + Explore/Ops 卫星）出现在各外壳 chrome。

| 模块 | 路由 | 角色 |
|---|---|---|
| **XAR Chathy** | `/`（默认首页；`/chathy`→`/`） | ChatGPT 式、流式、**工具调用**分析师：以对话面覆盖全平台，调用仪表盘所用的**同一批进程内函数**（语义事实、混合文档检索、仪表盘、供应链图、公司/细分详情、数据室文档、宏观指标，见 `chathy/tools.py`） |
| **XAR Andy** | `/andy/*`（懒加载，全局 `?as_of=`） | **新增**理论锚定宏观指标平台：vendored `siliconomics` 硅基经济指标库（`src/slx`，自 `github.com/qzjacob/xar-andi`，见 `ANDY_UPSTREAM.md`）；10 理论锚 × 43 指标、双时态 PIT 库、9 条过度宣称登记簿；经勾稽层与产业链本体融合（详见 §2.4） |
| **XAR Genny** | `/genny`（+ `/genny/segment/:id` · `/genny/company/:id` · `/genny/dataroom`） | 既有投研终端**改名并下移**至 `/genny`（旧 `/segment/:id`·`/company/:id` 302 重定向）；新增**数据室**上传→摄取→检索；新增 `MacroStrip` 宏观带（反向勾稽 pill 深链回 `/andy`，见 §2.4） |
| **XAR Fenny** | `/fenny/*`（懒加载） | **新增**结构化票据（FCN/Phoenix/Snowball）+ 期权台：从 `github.com/qzjacob/fenny` **vendored** 的 `fcn` 包（见 `FENNY_UPSTREAM.md`），4 个工作区 |

**Chathy（`src/xar/chathy/` + `api/chathy.py`）**：后端 `models/llm.complete_stream()`（SSE 流式 + function-calling，复用任务管理器回退/计费；新增 `TaskClass.CHAT`=STRONG token）；`chathy/{tools,sessions,agent}.py`（code-as-truth 工具注册表、Postgres `chat_sessions`/`chat_messages`、≤8 轮工具循环）；`api/chathy.py`（SSE `POST /api/chathy/sessions/{sid}/chat` + 会话 CRUD）。前端 `pages/chathy/ChathyPage.tsx` + `components/chathy/*`（react-markdown、工具活动 chip、会话侧栏、fetch+ReadableStream 的 stop/abort）。

**Genny 数据室（`api/dataroom.py` + `pages/genny/DataRoomPage.tsx`）**：上传 PDF/TXT/MD 研报 → 既有 `Doc`/`objects`/`parse_pending` 管线，按主题·细分打标（`documents` **加性列** `theme`/`segment`）→ 分块 + 嵌入 → 可浏览/下载，且**可被 Chathy 检索**。

**Fenny（`src/fcn` vendored + 挂载 `/api/fenny`）**：Monte-Carlo Dupire 本地波动率定价、greeks、期权分析；其 FastAPI 子应用经 `api/app.py` `app.mount("/api/fenny", get_fenny_app())` 挂载；LLM 经 XAR 任务管理器（`src/fcn/service/llm.py` `route_via_xar` 标志）；blotter 迁至 Postgres（`fenny_blotter` 表，`src/xar/fenny/blotter_pg.py PgBlotterStore`）。UI：4 个懒加载工作区（Quotation Desk / Market Read / Underlying Finder / Options Desk，`pages/fenny/{QuoteDesk,MarketRead,Finder,OptionsDesk}.tsx`）带 plotly 收益图——plotly **隔离在懒加载分片内**（现与 Andy 共享同一分片 `components/charts/PlotlyChart.tsx`，见 §2.4），主 bundle 保持精简。

**深色金融终端主题（Bloomberg 风）**：CSS 变量令牌系统（`web/src/styles/theme.css`，`--c-*`）由 `tailwind.config.js` 以 `rgb(var(--c-*))` 消费；琥珀强调、仅深色。

**加性/幂等 schema + 依赖**：`documents.theme/segment`、`chat_sessions`、`chat_messages`、`fenny_blotter` 均 `ADD COLUMN / CREATE TABLE IF NOT EXISTS`。新增依赖：numpy/scipy/python-multipart（后端）、react-markdown/remark-gfm/plotly（前端）；`requires-python` 3.12。Fenny 实盘 IV 需 `MASSIVE_API_KEY`（可选，缺失回退参数化曲面），经 docker-compose `env_file:.env` 注入；上游 `.env` 曾含真实 key（**未 vendored**），应轮换（见 `FENNY_UPSTREAM.md`）。

### 2.4 Andy 宏观指标模块设计（As-Built，2026-07，vendored `src/slx`）

**意图**：给主题/环节级的微观产业链追踪接上一个**理论锚定、防前视**的宏观指标层——宏观「说了什么」与产业链「谁受益」经勾稽层互相印证。上游 `github.com/qzjacob/xar-andi`（`siliconomics` 硅基经济指标库）**vendored 至 `src/slx`**，沿用 Fenny 的 **vendor + mount，先挂后并**模式（溯源/pin/再同步细节见 `ANDY_UPSTREAM.md`，此处不重复）；上游 Streamlit/dagster/dbt/soda 全部裁除，调度 = `xar andy` CLI（`init/ingest[--seed|--connector NAME|--all-real]/identify/evaluate[--sync]/sync-events/status`）+ 每日链 opt-in `macro` 源（`orchestration/daily.py`）。

- **注册表（code-as-truth + YAML）**：**10 条理论锚**（A1–A8 + 2 META）、**43 个指标**（硬度分级：10 hard / 21 medium / 5 soft / 7 条不可量化「承重墙」）、**9 条过度宣称登记簿**（安全 AST DSL 表达式，判定 open / fixation_triggered / falsified / expired / inconclusive）。
- **PIT 纪律（双时态点时库）**：观测三时间轴 `valid_time`（世界时间）/ `knowledge_time`（获知时间）/ `vintage_date`（数据版本）；一切读取过严格 `knowledge_time <= as_of` 防前视守卫——与 XAR 主库的双时态 / PIT 回测同一纪律，前端以全局 as_of 控制（`?as_of=` URL）贯穿。
- **`slx` schema 隔离**：与主库**同一 Postgres**、专用 schema `slx`（`search_path` 隔离）——零新增服务，备份/HA/监控同一套；`xar init` 守卫式执行 andy init（失败不阻塞主 schema）。
- **挂载 + 原生路由顺序不变量**：vendored FastAPI 子应用挂 **`/api/andy`**（`health` · `metrics[/{key}?as_of=]` · `registry/anchors` · `registry/metrics` · `overclaims[/evaluate]`）；XAR 原生勾稽路由 `/api/andy/link/{themes, theme/{theme}?as_of, metric/{metric_key}, sync-events}` **必须先于 mount 注册**（Starlette 按注册序匹配，先注册者遮蔽 mount 前缀下的同路径）——`api/app.py` 中的显式顺序不变量，`api/{andy_mount,andy_links}.py`。
- **勾稽层（crosswalk，code-as-truth）**：`ontology/macro_links.py` 把 **43/43 指标**映射到 主题/环节/技术路线（`scope chain|platform`、`good_when rising|falling|None`、`rationale_zh`），另含 9 条 `OVERCLAIM_LINKS` 带判定极性——宏观↔产业链映射本身即代码、可测试（`tests/test_macro_links.py`）。
- **macro_bridge 幂等蒸馏**：`ingestion/macro_bridge.py` 把指标印字（极性 = 斜率符号 × `good_when`）与过度宣称判定跃迁写为 `kg_events(event_type='macro_print', license_tag='slx')`——逐关联主题一行、`dedup_key` 幂等（重跑不重复，`tests/test_macro_bridge.py`）；经既有 `semantic_facts` 视图**零额外代码**流入 Genny 信号流与 Chathy 工具。
- **识别水印直通**：计量识别引擎（DID + within-FE，真实 t 检验 p 值）的识别等级随指标透出且**逐字直通**——soft 指标标注「未识别·勿作因果」；Chathy 新增 `macro_indicators` 工具（主题面板 PIT 读数 / 反向勾稽 / 全矩阵）与前端审讯页同样呈现。
- **前端**：懒加载 `/andy/*` 5 页——总览（硬度 KPI + 9 灯判定墙 + 理论锚带 + 勾稽矩阵）、指标库、指标审讯页（PIT plotly 图 + 审讯面板 + 关联产业链面板带 Genny 深链）、过度宣称登记簿、承重墙 + 合法性代理；teal 强调 ramp；plotly 与 Fenny 共享**同一懒加载分片**（`components/charts/PlotlyChart.tsx`）。Genny 侧 `MacroStrip`（仪表盘 + 环节页，反向勾稽 pill 深链回 `/andy`）+ SignalFeed 的 `macro_print` 记号。
- **新增配置/依赖**：config 键 `FRED_API_KEY`/`BEA_API_KEY`/`EIA_API_KEY`/`EMBER_API_KEY`/`ACLED_API_KEY`+`ACLED_EMAIL`/`TICKETMASTER_API_KEY`/`SLX_SLACK_WEBHOOK`（全可选，零 key 连接器 sec_edgar/epoch_ai/fhfa/lbnl/indeed_hiring_lab/bls/stooq 开箱即用）；依赖 pyyaml/jsonschema/requests/fredapi；`pyproject` packages = `["src/xar","src/fcn","src/slx"]`；测试 `tests/andy`（vendored 28）+ 勾稽双测。

---

## 3. 技术选型（推荐栈）

> 每一层都标注了**复用的开源项目 + 许可**。许可纪律见 §7。

| 层 | 选型 | 复用的开源项目（许可） | 理由 |
|---|---|---|---|
| **RAG 引擎** | RAGFlow（引擎）；Docling 主解析器；PaddleOCR-VL + MinerU(≥3.1.0) 处理扫描/中文；pdfplumber+Camelot 处理原生电子版规则表 | RAGFlow `Apache-2.0`；Docling `MIT`；PaddleOCR-VL `Apache`；MinerU；pdfplumber/Camelot `MIT` | 唯一一个 Docker Compose 即可自托管、内置深度解析 + **可溯源到 chunk 的引用** + BM25/稠密混合检索 + 融合重排的 Apache 引擎；解析器可换。分级路由（CPU 快路 → VLM）控 GPU 成本。"每条结论必带引用"靠它的检索侧锚点 |
| **知识图谱（核心）** | Graphiti → Neo4j 作系统-of-record；Pydantic 节点/边本体；**确定性实体消解作为一等公民**；可选 Neo4j LLM Graph Builder 做 HITL 摄取/可视化 | Graphiti `Apache-2.0`(双时态+Pydantic 已确认)；Neo4j Community `GPLv3`(外部服务)；LLM Graph Builder `Apache-2.0`；设计参考 iText2KG/ATOM `Apache-2.0` | 唯一一线维护、宽松许可、原生**双时态事实 + episode 溯源**的 KG 框架——正是"有日期、会被后续事实推翻、需按'某日为真'引用"的订单/催化剂所需原语。Pydantic 类型让抽取受 schema 约束 |
| **多 Agent 编排（核心）** | LangGraph 确定性外层 DAG + 受限的多空辩论子图；终端节点 = **图谱溯源的报告合成（非交易决策）**；`interrupt()` 人审 | LangGraph `MIT`；参考重写 TradingAgents `Apache-2.0`、FinRobot `Apache-2.0` | 深度报告是可控、可审计的多阶段流水线，不是放任 swarm。LangGraph 提供确定性阶段序、节点级重试、检查点续跑、原生人审中断。自治仅限辩论子图 |
| **GraphRAG 检索** | Graphiti 原生 图+语义+时序 检索（MVP）；LightRAG 复用同一 Neo4j 做全局主题查询（P4 延后） | Graphiti（同上）；LightRAG `MIT`(延后)；MS GraphRAG `MIT`(参考) | MVP 先做实体/时序检索（"X 在 Q2 的订单"、"谁在某日前二供了 EML"、"GB300 量产时 NVIDIA 已认证的供应商"）；全局主题延后 |
| **模型网关 + 成本控制** | LiteLLM 网关（仅 MIT core）+ **LLM 任务管理器**（注册表 + 任务路由 + 计费感知回退，见 §6.1）；**默认 DeepSeek V4**（v4-flash 抽取/摘要、v4-pro 论点/批判/辩论，high effort）；可经一行 env / 注册表 / 运行时 `route_overrides` 切 Claude（`claude-haiku-4-5` 抽取、`claude-opus-4-8` 推理）或 GLM/Kimi 订阅；稳定 prompt + 共享 filing 上下文开**prompt caching**；按调用计费 + 单次/单批预算上限 | LiteLLM `MIT` core | 供应商无关换模型 + 成本控制（计费/预算/降级/缓存）。按任务路由（订阅优先做 bulk、强 token 做 quality）+ 复用 filing 上下文缓存是控住成本的主要手段 |
| **存储** | 一个 PostgreSQL（pgvector + Apache AGE）；Neo4j Community 作外部网络服务=时序图谱系统-of-record；MinIO 存原始 filing；Redis 单飞/队列；Qdrant 延后（向量 >~1000 万再上） | pgvector `PostgreSQL-license`；Apache AGE `Apache-2.0`；Neo4j Community `GPLv3`(外部进程)；MinIO；Redis；Qdrant `Apache-2.0`(延后) | 向量+关系+次级图谱合到一个 Postgres，2 人团队一套备份/HA/监控。图谱双时态遍历需真正属性图，故 Neo4j 作独立进程跑在网络边界后（不嵌入即不触 GPLv3） |
| **公告/财报采集** | edgartools（美股 10-K/Q/8-K、20-F、XBRL、13F、Form 3/4/5）；AKShare（cninfo 公告 + A 股报表 + 研报**元数据**）；AData（不开代理轮换）取概念篮子；Tushare Pro（积分，可选）；FinanceToolkit 作纯计算库 | edgartools `MIT`；AKShare `MIT`；AData `Apache-2.0`；Tushare 客户端 `BSD`；FinanceToolkit `MIT` | edgartools 免 key 解决 XBRL/HTML，能处理 Fabrinet 的 20-F；AKShare 是唯一原生覆盖 cninfo 公告+财报+研报元数据的宽松库；FinanceToolkit 是即插即用的比率引擎（需补非日历财年 + 20-F 期对齐）。单维护者库要 vendor + 锁版本 |
| **嵌入 + 重排（中英）** | BGE-M3（一模出稠密+稀疏+ColBERT，喂 RAGFlow 稀疏+稠密）主 + bge-reranker-v2-m3；Qwen3-Embedding/Reranker（0.6B/4B）共默认；gte-multilingual-base CPU 兜底；TEI/vLLM 服务；嵌入器保持可换 | BGE-M3 `MIT`；Qwen3-Embedding/Reranker `Apache-2.0`(含权重)；gte `Apache-2.0`；**排除** jina-v3(`CC-BY-NC`) | BGE-M3 单模混合输出直接对上 RAGFlow 稀疏+稠密、中英混合 filing。重嵌入成本真实，前沿在动，故保持可换 |
| **爬取/新闻/招聘** | Scrapy 编排 + Crawl4AI + Playwright + trafilatura/news-please；GNE 仅中文且隔离子进程跑；招聘走 ATS 官方 API（Greenhouse/Lever/Ashby） | Scrapy `BSD`；Crawl4AI `Apache-2.0`；Playwright `Apache-2.0`；trafilatura/news-please `Apache-2.0`；**排除** Firecrawl(`AGPL`)；GNE `GPL`(隔离) | 全宽松许可（GNE 隔离）。ATS API 绕开 LinkedIn/招聘站 ToS+CFAA 雷区。爬取尊重 robots/crawl-delay，**存事实+引用而非转载全文** |
| **编排/调度** | Dagster（Apache-2.0 core）——filing、解析文档、嵌入、KG episode、报告皆为软件定义资产带血缘；sensor/schedule 驱动增量摄取 + 跟踪摘要刷新 | Dagster `Apache-2.0` core；**否决** Airflow(重)、Windmill(`AGPLv3`) | 资产/血缘模型贴合 RAG 摄取+嵌入+KG 流水线，比 Airflow 心智负担低。血缘是快照可复现报告的前提 |
| **可观测/评测/质量闸（信任层）** | Langfuse（MIT core）生产追踪/prompt 版本/成本延迟；Arize Phoenix（ELv2，仅内部自托管）离线 RAG 评测 + LLM-as-judge；**强制 TEDS/数值对账闸**——任何数字进报告前必过 | Langfuse `MIT` core(避 `/ee`)；Phoenix `ELv2`(仅内部) | 财务场景下错误表格抽取会静默产出"言之凿凿却错"的数字，故数值对账闸 + 证据覆盖度指标不可妥协 |
| **前端 / 对话研究 UI（P3）** | 自建 React + TS + Tailwind SPA（由 FastAPI 托管），三模块外壳（投研/运营/探索）+ 工具驱动生成式 UI：带内联引用 chip 的报告查看器、KG 子图可视化、催化剂时间轴 | React/TS/Tailwind；StockBot 仅参考模式 | 工具驱动生成式 UI 是对话研究面的正确 UX；每条 Agent 结论挂引用 chip 链回源 chunk/filing/图谱事实 |

---

## 4. 数据源矩阵（已按"自用"姿态调整）

> 自用前提下，颜色从"是否可商业再分发"重解读为"**自用取数的可靠性与风险**"。仍存"事实+引用"而非转载。

| 类别 | 来源 | 工具 | 自用风险/说明 |
|---|---|---|---|
| 美股 filing + 基本面 | SEC EDGAR (`data.sec.gov`)：10-K/Q/8-K、20-F、XBRL、13F、Form 3/4/5（COHR/LITE/FN/NVDA/MRVL/AVGO/ANET 等） | edgartools（官方免费 API；声明 User-Agent+邮箱；≤10 req/s） | **绿/极低**：美国政府公共领域，完全可用。唯一义务是公平访问限速。流水线的安全核心 |
| A 股法定披露 + 财报 | cninfo（证监会指定披露平台）、SSE/SZSE（中际旭创 300308、新易盛 300502、天孚 300394、博创 300548 及五大主题数百家 A 股标的） | AKShare cninfo/报表端点；限速 + 重试 + schema 校验 + 新鲜度监控 | **绿/公开允许**：强制公开披露可取读聚合，CN 最低风险路径。优先 cninfo 原件而非东财端点 |
| 概念/板块/同业篮子 | AData 多源（光模块/CPO/800G 概念成员） | AData（Apache 代码） | **灰**：底层东财/新浪/THS 是抓取；**生产勿开代理轮换**；仅用于篮子定义；不转售/替代源 |
| A 股归一化报表（可选） | Tushare Pro | Tushare Pro 托管 API（积分制） | **付费**：BSD 客户端许可不含数据权；自用买积分即可 |
| 分析师评级/目标价/公司新闻 | Finnhub（recommendation/price target/EPS/company_news） | finnhub-python（60 calls/min 免费） | **灰/自用 OK**：免费层为非商用（内部 R&D）——**自用契合**；若日后商业化需付费 |
| 电话会纪要 | FMP 或 EarningsCall（earningscall.biz） | Keyed REST / MIT SDK | **付费/版权**：纪要可能含第三方版权；自用可入库做检索，**勿转载**；勿抓 Motley Fool/Seeking Alpha（ToS 封号） |
| **卖方研报（最高风险）** | 东财研报**清单**（标题/机构/评级/目标价/EPS）；全文 PDF 仅授权来源（Wind/Choice/iFinD） | AKShare `stock_research_report_em` **仅元数据**；全文勿入 RAG | **红**：券商研报 PDF 是发行券商版权作品。自用前提下风险降低，但**默认只入元数据**；确需全文再逐源授权。绝不对外再现 |
| 新闻文章 | 公司 IR 页、交易所新闻、财经新闻 | Scrapy+Crawl4AI/Playwright，trafilatura/news-please 抽取（中文 GNE 隔离）；尊重 robots/crawl-delay | **灰/自用 OK**：自用抓取；落库存抽取事实+引用，不存转载全文；无 PII |
| 产品页/规格书 | 模块厂+芯片厂产品/规格页（800G/1.6T 模块、EML/DSP/SiPh 规格） | 定向礼貌爬取；Crawl4AI→markdown→KG 产品节点 | **绿偏**：公开营销页；尊重 robots/ToS；标注链接 |
| **招聘信号** | 公司招聘站的 ATS 公开 board（产能/技术路线信号，如招光器件工程师） | Greenhouse/Lever/Ashby 官方 ATS REST API | **绿/官方 API**：明确**不抓 LinkedIn/招聘站**（ToS+CFAA）。ATS 公开 API 是正道 |
| 行情/快速回填（仅原型） | Yahoo Finance / baostock | yfinance / baostock | **灰**：Yahoo 仅个人用；**仅开发原型期内部用，不进产品**；baostock 维护薄，勿作唯一依赖 |
| **前沿预印本（探索模块）** | arXiv（按领域类目近 21 天预印本：cs.AI/quant-ph/math.*/cs.DC/q-bio.NC/econ.GN…） | `providers/arxiv.py`（官方公开 Atom API，**无 Key**；标题+摘要+元数据） | **绿/极低**：arXiv 元数据公开；仅入摘要不转载全文；落 `documents(source=arxiv, meta.frontier=true)` |
| **前沿顶刊/科普（探索模块）** | Quanta Magazine + Physics World 等编审层 RSS | `providers/journals.py`（公开 RSS，逐领域 feed；失败返回空不抛错） | **绿/公开**：仅入标题+摘要做引用；落 `documents(source=journal)` |
| 统一数据抽象（仅参考） | OpenBB Platform 的 provider 映射设计 | 仅学模式；若用必须作**网络边界隔离微服务** | **红（链接层面）**：AGPL-3.0 网络 copyleft 会传染整个平台。复刻设计、不链接依赖 |

### 4.1 结构化 / 另类 / 前沿数据 provider（As-Built，`providers/`）

> 全部 **key-gated**：缺 Key 即 `available()=False`、`pull()`/`fetch()` 返回空，**不报错**（交钥匙路径零 provider Key 也能跑）。`providers.status()` 统一上报 **12 个 key-gated provider** 状态（含 **Gangtise 投研**）；另有 **`rss`** 公开无 key、恒可用（如 polymarket）。各结构化 provider 字段归一到 `FinMetric` 规范词表（§5.1），落 `fundamentals/estimates/...` 表，多源按 `source`+`as_of` 共存。

| 类别 | provider | 取数 | 姿态/说明 |
|---|---|---|---|
| 基本面/比率 | **Finnhub** | basic-financials(毛利/净利率/PE/PS/ROE…)、EPS/营收估计、recommendation、内部交易 | 灰/自用 OK：免费层非商用=自用契合 |
| 三大报表/估计/价格 | **FMP** | income/balance/cashflow 全字段、analyst-estimates、price-target、日线 OHLCV | 付费/免费层；自用；MCP 可接 |
| 深度行情 + vX 财报 | **Polygon** | 日聚合(深度历史)、vX reference-financials | 付费层；自用 |
| 免费全球行情+快照 | **Yahoo (yfinance)** | 全球价格(含 A 股 300308.SZ)、`.info` 基本面快照 | **无 Key**；`.[market]`；仅自用原型 |
| CN-A 深度基本面 | **Wind 万得** | WindPy 取 A 股报表指标 | 默认关；需本地授权终端；守护降级 |
| CN-A 专业源 | **AIFINmarket(万得)** | A 股基本面(→`FinMetric`) + 公告/资讯(→`documents`→本体) | **MCP-over-HTTP**(base url + token) 或本地 WindPy；`enable_aifinmarket` gated |
| CN 卖方投研 | **Gangtise 投研** | 财报/估值分位/**券商一致预期**(→`FinMetric`/`estimates`) + **投研文本**(一页通/投资逻辑/同业对比→`documents(grey)`→triage/KG→thesis) | **Open API**(AK/SK→loginV2 raw token；`open.gangtise.com`)；CN-only、`enable_gangtise` gated；净新增=卖方一致预期与叙事研究。数据端点用**裸 token**(带 `Bearer` 前缀即 0000001008)；防御资产负债表 companyType/currency 位错 |
| 预测市场 | **Polymarket** | Gamma 公开 API：AI/算力/加速器相关市场的远期概率 | **公开无 Key**；最早的需求侧催化信号 |
| 社媒情绪 / X 专家 | **X (Twitter)** | 经 TwitterAPI.io `advanced_search`：观察标的帖 + 前沿专家账号声音 + 轻量情绪 | 灰/自用；`TWITTERAPI_TOKEN` 或官方 `X_BEARER_TOKEN` |
| 社媒情绪 | **Reddit** | 提及观察标的的帖子 + 轻量词典情绪打分 | 灰/自用；公开回退 |
| 前沿预印本 | **arXiv** | 按领域类目近期预印本（标题+摘要+元数据） | **公开无 Key**（探索模块）；`source=arxiv` |
| 前沿顶刊 | **Journals** | Quanta/Physics World 等公开 RSS 文章（标题+摘要） | **公开无 Key**（探索模块）；`source=journal` |
| 行业资讯 RSS | **RSS** | `ingestion/feeds.py` code-as-truth 注册的 **16 条人工核验行业源 × 8 主题**（SemiWiki/DigiTimes/SpaceNews/Robohub/Retail Dive…）→ 主题标注 `documents(source=rss)` | **公开无 Key**；礼貌抓取（crawl-delay/UA），stdlib 解析 RSS/Atom，存标题+摘要+引用链接不转载全文；内容哈希幂等；CLI `xar pull-rss`，每日链默认源含 `rss` |

### 4.2 微信公众号（As-Built，`ingestion/wechat.py`）

| 类别 | 来源 | 工具 | 姿态/说明 |
|---|---|---|---|
| 公众号文章（国内最快非结构化情报） | 自建 [we-mp-rss](https://github.com/rachelos/we-mp-rss)（登录微信→抓订阅号→暴露公开 feed） | 连接器消费 `{base}/feed/{id}.json|.rss` 与聚合 `/rss`，**零鉴权、stdlib 解析**，无新增依赖 | **灰/自用**：落 `Doc(source=wechat, permission=grey)`，存事实+原文链接做引用、不转载；按中文别名或 `feed→company` 映射归属；**抽取前经多层级 SNR triage 门控深度额度，见 §5.10** |

### 4.3 采集源（ingestion，非结构化→本体）

`edgar`、`cninfo`、`news`、`jobs`、`wechat`（公众号）五个采集源，加上探索模块的两个前沿源（`arxiv`、`journals`），现统一登记进**运营控制台数据源注册表**（`api/ops.py` `SOURCES`），前沿源归类 `category="frontier"`，可在 `/ops/sources` 触发运行。

---

## 5. 产业链知识图谱本体（双时态）

> Graphiti Pydantic 节点/边类型 → Neo4j（As-Built 实现为自建 `kg_nodes/edges/events`，见 §0）。**每个事实带双时间**：`t_valid`/`t_invalid`（世界中为真的有效期）+ 观测/摄取时间（我们何时获知）——后发文档不覆盖先前为真的事实，"某日为真"可查询。每个节点/边/事件带 `source_filing_id` + `license_tag` + `confidence`。**`kg_events` 现加性扩列**承载语义层：`theme`/`segment`（本体锚定）、`narrative`（≤2 句因果/前瞻语境）、`time_orientation`（`forward_looking`|`backward_looking`）、`resolution`/`resolved_at`/`realizes_event_id`（前瞻声明解析生命周期）——全部经 `semantic_facts` 视图统一点查（见 §5.6）。

**多主题与细分**（As-Built，`ingestion/registry.py`）：篮子由 `THEMES`/`SEGMENTS`/`TECH_ROUTES` 驱动，覆盖 **8 大主题 / 947 公司 / 59 细分 / 33 技术路线**（其中 5 条 AI 产业链主题 + 3 条消费周期主题，后者见 §5.5；`TECH_ROUTES` 经本体增强从 25 扩到 33，见 §5.8）。公司 `themes` 为 TEXT[]（可跨主题，如 NVDA/AVGO/MRVL 同属 `ai_optical`+`ai_chip`），逐主题细分存 `meta.segments`（`seg` 字段）；chain 主题细分 `tier` 排上游→下游。五条 AI 产业链主题：

- **`ai_optical`（AI 光互连产业链）**：4 段。上游器件→光模块厂→代工→下游客户。
- **`ai_chip`（AI 算力芯片产业链）**：9 段（含跨主题巨头）。WFE→材料/EDA→晶圆代工→存储/GPU/CPU→先进封装→PCB。
- **`ai_software`（AI 软件普及链）**：9 段。tier=企业 **AI 采用浪潮**（研发与 AI 基础设施/可观测最先放量，如 JFrog/Datadog；CRM/Salesforce 较晚）；每段带中文 `thesisCn`。
- **`space_exploration`（太空探索产业链）**：8 段。发射→推进→卫星→**太空数据中心 / 在轨算力**（SpaceX 为中心的天基算力，非地面 DC）→地面站→组件→应用→防务。
- **`humanoid_robotics`（人形机器人产业链）**：8 段。执行器/谐波减速器/滚柱丝杠→无框力矩电机→传感器→域控/AI 大脑→电池→灵巧手→材料→整机 OEM。

全球域（US/CN/JP/KR/EU/TW/HK/SG/SE），仪表盘做 FX 归一；市值阈 >$2B（美股 Finnhub，其余 Yahoo+FX 核验）。

**主题感知抽取**（As-Built，`kg/extract.py` `_focus_for()`）：抽取的产业框架随锚点公司所属主题切换（光互连/芯片/软件/太空/人形），系统 prompt 因此对软件 filing 产出软件事实、对太空 filing 产出太空事实——**修复了 prompt 曾硬编码为光模块的潜伏 bug**。

**节点类型**（以 `ai_optical` 为例，其余主题同构展开）
- `ModuleMaker`：Innolight 中际旭创(300308.SZ)、Eoptolink 新易盛(300502.SZ)、T&S 天孚(300394.SZ，**同时建模为** UpstreamComponent：无源/精密光学)、Coherent(COHR)、Lumentum(LITE，日益偏组件)、AAOI。属性 `aliases[]/tickers[]/region`
- `ContractManufacturer`（ModuleMaker 子类）：Fabrinet(FN，20-F 境外发行人)
- `UpstreamComponent/Chip`，再细分：(a) 激光芯片 EML/DFB/CW——约束瓶颈，属性 `single_source/oligopoly`（EML 寡头=Lumentum/Coherent/三菱/Broadcom/住友；200G/lane 由 Lumentum 领跑）；(b) DSP/PAM4——Marvell(MRVL)、Broadcom(AVGO)；(c) 光引擎/硅光——TSMC "Coupe" SiPh、Coherent SiPh；(d) 无源/精密光学——MT 插芯、透镜、隔离器(T&S)。TIA/driver 为子组件
- `DownstreamCustomer`：NVIDIA（锚点 + 股权投资人，约各 $2B 入股 COHR/LITE）、超大规模厂(Google/MS/Meta/Amazon)、系统 OEM Arista(ANET)/Cisco。属性 `demand_clock`=NVIDIA GPU 节奏(GB300 2H2025、Rubin 2H2026；配套 NIC/交换 CX8/Quantum-3)
- `TechRoute`（每个带采用曲线属性，**时间版本化，绝不静态**）：400G/800G/1.6T/3.2T 速率代；可插拔 EML vs 硅光(SiPh 约占 800G 30%、1.6T 50%)；CPO(NVIDIA/Broadcom 200G/lane 2026 末，TSMC Coupe)；LPO(线性驱动、去 DSP)；空芯光纤(更长周期)

**边类型**（类型化、schema 约束、时间版本化）：`supplies`、`second_sources`、`single_source_risk`、`uses_techroute`、`invests_in`(股权)、`competes_with`、`substitutes`(技术替代，如 CPO/LPO 威胁 DSP 可插拔、SiPh 替代 EML)、`qualified_by`(客户认证——关键催化剂触发)

**有日期的催化剂/订单事件**（可被推翻的双时态五元组；每条带 company/date/magnitude/polarity/confidence/source_filing_id/affected_nodes/tech_route_tag）：
1. Capex 指引（超大规模厂+NVIDIA——最高阶驱动） 2. 订单（大额 PO，如 Lumentum 史上最大 ELSFP/CPO 订单、框架协议、产能预订——来自 8-K item 1.01 + 中文公告） 3. 客户认证/设计导入（通过 NVIDIA/超大规模厂认证——卡收入，区别于订单） 4. 新品量产（800G→1.6T、200G/lane EML/DSP、SiPh/CPO/LPO） 5. AI 加速器发布（GB300/Rubin/CX8/Quantum-3——外生需求催化） 6. 供应商扩产（Lumentum 约 $663M/+80%、Coherent/Fabrinet——EML 产能卡所有人） 7. 供给约束/短缺（200G EML 至 2027 缺口约 40-60%——负向供给催化） 8. 业绩/指引（run-rate、AI 占比毛利结构) 9. 股权/战略投资（NVIDIA 入股 COHR/LITE——保供信号→`invests_in`边） 10. 价格/结构切换与技术替代（SiPh 降本 vs EML、CPO/LPO 威胁 DSP attach——对 DSP 厂负向、SiPh 正向）

**变化检测**：双时态失效机制呈现"此事实在某日被推翻"，驱动跟踪摘要 diff（自上次快照以来的新订单/认证/capex 修正/催化剂极性变化）。
**实体消解为一等公民**：KG 写入**前**跑确定性规范化层（别名表 + 嵌入相似度）——Innolight=中际旭创=Zhongji、COHR=Coherent=II-VI legacy——高风险边（single_source_risk/订单/认证）走人工复核，因为图谱在此处会静默腐坏。

### 5.1 本体标准锚定 + 规范财务词表（As-Built，`ontology/standards.py`）

**本体决策**——"选最合适的开源本体或自建"：**自建轻量领域本体**（`NodeType/EdgeType/CatalystType`，code-as-truth）+ **锚定两个开源标准**保互操作：
- **FIBO**（Financial Industry Business Ontology，EDM Council，CC-BY）——机构/股权/角色的规范 IRI；
- **schema.org**（Organization/Corporation/Product）——轻量、便于 JSON-LD 导出。

为何不整体采用 FIBO：它穷尽刻画金融工具/合约，却无"光模块二供""CPO 技术路线"这类**垂直概念**；而垂直层用代码建模更快、可测。`node_iri()/edge_iri()` 把任一节点/边导出为 FIBO/schema.org 对齐的 IRI。

**规范财务词表（`FinMetric`）**——结构化数据互通的关键：Finnhub/FMP/Polygon/Yahoo/Wind/AIFINmarket 对同一事实命名各异（`grossProfitRatio` vs `grossMargin` vs `grossMargins`）。每个 provider 经 `canonical_metric(provider, field)` 归一到统一键（revenue/gross_margin/operating_income/capex/eps_diluted…），`fundamentals/estimates` 表因此只说一种语言、多源按 `source`+`as_of` 共存（双时态友好）。

### 5.2 结构化/另类/非结构化 → 本体桥（As-Built，`kg/signals.py`）

把蓝图之外的数据流**全部汇入同一条 `kg_events` 催化剂流**，于是检索、多空辩论、回测对它们与 filing 催化剂**一视同仁**：

- **结构化信号 → 事件**（映射保持在 §5 的 10 类催化剂内，子类记于 summary，见 `SIGNAL_TO_CATALYST`）：
  - 一致预期上修/下修 → `earnings`（极性随方向）；capex 估计跳升 → `capex_guidance`；
  - 内部人集中买入（90 日内多人/超额净买入）→ `equity_investment`；
  - 预测市场高概率（capex/加速器主题）→ `capex_guidance` / `accelerator_launch`。
- **非结构化镜像**：社媒高信号帖、微信公众号文章、AIFINmarket 公告/资讯统一落 `documents`，照常走分块嵌入(RAG) → 主题感知 LLM 抽取 → 双时态本体——与新闻/研报同管线。

### 5.3 前沿前沿（frontier fronts，探索模块本体，As-Built）

探索模块**不挂在产业链本体上**，而是另立两张表（`storage/schema.sql`）：

- **`frontier_fronts`**（`domain:slug` 主键）—— 每条是一个领域内、LLM 合成的前瞻性**研究前沿**：`title`/`summary`/`direction`（1–5+ 年方向论点）/`significance`/`maturity`(emerging|accelerating|maturing)/`horizon`(near|mid|long)/`momentum`(0–100)/`confidence`(0–1)/`key_papers[]`（**仅经校验的 arXiv id，禁幻觉**）/`key_terms[]`/`key_voices[]`。
- **`frontier_domain_state`**（领域主键）—— 领域 rollup：`headline` + `momentum` + `paper_count`/`voice_count`/`front_count` + `synthesized_by`。

设计原则与产业链侧一致：**有证据来源、可溯源、强调方向而非交易**；合成层（`exploration/synthesis.py`）每次按领域整体替换 fronts，反映前沿"当前态"。详见 §2.2。

### 5.4 全经济面本体扩展（As-Built，2026-06）

本体从"光模块形状"扩展为**工业级、跨全经济面**的金融交易本体，分阶段、严格增量（旧枚举值全部保留，泛化靠新增 + `attrs` 子类型；**零迁移框架、仅新增 1 张表**）：

- **行业分类骨干**（`ontology/sectors.py`）—— 自建 GICS 式 **11 Sector / 26 Industry**，锚定**公有领域 NAICS** + schema.org（不照抄 GICS 专有码）。公司经 `(theme, seg)` 自动分类（存量 294 司免改），分类落 `companies.meta.{sector,industry}`。
- **可插拔运营指标包**（`ontology/metric_packs.py`，**护城河中心件**）—— `MetricSpec`（key/label/unit/方向/classifiers）注册表，**168 个规范指标键**覆盖全行业 KPI：软件 `arr/nrr/grr/rpo/crpo/rule_of_40/…`、半导体 `book_to_bill/asp/utilization/hbm_capacity/…`、金融 `nim/cet1/rotce/combined_ratio/…`、能源 `production_boe/lifting_cost/capacity_mw/…`、消费互联网 `mau/dau/arpu/gmv/take_rate/…` 等。**复用长表 `fundamentals`**（KPI = 一个 `metric` 字符串键，无新表）；`kpis_for_company()` 按 industry∪sector∪theme 取用。`FIN_METRICS/RATIO_METRICS` 由 specs 派生（向后兼容）。新行业 = 加一个 pack 列表，零核心改写。
- **类型泛化**（`nodes/edges/catalysts.py`）—— NodeType +13（`Company/Product/EndMarket/Geography/Person/Facility/Regulator/Index/…`）、EdgeType +13（`customer_of/competes_in/sells_into_endmarket/partners_with/acquires/holds_stake/…`）、CatalystType +15（`mna/guidance_change/regulatory_action/litigation/index_inclusion/…`）。`extract.py` 自动按公司行业注入 **KPI 提示**并把 grounded 数值 KPI 写入 `fundamentals`（复用 `_grounded()` 反幻觉闸）。
- **前瞻日历**（`event_calendar` 表）—— scheduled 未来事件（财报/发布/投资者日/PDUFA…），与过去式 `kg_events` 分离；**三源喂入**：FMP（`fmp.pull_calendar`）+ Finnhub（免费层财报日历，`finnhub.pull_calendar[_basket]`）+ Yahoo（分红/拆股/财报日，§5.9）；`dashboard.calendar()` + `/api/ui/calendar`。
- **行业格局 / 行业格局**（`EndMarket` 节点 + `competes_in` 边）—— 每细分一个 EndMarket 节点；`graphrag.landscape()` 给竞争集；`dashboard.landscape()` 按市值份额算 **HHI 集中度** + `/api/ui/landscape`；显式 `market_share` 事实（落 `fundamentals`）并行呈现。
- **运营控制台**（`/ops/ontology`）新增 `sectors` + `metricPacks` 视图，呈现全经济面词表。

> 决策/交易层（Thesis→Signal→Position + Palantir 式 actions，接催化剂回测）——**Thesis 层已于 2026-07 交付**（类型化 `CompanyThesis`，见 §5.9）；Signal→Position 动作层仍后置（规划 P5）。

### 5.5 经济周期本体维度 + 消费周期主题（As-Built，2026-06）

在 5 个 **AI 产业链主题**（供应链上下游 `tier` 轴）之外，新增 3 个 **消费周期主题**（互联网 / 零售 / 餐饮服务）。这三类**不适用产业链上下游追踪**，改用一条新的 code-as-truth 本体维度组织——**经济周期位置**：

- **周期本体**（`ontology/cycle.py`）—— `CyclePosition`（5 态：`early_cycle/mid_cycle/late_cycle/defensive/counter_cyclical`）+ `Cyclicality` + `CycleProfile(position,cyclicality,sensitivity,note)`。单调 `CYCLE_RANK`（"越晚下跌排越后"：early=1…counter=5）**直接用作 cycle 主题细分的 `tier`**，使现有细分排序 / 热力图 / landscape **零改复用**。公司经 `cycle_of_company()` 从其细分继承周期画像（可公司级覆盖），落 `companies.meta.cycle`。直接编码用户语义——**折扣零售 = 逆周期、最晚下跌**（rank 5 > 服装 rank 1）。
- **主题判别**（`registry.THEMES[*].kind`）—— `"chain"`（产业链）| `"cycle"`（经济周期）。新增 3 主题 + 21 细分（按周期位置）+ **美股 roster**（`_consumer()` 构造 84 名精选核心，universe 扩展后约 127 只美股、无 SEED_EDGES）；主要由非美国经济周期驱动的标的（PDD/BABA/YUMC…）**排除**（消费非美周期黑名单）。细分→`industry`/KPI 包复用既有 `internet_media/ecommerce/retail/consumer_staples`，新增 `restaurants` 行业 + `RESTAURANTS_PACK`（same-store-sales/AUV/traffic/check/unit-count…）。
- **消费即可用**（零新表）—— `dashboard` 的 `tier` 直取全部加固为 `.get`，回退细分改 theme-aware；细分/公司 payload 带 `cycle` + `axis`，`coverage` 带 `kind`，`decision()` 文案按 kind 切换（"消费周期组合 / 最强·最弱周期段"）。前端把 `ChainHeatmap` 按 `kind` 重标为 **Cycle Map**（按周期位次排序 + EC/MC/LC/DEF/CC 徽标与图例），主题切换器数据驱动自动出现 3 模块。行业格局/HHI 由细分成员市值算出，**无供应链亦可用**。

> 新增任一周期行业 = 加 `THEMES`/`SEGMENTS`(带 `cycle`)/`_consumer` roster + 可选 `sectors`/`metric_packs` 行，**零核心改写**。

### 5.6 语义数据库 + 前瞻声明解析（As-Built，2026-06）

**意图**：结构化数值表（`fundamentals/estimates/prices`）承载"是多少"，但**不**承载催化剂**叙事 / 立场 / 因果 / 前瞻预期**。语义数据库补上这一层，且是**时间戳化、可回测、本体锚定**的。

**设计决策——加性复用既有三张双时态表，不另立并行表**（`storage/schema.sql`，全部 `ADD COLUMN IF NOT EXISTS` / `CREATE OR REPLACE`，幂等）：
- **`kg_events`** 加列 `theme`/`segment`/`narrative`/`time_orientation`（`forward_looking`|`backward_looking`）/`resolution`/`resolved_at`/`realizes_event_id`；
- **`kg_edges`** 加 `causally_linked` EdgeType（driver→company：某驱动因素引发某催化剂）；
- **`expert_insights`** 加 `as_of`/`theme`/`segment`/`time_orientation`。
- **`semantic_facts` VIEW** 统一二者：`kg_events`(license_tag≠'expert') ∪ 保留的 `expert_insights`，后者 LEFT JOIN 回其镜像 `kg_event` 以浮出 `resolution`。单一可点查视图，暴露 polarity/narrative/time_orientation/resolution。

**抽取（`kg/extract.py`）**：每条催化剂额外产出 `time_orientation`、一条因果/前瞻 `narrative`（"为何发生 / 将驱动什么"，仅证据支持时）、`drivers`（因果实体 → `causally_linked` 边 + `attrs.drivers`）；narrative 走反幻觉闸（事件本身已 grounded）。

**检索**：`retrieval/graphrag.semantic(company_id, theme, …)` 点查该视图；`agents/nodes.py` 把语义流（30 条）注入分析师 brief，与 filing 催化剂一视同仁。

**前瞻声明解析生命周期**（`kg/resolve_claims.py`，唯一净新能力，采纳自对候选方案的评估）：`resolve_forward_claims(window_days=120, grace_days=21)` 闭合"预期→兑现"环——一条有方向（polarity≠neutral）的 `forward_looking` 催化剂，在窗口内出现**同公司**的兑现型 `backward_looking` 事件（earnings/order/product_ramp…）时解析为 **hit**（极性一致）/ **miss**（极性相反），按 `COALESCE(event_date, observed_at)` 定时；否则 **stale**（非终态、可复查）。**仅 mutate forward 行**，经 `semantic_facts.resolution` 暴露，`realizes_event_id` 链回兑现事件。CLI `xar resolve-claims`，并作为每日链最后一步自动运行（§5.7）。

### 5.7 每日自动增量链 + Finnhub/FMP 新闻（As-Built，2026-06）

**每日链**（`orchestration/daily.py`）：`run_daily(stages=('pull','extract'))` 一遍跑全链——
1. **PULL**（per-source，按公司分片 `shard/n_shards`，逐源/逐公司隔离失败）→ 解析/嵌入；
2. **extract（全局只跑一次，非分片）**：`build_kg` → `expert.process` → `signals.derive_market_signals` → `resolve_forward_claims`。LLM 阶段受单批预算上限约束并在此处兜住，使廉价的纯 DB 阶段（signals/resolve）照常完成。

**run 日志 + 增量游标**：`storage/runlog.py` + 新表 **`ingest_runs`**（run 记录 + 逐源 `last_success_ts` 游标）。链路**幂等可续**（内容哈希去重 + NOT-EXISTS 游标）。CLI `xar daily`。

**Dagster 旁车**（`orchestration/definitions.py`，实际部署）：`pull_shard`（`StaticPartitionsDefinition` 静态分片，scheduled `0 {run_hour}`=06:00）+ `extract_all`（单 run、单批预算，scheduled `30 {run_hour}`=06:30）+ `core_daily`（按需）。`docker-compose.yml` 增 `dagster` 服务，宿主端口 **`:3001`**（UI / run 历史 / 重试），带 `dagster_home` 卷；**仅 app 容器**跑 `xar init`（schema owner）。

**Finnhub/FMP 新闻源**（补上真实源缺口）：`providers/finnhub.pull_news`(+`pull_general_news`) 与 `providers/fmp.pull_news` 把公司新闻落 `documents`（source=`finnhub`/`fmp`，permission=`grey`，**抽取事实自用——存摘要非全文**，内容哈希去重）。`api/ops.py` 注册 `finnhub_news` 源 + `run_source` 分支；`kg/expert.ALT_SOURCES` 纳入 `finnhub`/`fmp`，使新闻同时流入 `build_kg` 与专家层。

### 5.8 Universe 本体增强（As-Built，2026-06）

基础本体（sector/industry/segment/chain_role）此前已对全 947 司 100% 完整。本次为 569 家 bulk 生成的"universe"公司**补深度**——多主题成员、技术路线暴露、更丰富别名、细分精化，使其达到精选核心的本体深度并叠加扩展维度。

- **`scripts/ontology_enrich.py`——白名单校验的批量 LLM 增强**：经任务管理器路由（`task="search_bulk"`，GLM 订阅 + DeepSeek 回退，528 司约 $0.43）。逐公司产出（全部严格校验于本体词表，词表外**一律丢弃**）：附加主题成员（+该主题下细分）、技术路线标签、额外别名（原生/罗马化/简称/品牌）、更优主要细分；自由文本 `suggest_route` 字段浮出扩展候选。确定性 `_CORRECTIONS` 表编码 18 条审计确认的修正。`generate()` 合并缓存 + 修正 → 重写 `ingestion/universe.py`（Python repr）。
- **route↔theme 源不变量（code-as-truth `registry.ROUTE_THEMES`）**：33 条技术路线各声明其"主场"主题（公司可合法暴露于该路线的主题集）；`_valid()` 在增强**源头**拒绝任何主场主题与公司主题**零重叠**的路线提案（跨域混淆，如芯片公司被打上空间推进路线）。这把可规则化的"供应商误判为路线"一类**从事后 `_CORRECTIONS` 补丁表上提为源不变量**——重跑增强不再可能重新生成该类错误。宽容设计（仅零重叠才丢；如 `tr_cv` 对 `ai_software`/`humanoid_robotics`/`ai_chip` 三者皆合法——视觉 SoC/ISP 厂亦属 `ai_chip`）；前向守护，非对存量数据的回溯重写。
- **`registry.TECH_ROUTES`：25 → 33**——从复现的 `suggest_route` 中数据驱动新增 **8 条扩展路线**：`tr_cybersec`、`tr_ddic`（显示驱动 IC）、`tr_power_semi`（功率半导体）、`tr_cv`（计算机视觉）、`tr_med_imaging`（医疗影像 AI）、`tr_pneumatic`（气动执行器）、`tr_industrial_gas`（工业/电子特气）、`tr_ceramic_pkg`（陶瓷封装基板），覆盖原光学/芯片集之外的专业化。
- **`kg/store.py bootstrap_seed`**：增强后的 `tech_routes` 落为 `uses_techroute` 边（`license_tag='enriched'`）；`competes_in`(seed) 与 `uses_techroute`(enriched) 现**从 roster 删后重建**（幂等性修复），使修正在 reseed 时干净传播。
- **结果（live DB，全 947）**：多主题公司 80、技术路线节点 33、`uses_techroute` 边 724（其中 360 enriched）、`competes_in` 1024、`entity_aliases` 3623。
- **独立双审（36 agents）+ /code-review（xhigh）裁定 GO**：全链完整（0 词表违规、5 项完整性不变量通过）；常识质量约 3% 错误率（LLM 把供应商误判为路线、多主题过度归属），均 P1–P3，已由确定性 `_CORRECTIONS` 修复。勾稽核查（`universe.py`↔DB↔词表）对账无误。

### 5.9 公司 360 与投资论点层（As-Built，2026-07）

**意图**：把"一家公司我们知道什么"与"我们因此怎么想"合成单一 360° 决策对象——论点（Thesis）是**类型化、可溯源、可被机器复核**的对象而非自由文本；覆盖度（coverage360）是**机器可算**的诚实口径。二者互为约束：覆盖薄 → conviction 有上限、`coverage_gaps` 必须承认。

**投资论点对象（`ontology/thesis.py`，Pydantic 模型兼作 LLM 结构化输出 schema）**：
- **类型化证据外键**：3–6 个支柱（8 类 `PILLAR_KINDS`：demand/moat/supply_chain/technology/financials/valuation/policy/cyclical），每条主张 ≥1 条 `ThesisEvidence(kind, ref_id, quote)` 锚回平台事实——`kind ∈ {event, edge, chunk, insight, fundamental, estimate, registry}`，`ref_id` 必须逐字来自 dossier 事实清单（容错剥 `kind:` 前缀这一常见模型笔误）；每个支柱另带可证伪 `falsifier_zh` 与 `watch_metrics`/`watch_event_types`；对象其余部分：drivers、bull/bear case、变体认知、2–5 条类型化风险（10 类 `RISK_TYPES`）、bull/base/bear 估值情形、what_to_watch、诚实 `coverage_gaps_zh`。
- **`validate_thesis` 纪律（宁可拒绝不可污染）**：立场/支柱数/权重和≈1/证据 kind 与 ref_id 存在性/watch_event_types ⊂ 催化剂词表/watch_metrics ⊂ 规范 KPI 全部硬校验；**conviction–证据密度耦合**——总证据锚 <5 条时 conviction >3 即违规。
- **生成管线（`research/thesis.py`）**：`dossier(cid)` 汇编该公司全部接地事实并给稳定 id（`[event:261]`/`[chunk:8f2]`/`[fundamental:cid:revenue]`…，含宏观勾稽语境与 coverage 缺口标注）→ `build(cid)` 经 **`TaskClass.THESIS`**（CHEAP_BULK 订阅池优先，947 司批量成本有界；`--quality` 走 EDITOR 强档）`complete_json` 产出 → `validate_thesis` 不过则带违规清单重试一次、仍不过**拒绝入库** → 通过则**版本化**写 `company_thesis` + 逐条 `thesis_evidence`，附确定性 quality 指标与 `changed_because` 差异注记；已有版本且无新事实即幂等跳过。
- **论点健康度（零 LLM）**：`health(cid)` 把 `as_of` 之后的新 `semantic_facts` 按支柱 `watch_event_types` × 极性聚合，对照支柱主张方向机器判 **confirming / challenging / mixed / quiet**——新事实到达时论点被自动复核，而非等人重读。
- **入口**：CLI `xar thesis {build[·--theme|--all],show,status}`（批量按覆盖度从高到低走查）· `POST /api/thesis/{cid}/build` · Chathy 工具 `get_thesis`/`coverage_360` · `dashboard.company_detail` 加性 thesis/coverage/estimates/holdings/calendar 五块（论点层异常绝不拖垮公司页）。

**360° 覆盖度（`ontology/coverage360.py`）**：**16 个维度** code-as-truth（身份/文档/已发生催化剂/前瞻日历/指引/财务快照/**财务时序（含 capex）**/预期/评级/行情/**13F 机构持仓**/内部人/供应链边/社媒/专家洞见/论点），每维 = 探针 SQL + 目标行数 + 权重（合计 1.0）；全库一轮 16 条 GROUP BY 批量算 947 司的 0–1 加权分（非 947×16 点查），缺表/失败降级为该维全 0 不崩调用方。用途：`GET /api/ops/coverage` + `/ops/coverage` 主题×维度热力看板、公司页 CoverageRing、采集优先级、论点 conviction/coverage_gaps 的诚实约束。

**五路数据纵深（并行工作流，喂 360°）**：
- **Finnhub**：财报日历（免费层）→ `event_calendar`（接入 `pull`）；速率感知（`_paced_get`）的全篮 news/calendar 扫描（美股 356 名）。
- **Yahoo 纵深**：全球评级/目标价/预期 → `analyst_ratings`/`estimates`；空头持仓+流通盘 → **4 个新 CORE 指标键**（`float_shares`/`short_interest_shares`/`short_ratio`/`short_pct_float`）；分红/拆股/财报日 → `event_calendar`；季度三表 → 带**真实 `period_end`** 的 `fundamentals` 时序（含 capex/FCF——首条真财务时间序列）。
- **EDGAR 纵深**：`ingestion/xbrl.py` company-facts **8 季度**核心科目（restatement 感知、YTD 差分导出离散季度）；`ingestion/holdings13f.py` **29 家核验 CIK** 的管理人 13F → 新 `holdings` 表。
- **CN 补齐**：cninfo 研报元数据的**确定性评级解析**（买入/增持等 5 档 + 目标价 → `analyst_ratings`，零 LLM，姿态仍"仅元数据"）；`kg/repair.py` 孤儿 `kg_events` 保守重指（与 `kg/resolve` 同阈值）+ theme/segment 锚回填，幂等。
- **RSS 框架**：`ingestion/feeds.py` code-as-truth 注册的 **16 条人工核验行业源 × 8 主题** + `providers/rss.py`（stdlib 解析、礼貌抓取、内容哈希幂等）→ 主题标注 `documents(source=rss)`；CLI `xar pull-rss`，`rss` 已入每日链默认源。

**Schema（加性、幂等）**：`company_thesis`（版本化论点：stance/conviction/content/quality）· `thesis_evidence`（逐条类型化证据行，FK→`company_thesis`）· `holdings`（13F 机构持仓）。**Ops 补刀**：Dagster 调度改**默认 RUNNING**（`DefaultScheduleStatus.RUNNING`——此前调度默认 STOPPED，夜间增量从未自动触发过）；夜跑 no-op 的另一根因是边车环境缺 provider key（compose 两容器均 `env_file: .env`，FMP key 已在本地 `.env` 激活）。

**前端（Company 360）**：`CompanyPage` 新增 **ThesisSection**（立场/信念度/支柱带证据 chip + 证伪框 + 健康度灯、多空/风险/估值/watch 时间线/缺口/质量条 + 就地 build/rebuild）、**CoverageRing**（16 维径向环）、**CompanyDataPanels**（预期/持仓/日历面板）；`/ops/coverage` = **CoveragePage** 主题×维度热力（枚举访问 crash-proof，敌意 payload SSR 冒烟 11/11）。

### 5.10 微信多层级挖掘（As-Built，2026-07，`mining/` + `ontology/cn_routing.py`）

**意图**：公众号是国内产业链最快的非结构化情报源，但信噪比极低。旧管线对**每篇**微信文章无差别发**两次满额 GLM 调用**（`build_kg` + `expert`），SNR 判断在昂贵调用**内部**、事后才丢弃——实测保留率 **3.75%**、约 **96% 的 GLM 订阅额度烧在噪音上**。本模块把「值不值得深抽」的判断**前移到深度抽取之前**，插入一层廉价、GLM 订阅钉扎的 **SNR triage 闸**，形成 **T0→T4 分层**：

- **T0 目标化（`mining/targeting.py`，零 LLM）**：从**被信号/事件挑战的活跃论点**（`thesis_signals.challenged_companies`）反推挖掘目标——被挑战公司优先，每个 `MiningTarget` 带中文别名、主题/技术路线、论点 `watch_event_types`/`what_to_watch` 中文盯盘项与派生的中文猎词（别名 + 主题词 + 路线词，经 `cn_routing`）。供名册采集优先级 / triage 队列重排 / ops 可见性 / 未来搜索查询源。
- **T1 策展采集（`mining/roster.py` + `wechat_accounts` 表）**：运营方在 we-mp-rss UI 订阅垂直号后登记 `feed_id → theme/segment/company_id/tier`；`glm_worker._wechat` 逐号拉取并带公司绑定（单号失败不沉整轮），**名册空则退回聚合 `/rss`**。**决策：策展名册而非关键词搜索**——公众号搜索不可靠且会触发账号限流/封禁，故 deliberately not built。
- **T2 抽取前 SNR 闸（`mining/triage.py`，`WechatTriage` schema）**：① **零 LLM 确定性预筛**——中文路由（theme/route）命中 / 别名命中 / 可解析到覆盖公司，**全不命中即打噪音地板分 `_NOISE_FLOOR=0.03` 并跳过 LLM**（免费滤掉盲目名册的闲聊，不耗额度）；② 幸存者 → **一次** `WECHAT_TRIAGE` 短 prompt 调用，注入命中主题/路线 + **已知 KG 摘要**（`graphrag.semantic`，供新颖度对照）+ 公司活跃论点 `watch_event_types`（支柱命中）；③ **可审计融合**：`0.35·priority + 0.25·credibility·(¬is_xiaozuowen) + 0.20·novelty + 0.20·specificity`，叠 **小作文地板**（`is_xiaozuowen ∧ credibility<0.4 → ≤0.15`）与 **novelty·specificity 救回**（`max(score, 0.55·novelty·specificity)`，补微信无阅读数、救回冷门号扎实一手信息）→ 写 `documents.triage_score/triaged_at/triage`；ingest 期 `company_id` 为空时按 triage 解析结果**回填**（`resolve` 阈值 0.62）。
- **T3/T4 深度抽取（`kg/extract.build_kg` + `kg/expert.process`）= 原来的两次 GLM 调用**：现由**两条 NULL 安全 WHERE 守卫**（`mining.triage.wechat_pending_clause`：`d.source <> 'wechat' OR d.triage_score IS NULL OR d.triage_score >= wechat_deep_min`）门控——`triage_score >= 0.4` 才进队列，**未 triage（NULL）照旧全流、非微信短路完全不受影响**（加性、向后兼容；关 `wechat_miner_enabled` 即退回旧的无差别抽取）。

**同周期排序不变量**：`glm_worker._llm_stage` 在 `llm.pinned(GLM_PIN)` 内**先 `triage_pending` 后 `build_kg`**——同一轮新拉进来的微信文档必须先打分，才能被本轮的深抽守卫正确门控。**GLM 订阅纪律**：triage 走新 `TaskClass.WECHAT_TRIAGE`（`CHEAP_BULK` + `SUBSCRIPTION` 优先，链外**永不回退计量 token**，见 §6.1），与夜间批量抽取共享同一封顶订阅池。

**中文路由表（`ontology/cn_routing.py`，code-as-truth）**：此前所有关键词表（`agents/nodes._THEME_TERMS`、`providers/twitter.DOMAIN_TERMS`、`polymarket`、`registry._TECH_ROUTE_HINTS`）都是英文/ASCII，只有公司**别名**是中文——中文微信文章若不点名公司又不含英文技术词就无法路由。本表补齐这一层：**8 主题 + 33 tr_\*** 的中文关键词；`theme_hits`/`route_hits`/`route_themes` 供 T0/T2 消费；`tests/test_cn_routing.py` 断言每个 key ∈ `registry.THEMES`/合法路线（镜像 `ROUTE_THEMES` 合法性不变式，防本表与本体漂移）。

**Schema（加性、幂等）**：`documents.triage_score/triaged_at/triage(JSONB)` + 部分索引 `idx_docs_triage_pending`；`wechat_accounts`（策展名册）。**Config**：`wechat_miner_enabled`（默认开）/ `wechat_deep_min=0.4` / `glm_worker_triage_docs=40`。**入口**：CLI `xar wechat-mine [--once|--stats]` · `xar wechat-account {add,list,rm}` · `xar wechat-targets`；API `GET /api/ops/wechat-mining`（triage 保留率 vs 旧 3.75% + 名册 + 猎取目标）。

**中文嵌入升级**：中英混合语料（中文公众号/cninfo + 英文 filing）的检索质量可经 `xar reembed` 全库重嵌——fastembed 无 bge-m3，故升级路径为 `intfloat/multilingual-e5-large`（1024d），`models/embeddings.py` 对 e5 系列自动加 `query:`/`passage:` 不对称前缀；config 默认仍 bge-small（384d，交钥匙），部署经 `.env`（`XAR_EMBED_MODEL`/`XAR_EMBED_DIM`）切换，`reembed` 就地 ALTER `chunks` 维度 + 分批重嵌 + 重建 ANN 索引。

---

## 6. 多 Agent 报告流水线

**可控 LangGraph DAG**（确定性、检查点、低温、结构化输出、LiteLLM 单次预算），仅含**一个受限自治岛**（多空辩论子图）。RAG + 时序 KG + 实时数据是 Agent 调用的**工具**；**无源不出结论**（每条挂 RAGFlow chunk / EDGAR-cninfo filing ID / 双时态图谱事实 + 有效期）。模型路由经 **LLM 任务管理器**（§6.1）按 `TaskClass` 分派：抽取/摘要/分类（`kg_extract`/`expert`/`analyst`/`judge`）走快/廉价候选链，论点/批判/辩论（`debate`/`editor`/`synth`）走强 token 候选链（默认 `deepseek-v4-pro`，high effort、结构化输出，跨厂回退）；运行时经 `route_overrides` 或一行 env 即可切 `claude-haiku-4-5` / `claude-opus-4-8` / GLM / Kimi。

**节点**
1. **规划** — 解析请求类型（深度报告 | 跟踪更新 | 单催化剂启示）+ 篮子，经实体消解映射到规范图谱实体；锁定版本化数据快照
2. **图谱检索** — 双路：(a) 实体/时序遍历取目标子图（供应商/客户/窗口内有效订单/催化剂/single_source_risk 边）；(b) [P4] LightRAG 社区摘要。返回带 source_filing_id + 有效期
3. **并行分析师**（各自基于 RAG 检索 + 双时态 KG，输出结构化带引用结论）：基本面（FinanceToolkit 比率、同业期对齐含非日历财年+20-F、估值）；公告/催化剂（把 8-K item 1.01 + cninfo 公告解析为有日期的催化剂事件，双时态回写 KG）；供应链/KG（多跳遍历：谁二供 EML、单一来源风险、NVIDIA `invests_in`边、技术替代威胁）；情绪/新闻（中英双语极性）；估值（DCF/WACC/多重 + KG 派生需求时钟）
4. **多空辩论子图**（唯一涌现区，受限：迭代上限、低温、结构化输出、LiteLLM 预算），仅基于检索到的图谱事实。拓扑取自 TradingAgents
5. **风险节点** — 压测论点（EML 缺货、CPO/LPO 颠覆 DSP attach、客户集中、单一来源暴露）
6. **主编合成** — FinRobot Data-CoT→Concept-CoT→Thesis-CoT 结构 + 报告模板；产出 深度报告/跟踪摘要/启示；每行可溯源到图谱证据
7. **批判 / 证据覆盖度闸** — LLM-as-judge 校验每个数值/关系结论是否解析到 TEDS 校验过的源或带有效引用的图谱事实；算证据覆盖度 + 幻觉风险（FinSight-AI 指标）；低于阈值回退到相应分析师/检索
8. **人工审批** — LangGraph `interrupt()` 暂停供分析师编辑/签发状态；强制非投资建议免责声明；任何内容不自动以建议形式呈现

**一图三品**：(a) 深度报告（按需全论点）；(b) 跟踪摘要（Dagster 调度的 vs 上一快照 diff——双时态"自某日变化了什么"；只重跑催化剂+KG delta+摘要节点）；(c) 启示（图谱引用的要点列表）。
**护栏**：检查点续跑；节点级重试；辩论迭代上限 + LiteLLM 单次预算；每份报告快照版本化并绑定其数据快照；每条结论带溯源 + 许可标签；KG 写入前跑确定性实体消解。端到端经 Langfuse 追踪（按节点 token/成本/延迟）；Phoenix 上对留出报告集做离线回归。

> **探索模块的合成层**与本流水线**共享同一模型网关与预算纪律**，但是**单节点**前沿合成（`task="synth"`，强 token），不含多空辩论/人审中断——它产出方向性研究前沿而非可交易结论。

### 6.1 LLM 任务管理器（As-Built，2026-06）

旧的"两级（fast/strong）路由"已升级为**任务管理器**——**扩展** `models/llm.py`、**不引入并行系统、不增重依赖**（LiteLLM 本就讲 `zhipu/` 与 `moonshot/`）。三件：

- **`models/registry.py`——code-as-truth 模型库（可更新的"模型库"）**：`Provider` + `ModelSpec` 数据类；枚举 `Billing`(token|subscription) / `Capability`(fast|strong|reasoning|long_context|cheap_bulk) / `Status`(active|preview|deprecated)。`PROVIDERS`：deepseek、anthropic、openai、**zhipu(=GLM)**、**moonshot(=Kimi)**。`MODELS`：token 模型（DeepSeek v4-flash/pro、Claude opus/haiku/sonnet）+ GLM/Kimi **订阅**条目。辅助 `candidates_for(capability, billing_pref)`（billing 优先稳定排序、保留 token 回退尾）、`preferred`、`get`/`by_litellm`/`provider_of`。**换代 = 改这一个文件**（加 `ModelSpec`、置 `preferred=True`、旧的翻 `deprecated`）；`_PRICES` 从 `MODELS` 派生。
- **`models/router.py`——任务路由**：`TaskClass`（11 类：kg_extract、expert、search_bulk、analyst、debate、editor、judge、synth、eval、adhoc_fast、adhoc_strong）+ `RoutePolicy` + `POLICIES`；`resolve(task)` 返回有序候选链。bulk/search 任务 → `CHEAP_BULK` + 订阅优先（GLM/Kimi 平价，使对 947 司语料的夜间抽取**永不跑出无界 token 账单**），再接预算内的廉价 DeepSeek token；quality 任务（debate/editor/synth）→ 强 token + 跨厂回退。解析优先级：`route_overrides` 表（ops API）> env（`XAR_MODEL_*`）> 注册表 `preferred`。`tier="fast|strong"` 作向后兼容别名（`as_task`），未迁移调用点不变。
- **`llm.py` 回退执行器**：`complete()`/`complete_json()` 新增 `task=`；逐候选取 api_base/key（`_endpoint`）、跳过未配置 provider、**预算感知**跳过超额 token 候选 + 硬停 `BudgetExceeded`、瞬时错误一次候选内重试（`_retryable`）、失败/空则轮转下一候选。**计费感知成本**：真订阅调用记 `usd=0`（订阅 bulk 不触预算闸），订阅 spec 回退到 provider 计量 key 时记**真实**按 token 成本（堵上计费漏洞）；`llm_usage` 增 `provider/task_class/billing` 列。

**运营接入**：`api/ops.py` 的 `/api/ops/llm` 现呈现注册表 vendors/models/路由表 + 按 billing/provider/task 的花费（历史行标 `legacy`、无 null 桶）+ `set_route()`；`api/app.py` 暴露 `POST /api/ops/llm/route`（不重部署的运行时**换代**）。`config.py` 增 `glm_api_key`/`moonshot_api_key` + 订阅 key/base + `model_bulk`；`schema.sql` 增 `llm_usage` 列 + `route_overrides` 表。调用点 `kg/extract.py`/`kg/expert.py` 已由 `tier="fast"` 迁移到 `task="kg_extract"`/`"expert"`；每日 bulk 拉取链（`orchestration/daily.py`）自动经 `task=` 路由。

> **独立双审 + /code-review（xhigh）裁定 GO**：`/code-review` 另**修复了 P0 订阅计费漏洞**、P1 重试门控 + ops null 桶、P2 retryable 集合 + 2 处测试卫生问题。

---

## 7. 许可纪律（CI 硬规则）

开源发布要求**核心代码链接图洁净**。CI 阻断把以下拉入被链接的核心：

- **排除/隔离**：OpenBB（`AGPL` 网络 copyleft——仅作网络边界微服务）、Dify（许可禁多租户 SaaS）、Firecrawl（`AGPL`）、MinerU pre-3.1.0（旧 `AGPL`——锁 ≥3.1.0）、Windmill（`AGPLv3`）、Marker（`GPL+RAIL`<$2M 限）、jina-embeddings-v3（`CC-BY-NC`）、REBEL/efinance/FinDKG 模型/FinReflectKG 数据集（非商用——仅设计参考）
- Neo4j Community（`GPLv3`）**作外部进程跑，绝不嵌入**
- LiteLLM：留在 `MIT` core（避 `enterprise/`）
- Phoenix（`ELv2`）：仅内部自托管，不作 OSS 转售/营销
- **逐仓核对实际 LICENSE 文件**（README 常误述——FinRobot README 写 MIT，仓库实为 Apache-2.0）

---

## 8. 路线图（约 6 周到首个可用版本）

| 里程碑 | 周期 | 范围 |
|---|---|---|
| **P0 — 基座 + 洁净骨架** | W0-1 | Docker Compose：Postgres(pgvector+AGE)、外部 Neo4j、MinIO、Redis、RAGFlow；LiteLLM 网关 + Langfuse 接默认模型含预算/计费。**CI 强制许可姿态**（阻断 AGPL/GPL/NC/source-available 进链接核心；OpenBB 仅网络边界后）。edgartools+AKShare 对全 15 家 smoke-pull。定义 Pydantic 本体 + 10 类催化剂分类法**为代码**。规范实体/别名表。TEI 嵌入/重排（BGE-M3 主）。每个连接器过数据权限矩阵闸 |
| **P1 — 采集 + RAG + 比率** | W1-3 | Dagster 资产化 filing/财报/新闻/产品页/ATS 招聘（全 15 家），含重试/schema 校验/新鲜度监控。RAGFlow 每公司知识库，Docling 主 / PaddleOCR-VL+MinerU 处理扫描中文；引用溯源跑通。FinanceToolkit 同业对齐比率（非日历财年+20-F 期对齐）。首轮催化剂抽取（8-K item 1.01 + cninfo 公告）。**强制 TEDS/数值对账闸上线**。原件归档 MinIO 带溯源 |
| **P2 — 时序 KG（护城河）+ 深度报告流水线** | W3-5 | Graphiti 用确定性实体规范化（Innolight/中际旭创/Zhongji、COHR/Coherent/II-VI）+ reflection/LLM-as-judge 抽取评测环 + 高风险边人审，把双时态产业链 KG 灌入 Neo4j；供应链遍历 + 有日期催化剂/订单事件作可推翻五元组可查。LangGraph 多 Agent 流水线产出单公司**深度报告 + 跟踪摘要**，含多空辩论、风险、主编合成、引用溯源、快照版本化、批判证据闸、人审 `interrupt()` 节点。强制非建议免责声明 |
| **P3 — 评测闸 + UI + 调度 = 首个可用版本** | W5-6 | Phoenix 在留出的**自有 filing 集**上跑离线 RAG/评测 + 证据覆盖度/命中率/幻觉风险作发布闸；Langfuse 成本仪表。React 对话研究 UI（投研门户 + 引用链接报告查看器 + KG 子图 + 催化剂时间轴）。Dagster sensor 在新 filing/催化剂上自动刷新跟踪摘要。**交付 AI 光模块垂直切片端到端（约 15 家）** |
| **P4 — MVP 后扩展（部分已交付）** | W6+ | **已交付**：从 1 主题扩展到 **8 大主题（5 产业链 + 3 消费周期）/ 947 公司**（`universe_build.py`）；新增**运营控制台**与**前沿探索**两大模块；**语义数据库**（`semantic_facts` + 前瞻声明解析）+ **每日自动增量链**（`run_daily` + Dagster 旁车 `:3001`）+ Finnhub/FMP 新闻源；AIFINmarket(万得) + arXiv/Journals provider；主题感知抽取；**公司 360 / 投资论点层 + 16 维覆盖度 + 五路数据纵深**（§5.9）。**待续**：LightRAG/MS GraphRAG 做全局主题查询；需严格多跳逻辑/数值推理再评 KAG/OpenSPG；需全文研报或规模化纪要再授权 Wind/Choice/iFinD + 纪要厂商；RAGFlow 数据集范围之上加应用级多租户授权；向量超 ~1000 万再迁 Qdrant |

---

## 9. 建议仓库结构

> 蓝图原始建议的多服务结构见下方"原始建议"；**实际交付**为单包 `xar` + `web/`（React SPA）：

**As-Built（实际）**
```
.
├── docker-compose.yml          # db(pgvector) + app + dagster 旁车(:3001，每日链调度/重试/run 历史)（+ 可选 --profile wechat: we-mp-rss）
├── Dockerfile                  # python:3.12-slim；pip install ".[market]"；预下载 bge-small
├── pyproject.toml              # 包 xar + fcn + slx；extras: cn/market/parse-deep/graph/orchestration/eval/crawl/dev
├── .env.example                # 一个 LLM Key 必填（默认 DeepSeek V4）；其余 provider Key 全可选
├── src/xar/
│   ├── config.py               # pydantic-settings（XAR_ 前缀 + provider 别名）；默认 model_fast=deepseek-v4-flash / model_strong=deepseek-v4-pro / model_effort=high
│   ├── cli.py                  # Typer: init/ingest/ingest-wechat/parse/build-kg/report/pull/pull-rss/providers-status/backtest/eval/status/explore/serve + daily/resolve-claims + thesis(build/show/status) + andy(init/ingest/identify/evaluate/sync-events/status) + wechat-mine/wechat-account(add/list/rm)/wechat-targets + reembed(中英嵌入升级)
│   ├── ontology/               # nodes/edges/catalysts + cycle(经济周期维度) + sectors + metric_packs + schema(抽取) + standards(FIBO/schema.org + FinMetric) + macro_links(【新, 2026-07】勾稽：43/43 指标↔主题/环节/技术路线 + 9 OVERCLAIM_LINKS) + thesis(【新, 2026-07】类型化 CompanyThesis + validate_thesis 纪律) + coverage360(【新, 2026-07】16 维覆盖度评分器) + cn_routing(【新, 2026-07】中文关键词→8 主题/33 tr_* 路由表, code-as-truth, 微信 triage 零 LLM 预筛)
│   ├── storage/                # db(pgvector 池) + schema.sql(含 semantic_facts 视图 / kg_events 语义扩列 / ingest_runs / frontier_fronts / frontier_domain_state / company_thesis / thesis_evidence / holdings / documents.triage_score+triaged_at+triage / wechat_accounts) + structured(结构化 upsert) + runlog(run 日志+游标) + objects
│   ├── ingestion/              # base + registry(8 主题/947 公司/59 细分) + universe(扩展名单，scripts/universe_build.py 生成) + edgar/cninfo/news/jobs/wechat + macro_bridge(【新, 2026-07】宏观印字/判定跃迁→kg_events(macro_print)，dedup_key 幂等) + xbrl(【新, 2026-07】EDGAR company-facts 8 季度→fundamentals) + holdings13f(【新, 2026-07】29 管理人 13F→holdings) + feeds(【新, 2026-07】16 条精选 RSS 源注册表)
│   ├── providers/              # base + finnhub/fmp/polygon/yahoo/wind/aifinmarket + polymarket/twitter/reddit + arxiv/journals + rss(【新, 2026-07】精选行业 RSS) + sentiment（12 个 provider）
│   ├── parsing/                # parse(分块/嵌入/索引) + tie_out(数值对账闸)
│   ├── kg/                     # store(双时态) + resolve(实体消解) + extract(主题感知 LLM 抽取, _focus_for, 填 narrative/time_orientation/drivers) + resolve_claims(前瞻声明 hit/miss/stale) + expert(专家加工) + signals(结构化→事件) + repair(【新, 2026-07】孤儿事件重指+锚回填)
│   ├── research/               # 【新, 2026-07】thesis(dossier→build→版本化 company_thesis/thesis_evidence + 零 LLM 健康度)
│   ├── mining/                 # 【新, 2026-07】微信多层级挖掘：targeting(T0 论点驱动目标, 零 LLM) + roster(T1 策展名册/wechat_accounts, 名册空退回 /rss) + triage(T2 抽取前 SNR 闸→documents.triage_score + WECHAT_TRIAGE + wechat_pending_clause 两条 NULL 安全守卫)
│   ├── chathy/                   # 【新, 2026-07】tools(工具注册表) + sessions(chat_sessions/messages) + agent(≤8 轮工具循环)——Chathy 流式工具调用分析师
│   ├── fenny/                  # 【新, 2026-07】blotter_pg(PgBlotterStore→fenny_blotter)——Fenny Postgres blotter
│   ├── exploration/            # 【新】domains(6 前沿领域) + ingest(arxiv/journals/voices→documents) + synthesis(研究前沿合成→frontier_fronts/_state)
│   ├── retrieval/              # vector(RRF 混合) + graphrag(双时态遍历)
│   ├── agents/                 # state/nodes/debate/evidence_gate/report/graph（可控 DAG）
│   ├── backtest/ eval/ orchestration/   # 催化剂回测(driven by semantic_facts) / 评测金标 / daily(run_daily 每日链) + definitions(Dagster pull_shard/extract_all/core_daily)
│   ├── api/                    # app(FastAPI 路由) + dashboard(投研 UI) + ops(运营控制台) + exploration(前沿探索) + chathy(SSE 对话) + dataroom(数据室) + fenny_mount(挂 /api/fenny 子应用) + andy_mount(【新, 2026-07】挂 /api/andy slx 子应用) + andy_links(【新, 2026-07】原生勾稽路由 /api/andy/link/*，注册于 mount 之上) + static/index.html（回退原生 UI）
│   ├── (src/fcn/)              # 【vendored, 2026-07】fenny fcn 包（定价/greeks/期权分析），子应用挂 /api/fenny，见 FENNY_UPSTREAM.md
│   └── (src/slx/)              # 【vendored, 2026-07】Andy siliconomics 硅基经济指标库（registry 10 锚/43 指标/9 过度宣称 + engine 识别/PIT + ingestion 18 连接器 + api），独立 slx schema，子应用挂 /api/andy，见 ANDY_UPSTREAM.md
├── web/                        # 【新】React + TS + Tailwind SPA（FastAPI 托管编译产物）
│   ├── tailwind.config.js      # 设计令牌 brand(navy)/accent(blue)/warn(amber)/explore(indigo)/pos/neg
│   └── src/
│       ├── App.tsx             # 路由：/(Chathy 默认) · /andy/*(懒加载·全局 ?as_of=) · /genny(+/segment/:id·/company/:id·/dataroom；旧顶层路径 302) · /fenny/*(懒加载) · /explore(+/:sectionId) · /ops + 9 子页(含 coverage)
│       ├── styles/theme.css    # 【新, 2026-07】深色终端主题 CSS 变量令牌 --c-*（tailwind 以 rgb(var(--c-*)) 消费）
│       ├── context.tsx         # 投研门户全局 DataProvider
│       ├── components/         # Layout/AppShell + Sidebar + TopBar + DecisionRail + AdminLayout + ExplorationLayout + ExplorationSidebar + ModuleNav(Chathy|Andy|Genny|Fenny) + chathy/*(ChatMessage/Composer/SessionList/ToolChip) + MacroStrip(Genny 宏观带，反向勾稽 pill) + charts/PlotlyChart(Andy/Fenny 共享懒加载 plotly 分片) + ThesisSection/CoverageRing/CompanyDataPanels(【新, 2026-07】Company 360)
│       ├── pages/              # chathy/ChathyPage + andy/*(5 页：Overview/Metrics/MetricDetail/Overclaims/Walls，懒加载) + genny/DataRoomPage + fenny/*(4 工作区，懒加载 plotly) + DashboardPage/SegmentPage/CompanyPage + ops/*(9 页，含 CoveragePage 覆盖度热力) + exploration/*(Overview/Section/_shared)
│       └── lib/, types-*.ts    # lib/exploration.ts + types-exploration.ts 等
├── tests/                      # test_units + test_pipeline(DB-gated, LLM-mocked) + andy/(vendored 28) + test_macro_links + test_macro_bridge
├── scripts/check_licenses.py   # 许可洁净
└── .github/workflows/ci.yml    # 许可闸 + ruff + pytest
```

**原始建议（蓝图，多服务）**
```
ontology/ · ingestion/connectors/ · parsing/(RAGFlow) · kg/(Graphiti) · retrieval/(GraphRAG)
agents/(LangGraph) · models/(LiteLLM) · eval/(Phoenix) · web/(Next.js) · .github/workflows/
docker-compose: Postgres(pgvector+AGE) · Neo4j · MinIO · Redis · RAGFlow · TEI · LiteLLM · Langfuse
```

---

## 10. 关键风险与缓解

> 注：信任与质量是财务场景的不可妥协项。以下整合自架构合成；合规反映"自用"姿态。

**数据/法律（真实暴露）**
1. **卖方研报 PDF 是发行券商版权作品**——即便自用，入库/再现全文仍是版权风险。缓解：默认**仅元数据**（标题/机构/评级/目标价/EPS）经 AKShare；确需全文从 Wind/Choice/iFinD 授权。
2. **CN 抓取（AUCL/DSL/PIPL）**：AKShare 东财/新浪/THS 端点与 AData 代理轮换是灰区，批量抓取/替代商业平台是不正当竞争暴露。缓解：优先 cninfo 官方披露、限速、低量、**生产不开代理轮换**、不转售/替代、无 PII。
3. **EDGAR 公平访问**：声明 User-Agent + ≤10 req/s，否则封 IP。
4. 自用边界：免费/非商用层（Finnhub/FMP/yfinance）契合自用 R&D；**若日后商业化需重审并购买付费层**。
5. **前沿源（arXiv/Journals/X）**：arXiv 元数据公开、Journals 走公开 RSS——仅入标题+摘要做引用、不转载全文；X 专家声音仅 curated handle、自用、`x-extracted-facts-self-use` 标签。

**技术**
- **解析保真**：VLM 在密集财务表会幻觉数字，错抽取静默产出"言之凿凿却错"的数——TEDS/数值对账闸不可妥协；在**自有 filing 组合**上跑内部评测，不信榜单分。
- **实体消解**跨文档/别名/ticker（中际旭创/Innolight/Zhongji、COHR/II-VI legacy）是图谱静默腐坏点——投入确定性规范化层 + 高风险边人审。
- **LLM 抽取 token 重**——`model_fast`(默认 v4-flash) 抽取路由、主题感知 prompt、复用 filing 上下文、辩论迭代上限、LiteLLM 单次预算。
- **主题感知抽取**：prompt 框架须随主题切换（曾硬编码为光模块——已修；`_focus_for()` 按公司 `themes` 选框架）。
- **技术路线在动**（CPO/LPO 压 DSP attach；SiPh 替 EML）——TechRoute 替代边建模为时间版本化，绝不静态。
- **时序正确性**——同时建模观测时间与有效时间，后发文档不覆盖先前为真事实。
- **单维护者/抓取脆弱库**（edgartools、AKShare）——vendor、锁版本、包重试+schema 校验+新鲜度监控。
- **前沿引用幻觉**：合成层只接受出现在所给清单内的 arXiv id（`key_papers` 经 `valid_ids` 过滤），杜绝编造论文。
- RAGFlow 多租户隔离是数据集范围**非安全边界**——对外多租户前加应用级授权（P4）。

**合规**：Agent 输出**不是**经核实的投资建议——强制非建议免责声明、发布前人审 `interrupt()` 节点、快照版本化证据溯源报告、对外呈现前合规审查。探索模块**强调长周期方向、非个股交易建议**。

---

## 11. 待办/开放问题

- [x] **催化剂信号回测**：已实现 `backtest/catalyst_returns.py`——现驱动于 `semantic_facts`（非仅 `kg_events`），按 `(category, polarity, kind, time_orientation)` 量化"催化剂/前瞻/情绪层→远期收益"，回答"前瞻/情绪层是否预测收益"；严格 PIT 入场 = `GREATEST(as_of, observed_at)`（无前视），优先本地 `prices` 表（yfinance 仅兜底）。
- [x] **事件级去重**：已实现 `kg_events.dedup_key`（company+type+date+magnitude+route 内容哈希），跨源同一事件自动去重。
- [x] **嵌入默认**：交钥匙默认 `bge-small`(384d) 求快；`BGE-M3`(1024d) 作高质量可选项，env 一行切换。
- [x] **多主题扩展**：已从单一光模块扩展到 **8 大主题（5 产业链 + 3 消费周期）/ 947 公司 / 59 细分**（`registry.py` `THEMES`/`SEGMENTS`，`scripts/universe_build.py` 生成 `universe.py`），抽取已主题感知。
- [x] **语义数据库 + 前瞻声明解析**：已实现加性复用三表的 `semantic_facts` 视图 + `kg_events` 语义扩列 + `causally_linked` 边 + `resolve_forward_claims`（hit/miss/stale），`graphrag.semantic()` 注入分析师 brief。
- [x] **每日自动增量链**：已实现 `orchestration/daily.py run_daily` + `ingest_runs` 游标 + Dagster 旁车（`pull_shard` 06:00 / `extract_all` 06:30，`:3001`），CLI `xar daily` / `xar resolve-claims`。
- [x] **Finnhub/FMP 新闻源**：`providers/finnhub.pull_news`/`fmp.pull_news` 落 `documents`，经 `ops` 与 `expert.ALT_SOURCES` 入本体。
- [x] **前沿探索模块**：已交付第三个顶层模块（6 领域、arXiv+Journals+X、`frontier_fronts`/`_state`、`/api/exploration/*`、`xar explore`），独立审计 PASS。
- [x] **公司 360 / 投资论点层**：已交付类型化 `CompanyThesis`（validate 纪律 + 版本化入库 + 零 LLM 健康度）+ 16 维 coverage360 + 五路数据纵深（Finnhub 日历/篮扫、Yahoo 纵深、EDGAR XBRL+13F、CN 补齐、RSS 框架）+ `xar thesis`/`xar pull-rss` + Company 360 前端与 `/ops/coverage`（§5.9）。
- [~] **报告质量评测基线**：已建 `eval/gold.json` + 检索命中率 + 报告 rubric(LLM-judge)；**仍待**扩充人工标注留出集做更强发布闸。
- [ ] **新数据面 UI 化**：结构化/社媒/信号/预测市场/`providers`/`ingest-wechat` 等端点已就绪并经 API 暴露；React SPA 已覆盖 Chathy / Andy / Genny / Fenny 四大前端模块与 运营控制台 / 前沿探索 两卫星，剩余少量端点的 UI 呈现仍在补齐（见 `UI.md`）。
- [ ] **公众号信号密度**：命中率取决于订阅哪些号——需筛选垂直号而非泛科技媒体；可经 `WERSS_FEED_MAP` 将垂直号直绑标的。
- [ ] **前沿源密度**：探索模块命中率取决于 arXiv 类目/Journals feed/专家账号的精选；`neuro`/`complex` 等弱 arXiv 领域更依赖顶刊与 X 声音。
- [x] **增量调度**：已落 `xar daily`（`run_daily` 逐源增量 + `ingest_runs` 游标）+ Dagster 旁车（`pull_shard` 06:00 / `extract_all` 06:30，`:3001`，含重试/run 历史）；`explore` 仍手动/后台触发。

---

*选型基于 2026-06 对开源生态的多源检索调研。所有仓库需在落地前逐一核对实际 LICENSE 文件。*
