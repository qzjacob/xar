# THESIS_ONTOLOGY_PLAN — 对冲基金工业级投研 Thesis Ontology 升级开发文档

> 状态:**设计定稿,未实施**。本文档是唯一交付物;实施须按阶段(P0→P5)推进,每阶段 pytest+ruff 绿。
> 范式示例(贯穿全文):ServiceNow(registry id `now`)——核心投资分歧「AI 对其生意模式是**颠覆还是赋能**」;
> 验证点 RPO/cRPO/ARR/NRR;资讯语义「企业客户**采用 vs 取消**其产品」回归到分歧的证实/证伪。

---

## 1. 现状与缺口(已实证勘探,含代码锚点)

XAR 已有一套 thesis 机器,升级是**在已验证接缝上做增量**,不是重建:

| 已有能力 | 位置 | 现状 |
|---|---|---|
| 类型化论点对象 | `ontology/thesis.py:110` `CompanyThesis` | pillars(claim_zh 可证伪主张、weight、score、evidence 锚、watch_metrics、watch_event_types、falsifier_zh)已类型化 |
| 论点纪律门 | `ontology/thesis.py:131` `validate_thesis` | 证据 id 必须存在于 dossier;证据<5 条 conviction≤3;宁缺毋滥 |
| 生成管线 | `research/thesis.py:38` `dossier` / `:195` `build` | 全事实稳定 id 锚(`[event:261]` `[fundamental:cid:revenue]`…),2-attempt validate 重试,版本化入库 `company_thesis` + `thesis_evidence` |
| 零 LLM 健康度 | `research/thesis.py:279` `health()` | **按 `watch_event_types` 桶 × 公司情绪极性计数** → confirming/challenging/quiet |
| 高频信号校正 | `research/thesis_signals.py` | alt z-score→pillar kind(`health_v2`);`challenged_companies()` → glm_worker 自动重写闭环(`orchestration/glm_worker.py:283` `_alt_correction`) |
| KPI 规范层 | `ontology/metric_packs.py` | ~200 个 MetricSpec;SOFTWARE_PACK 已含 **arr/nrr/grr/rpo/crpo/billings/rule_of_40/magic_number**(rpo 带 FIBO 锚 `us-gaap:RevenueRemainingPerformanceObligation`) |
| KPI 时序存储 | `storage/schema.sql:186` `fundamentals` | `(company_id, metric, period, period_end, freq, value, unit, source)` UNIQUE(company_id,metric,period,source) |
| 证据流水线 | `kg/extract.py` `kg/expert.py` `semantic_facts` 视图 | 事件带 polarity(positive/negative/neutral,**公司层面情绪**)+ time_orientation;`_grounded` 逐字引文反幻觉门 |
| 期望→兑现闭环 | `kg/resolve_claims.py` | forward 断言 × 后续 realizer 事件 → hit/miss/stale(现有的机器可判证实/证伪先例) |
| 争论(现状) | `agents/debate.py` | 深度报告 DAG 内 2 轮 bull/bear LLM 辩论,**输出自由文本进报告,不落库、无类型** |

### 四个缺口(用户需求逐条对应)

1. **核心分歧是散文**:`bull_case_zh / bear_case_zh / variant_perception_zh`(thesis.py:118-121)是自由文本。没有「AI 颠覆 vs 赋能」这样的一等类型化争论对象,没有争论↔支柱↔验证点的结构。
2. **证实/证伪只有一根杠杆**:`health()` 的匹配 = 事件类型命中 `watch_event_types` 桶 + 公司情绪极性求和。**没有「这条资讯支持/反驳这条具体主张」的语义**——「大客户取消订阅」对公司是 negative,但它到底打在哪个分歧的哪一边,今天无从表达。
3. **KPI↔论点无阈值语义**:`watch_metrics` 是裸字符串列表。「cRPO YoY ≥20% 证多 / ≤12.5% 证空」这种机器可判的验证点不存在。
4. **无衍生追踪指标**:fundamentals 只有原始序列。YoY、**增速二阶导**(加速/减速)、cRPO/营收比、NRR 趋势等衍生指标无处定义、无处计算、无处引用。

---

## 2. 目标数据流(ServiceNow 走查)

