# Phanny — 围绕季报的多空裁决终端(辩论收敛 + 组合正态约束)

> **状态:设计定稿,待执行**。与 Chathy / Fenny 同级的**第五大前端模块**(`ModuleNav` 中排在 Genny 之后),复用既有 ET 数据层,但拥有独立的多 LLM 对抗辩论引擎、独立裁决 schema(**禁止 neutral/期权**,conviction 1-10,组合级正态分布约束,size 1-15%)、独立表与前端。任务前缀 **PH-**。
>
> 本计划独立成稿,**未参考** `gemnius_plan_kimi/minimax/grok.md`。

---

## 0. Context(为什么 & 与 ET 的关系)

用户要求把"季报事件交易"从 ET(单判官、允许 `no_trade`、嵌在 Genny)升级为一个**独立的、更激进的决策模块 Phanny**:对 Genny 库内选中公司(默认 `EARNINGS_UNIVERSE` ~32 家期权流动性好的美股 + UI 临时选股)的下一次季报,产出**强制方向(long/short,禁止 neutral)、conviction 1-10 且全组合须呈正态分布、size 1-15%** 的交易观点,经**多个不同 LLM 反方挑战 → 持续辩论 → 观点收敛**得出,推理全程最高 reasoning effort、不限算力与数据源。

**已确认的 4 项决策**(对话澄清):
1. **方向严格 long/short**——禁止 neutral/no_trade 输出,禁止期权/结构化策略(纯方向性股票);
2. **正态分布 = 组合级 ensemble**:当批全部 Phanny 裁决的 conviction 须呈钟形(有合理展幅,禁止全低聚集);"为收敛而调低 conviction"不被接受,须继续补数据/观点;
3. **Phanny 是 Genny 的新同级模块,复用 ET 数据层**(dossier/implied_move/estimates/prices/beat/guidance/thesis/macro),不重复造数;Genny 内的 `EarningsSection`(单判官 ET)保留共存;
4. **Universe = `EARNINGS_UNIVERSE` + UI 临时选股**。

### 现状可直接复用(零重写,全部 file:line 实证)
- **季报 dossier 组装器**:`research/earnings.py:209 dossier_earnings()`(11 节接地事实面板,含预期/beat/guidance/评级/隐含波动/情绪/alt/宏观/主题争论/论点/价格/覆盖缺口),`known_ids` 纪律 + 单节容错。Phanny 直接 `from ..research import earnings; earnings.dossier_earnings(cid, event)`。
- **期权隐含波动**:`providers/alt/implied_move.py` + `altdata.py` 的 `alt.options_implied_move`(ATM straddle,免费 yfinance,`massive_api_key` armed 时切真 IV)。
- **价格/反应**:`earnings.reaction_return` / `hist_move_stats` / `backtest/catalyst_returns._series`。
- **校准分桶范式**:`earnings.py:666-698 calibration()`(`_CONVICTION_BUCKETS` 半开区间)——Phanny 校准直接镜像。
- **结构化裁决 + 校验范式**:`ontology/earnings_events.py`(`EarningsVerdict` Pydantic + `validate_verdict` 五规则);`research/earnings.py:516-533`(complete_json → validate → 违规清单重试)。
- **host 深度研究执行器**:`earnings.py:460-470 _preferred_pin()`(codex→claude-max→None);`models/llm.py:132-139 CLAUDE_MAX_PIN/CODEX_PIN`;`agentsdk.py:65-70,107-153` / `codex_cli.py:58-63,80-123`(订阅执行器,usd=0)。
- **多 provider 钉扎原语**:`models/llm.py:142-148 pinned()`(contextvar,可嵌套/可按角色切换)——Phanny 多 LLM 辩论的核心积木。
- **reasoning effort**:`llm.py:223-248`(`reasoning_effort=` 显式 > `model_effort="high"` > "low");codex 支持 `codex_effort`。
- **Worker 节拍模板**:`glm_worker.py:557-577 _earnings_step()`(模块级、pin/quota 门外、`_due`/`_stamp`);`:427 _run("earnings_watch",…)`。
- **能力 + Chathy 暴露**:`capabilities/registry.py:392-430`(CapabilitySpec + `kind="build"`)。
- **前端模块脚手架**:`web/src/lib/modules.ts:12,25-38`(nav registry)、`web/src/App.tsx:6,101`(lazy route)、`web/src/pages/fenny/FennyApp.tsx`(模块 App 范式)、`web/src/lib/fenny.ts:3,5-20`(BASE + jget/jpost)、`web/src/types-fenny.ts`(请求/响应类型)。

