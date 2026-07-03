"""识别引擎 —— 把"待识别假说"真正跑成识别后的判决（Phase 2.2 内核）。

差异化纪律第二条：**绝不把横截面相关当因果回报**。v1 内核对依赖 DID / 面板固定效应
的 soft 断言一律判 inconclusive（`engine/overclaim.py` 的 NotEvaluable 路径）。本模块把
那条"未支持→inconclusive"升级为**真实统计估计**：

  · `did_two_way_fe`  —— 双向固定效应（unit FE + time FE）+ treat×post 交互项的 DID。
    任何**纯时间维**混淆（利率、裁员周期）被 time FE 自动吸收 —— 这正是 K 文"对立假说=
    利率+裁员周期"在计量上的正解：把周期效应交给 γ_t，交互项 β 才是净 AI 暴露效应。
  · `within_fixed_effects` —— 个体固定效应（worker FE + time FE）的 within 估计。
    用于"AI 技能工资溢价是否为因果"：横截面 56% 里混着人/岗位选择；within 估计剥离个体
    恒定能力 α_i 后，回收**同一工人采用 AI 技能前后**的净溢价。

两者都返回点估计 + 标准误 + **真实 Student-t 双尾 p 值**（用正则化不完全 Beta 函数算，
纯 numpy 无 scipy 依赖），交给登记簿规则做 `coef`/`pvalue` 阈值判定。

设计取舍（为什么 SQL 侧不重算 DID）：识别只在此处**算一次**，结果（系数 / p 值）作为派生
observation 双时态写库；dbt 侧用既有 `value()`/`pit_value` 读同一持久化估计 —— 两条路径读
**同一行**，故 `assert_overclaim_parity` 天然为绿，无需在 SQL 里重写固定效应回归。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Estimate:
    """一次识别的结果：感兴趣系数的点估计、标准误、t 值、自由度、双尾 p 值、样本量。"""

    coef: float
    se: float
    t: float
    df: int
    pvalue: float
    n_obs: int

    def significant(self, alpha: float = 0.05) -> bool:
        return self.pvalue < alpha


# ── Student-t 双尾 p 值（正则化不完全 Beta，Numerical Recipes betacf）────────────
def _betacf(a: float, b: float, x: float) -> float:
    """连分式展开 I_x(a,b) 的核（Lentz 法），收敛快、数值稳。"""
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def reg_incomplete_beta(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lnbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lnbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def student_t_sf_two_sided(t: float, df: int) -> float:
    """T~t(df) 的双尾 p 值 = P(|T| >= |t|) = I_{df/(df+t^2)}(df/2, 1/2)。"""
    if df <= 0:
        return float("nan")
    if not math.isfinite(t):
        return 0.0
    x = df / (df + t * t)
    return reg_incomplete_beta(df / 2.0, 0.5, x)


# ── 最小二乘 + 单系数推断 ──────────────────────────────────────────────────────
def ols_coef_inference(X: np.ndarray, y: np.ndarray, target_col: int) -> Estimate:
    """对设计矩阵 X、响应 y 做 OLS，返回第 target_col 个系数的推断。

    用 pinv 稳健应对（被吸收的）共线虚拟变量；自由度用矩阵秩算（df = n - rank）。
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1)
    n = X.shape[0]
    XtX = X.T @ X
    XtX_inv = np.linalg.pinv(XtX)
    beta = XtX_inv @ (X.T @ y)
    resid = y - X @ beta
    rank = int(np.linalg.matrix_rank(X))
    df = n - rank
    if df <= 0:
        raise ValueError(f"自由度非正（n={n}, rank={rank}）：面板信息不足以做推断。")
    sigma2 = float(resid @ resid) / df
    var_beta = sigma2 * XtX_inv
    coef = float(beta[target_col])
    se = float(math.sqrt(max(var_beta[target_col, target_col], 0.0)))
    t = coef / se if se > 0 else float("inf")
    p = student_t_sf_two_sided(t, df)
    return Estimate(coef=coef, se=se, t=t, df=df, pvalue=p, n_obs=n)


# ── 设计矩阵：固定效应虚拟编码（drop-one 基准 + 截距）────────────────────────────
def _dummies(labels: list, drop_first: bool = True) -> tuple[np.ndarray, list]:
    levels = sorted(set(labels))
    if drop_first:
        levels = levels[1:]  # 留一作基准，避免与截距完全共线
    cols = [np.array([1.0 if lab == lv else 0.0 for lab in labels]) for lv in levels]
    M = np.column_stack(cols) if cols else np.empty((len(labels), 0))
    return M, levels


@dataclass(frozen=True)
class PanelRow:
    unit_id: str
    period: str  # ISO date 字符串（仅作离散时间标签）
    outcome: float
    treated: bool
    post: bool  # DID：是否处于处理后期


def did_two_way_fe(rows: list[PanelRow]) -> Estimate:
    """双向固定效应 DID：outcome ~ 截距 + unitFE + timeFE + (treated×post)。

    treated（unit 级恒定）与 post（time 级恒定）的主效应被 unit/time FE 吸收，故只放交互项；
    所有纯时间维混淆（利率、裁员周期）被 time FE 吸收。返回交互项系数的推断。
    """
    units = [r.unit_id for r in rows]
    periods = [r.period for r in rows]
    y = np.array([r.outcome for r in rows], dtype=float)
    interaction = np.array([1.0 if (r.treated and r.post) else 0.0 for r in rows])

    unit_d, _ = _dummies(units)
    time_d, _ = _dummies(periods)
    intercept = np.ones((len(rows), 1))
    # 交互项放在第 1 列（index 0 之后），便于定位 target_col。
    X = np.column_stack([interaction, intercept, unit_d, time_d])
    return ols_coef_inference(X, y, target_col=0)


def within_fixed_effects(rows: list[PanelRow], regressor: list[float]) -> Estimate:
    """个体固定效应 within 估计：outcome ~ 截距 + unitFE + timeFE + regressor。

    regressor 是与 rows 等长的时变自变量（如"该工人本期是否已采用 AI 技能"0/1）。
    unit FE 吸收个体恒定能力 α_i（选择偏差的主要来源），回收 within 净效应。
    """
    units = [r.unit_id for r in rows]
    periods = [r.period for r in rows]
    y = np.array([r.outcome for r in rows], dtype=float)
    x = np.array(regressor, dtype=float)

    unit_d, _ = _dummies(units)
    time_d, _ = _dummies(periods)
    intercept = np.ones((len(rows), 1))
    X = np.column_stack([x, intercept, unit_d, time_d])
    return ols_coef_inference(X, y, target_col=0)


def naive_cross_section(rows: list[PanelRow], regressor: list[float]) -> Estimate:
    """对照：朴素横截面 OLS（outcome ~ 截距 + regressor），不控个体 FE。

    用于审讯展示——同一面板里，横截面斜率（混入选择）vs within 斜率（剥离选择）的落差，
    正是"绝不把横截面相关当因果"的可视化证据。
    """
    y = np.array([r.outcome for r in rows], dtype=float)
    x = np.array(regressor, dtype=float)
    X = np.column_stack([x, np.ones(len(rows))])
    return ols_coef_inference(X, y, target_col=0)