```
ThesisDebate「AI 颠覆 vs 赋能」(key=ai_disrupt_vs_empower, 策展种子)
 ├─ bull_zh: AI 是其平台的增强器——Now Assist 提价、席位扩张、cRPO 加速…(steelman)
 ├─ bear_zh: Agent 原生栈绕开 ITSM 座位模型——席位压缩、NRR 衰减、新签放缓…(steelman)
 ├─ pillar_keys: [ai_monetization, seat_model_moat]
 └─ verification_points:
     ├─ VP crpo_growth_floor: metric=crpo_yoy(衍生指标), higher_is_bull,
     │    bull_threshold=0.20, bear_threshold=0.125, cadence=quarterly
     │    → 数值规则道(零 LLM):季报落地 → fundamentals 算出 crpo_yoy → 与双阈值比对
     │      → thesis_fact_links(origin='rule', verdict=confirms_bull/confirms_bear/neutral)
     └─ VP enterprise_adoption: event_types=[contract_win, order, partnership, tech_substitution]
          question_zh=「企业客户是在扩大采用还是取消订阅?」
          → 语义道(LLM):新 semantic_facts(新闻/研报/微信/expert insight)
            → THESIS_LINK 任务:相对主张分类(与公司利好利空解耦)
            → 「某财富500强弃用 Now Assist 转向自研 Agent」= 公司 negative + confirms_bear
            → thesis_fact_links(origin='llm', strength, rationale_zh)

health_v3: 两道合并 → debate.lean_now ∈ [-1,1](证据天平)
 → lean 与作者立场反号且 |lean|≥0.3 → status=flipped
 → challenged_companies_v2 拾起 → glm_worker 自动重写论点(既有闭环,零新进程)
```

---

## 3. 核心设计裁决(经独立 Plan agent 对抗审核定稿)

1. **VP 阈值 = 双阈值灰区对,不做 op 枚举、不做 DSL**
   `direction(higher_is_bull|lower_is_bull) + bull_threshold + bear_threshold`(两阈之间=neutral 灰区)。
   理由:单阈值表达不了真实对冲基金语义(「≥20% 证多、≤12.5% 证空、之间观望」);op 枚举(yoy_above/trend_up…)会与衍生指标层重复编码变换——趋势/加速度语义全部下沉为指标 key(`*_trend`/`*_accel`),数值检查器收敛到 ~30 行的水平比较。DSL 字符串不可审计、不可提示,否决。

2. **`thesis_fact_links` 独立新表,不复用 `thesis_evidence`**
   `thesis_evidence`(schema.sql:582)是 **build 时一次写死的作者态溯源**(在 `build()` 事务内落库,喂 `_quality` 覆盖度数学);fact-links 是**监控态逐日增量裁决**(verdict/strength/origin),生命周期、唯一键、语义都不同,混用会污染质量指标。
   粒度按 `thesis_id`:**表本身就是游标**——待分类事实 = `semantic_facts WHERE as_of > thesis.as_of LEFT JOIN links IS NULL`;rebuild 推进 as_of,天然无重分类风暴,无需游标状态表。

3. **衍生指标写回 `fundamentals`(`source='derived'`)——全计划最高杠杆的复用**
   dossier 财务节自动带上(免费获得 `[fundamental:cid:crpo_yoy]` 引用锚,LLM 可直接引用为证据);UNIQUE 键天然幂等(重算=覆盖);前端 KPI 表免费显示。
   指标注册表独立(**不**注入 `SPEC_BY_KEY`/`ALIAS_TO_KEY`——衍生指标严禁成为 LLM 抽取目标,只能计算);计算读取 `WHERE source<>'derived'` 防「衍生的衍生」自反馈。策展 ~25 个(有 VP/watch 需求才建,拒绝 200 specs × 5 变换的笛卡尔积)。

4. **health_v3 = 规则优先级合并,不加权求和**(防双计)
   同一条 kg_event 会同时出现在事件桶道(`health()` 既有逻辑)与 LLM 链接道。修复:pillar 层 LLM 裁决只做**升降级**(quiet/mixed→challenging,或给 confirming 加注),不参与计分;debate 层**只用自己的两条道**(LLM 链接 + VP 数值)——争论在事件桶道根本不存在,天然无双计。无权重可调=无权重可错。

