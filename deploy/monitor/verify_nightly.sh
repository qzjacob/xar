#!/usr/bin/env bash
# 夜间 dagster 调度 + 内存治理效果核验(2026-07-30 起)。
#
# 为什么是本地脚本而不是云端定时 agent:要看的东西全在这台机器上 —— docker、
# dagster 的 run 存储(2026-07-31 起在 Postgres 的 dagster 库,此前是容器内 sqlite)、
# cgroup 的 memory.peak、内核 OOM 日志、localhost:8000。
# 云端 agent 能 clone 仓库,但摸不到这些。
#
# 用法:
#   deploy/monitor/verify_nightly.sh              # 核验最近一个调度窗(默认 26h)
#   WINDOW_H=48 deploy/monitor/verify_nightly.sh  # 放宽窗口
#
# 想每天早上自动跑并推 Telegram(需先配 XAR_MONITOR_TELEGRAM_CHAT):
#   0 8 * * * /home/jake-ma/Project/XAR/main/deploy/monitor/verify_nightly.sh --notify \
#             >> ~/monitoring/verify_nightly.log 2>&1
set -uo pipefail

WINDOW_H="${WINDOW_H:-26}"
REPO="${REPO:-/home/jake-ma/Project/XAR/main}"
NOTIFY=0; [ "${1:-}" = "--notify" ] && NOTIFY=1
FAILS=()
note() { printf '%s\n' "$*"; }
bad()  { FAILS+=("$1"); printf '  ✗ %s\n' "$1"; }
good() { printf '  ✓ %s\n' "$1"; }

note "===== XAR 夜间调度核验  $(date -Is) (窗口 ${WINDOW_H}h) ====="

