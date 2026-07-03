"""把校验通过的理论本体 YAML 加载进 Postgres（幂等 upsert）。

顺序固定：theory_anchor → metric_registry(+metric_source) → overclaim_registry，
因为 DB 触发器会校验 metric.theory_anchor ∈ 受控词表、overclaim.related_metrics ∈ 已登记指标。

    python -m tools.load_registry
"""
from __future__ import annotations

from pathlib import Path

import yaml

from slx.db import connect

REG = Path(__file__).resolve().parent.parent / "registry"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_anchors(conn) -> int:
    doc = _load(REG / "theory_anchors.yml")
    n = 0
    for a in doc.get("anchors", []):
        conn.execute(
            """INSERT INTO theory_anchor (anchor_key, title, industrial_assumption, silicon_restatement, verdict)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (anchor_key) DO UPDATE SET
                 title=EXCLUDED.title, industrial_assumption=EXCLUDED.industrial_assumption,
                 silicon_restatement=EXCLUDED.silicon_restatement, verdict=EXCLUDED.verdict""",
            (a["anchor_key"], a["title"], a.get("industrial_assumption"),
             a.get("silicon_restatement"), a.get("verdict")),
        )
        n += 1
    return n


def load_metrics(conn) -> int:
    n = 0
    for mf in sorted((REG / "metrics").glob("*.yml")):
        for m in _load(mf).get("metrics", []):
            conn.execute(
                """INSERT INTO metric_registry
                   (metric_key, display_name_zh, family, theory_anchor, binding_scarcity, phase,
                    mechanism, hardness, identification_strategy, falsification_condition, decision_window,
                    source_grade, caveat, is_quantifiable, unit, geo_scope, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (metric_key) DO UPDATE SET
                     display_name_zh=EXCLUDED.display_name_zh, family=EXCLUDED.family,
                     theory_anchor=EXCLUDED.theory_anchor, binding_scarcity=EXCLUDED.binding_scarcity,
                     phase=EXCLUDED.phase, mechanism=EXCLUDED.mechanism, hardness=EXCLUDED.hardness,
                     identification_strategy=EXCLUDED.identification_strategy,
                     falsification_condition=EXCLUDED.falsification_condition,
                     decision_window=EXCLUDED.decision_window, source_grade=EXCLUDED.source_grade,
                     caveat=EXCLUDED.caveat, is_quantifiable=EXCLUDED.is_quantifiable,
                     unit=EXCLUDED.unit, geo_scope=EXCLUDED.geo_scope, status=EXCLUDED.status""",
                (m["metric_key"], m["display_name_zh"], m["family"], m["theory_anchor"],
                 m.get("binding_scarcity"), m.get("phase"), m["mechanism"], m["hardness"],
                 m.get("identification_strategy"), m.get("falsification_condition"),
                 m.get("decision_window"), m["source_grade"], m.get("caveat"),
                 m.get("is_quantifiable", True), m.get("unit"), m.get("geo_scope"),
                 m.get("status", "active")),
            )
            conn.execute("DELETE FROM metric_source WHERE metric_key=%s", (m["metric_key"],))
            for s in m.get("sources", []):
                conn.execute(
                    """INSERT INTO metric_source (metric_key, source_id, series_id, source_grade, ingest_cadence, vintage_aware)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (metric_key, source_id, series_id) DO UPDATE SET
                         source_grade=EXCLUDED.source_grade, ingest_cadence=EXCLUDED.ingest_cadence,
                         vintage_aware=EXCLUDED.vintage_aware""",
                    (m["metric_key"], s["source_id"], s.get("series_id", ""),
                     s.get("source_grade"), s.get("ingest_cadence"), s.get("vintage_aware", False)),
                )
            n += 1
    return n


def load_overclaims(conn) -> int:
    doc = _load(REG / "overclaim_registry.yml")
    n = 0
    for c in doc.get("claims", []):
        conn.execute(
            """INSERT INTO overclaim_registry
               (claim_key, claim_text_zh, related_metrics, hardness, decision_window, window_start,
                fixation_rule, falsify_rule, status, owner)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (claim_key) DO UPDATE SET
                 claim_text_zh=EXCLUDED.claim_text_zh, related_metrics=EXCLUDED.related_metrics,
                 hardness=EXCLUDED.hardness, decision_window=EXCLUDED.decision_window,
                 window_start=EXCLUDED.window_start, fixation_rule=EXCLUDED.fixation_rule,
                 falsify_rule=EXCLUDED.falsify_rule, owner=EXCLUDED.owner""",
            (c["claim_key"], c["claim_text_zh"], c.get("related_metrics", []), c.get("hardness"),
             c["decision_window"], c["window_start"], c["fixation_rule"], c["falsify_rule"],
             c.get("status", "open"), c.get("owner", "Andi")),
        )
        n += 1
    return n


def main() -> int:
    with connect() as conn:
        a = load_anchors(conn)
        m = load_metrics(conn)
        o = load_overclaims(conn)
        conn.commit()
    print(f"✓ 已加载：{a} 锚点 / {m} 指标 / {o} 登记簿断言")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