### 缺失(本计划要建的)
- **Phanny 决策引擎**:多 LLM 对抗辩论循环 + 收敛判据 + 反"调低 conviction"完整性守卫;
- **组合级正态分布约束器**(ensemble normality gate)+ 与辩论收敛的耦合;
- **仓位 sizing**(conviction/edge/risk → 1-15%);
- **PhannyVerdict schema**(long/short only、conviction 1-10、size 1-15、6 维分析、辩论 trace);
- **表 `phanny_verdicts`** + TaskClass `PHANNY_VERDICT`/`PHANNY_CHALLENGE` + config + CLI + API + 前端模块 + 能力。

---

## 1. 关键裁决(压力测试点定案)

1. **方向二元化**:Phanny 裁决 `direction ∈ {long, short}`,DB 层 `CHECK` 强制。ET 的 `no_trade` 在 Phanny 不存在——无 edge 时仍须二选一(取**期望赔率更优的一边**,并在 `caveat_zh` 写明把握不足)。这牺牲了 ET 的"宁缺毋滥"换"始终表态",正是用户要求。conviction 表达对该方向的信心(1=极弱、10=极强)。
2. **正态分布是验收门,不是捏造函数**:`ensemble_normality(batch)` 为 bool 门。**不可为凑钟形而篡改单家公司 conviction**——哲学:全低聚集 = 分析不够锐,系统须**往深里挖数据/重辩论**让强 edge 自然浮出高 conviction、弱 edge 自然落低,钟形是"分析足够锐"的**副产品**。唯一合法满足路径 = 更深分析 + 更多数据;**严禁**路径 = 普遍下调 conviction。两条都被 `convergence_integrity()` 守卫拦截(见 §2 Ph-P1)。
3. **辩论模型分配 = 不同 provider 钉扎**:主分析师、N 个反方挑战者、裁决官**各自钉到不同订阅模型**(`llm.pinned` 切换 contextvar),实现真正的多 LLM 对抗而非单模型自博。角色-模型映射见 §2 Ph-P3 表;host 无某执行器时该角色优雅回退(钉扎链内自动轮转)。
4. **收敛判据**(三者全满):① 方向在最近 ≥2 轮稳定;② conviction 跨轮 Δ < 0.8;③ 无反方提出**未回应的重大异议**(material objection 列表为空)。达上限轮数仍未收敛 → **回拉更多数据面板**重开(期权 skew/GEX、同业已出财报串扰、渠道专家)——不调低 conviction 凑收敛。
5. **size 确定性公式**(零 LLM,可测):`size_pct = clip(K(conviction, asymmetry) · inv_vol · portfolio_cap, 1, 15)`。组合层施加总敞口上限(config `phanny_gross_cap_pct`,默认 100%)+ 单一行业/主题集中度上限,溢出按 conviction 比例缩放。size 写 `sizing_rationale_zh`。
6. **锁定语义**:镜像 ET——INSERT 即锁(`created_at` = 锁定时刻),`--force` 才 version+1;`outcome/outcome_at` 盘后回填。新增 `ensemble_status`/`debate_models`/`size_pct`/`rounds` 列。组合正态门在**入库前**校验整批,失败则该批延后(标 `deferred_non_normal`),触发再分析,不入半成品。
7. **成本 = 订阅 usd=0**:全程钉扎订阅执行器(codex-sub/claude-opus-max/glm-5.2-sub/kimi-k3-sub/minimax-m3-sub),`_record` 记 `usd=0`(`llm.py:178-179`),**单次预算上限 `XAR_LLM_MAX_USD_PER_RUN` 对订阅调用是 no-op**(`llm.py:179` 注释)——即"不限制算力"的经济实现。token 回退仍受 cap,可经 env 调高。辩论 ~5-15 轮/家、每轮 1-3 次调用,财报季 ~15-40 家,全部 $0。
8. **与 ET 数据层单向依赖**:`phanny` → `research.earnings`(只读 dossier/implied/reaction),反向不依赖;ET 不改一行。`EarningsSection`(Genny)与 `PhannyApp` 共存。

---