# ── 1. 本窗口的 run 结果 ────────────────────────────────────────────────────────
note ""
note "[1] dagster run 结果"
# ⚠️ 数据源是 **Postgres 的 dagster 库**,不再是 /dagster/history/runs.db(2026-07-31 迁移)。
# 那个 sqlite 文件**还在卷里、还有 217 行**(含迁移前那批僵尸),继续读它不会报错,
# 只会永远回放迁移前的旧世界 —— 「不报错的错数据」比读不到更难发现。
# 备份 runs.db.pre-pg-2026-07-31 仅供取证,任何判定都不许再碰它。
# 走 db 容器的 psql 而不是在 dagster 容器里连库:少一层依赖(不要求 dagster 容器装 psycopg2),
# 且 dagster 挂掉时这一节仍能给出结论 —— 恰恰是最需要它的时候。
# create_timestamp 是 timestamp WITHOUT time zone、存的是 UTC,所以必须拿
# `now() at time zone 'utc'` 比,直接用 now() 会按本机 EDT 比,窗口整体偏 4 小时。
RUNS_RAW=$(docker exec main-db-1 psql -U xar -d dagster -tA -F'|' -c \
  "SELECT run_id, pipeline_name, status, coalesce(partition,''), coalesce(start_time::text,''),
          coalesce(end_time::text,'')
     FROM runs
    WHERE create_timestamp > (now() at time zone 'utc') - interval '${WINDOW_H} hours'" \
  2>/dev/null)
# ⚠️ 数据经 **argv** 传给 python,不走管道(2026-07-31 两次踩坑后的定型写法):
#   · `… | python3 - <<'PY'` 不行 —— heredoc 本身就是 stdin,会盖掉管道(README §6.5);
#   · `python3 -c "$(cat <<'PY' … PY)"` 嵌在外层 `$( )` 里也不行 —— 外层命令替换会在
#     heredoc 内部的括号处提前收口,实测 $RUNS 直接变空、下游全线 JSONDecodeError。
# 把脚本放 heredoc(占 stdin)、把数据放 argv,两者各走各的,不会互相抢。
RUNS=$(python3 - "$RUNS_RAW" <<'PY'
import sys, json, time
now = time.time()
rows = []
for line in (sys.argv[1] or "").splitlines():
    if not line.strip():
        continue
    f = line.split("|")
    if len(f) < 6:
        continue
    rid, job, st, part, s, e = f[0], f[1], f[2], f[3] or None, f[4], f[5]
    rows.append((rid, job, st, part, float(s) if s else None, float(e) if e else None))
out = {"total": len(rows), "by_status": {}, "shards_started": 0, "shards_ok": 0,
       "extract": [], "never_started": 0, "max_overlap": 0, "in_flight": 0,
       "oldest_in_flight_h": 0.0, "in_flight_ids": []}
iv = []
for rid, job, st, part, s, e in rows:
    out["by_status"][st] = out["by_status"].get(st, 0) + 1
    if s is None:
        out["never_started"] += 1
    else:
        # ⚠️ 在飞的 run 没有 end_time。此前写成 `(s, e or s)` —— 零长区间,
        # 于是「全部 run 都还在飞」的那一夜(2026-07-31)峰值并发算出来恒为 0,
        # 脚本还据此打了 ✓「并发上限生效」。未结束 = 一直占着槽,必须按 now 收尾。
        iv.append((s, e if e is not None else now))
    if st in ("STARTED", "STARTING"):
        out["in_flight"] += 1
        out["in_flight_ids"].append(rid)
        if s is not None:
            out["oldest_in_flight_h"] = max(out["oldest_in_flight_h"], (now - s) / 3600)
    if job == "pull_shard_job":
        if s is not None: out["shards_started"] += 1
        if st == "SUCCESS": out["shards_ok"] += 1
    if job == "extract_all_job":
        out["extract"].append(st)
# 峰值并发:扫描区间端点。key 让同一时刻的 -1(结束)排在 +1(开始)之前,
# 否则一个 run 结束、另一个紧接着开始会被算成一次虚假重叠。
pts = sorted([(s, 1) for s, _ in iv] + [(e, -1) for _, e in iv], key=lambda x: (x[0], x[1]))
cur = 0
for _, d in pts:
    cur += d
    out["max_overlap"] = max(out["max_overlap"], cur)
out["oldest_in_flight_h"] = round(out["oldest_in_flight_h"], 2)
print(json.dumps(out))
PY
)
if [ -z "$RUNS" ]; then
  bad "读不到 dagster 的 Postgres run 存储(db 容器在吗?dagster 库存在吗?)"
else
  python3 - "$RUNS" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
print(f"  总 run {d['total']}  状态 {d['by_status']}")
print(f"  pull_shard 启动 {d['shards_started']}/8  成功 {d['shards_ok']}")
print(f"  extract_all {d['extract'] or '(未跑)'}")
print(f"  实测峰值并发 {d['max_overlap']}  (期望 ≤7:6 pull + 1 extract)")
print(f"  从未启动(卡 QUEUED) {d['never_started']}")
PY
  MAXOV=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['max_overlap'])" "$RUNS")
  SHOK=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['shards_ok'])" "$RUNS")
  SHST=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['shards_started'])" "$RUNS")
  NEVER=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['never_started'])" "$RUNS")
  [ "$MAXOV" -le 7 ] && good "并发上限生效(峰值 $MAXOV ≤ 7)" \
                     || bad "峰值并发 $MAXOV > 7 —— tag_concurrency_limits 没生效?"
  [ "$SHST" -ge 8 ] && good "8 个分片全部启动过(队列未死锁)" \
                    || bad "只有 $SHST/8 个分片启动 —— 检查是否又有 run 卡住"
  [ "$SHOK" -ge 8 ] && good "8 个分片全部成功" \
                    || bad "只有 $SHOK/8 个分片成功 —— 判因顺序:先看下面「僵尸 run」那条(容器被重启则 run 是被杀不是跑挂),再看第 3 节 dagster 自己 cgroup 的 OOM"
  [ "$NEVER" -eq 0 ] && good "没有永久卡在 QUEUED 的 run" \
                     || bad "$NEVER 个 run 从未启动"

  # ── 僵尸 run 判别(2026-07-31 补)──────────────────────────────────────────
  # DefaultRunLauncher 的 run worker 是 dagster 容器内的**子进程**。因此
  # 「状态 STARTED 但容器里一个 run worker 都没有」= 这些 run 已经死了、只是没人改状态,
  # 它们会一直占着并发槽直到 run_monitoring 超时回收。
  # 这是 2026-07-31 那次误判的关键:当时脚本只看到「0/8 成功」,把矛头指向内存,
  # 而真因是容器 04:33 重启打死了全部 7 个 worker。有这一条就能一眼分开
  # 「跑失败了」和「跑的人被杀了」。
  INFL=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['in_flight'])" "$RUNS")
  OLDH=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['oldest_in_flight_h'])" "$RUNS")
  IDS=$(python3 -c "import json,sys; print(' '.join(json.loads(sys.argv[1])['in_flight_ids']))" "$RUNS")
  if [ "${INFL:-0}" -gt 0 ] && [ -n "$IDS" ]; then
    # ⚠️ 判据是**逐 run 点名**,不是「数一数有几个 worker 进程」(2026-07-31 修正)。
    # 第一版按命令行特征 `api execute_run` 数,结果恒为 0 —— 因为 DefaultRunLauncher
    # 的 run worker 根本不长那样:它是 daemon 用 multiprocessing spawn 出来的子进程
    #   python3.12 -c from multiprocessing.spawn import spawn_main; spawn_main(...)
    # 命令行里没有任何 dagster 字样。于是**活着的 run 也会被判成僵尸**,
    # 比原来那个漏报更糟(误报会让人去查根本没发生的事)。
    # 现在改为:把在飞 run 的 id 传进容器,看它的 id 是否出现在**任何**进程的命令行里
    # (dagster 会为每个 run 起 compute-log 的 tail -F /dagster/storage/<run_id>/… 和
    # watch_orphans.py,cmdline 里带 run_id)。点名法与启动器实现解耦,换 launcher 也不失效。
    ALIVE=$(docker exec main-dagster-1 python3 -c "
import glob, os, sys
me = os.getpid()
ids = [x for x in sys.argv[1:] if x]
cmds = []
for d in glob.glob('/proc/[0-9]*'):
    pid = int(os.path.basename(d))
    if pid == me: continue          # 排除本进程:它的 argv 里就带着这些 run_id
    try: cmds.append(open(d + '/cmdline').read().replace('\0', ' '))
    except Exception: pass
blob = '\n'.join(cmds)
print(sum(1 for r in ids if r in blob))" $IDS 2>/dev/null)
    note "  在飞 run ${INFL} 个(最老 ${OLDH}h),其中有活进程的 ${ALIVE:-?} 个"
    if [ -n "$ALIVE" ] && [ "$ALIVE" -lt "$INFL" ]; then
      bad "$((INFL - ALIVE))/${INFL} 个 run 状态为 STARTED 但容器内找不到对应进程 —— **僵尸 run**(容器中途重启/被杀),不是跑失败"
    elif [ "${ALIVE:-0}" -eq "$INFL" ]; then
      good "在飞的 ${INFL} 个 run 都有活进程(不是僵尸)"
    fi
  fi
fi

# ── 2. 内存:dagster 容器峰值 + docker.slice 聚合 ────────────────────────────────
note ""
note "[2] 内存治理"
CID=$(docker inspect main-dagster-1 -f '{{.Id}}' 2>/dev/null)
if [ -n "$CID" ]; then
  P=/sys/fs/cgroup/docker.slice/docker-$CID.scope
  # ⚠️ memory.peak 是**自容器本次启动以来**的累计,容器一重启就归零(2026-07-31 教训)。
  # 此前脚本无条件把它当作夜跑峰值读:那天 04:33 容器重启过,读到 0.73G,
  # 于是打了 ✓「峰值 0.73G < 7.4G」—— 把「测不到」报成了「通过」,
  # 这比报失败更糟,因为它让人以为治理已验证。
  # 现在先判 StartedAt 是否落在核验窗内:落在窗内 ⇒ peak 只覆盖重启后那一段 ⇒ 判为不可验证。
  # 同时把 UTC 的 StartedAt 转成本地时区显示 —— 此前直接打印 RFC3339 的 UTC 串,
  # 与表头的本地时间并排,出现过「容器启于 08:33 而现在 08:15」的时间倒流。
  read -r DPEAK DLIM DSTART DRESTARTED DRC <<<"$(python3 - "$P" \
      "$(docker inspect main-dagster-1 -f '{{.HostConfig.Memory}}')" \
      "$(docker inspect main-dagster-1 -f '{{.State.StartedAt}}')" \
      "$(docker inspect main-dagster-1 -f '{{.RestartCount}}')" "$WINDOW_H" <<'PY'
import sys, time, datetime as dt
p, lim, st, rc, w = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], float(sys.argv[5])
peak = int(open(p + '/memory.peak').read())
iso = st.replace('Z', '+00:00')
try:
    started = dt.datetime.fromisoformat(iso[:26] + '+00:00' if '.' in iso else iso)
