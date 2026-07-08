"""季报事件交易本体:8 维分析框架 + 裁决 schema + 策展 universe(代码即真相)。

`EarningsVerdict` 兼作 LLM 结构化输出 schema(models.llm.complete_json)与入库 content;
`validate_verdict` 是"宁缺毋滥"的证据密度门(与 ontology.thesis.validate_thesis 同哲学)。

尺度隔离:`EarningsVerdict.conviction` 是 **0-10 事件交易尺度**(≥7 可操作),与
`CompanyThesis.conviction`(1-5 论点尺度)是两个独立模型两个域,不换算、不混存。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# 8 维分析框架(dossier 节与 LLM 维度打分共用词表)。
EARNINGS_DIMENSIONS: tuple[str, ...] = (
    "guidance_habit",            # 指引惯例:beat-and-raise 历史 + guidance 兑现率
    "consensus_setup",           # 预期设定:一致预期水位 + 90 天修订方向
    "positioning_sentiment",     # 仓位与情绪:评级动量/PT 空间/社媒极性
    "alt_tracking",              # 数据追踪:另类信号 + 专家/渠道洞见
    "implied_vs_expected_move",  # 期权定价:implied move vs 自己预期的分布
    "valuation_cushion",         # 估值安全垫/拥挤度
    "thesis_alignment",          # 与长期论点/争论天平的一致性
    "event_risk",                # 特异事件风险(诉讼/宏观打印/同业财报串扰)
)
DIRECTIONS: tuple[str, ...] = ("long", "short", "no_trade")

# conviction≥ 此值视为可操作;≥7 触发证据密度门(≥ _MIN_ANCHORS 个去重锚 + 不对称论证)。
ACTIONABLE_CONVICTION = 7.0
_MIN_ANCHORS = 6


class DimensionRead(BaseModel):
    key: str = Field(description="必须 ∈ " + " / ".join(EARNINGS_DIMENSIONS))
    score: float = Field(ge=-2, le=2)           # -2 强空 .. +2 强多
    note_zh: str
    evidence: list[str] = Field(default_factory=list)   # dossier 接地 id,逐字抄


class EarningsVerdict(BaseModel):
    direction: str                              # ∈ DIRECTIONS
    conviction: float = Field(ge=0, le=10)      # ≥7 可行动
    expected_surprise_zh: str                   # 对本次 print 的预期差判断(方向+理由)
    move_view_zh: str                           # implied vs 自己预期波动的观点(贵/便宜/合理)
    dimensions: list[DimensionRead] = Field(min_length=4, max_length=8)
    plan_zh: str                                # 进出场计划(何时进、财报后何时出)
    falsifiers_zh: list[str] = Field(min_length=1, max_length=4)   # 盘前证伪条件
    asymmetry_zh: str = ""                      # 赔率不对称论证(conviction≥7 必填)
    no_trade_reason_zh: str = ""                # direction=no_trade 时必填


def _anchor_ids(v: EarningsVerdict) -> set[str]:
    """裁决引用的全部去重证据 id。"""
    return {e for d in v.dimensions for e in d.evidence}


def validate_verdict(v: EarningsVerdict, *, known_ids: set[str] | None = None) -> list[str]:
    """返回违规清单(空 = 通过)。known_ids 形如 {'estimate:now:eps_diluted', 'calendar:12'}。

    五规则(宁缺毋滥):
    ① 每个 evidence id ∈ known_ids(精确串匹配,禁幻觉);
    ② 每个 dimension.key ∈ EARNINGS_DIMENSIONS 且不重复;
    ③ conviction≥7 → 去重 evidence 锚 ≥6 ∧ asymmetry_zh 非空 ∧ direction≠no_trade;
    ④ direction=no_trade → conviction==0 ∧ no_trade_reason_zh 非空;
    ⑤ direction ∈ DIRECTIONS。
    """
    problems: list[str] = []
    if v.direction not in DIRECTIONS:
        problems.append(f"direction {v.direction!r} not in {DIRECTIONS}")
    seen_dims: set[str] = set()
    for d in v.dimensions:
        if d.key not in EARNINGS_DIMENSIONS:
            problems.append(f"dimension key {d.key!r} invalid")
        elif d.key in seen_dims:
            problems.append(f"dimension key {d.key!r} duplicated")
        seen_dims.add(d.key)
        if known_ids is not None:
            for e in d.evidence:
                if e not in known_ids:
                    problems.append(f"dimension {d.key}: unknown evidence {e!r}")
    if v.direction == "no_trade":
        if v.conviction != 0:
            problems.append(f"no_trade must have conviction=0 (got {v.conviction})")
        if not v.no_trade_reason_zh.strip():
            problems.append("no_trade requires no_trade_reason_zh")
    elif v.conviction >= ACTIONABLE_CONVICTION:
        n = len(_anchor_ids(v))
        if n < _MIN_ANCHORS:
            problems.append(f"conviction {v.conviction} needs ≥{_MIN_ANCHORS} distinct evidence anchors (got {n})")
        if not v.asymmetry_zh.strip():
            problems.append("conviction≥7 requires asymmetry_zh")
    return problems


# ── 策展 universe(代码即真相,debates.DEBATE_SEEDS 同构)────────────────────────────
# ~40 个 registry company_id,期权流动性人工判断挑选。测试(test_earnings_ontology.py)强制:
# 每 id ∈ COMPANIES ∧ 有无后缀 US ticker(纯字母,无 '.')∧ 无重复。CN/HK 名字本期不入
# (无个股期权,期标签归一化后置)。
EARNINGS_UNIVERSE: tuple[str, ...] = (
    # AI 软件 / SaaS
    "now", "crm", "snow", "ddog", "net", "crwd", "pltr", "panw",
    # AI 半导体 / 算力
    "nvidia", "amd", "broadcom", "micron", "u_us_dell", "arm", "marvell",
    # AI 光通信
    "coherent", "arista",
    # 互联网平台
    "googl", "meta", "amzn", "nflx", "uber",
    # 消费 / 零售 / 餐饮
    "wmt", "cost", "mcd", "cmg", "sbux", "u_us_dis",
    # 人形机器人 / 具身
    "tsla_hum",
    # 商业航天
    "rklb_spa", "asts_spa",
)


def earnings_universe(cap: int | None = None) -> list[dict]:
    """策展 universe ∩ registry → company dict 列表(cap 截断,默认 config.earnings_universe_cap)。
    registry 里缺失的 id 静默跳过(策展列表可能领先/落后于名册)。"""
    from ..ingestion.registry import company_by_id

    if cap is None:
        from ..config import get_settings

        cap = get_settings().earnings_universe_cap
    out: list[dict] = []
    for cid in EARNINGS_UNIVERSE:
        c = company_by_id(cid)
        if c:
            out.append(c)
        if cap and len(out) >= cap:
            break
    return out
