"""裁决质量的 A/B 评测 —— 「换个模型/换版提示词,判得更准了吗」。

按队列(模型 / 提示词版本 / 回放对照)分组算校准:每个 conviction 桶的命中率与平均反应。
复用 `distribution.calibration_buckets`,**全程停留在 Phanny 的 1-10 刻度内** ——
thesis(1-5)与 ET(0-10)的裁决不进这些查询,三个域永不混算。
"""
from __future__ import annotations

from ..logging import get_logger
from ..storage import db
from . import distribution as dist

log = get_logger("xar.phanny.evaluate")

_BASE = ("SELECT v.conviction, v.outcome FROM phanny_verdicts v "
         "WHERE v.outcome->>'status'='scored'")


def _buckets(rows: list[dict]) -> dict:
    """calibration_buckets 收的是 [{conviction, outcome}] 形状的 dict 列表(不是元组)。"""
    return dist.calibration_buckets(
        [{"conviction": float(r["conviction"]), "outcome": r["outcome"] or {}} for r in rows])


def compare(by: str = "model", *, limit_groups: int = 12) -> dict:
    """按 `model` | `template` | `variant` 分组比较校准。

    - `model`   —— 哪家模型判得准(debate_models 记的是实际参与的模型);
    - `template`—— 哪一版提示词判得准(经 build_snapshots 的 template_ver 关联);
    - `variant` —— 生产 vs 回放对照(回放也被回验打分,这正是 A/B 的意义)。
    """
    groups: dict[str, list[dict]] = {}
    try:
        if by == "template":
            # 提示词版本经构建快照关联到裁决 —— 这正是 M5 记 (template, version) 的用途:
            # 没有它,「换了提示词之后判得更准吗」永远只能靠时间猜。
            for r in db.query(
                    "SELECT DISTINCT ON (v.id) v.conviction, v.outcome, "
                    "       s.prompt_template, s.template_ver "
                    "FROM phanny_verdicts v "
                    "JOIN phanny_build_snapshots s ON s.build_id = v.build_id "
                    "WHERE s.stage='propose' AND v.outcome->>'status'='scored' "
                    "ORDER BY v.id, s.id"):
                groups.setdefault(f"{r['prompt_template']}@v{r['template_ver']}", []).append(r)
        elif by == "variant":
            for r in db.query("SELECT variant, conviction, outcome FROM phanny_verdicts "
                              "WHERE outcome->>'status'='scored'"):
                groups.setdefault(r["variant"] or "prod", []).append(r)
        else:      # model
            for r in db.query("SELECT model, conviction, outcome FROM phanny_verdicts "
                              "WHERE outcome->>'status'='scored'"):
                groups.setdefault(r["model"] or "?", []).append(r)
    except Exception as e:  # noqa: BLE001
        log.warning("evaluate.compare(%s) failed: %s", by, str(e)[:160])
        return {"by": by, "error": str(e)[:160], "groups": []}

    out = []
    for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit_groups]:
        hits = [r for r in rows if (r["outcome"] or {}).get("direction_hit") is not None]
        n_hit = sum(1 for r in hits if (r["outcome"] or {}).get("direction_hit"))
        out.append({"group": name, "n": len(rows),
                    "hit_rate": round(n_hit / len(hits), 3) if hits else None,
                    "buckets": _buckets(rows)})
    return {"by": by, "scale": "phanny_1_10", "groups": out,
            "note": "仅 Phanny 1-10 刻度;thesis(1-5)/ET(0-10)不进此统计"}


def replay_pairs(limit: int = 50) -> list[dict]:
    """回放 vs 原判的逐对对照(同一份证据、不同模型/提示词各判了什么)。"""
    try:
        return db.query(
            "SELECT r.id AS replay_id, r.model AS replay_model, r.direction AS replay_direction, "
            "       r.conviction AS replay_conviction, r.outcome AS replay_outcome, "
            "       o.id AS orig_id, o.model AS orig_model, o.direction AS orig_direction, "
            "       o.conviction AS orig_conviction, o.outcome AS orig_outcome, "
            "       o.company_id, o.event_date "
            "FROM phanny_verdicts r JOIN phanny_verdicts o ON o.id = r.replay_of "
            "WHERE r.variant='replay' ORDER BY r.created_at DESC LIMIT %s", (limit,))
    except Exception as e:  # noqa: BLE001
        log.warning("replay_pairs failed: %s", str(e)[:160])
        return []
