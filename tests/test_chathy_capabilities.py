"""UA-P3:Chathy 新工具 —— schema/读取/refresh 只 schedule/run_status/8k 预算。"""
from __future__ import annotations

import json

import pytest

from xar.capabilities import registry, runs


def _exec(name, args):
    return json.loads(registry.execute(name, args))


def test_new_tools_registered_and_chathy():
    names = {c.name for c in registry.chathy_specs()}
    for t in ("earnings_panel", "earnings_verdict", "run_status", "theme_debates",
              "exploration_frontier", "fenny_quote", "start_report"):
        assert t in names, f"{t} not a chathy capability"


def test_earnings_verdict_read(seeded_db, monkeypatch):
    import datetime as dt

    from xar.research import earnings
    ev = {"scheduled_for": dt.date(2099, 6, 30)}
    monkeypatch.setattr(earnings, "_next_earnings", lambda cid: ev)
    monkeypatch.setattr(earnings, "latest_verdict", lambda cid, d: {
        "version": 2, "direction": "long", "conviction": 7.5, "model": "codex-sub",
        "expected_move": 0.06, "content": {"expected_surprise_zh": "beat", "asymmetry_zh": "下行有限",
        "dimensions": [{"key": "consensus_setup", "score": 1.0}]}})
    out = _exec("earnings_verdict", {"company_id": "now"})
    assert out["direction"] == "long" and out["conviction"] == 7.5 and out["version"] == 2
    assert out["dimensions"][0]["key"] == "consensus_setup"


def test_earnings_verdict_refresh_schedules_not_inline(seeded_db, monkeypatch):
    import datetime as dt

    from xar.research import earnings
    monkeypatch.setattr(earnings, "_next_earnings", lambda cid: {"scheduled_for": dt.date(2099, 6, 30)})
    called = {"build": False}
    monkeypatch.setattr(earnings, "build_verdict",
                        lambda *a, **k: called.update(build=True) or {"status": "built"})
    monkeypatch.setattr(runs, "launch", lambda name, args, **kw: {"run_id": "r123", "status": "queued"})
    out = _exec("earnings_verdict", {"company_id": "now", "refresh": True})
    assert out["scheduled"] is True and out["run_id"] == "r123"
    assert called["build"] is False        # 不内联调 build_verdict


def test_run_status_roundtrip(seeded_db, monkeypatch):
    monkeypatch.setattr(runs, "status", lambda rid: {"run_id": rid, "status": "done", "result": {"x": 1}})
    out = _exec("run_status", {"run_id": "abc"})
    assert out["status"] == "done" and out["result"]["x"] == 1
    monkeypatch.setattr(runs, "status", lambda rid: None)
    assert "error" in _exec("run_status", {"run_id": "missing"})


def test_start_report_schedules(seeded_db, monkeypatch):
    monkeypatch.setattr(runs, "launch", lambda name, args, **kw: {"run_id": "rep1", "status": "queued"})
    out = _exec("start_report", {"company_id": "now"})
    assert out["scheduled"] is True and out["run_id"] == "rep1"


def test_build_capability_not_inline_via_execute(seeded_db):
    # 评审 #13:build 能力不得经 execute() 内联跑(会卡 SSE);返回错误提示走 /api/run
    out = _exec("build_earnings_verdict", {"company_id": "now"})
    assert "error" in out and "/api/run" in out["error"]


def test_theme_debates_caps_by_company(seeded_db, monkeypatch):
    from xar.research import thesis_health
    big = {"theme": "ai_optical", "debates": [
        {"key": "d1", "mean_lean": 0.2, "by_company": [{"company_id": f"c{i}"} for i in range(20)]}]}
    monkeypatch.setattr(thesis_health, "theme_debate_health", lambda t: big)
    out = _exec("theme_debates", {"theme": "ai_optical"})
    assert len(out["debates"][0]["by_company"]) == 8      # 截到 8


@pytest.mark.parametrize("name,args", [
    ("earnings_panel", {"company_id": "now"}),
    ("theme_debates", {"theme": "ai_optical"}),
])
def test_tool_output_within_8k_budget(seeded_db, name, args):
    out = registry.execute(name, args)          # 真跑(seeded_db);execute 保证 ≤ 8k
    assert len(out) <= registry._MAX_RESULT_CHARS


# --- oversized payloads degrade to data, never to a zero-information error ------------------

def test_fit_trims_nested_lists_and_keeps_the_payload_useful():
    """company_detail 形状:体积全在**嵌套** list 里。旧版只扫顶层 → 直接 result too large,
    等于白烧一轮工具调用(chat 撞 iteration cap 的主因之一)。"""
    payload = {"company": {"id": "nvidia", "ticker": "NVDA"},
               "supplyChain": {"suppliers": [{"name": f"s{i}", "blurb": "x" * 200} for i in range(60)],
                               "customers": [{"name": f"c{i}", "blurb": "y" * 200} for i in range(60)]},
               "signals": [{"summary": "z" * 300} for _ in range(40)]}
    out, truncated = registry._fit(payload, registry._MAX_RESULT_CHARS)

    assert truncated and len(out) <= registry._MAX_RESULT_CHARS
    got = json.loads(out)
    assert "error" not in got and got["company"]["ticker"] == "NVDA"     # scalars survive
    assert got["supplyChain"]["suppliers"] and got["signals"]            # partial > nothing
    assert "_truncated" in got


