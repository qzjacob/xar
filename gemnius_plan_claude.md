# gemnius_plan_claude — Phanny 季报事件交易模块设计

> 作者视角：Claude（Opus 4.8）。本文档为**独立设计**，撰写全程未读
> `gemnius_plan_{kimi,minimax,glm,grok}.md`（撰写时四者均为空占位文件），
> 以保证与其它开发者方案的严格独立。

---

## 0. 摘要

新增第五个同级前端模块 **Phanny**（`/phanny`），与 Chathy / Andy / Genny / Fenny 平级，
位于 **Genny 之后**，对 **Genny 覆盖库内选中的公司**产出**下一次季报的事件交易观点**：
**方向（仅 long / short）· conviction（1-10,跨整本 book 正态分布,禁止全低）· size（组合 1-15%）**。

核心事实（代码探索确认）：XAR 已存在一套季报事件交易引擎——`research/earnings.py`、
`ontology/earnings_events.py`（`EarningsVerdict`）、`earnings_verdicts` 表、`xar earnings` CLI。
**Phanny 不从零起步**，而是**独立的同级决策层**：复用平台的接地 dossier / 数据源 / LLM 路由底座,
但拥有自己的 schema、多模型辩论引擎、横截面正态定标、sizing 与前端。

Phanny 相对既有引擎新增三件既无先例之物：
1. **方向只允许 long / short**——删除 `no_trade`,禁止 neutral,**禁止把期权策略作为交易观点输出**
   （期权结构仅作**输入信号**）;
2. **conviction 1-10**——第三套尺度,与既有 0-10（事件）/ 1-5（论点）两套按尺度隔离契约互不换算;
   且**跨整本 book 呈正态分布、禁止全低**;
3. **size 1-15%**——`src/xar` 全库无仓位/组合权重先例,全新。

推理阶段用**最高 reasoning effort、不限算力与数据源**;不同 LLM 从反方持续挑战直至**观点收敛**;
正态分布必须由**证据的真实分化自然涌现**,**禁止"压低 conviction 逼收敛 / 把分数塞进正态模板"
的作弊捷径**,不达标时只能**补数据、补观点、加辩论轮**。

---

## 1. 需求 → 设计映射（逐条落地）

| 需求 | 设计落点 |
|---|---|
| 同级模块 Phanny,处 Genny 之后 | 第五个 ModuleNav 模块;native 推理模块骨架(镜像 chathy 包 + capabilities 异步 run) |
| 对 Genny 库内选中公司 | universe = 扩展 `EARNINGS_UNIVERSE`(~34 期权流动性名)∩ `COMPANIES` 注册表 |
| 下一次季报 | `research/earnings.py:_next_earnings(cid)` + `structured.upcoming_calendar` |
| 方向 long/short,禁 neutral/期权策略 | `PhannyView.direction ∈ {long,short}`;`validate_view` 硬拒 no_trade/neutral;期权仅输入维度 |
| conviction 1-10 正态、禁全低 | 横截面正态定标门(整本 live book ~30 名),合法性门 + 忠实分箱 + 升级(见 §6) |
| size 1-15% | `phanny/sizing.py`:conviction × edge × 风险(implied move),clamp[1,15],报 gross/net |
| 六维推理(基本/技术/资金/情绪/期权结构/概率赔率) | `PHANNY_DIMENSIONS` 六维分析师,MAX effort 并行异模型 |
| 不同 LLM 反方挑战至收敛 | `phanny/debate.py`:Proponent / 每轮换 Challenger / 独立 Judge,钉扎不同 registry 模型 |
| 收敛后仍须正态;禁止仅压 conviction 收敛 | 假收敛检测(Judge `false_convergence`)→ 触发数据补充继续辩论;定标器绝不为凑正态调低 edge |
| 最高 reasoning effort、不限算力/数据源 | `complete_json(reasoning_effort="high", task=DEBATE/EARNINGS_JUDGE)`;订阅优先 + 抬高预算帽 |

