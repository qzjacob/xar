"""核心投资争论种子(策展的代码即真相层)。

论点里最难自动生成、最值钱的部分是**核心分歧**——一家公司/一个主题上,顶级多头与
顶级空头到底在争什么(ServiceNow:「AI 对其生意模式是颠覆还是赋能」)。让廉价批量 LLM
凭空造分歧,要么造出伪争论(答案显然),要么两边都是稻草人。所以对全 8 主题的旗舰名字
(~15-20 家)我们**人工策展争论种子**:question + 两边最强因果叙事(steelman)+ 引导后续
验证点的 suggested_metrics / suggested_event_types。种子在 research/thesis.py:dossier 注入
生成提示词,并作为 validate_thesis 的 required_debate_keys —— 有种子的公司**必须逐条回应**
(key 保持不变),长尾公司由 LLM 自行判断有无真分歧(宁缺毋滥,可留空)。

两层:
  · DebateSeed —— 公司级,硬约束(required_debate_keys)。
  · ThemeDebate —— 主题级,成员旗舰继承为软种子;并在 P5 主题健康度聚合中作为骨架。

约束(tests/test_thesis_ontology.py 强制):company_id ∈ registry.COMPANIES;theme ∈ THEMES;
suggested_metrics ∈ metric_packs.ALL_METRIC_KEYS ∪ indicators.ALL_INDICATOR_KEYS;
suggested_event_types ∈ catalysts.CATALYST_TYPES;key 全局唯一。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 争论种子内容在模块末尾定义(策展数据);此处先给类型与装配逻辑。


@dataclass(frozen=True)
class DebateSeed:
    """公司级核心争论种子(硬约束:该公司论点必须覆盖此 key)。"""
    company_id: str
    key: str
    question_zh: str
    bull_zh: str
    bear_zh: str
    suggested_metrics: tuple[str, ...] = field(default_factory=tuple)
    suggested_event_types: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ThemeDebate:
    """主题级核心争论(成员旗舰继承为软种子;主题健康度聚合骨架)。"""
    theme: str
    key: str
    question_zh: str
    bull_zh: str
    bear_zh: str
    macro_metric_keys: tuple[str, ...] = field(default_factory=tuple)
    rationale_zh: str = ""


def company_seeds(company_id: str) -> list[DebateSeed]:
    """某公司的策展争论种子(0..2 个)。"""
    return [s for s in DEBATE_SEEDS if s.company_id == company_id]


def theme_debates_for(themes: list[str] | tuple[str, ...] | None) -> list[ThemeDebate]:
    """这些主题下的主题级争论。"""
    ts = set(themes or ())
    return [d for d in THEME_DEBATES if d.theme in ts]


def _theme_debate_as_seed(cid: str, d: ThemeDebate) -> DebateSeed:
    # 主题争论渲染为公司种子:suggested_metrics 置空(macro_metric_keys 是主题聚合口径,
    # 非公司验证点口径,不参与公司 VP 校验域),保证种子合法性不变式。
    return DebateSeed(company_id=cid, key=d.key, question_zh=d.question_zh,
                      bull_zh=d.bull_zh, bear_zh=d.bear_zh)


def seeds_for(company_id: str, themes: list[str] | tuple[str, ...] | None = None) -> list[DebateSeed]:
    """装配一家公司在生成/校验时应覆盖的争论种子。

    = 公司级种子(硬)+(仅当该公司是旗舰,即已有 ≥1 公司级种子时)继承其主题级争论。
    长尾公司无公司级种子 → 返回 [] → 不强制任何争论(LLM 可留空)。"""
    cs = company_seeds(company_id)
    if not cs:
        return []
    out = list(cs)
    have = {s.key for s in cs}
    for d in theme_debates_for(themes):
        if d.key not in have:
            out.append(_theme_debate_as_seed(company_id, d))
            have.add(d.key)
    return out


def seed_company_ids() -> set[str]:
    """所有有策展种子的旗舰公司 id(P5 回填名单)。"""
    return {s.company_id for s in DEBATE_SEEDS}


# ══ 策展数据(由 debate-seed-curation workflow 起草 + 对抗复核后定稿)══════════════
_D = DebateSeed
_T = ThemeDebate

DEBATE_SEEDS: tuple[DebateSeed, ...] = (
    # ── ai_software ──
    _D(
        company_id="now", key="ai_disrupt_vs_empower",
        question_zh="AI 对 ServiceNow 按座位收费的工作流平台,是颠覆(Agent 绕开 ITSM 座位模型、席位被压缩)还是赋能(Now Assist 成为提价与消费扩收的增强器)?",
        bull_zh="Now Assist 已是独立加价 SKU:采用 Pro Plus / 企业版的客户 ACV 溢价约 25-30%,GenAI 相关净新增 ACV 连续数季超指引,带动 cRPO 同比稳定在 ~20% 以上。ServiceNow 是唯一同时坐拥企业『记录系统 + 行动系统』的平台,Agent 需要它的工作流引擎与权限模型作为执行底座与护栏,AI 让每席位产出更高从而支撑提价而非降席位。管理层已把定价从纯座位转向『座位 + agentic action 按量计费』的混合模型,到 2026 年 Agent 用量按量收费可对冲席位放缓,打开第二增长曲线。",
        bear_zh="Agent 原生栈(Sierra/Decagon 类客服 Agent、微软 Copilot、Salesforce Agentforce)正绕过 ITSM 人工 fulfiller 座位模型——当一个 AI Agent 替代 5 个 L1 坐席,ServiceNow 的按人头座位收入基础被侵蚀而非扩张。需澄清:ServiceNow 的 ~98% 是毛续约率、并非 Snowflake 式的净扩张 NRR,真正的减速信号是净新增 ACV 增速与 cRPO 同比的二阶导——若 agentic action 按量定价无法抵消 fulfiller 座位流失,净新增 ACV 与 cRPO 同比二阶导转负、续约率也会从 98% 边际走弱。更深层的是 AI 自助解决(deflection)让 IT 服务台工单量本身下降,长期 TAM 面临重定义。在高企的 EV/FCF 估值下,任何新签或 cRPO 减速信号都会被市场剧烈重定价。",
        suggested_metrics=("crpo_yoy", "crpo_yoy_accel", "net_new_arr_yoy", "rpo_yoy"),
        suggested_event_types=("contract_win", "tech_substitution", "pricing_change", "guidance_change")),
    _D(
        company_id="crm", key="agentforce_consumption_vs_seat_saturation",
        question_zh="Agentforce 的消费式 AI 计费,是能把 Salesforce 增速从高个位数重新加速到双位数,还是只是掩盖核心 CRM 座位已经见顶的遮羞布?",
        bull_zh="Agentforce 把计费从纯座位转向按对话/按 action 消费(初期定价 ~$2/对话,现已演进为 Agentforce 360 的 Flex Credits 按 action 计量),打开一个不受客户员工人数封顶的新收入池。Salesforce 拥有全球最大的结构化 CRM 数据加 Data Cloud(已达 ~10 万亿条记录量级),而 Agent 质量取决于数据接地,这是难以复制的护城河;Data Cloud + AI 是公司增长最快的板块(相关 ARR 同比三位数)。即便核心席位增长放缓到中个位数,消费收入叠加提价可把总营收增速从 ~8-9% 重新推回双位数,而 cRPO 同比维持 ~10%+ 说明大额多年单在回补。",
        bear_zh="核心 Sales/Service Cloud 座位已高度渗透,净新增席位放缓到低个位数,总营收增速已从 20%+ 结构性降到 ~8-9%,而 Agentforce/Data Cloud 消费收入基数太小(占比个位数),2-3 年内难以逆转大盘。Agentforce 落地要求客户先做数据治理与流程改造,PoC 到付费转化慢、消费型 ARR 兑现远晚于当前定价所隐含的预期(Flex Credits 用量爬坡后置);同时微软(Copilot 捆绑 M365/Dynamics 低价获客)与垂直 Agent 初创从两端夹击。若净新增 ARR 与 cRPO 同比继续减速,『消费重加速』叙事即被证伪——目前 EPS 增长主要靠回购与利润率扩张而非有机增长。",
        suggested_metrics=("crpo_yoy", "rpo_yoy", "revenue_yoy_accel", "arr_yoy"),
        suggested_event_types=("product_ramp", "pricing_change", "contract_win", "guidance_change")),
    _D(
        company_id="snow", key="ai_consumption_reaccel_vs_lakehouse_erosion",
        question_zh="AI 工作负载是重启 Snowflake 消费增长与 NRR 的引擎,还是其专有护城河正被开放表格式 + Databricks Lakehouse 商品化侵蚀?",
        bull_zh="消费制没有座位天花板,单位数据与单位算力用量会随 AI 工作负载(Cortex LLM 函数、Snowpark、非结构化数据 + agentic 分析)加速放大——产品营收同比仍 ~25-30%,净新增产品营收重回加速。RPO 同比维持 20%+,显示企业在回补大额多年承诺;NRR 虽从峰值回落但已在 ~125% 附近企稳,Cortex/Snowpark 等新品采用率快速爬坡有望把 NRR 重新推高。Snowflake 的治理、跨云与数据共享网络效应,使其仍是企业首选的『AI 数据底座』。",
        bear_zh="NRR 已从 ~178%(2022)结构性衰减到 ~125% 且趋势线仍向下(nrr_trend 斜率为负),说明存量客户的用量扩张动能在枯竭;消费制没有座位地板,意味着效率优化与宏观降本可直接砍掉用量。开放表格式(Apache Iceberg)+ Databricks Lakehouse 正把存储/计算锁定商品化,客户可把数据留在开放格式、按需接不同引擎,侵蚀 Snowflake 的专有护城河;复杂 ML/训练类 AI 工作负载更多流向 Databricks(Mosaic)与超大规模云。在两位数 EV/S 估值下,只要净新增产品营收与 RPO 同比继续降速、或 NRR 破 120%,空头叙事即被证实。",
        suggested_metrics=("nrr", "nrr_trend", "rpo_yoy", "revenue_yoy_accel"),
        suggested_event_types=("tech_substitution", "product_ramp", "guidance_change", "contract_win")),
    # ── ai_optical ──
    _D(
        company_id="innolight", key="share_hold_vs_pricewar",
        question_zh="龙头份额与溢价毛利能否穿越 800G→1.6T 迁移,还是模块被打成价格战下的通用盒子?",
        bull_zh="旭创握有 800G 可插拔约 35-40% 的全球份额、率先在 2024H2-2025 量产 1.6T,并锁定 Lumentum/Marvell 的 EML 与 DSP 供给,是英伟达 GB200/GB300 与谷歌 TPU 的首选光模块供应商;1.6T 单价约为 800G 的 2 倍,产品结构上移让收入在单价年降中仍能同比增 60%+,泰国产能对冲关税、叠加高端占比把毛利率稳在 33% 左右;规模、良率与供应链绑定构成后来者短期难以复制的护城河。",
        bear_zh="光模块本质是可被多源采购的组装盒子,新易盛等已在同一批客户处送样/认证 1.6T,份额可被抢、ASP 每年下探 15-20%;超大厂系统性推行双源/三源化压毛利;一旦最高速链路转向 CPO/LPO,旭创押注的 1.6T 可插拔 TAM 会封顶,龙头护城河从'技术领先'退化为'产能领先';若 2026 出现 capex 空窗,重资产的周期性会被放大成盈利与估值双杀。",
        suggested_metrics=("gross_margin_trend", "revenue_yoy_accel", "module_asp", "market_share"),
        suggested_event_types=("qualification", "product_ramp", "pricing_change", "tech_substitution")),
    _D(
        company_id="eoptolink", key="margin_surge_durable_vs_peak",
        question_zh="新易盛超过龙头的毛利与份额跃升,是结构性重估还是周期顶点的一次性异常?",
        bull_zh="新易盛毛利率跃升至约 40%+、一度反超旭创,靠的是 800G 高端结构、低税泰国产能与集中于头部超大厂/英伟达的高质量订单;收入同比翻倍以上,并早于多数同业认证 1.6T,是真金白银抢下的行业第二名,经营杠杆把 EPS 拉出数倍弹性;客户集中恰是深度绑定顶级 AI 需求的体现,而非单纯脆弱性。",
        bear_zh="这份毛利是 800G 极度紧缺(供给约束)叠加单一大客户占比超 40% 的周期顶点产物,集中度即脆弱性;随 1.6T 供给正常化、旭创/Coherent 与新进入者压价,ASP 下行会把反超龙头的异常毛利均值回归至低 30% 甚至更低;一旦大客户砍单、转自供或引入二供,高集中度会带来断崖式下修——超额毛利买到的其实是最高的盈利波动率。",
        suggested_metrics=("gross_margin_trend", "revenue_yoy_accel", "module_asp", "large_customers_yoy"),
        suggested_event_types=("qualification", "order", "supply_constraint", "pricing_change")),
    _D(
        company_id="coherent", key="integrated_arsenal_vs_levered_conglomerate",
        question_zh="Coherent 是被低估的垂直整合 AI 光链军火商,还是被周期业务稀释、杠杆偏高的综合体?",
        bull_zh="Coherent 自产 InP EML/激光器与 VCSEL——连中国模块厂都要向它采购,因此在模块价格战中它卖的是稀缺光芯片、在 CPO 时代供的是外置光源(ELS)/光引擎,两头通吃;英伟达的股权投资背书了其数据通信激光路线图,datacom 收发模块收入高速增长,随 AI datacom 占比上升毛利率向高 30% 迈进并同步用自由现金流去杠杆,足以对全公司重估。",
        bear_zh="Coherent 仍是背负约 50 亿美元债务的综合体,AI datacom 只占收入少数,被周期性的工业激光、电信与材料业务稀释;其自有 datacom 模块份额正被旭创/新易盛蚕食,激光/光芯片产能又被 Lumentum、源杰及客户自研复制,'军火商'的定价权可被侵蚀;高杠杆叠加 II-VI 与 Coherent 合并的整合执行风险,使其在 AI capex 一旦停顿时的盈利与偿债压力更重。",
        suggested_metrics=("gross_margin_trend", "revenue_yoy_accel", "market_share", "fcf_margin_trend"),
        suggested_event_types=("equity_investment", "product_ramp", "qualification", "tech_substitution")),
    # ── ai_chip ──
    _D(
        company_id="nvidia", key="merchant_gpu_vs_custom_silicon",
        question_zh="NVIDIA 的加速卡霸权会被超大规模厂商的自研 ASIC 侵蚀吗?——CUDA/NVLink 机架级系统护城河 vs 定制芯片在推理侧的 TCO 抢份额与压毛利",
        bull_zh="NVIDIA 卖的已不是芯片而是机架级系统与全栈:GB200/GB300 NVL72 用 NVLink/NVSwitch 把 72 颗 GPU 做成单一 scale-up 域,ASIC 至今无对等的系统级互连;叠加 CUDA 十余年生态、每年一代节奏(Blackwell→Rubin→Rubin Ultra)与 Spectrum-X/InfiniBand 网络、AI Enterprise 软件的价值捕获,训练侧份额稳在 ~90%。关键机制:ASIC 与 GPU 并非零和——超大厂两条腿都在加码(Google 同时买 TPU 与 GPU),推理正从静态 batch 转向 reasoning/agent 的动态长上下文,恰恰奖励通用可编程平台;数据中心营收连续多季 >50% 同比、毛利率 ~73-75%,是短缺租金而非泡沫定价,当前估值并未 price-in Rubin 周期的完整算力缺口。",
        bear_zh="推理是长期更大的 TAM,而推理正是自研 ASIC 的主场,且 ASIC 已越界到训练:Google TPU v6/v7、Amazon Trainium2(承接 Anthropic 前沿训练)、Microsoft Maia、Meta MTIA 已在自家负载把单位 token 成本压到 GPU 的 1/2-1/3,超大厂有资本、有反锁定动机自供。当客户从抢产能转向抠 TCO,NVIDIA 在 AI 加速器份额将从 ~90% 向 70% 滑落、~75% 的毛利率向 65% 均值回归,构成量价双杀式二次 de-rate;更深的隐忧是需求质量——供应商出资扶持买家再回购芯片的循环融资夸大了终端真实拉动,一旦头部大客户自研占比过半,通用平台叙事与估值同步塌缩。",
        suggested_metrics=("market_share", "gross_margin_trend", "revenue_yoy_accel"),
        suggested_event_types=("accelerator_launch", "product_ramp", "tech_substitution", "contract_win")),
    _D(
        company_id="tsmc", key="pricing_power_vs_capex_cyclical",
        question_zh="台积电是拥有定价权的先进制程+先进封装双垄断者,还是资本开支越来越重、被少数 AI 客户绑架的周期代工厂?",
        bull_zh="台积电是先进制程(N3/N2)唯一可行代工厂,且独家掌控真正的瓶颈——CoWoS 先进封装:AI/HPC 已占营收 >50%,公司近年首次拿到真实定价权(2025-26 晶圆连续提价、CoWoS 产能售罄至 2026 年)。护城河是双层的——即便 AI capex 进入消化,N2(GAA)tape-out 数超同期 N3,需求由旗舰手机 SoC + 全部 HPC/ASIC(包括超大厂自研,自研 ASIC 仍要回到台积电流片)共同支撑,买方越分散、代工方议价越稳。海外厂稀释可控,毛利率结构性稳在 53%+ 并随定价与良率上移,制程领先 2-3 年 + 量价齐升,ROE 持续 >25%。",
        bear_zh="本质仍是重资产周期代工,且正被 AI 单一变量放大:资本开支强度结构性抬升(年 capex 向 $450-550 亿走),Arizona/日本/德国海外厂拉低毛利 200-400bps 且爬坡漫长;营收高度绑定个位数 AI 大客户(NVIDIA + 少数云厂),客户集中度是历史峰值。所谓“定价权”可能只是 AI 短缺期的一次性租金——台积电历史上是价格接受者;一旦 2026-27 AI capex 消化、成熟制程持续过剩、或客户自研 ASIC 改变晶圆结构与议价格局,利用率与毛利同步下滑,而市场正用长期成长倍数给一个高杠杆周期股定价。",
        suggested_metrics=("gross_margin_trend", "capex_yoy", "market_share", "revenue_yoy"),
        suggested_event_types=("pricing_change", "capex_guidance", "capacity_expansion", "product_ramp")),
    _D(
        company_id="amd", key="credible_no2_vs_perennial_gap",
        question_zh="AMD 是能真实抢下 AI 加速卡份额的可信第二供应商,还是与 NVIDIA 差距持续拉大的追赶者?",
        bull_zh="AMD 是唯一可信的商用 GPU 替代:MI300/MI325 已到 >$50-70 亿年化收入,MI350(CDNA4)/MI400 路线对标 Blackwell/Rubin,且 MI400 的 Helios 机架级方案正面回应 NVL72 的系统级打法、补上 scale-up 短板;ROCm 生态快速成熟。超大规模厂商出于反 NVIDIA 锁定的战略需求主动扶持二供(Meta、微软、OpenAI 已下单),在 >$4000 亿加速器 TAM 里哪怕拿到 10-15% 都是低基数上的巨大营收弹性;叠加 EPYC 服务器 CPU 份额已过 35% 并持续爬升,数据中心营收与毛利双升、经营杠杆放大 EPS。",
        bear_zh="ROCm 落后 CUDA 数年,MI 系列真实利用率与能效仍逊一档,AMD 拿到的多是推理边角料 + 少数旗舰战略订单(二供而非可持续份额),份额指引一再跳票。NVIDIA 每年一代 + NVL72/Rubin 系统级打法让加速卡差距不是缩小而是拉大;AMD 数据中心 GPU 毛利结构性低于 NVIDIA、稀释公司毛利;最致命的是 ASIC 替代对商用二供打击最重——超大厂宁可自研 Trainium/TPU/Maia 也不买 AMD,夹在 NVIDIA 全栈与自研 ASIC 之间,商用二供的结构性空间被两头挤压。",
        suggested_metrics=("market_share", "revenue_yoy_accel", "gross_margin_trend", "eps_yoy"),
        suggested_event_types=("accelerator_launch", "contract_win", "product_ramp", "tech_substitution")),
    # ── space_exploration ──
    _D(
        company_id="rklb_spa", key="neutron_credible_no2_vs_starship_commoditizes",
        question_zh="Neutron 能否在 Starship 重塑 $/kg 经济性之前，把 Rocket Lab 变成可信、结构性盈利的中型运载\"二号供应商\"(并借此拉动高毛利 Space Systems)，还是注定迟到、烧钱、被商品化？",
        bull_zh="Neutron(LEO 约 13 吨、一级可回收)瞄准的是\"SpaceX 之外唯一可信的西方中型运载\"这块钱包——NSSL Phase 3 双源、SDA/太空军的国安发射与除星链外的所有商业星座都需要非 SpaceX 的第二选择，愿意为供应链多元化付溢价。Rocket Lab 已有 Electron 50+ 次发射与回收工程积累、以及罕见的垂直整合(自研 Rutherford/Archimedes 发动机、结构、航电、反应轮)；Neutron 目标单发约 5,000-5,500 万美元，是对 Falcon 9 的补位而非对撞。真正的价值不在发射价格战本身：发射一旦跑通，会把已占营收约三分之二、毛利更高的 Space Systems 从\"卖零件\"抬升为\"端到端交付+平台\"，backlog 已超 10 亿美元。一旦 Neutron 首飞并进入量产，现金流将从\"烧钱做研发\"翻转为\"发射+系统\"双轮盈利，$/kg 下降反而是其系统业务的成本顺风。",
        bear_zh="Neutron 首飞时间已多次右移(2024→2025→2026)，Archimedes 发动机与可回收一级仍未经飞行验证；每滑一个季度就多烧一个季度现金，公司仍深度亏损、经营现金流为负、靠增发稀释续命。真正的问题是终局经济性：Starship 一旦进入商业运营，$/kg 会被压向个位数美元区间，把中型可回收运载(正是 Neutron 的定位)整体商品化——届时 Neutron 就算按时首飞，也是\"用 Falcon 9 的价格去打一个 Starship 已经清场的市场\"，而其寄予厚望的 Space Systems 也要在一个被 SpaceX 免费运力与星链压低的价格带里竞争。当前估值已隐含 Neutron 无瑕执行+高利用率+系统持续放量，任何滑期或单位经济性证伪都是戴维斯双杀。",
        suggested_metrics=("launch_cadence", "gross_margin_trend", "fcf_margin_trend", "backlog_yoy"),
        suggested_event_types=("qualification", "product_ramp", "contract_win", "secondary_offering")),
    _D(
        company_id="asts_spa", key="d2d_broadband_platform_vs_starlink_outdeploy",
        question_zh="AST 的大孔径卫星\"手机直连宽带\"能否凭运营商合作+收入分成护城河成为全球标准，还是会被 Starlink 直连以数量级更快的部署节奏出局、并在建成连续覆盖前被资本闸门消耗殆尽？",
        bull_zh="AST 走\"运营商内嵌\"的轻资产路线——已与 AT&T、Verizon、Vodafone、乐天等锁定覆盖约 28 亿用户的独家/优先合作，采收入分成(无需自建用户获取)，且大孔径相控阵支持真正的宽带(语音/数据)直连、而非仅短报文，这是相对 Starlink 直连当前能力的差异化卡位。AT&T/Verizon 已有商业承诺、Google/Vodafone/AT&T/Verizon 战略入股、并拿下 FirstNet/太空军等政府合同，需求与资金两端被背书。一旦 Block 2 BlueBird 部署到约 25 颗(美国连续覆盖)/45-60 颗(全球)，收入将从零跃升至数十亿美元级、边际毛利极高——运营商把它当作现网频段的太空扩展而非新对手，这是 D2D 宽带的先发+专利卡位。",
        bear_zh="这是一个\"资本闸门\"决定生死的部署竞赛：每颗 Block 2 卫星造价高昂、连续覆盖需数十颗，而现有现金只够部署一小部分 → 结构性、反复的增发稀释，稀释本身就在吞噬多头的每股价值。执行上目前在轨仅个位数颗卫星，连续商用覆盖仍需数年。与此同时 Starlink 直连(自有火箭免费发射+T-Mobile 等渠道)以数量级更快的节奏铺星，可从发射运力和频谱两端\"饿死\"AST；叠加频谱依赖运营商地面频段的监管不确定性、以及收入分成模式下终端用户付费能力尚未被验证。若连续覆盖滑到 2027 年之后，先发窗口关闭，隐含无瑕执行+高 ARPU 变现的估值将戴维斯双杀。",
        suggested_metrics=("launch_cadence", "capex_yoy", "free_cash_flow", "revenue_yoy"),
        suggested_event_types=("product_ramp", "partnership", "equity_investment", "secondary_offering")),
    # ── humanoid_robotics ──
    _D(
        company_id="tsla_hum", key="integrator_value_capture_vs_assembler",
        question_zh="作为人形整机厂/集成商,特斯拉是像做 Model 3 一样吃掉全链最厚的利润,还是在人形上沦为只赚组装薄利的组装厂?",
        bull_zh="特斯拉自研旋转/线性执行器、自建产线,并复用 FSD/Dojo 与自有工厂真实作业场景训练 VLA 大脑,构成'整机+大脑+数据'闭环——自有工厂是别人没有的独占训练环境,数据飞轮随部署自我强化。就像电动车时代它并不自产电芯、却靠整合+软件+品牌吃到最厚利润一样,人形上它同时掌握 spec 定义、放量节奏与终端场景,能用规模把 BOM 压向 2 万美元、把单机毛利做到 30%+。人形价值链里唯一同时握有终端场景(自有工厂)、AI 栈与整机定义权的就是它,集成者即价值捕获者。",
        bear_zh="人形 BOM 的 60%+ 沉在上游硬件(丝杠/减速器/无框电机/灵巧手),且被绿的谐波、三花、拓普等中国供应链主导——特斯拉既造不出比专业厂更便宜的核心件,又在 AI 侧面对 Nvidia 与中国大模型的商品化竞争,两端受挤,最终更像 PC/手机 OEM 只赚个位数组装利润。'2 万美元 BOM、30%+ 毛利'至今没有在任何量产规模上被证明,只是假设;Optimus 迄今零外部收入、纯自用部署,TAM 是自我循环、缺第三方验证,却已被市值 price-in 成第二增长曲线。",
        suggested_metrics=("gross_margin_unit", "bom_cost", "units_shipped", "market_share"),
        suggested_event_types=("product_ramp", "qualification", "tech_substitution", "capex_guidance")),
    _D(
        company_id="002050sz_hum", key="actuator_second_curve_vs_priced_option",
        question_zh="三花的机器人执行器业务,是把它从热管理周期股重估为人形执行器总成龙头的第二成长曲线,还是尚未落地、已被过度定价的期权?",
        bull_zh="三花凭十年绑定特斯拉汽车热管理建立的信任、精密制造与规模化降本,已切入 Optimus 线性执行器总成——而线性执行器本质是机电总成集成,离三花的电机/阀件/精密件主业远比'纯做丝杠'更近,它可自研总成、外购或合作解决丝杠环节。单机执行器价值量约 3000-5000 美元,若 Optimus 2027 年放量十万台级,仅执行器即贡献数十亿元收入,且毛利高于热管理主业;热管理现金流又为产能扩张提供无稀释的安全垫。这是把 200 亿营收公司重估为 20x+ 成长股的第二曲线。",
        bear_zh="机器人收入至今近乎为零,执行器定点未官宣、未 SOP;即便拿下总成,价值与毛利最厚的行星滚柱丝杠与轴承恰恰是三花不生产的环节,利润会向丝杠专业厂外溢,而它还要直面拓普与绿的谐波。3000-5000 美元的单机口径也建立在特斯拉不双源、不自制的假设上,单一客户集中度高。与此同时家电制冷主业陷价格战、汽车热管理增速降到 15-20%,当前股价已把机器人期权 price-in——一旦定点落空或延期,就是'主业降速+期权证伪'的戴维斯双杀。",
        suggested_metrics=("actuator_content_usd", "revenue_yoy", "gross_margin_trend", "backlog_yoy"),
        suggested_event_types=("qualification", "product_ramp", "order", "guidance_change")),
    _D(
        company_id="601689ss_hum", key="rollerscrew_moat_vs_specialist_squeeze",
        question_zh="拓普能否把汽车 Tier-1 的客户绑定与平台化制造复制到人形,凭行星滚柱丝杠/线性执行器成为 Optimus 多品类核心供应商,还是在高精度传动上并无壁垒、被专业厂挤压?",
        bull_zh="作为特斯拉最深度的中国 Tier-1 之一,拓普已自研行星滚柱丝杠+线性执行器总成、单设机器人事业部与专线,并能把自有锻造/轴承等垂直整合能力延伸到传动件。凭与 Tesla 的信任+平台化降本,有望在 Optimus 执行器与灵巧手拿下多个定点,单机价值量可达 5000-8000 美元。多品类卡位(执行器+灵巧手)比单一环节供应商弹性更大——这把一家 200 亿汽车零部件公司重估为人形核心供应商。",
        bear_zh="行星滚柱丝杠是真正难啃的高精度传动,拓普此前无量产积累,良率/精度/寿命(MTBF)在大批量下尚未验证,直面丝杠专业厂、绿的谐波与三花挤压,份额与定价并无先发壁垒——保险杠/底盘出身的 Tier-1 未必能把 Tesla 亲密度转化为传动件的持久护城河。机器人收入贡献仍近乎为零,而汽车主业高度依赖单一客户 Tesla、随其销量增速放缓承压;当前估值已含机器人期权,任何定点或量产延期都会回吐。",
        suggested_metrics=("actuator_content_usd", "gross_margin_unit", "revenue_yoy", "market_share"),
        suggested_event_types=("qualification", "product_ramp", "order", "tech_substitution")),
    # ── internet ──
    _D(
        company_id="googl", key="ai_search_disrupt_vs_defend",
        question_zh="AI 答案引擎(ChatGPT/Perplexity/Gemini)是在结构性解构 Google 的搜索广告垄断,还是 AI Overviews/AI Mode + Gemini 反而守住并扩大了查询量与变现?",
        bull_zh="Google 仍握约 90% 搜索份额、年约 5 万亿次查询与 Chrome/Android/默认位的分发闭环;AI Overviews 已覆盖 15 亿+ 用户,管理层称其变现'与传统搜索大致相当',且 AI Overviews/AI Mode 把更长、更复杂、以前根本不会在搜索里问的问题也纳入进来——总查询量与商业查询量双双继续增长,查询 TAM 不减反增。Gemini 2.x 已追平模型代差、Gemini app 月活破数亿,Cloud 连续加速至 30%+、backlog 创新高,给了 Google 把 AI 变现的第二增长极。搜索广告收入维持 10%+ 增长,证明这是一门被 AI 强化、而非被掏空的生意。",
        bear_zh="首次出现 Safari 内 Google 搜索量同比下滑(Eddy Cue 庭上证词),ChatGPT 周活 8 亿+、且已内建购物/结账,Perplexity 亦推商业查询——正把最值钱的信息型/商业型/零售查询整块抽走。零点击 AI 摘要 + AI Mode 把付费蓝链往下压、可挂广告的结果位在缩小,即便'每次查询变现相当',查询结构也在向低变现的对话式迁移。叠加反垄断补救动摇默认位分发(见另一种子),而 750-850 亿美元/年 capex 恰恰是在'防守一门正被 AI 结构性进攻的生意'时吞噬 FCF——高投入撞上入口被侵蚀,是最危险的组合。",
        suggested_metrics=("revenue_yoy", "revenue_yoy_accel", "market_share", "capex_yoy"),
        suggested_event_types=("tech_substitution", "product_ramp", "regulatory_action", "earnings")),
    _D(
        company_id="googl", key="antitrust_remedy_break_vs_routearound",
        question_zh="反垄断补救(搜索默认位案 + adtech 案)会结构性拆掉 Google 的分发与广告服务栈,还是行为层面可管理、Google 能绕开?",
        bull_zh="2025 年 9 月搜索案补救远轻于市场最坏预期——不强制拆分 Chrome、仍可付费竞标默认位(仅去独家),法院明确不会把 Google 拆散;adtech(EDVA)案即便被迫剥离 AdX,卖方展示广告栈占集团营收不到 10%、且卖方分析师早已 de-risk。Google 靠 Chrome+Android+Gemini 的自有分发逐年降低对付费默认位的依赖,监管拖尾更多是估值的悬顶压制而非现金流毁灭;随着补救逐步落地,不确定性折价正被移除。",
        bear_zh="adtech 案(Brinkema 法官已判其非法垄断)补救仍在进行,一旦强制剥离 AdX/DFP,将结构性削弱卖方广告栈与开放网络展示变现、并树立可被后续私诉援引的判例;若默认搜索独家性被剥夺、给 Apple 约 200 亿美元/年 TAC 的逻辑被打断,最高毛利的流量入口将被慢性侵蚀。叠加欧盟 DMA 罚单与漫长上诉,监管从'尾部风险'固化为持续多年压制估值倍数与再投资节奏的实体成本,而非一次性可清算的事件。",
        suggested_metrics=("market_share", "operating_margin", "revenue_yoy", "free_cash_flow"),
        suggested_event_types=("regulatory_action", "litigation", "guidance_change", "mna")),
    _D(
        company_id="meta", key="capex_roi_vs_fcf_compression",
        question_zh="Meta 千亿级 AI capex + 超级智能实验室支出,是有回报的变现引擎(Advantage+ 自动化广告 + 推荐 AI + 消费级 AI 助手),还是低可见度、压制 FCF 与利润率的开放式烧钱(Reality Labs + 人才军备赛)?",
        bull_zh="核心 AI 投入已经在赚钱:Advantage+ 购物广告年化跑到 200 亿美元+,AI 推荐把 FB/IG 时长同比拉双位数、把广告转化率提升个位数到双位数;34 亿+ 家族 DAU 给 Meta AI(月活 10 亿+)与商业消息(点击到 WhatsApp)无可比拟的分发,自有推理算力压低长期单位成本。扎克伯格在 2023'效率之年'已证明该砍就砍,核心广告经营利润率约 50%;且服务器可用年限延长在会计上平滑了折旧冲击——capex 是在建可复用、可租(Cloud/推理)的算力资产,而非填坑。只要广告端持续吸收折旧,高 capex 反而是加宽护城河的再投资。",
        bear_zh="2026 年 capex 指引冲向 1000 亿美元+、总费用同步膨胀,Reality Labs 累计亏损已超 700 亿且仍无消费级起量,如今又叠加'超级智能'实验室九位数薪酬的人才军备赛、却拿不出对应的变现模型——2022 帝国式烧钱的风险重演。这轮 capex 的折旧与员工成本将在 2026-27 撞上因高基数/宏观回落而减速的广告增速,FCF 利润率从约 30% 被压向低 20%;消费级 AI 助手变现未证,Llama 开源换来声量却换不回美元。可见度低、久期长、终局不清的开放式支出,正是估值最难给溢价的那类。",
        suggested_metrics=("capex_yoy", "fcf_margin_trend", "operating_margin", "arpu_yoy"),
        suggested_event_types=("capex_guidance", "guidance_change", "product_ramp", "earnings")),
    # ── retail ──
    _D(
        company_id="wmt", key="ad_flywheel_vs_margin_ceiling",
        question_zh="沃尔玛能否靠高毛利的『另类利润』(零售媒体+履约+会员)把自己从 ~4% 营业利润率的折扣零售商重估为高质量平台?",
        bull_zh="沃尔玛的利润引擎已从卖货转向广告/履约/会员——Walmart Connect 广告收入约 44-45 亿美元、同比增 25-30%,叠加 3P 市场、履约服务(WFS)与 Walmart+ 会员,这些 50-80% 增量毛利的收入以 20-30% 增长。真正的杠杆不是它占 6800 亿营收的比例,而是它以远高于集团的增量利润率,持续贡献到『营业利润增速』——已连续多季实现营业利润增速快于营收,管理层亦明确指引利润增速跑赢销售额;再叠加供应链自动化与 GenAI 降本、后 Cookie 时代难以复制的第一方购物数据,营业利润率有望从 ~4.2% 结构性抬升至 5%+,支撑其从 18-20x 的零售估值重估到 35x+ 的平台估值。",
        bear_zh="重估已把广告梦提前定价,但广告收入仍只有 ~45 亿美元、占 6800 亿营收不到 1%,即便高增几年内也撬不动 ~4% 的合并利润率;核心仍是毛利率 <25%、以生鲜为主、资本开支沉重的零售商,正面临关税推高 COGS、工资通胀、以及为守份额对抗 Amazon/Aldi 的持续价格投入。自动化 capex 本身是双刃——一旦拖累 SG&A 去杠杆、或利润率抬升不及指引。在 35x+(vs 十年历史 ~18-20x)估值下,倍数压缩的下行将远大于个位数的 EPS 上行。",
        suggested_metrics=("operating_margin", "gross_margin_trend", "same_store_sales", "eps_yoy"),
        suggested_event_types=("earnings", "guidance_change", "pricing_change", "mna")),
    _D(
        company_id="cost", key="membership_annuity_vs_valuation",
        question_zh="Costco 近乎纯利润的会员年金(~90% 续费率+周期性提价)与个位数的盈利增速,能否撑住其 ~50x 的历史级估值?",
        bull_zh="Costco 本质是收会员费的『收费公路』而非零售商——~48 亿美元会员费收入几乎全是利润,美加续费率 ~92.9%、全球 ~90.5% 且屡创新高,2024 年 9 月刚把金星卡 60→65、精英卡 120→130 美元提价,每次提价约 5-10 亿美元几乎直落营业利润;商品端接近平进平出只为拉动会员,真正的引擎是这笔抗通胀、带定价权的年金,加上 ~3% 的门店数增长(全球 ~900 家仓店,中国单店销量约为美国的 2 倍,长跑道未开发)。这种债券式复利+周期性提价权,配上 ~10% 的 EPS 增长,配得上溢价倍数。",
        bear_zh="在 ~50-55x 前瞻 PE、一个 ~3.5% 营业利润率、会员费仅占利润 ~11% 的生意上,估值已是自身历史(~25x)与同业的 2-3 倍;剔除油价/汇率后 comps 中个位数、~3% 面积增长、~9-10% EPS——本质就是个 ~9% 的盈利增速,50x 对应盈利收益率不到 2%,而无风险利率已 4%+,倍数必须压缩。国际扩张(尤其中国执行风险)比多头讲的更慢更颠簸,每年 ~25-30 家开店并不激进;任何续费率或会员增长的减速,都会重估一只被定价为『永远完美』的股票。",
        suggested_metrics=("same_store_sales", "operating_margin", "net_new_units_yoy", "eps_yoy"),
        suggested_event_types=("earnings", "pricing_change", "capacity_expansion", "guidance_change")),
    # ── restaurants ──
    _D(
        company_id="mcd", key="value_loyalty_flywheel_vs_margin_drain",
        question_zh="麦当劳的\"McValue 价值平台 + 数字忠诚度飞轮\"——是能穿越周期的份额与频次增强引擎,还是对结构性走弱的低收入客群做的、侵蚀餐厅层 / 加盟商利润的防御性让利?",
        bull_zh="忠诚度会员 90 天活跃已达约 1.75 亿、目标 2027 年 2.5 亿,数字化占系统销售约 30% 并向 40% 爬升;长期化的 $5 套餐 / McValue 平台重新拉动了美国客流,把与休闲正餐的价差进一步拉开,在缩小的蛋糕里持续抢份额。麦当劳约 95% 加盟:价值驱动的客流仍在更高系统销售上收取特许权使用费,而四面墙成本由加盟商承担;第一方 CRM / 忠诚度让麦当劳能在 $5 锚点之下做\"外科手术式\"个性化定价、提升有效客单与到店频次,因此让利本质是获客漏斗而非无差别牺牲利润。IOM / IDL(国际运营 / 授权市场)同店仍为正,对冲美国低端疲软;G&A 纪律与单店效率守住加盟商现金流。",
        bear_zh="把 $5 价值平台永久化,本身就是承认核心低收入客群在 2019 年以来累计约 40% 菜单涨价后已被支出约束;价值客流是利润最低的部分,即便同店转正,餐厅层与加盟商四面墙利润率也会被压缩、同店质量恶化。永久化 $5 需要加盟商共同出资,加剧总部—加盟商(NOA)张力,而价值组合上移会挤压加盟商四面墙现金流、封住翻新 / 再投资——恰恰是飞轮的燃料。忠诚度\"活跃会员\"被重度折扣买来的低增量利润互动高估,并不代表增量需求;价值战会招致 Wendy's / Burger King / Taco Bell 的 $5 跟进,对冲掉份额收益,最终麦当劳只是在为全行业的利润率重置买单;同店靠客单转正而客流仍软,就是\"借来的同店\"。",
        suggested_metrics=("same_store_sales", "traffic", "check_size", "operating_margin", "digital_mix"),
        suggested_event_types=("pricing_change", "earnings", "guidance_change", "product_ramp")),
    _D(
        company_id="cmg", key="growth_durability_vs_priced_for_perfection",
        question_zh="Chipotle 的\"单店扩张 + 吞吐 / AUV 提升\"故事——是能撑起约 45x 前瞻 PE 的持久双位数复利,还是 2025 年客流转负已暴露概念走向成熟、溢价必须去化?",
        bull_zh="北美门店从约 3,700 家有望向 7,000+ 家扩张、年开店 8–10%,新店现金回报率约 60%、单店投资约 100 万美元;100% 直营意味着全额利润捕获与零加盟摩擦。吞吐工具(双面炉、切菜机、Autocado 牛油果机、Hyphen 自动备餐线)在结构上把劳动力移出模型,在加州 20 美元最低工资下守住约 27% 的餐厅利润率,并把 AUV 从约 320 万美元推向 400 万。忠诚度 4,000 万+ 会员、菜单创新(限时蛋白 LTO + 忠诚度游戏化)支撑中个位数同店并可能在下半年带来客流拐点;合起来即约 15% 单店增长 × 正同店 × 利润扩张 = 高双位数至 20% 的 EPS 复合增速,足以支撑当前溢价。",
        bear_zh="2025 年客流在\"没有衰退\"的情况下就转负、同店降到低个位数至持平,揭示出在 2019 年以来累计约 30% 菜单涨价后、以价格驱动的同店动能已经耗尽,而周期性下行尚未被模型计入;100% 直营意味着零加盟缓冲——工资与客流下行完全由公司自担。AUV 与吞吐已接近实际天花板,新店开进更边际的商圈使增量单店回报递减。在约 45x 前瞻 EPS 下,股价已定价了零容错的约 20% 增长,一次同店 / 客流不及预期就会像 2025 年那样把估值打去 20–30%,溢价本身就是最大的风险敞口。",
        suggested_metrics=("same_store_sales", "traffic", "check_size", "net_new_units_yoy", "restaurant_margin"),
        suggested_event_types=("earnings", "guidance_change", "product_ramp", "capacity_expansion")),
)

THEME_DEBATES: tuple[ThemeDebate, ...] = (
    _T(
        theme="ai_software", key="seat_saas_survival_in_agent_era",
        question_zh="在 AI Agent 时代,按座位/席位收费的 SaaS 商业模式究竟是被『赋能』(每席位价值提升 + 消费计费打开第二增长曲线),还是被『颠覆』(Agent 替代人工座位、席位被压缩、价值向模型层与编排层转移)?",
        bull_zh="现有 SaaS 龙头掌握企业的『记录系统 + 工作流引擎 + 结构化数据』,这是任何可靠 Agent 的接地层与执行底座——Agent 越强,越依赖这些平台的权限模型与数据护栏。龙头正快速把定价从纯座位迁移到『座位 + 消费(按 action/对话/token 计费)』混合模型,打开不受员工人数封顶的新收入池;AI 让每席位产出更高从而支撑提价而非降席位。板块头部厂商 cRPO/RPO 同比仍维持在 ~15-25%,证明企业 IT 预算在向 AI 增强的现有平台集中而非分散,整体 Rule of 40 依然成立。",
        bear_zh="Agent 原生栈把价值锚点从『人使用软件』转向『Agent 完成工作』,直接侵蚀按人头计费的根基——当一个 Agent 替代 5 个一线坐席,席位数被压缩而非扩张。消费计费的增量在 2-3 年内远不足以抵消座位流失,头部厂商营收增速已从 20%+ 结构性降到高个位数;与此同时价值加速向模型层(OpenAI/Anthropic)与编排层(Copilot/LangGraph 类)转移,应用层 SaaS 有沦为『薄壳』之虞。若板块 cRPO 同比与净新增 ARR 持续减速、NRR 破位,按座位 SaaS 赖以支撑的高 EV/S、高 NRR 估值范式将被系统性下修。",
        rationale_zh="三家旗舰(ServiceNow 工作流、Salesforce CRM、Snowflake 数据)分处软件链不同环节,但都在同一根本问题上被多空双方定价:AI Agent 是把现有平台变成更贵的底座,还是把它们变成被绕过的中间层。这条分歧决定了整个 ai_software 板块的估值范式存废,是成员公司各自争论(座位/消费/lakehouse)的共同上位骨架。"),
    _T(
        theme="ai_optical", key="pluggable_vs_cpo",
        question_zh="在 1.6T→3.2T 代际,独立可插拔光模块环节是被 CPO/硅光集成抹掉,还是继续做算力互连的规模化承载体?",
        bull_zh="可插拔在 2025-2027 的 1.6T 时代仍是主流承载体:scale-up 域(NVLink)现阶段仍走铜互连,真正放量的是 scale-out 光互连;而 CPO 的良率、现场可维护性(一颗激光器失效即拖累整台交换机)与热管理尚未过关,超大厂出于运维弹性与多源采购继续锁定可插拔,英伟达 Spectrum-X 以太网路线图在推 CPO 的同时仍保留可插拔 OSFP。每台 GB200/GB300 随网络端口数扩张,单加速器对应的光模块用量成倍增长,行业 TAM 从约 100 亿美元向 200-300 亿美元扩张。LPO/LRO 是模块厂自身去 DSP 的降本升级而非被第三方替代,硅光自研反而让模块厂垂直整合光引擎、把毛利做得更高。",
        bear_zh="英伟达 Quantum-X Photonics(2026-2027)把光引擎搬进交换机 ASIC 封装、博通 Bailly CPO 已进入量产,最高速率、最短 reach 的链路(1.6T/3.2T)在功耗与密度上会优先转 CPO,可插拔被挤向低速与边缘,独立模块 TAM 见顶。价值迁移已经在发生:LPO/LRO 抽掉 DSP(约占模块 BOM 20-30%)本身就证明模块的价值含量在流失;CPO 进一步取消可插拔连接器与组装环节,把价值锁进交换机/GPU 封装与上游光芯片。博通/英伟达及部分客户的硅光自研,使模块厂有从'系统供应商'沦为封装代工的风险。",
        rationale_zh="这是全体模块厂(旭创/新易盛/Coherent)与上游光芯片的共同最高阶分歧,决定整个环节的存续与价值捕获位置——继续做规模化互连承载体,还是被交换机/GPU 封装吸收。多空两侧均有顶级资金真实持有:看多方押注 AI 互连需求扩张、可插拔延续;看空方押注环节被 CPO/硅光集成、价值上移到光芯片与封装。宏观键取 hyperscaler_capex_yoy(需求周期)与 cpo_attach_rate(替代进度),二者交叉点即本分歧的裁决线。"),
    _T(
        theme="ai_chip", key="ai_capex_supercycle_vs_bubble",
        question_zh="AI 算力资本开支是多年结构性超级周期,还是正走向 2026-27 消化与 ROI 清算的 capex 泡沫?",
        bull_zh="AI 基础设施仍处早期:推理与 token 需求随 reasoning 模型和 agent 从静态 batch 转向长上下文,消耗量指数级放大,四大超大规模厂商 2026 年 capex 合计指向 >$5000 亿且仍在加速,主权 AI 与企业侧推理构成第二、第三增长腿。关键是真正的约束在供给侧而非需求——CoWoS、HBM、数据中心电力全线售罄且交货可交叉验证,是短缺租金而非过剩泡沫。整条链(代工→GPU/ASIC→系统)可复利成长多年,当前估值并未 price-in 完整的算力短缺。",
        bear_zh="这是一场带循环融资的 capex 泡沫:供应商(NVIDIA/微软/甲骨文)出资扶持买家(OpenAI/CoreWeave)再回购自家芯片,夸大了终端真实拉动,累计 AI capex 冲向 >$1 万亿,而终端 AI 应用收入只是其零头。快速贬值的 GPU 折旧、需求 air-pocket、或任一超大厂在 2026-27 削减 capex,都会触发全链条同步消化与去估值;NVDA/TSMC/AMD 的营收、利用率、毛利同锚一个变量,高经营与财务杠杆意味着回撤剧烈同步。",
        rationale_zh="全链条成员(NVDA/TSMC/AMD)的营收、产能利用率、毛利与估值同时锚定同一个变量——AI 算力 capex 的持续性,这是决定 ai_chip 主题 β 的最高阶分歧;任一成员的公司级争论(份额、定价权、二供)都在其之下展开。"),
    _T(
        theme="space_exploration", key="multi_vendor_profit_pool_vs_spacex_winner_take_most",
        question_zh="在 SpaceX/Starship 用可回收+垂直整合+内部免费发射持续压低 $/kg 的范式下，上市的太空纯玩家能否靠\"非 SpaceX 需求\"守住一块结构性利润池，还是终将沦为反复增发稀释的估值陷阱、被赢家通吃？",
        bull_zh="太空 TAM 正从数百亿走向数千亿美元，且制度性地内建了\"第二供应商\"需求：美国国安发射(NSSL Phase 3 明文双源、SDA/太空军)、盟国主权发射与卫星能力、运营商要一个中立于 Starlink 的手机直连平台——这些钱包按定义不会流向唯一的 SpaceX。更关键的是最聪明的多头并不在 $/kg 上跟 SpaceX 打价格战：利润池正沿栈下移，从被商品化的\"发射\"迁往有护城河的\"太空系统/连接层\"——$/kg 每下一个台阶，可寻址的星座与应用市场指数级放大，对已建立垂直整合(自研发动机/卫星/相控阵)且握有在手 backlog 的独立玩家而言，SpaceX 便宜的运力是它们系统与连接业务的成本顺风、需求放大器，而非终局威胁；它们能在成本曲线下移中同时吃到量与结构性毛利。",
        bear_zh="这是一个赢家通吃的规模游戏：SpaceX 用 Falcon 9 复用把发射价格锚死、再用 Starship 把 $/kg 压向个位数美元，且自身既是最大运载方又是最大需求方(星链)，享有\"内部免费发射+现金流自造血\"的飞轮；任何上市纯玩家都得在公开市场为同量级资本开支反复稀释融资——这是结构性的资本成本劣势，不是一次性的。而\"第二供应商\"这块钱包被高估：真正制度性锁定的 TAM(盟国主权发射+非星链商业星座+国安双源份额)相对独立玩家必须募集的巨额 capex 偏小，即便如愿拿到\"二号供应商\"身份，也未必打得过自身的资本成本。一旦范式固化为\"SpaceX 定价、别人跟随\"，独立玩家的终局要么是低利润补位者、要么是烧钱先烈；而当前板块估值普遍隐含各家都无瑕执行、且 $/kg 下降\"只利己不利敌\"——在赢家通吃格局下这是系统性高估。",
        rationale_zh="两家旗舰赛道看似不同(Rocket Lab 做运载+太空系统、AST 做手机直连宽带)，但最强多头与最强空头的分歧收敛到同一个变量：SpaceX/Starship 的规模与垂直整合，究竟是\"做大整个行业蛋糕、把利润池推向第二供应商与系统/连接层\"，还是\"把利润池连同免费运力飞轮收敛给自己、让所有上市纯玩家沦为稀释机器\"。这个宏观变量(尤其 $/kg 的下降路径、Starship 发射节奏、以及利润是否外溢出发射环节)决定了对每一家的估值终局，因此作为主题级骨架被两家旗舰继承。"),
    _T(
        theme="humanoid_robotics", key="massprod_inflection_vs_narrative_bubble",
        question_zh="人形机器人是否会在 2026-2028 兑现从 demo/试产 到真实规模量产(万台→十万台级)的产业拐点,还是又一轮由叙事与估值支撑、反复跳票的泡沫?",
        bull_zh="与 2015 年的自动驾驶、以及历次'明年就量产'不同,本轮首次出现可验证的供给侧领先信号:特斯拉 Optimus 已进入 2026 年数千台自用试产、指引 2027 年产线拉到十万台级;中国核心零部件(行星滚柱丝杠、谐波减速器、无框电机、灵巧手)多数已进入定点(SOP 前)/小批量交付,整机 BOM 正从 5-10 万美元向 2 万美元下探。真正的领先指标不是当期出货量,而是全链定点节奏与产能预定——它们领先真实放量约 12-18 个月,现在正是这个先导窗口。一旦单机全成本击穿工业替代的经济性门槛(对标 3-5 万美元/年的人力),就会像电动车 2019 年渗透拐点那样进入非线性放量。",
        bear_zh="至今全行业真实交付仍以百台计,量产指引被反复下修(Optimus 出货目标一再跳票);VLA 泛化、灵巧手可靠性、单位经济三大瓶颈无一真正解决,机器人在真实产线上的良率与 MTBF 远未达标。更关键的是它与电动车并不同构:电动车替代的是需求与价格点都已知的成熟品类,而人形要同时押注'技术能跑通'和'催生出全新劳动替代市场'两件事——是双重下注而非单一下注。定点(SOP)≠ 上量,历史上每一轮机器人周期都有过同样的定点与'明年量产'。而板块普遍 40-80x PE 已把 2030 年 TAM 贴现回今天,任一量产节点不及预期都会触发戴维斯双杀式 de-rate。",
        rationale_zh="这是全主题成员共享的最高阶分歧——量产拐点的真伪与时点决定了整条链(OEM 到零部件)的定价基准;所有公司级争论都是它在各环节价值捕获上的投影。"),
    _T(
        theme="internet", key="ai_answer_engine_vs_ad_moat",
        question_zh="生成式 AI 时代,'聚合注意力 → 竞价广告变现'的平台模型,是被答案引擎/AI Agent 解构,还是被 AI 反向加深护城河(参与度、投放 ROAS、自有算力壁垒)?",
        bull_zh="AI 是延续式创新、不是颠覆:头部平台手握数十亿 DAU 的分发闭环、十年行为数据与既有广告主预算池,而生成式创意 + 全自动投放(Advantage+/Performance Max)把广告 ROAS 抬高个位数到双位数、把定价权与 CPM 留在平台侧;AI 推荐把 Reels/Shorts 时长同比拉双位数,单位流量供给不降反升。隐私新政(ATT/Cookie 退场)后,能用第一方登录图 + 自有推理算力做转化建模的正是这些巨头——AI 反而加宽了它们相对中小竞品的护城河。更关键:答案引擎自己也要变现,而最可能把对话流量兑成美元的,仍是握有广告主关系与竞价系统的在位者。数百亿美元自有推理算力抬高新进入者门槛,行业广告收入继续双位数复合。",
        bear_zh="LLM 答案引擎正在拆掉漏斗顶端:ChatGPT 周活 8 亿+、Perplexity 等零点击直接交付答案,最值钱的信息型/商业型查询被从竞价广告位抽走;而对话式界面天然广告密度只有搜索结果页的零头,'把注意力批发给竞价广告'这一变现范式的可挂载面在结构性收缩。Agentic 购物让 AI 代人比价下单、绕过点击广告,直接侵蚀零售/商业查询这块利润最厚的地带。与此同时全行业被拖入千亿美元级 capex 军备赛,折旧洪峰将在 2026-27 与因高基数/宏观回落而减速的广告增速正面相撞,FCF 利润率被吞噬——而'AI 答案'的终局变现远未验证:先确定性地花钱,再赌有没有钱赚。",
        rationale_zh="这是 ServiceNow 范式('AI 颠覆 vs 赋能')在广告平台世界的映射:全体成员的最高阶分歧不是某一家的份额,而是'把注意力卖给竞价广告'这一变现范式在 Agent/答案引擎时代的存废。googl 站在被解构的正面战场(搜索即答案),meta 站在被赋能的一侧(信息流参与度 + 自动化投放),两家对同一问题给出相反赌注,恰好张起主题的多空谱系;主题健康度可据两家验证点的合成天平判断范式是被证伪还是被巩固。"),
    _T(
        theme="retail", key="annuity_premium_vs_thin_margin",
        question_zh="折扣零售龙头的『另类利润年金』(会员费/零售媒体)能否支撑整个板块从零售估值(~18-25x)重估为复利估值(~35-50x)并守得住?",
        bull_zh="折扣零售龙头已在门店基座上叠出经常性、高毛利的年金——Costco ~48-50 亿美元会员费(续费率屡创新高:美加 ~92.9%、全球 ~90.5%),沃尔玛全球广告(Walmart Connect 为核)~44-45 亿美元、同比增 25-30%。关键不在这笔年金当下的绝对占比,而在其 50-80% 的增量毛利让它对集团『利润增速』的边际贡献远超其收入体量,连续多季推动营业利润增速快于营收;叠加履约(WFS)、3P 市场与后 Cookie 时代难以复制的第一方交易数据护城河,这是一台把薄利零售商改造成轻资产复利机器的引擎。规模、闭环数据与消费走弱时的降级消费(逆周期份额获取)让这笔年金既耐久、又在持续变宽,配得上 35-50x。",
        bear_zh="重估(沃尔玛 ~35x、Costco ~50x vs 历史 ~18x/25x)已比基本面快跑 2-3 倍——广告/会员费至今仍占集团利润 <10%,即便以 25-30% 增长,对集团利润增速的边际拉动也不过 2-3 个百分点;内核仍是营业利润率 <4%、以生鲜/日百为主的零售生意,正面临关税推高 COGS、工资通胀与 Amazon/Aldi 价格战,集团盈利只有中到高个位数增长。35-50x 是一笔『年金要持续复利、核心还不能恶化』的双重完美下注,对应盈利收益率已低于 4%+ 的无风险利率。一旦宏观/利率正常化,或某一季利润率、续费率、广告增速令人失望而击穿『平台/年金溢价』叙事,整个板块面临的倍数压缩将远超其温和的 EPS 增长。",
        rationale_zh="这是两个旗舰种子的共同母题——沃尔玛的『广告飞轮 vs 利润率天花板』与 Costco 的『会员年金 vs 估值』都是同一分歧的实例:市场愿不愿意、以及能维持多久,给一个本质薄利、受关税/工资/Amazon 挤压的零售生意,一个建立在 <10% 利润占比的高毛利年金之上的平台级倍数。"),
    _T(
        theme="restaurants", key="away_from_home_share_grower_vs_capped",
        question_zh="美国\"在外就餐\"的真实客流 / 胃份额——是相对居家做饭的结构性长期赢家,还是被 GLP-1、累计菜单通胀与低收入天花板结构性封顶(即当前同店主要是从价格借来的)?",
        bull_zh="在外就餐占美国食品支出份额数十年持续上行、现约 55%,由劳动参与率、双职工与单人户增多、便利化、外卖 / 聚合平台渗透仍在爬升所驱动,每次衰退回撤后都创新高;餐饮内部 QSR / 快休闲是降级消费(trade-down)的赢家,拥有\"价值 + 忠诚度 + 单店扩张\"的运营商能穿越周期复利。GLP-1 的热量抑制是渐进的:用药人群偏高收入、作为客单本身是净客流正贡献,而菜单组合适配(高蛋白、小份量、饮品 / 零食化)可在热量下降时守住客单;过去多年的\"客流衰退\"高度集中在低收入客群与后疫情正常化,而非全人群的世俗性断层——历史上任何一轮减肥风潮都未真正压低快餐的胃份额。",
        bear_zh="2019 年以来快餐菜单累计涨价约 35–40%、跑赢(且在杂货已转平 / 通缩时仍在涨的)杂货通胀,\"在外吃\"相对\"在家做\"的价值主张已结构性反转,把价格敏感客群推回居家做饭——行业客流已连续多年持平至负增长,而客单仍在涨,即同店几乎全靠价格、而非需求。叠加的 GLP-1 是史上第一轮\"药理性且持续\"的减重浪潮(非靠意志力、可医保报销并向口服 / 雇主覆盖扩面),到 2027–28 年触及约 10–15%+ 美国成年人口、且恰好集中在高频快餐的价值客群,结构性削减在外热量摄入;再叠加被挤压的低收入消费者,行业真实客流被结构性封顶,当前同店是从价格借来的、质量正在恶化。",
        rationale_zh="餐饮所有子环节(QSR / 快休闲 / 休闲正餐)的多空最终都收敛到同一个最高阶问题:剔除涨价后的行业真实客流,到底是结构性增长还是被封顶。GLP-1 渗透、居家 vs 在外价差、低收入消费力构成这条主轴上共享的宏观砝码,决定了每家成员公司同店的\"质量\"(客流 × 客单的构成)与其估值溢价的可持续性——因此它是本主题所有旗舰共同继承的骨架争论。"),
)