## 2. 分阶段(每阶段 pytest+ruff 独立绿;零 DDL 除 Ph-P0 一表)

### Ph-P0 — 本体 + 路由 + DDL + 分布/sizing 骨架(零网络/零 LLM)

**新建 `src/xar/ontology/phanny.py`**(骨架见附录 A):
- `PHANNY_DIMENSIONS`(**6 维**,比 ET 的 8 维更聚合,覆盖用户要求的全谱面):`fundamental` / `technical` / `capital_flow` / `sentiment` / `options_structure` / `probability_odds`;`DIRECTIONS=("long","short")`;
- `DimensionRead(score -2..+2 / note_zh / evidence)`、`PhannyVerdict`(Pydantic,兼作 LLM 结构化输出 schema):`direction∈{long,short}`、`conviction=Field(ge=1,le=10)`、`size_pct=Field(ge=1,le=15)`、6 维、`expected_surprise_zh`/`move_view_zh`/`plan_zh`/`falsifiers_zh`/`asymmetry_zh`/`sizing_rationale_zh`/`caveat_zh`/`debate_trace`;
- `validate_phanny(v, *, known_ids, round1_conviction=None)`(规则见附录 A,含**反调低守卫**:`final<round1-Δ` 且 ensemble 非正态 → 拒)。

**新建 `src/xar/phanny/distribution.py`**(附录 B):
- `ensemble_normality(convictions) -> {ok, mean, std, skew, exkurt, shapiro_p, n, buckets, reason}`;门:`std≥1.5` ∧ `mean∈[4,7]` ∧(`scipy` 且 `n≥8` → Shapiro-Wilk `p≥0.05`,否则 `|skew|<1.0 ∧ |exkurt|<1.5`)∧ 高信念(≥7)占比 `≥0.10`;
- `convergence_integrity(verdicts_trace) -> list[str]`:逐家比 `final vs round1` conviction,下调幅度 `>Δ(=2.0)` 且本批 `ensemble_normality=False` → 列入违规(须重辩论+数据,非降 conviction)。
- **scipy 可选**:`try import scipy.stats`;缺失退矩法带(已在门里分支),不引入硬依赖。

**新建 `src/xar/phanny/sizing.py`**(附录 C):确定性 `size_pct(conviction, asymmetry, vol_pct, portfolio) -> (pct, rationale_zh)`,Kelly 化基 × `inv_vol` × 组合缩放,clip[1,15]。

**修改**:
- `models/router.py`:加 `PHANNY_VERDICT = "phanny_verdict"` + `PHANNY_CHALLENGE = "phanny_challenge"`(均 `RoutePolicy(STRONG, TOKEN, "normal")`,注释同 EARNINGS_JUDGE:host 由 pinned 提级订阅执行器)。
- `storage/schema.sql` 底部加性幂等:`phanny_verdicts`(附录 D,镜像 `earnings_verdicts` + `size_pct`/`debate_models`/`rounds`/`ensemble_status`)。
- `config.py`(镜像 `earnings_*` 块,`config.py:79-83` 之后):
  ```python
  phanny_watch_days: int = 10
  phanny_verdict_lead_days: int = 3
  phanny_outcome_max_days: int = 5
  phanny_debate_max_rounds: int = 6          # 收敛上限(到顶回拉数据,非降 conviction)
  phanny_convergence_conv_delta: float = 0.8
  phanny_ensemble_sigma_min: float = 1.5
  phanny_gross_cap_pct: float = 100.0        # 组合总敞口
  phanny_verdict_host_only: bool = False
  ```

**测试 `tests/test_phanny_ontology.py`**:schema roundtrip;validate(long/short only、幻觉 id 拒、conviction 范围、调低守卫触发);`ensemble_normality`(合成正态=ok、全低聚集=拒+reason、单点样本=insufficient);`size_pct` 数学+clip+组合缩放;`PHANNY_VERDICT∈POLICIES ∧ STRONG`。

### Ph-P1 — 分布/sizing 完整 + 校准复用(零 LLM)

- `phanny/distribution.py` 增 `calibration()`(镜像 `earnings.calibration`,但分桶按 Phanny 的 [1-3/4-6/7-8/9-10],无 abstain)。
- **复用**:`phanny.engine` 直接 `from ..research import earnings`(dossier/implied/reaction/beat),**不重写任何数据件**。

