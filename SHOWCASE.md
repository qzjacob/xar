# XAR — 下一代 AI 驱动产业链投研 + 前沿探索平台 · 技术讲解与营销展示

> **eXplanatory Augmented Research**：围绕一条产业链，把全市场散落的公告、财报、研报、新闻、产品页、招聘、社媒、预测市场自动汇聚成一张**带时间维度、可溯源引用**的知识图谱，再由**可控多 Agent 流水线**产出深度报告、跟踪摘要与投资启示 —— **每一条结论都能点回到源文件或图谱事实**。
>
> 同一套引擎，向外延伸出**前沿探索（Exploration）**：把 arXiv 预印本、顶级期刊/专业平台、X 专家之声汇聚为**前瞻性的研究前沿地图**，AI 优先，独立审计通过。
>
> 一个 API Key，一条 `docker compose up`，端到端可用。

---

## 一、一句话定位

**XAR 不是"又一个聊天式研报生成器"，而是一套"机构级、可审计、强溯源"的产业链投研操作系统 —— 并在同一引擎之上长出"人类知识前沿"的探索之眼。**

它把一名买方分析师团队的整套工作流 —— 信息采集 → 事实抽取 → 关系建模 → 多空辩论 → 风险压测 → 主编合成 → 合规人审 —— **工程化为一条可控、可复现、带证据闸的数据流水线**，把"人海战术 + Word + Excel"升级为"知识图谱 + 多 Agent + RAG"。

XAR 由**三个对等顶层模块**构成，各自拥有独立的 SPA 外壳与主题色：

| 模块 | 路由 | 主题色 | 定位 |
|---|---|---|---|
| **投研门户 Research Portal** | `/` | 海军蓝 chrome + 蓝色 accent | 投资终端：主题 → 环节 → 公司 → 信号 → 决策 |
| **运营控制台 Operations Console** | `/ops/*` | 琥珀色 accent | 管理控制面：自省与操作真实平台状态（8 页） |
| **前沿探索 Exploration** | `/explore`、`/explore/:sectionId` | 靛蓝 "explore" accent | 人类知识前沿：arXiv + 期刊 + X → 前瞻研究前沿 |

| 维度 | 传统人工投研 | **XAR AI 投研体系** |
|---|---|---|
| 信息覆盖 | 5–10 个常用终端 + 人工浏览 | **17 个数据源**自动采集（中美欧、结构化+另类+非结构化+前沿），含每夜增量自动采集 |
| 关系建模 | 散落在分析师大脑 / PPT | **双时态产业链知识图谱**（节点/边/事件全部带有效期）+ **时间戳化语义层** |
| 语义/前瞻 | 数字表之外的"立场/叙事/因果/前瞻预期"无处沉淀 | **语义数据库** `semantic_facts`：催化剂+专家观点统一为可点查的语义事实流，含**预期→兑现**闭环 |
| 时间一致性 | "某日为真"难查证 | **Bi-temporal**：后发文档不覆盖先前为真事实 |
| 报告产出 | 单人 1–2 周/深度报告 | 分钟级流水线，**一图三品**（深度/跟踪/启示） |
| 溯源能力 | "据我们研究" | 每条结论挂 **`[n]` 引用标记** → 源 chunk / filing / 图谱事实 |
| 数值可信度 | 手工抄录易错 | **数值对账闸**（tie-out）阻止"言之凿凿却错"的数字 |
| 多视角对抗 | 依赖单一分析师立场 | **多空辩论子图**强制对抗 + 风险压测 |
| 合规人审 | 终审靠自觉 | `awaiting_approval` 强制人审中断节点 |
| 知识前沿 | 信息时效 + 个人阅读上限 | **前沿探索模块**：AI 优先综合 arXiv/期刊/X，描绘前瞻方向 |
| 边际成本 | 加一份报告 = 加一个分析师 | 加一份报告 ≈ 几美元 LLM 调用（单次预算上限） |

---

## 二、为什么是 XAR：行业的三个痛点

### 痛点 ① 信息碎片化 —— 关系与时间是"隐形资产"
单一行业（如 AI 光模块）的投研信息高度碎片化：美股 10-K/8-K、A 股公告/财报、中外研报、产业新闻、厂商产品页、招聘动向分散在数十个来源，且**谁供谁、走哪条技术路线、何时拿到订单**这类关系，**没有任何传统工具能结构化呈现**，更别说"某事实在某日是否成立"。

### 痛点 ② LLM 直出研报 = "幻觉地雷"
直接把文档丢给大模型生成研报，在金融场景下是灾难：**密集财务表的错抽取会静默产出"言之凿凿却错"的数字**，且无任何溯源。买方无法据此下单。

### 痛点 ③ 多 Agent 框架"放养"= 不可控成本与不可审计结论
把一群 Agent 放进 LangGraph 自由 swarm，会得到**不可复现、成本失控、无法人审**的输出，与机构合规要求背道而驰。

**XAR 的回答**：护城河不是"会用 LLM"，而是 **(1) 带双时态、可引用的产业链知识图谱** + **(2) 可控、可审计、强溯源的多 Agent 报告流水线**。其余皆为可复用的"管道商品"。而**同一套图谱/嵌入/LLM/DB 栈**被复用到第三模块——**前沿探索**，证明这套底座的可迁移性。

---

## 三、完整系统架构

### 3.1 总体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│              多 Agent 报告流水线 (可控 DAG + 人审中断)   ← 护城河 #2        │
│  规划 → 图谱检索 → 5 分析师 → 多空辩论 → 风险 → 主编 → 证据闸 → 人工审批     │
└───────────▲────────────────────────────▲────────────────────────────────┘
            │ 工具调用                     │ 工具调用
┌───────────┴──────────────┐  ┌───────────┴───────────────────────────────┐
│  混合 RAG 检索 (RRF 融合)  │  │  双时态产业链知识图谱 (GraphRAG) ← 护城河 #1 │
│  pgvector 稠密 + trigram  │  │  节点/边/事件 · 实体消解 · 事件级去重        │
│  词法 · 数值对账闸过滤      │  │  + 结构化信号统一蒸馏为催化剂流              │
└───────────▲──────────────┘  └───────────▲───────────────────────────────┘
            │                              │
┌───────────┴──────────────────────────────┴───────────────────────────────┐
│  采集与解析层 (key-gated，缺 Key 自动跳过)                                  │
│  非结构化: SEC EDGAR · cninfo · 新闻 · 产品页 · ATS招聘 · 微信公众号         │
│  结构化/另类: Finnhub · FMP · Polygon · Yahoo · Polymarket · X · Reddit · Wind│
│  前沿: arXiv 预印本 · 顶级期刊/专业平台(Quanta/Physics World) · X 专家之声    │
│           → 统一归一到 FinMetric 规范词表 → 蒸馏进 kg_events 催化剂流         │
└───────────▲───────────────────────────────────────────────────────────────┘
            │
┌───────────┴───────────────────────────────────────────────────────────────┐
│  存储: 单 Postgres + pgvector (向量 + 关系 + 双时态图谱 + 前沿前沿表，一库打通)│
│  模型网关: LiteLLM LLM 任务管理器(按任务路由+多供应商+计费感知) + 预算上限 → llm_usage│
│  嵌入: fastembed (ONNX/CPU，零 GPU) · 可换 BGE-M3                          │
│  信任层: 数值对账闸 · 证据覆盖度 · LLM-as-judge 幻觉检测 · 人审中断            │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 分层架构与"热插拔"设计

XAR 的每一层都遵循 **"交钥匙默认 + 可热插拔升级"** 原则 —— 默认栈零运维成本即可跑通，企业级场景可逐层替换为重型组件，**接口不变**：

