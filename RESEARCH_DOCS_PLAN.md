# RESEARCH_DOCS_PLAN — 非标投研数据专属本体编排开发计划(RD 轨)

> 状态:**设计定稿,未实施**。本文档是唯一交付物;实施按 RD-P0→P5 推进,每阶段 pytest+ruff 独立绿。
> 范围:Gangtise / Wind AIFinMarket 两家 CN 数据商的**非标准化数据**——公司纪要 / 会议·业绩会纪要 /
> 专家纪要 / 券商研报 / 数据追踪(指标时序)——建成专属 Ontology 编排,持续供给 thesis 撰写与追踪
> (dossier → evidence_link → thesis_fact_links → health_v3 争论天平);GLM 常驻工人自动抓取存档
> (**核心公司优先→非核心、最新优先→历史前溯、每日刷新**);并建**独立智能体**每日复核抓取质量。
>
> **已确认决策(用户,2026-07-07)**:①内容策略=**保守只存摘要**(brief+精华段落,零文件下载信用);
> ②审计模型=**Token 强模型**(独立于生产 GLM,~$0.2-0.5/日);③**EDB 数据追踪本期必做**。

---

## 1. 数据源盘点(已完整阅读两家最新文档,实证核对)

### 1.1 Gangtise —— 已接 vs 未接

**已接**(`src/xar/providers/gangtise/`):loginV2 裸 token 认证(带 `Bearer` 前缀即 0000001008)、
`open-reference/securities/search`(代码解析)、`open-fundamental` 三表/估值分位/一致预期(结构化)、
`open-ai/agent` 三个同步端点(one-pager / investment-logic / peer-comparison → documents grey)。
glm_worker `_gangtise`:12h 节拍、轮转游标 `get_state("cursor")["gangtise"]`、limit 15、**纯注册表序**。

**未接——本计划的核心供给面**(base `https://openapi.gangtise.com/application/...`):

| 数据域 | 端点 | file_type 码 | 关键字段/参数 |
|---|---|---|---|
| **券商研报** | `open-insight/broker-report/getList` | 10 | `reportId, title, brief, publisher.{brokerName,author}, publishTime(13位ms), securityList[], industryList[], category, llmTagList(inDepth/earningsReview/industryStrategy), rating*`;参数 `keyword/startDate/endDate/securities/institutions/categoryList/ratingList/ratingChangeList`;页 ≤50 |
| **会议纪要/业绩会/专家纪要** | `open-insight/summary/v2/getList` | 60 | `summaryId, title, brief, essence[].content(精华段落), publishTime/summaryTime, securityList[], institutionList[], categoryList(earningsCall/strategyMeeting/fundRoadshow/shareholdersMeeting/maMeeting/specialMeeting/companyAnalysis/industryAnalysis/other), marketList(aShares/hkStocks/usChinaConcept/usStocks), participantRoleList(management/expert), guest(专家), sourceName` |
| **经营讨论 MD&A** | `open-ai/management_discuss/from-announcement` 与 `/from-earningsCall` | — | `{securityCode, reportDate(yyyy-MM-dd 季/半年末), discussionDimension: businessOperation\|financialPerformance\|developmentAndRisk\|all}` → 全文 Markdown。**最干净的结构化业绩会/经营讨论源;按 reportDate 取历史季度,不受账户历史窗限制** |
| **投研线索(变更雷达)** | `open-ai/agent` security_clue | — | `{queryMode: bySecurity\|byIndustry, securities\|[all], pageFrom, pageSize≤500, startTime/endTime(ms), source:[researchReport\|conference\|announcement\|view]}` → 统一"每证券有什么新东西"行(研报/电话会议纪要/公告/观点)。**天然每日增量驱动器** |
| **首席观点** | `open-insight/chief-opinion/getList` | 40 | `researchAreaList(宏观/策略/固收/金工/海外), chiefs, llmTags(strongRcmd/earningsReview/topBroker/newFortune)` |
| **KB 语义检索(RAG)** | `open-data/ai/search/knowledge_base` | — | `{query, startTime, endTime, resourceTypes:[FILE_TYPE_MAP], top}` → 语义片段 `{title, time, content, resourceType, sourceId}` |
| 其余(本期不接) | 外资研报(11)/外资观点/独立观点(42)/公告(50/51)/公众号(90, 10信用/行)/日程(路演/调研/策略会)/热点话题/个股一句话/业绩会点评(异步)/私有会议(asr/速记, ~5信用) | — | 列 backlog |

**FILE_TYPE_MAP**(KB resourceTypes / 下载 file-type 码):研究报告=10 外资研报=11 内部报告=20 AI云盘=30
首席观点=40 外资独立观点=42 公司公告=50 港股公告=51 **会议纪要=60 调研纪要=70 网络纪要=80** 产业公众号=90。

**约束**:页 ≤50(默认帽 100);内容下载信用计费(纪要全文 ~5 信用/篇、公众号 10 信用/行)——
**保守策略下全部不碰 file 下载端点**;试用账户历史深度 ≈1 个月(研报/观点/纪要 list);CN egress。

### 1.2 Wind AIFinMarket —— 已接 vs 未接

MCP JSON-RPC(`Bearer` token,SSE 响应)。已接:`financial_docs.get_company_announcements`(公告)、
`stock_data.get_stock_price_indicators`(市值/PE)。**孤儿函数**:`pull_news`/`pull_theme_news` 已定义
但无调用方(providers/aifinmarket.py)。未接且本期必做:**`economic_data.natural_language_get_edb_data`**
(EDB 宏观/行业指标时序,`{executionMode: search|fetch|searchFetch, question, beginDate/endDate}`)——
即用户要求的"数据追踪"维度。Wind 的深度研究/纪要生成属 wind-alice agent 工作流(独立 key),非批量数据面,不接。

---

## 2. 管线现状与缺口(已实证,file:line)

现有文档流:`provider.pull → ingestion/base.Doc → save → documents` →(仅 wechat)triage 闸 →
`parse.parse_pending` 全量分块嵌入 → `kg/extract.build_kg`(permission≠red)→ `kg/expert.process`
(**仅 ALT_SOURCES**)→ `semantic_facts` 视图 → `research/thesis.dossier`「语义事实/文档段落」+
`research/evidence_link._pending_facts` → thesis_fact_links → health_v3。

| # | 缺口 | 证据 |
|---|---|---|
| 1 | **无文档类型本体**:doc_type 是自由文本,五类 CN 投研文档无类型语义 | 全 repo 仅 api/dataroom.py:19 有 5 值 UI 白名单;ontology/ 无任何文档分类 |
| 2 | **研报/纪要绕过 expert 语义道**:`ALT_SOURCES` 不含 gangtise → 洞见永不入 semantic_facts → thesis dossier 与 evidence_link 拾取不到 | kg/expert.py:29 `ALT_SOURCES=(wechat,x,news,aifinmarket,social,product,finnhub,fmp)` |
| 3 | **抓取无优先序/无历史回填**:`_gangtise` 纯注册表序轮转;history.py 回填规划器(us/cn 相位,(source,company,year) 单元)不含 gangtise | glm_worker.py:207-235;ingestion/history.py:47,78 |
| 4 | **无抓取质量审计**:TaskClass 只有 JUDGE/EVAL(FAST);无 AUDIT 类;无抓取对账面板 | models/router.py:30-77 |
| 5 | 研报文本→评级/目标价无抽取(仅 cninfo 元数据 regex → analyst_ratings);build_kg 里 gangtise 排 ELSE 3 最低优先 | ingestion/cninfo.py:172;kg/extract.py:227-229 |

