# XAR 第一性原理代码审核（首席技术官视角）

> 评审范围：全仓代码 —— 后端 `src/xar` (~7,812 LOC, 73 文件) + 前端 `web/src` (~7,062 LOC, 41 文件) + schema + tests + CI + 部署。
> 评审视角：第一性原理 / 技术架构 / 产品审美 / 技术债。所有结论带 `file:line` 可追溯。
> 评审目标：判断本项目是否真正服务于"新时代基于 AI 能力的投研平台"这一核心命题，是否埋下不可逆的技术债。
> 性质：本文件仅记录审核意见，**不修改任何代码**。与既有 `ARCHITECTURE_REVIEW.md` 并存，视角互补。

---

## 〇、总体裁决

**这是一份品味明显高于行业平均的工程实现**，不是屎山：~7,800 行撑起了 15+ 数据源、双时态图谱、多 Agent 流水线、RRF 检索、本体即代码、全栈三模块终端（投研主轴 / 运维控制台 / 前沿探索），"交钥匙 + 优雅降级 + 许可合规 CI 硬规则"的工程纪律扎实，README/DESIGN/UI 文档质量远超一般早期项目。

**但作为"机构级 AI 投研平台"，它目前更像设计精美的研究原型，而非可交付的产品。** 核心矛盾在于：

> **平台把"可信 / 可溯源 / 可对账"作为唯一真正的护城河写进了所有文档，但实现层面这一层是装饰性的。**

证据闸不闸、引用编号不校验、抽取的证据原文被丢弃、回测有四重方法论偏差、护城河代码零测试。这意味着用户看到的"高置信报告"和"低置信报告"在产物上没有本质区别，而"催化剂→收益"信号是不可信的。**把机构投研招牌挂上去的最大风险，恰恰是这一层。**

下文先给第一性原理审视（核心命题 vs 实现），再给分优先级的修复路线。所有问题均可独立交付、且基本不引入新依赖。

---

## 一、第一性原理审视：核心命题 vs 实现

一个"新时代 AI 投研平台"的根本命题应当是：**LLM 会幻觉，所以平台的核心价值 = 让每条结论可溯源、每个数字可对账、每个推理可验证。** 基于此，逐层对照代码：

### 1.1 证据闸 —— 闸不闸 [严重 / 设计违背]

- **意图**：`agents/evidence_gate.py` 计算引用覆盖度 + 数值对账 + LLM-judge 风险，低置信报告应拦下交人审。
- **真相**：`agents/evidence_gate.py:36` 算出 `passed`，但 `agents/graph.py:36-41` 无论 `passed` 真假一律推进到 `awaiting_approval`。**闸是 advisory 文本，没有任何条件分支、重路由、escalate、挂起。** 覆盖度 0.1 + 风险 0.9 的报告与干净报告走完全相同的发布路径。
- **引用编号不做范围校验**：`evidence_gate.py:12` `_CITE = re.compile(r"\[\d+\]")` 匹配任意"方括号+数字"。`coverage`(`:23`) 只要句子里出现 `[7]` 就算"已引用"，即使实际只有 3 条引用。**幻觉的引用编号会虚增覆盖度。**
- **数值正则过松**：`evidence_gate.py:13` `_NUM = re.compile(r"\d")` —— "Q3"、"2024"、"Step 2" 全算"含数值句"，既虚增分母又制造假的"未引用数值"。
- **judge 看不到原文**：`evidence_gate.py:41-55` 用 **weak tier** 做裁判，且只喂 `body[:8000]`，**拿不到被引用的源 chunk**。它只能看眼缘判断合理性，**无法检测 groundedness**——一个没有证据的裁判查不出幻觉。
- **失败即放行**：`evidence_gate.py:45` 解析失败默认 `risk=0.3`（< 0.5 阈值），judge 失败 = 自动放行。
- **数值 groundedness 默认 True**：`evidence_gate.py:27` `c.get("tie_out_ok", True)`，缺标志位按已对账计。

### 1.2 抽取的证据原文被丢弃 [严重 / 反防幻觉]

- **意图**：`ontology/schema.py:26,38` 把 `evidence` 设为**必填**的 verbatim 引用；`kg/extract.py:47-49` 的 system prompt 明确要求"Every edge and event must include a short verbatim evidence quote"。
- **真相**：`kg/extract.py:92-117` 抽取循环**从不读取 `e.evidence` / `ev.evidence`**。引用既不存储、也不验证是否真在原文中、也不下传给下游 Agent。**最强的防幻觉杠杆接线后直接扔掉。**
- **同类问题**：`kg/expert.py` 同样丢弃证据；`exploration/synthesis.py:125` 反而做对了（`[pid for pid in f.key_papers if pid in valid_ids]` 校验引用 ID 真实存在）——说明团队能做到，只是没在护城河代码里做。

### 1.3 数值对账闸 —— 正则缺陷使其近乎失效 [高]

- **文件**：`parsing/tie_out.py`（66 行的正则启发式闸，被定位为"金融最高风险失败"的防线）。
- **正则解析错金融数字**：`tie_out.py:12` `_NUM` 把括号负数 `(1,234)` 解析成正数 `1,234`；百分比 `12.3%` 丢 `%`；科学计数 `1.2e6` 拆成 `1.2` 和 `6`；区间 `2023-2024` 解析成负数 `-2023` + `2024`。**这些对金融对账闸是实质性正确性 bug。**
- **关键词过宽**：`tie_out.py:17-20` `_NON_ADDITIVE` 关键词 `net` 会匹配 `network`、`Bernets`；`less` 匹配 `endlessly`。真实利润表几乎都含 "net income"，整条判定退化为"非可加通过"——**闸的覆盖率被悄悄降到接近为零**。
- **盲区**：`tie_out.py:64` 50% 阈值使 2%–50% 之间的不匹配静默归为 `indeterminate_pass`。
- **接口设计正确**：`_reason` 返回值（reconciled / non_additive_pass / total mismatch）是为可插拔更重闸准备的接口，但目前没有任何重闸接入。

### 1.4 双时态承诺 —— 边去重违反双时态语义 [高]

- **意图**：存储层 `storage/schema.sql:65-119` 正确建模了 `t_valid_from/to` + `observed_at` + `invalidated_at`，模块 docstring 承诺"later docs never overwrite earlier-true facts (supersession is explicit)"。
- **真相**：`kg/store.py:32-39` 的 `add_edge` 去重**只检查 `(src_id, dst_id, rel_type, invalidated_at IS NULL)`**，忽略 `t_valid` 窗口。Q1 为真的边和 Q3 再断言的同一关系被当重复，**后者被静默丢弃，丢失了"后生效"的事实**——直接违反双时态承诺。
- **佐证丢失**：同一位置，第二个来源确认既有边时函数直接 return（`:39`），不做置信度提升、不做来源叠加——**KG 无法表达"3 个来源佐证此关系"**。

### 1.5 实体消解 —— 文档承诺不存在 [高]

- **文件**：`kg/resolve.py`（65 行）。
- **文档撒谎**：`resolve.py:3` docstring 称 "High-stakes edges are flagged for human review when confidence is low." —— **代码中根本不存在 flagging / review-queue / 低置信标记机制**。`resolve()`(`:34-52`) 返回置信度浮点数，**每个调用方都忽略它**。
- **阈值偏低**：`_FUZZY_THRESHOLD = 0.55`（`:13`），对金融场景偏低（"Advanced Micro Devices" vs "Advanced Micro Sensors" 有混淆风险），且无人审兜底。
- **英文单语**：`:18` 正则去掉 `inc|corp|co|ltd` 但**不去中文后缀**（有限公司 / 股份有限公司 / 集团 / 科技）。双语平台下 "中际旭创股份有限公司" 和 "中际旭创" 不走 alias 匹配——CN 侧实体消解有真实缺口。
- **学习型 alias 会污染**：`:50` 把任何模糊匹配（低至 0.55）写回为**学习到的 alias**，置信度 1.0。**一次错误永久把该拼写绑定到错误节点。**

### 1.6 成本上限 —— 不覆盖批量路径 [高]

- **意图**：`models/llm.py` 两级路由 + 单 run 美元预算上限（`XAR_LLM_MAX_USD_PER_RUN` 默认 5）。
- **真相**：
  - **预算只调用前检查**：`llm.py:122-123`。在途调用和 retry(`:138-144`) 的成本不再复核。单次大 `strong` 调用可大幅超额。"硬上限"非硬。
  - **retry 丢 JSON 模式**：`llm.py:140-144` 失败重试时既 pop `reasoning_effort` 又 pop `response_format`——`complete_json` 的重试静默退化为自由文本，**恰恰在最需要结构化输出的时候。**
  - **批量任务无预算**：`build_kg`(`extract.py:124`)、`expert.process`(`expert.py:102`)、`synthesize_all`(`synthesis.py:159`) 默认 `run_id=None`，`_spent(run_id)` 返回 0，**$5 上限永不生效**。语料级 `build_kg` 或 6 域夜间 `synthesize_all` 可无界烧钱。
- **价格表虚假**：`llm.py:25-41` 含 `"claude-opus-4-8"`、`"claude-fable-5"`、`"deepseek-v4-flash"` 等未发布模型名；未知模型 `:83-85` 静默回落到猜测的 $3/$15——**预算数学本身不可信。**

### 1.7 Prompt 注入防御 —— 完全缺失 [高]

- **文件**：`kg/extract.py`、`kg/expert.py`、`exploration/synthesis.py`。
- **真相**：`extract.py:71` 把文档原文（最长 12k 字符的任意公告/社交内容）**裸拼接进 prompt**，无分隔符、无"以下为不可信数据"声明、无消毒。`expert.py:54` 拼 `(d['text'] or '')[:6000]`，`synthesis.py:94-99` 拼 arXiv 摘要 + 期刊文 + X 帖子。**整个模块存在的意义就是处理 X/微信这类对抗性 UGC。**
- **注入直通车**：`kg/signals.py:113-133` `mirror_social` 把高互动帖子（sentiment≥0.5 或 score≥50）自动 `permission="grey"` 镜像进 `documents`，随后流经 `extract.py`/`expert.py`——**对抗性 X 帖子被自动晋升进抽取管线。**
- **零防护**：全仓 grep `sanitize|injection|ignore previous` 无相关命中。

### 1.8 主题一致性 —— 撕裂 [高]

- **真相**：`kg/extract.py:18-31` 设计了 5 主题 focus map，但：
  - `kg/expert.py:43-49` 的 system prompt **焊死在光模块/EML/DSP/CPO/LPO**，给 `ai_software` 的 Salesforce 找光模块事实。
  - `agents/nodes.py:96-107` 的分析师检索词（"EML DSP single source qualification NVIDIA"、"800G 1.6T capex"）同样光模块化。
  - `agents/debate.py:31-34,44-47` 的风险词（"CPO/LPO substitution of DSP attach"、"EML undersupply"）同理。
  - `extract.py:24-30` 的 `space_exploration`/`humanoid_robotics` focus 是**死配置**（registry 里从未分配这俩 theme）。
- **后果**：平台宣称多主题（5 主题、294 公司），但 Agent/expert 层只认光模块。给软件/航天/机器人公司跑，喂的是光模块 prompt，检索词召回零相关。

### 1.9 Agent DAG —— 名实不符 [中]

- **真相**：`agents/graph.py:25-41` 是固定线性脚本（scope→retrieve→analysts→debate→risk→synthesize→evidence_gate），无条件边、无并行分支、无重试。docstring(`graph.py:1-5`) 和模块名 "graph" 暗示 LangGraph 式拓扑，实际是命令式调用——**命名过度营销了设计**。
- **5 分析师串行**：`nodes.py:110-112` 互相独立却顺序跑，4-5x 延迟浪费。
- **仅捕获 `BudgetExceeded`**：`graph.py:46`。其他异常（provider 500、DB 中断、某 analyst 解析错误）使 run 卡在 `"running"` 状态，`state.py` checkpoint 只在成功路径写。**无通用 `except → checkpoint(status="failed")`。**
- **人工审批是状态标志不是工作流**：`graph.py:60-69` `approve()` 仅按 run_id 发布，不复核 metrics、不审计批准了什么。

---

## 二、必须立即处理（P0 / 止血）

### 2.1 已发生的凭证泄露 [立即]

- **证据**：`.env.example:7` 提交了一个真实 DeepSeek API Key `sk-ccaf700b2abf444895bbf9860dc40ba8`，已进入 git 历史（commit `9a2091b`，唯一 commit，HEAD 可恢复）。其余 provider key 均为注释 stub，唯独此行已填且非占位——明显是真实 key 粘贴。
- **处置**：(1) 立即去 platform.deepseek.com 吊销轮换；(2) 改为注释 stub（对齐其它 provider）；(3) `git filter-repo` / BFG 清历史；(4) CI 加 `gitleaks`/`trufflehog` 防复发。

### 2.2 Secret 处理 [立即]

- **证据**：`config.py:17-48` 全部 API key（anthropic/openai/deepseek/finnhub/twitter/x/reddit/aifinmarket/werss）为 `str` 而非 `SecretStr`——任何 `Settings` repr / traceback / 调试日志都会明文泄露。
- **放大面**：`models/llm.py:53-68`（尤其 `:67`）把 key 镜像进 `os.environ` 以满足 LiteLLM。**所有下游子进程（subprocess / multiprocessing）继承这些 secret。**

### 2.3 Docker / 部署安全 [高]

- **以 root 运行**：`Dockerfile` 全程无 `USER` 指令，`docker-compose.yml:32` 挂 `xar_models:/root/.cache` 坐实。配合 API 零鉴权，任何 RCE 直接是容器内 root。
- **Postgres 暴露 + 弱口令**：`docker-compose.yml:7-9` 硬编码 `xar:xar`，`:17-18` 绑 `0.0.0.0:5432`。在非回环主机上，弱口令 Postgres 直接对网络开放。
- **API 端口暴露 + 零鉴权**：`docker-compose.yml:33-34` 绑 `0.0.0.0:8000`，配合下文 §3.1 的全 API 零鉴权——任何网络可达者可触发烧 LLM 钱 / 污染 KG。
- **建议**：非 root `USER`；Postgres 绑 `127.0.0.1`；app 加 `healthcheck`（app 当前无 healthcheck，仅 db 有）；镜像按 digest pin。

### 2.4 CI 不阻塞 [高]

- **证据**：`.github/workflows/ci.yml:28` `ruff check src || true` —— **lint 是非阻塞建议**，等于没 lint。
- **缺失**：无 `ruff format --check`、无 mypy/pyright 类型检查、无 coverage 阈值、无前端 build/typecheck/test、无 Docker build 校验、无 secret scan、无依赖审计（pip-audit/npm audit）、无 Python 矩阵（仅测 3.12，但声明 `>=3.11`）。

### 2.5 依赖零 pin + 无 lockfile [高]

- **证据**：`pyproject.toml:10-46` 全部 18 个核心依赖为浮动 `>=` 下界、0 个 `==`、无 lockfile（无 requirements.txt/poetry.lock/uv.lock）。`pip install .` 今天和半年后可解析出实质性不同版本。`litellm>=1.44`、`edgartools>=3.0` 这类 API 表面依赖很重的库一旦 breaking change，静默断裂。
- **前端**：`web/package.json` 全 caret `^`，但 `package-lock.json` 存在（至少有记录），只是 CI 不 `npm ci` 校验。

---

## 三、架构级技术债（影响"是否埋债"）

### 3.1 全 API 零鉴权 [严重]

- **证据**：`src/xar/api/app.py` 全部 ~45 端点无 auth。grep `Depends|Authorization|HTTPBearer` 全仓无命中。所有写/昂贵端点匿名可达：
  - `POST /api/report`（`app.py:120`）—— 任何人可烧配置的 LLM 预算（仅 per-run 上限，**无全局/速率上限**，无限调用 = 无限烧钱）。
  - `POST /api/report/{run_id}/approve`（`app.py:127`）—— **整个"人审"护城河可被任意调用方绕过**。`agents/graph.py:60-69` 甚至不校验 run 是否处于 `awaiting_approval`。
  - `POST /api/ingest`/`/api/pull`/`/api/ingest/wechat`/`/api/ops/sources/{sid}/run`/`/api/ops/datalake/process`/`/api/ops/altdata/process`/`/api/exploration/refresh` —— 全部匿名触发后台 ingest/烧 LLM。
  - `test_llm`（`ops.py:312-324`）匿名触发真实 LLM 调用 = 真花钱；`:324` 还把异常 `str(e)[:200]` 返回，泄露栈/路径。
- **信息泄露**：`/api/health`（`app.py:59-71`）、`/api/ops/llm`、`/api/ops/connectors`、`/api/ops/sources` 向匿名者暴露模型名、base URL、provider key 配置态、行数——免费的侦察地图。
- **无 CORS / 无 TrustedHost / 无 rate limiter / 无 request-size cap**（`app.py:29` 构造 FastAPI 后未加任何中间件）。
- **昂贵流水线同步跑在请求处理器内**：`POST /api/report` 在请求内跑完整多 Agent DAG（1-3 分钟）；`GET /api/backtest`（`app.py:264`）在请求内做至多 500 次顺序 yfinance 下载（10+ 分钟）。配合全部为 sync `def`（threadpool ~40），几个并发即耗尽、app 冻结。

### 3.2 回测方法论四重偏差 [严重 / 作为金融产品不可接受]

- **文件**：`backtest/catalyst_returns.py`（68 行），暴露于 `GET /api/backtest`。
  - **B1 基价取错**：`:48` 取窗口 `d0-5d ~ d0+max(h)+10`，`:52` `base = series.iloc[0]` 是**事件前 ~5 天**的价格；`:56` `fwd = series.iloc[h]/base - 1`。所谓 "5d 远期收益" 实为事前漂移；"20d" 实为 −5d~+15d。**报告的数字混淆了事前事后移动。** 正确的事件研究法应以事件日（或事件+1）为基价算 CAR/CAAR。
  - **B2 前视**：`:37` 用 `e.event_date` 入场，但交易者只能从 `observed_at` / 公告发布日才知道。应取 `GREATEST(event_date, observed_at)` 或文档 `published_at`。
  - **B3 幸存者偏差**：`:35-36` join 当前 `companies` basket（来自固定 registry），已退市/被收购的负样本全丢。远期收益均值系统性偏高。
  - **B4 无基准/无风险调整/无显著性/无交易成本/无多重比较校正**：裸均值 + magic。2% 均值在牛市是负 alpha 却报为"正向信号"。多 `(event_type, polarity)` 桶无 Bonferroni/FDR，部分桶偶然显著。
  - **B5 n 误导**：`:66` 报的是首个 horizon 的样本数，但 `:55` 的 `if len(series) > h` 门槛使不同 horizon 样本数不同。
  - **B6 仅 US**：`:29-30,44-46` `_us_ticker` 过滤掉所有非美名，CN/HK/JP 事件被静默丢弃。与 B3 叠加，回测只代表"美股幸存者"。
- **性能**：`_prices`(`:18-26`) 每事件同步 HTTP 下载，尽管 schema 有 `prices` 表却不复用本地缓存。

### 3.3 仪表盘"投资信号"无校准 [高]

- **文件**：`api/dashboard.py`。
  - `conviction`(`:121`)、`alpha`(`:221`)、`crowding`(`:220`)、`supplyTightness`(`:219`)、`estRevision`(`:101-108` 的 `*22` magic)、`momentum = change_m*4`(`:118`) —— **无校准的线性拼凑**，直接当投资结论呈现给用户。项目里有 `backtest/` 模块却从不拿它校准这些分。每项至少需配 disclaimer，理想是标定到催化剂-收益回测。
  - **过期硬编码汇率**：`:22-23` 硬编码 FX 表，且 region 当货币用（`EU/GB/NO` 并列 `KR/JP`）。公司报告币种取决于上市地而非总部 region。**跨区域市值比较有系统性、未标注的误差。**
  - **`valuationPctile` 混 PE 和 PS**：`:159-170` `pe_ratio or ps_ratio`，两个不同比率混排，排名不可比。
- **性能**：`_load()`(`:71-94`) 每次调用做 4 次全表扫（companies/prices/fundamentals/events），且 `overview()`(`:427-429`) 级联多次 `_load()`、`segments()`、`regime()` —— 一次 `/api/ui/overview` 触发 6+ 次全表扫，无任何缓存（`@lru_cache`/TTL/物化视图）。

### 3.4 前沿探索模块 DELETE+INSERT 非事务 [高]

- **文件**：`exploration/synthesis.py:118`。
- **真相**：`DELETE FROM frontier_fronts WHERE domain=%s` 后接循环 INSERT，**裸在 autocommit 下**。重插中途失败，**旧前沿被删光只剩半套（或空）**。docstring 称 "replace don't accumulate"，实现却是无安全网的破坏性操作。应把 delete+insert 批包进单事务。

### 3.5 护城河代码零测试 [严重 / 不对称]