| 层 | 交钥匙默认实现 | 可热插拔升级为 |
|---|---|---|
| **存储** | 单 **Postgres + pgvector**（向量 + 关系 + 双时态图谱一库） | Neo4j/Graphiti（图）、Qdrant（向量）、MinIO（对象存储） |
| **非结构化采集** | edgartools（SEC，绿）· AKShare（cninfo，绿）· trafilatura（新闻）· **微信公众号** · ATS 官方 API（招聘） | Crawl4AI、Tushare Pro |
| **结构化/另类数据** | Finnhub · FMP · Polygon · Yahoo · Polymarket · X · Reddit · Wind · AIFINmarket | 任意 provider，按需配置 |
| **前沿数据** | arXiv（公开 Atom API，无 Key）· 期刊 RSS（Quanta/Physics World）· X 精选研究者 | 任意预印本/期刊源 |
| **解析** | pdfplumber + 分块 + **数值对账闸** | Docling（`.[parse-deep]`）、MinerU |
| **嵌入** | fastembed（CPU，bge-small 384d） | BGE-M3 1024d、TEI/vLLM 服务 |
| **知识图谱** | 自建双时态 节点/边/事件 + **确定性实体消解** + **事件级去重** | Graphiti（`.[graph]`） |
| **检索** | pgvector 稠密 + trigram 词法，**RRF(k=60) 融合** + GraphRAG | RAGFlow、LightRAG |
| **多 Agent** | 自建可控 DAG（8 阶段，检查点续跑） | LangGraph（同构） |
| **模型** | **LiteLLM LLM 任务管理器**（按任务路由 · 多供应商 DeepSeek/GLM/Kimi/Anthropic · token-vs-订阅计费感知）+ 成本追踪 + 预算上限 | 编辑 `models/registry.py` 一处即换代 / 任意 LiteLLM provider |
| **编排** | CLI / 后台任务 | Dagster 资产化（`.[orchestration]`） |
| **前端** | React + TypeScript + Tailwind（编译产物由 FastAPI 服务） | — |

> **设计哲学**：蓝图目标是"复用业界最佳开源件"，落地时为**交钥匙（填一个 API Key 即跑）**做了精简 —— 把多进程重栈（Neo4j + RAGFlow + Graphiti + LangGraph + Dagster）收敛为**单 Postgres + 自建薄层**，能力等价、运维成本骤降，而蓝图的全部"护城河"（双时态可引用 KG、可控可审计多 Agent、数值对账闸、实体消解、许可纪律）**完整保留**。**前沿探索模块复用了同一栈**（documents / embeddings / LLM / Postgres），零新增基础设施。

### 3.3 数据流（端到端）

```
数据源                     归一/抽取                      入库                     检索/推理             产出
─────── ───────────────────────────────── ────────────────────── ────────────────── ──────────
EDGAR 8-K ─┐
cninfo 公告 ┤→ Doc(permission) ──→ 分块 ──→ tie-out ──→ 嵌入 ──→ chunks(tie_out_ok) ─┐
新闻/产品页 ┤                    └→ LLM 抽取(schema 约束) ──→ kg_nodes/edges/events   │
微信公众号 ─┘                       └→ 专家智能体过滤 ──→ kg_events(license=expert)   │
                                                                          ├→ RRF 混合检索 ──→ 5 分析师
Finnhub/FMP ─┐                                                                                  │
Polygon/Yahoo┤→ canonical_metric(FinMetric) ──→ fundamentals/estimates/prices ... ──┐         ├→ 多空辩论
Polymarket  ─┤                                                                       │         ├→ 风险压测
X/Reddit    ─┘→ signals.derive ──────────────────────────→ kg_events(统一催化剂流) ─┘         ├→ 主编合成
                                                                                              ├→ 证据闸
                                                                                              └→ 人审 → 发布

arXiv 预印本 ─┐                                                                  前沿探索 (Exploration)
顶级期刊 RSS ─┤→ documents(meta.frontier, meta.domain) ──→ 强推理 LLM 综合 ──→ frontier_fronts ──→ /explore
X 专家之声  ─┘   (绿/灰 license)                            (按域综合研究前沿)   + frontier_domain_state
```

---

## 四、八大主题（5 条产业链 + 3 条消费周期，已内置）

XAR 不是泛泛的"通用投研机器人"，而是**按主题垂直切片**交付。内置 **8 条平行主题、共 947 家公司**，由 `THEMES` 的 `kind` 判别轴分两类：**5 条"产业链"主题（`kind="chain"`，上游→下游 tier 轴）** + **3 条"消费周期"主题（`kind="cycle"`，经济周期轴）**。公司带 `themes[]`（可同属多链）与 per-theme 段位（`meta.segments`），共享巨头如 NVIDIA/Broadcom/Marvell 同属光互连与算力芯片两链。覆盖 US + JP/KR/TW（含部分 CN）。

#### 5 条产业链主题（`kind="chain"`，上游 → 下游 tier 轴）

| 主题 | 产业链结构（上游 → 下游） |
|---|---|
| **AI 光互连** (`ai_optical`) | 上游器件 → 光模块厂 → 代工制造 → 下游客户（NVIDIA/超大规模厂） |
| **AI 算力芯片** (`ai_chip`) | WFE 设备 → 材料/EDA → 晶圆代工 → 存储/HBM → GPU/CPU → 先进封装 → PCB |
| **AI 软件普及链** (`ai_software`) | 研发与AI基建 → 可观测/AIOps → 数据平台 → 安全 → 协作生产力 → CRM → 营销 → ERP/HR → 垂直 SaaS |
| **太空探索** (`space_exploration`) | 发射 → 推进 → 卫星制造 → **太空数据中心/在轨算力** → 地面站 → 组件材料 → 应用 → 防务 |
| **人形机器人** (`humanoid_robotics`) | 执行器/减速器/丝杠 → 力矩电机 → 传感器 → 域控/AI 大脑 → 电池 → 灵巧手 → 材料 → 本体整机 |

每条产业链都**手工策展了结构化"骨干边"（`SEED_EDGES`）与技术路线节点（`TECH_ROUTES`，共 **33 条**）**，是图谱的可靠起点，而非从零冷启动。其中含 8 条由本体富化反复浮现的真实需求**派生扩展路线**（`tr_cybersec` / `tr_ddic`(显示驱动 IC) / `tr_power_semi` / `tr_cv`(计算机视觉) / `tr_med_imaging` / `tr_pneumatic` / `tr_industrial_gas` / `tr_ceramic_pkg`），覆盖原光互连/芯片中心集之外的专业化方向。

#### 3 条消费周期主题（`kind="cycle"`，经济周期轴）

消费链没有上游→下游的供应链 tier；区分其子环节的是**它们在宏观/消费周期中的位置**。XAR 为此新增一条本体维度 `src/xar/ontology/cycle.py`：5 态 `CyclePosition`（`early_cycle` / `mid_cycle` / `late_cycle` / `defensive` / `counter_cyclical`），`CYCLE_RANK`（"越晚下跌、rank 越高"）**复用为段位 tier**，于是仪表盘/热力图沿周期轴排序、**前端 `ChainHeatmap` 自动改标题为 "Cycle Map"**（无需改组件逻辑）。

| 主题 | 周期轴（早周期 → 晚周期 / 防御 / 逆周期） |
|---|---|
| **互联网平台** (`internet`) | 按各子环节的 `CyclePosition` 排序（高 beta 早周期 → 防御/逆周期） |
| **美国零售** (`retail`) | 服饰早周期（高 beta，先回落）→ 折扣/平价零售逆周期（受益于消费降级、最后下跌） |
| **餐饮服务** (`restaurants`) | 休闲正餐早周期 → 快餐/QSR 逆周期（降级承接） |

