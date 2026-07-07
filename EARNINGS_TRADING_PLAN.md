# EARNINGS_TRADING_PLAN — ET:围绕季报的 360° 事件交易投研系统

> 状态:**设计定稿,待执行**。美股专属(~40 家期权流动性好的旗舰),conviction 0-10(≥7 可操作),
> T-10 起每日数据刷新、T-3 生成并**锁定**裁决(仅 `--force` 重生成),T+1..T+5 最小盘后回验做校准。
> **唯一新表 `earnings_verdicts`**;其余全部复用既有基础设施(alt_signals / estimates / event_calendar /
> analyst_ratings / thesis 层 / TaskClass 路由 / glm_worker 节拍)。任务前缀 **ET-**。

---

## 0. Context(为什么)

把 XAR 从"论点跟踪"推进到"**事件交易决策支持**":对临近季报的美股标的,组装
**指引 / 一致预期 / beat-and-raise 惯例 / 数据追踪(券商研报·专家访谈·另类信号)/ 期权隐含波动 / 情绪 / 资金 / 估值**
的季报 dossier,由 LLM 判官输出 **做多/做空/不交易 + conviction 0-10**。

用户的 ServiceNow 例即验收画像:指引(公司给的 guide)+ 预期(卖方一致预期与修订方向)+ 惯例
(NOW 历史上惯常 beat-and-raise,beat 率与幅度)+ 数据追踪(券商/行业报告、专家/经销商访谈、alt 信号)
+ 期权隐含股价波动(ATM straddle 定价的 |move| vs 历史财报日实际 |move|)+ 估值(安全垫/拥挤度)
→ 综合判断:「季报前做多 @7/10」。

**用户已确认的四项决策**:
1. **仅美股**(期权链 + 分析师预期 yfinance 免费可得;A股无个股期权、CN 后置);
2. **conviction 0-10、≥7 = 可行动**(<7 仅观察);
3. **T-10 起每日刷新,T-3 生成正式判断并锁定**(之后数据继续刷新但判断不变,盘后记录实际结果);
4. **本期包含最小盘后回验**(实际 surprise + 财报反应日走势 vs 判断方向 → hit/miss 战绩,供 conviction 校准)。

### 现状缺口(4 角侦察 recon + gap 分析,全部 file:line 实证)

**可直接复用(无需新建)**:
- `estimates` 表(schema.sql:204)——`as_of DATE` 在唯一键里 = **PIT 修订轴已内建**;writer:`yahoo.pull_analyst`
  (yahoo.py:175,'0q/+1q/0y/+1y' 相对期 + 评级 + 目标价)、`finnhub.pull_estimates`、`fmp.pull_estimates`;
  reader:`structured.estimate_series`;修订信号:`kg/signals.py:36 _estimate_revisions`(最近 2 快照 delta)。
- `analyst_ratings` 表(schema.sql:224)——strong_buy..strong_sell 计数 + pt_mean/high/low;美股 writer 已接;
  **尚无任何分析型消费者**(评级动量/PT-vs-price 是空白地,ET-P2 首次消费)。
- `event_calendar` 表(schema.sql:314)——**yahoo.pull_calendar(yahoo.py:246)是全库唯一写入历史财报行的
  provider,meta 已带 `{eps_estimate, reported_eps, surprise_pct}`(~8 季)**,但 yahoo 不在
  `daily_enabled_sources`(config.py:153)——只有手动 `xar pull` 才刷新 → ET-P1 提进 worker。
- `alt_signals` 表(schema.sql:595,PIT `observed_at`)+ `AltSignalSpec` 注册表(ontology/altdata.py)——
  书面合同「新增追踪器 = 加一条 spec + 一个 provider,零下游改动」(z-score/health_v2 支柱芯片/
  sync_alt_events→semantic_facts/API 全自动)。
- 论点层:`thesis.dossier()`(research/thesis.py:41,接地 id 纪律的组装范式)、`CompanyThesis` +
  `validate_thesis`(宁缺毋滥拒绝式校验范式)、`thesis_health.health_v3`、debates lean。
- LLM 路由:`TaskClass` + `POLICIES`(加 EARNINGS_JUDGE = 2 行);深度研究订阅执行器
  `claude-opus-max`(agent_sdk)/ `codex-sub`(codex_cli)+ `llm.CLAUDE_MAX_PIN`/`llm.CODEX_PIN`;
  `glm_worker._research_audit_step`(glm_worker.py:330)= **GLM pin/quota 门之外的每日强模型阶段模板**。
- 情绪:`social_posts.sentiment`(词表含 beat/guidance)、twitter 1h、`expert_insights`
  (提示词已抽"预期差 vs 共识");guidance 习惯底座:`kg_events.time_orientation='forward_looking'`
  + `resolution`(hit|miss,kg/resolve_claims.py)。
- 价格:`prices`(2y yahoo);`backtest/catalyst_returns._series`(本地优先 + yfinance 兜底取价)。

**缺失(本计划要建的)**:
- **期权/隐含波动:src/xar 零期权数据**(完整期权栈在 vendored `src/fcn` Fenny——
  `fcn/marketdata/massive.py fetch_option_chain` 有真 IV/greeks,`MASSIVE_API_KEY` 已在 config.py:74,
  但是付费可选路;yfinance `Ticker.option_chain` 未被任何代码使用 = 免费主路)。
