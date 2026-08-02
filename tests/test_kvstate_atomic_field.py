"""`set_state_field` 的原子性回归(2026-08-02)。

背景是一次真实事故:`cadence` 一块 JSONB blob 里装着 **13 个拉取源的心跳戳**,
而写入方是 `glm_worker._stamp` 的读-改-写:

    st = get_state("cadence"); st[key] = ts; save_state("cadence", st)

只要那次 `get_state` 拿到的是空/陈旧字典,回写就把**其余 12 个源的戳一起抹掉**。
监控面板上的表现是一批源同时翻 `unknown`(实测 15 个任务失联),而源本身好好的 ——
「监控自己成了停摆源」。同类事故本会话发生过两次(另一次是 `actions.trigger_pull`)。

判据可以概括成一句,值得钉住:
**一块 blob 里装着多个互不相干的写入方时,就不能整块回写。**
"""
from __future__ import annotations

import pytest

from xar.storage.kvstate import delete_state, get_state, save_state, set_state_field

_K = "zz_test_atomic_field"


@pytest.fixture()
def _key(isolated_db):
    delete_state(_K)
    yield _K


def test_creates_blob_when_absent(_key):
    """键不存在时直接建出来,不需要调用方先 save 一个空字典。"""
    set_state_field(_K, "a", "2026-08-02T00:00:00+00:00")
    assert get_state(_K) == {"a": "2026-08-02T00:00:00+00:00"}


def test_writing_one_field_preserves_the_others(_key):
    """核心不变量:写一个字段不得动其余字段。"""
    save_state(_K, {"twitter": "t0", "wechat": "w0", "finnhub_news": "f0"})
    set_state_field(_K, "wechat", "w1")
    assert get_state(_K) == {"twitter": "t0", "wechat": "w1", "finnhub_news": "f0"}


def test_many_sources_survive_sequential_stamps(_key):
    """模拟 cadence 的真实形态:13 个源轮流盖戳,最终 13 个戳必须都在。

    这正是事故现场 —— 读-改-写下只要中间有一次读到空,最后只会剩一个键。
    """
    sources = ["twitter", "wechat", "finnhub_news", "rss", "alt", "futu_news",
               "gangtise", "gangtise_backfill", "wind_edb", "alt_fetch_chain",
               "earnings_watch", "flow", "andy_macro"]
    for i, s in enumerate(sources):
        set_state_field(_K, s, f"ts-{i}")
    got = get_state(_K)
    assert set(got) == set(sources), f"丢了源:{set(sources) - set(got)}"
    assert got["twitter"] == "ts-0" and got["andy_macro"] == "ts-12"


def test_overwrite_same_field_is_idempotent(_key):
    set_state_field(_K, "a", "v1")
    set_state_field(_K, "a", "v2")
    assert get_state(_K) == {"a": "v2"}


def test_non_string_values_round_trip(_key):
    """cadence 存的是字符串,但这个原语要能装下 dict/数字(counters 一类也想用)。"""
    set_state_field(_K, "n", 7)
    set_state_field(_K, "d", {"x": 1, "y": [1, 2]})
    got = get_state(_K)
    assert got["n"] == 7 and got["d"] == {"x": 1, "y": [1, 2]}


def test_does_not_read_before_write():
    """它必须**不读**就能写 —— 「先读」正是事故的源头。

    这条守在源码层而不是运行时:合并发生在数据库端的一次 `jsonb_set`,
    实现里不该出现任何 SELECT / get_state。行为测试证不了这一点 ——
    读一次再写在单线程顺序调用下看起来完全正常,只有并发或读到空时才毁数据。
    """
    import inspect

    from xar.storage import kvstate

    import ast
    import textwrap

    src = inspect.getsource(kvstate.set_state_field)
    assert "jsonb_set" in src, "合并必须在数据库端做"
    # 只看**可执行代码**:docstring 里为了解释事故必然会提到 get_state,不能算数。
    fn = ast.parse(textwrap.dedent(src)).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    code = "\n".join(ast.unparse(n) for n in body)
    assert "get_state" not in code and "SELECT" not in code.upper(), \
        "set_state_field 不得先读后写 —— 那就退回了会整块覆盖的老路"


def test_stamp_uses_atomic_write():
    """护栏:`glm_worker._stamp` 不得退回读-改-写。

    行为测试挡不住这次回归 —— 单线程顺序调用下,读-改-写看起来完全正常,
    只有在「读到空」的那一刻才会毁数据。所以这里直接守写法本身。
    """
    import inspect

    from xar.orchestration import glm_worker

    src = inspect.getsource(glm_worker._stamp)
    assert "set_state_field" in src, "_stamp 必须用单字段原子写"
    assert 'save_state("cadence"' not in src, "_stamp 不得整块回写 cadence"