**可复用接缝**(全部已核实):`ontology/altdata.py` 注册表模式(frozen dataclass tuple + 自检断言);
`ingestion/base.Doc/save`(幂等 upsert);`structured.upsert_rating/upsert_estimate` 沉淀端 +
`FUNDAMENTAL_SOURCE_PRIORITY`;`mining/targeting.build_targets`(被挑战论点优先序先例)+
`ontology/debates.seed_company_ids()`(~20 旗舰)+ `coverage360.coverage_all()`(证据厚度序);
kvstate 游标模式(cadence/cursor/history_cursor/quota);`_due/_stamp` 节拍;`llm.pinned` + quota 状态机 +
`new_batch_run_id` 预算帽;`agents/evidence_gate`(LLM 裁判先例)+ `kg/repair`(零 LLM 完整性审计先例)+
`GET /api/ops/wechat-mining`(对账端点模板)。

---

## 3. 目标数据流(端到端走查)

```
[每日 fresh_sweep]
security_clue(全市场变更雷达,零 LLM) ──→ 目标集 (company, source)
  → broker-report/getList 全局日期窗扫(≤50/页×2页)──┐
  → summary/v2/getList 全局日期窗扫(essence 精华段落)──┤→ documents(doc_type ∈ research_docs 注册表,
  → 核心 30 家:management_discuss 新季度 MD&A ────────┘   permission=grey, doc_id=gangtise:{kind}:{id})
                                                            │
[第二遍,零 LLM] parse_broker_ratings:meta.rating/目标价 → analyst_ratings(source='gangtise')
                                                            │
parse_pending 分块嵌入(既有) → dossier「文档段落」hybrid_search
build_kg(kg_priority 升至 1) → kg_events(catalyst)
expert.process(ALT_SOURCES+gangtise,研报提示词变体) → expert_insights(stance/thesis/预期差)
                                                            │
                     semantic_facts 视图 ──→ thesis dossier「语义事实」
                                        └──→ evidence_link 相对主张分类 → thesis_fact_links
                                              → health_v3 争论天平 lean_now → flipped → 自动重写论点
[每日] wind_edb.pull:每主题 3-6 条策展 EDB 指标 → alt_signals → sync_alt_events(|z|≥2) → 支柱信号校正
[每 6h] backfill_step:(doc_type, 月窗) 单元向旧行走;MD&A 按 reportDate 回填 8 个历史季度
[每日,独立] research_audit.run_audit():零 LLM 完整性对账 + Token 强模型抽样复核 → 失败重排队
```

---

## 4. 关键设计裁决(经独立 Plan agent 对抗审核定稿)

1. **零新表**。documents(meta JSONB)+ kvstate 水位线足够:list 元数据行本身就是文档行(brief 即正文),
   `documents.id` 即唯一键;审计计数直接查 documents。**前置必改**:`ingestion/base.py:47-49` 的 `Doc.id`
   把 `text[:200]` 掺进哈希——同一篇纪要先存 brief 再更新会裂成两行。加性修法:`Doc` 增
   `doc_id: str | None = None`,`id` property 优先返回显式 id(`gangtise:summary:{summaryId}` /
   `gangtise:report:{reportId}` / `gangtise:mgmt:{code}:{reportDate}:{origin}`),未设置时哈希行为
   **逐字节不变**(测试锁死,防存量 id 漂移)。同文更新走 `save()` 既有 `ON CONFLICT ... SET text` 原地升级。
2. **security_clue 不落 documents**。线索是指针非内容,入库会污染 KG 队列(build_kg 只按 permission 过滤)。
   作为零 LLM 每日"变更雷达"返回目标集驱动定向拉取;当日摘要存 kvstate(`gangtise_clue_state`)供审计对账
   (线索数 vs 实拉文档数)。
3. **triage 闸门不泛化**。纪要/研报是策展高 SNR 付费源,与公众号噪声不同类;`wechat_pending_clause`
   保持 wechat-only,零改动(文档化该决定)。
4. **券商评级走零 LLM 确定性通道**。镜像 `cninfo.parse_research_ratings`(ingestion/cninfo.py:172)惯例:
   第二遍扫描已入库 doc 行 meta 里的 rating/ratingChange/目标价字段,按 (company, day) 聚合 →
   `structured.upsert_rating(source='gangtise')`。幂等、零 LLM、复用 analyst_ratings 表。
5. **EDB 进 alt_signals 不进 documents**。指标时序的语义归宿是 `AltSignalSpec` + `alt_signals` 表 +
   `thesis_signals` 支柱校正引擎(全套机器已存在,零下游改动)。NL 接口稳定性工程化见 §6 RD-P1b。
6. **gangtise 回填不塞 history.py**。history 单元是 (source, company, year) **逐公司**;Gangtise list 的
   天然单元是 **(doc_type, 月窗) 全局扫描**(一页 50 行覆盖全市场)——硬套会逐公司重复扫同一批全局数据。
   独立小游标行走器(`planner.backfill_step`,自有 kvstate key),复刻 history.py 的毒单元重试/断点续走语义。
7. **自适应回填深度 vs 账户历史上限**。"从最新到历史前溯"撞上试用账户 ~1 个月可见窗:月窗从水位线向旧行走,
   **连续 2 个空窗即判定账户可见深度到底**,盖 `exhausted` 戳停机;账户升级后 `xar research backfill --reset`
   清戳自动续挖。**例外:MD&A 按 reportDate 取历史季度不受此限**——核心公司 8 个历史季度的经营讨论是
   试用期就能拿满的最深语料,回填优先级最高。
8. **审计独立性是硬保证**。新 `TaskClass.AUDIT → RoutePolicy(Capability.STRONG, Billing.TOKEN, "normal")`
   (解析到 DeepSeek v4-pro / Claude token 链,**永不落 GLM 订阅池**);`run_audit()` 在 glm_worker 中于
   `llm.pinned(GLM_PIN)` 上下文**之外**、quota 门**之外**调用。验收模型与生产模型不同源、不同计费池。
9. **expert 单文档单洞见局限接受为 v1**。多公司纪要只出一条洞见(expert_insights.doc_id 唯一键,改 shape
   属侵入性变更);prompt 明示以锚公司(securityList[0]/documents.company_id)为主对象;拆分列 backlog。

---

## 5. 本体设计(RD-P0 交付物)

### 5.1 新 `src/xar/ontology/research_docs.py`(altdata.py 注册表模式)