def test_fit_clips_prose_when_there_are_no_lists_to_trim():
    """get_thesis 形状:一大段散文、零 list。旧版 while 循环立刻 break → result too large。"""
    payload = {"company_id": "msft", "stance": "long", "content": "论点。" * 8000}
    out, truncated = registry._fit(payload, registry._MAX_RESULT_CHARS)

    assert truncated and len(out) <= registry._MAX_RESULT_CHARS
    got = json.loads(out)
    assert "error" not in got and got["stance"] == "long"
    assert got["content"].startswith("论点。") and got["content"].endswith("…")


def test_fit_does_not_mutate_the_callers_payload():
    """能力函数可能返回缓存对象(dashboard._load 有 TTL 缓存)——原地削会污染其他消费者。"""
    payload = {"rows": [{"i": i, "pad": "p" * 500} for i in range(80)]}
    before = json.dumps(payload, ensure_ascii=False)
    registry._fit(payload, registry._MAX_RESULT_CHARS)
    assert json.dumps(payload, ensure_ascii=False) == before


def test_fit_passes_small_payloads_through_untouched():
    payload = {"a": [1, 2, 3], "b": "ok"}
    out, truncated = registry._fit(payload, registry._MAX_RESULT_CHARS)
    assert truncated is False and json.loads(out) == payload


def test_fit_drops_the_smallest_sufficient_branch_and_names_it():
    """纯 key 体积(company_detail.thesis 那种嵌套小字段)—— ①② 都够不着,只能整枝砍。
    砍「够用的最小枝」:兄弟枝全留,被砍的枝留下可读的占位符(模型可改用窄工具重取)。"""
    payload = {"stance": "long",
               "small": {"a": 1, "b": 2},
               "bulky": {f"k{i}": {"v": i, "note": "n" * 20} for i in range(200)},
               "mid": {f"m{i}": i for i in range(30)}}
    out, truncated = registry._fit(payload, registry._MAX_RESULT_CHARS)

    assert truncated and len(out) <= registry._MAX_RESULT_CHARS
    got = json.loads(out)
    assert "error" not in got
    assert got["stance"] == "long" and got["small"] == {"a": 1, "b": 2}   # 兄弟枝原样保留
    assert isinstance(got["bulky"], str) and got["bulky"].startswith("<omitted")
    assert got["mid"] == {f"m{i}": i for i in range(30)}                  # 够用的最小枝 → mid 不该被砍


def test_fit_preview_fallback_still_respects_the_budget():
    """兜底路径也必须 ≤ budget —— preview 是 JSON-in-JSON,转义会膨胀,不能按算术切。"""
    out = registry._preview("x\"y\n" * 5000, registry._MAX_RESULT_CHARS)
    assert len(out) <= registry._MAX_RESULT_CHARS
    got = json.loads(out)
    assert got["error"] == "result too large" and got["preview_json"]


@pytest.mark.parametrize("payload", [
    {"deep": {"a": ["s" * 400] * 50, "b": {"c": [{"x": "y" * 300}] * 40}}},
    [{"row": i, "pad": "p" * 400} for i in range(60)],
    {"prose": "文" * 9000, "n": 1},
    {f"k{i}": {"note": "n" * 30} for i in range(400)},
])
def test_fit_always_lands_within_budget(payload):
    out, truncated = registry._fit(payload, registry._MAX_RESULT_CHARS)
    assert truncated and len(out) <= registry._MAX_RESULT_CHARS
    json.loads(out)                              # 永远是合法 JSON


def test_execute_never_raises_on_unserialisable_results(monkeypatch):
    """execute 的契约是「永不抛」—— 抛出来会直接把 SSE turn 打断。裁剪/序列化也在契约内。"""
    class Weird:
        def __deepcopy__(self, memo):
            raise RuntimeError("nope")
        def __str__(self):
            return "w" * 400

    def _register(payload):
        spec = registry.CapabilitySpec("weird_cap", "test", {"type": "object", "properties": {}},
                                       lambda **kw: payload)
        monkeypatch.setitem(registry._BY_NAME, "weird_cap", spec)

    _register({"rows": [Weird() for _ in range(60)]})       # deepcopy 抛
    out = registry.execute("weird_cap", {})
    assert len(out) <= registry._MAX_RESULT_CHARS
    json.loads(out)                              # 合法 JSON,而不是异常

    circular: dict = {}
    circular["self"] = circular                             # json.dumps 抛
    _register(circular)
    assert "error" in json.loads(registry.execute("weird_cap", {}))
