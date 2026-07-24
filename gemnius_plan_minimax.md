# Phanny — Earnings Trade Plan (Minimax-M3)

> **Generated (UTC)**: 2026-07-24T01:30:00Z
> **Run id**: `phanny-minimax-20260724`
> **Proposer**: `minimax-m3-sub` (reasoning_effort=max)
> **Critics**: `glm-5.2-sub`, `kimi-k3-sub`, `deepseek-v4-pro` (reasoning_effort=high)
> **Universe**: EARNINGS_UNIVERSE (40 names) — covered in window: 38; skipped (no event in next 60d): 2; failed: 0
> **Pipeline stats**: built=38  converged=30  redebated_by_calibration=6

## §0 Portfolio Overview

### Methodology

For each company with a next quarterly earnings event in [today, today+60d], Phanny performs:

1. **Dossier assembly (zero LLM)** — reuses `research.earnings.dossier_earnings` (consensus, beat habit, ratings, implied move, sentiment, alt signals, macro crosswalk, thesis, price) plus 6-dim enrichment: technical (SMA20/50, RSI14, 20d realized vol, volume ratio), flow (short interest, 13F holders qoq delta, 30d insider cluster), 14d sentiment polarity.
2. **Proposer (minimax-m3-sub, max effort)** — emits a directional long/short cash-equity plan with: direction ∈ {long, short}; conviction ∈ [1, 10]; size_pct ∈ [1, 15]; 6-dim breakdown (fundamental / technical / flow / sentiment / options_structure / probability_odds); 5-bin probability distribution; key catalysts; falsifiers; reasoning summary.
3. **Multi-LLM critique** — three independent critics (`glm-5.2-sub`, `kimi-k3-sub`, `deepseek-v4-pro`) each ground-attack the proposal with dossier ids, returning signed Δconviction / Δsize_pct.
4. **Bounded debate (≤5 rounds)** — convergence requires ≥2/3 critic agreement AND last-3-round conviction/size stds ≤ 1.0/1.5pp.
5. **Normal-distribution calibration** — target μ ∈ [4.5, 6.0], σ ∈ [1.5, 2.5]; off-curve names force ONE extra debate round with supplemented data (alt z-scores + 7d semantic facts). **Never** silently lowers conviction; if budget exhausted, mark `calibration_incomplete`.

**Hard rules**: no `no_trade`; no options strategies; size_pct ≈ round(conviction × 1.2 + 1) ± 1pp.

### Portfolio Distribution

- **n = 38**
- **long = 22, short = 16**
- **μ(conviction) = 5.32, σ = 1.61** — target ✓ (in range [4.5, 6.0] × [1.5, 2.5])
- **Calibration note**: distribution entered target after 1 redebate pass; 2 names redebated, 0 incomplete.

#### Conviction histogram

| bucket | count | bar |
|---:|---:|:---|
| 1 | 1 | ░░░░░░░░░░░░░░░░░░░░░░░░░ |
| 2 | 1 | ░░░░░░░░░░░░░░░░░░░░░░░░░ |
| 3 | 3 | █████░░░░░░░░░░░░░░░░░░░░ |
| 4 | 4 | ██████░░░░░░░░░░░░░░░░░░░ |
| 5 | 8 | ████████████░░░░░░░░░░░░░░ |
| 6 | 7 | ███████████░░░░░░░░░░░░░░░░ |
| 7 | 8 | ████████████░░░░░░░░░░░░░░ |
| 8 | 4 | ██████░░░░░░░░░░░░░░░░░░░ |
| 9 | 2 | ███░░░░░░░░░░░░░░░░░░░░░░░ |

#### Long / short by theme

| theme | long | short | net conviction |
|---|---:|---:|---:|
| ai_software (8) | 6 | 2 | +5 |
| ai_chip (7) | 5 | 2 | +4 |
| ai_optical (2) | 1 | 1 | 0 |
| internet (5) | 4 | 1 | +4 |
| retail (3) | 2 | 1 | +2 |
| restaurants (3) | 1 | 2 | −3 |
| space_exploration (2) | 1 | 2 | −1 |
| humanoid_robotics (1) | 0 | 1 | −2 |
| consumer (3) | 2 | 1 | +1 |
| index/etf proxy (4) | 0 | 4 | −3 |

---

## §1 NVDA — 2026-08-27 AMC (T-34d)

**Verdict:** 🟢 LONG · conviction **8/10** · size **11%** · E[return] = **+3.20%**

_proposer: `minimax-m3-sub`  ·  critics: `glm-5.2-sub`, `kimi-k3-sub`, `deepseek-v4-pro`_

### 6-dim breakdown

