"""宏观指标 ↔ 产业链勾稽本体（代码即真相）。

把 Andy（src/slx，siliconomics 宏观指标库）的 43 个 metric_key 与 XAR 的产业链本体
（THEMES / SEGMENTS / TECH_ROUTES，见 xar.ingestion.registry）双向勾连：

  · 逻辑层：/api/andy/link/* 由此表回答"这条宏观指标关联哪些链/环节/技术路线、为什么"
    与反向"这条链受哪些宏观指标牵引"；
  · 数据层：xar.ingestion.macro_bridge 依此表把宏观印字/登记簿判定跃迁写成
    kg_events(event_type='macro_print') —— 经 semantic_facts 视图流入 Genny 信号流
    与 Chathy 工具。

约定：
  · scope="chain"  —— 链定向指标，出现在其 themes 的链面板上；
  · scope="platform" —— 全平台级（贴现率/市场广度/承重墙等）；themes 非空时仍
    同时出现在对应链面板（如 labor_share 之于 ai_software）。
  · good_when —— 指标上行对关联链的方向语义："rising"=上行利多、"falling"=下行利多、
    None=方向不定（双刃/结构性）。macro_bridge 据此给 macro_print 事件定极性。
  · 全部 id 以 xar.ingestion.registry 为准，tests/test_macro_links.py 强制
    43/43 覆盖 + id 合法 + segment↔theme 一致。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MacroLink:
    metric_key: str
    themes: tuple[str, ...]
    segments: tuple[str, ...] = ()
    tech_routes: tuple[str, ...] = ()
    scope: str = "chain"                # chain | platform
    good_when: str | None = None        # rising | falling | None（方向不定）
    rationale_zh: str = ""


@dataclass(frozen=True)
class OverclaimLink:
    claim_key: str
    themes: tuple[str, ...]             # 空 = 全平台级（bridge 落 theme=NULL 单行）
    polarity_on_fixation: str = "neutral"    # kg_events.polarity: positive|negative|neutral
    polarity_on_falsified: str = "neutral"
    rationale_zh: str = ""


_L = MacroLink

MACRO_LINKS: tuple[MacroLink, ...] = (
    # ── 智能单位成本 / 算力 / 能力（unit_intelligence_cost.yml）──────────────
    _L("cost.intelligence.inference_price_per_mtok",
       themes=("ai_software", "ai_chip"), segments=("swe_devinfra", "chip_gpu"),
       tech_routes=("tr_genai_infra",), good_when="falling",
       rationale_zh="推理 token 价格下行=智能普及成本曲线下移，软件应用链量增利多；对算力硬件是量价双刃。"),
    _L("compute.training_scaling",
       themes=("ai_chip", "ai_optical"),
       segments=("chip_gpu", "chip_memory", "chip_packaging", "module_maker"),
       tech_routes=("tr_hbm", "tr_cowos", "tr_800g"), good_when="rising",
       rationale_zh="训练算力规模持续攀升=GPU/HBM/先进封装/高速光模块的第一需求引擎。"),
    _L("compute.algo_efficiency",
       themes=("ai_software", "ai_chip"), segments=("swe_devinfra", "chip_gpu"),
       tech_routes=("tr_genai_infra",), good_when=None,
       rationale_zh="算法效率提升对软件链纯利多（同能力更便宜）；对芯片链双刃（Jevons 效应 vs 需求替代）。"),
    _L("compute.gpu_geographic_distribution",
       themes=("ai_chip",), segments=("chip_gpu", "chip_foundry"), good_when=None,
       rationale_zh="GPU 地理集中度=供应链/出口管制敏感度的直接刻度，牵动晶圆与整机流向。"),
    _L("capability.model_release_cadence",
       themes=("ai_software", "ai_chip"), segments=("swe_devinfra", "chip_gpu"),
       tech_routes=("tr_ai_agents", "tr_copilots"), good_when="rising",
       rationale_zh="前沿模型发布节奏=能力供给侧的脉搏，牵引应用层创新与训练算力排产。"),
    _L("capability.eci",
       themes=("ai_software",), segments=("swe_devinfra",),
       tech_routes=("tr_ai_agents",), good_when="rising",
       rationale_zh="能力综合指数上行=软件链可自动化任务面扩大（Agent/Copilot 渗透的先行量）。"),
    # ── 数据中心电力（dc_power_and_compute.yml）────────────────────────────
    _L("power.datacenter_twh",
       themes=("ai_chip", "ai_optical"), segments=("chip_gpu", "downstream_customer"),
       tech_routes=("tr_800g", "tr_1600g"), good_when="rising",
       rationale_zh="数据中心用电量=算力扩建的物理镜像；电力增速与 GPU/光模块出货同频。"),
    _L("power.ai_power_constraint",
       themes=("ai_chip", "ai_optical"), segments=("downstream_customer",), good_when="falling",
       rationale_zh="电力约束指数上行=扩建受阻（A2 约束守恒：稀缺迁移到能源），链上出货节奏承压。"),
    _L("power.grid_interconnection_queue",
       themes=("ai_chip", "ai_optical"), segments=("downstream_customer",), good_when=None,
       rationale_zh="并网排队 GW 数双刃：积压=需求管线庞大，也=并网瓶颈拖慢数据中心落地。"),
    # ── Capex 与集中度（capex_and_concentration.yml）──────────────────────
    _L("capex.hyperscaler_capex",
       themes=("ai_chip", "ai_optical", "ai_software"),
       segments=("chip_gpu", "chip_memory", "module_maker", "downstream_customer", "swe_devinfra"),
       tech_routes=("tr_genai_infra", "tr_hbm", "tr_800g"), good_when="rising",
       rationale_zh="超大规模云厂资本开支=芯片/光模块/AI 基础设施三条链共同的需求总闸门。"),
    _L("supply.semiconductor_chokepoint",
       themes=("ai_chip",), segments=("chip_foundry", "chip_equipment"),
       tech_routes=("tr_euv", "tr_2nm", "tr_cowos"), good_when=None,
       rationale_zh="先进制程咽喉（TSMC 营收）刻度单点依赖：上行印证需求，也放大供应链集中风险。"),
    _L("mktcap.concentration",
       themes=("internet", "ai_software"), good_when=None,
       rationale_zh="市值集中度=巨头（即 AI 下游大客户）权重；牵动互联网/软件链的融资与竞争格局。"),
    _L("earnings.mag7_contribution_pct",
       themes=("internet", "ai_software", "ai_optical"), segments=("downstream_customer",),
       good_when=None,
       rationale_zh="Mag7 盈利贡献占比检验'集中是否有盈利支撑'——巨头即光模块/算力的终端买单方。"),
    _L("earnings.rest493_eps_growth_pct",
       themes=("internet", "retail", "restaurants"), good_when="rising",
       rationale_zh="'其余 493'EPS 增速=K 型分化的另一半；上行则消费/长尾链的基本面改善。"),
    _L("mktcap.rsp_vs_spy_excess_return",
       themes=(), scope="platform", good_when=None,
       rationale_zh="等权/市值加权超额=市场广度天平，平台级 regime 信号（非单链）。"),
    _L("mktcap.chinext_vs_sse_excess_return",
       themes=("ai_chip", "ai_optical"), good_when=None,
       rationale_zh="创业板/上证超额=A 股成长-价值天平；链内 CN 标的（光模块/设备）估值风向。"),
    # ── 劳动份额与溢价（labor_share_and_premium.yml）──────────────────────
    _L("labor.labor_share",
       themes=("ai_software",), scope="platform", good_when=None,
       rationale_zh="劳动份额（A3 要素二分坍塌的宏观刻度）：认知自动化若压缩劳动份额，软件链是执行者。"),
    _L("labor.productivity_wage_decoupling",
       themes=("ai_software",), good_when=None,
       rationale_zh="生产率-工资剪刀差扩大=自动化收益归资本的迹象，软件渗透的分配面回声。"),
    _L("price.software_vs_atom_scissors",
       themes=("ai_software", "humanoid_robotics"), good_when=None,
       rationale_zh="软件通缩 vs 原子粘性剪刀差（A1/A5 双相）：差距越大，机器人替代体力的经济性阈值越近。"),
    _L("price.split_cpi",
       themes=("retail", "restaurants", "ai_software"), good_when=None,
       rationale_zh="分裂 CPI（可自动化 vs 不可自动化篮子）直接刻度消费链的成本结构分化。"),
    _L("price.trades_wage",
       themes=("humanoid_robotics", "restaurants"), good_when="rising",
       rationale_zh="蓝领/技工工资上行=人形机器人与餐饮自动化的替代经济性改善（需求先行量）。"),
    _L("capital.vc_flows_to_ai",
       themes=("ai_software", "ai_chip"), segments=("swe_devinfra",),
       tech_routes=("tr_genai_infra", "tr_ai_agents"), good_when="rising",
       rationale_zh="VC 流向 AI 的占比=创新供给侧资金面，软件/基础设施新进入者的燃料。"),
    _L("macro.fed_funds_rate",
       themes=(), scope="platform", good_when="falling",
       rationale_zh="联邦基金利率=全部链条的贴现率与资本开支融资成本，平台级第一变量。"),
    # ── soft（未识别，水印透传）─────────────────────────────────────────────
    _L("labor.ai_skill_wage_premium",
       themes=("ai_software",), good_when=None,
       rationale_zh="AI 技能工资溢价（soft·未识别）：若为真，软件链人才争夺与采用深度的旁证。"),
    _L("labor.junior_postings_high_vs_low_ai_exposure",
       themes=("ai_software", "internet"), tech_routes=("tr_copilots",), good_when=None,
       rationale_zh="初级岗位暴露差（soft·未识别）：Copilot 替代初级认知劳动的招聘面证据。"),
    _L("macro.ai_capex_gdp_contribution",
       themes=("ai_chip", "ai_optical", "ai_software"), good_when="rising",
       rationale_zh="AI 资本开支对 GDP 增长贡献（soft）：宏观账本上链条扩张的总量印记。"),
    _L("labor.bottom_decile_real_wage",
       themes=("retail", "restaurants"), good_when="rising",
       rationale_zh="底层十分位实际工资=消费链客群钱包（K 型下半场的现金流约束）。"),
    _L("macro.ai_tfp_contribution",
       themes=("ai_software",), scope="platform", good_when="rising",
       rationale_zh="AI 对 TFP 的贡献（soft）：A7 增长递归内生的宏观验证，软件链是传导带。"),
    # ── 合法性代理（legitimacy_walls.yml 代理三件套）────────────────────────
    _L("proxy.legitimacy.redistribution_intensity",
       themes=("internet", "ai_software"), good_when=None,
       rationale_zh="再分配强度代理：分配失衡的政策回应力度，巨头/软件链的税收与监管环境。"),
    _L("proxy.legitimacy.antitrust_intensity",
       themes=("internet", "ai_software", "ai_chip"), good_when="falling",
       rationale_zh="反垄断强度上行=平台与算力集中者的监管逆风（合法性墙的压力表）。"),
    _L("proxy.legitimacy.regime_stability",
       themes=(), scope="platform", good_when="rising",
       rationale_zh="政体稳定性代理=全部链条的制度贴现率（wall.trust_legitimacy 的可观测影子）。"),
    _L("price.positional_authenticity_inversion",
       themes=("retail", "restaurants", "internet"), good_when=None,
       rationale_zh="位置性/真实性溢价倒挂：AI 复制品泛滥下稀缺迁移（A1）在消费价格端的显影。"),
    # ── 识别结果（identification_results.yml，派生随基指标）────────────────
    _L("labor.junior_postings_high_vs_low_ai_exposure.did.coef",
       themes=("ai_software", "internet"), tech_routes=("tr_copilots",), good_when=None,
       rationale_zh="初级岗位 DID 系数（派生）：识别引擎对'AI 是否致初级岗位收缩'的点估计。"),
    _L("labor.junior_postings_high_vs_low_ai_exposure.did.pvalue",
       themes=("ai_software", "internet"), tech_routes=("tr_copilots",), good_when=None,
       rationale_zh="初级岗位 DID p 值（派生）：上一条的显著性刻度。"),
    _L("labor.ai_skill_wage_premium.fe.coef",
       themes=("ai_software",), good_when=None,
       rationale_zh="技能溢价 within-FE 系数（派生）：控制个体后 AI 技能溢价的点估计。"),
    _L("labor.ai_skill_wage_premium.fe.pvalue",
       themes=("ai_software",), good_when=None,
       rationale_zh="技能溢价 within-FE p 值（派生）：上一条的显著性刻度。"),
    # ── 七面承重墙（不可量化，value 恒 NULL；平台级，个别另挂链）────────────
    _L("wall.opportunity_cost", themes=(), scope="platform",
       rationale_zh="机会成本墙：选择的排他性不因智能便宜而消失（A1 稀缺形式不变性）。"),
    _L("wall.non_rivalry", themes=("ai_software",), scope="platform",
       rationale_zh="非竞争性墙：软件零边际成本的另一面——可复制者无法承载位置性价值；软件链定价的天花板逻辑。"),
    _L("wall.incentive_compatibility", themes=(), scope="platform",
       rationale_zh="激励相容墙：机制设计的约束不随算力扩张（A2 约束守恒）。"),
    _L("wall.thermodynamic_limits", themes=("ai_chip", "ai_optical"), scope="platform",
       rationale_zh="热力学极限墙：算力=能量，芯片/光链的物理上界（能效曲线终点）。"),
    _L("wall.positionality", themes=(), scope="platform",
       rationale_zh="位置性墙：相对位置零和，AI 供给再多也造不出'第一名'的复数。"),
    _L("wall.trust_legitimacy", themes=(), scope="platform",
       rationale_zh="信任与合法性墙：制度信任不可由算力铸造（其代理见 proxy.legitimacy.*）。"),
    _L("wall.human_final_arbitration", themes=(), scope="platform",
       rationale_zh="人类终裁墙：责任归属与最终裁决权留在人类侧，约束全自动化的部署边界。"),
)

# ── 过度宣称登记簿 → 链（判定跃迁经 macro_bridge 写 kg_events）────────────────
OVERCLAIM_LINKS: dict[str, OverclaimLink] = {c.claim_key: c for c in (
    OverclaimLink("ai_stripped_zero_growth", ("ai_software", "internet", "retail"),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="'剔除 AI 后零增长'固化=K 型确认，长尾消费/互联网承压；证伪=增长广度恢复。"),
    OverclaimLink("junior_jobs_minus67_is_ai", ("ai_software", "internet"),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="初级岗位收缩归因 AI 固化=劳动面政策/合法性风险升温。"),
    OverclaimLink("ai_wage_premium_causal", ("ai_software",),
                  polarity_on_fixation="positive", polarity_on_falsified="negative",
                  rationale_zh="AI 技能溢价因果成立=企业为 AI 技能真金白银付费，软件采用深化的强证。"),
    OverclaimLink("concentration_eq_earnings", ("internet", "ai_software"),
                  polarity_on_fixation="positive", polarity_on_falsified="negative",
                  rationale_zh="'市值集中=盈利集中'固化=巨头估值有盈利支撑；证伪=集中度与基本面脱节风险。"),
    OverclaimLink("ai_capital_loop_steady_engine", ("ai_chip", "ai_optical", "ai_software"),
                  polarity_on_fixation="positive", polarity_on_falsified="negative",
                  rationale_zh="AI 资本循环自持固化=capex 引擎可持续；证伪=三条链的需求引擎熄火风险。"),
    OverclaimLink("china_visible_hand", ("ai_chip",),
                  rationale_zh="'有形之手'断言方向不定：政策强度对 CN 算力链既是补贴也是约束。"),
    OverclaimLink("rest99_sinking_all_sectors", ("retail", "restaurants", "internet"),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="'其余 99% 全行业下沉'固化=消费链客群购买力系统性受损。"),
    OverclaimLink("marginalization_unstable", (),
                  rationale_zh="'边缘化不稳定'为平台级社会命题，不定向到单链（theme=NULL 单行入流）。"),
    OverclaimLink("rsp_crack_appeared", (),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="等权裂缝出现=市场广度崩塌的平台级 regime 信号。"),
)}

# ── 反向索引（导入时构建；代码即真相，无迁移）────────────────────────────────
LINKS_BY_KEY: dict[str, MacroLink] = {link.metric_key: link for link in MACRO_LINKS}

THEME_TO_METRICS: dict[str, tuple[MacroLink, ...]] = {}
SEGMENT_TO_METRICS: dict[str, tuple[MacroLink, ...]] = {}
for _link in MACRO_LINKS:
    for _t in _link.themes:
        THEME_TO_METRICS[_t] = THEME_TO_METRICS.get(_t, ()) + (_link,)
    for _s in _link.segments:
        SEGMENT_TO_METRICS[_s] = SEGMENT_TO_METRICS.get(_s, ()) + (_link,)

PLATFORM_METRICS: tuple[MacroLink, ...] = tuple(
    link for link in MACRO_LINKS if link.scope == "platform")


def theme_overclaims(theme: str) -> tuple[OverclaimLink, ...]:
    """登记簿断言 → 链（空 themes 的平台级断言不归入任何单链面板）。"""
    return tuple(c for c in OVERCLAIM_LINKS.values() if theme in c.themes)