except ValueError:
    started = dt.datetime.fromisoformat(iso[:19] + '+00:00')
local = started.astimezone()
in_window = (time.time() - started.timestamp()) < w * 3600
# ⚠️ 时间戳里**不能有空格** —— 外层是 `read -r A B C D E`,按空白拆字段,
# 带空格的时间戳会把后面所有字段挤位(实测:DRESTARTED 拿到时间串而非 yes/no,
# 于是「窗内重启」分支永远不触发,又退回「把 0.77G 当夜跑峰值」的老毛病)。
print(f"{peak/2**30:.2f} {lim/2**30:.2f} {local.strftime('%m-%dT%H:%M:%S%z')} "
      f"{'yes' if in_window else 'no'} {rc}")
PY
)"
  note "  dagster memory.peak = ${DPEAK} GiB / 限额 ${DLIM} GiB"
  note "  容器启于 ${DSTART}(本地时区)  重启次数 ${DRC}"
  if [ "$DRESTARTED" = "yes" ]; then
    # 区分两种「窗内启动」:RestartCount>0 = docker 自动拉起(崩溃/被杀),是事故;
    # RestartCount=0 = 人为 recreate(部署/改配置),不是事故。两者对 peak 的影响相同
    # (都归零 ⇒ 不可验证),但对「是否要去查根因」的指示完全相反 —— 混为一谈会让
    # 一次正常部署看起来像一次崩溃。
    if [ "${DRC:-0}" -gt 0 ]; then
      bad "容器在核验窗内被自动拉起过(RestartCount=${DRC},即崩溃/被杀)—— memory.peak 已归零,**本窗口峰值不可验证**;且这会打死全部在飞 run worker,需查根因"
    else
      bad "容器在核验窗内重建过(RestartCount=0,属人为部署而非崩溃)—— memory.peak 已归零,**本窗口峰值不可验证**"
    fi
    note "      → 这一夜的内存结论只能靠第 3 节的 OOM 归因来判,不能看 peak"
  else
    # 目标 6.7G(6 pull×0.73 + extract 1.76 + 固定 0.58);留到 7.4 作为告警线
    python3 -c "import sys; sys.exit(0 if float('$DPEAK') < 7.4 else 1)" \
      && good "峰值 ${DPEAK}G < 7.4G,未逼近 8G 硬限(预算模型:约 6.7G)" \
      || bad "峰值 ${DPEAK}G 已逼近 8G 硬限 —— 把 pull 限额从 6 再降一档"
  fi
