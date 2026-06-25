# Ontology 语义数据库 + 每日自动化 · 执行计划

> 目标：围绕从零构建的 Ontology，建立一个**带时间戳、可回测、服务于 LLM 推理**的语义数据库；包含 Ontology 标准结构化数据（fundamentals/estimates/prices）所**未覆盖的语义级数据**（情绪/立场/前瞻主张/期望-兑现/叙事/传闻/管理层口径……）；并搭建**每日自动从全部可及来源抓取与更新**的系统。
> 本文件为**执行计划**，非代码改动。落地需另起实现，严格遵循本计划的基线约束。
> 关联：与 `CODE_REVIEW.md`（全仓审核）并列；本计划在落地时须一并修掉 `CODE_REVIEW.md` 附录 A 指出的若干既有缺陷（见 §0）。

---

## 评审裁定（DISPOSITION · 本次会话）

> 本计划（GLM-5.2 提案）已从第一性原理与**已落地的"复用既有三表"设计**逐条对照评审，结论如下；**计划正文保留不改**，仅记录裁定：
>
> - **否决（REJECTED）·核心提案**：新建独立 `semantic_claims` 表 + `signal_events` 桥接视图（§2）。判定为**重叠存储 / 技术债**——已落地方案改为**加性复用既有三张双时态表**（`kg_events` 增 `theme/segment/narrative/time_orientation`、`kg_edges` 增 `causally_linked` EdgeType、`expert_insights` 增 `as_of/theme/segment/time_orientation`），并由单一 SQL 视图 **`semantic_facts`**（`storage/schema.sql`，`kg_events` 非 expert 行 UNION 保留的 `expert_insights`，insight 臂 LEFT JOIN `kg_events` 回填 resolution）统一检索/回测/agents 入口，无需平行表。
> - **采纳（ADOPTED）·唯一净新增能力**：**前瞻主张兑现生命周期**（§1.2 `Resolution` / §5 `claim_resolve`）。落地为**精简加性**版：`kg_events` 增 `resolution / resolved_at / realizes_event_id` 三列；`src/xar/kg/resolve_claims.py:resolve_forward_claims()` 闭合 expectation→realization（方向性 `forward_looking` 催化剂 → 后续同公司 backward 实现事件命中即 hit/miss，否则 stale 可复检；仅改 forward 行；窗口按 `COALESCE(event_date, observed_at)` 定日）；经 `semantic_facts.resolution` 暴露，CLI `xar resolve-claims`。
> - **采纳（ADOPTED）·回测 PIT**：`COALESCE(event_date, observed_at)` 取日 + `GREATEST(as_of, observed_at)` 严格入场（§0.1 / §6），已落地于 `backtest/catalyst_returns.py`（驱动自 `semantic_facts`，按 `category/polarity/kind/time_orientation` 分层，本地 `prices` 表优先）。
> - **已覆盖（既有/同期落地）**：Finnhub/FMP 新闻接入（`providers/finnhub.pull_news` / `pull_general_news` / `providers/fmp.pull_news` → `documents`，§4）；每日自动化与 Dagster 进 Docker（`orchestration/daily.py:run_daily` + `orchestration/definitions.py` + 新增 `ingest_runs` 表 + `xar daily`，Dagster UI 见 `:3001`，§5）。
> - **暂缓（DEFERRED）**：`source_reliability` 基线表、Reddit/X 专家更频调度、`kg_event_returns` 物化表等——非阻塞，按需再议。

---

## 决策基线（已与项目方确认）

| 决策点 | 选定 | 备注 |
|---|---|---|
| 语义数据落点 | **新建独立 `semantic_claims` 表** | 与 `kg_events` 平行；经 `realizes_event_id` + `signal_events` 视图桥接，避免检索/回测割裂 |
| 主张粒度 | **多主张 / 文档** | 一篇新闻/帖子可产出 N 条 claim；LLM 成本×N，但语义完整、回测样本更多 |
| 新闻/社交源 | **全部 + 追加 Reddit + X 专家** | TwitterAPI + Finnhub 新闻(新代码) + 微信 + aifinmarket + Reddit + X 专家 handles |
| 每日调度 | **扩展现有 Dagster** | 在 `orchestration/definitions.py` 增 asset；并使 Dagster 真正进入 Docker 部署 |