---

## 2. 关键设计决策（已确认）

- **Universe**：复用并扩展 `ontology/earnings_events.py:EARNINGS_UNIVERSE`(~34 期权流动性名)为
  Phanny 固定 universe;每名字始终携带一份对其下一季报的 live 观点。
- **正态口径**：跨**整本 live book**(universe 中当前有活跃 next-quarter 观点的全部 ~30 名),
  conviction = **持续重定标的横截面 rank**(而非单模型直接给分)。
- **算力/成本**：**订阅优先**——不同免费订阅/宿主模型(`claude-opus-max` / `glm-5.2-sub` /
  `kimi-k3-sub` / `minimax-m3-sub` / `deepseek-v4-pro`)跑最高 effort,抬高单次预算帽
  (`new_batch_run_id("phanny")`);仅订阅额度耗尽才升级计费 Opus(`claude-opus-4-8`)。

---

## 3. 架构总览（每公司流水 + 整本定标）

```
Stage 0  证据装配(零 LLM,复用)   dossier_earnings(cid,event) + 期权结构增补 + 概率赔率增补 → text + known_ids(覆盖 6 维)
Stage 1  六维分析师(MAX,并行异模型)  subpool.run_parallel × llm.pinned(distinct) × complete_json(DimensionRead, effort=high)
Stage 2  综合(proponent 开局)       distinct 强模型 → 临时 PhannyView 草稿(direction + edge_score + asymmetry)
Stage 3  对抗辩论至收敛            Proponent / Challenger(每轮换模型) / Judge(第三模型) + 收敛 & 假收敛 & 数据补充回灌
Stage 4  终稿 + 校验              complete_json(PhannyView, EARNINGS_JUDGE) → validate_view → retry-once → 落 phanny_views
Stage 5  整本正态定标门(横截面)     收集全 book edge_score → 合法性门 → 不达标则升级(补数据/加轮)而非重标 → rank→conviction 1-10
Stage 6  sizing                  conviction × edge × 风险 → size 1-15%,报 gross/net
Stage 7  回验                    复用 earnings.score_outcomes / calibration → 按 conviction 分桶命中率
```

**贯穿全程的防作弊不变式**：
- Stage 3 收敛**绝不允许**靠"压低 conviction/edge"达成;Judge `false_convergence` 命中 → 补数据继续辩论。
- Stage 5 定标器**从不**做把分数压进正态的单调挤压、**从不**为凑正态调低任一 edge;仅在原始 edge
  已近似正态时做**一次、单调、忠实于 rank** 的分箱;不达标只**升级分析**,并写审计位断言未做重标。
- conviction 最终整数**不由单个 LLM 直接给**——横截面定标赋值,使"正态"是**整本 book 的属性**。

---

## 4. 数据模型

### `src/xar/ontology/phanny_events.py`（新建,镜像 `earnings_events.py` 纪律）

```python
DIRECTIONS = ("long", "short")           # 无 no_trade / 无 neutral
PHANNY_DIMENSIONS = (
    "fundamental", "technical", "capital_flows",
    "sentiment", "options_structure", "probability_odds",
)
ACTIONABLE_EDGE = ...        # 高 edge 触发证据密度门的阈值
_MIN_ANCHORS = 6
```

- `DimensionRead{key, score(-2..2), lean(long|short), confidence(0..1), note_zh, evidence[]}`
- `PhannyView{direction(long|short), edge_score(float 连续、按证据挣得), conviction(int 1..10 定标赋值),
  size_pct(float 1..15), expected_surprise_zh, move_view_zh, dimensions[6], thesis_zh, asymmetry_zh,
  falsifiers_zh[1..4], plan_zh, debate_summary_zh, evidence_robustness(0..1)}`
- `DebateRoundVerdict{posterior_direction, posterior_edge, delta_from_prev, new_evidence_entered,
  disagreement_resolved, residual_uncertainty_zh, false_convergence, needs_data[]}`