**测试 `tests/test_phanny_distribution.py`**:正态/偏态/小样本全套;校准分桶。

### Ph-P2 — PhannyAnalysis 推理组装器(最高 reasoning effort)

`src/xar/phanny/engine.py`(附录 E):
- `assemble_analysis(cid, event) -> dict`:调 `earnings.dossier_earnings(cid, event)`(复用接地事实)→ 注入 **6 维**分析框架提示词 → `llm.complete_json(prompt, PhannyAnalysis, system=_SYSTEM_ANALYSIS, task=PHANNY_VERDICT, reasoning_effort="high", node="phanny_analyze", max_tokens=8000)`,包 `with llm.pinned(_primary_pin()):`;违规重试一次(同 `earnings.build_verdict` 范式)。
- `_primary_pin()`:`_preferred_pin()`(codex→claude-max)+ `("claude-opus-max","glm-5.2-sub")` 兜底;host 无执行器 → 裸 token 强模型(deepseek-v4-pro)。**reasoning_effort 显式 "high"**(codex 走 `codex_effort`,agent_sdk 走 `anthropic_max_effort`,token 走 `model_effort`)。

**测试 `tests/test_phanny_analysis.py`**(mock dossier + mock complete_json):6 维出现、known_ids 接地、effort 透传、违规重试。

### Ph-P3 — 多 LLM 对抗辩论引擎(核心)

`src/xar/phanny/debate.py`(附录 F):角色-模型钉扎表

| 角色 | 钉扎(host 回退链内自动轮转) | 职责 |
|---|---|---|
| 主分析师(Ph-P2) | `("codex-sub","claude-opus-max","glm-5.2-sub")` | 6 维分析 + 初稿裁决 |
| 反方挑战者 ×3(不同 provider) | `("claude-opus-max",…)` / `("glm-5.2-sub",…)` / `("kimi-k3-sub","minimax-m3-sub")` | 钢人反方:攻击最弱维度、列举证伪证据、提替代叙事 |
| 数据补强官(按需) | `("glm-5.2-sub","kimi-k3-sub")` | 收敛失败时回拉**额外面板**(期权 skew/GEX、同业财报串扰、渠道专家、宏观打印) |
| 裁决官 | `("codex-sub","claude-opus-max")` | 收敛后输出最终 `PhannyVerdict`(带 `debate_trace`) |

- `debate(cid, event, analysis) -> {verdict, rounds, models, residual_objections}`:循环 ≤ `phanny_debate_max_rounds`,每轮三挑战者各钉扎不同模型提异议 → 主分析师范式内 `pinned` 回应/修正 → 收敛判据(§1 裁决 4)判定;未收敛 → 数据补强官加面板后重开(非降 conviction)。
- `_converged(history) -> (bool, residual)`。
- 全程 `task=TaskClass.PHANNY_CHALLENGE`/`PHANNY_VERDICT`,`reasoning_effort="high"`。

**测试 `tests/test_phanny_debate.py`**(打桩 pinned/complete):角色钉扎分配、收敛在稳定方向+小 Δ 时成立、max_rounds 触发数据补强而非降 conviction、debate_trace 记录每轮模型与异议。

### Ph-P4 — 组合正态约束 + 入库 + 闭环 + 编排 + CLI/API/能力

- `engine.build_verdict(cid, *, event, force, run_id)`:`assemble_analysis` → `debate` → `validate_phanny(known_ids, round1_conviction)` → 暂存(不入库);`engine.judge_due()`:批量组装后跑 `ensemble_normality`,**ok 才整批 INSERT**(version 原子子查询,镜像 `earnings.py:544-557` 的并发竞态处理);**非正态** → `convergence_integrity` 判是"调低致贫"(拒,回拉数据重辩)还是"市场本就缺可分 edge"(诚实 `deferred_low_signal` 状态,不造假)。
- `score_outcomes()`/`calibration()`:镜像 ET,方向命中(long=reaction>0 / short=reaction<0),无 abstain。
- **`glm_worker.py`**:加模块级 `_phanny_step()`(镜像 `_earnings_step:557`,pin/quota 门外),`_due("phanny_verdicts",24h)→judge_due()`,`_due("phanny_outcomes",12h)→score_outcomes()`;`run_once` 增 `out["phanny"]=_phanny_step()`。`_pull_fresh` 复用 `_run("earnings_watch",…)`(数据源共享,不重复拉)。
- **`cli.py`**:`phanny_app`(镜像 `earnings_app:756`):`portfolio`(组合表 + 正态摘要 + 总 size)/ `panel CID` / `judge [CID|--due] [--force]` / `trace CID`(辩论全流程)/ `calibration`。
- **API**:`api/app.py` 镜像 `ops_earnings*`(`app.py:638,674`):`GET /api/phanny/portfolio`(组合 + ensemble 统计 + 钟形分桶)/ `GET /api/phanny/verdict/{cid}` / `POST /api/phanny/{cid}/judge`(BackgroundTasks)/ `GET /api/phanny/calibration`。
- **能力**:`capabilities/registry.py` 加 `build_phanny_verdict`(kind=build)、`phanny_verdict`/`phanny_portfolio`(Chathy 读工具)。

