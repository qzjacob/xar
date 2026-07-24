# PHANNY_PLAN — Phanny:Genny 选中公司的 Earnings Trade 交易方案模块(多 LLM 辩论收敛版)

> 状态:**设计定稿,待执行**。本文件为 Phanny 模块的唯一实施蓝图(kimi 撰写;未参考 glm/minimax/grok 文档)。
> Phanny 是与 Chathy / Andy / Genny / Fenny **平级**的第五大命名前端模块,顶栏位置**紧随 Genny 之后**。
> 功能:对 Genny 库(registry)内**选中的公司**,就**下一次季报**输出 earnings trade 交易方案——
> **方向(long / short / no_trade)**、**conviction 1-10(全样本呈正态分布)**、**Size 建议(组合 1-15%)**。
> 推理覆盖 基本面/技术面/资金面/情绪面/期权结构/概率赔率 六面;随后由**不同厂商 LLM** 扮演反方
> 进行 challenge + debate,直至观点收敛且**群体 conviction 分布通过正态性闸门**。
> 推理阶段一律使用各执行器**最高 reasoning effort**;算力与数据源不设上限(订阅执行器并行打满)。
> **唯一新表 `phanny_plans`**;其余全部复用既有基础设施(ET dossier/verdict、capability runs、
> TaskClass 路由、llm.pinned、subpool 并行池、fcn 期权栈、alt_signals)。任务前缀 **PH-**。

---

## 0. Context(为什么)

平台已有 ET(EARNINGS_TRADING_PLAN.md,`src/xar/research/earnings.py`):T-10 刷新 → T-3 锁定裁决
(direction ∈ {long, short, no_trade}, conviction 0-10,≥7 可操作)→ 盘后回验校准。ET 回答的是
"**该不该做这笔季报交易**";Phanny 回答的是"**以多大信念、多大仓位做,且该信念经受住多模型反方
攻击了吗**"。差异与增量:

1. **产物升级**:ET 出 verdict(方向+conviction);Phanny 出 **plan(方向 + conviction 1-10 + size 1-15%
   + 六面推理 + 辩论纪要 + 概率赔率量化)**,且**禁止**输出字面 `neutral` 或任何期权结构策略
   (straddle/iron condor 等)——方向字段只能是 `long` / `short` / `no_trade`。
2. **过程升级**:单判官 → **多 LLM 对抗制**。正方(高 reasoning effort 强模型)出初稿,**异厂商**
   反方(devil's advocate)逐条攻击,正方答辩,风险官复核,直至收敛(方向不变且 |Δconviction| ≤ 1
   连续一轮),硬上限 6 轮。
3. **群体纪律升级**:单票 verdict 无分布约束;Phanny 对**一批选中公司**的 conviction 施加
   **正态分布闸门**(见 §3)。不满足时**禁止简单调低 conviction 凑分布**——必须回到推理层补充
   数据/维度(加餐 feed)并重开辩论,直至收敛与分布**同时**达标。
4. **算力纪律升级**:推理一律 `reasoning_effort="high"`(codex `model_reasoning_effort=high`,
   models/codex_cli.py:98;`llm.complete(reasoning_effort=...)` 显式参数 models/llm.py:263),
   正方走 host 订阅执行器 pin(codex-sub / claude-opus-max),反方走 **subpool 三订阅并行**
   (GLM/Kimi/MiniMax,models/subpool.py:121 `run_parallel`)——互不阻塞、额度并行消耗。

### 现状可复用(全部 file:line 实证)

- **ET 六维 dossier 原料**:`research/earnings.py` `dossier_earnings`(:209,11 个 fail-soft section:
  指引/一致预期/修订/beat 习惯/alt 信号/情绪/论点/期权隐含波动/估值/资金面/宏观),
  `beat_stats`(:93)、`hist_move_stats`(:118)、`reaction_return`(:50);
  implied move 序列来自 `alt.options_implied_move`(providers/alt/implied_move.py)。