- beat/miss 习惯聚合(原料在 event_calendar yahoo 行 meta 里,无人聚合)。
- 财报日历史 |move| 统计、IV run-up 序列、realized-vs-implied 比较(全部无)。
- 裁决存储 + 锁定 + 盘后闭环(全部无)。
- **顺带必修缝(recon 发现的真 bug)**:`structured.upsert_calendar` UPDATE 分支 `meta=%s` **整体覆盖**——
  yahoo 重拉会抹掉 finnhub 写入的 `hour`(amc/bmo)→ 改 jsonb 合并(加性,全源受益)。

---

## 1. 关键裁决(8 个压力测试点定案)

1. **锁定语义:INSERT 即锁**。无 locked_at 列(`created_at` 即锁定时刻);行不可变,仅 `outcome/outcome_at`
   盘后回填。新版本**唯一合法触发 = 人工 `--force`**(version+1)。T-3 后数据剧变**不自动重生成**——
   前端/面板实时计算「**锁后漂移 chips**」(锁定至今 implied move 变化、estimate 新修订数、新语义事实数),
   让人看到陈旧度但保住校准诚实性(裁决可反复改 = 事后美化,战绩全废)。
   财报**改期 ≥2 天**:旧行 outcome 盖 `{"status":"event_moved","new_date":…}`,新日期是新 `event_date` 键
   → 新裁决从 version 1 起;取消 → `{"status":"cancelled"}`。
2. **implied move 的 PIT 存储**:`alt_signals` 唯一键 `(signal_key, company, '', period_end)` →
   **`period_end` = 快照日(观察日),不是财报日**——IV 本来就是"当日市场可观察量",经济期=观察日语义正确,
   且天然不撞键;`meta={"earnings_date","expiry","spot","atm_iv","straddle_mid","dte"}`。
   dossier 按 `meta->>'earnings_date'` 过滤取**本事件的 IV run-up 序列**(T-10..T-1 每日点);
   盘后 realized-vs-implied 取事件前最后一个快照。z-score 机器零改动可用
   (cadence="daily", min_history=5, **good_when=None** → contribution 0,纯注意力旗标);
   `sync_alt_events` 的 |z|≥2 → 中性 kg_event(「期权市场定价异常波动」)顺带成立。
3. **Universe = 策展代码即真相**(debates.py `DEBATE_SEEDS` 同构)。`EARNINGS_UNIVERSE: tuple[str,...]`
   ~40 个 registry company_id;起点 = 16 个美股 debate 旗舰
   (now/crm/snow/nvidia/amd/tsmc/coherent/googl/meta/wmt/cost/mcd/cmg/tsla_hum/rklb_spa/asts_spa),
   实施时补充期权流动性好的主题名到 ~40。派生方案(从 registry 自动推)被否:期权流动性不在 registry 里,
   反正要人工判断——策展列表可测试、可 review。测试强制:每 id ∈ COMPANIES ∧ 有无后缀 US ticker ∧ 无重复。
4. **Worker 双道**:
   - 零 LLM 刷新 → `_pull_fresh` 加 `_run("earnings_watch", 6*3600, earnings.refresh_window)`
     (轮转游标扫全 universe 日历 + 窗口内名字拉 analyst/implied move,单司 try/except 不沉轮,gangtise 循环模式);
   - 裁决/回验 → 模块级 `_earnings_step()` 在 **GLM pin/quota 门之外**(`_research_audit_step` 同款,
     worker 单测可整体打桩):`_due("earnings_verdicts", 24h)→judge_due()`;`_due("earnings_outcomes", 12h)→score_outcomes()`。
   - 路由:`TaskClass.EARNINGS_JUDGE = RoutePolicy(STRONG, TOKEN, "normal")`(AUDIT 同款);
     `build_verdict` 内部在 **host** 上包 `llm.pinned(择优)`——`codex_enabled+codex_cli.available()` →
     CODEX_PIN,否则 `anthropic_max_enabled+agentsdk.available()` → CLAUDE_MAX_PIN,都不可用 → 裸任务路由
     (docker 落 deepseek-v4-pro token)。量 ~1-3 次/天,成本有界。
     **config `earnings_verdict_host_only=False`**:置 True 时 docker worker 对裁决返回
     `{"status":"deferred_host"}`,host cron 跑 `xar earnings judge --due`。
5. **盘后口径(session 与 reaction)**:session 三层来源——finnhub 日历 meta **已带 `hour`**
   (amc/bmo/dmh,providers/finnhub.py:229)、yahoo `get_earnings_dates` 时间戳(ET-P1 起写 meta.session:
   ≥16 点→amc,<9:30→bmo)、都缺时用 max-|move| 推断(标 `"session":"inferred"`)。
   reaction return:**amc → close(D+1)/close(D)−1;bmo → close(D)/close(D−1)−1**(D = 首个 ≥ 事件日的交易日)。
   `direction_hit = sign(reaction) == 方向`;**no_trade 记 "abstain" 不进 hit-rate**。
   改期:T+2 起在 occurred earnings 里按 **±3 天窗**对齐;event_date+5 天仍无价格/无事件 →
   `price_missing` / `event_moved` 收尾,**绝不无限挂起**。