**测试 `tests/test_phanny_engine.py`**(打桩):整批正态才入库、并发 version 竞态 raced、调低致贫拒、`deferred_low_signal` 诚实路径、outcome hit/miss、校准。

### Ph-P5 — 前端模块 + 文档 + 真机 E2E

- **nav**:`web/src/lib/modules.ts:12` 加 `"phanny"` 到 `ModuleKey`;`:25-38 MODULES` 在 `genny`(`:30-31`)之后插入 `{key:"phanny", label:"Phanny", cn:"季报多空裁决", route:"/phanny", icon: Crosshair/*lucide*/, match:p=>p.startsWith("/phanny")}`。
- **路由**:`web/src/App.tsx:6` 加 `const PhannyApp = lazy(()=>import("./pages/phanny/PhannyApp"));`,在 `:101` 后加 `<Route path="/phanny/*" element={<Suspense fallback={<LazyFallback name="Phanny"/>}><PhannyApp/></Suspense>} />`。
- **模块 App**:`web/src/pages/phanny/PhannyApp.tsx`(镜像 `FennyApp.tsx`:`ModuleShell`+`SidebarFrame`+`SidebarNav`,子路由 index=Portfolio blotter、`verdict/:cid`=详情、`watch`=队列)。用全局 `accent-*` token(与 Fenny 一致,无需自定义色)。
- **类型/库**:`web/src/types-phanny.ts`(镜像 `types-fenny.ts`)+ `web/src/lib/phanny.ts`(`const BASE="/api/phanny"`,镜像 `jget/jpost`)。
- **核心页**:
  - **Portfolio blotter**:每行 company/direction/conviction/size/距财报天;顶部** conviction 钟形直方图**(Plotly 复用 `components/charts/PlotlyChart.tsx` 懒加载分片)+ ensemble 统计(mean/std/skew/Shapiro p)+ 总敞口 vs `gross_cap`;`deferred_low_signal` 醒目诚实提示。
  - **Verdict 详情**:方向徽章、conviction 表针(≥7 高亮)、size 环、6 维雷达/条、asymmetry/odds、**辩论 trace**(每轮模型 + 异议 + 主方回应 + 收敛点)、falsifiers、implied vs 历史、outcome 历史。
- **文档**:`DESIGN.md` 新增 §5.15;README 四大模块表述升为五大(Chathy/Andy/Genny/Phanny/Fenny);`UI.md` 一段。
- **真机 smoke(host,美股无 CN egress)**:`xar phanny watch → panel → judge --due`(真订阅执行器,记录 debate_models)→ `GET /api/phanny/portfolio` 钟形校验 → 财报后 `xar phanny calibration` 闭环。
- 全量 `pytest -q` + `ruff`;`xar init` 幂等(新 DDL);对抗代码评审(house 惯例)→ 修复 → 合并 → 部署(经用户确认)。

---

## 3. 成本纪律

| 项 | 频率 | 约束 |
|---|---|---|
| 分析/辩论 LLM(PHANNY_*) | 财报季 ~15-40 家 × ~5-15 轮 × 1-3 调用 | **全订阅 usd=0**(codex/claude-max/glm/kimi/minimax);token 回退受 cap 可调高 |
| dossier/分布/sizing/outcome | 每日 | **零 LLM**(复用 ET 数据 + 纯计算) |
| 数据回拉(期权 skew/GEX/同业) | 收敛失败按需 | 复用既有 provider,无新成本 |