- **比率**：73 源文件 / 206 行测试（2.6% LOC）。前端 0 测试（`web/package.json` 无 test runner、无 ESLint）。
- **唯一 e2e**（`tests/test_pipeline.py`）问题：
  - `:31-38` `fake_docs` 返回常量向量 → RRF 排序实际没被走到，"检索"近乎随机。
  - `:40-52` `fake_complete_json` 返回固定抽取结果 → 主题路由/边校验/事件去重被绕过。
  - 断言浅（`"NVIDIA" in content_md`、`citation_count >= 1`），从不测 `evidence_gate` 失败路径。
  - `:12-22` import 时就 `db.init_schema()`，有副作用危险（指向共享/staging DB 时）。
- **关键路径全无专用测试**：

| 模块 | 测试? |
|---|---|
| `agents/evidence_gate.py`（信任闸） | 无 |
| `kg/store.py`（双时态 supersession） | 无 |
| `kg/resolve.py`（模糊消解） | 无 |
| `kg/extract.py`（主题路由/边事件校验/去重） | 无 |
| `retrieval/vector.py`（RRF 融合） | 无 |
| `models/llm.py`（BudgetExceeded / `_extract_json` / retry） | 无 |
| `agents/state.py`（cite 去重） | 无 |
| `eval/harness.py`（评测工具本身） | 无 |
| `parsing/tie_out.py` | 有（3 项，尚可） |
| `api/app.py`（45 端点） | 无（无 TestClient） |

### 3.6 评测 harness 形同虚设 [高]

- **文件**：`eval/harness.py` + `eval/gold.json`。
- **gold 集过小**：`gold.json:3-8` 仅 **4 条**检索项。`eval_retrieval`(`:19-30`) 的"命中"定义为 `expect_keywords` 任一作为子串出现在 top-8 chunk 文本里。关键词如 `["revenue","data center"]` 对 NVIDIA 公告，hit-rate 几乎必然 1.0——**统计上无意义**。无 MRR/NDCG/recall@k，无难负例。
- **judge 无校准**：`eval_report_rubric`(`:33-51`) 单 judge、无人类相关、无 inter-judge agreement、无 few-shot；`:46` 报告截断到 `[:9000]`；`:49` `passed = s.passed[:len(rubric)]` —— 模型少返回的项静默计 False。

### 3.7 事务边界缺失 [高]

- **证据**：`storage/db.py:85-95` 每次 `query/execute` = 独立 autocommit 事务；`kg/store.py:33-45` check-then-insert TOCTOU；`schema.sql:80-96` `kg_edges` 无 UNIQUE 约束。
- **后果**：并发或重跑插重复边；多步 KG 写无原子性；`parsing/parse.py:84` 的 DELETE chunks 与随后的 INSERT 循环在不同连接/事务，中途失败该 doc 0 chunks。
- **修复方向**：暴露 `db.tx()` 事务上下文；所有多步写包进单事务；关键去重走 DB UNIQUE 约束而非应用层 check-then-insert。

### 3.8 后台任务非持久化 [中]

- **证据**：ingest/pull/report/exploration-refresh/ops-source-run 全用 Starlette `BackgroundTasks`（`app.py:109/178/206/343/451`）；无队列、无重试、无幂等令牌、无状态端点（仅 `report_runs` 被跟踪）。bg 闭包全 `except Exception: log.warning`（`app.py:103/175/203`），客户端只见 `"status":"started"` 永远。
- **后果**：进程重启 = 在途任务丢失；连点两次"采集"/两次"刷新前沿" = 双跑。
- **Starlette bg 语义**：sync bg task 在响应后跑在同一 threadpool，与请求处理器争 worker，长 bg job 会饿死入站请求。

### 3.9 报告流水线宣称"可续跑"实际不可恢复 [中]

- **证据**：`agents/graph.py:20-49` 总从 scope 重跑；`state.py:68-78` `RunState.load` 无调用者；仅 `BudgetExceeded` 被捕获。
- **后果**：任何瞬时错误 kill 整个 run，已烧 token 全废；与 DESIGN §6 "检查点续跑"承诺不符。

---

## 四、工程债精选（非穷尽，证据导向）

### 4.1 后端

- **配置 / 模型层**
  - `config.py:52` `market_data_order` 是**死配置**：`providers/__init__.py:23` 硬编码 `_MARKET = [fmp, finnhub, polygon, yahoo, wind, aifinmarket]` 从不读此设置。
  - `llm.py:184` `complete_json` 的"安全空默认"对带必填字段的 schema 是谎言：本体自己的 `ExtractedNode/Edge/Event`(`schema.py:12-29`) 有必填字段（name/node_type/src/dst/rel_type/evidence）。失败解析 → `ValidationError` 而非安全默认。更糟的是，调用方（extract/expert）把空默认当"无事实"并写 idempotency 标志 → **失败 doc 永不重试**。
  - `llm.py:21` import 时改全局 `litellm.drop_params = True`——全局库突变，影响同进程其他 LiteLLM 用户。
  - `llm.py:99-106` `_record` `except Exception: pass` 把 usage 日志失败全吞（含 schema bug）。

- **存储层**
  - `db.py:68` `class conn:` 小写类名违反 PEP8，且 `:58` 在定义前引用，可读性陷阱；`db.py:24-29,74-77` 每次 checkout 重新 `register_vector` 并吞异常，vector 缺失时报错远离根因。
  - `objects.py:22,39` 把 netloc 当目录：`file://localhost/data` → `Path("localhost/data")`。
  - `objects.py:12-13` SHA-256 截断到 24 hex（96 位），截断无理由。
  - `structured.py:99-113` `upsert_insider` SELECT-then-INSERT 的 TOCTOU（`ON CONFLICT DO NOTHING` 已使 SELECT 冗余且原子）。
  - `structured.py:100-102` dedup 把 `None` stringify 成 `"None"`，与真字符串 `"None"` 不可区分。

- **本体层**
  - `schema.py:14,22,31` `node_type/rel_type/event_type` 为自由 `str`，合法值只在 description 文本里——pydantic 不强制。LLM 幻觉 `node_type:"Company"`（不在枚举）通过校验并污染 KG。应用 `Literal[...]` 或 enum。
  - `standards.py:116-122` `RATIO_METRICS` 把 `EPS_DILUTED`（每股货币额）当"无量纲%"。
  - **canonical 指标映射散落**：`standards.py` 只有 fmp/finnhub/yahoo 的 map；polygon/wind/aifinmarket 各自的 map 藏在 provider 模块里——"单一指标词表"的承诺被掏空。`canonical_metric`(`:192`) 仅覆盖 3 provider 且大小写敏感。

