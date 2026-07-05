"""富途板块(板块/产业链)→ 本体主题/技术路线映射。

富途板块名本就是中文(人工智能/芯片股/半导体/光模块/机器人概念股…),与本体的中文
路由表同域,故**直接复用 cn_routing**——无需另建映射表,也天然继承其代码即真相守卫
(命中的 key 必 ∈ registry.THEMES / tr_*)。部分板块(如"白酒""换电概念")落在本
项目 8 主题之外 → 空命中,合理(那些公司本不在本体范围)。

用途:给已在册公司标注富途行业归属(futu_plates 表),并作为**本体缺口发现器**——
富途板块暗示某公司属于某主题,但该公司当前未被策展为此主题 → 值得人工复核。
"""
from __future__ import annotations

from . import cn_routing


def plate_themes(plate_names: list[str]) -> list[str]:
    """板块名列表 → 去重的本体主题 id(经 cn_routing 中文关键词匹配)。"""
    out: list[str] = []
    for nm in plate_names:
        out += cn_routing.theme_hits(nm)
    return list(dict.fromkeys(out))


def plate_routes(plate_names: list[str]) -> list[str]:
    """板块名列表 → 去重的技术路线 id(tr_*)。"""
    out: list[str] = []
    for nm in plate_names:
        out += cn_routing.route_hits(nm)
    return list(dict.fromkeys(out))


def name_themes(plate_name: str) -> list[str]:
    """单个板块名 → 主题 id(存 futu_plates.themes 列)。"""
    return cn_routing.theme_hits(plate_name)