5. **主题争论 = code-as-truth 元组,不建 theme_thesis 表**
   8 个主题、内容策展,走 `macro_links.py` 模式(纯代码注册表 + 测试守卫不变式)。为 8 行数据复制整套 build/validate/version 机器是过度工程。主题争论同时作为**种子被成员公司 prompt 继承**——这是它真正挣饭吃的地方。

6. **调用量实证可负担**(GLM 订阅池,$0)
   有 thesis 的名字仅数十家(coverage-ranked);每家新鲜事实 0-5 条/天(财报日 ~20);批 ≤20 条/调用 → **~20-40 调用/天,~60-150k tokens/天**,对 build_kg 夜批是噪音量级。上限:`glm_worker_link_companies=15`/周期 + 既有额度治理。

7. **宁缺毋滥延续到争论**:有策展种子(含主题继承)的公司**必须**逐条回应种子 key(缺失即 validate 拒绝);长尾公司允许 `debates=[]`——不许为薄覆盖名字硬编假分歧。

8. **已确认决策(用户)**:策展种子覆盖 = **全 8 主题旗舰 ~15-20 家 + 8 条主题级争论**;长尾 LLM 自行生成。

---

## 4. 本体层设计(P0 交付物,`ontology/` 三个文件)

### 4.1 `ontology/thesis.py` 扩展(向后兼容,追加在 WatchItem L103 之后)

```python
DEBATE_VERDICTS = ("confirms_bull", "confirms_bear", "neutral")   # debate 目标裁决词表
PILLAR_VERDICTS = ("confirms", "falsifies", "neutral")            # pillar 目标裁决词表

class VerificationPoint(BaseModel):
    key: str                       # stable slug, e.g. "crpo_growth_floor"
    question_zh: str               # "企业客户是在扩大采用还是取消订阅?"
    metric: str = ""               # canonical KPI 或衍生指标 key;"" = 纯事件型 VP
    event_types: list[str] = []    # ⊆ CATALYST_TYPES;事件型 VP 的催化剂桶
    bull_reading_zh: str           # 数据怎么读算多头得分(必须含具体数字)
    bear_reading_zh: str
    direction: str = "higher_is_bull"        # higher_is_bull | lower_is_bull
    bull_threshold: float | None = None      # 达到 → confirms_bull(机器可判)
    bear_threshold: float | None = None      # 跌破 → confirms_bear;两阈之间 = neutral 灰区
    cadence: str = "quarterly"               # quarterly | monthly | event(陈旧度标记用)

class ThesisDebate(BaseModel):
    key: str                       # stable slug, e.g. "ai_disrupt_vs_empower"
    question_zh: str               # "AI 颠覆还是赋能其商业模式?"
    bull_zh: str                   # 多方最强因果叙事(steelman,2-3 句含数字)
    bear_zh: str                   # 空方最强因果叙事
    weight: float = 0.5            # ge=0 le=1,对论点的重要度
    lean: float = 0.0              # ge=-1 le=1,作者态证据天平:-1 全 bear … +1 全 bull
    pillar_keys: list[str] = []    # 该争论压在哪些支柱上(pillar.key)
    verification_points: list[VerificationPoint]          # 1..4
    evidence: list[ThesisEvidence] = []                   # 可选,入库 slot='debate:<key>'

class CompanyThesis(BaseModel):
    ...(现有字段全部不动)...
    debates: list[ThesisDebate] = Field(default_factory=list,
        description="0-3 个核心争论;必须是真分歧(两边都有聪明钱),没有就留空")
```

**向后兼容**:`debates` 默认 `[]`,存量 `company_thesis.content` JSONB 全部照常 parse——零迁移。

**`validate_thesis` 扩展**(新增参数 `known_indicators: set[str] | None`、`required_debate_keys: set[str] | None`):
- ≤3 debates;debate key 唯一;`pillar_keys ⊆ {p.key}`;
- 每 debate 1-4 个 VP;VP 必须有 `metric` 或 `event_types` 至少其一;
- `metric ∈ known_kpis ∪ known_indicators`;`event_types ⊆ CATALYST_TYPES`;
- 阈值排序 sanity:`higher_is_bull → bull_threshold ≥ bear_threshold`(反向则倒过来);
- `required_debate_keys` 缺失即违规(种子公司必须回应种子,key 保持不变)。