#### 各主题的"灵魂"（环节分级即投资论点）
- **AI 软件普及链**：环节 `tier` = **企业 AI 采用浪潮**的先后 —— 研发工具链 / 可观测最先放量（如 JFrog 制品/模型仓库、Datadog/Dynatrace 智能体可观测），CRM/Salesforce 类前台改造晚于工具链。每个环节内置中文 `thesisCn` 论点。
- **太空探索**：主题核心是 **太空数据中心 / 在轨算力**（以 SpaceX 为代表的天基 AI 算力，太阳能 + 真空散热承载 AI 推理；**不含地面数据中心**），而非传统地面 DC。
- **人形机器人**：从最贵的 BOM 环节（执行器/谐波减速器/行星滚柱丝杠，约占 40–55%）一路到本体整机厂（Tesla Optimus、宇树等下游需求枢纽）。
- **消费周期主题**：`internet/retail/restaurants` 不走供应链 tier，而由 `cycle.py` 的 `CycleProfile`（per-segment：`position`/`cyclicality`/`sensitivity` beta 提示）定义周期定位 —— 折扣零售逆周期、生鲜防御、服饰早周期，公司从段位继承 profile（可显式 override）。
- **全球票池（已扩容至 947 家）**：由 `scripts/universe_build.py` 构建 —— 以权威 Finnhub 各交易所符号集为"存在性闸"，按 主题×区域 做 LLM 枚举，再经确定性核验（存在性 + 去重 + 美股 ≥$2B 市值闸 + 消费链非美周期 blocklist + 名↔代码 same_entity 校验）生成 `src/xar/ingestion/universe.py`（`UNIVERSE` 追加进 `registry.COMPANIES`）。覆盖 US + JP/KR/TW（+ 部分 CN）。仪表盘内置 **FX 归一**。
- **本体富化已覆盖全部 947 家至完整深度**：基础本体（sector/industry/segment/chain_role）原本就 100% 完整；`scripts/ontology_enrich.py`（白名单校验的批量 LLM 富化，经任务管理器以 `task="search_bulk"` 路由到 GLM 订阅、DeepSeek 回退，528 家约 $0.43）为 569 家批量生成的"宇宙"公司补足**深度维度**：多主题成员、技术路线暴露、更丰富别名、精炼主段位 —— 一切严格对本体词表校验，越界项丢弃；一张确定性 `_CORRECTIONS` 表编码 18 处审计确认修正，`generate()` 合并缓存+修正后重写 `universe.py`。富化后的 `tech_routes` 经 `kg/store.py` 的 `bootstrap_seed` 落为 `uses_techroute` 边（`license_tag='enriched'`），重播种时"删后重建"以让修正干净传播。**结果（全库 947）**：多主题公司 80，技术路线节点 33，`uses_techroute` 边 724（360 为 enriched），`competes_in` 1024，`entity_aliases` 3623；经独立双重审计 + 代码审查，词表 0 违规、5 项完整性不变量通过。
- **KG 抽取是主题感知的**：`kg/extract.py` 的 `_focus_for(company)` 按公司所属主题选择行业框架（修复了一个潜在 bug —— 此前 prompt 被硬编码为光模块语境）；抽取同时填写 `time_orientation`（前瞻/回溯）、因果 `narrative` 与 `drivers`（因果实体）。

---

## 五、前沿探索（Exploration）—— 第三模块：人类知识的前沿之眼

> 关键文件：`src/xar/exploration/`（`domains.py` · `ingest.py` · `synthesis.py`）+ `src/xar/api/exploration.py`。前端：`web/src/pages/exploration/*` · `components/ExplorationLayout.tsx` + `ExplorationSidebar.tsx` · `lib/exploration.ts` · `types-exploration.ts`。表结构见 `storage/schema.sql`（`frontier_fronts` + `frontier_domain_state`）。

**投研门户回答"哪个公司/环节值得关注"，前沿探索回答"人类知识的边界正往哪里移动"。** 这是 XAR 把同一套图谱/嵌入/LLM/DB 引擎延伸到**基础研究前沿**的第三个对等模块 —— 靛蓝 "explore" 主题色，独立的 `ExplorationLayout` + `ExplorationSidebar` 外壳，研究终端侧边栏内置**模块切换按钮**一键跳转。**本模块已由一个独立 Agent 端到端审计 → 通过（PASS）。**

### 5.1 六大前沿领域（显示顺序，AI 优先）

| 领域 | id | arXiv 类目（示例） | 专家之声（X 精选研究者） |
|---|---|---|---|
| **人工智能前沿** | `ai` | cs.AI · cs.LG · cs.CL · cs.CV · cs.MA · stat.ML | LeCun、Karpathy、Jeff Dean、Fei-Fei Li、Jason Wei、Jim Fan、Noam Brown、Oriol Vinyals、Demis Hassabis… |
| **物理学** | `physics` | quant-ph · cond-mat · hep-th · gr-qc | Sean Carroll、Preskill、Quanta Magazine… |
| **数学** | `math` | math.AG · math.NT · math.CO · math.PR · math.OC | Terence Tao、Quanta Magazine… |
| **计算与系统** | `cs_systems` | cs.DC · cs.AR · cs.OS · cs.DS · cs.CR | Matei Zaharia、Dan Luu、Andrew Ng… |
| **神经与认知** | `neuro` | q-bio.NC | Konrad Kording、Tony Zador… |
| **复杂系统与社会** | `complex` | physics.soc-ph · econ.GN · nlin.AO | 经济物理 + 科技地缘（compute governance / export controls） |

### 5.2 三类前沿数据源（全部"元数据 / 摘要 only"，不转载全文）

| 源 | 实现 | 姿态 | 作用 |
|---|---|---|---|
| **arXiv 预印本** | `providers/arxiv.py`（公开 Atom API，**无 Key**） | 🟢 绿（abstract+metadata） | 一手研究信号；按域 `arxiv_cats` 拉取，落 `documents(source='arxiv')` |
| **顶级期刊 / 专业平台** | `providers/journals.py`（Quanta Magazine + Physics World 公开 RSS） | 🟢 绿（metadata+summary） | 预印本之上的同行评审 / 编辑精选层，落 `documents(source='journal')` |
| **X 专家之声** | `providers/twitter.py`（**仅精选研究者 handle**，回复过滤） | 🟡 灰（自用事实摘录） | 真正的前沿研究者声音（非噪声关键词检索），落 `documents(source='x')` |

三类源都标记 `meta.frontier=true` + `meta.domain`，**复用共享的 documents/embeddings 栈**，并自动出现在运营控制台的 **Sources** 注册表中（category = `frontier`）。

### 5.3 LLM 综合"研究前沿"（AI 优先，独立审计）

强推理 LLM（`tier="strong"`，默认 DeepSeek V4-pro）读取每个领域近期的预印本 + 期刊文章 + 专家之声，**蒸馏出 5–7 条前瞻性"研究前沿"（ResearchFront）**，每条结构化输出：

- **title**（3–6 词命名）· **summary**（当下正在发生什么）
- **direction**（前瞻方向论点：未来 1–5+ 年走向）· **significance**（意义 / 二阶影响）
- **maturity**（`emerging` | `accelerating` | `maturing`）· **horizon**（`near` | `mid` | `long`）
- **momentum** 0–100 · **confidence** 0–1
- **key_papers**：**经校验的 arXiv id 引用**（只保留确实存在于输入列表中的 id，**杜绝幻觉引用**）· **key_terms** · **key_voices**

> **强调长周期方向，而非交易建议**：System prompt 明令"偏好精确与智识诚实，胜过炒作；强调长周期 DIRECTION 与二阶影响，不做近期交易"。一次综合代表**前沿的当前状态** —— 每次 `synthesize` 会**替换**而非累加该领域的前沿（先 `DELETE` 再重写）。

### 5.4 接口与 CLI

```
GET  /api/exploration/overview          # 探索仪表盘：每个领域一张卡（AI 优先）
GET  /api/exploration/section/{domain}  # 领域详情：前沿 + 被引论文 + 期刊 + 专家之声（未知域 404）
POST /api/exploration/refresh[?domain=] # 后台任务：拉取最新预印本/期刊/X → 重新综合
```

```bash
xar explore              # 全部 6 个领域：拉取 → 综合
xar explore ai           # 仅 AI 前沿
xar explore physics --days 30 --no-voices
```

存储：`frontier_fronts`（每条前沿一行）+ `frontier_domain_state`（每域 rollup：headline + momentum + 计数）。前端按 momentum 排序展示前沿卡片，点开领域看完整前沿 + 被引论文（可点回 arXiv）+ 期刊 + 专家之声。

---

## 六、两大技术护城河（深度讲解）

