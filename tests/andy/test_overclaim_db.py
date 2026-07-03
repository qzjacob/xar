"""登记簿活监控端到端（需 DB）：真实数据流过 point-in-time → 引擎自动判定 → 写状态。"""
from __future__ import annotations

from datetime import date

from tests.andy.conftest import requires_db


@requires_db
def test_concentration_claim_falsified_on_seed(seeded):
    """seed 的当前数据使"市值集中度=盈利集中度"被自动判为 falsified。"""
    from slx.engine.overclaim import run

    verdicts = dict(run(date(2026, 6, 23)))
    assert verdicts["concentration_eq_earnings"] == "falsified"


@requires_db
def test_identified_soft_claims_get_real_verdict(seeded):
    """Phase 2.2：接入识别后，两条 soft 断言走真实 DID/面板得出判决，不再 inconclusive。"""
    from slx.engine.overclaim import run

    verdicts = dict(run(date(2026, 6, 23)))
    # 双向固定效应 DID 显著负 → "初级岗位锐减是 AI 位移"固化。
    assert verdicts["junior_jobs_minus67_is_ai"] == "fixation_triggered"
    # 个体固定效应 within 净溢价 < 0.10（« 横截面 56%）→ "56% 是因果回报"被证伪。
    assert verdicts["ai_wage_premium_causal"] == "falsified"


@requires_db
def test_still_unidentified_soft_claims_remain_inconclusive(seeded):
    """仍依赖未支持算子（structural_change/beta 剥离/会计稳健性…）的 soft 断言绝不被误判成立。"""
    from slx.engine.overclaim import run

    verdicts = dict(run(date(2026, 6, 23)))
    for ck in ("ai_stripped_zero_growth", "china_visible_hand",
               "marginalization_unstable", "rsp_crack_appeared",
               "ai_capital_loop_steady_engine"):
        assert verdicts[ck] == "inconclusive", ck


@requires_db
def test_verdict_persisted_to_registry(seeded):
    """评估结果写回 overclaim_registry.status 并留痕 eval_log。"""
    from slx.engine.overclaim import run
    from slx.db import connect

    run(date(2026, 6, 23))
    with connect() as c:
        status = c.execute(
            "SELECT status FROM overclaim_registry WHERE claim_key='concentration_eq_earnings'"
        ).fetchone()[0]
        assert status == "falsified"
        logged = c.execute(
            "SELECT count(*) FROM overclaim_eval_log WHERE claim_key='concentration_eq_earnings'"
        ).fetchone()[0]
        assert logged >= 1