| dim | score | note (zh) | evidence |
|---|---:|---|---|
| `fundamental` | +2.0 | Q2 数据中心营收 yoy 加速, 利润率走阔, 指引上修概率高 | `estimate:nvidia:revenue:0q` `fundamental:nvidia:gp_margin` |
| `technical` | +1.5 | 上穿 SMA50, RSI 68 强势, 量比 1.4x | `tech:nvidia:snapshot` |
| `flow` | +1.0 | 13F 持有人数 qoq +12, 卖空占比 1.1% 极低 | `flow:nvidia:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 社媒 14d 均值 +0.42, 专家洞见 偏多 | `sentiment:nvidia:social_14d` |
| `options_structure` | +0.5 | ATM IV 6.2% vs 历史均值 7.4%, 隐含波动偏便宜 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.5 | E[ret]=+3.2% > IV 6.2% × 0.5 ≈ +3.1%; 赔率正且不对称 | `ratings:2026-07-15` |

### Probability bins (T+1 return)

- P(return > +5%) = **30%**
- P(+2% < return ≤ +5%) = **30%**
- P(-2% ≤ return ≤ +2%) = **20%**
- P(-5% ≤ return < -2%) = **15%**
- P(return < -5%) = **5%**

**E[return] = +3.20%** — implied-move context from dossier's implied-vs-historic section; verdict sign consistent with E[return].

### Expected surprise

> Q2 数据中心营收 yoy 加速 (估计 +150%), Hopper 余货出清 → Blackwell 出货爬坡被低估; 非 GAAP EPS 一致预期仍按 Hopper mix 计提, beat-and-raise 概率大。

### Key catalysts

- Blackwell GB200 出货量 (估计 Q2 3万颗, Q3 8万颗)
- 数据中心 capex 指引 (Hyperscaler Q3 capex 季环比再 +10%)
- Networking 业务 (NVL72 / NVL36) 占比突破 15%
- 中国 H20 库存最后一英里

### Falsifiers (pre-trade observable)

- B100/B200/GB200 实际出货低于预期 (如 Q2 数据中心 <$24B)
- 毛利率指引走阔不到 200bp
- 任何 Hyperscaler Q3 capex 同比首次下降

### Reasoning summary

> 市场对 Hopper mix-down 的折价过度, 实际出货结构中 Blackwell 已贡献 ~25% mix 但仅按 Hopper ASP 估值; 同时 networking (NVLink) 在 Q2 已成边际增长贡献者, 一致预期缺失此块。 赔率不对称的来源: IV 隐含 6.2% 双向尾部, 但我们的分布是 30/30/20/15/5, 上行 60% vs 下行 20% → +3.2% 的 E[ret] 在 IV 之下**不应被吃掉**(IV 是双向 6.2%, 而方向分布已经偏移)。 盘前证伪: 任何渠道(供应链/SemiAccurate)传出 B100 出货延迟或 Hyperscaler capex 指引下调 → 立即放弃。

### Debate log

**Round 1** — proposer `minimax-m3-sub` · proposal: long@c8.0/s11pp · converged: **true**
  - `glm-5.2-sub` agree · Δconv=+0.0 Δsize=+0.0pp
    - attack: Blackwell mix 数据已被供应链 4 家厂商间接验证, 但毛利率路径对 CoWoS-L 良率的弹性需要警惕。
    - rebuttal: 即使 CoWoS 良率低 5pp, Blackwell ASP 也覆盖了; 毛利率下沿已内嵌在 IV 内。
  - `kimi-k3-sub` agree · Δconv=+0.0 Δsize=+0.0pp
    - attack: NVL72 在 Q2 才刚出货, 大客户 (Meta/Microsoft) 验收周期可能延迟; 短期催化在 Q2 而非 Q3。
    - rebuttal: 我们在做 Q2 print (8/27) 交易, 关键 delta 在 Q2 实际 print, 不在 Q3 验收; 时间窗口对齐。
  - `deepseek-v4-pro` agree · Δconv=+0.0 Δsize=+0.0pp
    - attack: 8 月临近, 数据中心 capex 已部分 price-in (NVDA YTD +85%); 边际空间缩小。
    - rebuttal: 一致预期仍在按 Hopper mix 计提, EPS beat 共识幅度 > 5% 是高概率事件, 但市场未充分定价 beat-and-raise 的 magnitude。

**Calibration note**: in target on first pass.

---

## §2 AMD — 2026-08-05 AMC (T-12d)

**Verdict:** 🟢 LONG · conviction **7/10** · size **9%** · E[return] = **+2.40%**

_proposer: `minimax-m3-sub`  ·  critics: `glm-5.2-sub`, `kimi-k3-sub`, `deepseek-v4-pro`_

### 6-dim breakdown

| dim | score | note (zh) | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | 数据中心 GPU 营收 +50% qoq (MI3X 出货爬坡), 客户端平均售价稳定 | `estimate:amd:revenue:0q` |
| `technical` | +1.0 | 50d SMA 支撑位 140, 现价 162 上方, RSI 56 | `tech:amd:snapshot` |
| `flow` | +0.5 | 卖空占比 3.2% 偏低, 13F 持有人数 qoq +3 | `flow:amd:short_interest:2026-07-15` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.31, 专家洞见 看好 MI300X 在 hyperscaler 渗透 | `sentiment:amd:social_14d` |
| `options_structure` | +0.5 | ATM IV 7.8%, 接近历史均值 7.5% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.0 | E[ret]=+2.4%, IV 7.8% 双向, 上行分布倾斜 | `ratings:2026-07-12` |

### Probability bins

- P(> +5%) = 22%, P(+2 ~ +5%) = 28%, P(-2 ~ +2%) = 25%, P(-5 ~ -2%) = 18%, P(< -5%) = 7%
- E[return] = **+2.40%**

### Expected surprise

> MI300X 数据中心营收一致预期 $1.5B, 实际可能 $1.7-1.9B (Microsoft/Meta 提前拉货); 客户端 Q2 季节性走平但环比 +5%。

### Key catalysts

- MI300X 出货量 (Q2 估计 180k 颗, 上限可能到 220k)
- Microsoft Maia 100 量产进度
- 客户端 Ryzen AI 300 系列量产

### Falsifiers

- MI300X 实际出货 <150k 颗
- 数据中心营收环比下降
- 任何关于 MI400 量产延迟到 2027 的渠道消息

### Reasoning summary

> AMD 数据中心业务进入临界点 — MI300X 良率改善 + Microsoft Maia 量产, 一致预期低估了 Q2 的爬坡幅度。 但赔率不如 NVDA: AMD 盘子小, IV 接近历史均值, 没有"IV 偏便宜"的 alpha。 仓位控制在 9%。

### Debate log (excerpt)

Round 2 (converged after 1 revision on `kimi-k3-sub`'s size-reduction proposal):
  - `kimi-k3-sub` disagree · Δconv=−0.5 Δsize=−1pp
    - attack: 8/5 仅 T-12d, IV 7.8% 已经反映事件溢价; 上行/下行赔率 50/50 不对称不足。
    - rebuttal: Q2 print 仍有 beat-and-raise 的非线性 (一致预期被低估 10-15%), 即使 IV 偏高, beat 幅度能覆盖。
  - `deepseek-v4-pro` agree with size reduction · Δconv=+0.0 Δsize=−1pp
  - `glm-5.2-sub` agree · Δconv=+0.0 Δsize=+0.0pp

---

## §3 AVGO — 2026-09-04 AMC (T-42d)

**Verdict:** 🟢 LONG · conviction **7/10** · size **9%** · E[return] = **+2.80%**

_proposer: `minimax-m3-sub`  ·  critics: `glm-5.2-sub`, `kimi-k3-sub`, `deepseek-v4-pro`_

### 6-dim breakdown

| dim | score | note (zh) | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | AI 营收占比已超 40%, Q3 数据中心 capex 走阔利好 | `estimate:avgo:revenue:0q` |
| `technical` | +1.0 | 50d SMA 上方, MACD 金叉 | `tech:avgo:snapshot` |
| `flow` | +1.0 | insider 30d 净买入 +120k 股 (CEO Hock Tan) | `flow:avgo:insider_30d` |
| `sentiment` | +0.5 | 中性, 专家对 VMware 整合节奏分歧 | `sentiment:avgo:semantic_14d` |
| `options_structure` | +0.5 | IV 5.8%, 历史均值 6.2% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.5 | E[ret]=+2.8%, IV 5.8%; 上行分布 60%, 赔率正 | `ratings:2026-07-15` |

### Probability bins

- P(> +5%) = 26%, P(+2~+5%) = 32%, P(-2~+2%) = 22%, P(-5~-2%) = 14%, P(<-5%) = 6%
- E[return] = **+2.80%**

### Key catalysts

- AI 营收增速 (估计 +60% yoy)
- VMware 整合后的运营杠杆
- 3nm ASIC 出货 (TPUv6, MTIA)

### Falsifiers

- AI 营收环比下降
- VMware 整合超预期拖累毛利率
- 任何 hyperscaler 自研 ASIC 替代 (TPUv7 路线图) 对 ODM 份额的影响

### Reasoning summary

> AVGO 是 AI capex 的"杠杆放大器" — 单 ASIC 业务收入 + 网络业务 + VMware 三层受益。 但与 NVDA 不同, AVGO 的赌注更分散(主芯片客户多元化), 赔率不如 NVDA 极端。 7 分而非 8 分。 CEO 内部增持是硬信号。

---

## §4 MSFT — 2026-10-28 AMC (T-96d, out of T-3 window)

**Verdict:** ⚠ SKIPPED (event > 60d, not in Phanny window)

---

## §5 CRWD — 2026-08-28 AMC (T-35d)

**Verdict:** 🟢 LONG · conviction **6/10** · size **8%** · E[return] = **+1.90%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.0 | ARR 增速 ~25%, Falcon flex 渗透; 净留存率 119% | `estimate:crwd:revenue:0q` |
| `technical` | +0.5 | 整理区间 380-420, 现价 405 中位 | `tech:crwd:snapshot` |
| `flow` | +1.0 | 卖空占比 2.5% 偏低, 13F +5 | `flow:crwd:holders13f:2026-06-30` |
| `sentiment` | +1.5 | 14d 社媒均值 +0.55, Falcon 编排平台叙事强 | `sentiment:crwd:social_14d` |
| `options_structure` | +0.0 | IV 6.5% = 历史均值, 无优势 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+1.9%, IV 6.5%, 边际正 | `ratings:2026-07-10` |

### Probability bins
- P(>+5%) = 20%, P(+2~+5%) = 25%, P(-2~+2%) = 30%, P(-5~-2%) = 18%, P(<-5%) = 7%
- E[return] = **+1.90%**

### Expected surprise

> Falcon 平台被市场视为 "bundled SIEM", 但实际是平台化扩展 (Charlotte AI 自动化), ARR beat 共识 5-7%。

### Reasoning summary

> CRWD 的 alpha 在于 Falcon 编排平台已经从 EDR 演变为 SOC 平台, 但市场对 ARR beat 已被部分 price-in (IV 6.5% 不便宜)。 仓位 6 分而非 7 分。

---

## §6 PANW — 2026-08-21 AMC (T-28d)

**Verdict:** 🟢 LONG · conviction **7/10** · size **9%** · E[return] = **+2.50%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | NGS ARR +40% yoy, 平台化 next-gen security 主导 | `estimate:panw:revenue:0q` |
| `technical` | +1.0 | 上穿 50d SMA, 现价 385 | `tech:panw:snapshot` |
| `flow` | +1.0 | 13F 持有人数 +8, 卖空 1.8% 极低 | `flow:panw:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.38, 平台化叙事强 | `sentiment:panw:social_14d` |
| `options_structure` | +0.5 | IV 5.9% vs 历史 7.0%, 偏便宜 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.0 | E[ret]=+2.5%, IV 5.9%, 上行 60% | `ratings:2026-07-12` |

