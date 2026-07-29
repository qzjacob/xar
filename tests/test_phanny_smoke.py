"""Phanny 端到端冒烟:整本 book 全流水(dossier → 提议 → 多 critic 辩论 → 校验 → 组合正态门
→ sizing → 入库 → 读回)。LLM/dossier/next-event 打桩 → 确定性、离线可跑;事务回滚隔离 → 零残留。

覆盖两条关键路径:
  ① happy path:证据真实分化 → conviction 呈正态 → status=normal,每名 long/short + 1-10 + 1-15%;
  ② 反作弊:全低聚集 → **不伪造正态**(status=calibration_incomplete),且未静默抬高任何 conviction。
"""
from __future__ import annotations

import re
from datetime import date

from xar.models import llm
from xar.ontology import phanny_events as pe
from xar.ontology.phanny_events import CriticVote, DimensionRead, PhannyProposal
from xar.phanny import book, engine

# 一组期权流动性名(取 universe 前 15;冒烟只需 ≥12 使正态检验有意义)
_CIDS = list(pe.PHANNY_UNIVERSE)[:15]
# 一条钟形 conviction 序列(mean≈5.1,std≈1.5,高信念占比≥0.10,Shapiro/矩法均过)
_BELL = [2, 3, 4, 4, 5, 5, 5, 6, 6, 7, 8, 4, 6, 5, 7]


def _install_stubs(monkeypatch, conv_map, dir_map):
    """打桩 next-event / dossier / LLM(complete_json 按 schema 分支 + 从 prompt 提 CID)。"""
    def fake_next(cid):
        return {"scheduled_for": date(2026, 9, 1), "id": None, "meta": {"session": "amc"},
                "event_type": "earnings"}

    def fake_dossier(cid, event):
        known = {f"estimate:{cid}:m{i}" for i in range(6)} | {f"tech:{cid}", f"flow:{cid}", f"opt:{cid}"}
        text = (f"CID={cid}\n## 财报事件\n{cid} 财报日 2026-09-01 amc\n"
                + "".join(f"[estimate:{cid}:m{i}] 指标{i}\n" for i in range(6))
                + f"[tech:{cid}] 技术面\n[flow:{cid}] 资金面\n[opt:{cid}] ATM 隐含波动 6.0%")
        return {"text": text, "known_ids": known, "panel": {}, "as_of": "2026-07-24",
                "event_date": "2026-09-01", "n_facts": len(known), "implied_move": 0.06}

    def fake_cj(prompt, schema, **kw):
        m = re.search(r"CID=(\S+)", prompt)
        cid = m.group(1) if m else _CIDS[0]
        if schema is CriticVote:                       # 反方:同意但指残留风险(非反射式)
            return CriticVote(direction_vote="agree", conviction_delta=0.0, size_delta=0.0,
                              attack_zh="残留风险:指引口径可能偏保守", rebuttal_zh="原方向仍成立")
        direction = "long" if dir_map.get(cid, True) else "short"
        conv = float(conv_map[cid])
        sc = 1.0 if direction == "long" else -1.0
        dims = [DimensionRead(key=k, score=sc, note_zh="数据支撑", evidence=[f"estimate:{cid}:m{i}"])
                for i, k in enumerate(pe.PHANNY_DIMENSIONS)]
        return PhannyProposal(direction=direction, conviction=conv, dimensions=dims,
                              expected_surprise_zh="预期差偏正", move_view_zh="implied 便宜",
                              asymmetry_zh="下行有限、上行更大", plan_zh="T-3 进场，财报后了结",
                              falsifiers_zh=["指引下修", "渠道走弱"],
                              prob_bins=[0.3, 0.3, 0.2, 0.15, 0.05], e_return_pct=2.5 * sc,
                              catalysts_zh=["数据中心营收", "指引"])

    monkeypatch.setattr(engine, "_next_earnings", fake_next)
    monkeypatch.setattr(engine, "dossier_phanny", fake_dossier)
    monkeypatch.setattr(engine, "_host_executor", lambda: "claude-max")   # 绕过 host_only 闸(与配置解耦)
    monkeypatch.setattr(llm, "complete_json", fake_cj)