- `validate_view(v, known_ids)`——镜像 `validate_verdict`(`earnings_events.py:55`)：direction∈{long,short}
  (拒 no_trade/neutral);6 维 key 合法且齐全;evidence id ∈ known_ids;高 edge → 去重锚 ≥6 ∧
  `asymmetry_zh` 非空 ∧ ≥1 盘前 falsifier;`size_pct∈[1,15]`、`conviction∈{1..10}`。
- `PHANNY_UNIVERSE`(扩展 `EARNINGS_UNIVERSE`)、`phanny_universe(cap)`(∩ `registry.COMPANIES`)。

### `src/xar/storage/schema.sql`（追加三张幂等表）

- `phanny_views`：镜像 `earnings_verdicts`(`schema.sql:756`),`direction CHECK(long|short)`、新增
  `edge_score REAL`、`conviction INT CHECK(1..10)`(可空=未定标)、`size_pct REAL CHECK(1..15)`、
  `status CHECK(converged|calibrated|uncalibrated)`、`UNIQUE(company_id,event_date,version)`、INSERT 锁 + `--force` 版本递增。
- `phanny_debates`：`view_id FK, round, role(proponent|challenger|judge), model, content JSONB`——辩论全转录可审计。
- `phanny_book`：`as_of UNIQUE, members JSONB, distribution JSONB{mean,std,skew,normality_stat,passed,escalations}, status`——定标快照。

---

## 5. 后端包 `src/xar/phanny/`（native 推理模块）

- `__init__.py` · `dimensions.py`(6 维 prompt + 期权结构/概率赔率增补) · `engine.py`(单公司 Stage 0-4,
  镜像 `build_verdict` 的 2-attempt validate-repair + 原子版本 INSERT) · `debate.py`(多模型对抗环) ·
  `calibration.py`(正态门+分箱+升级) · `sizing.py` · `book.py`(编排 + 回验)。CRUD 一律 `from ..storage import db`。

### Stage 1 六维分析师
`subpool.run_parallel(dimensions, fn)`(`models/subpool.py:121`,每线程内重新 `llm.pinned` 到指定异模型),
`fn` 内 `complete_json(prompt, DimensionRead, task=TaskClass.DEBATE, reasoning_effort="high", max_tokens=大)`;
每维接地于 `known_ids` 白名单(镜像 `agents/nodes.py:analyst` 强制 `[id]` 引用);维度间跨 provider 分散避盲区。

### Stage 3 对抗辩论
- 角色钉扎不同 registry 模型(每轮轮换 Challenger)：Proponent(如 `claude-opus-max`)/ Challenger
  (每轮换 `deepseek-v4-pro`→`kimi-k3-sub`→`minimax-m3-sub`→`glm-5.2-sub`,构建**最强反方**,攻方向/幅度/edge)/
  Judge(第三独立模型,复用 `agents/evidence_gate.py:_judge` 裁决模式)。
- 开局立场用 `ontology/debates.py:DEBATE_SEEDS`(策展 steelman 多空)注入。
- **收敛** = 连续 2 轮 `|delta|<eps` ∧ `disagreement_resolved` ∧ 非 `false_convergence`。
- **假收敛检测(防作弊核心)**：轮间变化被"edge/conviction 下降"主导、而方向/幅度分歧仍活且可用证据解决 →
  Judge 置 `false_convergence=true` → **不接受**;执行 `needs_data`(更深期权链、FMP transcript/statements/analyst
  MCP、更多新闻社媒、预测市场、加维/加 effort 重跑),新证据接地进 dossier(扩 `known_ids`)后**继续辩论**。

---

## 6. 正态分布合法性（本方案核心贡献,防作弊）

需求最难点：conviction 1-10 须跨整本 book 正态、禁全低,且**正态必须由证据真实分化涌现**,
**不得**靠(i)把分数经 CDF 挤压进正态模板(相当于纯重贴标),或(ii)压低 conviction 逼辩论收敛。

