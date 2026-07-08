# UNIFIED_ARCH_PLAN — UA:统一能力架构(全模块链路耦合)

> 状态:**设计定稿,待执行**。任务前缀 **UA-**,阶段 P0–P6,每阶段独立绿。
> 目标:**一处定义分析能力 → Chathy 对话、Genny 页面按钮、通用 API、CLI、worker 全部走同一入口**;
> 并完成 ET 三向耦合(裁决 dossier 吃 Andy 宏观 + Genny 主题争论;公司页「跑 ET」按钮;Chathy 全套工具)。
> 唯一新表 `capability_runs`;其余全部复用/重排既有基础设施。

---

## 0. Context(为什么)

ET(季报事件交易)是孤岛:裁决 dossier 没有 Andy 宏观与 Genny 主题争论输入;Genny 公司页
(`/genny/company/:id`)无「跑 ET」入口;Chathy 够不到 ET。更深层的架构债(4 角侦察实证):

- **同一能力四处重复接线**:thesis build 有 4 个入口(CLI/`POST /api/thesis/{cid}/build`/worker/Chathy get_thesis),
  earnings judge 有 3 个(CLI/ops API/worker)但**缺 UI 与 Chathy**;报告/探索各 2-3 个。
- **4 种互不相同的异步触发风格**并存:fire-and-forget BackgroundTasks(无 run id 无法轮询,
  `POST /api/ops/earnings/{cid}/judge` 即此)、同步阻塞(`/api/thesis/{cid}/build` ~60s)、
  report_runs 状态机(带审批,但同步跑)、Fenny 内存 job+轮询(`lib/fenny.ts runJob`,全库唯一成熟轮询范式)。
- **5 个状态存储**互不相通:report_runs / ingest_runs / kvstate / fcn _JOBS / llm_usage.run_id。
- **模块耦合缺失**:`earnings.py` 零 andy/macro import;`thesis.dossier` 宏观节只贴静态 rationale
  (不调 `link_theme` 活读数);`theme_debate_health` 存在但零消费者;Explorer 全孤立;
  Chathy 工具结果渲染为不可点的 `<pre>`,Genny 页面无「问 Chathy」。

**用户已确认**:①机制 = **能力注册表**;②Chathy = **读 + 触发**(可跑 ET 裁决/重建论点);③**全量一期**。

### 已实证的复用资产(file:line)
- `src/xar/chathy/tools.py` `TOOLS`(24 个 `ToolSpec{name, description, parameters JSON-schema, fn}`,
  进程内直调「no HTTP hop」,`execute()` 永不 raise + 8k JSON 安全截断)——**全库最接近能力注册表的存在**;
  docstring 本就预告向 Fenny/Genny 泛化;`get_thesis(refresh=true)` 是唯一触发型工具先例("slow, seconds")。
- `src/xar/api/andy_links.py::link_theme(theme, as_of)` L110 —— 每主题**PIT 活读数**
  (值/斜率/12 点序列 + 识别水印 + overclaim 状态),slx 不可用内部 try/except 优雅降级;
  Chathy `_macro_indicators` L118-145 已有 8k 压缩器(drop series 保读数+水印)。
- `src/xar/research/thesis_health.py::theme_debate_health(theme)` L155(主题旗舰争论 lean 聚合 + 翻转清单),
  路由 `GET /api/themes/{tid}/debates` 存在但**零消费者**。
- `web/src/components/ThesisSection.tsx` 生成论点按钮 → `api.buildThesis`(同步 POST→onRefetch)
  = UI 触发分析的既有范式;`web/src/lib/fenny.ts::runJob` = POST→轮询范式。
- `src/xar/agents/graph.py::run_report` + `report_runs` 表(run_id+审批状态机)——已有但同步跑、
  nodes.py 早于 thesis/earnings/andy(报告不吃这些块 = 会显得降级)。
- 分层安全已核:**slx 永不 import xar**(单向);风险在 research→api 倒置(dossier 要活宏观)。

---

## 1. 顶层裁决

