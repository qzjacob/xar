"""识别面板 + 识别运行器 —— Phase 2.2 的数据侧与编排侧。

把 `engine/identification.py` 的纯估计器接到双时态库上：
  1. `panel_observation`（双时态微观面板）承载 DID / 个体面板的原始观测（unit×period）。
  2. `run_identification(as_of)` 在 **point-in-time**（knowledge_time<=as_of）下读面板、跑估计、
     把"系数 / p 值"作为**派生 observation** 双时态写回 —— 登记簿规则随后用既有 `value()` 读它。

⚠ 诚实登记（与 §7 数据缺口纪律一致）：本文件内的两张面板是**确定性的「示例面板」**，
   是真实数据落库前的可复现占位（source_grade=D_derived），用来端到端验证**识别机制**本身。
   - junior：占位 Indeed Hiring Lab + AI 暴露度分类的 DID 面板；接真数据后此处替换，verdict 自动更新。
   - wage：占位个体雇主—雇员链接面板（PwC 56% 横截面 → within 净溢价）。
   机制（双向 FE 吸收周期混淆、within 剥离选择）是真的；**具体 verdict 取决于面板**，会随真数据变。
   交付的是可审计的「识别流水线」，不是对该断言的终局结论。

    python -m ingestion.identification_panels [YYYY-MM-DD]
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np

from slx.engine.identification import (
    Estimate,
    did_two_way_fe,
    naive_cross_section,
    within_fixed_effects,
    PanelRow,
)
from slx.ingestion.base import git_commit, sha256_payload
from slx.db import connect

# ── 派生估计指标键（与 registry/metrics/identification_results.yml 逐字一致）─────────
M_JUNIOR = "labor.junior_postings_high_vs_low_ai_exposure"
M_WAGE = "labor.ai_skill_wage_premium"
DERIVED_KEYS = [
    f"{M_JUNIOR}.did.coef",
    f"{M_JUNIOR}.did.pvalue",
    f"{M_WAGE}.fe.coef",
    f"{M_WAGE}.fe.pvalue",
]
IDENT_SOURCE = "identification"
_SEED = 20260601  # 固定种子 → 面板与 snapshot_hash 完全可复现

# ── panel_observation 双时态微观面板表（与 db/schema.sql 同义；此处幂等保证已建库可用）──
PANEL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS panel_observation (
    panel_key      text        NOT NULL,                 -- 它所识别的 base metric_key
    unit_id        text        NOT NULL,                 -- 个体/职类单元
    period         date        NOT NULL,                 -- 离散时间索引
    treated        boolean     NOT NULL,                 -- DID 处理组 / within 采用者
    post           boolean     NOT NULL DEFAULT false,   -- DID 处理后期
    regressor      numeric     NOT NULL DEFAULT 0,        -- within 时变自变量（本期是否已采用 AI 技能）
    outcome        numeric     NOT NULL,
    covariates     jsonb       NOT NULL DEFAULT '{}',     -- 透明记录的时间维混淆（被 time FE 吸收）
    valid_time     timestamptz NOT NULL,
    knowledge_time timestamptz NOT NULL,                 -- 双时态：那天能知道这条面板观测吗
    ingest_run_id  uuid,
    snapshot_hash  text,
    PRIMARY KEY (panel_key, unit_id, period, knowledge_time)
);
CREATE INDEX IF NOT EXISTS idx_panel_pit
    ON panel_observation (panel_key, period, knowledge_time DESC);
"""


def ensure_panel_table(conn) -> None:
    conn.execute(PANEL_TABLE_DDL)
    conn.commit()


def _q(year: int, q: int) -> date:
    return [date(year, 3, 31), date(year, 6, 30), date(year, 9, 30), date(year, 12, 31)][q - 1]