**"不限算力"的实现**:所有 LLM 调用钉扎订阅执行器 → `usd=0` → `XAR_LLM_MAX_USD_PER_RUN` 对其为 no-op(`llm.py:179`)。

## 4. 风险

| 风险 | 缓解 |
|---|---|
| 强制 long/short 在无 edge 时给伪观点 | conviction 表达真实信心(可至 1)+ `caveat_zh` 明示;组合正态门 + 校准回看暴露低命中桶 |
| 正态门逼出"为钟形而捏 conviction" | `convergence_integrity` 守卫 + 哲学定调(钟形=分析锐度的副产品,唯一合法路径=更深分析);诚实 `deferred_low_signal` 兜底 |
| host 无 codex/claude → 辩论退化为单 token 模型 | 钉扎链内自动轮转 GLM/Kimi/Minimax 订阅(仍 usd=0);`debate_models` 列记录实际模型供校准分层 |
| 小样本(n<8)正态检验无意义 | `ensemble_normality` 小样本走矩法带 + `insufficient_sample` 状态(不判 ok 也不强入库) |
| 辩论不收敛耗算力 | `phanny_debate_max_rounds` 上限 + 到顶回拉数据而非无限轮;全订阅 $0 无预算压力 |
| 与 ET 表/语义混淆 | 独立表 `phanny_verdicts`、独立尺度(conviction 1-10 Phanny 域 vs ET 0-10 域,文档明示不换算) |

## 5. 明确不做(本期)
盘中执行/下单、期权策略(用户禁)、CN/HK 季报(期标签归一化后置)、conviction 自动再校准(先攒 outcome 样本)、跨周期组合优化(MVO)。

---

## 附录 A — `ontology/phanny.py` 骨架

```python
"""Phanny 季报多空裁决本体:6 维分析 + long/strict-only 裁决 + 组合正态约束守卫。

PhannyVerdict.conviction 是 1-10 Phanny 域(强制 long/short,无 neutral),
与 EarningsVerdict(0-10,允许 no_trade)是两个独立模型两个域,不换算、不混存。
"""
from pydantic import BaseModel, Field

PHANNY_DIMENSIONS: tuple[str, ...] = (
    "fundamental",         # 基本面:一致预期/修订漂移/guidance 习惯/beat 率
    "technical",           # 技术面:价格语境/动量/支撑阻力/财报前 positioning
    "capital_flow",        # 资金面:评级动量/PT 空间/内部人/13F/主力资金
    "sentiment",           # 情绪面:社媒极性/语义事实/专家洞见/拥挤度
    "options_structure",   # 期权结构:implied move vs 历史/Gamma/skew/期限/realized-vs-implied
    "probability_odds",    # 概率赔率:预期分布/不对称/赢面×赔付/Kelly 化 sizing
)
DIRECTIONS = ("long", "short")

class DimensionRead(BaseModel):
    key: str = Field(description="必须 ∈ " + " / ".join(PHANNY_DIMENSIONS))
    score: float = Field(ge=-2, le=2)
    note_zh: str
    evidence: list[str] = Field(default_factory=list)   # dossier 接地 id,逐字抄

class PhannyVerdict(BaseModel):
    direction: str                              # ∈ {long, short};无 neutral
    conviction: float = Field(ge=1, le=10)
    size_pct: float = Field(ge=1, le=15)        # 组合占比
    dimensions: list[DimensionRead] = Field(min_length=4, max_length=6)
    expected_surprise_zh: str
    move_view_zh: str
    asymmetry_zh: str                           # 赔率不对称(必填)
    plan_zh: str                                # 财报前后进出场
    falsifiers_zh: list[str] = Field(min_length=1, max_length=4)
    sizing_rationale_zh: str
    caveat_zh: str = ""                         # 把握不足/数据缺口诚实声明
    debate_trace: list[dict] = Field(default_factory=list)  # 每轮 {model, role, objection, response}

def validate_phanny(v, *, known_ids, round1_conviction=None) -> list[str]:
    """① direction∈{long,short};② dim.key∈PHANNY_DIMENSIONS 不重复;③ evidence∈known_ids;
    ④ conviction≥7→去重锚≥6 ∧ asymmetry_zh 非空;⑤ size_pct∈[1,15];
    ⑥ 反调低守卫:round1_conviction 给定时,final<round1-2.0 视为'为收敛下调',须由
       调用方结合 ensemble_normality 复核(此处仅标 warning,engine 层据 ensemble 拒)。"""
```

