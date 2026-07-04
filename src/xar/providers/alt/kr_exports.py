"""半导体宏观出货/出口 (theme-scope signals) — 行业总需求的月频官方刻度.

本 provider 一个文件里跑两条 **theme 级** 信号(company_id=None,写归属链主题):

  1. ``alt.semi_billings``  —— SIA/WSTS 全球半导体月度销售额(**零 key**,已实核可用)。
     SIA 每月发一篇新闻稿,正文含确定性口径句:
       "global semiconductor sales were **$110.5 billion** during the month of
        **April 2026**, an increase of **11%** compared to the March 2026 total of
        $99.5 billion and **93.9%** more than the April 2025 total of $56.9 billion."
     用 **纯正则**(无 LLM)从新闻列表页(``/news-events/latest-news/``)取月度稿链接,
     逐篇解析出 {金额, 月份, 环比, 同比},写 ``ai_chip`` 主题行。WSTS 口径为
     **三个月移动平均**,``period_end`` = 该月月末。

  2. ``alt.kr_chip_exports`` —— 韩国海关半导体出口。关税厅 UNIPASS OpenAPI 与
     data.go.kr(관세청_품목별 수출입실적)**均需 serviceKey**;实测无 key 命中
     ``401 Unauthorized``,且不存在 item 级半导体出口的公开 keyless JSON/CSV。故走
     **可选环境变量** ``KR_DATA_API_KEY``(经 ``os.environ`` 读取,值永不打印)的路径:
       · 无 key  → 记一行诚实 skip 日志并 no-op(返回 skipped 统计,绝不抛出);
       · 有 key  → 打 data.go.kr Itemtrade(HS 8542 电子集成电路)最近数月月度出口,
                  纯正则/ElementTree 解析,写 ``ai_chip``/``ai_optical`` 两个主题行。
     spec cadence=monthly:20 日旬报为关税厅**新闻稿**口径(非本 API),此处以月度
     海关出口为经济期真值。

所有 HTTP 走 ``_fetch``:每主机 ``polite()`` 硬间隔(默认 2s)。逐篇/逐月失败只记
日志并跳过,绝不抛出。upsert 幂等(唯一键含 period_end),重叠窗口/重跑安全。
"""
from __future__ import annotations

import calendar
import html as _html
import os
import re
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET

import httpx

from ...config import get_settings
from ...ingestion.base import polite
from ...ontology.altdata import SIGNALS_BY_KEY
from ...storage.altstore import upsert_signal
from ..base import log

_BILLINGS_KEY = "alt.semi_billings"
_KR_KEY = "alt.kr_chip_exports"

# ── SIA/WSTS 全球月度销售额(零 key) ────────────────────────────────────────────
_SIA_LIST_URL = "https://www.semiconductors.org/news-events/latest-news/"
_SIA_HOST = "www.semiconductors.org"
_SIA_BASE = "https://www.semiconductors.org"
_DEFAULT_RELEASES = 12   # 无 limit 时最多处理的月度稿数(列表页只列近期)

# ── 韩国海关(可选 env key)────────────────────────────────────────────────────
# data.go.kr 관세청_품목별 국가별 수출입실적(getItemtradeList);serviceKey 必填。
_KR_ITEMTRADE_URL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
_KR_HOST = "apis.data.go.kr"
_KR_HS_SEMI = "8542"     # 전자집적회로 / integrated circuits(半导体核心线,含存储/逻辑)
_KR_KEY_ENVS = ("KR_DATA_API_KEY", "KR_CUSTOMS_API_KEY", "UNIPASS_API_KEY")
_DEFAULT_KR_MONTHS = 6

_MONTHS: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# "$110.5 billion during the month of April 2026"
_BILLION_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*billion\s+during\s+the\s+month\s+of\s+"
    r"([A-Za-z]+)\s+(\d{4})", re.IGNORECASE)