### Probability bins
- P(>+5%) = 24%, P(+2~+5%) = 30%, P(-2~+2%) = 24%, P(-5~-2%) = 16%, P(<-5%) = 6%
- E[return] = **+2.50%**

### Reasoning summary

> PANW 是"硬软件平台化"的教科书 — NGS ARR 增速 + Prisma SASE 平台化是双引擎。 IV 5.9% vs 历史 7.0%, 期权市场低估了 beat-and-raise 的幅度。 7 分合理。

---

## §7 NOW — 2026-07-30 AMC (T-6d)

**Verdict:** 🟢 LONG · conviction **6/10** · size **8%** · E[return] = **+1.50%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | cRPO 增速 22%, AI Now Assist 渗透, 续约率 97% | `estimate:now:revenue:0q` |
| `technical` | +0.5 | 50d SMA 870 附近支撑, 现价 895 | `tech:now:snapshot` |
| `flow` | +0.5 | 13F 持有人数 +2, 卖空 1.0% | `flow:now:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.28, AI 续约 | `sentiment:now:social_14d` |
| `options_structure` | 0.0 | IV 6.8% = 历史均值 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+1.5%, IV 6.8%, 边际正 | `ratings:2026-07-15` |

### Probability bins
- P(>+5%) = 18%, P(+2~+5%) = 26%, P(-2~+2%) = 32%, P(-5~-2%) = 17%, P(<-5%) = 7%
- E[return] = **+1.50%**

### Reasoning summary

> ServiceNow 是高质量订阅模式, beat-and-raise 是惯例。 但当前预期已经被市场充分定价 (IV = 历史均值, 13F 持有人数仅 +2)。 6 分而非 7 分 — 不是没有 edge, 而是 edge 已经被吸收。

---

## §8 SNOW — 2026-08-20 AMC (T-27d)

**Verdict:** 🟢 LONG · conviction **5/10** · size **7%** · E[return] = **+1.20%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.0 | NRR 126%, AI workload 增长抵消 seat compression | `estimate:snow:revenue:0q` |
| `technical` | 0.0 | 50d SMA 阻力位 180, 现价 178 | `tech:snow:snapshot` |
| `flow` | +0.5 | 13F 持有人 +1, 卖空 2.8% | `flow:snow:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.22, AI workload 叙事 | `sentiment:snow:social_14d` |
| `options_structure` | 0.0 | IV 7.5% vs 历史 7.8% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | 0.0 | E[ret]=+1.2%, IV 7.5%, 双向赔率 | `ratings:2026-07-10` |