6. **去重复用清单(砍掉的自建件)**:
   - beat/miss 习惯**不建新信号、不接 finnhub `/stock/earnings`**(free tier 仅 4 季)——直读
     `event_calendar` yahoo occurred 行 meta(`status='occurred' AND meta->>'surprise_pct' IS NOT NULL`,
     ~8 季;ET-P1 把 yahoo calendar 提进 worker 后自动保鲜);
   - 修订漂移 = `structured.estimate_series`(yahoo '0q' 相对期,as_of 轴天然 PIT);
   - 历史财报日波动 = 复用 `backtest/catalyst_returns._series`(跨模块私有导入是 house style);
   - 论点状态 = `thesis.latest` + `thesis_health.health_v3`;情绪 = social_posts/semantic_facts/expert_insights 直查。
7. **yfinance 节流纪律**:只打窗口内名字(财报季峰值 ~15-40 家),每家每日 ≈4 次调用
   (earnings_dates 1 + fast_info 1 + options 1 + option_chain 1;analyst 另 2-3);
   模块级 `_RATE_MIN_INTERVAL=1.5s`(finnhub._paced_get 同款)→ 峰值全 universe ≈4-6 分钟/日。
8. **conviction 尺度隔离**:`EarningsVerdict.conviction` 是 **0-10 事件交易尺度**,与
   `CompanyThesis.conviction`(1-5 论点尺度)**是两个模型两个域**,不换算、不混存。

---

## 2. 分阶段(每阶段 pytest+ruff 独立绿;零 DDL 除 ET-P0 一表)

### ET-P0 — 本体 + 路由 + DDL(零网络)

**新建 `src/xar/ontology/earnings_events.py`**(骨架见附录 A):
- `EARNINGS_DIMENSIONS`(8 维分析框架)、`DIRECTIONS=("long","short","no_trade")`;
- `DimensionRead`(score -2..+2 / note_zh / evidence ids)、`EarningsVerdict`(Pydantic,兼作 LLM 结构化输出 schema);
- `validate_verdict(v, *, known_ids) -> list[str]`(五规则,附录 A);
- `EARNINGS_UNIVERSE`(~40 策展)+ `earnings_universe(cap=None) -> list[dict]`(∩ registry)。

**修改**:
- `src/xar/models/router.py`:`EARNINGS_JUDGE = "earnings_judge"` + `POLICIES[…] = RoutePolicy(Capability.STRONG, Billing.TOKEN.value, "normal")`(2 行;注释同 AUDIT:host 由 pinned 提级订阅执行器)。
- `src/xar/storage/schema.sql` 底部加性幂等:`earnings_verdicts`(完整 DDL 见附录 B)。
- `src/xar/config.py`:
  ```python
  earnings_watch_days: int = 10        # 观察窗:财报前 N 天进入每日刷新
  earnings_verdict_lead_days: int = 3  # T-N 生成正式裁决
  earnings_outcome_max_days: int = 5   # 盘后回验兜底收尾天数
  earnings_universe_cap: int = 50      # universe 截断帽
  earnings_verdict_host_only: bool = False  # True → docker worker 裁决 deferred,host 专跑
  ```

**测试 `tests/test_earnings_ontology.py`**:universe ⊆ registry + US ticker 存在 + 无重复;
EarningsVerdict schema roundtrip;validate 五规则(高信念缺锚拒/no_trade conviction≠0 拒/幻觉 id 拒/
非法 dimension 拒/asymmetry 缺失拒);`EARNINGS_JUDGE ∈ POLICIES` 且 STRONG/token。

### ET-P1 — 数据获取(零 LLM)

- **新建 `src/xar/providers/alt/implied_move.py`**(骨架见附录 C;alt 派发合同:模块名=source,
  自动进 `ingestion/alt.pull_all` 与 worker 6h alt 节拍):
  窗口内(≤ earnings_watch_days)universe 名字 → `yahoo._handle` 复用 Ticker → `tk.options` 选
  **首个 ≥ 财报反应日的 expiry** → `tk.option_chain(expiry)` → spot=`tk.fast_info["last_price"]` →
  ATM straddle mid(bid/ask 中价,双零回落 lastPrice)→
  `upsert_signal("alt.options_implied_move", company_id, period_end=今天, value=straddle/spot, unit="ratio")`;
  1.5s 模块级节流;可选 `_massive_pull()` 分支(`massive_api_key` 已在 config.py:74,
  `fcn.marketdata.massive` 有真 IV,armed 时优先)。
- **修改 `src/xar/ontology/altdata.py`**:
  `_S("alt.options_implied_move", "期权隐含波动(财报)", …, "daily", "ratio", "company", None, ("valuation",), "implied_move", min_history=5)`;
  `AltBinding` 加 `options_ticker: str | None = None` + `signals()` 分支;`bindings()` 内由
  `EARNINGS_UNIVERSE` 派生绑定 → 公司页支柱芯片/快照零下游改动。
- **修改 `src/xar/storage/structured.py`**:`upsert_calendar` UPDATE 分支
  `meta = COALESCE(event_calendar.meta,'{}'::jsonb) || %s::jsonb`(**修 meta 覆盖 bug,保 finnhub hour**)。
- **修改 `src/xar/providers/yahoo.py` `pull_calendar`**:earnings meta 增写 `"session"`
  (行时间戳 ≥16 点→"amc",<9:30→"bmo",无时间不写)。
