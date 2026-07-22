# ONTOLOGY_REVIEW — Ontology 模块独立架构审核

> 审核范围：`src/xar/ontology/` 全部 20 个文件（~3,672 LOC）+ 消费方接线（research/kg/providers/api）+ 对应测试。
> 审核原则：与 ARCHITECTURE_REVIEW.md 同一套——证据导向（file:line 可溯）、双门槛（垂直知识更准 / 信任纪律更硬）、自用姿态、防平行系统。
> 裁决：**GO**。这是全仓品味最高、模式最统一的模块，是全平台护城河的河床本体。无 P0 正确性硬伤；发现 **2 个 P1 语义一致性缺口**、**1 个 P1 架构纯度问题**，及若干架构级升级空间。

---

## 〇、总体裁决

Ontology 模块是"代码即真相"范式在全仓执行得最彻底的地方：20 个文件共享同一套注册表模式（frozen dataclass 元组 + 派生索引 dict + import 时 assert 自检 + pytest 不变量守卫），且每个文件都自带 docstring 写明"新增 X = 加一行数据，消费方零改动"的扩展纪律。关键设计判断全部正确：

1. **词表只增不改的考古纪律**：`catalysts.py:14-43` legacy 10 类逐字保留（dedup_key/dashboard 依赖），P0/P1 扩展纯追加；`nodes.py:15-19` legacy 光模块四类型保留并重释为 "Company + chain_role"。这让 947 公司的存量数据零迁移跨过了三次本体扩张。
2. **零成本复用的升维**：`cycle.py:52-58` 的 `CYCLE_RANK` 兼作 cycle 主题 segment tier → ChainHeatmap 零改动渲染周期轴；`indicators.py` 衍生指标写回既有 `fundamentals` 表（source='derived'）→ dossier/前端/UNIQUE 幂等免费获得。这是"加列不加表"红线的本体层镜像。
3. **抽取/计算边界的显式防腐**：`indicators.py:9-16` 衍生指标**独立注册、绝不注入 SPEC_BY_KEY/ALIAS_TO_KEY**——"抽取出来的同比"与"计算出来的同比"被制度性隔离，防自反馈环。这是全模块最锋利的一条信任纪律。
4. **争论本体的一等对象化**：`thesis.py:140-157` ThesisDebate + VerificationPoint 双阈值灰区（`bull_threshold`/`bear_threshold` + direction），把"≥20% 证多 / ≤12.5% 证空 / 之间观望"变成 ~30 行水平比较即可机器复核的结构；`debates.py` 策展种子 + `required_debate_keys` 硬约束（thesis.py:270-272）把"宁缺毋滥"的反面（有种子必须回应）也编码进了校验。
5. **已学习的历史教训被固化**：thesis.py:236-238 debate key 与 pillar key 撞名即违规（评审 #1 的产物）——审核意见沉淀为不变量，而非一次性补丁。

---

## 一、技术审核（证据导向）

### 🟡 P1-1　`standards.FinMetric` 与 `metric_packs.CORE_PACK` 两套 GAAP 词表漂移 —— `pb_ratio` / `dividend_yield` 成为"幽灵指标"

- **证据**：`FinMetric`（standards.py:115-151）含 `PB = "pb_ratio"`、`DIVIDEND_YIELD = "dividend_yield"`，且 `FUTU_SNAPSHOT_MAP`（standards.py:233-239）确实把富途快照写入这两个 key；但 `CORE_PACK`（metric_packs.py:42-77）**不含**这两个 key——尽管 metric_packs.py:41 注释自称 "mirrors FinMetric; single source of truth"。
- **后果**（三个真实场景，非假想）：
  1. 富途落库的 `pb_ratio`/`dividend_yield` 行不在 `SPEC_BY_KEY` → `kpis_for_company()` 永远不把它们列入公司 KPI 谱 → 论点 VP / watch_metrics 无法合法引用它们（validate_thesis 会拒），dossier 财务节不带单位/方向；
  2. `is_higher_better("pb_ratio")` 走 metric_packs.py:327-329 的 `None → True` 默认——**方向恰好错**（PB 越低越好）；ops.py:106 / dashboard.py:212 浮出 KPI 时这两行没有 spec 可标；
  3. 本体层"单一真相"承诺与实现不符，后续任何以 `FIN_METRICS`（standards.py:158，为两者并集）为合法域的校验都会放过这两个 key、以 `SPEC_BY_KEY` 为域的校验都会拒——同一张基本面表上出现两个合法域。
