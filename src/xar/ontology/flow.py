"""资金流本体 —— "资金如何流动并影响资产价格"的代码即真相层。

四件事:
  1. **信号谱系** FLOW_SIGNALS:每个 flow.* signal_key 的口径(节奏/单位/作用域/
     方向语义)。序列复用 alt_signals 表(period_end=经济期,observed_at=知晓时,
     PIT 安全);scope=market/style 的行 company_id 与 theme(主题) 双空,身份编进
     theme 列(`etf:SPY` / `pair:RSP-SPY`,前缀含 ":" 与注册表主题 id 永不冲突)。
  2. **观测宇宙**:大类资产 ETF 篮(ASSET_ETFS)+ 风格因子对(STYLE_PAIRS,
     RSP/MTUM/QUAL/VLUE/IWM/SPHB-SPLV/BTAL)+ risk-on/off 分组。
  3. **语义抽取 schema** FlowInsight:投行 flow 点评/客户交易动向/仓位表述的
     定向抽取(方向/资产/资金类型/强度),由 kg/flow_extract.py 消费。
  4. **资金类型分布**(HF/LO/retail)的诚实口径:硬数据仅 13F 机构持仓与
     short interest(HF 代理);细分主要来自语义道 FlowInsight.investor_type。

新增观测标的 = 加一条 EtfSpec/StylePair;新增信号 = 加一条 FlowSignalSpec +
research/flow.py 一个计算函数——评分/前端/Chathy 工具零改动。
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

# ── 身份编码:market/style 行的 alt_signals.theme 列 ─────────────────────────────
# uq_alt_signal 唯一键不含 meta → 同一 signal_key 的不同 ETF/因子对必须用 theme 列区分。
ETF_PREFIX = "etf:"
PAIR_PREFIX = "pair:"


def etf_theme(ticker: str) -> str:
    return f"{ETF_PREFIX}{ticker}"


def pair_theme(key: str) -> str:
    return f"{PAIR_PREFIX}{key}"


# ── 信号谱系 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FlowSignalSpec:
    key: str                  # e.g. "flow.obv_z"
    name: str
    name_cn: str
    cadence: str              # daily | biweekly | quarterly
    unit: str
    scope: str                # market | style | theme | company(一个 key 可多 scope,取主)
    good_when: str | None     # rising | falling | None(方向不定/本身已带方向)
    source: str               # 取数/计算来源(massive | futu_flow | holdings | flow)
    min_history: int = 10     # 计 z 所需最少历史点
    rationale_zh: str = ""


_F = FlowSignalSpec

FLOW_SIGNALS: tuple[FlowSignalSpec, ...] = (
    # ── 量价资金流代理(ETF 与个股同 key;ETF 行 theme=etf:*,个股行 company_id)──
    _F("flow.obv_z", "OBV accumulation z", "OBV 量能累积 z", "daily", "z", "market", "rising",
       "flow", rationale_zh="On-Balance-Volume 累积序列的 20 日增量对 120 日历史的 z——"
       "上涨日放量/下跌日缩量的净累积,是'资金在买还是在卖'的最经典量价代理。"),
    _F("flow.dollar_vol_z", "Dollar volume z", "美元成交额 z", "daily", "z", "market", None,
       "flow", rationale_zh="20 日均美元成交额对 120 日历史的 z——资金参与度/换手热度;"
       "方向不定(放量可能是抢筹也可能是出逃),与 OBV 方向联读。"),
    _F("flow.mom_63d", "63-day momentum", "63 日动量", "daily", "ratio", "market", "rising",
       "flow", rationale_zh="63 交易日(一季度)价格动量——资金追逐的结果刻度,"
       "与 OBV/成交额组成量价三角。"),
    # ── 风格因子对(theme=pair:*)────────────────────────────────────────────────
    _F("flow.style_ratio_z", "Style pair relative strength z", "风格对相对强弱 z", "daily",
       "z", "style", None, "flow", rationale_zh="风格对 log 比值 20 日斜率对一年历史的 z:"
       "RSP/SPY=广度(等权跑赢=资金扩散),MTUM/SPY=动量拥挤,QUAL/VLUE/IWM=质量/价值/"
       "规模轮动,SPHB/SPLV=风险偏好,BTAL=反β防御资金;正负本身即方向,无需翻转。"),
    # ── 市场综合 ──────────────────────────────────────────────────────────────
    _F("flow.risk_on_composite", "Risk-on composite", "风险偏好综合分", "daily", "score",
       "market", "rising", "flow", min_history=1,
       rationale_zh="risk-on 篮(股票/信用/加密)与 risk-off 篮(久期/黄金/美元/现金)"
       "动量 z 之差归一到 [-1,1]——大类资产层面'资金开风险还是关风险'的单值刻度。"),
    # ── 期权情绪 ──────────────────────────────────────────────────────────────
    _F("flow.pc_ratio", "Put/Call ratio", "期权 Put/Call 比", "daily", "ratio", "market",
       None, "massive", min_history=5,
       rationale_zh="期权链 put/call 成交量比(缺量退回持仓量比,meta.basis 注明)——"
       ">1 防御性对冲盘主导,极端值常为反指;市场级用 SPY,个股级逐名计算。"),
    # ── 空头面(公司级;arm-if-available)───────────────────────────────────────
    _F("flow.short_interest", "Short interest", "空头持仓量", "biweekly", "shares",
       "company", "falling", "massive", min_history=4,
       rationale_zh="FINRA 双周空头持仓(经 Massive)——空头回补压力与拥挤做空的直接刻度;"
       "持续下降=空头撤退(资金面转多)。"),
    _F("flow.days_to_cover", "Days to cover", "空头回补天数", "biweekly", "days",
       "company", None, "massive", min_history=4,
       rationale_zh="空头持仓 / 日均成交量 = 全部空头平仓所需天数——短挤压(short squeeze)"
       "燃料表;方向不定(高 DTC 既是风险也是挤压弹药)。"),
    # ── 资金类型分布:机构面硬数据(13F)────────────────────────────────────────
    _F("flow.inst_own_delta", "Institutional ownership delta", "机构持仓季度变动", "quarterly",
       "%", "company", "rising", "holdings", min_history=2,
       rationale_zh="13F 机构持仓市值的季度环比——LO/HF 合计的低频真值;"
       "HF/LO/retail 细分无公开托管数据,由语义道 FlowInsight.investor_type 补足。"),
    # ── 主题聚合(theme=注册表主题 id)──────────────────────────────────────────
    _F("flow.theme_net_score", "Theme net flow score", "主题资金流净分", "daily", "score",
       "theme", "rising", "flow", min_history=1,
       rationale_zh="主题成员的富途主力净流入 z(港/A/美)⊕ 美股成员 OBV/成交额复合 ⊕ "
       "空头持仓变动的加权净分 ∈ [-1,1]——行业链层面的资金流单值刻度。"),
)

FLOW_BY_KEY: dict[str, FlowSignalSpec] = {s.key: s for s in FLOW_SIGNALS}

for _s in FLOW_SIGNALS:  # 口径自检
    assert _s.scope in ("market", "style", "theme", "company"), _s.key
    assert _s.good_when in ("rising", "falling", None), _s.key


# ── 大类资产 ETF 篮 ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EtfSpec:
    ticker: str
    label: str
    label_cn: str
    asset_class: str          # equity_us | equity_intl | duration | credit | gold |
                              # commodity | usd | crypto | cash


_E = EtfSpec

ASSET_ETFS: tuple[EtfSpec, ...] = (
    _E("SPY", "S&P 500", "美股大盘", "equity_us"),
    _E("QQQ", "Nasdaq 100", "美股科技", "equity_us"),
    _E("EFA", "EAFE developed", "发达市场(除美)", "equity_intl"),
    _E("EEM", "Emerging markets", "新兴市场", "equity_intl"),
    _E("TLT", "20Y+ Treasuries", "长久期美债", "duration"),
    _E("IEF", "7-10Y Treasuries", "中久期美债", "duration"),
    _E("HYG", "High yield credit", "高收益信用", "credit"),
    _E("LQD", "IG credit", "投资级信用", "credit"),
    _E("GLD", "Gold", "黄金", "gold"),
    _E("DBC", "Broad commodities", "大宗商品", "commodity"),
    _E("UUP", "US dollar", "美元", "usd"),
    _E("IBIT", "Bitcoin (spot ETF)", "比特币", "crypto"),
    _E("BIL", "1-3M T-bills", "现金(短债)", "cash"),
)

ETF_BY_TICKER: dict[str, EtfSpec] = {e.ticker: e for e in ASSET_ETFS}

# risk-on/off 分组(flow.risk_on_composite 的构成;代码即真相,面板据此列 drivers)
RISK_ON_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "EEM", "HYG", "IBIT")
RISK_OFF_TICKERS: tuple[str, ...] = ("TLT", "GLD", "UUP", "BIL")


# ── 风格因子对 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StylePair:
    key: str                  # theme 列存 pair:{key}
    long: str
    short: str | None         # None = 单腿(BTAL 自带多空结构)
    label: str
    label_cn: str
    rationale_zh: str = ""


_P = StylePair

STYLE_PAIRS: tuple[StylePair, ...] = (
    _P("RSP-SPY", "RSP", "SPY", "Breadth (equal/cap weight)", "市场广度(等权/市值)",
       "等权跑赢市值加权 = 资金从巨头扩散到中腰部——广度改善;跑输 = 集中度虹吸。"),
    _P("MTUM-SPY", "MTUM", "SPY", "Momentum factor", "动量因子",
       "动量组合超额 = 趋势资金(CTA/动量策略)拥挤度的价格足迹。"),
    _P("QUAL-SPY", "QUAL", "SPY", "Quality factor", "质量因子",
       "质量超额走强 = 资金转向防御性优质资产(晚周期/避险偏好)。"),
    _P("VLUE-SPY", "VLUE", "SPY", "Value factor", "价值因子",
       "价值超额 = 资金从成长向价值轮动(通常伴随利率上行/再通胀交易)。"),
    _P("IWM-SPY", "IWM", "SPY", "Size (small/large)", "规模因子(小/大盘)",
       "小盘超额 = 风险偏好扩张与广度改善的确认;跑输 = 资金龟缩大盘。"),
    _P("SPHB-SPLV", "SPHB", "SPLV", "High beta / low vol", "高β/低波",
       "高β跑赢低波 = 最纯粹的股内风险偏好刻度(risk-on 确认)。"),
    _P("BTAL", "BTAL", None, "Anti-beta (defensive bid)", "反β防御资金",
       "BTAL(做多低β/做空高β)走强 = 防御性资金入场——与 SPHB/SPLV 互为印证。"),
)

PAIR_BY_KEY: dict[str, StylePair] = {p.key: p for p in STYLE_PAIRS}

# 全部需要日线的 ETF ticker(取数宇宙 = 资产篮 ∪ 风格对两腿)
FLOW_ETF_UNIVERSE: tuple[str, ...] = tuple(dict.fromkeys(
    [e.ticker for e in ASSET_ETFS]
    + [p.long for p in STYLE_PAIRS]
    + [p.short for p in STYLE_PAIRS if p.short]))


# ── 资金类型 + 语义抽取 schema ─────────────────────────────────────────────────
INVESTOR_TYPES: tuple[str, ...] = ("HF", "LO", "retail", "CTA", "dealer", "corporate")

# 定向抽取的关键词 triage(中英双语;kg/flow_extract.py 与测试共用口径)
FLOW_KEYWORDS: tuple[str, ...] = (
    "fund flow", "fund flows", "inflow", "outflow", "positioning", "net long", "net short",
    "CTA", "prime brokerage", "buyback desk", "short interest", "short covering",
    "risk-on", "risk-off", "rotation into", "rotation out",
    "资金流", "资金面", "净流入", "净流出", "北向", "南向", "仓位", "加仓", "减仓",
    "回购", "空头回补", "轧空", "风格切换", "高低切",
)


class FlowInsight(BaseModel):
    """一条投行/媒体 flow 点评的定向抽取(方向/资产/资金类型/强度)。"""
    relevant: bool = False
    direction: str = "rotation"       # inflow | outflow | rotation
    asset_or_sector: str = ""         # 资金流向的资产/行业/风格(自由文本,尽量具体)
    entity: str = ""                  # 若指向具体覆盖公司:名称/代码;否则留空
    investor_type: str = ""           # HF | LO | retail | CTA | dealer | corporate | ""(未指明)
    strength: float = Field(default=0.0)   # 0..1:点评断言的资金流强度/确信度
    horizon: str = "current"          # current | weeks | quarters
    evidence: str = ""                # 原文逐字引用
    time_orientation: str = "backward_looking"   # forward_looking | backward_looking