- **新建 `src/xar/research/earnings.py`(第一批,零 LLM;函数规格见附录 D)**:
  `reaction_return(cid, event_date, session)`(复用 catalyst_returns._series)、
  `beat_stats(cid, n=8)`(event_calendar yahoo occurred meta → beat_rate/streak/avg_abs_surprise/详情行)、
  `hist_move_stats(cid, n=8)`(历史财报日 |reaction| 均值/最大/明细)、
  `refresh_window()`(轮转游标全 universe `yahoo.pull_calendar`;窗口内名字加 `yahoo.pull_analyst` +
  `alt.pull_source("implied_move")`;kvstate cursor `earnings_watch`)。

**测试**:`tests/test_implied_move.py`(假 tk:straddle 数学/expiry 选择/period_end=今日/同日重拉幂等/
无 expiry 优雅跳过);`tests/test_earnings_pipeline.py`(seeded_db,2099 隔离:meta jsonb 合并保 hour、
beat_stats 3/4=0.75、amc/bmo reaction 数学用合成 prices)。

### ET-P2 — 季报 dossier 组装器(零 LLM)

`research/earnings.py` 增 **`dossier_earnings(cid, event) -> {text, known_ids, panel, as_of, event_date, n_facts} | None`**
(`panel` = 结构化 dict 供 API/CLI 复用;`text` 镜像 thesis.dossier 的接地 id 纪律)。

面板节(11 节,每节独立 try/except 单节失败不沉整包,thesis.dossier 同款):
| 节 | 数据源 | 关键量 |
|---|---|---|
| 事件头 | event_calendar | date / session / days_to / calendar_id |
| 预期设定 | estimates('0q' eps/rev)+ estimate_series | 最新一致预期 + **90 天修订漂移**(方向/幅度/n_analysts) |
| beat 习惯 | beat_stats | beat 率 / 连续 beat 季数 / 平均 |surprise| |
| guidance 习惯 | kg_events forward_looking ∧ guidance 类 + resolution | 惯常 raise/cut + 兑现 hit/miss 率(= ServiceNow 例的「惯例」维度) |
| 评级动量 | analyst_ratings 最近 2 快照 | 上调/下调 delta + pt_mean vs 现价空间 |
| implied vs 历史 | alt.options_implied_move + hist_move_stats | 最新 implied move、窗内 run-up delta、vs 历史平均实际 |move| 的溢价/折价 |
| 情绪 14d | social_posts / semantic_facts / expert_insights | 均值极性 / 事实极性计数 / kept 洞见摘录 |
| alt 快照 | thesis_signals.signal_snapshot | 全部绑定信号 z 与方向 |
| 论点状态 | thesis.latest + health_v3 + debates | stance/conviction(1-5 域)/challenged 支柱/争论 lean |
| 价格语境 | prices | 上季财报以来收益 / 20d 实现波动 |
| 覆盖缺口 | 诚实声明 | 如「无买方持仓变动数据」「一致预期无卖方逐家明细」 |

id 约定:复用 `[event:] [insight:] [estimate:cid:metric] [fundamental:]`,新增
`[calendar:<id>] [alt:<key>:<period_end>] [ratings:<as_of>] [price:<cid>:<win>]`
(validator 只做 known_ids 精确匹配,**无需扩 thesis 的 EVIDENCE_KINDS 词表**——两套 schema 独立)。

**测试 `tests/test_earnings_dossier.py`**:2099 合成全套行 → 各节出现、known_ids 含新 kind、
数字正确(修订漂移符号/beat 率/implied 溢价)、缺数据节优雅缺席。

### ET-P3 — 裁决引擎

`research/earnings.py` 增(函数规格附录 D,系统提示词草案附录 E):
```python
def latest_verdict(cid, event_date) -> dict | None
def build_verdict(cid, *, event: dict | None = None, force=False, run_id=None) -> dict
    # 解析下一次财报(upcoming_calendar)→ 已有裁决且非 force → {"status":"skipped"}(锁定)
    # → host_only 且本进程无订阅执行器 → {"status":"deferred_host"}
    # → dossier_earnings → llm.complete_json(prompt, EarningsVerdict, system=_SYSTEM_EARNINGS,
    #    task=TaskClass.EARNINGS_JUDGE, node="earnings_judge", run_id=run_id, max_tokens=6000)
    #    (host 上包 llm.pinned(_preferred_pin());违规带清单重试一次,仍违规 → rejected 不入库)
    # → INSERT earnings_verdicts(version 递增;expected_move=裁决时点最新 implied move)
    # → {"status":"built", "direction","conviction","version","model"}
def judge_due(*, force=False) -> dict   # 窗口 [today, today+lead_days] 内无裁决的 universe 事件逐个 build
def _preferred_pin() -> tuple[str,...] | None   # codex 可用→CODEX_PIN;claude-max 可用→CLAUDE_MAX_PIN;否则 None
```
`_SYSTEM_EARNINGS`(中文,thesis._SYSTEM 纪律同款,全文草案附录 E):证据 id 逐字抄、
conviction 与证据密度耦合、**≥7 必须写明赔率不对称(asymmetry_zh)与盘前证伪条件(falsifiers_zh)**、
没有 edge 就 no_trade(宁缺毋滥)、区分「预期差」与「好公司」(好公司+高预期=没有交易)。
quality dict:锚数/维度覆盖数/数字接地率。

**测试 `tests/test_earnings_verdict.py`**(mock dossier + mock complete_json,test_thesis_build 范式):
v1 入库含 expected_move;重跑 skipped(锁);force→v2;幻觉 id→rejected 不入库;host_only+无执行器→deferred。