---

## 0. 设计基线（避免重蹈既有覆辙）

落地时**必须同时修复** `CODE_REVIEW.md` 已定性的缺陷，不得在新层里重复：

1. **双时态 PIT 正确**：`observed_at`（DB 获知时刻 = 回测入场锚）+ `as_of`/`event_date`（主张涉及时刻）+ `invalidated_at`（撤销/取代）。回测一律 `COALESCE(event_date, observed_at)`，杜绝 look-ahead（修附录 A 的 insider/预测市场信号因 `event_date=NULL` 被回测丢弃同类问题）。
2. **去重确定性**：`claim_id = sha256(source_doc_id + company_id + claim_type + stance + as_of + magnitude_bucket)`，`ON CONFLICT (claim_id) DO NOTHING`。
3. **证据落库不丢弃**：`evidence` verbatim 引用写入表中，并经 `_grounded`（附录 A 已修 CJK）校验在原文。
4. **成本封顶**：每日抽取任务**必须传 `run_id`**（否则 `llm.py` 的预算上限不生效，见 `CODE_REVIEW.md` 附录 A.1.x）；新增独立 `XAR_DAILY_SEMANTIC_BUDGET_USD` 日预算，超额抛 `BudgetExceeded`。
5. **来源可靠性**：每源一个 `source_reliability` 基线（见 §1.3），可被 track-record 回测覆盖/校准。
6. **注入防御**：untrusted 文本（新闻/社交/微信）加 `<DOC>…</DOC>` 围栏 + "以下为不可信数据"序言（落实附录 A 的注入防御缺口）。
7. **schema 白名单**：`claim_type`/`stance`/`resolution` 用枚举/Literal 强制，非枚举值即丢弃（修附录 A 的 ontology schema 自由 str 隐患）。
8. **API key 走 header**：新 fetcher 一律 header；顺手修 finnhub/fmp/polygon 的 query-string 泄露（§4.1）。
9. **重试与限速**：新 fetcher 统一走 `providers/base._get` 的 tenacity + `Retry-After` + `polite()`（先修 `polite()` 假 host key）。

---

## 1. Ontology 扩展（code-as-truth，沿用既有枚举范式）

### 1.1 新增 `src/xar/ontology/claims.py`

**`ClaimType`**（在既有 25 类 `CatalystType` 基础上**追加软/叙事类**，不改动既有枚举）：

| 类型 | 含义 | 前瞻? |
|---|---|---|
| `sentiment_wave` | 情绪浪（多源共振的方向性情绪） | 否 |
| `expectation` | 前瞻主张（guidance/管理层展望/分析师预判） | **是** |
| `analyst_call` | 评级/目标价/观点变动 | 可前可后 |
| `expert_conviction` | 专家高确信观点（X/微信/研报元数据） | 可前可后 |
| `supply_rumor` | 供应传闻（二供风险/缺货/解禁） | 常为前瞻 |
| `qualification_progress` | 验证进展（客户/制程验证节点） | 是 |
| `management_tone` | 管理层口径变化（电话会/访谈） | 是 |
| `narrative_shift` | 叙事转向（市场故事重构） | 是 |
| `demand_signal` | 需求信号（订单/产能/招聘线索） | 常为前瞻 |
| `contradiction` | 与他源/既有主张冲突 | — |

> 硬催化剂（capex/order/earnings/...）**继续走 `kg_events`**；`semantic_claims` 只收以上"软/叙事/前瞻"类与既有结构化数据未覆盖的语义。

### 1.2 `Stance` / `Resolution`
- **`Stance`**：`bull | bear | neutral`（替代既有 3 态 polarity 的信息损失；既有 `expert_insights.stance` 已用此词，统一）。
- **`Resolution`**：`pending | hit | miss | withdrawn | stale`（前瞻主张生命周期）。