**两层 conviction**：
- 每公司：辩论收敛得到**连续 `edge_score`**(按证据挣得的方向强度)+ direction——这是收敛的"观点"。
- 整本：`edge_score` 横截面 → 整数 conviction 1-10。

**Stage 5 定标门(`phanny/calibration.py`)**：
1. 收集全 book `{cid: edge_score}` + `evidence_robustness`。
2. **正态合法性门**(N~30 用组合判据)：非退化 spread、**非全低**(存在高 edge 名、均值不贴底)、近似对称单峰
   (|skew| 有界)、Shapiro–Wilk / Anderson–Darling 作参考;标准化复用 `research/thesis_signals.py:_zscore`
   (clip±3、`statistics.fmean/pstdev`)。
3. **不达标 → 升级(绝不重标)**：聚簇-含糊(证据薄)→ 取低 robustness/近中位名加数据源+加轮+提 effort 重跑至
   edge 真实分化;过度自信簇 → 对拥挤高位名加对抗轮找证伪。受 `phanny_max_book_escalations` 限;仍不达标 →
   **不作弊**,落 `status=uncalibrated` 并显式告警("正态无法在不制造前提下达成——需人工/补数据")。
4. **达标 → 忠实分箱**：edge_score → z → 固定正态分位切点 → conviction 1-10(单调、居中~5-6、忠实 rank;
   因输入已近似正态故输出正态而**非挤压所致**)。每次定标写审计位断言未为凑正态调低任一 edge。

---

## 7. 注册触点（镜像既有同级模块）

- `api/phanny.py` + `api/app.py` 在 SPA catch-all(`app.py:1016`)前加路由(lazy `from . import phanny`)：
  `GET /api/phanny/book` · `GET /api/phanny/view/{cid}` · `POST /api/phanny/build/{cid}` ·
  `POST /api/phanny/calibrate` · `GET /api/phanny/calibration`。
- `cli.py`：`phanny_app = typer.Typer(...)` + `app.add_typer(phanny_app, name="phanny")`;命令
  `build[--cid][--force]` / `calibrate` / `book` / `view <cid>` / `outcomes` / `calibration`;`_phanny_init_impl()` 挂进 `init()`。
- `config.py`：`enable_phanny`(alias `XAR_ENABLE_PHANNY`) + `phanny_max_debate_rounds`(6) /
  `phanny_convergence_eps`(0.05) / `phanny_max_book_escalations`(3) / `phanny_min_high_conviction` /
  `phanny_gross_cap_pct`(0=off) / `phanny_challenger_models`;`.env.example` 记录。
- `capabilities/registry.py`：`phanny_build_view` / `phanny_calibrate_book`(`kind="build",duration="slow",
  chathy=False` → 走 `capability_runs` 异步)；`phanny_book` / `phanny_view`(`kind="read",chathy=True` → Chathy 工具)。
- 前端：`web/src/lib/modules.ts`(加 `"phanny"` 到 `ModuleKey`+`MODULES`) · `web/src/App.tsx`(`lazy()`+
  `<Route path="/phanny/*">`) · `web/src/pages/phanny/PhannyApp.tsx`(用 `ModuleShell`) · `web/src/lib/phanny.ts`+
  `types-phanny.ts`;复用 `accent` token。**签名 UI = book 页 conviction 正态直方图** + 每名卡片(方向/conviction/size)
  + view 详情(6 维 reads + 辩论转录 + falsifiers + plan) + calibration(分桶命中率);图表遵循 dataviz 规范。
- (可选后置) `orchestration/glm_worker.py` 加 `_phanny_step`(镜像 `_earnings_step`)或 dagster asset 每日重建+定标;host-only 门控。

---

## 8. 复用清单（勿新写）