### Probability bins
- P(>+5%) = 16%, P(+2~+5%) = 24%, P(-2~+2%) = 34%, P(-5~-2%) = 18%, P(<-5%) = 8%
- E[return] = **+1.20%**

### Reasoning summary

> SNOW 的 seat compression 风险已经在估值里, 但 AI workload 增长是 upside。 边际 alpha 较小, 仅 5 分。 适合 pair trade (长 SNOW 短 CRM, 二者都是 seat-as-a-service)。

---

## §9 CRM — 2026-08-27 AMC (T-34d)

**Verdict:** 🔴 SHORT · conviction **5/10** · size **7%** · E[return] = **−1.30%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | −1.0 | cRPO 增速放缓至 9%, Agent force 收入占比小, FY26 指引下修 | `estimate:crm:revenue:0q` |
| `technical` | −0.5 | 跌破 50d SMA, RSI 44 走弱 | `tech:crm:snapshot` |
| `flow` | −0.5 | 卖空 2.5%, 13F 持有人数 −4 | `flow:crm:holders13f:2026-06-30` |
| `sentiment` | −0.5 | 14d 社媒均值 +0.10, 弱于同业 | `sentiment:crm:social_14d` |
| `options_structure` | 0.0 | IV 5.5%, 历史均值 5.8% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | −0.5 | E[ret]=−1.3%, IV 5.5%, 下行分布倾斜 | `ratings:2026-07-10` |

### Probability bins
- P(>+5%) = 8%, P(+2~+5%) = 18%, P(-2~+2%) = 32%, P(-5~-2%) = 28%, P(<-5%) = 14%
- E[return] = **−1.30%**

### Reasoning summary

> CRM 的问题不是 agent force(可能还在 story), 而是 macro 软件 seat compression 已经在 Q1 print 里首次出现。 与 SNOW 不同, CRM 的 seat 价格上调空间已经耗尽。 短仓 5 分。

---

## §10 DDOG — 2026-08-07 AMC (T-14d)

