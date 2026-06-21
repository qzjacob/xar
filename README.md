# XAR — 开源产业链投研 + 前沿探索平台 / Industry-Chain Research & Frontier-Exploration Platform

围绕多条并行产业链（首发：**AI 光互连**，现已扩展至 **5 大主题**）自动汇聚公司公告、财报、研报元数据、新闻、产品页与招聘信号 →
构建**双时态产业链知识图谱 + RAG** → 经**多 Agent 流水线**产出**深度报告 / 跟踪摘要 / 投资启示**，每条结论可溯源引用。
平台由**三个同级模块**组成：**投研终端**（Research Portal）、**运维控制台**（Operations Console）、以及新增的**前沿探索**（Exploration）。

> 交钥匙工程：填入一个 LLM Key 即可运行。设计蓝图见 [`DESIGN.md`](./DESIGN.md)，前端三模块说明见 [`UI.md`](./UI.md)。

## 30 秒启动（Docker）

```bash
cp .env.example .env
# 编辑 .env，填任一 LLM Key 即可（经 LiteLLM 路由）。默认走 DeepSeek V4：
#   DEEPSEEK_API_KEY=sk-...          默认（v4-flash 抽取 / v4-pro 推理，开箱即用）
#   ANTHROPIC_API_KEY=sk-ant-...     + 设 XAR_MODEL_FAST/STRONG=claude-haiku-4-5/claude-opus-4-8
#   OPENAI_API_KEY=sk-...
docker compose up --build
```

打开 <http://localhost:8000> → 点「采集全部公司」→ 选公司 + 报告类型 → 「生成报告」。
顶栏的模块切换可进入 **/explore**（前沿探索）与 **/ops**（运维控制台）。内置 Web UI 说明见 [`UI.md`](./UI.md)。