### 4.2 新 `ontology/indicators.py` — 衍生指标注册表(altdata.py 风格)

```python
@dataclass(frozen=True)
class IndicatorSpec:
    key: str                 # fundamentals.metric 取值, e.g. "crpo_yoy"
    label_zh: str
    base_metric: str         # 必须 ∈ metric_packs.SPEC_BY_KEY(代码即真相测试守卫)
    transform: str           # yoy | qoq | yoy_accel | ratio_to | slope4
    other_metric: str = ""   # ratio_to 的分母
    unit: str = "ratio"
    higher_is_better: bool = True
    min_points: int = 5      # 序列点数不足则跳过
```

策展 `INDICATORS`(~25 个,按需增补):
`revenue_yoy` `revenue_yoy_accel` `arr_yoy` `rpo_yoy` `crpo_yoy` `crpo_yoy_accel`(**增速二阶导**)
`billings_yoy` `nrr_trend`(slope4) `crpo_to_revenue` `customers_yoy` `large_customers_yoy`
`backlog_yoy` `book_to_bill_trend` `gross_margin_trend` `fcf_margin_trend` `capex_yoy`
`inventory_to_revenue` `doi_trend` `eps_yoy` …

导出:`INDICATOR_BY_KEY`;`indicator_keys_for_company(company)`(仅 base_metric ∈ 该公司 `kpis_for_company` 的指标可用)。

### 4.3 新 `ontology/debates.py` — 争论种子注册表(macro_links.py 模式)

```python
@dataclass(frozen=True)
class DebateSeed:            # 公司级策展种子
    company_id: str
    key: str
    question_zh: str
    bull_zh: str
    bear_zh: str
    suggested_metrics: tuple[str, ...] = ()      # 引导 VP 的 KPI/指标 key
    suggested_event_types: tuple[str, ...] = ()

@dataclass(frozen=True)
class ThemeDebate:           # 主题级争论(成员公司继承为种子;主题健康度聚合用)
    theme: str
    key: str
    question_zh: str
    bull_zh: str
    bear_zh: str
    macro_metric_keys: tuple[str, ...] = ()      # macro_links / alt-signal keys
    rationale_zh: str = ""
```

**种子内容(全 8 主题 ~15-20 家旗舰,每主题 2-3 家;实施时定稿)**,示例:
- `now`(ai_software):`ai_disrupt_vs_empower`「AI 颠覆还是赋能座位制 SaaS」;metrics: crpo_yoy/crpo_yoy_accel/nrr/rpo;events: contract_win/order/partnership/tech_substitution
- `300308`(ai_optical,中际旭创):「1.6T 份额延续 vs 价格战/硅光自研侵蚀」;metrics: gross_margin_trend/revenue_yoy_accel
- `nvda`(ai_chip):「capex 超级周期持续 vs 推理需求消化期」;metrics: revenue_yoy_accel/backlog_yoy;events: capex_guidance/order
- `002050`(humanoid,三花智控):「Optimus 定点兑现 vs 预期透支」;events: qualification/order/product_ramp
- `tsla`(humanoid):「Optimus 量产节奏 vs 长期期权定价」
- `9988`(internet,阿里):「AI 云重加速 vs 电商份额持续流失」
- `mcd`(restaurants):「低收入客群流量恢复 vs 价格敏感换挡」
- spacex 链 / retail 各 2-3 家…
+ 8 条 `THEME_DEBATES`,如 ai_software:「Agent 时代按座位收费的 SaaS 模型存废」。

`seeds_for(cid, themes) -> list[DebateSeed]`:公司种子 + 主题争论渲染为继承种子,合并去重。

---

## 5. 存储层(P3 一张新表,schema.sql 底部增量;其余零 DDL)

