"""云端订阅并行池:GLM-5.2 / Minimax-M3 / Kimi-K3 三订阅并行跑重任务直到各自额度耗尽。

现状(见路由探查):路由是严格串行 `for spec in chain`,thesis 又被 GLM 独钉 —— 只用一家订阅、
另两份订阅额度全闲置。本模块把工作项**分发到当前可用的多个订阅 provider 并发跑**:每项在
`llm.pinned(provider_pin)` 内执行(pinned 是 contextvar、逐线程安全;必须在 worker 线程内钉扎,
因 contextvars 不自动传入线程池)。每 provider(zhipu/minimax/moonshot)独立 5h 额度窗:触限即
冷却,按 `subpool_probe_seconds` 节拍周期性探针探测恢复(5h 窗刷新后探针成功即复用)。目标:
三份订阅计划的 token 额度都被吃满,而非只用 GLM 一家。
"""
from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone

from ..config import get_settings
from ..logging import get_logger
from ..models import llm
from ..models import registry as reg
from ..storage.kvstate import get_state, save_state

log = get_logger("xar.subpool")

STATE_KEY = "sub_quota"
_MAX_PROVIDER_FAILS = 3    # 连续失败(额度/鉴权失效/持续返空)达此数 → 冷却该 provider 退出,不再抢单
# 额度/限流标记(与 glm_worker._QUOTA_MARKERS 同族;刻意不含 exceed/429 之类 —— 预算帽≠订阅额度)。
_QUOTA_MARKERS = ("余额不足", "无可用资源包", "rate limit", "ratelimit", "too many requests",
                  "quota", "额度", "配额", "限额", "超限", "insufficient")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_quota_error(e: Exception) -> bool:
    if type(e).__name__ == "BudgetExceeded":     # token 预算帽,不是订阅额度耗尽
        return False
    if type(e).__name__ == "RateLimitError":     # litellm 限流类型
        return True
    m = str(e).lower()
    return any(k in m or k in str(e) for k in _QUOTA_MARKERS)


def provider_pins() -> list[tuple[str, tuple[str, ...]]]:
    """解析 subpool_pins → [(provider_key, pin_tuple)]。provider_key = 首模型的 registry provider
    (zhipu/minimax/moonshot),用作独立额度键;pin_tuple 是该 provider 的钉扎链(含同家回退)。"""
    out: list[tuple[str, tuple[str, ...]]] = []
    for group in (get_settings().subpool_pins or "").split("|"):
        ids = [x.strip() for x in group.split(",") if x.strip()]
        if not ids:
            continue
        spec = reg.get(ids[0])
        prov = spec.provider if spec else ids[0]
        out.append((prov, tuple(ids)))
    return out


def status() -> dict:
    return get_state(STATE_KEY)


def _mark(prov: str, *, ok: bool, reason: str = "") -> None:
    st = get_state(STATE_KEY)
    p = st.setdefault(prov, {})
    if ok:
        if p.get("status") == "exhausted":
            p["resumed_at"] = _now()
            p["resume_count"] = int(p.get("resume_count", 0)) + 1
            log.info("subpool provider %s quota RECOVERED", prov)
        p["status"] = "ok"
    else:
        if p.get("status") != "exhausted":
            p["status"] = "exhausted"
            p["exhausted_at"] = _now()
            p["exhaust_count"] = int(p.get("exhaust_count", 0)) + 1
            p["last_reason"] = reason[:160]
            log.warning("subpool provider %s quota EXHAUSTED — cooling (%s)", prov, reason[:100])
    p["last_probe_at"] = _now()
    save_state(STATE_KEY, st)


def probe(prov: str, pin: tuple[str, ...]) -> bool:
    """探针(订阅零成本):该 provider 额度是否恢复。max_tokens=256 够越过 reasoning 吐可见内容。"""
    try:
        with llm.pinned(pin):
            llm.complete("Reply with exactly: ok", task="adhoc_fast", node="subpool_probe",
                         max_tokens=256)
        _mark(prov, ok=True)
        return True
    except Exception as e:  # noqa: BLE001
        if is_quota_error(e):
            _mark(prov, ok=False, reason=str(e))
        else:
            log.warning("subpool probe %s non-quota failure: %s", prov, str(e)[:120])
        return False


def available_pins() -> list[tuple[str, tuple[str, ...]]]:
    """当前可用的 (provider, pin):ok 的直接用;exhausted 的若 probe 到期则探测,恢复则纳入。"""
    st = get_state(STATE_KEY)
    probe_s = get_settings().subpool_probe_seconds
    now = datetime.now(timezone.utc)
    out: list[tuple[str, tuple[str, ...]]] = []
    for prov, pin in provider_pins():
        p = st.get(prov, {})
        if p.get("status") != "exhausted":
            out.append((prov, pin))
            continue
        last = p.get("last_probe_at")
        due = True
        if last:
            try:
                due = (now - datetime.fromisoformat(last)).total_seconds() >= probe_s
            except ValueError:
                due = True
        if due and probe(prov, pin):
            out.append((prov, pin))
    return out


def run_parallel(items: list, fn) -> list:
    """把 items 分发到当前可用 provider 并发跑 fn(item);每项在 llm.pinned(provider_pin) 内执行。
    某 provider 触限即冷却并停(手上项 requeue 让其他 provider 接手),其余 provider 继续。
    返回 [(item, result|None)];未及处理(全 provider 冷却)的项 result=None,调用方下轮重试。

    并发模型:每个可用 provider 一个 worker 线程,从共享队列取项;这样三订阅真正并行、各自
    吃自己的 5h 额度,直到耗尽。"""
    items = list(items)
    pins = available_pins()
    if not pins or not items:
        return [(it, None) for it in items]
    q: queue.Queue = queue.Queue()
    for i, it in enumerate(items):
        q.put((i, it))
    results: list = [None] * len(items)
    rlock = threading.Lock()

    def worker(prov: str, pin: tuple[str, ...]) -> None:
        fails = 0
        while True:
            try:
                i, it = q.get_nowait()
            except queue.Empty:
                return
            ok = False
            try:
                with llm.pinned(pin):           # 必须在 worker 线程内钉扎(contextvar 不入池)
                    res = fn(it)
                if res is not None:             # fn 返回 None = 该 provider 没产出(返空/被拒),记失败
                    with rlock:
                        results[i] = res
                    ok = True
            except Exception as e:  # noqa: BLE001
                if is_quota_error(e):
                    _mark(prov, ok=False, reason=str(e))
                    q.put((i, it))              # 该项交回队列,其他 provider 接手
                    return                       # 本 provider 触限,退出
                log.warning("subpool %s item exc: %s", prov, str(e)[:120])
            if ok:
                fails = 0
                continue
            # 失败(异常/返 None):交回队列让其他 provider 试;连续失败达阈值(如鉴权失效)→ 冷却退出
            fails += 1
            q.put((i, it))
            if fails >= _MAX_PROVIDER_FAILS:
                _mark(prov, ok=False, reason="repeated failure (auth invalid / empty / rejected)")
                log.warning("subpool %s cooled after %d consecutive failures", prov, fails)
                return

    threads = [threading.Thread(target=worker, args=(prov, pin), daemon=True) for prov, pin in pins]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return list(zip(items, results))
