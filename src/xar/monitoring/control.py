"""worker 侧的软重启协议 —— **零 docker socket**。

原理:所有 worker 服务在 compose 里都是 `restart: unless-stopped`,所以进程干净退出后
docker 会立刻拉起。于是「重启 worker」= 在 kvstate 放一个标记,worker 循环每轮读一下,
看到就 `sys.exit(0)`。

为什么不挂 docker socket:那等于把宿主 root 权限交给 app 容器,而且无法按操作收窄权限。
软重启唯一的盲区是「进程卡得连循环检查都到不了」—— 而那恰恰是监控会上报
「需人工 docker restart」的情形,面板按钮的 tooltip 就写这句。

标记与**进程启动时刻**比较而不是「用完即删」:删除会与多 worker 并发、与重启期间的
再次点击产生竞态;比时间则天然一次性 —— 只有比我这个进程更新的请求才对我有效。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..logging import get_logger

log = get_logger("xar.monitoring.control")

RESTART_KEY = "control_restart"     # {service: iso_ts}


def _parse(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def request_restart(service: str) -> dict:
    from ..storage.kvstate import get_state, save_state
    st = get_state(RESTART_KEY)
    st[service] = datetime.now(timezone.utc).isoformat()
    save_state(RESTART_KEY, st)
    return {"service": service, "requestedAt": st[service]}


def pending(service: str, *, started_at: datetime) -> bool:
    """是否有针对本服务、且**晚于本进程启动**的重启请求。"""
    from ..storage.kvstate import get_state
    ts = _parse((get_state(RESTART_KEY) or {}).get(service))
    return bool(ts and ts > started_at)


def exit_if_requested(service: str, *, started_at: datetime) -> None:
    """worker 循环顶部调用。命中即干净退出,由 docker 的 restart 策略拉起。
    任何异常都吞掉 —— 重启协议本身绝不能成为 worker 崩溃的新来源。"""
    try:
        if pending(service, started_at=started_at):
            log.info("%s: restart requested via monitor — exiting cleanly for docker restart",
                     service)
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("restart-flag check failed (ignored): %s", str(e)[:120])