```sql
-- 论点↔事实链接:监控态裁决(LLM 语义道 + 数值规则道共用)。表本身即游标。
CREATE TABLE IF NOT EXISTS thesis_fact_links (
    id          BIGSERIAL PRIMARY KEY,
    thesis_id   BIGINT NOT NULL REFERENCES company_thesis(id) ON DELETE CASCADE,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    fact_kind   TEXT NOT NULL,   -- event | insight(LLM 道)| fundamental(规则道)
    fact_ref    TEXT NOT NULL,   -- semantic_facts.id;规则道: '<metric>:<period_end>'
    target_kind TEXT NOT NULL,   -- debate | pillar
    target_key  TEXT NOT NULL,   -- debate.key | pillar.key
    verdict     TEXT NOT NULL,   -- debate: confirms_bull|confirms_bear|neutral
                                 -- pillar: confirms|falsifies|neutral
    strength    REAL,            -- 0..1(规则道恒 1.0)
    rationale_zh TEXT,
    origin      TEXT NOT NULL DEFAULT 'llm',   -- llm | rule
    model       TEXT, run_id TEXT,
    as_of       DATE,            -- 事实经济日期(PIT lean 时序回放用)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(thesis_id, fact_kind, fact_ref, target_kind, target_key)
);
CREATE INDEX IF NOT EXISTS idx_tfl_thesis  ON thesis_fact_links(thesis_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tfl_company ON thesis_fact_links(company_id, as_of DESC);
```

- debate evidence(作者态)写入**现有** `thesis_evidence`,slot=`debate:<key>` —— schema.sql:585 的 slot 注释早已预留非 pillar 槽位,**无 DDL 变更**。
- 衍生指标落**现有** `fundamentals`(source='derived', meta={indicator, transform, inputs, computed_at})。

---

## 6. 分阶段实施(每阶段独立绿:ruff + pytest 离线)

### P0 — 本体层(零 DB、零 LLM)
§4 三个文件 + 测试 `tests/test_thesis_ontology.py`:
- 旧 thesis JSON(无 debates)round-trip → `debates == []`;
- 各违规(假 VP metric / 假 event_type / 假 pillar_key / 阈值倒挂 / >3 debates / 种子 key 缺失)逐一产生 violation;ServiceNow 形状的合法论点通过;
- 注册表完整性不变式(test_macro_links.py 风格):`IndicatorSpec.base_metric ∈ SPEC_BY_KEY`、指标 key 不与 canonical KPI 冲突、种子 `company_id ∈ COMPANIES`、`theme ∈ THEMES`、suggested_* ∈ 合法集。

### P1 — 衍生指标计算引擎(零 LLM)
新 `research/indicators.py`:
- `_series(cid, base_metric)`:`WHERE source<>'derived' AND period_end IS NOT NULL ORDER BY period_end`;同 period_end 多源去重按优先级 edgar>gangtise>futu>extracted>其余;优先 `freq='quarter'`;
- **财年安全变换**(不 parse period 字符串):YoY 配对 = period_end 差 350-380 天的行;QoQ = 80-100 天;`yoy_accel = yoy(t) − yoy(t−1q)`;`slope4` = 近 4 点 OLS 斜率/均值;`ratio_to` = 同期两指标比;
- `compute_company(cid)` → `upsert_fundamental(..., source='derived')`(structured.py:24,幂等);`compute_all()`;不足 `min_points` 跳过;单公司异常吞掉不拖批。

接线:`research/thesis.py:dossier`(L143)`kpis |= indicator_keys_for_company(c)` + prompt 追加「## 可用衍生指标」清单(值已在财务节免费出现);`glm_worker.run_once` 加零 LLM 节拍 `_due("indicators", 6*3600)`;CLI `xar indicators compute [company]` / `status`。

测试 `tests/test_indicators.py`(seeded_db):6 季 crpo → crpo_yoy 精确 1e-6;减速序列 → accel<0;双跑行数不变;3 点跳过;derived 输入被排除。

### P2 — 生成管线升级(种子驱动的争论生成)
`research/thesis.py`:
- `_SYSTEM`(L29)加纪律:争论必须真分歧、两边 steelman、VP metric 只能取清单 key、阈值必须具体数字、种子 key 必须逐条覆盖且不变、无实据禁止硬编(宁缺毋滥留空);
- `dossier` 注入「## 核心争论种子(必须逐条回应,key 保持不变)」;返回 dict 增加 `debate_seeds` / `indicators`;
- `build`(L195)把 `known_indicators` / `required_debate_keys` 传入**现有** 2-attempt validate 循环(L212-225,零新控制流);`max_tokens` 6000→8000(schema 变大);debate evidence 并入现有事务写 `thesis_evidence(slot='debate:<key>')`;
- `_quality` 增加 debates 数 + 机器可判 VP 数;`_changed_because` 记录 lean 漂移(`debate ai_disrupt_vs_empower lean +0.2→-0.3`)。