1. **`POST /api/run/{capability}` 成为规范触发入口**。
   - `POST /api/ops/earnings/{cid}/judge` **保留为 shim**:内部 `runs.schedule(...)` + BackgroundTasks,
     响应保持 `{status:"scheduled", ...}` **加新字段 `run_id`**(加性,不破坏)。
   - `POST /api/thesis/{cid}/build` **保持同步不动**(ThesisSection 的 60s await 契约可用;文档标注为
     同步特例);`build_thesis` 另注册为异步能力供 Chathy/CLI/通用 API。
2. **CLI**:新 `xar run <capability> [--args JSON]`(inline 执行,落 capability_runs 行 origin=cli)
   + `xar capabilities`(清单);既有 `xar earnings/thesis` 命令不动。
3. **Chathy 慢动作(分钟级)一律 schedule + 返回 run_id**——同步分钟级工具会卡死 SSE turn;
   新 `run_status` 工具闭环;"slow, seconds" 级(get_thesis refresh)保持同步。
4. **dossier 级耦合 = 进程内普通函数调用,不走注册表**。注册表管**入口(triggers)**;
   dossier 组合走层函数;`dashboard._*_block` 仍是读契约(加块 → 加前端 Section)。
5. **research→api 倒置**由新 `src/xar/macro/view.py` 解决:link_theme 逻辑整体迁入,
   `api/andy_links.link_theme` 变委托(路由契约字节级不变);research 只 import `..macro`。
6. **glm_worker 不动**(judge_due 直调层函数,不需要 job 表);防双跑双保险:
   `uq_capruns_active` 部分唯一索引(capability+args_hash 活跃去重)+ `build_verdict` 的
   INSERT 锁(skipped/raced)。
7. **明确不做**:事件总线、插件发现、模块间 HTTP 自调(repo 惯例进程内直调)、
   吞并 Fenny 的 `_JOBS`(挂载子应用自治,只加只读报价工具)。

---

## 2. 分阶段(每阶段 pytest+ruff 独立绿)

### UA-P0 — 能力注册表(零行为变化)

- **新建 `src/xar/capabilities/__init__.py` + `src/xar/capabilities/registry.py`**(骨架附录 A):
  `CapabilitySpec` = ToolSpec 字段 + `kind: "read"|"build"` + `duration: "fast"|"slow"` + `chathy: bool`;
  **迁入** chathy/tools.py 的 24 个 ToolSpec 与全部私有实现(_find_company/_macro_indicators/_dash/_graph…);
  `by_name()/chathy_specs()/openai_tool_defs()/execute()`(8k 截断循环原样迁)。
- **`src/xar/chathy/tools.py` 变 re-export shim**:`ToolSpec = CapabilitySpec`、`TOOLS = chathy_specs()`、
  re-export execute/openai_tool_defs/_MAX_RESULT_CHARS —— `agent.py` 与既有测试 import 面不变。
- **测试 `tests/test_capabilities.py`**:名字唯一/parameters type=object/kind-duration 枚举合法;
  **parity 锁**(`tools.TOOLS` 名单 == 迁移前 24);`execute("coverage", …)` 可跑(seeded_db);
  未知工具 → 错误 JSON 不 raise。

### UA-P1 — capability_runs + 通用 run API + CLI

- **DDL(唯一新表,schema.sql 底部加性幂等,全文附录 B)**:`capability_runs`
  (id uuid hex PK / capability / args JSONB / args_hash / status queued|running|done|error CHECK /
  result JSONB / error / origin chathy|ui|api|cli / created_at / started_at / finished_at)+
  `idx_capruns_recent(capability, created_at DESC)` +
  **`uq_capruns_active UNIQUE(capability, args_hash) WHERE status IN ('queued','running')`**。
- **新建 `src/xar/capabilities/runs.py`**(规格附录 C):
  `schedule(name, args, *, origin)`(>30min 陈旧 running 先标 error:'stale' 再放行;活跃去重命中 →
  返回既有 `{run_id, status, dedup:True}`;并发撞唯一索引 → 读回既有行)/
  `execute_run(run_id)`(running→fn(**args)→done+result 或 error;**绝不 raise**,BackgroundTasks 安全)/
  `status(run_id)` / `recent(capability?, limit)`。