- Dossier/事件：`research/earnings.py`(`dossier_earnings` `_next_earnings` `_implied_series_for`
  `hist_move_stats` `beat_stats` `_revision_drift` `reaction_return` `score_outcomes` `calibration`)
- Universe：`ontology/earnings_events.py:EARNINGS_UNIVERSE/earnings_universe`、`ingestion/registry.py:company_by_id/COMPANIES`
- LLM：`models/llm.py`(`complete_json/complete/pinned/CLAUDE_MAX_PIN/new_batch_run_id`)、
  `models/router.py:TaskClass.DEBATE/EARNINGS_JUDGE`、`models/subpool.py:run_parallel`、
  `models/registry.py:candidates_for/get`、`models/{agentsdk,codex_cli}.available`
- 期权结构：`fcn/options/analytics.py:analyze_surface`(iv_rv_gap/skew/term/vol_regime)、
  `fcn/options/greeks.py:implied_vol/bs_greeks`、`fcn/marketdata/volsurface.py`、
  `fcn/options/chain.py:OptionChain.from_massive`、`providers/alt/implied_move.py`
- 资金/情绪/技术：`research/flow.py:flow_snapshot`、`research/thesis_signals.py:signal_snapshot/_zscore`、
  `providers/sentiment.py:score`、`storage/structured.py:upcoming_calendar/estimate_series/latest_fundamentals`
- 辩论/裁判范式(适配)：`agents/debate.py`、`agents/evidence_gate.py:_judge`、`agents/nodes.py:analyst`;
  纪律模板 `ontology/earnings_events.py:validate_verdict`、`ontology/thesis.py:validate_thesis`
- 存储：`storage/db.py:query/execute/tx`;`storage/schema.sql`(追加)

---

## 9. 分期

- **P0**：`ontology/phanny_events.py`(schema+validate+universe)+ `schema.sql` 三表 + config 开关 + 单测(纯逻辑,无 LLM)。
- **P1**：`engine.py` Stage 0-2 + 单模型落 converged view。
- **P2**：`debate.py` 多模型对抗环 + 收敛 + 假收敛 + 数据补充。
- **P3**：`calibration.py`(正态门+分箱+升级)+ `sizing.py` + `book.py` 编排。
- **P4**：API + CLI + capabilities 注册。
- **P5**：前端 `/phanny`(正态直方图 + view 详情 + calibration)。
- **P6**：回验 grading + 可选 worker/dagster 接线。

---

## 10. 验证（端到端）