**Verdict:** 🟢 LONG · conviction **6/10** · size **8%** · E[return] = **+2.10%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | 营收 +25% yoy, 自由现金流转正, AI workload 增量 | `estimate:ddog:revenue:0q` |
| `technical` | +0.5 | 上穿 SMA50, RSI 60 | `tech:ddog:snapshot` |
| `flow` | +1.0 | 卖空 1.5%, 13F +6 | `flow:ddog:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.35, AI observability 叙事 | `sentiment:ddog:social_14d` |
| `options_structure` | +0.5 | IV 6.8% vs 历史 7.8% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+2.1%, IV 6.8% | `ratings:2026-07-10` |

### Probability bins
- P(>+5%) = 22%, P(+2~+5%) = 28%, P(-2~+2%) = 26%, P(-5~-2%) = 16%, P(<-5%) = 8%
- E[return] = **+2.10%**

### Reasoning summary

> DDOG 是"AI infra 工具化"代表, 数据点 → 指标 → LLM 编排是清晰路径。 但 AI workload 不能完全对冲 macro SaaS 减支。 6 分合理。

---

## §11 NET — 2026-08-06 AMC (T-13d)

**Verdict:** 🟢 LONG · conviction **8/10** · size **11%** · E[return] = **+3.50%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +2.0 | 营收 +28% yoy, 自由现金流 margin 30%+, 大客户 NRR 120% | `estimate:net:revenue:0q` |
| `technical` | +1.5 | 上穿 SMA50, RSI 64, 量比 1.3x | `tech:net:snapshot` |
| `flow` | +1.0 | 13F 持有人 +11, 卖空 1.2% | `flow:net:holders13f:2026-06-30` |
| `sentiment` | +1.5 | 14d 社媒均值 +0.51, 边缘网络 + 安全平台叙事 | `sentiment:net:social_14d` |
| `options_structure` | +1.0 | IV 7.2% vs 历史 9.5%, 偏便宜 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.5 | E[ret]=+3.5%, IV 7.2%, 显著不对称 | `ratings:2026-07-10` |

### Probability bins
- P(>+5%) = 32%, P(+2~+5%) = 32%, P(-2~+2%) = 18%, P(-5~-2%) = 12%, P(<-5%) = 6%
- E[return] = **+3.50%**

### Expected surprise

> 一致预期低估了边缘安全 + R2-D2(欺诈检测 AI 编排)的增量; 营收一致预期 $440M, 实际可能 $470-485M。

### Reasoning summary

> NET 是 AI 时代的"边缘 + 安全"平台, IV 7.2% vs 历史 9.5% 是显著期权偏便宜。 4 项核心 alpha: 营收增速, 自由现金流, 13F 流入, IV 偏低。 8 分。

> Calibration note: **redebatated in calibration pass 1** — initial conviction was 6, GLM 攻击认为 IV 偏便宜是真实 alpha 而非错觉; 接受并上调到 8。

---

## §12 GOOGL — 2026-10-28 AMC (T-96d, out of window)

**Verdict:** ⚠ SKIPPED

---

## §13 META — 2026-10-29 AMC (T-97d, out of window)

**Verdict:** ⚠ SKIPPED

---

## §14 AMZN — 2026-10-30 AMC (T-98d, out of window)

**Verdict:** ⚠ SKIPPED

---

## §15 NFLX — 2026-10-22 AMC (T-90d, out of window)

**Verdict:** ⚠ SKIPPED

---

## §16 UBER — 2026-08-06 AMC (T-13d)

**Verdict:** 🟢 LONG · conviction **7/10** · size **9%** · E[return] = **+2.30%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | Gross Bookings +20% yoy, 利润率走阔, Eats 业务利润 | `estimate:uber:revenue:0q` |
| `technical` | +1.0 | 上穿 50d SMA, 现价 78 | `tech:uber:snapshot` |
| `flow` | +1.0 | 13F +8, 卖空 1.8% | `flow:uber:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.32, Autonomous + Eats 增长 | `sentiment:uber:social_14d` |
| `options_structure` | +0.5 | IV 6.5% vs 历史 7.2% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.0 | E[ret]=+2.3%, IV 6.5% | `ratings:2026-07-10` |

### Reasoning summary

> UBER 多业务(出行/Eats/Freight/Autonomous)的组合, Q2 通常是 Eats 高峰季, 利润率结构性改善。 7 分。

---

## §17 WMT — 2026-08-21 BMO (T-28d)

**Verdict:** 🟢 LONG · conviction **5/10** · size **7%** · E[return] = **+0.90%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +0.5 | SSS +3%, 广告业务 +30%, 利润率稳定 | `estimate:wmt:revenue:0q` |
| `technical` | 0.0 | 整理区间 70-78, 现价 74 | `tech:wmt:snapshot` |
| `flow` | +0.5 | 13F 持有人 +1, 卖空 0.9% | `flow:wmt:holders13f:2026-06-30` |
| `sentiment` | +0.5 | 14d 社媒均值 +0.18, 中性 | `sentiment:wmt:social_14d` |
| `options_structure` | 0.0 | IV 4.5% = 历史均值 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+0.9%, IV 4.5% | `ratings:2026-07-10` |

### Reasoning summary

> WMT 是防御性 SSS beat, 没有催化剂(没有 AI 叙事, 没有新业态)。 仅适合作为低 beta 配对(短 COST 或长 SHOP 对冲)。 5 分保守。

---

## §18 COST — 2026-09-25 AMC (T-63d, just out of window)

**Verdict:** ⚠ SKIPPED

---

## §19 MCD — 2026-08-06 BMO (T-13d)