- **注册 build 能力**(kind=build, duration=slow, `chathy=False`——Chathy 包装在 P3):
  `build_earnings_verdict{company_id, force}` → `research.earnings.build_verdict`(lazy import);
  `build_thesis{company_id, force}` → `research.thesis.build`;
  `refresh_exploration{domain?}` → exploration ingest+synthesis;
  `report{company_id, kind='company', since?}` → `agents.graph.run_report`(DAG 补喂在 P5)。
- **`src/xar/api/app.py`**(mounts 之前注册;全新前缀,`/api/andy/link/*` 顺序不动):
  `GET /api/capabilities`(清单)/ `POST /api/run/{name}`(read+fast → 内联执行返回
  `{status:'done', result}`;build|slow → schedule + `bg.add_task(execute_run)` →
  `{run_id, status, dedup?}`;未知 404)/ `GET /api/run/{run_id}` / `GET /api/runs`。
  `ops_earnings_judge` shim 化(裁决 1)。
- **`src/xar/cli.py`**:`xar run <name> --args '{...}'`(schedule origin=cli → execute_run inline →
  rich 打印 result)+ `xar capabilities`。
- **测试 `tests/test_capability_runs.py`**(seeded_db + 假 fn 注册 + TestClient):
  schedule→execute→done result 落库;同参去重同 run_id;异参新 run;fn 抛错→status=error 不上抛;
  stale 收割;`POST /api/run/unknown`→404;read 内联;build 返 run_id;`GET /api/run/{id}`;
  judge shim 响应带 run_id。

### UA-P2 — ET 三耦合之 dossier 注入(宏观 + 主题争论)

- **新建 `src/xar/macro/__init__.py` + `src/xar/macro/view.py`**(骨架附录 D;research 可 import,
  **禁 import `..api`**):
  - `theme_macro_view(theme, as_of=None) -> dict|None`:**整体迁入** andy_links.link_theme 逻辑
    (PIT 值/斜率/12 点序列 + 水印 + overclaims;slx 不可用降级 static rationale + `live=False`);
  - `compact_theme_macro(view, max_metrics=8) -> dict`:提炼自 _macro_indicators
    (drop series,保 value/slope/identification_status/watermark——**soft ⇒「未识别·勿作因果」必须原文到模型**);
  - `macro_dossier_lines(themes, as_of=None, per_theme=5) -> (list[str], set[str])`:
    dossier 注入行 + known_ids;id 格式沿用 `[registry:macro:<key>]`(与论点证据格式兼容),
    有活读数追加 值/slope/valid_time+水印后缀。
- **`src/xar/api/andy_links.py`**:link_theme 委托 macro.view(路由契约不变);
  registry 的 `_macro_indicators` 改用 `compact_theme_macro`(三处复制归一)。
- **`src/xar/research/earnings.py dossier_earnings`** 加两节(§8 alt 与 §9 thesis 之间,
  各自 `_sect` 容错,known_ids 注册 → `validate_verdict` 可引用):
  - `_macro`:前 2 主题 `macro_dossier_lines`(as_of=dossier 当日,裁决在事件前跑 → PIT 安全);
  - `_theme_debates`:`theme_debate_health(t)`(mean_lean/flipped/members,
    id `theme_debate:<theme>:<key>`)。
- **`src/xar/research/thesis.py` dossier** 宏观节(L135-146)从静态 rationale 升级为同一
  `macro_dossier_lines`(id 前缀不变,live 读数有则带)。
- **测试**:`tests/test_macro_view.py`(monkeypatch slx 导入失败 → 降级 live=False;压缩器 drop series
  保水印;link_theme 委托后形状对齐);`tests/test_earnings_dossier.py` 增(宏观+争论节出现在
  text/panel、known_ids 增 `registry:macro:*`/`theme_debate:*`、view 抛错 dossier 仍返);
  `tests/test_thesis_build.py` 增(`[registry:macro:` 前缀保持)。

### UA-P3 — Chathy 全套工具(ET / 探索 / Fenny / 报告 / run 状态)