### ET-P4 — 编排 + 结果闭环 + CLI/API

- **`src/xar/orchestration/glm_worker.py`**(wiring diff 见附录 F):
  `_pull_fresh` 加 `_run("earnings_watch", 6*3600, lambda: earnings.refresh_window())`;
  `run_once` 加 `out["earnings"] = _earnings_step()`(**模块级、GLM pin/quota 门外**):
  `_due("earnings_verdicts", 24*3600) → judge_due()`;`_due("earnings_outcomes", 12*3600) → score_outcomes()`。
- **`research/earnings.py`** 增:
  `score_outcomes() -> dict`(`outcome IS NULL AND event_date < today` 的裁决 → ±3 天窗对齐 occurred
  earnings → actual surprise(calendar meta)+ `reaction_return` + realized_vs_implied + direction_hit
  → UPDATE outcome/outcome_at;event_date+outcome_max_days 兜底收尾);
  `calibration() -> dict`(conviction 分桶 [0-3/4-6/7-8/9-10] × hit-rate × 平均 reaction;≥7 桶单列;
  按 model 分层可选)。
- **`src/xar/cli.py`**:`earnings_app`(thesis_app 模式)——
  `xar earnings watch`(窗口队列表格:days_to/implied/beat 率/裁决状态)/ `panel CID` / 
  `judge [CID|--due] [--force]` / `outcomes` / `calibration`。
- **API**:`api/ops.py` 加 `earnings()`(队列+近期裁决+校准);`api/app.py` 加
  `GET /api/ops/earnings`、`POST /api/ops/earnings/{cid}/judge`(BackgroundTasks);
  `api/dashboard.py` 加 `_earnings_block(cid)`(下一事件 panel 摘要 + 最新裁决 + **锁后漂移 chips** +
  近 4 次 outcome)挂进 `company_detail` 返回 dict。

**测试**:`tests/test_glm_worker.py` 扩(`_earnings_step` 打桩不发真 LLM);
`tests/test_earnings_outcomes.py`(合成价格 hit/miss、amc/bmo 两口径、event_moved、price_missing 收尾、
no_trade=abstain、calibration 分桶)。

### ET-P5 — 前端 + 文档 + 真机 E2E

- 新建 `web/src/components/EarningsSection.tsx` + `web/src/types-earnings.ts`(规格见附录 H):
  裁决卡(方向 + conviction 表针,**≥7 高亮**)、implied vs 历史 |move| 对比条、beat 习惯序列格、
  财报倒计时、锁后漂移 chips、outcome hit/miss 历史;插 `CompanyPage.tsx` `<ThesisSection/>` 之后;
  ops 控制台加校准小卡。
- `DESIGN.md` 新增 **§5.14 围绕季报的事件交易投研编排(As-Built)** + README 一段。
- **真机 smoke(host,CN egress 无关——纯美股)**:选 10 天内出财报的 universe 名字 →
  `xar earnings watch → panel → judge`(真 LLM:CODEX/CLAUDE-MAX 择优,记录实际 model)→
  `GET /api/ui/company/{cid}` earnings block + 前端渲染 → 财报后 `xar earnings outcomes` 验证闭环。
- 全量 pytest+ruff;`xar init` 幂等(新 DDL);**独立对抗代码评审**(house 惯例)后合并;docker 重建部署(经用户确认)。

---

## 3. 成本纪律

| 项 | 频率 | 约束 |
|---|---|---|
| 裁决 LLM(EARNINGS_JUDGE) | 1-3 次/天(财报季峰值 ~5) | 订阅执行器 $0 或 deepseek token ~$0.1-0.5/次 |
| yfinance(日历+analyst+期权链) | 窗口内名字 ~4-7 调用/家/日 | 1.5s 模块级节流;峰值 ~200 请求/日 ≈4-6 分钟 |
| 其余(dossier/beat/outcome) | 每日 | 全零 LLM(纯 SQL/计算) |

## 4. 风险

| 风险 | 缓解 |
|---|---|
| yfinance 期权报价质量(盘后零 bid/IV 粗) | straddle 用 mid 回落 lastPrice;worker 6h 节拍覆盖盘中;massive 路径(key 已在 config)一行 arm |
| yfinance 限流/接口破版 | 1.5s 节流 + 单司单节非致命(house style);watch 降级不沉轮 |
| 财报日期跨源 ±1 天 / TBD / 改期 | dedup 双行都在窗内被刷;outcome 按 ±3 天对齐 occurred;event_moved 盖章不改锁行 |
| keyless 一致预期无真 PIT 全修订史 | T-10 起每日快照令 estimates as_of 轴自建修订史;dossier 诚实标注覆盖缺口 |
| LLM 过度自信 | validate 证据密度门(≥7 需 ≥6 锚 + 不对称声明)+ INSERT 即锁防事后改 + calibration 分桶回看 |
| docker 无 codex/claude → 裁决落 deepseek | `model` 列记录实际模型(校准可分层);`earnings_verdict_host_only` 可改 host 专跑 |
| implied move 首季历史稀疏 | dossier 用原始值+窗内 delta,day 1 即可用;z-chips 到 min_history=5 自然出现 |
| 期标签混乱(库内 4 种约定) | US-only + yahoo '0q' 相对期 + vendor 预算 surprise **完全绕开归一化**;universe 测试锁 US ticker |

