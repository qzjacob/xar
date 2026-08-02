"""额度状态跨重启存活(2026-08-02)—— 本轮 L0 改造的**核心验收**。

事故:`alphapai._QUOTA` 与 `aifinmarket._usage/_cooldown` 都是进程内模块级变量,
而 glmworker 一天重启 14 次。每次重启:

  · alphapai 忘掉「今天已经 203(当日额度耗尽)」→ 抓取链把它重新排到链首、
    继续打已耗尽的付费 API,直到再吃一串 203 才重新学会。**`fetch_chain` 的
    `drain_first`(榨干才交棒)整个语义都建立在这个活不过重启的变量上。**
  · aifinmarket 每个账号的日帽重新从 0 计 → 可一路超发;`all_seats_exhausted()`
    随之假阴,链以为还有额度、不 fallback。

而且这两个源有**两个调用进程**(glmworker 抓取链 + dagster 夜批分片),两份进程内存
互不知情 —— 「每账号每日 N 次」这个帽从来没真正生效过。

下面每个用例都用「清空进程内状态」来**模拟一次容器重启**,断言谓词仍然记得。
"""
from __future__ import annotations

import pytest

from xar.providers import aifinmarket as af
from xar.providers import alphapai as ap
from xar.storage import db, quota


def _restart_alphapai() -> None:
    """模拟 glmworker 重启:抹掉 alphapai 的全部进程内额度状态与读缓存。"""
    ap._reset_quota_state()
    ap._invalidate()


def _restart_aifin() -> None:
    af._reset_state()
    af._invalidate()


@pytest.fixture()
def _clean(isolated_db):
    quota.snapshot("alphapai")                       # 触发 _ensure()
    db.execute("DELETE FROM provider_quota WHERE provider IN ('alphapai','aifinmarket')")
    _restart_alphapai()
    _restart_aifin()
    yield
    _restart_alphapai()
    _restart_aifin()


# ── alphapai ─────────────────────────────────────────────────────────────────
def test_alphapai_remembers_203_across_restart(_clean):
    """**核心验收**:收到 203 后重启,仍然知道今天已经耗尽。"""
    assert ap.quota_exhausted() is False
    ap._persist_exhausted(203)
    assert ap.quota_exhausted() is True
    _restart_alphapai()                              # ← 容器重启
    assert ap.quota_exhausted() is True, "重启后忘了今天已 203 —— drain_first 会被架空"


def test_alphapai_remembers_backoff_across_restart(_clean):
    ap._persist_backoff(120, 42900)
    assert ap.quota_backing_off() is True
    _restart_alphapai()
    assert ap.quota_backing_off() is True


def test_alphapai_backoff_is_not_exhaustion(_clean):
    """42900/204 是瞬时节流,不得升级成「当日耗尽」—— 混淆这两者正是额度剩在桌上的主因。"""
    ap._persist_backoff(120, 42900)
    _restart_alphapai()
    assert ap.quota_backing_off() is True and ap.quota_exhausted() is False


def test_alphapai_new_day_is_clean(_clean):
    """换日即换行:昨天耗尽不影响今天(靠主键,不靠任何重置代码)。"""
    ap._persist_exhausted(203)
    db.execute("DELETE FROM provider_quota WHERE provider='alphapai'")   # 等价于换到新的一天
    _restart_alphapai()
    assert ap.quota_exhausted() is False


def test_alphapai_fails_open_when_db_unreadable(_clean, monkeypatch):
    """DB 读不到时回落进程内镜像并**放行** —— 额度门是优化信号,不能因它停摆抓取链。"""
    ap._persist_exhausted(203)
    _restart_alphapai()                              # 镜像已空
    monkeypatch.setattr(quota, "snapshot", lambda p: {})
    ap._invalidate()
    assert ap.quota_exhausted() is False, "读不到额度状态时必须放行,而不是当作耗尽卡住"