registry 追加(全 `chathy=True`;输出全部压缩至 `_MAX_RESULT_CHARS=8000` 内;schema 详表附录 E):

| 工具 | kind/duration | 行为 |
|---|---|---|
| `earnings_panel{company_id}` | read/fast | `dashboard._earnings_block` + `_next_earnings` 头;非 universe 名字诚实说明 |
| `earnings_verdict{company_id, refresh=false, force=false}` | read/fast | 读 latest_verdict(维度裁剪);**refresh=true → `runs.schedule("build_earnings_verdict", origin="chathy")` → `{scheduled:true, run_id, note:"分钟级;用 run_status 查询,勿轮询"}`** |
| `run_status{run_id}` | read/fast | runs.status(result 压缩) |
| `theme_debates{theme}` | read/fast | theme_debate_health(by_company 截 8) |
| `exploration_frontier{domain?}` | read/fast | exploration.section/overview 压缩 |
| `fenny_quote{termsheet, market?}` | read/fast | 进程内 fcn 报价(no HTTP hop;description 指明 preset 字段名) |
| `start_report{company_id, since?}` | build/slow | runs.schedule("report") → run_id |

`build_thesis`/`build_earnings_verdict`/`refresh_exploration` 保持 `chathy=False`
(Chathy 经 get_thesis(refresh)/earnings_verdict(refresh) 触达;探索刷新是 worker/ops 面)。

- **测试 `tests/test_chathy_capabilities.py`**:schema 合法;earnings_verdict 读 2099 种子行;
  **refresh=true 只 schedule 不内联**(monkeypatch runs.schedule,断言 build_verdict 未被直调);
  run_status 回环;theme_debates 压缩;fenny_quote 小 preset(MC 引擎打桩);start_report schedule;
  全部新工具 `len(execute(...)) <= 8000`。

### UA-P4 — 前端:run 客户端 + ET 按钮 + 跨模块语境

- **新建 `web/src/lib/runs.ts`**(fenny runJob 移植;规格附录 F):
  `runCapability(name, args, onPoll?, signal?, opts?)` = POST `/api/run/{name}` → 轮询
  GET `/api/run/{run_id}` @1500ms×400(10 分钟窗);dedup 命中(既有 run_id)照常轮询。
  新 `web/src/types-runs.ts`(RunStatus)。
- **`web/src/components/EarningsSection.tsx`** props 改
  `{cid, earnings, onRefetch}`;头部右侧「**跑 ET 裁决 Run verdict**」按钮
  (已有裁决时 label「重跑 (force)」→ `{force:true}`);idle/`裁决生成中…`/error 三态;
  done → `await onRefetch()`(镜像 ThesisSection 流)。
  `CompanyPage.tsx` 传 `cid={company.id} onRefetch={refetch}`;公司头加「**问 Chathy Ask Chathy**」
  → `navigate("/?q=" + encodeURIComponent("分析 {name}({cid}):论点健康度、临近财报裁决与宏观勾稽语境"))`。
- **`web/src/pages/chathy/ChathyPage.tsx`**:`useSearchParams()` 一次性消费 `?q=`
  (consumedRef + `setSearchParams({}, {replace:true})`)→ 自动 `send(q)`(send 已自动建会话)。
- **`web/src/components/chathy/ToolChip.tsx`(+ ChatMessage 小助手)**:工具结果 preview 里
  `"(?:company_)?id"\s*:\s*"([a-z0-9_]+)"` 正则提取 → 去重 → `/genny/company/{id}` deep-link chips
  (错配 id 只会落到公司页 not-found 态,无害)。其余新工具零前端改动(通用 ToolChip 契约)。
- **验证门**:`cd web && npx tsc --noEmit && npm run build`(无 web 单测框架;行为归 P6 冒烟)。

### UA-P5 — 报告 DAG 补喂(**最后做**,报告 ≠ 降级)