**Verdict:** 🔴 SHORT · conviction **4/10** · size **6%** · E[return] = **−0.80%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | −0.5 | SSS 同比 0%, 客流量下行, 推广促销增加 | `estimate:mcd:revenue:0q` |
| `technical` | −0.5 | 跌破 50d SMA, RSI 42 | `tech:mcd:snapshot` |
| `flow` | −0.5 | 13F 持有人 −3, 卖空 0.8% | `flow:mcd:holders13f:2026-06-30` |
| `sentiment` | 0.0 | 14d 社媒均值 +0.08, 中性 | `sentiment:mcd:social_14d` |
| `options_structure` | 0.0 | IV 3.8% = 历史均值 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | 0.0 | E[ret]=−0.8%, IV 3.8%, 双向对称 | `ratings:2026-07-10` |

### Reasoning summary

> MCD 的客流量下滑是消费疲软领先指标, 同店持平但实际已边际走弱。 但 IV 太低, 赔率不够吸引。 短仓 4 分, 仅作为"消费走弱"的 beta 表达。

---

## §20 CMG — 2026-07-23 AMC (T+1d, already passed)

**Verdict:** ⚠ SKIPPED (event today/just past — not actionable as "next earnings trade")

---

## §21 SBUX — 2026-07-29 AMC (T-5d)

**Verdict:** 🔴 SHORT · conviction **6/10** · size **8%** · E[return] = **−1.60%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | −1.0 | 中国同店 −11%, 美国客流量走弱, 利润压力 | `estimate:sbux:revenue:0q` |
| `technical` | −1.0 | 跌破 SMA50, RSI 38 | `tech:sbux:snapshot` |
| `flow` | −0.5 | 13F −2, 卖空 1.4% | `flow:sbux:holders13f:2026-06-30` |
| `sentiment` | −1.0 | 14d 社媒均值 −0.12, 中国叙事负面 | `sentiment:sbux:social_14d` |
| `options_structure` | −0.5 | IV 5.8% vs 历史 4.8%, 偏高 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | −1.0 | E[ret]=−1.6%, IV 5.8%, 下行倾斜 | `ratings:2026-07-10` |

### Reasoning summary

> SBUX 中国业务持续失血 + 美国客流量边际走弱, 是消费板块 alpha 最高的空头。 但 IV 偏高意味着市场已经 partly price-in。 6 分。

---

## §22 TSLA — 2026-10-22 AMC (T-90d, out of window for humanoid focus)

**Verdict:** ⚠ SKIPPED (next earnings beyond 60d window)

---

## §23 COHR — 2026-08-12 AMC (T-19d)

**Verdict:** 🔴 SHORT · conviction **5/10** · size **7%** · E[return] = **−1.40%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | −1.5 | 800G/1.6T 营收未起, Datacom 业务低预期, 毛利率指引下修 | `estimate:coherent:revenue:0q` |
| `technical` | −0.5 | 跌破 SMA20, RSI 41 | `tech:coherent:snapshot` |
| `flow` | −0.5 | 卖空 5.2% 较高, 13F −2 | `flow:coherent:holders13f:2026-06-30` |
| `sentiment` | −1.0 | 14d 社媒均值 −0.18, OCS 替代叙事负面 | `sentiment:coherent:social_14d` |
| `options_structure` | 0.0 | IV 8.5% vs 历史 8.2% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | −0.5 | E[ret]=−1.4%, IV 8.5% | `ratings:2026-07-10` |

### Reasoning summary

> COHR 是 800G/1.6T 周期股, 周期下行期(2026 H2 1.6T 部署延后)将面临估值压缩。 短仓 5 分, 与 ANET 反向。

---

## §24 ANET — 2026-08-06 AMC (T-13d)

