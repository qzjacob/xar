"""Wind EDB 宏观/行业指标时序 → alt_signals(数据追踪维度)。

复用 aifinmarket 的 MCP `economic_data.natural_language_get_edb_data`(自然语言 → EDB 序列)。
每条 wind_edb 信号(ontology.altdata,source='wind_edb')有一个**固定中文问题**(EDB_QUESTIONS,
code-as-truth)——NL 接口稳定性靠固定 question + 逐指标容错(单位/量级 sanity、日期单调、
空序列跳过,单指标失败不拖批)保证。落 alt_signals(theme 级,PIT:period_end=经济期、
observed_at=拉取时)→ sync_alt_events(|z|≥2→kg_events)→ thesis_signals 支柱校正,零下游改动。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ...logging import get_logger
from ...ontology.altdata import ALT_SIGNALS
from ...storage import kvstate
from ...storage.altstore import upsert_signal

log = get_logger("xar.alt.wind_edb")

# 固定中文查询串(真机首跑核对可检索性后定稿;绝不随运行变化)。
EDB_QUESTIONS: dict[str, str] = {
    "alt.edb_semi_sales": "全球半导体销售额 当月值",
    "alt.edb_ic_output": "中国 集成电路 产量 当月值",
    "alt.edb_optical_export": "中国 光电子器件 出口金额 当月值",
    "alt.edb_robot_output": "中国 工业机器人 产量 当月值",
    "alt.edb_catering": "社会消费品零售总额 餐饮收入 当月值",
    "alt.edb_retail_total": "社会消费品零售总额 当月同比",
    "alt.edb_online_retail": "实物商品网上零售额 累计同比",
}


def available() -> bool:
    # 与 aifinmarket provider 同口径(token 在即可用),而非 enable 旗标——EDB 复用同一 MCP。
    from ...providers import aifinmarket
    return aifinmarket.available()


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month // 12), (d.month % 12) + 1, 1)
    return nxt - timedelta(days=1)


def _to_period_end(s) -> date | None:
    """'2099-01-31' / '2099-01' / '202901' / '2099/01' → 月末 date。"""
    t = str(s or "").strip()
    if not t:
        return None
    m = re.match(r"(\d{4})[-/年]?(\d{1,2})[-/月]?(\d{1,2})?", t)
    if not m:
        return None
    y, mo, dd = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mo <= 12):
        return None
    if dd and dd.isdigit() and 1 <= int(dd) <= 31:
        try:
            return date(y, mo, int(dd))
        except ValueError:
            return _month_end(date(y, mo, 1))
    return _month_end(date(y, mo, 1))


def _num(v) -> float | None:
    if v in (None, "", "N/A", "--", "nan"):
        return None
    try:
        f = float(str(v).replace(",", "").replace("%", ""))
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _extract_rows(payload: dict) -> list[tuple[date, float]]:
    """row-dict 形状回退:{... : [{date,value}, ...]}。"""
    rowsets = None
    for key in ("data", "series", "items", "result", "records", "rows"):
        v = payload.get(key)
        if isinstance(v, list) and v:
            rowsets = v
            break
    if rowsets is None:
        return []
    out: list[tuple[date, float]] = []
    for r in rowsets:
        if not isinstance(r, dict):
            continue
        d = next((_to_period_end(r[k]) for k in ("date", "time", "period", "period_end", "dt",
                                                 "日期", "时间") if r.get(k)), None)
        val = next((_num(r[k]) for k in ("value", "val", "数值", "amount", "v") if k in r), None)
        if d is not None and val is not None:
            out.append((d, val))
    return out


def _extract_series(payload: dict | None) -> list[tuple[date, float]]:
    """从 Wind EDB NL 响应里挖 (period_end, value) 序列。

    真机形状(嵌套 + 列式):{"data":{"code":0,"data":[{"meta":…,"date":[yyyyMMdd…],
    "value":[…]}]}} —— date/value 是并行数组。也容忍 row-dict 形状(_extract_rows 回退)。"""
    if not isinstance(payload, dict):
        return []
    node: object = payload
    for _ in range(3):                       # 解开 Wind 的 data.data 嵌套
        if isinstance(node, dict) and isinstance(node.get("data"), (dict, list)):
            node = node["data"]
        else:
            break
    series_objs = node if isinstance(node, list) else ([node] if isinstance(node, dict) else [])
    out: list[tuple[date, float]] = []
    for so in series_objs:
        if not isinstance(so, dict):
            continue
        dates, vals = so.get("date"), so.get("value")
        if isinstance(dates, list) and isinstance(vals, list):   # 列式并行数组
            for d, v in zip(dates, vals):
                pe, nv = _to_period_end(d), _num(v)
                if pe is not None and nv is not None:
                    out.append((pe, nv))
            if out:
                break                        # 取第一条命中的序列(NL 最佳匹配)
    if not out:
        out = _extract_rows(payload)         # row-dict 回退(离线 fixture 用)
    out.sort(key=lambda x: x[0])
    return out


def _fetch(question: str, begin: str, end: str) -> dict | None:
    from ...providers.aifinmarket import _mcp_call
    return _mcp_call("economic_data", "natural_language_get_edb_data",
                     {"executionMode": "searchFetch", "question": question,
                      "beginDate": begin, "endDate": end})


def pull(limit: int | None = None) -> dict:
    if not available():
        return {"skipped": "aifinmarket disabled"}
    state = kvstate.get_state("wind_edb_state")
    specs = [s for s in ALT_SIGNALS if s.source == "wind_edb" and s.key in EDB_QUESTIONS]
    if limit:
        specs = specs[:limit]
    out = {"indicators": 0, "points": 0, "skipped": []}
    # Wind EDB NL 接口要求日期为 yyyyMMdd(真机核实:'yyyy-MM-dd' 报 code 1002)。
    end = date.today().strftime("%Y%m%d")
    begin = (date.today() - timedelta(days=400)).strftime("%Y%m%d")
    for spec in specs:
        theme = spec.themes[0] if spec.themes else None
        try:
            payload = _fetch(EDB_QUESTIONS[spec.key], begin, end)
            series = _extract_series(payload)
        except Exception as e:  # noqa: BLE001 —— 单指标失败不拖整批
            log.warning("wind_edb %s failed: %s", spec.key, e)
            out["skipped"].append(spec.key)
            continue
        if not series:
            out["skipped"].append(spec.key)
            continue
        # 增量:只写水位线之后的新点,避免每轮重写整段历史把 observed_at 刷成今天(评审 #13,破坏 PIT)。
        last = state.get(spec.key)
        last_pe = date.fromisoformat(last) if last else None
        wrote = 0
        for pe, val in series:
            if last_pe and pe <= last_pe:
                continue
            upsert_signal(spec.key, period_end=pe, value=val, theme=theme,
                          unit=spec.unit, source="wind_edb",
                          meta={"question": EDB_QUESTIONS[spec.key]})
            wrote += 1
        if series:
            state[spec.key] = series[-1][0].isoformat()   # 推水位线(即使本轮无新点)
        if wrote:
            out["indicators"] += 1
            out["points"] += wrote
    kvstate.save_state("wind_edb_state", state)
    log.info("wind_edb: %s", out)
    return out