- **`src/xar/agents/nodes.py`**:`graph_retrieve` 增填(各 try/except fail-soft):
  `state.graph.thesis`(thesis.latest + health_v3 压缩)、`state.graph.earnings`
  (`dashboard._earnings_block(cid)`——agents 可 import api,它是读层)、
  `state.graph.macro`(`compact_theme_macro(theme_macro_view(t))` ≤2 主题);
  `_graph_brief` 渲染三新砖:`## 投资论点`(stance/conviction/健康度/争论)、
  `## 季报事件`(下一事件/裁决/beat 习惯)、`## 宏观勾稽`(活读数+水印)。
- `graph.py` 结构不动(report_runs 审批状态机仍权威);P1 已注册 `report` 能力 →
  Chathy start_report → capability_runs.result 内含报告 run_id,run_status 指向 `/api/report/{run_id}`。
- **测试**:graph_retrieve 三新键(层函数打桩)/_graph_brief 三砖出现/report 能力可 schedule
  (run_report 打桩)。

### UA-P6 — 文档 + 全量验证 + 真机冒烟

- `DESIGN.md` 新 **§5.15 统一能力架构(UA,As-Built)**(CapabilitySpec/capability_runs/
  `/api/run/*`/`xar run`/ET 三耦合/Chathy 新工具/跨模块语境/报告补喂)+ §5.14 加指针;
  UI.md 记「跑 ET / 问 Chathy」。
- 全量 pytest + ruff + `npm run build`;`xar init` 幂等(新表)。
- **真机冒烟(host,cid=now/ServiceNow;脚本附录 G)**:六步——能力清单 → 通用 API 跑 ET 裁决(轮询→
  result)→ 公司页 bundle 出现裁决 → Chathy SSE 对话触发 refresh(拿 run_id)→ dossier 出现
  宏观勾稽节 + theme_debates 能力返回 mean_lean → 浏览器手测(按钮/问 Chathy/deep-link)。
- 完成后**独立对抗代码评审**(house 惯例)→ 修复 → 汇报(推送/合并/部署经用户确认)。

---

## 3. 成本纪律

| 项 | 说明 |
|---|---|
| 新增 LLM 消耗 | 零新常驻消耗——注册表/run 表/前端全是接线;Chathy 触发的裁决/论点与既有成本同源(逐次、有界) |
| 轮询负载 | 1.5s×10min 帽,单 run 一个客户端;`GET /api/run/{id}` 为 O(1) 主键读 |
| 8k 工具预算 | 全部新读走压缩器;慢动作只返 {run_id} |

## 4. 风险

| 风险 | 缓解 |
|---|---|
| research→api 分层倒置(dossier 要活宏观) | `src/xar/macro/view.py` 承接;andy_links 委托;xar→slx 保持单向(已验证 slx 零 xar import) |
| slx 不可用(api 容器/裸机 CLI) | theme_macro_view 降级 static `THEME_TO_METRICS` rationale(live=False);dossier 节 fail-soft |
| worker judge_due vs UI/Chathy 双跑同一 cid | `uq_capruns_active` 活跃去重 + build_verdict INSERT 锁(skipped/raced)——双保险,最坏重复成本非数据损坏 |
| BackgroundTasks 进程死 → running 僵尸行 | schedule() 内 >30min stale 收割(标 error:'stale' 放行新 run);UI 轮询可见 error |
| SSE 8k 工具预算 / 分钟级工具卡流 | 慢动作一律 schedule+run_id;工具 description 明示「勿轮询,告知用户稍后查询」 |
| 迁 24 ToolSpec 破坏 Chathy | tools.py re-export shim 保 import 面 + parity 测试锁名单 |
| docker api 容器执行 UI 触发裁决(无订阅执行器) | `_preferred_pin` → token 兜底,model 落库可分层校准;`earnings_verdict_host_only=True` 时 deferred_host 透传到 run.result |
| 路由遮蔽 | `/api/run|/api/runs|/api/capabilities` 全新前缀且 mounts 前注册;`/api/andy/link/*` 顺序不动 |
| 报告 DAG 陈旧(P5 前 start_report 即可用) | P5 前报告仍产出(旧口径);P5 补喂后才在 Chathy 里宣传 |

## 5. 明确不做(本期)