## 附录 B — `phanny/distribution.py`(组合正态门 + 完整性守卫)
```python
def ensemble_normality(convictions: list[float]) -> dict:
    """{ok, mean, std, skew, exkurt, shapiro_p, n, buckets, reason}。
    门:std≥sigma_min ∧ mean∈[4,7] ∧ (scipy∧n≥8→Shapiro p≥.05 else |skew|<1∧|exkurt|<1.5)
        ∧ high(≥7)占比≥0.10。n<2→insufficient_sample。"""

def convergence_integrity(traces: list[dict]) -> list[str]:
    """逐家 final vs round1 conviction:下调>2.0 且 ensemble_normality=False→违规
    (强制回拉数据重辩,严禁降 conviction 凑收敛)。"""
```

## 附录 C — `phanny/sizing.py`(确定性 size)
```python
def size_pct(conviction, asymmetry_score, vol_pct, portfolio) -> tuple[float, str]:
    """kelly_base = conviction/10 * asymmetry_score;raw = kelly_base * (0.20/vol_pct);
    portfolio 缩放:若 sum(|weights|)>gross_cap → 按 conviction 比例缩;clip[1,15]。
    返回 (pct, 中文 rationale)。零 LLM,纯可测。"""
```

## 附录 D — `phanny_verdicts` DDL(加性幂等,镜像 earnings_verdicts + Phanny 列)
```sql
CREATE TABLE IF NOT EXISTS phanny_verdicts (
    id BIGSERIAL PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    event_date DATE NOT NULL, calendar_id BIGINT,
    version INT NOT NULL DEFAULT 1,
    direction TEXT NOT NULL CHECK (direction IN ('long','short')),  -- 无 neutral
    conviction REAL NOT NULL CHECK (conviction BETWEEN 1 AND 10),
    size_pct REAL NOT NULL CHECK (size_pct BETWEEN 1 AND 15),
    expected_move REAL,
    debate_models TEXT[],                 -- 各角色实际模型
    rounds INT,                           -- 收敛轮数
    ensemble_status TEXT,                 -- normal | insufficient_sample | deferred_low_signal
    content JSONB NOT NULL,               -- PhannyVerdict 全量(含 debate_trace)
    quality JSONB NOT NULL DEFAULT '{}',
    model TEXT, run_id TEXT, as_of DATE NOT NULL,
    outcome JSONB, outcome_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, event_date, version));
CREATE INDEX IF NOT EXISTS idx_pv_company ON phanny_verdicts(company_id, event_date DESC);
CREATE INDEX IF NOT EXISTS idx_pv_pending ON phanny_verdicts(event_date) WHERE outcome IS NULL;
```

## 附录 E/F — `engine.assemble_analysis` / `debate.debate`(角色钉扎表见 §2 Ph-P3;调用范式镜像 `earnings.build_verdict:516-533`,各角色 `with llm.pinned(role_pin): llm.complete_json(..., reasoning_effort="high")`)。

## 附录 G — 测试矩阵(全部离线 monkeypatch + seeded_db + 2099 隔离)
| 文件 | 覆盖 |
|---|---|
| test_phanny_ontology.py | schema roundtrip;validate(long-only/幻觉/调低守卫);路由存在 |
| test_phanny_distribution.py | 正态/偏态/小样本;convergence_integrity |
| test_phanny_sizing.py | 数学/clip/组合缩放 |
| test_phanny_analysis.py | 6 维/接地/effort 透传/违规重试 |
| test_phanny_debate.py | 角色钉扎/收敛/数据补强非降 conviction/trace |
| test_phanny_engine.py | 整批正态入库/竞态/诚实兜底/outcome/校准 |
| test_glm_worker.py(扩) | `_phanny_step` 打桩不发真 LLM |

## 执行策略
- Ph-P0 分布/守卫逻辑 + Ph-P3 接缝(收敛判据、反调低守卫、角色钉扎、组合入库门)主循环亲手写;
- 提示词草案、前端组件可 Workflow 扇出起草 + 对抗复核;
- 每阶段附录 G 对应测试 + 全量 pytest + ruff 再进下一阶段;
- Ph-P5 真机集中验收(美股,记录 debate_models 供校准分层);完成后对抗评审 → 修复 → 合并 → 部署(经用户确认)。