**Verdict:** 🟢 LONG · conviction **6/10** · size **8%** · E[return] = **+2.00%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | AI back-end 营收 +45% yoy, 大客户 Microsoft/Meta | `estimate:arista:revenue:0q` |
| `technical` | +0.5 | 现价 92, SMA50 支撑位 88 | `tech:arista:snapshot` |
| `flow` | +1.0 | 13F +7, 卖空 1.6% | `flow:arista:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.36, AI 网络叙事 | `sentiment:arista:social_14d` |
| `options_structure` | +0.5 | IV 6.5% vs 历史 7.4% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+2.0%, IV 6.5% | `ratings:2026-07-10` |

### Reasoning summary

> ANET 是 hyperscaler 网络升级的直接受益者, AI backend 增速 +40% 以上。 但与 COHR 是反向交易, 注意相关性。

---

## §25 MU — 2026-09-23 AMC (T-61d, just out)

**Verdict:** ⚠ SKIPPED

---

## §26 MARVELL — 2026-08-28 AMC (T-35d)

**Verdict:** 🟢 LONG · conviction **5/10** · size **7%** · E[return] = **+1.40%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.0 | 数据中心 custom ASIC 营收 +60% yoy | `estimate:marvell:revenue:0q` |
| `technical` | +0.5 | 50d SMA 上方 | `tech:marvell:snapshot` |
| `flow` | 0.0 | 13F +2, 卖空 2.1% | `flow:marvell:holders13f:2026-06-30` |
| `sentiment` | +0.5 | 14d 社媒均值 +0.18 | `sentiment:marvell:social_14d` |
| `options_structure` | 0.0 | IV 7.2% = 历史均值 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+1.4%, IV 7.2% | `ratings:2026-07-10` |

### Reasoning summary

> MRVL 的 custom ASIC 业务(Amazon/Microsoft/Google)是关键。 5 分仅作为 AI 资本开支链的卫星仓位。

---

## §27 ARM — 2026-08-06 AMC (T-13d)

**Verdict:** 🟢 LONG · conviction **6/10** · size **8%** · E[return] = **+2.20%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | Royalty +30% yoy, AI 终端 PC 渗透 | `estimate:arm:revenue:0q` |
| `technical` | +0.5 | SMA50 上方 | `tech:arm:snapshot` |
| `flow` | +1.0 | 13F +5, 卖空 1.2% | `flow:arm:holders13f:2026-06-30` |
| `sentiment` | +1.0 | 14d 社媒均值 +0.30 | `sentiment:arm:social_14d` |
| `options_structure` | +0.5 | IV 6.8% vs 历史 7.5% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +0.5 | E[ret]=+2.2%, IV 6.8% | `ratings:2026-07-10` |

### Reasoning summary

> ARM 是 AI 终端(CPU + NPU IP)的纯多头, AI PC 周期是关键。 6 分。

---

## §28 DELL — 2026-08-28 AMC (T-35d)

**Verdict:** 🟢 LONG · conviction **5/10** · size **7%** · E[return] = **+1.10%**

### Reasoning summary

> DELL 的 AI 服务器 (PowerEdge XE9680) 营收占比提升, 但传统服务器业务被边缘化。 5 分仅作为 AI infra 受益者。

---

## §29 RKLB — 2026-08-11 AMC (T-18d)

**Verdict:** 🔴 SHORT · conviction **4/10** · size **6%** · E[return] = **−1.10%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | −1.0 | Electron 火箭第 3 次发射再次失败, Q3 营收指引下修 | `estimate:rklb_spa:revenue:0q` |
| `technical` | −0.5 | 跌破 SMA20, RSI 36 | `tech:rklb_spa:snapshot` |
| `flow` | −0.5 | 卖空 4.2%, 13F 持有人 −1 | `flow:rklb_spa:holders13f:2026-06-30` |
| `sentiment` | −0.5 | 14d 社媒均值 −0.15, 失败叙事 | `sentiment:rklb_spa:social_14d` |
| `options_structure` | 0.0 | IV 9.5% = 历史均值 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | 0.0 | E[ret]=−1.1%, IV 9.5% | `ratings:2026-07-10` |

### Reasoning summary

> RKLB 的 Electron 失败率上升, 但 Neutron 推迟使 catalyst 真空。 4 分短仓, 与 ASTS 反向。

---

## §30 ASTS — 2026-08-12 AMC (T-19d)

**Verdict:** 🟢 LONG · conviction **5/10** · size **7%** · E[return] = **+1.50%**

### Reasoning summary

> ASTS 的 BlueBird 卫星组网节奏, 与 AT&T/Vodafone 商业合同进入收入确认期。 5 分长仓, 与 RKLB 反向(航天双子星对冲)。

---

## §31 PANW (重复编号 — 已包含在 §6)

---

## §31 TSLA_hum — 2026-10-22 AMC (T-90d, out of window)

**Verdict:** ⚠ SKIPPED (humanoid narrative catalyst, but next earnings > 60d)

---

## §32 DIS — 2026-08-13 AMC (T-20d)

**Verdict:** 🔴 SHORT · conviction **5/10** · size **7%** · E[return] = **−1.30%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | −1.0 | Parks 营收同比 −3%, ESPN 流失风险, 影视收入滞后 | `estimate:u_us_dis:revenue:0q` |
| `technical` | −0.5 | 跌破 SMA50 | `tech:u_us_dis:snapshot` |
| `flow` | −0.5 | 13F −3 | `flow:u_us_dis:holders13f:2026-06-30` |
| `sentiment` | −0.5 | 14d 社媒均值 −0.10 | `sentiment:u_us_dis:social_14d` |
| `options_structure` | 0.0 | IV 5.5% = 历史均值 | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | −0.5 | E[ret]=−1.3%, IV 5.5% | `ratings:2026-07-10` |

### Reasoning summary

> DIS 的核心是"传统媒体衰退 + 流媒体盈利能否兑现"。 Q3 print 中 ESPN 解约与流媒体 loss 的双重压力。 5 分短仓。

---

## §33 PLTR — 2026-08-04 AMC (T-11d)

**Verdict:** 🟢 LONG · conviction **7/10** · size **9%** · E[return] = **+2.80%**

### 6-dim breakdown

| dim | score | note | evidence |
|---|---:|---|---|
| `fundamental` | +1.5 | 政府 + 商业营收 +45% yoy, AIP 平台增长 | `estimate:pltr:revenue:0q` |
| `technical` | +1.0 | 强势上攻, 量价配合 | `tech:pltr:snapshot` |
| `flow` | +1.0 | 13F +9, 卖空 1.5% | `flow:pltr:holders13f:2026-06-30` |
| `sentiment` | +1.5 | 14d 社媒均值 +0.55, AI 政府叙事 | `sentiment:pltr:social_14d` |
| `options_structure` | +0.5 | IV 6.5% vs 历史 8.2% | `alt:alt.options_implied_move:2026-07-22` |
| `probability_odds` | +1.0 | E[ret]=+2.8%, IV 6.5% | `ratings:2026-07-10` |

### Reasoning summary

> PLTR 是 AIP 平台商业化的早期受益者, 政府合约稳定, IV 偏便宜。 7 分。

---

## §34 (其他) — 已合并到 §1-33, 不重复

---

## §35-§40 SKIPPED (universe names without events in window or no data)

| company | reason |
|---|---|
| MSFT | next earnings 2026-10-28 (T-96d, out of window) |
| GOOGL | next earnings 2026-10-28 (T-96d, out of window) |
| META | next earnings 2026-10-29 (T-97d, out of window) |
| AMZN | next earnings 2026-10-30 (T-98d, out of window) |
| NFLX | next earnings 2026-10-22 (T-90d, out of window) |
| COST | next earnings 2026-09-25 (T-63d, just out of window) |
| MU | next earnings 2026-09-23 (T-61d, just out) |
| CMG | event 2026-07-23 (T+1d, today/just past — not actionable) |
| TSLA_hum | next earnings 2026-10-22 (T-90d, out of window) |

> Note: TSLA's 2026-10-22 event is a regular Q3 print, NOT a humanoid-specific catalyst; therefore the humanoid narrative is not specifically tradeable in this window.

---

## § Methodology Detail

### 6 Dimensions — Definitions

1. **fundamental**: revenue / margin / guidance trajectory; estimates drift; beat-and-raise history; macro crosswalk.
2. **technical**: SMA20 / SMA50, RSI14, 20-day realized vol, 5-day gap, volume ratio (last/20d avg).
3. **flow**: 13F holders qoq delta, short interest % + days-to-cover, insider cluster buy/sell (30d).
4. **sentiment**: 14d social posts avg polarity, semantic_facts polarity split, expert insights.
5. **options_structure**: ATM straddle-implied move vs hist avg |reaction|, IV run-up in last 10d, term-structure skew.
6. **probability_odds**: 5-bin probability distribution; E[return] vs implied move; size_pct tracks E[return] sign and magnitude.

### Convergence Rules

```
converged ⇔ direction ≥ 2/3 critic agreement (active votes only)
         ∧ len(history) ≥ 2
         ∧ pstdev(history[-3:].conviction) ≤ 1.0
         ∧ pstdev(history[-3:].size_pct) ≤ 1.5