## 5. 明确不做(本期)

CN/HK 财报事件(期标签归一化是长杆,后置)、期权 IV 曲面/skew/期限结构、盘中执行/下单、
finnhub `/stock/earnings`(free 4 季 < yahoo 8 季)、conviction 自动再校准(先攒 outcome 样本)、
美股日频资金流(用期权 volume/OI 做代理并标注缺口)、历史估值分位(标注为覆盖缺口)。

---

---

# 附录(可执行细节)

## 附录 A — `ontology/earnings_events.py` 骨架

```python
"""季报事件交易本体:8 维分析框架 + 裁决 schema + 策展 universe(代码即真相)。

EarningsVerdict 兼作 LLM 结构化输出 schema(llm.complete_json)与入库 content;
conviction 0-10 是事件交易域,与 CompanyThesis.conviction(1-5 论点域)互不换算。
"""
from __future__ import annotations
from pydantic import BaseModel, Field

# 8 维分析框架(dossier 节与 LLM 维度打分共用词表)
EARNINGS_DIMENSIONS: tuple[str, ...] = (
    "guidance_habit",            # 指引惯例:beat-and-raise 历史 + guidance 兑现率
    "consensus_setup",           # 预期设定:一致预期水位 + 90 天修订方向
    "positioning_sentiment",     # 仓位与情绪:评级动量/PT 空间/社媒极性
    "alt_tracking",              # 数据追踪:另类信号 + 专家/渠道洞见
    "implied_vs_expected_move",  # 期权定价:implied move vs 自己预期的分布
    "valuation_cushion",         # 估值安全垫/拥挤度
    "thesis_alignment",          # 与长期论点/争论天平的一致性
    "event_risk",                # 特异事件风险(诉讼/宏观打印/同业财报串扰)
)
DIRECTIONS = ("long", "short", "no_trade")

class DimensionRead(BaseModel):
    key: str                                  # ∈ EARNINGS_DIMENSIONS
    score: float = Field(ge=-2, le=2)         # -2 强空 .. +2 强多
    note_zh: str
    evidence: list[str] = []                  # dossier 接地 id,逐字抄

class EarningsVerdict(BaseModel):
    direction: str                            # ∈ DIRECTIONS
    conviction: float = Field(ge=0, le=10)    # ≥7 可行动
    expected_surprise_zh: str                 # 对本次 print 的预期差判断(方向+理由)
    move_view_zh: str                         # implied vs 自己预期波动的观点(贵/便宜/合理)
    dimensions: list[DimensionRead] = Field(min_length=4, max_length=8)
    plan_zh: str                              # 进出场计划(何时进、财报后何时出)
    falsifiers_zh: list[str] = Field(min_length=1, max_length=4)   # 盘前证伪条件
    asymmetry_zh: str = ""                    # 赔率不对称论证(conviction≥7 必填)
    no_trade_reason_zh: str = ""              # direction=no_trade 时必填

def validate_verdict(v: EarningsVerdict, *, known_ids: set[str]) -> list[str]:
    """五规则,违规返回清单(宁缺毋滥,与 validate_thesis 同哲学):
    ① 每个 evidence id ∈ known_ids(精确串匹配,禁幻觉);
    ② 每个 dimension.key ∈ EARNINGS_DIMENSIONS 且不重复;
    ③ conviction≥7 → 去重 evidence 锚 ≥6 ∧ asymmetry_zh 非空 ∧ direction≠no_trade;
    ④ direction=no_trade → conviction==0 ∧ no_trade_reason_zh 非空;
    ⑤ direction ∈ DIRECTIONS。"""

# 策展 universe(~40;实施时从 16 个美股 debate 旗舰起补足;测试锁 ⊆ registry ∧ US ticker)
EARNINGS_UNIVERSE: tuple[str, ...] = (
    "now", "crm", "snow", "nvidia", "amd", "tsmc", "coherent",
    "googl", "meta", "wmt", "cost", "mcd", "cmg",
    "tsla_hum", "rklb_spa", "asts_spa",
    # …实施时补充(anet/avgo/mu/dell/smci/panw/crwd/ddog/net/zs/uber/abnb/nke/sbux/dis 等,
    #   以 registry 实际 company_id 为准,期权流动性人工判断)
)
def earnings_universe(cap: int | None = None) -> list[dict]: ...
```

## 附录 B — `earnings_verdicts` DDL(schema.sql 底部,加性幂等)

```sql
-- ET:季报事件裁决(INSERT 即锁;仅 outcome/outcome_at 盘后回填;--force 才有新 version)
CREATE TABLE IF NOT EXISTS earnings_verdicts (
    id BIGSERIAL PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    event_date  DATE NOT NULL,                 -- 财报日(改期=新键)
    calendar_id BIGINT,                        -- event_calendar.id(可空,日历行可能重写)
    version     INT  NOT NULL DEFAULT 1,
    direction   TEXT NOT NULL CHECK (direction IN ('long','short','no_trade')),
    conviction  REAL NOT NULL CHECK (conviction BETWEEN 0 AND 10),
    expected_move REAL,                        -- 裁决时点最新 implied move(straddle/spot)
    content     JSONB NOT NULL,                -- EarningsVerdict 全量
    quality     JSONB NOT NULL DEFAULT '{}',   -- 锚数/维度覆盖/数字接地率
    model TEXT, run_id TEXT,
    as_of       DATE NOT NULL,                 -- 裁决生成日(T-3)
    outcome     JSONB,                         -- {status: scored|event_moved|cancelled|price_missing,
                                               --  session, actual_surprise_pct, reaction_pct,
                                               --  realized_vs_implied, direction_hit|abstain}
    outcome_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, event_date, version));
CREATE INDEX IF NOT EXISTS idx_ev_company ON earnings_verdicts(company_id, event_date DESC);
CREATE INDEX IF NOT EXISTS idx_ev_pending ON earnings_verdicts(event_date) WHERE outcome IS NULL;
```

