"""识别引擎（Phase 2.2）单测：t 分布 p 值、DID/within 回收、选择剥离、DSL 语法糖。

纯逻辑（不需 DB），快。端到端 verdict + dbt parity 见 test_overclaim_db.py 与 dbt 测试。
"""
from __future__ import annotations

import numpy as np

from slx.engine.identification import (
    did_two_way_fe,
    naive_cross_section,
    student_t_sf_two_sided,
    within_fixed_effects,
    PanelRow,
)
from slx.engine.overclaim import _expand_identification_sugar
from slx.ingestion.identification_panels import build_junior_panel, build_wage_panel, M_JUNIOR, M_WAGE


def test_student_t_pvalue_matches_known_values():
    """双尾 p 值对标已知 t 表值（正则化不完全 Beta 实现正确）。"""
    assert abs(student_t_sf_two_sided(2.0, 48) - 0.0511) < 1e-3
    assert abs(student_t_sf_two_sided(2.626, 14) - 0.0200) < 1e-3
    assert abs(student_t_sf_two_sided(0.0, 10) - 1.0) < 1e-9
    # 单调性：|t| 越大 p 越小。
    assert student_t_sf_two_sided(3.0, 30) < student_t_sf_two_sided(1.0, 30)


def _panelrows(panel_rows):
    rows = [PanelRow(unit_id=r["unit_id"], period=r["period"].isoformat(),
                     outcome=r["outcome"], treated=r["treated"], post=r["post"]) for r in panel_rows]
    reg = [r["regressor"] for r in panel_rows]
    return rows, reg


def test_did_two_way_fe_recovers_negative_significant_effect():
    """junior 示例面板：双向固定效应 DID 回收显著负的净 AI 暴露效应（≈-0.25）。"""
    rows, _ = _panelrows(build_junior_panel())
    est = did_two_way_fe(rows)
    assert est.coef < 0
    assert abs(est.coef - (-0.25)) < 0.05
    assert est.significant(0.05)
    assert est.n_obs == 64


def test_within_fe_shrinks_cross_section_premium():
    """wage 示例面板：横截面溢价被选择抬高，within 剥离 α_i 后回收净溢价 ≈0.06 « 横截面。"""
    rows, reg = _panelrows(build_wage_panel())
    within = within_fixed_effects(rows, reg)
    cross = naive_cross_section(rows, reg)
    assert within.coef < 0.10                      # 低于因果阈值 → 证伪路径
    assert abs(within.coef - 0.06) < 0.03
    # 选择剥离的核心证据：within « 横截面（"勿把相关当因果"的可视化）。
    assert cross.coef > within.coef + 0.20


def test_identification_sugar_expands_to_value_primitive():
    """识别族函数是纯语法糖，展开为 value(<派生指标>)；后缀约定与持久化键一致。"""
    fix = "did_estimate(labor.junior_postings_high_vs_low_ai_exposure) < 0 AND did_pvalue(labor.junior_postings_high_vs_low_ai_exposure) < 0.05"
    out = _expand_identification_sugar(fix)
    assert "value(labor.junior_postings_high_vs_low_ai_exposure.did.coef)" in out
    assert "value(labor.junior_postings_high_vs_low_ai_exposure.did.pvalue)" in out
    assert "did_estimate(" not in out and "did_pvalue(" not in out

    wage = "panel_fixed_effects(labor.ai_skill_wage_premium) > 0.10 AND panel_fixed_effects_pvalue(labor.ai_skill_wage_premium) < 0.05"
    out2 = _expand_identification_sugar(wage)
    # `\(` 锚点：panel_fixed_effects( 不误吃 panel_fixed_effects_pvalue(。
    assert "value(labor.ai_skill_wage_premium.fe.coef)" in out2
    assert "value(labor.ai_skill_wage_premium.fe.pvalue)" in out2
    assert "panel_fixed_effects(" not in out2 and "panel_fixed_effects_pvalue(" not in out2


def test_panels_are_deterministic():
    """固定种子 → 面板逐行可复现（snapshot_hash 稳定的前提）。"""
    a = build_junior_panel() + build_wage_panel()
    b = build_junior_panel() + build_wage_panel()
    assert [r["outcome"] for r in a] == [r["outcome"] for r in b]
    assert {r["panel_key"] for r in a} == {M_JUNIOR, M_WAGE}
