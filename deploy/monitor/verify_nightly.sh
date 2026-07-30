#!/usr/bin/env bash
# 夜间 dagster 调度 + 内存治理效果核验(2026-07-30 起)。
#
# 为什么是本地脚本而不是云端定时 agent:要看的东西全在这台机器上 —— docker、
# dagster 容器里的 runs.db、cgroup 的 memory.peak、内核 OOM 日志、localhost:8000。
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
# 注意 -i:没有它 docker exec 不接 stdin,`python3 -` 会读到空脚本、静默什么都不干。
RUNS=$(docker exec -i main-dagster-1 python3 - "$WINDOW_H" <<'PY' 2>/dev/null
import sqlite3, sys, json
w = sys.argv[1]
c = sqlite3.connect('file:/dagster/history/runs.db?mode=ro', uri=True)
q = ("SELECT pipeline_name, status, partition, start_time, end_time FROM runs "
     f"WHERE create_timestamp > datetime('now','-{w} hours')")
rows = list(c.execute(q))
out = {"total": len(rows), "by_status": {}, "shards_started": 0, "shards_ok": 0,
       "extract": [], "never_started": 0, "max_overlap": 0}
iv = []
for job, st, part, s, e in rows:
    out["by_status"][st] = out["by_status"].get(st, 0) + 1
    if s is None:
        out["never_started"] += 1
    else:
        iv.append((s, e or s))
    if job == "pull_shard_job":
        if s is not None: out["shards_started"] += 1
        if st == "SUCCESS": out["shards_ok"] += 1
    if job == "extract_all_job":
        out["extract"].append(st)
# 峰值并发:扫描区间端点
pts = sorted([(s, 1) for s, _ in iv] + [(e, -1) for _, e in iv])
cur = 0
for _, d in pts:
    cur += d
    out["max_overlap"] = max(out["max_overlap"], cur)
print(json.dumps(out))
PY
)
if [ -z "$RUNS" ]; then
  bad "读不到 runs.db(dagster 容器在吗?)"
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
                    || bad "只有 $SHOK/8 个分片成功 —— 查失败原因(下面第 3 节看 OOM)"
  [ "$NEVER" -eq 0 ] && good "没有永久卡在 QUEUED 的 run" \
                     || bad "$NEVER 个 run 从未启动"
fi

# ── 2. 内存:dagster 容器峰值 + docker.slice 聚合 ────────────────────────────────
note ""
note "[2] 内存治理"
CID=$(docker inspect main-dagster-1 -f '{{.Id}}' 2>/dev/null)
if [ -n "$CID" ]; then
  P=/sys/fs/cgroup/docker.slice/docker-$CID.scope
  read -r DPEAK DLIM DSTART <<<"$(python3 - "$P" "$(docker inspect main-dagster-1 -f '{{.HostConfig.Memory}}')" \
      "$(docker inspect main-dagster-1 -f '{{.State.StartedAt}}')" <<'PY'
import sys
p, lim, st = sys.argv[1], int(sys.argv[2]), sys.argv[3]
peak = int(open(p + '/memory.peak').read())
print(f"{peak/2**30:.2f} {lim/2**30:.2f} {st[:19]}")
PY
)"
  note "  dagster memory.peak = ${DPEAK} GiB / 限额 ${DLIM} GiB   (容器启于 ${DSTART})"
  # 目标 6.7G(6 pull×0.73 + extract 1.76 + 固定 0.58);留到 7.4 作为告警线
  python3 -c "import sys; sys.exit(0 if float('$DPEAK') < 7.4 else 1)" \
    && good "峰值 ${DPEAK}G < 7.4G,未逼近 8G 硬限(预算模型:约 6.7G)" \
    || bad "峰值 ${DPEAK}G 已逼近 8G 硬限 —— 把 pull 限额从 6 再降一档"
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
note "[3] 内核 memcg OOM(窗口内)"
OOM=$(journalctl --since "-${WINDOW_H} hours" 2>/dev/null | grep -ac 'Memory cgroup out of memory' || true)
OOM=${OOM:-0}
[ "$OOM" -eq 0 ] && good "窗口内零 memcg OOM" || {
  bad "窗口内有 $OOM 次 memcg OOM"
  journalctl --since "-${WINDOW_H} hours" 2>/dev/null \
    | grep -a 'Memory cgroup out of memory' | tail -4 | sed 's/^/      /'
}

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
  MSG="[XAR 夜检] 全绿:并发≤7、8/8 分片成功、dagster 峰值 ${DPEAK:-?}G/8G、零 OOM、零僵尸"
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
