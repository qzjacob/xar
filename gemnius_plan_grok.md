# Phanny 实现计划（gemnius_plan_grok）

> 状态：**设计定稿，待执行**。  
> 模块名 **Phanny**，与 Chathy / Andy / Genny / Fenny 等平级，导航顺序插在 **Genny 之后**。  
> 目标：对 Genny 库内策展宇宙 `EARNINGS_UNIVERSE` 的下一次季报，产出可执行的 **earnings trade** 方案。  
> 本文件为 Grok 侧设计文档；**禁止**读取 `gemnius_plan_kimi.md` / `gemnius_plan_glm.md` / `gemnius_plan_minimax.md` 等其它 plan 文档。

---

## 0. 已确认决策

| 项 | 决策 |
|---|---|
| 定位 | **升级扩展现有 ET**（`research/earnings.py` + `ontology/earnings_events.py`），非并行引擎、非仅批跑脚本 |
| 公司范围 | `EARNINGS_UNIVERSE`（~33 家美股策展名单，与现有 ET 一致） |
| 方向 | **仅 `long` \| `short`**；禁止中性 / neutral / no_trade / skip；禁止期权策略作为交易观点 |
| Conviction | **1–10**；单票允许低 conviction；**组合级**须近似正态分布 |
| Size | 组合建议仓位 **1–15%**（每票） |
| 收敛纪律 | 多 LLM 对抗辩论至观点收敛；**禁止**仅靠下调 conviction 获得假收敛——须继续补观点/数据 |
| 推理 | 最高级别 `reasoning_effort`；不人为卡算力与数据源；目标最精准交易策略 |
| 本文件用途 | **本任务设计方案文档**；模块运行时产物落 DB/UI，不覆盖本 path 作为 runtime 快照 |

导航顺序：`Chathy | Andy | Genny | **Phanny** | Fenny | Romy | Jarvy`

---

## 1. Context（为什么）

XAR 已具备完整季报事件交易（ET）栈：

- Dossier 11 节 + `EarningsVerdict`（`long` / `short` / `no_trade` + conviction **0–10**）
- 表 `earnings_verdicts`、implied move、beat/hist move、worker 节拍、校准闭环
- **无** `size_pct`、**无**强制 long/short、**无**多厂商对抗收敛、**无**组合 conviction 正态门控、**无**独立产品模块面

Phanny 把 ET 从「单票裁决 + 可 no_trade」推进到：

1. **必选方向** long/short（证据弱 → 低 conviction，不跳过）  
2. **仓位建议** 1–15%  
3. **多 LLM 反方挑战**直至收敛  
4. **Basket 级 conviction 正态分布**硬闸  
5. **一等公民 UI 模块**（与 Genny 深链）

尺度隔离（保持）：

| 域 | 尺度 | 说明 |
|---|---|---|
| `CompanyThesis.conviction` | 1–5 | 长期论点 |
| 旧 ET `EarningsVerdict.conviction` | 0–10（no_trade=0） | 兼容路径可保留 |
| **Phanny** conviction | **1–10**（无 0） | 事件交易；与上两者不换算、不混存 |
| Fenny options conviction | 1–5 | 合约张数映射，正交 |

---

## 2. 现状可复用（不重造）

| 能力 | 路径 |
|---|---|
| Dossier 11 节 + 裁决骨架 | `src/xar/research/earnings.py` |
| 8 维 + universe + validate | `src/xar/ontology/earnings_events.py` |
| IV / implied move | `src/xar/providers/alt/implied_move.py` |
| 资金 / 情绪 / alt | `src/xar/research/flow.py`, `thesis_signals.py`, `thesis_health.py` |
| 期权表面（**只作输入**） | `src/fcn/options/analytics.py` — **禁止**调用 `advisor` / 策略目录作为交易输出 |
| 多空角色辩论模板 | `src/xar/agents/debate.py` |
| 强推理 + pin | `src/xar/models/llm.py`（`reasoning_effort="high"`, `CLAUDE_MAX_PIN` / `CODEX_PIN`） |
| 多厂商并行 | `src/xar/models/subpool.py`（GLM \| MiniMax \| Kimi） |
| Worker 节拍 | `src/xar/orchestration/glm_worker.py` 的 `earnings_*` |
| UI 壳 / 模块注册 | `web/src/lib/modules.ts`, `App.tsx`, `ModuleShell` |
| 公司宇宙 | `EARNINGS_UNIVERSE` ∩ `ingestion/registry.COMPANIES` |