- **修复**：把 `pb_ratio`/`dividend_yield` 补进 `CORE_PACK`（hib=False/True），并加 import 时不变量：`{m.value for m in FinMetric} ⊆ SPEC_BY_KEY`（5 行 assert，杜绝再漂移）。

### 🟡 P1-2　`is_higher_better()` 对未知指标静默返回 True

- **证据**：metric_packs.py:327-329，`SPEC_BY_KEY.get(metric)` 为 None 时返回 True。
- **后果**：任何未注册/拼错的 metric key 在 ops/dashboard 浮出时被静默标注为"越高越好"。在 P1-1 修复后残余风险变小，但对**未来新增数据源写入未注册 key** 这一现实路径，方向语义会被静默编错——而方向语义正是 VP 判定与看板着色的依据。
- **修复**：返回 `None`（三态）并让两个消费端（ops.py:106、dashboard.py:212）显式处理 unknown；或至少在 debug 下 log。成本 ~10 行。

### 🟡 P1-3　Ontology 不是叶子模块：对 `storage.db` 与 `ingestion.registry` 的反向依赖

- **证据**：`coverage360.py:17` `from ..storage import db`（模块级）；`altdata.py:155` 依赖 `providers.futu.code_from_tickers`；`cycle.py:141` / `altdata.py:183` / `coverage360.py:82` 均 lazy-import `ingestion.registry`（cycle.py 注释自承"avoids ontology↔registry import cycle"）。
- **后果**：依赖方向倒置——按分层，`ingestion/kg/research` 应依赖 ontology，ontology 不应反向依赖它们。目前靠 lazy import 续命，已经出现过一次真实 import cycle；`coverage360` 更是让"纯词表层"带上了 DB 句柄，使 `import xar.ontology` 隐含连接池副作用风险（虽然 `_probe` 只在调用时执行）。
- **性质**：不是 bug（现有 lazy 模式都能跑），是**架构纯度债**。随着 P2-3（registry 数据化）推进，registry 只会更重，反向依赖会越来越多。
- **处置（P1/P2）**：明确分层并文档化——
  - **L0 纯词表（叶子，零 import 出包）**：catalysts/nodes/edges/sectors/metric_packs/indicators/standards/thesis/earnings_events/research_docs/flow/altdata 的 spec 部分/debates 的类型部分/macro_links/cn_routing/futu_plates/cycle 的词表部分；
  - **L1 解析服务（可依赖 registry/db）**：`cycle_of_company`、`bindings()`、`coverage360`、`seeds_for` 等。建议物理拆分（如 `xar/ontology_runtime/` 或并入 `research/`），或至少在 `ontology/__init__.py` 注明两层边界并把 coverage360 迁出。

### 🟢 P2 观察项