```python
@dataclass(frozen=True)
class ResearchDocSpec:
    doc_type: str            # 'broker_report' | 'meeting_minutes' | 'expert_minutes'
                             # | 'mgmt_discussion' | 'one_pager' | 'investment_logic' | 'peer_comparison'
    label_zh: str            # 券商研报 / 会议·业绩会纪要 / 专家纪要 / 经营讨论MD&A / …
    vendor: str              # 'gangtise' | 'aifinmarket'
    endpoint: str            # 文档化真相:'open-insight/broker-report/getList' 等
    kb_resource_type: int | None   # Gangtise FILE_TYPE_MAP 码(10/60/…;KB RAG 检索用)
    permission: str = "grey"
    license_tag: str = "gangtise-research-extracted-facts-self-use"
    extraction: str = "expert"     # 'expert'(语义洞见道)| 'kg_only' | 'none'
    rating_extractor: bool = False # True → 零 LLM 评级第二遍(broker_report)
    catalyst_types: tuple[str, ...] = ()   # 典型催化类型,⊆ CATALYST_TYPES(自检断言)
    pillar_kinds: tuple[str, ...] = ()     # ⊆ thesis.PILLAR_KINDS(自检断言)
    kg_priority: int = 1           # build_kg CASE 权重(0 最高;研报/纪要=1,与 cninfo/news 平级)
    cadence_hours: int = 24        # 新鲜度 SLO(审计对账用)
    body: str = "brief"            # 保守策略:全部 'brief'(brief+essence;mgmt_discussion 本体即全文)
    rationale_zh: str = ""
```

**注册内容**:`broker_report`(10, rating_extractor=True, catalyst=earnings/guidance_change/contract_win…)、
`meeting_minutes`(60, categoryList 驱动;catalyst=earnings/guidance_change/product_ramp)、
`expert_minutes`(60, participantRoleList 含 expert 时分流;pillar_kinds=demand/technology/supply_chain)、
`mgmt_discussion`(open-ai/management_discuss;catalyst=earnings/guidance_change)、
既有三个 agent 类型(one_pager/investment_logic/peer_comparison,extraction='expert')使 vendor 注册完备。

**派生集合**:`DOCS_BY_TYPE`、`EXPERT_DOC_TYPES`(extraction=='expert' 的 (vendor, doc_type) 集)、
`RATED_DOC_TYPES`、`kg_priority_case() -> str`(可信常量编译 SQL CASE 片段,同
`structured.source_priority_sql` 手法——单一真相防两处漂移)。模块底部 assert 自检(altdata.py:78 同款)。

### 5.2 `ingestion/base.py` 加性改动(裁决 1)

`Doc.doc_id: str | None = None`;`id` property:`return self.doc_id or f"{source}:{sha256(...)[:20]}"`。

### 5.3 `ontology/altdata.py` 增补(RD-P1b)

每主题 3-6 条策展 `AltSignalSpec(source="wind_edb", scope="theme", cadence="monthly", pillar_kinds=…)`,
spec 携带**固定中文 question**(code-as-truth;示例:ai_chip→「中国集成电路产量当月值」「全球半导体销售额」、
ai_optical→「光模块/光电子器件出口金额」、restaurants→「社会消费品零售总额:餐饮收入当月值」、
retail→「社会消费品零售总额当月同比」、humanoid_robotics→「工业机器人产量当月值」、
internet→「实物商品网上零售额累计同比」…实施时逐条真机核对 EDB 可检索性再定稿)。

---

## 6. 分阶段实施(每阶段 pytest+ruff 独立绿;零 DDL)

### RD-P0 — 本体层(纯代码,零 DB、零网络)
- 交付:§5.1 `ontology/research_docs.py` + §5.2 `Doc.doc_id` + `ontology/__init__` 导出。
- **测试 `tests/test_research_docs.py`**(test_macro_links 的 code-as-truth 风格):doc_type 全局唯一;
  catalyst/pillar 词表合法;kg_priority_case() 片段覆盖全部注册类型;`Doc(doc_id="x").id=="x"`;
  **未设 doc_id 时哈希与现值逐字节相同**(锁旧行为)。

### RD-P1 — 爬取器扇出(零 LLM):`providers/gangtise/insight.py`
- **`client.py`**:URL 常量(BROKER_REPORT_LIST_URL / SUMMARY_LIST_URL / MGMT_DISCUSS_ANN_URL /
  MGMT_DISCUSS_EC_URL / SECURITY_CLUE_URL)+ 分页助手
  `pages(url, payload, *, page_size=50, max_pages) -> Iterator[list[dict]]`。
- **新 `insight.py`** 函数签名:
  ```python
  def pull_broker_reports(*, start_ms: int, end_ms: int, max_pages: int = 2) -> dict
  def pull_minutes(*, start_ms: int, end_ms: int, max_pages: int = 2) -> dict
  def pull_mgmt_discussion(company_id: str, report_date: str) -> int   # 两端点各一 doc,meta.origin 区分
  def pull_clues(*, start_ms: int, end_ms: int) -> dict   # → {targets:[(cid, source)], counts};不落库
  def parse_broker_ratings(company_id: str | None = None) -> dict      # 裁决 4:零 LLM 评级第二遍
  def _company_for_security(sec: dict) -> str | None       # securityList/gtsCode 反解 registry(零网络索引)
  ```
  要点:**全局日期窗扫描不逐公司**(一页 50 行覆盖全市场,反解过滤到 registry 名单);13 位 ms
  `publishTime` → `published_at`;`text` = title + brief + essence[].content;meta 存整行(rating 字段、
  categoryList、guest、sourceName、summaryTime、llmTagList);participantRoleList 含 expert →
  doc_type='expert_minutes';**不实现任何 file 下载函数**(保守策略,零信用消耗)。
- **config 新旋钮**:`gangtise_insight_pages=2`、`gangtise_history_months=12`、
  `gangtise_history_quarters=8`、`gangtise_core_size=30`。
- **测试 `tests/test_gangtise_insight.py`**(离线 monkeypatch client.post/pages;fixture 形状以真机捕获为准):
  ms 时间戳解析;securityList 反解;同 doc_id 原地更新(seeded_db 一测);评级聚合镜像 cninfo 语义
  (多报同日计数、pt_mean/high/low);clue 摘要形状与 targets 去重。

### RD-P1b — EDB 数据追踪(本期必做):`providers/alt/wind_edb.py`
- §5.3 altdata 策展 + 新 `wind_edb.pull()`:逐指标
  `natural_language_get_edb_data(executionMode="searchFetch", question, beginDate, endDate)` →
  解析序列 → altstore 入 `alt_signals`(PIT:period_end=经济期 / observed_at=拉取时);
  **逐指标容错**:单位 sanity(量级区间断言)+ 日期单调 + 空序列跳过,单指标失败不拖批;
  水位线 kvstate(`wind_edb_state`,每指标 last period_end)。
- 接入 `ingestion/alt.pull_all` 源表(与 twse_revenue 等并列)→ 自动进 `sync_alt_events`(|z|≥2 → kg_events)
  → thesis_signals 支柱校正,**零下游改动**。
- NL 接口稳定性纪律:question 固定不变(code-as-truth);响应解析容忍字段别名;首跑打印"question→
  解析到的指标名"映射供人工核对;极端不稳的个别指标降级跳过(本期仍交付稳定子集,不算失败)。