### 🏰 护城河 #1 —— 双时态、可引用的产业链知识图谱

> 关键文件：`src/xar/kg/store.py` · `resolve.py` · `extract.py` · `signals.py` · `expert.py`，本体见 `src/xar/ontology/`

这是 XAR 与所有"LLM 直出研报"产品的**根本分水岭**。传统做法是把文档切块丢进向量库，**关系和时间全丢失**；XAR 则把每个事实建模为**带双时间戳的可引用图谱实体**。

#### ① Bi-temporal（双时态）—— "某日为真"可查
每条边/事件携带两套时间：

- **`t_valid_from` / `t_valid_to`**：世界中为真的有效期（这笔订单何时生效、何时被取代）
- **`observed_at` / `invalidated_at`**：我们何时获知、何时被推翻

**关键纪律**：后发文档**从不覆盖**先前为真事实，而是显式 `supersede`（`store.supersede_edge`，只置 `invalidated_at = now()`，**永不删除**）。于是"X 在 2024-Q3 的供应商是谁"这种**时点查询**是一等公民（`graphrag.neighbors(as_of=...)`），这正是传统图谱做不到的。

#### ② 确定性实体消解（一等公民，写入前执行）
LLM 抽取的 KG 最大的"静默腐坏点"是**实体歧义**：Innolight / 中际旭创 / Zhongji 是同一家，COHR / Coherent / II-VI legacy 也是同一家。XAR 在**每次 KG 写入前**强制跑三层级联消解（`resolve.py`）：

1. **精确归一别名**（剥 Inc/Corp/Ltd/Technology… + 别名表）→ 置信度 1.0
2. **trigram 模糊匹配**（`pg_trgm similarity ≥ 0.55`）→ 自动学习新别名
3. **resolve_or_create**（确定性 `ent_<sha256>` id）

#### ③ 事件级跨源去重
同一笔订单可能同时出现在 EDGAR 8-K、cninfo 公告、路透新闻里。XAR 用 **`dedup_key` = SHA-256(company|type|date|magnitude|route)** 做内容哈希去重（`store.add_event`，`ON CONFLICT DO NOTHING`），三源同一事件**自动收敛为一条**。

#### ④ 本体标准锚定 —— 既要领域速度，又要开放互操作
**决策**：**自建轻量领域本体**（`NodeType`/`EdgeType`/`CatalystType`，code-as-truth）+ **锚定两个开源标准**：

- **FIBO**（金融业务本体，EDM Council）—— 机构/股权/角色的规范 IRI
- **schema.org**（Organization/Product）—— 便于 JSON-LD 导出

为何不整体采用 FIBO：它穷尽刻画金融工具/合约，却没有"光模块二供""CPO 技术路线"这类**垂直概念**。垂直层用代码建模更快、可测；`node_iri()`/`edge_iri()` 把任一节点/边导出为 FIBO/schema.org 对齐 IRI，保互操作。

#### ⑤ FinMetric 规范财务词表 —— 结构化数据互通的钥匙
Finnhub/FMP/Polygon/Yahoo/Wind 对同一事实命名各异（`grossProfitRatio` vs `grossMargin` vs `grossMargins`）。XAR 定义 **29 个规范指标**（`FinMetric` 枚举），每个 provider 经 `canonical_metric(provider, field)` 归一到统一键，于是 `fundamentals`/`estimates` 表**只说一种语言**，多源按 `source`+`as_of` 共存（双时态友好）。

#### ⑥ 统一催化剂流 —— 结构化与非结构化"一视同仁"
**最优雅的抽象**（`kg/signals.py`）：把估计修正、内部人集群买入、预测市场异动**全部蒸馏进同一条 `kg_events` 催化剂流**，映射保持在 10 类催化剂分类内（`SIGNAL_TO_CATALYST`）。于是检索、多空辩论、回测对"一致预期上修"和"公告催化剂"**完全等价对待**。

| 结构化信号 | 阈值（保守） | 蒸馏为催化剂 |
|---|---|---|
| 营收估计修正 | ±2% | `earnings`（极性随方向） |
| EPS 估计修正 | ±3% | `earnings` |
| 内部人集群买入 | 90 日内 ≥3 人 / ≥$250k 净买 | `equity_investment` |
| 预测市场高概率 | ≥0.60 | `capex_guidance` / `accelerator_launch` |

#### ⑦ 专家智能体 —— 信噪比放大层（关键创新）
原始召回式抽取会把社媒/公众号噪声也写进图谱。XAR 在其上加一层**买方分析师级 LLM 过滤**（`kg/expert.py`）：

- 对每条另类数据（X / 公众号 / 资讯 / AIFINmarket）跑领域专家 LLM，输出 `ExpertInsight`（含 `stance`/`catalyst_type`/`thesis`/`signal_quality` 0–1）
- **质量门 `QUALITY_MIN = 0.55`**：只有 `relevant AND thesis AND quality ≥ 0.55 AND 解析到公司` 才写入 `kg_events(license_tag='expert')`
- **实测**：80 篇公众号文章 → **3 条买方级观点**（keep-rate **3.75%**）—— **质量优先于召回**

#### ⑧ 10 类催化剂分类法（领域知识沉淀）
经过深度行业建模，XAR 把产业链所有驱动事件收敛为 10 类有日期、可被推翻的催化剂五元组（每条带 company/date/magnitude/polarity/confidence/source_filing_id/affected_nodes/tech_route_tag）：

`capex_guidance`(capex 指引) · `order`(订单) · `qualification`(客户认证) · `product_ramp`(新品量产) · `accelerator_launch`(AI 加速器发布) · `capacity_expansion`(供应商扩产) · `supply_constraint`(供给约束) · `earnings`(业绩/指引) · `equity_investment`(股权投资) · `tech_substitution`(技术替代)

---

### 🏰 护城河 #1.5 —— 语义数据库：数字表之外的"立场 / 叙事 / 因果 / 前瞻"

> 关键文件：`src/xar/storage/schema.sql`（`semantic_facts` 视图） · `src/xar/kg/extract.py` · `src/xar/kg/resolve_claims.py` · `src/xar/retrieval/graphrag.py`（`semantic()`）

结构化数字表（`fundamentals`/`estimates`/`prices`）记录"数字是多少"，却**装不下**催化剂叙事、立场、因果、前瞻预期。XAR 在三张既有双时态表上**加性扩展**（不另起平行表）出一层**时间戳化、可回测、本体锚定的语义层**：

- **加性列**：`kg_events` 增 `theme/segment/narrative/time_orientation`；`kg_edges` 增 `causally_linked` EdgeType；`expert_insights` 增 `as_of/theme/segment/time_orientation`。抽取时填写 `time_orientation`（`forward_looking`|`backward_looking`）、一段因果/前瞻的 `narrative`（"为什么 / 将驱动什么"）与 `drivers`（因果实体 → `causally_linked` 边 + `attrs.drivers`）。
- **统一语义流 `semantic_facts`（一个 SQL VIEW）**：把催化剂事件层（`kg_events`，排除 `license_tag='expert'`）与专家叙事/立场层（`expert_insights` 保留行，其 insight 臂 LEFT JOIN `kg_events` 以回填兑现结论）**UNION 成一条可点查的形状** —— 每行携带 `as_of`(有效时) + `observed_at`(事务时) 做时点查询、`source_doc_id` 溯源、`theme/segment/company_id` 本体锚、`polarity/narrative/time_orientation/resolution` 立场与因果。
- **检索**：`graphrag.semantic()` 对该视图做时点查询；`agents/nodes.py` 把语义流注入分析师简报。

#### 前瞻声明的"预期 → 兑现"闭环（净新增能力）