**缺口**：`size_pct`、Phanny 仅 long/short、多 LLM 对抗收敛、组合正态门控、模块 API/前端。

---

## 3. 架构总览

```
EARNINGS_UNIVERSE
       │
       ▼
┌──────────────────┐     复用 dossier / flow / alt / IV / Fenny surface(只读)
│  Phanny Research │
│  (最高 effort)   │     基本面 / 技术面 / 资金面 / 情绪面 / 期权结构观察 / 概率赔率
└────────┬─────────┘
         ▼
┌──────────────────┐     long|short + conv 1-10 + size 1-15 + 推理链
│ Opening Stance   │     pin: Claude-Max / Codex / strong token
└────────┬─────────┘
         ▼
┌──────────────────┐     反方 = 另一厂商 LLM；轮换 Kimi / GLM / MiniMax / DeepSeek
│ Adversarial Loop │     直到 direction 稳定 + conv 差≤ε + 证据补全
│ (多厂商 challenge)│     ✗ 禁止仅靠下调 conviction「假收敛」
└────────┬─────────┘
         ▼
┌──────────────────┐     分箱 / 矩条件 vs 目标正态；失败 → 补数据/再辩论
│ Basket Gate      │     不得批量 haircut conviction
└────────┬─────────┘
         ▼
 earnings_verdicts (扩展 size_pct + content) + Phanny UI + 可选 Markdown 导出
```

推理维度（opening + debate 共用，不限于）：

1. **基本面**：指引习惯、一致预期与修订、beat 惯例、guidance 兑现  
2. **技术面**：财报前价格位置、实现波动、历史财报日 |move|  
3. **资金面**：`flow_snapshot`、主力/成交异常、short interest 等  
4. **情绪面**：社媒极性、semantic_facts、expert_insights、评级动量  
5. **期权结构（观察）**：ATM implied move vs 历史、IV run-up、可选 Fenny surface（skew/RR/IV-RV）；**不得**输出 straddle/iron condor 等策略作为 direction/plan 主观点  
6. **概率赔率**：asymmetry、expected surprise vs priced move、校准桶 hit_rate（若有）

---

## 4. 数据契约

### 4.1 Schema（建议 `src/xar/ontology/phanny.py`，或扩展 `earnings_events.py`）

```python
PHANNY_DIRECTIONS = ("long", "short")  # 无 no_trade / neutral

class PhannyTrade(BaseModel):
    direction: Literal["long", "short"]
    conviction: float = Field(ge=1, le=10)
    size_pct: float = Field(ge=1, le=15)
    expected_surprise_zh: str
    move_view_zh: str
    dimensions: list[DimensionRead]  # 复用 ET 8 维词表
    plan_zh: str                     # 进出场；禁止期权策略名作为主策略
    falsifiers_zh: list[str]
    asymmetry_zh: str
    odds_zh: str = ""
    prob_edge_zh: str = ""
    options_structure_note_zh: str = ""  # 仅结构观察
    reasoning_trace_zh: str = ""
    # debate_rounds 可放 quality/content 附属，不强制进 LLM schema

class PhannyNameResult(BaseModel):
    company_id: str
    event_date: date
    trade: PhannyTrade
    debate_log: list[dict] = []
    status: Literal["converged", "rejected_debate", "no_data"]

class PhannyBasket(BaseModel):
    as_of: date
    trades: list[PhannyNameResult]  # 每名尽量必有 trade；禁止以 neutral 跳过
    conviction_stats: dict          # mean, std, skew, bins, test metrics
    status: Literal["converged", "rejected_distribution", "partial"]
```