def _existing(db, cids):
    have = {r["id"] for r in db.query("SELECT id FROM companies WHERE id = ANY(%s)", (cids,))}
    return [c for c in cids if c in have]


def test_book_e2e_happy_path_normal_distribution(isolated_db, monkeypatch):
    db = isolated_db
    cids = _existing(db, _CIDS)
    assert len(cids) >= 12, f"need ≥12 seeded universe names, got {len(cids)}"
    conv_map = {c: _BELL[i % len(_BELL)] for i, c in enumerate(cids)}
    dir_map = {c: (i % 3 != 0) for i, c in enumerate(cids)}   # ~2/3 long, 1/3 short
    _install_stubs(monkeypatch, conv_map, dir_map)

    res = book.run_book(cids, force=True, run_id="phanny-smoke")

    # 组合正态门:证据分化 → 正态(不靠压分)
    assert res["status"] == "normal", res["distribution"]
    assert res["distribution"]["ok"] is True
    assert res["integrity_violations"] == []           # 无"降分凑收敛"作弊
    assert res["n"] >= 12
    dc = res["distribution"]
    assert 4.5 <= dc["mean"] <= 6.5 and dc["std"] >= 1.5 and dc["high_ratio"] >= 0.10
    assert sum(dc["histogram"].values()) == res["n"]   # 直方图与本数一致

    # 每名裁决:强制 long/short + conviction 1-10 + size 1-15%
    stored = [s for s in res["stored"] if s["status"] == "built"]
    assert len(stored) == res["n"]
    for s in stored:
        assert s["direction"] in ("long", "short")
        assert 1.0 <= s["conviction"] <= 10.0
        assert 1.0 <= s["size_pct"] <= 15.0
        assert s["ensemble_status"] == "normal"

    # 组合层:gross/net 报出,每名仍在 [1,15]
    pf = res["portfolio"]
    assert pf["long"] + pf["short"] == res["n"]
    assert all(1.0 <= x["size_pct"] <= 15.0 for x in pf["sizes"])

    # 读回:落库可查(同事务内)
    v = engine.latest_verdict(cids[0], date(2026, 9, 1))
    assert v is not None and v["direction"] in ("long", "short")
    content = v["content"]
    assert len(content["dimensions"]) == 6 and content["debate_trace"]

    # DB 计数(事务内)= 本次入库名数
    n_rows = db.query("SELECT count(*) c FROM phanny_verdicts WHERE run_id=%s", ("phanny-smoke",))[0]["c"]
    assert n_rows == res["n"]


def test_book_all_low_not_faked(isolated_db, monkeypatch):
    """全低聚集:系统**不伪造正态**(诚实 calibration_incomplete),且未静默抬高任何 conviction。"""
    db = isolated_db
    cids = _existing(db, _CIDS)
    conv_map = {c: 3.0 for c in cids}                  # 全部低信念
    dir_map = {c: True for c in cids}
    _install_stubs(monkeypatch, conv_map, dir_map)

    res = book.run_book(cids, force=True, run_id="phanny-smoke-low")

    assert res["status"] == "calibration_incomplete"   # 不判 normal(不造假)
    assert res["distribution"]["ok"] is False
    # 未静默抬高:所有入库 conviction 仍为 3(REDEBATE 只来自辩论输出,此处 stub 不变)
    rows = db.query("SELECT conviction FROM phanny_verdicts WHERE run_id=%s", ("phanny-smoke-low",))
    assert rows and all(abs(float(r["conviction"]) - 3.0) < 1e-6 for r in rows)
    # 且 REDEBATE 确实尝试过(passes 用满上限)
    assert res["passes"] >= 1


