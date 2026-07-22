# WeChat「全网发现」混合漏斗 —— 代码审核

> 评审范围：微信发现链路新增/变更（`wechat_discover.py`、`wechat_search.py`、`wechat_promote.py`、`daily.py`、`schema.sql`、`config.py`、API/CLI、`tests/test_wechat_discover.py`）。
> 性质：本文件仅记录审核意见，**不修改任何代码**。配套文档：`WECHAT_DISCOVERY_PLAN.md`。
> 测试状态：11/11 通过。代码整体遵循既有 connector 模式（fail-soft 搜索、roster/candidate 分表、复用 NULL-safe triage 闸）。

---

## 复核与修复结论（2026-07-20）

四条意见全部复核为**合理**并已修复;测试 12/12 通过（+1 幽灵订阅回归）。

| # | 意见 | 复核 | 修复 |
|---|---|---|---|
| 1 | 幽灵订阅：畸形 subscribe 响应产生假 feed_id | ✅ 确实 | `wechat_promote._werss_subscribe` 无 `feed_id/id` 时返回 `None`（不再 `or gh_id`）→ 候选下轮重试；新增回归测试 `test_werss_subscribe_rejects_missing_feed_id` |
| 2 | 分片下 discovery 重复 N 次，放大外部服务负载 | ✅ 确实 | `daily._run_source` 加 `shard` 形参，discovery+promote 仅在 `shard ∈ {None, 0}` 执行（每日一次）；订阅轮询保持既有逐分片行为不动 |
| 3 | 每日上限跨时区混算 | ✅ 确实（**当前 DB=Etc/UTC 故潜伏**，非活跃 bug）| `_promoted_today` 改 `date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'`，与 `_now()` 的 UTC 写入侧对齐（防 DB 时区漂移） |
| 4 | discover/promote 失败拖累整轮 wechat 运行状态 | ✅ 确实 | discovery+promote 块包 `try/except` WARN，隔离脆弱环节，不误标整行 `wechat` ingest_runs 失败（订阅轮询已成功不受牵连） |

---

## 发现

### 1. 幽灵订阅：畸形 subscribe 响应产生假 feed_id [中 / 静默且不可逆]

- **文件**：`src/xar/mining/wechat_promote.py:49`

```python
return str(data.get("feed_id") or data.get("id") or gh_id)
```

- **问题**：we-mp-rss 返回 HTTP 200 但 body 缺少 `feed_id`/`id` 时（该端点尚在 spike-pending，完全可能），裸 `gh_id` 会被当作 feed id 返回。`promote_candidates` 随即将其写入 roster 并设置 `promoted_at`——候选被**永久标记为已 promote、永不再重试**，而 roster 会一直轮询 `/feed/{gh_id}.json`。
- **触发条件**：仅 200-但-body-异常（网络错误已正确返回 `None`），但失败模式静默，且需手工清库才能恢复。
- **建议**：无 feed id 时返回 `None`，让候选下轮重试。

### 2. 分片下发现逻辑重复执行 N 次 [中 / 外部服务负载放大]

- **文件**：`src/xar/orchestration/daily.py:79-82`

- **问题**：wechat source 运行在分片的 `pull` 阶段内，而 `discover()` 忽略 company 分片（query 是全局的）。`n_shards > 1` 时每个分片执行相同的每日 query 切片、抓取相同候选——对旋转切片本就为规避限流而设计的外部服务造成 N 倍搜索/抓取负载（`_slice_for_today` 假设每日一片）。
- **现状**：`save()` 的 upsert 去重了 DB 写入，但去不掉 HTTP 成本。
- **影响范围**：仅在实际使用分片 daily 运行时成立（上方订阅轮询有同样的既有问题）。
- **建议**：discovery 限定在 shard 0 执行，或移到 extract 阶段。

### 3. 每日上限跨时区混算 [低]

- **文件**：`src/xar/mining/wechat_promote.py:79-82`

- **问题**：`_promoted_today()` 用 `date_trunc('day', now())` 按 DB 会话时区截断，而 `promoted_at` 由 Python 以 UTC 写入（`_now()`）。Postgres 非 UTC 时，午夜前后的 promote 可能被计入错误的日期，导致每日上限被突破（或额度浪费）。
- **建议**：改为 `date_trunc('day', now() AT TIME ZONE 'UTC')`，与写入侧对齐。

### 4. discover/promote 失败拖累整轮运行状态 [低]

- **文件**：`src/xar/orchestration/daily.py:79-82`

- **问题**：`discover()`/`promote_candidates()` 抛错（如迁移前缺 `wechat_discovered` 表）会向上传播，把整行 `wechat` ingest_runs 标记为失败，尽管上方订阅轮询已成功。调用方会把它与其他 source 隔离，影响仅是运行状态误导。
- **建议**：在 discover+promote 块外包 try/except，与模块 docstring 声明的「脆弱环节不拖垮整轮」意图一致。