### 1.3 `source_reliability` 基线表（code-as-truth，可被回测覆盖）
`8-K/EDGAR=0.90 · cninfo 法定披露=0.90 · Finnhub 新闻=0.70 · aifinmarket=0.70 · X 专家(精选 handle)=0.60 · X 普通搜索=0.45 · Reddit=0.40 · 匿名新闻=0.30`。

---

## 2. 存储层（新增 `semantic_claims` + 极小既有改动）

### 2.1 新表 `semantic_claims`（追加到 `storage/schema.sql`）

```
id              BIGSERIAL PK
claim_id        TEXT UNIQUE NOT NULL          -- sha256(source_doc_id+company_id+claim_type+stance+as_of+magnitude_bucket)
-- Ontology 锚
company_id      TEXT REFERENCES companies(id)
node_id         BIGINT REFERENCES kg_nodes(id)        -- 非公司目标(tech-route/commodity/person)
segment_id      TEXT
tech_route_tag  TEXT
topics          TEXT[]                                  -- 自由语义标签(超出 claim_type 的横切主题)
-- WHAT
claim_type      TEXT NOT NULL                           -- ClaimType 枚举
sub_type        TEXT                                    -- 更细粒度
summary         TEXT NOT NULL                           -- 主张句
-- 方向与强度
stance          TEXT                                    -- bull|bear|neutral
sentiment_value REAL                                    -- [-1,1] 连续
conviction      REAL                                    -- [0,1]
-- 数值量级(替代 kg_events 的 free-text magnitude)
magnitude_value DOUBLE PRECISION
magnitude_unit  TEXT                                    -- USD|pct|x|count|null
surprise_pct    REAL                                    -- vs 共识(若适用)
-- 时间结构(双时态 + 前瞻)
observed_at     TIMESTAMPTZ NOT NULL DEFAULT now()      -- DB 获知时刻(回测 PIT 锚)
as_of           DATE                                    -- 主张涉及时刻
event_date      DATE                                    -- 公共信息日(回测入场)
is_forward_looking BOOL NOT NULL DEFAULT false
claim_horizon_days INT                                  -- 前瞻投射天数
expected_by     DATE                                    -- 预期兑现日
invalidated_at  TIMESTAMPTZ                             -- 撤销/取代
supersedes_claim_id TEXT                                -- 修订链
-- 期望-兑现桥
realizes_event_id BIGINT REFERENCES kg_events(id)       -- 前瞻主张 → 后续实现的 kg_event
resolution      TEXT DEFAULT 'pending'                  -- pending|hit|miss|withdrawn|stale
resolved_at     TIMESTAMPTZ
-- 溯源与信任
source_doc_id   TEXT REFERENCES documents(id)
source_type     TEXT                                    -- twitter|wechat|finnhub_news|aifinmarket|reddit|news|x_expert
source_reliability REAL                                  -- 0..1
author          TEXT                                    -- handle/outlet
license_tag     TEXT                                    -- green|grey|red(继承自文档)
evidence        TEXT                                    -- verbatim 引用(落库不丢弃)
-- 语义检索
embedding       vector({EMBED_DIM})
-- 抽取溯源
extraction_run_id TEXT
extracted_at    TIMESTAMPTZ DEFAULT now()
attrs           JSONB
```

**索引**：`(company_id, event_date)`、`(claim_type, stance)`、`(is_forward_looking, resolution)`、`embedding` IVFFlat（行数达阈后再建，沿用 `db.ensure_vector_index`）、`topics` GIN、`(source_doc_id)`。

### 2.2 既有表的最小改动（增量、可回填）
- `documents` / `social_posts` 加 `observed_at TIMESTAMPTZ DEFAULT now()` + `invalidated_at`（修附录 A "未双时态" 项）。
- `social_posts` 加 `entity_sentiments JSONB`（一帖提及多公司按实体分摊情感）。
- **不动 `kg_events` 结构**（保持既有消费者稳定），仅通过视图桥接。