def test_book_respects_wall_clock_budget(isolated_db, monkeypatch):
    """墙钟预算到点 → 不再开新名,余下按既有 cap 的同一套语义顺延下轮并留声。

    2026-07-29 加入:phanny 是 glm_worker.run_once 的最后一个阶段而拉取是第一个,单线程下
    phanny 超时多久、下一轮拉取就冻结多久(实测冻结 3.5 小时、全库零新文档)。名次上限挡不住
    ——订阅模型实测 49~124 秒/次,单名 N critic × max_rounds 轮就是半小时起。
    """
    db = isolated_db
    cids = _existing(db, _CIDS)
    conv_map = {c: _BELL[i % len(_BELL)] for i, c in enumerate(cids)}
    dir_map = {c: (i % 3 != 0) for i, c in enumerate(cids)}
    _install_stubs(monkeypatch, conv_map, dir_map)

    # 受控时钟:每次读表推进 10s。t0 取第一次读数,故第 i 名开工前 elapsed = 10*(i+1)。
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 10.0
        return clock["t"]

    monkeypatch.setattr(book.time, "monotonic", fake_monotonic)
    logged: list = []
    monkeypatch.setattr(book.buildlog, "record",
                        lambda *a, **k: logged.append((a[1] if len(a) > 1 else k.get("company_id"),
                                                       k.get("status"), k.get("reason"))))

    res = book.run_book(cids, force=True, run_id="phanny-smoke-budget", max_seconds=25)

    assert res.get("timed_out"), "预算用尽必须留声,否则会被读成「窗内只有这几家」"
    assert res["n"] + res["timed_out"] == len(cids), (
        f"完成 {res['n']} + 顺延 {res['timed_out']} 应等于总数 {len(cids)}")
    assert res["n"] == 2, f"预算 25s / 每步 10s 应恰好放行 2 名: n={res['n']}"
    # 顺延的名字都进了 buildlog(可用 `xar phanny why <cid>` 查为何没产出)
    deferred = [c for c, st, _ in logged if st == "skipped"]
    assert len(deferred) == res["timed_out"]
    assert all("墙钟预算" in (r or "") for c, st, r in logged if st == "skipped")


def test_book_no_budget_runs_all_names(isolated_db, monkeypatch):
    """max_seconds=None(CLI/单测默认)→ 旧语义不变,不会因新闸少跑名字。"""
    db = isolated_db
    cids = _existing(db, _CIDS)
    conv_map = {c: _BELL[i % len(_BELL)] for i, c in enumerate(cids)}
    dir_map = {c: (i % 3 != 0) for i, c in enumerate(cids)}
    _install_stubs(monkeypatch, conv_map, dir_map)

    res = book.run_book(cids, force=True, run_id="phanny-smoke-nobudget")

    assert "timed_out" not in res
    assert res["n"] == len(cids)


def test_book_isolates_single_name_llm_failure(isolated_db, monkeypatch):
    """单名 propose 的 LLM 调用抛错(网络/解析)→ 只隔离该名(skipped),其余仍入库(review M.1.1)。"""
    db = isolated_db
    cids = _existing(db, _CIDS)
    conv_map = {c: _BELL[i % len(_BELL)] for i, c in enumerate(cids)}
    dir_map = {c: (i % 3 != 0) for i, c in enumerate(cids)}
    _install_stubs(monkeypatch, conv_map, dir_map)
    bad = cids[0]
    stubbed = llm.complete_json   # = fake_cj(after _install_stubs)

    def flaky(prompt, schema, **kw):
        m = re.search(r"CID=(\S+)", prompt)
        if m and m.group(1) == bad and schema is PhannyProposal:
            raise RuntimeError("simulated LLM provider error")
        return stubbed(prompt, schema, **kw)

    monkeypatch.setattr(llm, "complete_json", flaky)

    res = book.run_book(cids, force=True, run_id="phanny-smoke-flaky")   # 不抛异常
    assert bad not in res["plans"]                                       # 坏名被隔离
    assert any(s["company_id"] == bad for s in res["skipped"])
    assert res["n"] == len(cids) - 1                                     # 其余全部构建
    built = {s["company_id"] for s in res["stored"] if s["status"] == "built"}
    assert bad not in built and len(built) == len(cids) - 1              # 其余全部入库
