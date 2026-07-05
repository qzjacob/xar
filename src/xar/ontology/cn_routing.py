"""中文关键词 → theme / tech-route 路由表(代码即真相)。

现有的所有关键词表(agents/nodes._THEME_TERMS、providers/twitter.DOMAIN_TERMS、
polymarket._THEME_KEYWORDS、registry._TECH_ROUTE_HINTS)都是英文/ASCII,只有公司
**别名**是中文。中文微信文章若不含英文技术词又没点名公司,就无法路由到主题/路线。
本表补齐这一层:8 主题 + 33 tr_* 的中文关键词,供微信 triage 的零 LLM 预筛使用。

代码即真相守卫:tests/test_cn_routing.py 断言每个 key ∈ registry.THEMES / {tr ids}
(镜像 ROUTE_THEMES 的合法性不变式),防止本表与 ontology 漂移。
"""
from __future__ import annotations

# ── 8 主题的中文关键词 ─────────────────────────────────────────────────────────
CN_THEME_TERMS: dict[str, tuple[str, ...]] = {
    "ai_optical": ("光模块", "光通信", "光器件", "光芯片", "硅光", "数通光模块",
                   "相干光", "光引擎", "光收发", "CPO", "LPO"),
    "ai_chip": ("算力芯片", "AI芯片", "GPU", "加速卡", "先进封装", "先进制程",
                "晶圆", "光刻", "存储芯片", "半导体", "高带宽内存", "HBM",
                "芯粒", "封测", "晶圆代工"),
    "ai_software": ("大模型", "生成式", "AI软件", "智能体", "云计算", "应用软件",
                    "SaaS", "推理", "训练", "算力租赁", "AIGC", "多模态"),
    "space_exploration": ("商业航天", "卫星", "火箭", "运载", "星座", "星链",
                          "太空", "低轨", "发射", "卫星互联网", "在轨"),
    "humanoid_robotics": ("人形机器人", "机器人", "减速器", "丝杠", "灵巧手",
                          "伺服", "具身智能", "执行器", "关节模组", "力矩电机",
                          "触觉传感"),
    "internet": ("互联网", "电商", "广告", "社交", "游戏", "平台经济",
                 "本地生活", "GMV", "直播电商", "短视频", "在线", "流量"),
    "retail": ("零售", "消费", "门店", "同店", "客流", "折扣", "商超",
               "品牌", "连锁零售", "会员店", "线下"),
    "restaurants": ("餐饮", "餐厅", "连锁餐饮", "快餐", "客单价", "翻台",
                    "同店销售", "门店扩张", "外卖", "预制菜", "现制"),
}

# ── 33 tech-route 的中文关键词(多数中文锚点已在 TECH_ROUTES.attrs.family)────────
CN_ROUTE_TERMS: dict[str, tuple[str, ...]] = {
    "tr_800g": ("800G", "800G光模块"),
    "tr_1600g": ("1.6T", "1.6T光模块", "1600G"),
    "tr_cpo": ("CPO", "共封装光学", "光电共封装"),
    "tr_lpo": ("LPO", "线性直驱", "线性驱动"),
    "tr_siph": ("硅光", "硅光子", "硅基光电子"),
    "tr_eml": ("EML", "激光器芯片", "EML激光器", "电吸收调制"),
    "tr_euv": ("EUV", "极紫外", "极紫外光刻"),
    "tr_2nm": ("2nm", "2纳米", "GAA", "环栅"),
    "tr_cowos": ("CoWoS", "2.5D封装", "台积电封装"),
    "tr_hbm": ("HBM", "高带宽内存", "高带宽存储"),
    "tr_chiplet": ("Chiplet", "芯粒", "UCIe", "小芯片"),
    "tr_ai_agents": ("AI智能体", "自主智能体", "智能体", "Agent", "自主代理"),
    "tr_copilots": ("Copilot", "副驾驶", "编程助手", "代码助手"),
    "tr_rag": ("RAG", "检索增强", "企业搜索", "知识库问答"),
    "tr_genai_infra": ("大模型基础设施", "LLMOps", "算力平台", "模型服务",
                       "推理框架"),
    "tr_reusable": ("可复用运载", "可回收火箭", "复用火箭", "回收火箭"),
    "tr_methalox": ("甲烷推进", "液氧甲烷", "甲烷发动机"),
    "tr_megaconstellation": ("巨型星座", "星链", "卫星星座", "低轨星座"),
    "tr_orbital_compute": ("在轨算力", "太空计算", "在轨计算"),
    "tr_electric_prop": ("电推进", "电推力器", "霍尔推进"),
    "tr_harmonic": ("谐波减速", "谐波减速器"),
    "tr_roller_screw": ("行星滚柱丝杠", "滚柱丝杠", "丝杠"),
    "tr_frameless": ("无框力矩电机", "无框电机", "力矩电机"),
    "tr_vla": ("具身大模型", "具身智能", "VLA", "视觉语言动作"),
    "tr_tactile": ("触觉传感", "触觉传感器", "电子皮肤"),
    "tr_cybersec": ("网络安全", "信息安全", "安全软件", "零信任"),
    "tr_ddic": ("显示驱动芯片", "DDIC", "驱动IC", "显示驱动"),
    "tr_power_semi": ("功率半导体", "功率器件", "IGBT", "碳化硅", "SiC", "第三代半导体"),
    "tr_cv": ("计算机视觉", "机器视觉", "视觉AI"),
    "tr_med_imaging": ("医学影像AI", "医疗影像", "AI影像", "影像辅助诊断"),
    "tr_pneumatic": ("气动执行器", "气动"),
    "tr_industrial_gas": ("电子特气", "电子特种气体", "工业气体", "特种气体"),
    "tr_ceramic_pkg": ("陶瓷封装", "陶瓷基板", "陶瓷封装基板"),
}


def _hits(text: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    """text 中命中任一关键词的 key(去重,保持 table 声明序)。大小写不敏感。"""
    low = (text or "").lower()
    out: list[str] = []
    for key, terms in table.items():
        if any(t.lower() in low for t in terms):
            out.append(key)
    return out


def theme_hits(text: str) -> list[str]:
    """返回 text 命中的 theme id 列表(∈ registry.THEMES)。"""
    return _hits(text, CN_THEME_TERMS)


def route_hits(text: str) -> list[str]:
    """返回 text 命中的 tech-route id 列表(tr_*)。"""
    return _hits(text, CN_ROUTE_TERMS)


def route_themes(route_ids: list[str]) -> list[str]:
    """路线命中 → 其归属主题(经 registry.ROUTE_THEMES),用于主题回填。"""
    from ..ingestion.registry import ROUTE_THEMES

    themes: list[str] = []
    for r in route_ids:
        for t in ROUTE_THEMES.get(r, ()):
            if t not in themes:
                themes.append(t)
    return themes