### 2.3 桥接视图 `signal_events`
`UNION ALL` `kg_events`（投影成统一列：`source='kg_event'`）与 `semantic_claims`（`source='semantic'`）→ **回测 / 检索 / agents 的单一入口**，避免双表割裂。投影统一列：`id, source, company_id, event_date, observed_at, stance, sentiment_value, claim_type, magnitude_value, magnitude_unit, is_forward_looking, resolution, source_reliability, topics, summary, evidence`。

---

## 3. 语义抽取器（`src/xar/kg/semantic.py`，新增）

- 复用 `expert.py` 的 LLM→结构化模式，但**claim 级、多主张 / 文档**（一篇新闻产出 N 个 `SemanticClaim`）。
- Pydantic schema 增 `SemanticClaim` / `SemanticResult`（`ontology/schema.py`），字段 `description=` 即 JSON-schema 进 prompt（沿用既有范式，`complete_json`）。
- **Prompt 注入防御**：untrusted 文本加 `<DOC>…</DOC>` 围栏 + 序言。
- 落库前：`evidence` 经 `_grounded` 校验 → 写 `semantic_claims` + 向量化（复用 `models/embeddings`）。
- **枚举白名单校验**：非 `ClaimType`/`Stance` 值即丢弃。
- 多主题感知：focus/词汇复用 `extract._focus_for` + `metric_packs.kpi_labels_for_company`。
- **成本**：`complete_json(..., run_id=run_id)`；日预算 `XAR_DAILY_SEMANTIC_BUDGET_USD`（默认 $10）。

---

## 4. 新闻 / 社交源补齐与接入

| 源 | 动作 | 落点 | 现状 |
|---|---|---|---|
| **Finnhub 新闻（新代码）** | `providers/finnhub.py:pull_news(company_id, days=2)` → `/company-news?symbol&from&to` → `documents(source=finnhub_news)` | 全 US 名每日 | **完全无代码（latent）** |
| **Reddit（已接线未排程）** | `reddit.pull_basket` 接入每日 | `social_posts` + 镜像 `documents` | 已接线 |
| **X 专家（已接线）** | `twitter.pull_experts(theme)` 8 主题每日（5 chain + 3 cycle） | `social_posts` + `documents` | 已接线 |
| **Twitter（已接线）** | `twitter.pull` 主题扫 | 同上 | 已接线 |
| **aifinmarket（已接线）** | `aifinmarket.pull_theme_news` | `documents` | 已接线 |
| **WeChat（已每日跑）** | 保留；接入 semantic 抽取 | `documents` | 已每日 |

**前置修复**（落实 `CODE_REVIEW.md`）：
- 修 `polite()` 假 host key（cninfo/jobs 等）再加新 fetcher。
- 新 fetcher 统一走 `providers/base._get`（tenacity 重试 + `Retry-After`），不裸 `httpx.get`。
- API key 一律 header（修 finnhub/fmp/polygon 的 query-string 泄露）。

---

## 5. 每日自动化（扩展现有 Dagster `orchestration/definitions.py`）

既有资产链 `filings → chunks → knowledge_graph` 保留，新增并接：

```
news_social (新)            ← Twitter(主题+专家) + Reddit + Finnhub新闻 + aifinmarket主题资讯 + WeChat
   ↓
semantic_extract (新)       ← kg/semantic.py 多主张抽取 → semantic_claims (+embedding)
   ↓ (与 knowledge_graph 汇合)
claim_resolve (新)          ← 前瞻主张 vs 新实现的 kg_events 结算 → resolution(hit/miss/stale)
   ↓
semantic_returns (新)       ← 物化 forward returns 到 kg_event_returns
```

- **调度**：保留 `0 6 * * *`；社交源可另设更频 schedule（如每 4h）。
- **并行化**：既有 fan-out 串行（附录 A）；本期可先串行，预留 `concurrent.futures` 并行钩子。
- **Dagster 进 Docker**（当前未启用，附录 A）：加 `dagster` daemon 服务到 `docker-compose.yml`，使"每日自动"在生产部署里真的发生。
- **幂等可续**：所有写 `ON CONFLICT DO NOTHING`；失败可重跑。

---

## 6. 回测与 LLM 推理消费

