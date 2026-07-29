"""Phanny 季报多空事件交易本体:6 维分析 + 强制 long/short 裁决 schema + 校验纪律。

尺度隔离(三域并列,互不换算/混存):
  · PhannyProposal.conviction —— **1-10 Phanny 事件多空域**(强制 long/short,无 neutral/no_trade)
  · EarningsVerdict.conviction —— 0-10 ET 事件域(允许 no_trade)
  · CompanyThesis.conviction  —— 1-5 论点域

`PhannyProposal` 兼作 LLM 结构化输出 schema(models.llm.complete_json)与入库 content 主体;
`validate_proposal` 是"接地 + 六维齐全 + 高信念证据密度门 + 禁 neutral/禁期权策略作输出"的纪律门
(与 ontology.earnings_events.validate_verdict 同哲学)。**size 由代码侧确定性算,LLM 不报**。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

PHANNY_DIMENSIONS: tuple[str, ...] = (
    "fundamental",        # 基本面:一致预期/修订漂移/guidance 习惯/beat 率/估值垫
    "technical",          # 技术面:价格结构/动量/均线偏离/RV/财报前 positioning
    "capital_flow",       # 资金面:ETF/主力资金/13F/short-interest/OBV/内部人
    "sentiment",          # 情绪面:社媒极性/语义事实/评级动量/PT 空间/专家洞见
    "options_structure",  # 期权结构(仅作输入信号,非策略输出):implied move/IV/skew/term/IV-RV gap
    "probability_odds",   # 概率赔率:分布/不对称/赢面×赔付/expected surprise vs priced move
)
DIRECTIONS: tuple[str, ...] = ("long", "short")   # 无 neutral / 无 no_trade
ACTIONABLE_CONVICTION = 7.0
_MIN_ANCHORS = 6

# 期权/结构化策略关键词闸:出现在方向/计划表达里即"把期权策略当交易观点",违反 brief。
# (options_structure 维度里描述 IV/skew/隐含波动 属证据引用,合法;此闸只扫 plan_zh。)
_OPTION_STRATEGY_KW = (
    "straddle", "strangle", "iron condor", "condor", "butterfly", "calendar spread",
    "vertical spread", "credit spread", "debit spread", "risk reversal", "collar",
    "跨式", "宽跨式", "铁鹰", "铁蝶", "蝶式", "日历价差", "垂直价差", "价差组合", "领口期权",
)


class DimensionRead(BaseModel):
    key: str = Field(description="必须 ∈ " + " / ".join(PHANNY_DIMENSIONS))
    score: float = Field(ge=-2, le=2)                    # -2 强空 .. +2 强多
    note_zh: str
    evidence: list[str] = Field(default_factory=list)    # dossier 接地 id,逐字抄


class PhannyProposal(BaseModel):
    """提议者/裁决者的结构化输出。**不含 size_pct**(size 由 phanny.sizing 代码侧算)。"""
    direction: str                                       # ∈ {long, short}
    conviction: float = Field(ge=1, le=10)               # 该名信念(1 极弱 .. 10 极强)
    dimensions: list[DimensionRead] = Field(min_length=1, max_length=6)
    expected_surprise_zh: str = ""
    move_view_zh: str = ""                               # implied vs 自己预期:贵/便宜/合理
    asymmetry_zh: str = ""                               # 赔率不对称论证(conviction≥7 必填)
    plan_zh: str = ""                                    # 进出场(禁期权策略名作主策略)
    falsifiers_zh: list[str] = Field(default_factory=list)     # 盘前可观测证伪
    prob_bins: list[float] = Field(default_factory=list)       # 5 分箱 [>+5,+2~5,-2~+2,-5~-2,<-5]
    e_return_pct: float = 0.0                            # T+1 期望收益(%)
    catalysts_zh: list[str] = Field(default_factory=list)


class CriticVote(BaseModel):
    """反方 critic 的 signed-Δ 结构化输出(多 LLM 对抗协议)。"""
    direction_vote: str = "abstain"                      # agree | disagree | abstain
    conviction_delta: float = Field(default=0.0, ge=-2, le=2)
    size_delta: float = Field(default=0.0, ge=-3, le=3)
    attack_zh: str = ""                                  # 最强反方(须引用 dossier id)
    rebuttal_zh: str = ""                                # 对原方向的钢人化


def anchor_ids(p: PhannyProposal) -> set[str]:
    return {e for d in p.dimensions for e in d.evidence}


def validate_proposal(p: PhannyProposal, *, known_ids: set[str] | None = None,
                      require_full_dims: bool = True) -> list[str]:
    """返回违规清单(空 = 通过)。纪律:
    ① direction ∈ {long, short}(拒 neutral/no_trade);
    ② dimensions[].key ∈ PHANNY_DIMENSIONS 且不重复;require_full_dims → 六维齐全;
    ③ 每个 evidence id ∈ known_ids(精确串匹配,禁幻觉);
    ④ conviction ≥ 7 → 去重锚 ≥6 ∧ asymmetry_zh 非空 ∧ ≥1 盘前 falsifier;
    ⑤ prob_bins 若给出:长度 5 且和 ≈ 1;
    ⑥ 期权策略闸:plan_zh 不得把期权/结构化策略当交易观点。"""
    problems: list[str] = []
    if p.direction not in DIRECTIONS:
        problems.append(f"direction {p.direction!r} not in {DIRECTIONS} (neutral/no_trade 被禁)")
    seen: set[str] = set()
    for d in p.dimensions:
        if d.key not in PHANNY_DIMENSIONS:
            problems.append(f"dimension key {d.key!r} invalid")
        elif d.key in seen:
            problems.append(f"dimension key {d.key!r} duplicated")
        seen.add(d.key)
        if known_ids is not None:
            for e in d.evidence:
                if e not in known_ids:
                    problems.append(f"dimension {d.key}: unknown evidence {e!r}")
    if require_full_dims:
        missing = [k for k in PHANNY_DIMENSIONS if k not in seen]
        if missing:
            problems.append(f"dimensions incomplete, missing: {missing}")
    if p.conviction >= ACTIONABLE_CONVICTION:
        n = len(anchor_ids(p))
        if n < _MIN_ANCHORS:
            problems.append(f"conviction {p.conviction} needs ≥{_MIN_ANCHORS} distinct anchors (got {n})")
        if not p.asymmetry_zh.strip():
            problems.append("conviction≥7 requires asymmetry_zh")
        if not any(f.strip() for f in p.falsifiers_zh):
            problems.append("conviction≥7 requires ≥1 falsifier")
    if p.prob_bins:
        if len(p.prob_bins) != 5:
            problems.append(f"prob_bins must have 5 bins (got {len(p.prob_bins)})")
        elif abs(sum(p.prob_bins) - 1.0) > 0.06:
            problems.append(f"prob_bins must sum to ~1 (got {round(sum(p.prob_bins), 3)})")
    plan = (p.plan_zh or "").lower()
    hit = [k for k in _OPTION_STRATEGY_KW if k in plan]
    if hit:
        problems.append(f"plan_zh 含期权/结构化策略作交易观点(禁): {hit[:3]}")
    return problems


# ── 策展 universe(复用 ET 的期权流动性名单;Phanny 与 ET 共享同一横截面 book)────────────
from .earnings_events import EARNINGS_UNIVERSE as _ET_UNIVERSE  # noqa: E402

PHANNY_UNIVERSE: tuple[str, ...] = _ET_UNIVERSE


def universe_ids() -> tuple[str, ...]:
    """当前生效的 Phanny 覆盖名单(config.phanny_universe_mode 驱动)。

    `registry` 模式返回**全部注册公司**——「够不够格做季报裁决」不再靠一张硬编码名单预判,
    而由数据可得性在管线里自然把关:无财报日历行的公司走不到 dossier;接地事实 <4 直接
    no_data。两种跳过都会进 build_rejections 台账,可逐家追问原因,而不是被名单静默排除。
    ET 的 EARNINGS_UNIVERSE 保持不动(两个模块的 universe 与 conviction 刻度一样彼此隔离)。"""
    from ..config import get_settings

    if (get_settings().phanny_universe_mode or "list").lower() != "registry":
        return PHANNY_UNIVERSE
    from ..ingestion.registry import COMPANIES
    return tuple(c["id"] for c in COMPANIES)


def phanny_universe(cap: int | None = None) -> list[dict]:
    """生效 universe ∩ registry → company dict 列表(cap 截断,默认 config.phanny_universe_cap)。"""
    from ..ingestion.registry import company_by_id

    if cap is None:
        from ..config import get_settings

        cap = get_settings().phanny_universe_cap
    out: list[dict] = []
    for cid in universe_ids():
        c = company_by_id(cid)
        if c:
            out.append(c)
        if cap and len(out) >= cap:
            break
    return out