`time_orientation='forward_looking'` 标记**只写不验**。`src/xar/kg/resolve_claims.py` 的 `resolve_forward_claims()` 闭合这条"预期 → 兑现"环：一条有方向（polarity ±）的前瞻催化剂，当**之后同公司、同向、属于"兑现类"事件**（`earnings`/`order`/`product_ramp`/`capacity_expansion`/…，按 `COALESCE(event_date, observed_at)` 在窗口内）出现时，置为 `hit`/`miss`；否则 `stale`（非终态，每轮可重检，迟到的回填仍可升级）。**只改 `forward_looking` 行**，其余事件日志保持仅追加。结论经 `kg_events.resolution / resolved_at / realizes_event_id` 落库，并通过 `semantic_facts.resolution` 暴露。CLI：`xar resolve-claims`。

#### 一条具体的语义记录（Micron 风格示例，形状真实）

```text
kind=event  company_id=micron  category=capex_guidance  polarity=positive
as_of=2025-12-18  observed_at=2025-12-18  time_orientation=forward_looking
narrative="HBM 2026 产能已售罄、定价上修 —— 将驱动 DRAM 毛利与数据中心营收上行"
drivers=["hbm","datacenter_demand"]   tech_route_tag=hbm   confidence=0.72
source_doc_id=doc_8f3a…   license_tag=NULL   theme=ai_chip  segment=chip_memory
resolution=hit            ← 由 2026-03-20 的 earnings(positive) 兑现回填
```

---

### 🏰 护城河 #2 —— 可控、可审计、强溯源的多 Agent 报告流水线

> 关键文件：`src/xar/agents/graph.py` · `nodes.py` · `debate.py` · `report.py` · `evidence_gate.py` · `state.py`

这是 XAR 与"放养式 Agent swarm"的**根本分水岭**。它是一条**手工编排的确定性 DAG**（刻意不走自由 LangGraph，保可控），仅含**一个受限自治岛**（多空辩论）。

#### 8 阶段流水线

```
[1]规划 → [2]图谱检索 → [3]5并行分析师 → [4]多空辩论 → [5]风险压测
                                                    → [6]主编合成 → [7]证据闸 → [8]人工审批 → 发布
```

每经过一个节点，状态 checkpoint 入 `report_runs.state`（**节点级可续跑**），全程受 `BudgetExceeded` 保护（单次美元预算上限）。

| # | 阶段 | 实现 | 技术亮点 |
|---|---|---|---|
| 1 | **规划/Scope** | `resolve.resolve()` 把请求公司解析到规范图谱实体，锁定数据快照 | 进入本体坐标系 |
| 2 | **图谱检索** | `graphrag.supply_chain()` + `events()` 取上下游/股权/单一来源风险/最近 40 条催化剂 | 双时态遍历，构建共享 "graph brief" |
| 3 | **5 并行分析师** | 每个经 `_ground()` 调混合 RAG，**每次命中立即 `cite()` 注册引用** | 数值类分析师 **`numeric=True` 仅在通过 tie-out 的 chunk 上 grounding** |
| 4 | **多空辩论** | 两轮（`_ROUNDS=2`），strong tier，**仅基于分析师已引用发现**（不接原始检索） | 唯一涌现区，bear prompt 显式枚举供给约束/CPO-LPO 替代/客户集中 |
| 5 | **风险压测** | 枚举 4–6 个会改变结论的风险，各带 severity + 证实/证伪证据 | 强制对抗性思考 |
| 6 | **主编合成** | Data-CoT → Concept-CoT → Thesis-CoT，**指令"保留所有 `[n]` 标记，禁止发明引用"** | 一图三品 |
| 7 | **证据闸** | `evidence_coverage` + `numeric_grounding` + LLM-as-judge `hallucination_risk` | `coverage ≥ 0.55 AND risk < 0.5` 才放行 |
| 8 | **人工审批** | `interrupt()` 风格，报告默认 `awaiting_approval`，POST `/api/report/{id}/approve` 才发布 | 强制非投资建议免责声明 |

#### 5 个并行分析师
1. **fundamental**（fast，数值）—— 营收/毛利/指引
2. **catalyst**（fast）—— 有日期催化剂与极性
3. **supply_chain**（fast）—— 多跳遍历：谁二供 EML、单一来源暴露、NVIDIA 股权、技术替代威胁
4. **sentiment**（fast，中英双语）—— 前瞻需求信号
5. **valuation**（strong，数值）—— DCF/估值倍数，绑定 KG 派生的"需求时钟"（GPU 发布节奏）

每个 prompt 明令：**"不要陈述无法引用的数字。证据薄弱就直说。"**

#### 一图三品（一份图谱事实库，三种制品）
- **深度报告** `deep_report`：7 节（快照/论点、供应链、催化剂、多空、风险、估值、看点）
- **跟踪摘要** `tracking_summary`：仅输出**自上次快照以来变化了什么**（`graphrag.changes_since` 利用双时态 `observed_at`/`invalidated_at`）
- **启示** `takeaways`：5–8 条带引用要点

#### 证据闸（信任层）—— 金融场景不可妥协
- **`evidence_coverage`**：数值句子中携带 `[n]` 引用的比例
- **`numeric_grounding`**：引用中通过 `tie_out_ok` 的比例
- **`hallucination_risk`**：LLM-as-judge（Pydantic 结构化）估算"任何重要论点无支撑"的概率
- 低于阈值 → 状态置 `awaiting_approval`，**人工复核在前**

---

## 七、六个工程级创新（与"通用 RAG 套壳"的差异）

### ⚙️ 创新 1 —— 数值对账闸（tie-out）：金融场景的头号杀手锏

> 关键文件：`src/xar/parsing/tie_out.py`

**问题**：VLM/LLM 在密集财务表上会幻觉数字，错抽取静默产出"言之凿凿却错"的数字 —— 这是 LLM 金融场景**第一失败模式**。

**XAR 的解法**（确定性检查，非 LLM 判断）：
- 检测合计/小计行（`合计/小计/total/sum`）
- **智能识别非可加表**（利润表：cost/expense/profit/margin/net/减/利润/成本…）→ 保守放行，避免误报"营收−成本=利润"
- 真正的加法明细表：合计须与列和在 **2% 容差**内对账，偏差 >50% 才判失败
- 失败的 chunk 置 `chunks.tie_out_ok=FALSE`
- **`vector.hybrid_search(numeric=True)` 排除未对账 chunk**，于是数值报告结论**绝不建立在未对账的表上**

证据闸把此指标暴露为 `numeric_grounding`。**这一层是绝大多数 LLM 研报产品完全没有的。**

### ⚙️ 创新 2 —— 单 Postgres 收敛：运维成本骤降，能力等价

蓝图原计划 Neo4j + RAGFlow + Graphiti + LangGraph + Dagster + Langfuse + Next.js 多进程重栈。XAR 落地时收敛为 **单 Postgres + pgvector + 自建薄层**：

- **向量 + 关系 + 双时态图谱 + 对象指针 + 前沿前沿表** 一库打通
- **RRF over pgvector + pg_trgm** 做混合检索，无需独立 BM25 服务（Elasticsearch/RAGFlow）
- 一套备份/HA/监控，2 人团队即可运维
- **能力等价**：双时态、实体消解、事件去重、GraphRAG 全部保留；前沿探索零新增基础设施

### ⚙️ 创新 3 —— 确定性 + 受限自治的 Agent 设计哲学

不是"放养 swarm"，而是 **"确定性外层 DAG + 一个受限自治岛"**：
- 节点级重试 + 检查点续跑 + 单次预算上限
- 唯一涌现区是多空辩论（迭代上限、低温、结构化输出、预算受限）
- 终端节点是**图谱溯源的报告合成，不是交易决策**
- 强制非投资建议免责声明 + 人审中断

这是**机构合规要求的正确形态**：可审计、可复现、可中断。

### ⚙️ 创新 4 —— LLM 任务管理器：按任务路由 · 多供应商 · 计费感知 · 完整回退 · 可换代

> 关键文件：`src/xar/models/registry.py`（可更新模型库）· `src/xar/models/router.py`（任务路由器）· `src/xar/models/llm.py`（回退执行器）· `config.py`

旧的"两级 fast/strong 路由"已升级为一套**任务管理器**：不再只分快/强两档，而是按**任务类别**择优路由到合适的供应商与计费方式，全程带跨供应商回退与计费感知预算。