测试 `tests/test_thesis_build.py`(seeded_db + test_pipeline.py 的 mocked complete_json 模式):种子公司返回含种子争论 → content['debates'] 落库 + `debate:` slot 行;`dossier("now")["text"]` 含种子问题;种子公司返回无 debates → 2 次后 rejected;VP 假 metric → violation 文本提及该 metric。

### P3 — 相对主张的证据链接(语义升级核心)+ VP 数值检查器
- §5 新表;`models/router.py` 新任务类 `THESIS_LINK = RoutePolicy(CHEAP_BULK, SUBSCRIPTION, "bulk")`(拷 THESIS/WECHAT_TRIAGE 模式,L74-76);
- 新 `research/evidence_link.py`:
  - LLM schema `FactLink{ref_id, target_kind, target_key, verdict, strength, rationale_zh}` / `FactLinkBatch{links}`;
  - `_pending_facts(cid, thesis)`:`semantic_facts` as_of>thesis.as_of LEFT JOIN links IS NULL,LIMIT 20(无游标状态);
  - `link_company(cid)`:每公司 1 次 `complete_json(task=THESIS_LINK, max_tokens=2500)`;prompt = 论点摘要(debates 的 question/bull/bear/VP 问题 + pillars 的 claim_zh/falsifier_zh)+ 编号事实清单(ref_id/type/date/polarity/narrative);**分类纪律含反例**:「大客户取消订阅」对公司 negative、对 bear 叙事 confirms_bear——裁决与公司利好利空解耦;拿不准必须 neutral,禁止硬判;
  - 行级校验(ref_id∈给出清单、target_key∈thesis、verdict∈对应词表),**无效行静默丢弃**(未入库事实下周期自然重试,无批级重试);`ON CONFLICT DO NOTHING`;
  - `check_verification_points(cid, thesis)`(**零 LLM 规则道**):每个带 metric+阈值的 VP,取最新 fundamentals 值(优先 derived)按 direction/双阈值判 → 每 period 一条 rule 行(fact_ref=`<metric>:<period_end>`, model='rule', strength=1.0),UNIQUE 保证 write-once;
- `glm_worker._llm_stage`(L257-280):pinned 块内 expert 后加 `out["links"] = evidence_link.link_pending(s.glm_worker_link_companies, run_id)`;pinned 外跑零 LLM VP 检查;quota 错误走既有 `is_quota_error` 路径。config 新旋钮 `glm_worker_link_companies=15`;
- CLI:`xar thesis link [company]`、`xar thesis links <company>`(裁决表,人工抽查信任度)。

测试 `tests/test_evidence_link.py`:1 有效+1 假 ref → 只入 1 行;重跑 0 新增(UNIQUE);VP 三态(超 bull 阈/破 bear 阈/灰区);router 测试 THESIS_LINK 订阅池优先。

### P4 — health_v3(争论感知健康度)+ 闭环闭合
新 `research/thesis_health.py`(thesis.py / thesis_signals.py 保持稳定,旧 health/health_v2 保留):
- `debate_health(cid, thesis_row)`:每争论只合并自己两条道——LLM 链接得分 = `Σ strength·(+1 bull/−1 bear) / max(n,3)` 截断 ±1;VP 规则得分 = 各 VP 裁决均值(±1/0);`lean_now = clip(0.6·llm + 0.4·vp)`;status ∈ `confirming_bull | confirming_bear | flipped | quiet`(**flipped** = lean_now 与作者立场反号且 |lean_now|≥0.3);附 top_facts(ref/verdict/strength/rationale_zh);
- `health_v3(cid)` = `thesis_signals.health_v2`(L108-141 整体复用)⊕ debates 块 ⊕ pillar 升降级规则(裁决 4:pillar 目标 LLM 链接只升降级 quiet/mixed→challenging,不改分);overall challenged = v2 challenged **或** 任一 weight≥0.3 的争论 flipped;
- `challenged_companies_v2(limit)`:并入争论翻转压力 → 换入 `glm_worker._alt_correction`(L296,一行)——**天平翻转自动触发论点重写**;
- 表面:API `GET /api/thesis/{cid}/health`(v3)+ `GET /api/thesis/{cid}/links`;dashboard `_thesis_health`(dashboard.py:637)切 v3(公司 360 页经 `_thesis_block` 自动获得 debates);CLI 新命令 `xar thesis health <company>`(争论 lean 条 / VP 读数 vs 阈值 / top facts;现状只有 show 内嵌 health);前端 `web/src/types-thesis.ts` 加 Debate 类型 + 公司页争论卡(问题/双方叙事/lean 计量条/VP 表/正反 top 事实)。