| # | 问题 | 证据 | 说明 |
|---|---|---|---|
| 1 | 不变量强制点不统一 | altdata.py:107-108 / research_docs（import 时 assert）vs debates.py（仅 tests/test_thesis_ontology.py） | debates 的词表合法性（company_id∈COMPANIES、suggested_*∈合法集、key 全局唯一）只在 pytest 强制；运行期 import 一个坏注册表不炸。建议统一：便宜的查 import 时查，贵（依赖 registry）的保留测试。 |
| 2 | 方向语义三轨并存 | `good_when: rising/falling`（altdata/flow/macro_links）vs `higher_is_better: bool`（metric_packs/indicators）vs `direction: higher_is_bull/lower_is_bull`（thesis VP） | 三种编码同一语义无共享类型无换算层。VP 挂在一个 `higher_is_better=False` 的指标上时，方向是否一致全靠策展人自觉。见 创新 §3。 |
| 3 | `seeds_for` 主题继承无上界 | debates.py:68-82 | 多主题旗舰可继承多条 ThemeDebate → `required_debate_keys` 膨胀 → debate_cap 同步抬（thesis.py:229），prompt/token 压力与"0-3 个核心争论"的 schema 纪律（thesis.py:180）隐性冲突。目前 8 主题 8 条 ThemeDebate 下不触发；新增主题争论时需重新对账。 |
| 4 | `bindings()` 每次调用全量重建 | altdata.py:181-210 | `binding_for(cid)` = 全宇宙 947 家重建 + import providers。夜批里按公司循环调用时是 O(n²)。加 `functools.lru_cache` 一行解决。 |
| 5 | `_primary_seg` 多主题取首个 | sectors.py:216-225 | dict 迭代序决定 multi-theme 公司的 primary segment → industry 解析对 themes 列表顺序敏感。策展数据目前顺序稳定，无现实后果；值得一行注释或确定性排序。 |
| 6 | VP 阈值无单位校验 | thesis.py:132-135 + metric_packs unit 自由字符串 | `bull_threshold=0.20` 对 `unit='ratio'` 与 `unit='%'` 的同名指标含义差 100 倍；不同数据源单位混存（TWD vs USD）时规则道静默误判。见 创新 §4。 |

---

## 二、架构级创新与升级空间（按杠杆排序）

### 1. ★★ 统一信号谱系：一个 meta-catalog 收敛五套平行 Spec 注册表

现状有 **5 套形状近似的"可追踪量"注册表**，各自重复 key/label/unit/cadence/scope/good_when/min_history 字段：

| 注册表 | 位置 | 落库 |
|---|---|---|
| MetricSpec (~200) | metric_packs.py | fundamentals |
| IndicatorSpec (~24) | indicators.py | fundamentals (derived) |
| AltSignalSpec (18) | altdata.py | alt_signals |
| FlowSignalSpec (10) | flow.py | alt_signals（同一张表，两套 spec 类型！） |
| MacroLink (43+38) / ThemeDebate.macro_metric_keys | macro_links.py / debates.py | slx + kg_events |

其中 **AltSignalSpec 与 FlowSignalSpec 同写 `alt_signals` 表却各自定义 dataclass**——schema 复制已在发生。升级路径不是合并词表（各自语义域合理），而是：
- 抽一个共享 `SignalSpecBase`（key/label/unit/cadence/scope/direction/rationale）+ 各域扩字段；
- 建 **`CATALOG`**（registry-of-registries）：`{namespace: {key: spec}}`，使 Ops 看板、Chathy 工具、coverage360 能枚举"平台上全部可追踪量"而无需逐模块 import；
- 这直接服务 P2-3（registry 数据化）：未来 YAML 化时有一个统一的 schema 可校验。

**双门槛判定**：垂直知识更准（消重 + 统一口径）✓。建议 P1 尾/P2 头做，趁只有 5 套。

### 2. ★★ 方向语义归一：`Polarity` 一等类型 + 换算适配层

承接 P2 观察 #2。`good_when`/`higher_is_better`/`direction` 三者是同一概念（"这个量的哪个方向对谁有利"）在三个年代的方言。建议：
- 定义单一 `Direction` 枚举（`rising_good | falling_good | two_sided`）；
- VP 的 `higher_is_bull/lower_is_bull` 保留（它相对的是 bull 而非 good，语义真不同），但 validate_thesis 增加**交叉 sanity**：VP.metric 的 `higher_is_better` 与 VP.direction 冲突时（如 `lower_is_bull` 挂在 `higher_is_better=False` 的 `capex` 上是合法的——capex 降=烧钱减少；但挂在 `doi_days` 上通常是策展错误）——不做硬违规，做 **warning 级 violation**（validate 现只有硬违规一档；这是"校验分档"的净新能力，先只在 validate_thesis 落地）。

### 3. ★ 词表生命周期管理：deprecated 通道 + 本体版本戳