- **可更新模型库（code-as-truth）`registry.py`**：`Provider` + `ModelSpec` 数据类，枚举 `Billing`(token|subscription) / `Capability`(fast|strong|reasoning|long_context|cheap_bulk) / `Status`。供应商覆盖 **DeepSeek · Anthropic · OpenAI · GLM(Zhipu) · Kimi(Moonshot)**；`MODELS` 同时收录 token 计费模型与 GLM/Kimi 的**订阅(subscription)条目**。`candidates_for()` 按计费优先稳定排序产出候选链，`_PRICES` 从 `MODELS` 派生。**换代 = 编辑这一个文件**（加 `ModelSpec`、置 `preferred=True`、把旧的翻成 deprecated）。
- **任务路由器 `router.py`**：`TaskClass` 枚举 11 类（`kg_extract`/`expert`/`search_bulk`/`analyst`/`debate`/`editor`/`judge`/`synth`/`eval`/`adhoc_fast`/`adhoc_strong`），`POLICIES` + `resolve(task)` → 有序候选链。**批量/搜索类**（kg_extract/expert/search_bulk）→ `CHEAP_BULK` + **订阅优先**（GLM/Kimi 包月，于是对 947 家公司的夜间抽取走**固定费率**而非无上限 token 账单），再退到预算内的廉价 DeepSeek token；**质量类**（debate/editor/synth）→ STRONG token + 跨供应商回退。解析优先级：`route_overrides` 表（ops API）> env(`XAR_MODEL_*`) > registry `preferred`；`tier="fast|strong"` 经 `as_task` 保留为向后兼容别名，未迁移的调用点零改动。
- **回退执行器（`llm.py`）**：`complete()/complete_json()` 新增 `task=`；逐候选用各自 api_base/key 调用，跳过未配置的供应商、对 transient 错误做一次候选内重试、失败/空响应即轮转到下一候选。
- **计费感知成本**：真正的包月调用记 `usd=0`（订阅批量永不触发预算上限）；而一条订阅 spec 若回退到供应商的**计量 key**，则按真实 per-token 成本入账（堵住计费漏洞）。`_spent(run_id)` 超 `XAR_LLM_MAX_USD_PER_RUN`（默认 $5）抛 `BudgetExceeded`。`llm_usage` 表新增 `provider/task_class/billing` 列。
- **运行时换代（无需重部署）**：`POST /api/ops/llm/route {key, model_id}` 把某能力/任务实时改指到新模型（落 `route_overrides` 表）；运营控制台 **Models** 页展示供应商/模型/路由表/按计费·供应商·任务的花费明细。
- **`complete_json()`**：provider 无关的结构化输出（JSON-mode）—— 注入 Pydantic JSON Schema，解析 + 校验 + 单次重试 + 兜底空默认（DeepSeek 推理模型按 `reasoning_effort` 调参）。

### ⚙️ 创新 5 —— 许可纪律作为架构（CI 硬规则）

> 关键文件：`scripts/check_licenses.py`

开源发布要求**核心代码链接图洁净**。CI 阻断 AGPL/GPL/NC/source-available 进链接核心：

- 排除/隔离：OpenBB(AGPL)、Firecrawl(AGPL)、MinerU<3.1.0(旧 AGPL)、Windmill(AGPLv3)、Marker(GPL+RAIL)、jina-v3(CC-BY-NC)
- Neo4j Community(GPLv3) **作外部进程，绝不嵌入**
- LiteLLM 留 MIT core；Phoenix(ELv2) 仅内部自托管
- **每个文档落库带 `permission`(green/grey/red) 标签**：研报**仅入元数据**（绝不入全文 PDF，版权）；前沿源（arXiv/期刊）**仅入摘要+元数据**

### ⚙️ 创新 6 —— 交钥匙 + 增量点亮（key-gated provider 套件）

17 个数据源全部 **key-gated**：缺 Key 即 `available()=False`、`pull()` 返回空，**从不报错**。

```
零 provider Key → 仅靠 SEC EDGAR + 新闻 + 招聘 + Yahoo(无 key) + arXiv(无 key) 即可端到端跑通
填一个 Finnhub Key → 点亮基本面/估计/评级/内部交易 + 公司新闻（finnhub_news）
填一个 FMP Key → 点亮三大报表/目标价/日线 + 公司新闻
填一个 X Token → 点亮专家社媒流 + 前沿专家之声
arXiv / 期刊 RSS → 开箱即用（无需 Key），前沿探索立即可跑
……逐源点亮，永不阻塞
```

### ⚙️ 创新 7 —— 每夜自动增量采集 + Dagster 运行时

> 关键文件：`src/xar/orchestration/daily.py`（`run_daily`） · `src/xar/orchestration/definitions.py`（Dagster） · `src/xar/storage/runlog.py` + `ingest_runs` 表

XAR 把"每个可达源每夜增量入库"工程化为一条幂等、可续跑的链：`run_daily(stages=('pull','extract'))` —— **按源增量 PULL**（按公司分片、单源失败隔离）→ 解析/嵌入 → `build_kg` → 专家层 → 信号 → `resolve_forward_claims`。**`extract` 阶段全局只跑一次**（不按分片，单批预算上限），廉价 DB 阶段始终运行。`ingest_runs` 表兼作运行日志与**每源增量游标**（`last_success_ts`）；内容哈希 + NOT-EXISTS 游标保证**幂等可续跑**。CLI：`xar daily`。

**Dagster 边车（已部署的每夜运行时）** `definitions.py`：`pull_shard`（8 个静态分区，06:00 调度）+ `extract_all`（单次，06:30，一份批预算）+ `core_daily`（按需）。`docker-compose.yml` 新增 dagster 服务，宿主端口 **`:3001`**（UI / 运行历史 / 重试），独立 `dagster_home` 卷；**仅 `app` 容器跑 `xar init`**（schema owner）。

---

## 八、完整数据源矩阵

### 8.1 非结构化采集（→ 公告/财报/新闻/产品/招聘/公众号 → 本体）

