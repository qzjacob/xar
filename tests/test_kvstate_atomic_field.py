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


# ── merge_state_field:两层状态的跨进程安全合并(2026-08-02)────────────────────
# `sub_quota` 是 {provider: {status, exhaust_count, ...}} 的两层 blob,有**三个进程**
# 在写(subpool 容器 / glmworker / app 的 on-demand phanny),而 subpool._STATE_LOCK
# 是 threading.Lock —— 对跨容器并发一直无能为力。合并必须放到数据库端做。
def test_merge_creates_nested_object_when_absent(_key):
    """中间层不存在时也要能建出来 —— 这正是 jsonb_set 深 path 会**静默 no-op** 的地方。"""
    from xar.storage.kvstate import merge_state_field
    merge_state_field(_K, "zhipu", {"status": "ok"})
    assert get_state(_K) == {"zhipu": {"status": "ok"}}


def test_merge_does_not_touch_sibling_providers(_key):
    """核心不变量:冷却 minimax 不得动 zhipu —— 整块回写下这正是丢更新的形态。"""
    from xar.storage.kvstate import merge_state_field
    save_state(_K, {"zhipu": {"status": "ok"}, "minimax": {"status": "ok"}})
    merge_state_field(_K, "minimax", {"status": "exhausted"})
    got = get_state(_K)
    assert got["zhipu"] == {"status": "ok"}
    assert got["minimax"]["status"] == "exhausted"


def test_merge_preserves_unpatched_fields_of_same_provider(_key):
    """浅合并:只覆盖 patch 里出现的字段,exhaust_count 这类过渡计数不能被抹掉。"""
    from xar.storage.kvstate import merge_state_field
    save_state(_K, {"zhipu": {"status": "exhausted", "exhaust_count": 3,
                              "last_reason": "quota"}})
    merge_state_field(_K, "zhipu", {"status": "ok", "resumed_at": "t1"})
    got = get_state(_K)["zhipu"]
    assert got["status"] == "ok" and got["resumed_at"] == "t1"
    assert got["exhaust_count"] == 3 and got["last_reason"] == "quota"


def test_merge_creates_blob_when_key_absent(_key):
    from xar.storage.kvstate import merge_state_field
    merge_state_field(_K, "kimi", {"status": "exhausted"})
    assert get_state(_K)["kimi"]["status"] == "exhausted"


def test_merge_does_not_read_before_write():
    """与 set_state_field 同理:合并在 DB 端做,实现里不得先读 —— 读是整块覆盖的源头。"""
    import ast
    import inspect
    import textwrap

    from xar.storage import kvstate

    fn = ast.parse(textwrap.dedent(inspect.getsource(kvstate.merge_state_field))).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    code = "\n".join(ast.unparse(n) for n in body)
    assert "jsonb_set" in code and "get_state" not in code and "SELECT" not in code.upper()


def test_subpool_mark_uses_merge_not_wholesale_write():
    """护栏:_mark 不得退回整块回写 —— 那会同时带回跨进程丢更新与整块抹除两个问题。"""
    import inspect

    from xar.models import subpool

    src = inspect.getsource(subpool._mark)
    assert "merge_state_field" in src
    assert "save_state(STATE_KEY" not in src


# ── PR4 护栏:cursor 四写方与 cadence 写法的统一(2026-08-02)────────────────────
def test_no_wholesale_cursor_writes_anywhere():
    """`cursor` 这块 blob 有 **4 个写入点**(glmworker 的 futu/gangtise、futu_flow、flow_si)。
    整块回写时任一次读到空/陈旧就会抹掉其余源的游标 —— 与 cadence 那次事故同型。
    这条护栏扫全仓源码,防止将来任何一处悄悄改回去。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "xar"
    files = list(root.rglob("*.py"))
    # ⚠️ 先证明扫到了东西:原来用 CWD 相对路径,从仓库根以外跑 pytest 时 rglob 产出空集,
    # assert 对空列表恒真 —— **护栏会静默变绿**,而它正是 PR4 的主要交付物。
    assert len(files) > 50, f"源码树扫描失败(只扫到 {len(files)} 个文件),护栏无效"
    hits = []
    for f in files:
        if 'save_state("cursor"' in f.read_text():
            hits.append(str(f))
    assert not hits, f"这些文件仍在整块回写 cursor:{hits}"


def test_cadence_has_exactly_one_write_implementation():
    """同一个不变量只能有一个实现。此前 monitoring/actions.py 另写了一份裸 SQL jsonb_set,
    与 kvstate.set_state_field 语义相同但措辞不同,且漏了 coalesce —— 键不存在时行为分叉。
    修一处补不到另一处,正是这类重复实现的代价。
    """
    import pathlib
    # 判据是「谁直接写 glm_worker_state 表」——不是散文里提没提 jsonb_set
    # (注释解释事故时必然会提到它,那不算实现)。
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "xar"
    files = list(root.rglob("*.py"))
    assert len(files) > 50, f"源码树扫描失败(只扫到 {len(files)} 个文件),护栏无效"
    raw = []
    for f in files:
        if f.name == "kvstate.py":
            continue
        if "INSERT INTO glm_worker_state" in f.read_text():
            raw.append(str(f))
    assert not raw, f"kvstate 之外不得再写一份 glm_worker_state 的写入实现:{raw}"
