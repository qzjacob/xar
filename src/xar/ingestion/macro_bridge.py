"""数据层勾稽桥：Andy（slx 宏观库）→ kg_events(event_type='macro_print') → semantic_facts。

两个发射器（全幂等，重跑零重复）：
  1. sync_metric_prints(as_of)   —— 硬/中指标的最新 PIT 印字，按 macro_links 落到关联链；
  2. sync_claim_transitions(as_of) —— 过度宣称登记簿的判定跃迁（open→fixation 等）。

幂等锚点：kg_events.dedup_key UNIQUE + ON CONFLICT DO NOTHING。
  指标印字  macro:{metric_key}:{valid_time}:{knowledge_time}:{theme|*}
  判定跃迁  macro:claim:{claim_key}:{old}->{new}:{as_of}:{theme|*}
极性（kg_events.polarity 为 TEXT）：good_when × 斜率符号 → positive/negative/neutral；
判定跃迁按 OVERCLAIM_LINKS 的 polarity_on_*。

写入走 xar.storage.db（xar 侧连接池）；读取走 slx.db（search_path=slx 的裸连接）——
依赖保持单向 xar → slx。license_tag='slx' 使事件在 semantic_facts 中可溯源（且不与
expert 镜像的排除规则冲突），company_id=NULL（宏观事件不锚定单一公司）。
"""
from __future__ import annotations

import json
from datetime import date

from ..logging import get_logger
from ..ontology.macro_links import LINKS_BY_KEY, OVERCLAIM_LINKS, MacroLink
from ..storage import db

log = get_logger("xar.macro_bridge")

_INSERT = """
INSERT INTO kg_events(company_id, event_type, event_date, polarity, tech_route_tag,
                      summary, narrative, attrs, confidence, license_tag, dedup_key,
                      theme, segment, time_orientation)
VALUES (NULL, 'macro_print', %s, %s, %s, %s, %s, %s, 0.9, 'slx', %s, %s, %s, 'backward_looking')
ON CONFLICT (dedup_key) DO NOTHING
"""


def _print_polarity(good_when: str | None, slope: float | None) -> str:
    if good_when is None or slope is None or slope == 0:
        return "neutral"
    sign = 1 if slope > 0 else -1
    aligned = sign if good_when == "rising" else -sign
    return "positive" if aligned > 0 else "negative"


def _themes_or_star(themes: tuple[str, ...]) -> list[str | None]:
    """空 themes（纯平台级）→ 单行 theme=NULL；dedup 键位用 '*' 占位。"""
    return list(themes) if themes else [None]


def _insert_event(*, event_date, polarity: str, link: MacroLink | None, summary: str,
                  narrative: str, attrs: dict, dedup: str, theme: str | None) -> bool:
    tech_route = link.tech_routes[0] if link and link.tech_routes else None
    segment = None
    if link and theme:  # 主 segment：该 theme 下的第一个关联环节
        from .registry import SEGMENTS

        for seg in link.segments:
            if SEGMENTS.get(seg, {}).get("theme") == theme:
                segment = seg
                break
    before = db.query("SELECT 1 FROM kg_events WHERE dedup_key=%s", (dedup,))
    if before:
        return False
    db.execute(_INSERT, (event_date, polarity, tech_route, summary, narrative,
                         json.dumps(attrs, ensure_ascii=False, default=str), dedup,
                         theme, segment))
    return True