- **技术面**:`research/indicators.py`(动量/RSI/均线族,Company 360 已消费)。
- **资金面**:`research/flow.py`(公司页 flow 面板,dashboard.py:606-618 已并入 company_detail)。
- **期权结构深化**:vendored `src/fcn`(Fenny)`marketdata/massive.py fetch_option_chain`
  (真 IV/greeks/skew/term structure;`MASSIVE_API_KEY` config.py:74);免费路 yfinance option_chain。
- **情绪**:`social_posts.sentiment`、`expert_insights`、semantic_facts 极性(earnings.py:322-336 范式)。
- **辩论骨架**:`agents/debate.py`(_ROUNDS=2、bull/bear/risk 三角色、`_findings_brief` 接地纪律)。
- **裁决校验范式**:`ontology/earnings_events.py` `validate_verdict`(:55,证据 id 必须 verbatim
  ∈ known_ids、高信念锚数门槛、no_trade ⇒ conviction=0)——Phanny 校验器同构扩展。
- **异步任务机**:capabilities registry + runs(schedule→poll,`web/src/lib/runs.ts` `runCapability`);
  「跑 ET 裁决」按钮范式 `web/src/components/EarningsSection.tsx:42`。
- **路由/钉扎**:`models/router.py` POLICIES、`models/llm.py` `pinned()` + CLAUDE_MAX_PIN/CODEX_PIN
  (:129-139)、`models/subpool.py` 订阅并行池 + 5h quota 冷却。
- **Genny 选中公司语义**:无全局选中态;选中 = `/genny/company/:id` 路由参数(App.tsx)或
  capability 入参 `company_id` ∈ `ingestion/registry.COMPANIES`(:801,947 家,`company_by_id` :1290)。
  Phanny 支持**单票**(公司页按钮/Chathy)与**批量**(Phanny 页多选/按主题全选)两种选中方式。

---

## 1. 关键裁决(10 个压力测试点定案)

1. **方向域 = {long, short, no_trade}**。字面 `neutral` 非法(校验器拒绝);期权/衍生品结构策略
   非法(校验器扫 `content` 关键词:straddle/strangle/condor/spread/call/put 作为**策略表达**时拒绝;
   期权数据作为**证据引用**隐含波动/偏度合法)。`no_trade` ⇒ conviction=0 且 size_pct=0
   (与 ET 同纪律,ontology/earnings_events.py:80-84 范式)。
2. **conviction 1-10 整数**(方向性观点才允许 ≥1)。**单票无分布约束;批量(≥5 票)过正态闸门**。
3. **size_pct ∈ [1,15] 一位小数**,由 conviction 经**分段线性映射 + 赔率调整**得出(见 §2 公式),
   不允许 LLM 直接报 size(LLM 只出方向/conviction/赔率,size 由代码算——可审计、可复现)。
4. **收敛定义**:第 r 轮反方攻击后,正方维持同方向且 |conviction_r − conviction_{r−1}| ≤ 1
   ⇒ 收敛;否则进入下一轮,上限 6 轮。6 轮未收敛 ⇒ `quality="divergent"`,conviction 取各轮
   **最小值**并按 §3 闸门处理(不自动放行)。
5. **正态分布闸门**(批量产物验收,`check_conviction_distribution`):n≥5 时要求
   - mean ∈ [4.5, 6.5](中心在中段,**禁止全低 conviction 提交**);
   - std ∈ [1.2, 2.5](有区分度,禁止全部挤在同一档);
   - |skew| ≤ 1.0;
   - ≥20% 的票 conviction ≥ 7(高端非空)、≥20% ≤ 4(低端非空)。
   **未达标 ⇒ 不允许直接改分**。触发 **supplement loop**:对离群/低信息票补充数据(加餐:
   加拉 fcn greeks/skew、13F 变动、专家访谈、theme debate、宏观印字)并重开该票辩论(从第 1 轮),
   最多 3 个 supplement 循环;仍不达标 ⇒ 批量 `status="distribution_failed"` 原样落库并暴露原因,
   由人工决定(force 重跑或缩减批量)。所有循环痕迹写 `content.distribution_audit`。