fi
SPEAK=$(python3 -c "print(f\"{int(open('/sys/fs/cgroup/docker.slice/memory.peak').read())/2**30:.2f}\")" 2>/dev/null)
SHIGH=$(python3 -c "print(f\"{int(open('/sys/fs/cgroup/docker.slice/memory.high').read())/2**30:.2f}\")" 2>/dev/null)
SCUR=$(python3 -c "print(f\"{int(open('/sys/fs/cgroup/docker.slice/memory.current').read())/2**30:.2f}\")" 2>/dev/null)
# ⚠️ docker.slice 的 memory.peak 是**开机以来**的累计,清零要 root,所以它会长期停在
# 历史最高水位(2026-07-30 那次事故已把它顶到 24G=软闸)。因此这里只作参考,
# 判定看 memory.current 与软闸的距离 —— 那才反映当下水位。
note "  docker.slice current = ${SCUR:-?} GiB / 软闸 ${SHIGH:-?} GiB"
note "                peak = ${SPEAK:-?} GiB(开机以来累计,需 root 才能清零 → 仅参考)"
if [ -n "$SCUR" ] && [ -n "$SHIGH" ]; then
  python3 -c "import sys; sys.exit(0 if float('$SCUR') < float('$SHIGH')*0.9 else 1)" \
    && good "当下聚合水位 ${SCUR}G,距软闸尚有余量" \
    || bad "当下聚合水位 ${SCUR}G 已达软闸 ${SHIGH}G 的 90% —— 全栈开始承受回收压力"
fi