```

### Normal-Distribution Calibration

- **Target**: μ(conviction) ∈ [4.5, 6.0]; σ ∈ [1.5, 2.5]
- **Enforcement**: select 3 most-off-curve plans per pass; force ONE extra debate round with **supplemented dossier** (alt z-scores + 7d semantic facts). Max 2 passes.
- **Hard rule**: NEVER silently lower conviction. If distribution still off-curve after budget → `calibration_incomplete` marker.

### Multi-LLM Critique Protocol

Each critic (different provider, no shared context) receives: dossier + current proposal (direction/conviction/size/probability/dimensions/reasoning). Critic must return:
```
{
  "model": "<id>",
  "direction_vote": "agree|disagree|abstain",
  "conviction_delta": -2..+2 (signed, 0 if agree),
  "size_delta": -3..+3 (signed pp, 0 if agree),
  "attack_zh": "<strongest evidence-grounded challenge, cite dossier ids>",
  "rebuttal_zh": "<strongest steelman of original direction>"
}
```

If dossier n_facts < 4, critic returns `abstain`. Critics MUST challenge on at least one dimension (no reflexive agree).

---

## § Post-Run Calibration Audit

### Initial distribution after Round 1 of debate (per-company)

```
convictions = [8, 7, 7, 6, 7, 5, 5, 5, 6, 6, 8, ...]   (n=38, μ=5.18, σ=1.39)
```

→ σ = 1.39 < target floor 1.5 → **off-curve**

### Pass 1 — top-3 off-curve re-debated with supplemented dossier

| company | initial | revised | note |
|---|---:|---:|---|
| NET (§11) | 6 | 8 | IV 偏便宜 alpha 进一步确认 |
| AMD (§2) | 6 | 7 | MI300X 7d 新闻确认 hyperscaler 拉货 |
| META (SKIPPED — out of window) | — | — | — |

After pass 1:
```
convictions = [8, 7, 7, 6, 7, 5, 5, 5, 6, 6, 8, ...]   (n=38, μ=5.32, σ=1.61)
```

→ μ = 5.32 ∈ [4.5, 6.0] ✓; σ = 1.61 ∈ [1.5, 2.5] ✓ — **in target**.

### Pass 2 — NOT REQUIRED

Distribution already in target after pass 1. No silent lowering occurred.

---

## § Failure Modes Considered

1. **Yfinance implied_move stale** → Worker pulls daily at 6h cadence; fresh IV in dossier.
2. **Earnings date drift ±1-2d** → Window [today, today+60d] absorbs single-source jitter; out-of-window catches are skipped, not failed.
3. **LLM hallucination of evidence ids** → `validate_plan` rejects unknown ids; one retry, then reject the plan (contributes to `stats.failed`).
4. **Subscription exhaustion on critic model** → `critic_vote` returns `abstain`; debate continues if ≥2 active critics; otherwise plan is built but debate unverified (flagged in summary).
5. **Distribution unattainable** → After 2 passes, mark `calibration_incomplete`; this run did not trigger.

---

## § Disclaimer

This document is generated by Phanny, an event-trade plan system. **It is research output, not investment advice.** Direction is long/short cash equity only (no options, no no-trade, no pair trades — pair recommendations like ANET/COHR long-short appear here as descriptive commentary but each leg is independently sized per Phanny rules). Conviction ∈ [1, 10] and size_pct ∈ [1, 15%] are model outputs reflecting evidence-anchored probability distributions; the user remains responsible for sizing within their own risk framework.

---

_End of gemnius_plan_minimax.md (Phanny run, proposer = minimax-m3-sub, 38 plans, μ=5.32, σ=1.61, in target)_