## 附录 C — `providers/alt/implied_move.py` 骨架

```python
"""期权隐含波动(财报)追踪器:窗口内 universe 名字的 ATM straddle 快照 → alt_signals。

period_end = 快照日(观察日;IV 是当日可观察量,且不与唯一键相撞);
meta.earnings_date 是事件锚 —— dossier 按它取本事件 IV run-up 序列。
免费主路 yfinance;massive_api_key armed 时可切 fcn 栈真 IV(_massive_pull)。
"""
_RATE_MIN_INTERVAL = 1.5   # 模块级节流(finnhub._paced_get 同款)

def available() -> bool: ...        # yfinance 可导入(house 惯例:纯配置/依赖探测)

def _pick_expiry(expiries: list[str], reaction_day: date) -> str | None:
    """首个 ≥ 财报反应日的 expiry(amc → D+1;无 session 按 D+1 保守)。"""

def _straddle_mid(chain, spot: float) -> tuple[float, float] | None:
    """ATM(|strike-spot| 最小)call+put 的 mid 合计与 atm_iv;bid/ask 双零回落 lastPrice;
    仍无价 → None(跳过,不写垃圾)。"""

def pull(limit: int | None = None) -> dict:
    """窗口内名字(upcoming_calendar earnings ≤ earnings_watch_days ∩ EARNINGS_UNIVERSE)逐个:
    yahoo._handle(cid) → tk.options → _pick_expiry → tk.option_chain → fast_info spot
    → upsert_signal("alt.options_implied_move", company_id=cid, period_end=date.today(),
                    value=straddle/spot, unit="ratio", source="implied_move",
                    meta={"earnings_date","expiry","spot","atm_iv","straddle_mid","dte"})
    单司 try/except;返回 {"names": n, "written": k, "skipped": [...]}"""
```

## 附录 D — `research/earnings.py` 函数规格(核心模块,~5 批函数)

```python
# ── 第一批(ET-P1,零 LLM 计量)────────────────────────────────────────────
def reaction_return(cid, event_date: date, session: str | None) -> dict | None:
    # {"reaction_pct", "session", "d0", "d1"};amc→close(D+1)/close(D)−1;bmo→close(D)/close(D−1)−1
    # D=首个≥event_date 的交易日;价取 catalyst_returns._series(本地 prices 优先);缺价 → None
def beat_stats(cid, n=8) -> dict:
    # event_calendar: status='occurred' ∧ event_type='earnings' ∧ meta->>'surprise_pct' NOT NULL
    # → {"n", "beat_rate", "streak", "avg_abs_surprise_pct", "rows":[{date, surprise_pct}]}
def hist_move_stats(cid, n=8) -> dict:
    # 历史财报日 |reaction|:{"n", "avg_abs_move_pct", "max_abs_move_pct", "rows":[...]}
def refresh_window() -> dict:
    # ① 轮转游标(kvstate 'earnings_watch'.cursor)全 universe yahoo.pull_calendar(保日历+surprise 鲜)
    # ② 窗口内名字:yahoo.pull_analyst(estimates/ratings 每日快照→自建修订史)+ alt implied_move
    # 单司容错;返回 {"scanned", "in_window", "analyst": n, "implied": k}

# ── 第二批(ET-P2,dossier)──────────────────────────────────────────────
def dossier_earnings(cid, event: dict) -> dict | None:
    # {text, known_ids: set[str], panel: dict, as_of, event_date, n_facts}
    # 11 节(正文 §ET-P2 表);每节 try/except;known_ids 汇集全部接地 id

# ── 第三批(ET-P3,裁决)────────────────────────────────────────────────
def latest_verdict(cid, event_date) -> dict | None
def _preferred_pin() -> tuple[str, ...] | None      # codex→CODEX_PIN / claude-max→CLAUDE_MAX_PIN / None
def build_verdict(cid, *, event=None, force=False, run_id=None) -> dict
def judge_due(*, force=False) -> dict

# ── 第四批(ET-P4,闭环)────────────────────────────────────────────────
def score_outcomes() -> dict
    # outcome IS NULL ∧ event_date<today → ±3 天对齐 occurred → surprise+reaction+realized_vs_implied
    # +direction_hit(no_trade→"abstain")→ UPDATE;event_date+max_days 兜底 price_missing/event_moved
def calibration() -> dict
    # conviction 分桶 [0-3/4-6/7-8/9-10] × {n, hit_rate, avg_reaction_pct};≥7 桶单列;可按 model 分层
```

## 附录 E — `_SYSTEM_EARNINGS` 系统提示词(草案)