事件总线/消息队列、插件动态发现、模块间 HTTP 自调、吞并 Fenny `_JOBS`(只加只读报价工具)、
ThesisSection 迁移到 runs.ts(同步契约可用,后置)、Chathy 全局页面上下文 store(用 `?q=` 深链即够)、
report 审批 UI(报告状态机既有面不动)。

---

---

# 附录(可执行细节)

## 附录 A — `capabilities/registry.py` 骨架

```python
"""能力登记簿(代码即真相):一处定义 → Chathy 工具 + /api/run + UI 按钮 + CLI + worker。

由 chathy/tools.py 的 ToolSpec 泛化而来(24 个既有工具原样迁入,kind=read/duration=fast);
chathy/tools.py 保留为 re-export shim,import 面不变。
"""
from dataclasses import dataclass
from collections.abc import Callable

@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    parameters: dict                 # JSON Schema(type=object)
    fn: Callable[..., object]
    kind: str = "read"               # read | build(写库/生成)
    duration: str = "fast"           # fast(即答)| slow(分钟级 → 必走 capability_runs)
    chathy: bool = True              # 是否渲染为 Chathy 工具

CAPABILITIES: list[CapabilitySpec] = [
    # ── 迁入的 24 个 Chathy 工具(read/fast,原样)──
    # find_company, semantic_facts, search_documents, theme_overview, list_companies,
    # company_detail, segment_detail, list_segments, signals, catalysts, calendar,
    # theme_landscape, regime, decision, coverage, supply_chain, company_competitors,
    # single_source_risks, events, dataroom_docs, get_thesis, alt_signals, coverage_360,
    # macro_indicators
    # ── UA-P1 build 能力(chathy=False)──
    # build_earnings_verdict / build_thesis / refresh_exploration / report
    # ── UA-P3 Chathy 新工具(附录 E)──
]

def by_name(name: str) -> CapabilitySpec | None: ...
def chathy_specs() -> list[CapabilitySpec]: ...      # [c for c in CAPABILITIES if c.chathy]
def openai_tool_defs() -> list[dict]: ...            # 自 chathy/tools.py 迁入
def execute(name: str, args: dict) -> str: ...       # 自 chathy/tools.py 迁入(8k 截断循环原样)
```

## 附录 B — `capability_runs` DDL(schema.sql 底部,加性幂等)

```sql
-- ── UA-P1:能力运行表(统一异步触发;修复四种触发风格分裂)。running 去重靠部分唯一索引 ──
CREATE TABLE IF NOT EXISTS capability_runs (
    id          TEXT PRIMARY KEY,              -- uuid4 hex
    capability  TEXT NOT NULL,
    args        JSONB NOT NULL DEFAULT '{}',
    args_hash   TEXT NOT NULL,                 -- sha256(json.dumps(args, sort_keys=True))
    status      TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','done','error')),
    result      JSONB,
    error       TEXT,
    origin      TEXT,                          -- chathy | ui | api | cli
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_capruns_recent ON capability_runs(capability, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_capruns_active ON capability_runs(capability, args_hash)
    WHERE status IN ('queued','running');
```

## 附录 C — `capabilities/runs.py` 规格

```python
_STALE_SECONDS = 1800   # >30min 的 running = 进程死亡遗孤 → 标 error('stale') 放行新 run

def schedule(name: str, args: dict, *, origin: str) -> dict:
    """陈旧收割 → 活跃去重(命中返回既有 {run_id, status, dedup:True})→ INSERT queued。
    并发撞 uq_capruns_active → 读回既有行(不是错误)。返回 {run_id, status[, dedup]}。"""

def execute_run(run_id: str) -> dict:
    """queued→running→fn(**args)→done+result / error。**绝不 raise**(BackgroundTasks 安全);
    result 经 json.dumps(default=str) 落 JSONB。"""

def status(run_id: str) -> dict | None      # {run_id, capability, status, result, error, ...}
def recent(capability: str | None = None, limit: int = 20) -> list[dict]
```

## 附录 D — `macro/view.py` 骨架(research 可 import;禁 import ..api)

