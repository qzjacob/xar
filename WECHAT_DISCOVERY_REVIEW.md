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