测试 `tests/test_thesis_health.py`(纯插行,零 LLM):bear 链接 + 破 bear 阈 VP → flipped + overall challenged + 出现在 challenged_companies_v2;无 debates 旧 thesis → v3 ≡ v2 形状(回归守卫);双计守卫(事件桶已计的 pillar 链接只升降级不改分)。

### P5 — 主题争论表面 + 旗舰回填 + 文档
- `theme_debate_health(theme)`(零 LLM):成员公司同 key 争论 lean 加权均值 + `macro_metric_keys` 最新 prints(macro_links/alt_signals);API `GET /api/themes/{tid}/debates` + 主题页卡 + CLI;
- **回填 runbook(DB 零迁移)**:① `xar indicators compute`;② 旗舰重建 `xar thesis build <cid> --force`(种子名单 ~15-20 家,EDITOR 质量档);③ 长尾走既有 challenged→rebuild 循环自然获得 debates;④ linker 自动回填(facts > as_of);
- 文档:DESIGN.md thesis 节 + SHOWCASE.md ServiceNow 走查;部署重建(用户确认后执行)。

---

## 7. 验证清单

- 每阶段:`ruff check` + `pytest` 全绿(离线:`complete_json` monkeypatch;DB:seeded_db fixture)。
- E2E 冒烟(P4 后,真 GLM 订阅):
  `xar indicators compute now` → `xar thesis build now --force` → 检查 `content['debates']` 含种子 key、VP 阈值为具体数字 → 手插反例事实(取消订阅类 kg_event)→ `xar thesis link now` → `xar thesis health now` 显示争论天平移动与 top facts。
- 关键回归:存量 thesis 行(无 debates)在 show / health / dashboard / API 全路径不炸。
- 人工抽查:`xar thesis links now` 审 LLM 裁决的 rationale_zh 是否真的「相对主张」而非复读公司情绪极性。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 批量 GLM 对 3 层嵌套 schema 的 JSON 可靠性 | 字段全原始类型;≤3 debates × ≤4 VP 校验;既有 retry-with-violations;旗舰走 EDITOR 档;max_tokens 8000 |
| 阈值随基数漂移变陈旧(「≥20%」两年后失真) | 每次 rebuild 重新作者化阈值;`_changed_because` 记录漂移;cadence 字段驱动陈旧标记(最新读数超 2 个 cadence 即标 stale) |
| 财年对齐 bug(period 字符串格式混杂) | 只按 period_end 日窗配对(350-380 天/80-100 天),永不 parse 标签;annual/quarter 混频跳过 |
| 廉价模型把公司极性当主张极性 | prompt 显式反例(取消订阅→公司 negative + confirms_bear);strength/rationale 全量可审计;数值规则道免疫;`xar thesis links` 抽查后再信任 flipped 触发的重写 |
| 范围失控 | 新代码面 = 3 新 ontology/research 模块 + 1 表 + 1 任务类 + ~5 测试文件;其余全是已验证接缝(§1 表)的编辑;估值修正 VP 与 LLM 主题论点显式推迟 |

## 9. 明确不做(本期)

- 微信关键词搜索式发现(已由 WM 计划裁决)/ LLM 生成主题级论点表 / 估值类 VP(estimates 修正驱动)/ 交易信号层(task #59 仍推迟)/ `agents/debate.py` 报告辩论的重构(保留原样,两者互补:报告辩论是叙事生成,本计划是结构化监控)。