- 现状：词表只增（考古纪律正确），但**没有退役通道**。`LEGACY_CATALYST_TYPES`（catalysts.py:50-53）已是事实上的第一代"deprecated 集合"，靠注释而非类型表达。
- 建议：`CatalystType` 等加 `deprecated_since`/`replaced_by` 元数据（或在注册表旁建 `DEPRECATED: dict[str, str]`），dossier/校验对新写入拒用、对存量读取放行——把考古纪律从"人肉注释"升级为"机器可执行"。
- 加 `ONTOLOGY_VERSION` 常量（或各注册表 hash），`company_thesis.meta` / `thesis_fact_links.model` 记录生成时本体版本——论点 rebuild 时可回答"这条论点是在哪版词表下写的"，与 as_of 时间戳正交互补。**这是给 PIT 纪律补上"语义 PIT"维度**：现在能回放"当时知道什么"，不能回放"当时词表是什么"。

### 4. ★ VP 单位锚定：阈值校验接入 spec.unit

承接 P2 观察 #6。`VerificationPoint` 加可选 `unit` 字段，validate_thesis 校验 `vp.unit == SPEC_BY_KEY[vp.metric].unit`（或 unit 族换算表：pct↔ratio ×100）。策展种子 `suggested_metrics` 与 VP 阈值同屏审阅时即可机器对账。成本：一个字段 + 5 行校验；收益：消灭整类"阈值单位错配"静默误判。

### 5. ★ 边方向逆映射：`INVERSE_EDGES` 表

`supplies`/`customer_of`、`invests_in`/`holds_stake`、`qualified_by`/`supplies` 存在语义互逆/重叠（edges.py:14-41），但没有机器可读的逆关系表。同一事实可能被两个 extractor 分别编码成正反两条边，graphrag 1 跳遍历无法对账。建议：
- edges.py 加 `INVERSE_OF: dict[str, str]`（`customer_of ↔ supplies`、`subsidiary_of` 自逆为空等）；
- graphrag 多跳（P2-2 递归 CTE）落地时据此做方向归一遍历；
- dedup/对账闸可新增一类不变量：互逆边对不一致即报。**不建新边类型，只加元数据**——符合"不为 X 另起平行系统"红线。

### 6. ★ 不变量总装：`xar ontology check` 运维闸

散落的不变量（import assert ×3 处 + 7 个测试文件）统一进 `ontology/invariants.py` 单一入口，CLI `xar ontology check` 输出逐条 PASS/FAIL——Ops 控制台本体页（ops.py:78-83 已有雏形）浮出为健康灯。让"本体完整性"从 CI 产物变成运行期可见的平台状态，与 coverage360 的 Ops 看板同一哲学。

### 7. 策展数据可持续性（与主评审 P2-3 同源）

`debates.py` 20 条公司种子 + 8 条主题争论（每条 bull/bear 各 ~200 字策展叙事）是**全仓单位字符价值最高的代码**，也是最先会撞上"改代码才能改数据"天花板的部分。随 P2-3（companies/registry 数据化）一并把 DEBATE_SEEDS/THEME_DEBATES 抽为版本化 YAML + 同一套测试守卫——策展工作流（起草→对抗复核→定稿）不依赖改 .py 文件。**不提前做**：等 YAML 化 registry 的既定路径启动时搭车。

### 8. 显式不做（防范围失控）

- ❌ 不引 OWL/RDF reasoner 到运行路径（standards.py 的 IRI 锚定 + ops 浮出已够，导出是展示层而非推理层）；
- ❌ 不建 indicator-of-indicator（indicators.py:12 的自反馈禁令是对的设计，ratio_to 的 other_metric 已是受控的二级）；
- ❌ 不把 theme_thesis 升格为表（THESIS_ONTOLOGY_PLAN §3.5 裁决维持：code-as-truth 元组足够）；
- ❌ 不为 Direction/单位建 DSL 或换算引擎（一个枚举 + 一个 pct↔ratio 特例足够）。

---

## 三、红线对照（本模块 vs 主评审 §六）

