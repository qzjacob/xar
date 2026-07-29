"""Phanny 构建快照写入 —— 让一条已入库裁决**可字节级回放**。

**为什么存在**:此前 dossier 用完即弃、known_ids/panel 从不入库、提示词是内联 f-string、
模型原文解析后丢掉。于是一条裁决只剩结论,输入全部蒸发:
  - `as_of` 只是**构建日期**,而 prices/estimates/ratings/alt_signals 一直在变 ——
    「同样的代码、同样的日期重跑一遍」得到的 dossier 与当初并不相同;
  - 接地 id 形如 `tech:{cid}` / `price:{cid}:recent`,指向的是**一次查询**而非某一行,
    事后无法还原模型当时究竟看到了什么数值。
快照把这些定格下来:回放直接吃快照,不碰活表。

**契约:绝不 raise**。快照是观测面,坏掉不许拖垮一次真实构建(与 buildlog 同纪律)。
**体积**:dossier 内容寻址,一次 build 的 ~20 次调用共享一行;渲染后的提示词只存 sha +
模板 (key, version) + 插值参数,回放时按同一模板重渲染再比对 sha —— 漂移会被显式抓到,
而不是把 20 份近乎重复的 30KB 文本抄进库里。
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from ..logging import get_logger
from ..storage import db

log = get_logger("xar.phanny.snapshots")


def new_build_id() -> str:
    return uuid4().hex[:12]


def _sha(body: str) -> str:
    return hashlib.sha256((body or "").encode()).hexdigest()


def save_artifact(kind: str, body: str | None, meta: dict | None = None) -> str | None:
    """内容寻址入库,返回 sha(已存在则不重复写)。body 为空返回 None。"""
    if not body:
        return None
    sha = _sha(body)
    try:
        db.execute("INSERT INTO artifacts(sha, kind, body, meta) VALUES(%s,%s,%s,%s::jsonb) "
                   "ON CONFLICT (sha) DO NOTHING",
                   (sha, kind, body, json.dumps(meta or {}, ensure_ascii=False, default=str)))
    except Exception as e:  # noqa: BLE001 — never-raise,但留声
        log.warning("artifact save failed (%s): %s", kind, str(e)[:120])
        return None
    return sha


def snap_dossier(build_id: str, cid: str, d: dict, *, run_id: str | None = None,
                 event_date=None) -> str | None:
    """定格这次构建的证据面:dossier 全文 + **known_ids**(模型被允许引用的 id 全集)
    + **panel**(那些接地 id 实际所指的数值)。三者齐全,回放才不必回读活表。"""
    try:
        sha = save_artifact("dossier_text", d.get("text"),
                            {"company_id": cid, "as_of": str(d.get("as_of"))})
        db.execute(
            "INSERT INTO phanny_build_snapshots(build_id, run_id, company_id, event_date, stage, "
            "dossier_sha, known_ids, panel, meta) VALUES(%s,%s,%s,%s,'dossier',%s,%s::jsonb,%s::jsonb,%s::jsonb)",
            (build_id, run_id, cid, event_date, sha,
             json.dumps(sorted(d.get("known_ids") or []), ensure_ascii=False, default=str),
             json.dumps(d.get("panel") or {}, ensure_ascii=False, default=str),
             json.dumps({"n_facts": d.get("n_facts"), "implied_move": d.get("implied_move"),
                         "as_of": str(d.get("as_of")),
                         "failed_sections": d.get("failed_sections") or []},
                        ensure_ascii=False, default=str)))
        return sha
    except Exception as e:  # noqa: BLE001
        log.warning("snap_dossier failed (%s): %s", cid, str(e)[:120])
        return None


def snap_call(build_id: str, cid: str, *, stage: str, run_id: str | None = None,
              event_date=None, round: int | None = None, attempt: int | None = None,
              model: str | None = None, capture: dict | None = None,
              template: str | None = None, template_ver: int | None = None,
              params: dict | None = None, meta: dict | None = None) -> None:
    """记一次 LLM 调用的可回放痕迹。`capture` 来自 `llm.complete_json(capture=...)`:
    模型原文进 artifacts(每次都不同,必须全存 —— 它同时是辩论逐轮全文的载体),
    提示词只留 sha(回放时按 template/params 重渲染比对)。"""
    cap = capture or {}
    try:
        resp_sha = save_artifact("response_raw", cap.get("raw"),
                                 {"company_id": cid, "stage": stage, "round": round})
        db.execute(
            "INSERT INTO phanny_build_snapshots(build_id, run_id, company_id, event_date, stage, "
            "round, attempt, model, prompt_sha, response_sha, prompt_template, template_ver, "
            "schema_sha, params, meta) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
            (build_id, run_id, cid, event_date, stage, round, attempt, model,
             cap.get("prompt_sha"), resp_sha, template, template_ver, cap.get("schema_sha"),
             json.dumps(params or {}, ensure_ascii=False, default=str),
             json.dumps({**(meta or {}),
                         "attempts": cap.get("attempts"),
                         "fallback": cap.get("fallback", False)},
                        ensure_ascii=False, default=str)))
    except Exception as e:  # noqa: BLE001
        log.warning("snap_call failed (%s/%s): %s", cid, stage, str(e)[:120])


def stamp_verdict(build_id: str, verdict_id: int | None) -> None:
    """入库后把 verdict_id 回填到本次 build 的全部快照 —— 建立 verdict ↔ 输入的双向索引。"""
    if not verdict_id:
        return
    try:
        db.execute("UPDATE phanny_build_snapshots SET verdict_id=%s WHERE build_id=%s",
                   (verdict_id, build_id))
    except Exception as e:  # noqa: BLE001
        log.warning("stamp_verdict failed (%s): %s", build_id, str(e)[:120])


def mark_superseded(build_id: str, by_build_id: str) -> None:
    """REDEBATE 谱系:旧 build 的快照标记被谁取代(否则被丢弃的那一稿彻底消失)。"""
    try:
        db.execute("UPDATE phanny_build_snapshots "
                   "SET meta = meta || jsonb_build_object('superseded_by', %s::text) "
                   "WHERE build_id=%s", (by_build_id, build_id))
    except Exception as e:  # noqa: BLE001
        log.warning("mark_superseded failed (%s): %s", build_id, str(e)[:120])


def load_build(build_id: str) -> dict | None:
    """回放取数:把一次 build 的快照还原成 {dossier, calls[]}(含 artifacts 正文)。"""
    try:
        rows = db.query(
            "SELECT s.*, a.body AS dossier_text FROM phanny_build_snapshots s "
            "LEFT JOIN artifacts a ON a.sha = s.dossier_sha "
            "WHERE s.build_id=%s ORDER BY s.id", (build_id,))
    except Exception as e:  # noqa: BLE001
        log.warning("load_build failed (%s): %s", build_id, str(e)[:120])
        return None
    if not rows:
        return None
    head = next((r for r in rows if r["stage"] == "dossier"), None)
    return {"build_id": build_id, "dossier": head,
            "calls": [r for r in rows if r["stage"] != "dossier"]}