# ── aifinmarket(多账号)────────────────────────────────────────────────────────
def test_aifinmarket_seat_cap_survives_restart(_clean, monkeypatch):
    """席位日帽跨重启存活 —— 否则重启即清零,可一路超发到供应商真的拒绝。"""
    monkeypatch.setattr(af, "_pool", lambda: ["tokA"])
    # ⚠️ 生产里 aifinmarket_daily_calls_per_account **默认是 0(= 不设帽)**,
    # 所以这里必须显式注入一个帽值,否则测的是「没有帽」而不是「帽跨重启存活」。
    cap = 3
    monkeypatch.setattr(af, "get_settings",
                        lambda: type("S", (), {"aifinmarket_daily_calls_per_account": cap})())
    tid = af._tok_id("tokA")
    for _ in range(cap):
        af._bump_seat(tid)
    assert af.all_seats_exhausted() is True
    _restart_aifin()
    assert af.all_seats_exhausted() is True, "重启后日帽归零 —— 会超发"


def test_aifinmarket_one_exhausted_seat_does_not_block_others(_clean, monkeypatch):
    """多账号语义:**每个**席位都触顶才算耗尽。任一账号还有额度就不准交棒。"""
    monkeypatch.setattr(af, "_pool", lambda: ["tokA", "tokB"])
    af._persist_seat_exhausted(af._tok_id("tokA"), "额度不足")
    _restart_aifin()
    assert af.all_seats_exhausted() is False
    assert af._pick_token() == "tokB", "应轮转到还有额度的账号,而不是整段放弃"


def test_aifinmarket_all_seats_exhausted_then_advances(_clean, monkeypatch):
    monkeypatch.setattr(af, "_pool", lambda: ["tokA", "tokB"])
    for t in ("tokA", "tokB"):
        af._persist_seat_exhausted(af._tok_id(t), "额度不足")
    _restart_aifin()
    assert af.all_seats_exhausted() is True and af._pick_token() is None


def test_aifinmarket_empty_pool_is_exhausted(_clean, monkeypatch):
    monkeypatch.setattr(af, "_pool", lambda: [])
    assert af.all_seats_exhausted() is True


def test_aifinmarket_fails_open_when_db_unreadable(_clean, monkeypatch):
    monkeypatch.setattr(af, "_pool", lambda: ["tokA"])
    af._persist_seat_exhausted(af._tok_id("tokA"), "额度不足")
    _restart_aifin()
    monkeypatch.setattr(quota, "snapshot", lambda p: {})
    af._invalidate()
    assert af.all_seats_exhausted() is False, "读不到时必须放行"


# ── 护栏 ──────────────────────────────────────────────────────────────────────
def test_all_seats_exhausted_uses_all_not_any():
    """一字之差的退化(all→any)会让「任一账号触顶就交棒」,行为测试很难发现。"""
    import ast
    import inspect
    import textwrap

    # 只看**可执行代码**:注释里为了说明「而非 any(...)」必然会出现 any,不能算数
    # (同一类误伤本会话已经踩过一次,见 test_kvstate_atomic_field)。
    tree = ast.parse(textwrap.dedent(inspect.getsource(af.all_seats_exhausted)))
    code = ast.unparse(tree)
    assert "all(" in code, "多账号语义必须是 all(...)"
    assert "any(" not in code, "any(...) 会让任一账号触顶就交棒 —— 一字之差的语义退化"


def test_dead_aifin_usage_blob_writer_is_gone():
    """`aifin_usage` 是零读者的读-改-写 blob,权威已移到 provider_quota,不得复活。"""
    import inspect
    src = inspect.getsource(af)
    assert "_persist_usage" not in src.replace("# ", "")
    assert 'save_state("aifin_usage"' not in src


def test_quota_predicates_read_the_table():
    """护栏:谓词必须读 provider_quota,不得退回纯进程内变量。"""
    import inspect
    assert "_row()" in inspect.getsource(ap.quota_exhausted)
    assert "_seat_blocked" in inspect.getsource(af.all_seats_exhausted)