唯一**必填**项是**一个 LLM Key**（任选其一）。数据库、向量库、嵌入模型（CPU，无需 GPU）、对象存储都已内置自动初始化。所有行情/另类数据 provider 的 Key 全部可选，缺失即自动跳过。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"           # 核心；免费行情(Yahoo)加 ".[market]"，中国数据源加 ".[cn]"，深度解析加 ".[parse-deep]"
# 需要一个 Postgres(pgvector)：docker run -d -p 5432:5432 \
#   -e POSTGRES_USER=xar -e POSTGRES_PASSWORD=xar -e POSTGRES_DB=xar pgvector/pgvector:pg16
cp .env.example .env              # 填 DEEPSEEK_API_KEY（或任一 LLM Key）
xar init                          # 建表 + 录入公司 + 知识图谱种子
xar ingest                        # 采集→解析→建图谱（全 basket；先跑单家用 `xar ingest nvidia`）
xar report nvidia                 # 生成深度报告
xar explore ai                    # 前沿探索：采集 arXiv/期刊/X→综合 AI 研究前沿
xar serve                         # Web UI: http://localhost:8000
```

## 三大模块（同级，各自独立的 SPA 外壳）

由 FastAPI 托管的单页应用（React + TS + Tailwind），共享同一套 Postgres + 文档/嵌入/LLM 栈：

1. **投研终端 Research Portal**（`/`）— 投研主控台，主线为 **主题 → 环节 → 公司 → 信号 → 决策**。藏青底色 + 蓝色强调（`accent`），`Layout`/`AppShell` + `Sidebar` + `TopBar` + `DecisionRail`，全局 `DataProvider` context；侧栏含跨模块切换按钮。
2. **运维控制台 Operations Console**（`/ops/*`）— 管理控制平面，琥珀色强调（`warn`），`AdminLayout`。8 个子页：总览、本体、数据源、数据湖、另类数据、模型、连接器、技能（overview / ontology / sources / datalake / altdata / models / connectors / skills）。
3. **前沿探索 Exploration**（`/explore`、`/explore/:sectionId`）— **新增第三模块**，面向人类知识前沿。靛蓝色强调（`explore`），`ExplorationLayout` + `ExplorationSidebar`。详见下文。

## 5 大投资主题（294 家公司 · 38 个环节 · 25 条技术路线）

公司携带 `themes`（可同属多个主题）与按主题区分的 `seg`（环节 id）。新增赛道 = 在 `ingestion/registry.py` 加一个主题 + 公司，其余不变。各主题环节按 `tier` 自上游 → 下游排列：

| 主题 | 环节 | 主线（上游 → 下游） |
|---|---|---|
| **ai_optical**（AI 光互连产业链） | 4 | 上游器件 → 光模块厂 → 代工制造 → 下游客户 |
| **ai_chip**（AI 算力芯片产业链） | 9 | 晶圆厂设备(WFE) → 材料/EDA → 晶圆代工 → 存储HBM/GPU/CPU → 先进封装 → PCB |
| **ai_software**（AI 软件普及链） | 9 | 按**企业 AI 采纳浪潮**分层：研发与AI基础设施/可观测先放量（JFrog/Datadog），CRM/ERP（Salesforce 类）较晚；每个环节带中文 `thesisCn` |
| **space_exploration**（太空探索产业链） | 8 | 发射 → 推进 → 卫星制造 → **太空数据中心 / 在轨算力**（以 SpaceX 为代表的天基算力，非地面 DC）→ 地面站 → 组件 → 应用 → 防务 |
| **humanoid_robotics**（人形机器人产业链） | 8 | 执行器/谐波减速器/滚柱丝杠 → 无框电机 → 传感器 → 域控/AI 大脑 → 电池 → 灵巧手 → 材料 → 整机 OEM |

覆盖全球资本市场（US/CN/JP/KR/EU/TW/HK/SG/SE），仪表盘内做 FX 归一。市值校验 > $2B（美股用 Finnhub，其余用 Yahoo + 汇率）。
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
| 采集（非结构化） | edgartools（美股 SEC，绿）· AKShare（A 股 cninfo，绿；研报仅元数据，红）· trafilatura（新闻/产品页）· **微信公众号（we-mp-rss，灰）** · ATS 官方 API（招聘，绿） | Crawl4AI、Tushare Pro |
| 采集（结构化/另类） | **多 provider 套件**：Finnhub（基本面/估计/评级/内部交易）· FMP（三大报表/分析师估计/目标价/日线）· Polygon（深度日线 + vX 财报）· Yahoo/yfinance（免费全球价格+基本面，含 A 股，无 key）· **AIFINmarket / 万得**（CN A 股基本面+公告+资讯，MCP-over-HTTP）· Polymarket（预测市场，公开）· X/Reddit（社媒情绪）· Wind（CN-A 深度，需本地终端） | 任意 provider；均按需配置，缺 key 自动跳过 |
| 采集（前沿） | **arXiv**（预印本，公开）· **Journals**（Quanta / Physics World RSS）· X 专家之声 → 前沿探索模块 | 任意公开学术源 |
| 解析 | pdfplumber + 分块 + **数值对账闸(tie-out)** | Docling（`.[parse-deep]`）、MinerU |
| 嵌入 | fastembed（CPU，默认 bge-small；可设 BGE-M3/Qwen3） | TEI/vLLM 服务 |
| 知识图谱 | 双时态 节点/边/事件 + **确定性实体消解** + **事件级去重** + **主题感知抽取** | Graphiti（`.[graph]`） |
| 检索 | pgvector 稠密 + trigram 词法，RRF 融合 + GraphRAG | RAGFlow、LightRAG |
| 多 Agent | 可控 DAG：规划→图谱→分析师→多空辩论→风险→主编→**证据闸**→**人工审批** | LangGraph（同构） |
| 模型 | **LiteLLM** 路由：DeepSeek V4 默认（v4-flash 抽取 / v4-pro 推理）+ 成本追踪 + 单次预算上限 | 任意 LiteLLM provider / 本地开源 |
| 编排 | Dagster 资产化增量刷新（`.[orchestration]`） | — |
| 评测 | 检索命中率 + 报告 rubric（LLM-judge）+ Phoenix（`.[eval]`） | — |
| 回测 | 催化剂→远期收益 信号有效性 | — |

## 模型路由（LiteLLM）

两级路由，默认 provider = **DeepSeek V4**（`config.py`，全部经 `XAR_*` 覆盖）：

- `XAR_MODEL_FAST=deepseek/deepseek-v4-flash` — 抽取 / 分类 / 快速综合。
- `XAR_MODEL_STRONG=deepseek/deepseek-v4-pro` — 推理 / 多空辩论 / 研究前沿综合。
- `XAR_MODEL_EFFORT=high` — 推理强度（V4 推理模型需调 `reasoning_effort`）。

`complete_json` 走 JSON-mode。可覆盖为任意 LiteLLM 模型（如带 `ANTHROPIC_API_KEY` 时设 `claude-opus-4-8` / `claude-haiku-4-5`）。每次运行有美元预算上限（`XAR_LLM_MAX_USD_PER_RUN`，默认 5）。

## CLI

```
xar init            建表 + 种子          xar report <id> --kind deep_report|tracking_summary|takeaways
xar ingest [id]     采集→解析→建图谱      xar pull [id]       结构化+另类数据→信号
xar ingest-wechat   微信公众号→本体        xar explore [domain] 前沿探索：arXiv/期刊/X→研究前沿
xar parse           仅解析+嵌入           xar backtest        催化剂→收益回测
xar build-kg        仅抽取图谱            xar eval            检索命中率
xar providers-status provider 配置状态     xar status          各表行数
xar serve           Web UI + API
```

**前端路由**：`/` · `/segment/:id` · `/company/:id` · `/explore`（+ `/:sectionId`）· `/ops` + 8 个子页。
设计 token：`brand`（藏青）· `accent`（蓝，投研）· `warn`（琥珀，运维）· `explore`（靛蓝，探索）· `pos`/`neg`。

主要 API：`/api/providers` · `/api/pull` · `/api/fundamentals/{id}` · `/api/estimates/{id}` ·
`/api/prices/{id}` · `/api/prediction-markets` · `/api/social/{id}` · `/api/signals/{id}` ·
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

**专家 Agent 层**（`kg/expert.py`）：用 LLM 对另类数据（X / 微信公众号 / 新闻 / AIFINmarket）做相关性 / 立场 /
质量过滤 → `expert_insights` 表 + `kg_events(license=expert)`，作为信噪比放大器展示于 `/ops/altdata`。

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
- **成本控制**：两级模型路由 + 单次运行美元预算上限（`XAR_*` 可调）。

## 测试

```bash
pytest -q          # 单元测试始终跑；端到端测试在检测到 Postgres 时运行（LLM/嵌入已 mock，无需 API Key）
```

## License

Apache-2.0（代码）。各上游依赖以各自许可分发；可选 extras（cn/graph/crawl 等）按其许可自行评估，部分需隔离运行。