---

## WD-11 提交审核（`8ec544c`，2026-07-20）

> 评审范围：单提交 `8ec544c`（纯数据变更，`_OVERSEAS_ASSET_TERMS` 13 → 25 词）。
> 测试状态：30/30 通过。**未发现 bug。**

### 已核对一致项

- overseas queries 20→32 与提交信息一致：25 主题词 + 7 标志性名称（`wechat_discover.py:279`）。
- precise queries +11（而非 +12）属**正确行为**："存储芯片"、"算力租赁"已存在于 `cn_routing.py:20,23` 主题词表，被 `_precise_queries` 的 `seen` 去重吞掉（`wechat_discover.py:246-250`），无重复 query 发出。

### 1. 注释与实际集合构成不符 [低 / 文档漂移]

- **文件**：`src/xar/ingestion/wechat_discover.py:234`
- **问题**：本次新增两个公司名（"长江存储"、"长鑫存储"），与上方注释块（`wechat_discover.py:224-227`）声明的"剔除公司名"（33% 保留率）直接矛盾。判断为有意为之——当时记录的失败模式是**海外**公司名与同名中文账号碰撞，不适用于国内供应链公司——但注释现已错误描述集合构成。
- **建议**：更新注释（如注明国内供应链公司名豁免剔除规则），避免下一轮赛马驱动的裁剪基于过时理由将其删除。

---

## WD-13 提交审核（`52f58db`，2026-07-20）

> 评审范围：单提交 `52f58db`（promoted_at 误盖修复、占位订阅端点、广度优先收号、主题均衡选臂），通读 diff 及全部改动文件完整上下文。
> 测试状态：`pytest tests/test_wechat_discover.py` 29 passed 本地复验通过。核心修复方向正确，广度优先收号与主题均衡选臂逻辑实现无误。

### 中低严重度

**1. `approved` 候选仍被 `keep_rate >= min_keep_rate` 闸过滤，与 docstring 承诺矛盾**

- **文件**：`src/xar/mining/wechat_promote.py:89-95`
- **问题**：docstring 写明「运营方已批准 → 订阅（人已裁定，不看 keep_rate）」，但 SQL 的 `WHERE ... keep_rate >= %s` 对所有行生效。
- **触发场景**：运营方在 Fetchy 批准一个 queued 边缘号（keep_rate 0.6）→ 下一轮 `_sync_candidates` 重算后该号 keep_rate 漂到 0.5 以下（新进几篇低分文即可）→ 该号从 `cands` 静默消失，永远停在 `approved` 态、永不订阅，且无任何告警。
- **建议**：`approved` 分支单独查询（只要求 `promoted_at IS NULL AND promote_status='approved'`），或在 SQL 加 `OR promote_status='approved'`。

**2. 人工批准的号订阅成功后被改写为 `promote_status='auto'`**

- **文件**：`src/xar/mining/wechat_promote.py:129-131`
- **问题**：丢失「人工批准」来源信息，事后审计无法区分自动晋升与人工晋升。
- **建议**：按入档分支写 `'approved'`（或新增 `'manual'`）而非一律 `'auto'`。

### 低严重度

**3. HITL 队列存在滞留条目** — `wechat_promote.py:118-120` + `hitl_queue():201-207`
`queued` 只从 `NULL→queued` 单向写入；某号入队后 keep_rate 跌破 min_keep_rate 便不再出现在 `cands` 中，也就再无人更新其状态，`hitl_queue`（无 keep_rate 下限）会一直把它显示在 Fetchy 待批区。建议 `hitl_queue` 加 `keep_rate >= min_keep_rate` 过滤，或 promote 每轮把掉队者降回 NULL。

**4. `promotion_stats.eligible_pending` 不排除 `rejected`** — `wechat_promote.py:190-197`
与 `promote_candidates` 的过滤条件不一致，被拒号仍计入「够格待晋升」，观测口径失真。

**5. KG 挖词查询无界** — `src/xar/mining/wechat_evolve.py:130-134`
`JOIN kg_edges ON e.src_id = n.id OR e.dst_id = n.id` 的 OR 条件阻索引，且 `SELECT DISTINCT` 无 `LIMIT`、无时间窗，每轮 select_queries 全量扫 KG。当前 KG 规模小可接受；文档/边数增长后会变慢。建议加 `LIMIT`（如 500）后再做 Python 侧过滤。