- **测试**:test_altdata 不变式扩展(新 spec 合法、theme 存在、pillar_kinds 合法)+ 离线 fixture
  解析/校验/幂等(双跑行数不变)。

### RD-P2 — 语义路由:事实流进 thesis(最小接线)
- **`kg/expert.py`**:
  - `ALT_SOURCES += ("gangtise",)` ——**最小开关**:纪要/研报洞见 → `kg_events(license='expert')` +
    `expert_insights` → `semantic_facts` → dossier「语义事实」+ `evidence_link._pending_facts` 自动拾取
    (已核实走 semantic_facts 视图,下游零改动)。
  - 新 `_SYSTEM_RESEARCH` 提示词变体,`(source, doc_type) ∈ EXPERT_DOC_TYPES` 时启用。方向:这是
    **策展的专业投研内容**(非噪声怀疑姿态)——提取报告/纪要自身的核心论断与**预期差**(超预期/不及预期),
    stance 取报告观点,catalyst_type + 关键数字进 evidence,entity 默认=锚公司(process_document 的
    SELECT 补 `company_id, doc_type` 两列;实体解析失败且为锚定 vendor 文档时 fallback `d.company_id`)。
  - 副作用声明:存量 gangtise agent 文档(数百篇)一次性进入 expert 队列——订阅池内数日消化,合意。
- **`kg/extract.py build_kg`**:ORDER CASE 注入 `research_docs.kg_priority_case()`(注册类型升至 1;
  8-K 仍 0 不被饿死;其余 gangtise 留 ELSE 3)。
- **测试 `tests/test_research_routing.py`**:prompt 变体选择(capture complete_json 的 system);
  ALT_SOURCES 含 gangtise;seeded_db 插两 doc 验证队列顺序;插 kept 洞见行验证 semantic_facts 可见
  (即 evidence_link 可拾取)。

### RD-P3 — 爬取规划器:核心优先 + 最新优先 + 自适应回填 + 每日节拍
- **新 `providers/gangtise/planner.py`**:
  ```python
  def cn_priority_order() -> list[str]   # (1) debates.seed_company_ids()∩CN
                                          # (2) coverage360.coverage_all() composite 降序 (3) 注册表序
  def core_set() -> set[str]              # 层1 ∪ coverage top-N(N=gangtise_core_size,默认 30)
  def fresh_sweep() -> dict               # 每日:clue 雷达 → 全局研报/纪要日期窗扫 → 核心公司新季度
                                          # mgmt_discussion → parse_broker_ratings → 推水位线
  def backfill_step(units: int = 2) -> dict  # (doc_type, 月窗) 单元最新月先行;连续 2 空窗盖 exhausted 戳;
                                              # MD&A 按 reportDate 季度序回填(不受历史上限,优先级最高)
  def backfill_status() -> dict
  def reset_backfill() -> None
  ```
  kvstate keys:`gangtise_crawl`(每 doc_type 水位线)、`gangtise_backfill`(游标 + exhausted 戳 +
  毒单元重试,复刻 history.py 语义)、`gangtise_clue_state`。
- **`orchestration/glm_worker.py`**:
  - `_gangtise` 结构化轮转切片顺序:注册表原序 → `planner.cn_priority_order()`(游标是偏移量,
    名单日内稳定,轻微漂移可接受);
  - `_pull_fresh` 增四站:`_run("gangtise_insight", s.gangtise_insight_hours*3600, planner.fresh_sweep)`
    (默认 24h,满足"每日自动刷新")、`_run("gangtise_backfill", 6*3600, backfill_step)`、
    `_run("wind_edb", 24*3600, wind_edb.pull)`、`_run("aifinmarket_theme", 24*3600, _aifin_theme)`
    (修 `pull_theme_news` 孤儿:THEMES 驱动每主题一查)。
- **config**:`gangtise_insight_hours=24`、`gangtise_backfill_units=2`。
- **测试**:planner 三段排序(monkeypatch coverage_all/seed_company_ids);月窗生成最新在前 + 连续空窗
  停机 + exhausted 后 reset 续走;毒单元两次后跳过;glm_worker 节拍注册(test_glm_worker 模式,
  monkeypatch 内层函数断言 `_run` 被正确 key/cadence 调用)。

### RD-P4 — 独立审计智能体
- **新 `orchestration/research_audit.py`**:
  - `integrity_report() -> dict`(零 LLM):每 doc_type 计数/24h 增量;水位线新鲜度 vs spec.cadence_hours
    SLO;company 链接率(company_id 非空占比);`kg_extracted_at IS NULL` 积压;expert 覆盖率
    (join expert_insights);评级行/日;url/doc_id 重复率;clue 数 vs 实拉数对账;回填游标态;
    EDB 各指标最新 period_end 新鲜度。
  - `spot_check(n: int = 12, run_id=None) -> dict`(LLM,TaskClass.AUDIT):按 doc_type 分层抽样
    近 24-48h 文档 + 其衍生物(kg_events / expert_insights / thesis_fact_links),每篇一裁决
    `AuditVerdict{company_link_ok, doc_type_ok, extraction_grounded, link_sensible: bool,
    severity: low|medium|high, notes_zh}`;`kg.extract._grounded` 零 LLM 预检先行降 LLM 负担。
  - `run_audit() -> dict`:integrity + spot_check → kvstate `research_audit`(含历史环比);
    处置:文档级失败 → `UPDATE documents SET kg_extracted_at=NULL, meta=jsonb_set(meta,'{audit}',…)`
    (重排队);系统级(链接率跌破阈值/水位线超 SLO 2×)→ 标旗进报告,不自动动刀。
- **`models/router.py`**:`AUDIT = "audit"` + `POLICIES[AUDIT] = RoutePolicy(Capability.STRONG,
  Billing.TOKEN.value, "normal")`(裁决 8:永不落 GLM 池)。
- **`glm_worker.run_once`**:`if _due("research_audit", 24*3600)` 于 pinned 上下文外、quota 门外调用
  (token 计费、量极小,不受 GLM 耗尽影响——独立性的另一半)。
- **`api/app.py`**:`GET /api/ops/research-crawl`(仿 wechat-mining 端点:integrity + 最近审计裁决 +
  回填/水位线态)。**`cli.py`**:`research_app` 子应用——`xar research crawl`(手动 fresh_sweep)/
  `backfill [--reset]` / `audit [--no-llm]` / `status`。
- **测试 `tests/test_research_audit.py`**:seeded_db 插 fixture 文档 → integrity 计数确定性;
  monkeypatch complete_json → 失败裁决触发 kg_extracted_at 清空 + meta.audit 标记;
  router 测试:`resolve(TaskClass.AUDIT)[0].billing == "token"`(**硬锁独立性**)。

### RD-P5 — 面板/文档/真机 E2E/部署
- DESIGN.md 新节(§5.13 非标投研数据编排:注册表 + 数据流图 + 审计)+ README env 表;
  ops 面板最小卡片(可选)。