6. **反方必须异厂商**:正方 pin(`CODEX_PIN` 或 `CLAUDE_MAX_PIN`)≠ 反方 pin 池
   (subpool GLM/Kimi/MiniMax 轮换,models/subpool.py:44 `provider_pins`),且同一票的
   相邻两轮反方不同厂商。风险官固定走与正方**不同**的订阅。角色-厂商映射记录进
   `content.debate[].model`,全程可审计。
7. **锁定语义与 ET 对齐**:INSERT 即锁,行不可变;同一 (company_id, event_date) 重跑唯一合法触发
   = 人工 `force=true`(version+1,earnings.py:542-558 同款原子 bump)。盘后回验**复用**
   `earnings.score_outcomes`(:602)的 reaction 口径,Phanny 只回填 `outcome` jsonb
   (direction_hit / size_weighted_pnl_pct)。
8. **与 ET 的关系**:Phanny **消费** ET dossier(直接函数调用,跨模块私有导入是 house style,
   EARNINGS_TRADING_PLAN.md:106),不复写其存储;若该公司当期已有 ET verdict,作为
   一维证据(grounded id `earnings_verdict:cid:date`)进入 dossier;ET 不存在不阻塞。
9. **推理 effort 纪律**:所有 Phanny LLM 调用显式 `reasoning_effort="high"`、`max_tokens` ≥ 4000;
   codex 走 `codex_effort` 高档;subpool 各侧不受 token 预算截断(budget 检查对订阅计费天然放行,
   models/llm.py:317)。**不做动态降级**——router 动态路由(router.py:170)在 Phanny 上下文内
   经 pin 链绕过。
10. **模块位置**:前端顶栏顺序 Chathy | Andy | Genny | **Phanny** | Fenny | Romy(+ Jarvy 右侧),
    即 `web/src/lib/modules.ts` MODULES 数组中插于 genny 之后;后端直连路由
    `/api/phanny/*` 注册于 SPA catch-all 之前(app.py:1016 前)。

---

## 2. 方案计算(可复现公式)

**赔率与概率**:p_beat 由三源融合——beat 习惯(beat_stats 命中率)、估计修订漂移方向与幅度
(estimate_series delta 标准化)、implied move vs 历史 |move| 比(implied/hist 比值高 ⇒ 预期已 priced)。
odds = 预期 reaction(implied move × 方向一致性折扣) / 预期下行(历史 miss 时平均 reaction)。
**Size 映射**(代码侧,非 LLM):

```
base = 1 + (conviction - 1) * (14 / 9)          # 1→1%, 10→15% 线性
edge_adj = clip(odds / 2.0, 0.5, 1.25)          # 赔率调节 ±
size_pct = round1(clip(base * edge_adj, 1, 15)) # 终值,1 位小数
```

`no_trade` ⇒ size_pct=0,不进组合汇总。批量视图给出 Σsize 与重叠主题敞口告警(同主题 Σ>40% 亮黄灯)。

---

## 3. 分阶段(每阶段 pytest+ruff 独立绿;零 DDL 除 PH-P0 一表)

### PH-P0 — 本体 + 路由 + DDL(零网络)

**新建 `src/xar/ontology/phanny.py`**:
- `PHANNY_DIMENSIONS`(6 维:fundamentals / technicals / flows / sentiment / options_structure / odds),
  `DIRECTIONS=("long","short","no_trade")`;
- `PhannyPlan`(Pydantic,兼作 LLM 结构化输出 schema):company_id / event_date / direction /
  conviction 1-10 / size_pct(代码回填,LLM 输出恒 0)/ p_beat / odds / dimensions[6]
  (score -2..+2 + note_zh + evidence ids)/ debate[](round, side, model, text, attack_points)/
  converged / rounds / quality / distribution_audit;
