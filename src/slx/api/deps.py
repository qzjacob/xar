"""共享依赖与水印逻辑 —— 服务层的纪律命门。

两件事在此集中，谁也别想绕开：
  1) get_conn(): FastAPI 依赖，按请求开/关一个 psycopg 连接（用 slx.db.connect）。
  2) identification(): 把 metric_registry.hardness/identification_strategy 翻成对外的
     identification_status + caveat 水印。soft 永远是 'unidentified'——横截面相关不是因果，
     在拿到识别（DID/面板）前看板必须打"未识别"水印，本函数是唯一真值来源。
"""
from __future__ import annotations

from typing import Iterator

from slx.db import connect

# hardness → 对外识别状态。语义见 schema.sql 的 hardness_enum 注释（物理/会计→逻辑→待识别→承重墙）。
#   hard   : 物理/会计事实，无需因果识别即可读 → identified
#   medium : 逻辑推论，可由 value/slope 判定，但仍非随机化识别 → partially_identified
#   soft   : 待识别假说，横截面相关；未接 DID/面板前一律 unidentified（绝不当因果暴露）
#   wall   : 承重墙不可量化项，value 永远 NULL → not_quantified
_STATUS_BY_HARDNESS = {
    "hard": "identified",
    "medium": "partially_identified",
    "soft": "unidentified",
    "wall": "not_quantified",
}

# 各识别状态的对外水印措辞（中文，审讯口吻；前端可直接渲染为提示条）。
_WATERMARK = {
    "identified": "物理/会计事实，可直接读取；口径限度见 caveat。",
    "partially_identified": "逻辑推论，可由 value/slope 观测，但非随机化识别；勿当因果终局。",
    "unidentified": "未识别：横截面/时序相关，未接 DID/面板，严禁当作因果回报或确定结论。",
    "not_quantified": "承重墙不可量化项：value 恒为 NULL，仅作定性边界，不提供数值断言。",
}


def get_conn() -> Iterator:
    """FastAPI 依赖：每请求一个连接，结束即关闭（只读用途，不在路由里 commit 业务数据）。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def identification(hardness: str | None, identification_strategy: str | None,
                   caveat: str | None) -> dict:
    """把硬度/识别策略翻成对外水印块。soft → unidentified（铁律，不看 strategy 是否填写）。

    返回结构稳定，前端可无脑渲染：
      {
        "hardness": "soft",
        "identification_status": "unidentified",
        "identification_strategy": "...或 null（soft 注册表里通常已声明，但状态仍是未识别）",
        "is_causal_claim": false,           # 永远 false——本服务不输出因果断言
        "caveat": "<注册表 caveat 原文>",
        "watermark": "<对应状态的标准措辞>"
      }
    """
    status = _STATUS_BY_HARDNESS.get(hardness or "", "unidentified")
    return {
        "hardness": hardness,
        "identification_status": status,
        "identification_strategy": identification_strategy,
        # 服务层从不把任何读数暴露为因果结论——即便 hard，也只是"事实"而非"因果回报"。
        "is_causal_claim": False,
        "caveat": caveat,
        "watermark": _WATERMARK.get(status, _WATERMARK["unidentified"]),
    }