- **真机验收清单**(CN egress 环境;字段名以真机为准修正离线 fixture——前科:Gangtise 资产负债表
  companyType/currency 位错):
  1. `xar research crawl` 冒烟 → documents 各 doc_type 落库、meta 完整、doc_id 格式正确;
  2. `parse_broker_ratings` → analyst_ratings(source='gangtise')行;
  3. `xar glm-worker run --once` → expert 洞见出现(license='expert',stance/thesis 非空);
  4. `xar thesis link <core-cid>` → 新事实被相对主张分类进 thesis_fact_links;
  5. `xar research backfill` 两轮 → 月窗后退、MD&A 历史季度落库;人为造空窗验证 exhausted 停机;
  6. EDB:`alt_signals` 各主题指标落库、`signal_snapshot` 可见;
  7. `xar research audit` → integrity + 抽样裁决;`GET /api/ops/research-crawl` 返回全景;
  8. security_clue `securities=[all]` 真机验证;不支持则切核心 30 家逐司降级路径。
- 全量 pytest+ruff;docker 重建(worker 自动带新站,compose 零改动);部署经用户确认后执行。

---

## 7. 成本纪律(估算)

| 项 | 日频估算 | 约束 |
|---|---|---|
| Gangtise list(clue+研报+纪要) | 5-10 次全局日期窗调用(非逐公司) | `gangtise_insight_pages=2` |
| mgmt_discussion | 核心 30 家摊薄 1-2 次/日(回填期突发由 `gangtise_backfill_units=2` 限速) | 回填游标 |
| **文件下载** | **0(保守策略,file 端点全部不碰)** | — |
| Wind EDB | ~30 指标 × 1 次/日 | 水位线 |
| GLM 抽取增量 | +30~100 docs/日(brief 短文本,便宜) | 既有 batch 帽 $20 + quota 自愈 |
| 审计 LLM | ~13 次/日 Token 强模型 ≈ **$0.2-0.5/日** | TaskClass.AUDIT + 预算帽 |

## 8. 风险

| 风险 | 缓解 |
|---|---|
| Gangtise 实 API 字段名漂移(前科:资产负债表位错) | 离线 fixture 以真机捕获为准;P5 真机对照逐端点修正后才算验收 |
| 试用账户历史深度 ~1 月 | 裁决 7 自适应空窗停机 + MD&A 季度道不受限先吃满;升级后 `--reset` 续挖 |
| EDB NL 接口批量稳定性 | 固定 question(code-as-truth)+ 逐指标容错跳过 + 首跑人工核对映射;交付稳定子集 |
| 多公司纪要单洞见损失 | v1 锚公司为主(裁决 9);拆分列 backlog |
| Doc.id 覆写回归 | 测试锁死"未设 doc_id 哈希逐字节不变" |
| security_clue `[all]` 不支持 | 降级核心 30 家逐司 clue 查询(30 次/日可接受) |
| expert 队列被存量文档挤占 | 订阅池 + 每轮 limit 既有;数日自然消化 |

## 9. 明确不做(本期)

外资研报/外资观点/独立观点/公告全文/产业公众号(10 信用/行)/投研日程/热点话题/私有会议 asr(逐项列
backlog,注册表模式下每项=加一条 spec+一个 pull 函数);Gangtise KB RAG 检索作为 Chathy 工具(独立小任务);
文件全文下载(用户选保守;打开=实现 download_full+信用账本,注册表 body 字段已预留 'full_core' 语义);
triage 闸门泛化;expert 多洞见拆分;wind-alice agent 工作流接入。

## 附录 A — `ontology/research_docs.py` 完整骨架(RD-P0)

```python
"""非标投研文档本体(代码即真相)——给两家 CN 数据商的券商研报/会议·业绩会·专家纪要/
经营讨论建立类型语义,决定每类文档:进哪条抽取道(expert 语义 / 仅 KG / 不抽)、
build_kg 队列优先级、是否走零 LLM 评级第二遍、典型催化类型与影响的论点支柱 kind。
镜像 ontology/altdata.py 的注册表 + 自检不变式模式;经 ontology/__init__ 导出。"""
from __future__ import annotations
from dataclasses import dataclass, field
from .catalysts import CATALYST_TYPES
from .thesis import PILLAR_KINDS


@dataclass(frozen=True)
class ResearchDocSpec:
    doc_type: str
    label_zh: str
    vendor: str                     # 'gangtise' | 'aifinmarket'
    endpoint: str                   # 文档化真相(open-insight/broker-report/getList …)
    kb_resource_type: int | None    # Gangtise FILE_TYPE_MAP 码(10/40/60);非 KB 检索类为 None
    extraction: str = "expert"      # 'expert' | 'kg_only' | 'none'
    rating_extractor: bool = False
    catalyst_types: tuple[str, ...] = ()
    pillar_kinds: tuple[str, ...] = ()
    kg_priority: int = 1            # build_kg CASE(0 最高;研报/纪要=1 与 cninfo/news 平级)
    cadence_hours: int = 24
    body: str = "brief"            # 'brief'(保守:brief+essence)| 'full_core'(预留,本期不用)
    permission: str = "grey"
    license_tag: str = "gangtise-research-extracted-facts-self-use"
    rationale_zh: str = ""

_R = ResearchDocSpec
RESEARCH_DOCS: tuple[ResearchDocSpec, ...] = (
    _R("broker_report", "券商研报", "gangtise", "open-insight/broker-report/getList", 10,
       rating_extractor=True,
       catalyst_types=("earnings", "guidance_change", "contract_win", "tech_substitution"),
       pillar_kinds=("demand", "moat", "financials", "valuation"),
       rationale_zh="卖方深度/点评/行业策略;元数据含评级/目标价 → 零 LLM 评级第二遍。"),
    _R("meeting_minutes", "会议·业绩会纪要", "gangtise", "open-insight/summary/v2/getList", 60,
       catalyst_types=("earnings", "guidance_change", "product_ramp", "order"),
       pillar_kinds=("demand", "financials", "technology"),
       rationale_zh="业绩会/策略会/路演纪要;essence 精华段落即高浓度语义。"),
    _R("expert_minutes", "专家纪要", "gangtise", "open-insight/summary/v2/getList", 60,
       catalyst_types=("tech_substitution", "supply_constraint", "product_ramp"),
       pillar_kinds=("technology", "supply_chain", "demand"),
       rationale_zh="participantRoleList 含 expert / guest 的专家交流纪要——链上一手草根验证。"),
    _R("mgmt_discussion", "经营讨论 MD&A", "gangtise", "open-ai/management_discuss", None,
       catalyst_types=("earnings", "guidance_change"),
       pillar_kinds=("demand", "financials"),
       rationale_zh="from-earningsCall/from-announcement 结构化经营讨论,按 reportDate 取历史季度。"),
    # 既有 agent 类型纳入注册表(vendor 完备;这三类已在 pull_research 落库,extraction=expert)
    _R("one_pager", "一页通", "gangtise", "open-ai/agent/one-pager", None,
       pillar_kinds=("demand", "moat", "valuation")),
    _R("investment_logic", "投资逻辑", "gangtise", "open-ai/agent/investment-logic", None,
       pillar_kinds=("demand", "moat", "technology")),
    _R("peer_comparison", "同业对比", "gangtise", "open-ai/agent/peer-comparison", None,
       pillar_kinds=("moat", "valuation")),
)

DOCS_BY_TYPE: dict[str, ResearchDocSpec] = {s.doc_type: s for s in RESEARCH_DOCS}
EXPERT_DOC_TYPES: frozenset[str] = frozenset(s.doc_type for s in RESEARCH_DOCS if s.extraction == "expert")
RATED_DOC_TYPES: frozenset[str] = frozenset(s.doc_type for s in RESEARCH_DOCS if s.rating_extractor)
RESEARCH_SOURCES: frozenset[str] = frozenset(s.vendor for s in RESEARCH_DOCS)  # {'gangtise'}


def kg_priority_case(col: str = "doc_type") -> str:
    """build_kg ORDER BY 里注入的可信常量 CASE 片段(同 structured.source_priority_sql 手法)。"""
    whens = " ".join(f"WHEN '{s.doc_type}' THEN {s.kg_priority}" for s in RESEARCH_DOCS)
    return f"CASE {col} {whens} ELSE 3 END"

# 代码即真相自检(import 时执行;test_research_docs.py 再断言一遍)
assert len({s.doc_type for s in RESEARCH_DOCS}) == len(RESEARCH_DOCS), "duplicate doc_type"
for _s in RESEARCH_DOCS:
    assert set(_s.catalyst_types) <= set(CATALYST_TYPES), f"{_s.doc_type}: bad catalyst_type"
    assert set(_s.pillar_kinds) <= set(PILLAR_KINDS), f"{_s.doc_type}: bad pillar_kind"
    assert _s.extraction in ("expert", "kg_only", "none")
```