# "an increase of 11%" / "a decrease of 3.2%"
_MOM_RE = re.compile(r"\b(increase|decrease|rise|fall|drop)\s+of\s+([\d.]+)\s*%", re.IGNORECASE)
# "93.9% more than the April 2025 total of $56.9 billion"
_YOY_RE = re.compile(
    r"([\d.]+)\s*%\s+(more|less|higher|lower)\s+than\s+the\s+[A-Za-z]+\s+\d{4}\s+"
    r"total\s+of\s+\$\s*([\d,]+(?:\.\d+)?)\s*billion", re.IGNORECASE)
# 月度稿 permalink(slug 含 global-semiconductor-sales)
_SIA_LINK_RE = re.compile(
    r'href="(https://www\.semiconductors\.org/[a-z0-9\-]*global-semiconductor-sales[a-z0-9\-]*/?)"',
    re.IGNORECASE)


def available() -> bool:
    return True  # SIA 路径零 key;韩国路径优雅降级(无 key 即 skip)


# ── 纯函数(可离线测试) ────────────────────────────────────────────────────────
def _clean(fragment: str | None) -> str:
    if not fragment:
        return ""
    return _WS.sub(" ", _html.unescape(_TAG.sub(" ", fragment))).strip()


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _f(v: str) -> float:
    return float(v.replace(",", ""))


def billing_urls(listing_html: str) -> list[str]:
    """新闻列表页 → 去重的**月度**销售稿链接(排除季度稿:季度 slug 无月份名)。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _SIA_LINK_RE.finditer(listing_html or ""):
        url = m.group(1)
        slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
        if "-from-q" in slug or "quarter" in slug:
            continue  # 季度稿(如 ...-from-q4-2025-to-q1-2026)
        if not any(mn in slug for mn in _MONTHS):
            continue  # 只保留 slug 里带月份名的月度稿
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def parse_billing(text_or_html: str) -> dict | None:
    """SIA 月度稿正文 → {period_end, value_usd, billions, month, year, mom_pct, yoy_pct}。

    确定性正则,无 LLM。缺"during the month of $XX billion"锚点则返回 None(跳过,
    季度稿/无关页天然被排除)。``mom_pct`` 按 increase/decrease 带符号;``yoy_pct``
    按 more/less 带符号。
    """
    txt = _clean(text_or_html)
    m = _BILLION_RE.search(txt)
    if not m:
        return None
    month = _MONTHS.get(m.group(2).lower())
    if not month:
        return None
    year = int(m.group(3))
    billions = _f(m.group(1))
    tail = txt[m.end():]  # 环比/同比在金额锚点之后
    mom_pct = None
    mm = _MOM_RE.search(tail)
    if mm:
        sign = -1.0 if mm.group(1).lower() in ("decrease", "fall", "drop") else 1.0
        mom_pct = sign * float(mm.group(2))
    yoy_pct = None
    ym = _YOY_RE.search(tail)
    if ym:
        sign = -1.0 if ym.group(2).lower() in ("less", "lower") else 1.0
        yoy_pct = sign * float(ym.group(1))
    return {
        "period_end": _month_end(year, month),
        "value_usd": billions * 1e9,
        "billions": billions,
        "month": month,
        "year": year,
        "mom_pct": mom_pct,
        "yoy_pct": yoy_pct,
    }


# data.go.kr Itemtrade 字段别名(容错;第一个命中的非空值)
_KR_F_HS = ("hsCd", "hs_cd", "hsSgn")
_KR_F_STAT = ("statKor", "statCd", "statNm")
_KR_F_YEAR = ("year", "prtlYear")
_KR_F_EXP = ("expDlr", "expUsd", "exp")
_KR_F_IMP = ("impDlr", "impUsd", "imp")
_KR_F_BAL = ("balPayments", "balpayments")


def _pick(el: ET.Element, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        node = el.find(k)
        if node is not None and (node.text or "").strip():
            return node.text.strip()
    return None


def parse_itemtrade(text: str) -> list[dict]:
    """data.go.kr Itemtrade XML → [{hs, stat, year, exp_usd, imp_usd, balance}]。

    纯 ElementTree,无网络。坏 XML → 空列表(记日志,绝不抛出)。金额按 API 的
    ``expDlr`` 原值(관세청 口径,USD)存入,``unit_note`` 诚实标注。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        log.warning("kr_exports itemtrade XML parse failed: %s", e)
        return []
    out: list[dict] = []
    for it in root.iter("item"):
        exp = _pick(it, _KR_F_EXP)
        if exp is None:
            continue
        try:
            exp_usd = float(exp.replace(",", ""))
        except ValueError:
            continue
        imp = _pick(it, _KR_F_IMP)
        bal = _pick(it, _KR_F_BAL)
        out.append({
            "hs": _pick(it, _KR_F_HS),
            "stat": _pick(it, _KR_F_STAT),
            "year": _pick(it, _KR_F_YEAR),
            "exp_usd": exp_usd,
            "imp_usd": _num(imp),
            "balance": _num(bal),
        })
    return out


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def select_semi_export(rows: list[dict], hs: str = _KR_HS_SEMI) -> float | None:
    """从 Itemtrade 行里取半导体出口额(USD):优先"총계/합계"总计行,否则匹配 HS 前缀
    的最大出口行。全无 → None。"""
    totals = [r for r in rows if (r.get("stat") or "") and
              any(k in r["stat"] for k in ("총계", "합계", "총 계"))]
    if totals:
        return max(totals, key=lambda r: r["exp_usd"])["exp_usd"]
    hs_rows = [r for r in rows if (r.get("hs") or "").startswith(hs)]
    pool = hs_rows or rows
    return max(pool, key=lambda r: r["exp_usd"])["exp_usd"] if pool else None