**6. 前端类型不准** — `web/src/types-ops.ts` `promoteQueue?: WechatReviewItem[]`
`hitl_queue` 返回 `{gh_id, name, articles_seen, articles_kept, keep_rate}`，无 `review_status` 字段，与 `WechatReviewItem` 不符。运行时不报错（FetchyPage 只用了存在的字段），但类型声明会误导后续开发。建议新增 `WechatPromoteItem` 接口。

### 行为变化（提示，非 bug）

- `discover_via_wcda` 现在对所有选中查询（最多 40 个）都发 searchbiz 请求后才按 round-robin 截断（`wechat_discover.py:339-343`），此前凑满 run_cap 即早停——每轮 WCDA 搜索调用量约增至 3 倍。与 commit 意图一致，但需确认 wechat-download-api 侧反爬/限流可承受。
- `discover_accounts`/`accounts_available` 在 cli/daily 中已无调用方（仅测试引用），作为弃用路径保留，可考虑后续清理。

### 无法验证项

- `werss_api.unsubscribe` 的 `DELETE /api/v1/wx/mps/{feed_id}`（`werss_api.py:130`）：we-mp-rss 的确切退订端点与路径参数（feed id 还是 mp id）无法离线核实，建议主机侧实测一次退订往返后再依赖 prune 的「停用+退订」语义。

---

## 复核与修复结论 — WD-11 审核(2026-07-21)

意见 #1(注释文档漂移)复核为**合理**并已修复;无代码/行为变更,测试 32/32。

| # | 意见 | 复核 | 修复 |
|---|---|---|---|
| 1 | 注释「剔除公司名」与新增国内公司名(长江存储/长鑫存储)矛盾 | ✅ 确实(低,纯文档) | 更新三处注释(`wechat_discover.py` 赛马实证块 / `_precise_queries` docstring / `_OVERSEAS_ASSET_TERMS` 内联),明确**剔除规则限定「歧义/海外」公司名**(博通→博通集成),国内无歧义龙头名(长江存储/长鑫存储=YMTC/CXMT)例外保留 |

**补充核实(reviewer 的裁剪担忧)**:`prune_query_pool()` 只删 `strategy='mined'` 查询;长江存储/长鑫存储在 `_OVERSEAS_ASSET_TERMS`(strategy=overseas/broad,本体词),**永不被剪枝**,仅由 UCB 按 keep_rate 决定选取频率。故「被裁剪删除」的实际风险不存在;修复消除的是人读注释的困惑。

**自查补充(reviewer 未覆盖 WD-9/WD-10 进化引擎)**:复核 `mining/wechat_evolve.py` 的 UCB 顺序(update→mine→prune→select→bump,反馈来自上轮已 triage 文档)、冷启动(evaluated 不足时全探索填满 n)、剪枝谓词(runs≥2 且 articles=0 或 keep_rate<5%,NULL 安全)——**均正确,无 correctness bug**。测试 test_wechat_evolve 覆盖利用/探索/反馈聚合/挖词/剪枝。

---

## WD-13 复核与处置（2026-07-22，第三方逐条实证）

WD-13 审核 6 条逐条复核为**合理**并已修复；`pytest tests/test_wechat_discover.py` 全绿、`ruff`/`tsc` 通过。

| # | 意见 | 复核 | 修复 |
|---|---|---|---|
| 1 | `approved` 候选仍被 `keep_rate>=min` 闸过滤 | ✅ 确实（本轮引入） | `promote_candidates` 查询改 `(promote_status='approved' OR (articles_seen>=min AND keep_rate>=min))`——已批准号不再受阈值闸，keep_rate 事后跌破也不静默消失 |
| 2 | 人工批准号订阅后被写 `'auto'` | ✅ 确实 | 按来源写：approved→`'approved'`、否则 `'auto'`，审计可区分自动/人工 |
| 3 | HITL 队列滞留掉队者 | ✅ 确实 | `hitl_queue` 加 `keep_rate >= min_keep_rate` 下限，过滤不再够格的僵尸号 |
| 4 | `eligible_pending` 不排除 `rejected` | ✅ 确实 | `promotion_stats` 加 `promote_status <> 'rejected'`，与 `promote_candidates` 口径一致 |
| 5 | KG 挖词查询无界（`OR` 阻索引/无 LIMIT/无窗） | ✅ 确实 | 改有界：只看**近 300 篇** kept 微信文档的边、整体 `LIMIT 500`，不再全 KG 扫 |
| 6 | 前端类型不准（`promoteQueue: WechatReviewItem[]`） | ✅ 确实 | 新增 `WechatPromoteItem`（`articles_kept`、无 `review_status`），`promoteQueue` 改用之 |

**行为变化项**（WCDA 搜索×3 倍、`discover_accounts` 弃用）已知悉；**无法验证项** `werss_api.unsubscribe` 端点本轮**已主机实测通过**（激活扫码会话后清理 bogus feed 时验证退订往返，返回 True）。
