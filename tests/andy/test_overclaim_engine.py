"""登记簿判定引擎单元测试（无需 DB）：用 FakeCtx 直接喂读数，覆盖三条分支。"""
from __future__ import annotations

from datetime import date

from slx.engine.overclaim import evaluate_claim
from slx.engine.point_in_time import NoData


class FakeCtx:
    def __init__(self, values=None, slopes=None, as_of=date(2026, 6, 23)):
        self.values = values or {}
        self.slopes = slopes or {}
        self.as_of = as_of

    def value(self, m, source_id=None):
        if m not in self.values:
            raise NoData(m)
        return self.values[m]

    def slope(self, m, n):
        if m not in self.slopes:
            raise NoData(m)
        return self.slopes[m]


def test_falsified_branch(claims):
    # Mag7 盈利贡献跌破 0.33 且"其余 493"EPS 增速上行 → 断言被证伪
    ctx = FakeCtx(values={"earnings.mag7_contribution_pct": 0.31},
                  slopes={"earnings.rest493_eps_growth_pct": 0.008})
    verdict, _ = evaluate_claim(claims["concentration_eq_earnings"], ctx)
    assert verdict == "falsified"


def test_fixation_branch(claims):
    # Mag7 贡献 ≥0.50 且"其余"增速掉头向下 → 断言固化
    ctx = FakeCtx(values={"earnings.mag7_contribution_pct": 0.55},
                  slopes={"earnings.rest493_eps_growth_pct": -0.01})
    verdict, _ = evaluate_claim(claims["concentration_eq_earnings"], ctx)
    assert verdict == "fixation_triggered"


def test_open_when_neither_rule_fires(claims):
    ctx = FakeCtx(values={"earnings.mag7_contribution_pct": 0.40},
                  slopes={"earnings.rest493_eps_growth_pct": 0.008})
    verdict, _ = evaluate_claim(claims["concentration_eq_earnings"], ctx)
    assert verdict == "open"


def test_soft_claim_is_inconclusive(claims):
    # 依赖 did_estimate/significant 的 soft 断言：v1 一律 inconclusive（绝不把相关当因果）
    verdict, _ = evaluate_claim(claims["junior_jobs_minus67_is_ai"], FakeCtx())
    assert verdict == "inconclusive"


def test_legitimacy_claim_inconclusive(claims):
    verdict, _ = evaluate_claim(claims["marginalization_unstable"], FakeCtx())
    assert verdict == "inconclusive"