# ── 3. 内核 OOM ────────────────────────────────────────────────────────────────
note ""
note "[3] 内核 memcg OOM(窗口内,**按 cgroup 归因**)"
# ⚠️ 这一节此前只做 `grep -c 'Memory cgroup out of memory'` —— 全主机不分容器地数。
# 2026-07-31 因此把 glmworker 的 5 次已知 OOM 自愈循环算到了 dagster 头上,
# 报出「窗口内有 5 次 memcg OOM」并据此判 FAIL,而 dagster 自己的 cgroup 当晚**零 OOM**。
# 结论方向被彻底带反。memcg OOM 必须按 oom_memcg= 的 cgroup 归因,不能按主机汇总。
#
# 归因方法:内核每条 oom-kill 行都带 `oom_memcg=/docker.slice/docker-<64位id>`。
# 能 docker inspect 到的直接用容器名;查不到的说明该容器**已被重建**(rebuild 会换 id),
# 这时不猜、如实标为「已销毁」,并用被杀进程名+RSS 给出线索:
# 各 worker 跑的是 `xar` CLI(RSS 撞各自 mem_limit,如 glmworker≈5G),
# 而 dagster 的 run worker 是 `python3.12`(RSS 约 0.7G pull / 1.8G extract)。
# ⚠️ 不要写成 `journalctl ... | python3 - <<'PY'`:heredoc **本身就是 stdin**,
# 会盖掉管道,python 里 sys.stdin 读到的是脚本自己、拿不到日志(实测 total 恒为 0)。
# 与 2026-07-30 那次 `docker exec` 漏 -i 是同一类错误。改由 python 自己调 journalctl。
OOMJSON=$(python3 - "$CID" "$WINDOW_H" <<'PY'
import sys, re, json, collections, subprocess
cid = (sys.argv[1] or "")[:12]
try:
    _j = subprocess.run(["journalctl", "-k", "--since", f"-{sys.argv[2]} hours", "--no-pager"],
                        capture_output=True, text=True, timeout=180).stdout.splitlines()
except Exception:
    _j = []
rx_cg   = re.compile(r'oom_memcg=/docker\.slice/docker-([0-9a-f]{12})')
rx_kill = re.compile(r'Memory cgroup out of memory: Killed process \d+ \((\S+)\).*?anon-rss:(\d+)kB')
per = collections.defaultdict(lambda: {"n": 0, "procs": collections.Counter()})
cur_cg = None
for line in _j:
    m = rx_cg.search(line)
    if m:
        cur_cg = m.group(1)
        per[cur_cg]["n"] += 0          # 建档
    k = rx_kill.search(line)
    if k:
        cg = cur_cg or "unknown"
        per[cg]["n"] += 1
        per[cg]["procs"][f"{k.group(1)}:{int(k.group(2))/1048576:.2f}G"] += 1
out = {"dagster": per.get(cid, {}).get("n", 0), "total": sum(v["n"] for v in per.values()),
       "by_cg": {c: {"n": v["n"], "procs": dict(v["procs"])} for c, v in per.items() if v["n"]}}
print(json.dumps(out))
PY
)
if [ -z "$OOMJSON" ]; then
  note "  (读不到内核日志,跳过)"
else
  python3 - "$OOMJSON" <<'PY'
import json, subprocess, sys
d = json.loads(sys.argv[1])
print(f"  窗口内全主机 memcg OOM 共 {d['total']} 次;其中 dagster {d['dagster']} 次")
for cg, v in sorted(d["by_cg"].items(), key=lambda x: -x[1]["n"]):
    try:
        name = subprocess.run(["docker", "inspect", "--format", "{{.Name}}", cg],
                              capture_output=True, text=True, timeout=20).stdout.strip() or None
    except Exception:
        name = None
    who = name or "已销毁(容器被重建过)"
    print(f"    {cg} → {who}  {v['n']} 次  {v['procs']}")
PY
  OOMDAG=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['dagster'])" "$OOMJSON")
  OOMALL=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['total'])" "$OOMJSON")
  # 判定只认 dagster 自己的 cgroup —— 这一节核验的是 dagster 内存治理。
  [ "${OOMDAG:-0}" -eq 0 ] && good "dagster cgroup 零 memcg OOM(内存治理成立)" \
                           || bad "dagster cgroup 有 ${OOMDAG} 次 memcg OOM —— 6 并发仍偏高,把 pull 限额降到 5"
  # 其它容器的 OOM 不判 dagster 的账,但要说出来:glmworker 的 5G 自愈循环是已知长期项。
  if [ "${OOMALL:-0}" -gt "${OOMDAG:-0}" ]; then
    note "  ℹ 另有 $((OOMALL - OOMDAG)) 次 OOM 属于其它容器(如 glmworker 的已知 5G 自愈循环),不计入本项判定"
  fi
fi