**`ingestion/base.py` 加性改动**(裁决 1;`Doc.id` property 现为 `f"{self.source}:{sha256((url+title+text[:200]))...}"`):
```python
@dataclass
class Doc:
    ...
    doc_id: str | None = None       # 显式稳定 id(供来源天然主键的文档,如 gangtise summaryId)
    @property
    def id(self) -> str:
        return self.doc_id or f"{self.source}:{_hash(...)}"   # 未设置 → 逐字节维持旧哈希
```
`ontology/__init__.py`:`from . import research_docs as research_docs`(+ 可选重导出 `RESEARCH_DOCS/EXPERT_DOC_TYPES`)。

---

## 附录 B — `providers/gangtise/insight.py` 骨架 + `client.py` 增补(RD-P1)

**`client.py` 追加常量 + 分页助手**:
```python
_INSIGHT = "https://openapi.gangtise.com/application/open-insight"
BROKER_REPORT_LIST_URL = f"{_INSIGHT}/broker-report/getList"
SUMMARY_LIST_URL       = f"{_INSIGHT}/summary/v2/getList"
CHIEF_OPINION_URL      = f"{_INSIGHT}/chief-opinion/getList"
MGMT_DISCUSS_ANN_URL   = f"{_AI}/management_discuss/from-announcement"
MGMT_DISCUSS_EC_URL    = f"{_AI}/management_discuss/from-earningsCall"
SECURITY_CLUE_URL      = f"{_AI}/agent/security_clue"     # 真机确认后固定

def pages(url, payload, *, page_size=50, max_pages=2):
    """按 from/size 翻页,逐页 yield list(≤max_pages)。end-of-data 提前停。"""
    for i in range(max_pages):
        data = post(url, {**payload, "from": i * page_size, "size": page_size})
        rs = rows(data) or (data or {}).get("list") or []
        if not rs:
            return
        yield rs
        if len(rs) < page_size:
            return
```

**`insight.py` 完整签名 + 落库要点**:
```python
from ...ingestion.base import Doc, save
from ...ingestion.registry import company_by_id, COMPANIES
from ...storage import structured, db
from ...ontology.research_docs import DOCS_BY_TYPE
from . import client
from .__init__ import gts_code       # 复用既有 registry→gtsCode(带精确码匹配 + 缓存)

# gtsCode → registry cid 的反解索引(一次构建,零网络):registry 每个 .SS/.SH/.SZ ticker 的
# 数字段 → cid;securityList[].securityCode(gtsCode)拆数字段查。
def _sec_index() -> dict[str, str]: ...
def _company_for_security(sec: dict) -> str | None: ...

def _save_doc(*, doc_type, vendor_id, company_id, title, text, published_at, meta) -> None:
    spec = DOCS_BY_TYPE[doc_type]
    save(Doc(company_id=company_id, source=spec.vendor, doc_type=doc_type,
             doc_id=f"gangtise:{'report' if doc_type=='broker_report' else 'summary' if 'minutes' in doc_type else 'mgmt'}:{vendor_id}",
             title=title, text=text, published_at=published_at,
             permission=spec.permission, license_tag=spec.license_tag, meta=meta))

def pull_broker_reports(*, start_ms, end_ms, max_pages=2) -> dict:
    """全局日期窗扫 broker-report/getList;securityList 反解 registry 过滤;text=title+brief;
    meta 存整行(rating/ratingChange/targetPrice/category/llmTagList/publisher)。返回 {saved,seen}。"""
def pull_minutes(*, start_ms, end_ms, max_pages=2) -> dict:
    """summary/v2/getList;participantRoleList 含 expert → doc_type=expert_minutes 否则 meeting_minutes;
    text=title + '\n' + brief + '\n'.join(essence[].content);meta 存 guest/institution/category/market。"""
def pull_mgmt_discussion(company_id, report_date) -> int:
    """from-earningsCall + from-announcement 各一 POST({securityCode, reportDate, discussionDimension:'all'});
    doc_id=gangtise:mgmt:{code}:{reportDate}:{ec|ann};text=Markdown content。"""
def pull_clues(*, start_ms, end_ms) -> dict:
    """security_clue({queryMode:'byIndustry'|'bySecurity', securities:[all]|核心码, pageSize≤500, startTime,endTime,
    source:[researchReport,conference,announcement,view]}) → {targets:[(cid,source)], counts_by_source}。不落库。"""
def parse_broker_ratings(company_id=None) -> dict:
    """裁决 4:扫 documents(source='gangtise', doc_type='broker_report') 的 meta.rating/targetPrice,
    映射 CN 5 档(强推/买入/增持/中性/减持/卖出→bucket,同 cninfo._RATING_BUCKETS)+ 目标价,
    按 (company_id, day) 聚合 → structured.upsert_rating(source='gangtise')。零 LLM、幂等。"""

# 时间:13 位 ms publishTime → datetime.fromtimestamp(ms/1000, tz=UTC);日期窗以 ms 传参。
# 无任何 file 下载函数(保守策略,零信用)。
```

**config 追加**:`gangtise_insight_pages:int=2`、`gangtise_history_months:int=12`、`gangtise_history_quarters:int=8`、`gangtise_core_size:int=30`、`gangtise_insight_hours:int=24`、`gangtise_backfill_units:int=2`。

---

## 附录 C — `providers/gangtise/planner.py` 骨架(RD-P3)