# ── HTTP 面(单次、无重试放大;测试里被 monkeypatch) ────────────────────────────
def _fetch(url: str, host: str, params: dict | None = None) -> str | None:
    polite(host)  # settings.crawl_delay_seconds 每主机硬间隔(默认 2s)
    s = get_settings()
    try:
        r = httpx.get(url, params=params, headers={"User-Agent": s.http_user_agent},
                      timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001 — 永不打印 str(e)(可能含 serviceKey 查询参数)
        status = getattr(getattr(e, "response", None), "status_code", "")
        log.warning("kr_exports GET %s failed: %s %s", url, type(e).__name__, status)
        return None


# ── SIA billings 采集 ──────────────────────────────────────────────────────────
def pull_semi_billings(limit: int | None = None) -> dict:
    """SIA/WSTS 全球半导体月度销售额 → 写 ``alt.semi_billings``(theme-scope)。零 key。"""
    spec = SIGNALS_BY_KEY[_BILLINGS_KEY]
    cap = limit if limit is not None else _DEFAULT_RELEASES
    stats: dict = {"signal": spec.key, "source": spec.source, "listing_ok": False,
                   "candidates": 0, "fetched": 0, "parsed": 0, "written": 0,
                   "months": [], "themes": list(spec.themes)}
    listing = _fetch(_SIA_LIST_URL, _SIA_HOST)
    if not listing:
        return stats
    stats["listing_ok"] = True
    urls = billing_urls(listing)[:cap]
    stats["candidates"] = len(urls)
    seen_periods: set[date] = set()
    for url in urls:
        page = _fetch(url, _SIA_HOST)
        if not page:
            continue
        stats["fetched"] += 1
        try:
            row = parse_billing(page)
        except Exception as e:  # noqa: BLE001 — 单篇失败不沉没整篮
            log.warning("kr_exports: parse billing %s failed: %s", url, type(e).__name__)
            continue
        if not row:
            continue
        stats["parsed"] += 1
        pe = row["period_end"]
        if pe in seen_periods:
            continue
        seen_periods.add(pe)
        try:
            for theme in spec.themes:  # semi_billings.themes = ("ai_chip",)
                upsert_signal(
                    spec.key, period_end=pe, value=row["value_usd"],
                    company_id=None, theme=theme, unit=spec.unit, source=spec.source,
                    meta={"billions": row["billions"], "mom_pct": row["mom_pct"],
                          "yoy_pct": row["yoy_pct"], "basis": "3-month moving avg (WSTS)",
                          "url": url, "publisher": "SIA/WSTS",
                          "unit_note": "value in USD; source reports $ billions"})
            stats["written"] += 1
            stats["months"].append(str(pe))
        except Exception as e:  # noqa: BLE001
            log.warning("kr_exports: upsert billings %s failed: %s", pe, type(e).__name__)
    log.info("kr_exports semi_billings: %s", {k: stats[k] for k in
             ("listing_ok", "candidates", "written", "months")})
    return stats


# ── 韩国海关出口采集(可选 env key + 优雅降级)─────────────────────────────────
def _kr_key() -> str | None:
    """关税厅/data.go.kr serviceKey(经 os.environ,值永不打印)。未设置 → None。"""
    for env in _KR_KEY_ENVS:
        v = os.environ.get(env)
        if v:
            return v
    return None


def _recent_months(n: int, *, today: date | None = None) -> list[tuple[int, int]]:
    """今天往回数 n 个已完成月(海关有 1~2 月滞后),返回 [(year, month)] 由近到远。"""
    d = today or datetime.now(timezone.utc).date()
    y, m = d.year, d.month
    out: list[tuple[int, int]] = []
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        out.append((y, m))
    return out


def pull_kr_exports(limit: int | None = None) -> dict:
    """韩国海关半导体月度出口 → 写 ``alt.kr_chip_exports``(theme-scope)。

    需 ``KR_DATA_API_KEY``(관세청/data.go.kr serviceKey)。无 key → 诚实 skip(no-op),
    绝不抛出。有 key → 拉最近数月 HS 8542 出口,写 ``ai_chip``/``ai_optical`` 两主题行。
    """
    spec = SIGNALS_BY_KEY[_KR_KEY]
    stats: dict = {"signal": spec.key, "source": spec.source, "written": 0,
                   "months": [], "themes": list(spec.themes)}
    key = _kr_key()
    if not key:
        msg = ("KR_DATA_API_KEY unset — Korea customs (UNIPASS / data.go.kr 관세청) "
               "OpenAPI requires a serviceKey; no keyless item-level semiconductor "
               "export JSON/CSV exists — skipping")
        log.info("kr_exports kr_chip_exports: %s", msg)
        stats["skipped"] = msg
        return stats

    months = _recent_months(limit if limit is not None else _DEFAULT_KR_MONTHS)
    stats["queried_months"] = [f"{y}-{m:02d}" for y, m in months]
    for y, m in months:
        try:
            text = _fetch(_KR_ITEMTRADE_URL, _KR_HOST, params={
                "serviceKey": key, "strYear": str(y), "strMonth": f"{m:02d}",
                "hsSgn": _KR_HS_SEMI})
            if not text:
                continue
            exp_usd = select_semi_export(parse_itemtrade(text))
            if exp_usd is None or exp_usd <= 0:
                continue
            pe = _month_end(y, m)
            for theme in spec.themes:  # ("ai_chip", "ai_optical")
                upsert_signal(
                    spec.key, period_end=pe, value=float(exp_usd),
                    company_id=None, theme=theme, unit=spec.unit, source=spec.source,
                    meta={"hs": _KR_HS_SEMI, "publisher": "KR Customs (관세청) / data.go.kr",
                          "basis": "monthly customs export, HS 8542 integrated circuits",
                          "unit_note": "value = API expDlr (customs USD)"})
            stats["written"] += 1
            stats["months"].append(str(pe))
        except Exception as e:  # noqa: BLE001 — 单月失败不沉没整篮
            log.warning("kr_exports: kr month %s-%02d failed: %s", y, m, type(e).__name__)
    log.info("kr_exports kr_chip_exports: written=%d months=%s",
             stats["written"], stats["months"])
    return stats


# ── 公共入口 ───────────────────────────────────────────────────────────────────
def pull(limit: int | None = None) -> dict:
    """两条 theme 级半导体信号一并采集。``limit`` = 每条最多处理的月/稿数(冒烟用)。

    Returns 合并统计:{written, semi_billings{...}, kr_chip_exports{...}}。
    """
    billings = pull_semi_billings(limit)
    kr = pull_kr_exports(limit)
    stats = {"written": billings.get("written", 0) + kr.get("written", 0),
             "semi_billings": billings, "kr_chip_exports": kr}
    log.info("kr_exports: written=%d (billings=%d kr=%d)", stats["written"],
             billings.get("written", 0), kr.get("written", 0))
    return stats