### 4.2 与 `earnings_verdicts` 兼容

1. **DDL**：`ALTER TABLE earnings_verdicts ADD COLUMN IF NOT EXISTS size_pct REAL;`（nullable，旧行空）  
2. **direction CHECK**：过渡期仍允 `long|short|no_trade`；**Phanny 写入路径只写 long/short**  
3. **content JSONB**：完整 `PhannyTrade` + `debate_log` / `phanny_version`  
4. **版本语义**：同 `(company_id, event_date)` `version = max+1`；INSERT 即锁；仅 `--force` 重跑  
5. **旧 ET** `build_verdict` 可继续写 `no_trade`（Jarvy/旧 CLI）；Phanny 批跑可对新 version 覆盖同事件  

### 4.3 `validate_phanny_trade`

- `direction ∈ {long, short}`  
- `conviction ∈ [1, 10]`，`size_pct ∈ [1, 15]`  
- 全部 evidence id ∈ dossier `known_ids`  
- dimension keys ∈ `EARNINGS_DIMENSIONS` 且不重复  
- `conviction ≥ 7` → 去重 anchors ≥ 6 + `asymmetry_zh` 非空  
- **禁止** plan/direction 主语义为期权策略族（关键词闸：straddle, strangle, iron condor, butterfly, calendar spread 等作为「交易观点」）  
- `options_structure_note_zh` 允许描述性 IV/skew 语言  

### 4.4 组合 conviction 正态门控（硬约束）

- 对象：basket 内全部已收敛名的 `conviction`  
- 目标形态：近似 \(\mathcal{N}(\mu \approx 5\text{–}6,\ \sigma \approx 1.5\text{–}2)\)  
- 失败信号（示例，实现时可调参）：  
  - `std` 过小（几乎同一 conviction）  
  - 全员 ≤ 4 或全员 ≥ 8（单侧塌缩）  
  - 分箱占比严重偏离目标正态桶（如 1–3 / 4–5 / 6–7 / 8–10）  
- **失败时动作**：对薄弱名 **补 dossier / 拉新数据 / 再开辩论轮**；**禁止**全局 `conviction *= k` 或统一下调以「通过」检验  
- 每 basket 最多 N 轮补强（建议 3）；仍失败 → `status=rejected_distribution`，**不入库伪收敛 basket**  

### 4.5 Size 规则（可解释 + LLM 微调）

```
base = f(conviction)     # 例: 1→1%, 5→5%, 7→8%, 10→12%
× edge_factor(implied vs hist_move, asymmetry 方向)
× (0.7 if thesis health challenged else 1.0)
→ clip [1, 15]
```

LLM 可在规则结果上微调 ±2pp，最终仍过 `validate_phanny_trade`。

---

## 5. 后端包结构

### 5.1 新建 `src/xar/phanny/`

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包导出 |
| `research_pack.py` | 组装：`dossier_earnings` + `flow_snapshot` + `signal_snapshot` + `health_v3` + beat/hist/IV + 可选 Fenny `analyze_surface` |
| `reason.py` | Opening stance：最高 `reasoning_effort`，强 pin，`complete_json` → `PhannyTrade` 草案 |
| `debate.py` | 多厂商对抗循环 + 收敛谓词 + 「禁止假降 conv」检测 |
| `size.py` | 确定性 size + 可选 LLM 微调闸 |
| `distribution.py` | basket 正态/分箱检验 + 补强候选名排序 |
| `pipeline.py` | `run_name(cid)` / `run_basket()` 主编排 |
| `export.py` | 可选 Markdown/JSON 导出（**非**覆盖 `gemnius_plan_grok.md`） |

### 5.2 多 LLM 辩论收敛（核心）

