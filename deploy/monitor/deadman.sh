#!/usr/bin/env bash
# 监控的监控 —— 带外死人开关(2026-07-29 审计产物)。
#
# 为什么需要它:巡检线程跑在 app 容器里,Telegram 推送也从那儿发出。所以「app 整个挂了」
# 恰恰是**报警会跟着一起死**的那种情况 —— 而那也正是最需要报警的情况。
# 本脚本由**主机 cron** 独立运行,只用 bash + curl,不依赖 XAR 的任何代码路径。
#
# 判定两件事:
#   ① /api/ops/monitor/summary 是否可达(app 活着、DB 通、路由在);
#   ② 上次巡检是否在 STALE_MIN 分钟内(app 活着但巡检线程死了 —— 静默失效最阴险的一种)。
#
# 装法(每 10 分钟一次):
#   crontab -e
#   */10 * * * * /home/jake-ma/Project/XAR/main/deploy/monitor/deadman.sh >> ~/monitoring/deadman.log 2>&1
#
# 手测:  DEADMAN_TEST=1 deploy/monitor/deadman.sh    # 强制走一次推送路径
set -uo pipefail

URL="${XAR_MONITOR_URL:-http://localhost:8000/api/ops/monitor/summary}"
ENV_FILE="${XAR_ENV_FILE:-/home/jake-ma/Project/XAR/main/.env}"
STALE_MIN="${DEADMAN_STALE_MIN:-15}"
STATE="${DEADMAN_STATE:-$HOME/monitoring/.deadman_state}"
# 自身也要节流:app 长时间宕机时不该每 10 分钟轰一条。
RENOTIFY_H="${DEADMAN_RENOTIFY_H:-6}"

mkdir -p "$(dirname "$STATE")"

# 只从 .env 取这两个值,不 source 整个文件(里面全是密钥,source 会污染环境)。
get_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'\r'; }
TOKEN="$(get_env BOT_HTTP_API)"
CHAT="$(get_env XAR_MONITOR_TELEGRAM_CHAT)"
[ -n "$CHAT" ] || CHAT="$(get_env TELEGRAM_ALLOWED_CHATS | cut -d, -f1)"

notify() {   # $1 = 文本
  local now; now=$(date +%s)
  local last=0; [ -f "$STATE" ] && last=$(cat "$STATE" 2>/dev/null || echo 0)
  if [ $((now - last)) -lt $((RENOTIFY_H * 3600)) ]; then
    echo "$(date -Is) suppressed (last notified $(( (now-last)/60 ))m ago): $1"
    return 0
  fi
  echo "$(date -Is) ALERT: $1"
  if [ -n "$TOKEN" ] && [ -n "$CHAT" ]; then
    curl -sS --max-time 15 -o /dev/null \
      "https://api.telegram.org/bot${TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${CHAT}" \
      --data-urlencode "text=[XAR deadman] $1" \
      && echo "$now" > "$STATE"
  else
    echo "$(date -Is) WARN: BOT_HTTP_API / chat id 未配置,无法推送"
  fi
}

if [ "${DEADMAN_TEST:-0}" = "1" ]; then
  rm -f "$STATE"; notify "测试消息:死人开关工作正常($(hostname))"; exit 0
fi

# 把 HTTP 状态码附在响应尾部,便于区分「连不上」与「连上了但接口 404/500」——
# 后者通常意味着镜像里还没有监控代码(未 rebuild),报错要说得准,否则会被当成宕机去查错方向。
RAW="$(curl -sS --max-time 20 -w '\n%{http_code}' -H 'Accept: application/json' "$URL" 2>/dev/null)"
CODE="$(printf '%s' "$RAW" | tail -1)"
BODY="$(printf '%s' "$RAW" | sed '$d')"
if [ -z "$CODE" ] || [ "$CODE" = "000" ]; then
  notify "监控接口不可达($URL)—— app 容器可能已停,请查 docker ps / docker logs main-app-1"
  exit 0
fi
if [ "$CODE" != "200" ]; then
  notify "监控接口返回 HTTP $CODE($URL)—— 若为 404,通常是镜像尚未 rebuild、监控路由还不存在"
  exit 0
fi

# 不引入 jq 依赖(主机不一定装):用 grep/sed 抠 lastSweepAt。
LAST="$(printf '%s' "$BODY" | sed -n 's/.*"lastSweepAt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
if [ -z "$LAST" ] || printf '%s' "$BODY" | grep -q '"lastSweepAt"[[:space:]]*:[[:space:]]*null'; then
  notify "接口可达但从未巡检过(lastSweepAt=null)—— 巡检线程未启动?查 XAR_MONITOR_ENABLED"
  exit 0
fi

LAST_EPOCH="$(date -d "$LAST" +%s 2>/dev/null || echo 0)"
NOW_EPOCH="$(date +%s)"
AGE_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))
if [ "$LAST_EPOCH" -eq 0 ]; then
  notify "无法解析 lastSweepAt=$LAST"
elif [ "$AGE_MIN" -gt "$STALE_MIN" ]; then
  notify "巡检已停 ${AGE_MIN} 分钟(阈值 ${STALE_MIN}m)—— app 活着但监控线程可能已死"
else
  # 恢复:清掉节流状态,下次真出事能立刻发声。
  [ -f "$STATE" ] && { rm -f "$STATE"; echo "$(date -Is) recovered — sweep age ${AGE_MIN}m"; }
  CRIT="$(printf '%s' "$BODY" | sed -n 's/.*"openCritical"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p')"
  echo "$(date -Is) ok — sweep age ${AGE_MIN}m, openCritical=${CRIT:-?}"
fi