```python
from ...storage.kvstate import get_state, save_state
from ...ontology.debates import seed_company_ids
from ...ontology import coverage360
from ...ingestion.registry import COMPANIES, company_by_id
from ...config import get_settings
from . import insight

def _is_cn(c) -> bool:
    return c.get("region") == "CN" or any(str(t).endswith((".SS",".SH",".SZ")) for t in (c.get("tickers") or []))

def cn_priority_order() -> list[str]:
    cn = [c["id"] for c in COMPANIES if _is_cn(c)]
    seeds = seed_company_ids()
    cov = coverage360.coverage_all()          # {cid: {composite}}
    def key(cid): return (cid not in seeds, -(cov.get(cid, {}).get("composite", 0.0)))
    return sorted(cn, key=key)                # 种子 → 覆盖厚 → 其余

def core_set() -> set[str]:
    order = cn_priority_order()
    return set(order[: get_settings().gangtise_core_size]) | (seed_company_ids() & set(order))

def fresh_sweep() -> dict:
    """每日:① clue 雷达(变更目标)② 全局研报+纪要日期窗扫(近 N 天)③ 核心公司新季度 mgmt_discussion
    ④ parse_broker_ratings ⑤ 推 kvstate['gangtise_crawl'] 各 doc_type 水位线。返回各步计数。"""

def backfill_step(units=2) -> dict:
    """kvstate['gangtise_backfill'] 游标:(doc_type, 月窗) 单元最新月先行,向旧行走;
    连续 2 空窗盖 doc_type.exhausted 戳(裁决 7);MD&A 走 reportDate 季度序不受历史上限;
    毒单元 retry-once-then-skip(复刻 history.py:296-303)。每单元后 save_state(crash-safe)。"""

def backfill_status() -> dict: ...
def reset_backfill() -> None: save_state("gangtise_backfill", {})
```

**glm_worker 接线**(`_pull_fresh` 内,`_run` 模式;`_gangtise` 结构化序换 `cn_priority_order()`):
```python
_run("gangtise_insight", s.gangtise_insight_hours*3600, planner.fresh_sweep)
_run("gangtise_backfill", 6*3600, lambda: planner.backfill_step(s.gangtise_backfill_units))
_run("wind_edb", 24*3600, lambda: alt.pull_source("wind_edb"))
_run("aifinmarket_theme", 24*3600, _aifin_theme)   # 修 pull_theme_news 孤儿,遍历 THEMES
```

---

## 附录 D — `orchestration/research_audit.py` 骨架 + router AUDIT(RD-P4)

```python
from pydantic import BaseModel, Field
from ..models import llm
from ..models.router import TaskClass
from ..storage import db, kvstate
from ..ontology.research_docs import RESEARCH_DOCS, DOCS_BY_TYPE

class AuditVerdict(BaseModel):
    company_link_ok: bool = True
    doc_type_ok: bool = True
    extraction_grounded: bool = True
    link_sensible: bool = True
    severity: str = "low"          # low | medium | high
    notes_zh: str = ""

def integrity_report() -> dict:
    """零 LLM:每 doc_type 计数 + 24h 增量 + 水位线新鲜度 vs spec.cadence_hours SLO;
    company 链接率;kg_extracted_at IS NULL 积压;expert 覆盖率(join expert_insights);
    评级行/日;doc_id/url 重复率;clue vs 实拉对账(读 gangtise_clue_state);回填游标态;
    EDB 各指标最新 period_end 新鲜度。全部 GROUP BY SQL 直查 documents/expert_insights/alt_signals。"""

def spot_check(n=12, run_id=None) -> dict:
    """分层抽样近 48h 各 doc_type 文档 + 衍生物(kg_events/expert_insights/thesis_fact_links),
    先跑 kg.extract._grounded 零 LLM 预检,再一篇一次 complete_json(task=TaskClass.AUDIT)→ AuditVerdict。
    独立性:AUDIT 解析到 token 强模型链,绝不落 GLM 池;调用在 llm.pinned(GLM_PIN) 之外。"""

def run_audit(no_llm=False) -> dict:
    """integrity + (可选)spot_check → kvstate['research_audit'](含环比);
    文档级失败 → UPDATE documents SET kg_extracted_at=NULL, meta=jsonb_set(meta,'{audit}',verdict)(重排队);
    系统级(链接率<阈 / 水位线超 SLO×2)→ 标旗进报告,不自动动刀。"""
```
**router.py**:`AUDIT = "audit"` 入 TaskClass;`POLICIES[TaskClass.AUDIT] = RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal")`。
**glm_worker.run_once**:`if _due("research_audit", 24*3600): out["audit"] = research_audit.run_audit()` —— 置于 `_alt_correction` 之后、**pinned 与 quota 门之外**。
**api/app.py**:`GET /api/ops/research-crawl` → `{integrity, lastAudit: kvstate.get_state("research_audit"), backfill: planner.backfill_status()}`(仿 `/api/ops/wechat-mining`,app.py:496)。
**cli.py**:`research_app = typer.Typer(...)`;`crawl`(planner.fresh_sweep)/`backfill [--reset]`/`audit [--no-llm]`/`status`。

---

## 附录 E — `providers/alt/wind_edb.py` 骨架 + EDB 策展指标(RD-P1b)

**`altdata.py` 追加**(每主题 3-6 条;meta.question 是固定中文查询串,真机核对可检索性后定稿):
```python
_S("alt.edb_semi_sales", "Global semiconductor sales", "全球半导体月度销售额", "monthly", "USD",
   "theme", "rising", ("demand","cyclical"), "wind_edb", themes=("ai_chip",),
   rationale_zh="WSTS/SIA 全球半导体销售额——AI 芯片链总需求月频刻度。"),   # meta 携 question
_S("alt.edb_ic_output", "China IC output", "中国集成电路产量当月值", "monthly", "亿块",
   "theme", "rising", ("demand","supply_chain"), "wind_edb", themes=("ai_chip",)),
_S("alt.edb_robot_output", "China industrial robot output", "工业机器人产量当月值", "monthly",
   "台", "theme", "rising", ("demand",), "wind_edb", themes=("humanoid_robotics",)),
_S("alt.edb_catering", "China catering retail", "社零餐饮收入当月值", "monthly", "亿元",
   "theme", "rising", ("demand","cyclical"), "wind_edb", themes=("restaurants",)),
_S("alt.edb_retail_total", "China retail sales YoY", "社零总额当月同比", "monthly", "%",
   "theme", "rising", ("demand","cyclical"), "wind_edb", themes=("retail",)),
_S("alt.edb_online_retail", "China online physical retail YoY", "实物商品网上零售额累计同比",
   "monthly", "%", "theme", "rising", ("demand",), "wind_edb", themes=("internet",)),
# ai_optical / ai_software / space 各补 1-2 条(光模块出口 / 云计算相关 / 卫星发射)
```
(AltSignalSpec 需加可选 `meta: dict = field(default_factory=dict)` 字段承载固定 question;或用独立 `EDB_QUESTIONS: dict[key,str]` 映射避免动 dataclass——**倾向后者,零 schema 改动**。)