def sync_metric_prints(as_of: date) -> dict:
    """把每条硬/中关联指标在 as_of 视角下的最新印字写入 kg_events（每关联链一行）。"""
    from slx.db import connect
    from slx.engine.point_in_time import PointInTimeContext

    inserted = skipped = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT metric_key, display_name_zh, hardness, unit FROM metric_registry "
            "WHERE hardness IN ('hard','medium') AND is_quantifiable"
        ).fetchall()
        ctx = PointInTimeContext(conn, as_of)
        for metric_key, name_zh, hardness, unit in rows:
            link = LINKS_BY_KEY.get(metric_key)
            if link is None:
                continue
            obs = conn.execute(
                "SELECT valid_time, knowledge_time, value FROM observation "
                "WHERE metric_key=%s AND knowledge_time <= %s AND value IS NOT NULL "
                "ORDER BY valid_time DESC, knowledge_time DESC LIMIT 1",
                (metric_key, as_of),
            ).fetchone()
            if obs is None:  # 无观测（正常：多数指标等真实连接器/key）
                continue
            valid_time, knowledge_time, value = obs
            try:
                slope = ctx.slope(metric_key, 4)
            except Exception:  # noqa: BLE001 — 单点序列无斜率
                slope = None
            polarity = _print_polarity(link.good_when, slope)
            summary = (f"宏观印字：{name_zh} = {value:g}{' ' + unit if unit else ''}"
                       f"（观测期 {valid_time:%Y-%m-%d}）")
            attrs = {"metric_key": metric_key, "value": float(value), "unit": unit,
                     "slope": slope, "hardness": hardness,
                     "valid_time": str(valid_time), "knowledge_time": str(knowledge_time)}
            for theme in _themes_or_star(link.themes):
                dedup = f"macro:{metric_key}:{valid_time}:{knowledge_time}:{theme or '*'}"
                if _insert_event(event_date=valid_time, polarity=polarity, link=link,
                                 summary=summary, narrative=link.rationale_zh, attrs=attrs,
                                 dedup=dedup, theme=theme):
                    inserted += 1
                else:
                    skipped += 1
    return {"inserted": inserted, "skipped": skipped}


def sync_claim_transitions(as_of: date) -> dict:
    """把登记簿判定跃迁（相邻两次评估 verdict 变化）写入 kg_events。"""
    from slx.db import connect

    inserted = skipped = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT l.claim_key, l.as_of_date, l.verdict, r.claim_text_zh "
            "FROM overclaim_eval_log l JOIN overclaim_registry r USING (claim_key) "
            "WHERE l.as_of_date <= %s ORDER BY l.claim_key, l.evaluated_at",
            (as_of,),
        ).fetchall()
    history: dict[str, list] = {}
    for claim_key, as_of_date, verdict, text_zh in rows:
        history.setdefault(claim_key, []).append((as_of_date, verdict, text_zh))
    lamp = {"fixation_triggered": "🔴固化", "falsified": "🟢证伪", "expired": "🟠过期",
            "inconclusive": "⚪待识别", "open": "🔵未决"}
    for claim_key, seq in history.items():
        clink = OVERCLAIM_LINKS.get(claim_key)
        prev = "open"
        for as_of_date, verdict, text_zh in seq:
            if verdict == prev:
                continue
            polarity = "neutral"
            if clink is not None:
                if verdict == "fixation_triggered":
                    polarity = clink.polarity_on_fixation
                elif verdict == "falsified":
                    polarity = clink.polarity_on_falsified
            summary = f"登记簿判定：「{text_zh}」 {lamp.get(prev, prev)} → {lamp.get(verdict, verdict)}"
            attrs = {"claim_key": claim_key, "from": prev, "to": verdict,
                     "as_of": str(as_of_date)}
            themes = clink.themes if clink else ()
            for theme in _themes_or_star(themes):
                dedup = f"macro:claim:{claim_key}:{prev}->{verdict}:{as_of_date}:{theme or '*'}"
                if _insert_event(event_date=as_of_date, polarity=polarity, link=None,
                                 summary=summary,
                                 narrative=(clink.rationale_zh if clink else ""),
                                 attrs=attrs, dedup=dedup, theme=theme):
                    inserted += 1
                else:
                    skipped += 1
            prev = verdict
    return {"inserted": inserted, "skipped": skipped}


def sync(as_of: date | None = None) -> dict:
    """全量勾稽同步（幂等）。"""
    as_of = as_of or date.today()
    prints = sync_metric_prints(as_of)
    claims = sync_claim_transitions(as_of)
    out = {"as_of": str(as_of), "prints": prints, "claims": claims}
    log.info("macro bridge sync: %s", out)
    return out
