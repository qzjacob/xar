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