```
opening = reason(pack, pin=STRONG_A, effort=high)   # long|short + conv + 初 size

for round in 1..MAX_ROUNDS:                          # 建议默认 4
    challenger_pin = next_vendor(≠ last speaker)
    challenge = complete(anti_thesis | opening, pin=challenger, effort=high)
    # 若 challenger 指出数据缺口 → fetch_extra(pack) 后 continue（不降 conv）
    rebuttal = complete(defend + new_evidence, pin=STRONG_A, effort=high)
    draft = merge_to_PhannyTrade(opening, challenge, rebuttal)
    problems = validate_phanny_trade(draft, known_ids=...)
    if problems: continue with repair prompt

    if direction_stable(last 2 rounds)
       and |Δconviction| ≤ 1.0
       and not conviction_only_haircut(prev, draft)   # 关键：假收敛检测
       and validate OK:
         break
else:
    status = rejected_debate
```

**假收敛检测 `conviction_only_haircut`**：若 direction 未变、证据锚未增、仅 conviction 下调导致「双方同意」→ **不视为收敛**，必须补数据或实质性改写 asymmetry/dimensions。

**厂商轮换**（按环境可用性）：

- Opening / 主辩：`CODEX_PIN` 或 `CLAUDE_MAX_PIN` 或 strong DeepSeek token  
- Challenger：`kimi-k3-sub` → `glm-5.2-sub` → `minimax-m3-sub` → strong DeepSeek  
- 仓库 **无** Grok/xAI provider 时不虚构调用  

### 5.3 路由与配置

**`models/router.py`**：

- `TaskClass.PHANNY_REASON` → STRONG + TOKEN（同 `EARNINGS_JUDGE` / `DEBATE`）  
- `TaskClass.PHANNY_DEBATE` → STRONG + TOKEN  
- 可选 `PHANNY_JUDGE` 用于轮末结构化收敛判定  

**`config.py`（`XAR_PHANNY_*`）示例**：

```python
phanny_max_debate_rounds: int = 4
phanny_conv_dist_min_std: float = 1.2
phanny_conv_dist_target_mean: float = 5.5
phanny_size_min: float = 1.0
phanny_size_max: float = 15.0
phanny_host_only: bool = False   # True → docker worker deferred，host 专跑
phanny_reasoning_effort: str = "high"
```

### 5.4 CLI

```text
xar phanny run [--cid ID] [--force]
xar phanny status
xar phanny export [--format md|json]
```

### 5.5 API `src/xar/api/phanny.py` + `app.py` 注册

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/phanny/basket` | 当前窗全部 trades + 分布统计 |
| GET | `/api/phanny/company/{id}` | 单票 + debate_log |
| POST | `/api/phanny/run` | 异步 basket 批跑 |
| POST | `/api/phanny/company/{id}/run` | 异步单票 |

Capabilities（可选 Chathy）：`phanny_panel`（read）、`build_phanny_trade`（build）。

### 5.6 Worker

- 扩展 `_earnings_step` 或独立 `_phanny_step`（建议 24h cadence）  
- 放在 **GLM pin/quota 门之外**（与现有 earnings verdict / research_audit 同款）  
- 数据依赖仍走 `earnings.refresh_window()`（6h）  

### 5.7 DDL

```sql
ALTER TABLE earnings_verdicts
  ADD COLUMN IF NOT EXISTS size_pct REAL;