# ── 4. 僵尸进程(init: true 是否在干活)────────────────────────────────────────
note ""
note "[4] dagster 容器僵尸进程"
Z=$(docker exec main-dagster-1 python3 -c "
import glob
z=t=0
for d in glob.glob('/proc/[0-9]*'):
    try:
        st=open(d+'/stat').read(); t+=1
        if st.rsplit(')',1)[1].split()[0]=='Z': z+=1
    except Exception: pass
print(f'{z} {t}')" 2>/dev/null)
if [ -n "$Z" ]; then
  set -- $Z
  note "  僵尸 $1 / 进程 $2"
  [ "$1" -eq 0 ] && good "init: true 在回收孤儿" || bad "$1 个僵尸 —— init reaper 没生效?"
fi

# ── 5. 监控自己怎么看 ──────────────────────────────────────────────────────────
note ""
note "[5] 监控面板判态"
SNAP=$(curl -s --max-time 20 localhost:8000/api/ops/monitor 2>/dev/null)
if [ -z "$SNAP" ]; then
  bad "监控接口不可达"
else
  python3 - "$SNAP" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
print("  summary:", d.get("summary"))
for t in d.get("tasks", []):
    if t["group"] in ("dagster",) or t["state"] in ("down", "stale"):
        hb = (t.get("detail") or {}).get("hb") or {}
        r = hb.get("reason") or (t.get("detail") or {}).get("reason") or ""
        print(f"  {t['state']:6} {t['id']:24} {r}")
PY
  DAG=$(python3 -c "
import json,sys
d=json.loads(sys.argv[1])
t=[x for x in d.get('tasks',[]) if x['id']=='dagster.runs']
print(t[0]['state'] if t else 'missing')" "$SNAP")
  # 监控判态要**相对第 1 节的实况**来评价,不能无条件要求 ok:
  # 若这一夜真的有失败,dagster.runs=down 恰恰证明检测在干活(那正是 2026-07-30 补的漏洞);
  # 真正的问题是「第 1 节全绿而监控说 down」(监控误报)或「第 1 节有失败而监控说 ok」(漏报)。
  RUN_CLEAN=1
  for f in "${FAILS[@]:-}"; do case "$f" in *分片*|*QUEUED*|*runs.db*) RUN_CLEAN=0;; esac; done
  if [ "$RUN_CLEAN" = "1" ]; then
    [ "$DAG" = "ok" ] && good "dagster.runs = ok,与实况一致" \
                      || bad "实况全绿但监控报 $DAG —— 监控误报,查阈值"
  else
    [ "$DAG" = "ok" ] && bad "实况有失败而监控仍报 ok —— **漏报**,正是 2026-07-30 那个漏洞复发" \
                      || good "dagster.runs = $DAG,如实反映了本窗口的失败(检测在干活)"
  fi
fi

# ── 结论 ───────────────────────────────────────────────────────────────────────
note ""
if [ ${#FAILS[@]} -eq 0 ]; then
  # 峰值不可验证时不得报数字 —— 否则「全绿」里混着一个没测到的指标。
  if [ "${DRESTARTED:-no}" = "yes" ]; then PEAKTXT="峰值不可验证(容器窗内重启过)"; else PEAKTXT="dagster 峰值 ${DPEAK:-?}G/8G"; fi
  MSG="[XAR 夜检] 全绿:并发≤7、8/8 分片成功、${PEAKTXT}、dagster cgroup 零 OOM、零僵尸"
  note "===== 结论:PASS ====="; note "$MSG"
else
  MSG="[XAR 夜检] ${#FAILS[@]} 项不通过:$(printf '%s; ' "${FAILS[@]}")"
  note "===== 结论:FAIL(${#FAILS[@]} 项)====="
  printf '  · %s\n' "${FAILS[@]}"
fi

if [ "$NOTIFY" = "1" ]; then
  ENVF="$REPO/.env"
  tok=$(grep -E '^BOT_HTTP_API=' "$ENVF" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'\''\r')
  chat=$(grep -E '^XAR_MONITOR_TELEGRAM_CHAT=' "$ENVF" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'\''\r')
  if [ -n "$tok" ] && [ -n "$chat" ]; then
    curl -sS --max-time 15 -o /dev/null "https://api.telegram.org/bot${tok}/sendMessage" \
      --data-urlencode "chat_id=${chat}" --data-urlencode "text=${MSG}" \
      && note "(已推 Telegram)"
  else
    note "(未推:BOT_HTTP_API / XAR_MONITOR_TELEGRAM_CHAT 未配)"
  fi
fi
[ ${#FAILS[@]} -eq 0 ]