```
你是对冲基金的财报事件交易判官。给你一份某公司季报前的 360° dossier(含接地事实 id)。
输出一个 EarningsVerdict JSON。纪律:
1. evidence 里的 id 必须逐字抄自 dossier(如 [estimate:now:eps_diluted]),严禁编造;
2. conviction 必须与证据密度耦合:≥7 分需 ≥6 个不同的接地锚,且 asymmetry_zh 必须写清
   为什么赔率不对称(市场定价了什么、你认为错在哪、错的代价与对的赔付);
3. ≥7 分还必须给出盘前可观察的证伪条件(falsifiers_zh)——出现即应放弃交易;
4. 没有 edge 就选 no_trade(conviction=0,写明 no_trade_reason_zh)。宁缺毋滥:
   本系统的价值在于极少数高把握时刻,不在于每次都有观点;
5. 区分「预期差」与「好公司」:好公司+人尽皆知的高预期+期权定价充分 = 没有交易;
   平庸公司+过度悲观的预期+便宜的 implied move 可能才是交易;
6. move_view_zh 必须表态:implied move 相对你预期的分布是贵、便宜还是合理——这决定
   方向对了也可能亏钱(赢面被期权定价吃掉);
7. dimensions 至少覆盖 4 维,分数与 note 一致;信息缺失的维度诚实写「数据不足」而非编造。
```

## 附录 F — 编排/CLI/API wiring diff(示意)

```python
# glm_worker.py — _pull_fresh 内(零 LLM 道)
_run("earnings_watch", 6 * 3600, lambda: earnings.refresh_window())

# glm_worker.py — 模块级(pin/quota 门外;_research_audit_step 同款,可打桩)
def _earnings_step() -> dict:
    out = {}
    if _due("earnings_verdicts", 24 * 3600):
        try:
            from ..research import earnings
            out["verdicts"] = earnings.judge_due()
            _stamp("earnings_verdicts", 24 * 3600, ok=True)
        except Exception as e:
            out["verdicts"] = {"error": str(e)[:160]}; _stamp(..., ok=False)
    if _due("earnings_outcomes", 12 * 3600):
        ...  # score_outcomes() 同型
    return out
# run_once():out["earnings"] = _earnings_step()

# cli.py
earnings_app = typer.Typer(help="季报事件交易:观察窗/面板/裁决/回验/校准")
# watch / panel CID / judge [CID|--due] [--force] / outcomes / calibration

# api/app.py
@app.get("/api/ops/earnings")          # 队列 + 近期裁决 + 校准摘要
@app.post("/api/ops/earnings/{cid}/judge")   # BackgroundTasks → build_verdict(force 可选)
# api/dashboard.py — company_detail dict 增 "earnings": _earnings_block(cid)
```

## 附录 G — 测试矩阵(全部离线 monkeypatch + seeded_db + 2099 隔离)

| 文件 | 覆盖 |
|---|---|
| test_earnings_ontology.py | universe ⊆ registry/US ticker/无重复;schema roundtrip;validate 五规则;路由存在 |
| test_implied_move.py | straddle 数学;expiry 选择(≥反应日);period_end=今日;幂等;无链/双零跳过 |
| test_earnings_pipeline.py | upsert_calendar jsonb 合并保 hour;beat_stats;amc/bmo reaction 数学(合成 prices) |
| test_earnings_dossier.py | 11 节出现;known_ids 新 kind;数字正确;缺数据节优雅缺席 |
| test_earnings_verdict.py | v1 入库;重跑 skipped(锁);force→v2;幻觉 id→rejected;host_only→deferred |
| test_earnings_outcomes.py | hit/miss;amc/bmo;event_moved;price_missing 收尾;abstain;calibration 分桶 |
| test_glm_worker.py(扩) | _earnings_step 打桩(不发真 LLM);watch 节拍注册 |

## 附录 H — 前端 `EarningsSection.tsx` 规格

- **裁决卡**:方向徽章(多=绿/空=红/不交易=灰)+ conviction 表针(0-10,≥7 段高亮)+ as_of/model +
  expected_surprise_zh/asymmetry_zh 摘录 + plan_zh/falsifiers_zh 折叠;
- **implied vs 历史对比条**:最新 implied move vs hist avg |move|(溢价/折价着色)+ 窗内 IV run-up 迷你线;
- **beat 习惯序列格**:近 8 季 surprise_pct 色块(beat 绿/miss 红)+ beat 率/连击;
- **倒计时**:days_to + session 徽章(AMC/BMO);
- **锁后漂移 chips**:锁定至今 implied move Δ、新修订数、新语义事实数(陈旧度提示,不改裁决);
- **outcome 历史**:近 4 次 hit/miss/abstain + reaction%;
- 挂载:`CompanyPage.tsx` `<ThesisSection/>` 之后;`types-earnings.ts` 对应 `_earnings_block` 返回形;
- ops 控制台:校准小卡(≥7 桶 hit-rate 大数字)。

---

## 执行策略(ultracode)

- ET-P0 universe 策展与 ET-P2 dossier 节文案可 Workflow 扇出起草 + 对抗复核;
- 核心接缝(锁定语义、alt_signals period_end 裁决、_earnings_step 门外放置、outcome 口径)主循环亲手写;
- 每阶段跑附录 G 对应测试 + 全量 pytest + ruff 再进下一阶段;
- ET-P5 真机验收集中做(美股数据,无 CN egress 依赖;裁决记录实际 model 供校准分层);
- 完成后独立对抗代码评审(house 惯例)→ 修复 → 合并 → 部署(经用户确认)。
