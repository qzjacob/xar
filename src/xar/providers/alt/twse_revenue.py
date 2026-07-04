"""台股月营收 (signal ``alt.tw_monthly_revenue``) — 公司级需求真值.

台湾上市/上柜公司依法每月披露营业收入(MOPS 表 t187ap05),是芯片/光模块产业链
上硬度最高、且完全免费的另类信号——比季报快约两个月。对每个带 ``tw_code`` 的公司
绑定写一条月频信号:

  * ``value``      = 当月营收,以 **原始 TWD** 存储(官方口径为 **仟元/千元**,×1000);
  * ``period_end`` = ``资料年月`` 的月末日(ROC 民国年+月 → 西元);
  * ``unit``       = TWD;
  * ``meta``       = {yoy_pct, mom_pct, ytd_twd, ytd_yoy_pct, data_ym, report_date,
                      market, tw_code, name, unit_note}。

数据源(公开 OpenAPI,无 key,UTF-8 JSON,返回**最新一个月快照**——月度累计口径,
天然幂等):

  * 上市 TWSE : ``https://openapi.twse.com.tw/v1/opendata/t187ap05_L``   (已实核可用)
  * 上柜 TPEx : ``https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O`` (同一 MOPS
    表结构;部分网络环境经 Cloudflare 边缘被挡 → 记日志并跳过,绝不抛出)。

字段名两源可能不同(上市为中文键;上柜历史上偶见英文/罗马拼音键),``parse_record``
用别名元组做容错映射。逐条失败只记日志并跳过。历史回填:开放 API 只给最新月快照,
MOPS 批量历史需逐月抓取表单(非本 provider 轻量范围),故此处诚实**不做回填**,靠
每月常驻拉取自然累计出序列。
"""
from __future__ import annotations

import calendar
from datetime import date

import httpx

from ...config import get_settings
from ...ingestion.base import polite
from ...ontology.altdata import SIGNALS_BY_KEY, bindings
from ...storage.altstore import upsert_signal
from ..base import log

_KEY = "alt.tw_monthly_revenue"
_THOUSANDS = 1000  # 官方营收口径为仟元;×1000 存原始 TWD

# (market, url, host) —— 上市已实核;上柜同表结构,失败即跳过。
_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("TWSE", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "openapi.twse.com.tw"),
    ("TPEx", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", "www.tpex.org.tw"),
)

# 字段别名(第一个命中的非空值)——两源键名差异容错。
_F_CODE = ("公司代號", "SecuritiesCompanyCode", "Code", "CompanyCode")
_F_NAME = ("公司名稱", "CompanyName", "Name")
_F_YM = ("資料年月", "DataYearMonth", "Datayearmonth")
_F_REV = ("營業收入-當月營收", "Operatingincome", "CurrentMonthOperatingIncome")
_F_YOY = ("營業收入-去年同月增減(%)", "YoY", "OperatingIncomeYoY")
_F_MOM = ("營業收入-上月比較增減(%)", "MoM")
_F_YTD = ("累計營業收入-當月累計營收", "CumulativeOperatingIncome")
_F_YTD_YOY = ("累計營業收入-前期比較增減(%)", "CumulativeYoY")
_F_REPORT_DATE = ("出表日期", "ReportDate")


def available() -> bool:
    return True  # 公开 API,无 key