| 红线 | 本模块状态 |
|---|---|
| 不为语义层另起平行表 | ✅ indicators 写回 fundamentals；debates 走 thesis_evidence slot |
| LLM 路由不另起平行系统 | ✅ thesis.py 仅 schema，生成走既有 complete_json |
| 本体富化不另起新层 | ✅ altdata/indicators 均是"注册表 + 消费方零改动" |
| 垂直知识更准 / 信任纪律更硬 双门槛 | ✅ 本模块即门槛本身的载体 |
| 可规则化纠错优先上升为不变量 | ✅（撞名检查、衍生隔离）但执行点不统一（P2 观察 #1） |

---

## 四、行动清单

| 档 | 项 | 工时 |
|---|---|---|
| P1 | FinMetric ⊆ CORE_PACK 补全 + import 不变量（§一-1） | 0.5 天 |
| P1 | `is_higher_better` 三态化 + 两消费端适配（§一-2） | 0.5 天 |
| P1 | L0/L1 分层文档化，coverage360 迁出 ontology（§一-3） | 0.5 天 |
| P1 | `bindings()` lru_cache；debates.py 不变量查 import 可行性（P2 观察 #1/#4） | 0.5 天 |
| P2 | SignalSpecBase + CATALOG（创新 §1） | 1.5 天 |
| P2 | Direction 归一 + validate warning 档（创新 §2） | 1 天 |
| P2 | ONTOLOGY_VERSION 写入论点 meta（创新 §3 后半） | 0.5 天 |
| P2 | INVERSE_EDGES 元数据（创新 §5，随 graphrag 多跳搭车） | 0.5 天 |
| 搭车 | debates/词表 YAML 化随 P2-3 registry 数据化（创新 §7） | 随 P2-3 |

---

## 复核与处置（2026-07-22，第三方逐条实证）

按自用姿态（修正确性/一致性缺陷；架构纯度/升级类记录不做）逐条裁定。验证：import 不变量通过、`is_higher_better` 方向修正实测、`ruff` clean。

| 条目 | 裁定 | 处置 |
|---|---|---|
| **P1-1** FinMetric/CORE_PACK 漂移（`pb_ratio`/`dividend_yield` 幽灵指标） | **成立·已修** | 补 `pb_ratio`(hib=False)/`dividend_yield`(hib=True) 进 `CORE_PACK`；加 import 不变量 `{FinMetric} ⊆ SPEC_BY_KEY`（置于 `standards.py`，其已 import metric_packs，无循环依赖）。实测 `is_higher_better("pb_ratio")` True→**False**（方向修正） |
| **P1-2** `is_higher_better` 未知指标静默 True | **成立·已修（轻量）** | 保留 bool 返回（不改 ops/dashboard 消费方），未知 key 加 `warning` log——未来新源写未注册 key 时可见，而非静默编错方向 |
| **P2#4** `bindings()` 无缓存 O(n²) | **成立·已修** | `@functools.lru_cache(maxsize=1)`（AltBinding `frozen=True` 不可变、registry 运行期不变 → 安全）；`binding_for` 在 `implied_move` 循环调用即受益 |
| **P1-3** Ontology 反向依赖 db/registry（架构纯度） | **成立·不做** | 非 bug（lazy import 都能跑）；L0/L1 物理分层是重构，自用姿态下记录不动 |
| **P2#5** `_primary_seg` dict 迭代序 | **复核为非问题** | 实为确定性：`themes` 是有序列表 + dict 插入序稳定；且「取 primary theme 的 segment」是**有意语义**，按 key 排序反破坏意图——无需改 |
| P2#1/#2/#3/#6、创新 §1–8 | 记录 | 架构升级/设计建议（SignalSpecBase+CATALOG、Direction 归一、INVERSE_EDGES、ONTOLOGY_VERSION、词表 YAML 化…），非正确性缺陷；随既定路径（P2-3 registry 数据化等）搭车，本轮不做 |

> 裁定：Ontology 审核的 **3 项正确性/一致性缺陷（P1-1/P1-2/P2#4）全部修复**；P1-3 架构纯度 + P2 观察/8 项创新为架构升级方向，自用姿态下记录供决策、不机械改。
