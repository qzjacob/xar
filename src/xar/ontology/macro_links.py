"""宏观指标 ↔ 产业链勾稽本体（代码即真相）。

把 Andy（src/slx，"以硅基经济学为核心的宏观经济学数据库"）的全部 metric_key
（硅基核心 43 + AM 宏观外环 38）与 XAR 的产业链本体（THEMES / SEGMENTS /
TECH_ROUTES，见 xar.ingestion.registry）双向勾连，并显式建模**宏观传导链**：

  · 逻辑层：/api/andy/link/* 由此表回答"这条宏观指标关联哪些链/环节/技术路线、为什么"
    与反向"这条链受哪些宏观指标牵引"；link/chain 沿 TRANSMISSIONS 顺藤摸瓜。
  · 数据层：xar.ingestion.macro_bridge 依此表把宏观印字/登记簿判定跃迁写成
    kg_events(event_type='macro_print') —— 经 semantic_facts 视图流入 Genny 信号流
    与 Chathy 工具。
  · 传导层（AM 波次）：MacroTransmission 把 metric→metric 的因果/领先关系写成
    受测本体（利率→贴现→capex→算力…），端点支持 `theme:{id}` / `flow:risk_on`
    哨兵，闭合"宏观外环→硅基核心→产业链→资金流"的全逻辑链条。

约定：
  · scope="chain"  —— 链定向指标，出现在其 themes 的链面板上；
  · scope="platform" —— 全平台级（贴现率/市场广度/承重墙等）；themes 非空时仍
    同时出现在对应链面板（如 labor_share 之于 ai_software）。
  · good_when —— 指标上行对关联链的方向语义："rising"=上行利多、"falling"=下行利多、
    None=方向不定（双刃/结构性）。macro_bridge 据此给 macro_print 事件定极性。
  · bridge_min_gap_days —— macro_print 事件的发射节流（0=每个新 PIT 印字都发）：
    日频市场序列（美债/OAS/VIX…）设 28,否则工人日跑=每天一条事件刷屏语义流。
  · 全部 id 以 xar.ingestion.registry 为准，tests/test_macro_links.py 强制
    与 slx registry 全量 1:1 覆盖 + id 合法 + segment↔theme 一致 + 传导链端点合法。
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
    bridge_min_gap_days: int = 0        # macro_print 发射节流（日频市场序列设 28）


@dataclass(frozen=True)
class MacroTransmission:
    """宏观传导边：from → to 的因果/领先关系（代码即真相,可被 link/chain BFS 展开）。

    端点是 slx metric_key,或哨兵 `theme:{注册表主题 id}`（传导入产业链）/
    `flow:risk_on`（传导入 MF 资金流台的 risk-on 综合分）。sign："+"=同向,
    "-"=反向,"±"=方向依状态;lag_hint 是人读的时滞提示（"2-4q"/"weeks"）。
    """
    from_key: str
    to_key: str
    sign: str                           # + | - | ±
    lag_hint: str = ""
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
    # ══ AM 宏观外环（macro_rates / inflation_growth / liquidity_credit / fiscal_fx_sentiment）══
    # 多数 platform 级（全链贴现/流动性/周期变量）;直挂 themes 严格限量（≤3 条,
    # 防 compact_theme_macro 8 条帽把硅基指标挤出 Chathy/MacroStrip 视野）。
    # ── rates 利率 ────────────────────────────────────────────────────────
    _L("rates.ust_2y", themes=(), scope="platform", good_when="falling", bridge_min_gap_days=28,
       rationale_zh="2Y=政策路径预期定价，风险资产贴现前段；下行=宽松预期=全链融资面转暖。"),
    _L("rates.ust_10y", themes=(), scope="platform", good_when="falling", bridge_min_gap_days=28,
       rationale_zh="10Y=全球资产定价锚，AI 基础设施超长久期资产的贴现率中枢。"),
    _L("rates.ust_30y", themes=(), scope="platform", good_when="falling", bridge_min_gap_days=28,
       rationale_zh="30Y=期限溢价+长期预期，数据中心 30 年资产久期的对标利率。"),
    _L("rates.ust_2s10s_spread", themes=(), scope="platform", good_when="rising", bridge_min_gap_days=28,
       rationale_zh="曲线斜率=周期领先指标；陡峭化=银行息差修复=信贷供给回暖。"),
    _L("rates.ust_10y_real",
       themes=("ai_software", "ai_chip"), scope="platform", good_when="falling", bridge_min_gap_days=28,
       rationale_zh="实际利率=AI capex 的真实融资成本锚——宏观外环压向硅基核心的最短传导链（久期面直挂软件/算力链）。"),
    _L("rates.breakeven_10y", themes=(), scope="platform", good_when=None, bridge_min_gap_days=28,
       rationale_zh="盈亏平衡通胀=名义/实际分解件；判别利率上行的成色（增长 vs 通胀）。"),
    _L("rates.sofr", themes=(), scope="platform", good_when=None, bridge_min_gap_days=28,
       rationale_zh="SOFR=政策向融资市场传导第一站；对走廊的偏离是准备金稀缺度体温计。"),
    _L("rates.mortgage_30y", themes=("retail", "restaurants"), good_when="falling",
       rationale_zh="按揭利率=利率→居民部门主通道；下行释放地产-耐用品-消费链条。"),
    # ── inflation 通胀 ────────────────────────────────────────────────────
    _L("inflation.cpi", themes=(), scope="platform", good_when=None,
       rationale_zh="CPI 总量=联储反应函数头号自变量;方向语义依 regime（过热期下行利多）。"),
    _L("inflation.core_cpi", themes=(), scope="platform", good_when=None,
       rationale_zh="核心 CPI=底层通胀趋势，政策路径定价的核心输入。"),
    _L("inflation.core_pce", themes=(), scope="platform", good_when=None,
       rationale_zh="核心 PCE=联储 2% 目标官方变量，反应函数最直接的自变量。"),
    _L("inflation.sticky_cpi_yoy", themes=("ai_software",), scope="platform", good_when=None,
       rationale_zh="粘性通胀=鲍莫尔成本病现世刻度——与软件通缩构成 A8 双相价格剪刀差的宏观对照。"),
    _L("inflation.ppi", themes=(), scope="platform", good_when=None,
       rationale_zh="PPI=上游价格→企业毛利传导，硬件链成本侧压力表。"),
    # ── growth 增长 ───────────────────────────────────────────────────────
    _L("growth.real_gdp", themes=(), scope="platform", good_when="rising",
       rationale_zh="实际 GDP=总量增长权威刻度，AI 资本形成贡献的分母与对照。"),
    _L("growth.industrial_production", themes=(), scope="platform", good_when="rising",
       rationale_zh="工业产出=实体制造月频真值，信贷与利率传导的第一实体响应件。"),
    _L("growth.retail_sales", themes=("retail", "restaurants", "internet"), good_when="rising",
       rationale_zh="零售销售=居民需求高频真值，消费周期主题的宏观闸门。"),
    _L("growth.nonfarm_payrolls", themes=(), scope="platform", good_when=None,
       rationale_zh="非农=劳动市场总量;A6 解耦检验场（产出与就业剪刀差）的分母侧。"),
    _L("growth.unemployment_rate", themes=(), scope="platform", good_when="falling",
       rationale_zh="失业率=劳动市场存量刻度，萨姆规则等衰退判据输入。"),
    _L("growth.initial_claims", themes=(), scope="platform", good_when="falling",
       rationale_zh="初请=劳动市场最高频领先指标，裁员脉冲先于失业率 1-2 月。"),
    _L("growth.housing_starts", themes=(), scope="platform", good_when="rising",
       rationale_zh="新屋开工=利率→地产实体投资的第一反应件。"),
    _L("growth.job_openings", themes=(), scope="platform", good_when=None,
       rationale_zh="JOLTS 空缺=劳动需求存量;与公司级 ATS 在招数构成宏微对照。"),
    _L("growth.avg_hourly_earnings", themes=(), scope="platform", good_when=None,
       rationale_zh="时薪=生产率-工资传导带（A8 断裂论）的直接观测,兼粘性通胀成本源。"),
    # ── liquidity 流动性层级 ──────────────────────────────────────────────
    _L("liquidity.fed_total_assets", themes=(), scope="platform", good_when="rising",
       rationale_zh="Fed 总资产=流动性层级之源，QE/QT 的存量刻度。"),
    _L("liquidity.on_rrp", themes=(), scope="platform", good_when=None, bridge_min_gap_days=28,
       rationale_zh="ON RRP=过剩流动性蓄水池；抽干=缓冲耗尽，QT 开始侵蚀准备金。"),
    _L("liquidity.tga", themes=(), scope="platform", good_when=None,
       rationale_zh="TGA=财政抽放水阀，债务上限周期的高频扰动源。"),
    _L("liquidity.bank_reserves", themes=(), scope="platform", good_when="rising",
       rationale_zh="银行准备金=流动性层级关键中间变量，风险资产承接力的最硬解释变量。"),
    _L("liquidity.m2", themes=(), scope="platform", good_when=None,
       rationale_zh="M2=广义货币存量，通胀的长滞后先导（货币主义对照组）。"),
    # ── credit 信用条件 ───────────────────────────────────────────────────
    _L("credit.hy_oas",
       themes=("ai_chip", "ai_optical"), good_when="falling", bridge_min_gap_days=28,
       rationale_zh="HY OAS=风险融资成本市场价——AI 中小链（光模块/设备/新进入者）融资面的直接约束（融资面直挂）。"),
    _L("credit.ig_oas", themes=(), scope="platform", good_when="falling", bridge_min_gap_days=28,
       rationale_zh="IG OAS=大盘融资成本，hyperscaler 发债融 capex 的价差基准。"),
    _L("credit.nfci", themes=(), scope="platform", good_when="falling",
       rationale_zh="NFCI=金融条件综合刻度（正=紧），领先信用利差与实体信贷。"),
    _L("credit.sloos_ci_standards", themes=(), scope="platform", good_when="falling",
       rationale_zh="SLOOS 收紧净比例=银行信贷闸门调查真值，领先工业产出 2-3 季。"),
    # ── fiscal 财政 ───────────────────────────────────────────────────────
    _L("fiscal.federal_deficit", themes=(), scope="platform", good_when=None,
       rationale_zh="联邦赤字=财政脉冲（私人部门净金融资产注入），托底名义增长同时抬升发债。"),
    _L("fiscal.public_debt", themes=(), scope="platform", good_when=None,
       rationale_zh="公共债务存量=久期供给→期限溢价，财政主导风险刻度。"),
    # ── fx_commodity 汇率商品 ─────────────────────────────────────────────
    _L("fx.usd_broad", themes=("ai_chip",), good_when="falling", bridge_min_gap_days=28,
       rationale_zh="美元=全球流动性价格；强美元压缩芯片链海外营收折算与离岸融资（海外敞口面直挂）。"),
    _L("cmdty.wti_crude", themes=(), scope="platform", good_when=None, bridge_min_gap_days=28,
       rationale_zh="油价=能源成本高频代理，通胀→政策传导的输入,能源墙影子价格之一。"),
    _L("cmdty.copper", themes=("ai_chip", "ai_optical"), good_when="rising",
       rationale_zh="铜价=全球实体需求温度计,兼电网/数据中心建设原料——能源墙 capex 的原料端印证。"),
    # ── sentiment 情绪 ────────────────────────────────────────────────────
    _L("sentiment.umich", themes=("retail", "restaurants"), good_when="rising",
       rationale_zh="密歇根信心=居民预期→消费的软先导，消费主题的情绪闸门。"),
    _L("sentiment.vix", themes=(), scope="platform", good_when="falling", bridge_min_gap_days=28,
       rationale_zh="VIX=风险偏好的期权定价刻度，与资金流台 risk-on 综合分互为印证。"),
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
    # ── AM 宏观外环断言 ──
    OverclaimLink("high_real_rates_compress_capex", ("ai_chip", "ai_optical", "ai_software"),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="高实际利率压制 capex 固化=三条链需求闸门收紧;证伪=军备竞赛动机压倒资本成本（本身即重大结构信息）。"),
    OverclaimLink("liquidity_drives_breadth", (),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="流动性→广度固化=准备金收缩期资金龟缩巨头（平台级 regime）;证伪=广度脱离流动性自主改善。"),
    OverclaimLink("credit_stress_breaks_ai_loop", ("ai_chip", "ai_optical"),
                  polarity_on_fixation="negative", polarity_on_falsified="positive",
                  rationale_zh="信用压力打断资本回路固化=中小链融资断流;证伪=巨头自有现金流令回路免疫信用周期。"),
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

# ── 宏观传导链（AM 波次）：metric→metric 的因果/领先本体,闭合到硅基核心与资金流 ──
_T = MacroTransmission

TRANSMISSIONS: tuple[MacroTransmission, ...] = (
    # 政策 → 利率结构
    _T("macro.fed_funds_rate", "rates.sofr", "+", "days",
       "政策利率经公开市场操作即时传导为货币市场实际资金价格。"),
    _T("macro.fed_funds_rate", "rates.ust_2y", "+", "weeks",
       "2Y 定价未来两年政策路径——政策转向预期先于行动进入价格。"),
    _T("rates.ust_2y", "rates.ust_10y", "+", "weeks",
       "曲线传导：前端带动长端,残差是期限溢价与增长/通胀预期。"),
    # 通胀 → 政策（反应函数）
    _T("inflation.core_pce", "macro.fed_funds_rate", "+", "1-2q",
       "联储反应函数：核心 PCE 偏离 2% 目标驱动政策利率路径。"),
    _T("liquidity.m2", "inflation.cpi", "+", "18-24m",
       "货币数量的长滞后通道（货币主义对照组,现代弹性低但方向仍在）。"),
    _T("cmdty.wti_crude", "inflation.cpi", "+", "1-2m",
       "能源价格直接进 CPI 能源分项,并经运输/化工间接渗透核心。"),
    _T("growth.avg_hourly_earnings", "inflation.sticky_cpi_yoy", "+", "1-2q",
       "工资是服务业主要成本——时薪增速支撑粘性服务通胀。"),
    # 利率 → 硅基核心（旗舰传导链）
    _T("rates.ust_10y_real", "capex.hyperscaler_capex", "-", "2-4q",
       "AI capex 是超长久期资产：实际利率抬升→NPV 门槛抬升→资本开支意愿承压（登记簿 high_real_rates_compress_capex 在验）。"),
    _T("rates.ust_2s10s_spread", "growth.real_gdp", "+", "lead 4q",
       "曲线斜率经银行息差→信贷供给领先实体增长约一年。"),
    _T("rates.mortgage_30y", "growth.housing_starts", "-", "1-2q",
       "按揭利率→购房负担→开工,居民端利率传导最短实体链。"),
    _T("sentiment.umich", "growth.retail_sales", "+", "lead 1-2m",
       "居民信心领先消费支出拐点（软先导）。"),
    _T("growth.initial_claims", "growth.unemployment_rate", "+", "lead 1-2m",
       "裁员脉冲（流量）先于失业率（存量）显影。"),
    # 流动性层级（恒等式：Fed 资产 − RRP − TGA ≈ 准备金）
    _T("liquidity.fed_total_assets", "liquidity.bank_reserves", "+", "weeks",
       "QE/QT 直接改变体系准备金（恒等式源头）。"),
    _T("liquidity.on_rrp", "liquidity.bank_reserves", "-", "weeks",
       "RRP 上升=货币基金停泊抽走准备金;RRP 释放=回补。"),
    _T("liquidity.tga", "liquidity.bank_reserves", "-", "weeks",
       "TGA 重建从市场抽流动性,下放即注入。"),
    _T("fiscal.federal_deficit", "fiscal.public_debt", "+", "months",
       "流量赤字累积为存量债务。"),
    _T("fiscal.public_debt", "rates.ust_30y", "+", "2-4q",
       "发债存量→久期供给→期限溢价（财政主导通道）。"),
    _T("liquidity.bank_reserves", "flow:risk_on", "+", "weeks",
       "准备金决定银行体系风险承接与做市能力——资金流台 risk-on 综合分的流动性解释变量（登记簿 liquidity_drives_breadth 在验）。"),
    # 信用条件 → 实体与硅基
    _T("credit.nfci", "credit.hy_oas", "+", "weeks",
       "金融条件综合收紧领先信用利差走阔。"),
    _T("credit.hy_oas", "capex.hyperscaler_capex", "-", "2-4q",
       "信用压力抬升融资成本并收紧发债窗口——A5 双相翻转的金融侧传导（登记簿 credit_stress_breaks_ai_loop 在验）。"),
    _T("credit.sloos_ci_standards", "growth.industrial_production", "-", "2-3q",
       "银行信贷闸门收紧领先实体制造投资转弱。"),
    # 汇率/情绪 → 链与资金流
    _T("fx.usd_broad", "theme:ai_chip", "-", "1-2q",
       "强美元压缩芯片链海外营收折算并收紧离岸美元融资（台/韩/欧客户敞口）。"),
    _T("sentiment.vix", "flow:risk_on", "-", "days",
       "隐含波动率飙升=风险偏好关闸,与资金流台 risk-on 互为印证。"),
    # 硅基核心内环（宏观外环最终闭合处）
    _T("capex.hyperscaler_capex", "compute.training_scaling", "+", "2-4q",
       "资本开支落地为训练算力扩张（硅基回路第一内环）。"),
    _T("compute.training_scaling", "power.datacenter_twh", "+", "2-4q",
       "算力扩张的物理镜像是数据中心用电（A2：约束迁移到能源）。"),
    _T("power.datacenter_twh", "cmdty.copper", "+", "2-4q",
       "数据中心/电网建设潮抬升铜需求——能源墙 capex 的原料端印证。"),
)

TRANSMISSIONS_BY_FROM: dict[str, tuple[MacroTransmission, ...]] = {}
TRANSMISSIONS_BY_TO: dict[str, tuple[MacroTransmission, ...]] = {}
for _tr in TRANSMISSIONS:
    TRANSMISSIONS_BY_FROM[_tr.from_key] = TRANSMISSIONS_BY_FROM.get(_tr.from_key, ()) + (_tr,)
    TRANSMISSIONS_BY_TO[_tr.to_key] = TRANSMISSIONS_BY_TO.get(_tr.to_key, ()) + (_tr,)


def theme_overclaims(theme: str) -> tuple[OverclaimLink, ...]:
    """登记簿断言 → 链（空 themes 的平台级断言不归入任何单链面板）。"""
    return tuple(c for c in OVERCLAIM_LINKS.values() if theme in c.themes)