- **采集 / Provider**
  - **零 robots.txt 尊重**：全仓仅 `ingestion/base.py:17` 注释提及，无任何 connector 实际抓取/尊重 robots。
  - **ingestion 路径零重试零断路器**：仅 `providers/base.py:18` 用 tenacity（且重试所有异常含 4xx，浪费配额、更快触发限流）；ingestion 连接器（cninfo/edgar/jobs/news/wechat）全裸 `httpx.get`。
  - **polite() host 键错**：`cninfo.py:44,68`(`polite("cninfo")`/`("eastmoney")` 但实际打 cninfo.com.cn / data.eastmoney.com）、`jobs.py:16,22`(`("greenhouse")`/`("lever")` 但实际打 boards-api.greenhouse.io / api.lever.co)——速率限制状态挂在假键上，与真实 host 不协调，**实质是装饰性的**。`edgar.py` docstring 称"honors SEC rate limit"但**从不调 `polite()`**。
  - **polite() 单全局锁跨 sleep 串行化所有 host**（`base.py:22-29`）：host A 的 2 秒 sleep 阻塞不相关 host B。
  - **API key 走 query string 泄露进日志**：`finnhub.py:31`、`fmp.py:39`、`polygon.py:47` 三个 provider（finnhub/fmp/polygon），应改 header。
  - **SSRF 面**：`news.py:16-22` 任意 URL `httpx.get(follow_redirects=True)` 无 scheme/host 白名单，云元数据/内网 RFC1918 可达；`wechat.py:81-85` base URL 无校验，typo 到攻击者主机即泄露 Bearer token。
  - **抓取脆弱**：`cninfo.py` 经 akshare（屏幕抓取库非官方 API）但打 GREEN 标签；两个 `except Exception`(`:48,72`) 使形状变化静默返回 `[]`。
  - **三处重复 `_alias_index`/`_link_company`**：wechat/polymarket/twitter 各一份相同逻辑。
  - **文本截断 magic 不一致**：`400_000`(edgar) / `120_000`(wechat,edgar news) / `20_000`(jobs) / `8_000` 等，无文档。

- **解析**
  - `parse.py:84` DELETE chunks 与 INSERT 循环非同事务。
  - `parse.py:86-95` 手动循环 `execute` 非 `executemany`，250 chunk = 250 round-trip。
  - `parse.py:47-56` Docling 失败静默 `except Exception: pass`，腐坏 PDF 不记日志。

- **retrieval**
  - `vector.py:82-87` 检索不按 `permission <> 'red'` 过滤——权限模型是 advisory 非强制。
  - `vector.py` 每次搜索 3 次 DB 往返；`nodes.run_analysts` 调 `_ground` 5 次（每 analyst）×k*3 → 每报告 ~15 往返、~120 候选行，无跨 analyst 缓存。
  - `graphrag.py:72-83` `changes_since` 是死代码（全仓无调用者）。

- **agents**
  - `nodes.py:27-35` `scope` 在无 company_id 时静默用 `company_name = company_id`（即 None 或未解析串）继续，下游全 no-op 却仍产出关于 "None" 的报告。
  - `report.py:14-15` `_findings_brief` 与 `debate.py:11-12` 逐字重复。
  - `state.py:30-32` `cite()` 去重键 `chunk_id or url or title` 脆弱：无 chunk_id 时按 url 去重，同 URL 不同 chunk 坍缩成一个引用号，丢失独立溯源。

- **CLI**
  - `cli.py:120` `open(out,"w").write(...)` 无 `with`，文件句柄泄漏。
  - `cli.py:105` `report` 默认 `auto_approve=True` + `cli.py:211` `serve` 默认绑 `0.0.0.0` —— 对金融平台是危险默认。
  - `cli.py:182-185` `except Exception: n="—"` 吞 DB 错误；`:183` f-string SQL `FROM {tbl}`（虽 tbl 来自硬编码列表，但模式是 SQL 注入种子）。

### 4.2 前端

- **死状态 / 假交互**
  - `context.tsx:14-17,30-32,60` **`period` 是死状态**：存了、TopBar 渲染了分段控件（`TopBar.tsx:69-76`），但**从不传给后端、不过滤数据、不重 fetch**。用户点 "1W/3M/YTD" 无任何反应。金融终端上这是误导。
  - `types.ts:88` `Company.watched` 后端恒为 `True`（`dashboard.py:145`），watchlist 的星列不传达信息。
  - `TopBar.tsx:38` 后端把所有 theme 标 `active=True`（`dashboard.py:421`），切到无公司主题产生空仪表盘，客户端无防护。

- **可访问性 [WCAG 违规]**
  - `components/ui/Card.tsx:8-12` `<div onClick>` **不可键盘聚焦、无键盘激活**，被探索页卡片导航（`ExplorationOverviewPage.tsx:46-52`）使用——键盘用户无法激活。违反 WCAG 2.1 Level A。应 onClick 存在时渲染 `<button>`。
  - `api/static/index.html:110,120-123` `innerHTML` 注入 DB 来源的 name/summary —— 潜在 XSS（mitigated 仅因数据为运营者种子）。

- **性能**
  - 多处 `[...x].sort(...)` 在每次 render 无 `useMemo`：`Sidebar.tsx:33`、`ChainHeatmap.tsx:46`、`RegimeSummaryCard.tsx:20`、`CatalystCalendar.tsx:18`、`CompanyWatchlist.tsx:41`。
  - `Sparkline.tsx:29` gradient id 只用颜色+长度生成 → 同色同长度的 sparkline 撞 id（无效 DOM，所有引用第一条定义）。应用 `useId()`。
  - `CompanyWatchlist.tsx:38-39` `segName = segments.find(...)` 嵌在 map 里 O(N*M)，应建 Map（`SignalFeed.tsx:30-34` 已正确）。

- **工程治理**
  - **无 ESLint**（`package.json` 连 lint 脚本都没有），却有一行 `// eslint-disable-next-line react-hooks/exhaustive-deps`（`pages/ops/_shared.tsx:34`）无 linter 背书。
  - **零前端测试**（无 vitest/jest）。
  - `tsconfig.json:16-17` `noUnusedLocals/Parameters: false`；缺 `noUncheckedIndexedAccess`（大量 `arr[i-1]`/`prices[0]` 索引）。
  - **API 边界无运行时校验**（无 zod/io-ts），`api.ts:13-17` 是 `fetch + as T` 强转。契约漂移静默。
  - `api.ts:15` 错误 `throw new Error(\`${path} -> ${r.status}\`)` 丢响应体，用户只见 "→ 500"。
  - **fetch helper 三处重复**：`lib/api.ts:13-17`、`lib/exploration.ts:4-14`、`lib/ops.ts:17-26` 各一份相同 4 行。
  - **`fmtUsd`/`fmtInt` 至少 3 份本地重复**（`CompanyPage.tsx:482`、`ModelsPage.tsx:29`、`DataLakePage.tsx:40` 实现还不同），应进 `format.ts`。
  - `App.tsx:38` `<Route path="*">` 把任何未知 URL 静默渲染成 dashboard，**无 404**。
  - 无 ErrorBoundary，任一 page 渲染错误整屏白屏。
  - `exploration/_shared.tsx:3` exploration 页 import ops 模块（`useAsync`）—— 层级倒置；应抽到 neutral 位置。
  - `vite.config.ts:15` 生产 `sourcemap:false` + 无错误追踪 —— 金融终端应有错误上报。
  - 假轮询：`SourcesPage.tsx:51`/`AltDataPage.tsx:26` 用 `setTimeout` 固定时长后假装"完成"并 reload，无真实 job-status 端点。

---

## 五、值得保留的优秀实践

为避免审核只挑刺，明确这些是**应保留、不要在重构中丢失**的品味：

1. **双时态 KG 存储建模**（`store.py` + `schema.sql:65-119`）—— `t_valid_*`/`observed_at`/`invalidated_at` 是"后发不覆盖前真"的正确模型，业界少见做得对。问题仅在 add_edge 去重。
2. **两级 LLM 路由 + per-run USD 上限 + per-node usage 记账**（`models/llm.py`）—— 成本可观测性是生产级（需补批量路径覆盖与硬上限）。
3. **Schema 约束抽取**（Pydantic JSON-schema 进 prompt，`ontology/schema.py` + `extract.py:73`）—— provider 无关的结构化输出，无厂商锁定。
4. **混合稠密+词法 RRF 检索 + 每条命中带完整溯源**（`retrieval/vector.py` 的 `Hit.citation()`）—— 正是 citation-disciplined Agent 层需要的。
5. **确定性、可 checkpoint、人工审批中断的 DAG 形状**（`graph.py`/`state.py`）—— 可控 Agent 的正确骨架（问题在执行不在形状）。
6. **有界多空辩论**（`debate.py:8` `_ROUNDS=2`）—— 唯一的"涌现自治"区被显式封顶。
7. **数值对账闸**（`parsing/tie_out.py`）—— 保守 by design（不能判定即通过）的姿态正确，重闸可同接口插入。
8. **本体即代码 + FIBO/schema.org IRI 锚定**（`ontology/standards.py` + `nodes/edges/catalysts.py`）—— 构建与采纳的权衡文档（`standards.py:1-25`）是范例级设计文档。
9. **信号→催化剂统一**（`signals.py` + `SIGNAL_TO_CATALYST`）—— 估计上修/内部人集中买入/Polymarket 映射到与公告同源的事件流，使检索/回测/Agent 接口统一。
10. **Expert-pass 双表模式**（`expert.py`）—— 每 doc 进审计表、仅 kept 进本体，带质量门控。
11. **Exploration 合成的引用校验**（`synthesis.py:125`）—— 唯一一处真正校验被引 ID 在源集内存在的代码，应复制到 extract/expert。
12. **全程参数化 SQL**（`%s` + tuple）—— 全仓零用户输入拼进查询（仅 ops.py 有非用户输入的表名字符串构造，属潜在非实际）。
13. **sync `def` 路由处理器**（非假 async）—— 给定阻塞的 psycopg3/yfinance/LLM 栈是正确的；勿在未 async 化 DB 前转 async。
14. **优雅降级**贯穿（provider `available()` 门控、`backtest/catalyst_returns.py:24-26` 缺 yfinance 时返回 None、dashboard NaN 守卫）。
15. **许可合规 CI 硬规则**（`scripts/check_licenses.py` 阻断 AGPL/GPL/NC）—— 机构级自觉，早期项目罕见。
16. **多语标签一致**（`types.ts` 的 `CATALYST_LABEL`/`SOURCE_LABEL`/`REGIME_LABEL` 字典与 types 同位）。

---

## 六、修复路线（按优先级，每步可独立交付、基本不引入新依赖）

### 阶段一：止血（~半天）
- [ ] **吊销** `.env.example:7` 的 DeepSeek Key，改注释 stub；`git filter-repo` 清历史；CI 加 gitleaks。
- [ ] Secret 改 `SecretStr`；评估是否必须镜像进 `os.environ`（否则注入式传 key）。
- [ ] `Dockerfile` 加非 root `USER`；`docker-compose.yml` Postgres 绑 `127.0.0.1`。
- [ ] `ci.yml:28` 去掉 `|| true`；加 `ruff format --check`。
- [ ] API 加可选 `XAR_API_TOKEN` 中间件（默认关）；`/approve` 与各 `*/run`、`/exploration/refresh` 即便自用也建议开。

### 阶段二：让信任层名副其实（3-5 天）—— 决定"是否合格 AI 投研平台"
- [ ] 证据闸真闸：`passed=False` 时 escalate/重检索/挂起，而非一律 `awaiting_approval`。
- [ ] 引用编号范围校验（`[n]` 必须在已注册 citations 内），修正 `_CITE`/`_NUM` 正则。
- [ ] 抽取的 `evidence` 真存真验（extract.py/expert.py 读取并校验存在于原文）。
- [ ] judge 拿到被引原文 chunk，并用 strong tier（或至少不让 weak 裁 strong）。
- [ ] `complete_json` 失败语义修正：失败 doc 标记"可重试"而非被 idempotency 冻结为"无事实"。
- [ ] retry 路径保留 `response_format`。
- [ ] Prompt 注入防御：所有 untrusted 文本加 `<DOC>...</DOC>` 围栏 + "以下为不可信数据"序言（extract/expert/synthesis）。
- [ ] 批量任务（build_kg/expert.process/synthesize_all）传 run_id 或加独立 batch 预算。
- [ ] 修正 `kg/store.add_edge` 去重按 validity window；支持多来源佐证叠加。
- [ ] 前沿探索 DELETE+INSERT 包进单事务。

### 阶段三：给护城河上测试（2-3 天）
- [ ] `conftest.py` 加 transaction-rollback 的 Postgres fixture，替代 import 时 `db.init_schema()`。
- [ ] 为 evidence_gate / store supersession / resolve 模糊 / vector RRF / llm 预算+`_extract_json` / state.cite 去重 写专用测试，覆盖失败路径。
- [ ] CI 加 `--cov=xar --cov-fail-under=40`。

### 阶段四：修正回测方法论（2-3 天）
- [ ] 基价改事件日（或 +1）；入场改 `observed_at`；加 benchmark 异常收益；至少标 n / std / 显著性；声明幸存者偏差 + 多重比较；非美股复用本地 prices 表。
- [ ] dashboard 的 conviction/alpha/crowding 等分要么标定到回测，要么明确标注"未校准启发式"。

### 阶段五：主题一致性 + 收尾（1-2 天/项）
- [ ] Agent/expert 层真做多主题（nodes.ANALYSTS/debate prompt 按 company.theme 选框架），或删除 extract.py 死 theme key 明确单主题边界。
- [ ] 死配置清理：`market_data_order` 接线或删除；`graphrag.changes_since` 删除或接入；`resolve.py` docstring 修正或实现人审 flagging。
- [ ] 三个 provider key 改 header；polite() host 键修正；ingestion 路径加重试 + Retry-After + 断路器。
- [ ] 前端：`Card` 可访问性；`period` 死状态接通或移除；Sparkline `useId()`；抽 fetch helper / fmtUsd 进共享；加 ESLint；App.tsx 加 404 + ErrorBoundary。

### 阶段六：工程基建（持续）
- [ ] 依赖 pin（uv.lock 或 pip-tools + hashes）；Docker 基镜像按 digest。
- [ ] 前端加 vitest；CI 跑前端 build/typecheck/test。
- [ ] Python 矩阵测 3.11/3.12/3.13；加 mypy。
- [ ] 后台任务轻量持久化（pg-based 幂等令牌 + 状态端点），替代裸 BackgroundTasks。
- [ ] `/api/report`、`/api/backtest` 改异步入队 + 轮询，释放请求线程。
- [ ] `_load()` 加缓存或物化摘要表。

---

## 七、结论

**骨架品味在线，工程纪律扎实，文档质量高——但"可信"这一唯一真正重要的命题，实现上是装饰性的。**

P0 凭证泄露须立即处理；P1 信任层（证据闸真闸 / 引用校验 / 证据原文落地 / judge 接原文 / 注入防御 / 批量预算）直接决定"这是不是个合格的 AI 投研平台"；P2 护城河测试 + 回测方法论修正决定"能否自称机构级"。

建议优先推进**阶段一（止血）+ 阶段二（信任层）**——这两步把项目从"研究原型"推向"可交付产品"，且不引入新依赖、可独立交付、不留新债。

---

*本文件为审核记录。原始 GLM-5.2 审核意见基于 commit `9a2091b`。下方"复核与处置"为对该意见的二次裁定与实际修复记录。*

---

## 八、复核与处置（2026-06-22）

**复核原则**：本项目为**自研自用、以效率与结果为目的**的投研系统，遵循*开发简单 / 易于维护 / 无合规要求*。据此对原意见二分：违反该原则的（企业级合规与防御开销）**不予采纳**；影响"可信结果"这一项目目的的**完整修复**。

### 8.1 已修复（直接影响可信结果）

| # | 意见 | 修复 | 文件 |
|---|---|---|---|
| §1.2 | 抽取的 evidence 原文被丢弃 | 新增 `_grounded()`：edge/event 的 evidence 必须在原文中（归一化子串或 ≥70% 词元重合），否则**丢弃**；evidence 落地进 `kg_edges.attrs` | `kg/extract.py`、`kg/store.py` |
| §1.8 | 主题撕裂（焊死光模块） | expert system prompt 改为覆盖 5 主题；analyst 检索词 / debate+risk 风险词按 `company.theme` 选择（`_THEME_TERMS`） | `kg/expert.py`、`agents/nodes.py`、`agents/debate.py` |
| §1.3 | tie_out 正则解析错金融数字 | token 级解析：`(1,234)`→负、`%` 排除、`$`/范围正确；英文关键词加 `\b` 边界（`net`≠`network`） | `parsing/tie_out.py` |
| §1.4 | 边去重违反双时态 | 去重按 validity window（`IS NOT DISTINCT FROM`）；同窗口重断言→置信度叠加（多源佐证） | `kg/store.py` |
| §1.1 | 证据闸不闸 / 正则缺陷 | 引用编号范围校验；数值正则收紧（排除年份/季度）；judge 接入被引原文 chunk；judge 失败默认 risk=0.6（不放行）；`auto_approve` 仅放行 PASS 报告 | `agents/evidence_gate.py`、`agents/graph.py` |
| §1.6 | retry 丢 JSON 模式 / 批量无预算 | retry 保留 `response_format`；批量任务自动生成 run_id + 独立 `llm_max_usd_per_batch` 预算 | `models/llm.py`、`config.py`、`kg/extract.py`、`kg/expert.py`、`exploration/synthesis.py` |
| §1.5 | 实体消解中文后缀 / alias 污染 | normalize 去中文后缀（有限公司/集团/科技…）；fuzzy 阈值 0.55→0.62；仅 ≥0.85 才写回学习型 alias | `kg/resolve.py` |
| §3.2 | 回测四重偏差 | 基价改事件日 t0；复用本地 `prices` 表；纳入非美股；输出 n/std + 幸存者/无基准免责声明 | `backtest/catalyst_returns.py` |
| §3.4 | DELETE+INSERT 非事务 | 新增 `db.tx()` 事务上下文，前沿替换包进单事务 | `storage/db.py`、`exploration/synthesis.py` |
| §3.3 | 估值分位混 PE/PS | PE 与 PS 各自分池排名，不混排 | `api/dashboard.py` |
| §1.7 | 注入防御缺失 | extract/expert 对不可信文本加 `<DOCUMENT>`/`<CONTENT>` 围栏 + "仅作数据、勿从指令"序言 | `kg/extract.py`、`kg/expert.py` |
| §3.5 | 信任层零测试 | 新增证据闸拦截路径 + 抽取 grounding 丢弃 的专项测试 | `tests/test_pipeline.py` |

验证：`pytest` 14 passed（+2）、`ruff check` 通过。

### 8.2 不予采纳（违反自用 / 简单 / 无合规原则）

- **§2.1/2.2 凭证 & SecretStr、§2.3 Docker 非 root / PG 绑定回环、§2.4 CI 阻塞 / mypy / 覆盖率门禁、§2.5 依赖 pin / lockfile、§3.1 API 鉴权 / CORS / 限流、§3.6 评测集扩容、§3.8 后台任务持久化队列、§3.9 续跑、§1.9 DAG 改造**：单操作者、Tailscale 内网自用，这些是企业级合规与防御开销，与"简单易维护"冲突，纯负担。
- **绝大多数 §4.x 工程债与前端 a11y/ESLint/404/ErrorBoundary 治理类**：不影响结果正确性，按需逐个轻量处理，不在本轮范围。

> 结论：原审核对"可信层装饰性"的核心判断成立且已逐项落地修复；其余以合规/工程治理为主的建议在自研自用语境下不予采纳。

---

## 附录 A：修复轮次独立复核（Ontology 专项，第二意见）

> 视角：基本面量化策略 + Palantir 技术专家；自研自用、效率优先、无外部合规约束。
> 复核对象：上述 §8.1 已落地的修复（证据闸绑定 / bitemporal 去重 / 证据原文落地 / resolve CN 修正 / synthesis 事务 / 估值分桶拆分 等）。
> 方法：逐条读取当前源码复核（line ref 均经本人核对，非转述）。结论：**§8.1 信任层修复整体成立**（详见 A.3），但引入 / 残留 **3 个中等 bug + 3 个轻微问题**，应作为 §8.1 的收尾项处理。验证：`pytest` 18 passed、`ruff check` 通过。

### A.1 中等 bug（建议本收尾轮修复）

**A.1.1 回测 `_series` 在本地价格部分覆盖时阻断 yfinance 兜底 —— 样本量被静默低估**

- 位置：`backtest/catalyst_returns.py:64-66`（`_series`）+ 调用方 `:88`。
- 现状：`_series` 仅当本地 `prices` 表 < 2 行时才回落 yfinance（`return s if len(s) >= 2 else _yf_series(...)`）；但调用方 `:88` 要求 `len(s) >= max(horizons)+1`（默认 21），不足即 `continue` 跳到**下一个 ticker**，而非对本 ticker 兜底。
- 后果：本地窗口有 2–20 行的事件 → `_series` 返回短序列 → 调用方拒绝并跳过 → yfinance 永不为该事件被尝试 → **该事件静默丢弃**，`n` 被低估。不产生错误数字，但样本量统计失真，且与模块 docstring"仅在本地缺失时回落 yfinance"的契约相违。
- 修复方向：把所需长度（`max(horizons)+1`）传入 `_series`，本地不足即兜底；或调用方在本地不足时显式再试 yfinance。

**A.1.2 KG seed 边置信度每次重启单调漂向 0.99 —— 破坏 bootstrap_seed 的"幂等"契约**

- 位置：`kg/store.py:44-49`（corroboration 分支）+ `bootstrap_seed` 在 `store.py:109-142`（docstring `:111` "Idempotent — safe to run on every startup"，且每次启动 / 每次 `build_kg` 都跑：`cli.py:33`）。
- 现状：`add_edge` 对**任意**同窗口再断言做 `old + (1-old)*0.4` 提升（`:47`），**无来源独立性校验**。`bootstrap_seed` 每次重插 `SEED_EDGES`（`license_tag="seed"`，`:135-136`）和 `competes_in`（`license_tag="seed"`，`:137-140`），于是：
  - seed 边：0.90 → 0.94 → 0.964 → …
  - competes_in：0.80 → 0.88 → 0.928 → …
- 后果：seed 置信度跨重启非平稳、不再代表字面值；docstring"独立来源 corroboration"的语义被自我再断言违背。**这是 Ontology 可信度的根基之一，与 §1.4 双时态修复并列同属"图谱完整性"地基**，建议同优先级修。
- 修复方向（已具备现成判据）：seed 边已带 `license_tag="seed"`，corroboration 分支应 `if license_tag == "seed" or existing.license_tag == "seed": return`（跳过 self-reinforcement）；或更严格地按 `source_doc_id`/`license_tag` 是否与已存记录不同来判定独立性。

**A.1.3 证据 grounded 校验对中文退化为"全串匹配"——信任层最强制幻觉杠杆在 CN 侧召回率受损**

- 位置：`kg/extract.py:64-78`（`_grounded`），尤其 `:75` `toks = [w for w in re.findall(r"\w+", ev) if len(w) > 2]`。
- 现状：Python `re` 的 `\w` 在 Unicode 模式下匹配 CJK 表意字，连续 CJK 无分隔符会被并成**单个巨型 token**。实测（与独立复核一致）：`evidence="公司Q3营收创下历史新高"`、`text` 含轻度改写 → `toks` 退化为单个超长 token → `w in hay` 等价于已在 `:73` 失败过的全子串检查 → 70% 重叠兜底**对中文实质失效**。
- 后果：双语平台上，中文文档的证据 grounded 校验退化为"严格全子串匹配"，而英文享受 70% token 容忍。LLM 对 evidence 做**轻度改写**（即便 prompt 要求 verbatim，实测仍高频）→ 合法 edge/event 被判未 grounded 而丢弃 → **CN 侧召回率系统性偏低**。precision 不受影响（这是召回不对称）。对一个 CN 公司占大半的产业链图谱，这是信任层的关键缺口。
- 修复方向：对含 CJK 的文本改用**字符 n-gram 重叠**（char-bigram），或引入 `jieba` 分词；至少对非 ASCII run 取消 `len(w) > 2` 过滤并改用字符级滑窗。

### A.2 轻微问题

- **A.2.1 `ontology/metric_packs.py:102` 语义错配**：`sbc_pct`（"SBC % of Revenue"，unit=`ratio`）的 alias 含 `"stock-based compensation"`——后者是**美元金额**科目名。若抽取把 SBC 美元额按此 alias 命中，`canonical_kpi` 会路由到 `sbc_pct` 并以错误单位写入。实践风险低（LLM 从 hint list 选），但 alias 把字段标错语义。
- **A.2.2 `storage/db.py:118-125` `tx` 冗余 commit/rollback**：psycopg3 pool 的 `connection()` CM 在干净退出时本就 commit、异常时 rollback；`tx.__exit__` 在 `_cm.__exit__()` 前再 `commit()`/`rollback()` 属双重（harmless）。若日后在块内加 savepoint 逻辑，`tx.__exit__` 会把部分回滚也提交掉——知悉即可。
- **A.2.3 `kg/store.py:38-57` `add_edge` 的 check-then-update/insert TOCTOU 仍在**（原审 §3.7）：SELECT 与 UPDATE/INSERT 非原子，两并发抽取同一 edge 可双双漏过 SELECT 而双 INSERT。单操作者自用姿态下可接受，但该函数本轮被重写过，顺带记一笔。

### A.3 已修复且经复核成立的项（§8.1 信任层，认可）

- **证据闸真闸**：`evidence_gate` 新增 `_has_valid_cite` + `_FINNUM` + judge 默认 risk 0.6 + `auto_approve and metrics["passed"]` —— 闸现在实质绑定；`_judge` 路径可被 monkeypatch 测试。原审 §1.1 闭合。
- **双时态去重**：`add_edge` 用 `t_valid_from/to IS NOT DISTINCT FROM` —— 同关系不同窗口得以保留。原审 §1.4 闭合（A.1.2 是其 corroboration 副作用，非去重本身）。
- **resolve CN 后缀迭代剥离 + `_LEARN_THRESHOLD` 门控** —— 原审 §1.5 闭合。
- **synthesis delete+reinsert 包进 `db.tx()`，且 LLM 调用正确留在事务外** —— 原审 §3.4 闭合。
- **估值分桶 PE/PS 拆分**（`elif` 优先 PE）—— 行为保留，原审 §3.3 部分闭合。

### A.4 本轮收尾建议

A.1.1 / A.1.2 / A.1.3 均为**一行级修复且不引入依赖**，且都落在"图谱完整性 / 信任层"这一本项目核心竞争力上（A.1.2、A.1.3 尤其是 Ontology 地基），建议在收尾轮一并处理：
- A.1.1：`_series` 接收所需长度阈值兜底。
- A.1.2：corroboration 分支按 `license_tag == "seed"` 跳过 self-reinforcement。
- A.1.3：`_grounded` 对 CJK 改用字符 n-gram。

> 第二意见结论：**§8.1 信任层修复方向正确、落地产出可信**；上述 3 项中等 bug 是修复引入的边界回归，修复成本极低，不影响对整体修复质量"成立"的判定。

### A.5 独立复核与处置（2026-06-22，第三方裁定）

对附录 A 逐条**独立复核**（每条以脚本/DB 经验证，非转述），裁定与修复如下。验证：`pytest` **20 passed**（新增 2 个信任层专项测试）、`ruff check` 通过。

| 条目 | 独立验证 | 裁定 | 修复 |
|---|---|---|---|
| **A.1.1** 回测兜底缺口 | 实证：`_series` 在 `len<2` 才兜底，调用方要 `≥21`，2–20 行本地窗口的事件被静默丢弃 | **成立·已修** | `_series(…, need)` 取所需长度兜底，本地不足即试 yfinance 并取更长序列（`backtest/catalyst_returns.py`） |
| **A.1.2** seed 置信度跨重启漂移 | 实证：新建 seed 边经 4 次再断言 `0.9→0.94→0.964→0.978`，破坏 `bootstrap_seed` 幂等 | **成立·已修** | `add_edge` corroboration 分支：incoming/existing 任一为 `seed`、或同 `source_doc_id` → 跳过自我强化；仅**独立来源**(不同 doc、非 seed)才提升（`kg/store.py`） |
| **A.1.3** 证据 grounded 对中文退化 | 实证：CN 轻改写 `_grounded`→False，EN→True（CJK run 被 `\w+` 并成单 token） | **成立·已修** | 改为 **ASCII 词元 + CJK 字符 bigram** 重叠，CN 召回恢复、精度不变（`kg/extract.py`） |
| **A.2.1** `sbc_pct` alias 语义错配 | 核对：alias 含美元科目名 `stock-based compensation` 却 unit=`ratio` | **成立·已修** | alias 改为 `SBC % of revenue / stock-based comp %`（`ontology/metric_packs.py`） |
| **A.2.2** `tx` 冗余 commit | 核对：psycopg pool CM 已 commit/rollback，属 harmless 冗余 | **不予采纳** | 非 bug；显式事务边界自文档、比依赖 pool 内部行为更稳，自用原则下保留 |
| **A.2.3** `add_edge` TOCTOU | 核对：SELECT→UPSERT 非原子 | **不予采纳** | 复核人亦认可"单操作者自用可接受"；唯一约束与双时态多窗口设计冲突，不引入锁 |

> 第三方裁定：附录 A 的 **4 项有效问题（A.1.1/1.2/1.3/2.1）全部修复并加测试**；2 项（A.2.2/2.3）经独立判断属"自用姿态下可接受/非 bug"，按本项目简单·无合规原则不予采纳。

---

## 附录 B：消费周期三主题（互联网 / 美国零售 / 餐饮服务）独立复核

> 视角：基本面量化策略 + Palantir 技术专家；聚焦从零构建的 Ontology 体系在"经济周期轴（cycle axis）"上的扩展一致性。
> 复核对象：新增 `internet` / `retail` / `restaurants` 三个 cycle-theme 模块及其对 Ontology（segments / cycle / metric_packs / registry / dashboard / 前端 types）的贯通。
> 方法：逐条读取当前源码复核（line ref 均经本人核对）。结论：**Ontology 周期轴扩展整体一致、结构正确**；独立复核裁定 **1 项潜伏中等 bug 成立 + 1 项为误报**，其余全部通过。

### B.1 中等 bug（潜伏·建议修）

**B.1.1 `cycle.as_dict()` 的 dict 分支产出的 dict 形状与前端契约不符 —— 一旦公司级 `cycle` 覆盖被启用即渲染 undefined**

- 位置：`src/xar/ontology/cycle.py:90-104`（dict 分支 `:95-98` vs CycleProfile 分支 `:99-104`）；调用入口 `cycle_of_company` `:113-115`（`override` 走 dict 分支）；前端契约 `web/src/types.ts:55-65` `CycleInfo`。
- 现状：两个序列化分支产出**不同形状**的 dict ——
  - **CycleProfile 分支**（`:99-104`）产出 `{position, cyclicality, sensitivity, label, labelCn, short, rank, note, noteCn}` —— 与前端 `CycleInfo` 契约逐字段吻合。
  - **dict 分支**（`:95-98`）对**部分覆盖**（如 `{"position":"early_cycle"}`，无 `"en"` 键）走 `return {**label(pos), **p}` → 仅产出 `{en, cn, short, position}` —— **缺 `label` / `labelCn` / `rank` / `cyclicality` / `sensitivity` 五个字段**（其中多个为 `CycleInfo` 必填）。
  - dict 分支的判据是 `"en" not in p`；而 CycleProfile 分支产出的是 `label` 不是 `en`，故一次完整序列化的 dict 二次经 `as_dict()` 会落入 `{**label(pos), **p}` 分支并被补进 `en/cn`（与已有 `label/labelCn` 冗余但可用）—— 唯独**部分覆盖**那条路径是真破坏。
- 影响场景：`cycle_of_company`（`:107-122`）显式支持公司级 `cycle` dict 覆盖（`:113-115`）。目前 registry 内**无公司**使用该 dict 覆盖形式（所有周期主题在 **segment 级**经 `cycle.profile(...)`（`registry.py:111`）设置 → 返回 `CycleProfile` → 走正确的 CycleProfile 分支），故**当前潜伏、不触发**。但这是文档化的 intended feature（`:113` 明确读取 `company.get("cycle")`），一旦运营者加一条 `cycle={"position":"mid_cycle"}` 的公司级覆盖，`company_detail()`（`dashboard.py:582` → `cycle_of_company(c)`）即产出缺字段的 dict，前端 `CycleInfo` 渲染 `undefined`。
- 修复方向：dict 分支归一到与 CycleProfile 分支同形状 —— 由 `position` 解析出 `label/labelCn/short/rank`，`cyclicality/sensitivity` 缺省补默认，**emit `label/labelCn` 而非 `en/cn`**。一致性优于两套键名并存。

### B.2 经独立复核为"误报"（不予采纳）

**B.2.1 "Block 的 ticker 设成 `XYZ` 是否应为 `SQ`" —— 非问题，`XYZ` 正确。**

- 位置：`src/xar/ingestion/registry.py:603`（`_consumer("block", "Block", "XYZ", "net_fintech", "internet", ["Block", "Square", "Cash App", "XYZ"])`）。
- 裁定：Block, Inc. 已于 **2025-02 将 NYSE 代码由 `SQ` 改为 `XYZ`**；截至复核日（2026-06-23）`XYZ` 已生效约 16 个月。原复核人"Block trades under SQ"基于过时信息。alias 列表含 `Square`/`Cash App`（品牌/产品别名）且**不含** `SQ`（旧 ticker），与 `XYZ` 为现行代码一致。
- 风险点（非 bug，仅提示）：yfinance / Polygon 等价格 provider 现均以 `XYZ` 识别 Block，故行情/基本面拉取正常；若日后某 provider 仍只认旧码 `SQ`，会在该 provider 单点失败（被 `available()`/异常路径优雅降级），不影响图谱。**建议留一行注释**说明 SQ→XYZ 历史，避免后人"修正"回 `SQ`。

### B.3 经复核通过的项（Ontology 周期轴扩展一致性，认可）

- **行业 / NAICS / sector 映射完整一致**：`RESTAURANTS` industry、NAICS `7225`、`THEME_INDUSTRY` / `SEG_INDUSTRY` 条目齐全，无遗漏/冲突。
- **`_first_seg()`（`dashboard.py:55`）正确替换旧的硬编码 `"module_maker"` 回退** —— 周期主题不再借用光模块段；这是对原审"主题撕裂"类问题的结构性改进。
- **`tier = cycle.rank(...)` 复用正确**：`_theme_segment_ids` 与 `ChainHeatmap` 按 `tier` 排序，周期轴渲染无需改逻辑即可工作（chain tier 与 cycle rank 复用同一字段，axis 在 `kind`/`axis` 元数据区分）。
- **`cycle_of_company` 内 `SEGMENTS` 的 lazy import（`cycle.py:119`）正确**规避了 `ontology ↔ registry` 循环依赖。
- **DoorDash 从 `swe_vertical` 迁至 `net_gig` 干净**：无重复 id/ticker，旧 `SEED_EDGES` 条目已移除。
- **metric packs 无 alias 冲突、无重复键**：`same_store_sales` 正确扩展到 `restaurants` 行业。
- **测试**：20 个单元测试 + 新增 pipeline 测试全部通过；TypeScript 编译无错误。

### B.4 本轮建议

- **B.1.1**（`cycle.as_dict` dict 分支归一）属一行级、零依赖修复，且落在 Ontology 契约一致性上，建议在下次触及 `cycle.py` 时顺手修掉，避免日后公司级 `cycle` 覆盖启用时踩坑。
- **B.2.1** 建议在 `registry.py:603` 加一行注释（"Block rebranded SQ→XYZ Feb 2025"）防误改。

> 第二意见结论：**消费周期三主题的 Ontology 扩展结构正确、与既有 chain 主题轴对称、测试充分**；唯一实质 bug（B.1.1）当前潜伏、影响面仅限"公司级 cycle dict 覆盖"这一尚未启用的路径。Block `XYZ` ticker 为复核误报。

### B.5 独立复核与处置（2026-06-23，第三方裁定）

对附录 B 逐条**独立复核**（脚本/一手来源经验证，非转述）。验证：`pytest` **26 passed**（新增 1 个 B.1.1 回归测试 `test_cycle_as_dict_uniform_shape`）、`ruff check` 通过、`tsc` 通过。

| 条目 | 独立裁定 | 处置 |
|---|---|---|
| **B.1.1** `as_dict` dict 分支形状不符 | **成立**。脚本复现：部分覆盖 `{"position":"mid_cycle"}` 产出 `{cn,en,position,short}`，缺 `cyclicality/label/labelCn/sensitivity/rank` 共 5 个 `CycleInfo` 必填字段 → 前端渲染 `undefined`；完整 dict 二次序列化还会泄漏冗余 `en/cn` 旧键。另核：`en/cn` 在前后端**从未被消费**。 | **已修复**（`cycle.py:as_dict`）：折叠为**单一归一化路径**——`CycleProfile` / 完整 dict / 部分覆盖三类输入统一产出 exact `CycleInfo` 形状（emit `label/labelCn`，去除 `en/cn`）；部分覆盖经 `_CYCLICALITY_BY_POSITION` 补全 cyclicality、由 position 解析 label/short/rank。加回归测试钉死形状。 |
| **B.2.1** Block ticker 应为 `SQ`？ | **确认为误报**（GLM 裁定正确）。一手来源核实：Block 于 **2025-01-21** 将 NYSE 代码 `SQ`→`XYZ`（Block IR / BusinessWire）；`XYZ` 为现行。附带独立核查 **Gap `GPS`→`GAP`（2024-08-22 生效）**——本项目所用 `GAP` 亦为现行正确码。 | **保留 `XYZ`**；按建议在 `registry.py` Block 行加注释（"SQ→XYZ 2025-01，勿改回"）防后人误改。 |
| **B.3** 周期轴扩展一致性（6 项） | 认可，逐项与本人实现/早前验证一致。 | 无 action。 |

> 第三方裁定：附录 B 的 **1 项有效问题（B.1.1）已修复并加测试**；B.2.1 经一手来源独立核实确为误报，按建议加防御性注释；B.3 全部通过。

---

## 附录 C：8 主题全球个股域扩展 + 独立多智能体审查（2026-06-23）

> 目标：把 8 个主题的覆盖扩到完整相关投资域——**美股 ≥ $2B + 日/韩/台全部相关个股**，跑通后端→前端全链，并由独立智能体复核。

### C.1 方法（防幻觉的可复现流水线，`scripts/universe_build.py`）

1. **权威存在性闸**：缓存 Finnhub 六大交易所符号全集（US 30,697 / Tokyo 4,497 / KOSPI 2,566 / KOSDAQ 1,872 / TWSE 1,332 / TPEx 1,517 = **42,481**）。LLM 枚举的任何 ticker 不在对应交易所集合即丢弃——**杜绝幻觉代码入库**。
2. **枚举**（23-cell Workflow，theme×region）：5 个产业链主题 × {US,JP,KR,TW} + 3 个消费周期主题 × US → **1,231 原始候选**。
3. **确定性 verify**：存在性闸 + 与**策展核心**去重（ticker + 规范化名，排除 `u_*` 自身）+ 多主题合并 + **美股 ≥$2B 市值闸**（Finnhub profile2）+ 消费主题 **country==US 闸 + 非美周期 ADR 黑名单**（PDD/BABA/SE/CPNG/MELI…）。
4. **生成** `src/xar/ingestion/universe.py`（`COMPANIES += UNIVERSE`，策展核心保持可读/可测）。

落地：**629 新增公司**（drop：no_exist 161 / dup 294 / US<$2B 40 / 非美周期 28 / 审查校正 22），合计 **1,007 家**（US 367 / JP 235 / TW 189 / KR 142 / CN 77 …；JP 24→235、KR 9→142、TW 2→189）。全链跑通：seed→bootstrap(59 end-markets, 1,084 competes_in 边)→dashboard→docker 前端均渲染扩展域；行情/基本面经 yfinance(keyless)+Finnhub 后台增量回填（幂等可续）。

### C.2 独立审查（多智能体 Workflow：11 个只读 Explore 评审 → 对抗式复核 → 综合）

28 agents / 65 findings / 11 high（含若干被标 high 的**正向确认**）。逐条**独立裁定**（非照单全收）：

| 类别 | 裁定 | 处置 |
|---|---|---|
| **off-theme 误纳**（确认成立） | ai_chip: TOTO/Kitz；humanoid: DuPont/Amphenol/TE/Jabil/Celestica/Sumida/Eternal/Dynapack/Simplo/Largan/Tamron/Asia Optical；internet: Comcast/Fox（传统有线/广播）；retail: Sysco/US Foods/Performance Food（B2B 餐饮分销）/Valvoline（服务）；restaurants: Casey's（便利店）；space: 大韩航空（客运航司） | **已校正**：`THEME_DROP` 丢弃 22 条主题归属（多主题公司仅删伪标签、保留正当主题，如 DuPont→仅 ai_chip、Jabil/Celestica→仅 ai_optical、Casey's→仅 retail）；regenerate + 剪除 DB 陈旧行 + docker 重建。 |
| **段位标错**（成立） | onsemi 标 hum_motors | **已校正** `RETAG`→hum_power。 |
| **评审误报（独立否决）** | ai_chip Nippon Pillar（实为半导体流体系统供应商）、NGK **Insulators**（评审与 NGK **Spark Plug** 混淆，前者有真实半导体陶瓷业务）、Linde/Air Products（晶圆厂电子特气主力供应商）；retail GPC（NAPA 汽配零售）/CVNA（二手车电商零售）——均**保留**。 | 不采纳（保留）。 |
| **"重大遗漏"高危（误报）** | "Boeing/Lockheed/RTX 缺失"（space）、"MCD/SBUX/CMG 缺失"（restaurants）——评审仅看 `universe.py` 孤立视图；这些**已在策展核心**该主题下（经 DB 核验 `space_exploration`/`restaurants` 标签皆 True），去重正确剔除。 | 不采纳（系正确去重）。 |
| **正向确认** | 去重正确、零 dup id/ticker、1024(后 1007) 全 ticker 在权威集合内、SPA+8 主题 API+landscape 全 200。 | 认可。 |
| **接受的广度权衡（中危，记录不改）** | JP `ai_software`(101) 含部分 SI/系统集成、TW `space`(≈28) 含通用 PCB/半导体名——按"全部相关个股"指令的广度取舍；name 规范化未含韩文谚文（KR 去重靠 ticker，无碍）。 | 记录；后续按需收紧。 |

> 裁定：扩展**结构正确、闸门稳健、全链跑通**；独立审查的 **off-theme 误纳 + 段位标错已全部校正并复跑**，其余 high 经独立复核为误报/正确去重/正向项。综合 agent 因瞬时 500 未出报告，结论由本人据已确认 findings 裁定。最终 `INTEGRITY: CLEAN ✓`、`pytest` 21 passed、`ruff` 通过、live API 1,007 家全主题渲染。

---

## 附录 D：universe 生成数据可靠性独立复核（第一性原理·后端到前端全链）

> 视角：基本面量化策略 + Palantir 技术专家；**第一性原理**而非"行业最佳实践"；严格审查数据可靠性与稳定性。
> 复核对象：`scripts/universe_build.py`（生成器）→ `src/xar/ingestion/universe.py`（629 条 `u_` 记录）→ `registry.COMPANIES` → `bootstrap_seed`（别名写入 KG）→ `dashboard`/前端展示 全链。
> 方法：逐条读取源码 + 直接核验已落库的 `universe.py` 内容（非转述）。验证：`pytest` 26 passed、`ruff` clean。
> **结论先行**：功能层稳定（不崩溃、全链跑通、闸门结构正确），但**数据正确性层有 2 个 P1 缺口**，二者均落在项目"可信数据"护城河上、且修复成本极低（零新依赖）。**本附录对附录 C 的 `INTEGRITY: CLEAN ✓` 作出修正：该结论在其校验维度（id/ticker/交易所存在性）内成立，但 name↔ticker 一致性与 intra-universe 实体去重从未被校验，故"CLEAN"被高估。**

### D.1 P1 — 存在性闸只校验「ticker 在交易所」，不校验「ticker↔公司名」一致

**第一性原理**：universe 的可信度 = 每个 (ticker, name) 二元组都对应同一真实实体。`resolve_symbol` 只完成了这条等式的一半。

- **机制（`scripts/universe_build.py:107-123`）**：`resolve_symbol` 仅判 `cand in symbols.get(exch, {})`（`:115,121`）——即"ticker 是否存在于该交易所"。而 `cache_symbols`（`:83-90`）**已把权威的 Finnhub `description` 缓存为 dict 值**（`:90` `{d["symbol"]: (d.get("description") or "")}`），**却从未拿它与 LLM 给的 `name` 比对**。函数 docstring 自称 "kills hallucinated tickers"——对 ticker 成立，对 name（人读、且 LLM 最易幻觉、且直接展示给用户的字段）**零校验**。
- **实证（已落库的污染名称，原样进入 `companies.name`、`register_alias`、前端）—— 逐条从 `universe.py` 核验**：

  | id | name（入库） | tickers | 交易所权威对应 | 性质 |
  |---|---|---|---|---|
  | `u_us_vicr` | `'Amphastar'` | `VICR` | **Vicor Corp**（Amphastar 实为 `AMPH`） | ticker↔名 全错（两家不同公司） |
  | `u_us_form` | `'Photon Dynamics / Photon Control n/a'` | `FORM` | **FormFactor** | ticker↔名 全错 |
  | `u_us_pool` | `'Williams Industrial'` | `POOL` | **Pool Corp** | ticker↔名 全错 |
  | `u_tw_2492` | `'Hermes-Epitek (via Marketech) / GMM Tech — Cireco? Walsin? — Walsin Technology'` | `2492.TW` | 2492.TW=華新科(Walsin Technology) | **LLM 思考链焊进 name**（含 `?`/`—`/多 `/`） |
  | `u_tw_6531` | `'Mosel Vitelic / ProMOS? — Etron-adjacent — AP Memory (RAMXEED?)'` | `6531.TW` | 6531=AP Memory（愛普） | LLM 思考链焊进 name |
  | `u_tw_3363` | `'Sintai Optical... LuxShare-peer... All Ring... Browave... Intchains... Prime... (上詮光纖)'` | `3363.TWO` | 3363=上詮 Precision（上詮光纖） | LLM 思考链焊进 name |

- **全链影响**：`name` 是 dashboard 排序/展示与 `company_detail` 的主标签；并经 `kg/store.py` `resolve.register_alias(c["name"], c["id"])` 写死为该公司别名 → KG 抽取凡引用这些别名（含 `'Cireco? Walsin?'` 这种非自然别名）将解析到错误/污染节点。对一个以"可信/可溯源"为核心卖点的平台，前端出现 "Cireco? Walsin?" 是直接信誉事故，且污染**向下流入实体消解、KG、检索、报告**全链。
- **修复方向（零新依赖，证据已在手）**：verify 阶段对每个 resolved symbol，取已缓存的 `description` 与 LLM `name` 做归一化匹配（复用现有的 `norm_name`，叠加 token 重合度阈值；CJK 用字符级）；不匹配则**用 `description` 覆盖 `name`** 或丢弃记录。

### D.2 P1 — universe 内部不去重 → 同名实体重复入库，别名解析被污染

- **机制（`scripts/universe_build.py:140-149`）**：`existing_index()` 显式 `if id.startswith("u_"): continue`（`:148`）——name 去重只对**策展核心**生效，universe 内部不做 name/alias 去重；构建期 `merged` 仅按 resolved symbol 合并。故同一实体被 LLM 用两个不同 symbol 发出时，两条都存活。
- **实证（`universe.py` 核验）**：
  - `u_jp_3185` "Macnica Holdings"（`3185.T`，ai_chip/chip_gpu）
  - `u_jp_3132` "Macnica Holdings"（`3132.T`，ai_software/swe_security + humanoid）
  - Macnica 真实代码为 `3132.T`，故 3185 那条要么是重复实体、要么是把真实但不同的 `3185.T` 误标为 Macnica（后者即 D.1 的又一实例）。
- **后果**：`companies` 出现两行同名；`register_alias("Macnica Holdings", id)` 被调用两次、**后写覆盖先写**，别名→节点映射取决于 seed 顺序（非确定）；KG 中"Macnica"身份被割裂。
- **审计盲点（`scripts/universe_build.py:355-361`）**：`audit()` 只查 `dup_ids`、`dup_tickers`，**没有 `dup_names`**。故附录 C 的 `INTEGRITY: CLEAN ✓` 能通过——它证明不了"无重复公司"。
- **修复方向**：`existing_index`/merge 增加 intra-universe 的规范化 name + alias 去重；`audit` 增加 `dup_names` 维度。

### D.3 P2 — 美股 $2B 市值闸是「软闸」：mcap 查不到即静默放行，且审计看不见

- **机制（`scripts/universe_build.py:259-266`）**：
  ```
  if mc is None: rec["mcap_unverified"]=True; stats["us_mcap_unverified"]+=1  # 标记，不丢
  elif mc < US_MIN_MCAP_USD: rec["_drop"]=True                                # 仅此处丢
  ```
  随后 `out = [r for r in merged.values() if not r.get("_drop")]`（`:272`）——**mc 为 None 的记录被保留**。
- **后果**：任何 Finnhub profile2 + yfinance 同时失败（限流、退市、ADR、二次上市）都绕过附录 C 反复强调的 "$2B floor"。`audit()`（`:377`）又只在 `mc is not None and mc < floor` 时报 `us_below_2b`——**未核实者既进了库也过得了审计**。
- **证据一次性**：`generate()`（`:301-310`）丢弃 `marketCapUsd`/`mcap_unverified`，事后无法复核究竟多少未核实名入库。
- **修复方向**：`mc is None` 按"未达闸"处理（或单独输出待审清单）；把 `marketCapUsd` 落进 `universe.py` 以备再核。

### D.4 P3 — 稳定性几项

- **`registry.py:686` 裸 `except Exception` 静默吞掉整个 universe**：`universe.py` 任何缺陷（CJK 编码、再生成截断、`SyntaxError`）→ 系统静默以 378 家（而非 1007 家）运行，无日志、无 flag。"optional" 的本意只需 `catch ImportError`；现写法对"数据可靠性"是隐患。至少应 `log.warning`。
- **`generate()` 用 `{rec!r}`（`:310`）**：依赖 `dict.__repr__`，跨版本/插入序变化时 diff 噪声大，且丢弃了 verify 阶段拿到的 `marketCapUsd`（见 D.3）。改用显式 `json.dumps(..., ensure_ascii=False)` + 固定键序。
- **`us_profile`（`:167-183`）**：每个 US symbol 重新读 settings、`time.sleep(1.1)`，~367 个 US 名 ≈ 7 分钟串行，仅靠 `profile.json` 断点续跑；Finnhub key 仍以 query string 走（与原审 §4.1 同类）。非阻塞，但脆弱。

### D.5 对附录 C `INTEGRITY: CLEAN ✓` 的修正

附录 C 的 CLEAN 结论在其**已校验维度内**（零 dup id、零 dup ticker、全 ticker 在权威交易所集合、seg id 全合法）成立且已核验。但本附录揭示该闸门设计**遗漏了两个同等重要的维度**：

1. **name↔ticker 一致性**（D.1）——权威 `description` 已缓存却未用；
2. **intra-universe 实体去重**（D.2）——`existing_index` 跳过 `u_`，audit 无 `dup_names`。

故"CLOSED/CLEAN"应表述为：**"ticker 层面可信（exchange-existence + id/ticker 唯一），name 层面尚未校验，存在已验证的污染名称与同名重复入库。"** 这不否定附录 C 的结构正确性，但把"可信"的边界划清。

### D.6 经复核通过的项（认可）

- `seg` id 全部存在于 `SEGMENTS`（0 invalid）；themes/seg 键一致，无空 themes。
- 消费段（net_*/ret_*）全部带 `cycle` profile → 新增 US internet/retail 名的 cycle 徽章链路通畅；`cycle.as_dict` 单一归一化路径（B.1.1 修复）成立。
- `universe.py` 唯一消费点是 `registry.py`；`bootstrap_seed` 的 `competes_in` 对多主题公司按 `seg.values()` 去重正确。
- `pytest` 26 passed、`ruff` clean —— 功能层稳定，上述问题集中在**数据正确性**而非崩溃。

### D.7 本轮建议（优先级）

D.1（name↔ticker 校验，复用已缓存 description）与 D.2（intra-universe name 去重 + audit 补 `dup_names`）是本次变更最该在合入前闭合的两项——它们直接落在项目"可信数据"的护城河上，且**修复成本极低、证据已在手、零新依赖**。D.3（市值硬闸 + 落库 marketCapUsd）与 D.4（registry 窄化异常 + `generate()` 显式序列化）建议同期处理。

> 第二意见结论：**universe 生成流水线的结构与闸门设计正确、全链跑通、不崩溃**；但**数据正确性地基有 2 个 P1 缺口**（name↔ticker 不校验、intra-universe 不去重），二者使已落库的 `u_` 记录中存在**可验证的污染名称与同名重复**。鉴于 name 是 KG 别名与前端展示的根字段，建议在 universe 正式作为生产事实源前修复 D.1/D.2，并据此修正附录 C 的 `INTEGRITY: CLEAN ✓` 表述。

### D.8 独立复核与处置（2026-06-24，第三方裁定）

逐条**独立实证复核**（脚本核验 `universe.py` × 权威 `description`，非转述）。GLM-5.2 附录 D **结论成立且严重**——name 层从未校验，已落库 `u_` 记录确含大量 name↔ticker 错配。**全部 4 项 P1/P2 + 关键 P3 已修复**。

| 条目 | 独立裁定 | 处置 |
|---|---|---|
| **D.1** name↔ticker 不校验 | **成立**。实证：VICR='Amphastar'(实为 Vicor)、POOL='Williams Industrial'(实为 Pool Corp)、FORM='Photon Dynamics'(实为 FormFactor)、3185.T='Macnica'(实为 Dream Vision)，及大量 TW/JP/KR 把 LLM 思维链(`?`/`—`/`...`)焊进 name。**用 proper token-set 重测得 73 条真·错配公司**（前次 167 系我 norm 拼接 bug 的假阳，已纠正）。 | **已修复**：verify 增 `same_entity()` 闸（共享有意义 token / 子串 / 编辑距离 / 首字母缩写四测）——错配公司即丢（drop **69**）；保留者一律用**权威 `description` 覆盖 name**，原 LLM 名经 `_GARBLE` 过滤后留作 alias。结果：VICR/POOL/FORM/Macnica-3185 丢弃；Walsin/AP Memory/IIJ/NRI/Chatwork(→Kubell) 以权威名保留。**并连带发现并修复一处策展 bug**：`3596.TW` 策展标 'Sercomm'，实为 **Arcadyan Technology**（已改名）。 |
| **D.2** intra-universe 不去重 + audit 无 `dup_names` | **成立但部分被 D.1 吸收**：实证 Macnica 双record 实为 3185 误标(D.1 已丢)。 | **已修复**：verify 增按权威名去重；`audit` 增 `dup_names` 维度。独立细化：去重 key 仅剥法律后缀（`Sercomm`==`Sercomm Corporation` 合并，但 **`PSK Holdings`≠`PSK Inc`** 系两家真实不同公司，不误并）。`dup_names` 现 **0**。 |
| **D.3** $2B 软闸（mc=None 静默放行） | **成立**（设计脆弱；实测 `us_mcap_unverified=0` 故无实际污染）。 | **已修复**：`mc is None` 改为**硬丢**（未核实即不过闸）；`marketCapUsd` 保留于 `verified.json` 备核。 |
| **D.4** registry 裸 except 静默吞 universe / `generate` 序列化 | **成立**（窄化异常合理）。 | **已修复**：`registry.py` 改 `except ImportError`(缺文件静默) + `except Exception` **`log.warning`**(损坏不再静默退回 378)。`us_profile` 微优化判为非阻塞 cosmetic，自用姿态下不改。 |
| **D.5** 修正附录 C 的 `CLEAN` 表述 | **接受**。 | audit 现含 `name_mismatch`+`dup_names` 两维；在**扩充后的校验集**上 `INTEGRITY: CLEAN ✓` 重新成立。 |

**净效果**：1007→**947 家**（剔除 **60** 条错配/思维链污染记录），余者 name 全部权威化、CoT 别名清除、`dup_names=0`、US 全硬过 $2B。`pytest` 21 passed、`ruff` 通过、docker 重建后 live API 947 家全主题渲染、name-truth 抽验通过（Arcadyan 改正、污染名消失、权威名保留）。

> 第三方裁定：附录 D 的 **4 项（D.1/D.2/D.3/D.4）全部修复**，并据 D.1 同法**连带修正一处策展 name↔ticker 错误（Sercomm→Arcadyan）**；附录 C 的 `CLEAN` 按 D.5 修正为「在含 name↔ticker 一致性 + 实体去重的扩充校验集上 CLEAN」。GLM-5.2 本轮复核质量高、定位准，is a genuinely valuable second opinion。

---

## 附录 E：前向预期闭环 + semantic_facts PIT/去重 + 日常编排拆分 独立复核（2026-06-24）

> 视角：基本面量化策略 + 后端架构；延续前序附录的"可信结果"第一性原理视角。
> 复核对象：本次**未提交工作树变更**——前向预期解决阶段（`kg/resolve_claims.py` + schema + CLI + daily 接入）、`semantic_facts` 视图修正、回测 PIT/入场修正、`run_daily` 拆分 pull/extract、Dagster 双作业、provider 日志去密、extract narrative 接地策略变更、`nodes._graph_brief`、`graphrag` PIT 等。新增未跟踪文件 `resolve_claims.py`。
> 方法：逐文件读取 + 实证（`pytest` **36 passed**、`ruff check` 通过、关键 SQL/逻辑经测试覆盖）。
> **结论先行**：**未发现正确性 bug**——核心逻辑（hit/miss/stale、PIT 入场、视图去重、编排拆分）经新增测试与全量测试验证成立，整体是对原审 §1.x / §3.2 的延续闭合，方向正确。但有 **1 项信任层行为变更需明确签字**（narrative 不再接地）、**1 项正确但下游可观测的行为变更**（视图去重改变回测/检索输入），以及若干低危性能/健壮性/仓库卫生项。**本附录仅记录意见，不修改代码。**

### E.1 正确性：未发现 bug（经测试实证）

逐项核验，均成立：

- **前向预期闭环 `resolve_forward_claims`**（`kg/resolve_claims.py:46-96`）：hit/miss/stale 三态与 docstring 一致；grace 窗口（`base < today - grace_days`，`:65`）防止过新 claim 被过早判定；realizer 窗口 `> base 且 <= base+window_days`（`:77-78`）边界正确；`stale` 非终态——每轮重查，迟到的回填 realizer 仍可升级为 hit/miss（`:61` 查询含 `resolution='stale'`，`:84-87` 覆写）；幂等（终态 hit/miss 被排除、stale 已写则不重写 `:90`）。`(%s || ' days')::interval` 整型参数模式与既有 `structured.upcoming_calendar:178` 一致、测试通过（非 bug）。
- **`semantic_facts` 视图**（`schema.sql:419-437`）：event 臂新增 `license_tag IS DISTINCT FROM 'expert'` 去重正确（expert 镜像由 insight 臂承载，二者经 `expert_insights.kg_event_id` 关联，`expert.py:95-117` 已建立该镜像关系）；insight 臂 `LEFT JOIN kg_events e2 ON e2.id = x.kg_event_id` 不会扇放大（`id` 主键），`resolution` 正确透出。`test_resolve_expert_forward_claim_visible_via_view` 钉死该 P0 契约。
- **回测 PIT 入场**（`backtest/catalyst_returns.py:92-107`）：`entry = GREATEST(COALESCE(as_of, observed_at::date), observed_at::date)`——因 `observed_at` 为 `NOT NULL`（`schema.sql:88,110`），`entry` 恒非空，闭合原审 §3.2-B2 前视；空 `prices` 表时 `maxp IS NULL` 跳过 gate（`:94`）而非静默返回零行；与附录 A.1.1 的 `_series(…, need)` 兜底协同正确。
- **`run_daily` 拆分**（`orchestration/daily.py:104-170`）：shard 切片仅作用于 `pull`（`:134-135`）；`extract` 的 signals 迭代 `all_ids`（全宇宙，`:164`）而非分片——修正了旧 Dagster 每 shard 只 derive 本片信号的覆盖缺口；BudgetExceeded 仅捕获 LLM 阶段（`:158-163`），DB-only 的 signals/resolve 照跑；与 `test_run_daily_isolates_source_failures` 一致（stages 含 `resolve`）。
- **Dagster 双作业**（`orchestration/definitions.py`）：job 名（`pull_shard_job`/`extract_all_job`/`core_daily_job`）与 asset/op 名解耦，闭合原"job 名与 asset 名冲突"；`extract_schedule` 单 run/日、晚 pull 30min，run_key 无碰撞。
- **provider 日志去密**（`providers/base.py:36-39`）：token 走 `params`（finnhub/fmp/polygon 均如此），`url` 为基路径——不再把含 `token=`/`apikey=` 的完整 URL 写进日志，正确闭合原审 §4.1 凭证进日志（针对 GET 失败路径）。

### E.2 行为变更（非 bug，需 awareness / 签字）

**E.2.1 `extract.py` narrative 不再做接地校验 [中 / 信任层行为变更]**

- 位置：`kg/extract.py:173-178`。
- 现状：旧代码 `narrative = ev.narrative if (ev.narrative and _grounded(ev.narrative, text)) else None`——对 narrative 这一"改写型"字段额外做逐字接地；新代码 `narrative = (ev.narrative or "").strip() or None`——**narrative 不再校验是否在原文中**，与 `summary`（`:183` 本就直通不接地）对齐。
- 判断：**非 bug，逻辑自洽**——event 的*存在性*仍由 `:167` `_grounded(ev.evidence, text)` 闸住（未接地 event 整条丢弃，§8.1 核心信任修复保留），narrative 只是 LLM 对"为何/驱动什么"的改写，逐字接地召回损失（~95% 被判 blank）确实无原则依据。
- 需签字的点：narrative 是 `forward_looking` 事件流入 `_graph_brief`（`agents/nodes.py`，本轮改为 insight+event-narrative 合并展示）与 agent 上下文的语义字段——**这是 LLM 最易幻觉、且最不被 event 证据引文覆盖的内容**。本变更放宽了对该字段的接地，与项目"可信/可溯源"护城河方向相反。属作者有意识的取舍（注释充分），但建议至少：保留接地作为*软标记*（narrative 未接地时打 `grounded=False` 标志位而非丢弃），或确认 narrative 在下游仅作 advisory 上下文、不进结构化结论。

**E.2.2 `semantic_facts` 视图去重改变回测/检索输入 [行为变更 / 正向修复]**

- 位置：`schema.sql:426`。
- 现状：event 臂从"全部 kg_events"收窄为"排除 `license_tag='expert'`"。此前一条带 kg_event 镜像的 kept expert_insight 会同时出现在 event 臂（kind=event）与 insight 臂（kind=insight）——**被双计入回测 n 与 graphrag 检索**。本轮去重正确。
- 提示：这是**下游可观测的数值变更**——回测 `events_used`、按 `kind` 分桶的统计、`graphrag.semantic()` 返回行数都会变（变少、变准）。若存在历史快照/对外披露的回测数字，需注明口径变更。另：insight 臂 narrative 恒为 `NULL`（`schema.sql:430` 第 9 列），expert 镜像的真实 narrative 现仅在 kg_events 层（已从视图 event 臂剔除）——expert 行的 causal narrative 经视图不再可见；当前 expert 镜像多不带 narrative（`expert.py:98` 未传 narrative），实际无损失，但属契约收紧，知悉即可。

### E.3 低危：性能 / 健壮性

- **E.3.1 `resolve_forward_claims` 为 N+1 查询 [低 / 夜间批]**（`kg/resolve_claims.py:69-81`）：claims 查询无 LIMIT，对每条未决 forward claim 各跑一次 realizer 查询。全宇宙（~947 家）累积下可达数千次 round-trip/夜。非热路径、夜间运行，当前可接受；如需优化可改为单条 `LATERAL`/窗口函数一次命中。
- **E.3.2 `run_daily(stages=...)` 无合法性校验 [低]**（`orchestration/daily.py:126`）：`do_pull, do_extract = "pull" in stages, "extract" in stages`——拼写错误（如 `stages=("puli",)`）会静默退化为"仅 seed/bootstrap"的空跑，无告警。建议校验未知 stage 名或至少 `log.warning`。
- **E.3.3 `graphrag` 过滤用 COALESCE、排序用裸 as_of [低 / cosmetic]**（`retrieval/graphrag.py:104-109`）：WHERE 用 `COALESCE(as_of, observed_at::date)` 做 PIT，但 `ORDER BY as_of DESC NULLS LAST` 仍按裸 `as_of`——过滤口径与排序口径不一致。仅影响无 `as_of` 行的相对顺序，非正确性问题。
- **E.3.4 `store.add_fundamental_from_extraction` FK 守卫为逐条查询 [低]**（`kg/store.py:140`）：每次抽取指标都 `SELECT 1 FROM companies`。正确防御 FK 违例，但属 per-metric 查询；自用规模无碍。
- **E.3.5 `realizes_event_id` / `resolved_at` 无索引 [低 / 当前无消费者]**（`schema.sql:406-408`）：`idx_events_resolution` 覆盖了 `(time_orientation, resolution)` 查询；若日后要反查"某 realizer 关闭了哪些 claim"会需 `realizes_event_id` 索引，当前无此查询，可不加。

### E.4 设计点（记录，非缺陷）

- **E.4.1 realizer 不按 `license_tag` 过滤 [设计]**（`kg/resolve_claims.py:71-81`）：realizer 仅按 `event_type ∈ _REALIZER_TYPES` + backward + 极性 + 时间窗匹配，不区分来源。故 `signals.py` 镜像的 signal 事件（`license_tag='signal'`，`:58/78/106`）或 social-extracted 事件亦可作 realizer。`SIGNAL_TO_CATALYST` 映射出的 guidance/earnings 类 signal 事件可能在语义上并非"真实兑现"，构成轻度噪声；grace/window/极性约束已大幅限噪，属可接受的设计取舍。
- **E.4.2 docker-compose Dagster 端口 3000→3001(host) [配置变更]**（`docker-compose.yml:40,61`）：合理（避 Grafana 占用），注释已同步；若 README/healthcheck/外部监控仍引用 3000 需顺带更新。

### E.5 仓库卫生

- **E.5.1 `src/xar/ingestion/universe.py`（577 行生成产物）状态不一致 [低]**：该文件为 `scripts/universe_build.py` 生成、被 `registry.py`（commit 396e861）`COMPANIES += UNIVERSE` 加载，但当前**既未提交也未 gitignore**（`git check-ignore` = NOT-IGNORED，`git status` = 未跟踪）。后果：本地能跑 947 家，但**新鲜 clone 缺该文件**（registry 的 `except ImportError` 优雅退回策展核心，无崩溃，但静默少一半公司且无日志）。建议二选一：作为生成事实源**提交**，或纳入 `.gitignore` 并在 CI/文档明确"需先跑 universe_build"。注意 `.gitignore` 已忽略 `.universe_cache/` 但未涵盖该输出文件本身。
- **E.5.2 `scripts/universe_build.py` 同为未跟踪**：生成器工具应随 universe.py 一并提交（否则无法复现生成）。`SEMANTIC_DB_PLAN.md` / `ultraplan.md` 为规划文档，按需提交或忽略。

### E.6 经复核认可的项（成立）

- 前向预期闭环是"semantic DB 唯一不可从 fundamentals/estimates/prices 派生的真实缺口"的正确填补；写仅在 `forward_looking` 行、backward 硬事实永不触犯，事件日志在"该保持 append-only 处"保持 append-only——与双时态承诺一致。
- 回测 `entry = GREATEST(as_of, observed_at)` + recency gate 是原审 §3.2-B2（前视）的干净闭合，且不丢非美/无 as_of 事件。
- daily 拆分把"分片安全"的 pull 与"必须全局一次"的 extract 正交化，消除旧实现"N× LLM 预算 + 同文档竞态"的真实风险；注释与实现一致。
- provider 失败日志去密是针对原审 §4.1（凭证进日志）的具体、正确处置，且未损失可观测性（保留异常类型 + status）。
- 新增测试 `test_resolve_forward_claims` / `test_resolve_expert_forward_claim_visible_via_view` 覆盖了 hit/miss/stale 三态 + expert 镜像经视图可读的 P0 路径，质量高。

### E.7 处置建议（按优先级，均可独立交付、零新依赖）

1. **E.2.1 签字**：明确 narrative 不接地是否可接受；推荐保留接地为软标志位（不丢、仅标记），以守住信任层。
2. **E.5.1/E.5.2**：决定 `universe.py` / `universe_build.py` 的提交或忽略策略——这是当前最易"静默退化"的仓库状态风险。
3. **E.3.2**：`run_daily` 校验未知 stage 名（一行），防静默空跑。
4. 其余 E.3.x / E.4.x 为低危/记录项，按需处理，不阻塞。

> 第二意见结论：**本轮变更高质量、无正确性 bug、测试覆盖到位、方向契合"可信结果"主线**；唯一需在合入前明确的是 E.2.1（narrative 接地取舍）与 E.5（universe 文件提交状态）。E.2.2 的视图去重是正向修复但属下游可观测的数值口径变更，建议在产物中注明。

---

## 附录 F：语义数据库 + 每日自动化建设、`SEMANTIC_DB_PLAN.md`（GLM-5.2）方案评估、xhigh `/code-review` 处置（2026-06）

> 本附录记录本会话（分支 `feat/semantic-db-daily-ingest`）的三件事：(1) 语义数据库 + 每日自动 ingest 的实际建设范围；(2) 对 GLM-5.2 起草的 `SEMANTIC_DB_PLAN.md` 的方案评估与取舍（拒绝平行表、采纳前向闭环 + PIT 回测）；(3) xhigh 档 `/code-review` 各 finding 的处置裁定。落地状态：36 个 pytest 通过、`ruff check` 通过、docker 双服务（app `:8000` + Dagster `:3001`）已部署。

### F.1 本会话建设范围（经源码核对）

- **三个消费周期主题**（在原 5 个 AI 产业链主题之上 → 共 **8 主题**）：`internet` / `retail` / `restaurants`。它们不走产业链 upstream→downstream tier 轴，改用**经济周期轴**——新 ontology 维度 `src/xar/ontology/cycle.py`，含 5 态 `CyclePosition`（`early_cycle`/`mid_cycle`/`late_cycle`/`defensive`/`counter_cyclical`）与单调 `CYCLE_RANK`（兼作 segment tier，使热力图渲染为 "Cycle Map"）。`registry.py:23-31` 的 `THEMES` 新增 `kind` 判别（`"chain"` vs `"cycle"`）；前端 `ChainHeatmap` 对 cycle 主题改标 Cycle Map。8 主题：`ai_optical`/`ai_chip`/`ai_software`/`space_exploration`/`humanoid_robotics`/`internet`/`retail`/`restaurants`（详见附录 B/C/D）。
- **个股域扩展至 947 家**（自约 378 名策展核心起）：`scripts/universe_build.py` 生成 `src/xar/ingestion/universe.py`（`COMPANIES += UNIVERSE`）；覆盖 US + JP/KR/TW（+ 部分 CN）。生成流水线、闸门与数据可靠性修复见附录 C / D（含 D.8 的 name↔ticker 校验、`dup_names` 去重、$2B 硬闸）。
- **语义数据库**（本会话主线）：带时间戳、可回测、anchored 到 Ontology 的语义层，承载结构化数值表（fundamentals/estimates/prices）所不覆盖的催化剂叙事、立场、因果、前瞻预期。**设计决策**：加性复用既有三张双时态表，而非新建平行表（见 F.2）——`kg_events`（追加列 `theme`/`segment`/`narrative`/`time_orientation`）+ `kg_edges`（`causally_linked` EdgeType，`ontology/edges.py:41`）+ `expert_insights`（追加 `as_of`/`theme`/`segment`/`time_orientation`），由单一 SQL 视图 `semantic_facts`（`schema.sql:419-437`）统一（event 臂 `license_tag IS DISTINCT FROM 'expert'`、insight 臂经 `kg_event_id` LEFT JOIN 回 `kg_events` 透出 `resolution`）。抽取（`kg/extract.py`）填 `time_orientation`、接地的 `narrative`（因果/前瞻"为何/将驱动什么"）与 drivers（因果实体 → `causally_linked` 边 + `attrs.drivers`）。检索 `graphrag.semantic()`（`retrieval/graphrag.py:88`）点查该视图；`agents/nodes.py` 把语义流注入分析师 brief（`_graph_brief`）。
- **前向预期解决生命周期**（本会话唯一净新增能力，评估 `SEMANTIC_DB_PLAN.md` 后采纳）：`kg_events` 增 `resolution`/`resolved_at`/`realizes_event_id`（`schema.sql:406-408`）。`src/xar/kg/resolve_claims.py` 的 `resolve_forward_claims()` 闭合"预期→兑现"环——一条有向 `forward_looking` 催化剂在窗口内出现同公司 realization 型 backward 事件（earnings/order/product_ramp…）即解析为 hit/miss（按 `COALESCE(event_date, observed_at)` 定时），否则 stale（可再检）。仅改写 forward 行；经 `semantic_facts.resolution` 透出；CLI `xar resolve-claims`。
- **Finnhub/FMP 新闻 ingestion**（补上真实来源缺口）：`providers/finnhub.pull_news`（+ `pull_general_news`）与 `providers/fmp.pull_news` 把公司新闻落进 `documents`（`source='finnhub'`/`'fmp'`，`permission='grey'`，content-hash 去重）。`api/ops.py` 注册 `finnhub_news` 源（`ops.py:146` + `run_source` 分支 `:259`）；`kg/expert.ALT_SOURCES`（`expert.py:29`）加入 `finnhub`/`fmp`，使新闻同时流入 `build_kg` 与 expert 层。
- **每日自动 ingest**：`src/xar/orchestration/daily.py` 的 `run_daily(stages=('pull','extract'))`——按来源增量 PULL（按公司分片、隔离失败）→ parse/embed → `build_kg` → expert → signals → `resolve_forward_claims`（extract 全局只跑一次，非每片；LLM 阶段限预算但廉价 DB 阶段照跑）。`src/xar/storage/runlog.py` + 新表 `ingest_runs`（`schema.sql:443`）= 运行日志 + 每源增量游标（`last_success_ts`）。CLI `xar daily`（`cli.py:147`），content-hash + NOT-EXISTS 游标使其幂等/可续。
- **Dagster sidecar**（每日运行时，已部署）：`src/xar/orchestration/definitions.py` —— `pull_shard`（8 静态分区，06:00 调度）+ `extract_all`（单 run，06:30，单批预算）+ `core_daily`（按需）。`docker-compose.yml` 新增 dagster 服务，host 端口 `:3001`（容器内仍 3000）、`dagster_home` 卷；仅 app 容器跑 `xar init`（schema owner）。
- **回测扩展**：`backtest/catalyst_returns.py` 改驱动于 `semantic_facts`（不止 `kg_events`），按 `(category, polarity, kind, time_orientation)` 分桶，回答"前瞻/情绪层是否预测收益"。严格 PIT 入场 = `GREATEST(as_of, observed_at)`（无前视），本地 `prices` 表优先（yfinance 仅兜底）。
- **新增 schema 对象**（均加性/幂等，`storage/schema.sql`）：上述追加列；`semantic_facts` 视图；`ingest_runs` 表；`init_schema()` 可重复跑。

### F.2 `SEMANTIC_DB_PLAN.md`（GLM-5.2）方案评估

GLM-5.2 起草的 `SEMANTIC_DB_PLAN.md` 提出**新建独立平行表 `semantic_claims`**（`SEMANTIC_DB_PLAN.md:13,66-119`），经 `realizes_event_id` + 一个 `signal_events` 视图与 `kg_events` 桥接，并以 `UNION ALL` 视图供回测/检索/agents 统一入口。

裁定：

- **拒绝平行 `semantic_claims` 表（判为技术债）**。理由：(1) 它在 `kg_events` 之外再立一套催化剂/事件实体，回测、检索、agent、双时态语义被迫维护两条几乎同构的路径，正是"双表割裂"的来源；(2) 既有 `kg_events`/`kg_edges`/`expert_insights` 已是双时态、已 anchored 到 Ontology，缺的只是几个语义列与一个统一视图——加性扩列 + 一个 `semantic_facts` 视图即可达成同样的"单一入口"目标，且不复制写路径。**采纳的落地**：F.1 所述的加性三表复用 + `semantic_facts` 视图。
- **采纳 `Resolution` 前向闭环**（计划 §1.2 提出 `pending|hit|miss|withdrawn|stale` 生命周期）。这是计划中唯一**无法从 fundamentals/estimates/prices 派生**的真实净新增能力，落地为 `resolve_claims.py` 的 hit/miss/stale 三态（去掉了 `pending`/`withdrawn`——以 `resolution IS NULL` 表达未决，stale 为非终态可再检），仅写 `forward_looking` 行、backward 硬事实永不触犯。
- **采纳 PIT 回测口径**（计划要求观测点入场）。落地为 `backtest/catalyst_returns.py` 的 `entry = GREATEST(as_of, observed_at)`，闭合原审 §3.2-B2 前视偏差。
- 计划 §0 要求落地时一并修掉本审核附录 A 的若干既有缺陷——已在前序轮次处理（见附录 A.5）。

### F.3 xhigh `/code-review` 处置

对本会话工作树变更跑 xhigh 档 `/code-review`，逐条裁定：

| # | finding | 裁定 | 处置 |
|---|---|---|---|
| P0 | `semantic_facts` 视图 expert 臂可见性 bug——前向 expert claim 的 `resolution` 经视图不可见 | **成立·已修** | insight 臂 `LEFT JOIN kg_events e2 ON e2.id = x.kg_event_id` 透出镜像 event 的 `resolution`；新增 `test_resolve_expert_forward_claim_visible_via_view` 钉死该契约（`schema.sql:419-437`） |
| — | realizer 日期口径——hit/miss 定时应按 `COALESCE(event_date, observed_at)` 两侧一致 | **成立·已修** | `resolve_claims.py:52` 两侧均用 `COALESCE(event_date, observed_at)` |
| — | realizer 相关性——不应让无关 litigation/short-report/管理层变动 充当兑现 | **成立·已修** | realizer 限 `event_type ∈ _REALIZER_TYPES`（earnings/order/product_ramp…）的 backward 硬结果（`resolve_claims.py:38-42`） |
| — | neutral 极性参与 hit/miss——非有向，不应判定 | **成立·已修** | `_SIGN` 仅含 positive/negative，neutral 被排除（`resolve_claims.py:43`） |
| — | 回测前视（事件日入场） | **成立·已修** | PIT 入场 `GREATEST(as_of, observed_at)`（见 F.2） |
| — | 每日 extract 预算——分片各跑一次 LLM 抽取 = N× 预算 + 同文档竞态 | **成立·已修** | extract 全局只跑一次（`extract_all` 单 run 单批预算）；signals 迭代全宇宙 `all_ids` 而非分片（`daily.py`、`definitions.py`） |
| #9 | 调度偏移——`pull_shard` 06:00 与 `extract_all` 06:30 仅隔 30min，慢 pull 可能未落地 | **接受（带理由）** | extract 阶段本就只抽"已有文档但无抽取"的 doc（NOT-EXISTS 游标），迟到的 pull 在次日窗口被抽；偏移仅为吞吐优化、非正确性约束。自用单租户姿态下 30min 足够，不引入跨作业 sensor 依赖 |
| #12 | realizer 非排他——一条 realizer 可关闭多条 forward claim | **接受（带理由）** | 同公司多条前瞻主张被同一兑现事件关闭，在语义上正确（一次 earnings 可同时兑现多条对该季的预期）；grace/window/极性约束已限噪。强行一对一会丢失合法的多对一兑现关系，故不加排他约束 |

其余 finding 与 §E（前向闭环 + PIT + 编排拆分的第二意见复核）重叠，处置见附录 E.7。

> 本附录结论：语义数据库以**加性复用**而非平行表落地，避免了 GLM-5.2 计划中 `semantic_claims` 的双表割裂技术债；前向预期闭环 + PIT 回测为采纳的净新增能力。xhigh `/code-review` 的 P0 视图可见性、realizer 日期/相关性/neutral、回测 PIT、每日预算均已修复并加测试；#9 调度偏移与 #12 realizer 非排他经裁定为自用姿态下可接受的设计取舍。验证：`pytest` 36 passed、`ruff` clean、docker 双服务（app `:8000` / Dagster `:3001`）已部署。

---

## 附录 G：LLM 任务管理器 + 本体深度回填（扩展维度）+ 独立双审 / xhigh `/code-review` 处置（2026-06）

> **2026-07-19 追记**：LLM 路由已再演进（+ollama 本地厂商，glmworker 抽取本地优先 `qwen3-14b-local`，云 GLM 回落）——见 `DESIGN.md §6.1`；本附录为历史快照。

> 本附录记录分支 `feat/semantic-db-daily-ingest` 的两组变更：(1) 用 **LLM 任务管理器**（registry + router + fallback + billing-aware 预算 + 运行时 `route_overrides`）替换原审 §1.6 / §5.2 所述的"两级 fast/strong 路由"；(2) 把 569 家 universe 公司**回填到策展核心深度**（多主题 / 技术路线 / 别名 / 段位精修），并新增 8 条数据驱动的扩展技术路线（25→33）。落地状态：`pytest` 43 passed、`ruff check` 通过、docker 双服务（app `:8000` / Dagster `:3001`）运行中。**本附录修正原审 §1.6 / §5.2 对"两级路由"的描述——该机制已被任务路由取代。**

### G.1 LLM 任务管理器（取代两级路由）

原审 §1.6 / §5.2 描述的"`models/llm.py` 两级路由（fast=Haiku / strong=Opus）+ 单 run USD 上限"已重构为**任务路由 + 可更新模型库 + billing-aware 预算 + 全链 fallback**（扩展 `models/llm.py`，无平行系统、无新增重依赖——LiteLLM 已能直连 `zhipu/`/`moonshot/`）：

- **代码即真相的模型库** `src/xar/models/registry.py`：`Provider` + `ModelSpec` dataclass；枚举 `Billing(token|subscription)` / `Capability(fast|strong|reasoning|long_context|cheap_bulk)` / `Status(active|preview|deprecated)`。`PROVIDERS` 含 `deepseek`/`anthropic`/`openai`/`zhipu`(=GLM)/`moonshot`(=Kimi)；`MODELS` 含 token 模型（DeepSeek v4-flash/pro、Claude opus/haiku/sonnet）+ GLM/Kimi 的 **SUBSCRIPTION** 条目（`glm-4.6-sub`/`kimi-k2-sub`，`price_in/out` 为兜底计价、flat-plan 命中记 0）。`candidates_for(capability, billing_pref=...)`（`:202`）按 **billing-first 稳定排序**（preferred-billing 优先但保留 token fallback 尾部，非丢弃），叠加 `preferred` 标志 + `price_in` + id；另有 `preferred`/`by_litellm`/`provider_of`。**换代 = 改这一个文件**（加 `ModelSpec`、置 `preferred=True`、旧的翻 `deprecated`）；`_PRICES` 由 `MODELS` 派生。
- **任务路由器** `src/xar/models/router.py`：`TaskClass` 枚举（11 类：`kg_extract`/`expert`/`search_bulk`/`analyst`/`debate`/`editor`/`judge`/`synth`/`eval`/`adhoc_fast`/`adhoc_strong`）+ `RoutePolicy` + `POLICIES`；`resolve(task)`（`:111`）→ 有序候选链。**批量 / 搜索类**（`kg_extract`/`expert`/`search_bulk`）→ `CHEAP_BULK` + **SUBSCRIPTION-first**（GLM/Kimi flat-rate，使 947 公司语料的夜间抽取永不撞无界 token 账单），其后才是预算内的廉价 DeepSeek token；**质量类**（`debate`/`editor`/`synth`）→ `STRONG` token + 跨 provider fallback。解析优先级：`route_overrides` 表（ops API）> env（`XAR_MODEL_*`）> registry `preferred`。`tier="fast|strong"` 经 `as_task`（`:66`）保留为向后兼容别名——未迁移调用点不变。
- **`models/llm.py` 重构**：`complete()`/`complete_json()` 新增 `task=`；一个**fallback 执行器**（按候选经 `_endpoint`（`:147`）取 api_base/key；跳过未配置 provider；按 **EFFECTIVE billing** 跳过超预算的 token 候选 + 硬停 `BudgetExceeded`；transient 错误经 `_retryable`（`:132`）做一次候选内重试；失败/空响应轮转到下一候选）。**billing-aware 计价**（`_record`，`:112`）：真正的 flat-plan 调用记 `usd=0`（订阅批量永不触发预算上限）；而一个**回落到 provider 计费 key 的订阅 spec 记其真实 per-token 成本**（billing 漏洞闭合，见 G.3 P0）。`llm_usage` 新增 `provider`/`task_class`/`billing` 列。
- **配套**：`config.py` 加 `glm_api_key`/`moonshot_api_key` + sub key/base 字段；`schema.sql` 加性补 `llm_usage` 三列 + `route_overrides` 表（运行时换模型）；`api/ops.py` 的 `/api/ops/llm`（`ops.py:304`）surface registry vendors/models/routing-table + 按 billing/provider/task 的花费（旧行标 `legacy`，无 null bucket）+ `set_route()`；`api/app.py` 新增 `POST /api/ops/llm/route`（`app.py:375`，运行时换代无需重部署）。`kg/extract.py` + `kg/expert.py` 已从 `tier="fast"` 迁移到 `task="kg_extract"`/`"expert"`；批量拉取路径（`orchestration/daily.py`）自动经 `task=` 路由。

> 对原审 §5.2 "值得保留：两级 LLM 路由 + per-run USD 上限"的更新：该实践**已演进**为任务路由 + billing-aware 预算 + 全候选链 fallback，且闭合了原审 §1.6 指出的"批量路径无预算"（订阅 flat-rate 天然封顶 + token 候选按 effective billing 受预算约束）与"价格表虚假"（subscription 命中不计价、计费回落记真实成本）两项。

### G.2 本体深度回填到策展核心 + 8 条扩展技术路线

基础本体（sector/industry/segment/chain_role）原已对 947 家 100% 完整；本轮为 569 家 bulk-generated universe 公司**补深度**：多主题成员、技术路线暴露、更丰富别名、段位精修。

- **`scripts/ontology_enrich.py`**：whitelist 校验的批量 LLM 富集，经任务管理器路由（`task="search_bulk"`，`:188`；GLM 订阅 + DeepSeek 兜底，528 公司成本约 \$0.43）。每公司新增（全部严格校验 against 本体词表，越界即丢）：额外主题成员（+ 该主题下的 segment）、技术路线标签、额外别名（原生 / 罗马音 / 简称 / 品牌）、更优 primary segment；free-text `suggest_route` 字段 surface 扩展候选。确定性 `_CORRECTIONS` 表（`:62`）编码 18 项审计确认的修正；`generate()`（`:229`）合并 cache + corrections → 以 Python repr 重写 `src/xar/ingestion/universe.py`。
- **`registry.py` `TECH_ROUTES`：25 → 33**——新增 8 条数据驱动的**扩展路线**（来自反复出现的 `suggest_route`）：`tr_cybersec`、`tr_ddic`（display-driver IC）、`tr_power_semi`、`tr_cv`（computer vision）、`tr_med_imaging`、`tr_pneumatic`、`tr_industrial_gas`、`tr_ceramic_pkg`——覆盖原 optical / chip-centric 之外的专门化。
- **`kg/store.py` `bootstrap_seed`**：富集后的 `tech_routes` 成为 `uses_techroute` 边（`license_tag='enriched'`）；`competes_in`(seed) + `uses_techroute`(enriched) 现按 roster **delete-then-recreate**（`store.py:185-186`），使修正在 reseed 时干净传播——一个幂等性修复（curated `SEED_EDGES` 与抽取边因 rel_type/license_tag 不同而不受影响）。
- **结果（live DB，全 947）**：多主题公司 80、技术路线节点 33、`uses_techroute` 边 724（其中 enriched 360）、`competes_in` 1024、entity_aliases 3623。

### G.3 独立双审 + xhigh `/code-review` 处置

- **独立双审（36 agents）+ xhigh `/code-review`：裁定 GO**。全链完整（0 词表违规、5 项完整性不变量通过）；common-sense 质量约 **3% 错误率**（LLM 把 supplier 误当 route、多主题过度归属），均 P1–P3，经确定性 `_CORRECTIONS` 修复。**勾稽核查**（`universe.py` ↔ DB ↔ vocab）对齐 clean：0 条跨主题 segment、0 条 out-of-vocab route、corrections 双向反映。
- **xhigh `/code-review` 各 finding 处置**：

| # | finding | 裁定 | 处置 |
|---|---|---|---|
| P0 | 订阅计价漏洞——SUBSCRIPTION spec 在无 sub key 时回落到 provider 计费 key，却仍记 `usd=0` → 该笔 token 花费对预算上限不可见 | **成立·已修** | `_record`（`llm.py:112-126`）按 **effective billing** 计价：`used_sub` 决定 token/subscription；真 flat-plan 记 0，回落到计费 key 的订阅按其**真实 per-token list price** 记账 → 预算可见、billing 漏洞闭合 |
| P1 | retry-gating——不应对 auth/bad-request 等确定性错误重试 | **成立·已修** | `_retryable`（`:132`）只对 transient 错误做一次候选内重试；确定性错误直接轮转下一候选 |
| P1 | ops null-bucket——`llm_usage` 旧行 provider/task_class/billing 为 NULL 会形成幻影空桶 | **成立·已修** | `ops.py` 三处聚合 `COALESCE(..., 'legacy')`（`:338/341/344`）→ 历史花费标 `legacy`、保持可见可归属，无 null bucket |
| P2 | retryable-set——可重试错误集合需收紧 | **成立·已修** | `_retryable` 限定 transient 集合 |
| P2 | test-hygiene（2 项） | **成立·已修** | 测试卫生修复 2 处 |

> 本附录结论：两级路由已由**任务管理器**取代（registry + router + fallback + billing-aware 预算 + 运行时 `route_overrides`），订阅 vs token 的计价决策使 947 公司语料的夜间批量在订阅 flat-rate 下天然封顶、且回落计费时真实记账（P0 漏洞闭合）；本体回填把 569 家 universe 公司补到策展核心深度并新增 8 条扩展技术路线（25→33）；独立双审裁定 GO、约 3% common-sense 错误率经确定性 `_CORRECTIONS` 修复、勾稽核查对齐 clean。验证：`pytest` 43 passed、`ruff` clean、docker 双服务（app `:8000` / Dagster `:3001`）运行中。

---

## 附录 H：LLM 任务管理器 + 本体回填的后续独立复核（第一性原理·commit 59213a8，2026-06）

> 视角：基本面量化策略 + 后端架构；**第一性原理**而非"行业最佳实践"；从"可信结果 / 不埋技术债"的项目核心目标出发推理最佳实现。
> 复核对象：commit `59213a8`（"Docs: LLM task manager + ontology enrichment"）本身为**文档提交**；按本会话惯例复核其描述的实际代码——`src/xar/models/{registry,router,llm}.py`（任务管理器）+ `scripts/ontology_enrich.py`（本体回填）+ `kg/store.py`。
> 方法：逐文件读取源码 + 核验 git 状态；`pytest` 43 passed、`ruff check` 通过。
> 与附录 G 的关系：G 是本批变更的**建设记录 + 首轮 xhigh 审查处置**（P0 计价漏洞、retry-gating、null-bucket 等）；本附录是对**同一代码的后续独立复核**，捕获 G.3 未覆盖的 3 项低危 + 1 项可维护性。**本附录仅记录意见，不修改代码。**
> **结论先行**：**未发现正确性 bug**——code-as-truth registry、billing-aware 计价、metered-fallback 漏洞修复（`usd=0` 仅当 flat-plan 真命中）、`bootstrap_seed` 的 `license_tag` 作用域 delete-then-recreate 均成立。下面 3 项 Low 为健壮性/可观测性改进点，1 项为应控制其增长的技术债。均不阻塞，但建议在下次触及相应文件时顺手处理。

### H.1 低危（健壮性 / 可观测性）

**H.1.1 `_overrides()` 在瞬时 DB 错误时把空结果缓存满 TTL [低 / cache-failure 反模式]**

- 位置：`src/xar/models/registry.py:172-183`（核验：`:180-181` `except Exception: _OVERRIDES = {}`，`:182` `_OVERRIDES_AT = now`）。
- 现状：TTL 到期时若恰好 Postgres 抖动，`_OVERRIDES={}` 被缓存满 `_OVERRIDES_TTL`（60s/进程）。
- 影响：运营者经 `route_overrides` 表做的运行时换模型在该进程**静默失效最多 60s**。爆炸半径有限——批量回落到 registry 默认值（仍 subscription-first），不会触发计价失控；但属典型"cache failure"反模式。
- 建议：异常路径**返回先前缓存的 `_OVERRIDES` 且不推进 `_OVERRIDES_AT`**（serve-stale-until-recover，尽快重试），而非缓存空值。

**H.1.2 `as_task` 对未知 task 字符串静默降级 [低 / 潜伏]**

- 位置：`src/xar/models/router.py:66-75`（核验：`:73` `except ValueError: pass`，`:75` `return ADHOC_STRONG if tier=="strong" else ADHOC_FAST`）。
- 现状：拼写错误或 `TaskClass` 重命名后的 stale 值（如 `"kg_extact"`）静默落到 `ADHOC_FAST`（一个 **token** 计费模型），而非报错。
- 影响：对批量任务，这会**静默绕过 subscription-first 的计费保护**——而这正是任务管理器的核心目的。当前所有调用点正确，故潜伏；但未来回归不可见。
- 建议：`ValueError` 路径加 `log.warning("unknown task %r, falling back to adhoc", task)`，把未来回归变 loud。

**H.1.3 跨 provider fallback 在部分 outage 下可把单次调用成本抬升约 10× [低 / 非失控]**

- 位置：`src/xar/models/llm.py:202-237`（核验：候选链 `:202`、按 effective billing 跳过超预算 token 候选 `:210`、失败轮转 `:218-227`）。
- 现状：一个 quality 任务的候选链 `[deepseek-v4-pro $0.60/$2.40 → sonnet $3/$15 → opus $5/$25 → …]`，在部分 provider outage 下单次 debate/editor 调用会轮转到 Opus。
- 影响：**非失控**——总花费仍受预算上限约束（token spend 计入、`spent >= cap` 跳过后续 token 候选 `:210`）。但 outage 期间的一次 report 会比改动前的单模型行为**消耗预算明显更快**。"fallback" 读起来像免费保险，实际不是。
- 建议：在 ops 控制台 / 文档显式说明 fallback 的成本语义（已在附录 G.1 描述 billing-aware 预算，可补一句"outage 期间预算消耗加速"）。

### H.2 可维护性（应控制其增长的技术债）

**H.2.1 `_CORRECTIONS` 是架在非确定性 LLM 输出之上的手维护补丁表 [技术债 / 半衰期]**

- 位置：`scripts/ontology_enrich.py:62-108`（核验：`:62` `_CORRECTIONS: dict[str, dict] = {`，18 项审计修正；`:85` `fix = _CORRECTIONS.get(c["id"])`）。
- 现状：18 项修正以 company id 为键的 dict 编码，对当前 18 个案例**实用且务实**。
- 技术债特征：重跑 `enrich` 会**再次产生同样的 ~3% LLM 错误**，故 `_CORRECTIONS` 必须**永远携带、并与 prompt/词表改动同步编辑**；失效条目（公司移除/重生成）静默 no-op 并累积。从第一性原理看，这些修正的**正确归宿是 whitelist 校验器（`_valid`）/ prompt 约束**，使错误无法被产生——例如 "supplier-vs-route 混淆" 一类（`u_us_lin`/`u_jp_7751`/`u_jp_4188`）看起来可由 `provider_of(route) != provider_of(company)` 式不变量在源头拦截。
- 建议：非阻塞；但在该表**增长前**把可规则化的类别上移到 `_valid` 不变量/prompt 约束。否则每次 enrich 迭代都会再添几行。

### H.3 经复核认可的项（成立）

- **metered-fallback 计价修复正确**：`used_sub` 由 key *presence* 派生，`usd=0` 仅在 flat-plan 真命中时记录（`llm.py:119-120` 核验）——附录 G.3 的 P0 漏洞确已闭合。
- **`bootstrap_seed` 的 delete-then-recreate 作用域正确**：仅按 `license_tag` 删 enriched/seed 边（`store.py:179-191`），**不触碰** curated `SEED_EDGES` 与抽取边；reseed 幂等。
- **批量预算前缀路由一致**：`build_kg`→`kg-`、`expert`→`expert-`、`synthesize_all`→`synth-`、`daily`→`batch-`（`_BATCH_PREFIXES`）；`daily.py:161,173` 捕获 `BudgetExceeded`。
- **`generate()` 输出合法**：经 `repr()` 产出有效 Python、保留 unicode——`universe.py` 解析干净（与本会话多次 regenerate 一致）。

### H.4 处置建议（按优先级，均可独立交付、零新依赖）

1. **H.1.1**（cache-failure）：异常路径返回旧 `_OVERRIDES` 且不推进 `_OVERRIDES_AT`——标准 serve-stale 模式，一行级。
2. **H.1.2**（silent downgrade）：`as_task` 的 `ValueError` 路径加 `log.warning`，防未来回归。
3. **H.2.1**（`_CORRECTIONS` 技术债）：在该表增长前，把可规则化类别上移到 `_valid` 不变量 / prompt 约束。
4. **H.1.3**（fallback 成本语义）：ops 控制台/文档补一句说明，非代码改动。

> 第二意见结论：**本批代码（任务管理器 + 本体回填）高质量、无正确性 bug**，附录 G.3 的 P0/P1 处置经核验成立。本附录捕获的 3 项 Low（override cache-failure、as_task 静默降级、fallback 成本加速）与 1 项可维护性（`_CORRECTIONS` 补丁表）均不阻塞合入，建议按 H.4 顺序在下次触及相应文件时顺手闭合，避免技术债累积。

### H.5 独立复核与处置（本会话，逐条实证后修复成立项）

逐条对照源码独立复核，4 项**全部成立**，已修：

- **H.1.1（cache-failure，已修）** `registry.py:_overrides()` 异常路径改为 **serve-stale**：保留上次成功的 `_OVERRIDES`、**不推进** `_OVERRIDES_AT`（下次立即重试），不再把空值缓存满 60s TTL → 瞬时 DB 抖动不再让运行时换模型静默失效。
- **H.1.2（silent downgrade，已修）** `router.py:as_task()` 在未知/拼错 task 字符串落到 adhoc 前加 `log.warning(...)`，把"静默绕过 subscription-first 计价保护"的未来回归变 loud。
- **H.1.3（fallback 成本语义，已修·注释级）** `llm.py` fallback 循环上方补注释，显式说明 outage 期间轮转到更贵模型会**加速预算消耗**（仍受预算上限约束、非失控）——把成本语义留在维护者最先看到的地方。
- **H.2.1（`_CORRECTIONS` 技术债，已按第一性原理上移到不变量）** 采纳 GLM 的核心建议——把**可规则化的"跨域路线误标"类别**从事后补丁上移到**源头 `_valid` 不变量**：在 `registry.py` 新增 **code-as-truth 的 `ROUTE_THEMES`**（每条 tech-route 声明其 home theme，新增路线被强制声明），`ontology_enrich._valid` 据此对**主题零重叠**的路线 tag 在富集时即拒绝（如"芯片公司被标空间推进路线"）。该不变量使**重跑 enrich 无法再生成此错误类**，降低对 `_CORRECTIONS` 的未来依赖。
  - 用当前（已修正）`universe.py` 对该 map 做了**反向校验**：初版 map 过紧，误伤 Nextchip(`tr_cv`)——一家做视觉 SoC 的 ai_chip 公司，遂将 `tr_cv` 的 home theme 补 `ai_chip`（视觉芯片确属 chip 域）。校验体现了"宁可放行、只拦零重叠"的保守设计。
  - **不做追溯性清洗**：gate 仅作用于**未来富集**（`_valid`），不在 `generate()` 回溯改动已审计数据——因残留 2 例（Ushio `tr_euv`、Dell `tr_genai_infra`）属**主题集不完整**而非路线错误（Ushio 确为 EUV 光源供应商），追溯丢弃会误删真实信号；正确归宿是补主题而非删路线，留作后续观察。
  - 既有 18 项 `_CORRECTIONS` 含异**域内事实性错误**（如 Canon=纳米压印≠EUV、多主题越界、别名串号），不可规则化，合理保留。

验证：`ruff` 通过、`pytest` **43 passed**、route↔theme map 反向校验仅余 2 例可解释残留。


---

## 附录 I：复评后修复轮（2026-07-20，第三方逐条验证驱动）

> 起因：对本文档与 ARCHITECTURE_REVIEW.md 的 46 条承重结论做了逐条对照代码的独立验证
> （24+22 条，0 WRONG / 0 OVERSTATED / 0 幻觉引用；方法与全量裁定见会话记录）。
> 验证确认 8 条属实但被搁置的发现仍可复现，按自用姿态择 4 项修复：

| 条目 | 处置 | 证据 |
|---|---|---|
| §2.1 凭证泄露（key 仍在 .env.example） | **止血**：换回 stub。实测泄露 key 已失效（DeepSeek API 401），吊销紧迫性解除；git 历史清洗（filter-repo + 远端 force-push）降为低优先卫生项，**留用户裁量** | .env.example |
| §3.1 局部：approve 不校验 run 状态 | **修复**：仅 awaiting_approval 可批准；published 幂等返回既有产物；running/failed 拒绝——人审闸不可被半成品绕过 | agents/graph.py::approve |
| §3.7 邻项：非预算异常把 run 卡死 running | **修复**：run_report 增广义 except → 落 failed + 错误入返回体/日志 | agents/graph.py::run_report |
| ARCH P1-5：可选 API token + ruff 硬执行 | **落地**：XAR_API_TOKEN 中间件（默认关=零行为变化，开启则变更类 /api/* 须带 X-API-Token/Bearer，hmac 常时比较）；ci.yml 去 `|| true`（ruff 现即全绿） | api/app.py::_api_token_gate、config.py、.github/workflows/ci.yml |

维持 §8.2 姿态裁决不动：SecretStr/依赖 pin/Docker 加固/mypy·覆盖率门禁/DAG 改造等仍不采纳。
专项测试 tests/test_review_fixes.py 3 项（approve 状态机 / 异常落 failed / token 闸六分支）。

---

## 附录 J：MODELS-P1~P7 / FETCHY-P3/P4 提交审核（2026-07-20）

> 评审范围：当日 7 个提交（模型目录 + 动态路由 + HITL 门控 + fetch 链路），全量读改动文件上下文，实跑 `pytest` 与前端 `tsc`。
> 总体：HITL 门控、动态路由、模型目录方向设计干净；但有 3 个确认 bug（2 个已致测试失败）与 1 个自相矛盾的设计问题。

### J.1 Bug

**J.1.1 提交了 failing 测试：`kimi-k2-sub` 被删但引用未清 [高]**

MODELS-P6 删除 `kimi-k2-sub` spec，引用未同步清理。实跑 `pytest tests/test_glm_worker.py`：2 failed / 20 passed：
- `test_pinned_restricts_chain_to_registry_specs`（`tests/test_glm_worker.py:16`）— `registry.get("kimi-k2-sub")` 返回 `None` → AttributeError。
- `test_glm52_leads_subscription_chains`（`tests/test_glm_worker.py:28`）— `minimax-m3-sub`（price 0/0）在 CHEAP_BULK 链中排到 glm-4.6-sub 之前，断言链序已变。
- `tests/test_pipeline.py:483-484` 同样引用 `kimi-k2-sub`，`set_route` 因 `registry.get() is None` 返回 `ok=False`（需 DB，集成测试必挂）。

**J.1.2 `_ensure_keys` 未同步新 key：host 环境 kimi/minimax 永远「未配置」 [中 / 环境相关]**

registry 将 moonshot 的 key_env 改为 `KIMI_API_KEY`、新增 `MINIMAX_API_KEY`/`MINIMAX_SUB_API_KEY`，但 `llm.py:78-87` 同步清单未跟进：MINIMAX 两个 key 完全缺失；`s.moonshot_api_key`（AliasChoices 已接受 `KIMI_API_KEY`）仍镜像到无人读取的 `MOONSHOT_API_KEY`。docker 部署靠 `env_file: .env` 注入不受影响；host 侧运行（CLI、测试、host worker）正是 `_ensure_keys` 存在的意义——此时 `model_usable` 报「未配置」、`complete()` 跳过候选。P7 使 `model_usable` 与 `_endpoint` 一致，但两者一致地读不到 host key。修复：同步清单补 `KIMI_API_KEY: s.moonshot_api_key`、`MINIMAX_API_KEY`、`MINIMAX_SUB_API_KEY`。

**J.1.3 `want_strong` 用静态策略计算，动态路由升/降层后思考力度错配 [中]**

`llm.py:267` `want_strong = router.POLICIES[tc].capability in (...)` 取**未调整的** capability。后果：bulk 任务升 STRONG 后，链首为思考模型却拿 `reasoning_effort="low"`（升层意义被抵消）；强任务降 FAST 后快模型反拿 `effort=high`（白烧延迟/token）。`route()` 内部已知调整后 cap，应随链返回而非在 `complete()` 重查静态策略。

### J.2 设计矛盾

**J.2.1 `minimax-m3-sub` 同时 `supports_reasoning=True` + `CHEAP_BULK` + price 0/0 [高]**

违反 `registry.py:221-222` 对 kimi-k3-sub 写下的纪律（思考模型不入 CHEAP_BULK：triage 小 token 预算会被思考耗尽 → 空 completion，qwen3.5 赛马实测同因）；亦触发 `registry.py:186-188` 记录的 price=0 模型「静默接住批量回退流量」越权风险。实测已成**所有** bulk 链（KG_EXTRACT/EXPERT/SEARCH_BULK/THESIS/WECHAT_TRIAGE/THESIS_LINK）第二候选（J.1.1 测试失败即此效应）。GLM-5.2 配额窗口耗尽时，全部批量/triage 流量静默轮转至 anthropic 兼容端点的思考模型，`reasoning_effort="low"` 能否被该端点翻译未验证（`drop_params=True` 静默丢弃）。处置：去掉 CHEAP_BULK/FAST 能力，或在 notes 写明 M3 豁免 K3 禁用理由（需实测支撑）。

### J.3 次要

- `set_wechat_review`（`ops.py:715`）对不存在 `gh_id` 返回 `ok:True`（UPDATE 0 行），UI 显示成功实无操作；建议查 rowcount 或先 SELECT。
- 动态升层后 `override_for(task.value, p.capability.value)` 用**调整后** capability：能力级 route override 在升级场景被静默绕过（task 级不受影响）；若有意应在 `route()` docstring 写明。
- kimi-k3/minimax-m3 只配主 key 时 `used_sub=False`，`llm_usage.billing` 记 "token" 实为订阅套餐（price 0/0 无预算影响，仅审计标签失真）。

### J.4 做得对的

- `schema.sql` `ALTER ... IF NOT EXISTS` 幂等向后兼容；HITL 门控「blocked 永不抓 / 严格模式只抓 approved」语义清晰且有两条针对性测试；动态路由 4 条测试覆盖升/降/无信号退化；`_record_discovered_account` 的 ON CONFLICT 不覆盖 `review_status`；前端 `tsc` 通过。

**优先级建议**：先修 J.1.1（测试红）与 J.2.1（批量流量静默落点），再 J.1.2/J.1.3，次要项随下一轮提交带过。

---

## 附录 K：Ontology / 数据 / LLM 调用链全流程审核（2026-07-20）

> 审核方式：走读核心路径（models/、kg/、ontology/、retrieval/、storage/、mining/、orchestration/、agents/、chathy/、backtest/、schema.sql），并在干净数据库上实测验证每个可疑点。

### K.1 P1 — 已验证缺陷

**K.1.1 HEAD 测试漂移红灯 [高]**

干净库实测：`tests/test_macro_bridge.py:87` 断言 anchors `count==10`，但 `src/slx/registry/theory_anchors.yml` 已是 12 条（A1–A8 + 4 条 META，由 2a42637 加入），测试与 `README.md:14` 的「10 条理论锚（A1–A8 + 2 META）」均未同步。这是本项目最该防住的一类回归——code-as-truth 词表改了，镜像断言没改。

**K.1.2 `kg_edges/kg_events.source_doc_id` FK 裸 RESTRICT → 数据室删除必然 500 [高]**

`schema.sql:91,113` 的 FK 默认 RESTRICT，`api/dataroom.py:102` 直接 `DELETE FROM documents`。任何走过 `build_kg` 的文档（产生了边/事件）都无法删除——包括数据室里已抽取的上传研报。这也是 `test_pipeline.py::test_end_to_end` 重复运行时 FKViolation 的根因（自清理 `DELETE FROM documents` 撞残留边）。建议：`ON DELETE SET NULL`（保事实、弃文档级溯源）或删除前显式清理派生行；两者取一，别留 RESTRICT 裸奔。

### K.2 P2 — 热路径性能/行为

**K.2.1 trigram 索引从未被用上（检索热路径全表扫）**

- `retrieval/vector.py:65-70`：词法臂 `ORDER BY similarity(c.text, %s) DESC` 没有 `c.text % query` 过滤条件，`idx_chunks_trgm`（`schema.sql:62`）是死索引；chunks 表增长后这是 Chathy 每次检索的最贵查询。
- `kg/resolve.py:60-64`：模糊消解对 `kg_nodes` 全表算 similarity 取 TOP1，`kg_nodes.name` 上连 trgm 索引都没有；抽取每篇文档每个未命中别名都付一次全表扫。
- 修复方向一致：加 `WHERE similarity(...) > 阈值` 或 `%` 操作符让 GIN 索引生效。

**K.2.2 Chathy 预算归属错误 [中]**

`chathy/agent.py:56` `run_id = f"chat:{session_id}"`：`llm._budget_cap` 按 run_id 累计，会话级 run_id 意味着 $5 上限在会话**整个生命周期**累计。长会话超限后所有 token 候选被永久跳过，静默降级到订阅池——与「per-run 上限」语义不符且用户无感知。应改为 per-turn run_id（如 `chat:{sid}:{turn}`）。

**K.2.3 抽取半成品不可恢复（事务粒度）**

`extract_from_document` 每条边/事件经 `db.execute` 各自提交；中途异常时 `build_kg`（`extract.py:255-261`）捕获后**仍盖 `kg_extracted_at` 戳**，该文档带着部分边永久退出队列——无事件、无重抽机会。第一性原理：「盖戳 = 处理完成」必须原子。建议抽取写入包进 `db.tx`，成功才盖戳；毒文档单独记失败原因而非同戳混排。

### K.3 P3 — 工程卫生

- **K.3.1** `bootstrap_seed` 每次 app 启动 + 每次 `build_kg` 全量重放：947 节点 upsert + 别名注册 + ~2000 条 `add_edge` 先 SELECT 后 INSERT，数千次独立往返（各自提交），且 competes_in/uses_techroute 先 DELETE 再逐条 INSERT（`store.py:183-191`），中间窗口图谱缺边。建议单事务 + `executemany`/批量。
- **K.3.2** 测试隔离脆弱：`test_evidence_link`、`test_earnings_outcomes`、`test_pipeline` 在复用库上因残留行失败（干净库全过）——fixture 应自清业务表。
- **K.3.3** 死代码：`extract.py:179` `company_node = d["company_id"]` 立即被 181 行覆盖。

### K.4 架构层评估（第一性原理）

**做对了的**（正确抽象，保持）：
- 有效计费记账（`llm.py:174-179`：订阅 spec 回落计量 key 时记真实成本）——成本可观测性的关键洞；
- 钉扎链无回退纪律 + `_sub_ready` 宁停不烧（`glm_worker.py:62`）——订阅制成本承诺的机制保证而非口头约定；
- evidence grounding（`extract.py:73`）+ 证据闸绑定发布（`graph.py:44`）+ 人审状态机——信任链完整；
- 双时态 + `GREATEST(as_of, observed_at)` 回测进场（`catalyst_returns.py:100`）——无前视，方法论诚实且限制明写；
- 词表/路由/本体全部 code-as-truth 且测试镜像不变式（除 K.1.1 漂移处）。

**结构性建议**：

1. **DB 层缺 unit-of-work 抽象**。`db.execute` 逐句 autocommit 是所有部分写入问题的根（K.2.3、K.3.1）。`db.tx` 已存在但写入热路径几乎不用。建议：多步写一律 tx 优先，execute 仅用于单句。
2. **向量索引选型**：IVFFlat `lists=100` 建一次永不维护（`db.py:56-65`），语料增长后召回率衰减无人察觉。pgvector 的 HNSW 无训练数据前置要求、召回更稳——当初「先攒 64 行再建 IVFFlat」的约束用 HNSW 直接消失，建议切换。
3. **schema.sql 无版本化**，靠 additive ALTER + DO 块数据迁移（`schema.sql:669-681`）每次启动重放。目前自愈合设计可接受，但 DO 块内做数据 DELETE 是味道；加一张 `schema_migrations` 表成本极低。
4. **embedding 换维度是运维地雷**：`{EMBED_DIM}` 只在建表时替换，已有库改 `XAR_EMBED_DIM` 后新向量插入维度不匹配。`xar reembed` 应有前置校验（读 `vector_dims(embedding)` 与 config 比对，不一致即拒绝并提示）。
5. `semantic_facts` 视图每次点查 UNION 双臂；量级再上一个数量级时考虑物化 + 增量刷新。现在不动，标记即可。

### K.5 优先级行动清单

K.1.1（一行断言 + README）、K.1.2（schema 两行）、K.2.2（一行）、K.2.1（两条 SQL + 一个索引）、K.2.3（tx 包裹）。前五项一天内可清，全部不引入新技术债。

---

## 附录 L：宏观架构审核（Mission-First，2026-07-20）

> 定位：与附录 K（微观缺陷）互补，只做**系统级架构与创新性**评审。评审范围：8 份计划/评审文档 + `src/xar` 全核（~29.5k LOC，163 文件，41 表）+ schema + 编排。
> 裁决尺度：(a) 架构性错误 (b) 方向对/形状错 (c) 对但未完成 (d) 该删的仪式。

### L.1 总体裁决

**这套架构只有一半在服务使命，但恰好是最重要的那一半。** 真正产生研究杠杆的主轴不是 README 宣扬的「双时态 KG + 多 Agent 报告 DAG」，而是文档后期长出来的另一条线：`semantic_facts` 统一事实流 → 类型化论点（Thesis/Debate/验证点）→ 零 LLM 健康度 → 挑战触发重建的**反身性论点循环**（thesis_signals.py:184-200、thesis_health.py:186-220、glm_worker.py:584-601）。这条线是真护城河。**而名义上的中心件——知识图谱——实际上是一个图形状的查找索引**：全库无一处多跳遍历（grep `RECURSIVE` 为零），`causally_linked` 因果边只写不读（extract.py:204 写入，无任何查询消费），`neighbors(as_of=)` 双时态读路径零调用方（graphrag.py:13-28，仅 supply_chain/landscape 两个内部调用且均不传 as_of）。被营销为「谁二供 EML」的图查询能力，实际是 1 跳查找 + prompt 散文。同时系统以「每周一个新 vendored 模块」的速度增生（Fenny/Andy/Exploration/资金流），自上轮评审 ~10.9k LOC 膨胀至 ~29.5k LOC，**新增面与 alpha 飞轮的耦合度递减**。架构上最值钱的资产（信任纪律 + 反身论点循环）被埋在三层冗余的管道商品之下。

### L.2 架构级发现

**L.2.1【(a) 高】KG 是图形状数据库，不是推理基底——「本体为中心件」名实不符**

`retrieval/graphrag.py` 全部 7 个函数均为 1 跳；全库 grep `WITH RECURSIVE` 为零；`causally_linked` 边在 extract.py:200-208 被认真写入但**没有任何代码读取**——没有传染分析、没有二阶效应。Agent 对图的全部消费是把供应商/客户/事件清单 dump 成文本 brief（nodes.py:50-66）。产业链 KG 的独有价值只有**结构推理**（多跳传染、二阶受益、替代传播）；从不做这些查询，`kg_nodes/kg_edges` 的实体消解、双时态 supersession、种子策展（store.py:151-193）就是**以图谱维护成本买一个可以用 `companies` + `events` 两张表实现的功能**。本体 schema 表达力够（17 类节点、21 类边、26 类事件）——问题在读侧缺失。要么补读侧（L.5 动作 2），要么承认 KG 是索引、`semantic_facts` 扶正为中心件，停止付图谱维护税。

**L.2.2【(b) 高】双时态是写侧纪律，读侧从未点查——昂贵的诚实，廉价的消费**

写侧完整（supersession store.py:67-68、corroboration boost :44-56、bitemporal 去重 :38-43）。读侧：`neighbors(as_of=)` 零调用方，全部读路径是 `invalidated_at IS NULL` 当前态。**例外且值得肯定**：tx-time 轴已兑现为可回测性——回测 `GREATEST(COALESCE(as_of, observed_at::date), ...)` 入场（catalyst_returns.py:98-107）、evidence_link 游标（evidence_link.py:76-86）、slx `knowledge_time<=as_of`。但有效时间轴（t_valid/supersession）是纯写侧仪式：系统从不回答「2025-03-01 那天供应链长什么样」。校准：保留写侧（防污染），停止宣传读侧能力，或补真实消费者（论点复盘「当时我们以为供应链是什么」）。

**L.2.3【(b) 高】报告 DAG 是线性 prompt 链，且已被 Thesis 子系统在功能上整体超越**

`agents/graph.py:24-52` 是 7 节点串行序列——无分支、无并行（analysts 串行循环 nodes.py:258）、无 resume。「多空辩论」是 2 轮 bull/bear 散文互驳（debate.py:21-38），**无裁判、无评分、不改变任何下游决策**。对比 Thesis 子系统（dossier → validate 硬校验 → 版本化 → 零 LLM 健康度 → 翻转触发重建），后者在结构化、可校验、可机器复核、可 reflexive 每个维度上严格优于前者，且报告管线已开始从 Thesis 借砖补喂（nodes.py:67-95）——等于承认原生管线是降级版。报告的正确形态是**论点对象的渲染**：报告 = 当前 Thesis + 争论天平 + 证据链 + 健康度 diff 的叙述化投影。现 DAG 烧 STRONG token（debate/editor 走最贵链 router.py:65-67）产出不可测量的散文。判 (b)：evidence_gate 保留，DAG 拓扑重建为 hypothesis-driven；同时消除文档三处背离（README:129 称 11 类 TaskClass 实为 17 类 router.py:31-48；蓝图称并行分析师实为串行；「可控 DAG」实为线性）。

**L.2.4【(c) 中高】Alpha 回测是断头路：测量存在，写回不存在**

`catalyst_returns` 的消费者只有 CLI（cli.py:350）和只读 API（api/app.py:319-321）；`resolve_claims` 的 hit/miss 只作为**文本**进入 dossier 供 LLM 阅读（thesis.py:128-131、earnings.py:270-274）。**没有任何确定性写回**：hit-rate 不调整 confidence、源可靠性、硬编码阈值（`_REVISION_PCT` signals.py:22-24、`_EVENT_Z=2.0`/`_CHALLENGE_SCORE=-0.5` thesis_signals.py:31-32、triage 融合权重 mining/triage.py 全部手工常量）。唯一真校准环是 earnings `score_outcomes → calibration`（DESIGN §5.14）——证明团队知道闭环长什么样，但只建了一个孤岛。量化飞轮的最小形态：信号 → 结算 → 按 (source × claim_type × polarity) 分桶的 empirical prior → 先验写回抽取置信度/信号阈值/论点权重。已完成前两步半，停在「把统计结果喂给 LLM 当阅读材料」——这是**最弱形式的反馈**：不可审计、不可复现、随 prompt 漂移。离闭环最近、缺口最小、杠杆最大。

**L.2.5【(b) 中】LLM 编排：路由是好工程，但 TaskClass 增生与三执行器是运维型复杂，非研究型复杂**

17 个 TaskClass（router.py:31-48，文档已漂）、3 种执行器（litellm / agent_sdk / codex_cli 子进程，llm.py:288-306）+ ollama 本地钉扎 + 动态升降层 + route_overrides + 额度探针。解决的问题真实（订阅额度内白嫖、账单封顶），计费记账与预算闸干净（llm.py:307-316）。但 glm_worker 的本质是**以「榨干 GLM 订阅额度」为目标的常驻守护**——优化成本函数而非研究质量函数；本地 qwen3-14b 抽取质量衰减无在线度量（赛马是一次性 scripts/bench_local_llm.py）。分工大体正确（z-score、验证点阈值、`_grounded`、validate_* 都是确定性闸），错位两处：① 报告分析师层用 LLM 产出本该是结构化查询结果的散文（L.2.3）；② KG 抽取的 drivers→causally_linked 无校验消费（L.2.1）。应给每个 TaskClass 配「为什么需要 LLM/为什么需要这一档」的度量，否则 17 类路由各自漂移。

**L.2.6【(a) 中】状态/运行存储碎片化：6 套互不相通的 run 存储**

`ingest_runs`（schema.sql:473）、`report_runs`、`capability_runs`（UA-P1 新增）、`kvstate`/`glm_worker_state`、`llm_usage.run_id`、`fcn._JOBS`（内存）。UNIFIED_ARCH_PLAN §0 自己承认「5 个状态存储互不相通」，然后**加了第 6 个**而不是收敛。10x 规模时运维排障需在 6 个地方拼接「昨晚到底跑了什么」。校准为 (a) 中级：架构错误但可逆——`capability_runs` 升格唯一运行表，report_runs/ingest_runs 降 view；便宜的地基修复，越晚越贵。

**L.2.7【(c) 中】供应商/数据广度与本体深度失衡：31 个 provider 文件喂 1 跳图**

31 个 provider 文件、15+ 数据源、947 公司、coverage360 十六维覆盖——**采集广度机构级**；消费侧是 1 跳图查找 + 30 条 semantic_facts 注入 brief（nodes.py:49）。数据进得多、结构推理出得少。微信 T0-T4 分层挖掘是采集侧优秀工程，但仍服务同一条「events → brief」窄管道。广度边际收益递减：论点质量上限由 dossier 证据密度和争论结构决定，不由第 17 个数据源决定。10x 真实断点：① `hybrid_search` trigram 全表扫（vector.py:65-70）；② `challenged_companies_v2` 全论点逐家 Python 循环（thesis_health.py:190-209）；③ `bootstrap_seed` 每晚全量 delete+recreate（store.py:183-191）——均有渐进解。

**L.2.8【(d) 中】Fenny 与（较轻微的）Exploration：共享数据库的并列产品，非复合架构**

`fcn` vendored、从不 import xar（by design）——反向读：与本体/论点/回测/语义层**零数据流耦合**，只共享 LLM 路由器和一张 blotter 表。Monte-Carlo Dupire 定价台对「可信可溯源投研」使命贡献为零。Exploration 至少复用 documents/embeddings 栈且姿态干净；Andy 至少有 macro_bridge → kg_events 的真实勾稽（ingestion/macro_bridge.py）。自用工具箱放多个产品无可厚非，但 Fenny 是**蹭底座的独立产品**，让每个架构决策变贵，且制造「平台很全」的假象稀释主轴完成度。判 (d)：不删代码，删「它是平台一部分」的叙事——应回独立 repo 经 API 消费 XAR。

### L.3 信任链/可溯源作为护城河的实现度：75%

**真实且锋利的部分**（逐条有证据）：
- `_grounded` 证据逐字回查（extract.py:73-93，CJK bigram 修正），不过即丢，写库前拦截——全库最强单点；
- 数值对账闸 tie_out.py + 检索侧 `[UNVERIFIED-NUMERIC]` 标记（nodes.py:20）；
- 证据闸 judge 看到**被引用 chunk 真实文本**（evidence_gate.py:58-70），失败默认 risk=0.6 不放过；
- 类型化论点纪律：证据 ref_id 白名单、证据锚 <5 时 conviction ≤3 硬耦合、不过拒绝入库（DESIGN §5.9）；
- 人审状态机：approve 仅接受 awaiting_approval（graph.py:84-88）；
- PIT 纪律 tx-time 轴已兑现（L.2.2）。

**缺口**（按严重度）：
1. **KG 无纠错回路**（两轮评审列为 P1-3 仍未落地）——图谱静默腐坏时用户无修正入口；可治理与可溯源同等重要。
2. **resolution/校准不写回**（L.2.4）——系统知道哪些断言兑现了，但不因此更可信；信任是静态的，不复利。
3. **人审只闸报告，不闸论点**——Thesis/earnings 裁决的 build 无人审闸，而它们恰是会被机器自动重写的对象（翻转触发重建）；自动重写 + 无人工抽检 = 信任链在最高价值对象上反而更薄。research_audit 方向正确，应扩展到论点重建。

### L.4 Alpha 飞轮闭环度：约 40%

- **环 A（论点反身环，已闭，本系统最好的架构）**：新事实 → evidence_link 相对主张分类（evidence_link.py:28-38，与利好利空解耦——hedge-fund 级正确抽象）→ 争论天平 lean_now（thesis_health.py:91,146）→ flipped/信号挑战 → `challenged_companies_v2` → `_alt_correction` 触发重建（glm_worker.py:584-601）→ 新版本论点。高频信号定「何时重写」（零 LLM z-score），LLM 定「写成什么」——分工教科书级正确。
- **环 B（信号有效性环，断头）**：催化剂 → forward returns + 期望兑现 → **终点是 CLI 打印和 dossier 文本**。不回写 confidence、不调阈值、不改路由、不养源可靠性。回测方法论四重局限（无基准/成本/多重比较/幸存者，catalyst_returns.py:29-31）自我声明「not investable」。
- **环 C（earnings 校准环，孤岛级闭环）**：证明闭环能力存在，未泛化。

**真实飞轮**：(1) 每个信号族有 empirical hit-rate/IC，按 (source × claim_type × polarity × horizon) 分桶；(2) prior 作为**参数**写回——信号权重、论点 conviction 先验、triage 融合权重、路由 relevance 判据；(3) 失效信号族自动降权/退役。当前距离 = 一张物化 `signal_performance` 表 + 三个读它的消费点。缺口小得刺眼。

### L.5 创新性评估与三个最大杠杆动作

**真新颖且可防御**：
1. **semantic_facts 统一事实流 + 前瞻断言结算生命周期**（schema.sql:449-467 + resolve_claims.py）——「叙事/立场/前瞻」与硬事件统一为可点查、可回测、带兑现状态的单一流。多数系统不做 expectation→realization 结算。
2. **类型化论点 + 争论天平 + 验证点 + 机器健康度 + 翻转触发重写**——把投研论点从散文变成带 falsifier、可被新证据机器复核的对象。对「研究」本身的正确建模，行业罕见。
3. **Triage 前置 SNR 闸 + 可审计融合权重**（mining/triage.py）——「值不值得深抽」从事后丢弃前移为事前门控，由 96% 额度烧噪音的实测驱动。

**商品重实现**：RRF 混合检索（教科书 k=60）、多 Agent 报告流水线（TradingAgents 同构且更弱——线性无并行）、RAG 对话壳、vendor+mount 子应用、LiteLLM 路由。

**三个最大杠杆动作（按 ROI 排序）**：

1. **建 `signal_performance` 物化表，把环 B 焊死**（~3-5 天）。聚合 resolution hit/miss + catalyst_returns + earnings calibration 为 (source × category × polarity × horizon) 的 empirical prior 表；三个消费点：`store.add_event` 的 confidence 先验、thesis dossier 证据权重、triage/信号阈值周期再校准。把「可信」从静态纪律变成复利资产，基础设施已存在 80%。
2. **给 KG 一个真的读侧，或给它降级**（二选一，~1 周）。实现 2-3 跳受限递归 CTE（`contagion(node, rels, max_depth=3)` over `supplies`+`single_source_risk`+`causally_linked`），注入 thesis/earnings dossier 作为「二阶暴露」节——回答「capex 砍单沿链传导到哪些持仓」，链式 KG 唯一不可替代的查询。若不做，则正式把 semantic_facts 扶正为中心件，KG 维护降级（冻结本体泛化）。**现在的状态最差：付全价维护税，收 1 跳查找的租。**
3. **报告 DAG 退役，报告 = 论点对象的渲染**（~1 周）。删除 5 散文分析师 + 2 轮无裁判辩论（debate.py 全文、nodes.py:240-259 的 ANALYSTS），报告改为 Thesis + 争论天平 + 证据链 + 健康度 diff + 宏观勾稽的叙述化投影，evidence_gate 保留继续生效。顺手把 report_runs 并入 capability_runs（L.2.6）。收益：消灭与 Thesis 子系统功能重叠 80% 的平行管线、消除文档三处名实不符、每份报告省一整条 STRONG token 链。

### L.6 该删的仪式清单

| 组件 | 证据 | 处置 |
|---|---|---|
| `causally_linked` 边（只写不读） | extract.py:200-208 写入；grep 零读取方 | 删写入或配读侧（动作 2 决定）；当前是纯税 |
| `neighbors(as_of=)` 双时态读路径 | graphrag.py:13-28；零调用方 | 保留签名、停止宣传；或配真实消费者 |
| bull/bear 辩论节点 | debate.py:21-38；无裁判、不改变任何决策 | 删；争论已由 ThesisDebate 严格建模 |
| 5 散文分析师 roster | nodes.py:240-259；串行、产出不可测 | 删；由 dossier + Thesis 渲染替代 |
| Fenny 作为「平台第四模块」的叙事 | `fcn` 与本体零耦合（by design） | 代码可留，架构上划出——独立产品经 API 消费 |
| 6 套 run 状态存储中的 4 套 | L.2.6 | capability_runs 升格唯一，其余降 view |
| 蓝图文档 Neo4j/Graphiti/LangGraph 选型叙事 | DESIGN.md §2 图、§3 表 | 文档层删除，防后来者「按蓝图回退」 |

**明确保留的成熟判断**：单 Postgres 收敛、semantic_facts 复用三表不另起（否决 semantic_claims 平行表是正确裁决）、加性幂等 schema、key-gated 优雅降级、订阅优先计费纪律、确定性闸优先于 LLM 闸。

### J.5 独立复核与处置（2026-07-21，第三方逐条实证）

对附录 J 逐条**独立实证复核**（源码 + 实跑 `pytest` + 端到端 LLM 探测，非转述）。裁定：**J.1.1/J.1.2/J.1.3 三 bug 成立·全修；J.2.1 设计矛盾成立·按实测据裁定为「对称归强层」；J.3 三项各裁定**。验证：`ruff` 通过；`pytest tests/test_glm_worker.py tests/test_capabilities.py tests/test_dynamic_routing.py` **27 passed**（J.1.1 的 2 红转绿）；host 侧 kimi/minimax key 镜像 + `model_usable` USABLE 实证。

| 条目 | 独立裁定 | 处置 |
|---|---|---|
| **J.1.1** 提交 failing 测试（kimi-k2-sub 死引用） | **成立**。实跑 2 failed：`registry.get("kimi-k2-sub")`=None → AttributeError；`minimax-m3-sub`(price0) 插到 glm-4.6 前致链序断言变。死引用共 7 处（test_glm_worker ×5、test_pipeline ×2） | **已修**：死引用 `kimi-k2-sub`→现役 `kimi-k3-sub`；`test_glm52_leads_subscription_chains` 经 J.2.1 修复后**自然复绿**（minimax 出 bulk 链 → chain[:2]=[glm-5.2,glm-4.6]），断言未被削弱 |
| **J.1.2** `_ensure_keys` 未镜像 KIMI/MINIMAX key | **成立**。`llm.py` 清单缺 `KIMI_API_KEY`（P6 后 moonshot 的 key_env）、`MINIMAX_API_KEY`、`MINIMAX_SUB_API_KEY`；host 侧 `s.moonshot_api_key` 镜像到无人读的 `MOONSHOT_API_KEY` | **已修**：清单补三 key（`KIMI_API_KEY:s.moonshot_api_key`、`MINIMAX_API_KEY:s.minimax_api_key`、`MINIMAX_SUB_API_KEY:s.minimax_sub_api_key`），死镜像 `MOONSHOT_API_KEY` 移除。实证：全新进程不手动注入 → `_ensure_keys()` 后两 key 在场、`model_usable(kimi/minimax)`=USABLE |
| **J.1.3** `want_strong` 取静态策略、动态升降层后错配 | **成立**。`complete()` 取 `POLICIES[tc].capability`(未调整)，升 bulk→STRONG 后仍 `reasoning_effort="low"`、降 STRONG→FAST 后仍 effort=high | **已修**：router 新增 `route_plan()→RoutePlan(capability, chain)` 返回**调整后能力**；`complete()` 据 `plan.capability` 定 want_strong（`complete_json` 经 `complete` 委派同得修；`complete_stream` 用静态 `resolve` 无此矛盾，不动）。实证：KG_EXTRACT complexity=high→cap=strong/want_strong=True；DEBATE complexity=low→cap=fast/want_strong=False |
| **J.2.1** minimax-m3-sub 思考模型入 CHEAP_BULK+price0 | **成立（设计矛盾），按实测据裁定** | **已修·对称归强层**：去 CHEAP_BULK/FAST → `(STRONG, REASONING, LONG_CONTEXT)`，与 kimi-k3-sub 对称。**实测据**：minimax 在 bulk 尺寸(mt=800/1200) `reasoning_len=0`、输出干净 JSON、`finish=stop` —— 故豁免 k3 的「空 completion」禁因（不同于 k3 的 reasoning_len=180 空补）；但仍**不做夜间 triage 静默默认**：price=0 思考模型排 bulk 第二席会在 GLM 额度耗尽时无声接住全部批量流量（越权 + 无成本信号）。改由**动态升层**（复杂/高价值 bulk→STRONG）与**强任务跨供应商回退**触达 —— 把 1M 强推理留在其所长的高价值端，cheap triage 仍由 GLM-5.2 默认。实证：bulk 链复为 [glm-5.2, glm-4.6, deepseek-v4-flash]，minimax 仍在 STRONG 候选内 |
| **J.3-①** `set_wechat_review` 对不存在 gh_id 返回 ok | **成立** | **已修**：改 `UPDATE...RETURNING gh_id` + 查 0 行 → `ok:False, detail:未找到发现号`（`db.execute` 无 rowcount，故走 `db.query`+RETURNING，经 pool CM 提交） |
| **J.3-②** 升层后 override 用调整后 capability | **成立（设计取舍）** | **已注**：`route_plan()` docstring 写明 —— 能力升层后 override 查表用调整后能力键，某 task 的 cheap_bulk 级 route override 在升层场景不参与（task 级 override 不受影响）。自用姿态下按预期，不改行为 |
| **J.3-③** 主 key 时 used_sub=False → billing 标 "token" | **不予采纳（cosmetic）** | minimax/kimi 编程套餐 price=0/0 → 无论标签 usd 恒 0、对预算上限无影响（与 G.3 P0 计价保护正交：那是给 glm-5.2 这类**有**计量价的订阅模型防漏）；仅审计标签在「主 key 命中」时显 token。自用·无预算影响下不动，避免触碰 G.3 的 effective-billing 逻辑 |

**附注（非附录 J 项，记录）**：全量 `pytest`（773 项）有 **7 项 DB-态失败**（test_earnings_outcomes ×4 / test_evidence_link::test_link_idempotent_cursor / test_macro_bridge::test_link_routes_and_mount_coexist / test_pipeline::test_end_to_end），经 `git stash` 本轮改动在**净 HEAD 上复现同样 7 红** —— 系共享生产库测试残留（FK 违例 / 游标态，如 test_end_to_end 先删 `documents` 后删引用它的 `kg_edges` 的隔离序 bug），**与本轮模型层改动无关**，属既有测试卫生问题，不在附录 J 范围。

> 第三方裁定：附录 J 的 **3 bug（J.1.1/1.2/1.3）全修 + J.2.1 设计矛盾按实测对称归强层解决 + J.3 两项修/注、一项 cosmetic 不采纳**；model 层改动 `ruff` 通过、目标测试 27 passed、host 侧 key 镜像与动态路由力度实证一致。7 项 DB-态失败为既有测试残留、净 HEAD 同复现，与本轮无关。

### K.6 独立复核与处置（2026-07-22，第三方逐条实证）

按自用姿态逐条裁定（修正确性缺陷；架构/perf-at-scale 记录不做）。验证：全量 `pytest` **771 passed / 2 failed**（较上轮 766/7，K.1.1+K.1.2 使 **5/7 长期红转绿**）、`ruff` clean、schema 幂等应用。

| 条目 | 裁定 | 处置 |
|---|---|---|
| **K.1.1** 测试漂移（anchors 断言 10 vs 实 12） | **成立·已修** | `test_macro_bridge` 断言 10→12 + README 两处「10 条→**12 条理论锚（A1–A8 + 4 META）**」。实测 test_macro_bridge 转绿——此前 RV-2 误归「DB-态」，实为 **code-as-truth 镜像漂移**（theory_anchors.yml 12 条，断言未同步） |
| **K.1.2** kg_edges/kg_events.`source_doc_id` FK 裸 RESTRICT | **成立·已修** | FK 改 `ON DELETE SET NULL`（CREATE 改 + 既有库幂等 DO 块 ALTER，仅当 delete_rule≠SET NULL 才改）。实测两 FK delete_rule=SET NULL；**test_end_to_end + 3 个 earnings 测试连带转绿**（它们复用库删 documents 撞残留边正是此 FK） |
| **K.2.2** Chathy 预算按会话累计 | **成立·已修** | `run_id` 改 per-turn `chat:{sid}:{len(msgs)}`——per-run 预算上限不再跨会话累计致长会话静默降级 |
| **K.3.3** 死代码 extract.py:179 | **成立·已修** | 删被 181 行立即覆盖的 `company_node = d["company_id"]` |
| **K.2.1** trigram 死索引（检索热路径全表扫） | **成立·不做（behavioral/perf-at-scale）** | 加 `%`/`similarity>阈值` 过滤会**改召回行为**，当前语料量非瓶颈；记录，量级上台阶再动 |
| **K.2.3** 抽取半成品盖戳不可恢复 | **成立·不做（热路径重构）** | tx 包裹抽取写入是热路径改动+回归风险；当前「毒文档盖戳跳过」是**有意设计**（不阻塞队列头），残留边为已 `_grounded` 的真事实，自用可接受。记录 |
| **K.3.2** 测试隔离（复用库红） | **部分·infra 级** | K.1.1/K.1.2 已修其中 **5/7**；余 `test_calibration_buckets`（生产 1 条真 verdict 污染全局桶计数）、`test_link_idempotent_cursor`（跨测试 kg_events 泄漏）——用真实公司 id（now/snow/crm）且聚合生产数据，**安全修复需 rollback-fixture 测试隔离基建（§8.2 自用不采纳）**。净 HEAD 同红、非本轮引入 |
| K.3.1 bootstrap 全量重放、K.4 结构建议 | 记录 | unit-of-work/HNSW/schema_migrations 等架构+perf，非正确性缺陷，自用姿态记录不做 |

### 附录 L 处置（2026-07-22）

附录 L（Mission-First 宏观架构）全部为**系统级架构方向**评审：KG 是索引非推理基底（补读侧或降级）、双时态读侧未消费、报告 DAG 退役换「论点对象渲染」、`signal_performance` 飞轮焊死环 B、6 套 run 存储收敛、Fenny 架构划出、蓝图 Neo4j/LangGraph 叙事删除。这些是**战略级重构决策**（每项数日~1 周），非机器可修的正确性缺陷——属用户裁量的架构演进方向，**不在「逐条修复」范围**。其判断（尤其「semantic_facts 是真中心件、KG 现付全价维护税收 1 跳租」「环 B 断头、signal_performance 是最大杠杆」）记录供决策，本轮不做机械修改。其「明确保留的成熟判断」（单 Postgres、加性幂等 schema、订阅优先计费、确定性闸优先）与本项目现状一致。