# ════════════════════════════════════════════════════════════════════════════
# 面板 1：junior_postings —— 高 vs 低 AI 暴露职类的 DID（示例面板）
#   8 职类（4 高暴露=处理 / 4 低暴露=对照）× 8 季（2024Q1–2025Q4），onset=2025Q1。
#   全体共同的时间冲击（加息、裁员周期）→ 被 time FE 吸收；交互项 = 净 AI 暴露效应≈-0.25。
# ════════════════════════════════════════════════════════════════════════════
def build_junior_panel() -> list[dict]:
    rng = np.random.default_rng(_SEED)
    periods = [_q(2024, q) for q in range(1, 5)] + [_q(2025, q) for q in range(1, 5)]
    onset = date(2025, 1, 1)
    # 共同时间效应（所有职类同样承受）：温和趋势 + onset 后的周期性下行（高利率+裁员）。
    fed = {p: (5.25 if p < onset else 4.50 + 0.05 * i) for i, p in enumerate(periods)}
    layoff = {p: (0.2 if p < onset else 1.0 - 0.1 * i) for i, p in enumerate(periods)}
    rows: list[dict] = []
    for g in range(8):
        treated = g < 4
        base = 4.60 + 0.05 * g  # log 岗位指数基线（职类固定效应）
        for i, p in enumerate(periods):
            post = p >= onset
            common = 0.015 * i - (0.18 if post else 0.0)  # 时间维冲击（time FE 吸收）
            did = -0.25 if (treated and post) else 0.0     # 净 AI 暴露效应（待识别的真信号）
            y = base + common + did + float(rng.normal(0, 0.03))
            rows.append({
                "panel_key": M_JUNIOR, "unit_id": f"occ_{g}", "period": p,
                "treated": treated, "post": post, "regressor": 0.0, "outcome": round(y, 6),
                "covariates": {"fed_funds_rate": fed[p], "layoff_cycle": round(layoff[p], 3)},
                "valid_time": p, "knowledge_time": p + timedelta(days=30),
            })
    return rows


# ════════════════════════════════════════════════════════════════════════════
# 面板 2：ai_skill_wage_premium —— 个体固定效应（示例面板）
#   30 工人 × 2 期（pre/post）。高能力 α_i 强相关于"采用 AI 技能"→ 横截面溢价被选择抬高(~0.5)；
#   within（worker FE + time FE）剥离 α_i 后，回收真实净溢价≈0.06 (<0.10 阈值)。
# ════════════════════════════════════════════════════════════════════════════
def build_wage_panel() -> list[dict]:
    rng = np.random.default_rng(_SEED + 1)
    periods = [date(2024, 12, 31), date(2025, 12, 31)]
    rows: list[dict] = []
    for w in range(30):
        ability = 0.9 * (w % 6) / 5.0          # 个体恒定能力 ∈ [0,0.9]
        adopt = 1 if ability >= 0.45 else 0    # 选择：高能力者才采用 AI 技能（强选择偏差源）
        for t, p in enumerate(periods):
            ai_skill = adopt if t == 1 else 0   # post 期采用者才"已接入"
            wage = 3.0 + ability + 0.06 * ai_skill + float(rng.normal(0, 0.015))
            rows.append({
                "panel_key": M_WAGE, "unit_id": f"worker_{w}", "period": p,
                "treated": bool(adopt), "post": (t == 1), "regressor": float(ai_skill),
                "outcome": round(wage, 6), "covariates": {},
                "valid_time": p, "knowledge_time": p + timedelta(days=45),
            })
    return rows


# ── 面板 → panel_observation（双时态 upsert）─────────────────────────────────────
def load_panels(conn, run_id: uuid.UUID) -> int:
    rows = build_junior_panel() + build_wage_panel()
    for r in rows:
        h = hashlib.sha256(
            json.dumps({k: r[k] for k in ("panel_key", "unit_id", "period", "outcome")},
                       sort_keys=True, default=str).encode()).hexdigest()
        conn.execute(
            """INSERT INTO panel_observation
               (panel_key, unit_id, period, treated, post, regressor, outcome, covariates,
                valid_time, knowledge_time, ingest_run_id, snapshot_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (panel_key, unit_id, period, knowledge_time)
               DO UPDATE SET outcome=EXCLUDED.outcome, treated=EXCLUDED.treated,
                 post=EXCLUDED.post, regressor=EXCLUDED.regressor,
                 covariates=EXCLUDED.covariates, ingest_run_id=EXCLUDED.ingest_run_id,
                 snapshot_hash=EXCLUDED.snapshot_hash""",
            (r["panel_key"], r["unit_id"], r["period"], r["treated"], r["post"],
             r["regressor"], r["outcome"], json.dumps(r["covariates"]),
             r["valid_time"], r["knowledge_time"], run_id, h),
        )
    conn.commit()
    return len(rows)