- **新 `backtest/claim_returns.py`**：消费 `signal_events` 视图（kg_events ∪ semantic_claims），PIT 正确（`COALESCE(event_date, observed_at)`），按 `claim_type/stance/is_forward_looking/source_reliability/topics` 分层；输出 `mean/std/n/t`。**修复** `CODE_REVIEW.md` §三 的回测四重偏差（基价取事件日、用 observed_at 防 look-ahead、声明幸存者偏差、加 n/std）。
- **物化表 `kg_event_returns(event_id, source, horizon, fwd_pct, base_date, base_close)`**：持久化远期收益，LLM agents 直接取用，避免每次重算；**复用本地 `prices` 表**（修附录 A 的"回测每次 500 次 yfinance 下载"性能问题）。
- **前瞻主张命中率**：`claim_resolve` 产出后，回测"前瞻主张是否兑现"维度——现有回测完全缺失的能力。
- **检索层**（`retrieval/vector.py`）：增 `search_claims(company_id, query, k)` 走 `semantic_claims.embedding`；agents（`nodes.py` 分析师）把高相关 claim 作为证据喂入 prompt。
- **permission 过滤**（修附录 A）：检索默认 `license_tag <> 'red'`。

---

## 7. 回填与迁移（零停机、增量）

- 新表/列均为 `IF NOT EXISTS` 增量，不动既有数据。
- **回填**：把现有 `expert_insights(kept=true)` + `kg_events(license_tag='expert')` 投影成 `semantic_claims`（`claim_type='expert_conviction'`），保证历史连续。
- `signal_events` 视图即刻让回测/agents 同时看到旧 kg_events 与新 claim。

---

## 8. 测试（钉死护城河，落实 `CODE_REVIEW.md` 附录 A "零测试" 教训）

- `semantic_claims` 去重（同源同主张不重复入库）。
- 前瞻主张结算（`expected_by` 到期 → `hit/miss/stale` 正确转换）。
- **PIT 正确性**（用 `observed_at` 而非未来 `event_date` 入场）。
- 抽取 schema 白名单（非枚举 `claim_type` 丢弃）。
- `evidence` grounded（CJK 路径，复用已修的 `_grounded`）。
- 成本上限（`run_id` 绑定，超 `XAR_DAILY_SEMANTIC_BUDGET_USD` 抛 `BudgetExceeded`）。
- 注入防御（含 `<DOC>` 围栏的对抗样本不越权）。

---

## 9. 待项目方细定的两个细节（不阻塞，可在实现时定）

1. **每日预算基线**：`XAR_DAILY_SEMANTIC_BUDGET_USD` 默认 $10（约数千条 claim），可按实际公司数/源数调。
2. **前瞻主张结算判定**：`hit/miss` 的阈值规则（guidance 实现值落在主张区间内 = hit）。建议先粗粒度（direction 一致即 hit），后续接 `estimates` 精细化。

---

## 10. 交付顺序（建议，每步可独立验证）

1. **Ontology + schema**（`claims.py` + `semantic_claims` 表 + `signal_events` 视图 + 回填）—— 立刻让回测/agents 看到"语义层"。
2. **抽取器**（`semantic.py`，先对已有 `documents` 跑，验证多主张质量与成本）。
3. **源补齐**（Finnhub 新闻新代码 + Reddit / X 专家接入 + §0 前置修复）。
4. **Dagster 扩展 + 进 Docker**（真正每日自动）。
5. **回测 + 物化收益 + agents 消费**（闭环"语义 → 回测 → 推理"）。

---

## 非目标（明确不做，保持范围克制）

- 不重构既有 `kg_events` / `kg_edges`（仅视图桥接 + 极小列增）。
- 不做多租户/RBAC（自研自用，见 `CODE_REVIEW.md` 附录 A.2 的范围决策）。
- 不引入新中间件（消息队列/向量库）；复用 Postgres+pgvector + Dagster 既有栈。
- 不在本计划内做前端展示改造（后续按需增 `/api/semantic/*` 与面板）。

> 本计划仅为设计与约束文档；按本计划落地时，须同步遵守 `CODE_REVIEW.md`（全仓审核）与其各附录的裁定。
