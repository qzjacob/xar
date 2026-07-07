"""另类数据本体 —— 对冲基金级 alt-data 追踪的代码即真相层。

三件事:
  1. **信号谱系** ALT_SIGNALS:每个 signal_key 的口径(节奏/单位/作用域/方向语义/
     映射到论点支柱 kind)。信号写入 storage 的 alt_signals 表(period_end=经济期,
     observed_at=知晓时,PIT 安全)。
  2. **绑定** :哪家公司有哪些追踪器。可派生的(台股月营收码=ticker 前缀、Wiki 词条
     =公司名)在此推导;需策展的(GitHub org / ATS 招聘板 / PyPI 包)在
     ontology/alt_bindings.py(生成式策展,逐条实核)。
  3. **支柱映射** SIGNAL_PILLAR_KINDS:高频校正引擎(research/thesis_signals.py)据此
     把信号动量聚合到 CompanyThesis 的对应支柱上。

新增追踪器 = 加一条 AltSignalSpec + 一个 provider——评分/健康度/前端零改动。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .thesis import PILLAR_KINDS


@dataclass(frozen=True)
class AltSignalSpec:
    key: str                      # e.g. "alt.tw_monthly_revenue"
    name: str
    name_cn: str
    cadence: str                  # daily | weekly | monthly
    unit: str
    scope: str                    # company | theme
    good_when: str | None         # rising | falling | None(方向不定)
    pillar_kinds: tuple[str, ...] # 影响哪些论点支柱 kind(thesis.PILLAR_KINDS)
    source: str                   # provider 名(providers/alt/…)
    themes: tuple[str, ...] = ()  # theme-scope 信号的归属链;company-scope 留空=按绑定
    min_history: int = 6          # 计 z-score 所需的最少历史点
    rationale_zh: str = ""


_S = AltSignalSpec

ALT_SIGNALS: tuple[AltSignalSpec, ...] = (
    # ── 实体经济:营收/出口(硬度最高的另类信号)────────────────────────────────
    _S("alt.tw_monthly_revenue", "TW monthly revenue", "台股月营收", "monthly", "TWD",
       "company", "rising", ("demand", "financials"), "twse_revenue",
       rationale_zh="台湾上市公司法定月营收披露——比季报快 2 个月的需求真值,"
                    "覆盖台积电/联电/日月光等全部 143 家台股链上公司。"),
    _S("alt.kr_chip_exports", "KR 20-day semiconductor exports", "韩国20日芯片出口", "monthly",
       "USD", "theme", "rising", ("demand", "cyclical"), "kr_exports",
       themes=("ai_chip", "ai_optical"),
       rationale_zh="韩国海关旬度出口——存储/半导体周期最经典的官方领先指标。"),
    _S("alt.semi_billings", "WSTS/SIA global semi billings", "全球半导体月度出货", "monthly",
       "USD", "theme", "rising", ("demand", "cyclical"), "kr_exports",
       themes=("ai_chip",),
       rationale_zh="SIA 月度全球半导体销售额——行业总需求的权威月频刻度。"),
    # ── 数字尾气:开发者/产品/雇佣遥测(软件链核心)──────────────────────────────
    _S("alt.github_momentum", "GitHub OSS momentum", "GitHub 开源动能", "weekly",
       "stars", "company", "rising", ("technology", "moat"), "github_metrics",
       rationale_zh="公司开源产品的 star 增速/发布节奏/贡献者数——开发者心智份额的直接遥测。"),
    _S("alt.pkg_downloads", "Package downloads (PyPI/npm)", "包下载量", "weekly",
       "count", "company", "rising", ("demand", "technology"), "pkg_downloads",
       rationale_zh="PyPI/npm 周下载——devtools/基础设施软件的采用漏斗顶端。"),
    _S("alt.hiring_velocity", "Job postings (ATS)", "在招职位数", "weekly",
       "count", "company", "rising", ("demand", "financials"), "ats_jobs",
       rationale_zh="Greenhouse/Lever 官方招聘板在招数——扩张/收缩的先行几个季度信号;"
                    "AI 岗位占比单独入 meta。"),
    _S("alt.wiki_attention", "Wikipedia pageviews", "维基注意力", "weekly",
       "views", "company", None, ("demand",), "wiki_attention",
       rationale_zh="公司词条周浏览量——大众/投资者注意力代理;方向不定(暴涨可能是丑闻)。"),
    # ── 资金流(富途):主力/机构净流入——盘面上的"聪明钱"代理(HK/A股/US)──────────
    _S("alt.futu_main_capital_flow", "Futu main capital net inflow", "富途主力资金净流入",
       "daily", "HKD", "company", "rising", ("demand", "valuation"), "futu_flow",
       min_history=10,
       rationale_zh="富途 OpenAPI 主力(超大单+大单)日度净流入——机构资金在盘面的直接足迹,"
                    "覆盖港股/A股/美股;主力持续净流入=需求与估值支撑的高频代理。"),
    # ── 数据追踪:Wind EDB 宏观/行业指标时序(theme 级需求真值;固定问题见 wind_edb.EDB_QUESTIONS)──
    _S("alt.edb_semi_sales", "Global semiconductor sales", "全球半导体月度销售额", "monthly",
       "USD", "theme", "rising", ("demand", "cyclical"), "wind_edb", themes=("ai_chip",),
       rationale_zh="WSTS/SIA 全球半导体销售额——AI 芯片链总需求的权威月频刻度。"),
    _S("alt.edb_ic_output", "China IC output", "中国集成电路产量当月值", "monthly",
       "亿块", "theme", "rising", ("demand", "supply_chain"), "wind_edb", themes=("ai_chip",),
       rationale_zh="国家统计局集成电路产量当月值——国产芯片产能与需求的实体刻度。"),
    _S("alt.edb_optical_export", "China optical device export", "中国光电子器件出口金额", "monthly",
       "USD", "theme", "rising", ("demand", "cyclical"), "wind_edb", themes=("ai_optical",),
       rationale_zh="海关光电子器件出口——光模块链外需景气的月频代理。"),
    _S("alt.edb_robot_output", "China industrial robot output", "工业机器人产量当月值", "monthly",
       "台", "theme", "rising", ("demand",), "wind_edb", themes=("humanoid_robotics",),
       rationale_zh="国统局工业机器人产量当月值——本体/零部件链需求的实体刻度。"),
    _S("alt.edb_catering", "China catering retail", "社零餐饮收入当月值", "monthly",
       "亿元", "theme", "rising", ("demand", "cyclical"), "wind_edb", themes=("restaurants",),
       rationale_zh="社会消费品零售总额:餐饮收入当月值——餐饮链需求周期的官方月频刻度。"),
    _S("alt.edb_retail_total", "China retail sales YoY", "社零总额当月同比", "monthly",
       "%", "theme", "rising", ("demand", "cyclical"), "wind_edb", themes=("retail",),
       rationale_zh="社会消费品零售总额当月同比——线下零售链需求的宏观刻度。"),
    _S("alt.edb_online_retail", "China online physical retail YoY", "实物商品网上零售额累计同比",
       "monthly", "%", "theme", "rising", ("demand",), "wind_edb", themes=("internet",),
       rationale_zh="实物商品网上零售额累计同比——电商/互联网平台 GMV 的宏观刻度。"),
)

SIGNALS_BY_KEY: dict[str, AltSignalSpec] = {s.key: s for s in ALT_SIGNALS}

for _s in ALT_SIGNALS:  # 口径自检:支柱 kind 必须合法
    assert all(k in PILLAR_KINDS for k in _s.pillar_kinds), _s.key


# ── 可派生绑定 ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AltBinding:
    """一家公司的追踪器绑定。策展字段(github/ats/pypi/npm)来自 alt_bindings.py。"""
    company_id: str
    tw_code: str | None = None            # 台股代码(派生自 ticker)
    wiki_title: str | None = None         # 维基词条(默认=英文名,可策展覆盖)
    github_orgs: tuple[str, ...] = ()
    ats: tuple[str, str] | None = None    # ("greenhouse"|"lever", org_slug)
    pypi_packages: tuple[str, ...] = ()
    npm_packages: tuple[str, ...] = ()
    futu_code: str | None = None          # 富途代码 HK./SH./SZ./US.(派生自 ticker)

    def signals(self) -> tuple[str, ...]:
        out = []
        if self.tw_code:
            out.append("alt.tw_monthly_revenue")
        if self.github_orgs:
            out.append("alt.github_momentum")
        if self.pypi_packages or self.npm_packages:
            out.append("alt.pkg_downloads")
        if self.ats:
            out.append("alt.hiring_velocity")
        if self.wiki_title:
            out.append("alt.wiki_attention")
        if self.futu_code:
            out.append("alt.futu_main_capital_flow")
        return tuple(out)


def _tw_code(c: dict) -> str | None:
    for t in c.get("tickers") or []:
        if t.endswith((".TW", ".TWO")):
            code = t.split(".")[0]
            if code.isdigit():
                return code
    return None


def _futu_code(c: dict) -> str | None:
    """公司 ticker → 富途代码。复用 provider 的转换器,保证绑定与查询用同一格式。"""
    from ..providers.futu import code_from_tickers

    return code_from_tickers(c.get("tickers") or [])


def _wiki_title(c: dict) -> str | None:
    name = (c.get("name") or "").strip()
    en = name.split(" 中")[0].strip() if " 中" in name else name
    # 名称的英文段(注册表命名 "English 中文" 惯例);过短/纯中文则不绑定
    en = en.split("(")[0].strip()
    ascii_part = "".join(ch for ch in en if ord(ch) < 128).strip(" ·-")
    return ascii_part if len(ascii_part) >= 3 else None


def bindings() -> dict[str, AltBinding]:
    """全宇宙绑定 = 派生(TW 码/Wiki 词条)⊕ 策展(alt_bindings.CURATED)。"""
    from ..ingestion.registry import COMPANIES

    try:
        from .alt_bindings import CURATED
    except ImportError:
        CURATED = {}
    out: dict[str, AltBinding] = {}
    for c in COMPANIES:
        cid = c["id"]
        cur = CURATED.get(cid, {})
        b = AltBinding(
            company_id=cid,
            tw_code=_tw_code(c),
            wiki_title=cur.get("wiki_title") or _wiki_title(c),
            github_orgs=tuple(cur.get("github_orgs", ())),
            ats=tuple(cur["ats"]) if cur.get("ats") else None,  # type: ignore[arg-type]
            pypi_packages=tuple(cur.get("pypi_packages", ())),
            npm_packages=tuple(cur.get("npm_packages", ())),
            futu_code=_futu_code(c),
        )
        if b.signals():
            out[cid] = b
    return out


def binding_for(company_id: str) -> AltBinding | None:
    return bindings().get(company_id)


@dataclass(frozen=True)
class _Coverage:
    companies: int = 0
    by_signal: dict = field(default_factory=dict)


def coverage_summary() -> dict:
    bs = bindings()
    by_sig: dict[str, int] = {}
    for b in bs.values():
        for s in b.signals():
            by_sig[s] = by_sig.get(s, 0) + 1
    return {"companies": len(bs), "by_signal": by_sig,
            "theme_signals": [s.key for s in ALT_SIGNALS if s.scope == "theme"]}