1. **建表**：`docker compose exec app xar init` → 确认 `phanny_views/phanny_debates/phanny_book`。
2. **单测**(`tests/test_phanny_*.py`)：`validate_view` 拒 no_trade/neutral、要求 6 维齐全、高 edge 需 ≥6 锚+asymmetry+falsifier;
   `PHANNY_UNIVERSE ⊂ COMPANIES` 且有纯字母 US ticker;`calibration` 分箱忠实 rank/单调/**从不为凑正态调低 edge**、
   聚簇触发升级、**全低输入被升级/拒绝而非接受**;`sizing` ∈[1,15]/随 conviction 单调/gross cap 缩 size 不动 conviction;
   **假收敛**脚本(仅 conviction 降)→ 触发数据补充而非接受。
3. **集成**(host、订阅 arm)：`xar phanny build --cid nvidia --force` → 带辩论转录的收敛观点;`xar phanny calibrate` →
   book 直方图近正态;`GET /api/phanny/book`。
4. **优雅降级**：无 LLM key / 离宿主(subpool 空)跳过,同 earnings host-only 路径。
5. **前端**：`/phanny` 渲染 book+直方图+view 详情。
6. **回验闭环**：财报后 `xar phanny outcomes`(direction_hit + realized-vs-implied) + `xar phanny calibration`(分桶命中率)。

---

## 11. 独立性声明

本方案撰写全程**未参考** `gemnius_plan_{kimi,minimax,glm,grok}.md`(撰写时均为空占位),
设计取材仅来自 XAR 代码库自身探索 + 用户需求。与其它开发者方案的任何相似纯属对同一代码库的独立收敛。

---

## 12. 融合最佳(v2)——取四份之长补短(执行版)

> 评估四份竞品(glm 81.2 / grok 78.6 / kimi 72.7 / minimax 47.8)后,吸收各家最强件,落成本次**实际执行**的架构。

**架构修正(核心)**:conviction **由每名辩论直接产出 1-10**,再经**批级正态门 → 不达标则 REDEBATE 离群名(补数据,绝不重标/压低)**——替代原 §6 的 rank→normal 挤压(后者有"套正态模板"嫌疑,不合"由证据涌现"要求)。`edge_score` 降为 sizing/排序辅助。

- **正态门(采 glm)**:`phanny/distribution.py:ensemble_normality(convictions)` = `mean∈[4.5,6.5]` ∧ `std≥1.5` ∧ 高信念(≥7)占比≥0.10 ∧ 非全低 ∧(scipy 且 n≥8 → **Shapiro-Wilk p≥0.05**;否则退矩法 `|skew|<1 ∧ |exkurt|<1.5`);`convergence_integrity(traces)` 比 final vs round1,**下调>2.0 且批非正态 ⇒ 违规**(强制补数据重辩)。scipy 可选,缺失退矩法,不加硬依赖。
- **诚实兜底(采 minimax)**:REDEBATE 上限 `phanny_max_book_passes=2`,仍不达标 → 批状态 `calibration_incomplete`/`deferred_low_signal`,原样留痕不造假。
- **假收敛(采 grok)**:`conviction_only_haircut(prev,cur)`(方向未变 ∧ 证据锚未增 ∧ 仅 conviction 下调)烘焙进收敛谓词 → **不算收敛**。
- **多 critic 协议(采 minimax)**:signed-Δ —— critic 返回 `{direction_vote(agree|disagree|abstain), conviction_delta(-2..2), size_delta(-3..3), attack_zh, rebuttal_zh}`;critic 必须攻击≥1 维;`n_facts<4 ⇒ abstain`;异厂商、无共享上下文。收敛 = `≥2/3 同向 ∧ 近3轮 conviction/size std≤(1.0,1.5) ∧ 非 conviction_only_haircut`。
- **sizing(采 glm+kimi,代码侧确定性,LLM 不报 size)**:`size_pct = clip(kelly(conviction,asymmetry) × inv_vol(implied_move) × gross_scale, 1, 15)`;组合层 `gross_cap`(默认 150%)与单主题集中度上限,超限**按比例缩 size(不动 conviction)**。
- **六维齐全门(采 kimi)** + **known_ids 白名单接地**:真实前缀 `estimate:{cid}:{metric}` / `ratings:{as_of}` / `alt:{key}:{period}` / `calendar:{id}` / `price:*` / `macro:*`,phanny dossier 新增 `tech:{cid}` / `flow:{cid}` / `opt:{cid}` 由本模块真实产出并纳入 known_ids。
- **输出卡(采 minimax)**:每名 6 维打分 + 5 分箱概率 + E[ret] + catalysts + falsifiers + debate log;book 页 conviction 直方图 + calibration 审计。
- **独立表(采 glm/kimi,拒 grok 的 ALTER 耦合)**:新建 `phanny_verdicts`,与 `earnings_verdicts` 尺度/存储隔离。

**执行分期(P0→P7,每期 pytest+ruff 绿)**:P0 ontology+schema+config+router → P1 dossier(6 维)+ distribution + sizing(零 LLM) → P2 proposer(engine) → P3 多 critic debate → P4 book 正态门+REDEBATE+入库 → P5 capabilities+API+CLI → P6 前端 `/phanny` → P7 全量测试 + **端到端冒烟**(stub LLM 确定性路径,离线可跑;host 订阅在则真跑)。