-- content JSONB 承载 Phanny 扩展；quality 可含 phanny_version / debate_rounds
```

---

## 6. 前端模块

1. **`web/src/lib/modules.ts`**  
   - `ModuleKey` 增加 `"phanny"`  
   - `MODULES` 数组插在 **genny 之后**：  
     `{ key: "phanny", label: "Phanny", cn: "季报交易", route: "/phanny", icon: …, match: p => p.startsWith("/phanny") }`

2. **`web/src/App.tsx`**  
   - `lazy(() => import("./pages/phanny/PhannyApp"))`  
   - 路由 `/phanny/*`

3. **页面** `web/src/pages/phanny/`  
   - `PhannyApp.tsx`：`ModuleShell` + 侧栏  
   - Basket 表：ticker / 事件日 / long|short / conviction / size% / 状态  
   - 单票页：推理摘要、8 维、辩论时间线、期权结构观察、falsifiers  
   - 分布直方图（conviction）  
   - Run / Force 按钮（调 API）

4. **`web/src/lib/phanny.ts` + `types-phanny.ts`**

5. **Genny 深链**  
   - `EarningsSection` / `CompanyPage` → `/phanny/company/:id`  
   - 反向：Phanny 行 → `/genny/company/:id`

6. **主题**  
   - 沿用终端 accent；图标建议 `TrendingUp` / 类似 lucide 图标  

---

## 7. 测试计划

| 文件 | 覆盖 |
|---|---|
| `tests/test_phanny_ontology.py` | schema；仅 long/short；size 界；禁期权策略关键词；conv 1–10 |
| `tests/test_phanny_distribution.py` | 正态 pass/fail；haircut-only 必须 fail |
| `tests/test_phanny_debate.py` | mock 多 pin 收敛 / 不收敛 / 假降 conv 拒绝 |
| `tests/test_phanny_pipeline.py` | seeded dossier → 入库含 `size_pct` |
| `tests/test_phanny_api.py` | basket / company 端点 |
| 既有 `tests/test_earnings_*.py` | 回归：旧 no_trade 路径不破 |

每阶段：相关 pytest 绿 + ruff。

---

## 8. 实施阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0** | 本体 `PhannyTrade` + validate + DDL `size_pct` + TaskClass + 本文件已落盘 | ontology 单测绿 |
| **P1** | `research_pack` + `reason`（单模型 high-effort）+ `size` + CLI 单票 | 单票可出 long/short+size |
| **P2** | 多厂商 `debate` + 收敛谓词 + 假降 conv 闸 | debate 单测绿 |
| **P3** | `run_basket` + distribution 门 + 补强循环 + 版本化入库 | basket 门控单测绿 |
| **P4** | API + capabilities + worker 节拍 | API 单测绿 |
| **P5** | 前端 modules + App + Basket/Detail + Genny 深链 | 手测导航在 Genny 后 |
| **P6** | 全量测试 + ruff；README / UI.md / DESIGN §2 补 Phanny 一行 | CI 绿 |

---

## 9. 明确不做

- 期权策略作为交易观点（straddle 等仅可出现在「结构观察」笔记）  
- Phanny 路径 direction = neutral / 中性 / no_trade / skip  
- 读取其它 `gemnius_plan_{kimi,glm,minimax}.md`  
- 为 Phanny 新建平行 company universe（不 fork `COMPANIES`）  
- thesis conviction(1–5) 与 Phanny(1–10) 混算或混存  
- 仅靠统一下调 conviction 通过 basket 正态门  
- 运行时产物覆盖写入本文件 `gemnius_plan_grok.md`  

---

## 10. 关键文件索引（实现时）

| 关注点 | 路径 |
|---|---|
| 模块导航 | `web/src/lib/modules.ts` |
| SPA 路由 | `web/src/App.tsx` |
| FastAPI 注册 | `src/xar/api/app.py` |
| ET 引擎 | `src/xar/research/earnings.py` |
| ET 本体 | `src/xar/ontology/earnings_events.py` |
| Phanny 包（新建） | `src/xar/phanny/*` |
| Phanny API（新建） | `src/xar/api/phanny.py` |
| LLM 路由 | `src/xar/models/router.py` |
| LLM 执行 | `src/xar/models/llm.py` |
| Subpool | `src/xar/models/subpool.py` |
| Worker | `src/xar/orchestration/glm_worker.py` |
| Schema | `src/xar/storage/schema.sql` |
| Config | `src/xar/config.py` |
| CLI | `src/xar/cli.py` |
| 本设计文档 | `gemnius_plan_grok.md` |

---

## 11. 执行入口（下一步）

1. ~~将本计划写入 `gemnius_plan_grok.md`~~ **（本文件）**  
2. 落地 **P0**：本体 / 路由 / DDL  
3. 按 **P1 → P6** 实现并测试  

---

*文档版本：2026-07-24 · 作者侧：Grok plan · 项目：XAR Phanny*
