"""衍生追踪指标本体(代码即真相)——从 fundamentals 原始序列计算的二阶信息。

原始 KPI(metric_packs.py)回答"是多少",衍生指标回答"在怎么变":同比增速(yoy)、
增速的二阶导(yoy_accel,加速 or 减速)、比率(ratio_to,如 cRPO/营收)、趋势斜率
(slope4,如 NRR 的方向)。它们是投资论点验证点(VerificationPoint.metric)与支柱
watch_metrics 的合法取值,让"cRPO 同比增速跌破 12.5% 即证空"这类判定可被机器复核。

关键约束(与 metric_packs 的边界):
  · 衍生指标**独立注册**,绝不注入 SPEC_BY_KEY / ALIAS_TO_KEY —— 它们只能被
    research/indicators.py 计算,严禁成为 LLM 抽取目标(否则"抽取出来的同比"会污染
    "计算出来的同比")。计算引擎读取 fundamentals 时以 `source <> 'derived'` 排除自身
    产物,防"衍生的衍生"反馈环。
  · base_metric 必须 ∈ metric_packs.SPEC_BY_KEY(tests/test_thesis_ontology.py 强制),
    保证每个衍生指标都锚在一个已存在的规范 KPI 上。
  · 策展原则:一个衍生指标存在,当且仅当有 VerificationPoint / watch_metric 会用它 ——
    不做 200 specs × 5 变换的笛卡尔积。
"""
from __future__ import annotations

from dataclasses import dataclass

# 变换族(research/indicators.py 逐一实现;财年安全,按 period_end 日窗配对):
#   yoy        —— 同比:value(t) / value(t-1y) - 1
#   qoq        —— 环比:value(t) / value(t-1q) - 1
#   yoy_accel  —— 同比增速的二阶导:yoy(t) - yoy(t-1q)(>0 加速,<0 减速)
#   ratio_to   —— 与另一指标同期比值:value(base) / value(other)
#   slope4     —— 近 4 点最小二乘斜率 / 均值(归一化趋势方向)
TRANSFORMS: tuple[str, ...] = ("yoy", "qoq", "yoy_accel", "ratio_to", "slope4")


@dataclass(frozen=True)
class IndicatorSpec:
    key: str                     # 写入 fundamentals.metric 的衍生指标 key(如 'crpo_yoy')
    label_zh: str
    base_metric: str             # 主输入,必须 ∈ metric_packs.SPEC_BY_KEY
    transform: str               # ∈ TRANSFORMS
    other_metric: str = ""       # ratio_to 的分母(必须 ∈ SPEC_BY_KEY)
    unit: str = "ratio"          # ratio | x
    higher_is_better: bool = True
    min_points: int = 5          # 序列点数不足则不计算(避免噪声)


_I = IndicatorSpec

# ── 策展的衍生指标谱系(~24 个;按 base 家族聚类)────────────────────────────────
INDICATORS: tuple[IndicatorSpec, ...] = (
    # 营收动能(全行业)
    _I("revenue_yoy", "营收同比", "revenue", "yoy"),
    _I("revenue_yoy_accel", "营收增速二阶导", "revenue", "yoy_accel"),
    _I("eps_yoy", "摊薄 EPS 同比", "eps_diluted", "yoy"),
    # 盈利质量趋势
    _I("gross_margin_trend", "毛利率趋势(近4季斜率)", "gross_margin", "slope4"),
    _I("fcf_margin_trend", "FCF 利润率趋势", "fcf_margin", "slope4"),
    _I("subscription_gm_trend", "订阅毛利率趋势", "subscription_gross_margin", "slope4"),
    # 资本开支 / 库存(周期与卡位)
    _I("capex_yoy", "资本开支同比", "capex", "yoy"),
    _I("inventory_to_revenue", "库存/营收", "inventory", "ratio_to",
       other_metric="revenue", higher_is_better=False),
    _I("doi_trend", "库存天数趋势", "doi_days", "slope4", higher_is_better=False),
    # SaaS 递延收入引擎(软件链核心,ServiceNow 范式)
    _I("arr_yoy", "ARR 同比", "arr", "yoy"),
    _I("net_new_arr_yoy", "净新增 ARR 同比", "net_new_arr", "yoy"),
    _I("rpo_yoy", "RPO 同比", "rpo", "yoy"),
    _I("crpo_yoy", "当期 RPO 同比", "crpo", "yoy"),
    _I("crpo_yoy_accel", "cRPO 增速二阶导", "crpo", "yoy_accel"),
    _I("billings_yoy", "计算账单同比", "billings", "yoy"),
    _I("crpo_to_revenue", "cRPO/营收(可见度)", "crpo", "ratio_to", other_metric="revenue"),
    # SaaS 留存与客户
    _I("nrr_trend", "净收入留存趋势", "nrr", "slope4"),
    _I("customers_yoy", "客户数同比", "customers_count", "yoy"),
    _I("large_customers_yoy", "大客户(>$100k)同比", "large_customers", "yoy"),
    # 订单可见度(半导体/工业/机器人)
    _I("backlog_yoy", "在手订单同比", "backlog", "yoy"),
    _I("book_to_bill_trend", "订单出货比趋势", "book_to_bill", "slope4"),
    # 平台 / 消费
    _I("arpu_yoy", "ARPU 同比", "arpu", "yoy"),
    _I("active_buyers_yoy", "活跃买家同比", "active_buyers", "yoy"),
    _I("net_new_units_yoy", "净新增门店同比", "net_new_units", "yoy"),
)

INDICATOR_BY_KEY: dict[str, IndicatorSpec] = {i.key: i for i in INDICATORS}
ALL_INDICATOR_KEYS: list[str] = [i.key for i in INDICATORS]
# 一个衍生指标依赖的全部 base 指标(计算引擎按此拉取 fundamentals 序列)。
BASE_METRICS: set[str] = (
    {i.base_metric for i in INDICATORS}
    | {i.other_metric for i in INDICATORS if i.other_metric})


def _company_metric_keys(company: dict | None) -> set[str]:
    from .metric_packs import kpis_for_company
    return {s.key for s in kpis_for_company(company, include_core=True)}


def indicator_keys_for_company(company: dict | None) -> list[str]:
    """该公司行业适用的衍生指标 key —— 其 base(及 ratio_to 的 other)都在公司 KPI 谱内。

    这是 research/thesis.py:dossier 注入 known_indicators 的口径,也是 VerificationPoint
    /watch_metric 校验的合法域。"""
    have = _company_metric_keys(company)
    out: list[str] = []
    for spec in INDICATORS:
        if spec.base_metric not in have:
            continue
        if spec.transform == "ratio_to" and spec.other_metric not in have:
            continue
        out.append(spec.key)
    return out
