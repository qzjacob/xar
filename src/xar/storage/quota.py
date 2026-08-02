"""沪日额度账本 —— 按国内日历日重置额度的源(alphapai / aifinmarket)的**持久**状态。

## 为什么存在

此前这两个源的额度状态是 provider 模块里的**进程内变量**:
`alphapai._QUOTA`(203=当日锁死)、`aifinmarket._usage/_cooldown`(每席位日帽)。
后果有二,都是真实事故:

1. **重启即失忆**。glmworker 一天重启 14 次,每次都忘掉「今天已经 203」→ 抓取链
   重新把 alphapai 排到链首、继续打已耗尽的付费 API,直到再吃一串 203 才重新学会。
   `fetch_chain` 的 `drain_first`(榨干才交棒)整个语义都建立在这个变量上 ——
   一个活不过重启的变量。
2. **跨进程不可见**。alphapai/aifinmarket 有两个调用进程(glmworker 抓取链 +
   dagster 夜批分片),两份进程内存互不知情,「每账号每日 N 次」的帽根本管不住。

## 设计要点

- **日界进主键**(照 `api_spend` 的 month-keyed):换日即换行 ⇒ 没有「重置」这个动作,
  也就没有重置竞态,`_quota_roll()`/`_reset_if_new_day()` 两套换日代码可以整个删掉。
- **沪日由数据库算**(`(now() AT TIME ZONE 'Asia/Shanghai')::date`):五个容器对
  「今天」的判定由同一个时钟给出,容器时区/时钟漂移不再能造成分歧。
- **每次调用直写,不做周期落库**。写量很小(alphapai 20s 节流 ⇒ ≤3 次/分;
  aifinmarket 0.3s 节流),单行 UPSERT 叠在 100ms+ 的 HTTPS 调用上可忽略。
  周期 flush 反而会**丢尾巴** —— 重启忘掉最后 N 次调用,正是本模块要修的 bug 的缩小版;
  且双进程并发下,进程本地计数再合并没有正确语义,DB 端 `calls + EXCLUDED.calls` 才有。
- **读 fail-open,写尽力而为**。额度门是**优化信号**(省调用),不是预算帽(省钱)——
  DB 抖动时必须退回调用方的进程内镜像继续抓,绝不能因为读不到额度状态就把抓取链停掉。
  这与 `api_spend` 的 fail-closed 方向相反,是刻意的:那边拦的是花钱,这边拦的是浪费。
"""
from __future__ import annotations

from ..logging import get_logger
from . import db

log = get_logger("xar.storage.quota")

# 沪日表达式:所有读写共用同一份,保证「今天」只有一个定义。
_CN_TODAY = "(now() AT TIME ZONE 'Asia/Shanghai')::date"

_DDL = (
    "CREATE TABLE IF NOT EXISTS provider_quota ("
    "provider TEXT NOT NULL, seat TEXT NOT NULL DEFAULT '-', cn_date DATE NOT NULL, "
    "calls BIGINT NOT NULL DEFAULT 0, exhausted BOOLEAN NOT NULL DEFAULT false, "
    "backoff_until TIMESTAMPTZ, last_code TEXT, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "PRIMARY KEY (provider, seat, cn_date))")
_ensured = False


def _ensure() -> None:
    """防御式建表(进程内一次)。schema.sql 才是权威,这里只是**消解部署顺序依赖** ——
    滚动重建时 glmworker 可能先于 app 的 `xar init` 起来,不该因为表还没建而炸。"""
    global _ensured
    if not _ensured:
        db.execute(_DDL)
        _ensured = True


def bump(provider: str, seat: str = "-", n: int = 1) -> dict:
    """记一次调用,**返回写入后的权威行**(calls/exhausted/backoff_until)。

    用 RETURNING 一次往返拿到写后值,调用方据此判日帽 —— 这样「两个进程合计不超帽」
    由数据库仲裁,而不是各自数各自的(那种数法在双进程下必然超发)。
    """
    _ensure()
    rows = db.query(
        "INSERT INTO provider_quota(provider, seat, cn_date, calls) "
        f"VALUES (%s, %s, {_CN_TODAY}, %s) "
        "ON CONFLICT (provider, seat, cn_date) DO UPDATE SET "
        "  calls = provider_quota.calls + EXCLUDED.calls, updated_at = now() "
        "RETURNING calls, exhausted, backoff_until",
        (provider, seat, int(n)))
    return dict(rows[0]) if rows else {"calls": 0, "exhausted": False, "backoff_until": None}


def mark_exhausted(provider: str, seat: str = "-", code: str | None = None) -> None:
    """当日额度耗尽(alphapai 203 / aifinmarket 席位额度错)—— 锁死到沪日换日为止。"""
    _ensure()
    db.execute(
        "INSERT INTO provider_quota(provider, seat, cn_date, exhausted, last_code) "
        f"VALUES (%s, %s, {_CN_TODAY}, true, %s) "
        "ON CONFLICT (provider, seat, cn_date) DO UPDATE SET "
        "  exhausted = true, last_code = EXCLUDED.last_code, updated_at = now()",
        (provider, seat, (code or "")[:160] or None))


def set_backoff(provider: str, seat: str = "-", *, seconds: float,
                code: str | None = None) -> None:
    """短退避(alphapai 204 系统繁忙 / 42900 短窗限流)——**不是**当日耗尽,到期自动失效。"""
    _ensure()
    db.execute(
        "INSERT INTO provider_quota(provider, seat, cn_date, backoff_until, last_code) "
        f"VALUES (%s, %s, {_CN_TODAY}, now() + make_interval(secs => %s), %s) "
        "ON CONFLICT (provider, seat, cn_date) DO UPDATE SET "
        "  backoff_until = EXCLUDED.backoff_until, last_code = EXCLUDED.last_code, "
        "  updated_at = now()",
        (provider, seat, float(seconds), (code or "")[:160] or None))


def snapshot(provider: str) -> dict[str, dict]:
    """该源**今日**全部席位的状态 → {seat: {calls, exhausted, backing_off}}。

    `backing_off` 在 SQL 端与 now() 比较,免得调用方各自处理时区。
    读不到(表不存在/DB 抖动)返回空 dict —— 调用方据此 fail-open 回自己的进程内镜像。
    """
    try:
        _ensure()
        rows = db.query(
            "SELECT seat, calls, exhausted, "
            "       (backoff_until IS NOT NULL AND backoff_until > now()) AS backing_off "
            "  FROM provider_quota "
            f" WHERE provider = %s AND cn_date = {_CN_TODAY}",
            (provider,))
    except Exception as e:  # noqa: BLE001 — 额度门是优化信号,读不到必须放行,不能停摆抓取链
        log.warning("provider_quota snapshot(%s) 读取失败,调用方将回落进程内镜像: %s",
                    provider, str(e)[:120])
        return {}
    return {r["seat"]: {"calls": int(r["calls"]), "exhausted": bool(r["exhausted"]),
                        "backing_off": bool(r["backing_off"])} for r in rows}
