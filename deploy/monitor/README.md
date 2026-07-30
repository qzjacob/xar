# 任务监控 —— 部署与运维

2026-07-29 全链路审计的产物。审计暴露的不是某个 bug,而是「**停摆不可见**」这一类问题:
Dagster 队列死锁 7 天零执行无人察觉、glmworker 被 phanny 拖死 3.5 小时只能翻 docker logs、
wechat/futu 静默哑火 6.5/24 天而 cadence 戳**至今仍是绿的**。

面板:`http://localhost:8000/jarvy/monitor`

---

## 一、它怎么判「停摆」

每 120 秒巡检一轮(跑在 app 容器的后台线程,同 `chathy.telegram.start_background`)。
每个任务两路信号:

| 信号 | 含义 | 例 |
|---|---|---|
| **心跳** | 这个任务最近一次**动过** | `counters.last_cycle_at`、cadence 戳、dagster daemon 心跳 |
| **产出** | 最近一次**真的产出了东西** | `documents.ingested_at`、`alt_signals.observed_at` |

**两者取较坏者**。这一条是整套设计的核心 —— 只看心跳就会精确复现当初那 7 天:
`glm_worker._stamp` 在 `fn()` 不抛异常时就盖绿戳,所以源死透之后戳照样绿。

状态:`ok → stale(超 SLA)→ down(超 SLA×3)`;另有 `unknown`(信号缺失,**不报警**)、
`unconfigured`(没配,不参与判定)。**恶化需连续 2 轮确认,恢复立即生效** ——
误报一次会训练人忽略报警,比漏报更致命。

三个刻意规避的陷阱(见 `src/xar/monitoring/detector.py` 模块头):
1. 「尝试过」≠「有产出」→ 双信号;
2. dagster `job_ticks` 在 7 天零执行期间全绿 → **只认 `runs.status`**;
3. 只在状态变化时写的 key,「行不存在」是第三态 `unknown`,不是停摆。

## 二、报警怎么送出去

- **页内**:面板告警流 + 左栏 Monitor 旁的红点(未解决 critical 计数,60s 刷新)。
- **手机**:Telegram。`severity=critical` 的任务转 `down` 时推一次;此后未 ack 则**每 24h**
  提醒一次;恢复推一次。`warn` 级只进页内,不打扰手机。

接通手机推送(**目前缺的就是这一步**):

```bash
# bot token 已在 .env 的 BOT_HTTP_API;只缺「推给谁」
echo 'XAR_MONITOR_TELEGRAM_CHAT=<你的 chat id>' >> .env
docker compose up -d app
```
chat id 可以从面板顶部的提示横幅直接复制(它会列出 `chat_channels` 里已知的 telegram chat)。
未配置时页内告警照常工作,只是不推手机 —— 面板会明确提示。

静音(维护窗):面板上的静音开关,或 `PUT /api/ops/monitor/mute {"hours": 2}`。
**静音只压推送,历史与台账照记** —— 否则静音期间的停摆会彻底消失在记录里。

## 三、带外死人开关(装一次)

巡检线程和 Telegram 推送都在 app 容器里,所以「app 整个挂了」恰恰是**报警会跟着一起死**
的情况。这个脚本由主机 cron 独立跑,只用 bash + curl:

```bash
crontab -e
# 每 10 分钟探活;不可达或巡检停摆 >15min 就直接打 Telegram API
*/10 * * * * /home/jake-ma/Project/XAR/main/deploy/monitor/deadman.sh >> ~/monitoring/deadman.log 2>&1
```

手测:`DEADMAN_TEST=1 deploy/monitor/deadman.sh`(强制走一次推送路径)。
自身也节流:同一故障 6 小时内不重复推。

## 四、处置操作(零 docker socket)

面板按钮:

| 动作 | 机制 |
|---|---|
| **重启 worker** | 往 kvstate 写 `control_restart`,worker 循环下一轮读到就 `sys.exit(0)`,由 compose 的 `restart: unless-stopped` 拉起 |
| **清 Dagster 队列** | GraphQL `terminateRun` 终止全部在飞 run(同 `deploy/dagster/unstick_run_queue.py` 语义,但不需要 docker exec) |
| **立即拉取** | 删掉该源的 cadence 戳,`_due()` 下一轮即为真 |

**没有挂 docker socket** —— 那等于把宿主 root 交给 app 容器,且无法按操作收窄权限。
软重启唯一盲区是「进程卡得连循环检查都到不了」,而那恰恰是监控会上报
「需人工 `docker restart`」的情形(按钮 tooltip 已写明)。

## 五、加新任务

改 `src/xar/monitoring/catalog.py` 加一个 `Task` 条目即可。
**13 个拉取源是自动生成的** —— 往 `glm_worker.FETCHY_SOURCES` 加源即自动纳入监控
(`tests/test_monitor_catalog.py` 有护栏防止这条退化;否则「加了新源但没被监控」
会成为新的静默盲区)。

产出探针写在 `catalog.YIELD_PROBES`(源 → 数据表 max 时间戳 + 产出 SLA)。
产出 SLA 必须比心跳 SLA 宽松(源本来就可能一整天没新内容),单测有断言守着。

## 六、排障

```bash
# 巡检活着吗
curl -s localhost:8000/api/ops/monitor/summary | head -c 300

# 现探一轮(不发报警),看每个探针的原始判定
curl -s 'localhost:8000/api/ops/monitor?fresh=1' | python3 -m json.tool | head -60

# 某任务的状态时间线
curl -s 'localhost:8000/api/ops/monitor/history?task=fetchy.wechat&hours=168'
```

- 面板全是 `unknown` → 巡检没跑过。查 `XAR_MONITOR_ENABLED`、`docker logs main-app-1 | grep monitor`。
- `dagster.*` 是 `unknown` → app 到 dagster 的 GraphQL 不通。
  `docker exec main-app-1 curl -s http://dagster:3000/graphql -d '{"query":"{version}"}' -H 'Content-Type: application/json'`
- 接口 404 → 镜像还没 rebuild(源码 baked 进镜像)。