- `validate_plan(p, *, known_ids) -> list[str]`:证据 id verbatim ∈ known_ids;方向域非法拒
  (含字面 neutral 拒);期权策略关键词拒;conviction≥7 ⇒ ≥6 锚 + asymmetry 必填;
  no_trade ⇒ conviction=0 ∧ size=0;维度缺项拒。
- `check_conviction_distribution(plans) -> DistributionReport`(§1.5 五规则,纯函数)。

**修改**:
- `src/xar/models/router.py`:`PHANNY_REASON = "phanny_reason"`、`PHANNY_DEBATE = "phanny_debate"`
  + 两条 POLICIES(STRONG / TOKEN / "normal",注释:host 由 pinned 提级订阅执行器)。
- `src/xar/storage/schema.sql` 底部加性幂等:`phanny_plans(company_id, event_date, version,
  direction, conviction, size_pct, content jsonb, quality, model, run_id, as_of, outcome jsonb,
  outcome_at, PRIMARY KEY(company_id, event_date, version))`。
- `src/xar/config.py`:`phanny_debate_max_rounds=6`、`phanny_distribution_min_n=5`、
  `phanny_supplement_max_loops=3`、`phanny_size_max_pct=15.0`。

**测试 `tests/test_phanny_ontology.py`**:schema roundtrip;validate 六规则(含 neutral 字面拒/
期权策略拒);分布闸门正反例(全低 conviction 必失败;均匀同分必失败;正态样本通过)。

### PH-P1 — 六面 dossier(零 LLM)

**新建 `src/xar/research/phanny.py`**:`dossier_phanny(company_id) -> {text, known_ids, panel}`,
在 `earnings.dossier_earnings` 11 section 基础上补齐 ET 未覆盖的三面:
- **技术面**:`research/indicators.py` 动量/RSI/均线偏离 → section;
- **资金面**:`research/flow.py` 面板 + 13F holdings 变动(dashboard.py:606 已有原料);
- **期权结构深化**:fcn `fetch_option_chain`(或 yfinance 兜底)取 skew(25Δ RR)、term structure、
  IV rank;implied/hist move 比与 run-up 斜率;
- **概率赔率**:§2 公式算出 p_beat / odds 写入 panel(供 LLM 引用,不由 LLM 拍)。
全部 section fail-soft,grounded id 前缀规范沿用(`alt:`/`estimate:`/`theme_debate:`/新增
`tech:`/`flow:`/`opt:`)。

**测试 `tests/test_phanny_dossier.py`**:打桩数据源,六 section 齐出、known_ids 覆盖、
缺数据降级不炸。

### PH-P2 — 推理 + 多 LLM 辩论收敛

