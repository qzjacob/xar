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

除年龄之外,探针还能用 `Probe(degrade=...)` 断言「不管时间戳多新,状态至少坏到这一档」,
用于那些**不是时间新鲜度**的坏消息:dagster 守护 unhealthy、队列死锁、**部分 run 失败**、
连接器批量报错。它取代了早期「把时间戳伪造成一年前」的写法 —— 伪造会让 `hbAgeS` 变成
假数据(排障时最误导人),而且只能表达 down、无法表达「部分失败 = stale」这种中间档。

四个刻意规避的陷阱(见 `src/xar/monitoring/detector.py` 模块头):
1. 「尝试过」≠「有产出」→ 双信号;
2. dagster `job_ticks` 在 7 天零执行期间全绿 → **只认 `runs.status`**;
3. 只在状态变化时写的 key,「行不存在」是第三态 `unknown`,不是停摆;
4. **「有一个成功」≠「跑好了」**(2026-07-30 补):那夜 9 个 run 死了 4 个,而面板显示
   `ok` —— 因为只看「距上次 SUCCESS 多久」,而 1.8h 前确实成功过。现在窗口(26h,覆盖
   一个夜间调度周期)内的失败数/失败率作为独立信号:≥1 个失败 → `stale`;
   失败率 ≥1/3 或一个都没成 → `down`。阈值在 `catalog._DAG_FAIL_*`。
   同理,队列死锁金丝雀的并发上限**从 dagster 现读**(GraphQL `runQueueConfig`),不写死 ——
   写死过 10,而后来把 `max_concurrent_runs` 调成 7,`started >= 10` 便永不成立、
   金丝雀静默失效。监控自己的阈值跟着被监控方的配置漂移,是这类工具最典型的烂法。

## 二、报警怎么送出去

- **页内**:面板告警流 + 左栏 Monitor 旁的红点(未解决 critical 计数,60s 刷新)。
- **手机**:Telegram。`severity=critical` 的任务转 `down` 时推一次;此后未 ack 则**每 24h**
  提醒一次;恢复推一次。`warn` 级只进页内,不打扰手机。
  ⚠️ `last_notified_at` 为 NULL 视作「**从未成功推送过**」→ 下一轮立即补发首条,不等提醒间隔。
  这不是优化而是必需:告警若在通道配通**之前**开出来(或推送失败),没有补发就**永久静默** ——
  而那恰恰是最需要被通知的一类。2026-07-31 接通当天实测踩到:dagster.runs 的 critical
  在配通前就已开启,当时若不补发,当夜的失败不会有任何动静。

**现状(2026-07-31):已接通** —— 专用 bot `@xar_alertbot`,推送目标为私聊 chat。
带外死人开关也已挂进主机 crontab(见 §三)。下面是配法与踩坑记录。

接通手机推送(三个键,顺序有讲究):

```bash
XAR_MONITOR_BOT=<专用告警 bot 的 token>     # 留空则回退 Chathy 的 BOT_HTTP_API
XAR_MONITOR_TELEGRAM_CHAT=<数字 chat id>    # ⚠️ 不是 bot 用户名
```

⚠️ **这条链路唯一不直观的一步**:Telegram bot **无法主动发起会话**。所以
`XAR_MONITOR_TELEGRAM_CHAT` 那个数字,必须**先由你在 Telegram 里给该 bot 发一条消息**
(如 `/start`)之后,才能从 `getUpdates` 里拿到。在那之前它根本不存在 ——
把 bot 的用户名(如 `xar_alertbot`)填进去是不行的,发送会 400 chat not found。

发完消息后,面板顶部横幅会**自动列出**发现到的 chat id(`alerts.discover_chats()` 直接问
告警 bot「谁跟我说过话」),复制填进 .env 即可。命令行等价物:

```bash
TOK=$(grep -E '^XAR_MONITOR_BOT=' .env | cut -d= -f2-)
curl -s "https://api.telegram.org/bot${TOK}/getUpdates" \
  | python3 -c "import json,sys; [print((u.get('message') or {}).get('chat',{}).get('id')) for u in json.load(sys.stdin)['result']]"
```

改完 .env 必须 `docker compose up -d app` —— compose 的 `env_file` 是在**创建容器时**注入的,
改文件不会自动生效(容器里没有 .env 文件本身)。

**为什么用专用 bot**:告警不该混进 Chathy 的聊天流,而且换掉其中一个不会连累另一个。
未配置时页内告警照常工作,只是不推手机 —— 面板会明确提示缺哪一个。

静音(维护窗):面板上的静音开关,或 `PUT /api/ops/monitor/mute {"hours": 2}`。
**静音只压推送,历史与台账照记** —— 否则静音期间的停摆会彻底消失在记录里。

## 三、带外死人开关(**已装**,2026-07-31)

巡检线程和 Telegram 推送都在 app 容器里,所以「app 整个挂了」恰恰是**报警会跟着一起死**
的情况 —— 这一层是唯一能在那时还发声的。脚本由主机 cron 独立跑,只用 bash + curl,
不依赖 XAR 的任何代码路径,也不碰 docker。

已装入 crontab 的行:

```cron
3-59/10 * * * * /home/jake-ma/Project/XAR/main/deploy/monitor/deadman.sh >> /home/jake-ma/monitoring/deadman.log 2>&1
```