# ── 纯函数(可离线测试) ────────────────────────────────────────────────────────
def _field(rec: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", "-"):
            return v
    return None


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _roc_period_end(ym) -> date | None:
    """``资料年月`` (民国年+月,如 ``11505`` = ROC115/2026 年 05 月) → 该月月末日。"""
    if ym is None:
        return None
    s = str(ym).strip()
    if not s.isdigit() or len(s) < 4:
        return None
    month = int(s[-2:])
    roc_year = int(s[:-2])
    if not (1 <= month <= 12) or roc_year < 1:
        return None
    year = roc_year + 1911
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_record(rec: dict) -> dict | None:
    """一条 MOPS t187ap05 记录 → 归一化行,缺关键字段则返回 None(跳过)。"""
    if not isinstance(rec, dict):
        return None
    code = _field(rec, _F_CODE)
    ym = _field(rec, _F_YM)
    rev = _num(_field(rec, _F_REV))
    period_end = _roc_period_end(ym)
    if not code or period_end is None or rev is None:
        return None
    ytd = _num(_field(rec, _F_YTD))
    return {
        "code": str(code).strip(),
        "name": (_field(rec, _F_NAME) or "").strip(),
        "period_end": period_end,
        "value_twd": rev * _THOUSANDS,
        "yoy_pct": _num(_field(rec, _F_YOY)),
        "mom_pct": _num(_field(rec, _F_MOM)),
        "ytd_twd": ytd * _THOUSANDS if ytd is not None else None,
        "ytd_yoy_pct": _num(_field(rec, _F_YTD_YOY)),
        "data_ym": str(ym).strip(),
        "report_date": _field(rec, _F_REPORT_DATE),
    }


# ── HTTP 面(单次、无重试放大;测试里被 monkeypatch) ────────────────────────────
def _fetch(url: str, host: str) -> list | None:
    polite(host)  # settings.crawl_delay_seconds 每主机硬间隔(默认 2s)
    s = get_settings()
    try:
        r = httpx.get(url, headers={"User-Agent": s.http_user_agent,
                                    "Accept": "application/json"},
                      timeout=30, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception as e:  # noqa: BLE001 — 永不打印 str(e)
        status = getattr(getattr(e, "response", None), "status_code", "")
        log.warning("twse_revenue GET %s failed: %s %s", url, type(e).__name__, status)
        return None


def _code_map() -> dict[str, str]:
    """tw_code → company_id(全宇宙绑定中带台股码的公司)。"""
    return {b.tw_code: cid for cid, b in bindings().items() if b.tw_code}


def _ingest(records: list, code_map: dict[str, str], market: str,
            spec, stats: dict, remaining: int | None) -> int:
    """写匹配公司的信号。返回本源实际写入行数;``remaining`` 为剩余写入配额。"""
    written = 0
    for rec in records or []:
        if remaining is not None and written >= remaining:
            break
        try:
            row = parse_record(rec)
            if not row:
                continue
            stats["parsed"] += 1
            cid = code_map.get(row["code"])
            if not cid:
                continue
            stats["matched"] += 1
            upsert_signal(
                spec.key,
                period_end=row["period_end"],
                value=row["value_twd"],
                company_id=cid,
                unit=spec.unit,
                source=spec.source,
                meta={
                    "yoy_pct": row["yoy_pct"],
                    "mom_pct": row["mom_pct"],
                    "ytd_twd": row["ytd_twd"],
                    "ytd_yoy_pct": row["ytd_yoy_pct"],
                    "data_ym": row["data_ym"],
                    "report_date": row["report_date"],
                    "market": market,
                    "tw_code": row["code"],
                    "name": row["name"],
                    "unit_note": "value in raw TWD; source reports NT$ thousands (仟元)",
                },
            )
            written += 1
            stats["written"] += 1
            stats["companies"].add(cid)
        except Exception as e:  # noqa: BLE001 — 单条失败不沉没整篮
            code = rec.get("公司代號") if isinstance(rec, dict) else "?"
            log.warning("twse_revenue: skip record %s: %s", code, type(e).__name__)
    return written


# ── 公共入口 ───────────────────────────────────────────────────────────────────
def pull(limit: int | None = None) -> dict:
    """拉取 TWSE(+TPEx)月营收快照,写 ``alt.tw_monthly_revenue``(company-scope)。

    ``limit`` = 跨两源累计写入行数上限(冒烟用);None = 全量。返回统计。
    """
    spec = SIGNALS_BY_KEY[_KEY]
    code_map = _code_map()
    stats: dict = {
        "bound_companies": len(code_map),
        "parsed": 0, "matched": 0, "written": 0,
        "companies": set(),
        "sources_ok": [], "sources_failed": [],
    }
    for market, url, host in _SOURCES:
        if limit is not None and stats["written"] >= limit:
            break
        data = _fetch(url, host)
        if data is None:
            stats["sources_failed"].append(market)
            continue
        stats["sources_ok"].append(market)
        remaining = None if limit is None else limit - stats["written"]
        _ingest(data, code_map, market, spec, stats, remaining)

    stats["companies"] = sorted(stats["companies"])  # set → 稳定可序列化
    stats["companies_matched"] = len(stats["companies"])
    log.info("twse_revenue: bound=%d matched=%d written=%d ok=%s failed=%s",
             stats["bound_companies"], stats["companies_matched"], stats["written"],
             stats["sources_ok"], stats["sources_failed"])
    return stats