| 类别 | 来源 | 工具 | 合规姿态 |
|---|---|---|---|
| 美股 filing + 基本面 | SEC EDGAR（10-K/Q/8-K/20-F/XBRL/13F/Form 3-4-5） | edgartools（官方免费 API） | 🟢 **绿**：美国政府公共领域 |
| A 股法定披露 + 财报 | cninfo（证监会指定披露） | AKShare | 🟢 **绿**：强制公开披露 |
| 卖方研报 | 东财研报**清单**（标题/机构/评级/目标价/EPS） | AKShare `stock_research_report_em` | 🔴 **红**：**仅元数据**，绝不入全文 PDF（版权） |
| 新闻文章 | 公司 IR 页、交易所、财经新闻 | Scrapy/Crawl4AI + trafilatura | 🟡 **灰**：存事实+引用，不转载全文 |
| **公司新闻 API** | Finnhub `company-news` + FMP（落 `documents`，source='finnhub'/'fmp'） | `finnhub.pull_news` / `fmp.pull_news`（标题/摘要，内容哈希去重） | 🟡 **灰**：自用事实摘录（摘要非全文），流入 build_kg + 专家层 |
| 产品页/规格书 | 模块厂+芯片厂产品页 | 定向礼貌爬取 | 🟢 **绿偏**：公开营销页 |
| 招聘信号 | Greenhouse/Lever/Ashby ATS board | **官方 ATS REST API** | 🟢 **绿**：绝不抓 LinkedIn（ToS+CFAA） |
| **微信公众号** | 自建 [we-mp-rss](https://github.com/rachelos/we-mp-rss) | 公开 feed 端点，零鉴权 stdlib 解析 | 🟡 **灰**：国内最快非结构化情报源 |

### 8.2 结构化 / 另类数据 provider（→ FinMetric 归一 → 信号 → 本体）

| provider | 取数 | 姿态 |
|---|---|---|
| **Finnhub** | basic-financials、EPS/营收估计、recommendation、内部交易（Form 4 codes）、**公司新闻**（`pull_news` / `pull_general_news`） | 灰/自用 OK |
| **FMP** | income/balance/cashflow 全字段、analyst-estimates、price-target、日线 OHLCV、**公司新闻**（`pull_news`） | 付费/免费层 |
| **Polygon** | 日聚合(深度历史)、vX reference-financials | 付费层 |
| **Yahoo (yfinance)** | 全球价格(含 A 股 300308.SZ)、`.info` 基本面快照 | **无 Key**，全球覆盖 |
| **Polymarket** | Gamma 公开 API：AI/算力/加速器相关市场远期概率 | **公开无 Key**：最早的需求侧催化信号 |
| **X (推特)** | 精选专家账号（SemiAnalysis、LightCounting…）+ 领域关键词检索 | 灰/自用；经专家加工进本体 |
| **Reddit** | wallstreetbets+stocks+investing+hardware+semiconductors+nvidia | OAuth 或公开回退 |
| **Wind 万得** | CN A 股深度基本面（WindPy） | 默认关；需本地终端 |
| **AIFINmarket** | CN A 股专业源（Wind/万得 MCP-over-HTTP：基本面+公告+资讯，纯 Python 客户端） | gated |

### 8.3 前沿数据源（→ Exploration 模块；仅元数据/摘要，复用 documents 栈）

| provider | 取数 | 姿态 |
|---|---|---|
| **arXiv** | 按域 `arxiv_cats` 拉取近期预印本（标题/摘要/作者/类目）；公开 Atom API | 🟢 **绿无 Key**：abstract + metadata，前沿一手信号 |
| **Journals / Quanta** | Quanta Magazine + Physics World 公开 RSS：同行评审 / 编辑精选层 | 🟢 **绿无 Key**：metadata + summary |
| **X 专家之声** | 复用 `twitter` provider，但**仅精选研究者 handle**、回复过滤 | 🟡 **灰**：真正的前沿研究者声音 |

> `providers.status()` 现统计 **11 个**结构化/另类/前沿 provider：`fmp · finnhub · polygon · yahoo · wind · polymarket · twitter · reddit · aifinmarket · arxiv · journals`。加上非结构化采集源（edgar · cninfo · news · jobs · wechat）与**新增的 `finnhub_news` 公司新闻源**（与 finnhub 同一 Key 闸控），运营控制台 **Sources** 注册表共展示 **17 个**数据源（11 provider + edgar/cninfo/news/jobs/wechat + 新增 finnhub_news；含 frontier 类 arxiv/journals），并经 `run_source` 注册为可一键运行/自检。

---

## 九、机构级 Web 终端（前端）

> 源码：`web/src/`（React 18 + TypeScript strict + Vite + Tailwind v3 + react-router v6 + lucide-react）。FastAPI 服务其编译产物，**无 mock，全部真实后端数据**。设计令牌：`brand`(海军蓝)、`accent`(蓝，投研)、`warn`(琥珀，运营)、`explore`(靛蓝，探索)、`pos`/`neg`。

XAR 前端是**三个对等模块、三套外壳**：投研门户（`/`，海军蓝）围绕 **Theme → Segment → Company → Signal → Decision** 完整投研链路；前沿探索（`/explore`，靛蓝）描绘研究前沿；运营控制台（`/ops`，琥珀）自省真实平台状态。统一呈现**机构级金融终端审美**（克制、信息密集、`tnum` 等宽数字、无 emoji、无大面积渐变）。研究终端侧边栏底部内置**模块切换按钮**（Exploration / Operations Console），一键跳转。

### 9.1 投研门户：三栏终端布局

```
┌────────────┬──────────────────────────────────────────────┬──────────────┐
│  Sidebar   │  TopBar  主题·周期·覆盖数·更新时间·市场筛选      │ Decision Rail│
│ (navy 导航)│──────────────────────────────────────────────│ (固定右栏)   │
│ Research   │  RegimeSummaryCard   产业链景气 / 综合分 / 动能 │  House View  │
│  Universe  │  ChainHeatmap        产业链热力矩阵 (中枢)      │  Top Opps    │
│  · 主题    │  ┌─────────────────────┬────────────────────┐  │  Top Risks   │
│  · 环节    │  │ SegmentRankingTable │ SignalFeed         │  │  Action Queue│
│  · 公司    │  │ CompanyWatchlist    │ CatalystCalendar   │  │              │
│ Workflow   │  └─────────────────────┴────────────────────┘  │ ──────────── │
│ 模块切换 ↓ │                                                │              │
│ Exploration│                                                │              │
│ Operations │                                                │              │
└────────────┴──────────────────────────────────────────────┴──────────────┘
```

### 9.2 核心组件

| 组件 | 作用 | 技术亮点 |
|---|---|---|
| **ChainHeatmap**（中枢） | 上游→下游每环节一行，6 维热力矩阵（动能/盈利修正/估值/拥挤/供给/Alpha）+ 走势 | `heat(value, scheme)` 三种语义着色（divergent / good-high / good-low） |
| **RegimeSummaryCard** | 链景气相位 + 综合分 + 趋势 + 广度 + 关键驱动 + 环节动能脉冲 | 相位由真实动能派生（accelerating/expansion/peaking/cooling/trough） |
| **SignalFeed** | 10 类催化剂信号流 + 来源 chip（filing/公众号/预测市场/估计/内部人）+ 极性/置信/相对时间 | 统一催化剂流的直接呈现 |
| **DecisionRail** | House View（自动生成的双语 prose）+ Top Opportunities + Top Risks + 可勾选 Action Queue | house view 由 `dashboard.decision()` 从真实图谱事实生成 |
| **CatalystCalendar** | 催化剂按周分组，类型/极性/重要度/"in Nd" | 双时态事件的可视化时间轴 |

### 9.3 前沿探索（Exploration，独立外壳 `/explore`，靛蓝）

| 视图 | 能力 |
|---|---|
| **探索仪表盘** `/explore` | 六大领域卡片（AI 优先）：headline + momentum + 论文/期刊/专家计数 + Top 前沿预览 |
| **领域详情** `/explore/:sectionId` | 该领域全部研究前沿（按 momentum 排序：方向/意义/maturity/horizon/momentum/置信）+ 被引 arXiv 论文（可点回）+ 期刊文章 + 专家之声 |

### 9.4 运营控制台（Operations Console，独立外壳 `/ops`，琥珀）

**8 个控制台页**，由 `/api/ops/*` 实时自省与操作**真实平台状态**（无 mock）：

| 页面 | 能力 |
|---|---|
| **Overview** | 平台全局自省：行数 / 覆盖 / 健康 |
| **Ontology** | 节点/边/催化剂类型 + FIBO/schema.org IRI + FinMetric 词表 + 实时 KG 计数 |
| **Sources** | **15 源**可用性/姿态/行数/上次运行（含 frontier 类的 arxiv/journals）+ **一键运行** + **自检(selftest)** |
| **DataLake** | 非结构化文档/分块/解析/抽取统计 + 浏览器 + **处理 pending** |
| **AltData** | 另类数据专家加工：X/公众号/AIFINmarket → AI 专家提炼（keep-rate 可见）+ 一键处理 |
| **Models** | LLM vendor/model 路由 + 定价 + 用量 + **Test LLM**（真实廉价往返） |
| **Connectors** | MCP & API 接口：出站连接器 + 入站 XAR API（MCP-ready） |
| **Skills** | 8 阶段报告 DAG + 平台能力（检索/消解/对账/抽取/信号桥/嵌入） |

---

## 十、相比传统人工投研的核心优势（营销总结）

### 🎯 优势 1 —— 信息广度：从"5 个终端"到"全市场自动汇聚"
传统分析师依赖 5–10 个付费终端 + 人工浏览。XAR **17 个数据源自动采集**，覆盖中美欧、结构化+另类+非结构化+前沿，且**统一归一到 FinMetric 词表**，多源按 `source`+`as_of` 共存。

### 🎯 优势 2 —— 关系深度：从"散落 PPT"到"双时态知识图谱"
传统分析师把供应链关系记在脑子里或 PPT 里，无法查询、无法版本化。XAR 把每个事实建模为**带双时态的可引用图谱实体**，"X 在 Q3 的供应商"、"谁在某日前二供了 EML"、"GB300 量产时 NVIDIA 已认证的供应商"**皆是一等查询**。

### 🎯 优势 3 —— 时间一致性：从"难查证"到"某日为真可查"
传统研报里"截至 XX 日"常被后续事实静默覆盖。XAR 的 bi-temporal 设计**永不删除**，后发文档只显式 supersede，**"某日为真"永远可查**。

### 🎯 优势 4 —— 产出速度：从"周/报告"到"分钟/报告"
单分析师写一份深度报告需 1–2 周。XAR 的 8 阶段流水线分钟级产出，且**一图三品**（深度/跟踪/启示），跟踪摘要仅重跑催化剂+KG delta+摘要节点，**增量极快**。

### 🎯 优势 5 —— 溯源可信：从"据我们研究"到"`[n]` 可点回源"
传统研报结论难追溯。XAR 每条结论挂 `[n]` 引用标记 → 源 chunk / EDGAR-cninfo filing ID / 双时态图谱事实 + 有效期。**证据闸**计算覆盖度/数值对账/幻觉风险，低置信醒目标注交人工。

### 🎯 优势 6 —— 数值可信：从"手工抄录易错"到"对账闸兜底"
传统分析师手工抄录财务表易错。XAR 的**数值对账闸**阻止"言之凿凿却错"的数字进报告 —— 数值类结论**仅在通过 tie-out 的 chunk 上 grounding**。

### 🎯 优势 7 —— 视角对抗：从"单一立场"到"强制多空辩论"
传统研报常陷单一立场确认偏误。XAR 强制**多空辩论子图**（两轮、仅基于已引用发现）+ 风险压测（枚举 4–6 个会改变结论的风险）。

### 🎯 优势 8 —— 合规人审：从"终审靠自觉"到"强制中断节点"
传统流程终审依赖自觉。XAR 报告默认 `awaiting_approval`，经人审才发布，**强制非投资建议免责声明**，快照版本化绑定数据快照，可复现可审计。

### 🎯 优势 9 —— 边际成本：从"加报告=加人"到"加报告≈几美元"
传统模式加一份报告 = 加一个分析师（高固定成本）。XAR 加一份报告 ≈ 几美元 LLM 调用（单次预算上限 $5 可调），**线性可扩展**。

### 🎯 优势 10 —— 信噪比：从"淹没在噪声"到"专家智能体过滤"
传统另类数据（社媒/公众号）信噪比极低。XAR 的**专家智能体**（买方分析师级 LLM）做质量门过滤，**实测 80 篇公众号 → 3 条买方级观点（3.75% keep-rate）**。

### 🎯 优势 11 —— 知识前沿：从"读不完"到"AI 优先的前沿地图"
个人阅读有上限、信息有时效。XAR 的**前沿探索模块**把 arXiv 预印本 + 顶级期刊 + X 专家之声**自动综合为前瞻研究前沿**（方向/意义/成熟度/时域/动能），AI 优先、引用经校验、独立审计通过 —— 让投研团队**先于市场看见知识边界的移动方向**。

---

## 十一、30 秒启动（交钥匙）

```bash
cp .env.example .env
# 编辑 .env，填任一 LLM Key 即可（经 LiteLLM 路由）：
#   DEEPSEEK_API_KEY=sk-...          默认（V4-flash 抽取 / V4-pro 推理，开箱即用）
#   ANTHROPIC_API_KEY=sk-ant-...     + 设 XAR_MODEL_FAST/STRONG=claude-haiku-4-5 / claude-opus-4-8
#   OPENAI_API_KEY=sk-...
docker compose up --build
```

打开 <http://localhost:8000> → 点「采集全部公司」→ 选公司 + 报告类型 → 「生成报告」；或点侧栏 **Exploration** 进入前沿探索。

> **唯一必填项是一个 LLM Key**（任选其一）。数据库、向量库、嵌入模型（CPU，无需 GPU）、对象存储都已内置自动初始化。**arXiv / 期刊 RSS 无需 Key，前沿探索开箱即跑**；所有行情/另类数据 provider 的 Key 全部可选，缺失即自动跳过。

### CLI（本地开发或脚本驱动）

```bash
xar init            # 建表 + 种子 + 知识图谱骨干
xar ingest [id]     # 采集→解析→建图谱（全 basket；先跑单家用 xar ingest nvidia）
xar ingest-wechat   # 微信公众号→本体
xar pull [id]       # 结构化+另类数据→信号
xar build-kg        # 仅抽取图谱
xar report <id>     # 生成深度报告 / 跟踪摘要 / 启示
xar explore [域]    # 前沿探索：拉取预印本/期刊/X → 综合研究前沿（omit=全部 6 域）
xar backtest        # 催化剂→收益回测
xar eval            # 检索命中率 + 报告 rubric
xar providers-status# 查看各 provider 是否已配置
xar status          # 各表行数自省
xar serve           # Web UI + API
```

---

## 十二、技术栈一览

| 类别 | 选型 |
|---|---|
| 语言 | Python 3.11+（后端）· TypeScript strict（前端） |
| Web 框架 | FastAPI + Uvicorn · Typer CLI |
| 数据库 | PostgreSQL 16 + **pgvector** + **pg_trgm**（一库：向量+关系+双时态图谱+前沿前沿表） |
| LLM 网关 | **LiteLLM + LLM 任务管理器**（按任务路由 · 多供应商 DeepSeek/GLM/Kimi/Anthropic · token-vs-订阅计费感知 · 跨供应商回退 · 预算上限 · 一处换代/运行时改路由；**默认 DeepSeek V4**） |
| 嵌入 | **fastembed**（ONNX/CPU，零 GPU；默认 bge-small 384d，可换 BGE-M3 1024d） |
| 采集 | edgartools · AKShare · trafilatura · we-mp-rss · ATS API · Finnhub · FMP · Polygon · yfinance · Polymarket · X · Reddit · Wind · AIFINmarket · **arXiv · 期刊 RSS** |
| 解析 | pdfplumber（默认）+ **数值对账闸** · Docling 可选 |
| 知识图谱 | 自建双时态 + 确定性实体消解 + 事件级去重 · Graphiti 可选 |
| 检索 | pgvector 稠密 + trigram 词法，**RRF(k=60)** 融合 + GraphRAG |
| 多 Agent | 自建可控 8 阶段 DAG + 多空辩论 + 证据闸 + 人审中断 |
| 前沿探索 | arXiv + 期刊 + X 专家 → 强推理 LLM 综合研究前沿（`frontier_fronts` / `frontier_domain_state`） |
| 前端 | React 18 + TypeScript + Vite + Tailwind v3（三模块三外壳）+ react-router v6 + lucide-react |
| 编排 | Dagster（可选 `.[orchestration]`） |
| 评测 | 检索命中率 + 报告 rubric（LLM-judge）+ Arize Phoenix（可选） |
| 容器 | Docker Compose（db + app + 可选 werss profile） |
| 许可 | **Apache-2.0**（代码）；CI 硬规则保链接图洁净 |

---

## 十三、一句话总结

> **XAR 把"一名买方分析师团队"工程化为"一条可控、可审计、强溯源的数据流水线" —— 并把同一套引擎延伸为"人类知识前沿的探索之眼"。**
>
> 它不是让 AI 取代分析师，而是让**一名分析师指挥一支 AI 分析师军团** —— 用双时态知识图谱作记忆，用数值对账闸作纪律，用多空辩论作对抗，用证据闸作信任，用人审中断作合规，**用前沿探索看见知识边界的移动方向**。
>
> **一个 API Key，一条产业链，一套机构级投研体系 —— 外加一只望向人类知识前沿的眼睛。**

*本项目设计蓝图见 [`DESIGN.md`](./DESIGN.md)，Web UI 说明见 [`UI.md`](./UI.md)，快速启动见 [`README.md`](./README.md)。代码以 Apache-2.0 开源；抓取到的第三方数据不对外分发（自用姿态）。*