```python
def theme_macro_view(theme: str, as_of: date | None = None) -> dict | None:
    """自 api/andy_links.link_theme 整体迁入:PIT 最新值/斜率/12 点序列 + 识别水印 + overclaims。
    slx 不可用 → 降级(metrics 仅 static rationale,live=False),绝不 raise。"""

def compact_theme_macro(view: dict, max_metrics: int = 8) -> dict:
    """LLM 压缩形:drop series,保 value/slope/identification_status/watermark。
    soft ⇒「未识别·勿作因果」水印必须原文透传(macro_indicators 既有纪律)。"""

def macro_dossier_lines(themes: list[str], as_of: date | None = None,
                        per_theme: int = 5) -> tuple[list[str], set[str]]:
    """dossier 注入行 + known_ids。id 沿用 [registry:macro:<key>](与论点证据格式兼容);
    live 读数有则行内追加 值/slope/valid_time + 水印后缀。"""
```

## 附录 E — UA-P3 Chathy 新工具 schema 表

```python
CapabilitySpec("earnings_panel",
    "公司季报事件 360 面板:下一财报/裁决/implied move/beat 习惯/近期战绩。非 universe 名字会明说。",
    _obj({"company_id": _CID}, ["company_id"]), _earnings_panel),
CapabilitySpec("earnings_verdict",
    "读取(或 refresh=true 触发重跑)季报前多空裁决。触发是分钟级后台任务:立即返回 run_id,"
    "用 run_status 查询;不要在本轮反复轮询,告知用户稍后再问。force=true 才会覆盖已锁定裁决。",
    _obj({"company_id": _CID, "refresh": {"type": "boolean", "default": False},
          "force": {"type": "boolean", "default": False}}, ["company_id"]),
    _earnings_verdict),
CapabilitySpec("run_status", "查询后台分析任务状态(build_earnings_verdict/report 等)。",
    _obj({"run_id": {"type": "string"}}, ["run_id"]), _run_status),
CapabilitySpec("theme_debates", "主题级争论天平:旗舰公司 lean 聚合、翻转清单。",
    _obj({"theme": _THEME}, ["theme"]), _theme_debates),
CapabilitySpec("exploration_frontier", "前沿研究综合(arXiv/X 合成的 research fronts)。",
    _obj({"domain": {"type": "string"}}), _exploration_frontier),
CapabilitySpec("fenny_quote", "结构化票据(FCN 等)进程内报价;termsheet 字段见 preset。",
    _obj({"termsheet": {"type": "object"}, "market": {"type": "object"}}, ["termsheet"]),
    _fenny_quote),
CapabilitySpec("start_report", "启动多智能体深度报告(分钟级)→ 返回 run_id;经 run_status 跟踪。",
    _obj({"company_id": _CID, "since": {"type": "string"}}, ["company_id"]),
    _start_report, kind="build", duration="slow"),
```

## 附录 F — 前端规格

```ts
// web/src/lib/runs.ts(fenny runJob 移植,DB-backed,分钟级)
export type RunStatus = { run_id: string; capability: string;
  status: "queued" | "running" | "done" | "error";
  result?: Record<string, unknown>; error?: string };
export async function runCapability(name: string, args: unknown,
  onPoll?: (r: RunStatus) => void, signal?: AbortSignal,
  opts?: { intervalMs?: number; maxPolls?: number }): Promise<RunStatus>
// POST /api/run/{name} → {run_id} → poll GET /api/run/{run_id} @1500ms×400(10min)
```

- `EarningsSection` props:`{ cid: string; earnings: EarningsBlock | null | undefined; onRefetch: () => Promise<void> }`;
  按钮三态 idle/`裁决生成中…`/error;done→`await onRefetch()`。
- `CompanyPage` 头部:「问 Chathy」→ `/?q=分析 {name}({cid}):论点健康度、临近财报裁决与宏观勾稽语境`。
- `ChathyPage`:`useSearchParams` 一次性消费 `?q=`(consumedRef + replace 清参)→ `send(q)`。
- `ToolChip`:preview 正则 `"(?:company_)?id"\s*:\s*"([a-z0-9_]+)"` → 去重 → `/genny/company/{id}` chips。