# ── 读 PIT 面板（knowledge_time<=as_of）──────────────────────────────────────────
def _read_panel(conn, panel_key: str, as_of: date) -> list[PanelRow]:
    sql = """
        SELECT DISTINCT ON (unit_id, period)
               unit_id, period, treated, post, regressor, outcome
        FROM panel_observation
        WHERE panel_key = %s AND knowledge_time <= %s
        ORDER BY unit_id, period, knowledge_time DESC
    """
    out = []
    reg = []
    for unit_id, period, treated, post, regressor, outcome in conn.execute(
        sql, (panel_key, as_of)
    ).fetchall():
        out.append(PanelRow(unit_id=unit_id, period=period.isoformat(),
                            outcome=float(outcome), treated=bool(treated), post=bool(post)))
        reg.append(float(regressor))
    return out, reg


# ── 识别运行器：读面板 → 估计 → 把 coef/pvalue 派生写回 observation ─────────────────
def run_identification(as_of: date | None = None) -> dict[str, Estimate]:
    as_of = as_of or date.today()
    know_dt = datetime(as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc)
    run_id = uuid.uuid4()
    estimates: dict[str, Estimate] = {}
    with connect() as conn:
        ensure_panel_table(conn)
        conn.execute(
            "INSERT INTO audit_log (ingest_run_id, source_id, connector, git_commit, status) "
            "VALUES (%s,%s,%s,%s,'running')",
            (run_id, IDENT_SOURCE, "ingestion.identification_panels", git_commit()),
        )
        conn.commit()
        try:
            n_panel = load_panels(conn, run_id)

            junior_rows, _ = _read_panel(conn, M_JUNIOR, as_of)
            wage_rows, wage_reg = _read_panel(conn, M_WAGE, as_of)
            if not junior_rows or not wage_rows:
                raise RuntimeError("PIT 面板为空：as_of 之前无可用面板观测。")

            did = did_two_way_fe(junior_rows)
            fe = within_fixed_effects(wage_rows, wage_reg)
            cs = naive_cross_section(wage_rows, wage_reg)  # 审讯对照（写进 audit 备查）
            estimates = {M_JUNIOR: did, M_WAGE: fe}

            derived = {
                f"{M_JUNIOR}.did.coef": did.coef,
                f"{M_JUNIOR}.did.pvalue": did.pvalue,
                f"{M_WAGE}.fe.coef": fe.coef,
                f"{M_WAGE}.fe.pvalue": fe.pvalue,
            }
            payload_hash = sha256_payload(derived)
            for mk, val in derived.items():
                conn.execute(
                    """INSERT INTO observation
                       (metric_key, source_id, value, unit, valid_time, knowledge_time,
                        snapshot_hash, ingest_run_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (metric_key, source_id, valid_time, knowledge_time, vintage_date)
                       DO UPDATE SET value=EXCLUDED.value, snapshot_hash=EXCLUDED.snapshot_hash,
                         ingest_run_id=EXCLUDED.ingest_run_id""",
                    (mk, IDENT_SOURCE, val, "coef_or_p", know_dt, know_dt, payload_hash, run_id),
                )
            conn.execute(
                "UPDATE audit_log SET status='ok', finished_at=now(), rows_written=%s, "
                "payload_hash=%s, request_meta=%s WHERE ingest_run_id=%s",
                (len(derived), payload_hash,
                 json.dumps({"as_of": as_of.isoformat(), "panel_rows": n_panel,
                             "did_coef": did.coef, "did_p": did.pvalue,
                             "fe_within_coef": fe.coef, "fe_within_p": fe.pvalue,
                             "naive_cross_section_coef": cs.coef}),
                 run_id),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                "UPDATE audit_log SET status='error', finished_at=now(), error=%s WHERE ingest_run_id=%s",
                (str(exc), run_id),
            )
            conn.commit()
            raise
    return estimates


if __name__ == "__main__":
    when = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    est = run_identification(when)
    print(f"识别运行 @ as_of={when}")
    print("─" * 64)
    did, fe = est[M_JUNIOR], est[M_WAGE]
    print(f"  DID  {M_JUNIOR}")
    print(f"       coef={did.coef:+.4f}  se={did.se:.4f}  p={did.pvalue:.5f}  n={did.n_obs}  "
          f"显著={did.significant()}")
    print(f"  FE   {M_WAGE}")
    print(f"       within coef={fe.coef:+.4f}  se={fe.se:.4f}  p={fe.pvalue:.5f}  n={fe.n_obs}  "
          f"显著={fe.significant()}")