**`providers/alt/wind_edb.py`**:
```python
from ...providers.aifinmarket import _mcp_call   # 复用既有 MCP tools/call(SSE 解析)
from ...ontology.altdata import ALT_SIGNALS
from ...storage.altstore import upsert_signal

EDB_QUESTIONS = { "alt.edb_semi_sales": "全球半导体销售额 当月值", ... }   # code-as-truth

def available() -> bool:
    from ...config import get_settings; return get_settings().enable_aifinmarket

def pull(limit=None) -> dict:
    """逐 wind_edb 信号:natural_language_get_edb_data(executionMode='searchFetch',
    question=EDB_QUESTIONS[key], beginDate=水位线-13m, endDate=today)→ 解析 (period_end,value) 序列 →
    逐点 upsert_signal(key, theme=spec.themes[0], period_end, value, unit=spec.unit, source='wind_edb',
    meta={question,edb_code?});单位 sanity + 日期单调 + 空跳过,单指标失败不拖批;水位线 kvstate['wind_edb_state']。"""
```
接入:`alt.pull_all` 自动发现(source='wind_edb' 出现在 ALT_SIGNALS)→ `sync_alt_events`(|z|≥2→kg_events)→ thesis_signals 支柱校正,**零下游改动**。

---

## 附录 F — 精确接线 diff 清单

| 文件 | 改动(加性) |
|---|---|
| `ingestion/base.py` | `Doc.doc_id` 字段 + `id` property 优先返回它 |
| `ontology/__init__.py` | 导出 `research_docs` |
| `providers/gangtise/client.py` | 6 URL 常量 + `pages()` 分页助手 |
| `kg/expert.py` | `ALT_SOURCES += ("gangtise",)`;`_SYSTEM_RESEARCH` 变体;`process_document` SELECT 补 `doc_type, company_id`,`(source,doc_type)∈EXPERT_DOC_TYPES` 时用研报 prompt + entity fallback `d.company_id` |
| `kg/extract.py` | `build_kg` ORDER CASE 注入 `research_docs.kg_priority_case()` |
| `models/router.py` | `TaskClass.AUDIT` + `POLICIES[AUDIT]=RoutePolicy(STRONG,TOKEN,"normal")` |
| `orchestration/glm_worker.py` | `_gangtise` 序换 `cn_priority_order()`;`_pull_fresh` 增 4 站;`run_once` 增 `_due("research_audit",24h)` |
| `config.py` | 附录 B 的 6 个 gangtise 旋钮 |
| `api/app.py` | `GET /api/ops/research-crawl` |
| `cli.py` | `research_app`(crawl/backfill/audit/status) |
| `providers/aifinmarket.py` | 导出/暴露 `_mcp_call` 供 wind_edb 复用;`pull_theme_news` 保持不变(P3 接线其调用) |

---

## 附录 G — 各阶段测试清单(离线打桩 + seeded_db)

- **test_research_docs.py**(纯离线):doc_type 唯一;catalyst/pillar 词表 ⊆ 合法集;`kg_priority_case()` 含全类型;`Doc(doc_id="x").id=="x"`;**未设 doc_id 时 `Doc(...).id` 与旧实现逐字节相同**(捕获一个 golden 值断言)。
- **test_gangtise_insight.py**(monkeypatch `client.post`/`client.pages`,fixture 用真机捕获形状):13 位 ms 解析;`_company_for_security` 反解(600519.SH→cid);brief→full 同 doc_id 原地升级(seeded_db);`parse_broker_ratings` 多报同日聚合 + pt_mean/high/low + CN 5 档映射;clue targets 去重。
- **test_wind_edb.py**(monkeypatch `_mcp_call`):序列解析;单位 sanity 拦截离群;日期单调;双跑幂等(upsert)。+ test_altdata 不变式扩展(新 spec theme/pillar 合法)。
- **test_research_routing.py**(seeded_db):`ALT_SOURCES` 含 gangtise;`(gangtise, broker_report)` 走 `_SYSTEM_RESEARCH`(capture system 串);build_kg 队列顺序(插 gangtise+news doc 验优先级);kept 洞见 → semantic_facts 可见。
- **test_gangtise_planner.py**(monkeypatch coverage_all/seed_company_ids):三段排序;月窗最新在前 + 连续空窗 exhausted + reset 续走;毒单元跳过。
- **test_research_audit.py**(seeded_db + monkeypatch complete_json):integrity 计数确定性;失败裁决 → `kg_extracted_at=NULL` + `meta.audit`;`resolve(TaskClass.AUDIT)[0].billing=="token"`(硬锁独立性)。

---

## 附录 H — Gangtise 端点请求/响应字段速查(真机对照基准)

| 端点 | 请求关键键 | 响应行关键字段 | 反解/落库 |
|---|---|---|---|
| broker-report/getList | `keyword,startDate,endDate,securities,institutions,categoryList,ratingList,ratingChangeList,from,size` | `reportId,title,brief,publisher.{brokerName,author},publishTime(13ms)/reportDate,securityList[].{securityCode,securityName},industryList[],category,llmTagList,rating,ratingChange,targetPrice` | doc_id=gangtise:report:{reportId};rating→upsert_rating |
| summary/v2/getList | `keyword,startDate,endDate,securities,institutions,categoryList,marketList,participantRoleList,sourceTypeList,from,size` | `summaryId,title,translatedTitle,brief,essence[].content,publishTime/summaryTime,securityList[],institutionList[],categoryList,marketList,participantRoleList,guest,sourceName` | doc_id=gangtise:summary:{summaryId};expert 分流看 participantRoleList |
| management_discuss/from-{earningsCall,announcement} | `{securityCode,reportDate(yyyy-MM-dd),discussionDimension:'all'}` | Markdown `content` + `reportDate` | doc_id=gangtise:mgmt:{code}:{reportDate}:{ec\|ann} |
| agent/security_clue | `{queryMode,securities|[all],pageFrom,pageSize≤500,startTime,endTime(ms),source:[…]}` | 行 `{securityCode/securityList,source(研报/电话会议纪要/公告/观点),title,time}` | 不落库 → targets |

> **真机纪律**:上表字段名以 Gangtise 文档为准;实 API 短字段常与文档不符(前科:资产负债表 companyType/currency 位错)。RD-P5 真机首跑时 dump 一页原始 JSON,逐字段核对后**修正离线 fixture 与 `_*_MAP`**,再放行常驻抓取。

---

## 10. 与既有轨道的关系

- **THESIS_ONTOLOGY_PLAN(TO 轨,已交付)**:本计划是它的**数据供给侧**——争论/验证点/evidence_link
  机器已就位,RD 轨把最高价值的 CN 卖方/纪要/专家语料持续灌进 semantic_facts,让争论天平有料可称。
- **WM 轨(微信挖掘)**:互补——WM 管噪声源的 SNR 闸,RD 管策展付费源的直通道;共用 expert/评级/
  thesis 下游。
- **GT 轨(Gangtise 结构化)**:RD 复用其 client 认证/分页/代码解析,扩 open-insight 与 open-ai 新域。