几个不显然的点:
- **错开 :00**(用 `3-59/10` 而非 `*/10`):本机整点已有每分钟的 rsync 与 Phantom 的整点任务扎堆。
- crontab 里已有的 `SHELL` / `PATH` / `CRON_TZ` 声明会被这一行继承,不必重复声明。
- 装法是**纯追加**(`crontab -l` 导出 → 追加 → 装回),并先备份到 `~/monitoring/crontab.bak-*`;
  这台机器上还跑着 Phantom 的交易任务,crontab 绝不可整体覆写。
- 它与 app 内的告警**共用同一个 bot**(`XAR_MONITOR_BOT` 优先,回退 `BOT_HTTP_API`)——
  两边若用不同 bot,「app 活着时的告警」与「app 死了时的告警」会落在两个会话里,
  而后者恰恰是最慌乱的时刻。

手测:`DEADMAN_TEST=1 deploy/monitor/deadman.sh`(强制走一次推送路径)。
⚠️ 测完记得删掉 `~/monitoring/.deadman_state` —— 否则真出事时会被 6 小时节流压住。
建议用 cron 的最小环境验证(交互 shell 里 `grep` 可能是函数,会掩盖 PATH 问题):

```bash
env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  HOME=$HOME LOGNAME=$USER USER=$USER deploy/monitor/deadman.sh
```

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

## 六、Dagster 内存预算与真实约束(改并发/限额前必读)

2026-07-30 夜:9 个 run 死了 4 个,全部 memcg OOM。**结论:不要提高容器内存限额,要降低并发需求。**

> **2026-07-31 夜复测 —— 内存治理成立,但真正的瓶颈是磁盘 IO,不是内存。**
>
> | | 07-30(治理前) | 07-31(治理后) |
> |---|---|---|
> | dagster memory.peak | 8.00 / 8 GiB(打满) | **6.68 / 8 GiB** |
> | memcg OOM | **9 次** | **0 次** |
> | 分片结果 | 5/8 成功 | 见 `verify_nightly.sh` |
>
> 预算模型算的是 6.7G,实测 6.68G —— 几乎分毫不差,`in_process` + 分 job 限额都按预期生效。
>
> **但同一时段 `nvme0n1` 打到 %util 174、831 MB/s 读、13.6k r/s,而内存还剩 18% 余量。**
> 也就是说:**再上调并发,先撞的是 IO,不是那道 8G 内存闸。** 想缩短夜跑墙钟,加内存或提限额
> 都没用 —— 得从 IO 侧下手(优先摘掉 FMP 那每片约 45min 的 403 空转;其次让 8 个分片错峰
> 起而不是齐发)。
>
> 连带事故(值得记住的耦合):IO 饱和期 Postgres 被拖成 `unhealthy`,qwendrain 的
> `_claim()` 拿不到连接,于是 **GPU 空转约 30 分钟(19W / 0% util)**——
> 夜间拉取会**饿死 GPU 抽取链**,二者共用同一个 Postgres 与同一块 NVMe。db 恢复后自愈。
>
> ⚠️ 诊断纪律:那晚 load 冲到 215,但查下来 **phantom-appsmith 的 mongo+java+caddy 在 02:59
> 起来才是主因**(mongod 单进程 19.7% CPU),XAR 夜跑只是叠加项。**本机是多租户,
> 别默认高负载就是 XAR 的锅。**

实测账(`dagster` 容器硬限 8G):

| 项 | 值 | 说明 |
|---|---|---|
| 固定开销 | ~580 MB | `dagster dev` = webserver + daemon + code server |
| worker import 基线 | **214 MB** | 每个 run 进程都要付 |
| fastembed 模型 | **+618 MB** | 只有 `parse_pending()` 加载 ⇒ 只有 `extract_all` 付 |
| pull_shard / run | ~730 MB | in_process;multiprocess 下约 951 MB |
| extract_all / run | ~1759 MB | 214 + 618 + parse 工作集,与内核 OOM 记录精确吻合 |
| 可用额度 | ~6629 MB | `8192 × 0.88 − 580`,12% 留给 page cache(cgroup v2 计入 file cache) |

`6×730 + 1759 + 580 = 6719` → 余 1473 MB(18%)✓ 而 `8×730 + …= 8179` 只余 13 MB ✗

**为什么不能提限额**:`docker.slice` 软闸 24G 昨夜已被打满,各容器限额之和已 22.5G。
提到 12G 会把聚合推向 28G 硬闸,冲顶时受害的是整个栈(db 峰值已 100%、werss 已 100%)。
本机 7×24 交易机 —— 不可接受。

**为什么用 in_process**:三个 job 各自只有一个 asset = 一个 op = 一个 step,multiprocess
买不到并行度,只买到「多一个进程 + 多一份 214MB import」;而 dagster 的 `start_method`
默认是 **spawn**(可选值只有 spawn/forkserver,**没有 fork**),子进程是全新解释器、
零 copy-on-write,那 214MB 是实打实再付一遍。

⚠️ **in_process 的代价**:multiprocess 恰恰是被 OOM 的 shard 能被记成 FAILURE 的原因
(内核杀 step 子进程,父进程活着写终态)。in_process 下 OOM 会连带杀掉唯一进程 →
run 卡 `STARTED`,即当初 7 天死锁的形态。现依赖 `run_monitoring.max_runtime_seconds`
兜底回收(8h)—— **那份配置不可删**。若改回 multiprocess,pull 上限须同步 6 → 5。

想缩短墙钟优先摘掉 FMP(端点 403 永久下线,每片纯空转约 45min),比调系数划算。

## 七、排障

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