## 附录 G — 真机冒烟脚本(host,cid=now)

```bash
# 0) stack
docker compose up -d db && xar init && (xar serve --port 8000 &)
# 1) 能力清单
curl -s localhost:8000/api/capabilities | jq -r '.[].name' \
  | grep -E 'build_earnings_verdict|theme_debates|start_report'
# 2) 通用 API 跑 ET 裁决(UI 按钮同路径)
RUN=$(curl -s -X POST localhost:8000/api/run/build_earnings_verdict \
      -H 'content-type: application/json' -d '{"company_id":"now"}' | jq -r .run_id)
until [ "$(curl -s localhost:8000/api/run/$RUN | jq -r .status)" != "running" ]; do sleep 5; done
curl -s localhost:8000/api/run/$RUN | jq '.result | {status, direction, conviction, version}'
# 3) Genny 公司页 bundle 出现裁决
curl -s localhost:8000/api/ui/company/now | jq .earnings.verdict
# 4) Chathy 对话触发(SSE):期待 tool_start earnings_verdict{refresh:true} → run_id
SID=$(curl -s -X POST localhost:8000/api/chathy/sessions -H 'content-type: application/json' -d '{}' | jq -r .id)
curl -N -s -X POST localhost:8000/api/chathy/sessions/$SID/chat -H 'content-type: application/json' \
  -d '{"message":"帮我给 ServiceNow 重跑一次财报事件裁决(force),并给我 run id"}' \
  | grep -E 'tool_start|tool_result' | head
# 5) dossier 宏观勾稽节 + 主题争论能力
xar earnings panel now | grep -A4 '宏观勾稽'
curl -s -X POST localhost:8000/api/run/theme_debates -H 'content-type: application/json' \
  -d '{"theme":"ai_software"}' | jq '.result'
# 6) 浏览器手测:/genny/company/now 「跑 ET 裁决」→ 轮询 → 裁决入卡;
#    「问 Chathy」跳 / 且问题自动发送;Chathy 工具结果公司 id 可点回 /genny/company/now
```

## 附录 H — 测试矩阵(全部离线 monkeypatch + seeded_db + 2099 隔离)

| 文件 | 覆盖 |
|---|---|
| test_capabilities.py | 名字唯一/schema 合法/枚举;TOOLS parity 锁(24 名);execute 未知工具错误 JSON |
| test_capability_runs.py | schedule→execute→done;同参去重/异参新 run;fn 抛错→error 不上抛;stale 收割;404;read 内联;build 返 run_id;judge shim 带 run_id |
| test_macro_view.py | 无 slx 降级 live=False;压缩器 drop series 保水印;link_theme 委托形状对齐 |
| test_earnings_dossier.py(扩) | 宏观+争论节出现;known_ids 增 registry:macro:*/theme_debate:*;view 抛错 dossier 仍返 |
| test_thesis_build.py(扩) | `[registry:macro:` 前缀保持 |
| test_chathy_capabilities.py | 新工具 schema;earnings_verdict 读+refresh 只 schedule;run_status 回环;全部 ≤8000 字符;fenny_quote 打桩;start_report schedule |
| test_report_nodes.py | graph_retrieve 三新键;_graph_brief 三砖;report 能力可 schedule |
| 前端 | `npx tsc --noEmit` + `npm run build`(无 web 单测框架;行为归 P6 冒烟) |

---

## 执行策略(ultracode)

- UA-P0 迁移与 UA-P3 工具文案可 Workflow 扇出起草 + 对抗复核;核心接缝
  (runs.py 并发语义、macro/view 分层抽取、EarningsSection 按钮态机、ChathyPage ?q= 消费)主循环亲手写;
- 每阶段跑附录 H 对应测试 + 全量 pytest + ruff(P4 后加 tsc/npm build)再进下一阶段;
- UA-P6 真机冒烟集中做(六步脚本);完成后独立对抗代码评审 → 修复 → 汇报;
- 推送/合并/部署经用户确认。