**新建 `src/xar/agents/phanny_debate.py`**:
- `reason_initial(dossier) -> PhannyPlan`:`llm.pinned(CODEX_PIN 或 CLAUDE_MAX_PIN 择优,
  earnings.py:460 `_preferred_pin` 同款)+ `reasoning_effort="high"` + complete_json 结构化输出,
  ≤2 次 validate 重试(earnings.py:518-533 范式);
- `challenge(plan, dossier, side_model) -> attacks`:**反方异厂商**(subpool provider_pins 轮换,
  round-robin 游标保证相邻轮不同厂商),effort=high,输出结构化攻击点(每条须引用 dossier id);
- `rebut(plan, attacks) -> plan'`(正方,可改分须附理由);`risk_review(plan')`(风险官,
  第三家厂商);
- `run_debate(company_id) -> PhannyPlan`:循环至 §1.4 收敛或 6 轮,逐轮落 `content.debate`;
  INSERT 即锁,force 原子 version+1;judge_due 式批量入口 `run_batch(company_ids)`。
- worker 接线:模块级 `_phanny_step()` 在 GLM pin/quota 门之外(`_research_audit_step` 同款,
  glm_worker.py:330 范式),`_due("phanny_plans", 24h)` 扫 T-3 窗内选中公司。

**测试 `tests/test_phanny_debate.py`**:全程打桩 llm;收敛路径(2 轮停)、发散路径(6 轮
quality=divergent)、异厂商断言(相邻轮 model 不同)、force version+1。

### PH-P3 — 分布闸门 + supplement loop

`research/phanny.py` 增 `finalize_batch(plans) -> BatchResult`:跑 `check_conviction_distribution`;
未达标 → 对「低信息票」(锚数最少/辩论轮数最多/离群)构造 supplement feed(加拉 fcn greeks、
expert_insights、macro_bridge 印字、theme debates)并重开这些票的辩论(PH-P2 全程),
最多 `phanny_supplement_max_loops=3` 轮;**任何循环禁止直接改 conviction**,改分只能来自
重开辩论的 LLM 输出。全程 `distribution_audit` 留痕。

**测试 `tests/test_phanny_distribution.py`**:构造偏斜样本 → 触发 supplement(打桩) →
收敛后达标;3 轮仍失败 → status=distribution_failed 且分数未被人工改动(与重开前逐票对比,
仅允许来自新辩论输出的变化)。

### PH-P4 — 能力注册 + API + worker + 盘后回填

- `capabilities/registry.py`:新增 `build_phanny_plan`(build/slow, chathy=False)与读工具
  `phanny_plan`(chathy=True,`refresh=true` 走 runs.launch,earnings_verdict :184-206 同款);
- `src/xar/api/app.py`(catch-all 前):`GET /api/phanny/plans?theme=`、`GET /api/phanny/plan/{cid}`、
  `POST /api/phanny/plan/{cid}/run`、`POST /api/phanny/batch/run`(body=company_ids)、
  `GET /api/phanny/distribution`(批量分布报告);
- Genny 公司页:`EarningsSection` 旁加「跑 Phanny」按钮(runCapability 轮询范式,
  EarningsSection.tsx:42 同款);
- 盘后:`score_outcomes` 完成后回填 `phanny_plans.outcome`(direction_hit、reaction、
  size_weighted_pnl_pct),calibration 视图新增 Phanny bucket(earnings.py:670 同款)。

**测试 `tests/test_phanny_api.py` + `test_phanny_pipeline.py`**:路由契约、能力注册、
回填空挂兜底。

### PH-P5 — 前端模块 `/phanny`

- `web/src/lib/modules.ts`:`ModuleKey` 加 `"phanny"`,MODULES 数组**插于 genny 之后**
  (`{key:"phanny", label:"Phanny", cn:"季报交易", route:"/phanny", icon: Target,
  match:(p)=>p.startsWith("/phanny")}`);
- `web/src/App.tsx`:`const PhannyApp = lazy(() => import("./pages/phanny/PhannyApp"))`,
  `<Route path="/phanny/*" element={…Suspense…}>` 置于 catch-all 前(App.tsx:97-101 范式);
- `web/src/pages/phanny/PhannyApp.tsx`:ModuleShell + SidebarFrame + 嵌套路由(FennyApp.tsx:11-46
  同款):**总览**(选中池表格:公司/财报日/方向 chip/conviction 条形/size%/收敛状态/辩论轮数)、
  **单票页**(六维雷达 + 概率赔率卡 + implied-vs-hist 图(PlotlyChart 共享懒分片)+ 辩论纪要
  时间线 + 锁后漂移 chips)、**分布页**(conviction 直方图 + 正态曲线叠加 + 闸门五灯 +
  distribution_failed 原因);
- `web/src/lib/phanny.ts`:API client + runCapability 轮询;主题:共享 accent ramp
  (theme.css:45-59 约定,不新增 hue)。

---

## 4. 验收

1. `pytest tests/test_phanny_*.py` 全绿 + 既有套件不回归;`ruff check` clean。
2. docker 运行:Genny 公司页「跑 Phanny」→ 轮询 → 方案落库并在 `/phanny` 单票页可见;
   批量 ≥5 票跑完后分布页五灯全绿(或 distribution_failed 原因展示)。
3. 抽查 content.debate:相邻轮反方厂商不同;conviction 变化仅在辩论轮次内发生;
   无字面 neutral、无期权策略表达。
