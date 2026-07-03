"""Ticketmaster Discovery API —— 现场活动票价（位置性/不可复制的"体验"腿，需 key，B_public_curated）。

拉取 Ticketmaster Discovery API v2 的 events 端点，按**月**聚合 focus 品类（默认 music/US）
的现场活动**一级市场票价**均值，作为"位置性真实性倒挂"比率的**现场/位置性腿**：
  price.positional_authenticity_inversion  ← Ticketmaster 现场活动 priceRanges 月度均价（value=均值，
                                              value_low/value_high 携带该月价格区间的 min/max）。

为什么写"现场活动均价"而非"比率本身"（口径声明 / 审讯纪律）：
  - 本指标在 registry 中最终是一个**比率**：positional / reproducible ——即"位置性/不可复制的
    现场体验"（一次性、不可再生产、稀缺座位）相对"可复制/可再生产商品"（可无限拷贝的数字商品
    或工业品）的相对价格，即所谓真实性倒挂（authenticity inversion）。
  - Ticketmaster 只提供其中的**现场/位置性腿**（现场活动的一级市场票价均值）。本连接器**只写
    位置性腿的原始均价**（value = 该月活动均价，单位 ratio 与 registry FK 对齐；value_low/value_high
    = 该月价格区间 min/max）。可复制品腿（如某数字商品/流媒体单价或工业品指数）由**别的连接器**
    提供；**派生层（派生 metric / derived asset）**再把两腿对齐相除，形成最终的倒挂比率。
    故此处单源不构成完整比率，仅是其分子（位置性）侧的输入真值。
  - valid_time = 当月月末（与 fhfa / epoch_ai 月度口径一致，便于跨源月度对齐）。

Ticketmaster Discovery API 口径与 caveat：
  - 端点：GET https://app.ticketmaster.com/discovery/v2/events.json，鉴权用 query 参数
    apikey=<TICKETMASTER_API_KEY>（从 env 读，缺 key 即跳过、不写库、不臆造）。
  - 过滤：classificationName=Music（品类）、countryCode=US（地区）、size=200（每页上限）、
    分页用 page 递增，按返回的 page.totalPages 收敛；含 startDateTime/endDateTime 时间窗以覆盖
    近月窗口。每个 event 的 priceRanges[].min/max 为**一级市场**价格区间（美元）。
  - 均值口径：先对单个 event 取其 priceRanges 的 (min+max)/2 作为该活动代表价（有多档取全部档
    的整体 min/max 与其中点）；再按活动**开演月份**（dates.start.localDate）归并，落该月所有活动
    代表价的算术均值为 value，落该月所有活动 min 的最小值与 max 的最大值为 value_low/value_high。
  - **口径 caveat（关键）**：Ticketmaster 只暴露**一级市场（primary）**价格区间，**不含二级市场/
    转售（secondary / resale）**成交价——二级溢价（真正的位置性稀缺定价）不可得，故本腿是一级
    票面价的下界代理，会**低估**位置性稀缺溢价。派生层解读比率时须记此偏差。
  - 覆盖偏差：并非所有 event 都带 priceRanges（部分免费/未定价/第三方售票活动缺该字段），无价活动
    自然跳过；Ticketmaster 主要覆盖美国主流场馆，长尾/独立演出覆盖不足。
  - **端点/字段形态存在版本差异**：若 Ticketmaster 改 JSON 结构或字段名，本连接器以"键名包含
    关键词"容错匹配（见 _pick），并打印告警、优雅降级为 []（绝不臆造数值）。

需 TICKETMASTER_API_KEY（免费注册 https://developer.ticketmaster.com/ 取 Consumer Key）。
缺 key 时打印清晰原因并返回 []（gated no-op，干净空跑），不报致命错误。

注：本连接器只写 positional_authenticity_inversion 的 **Ticketmaster 现场腿**（source_id=ticketmaster）；
可复制品腿由独立连接器写，比率由派生层合成。

    python -m ingestion.connectors.ticketmaster
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

_TARGET_METRIC = "price.positional_authenticity_inversion"

# focus 品类 / 地区（与项目"位置性体验 vs 可复制商品"叙事相关；用 Discovery API 的分类字段）。
# 仅为聚合范围声明，非数值；可按需扩展/替换。
_FOCUS_CLASSIFICATION = "Music"
_FOCUS_COUNTRY = "US"
_FOCUS_CURRENCY = "USD"        # 只纳入该币种 priceRanges，避免跨币种混合污染均值

# 时间窗：近多少个月的活动纳入统计（含未来在售场次；Discovery API 主要暴露未来/在售 event）。
# 给一个保守窗口，避免抓全量；月度归并后每月落一个观测点。
_WINDOW_MONTHS = 12

# 每页上限（Discovery API size 上限为 200）；分页安全阀。
_PAGE_SIZE = 200
_MAX_PAGES = 50               # Discovery API 深分页有 1000 条硬上限（size*page<=1000），故实际 ~5 页封顶


def _pick(d: dict, *keywords: str):
    """容错键匹配：返回 dict 中第一个键名包含全部关键词（小写）的值。

    Discovery API 返回字段（priceRanges / dates / _embedded 等）形态可能随版本微调，
    用包含匹配而非精确等值，降低改名导致的脆性（镜像 acled._pick / epoch_ai._col）。"""
    for k, v in d.items():
        low = str(k).lower()
        if all(kw.lower() in low for kw in keywords):
            return v
    return None


def _to_float(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_date(s) -> date | None:
    s = str(s or "").strip()
    if not s:
        return None
    # Discovery API dates.start.localDate 为 YYYY-MM-DD；dateTime 为 ISO8601（带 Z）。
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m"):
        try:
            return datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    return None


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    """给定日期 → 该月月末（valid_time 口径与 fhfa/epoch_ai 一致：月度点取月末）。"""
    nm = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return date.fromordinal(nm.toordinal() - 1)


def _add_months(d: date, n: int) -> date:
    """月份加减（用于构造时间窗下界，天数取 1 号，避免月末溢出）。"""
    idx = (d.year * 12 + (d.month - 1)) + n
    return date(idx // 12, idx % 12 + 1, 1)


class TicketmasterConnector(Connector):
    source_id = "ticketmaster"
    connector = "ingestion.connectors.ticketmaster"

    def __init__(self, api_key: str | None = None, classification: str | None = None,
                 country: str | None = None, currency: str | None = None,
                 window_months: int | None = None, session=None):
        # 所有参数皆有默认 / 从 env 读 → Connector() 可无参实例化；便于将来替换 focus 品类或注入 mock。
        self._api_key = (api_key or os.environ.get("TICKETMASTER_API_KEY", "")).strip()
        self._classification = classification or _FOCUS_CLASSIFICATION
        self._country = country or _FOCUS_COUNTRY
        self._currency = currency or _FOCUS_CURRENCY
        self._window_months = window_months or _WINDOW_MONTHS
        self._session = session

    def _client(self):
        import requests
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {"User-Agent": "Silicon-Index research qzjacob@gmail.com"}
            )
        return self._session

    def _get_json(self, params: dict):
        """带小重试的 GET；返回解析后的 JSON（dict）。失败抛出最后一次异常（镜像 acled）。"""
        sess = self._client()
        last = None
        for i in range(3):
            try:
                r = sess.get(_API_URL, params=params, timeout=45)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.8 * (i + 1))
        raise last

    def _fetch_events(self) -> list[dict]:
        """逐页拉取 focus 品类/地区在近 window_months 内的 event 原始行。

        Discovery API 分页：size + page（0-based），按返回 page.totalPages 收敛；
        深分页受 size*page<=1000 硬限。返回结构异常时打印告警并返回已得行（优雅降级）。"""
        today = datetime.now(timezone.utc).date()
        start_dt = _add_months(_month_start(today), -self._window_months)
        # Discovery API 时间参数为 ISO8601 UTC（带 Z）。
        start_iso = f"{start_dt.isoformat()}T00:00:00Z"
        end_iso = f"{_month_end(today).isoformat()}T23:59:59Z"

        out: list[dict] = []
        page = 0
        while True:
            params = {
                "apikey": self._api_key,
                "classificationName": self._classification,
                "countryCode": self._country,
                "startDateTime": start_iso,
                "endDateTime": end_iso,
                "size": _PAGE_SIZE,
                "page": page,
                "sort": "date,asc",
            }
            try:
                js = self._get_json(params)
            except Exception as e:  # noqa: BLE001
                print(f"[ticketmaster] 告警：第 {page} 页请求失败（{type(e).__name__}: {e}），停止分页。")
                break

            if not isinstance(js, dict):
                print("[ticketmaster] 告警：返回非 JSON 对象，停止（多为端点/鉴权异常）。")
                break
            # Discovery API 错误常带 fault / errors 字段；据此清晰报错并停。
            if _pick(js, "fault") or _pick(js, "errors"):
                print(f"[ticketmaster] 告警：API 报错——{_pick(js, 'fault') or _pick(js, 'errors')}"
                      "（多为 apikey 无效或额度耗尽）。")
                break

            embedded = _pick(js, "_embedded") or {}
            events = _pick(embedded, "events") if isinstance(embedded, dict) else None
            if not isinstance(events, list):
                # 无 _embedded.events：可能该窗口无活动或结构变动；容错取最大的 list 兜底。
                events = None
                if isinstance(embedded, dict):
                    events = next((v for v in embedded.values() if isinstance(v, list)), None)
            if not events:
                break

            out.extend(ev for ev in events if isinstance(ev, dict))

            # 收敛：读 page.totalPages；缺失则用返回条数是否满页近似判断末页。
            page_info = _pick(js, "page") or {}
            total_pages = _to_float(_pick(page_info, "totalPages")) if isinstance(page_info, dict) else None
            page += 1
            if total_pages is not None and page >= int(total_pages):
                break
            if len(events) < _PAGE_SIZE:  # 未满页 → 末页
                break
            if page >= _MAX_PAGES:        # 安全阀（Discovery API 深分页硬上限）
                print(f"[ticketmaster] 提示：分页达安全阀 {_MAX_PAGES} 页，停止（深分页受 1000 条硬限）。")
                break
        return out

    @staticmethod
    def _event_price(ev: dict, currency: str) -> tuple[float, float] | None:
        """从单个 event 的 priceRanges 抽取 (min, max)（美元一级市场票面价）。

        一个 event 可有多档 priceRanges（不同座位区）；取全部匹配币种档的整体 min 与 max。
        无 priceRanges / 无匹配币种 / 非正数 → 返回 None（该活动跳过，不臆造）。"""
        ranges = _pick(ev, "priceRanges") or _pick(ev, "pricerange")
        if not isinstance(ranges, list) or not ranges:
            return None
        lo_vals: list[float] = []
        hi_vals: list[float] = []
        for pr in ranges:
            if not isinstance(pr, dict):
                continue
            cur = str(_pick(pr, "currency") or "").strip().upper()
            if currency and cur and cur != currency.upper():
                continue  # 跨币种不混入均值
            lo = _to_float(_pick(pr, "min"))
            hi = _to_float(_pick(pr, "max"))
            if lo is not None and lo > 0:
                lo_vals.append(lo)
            if hi is not None and hi > 0:
                hi_vals.append(hi)
        if not lo_vals and not hi_vals:
            return None
        # 缺一端时用另一端兜底，确保 (min<=max) 且均为正。
        lo = min(lo_vals) if lo_vals else min(hi_vals)
        hi = max(hi_vals) if hi_vals else max(lo_vals)
        if lo <= 0 or hi <= 0:
            return None
        return (min(lo, hi), max(lo, hi))

    def fetch(self) -> list[dict]:
        # 缺 key → gated 空跑：打印清晰一行原因并返回 []（不写库、不臆造、不崩）。
        if not self._api_key:
            print(
                "[ticketmaster] 缺 TICKETMASTER_API_KEY——gated 空跑，返回 []。"
                "免费注册 https://developer.ticketmaster.com/ 取 Consumer Key，"
                "并把 TICKETMASTER_API_KEY 写入 .env。"
            )
            return []

        now = datetime.now(timezone.utc)

        try:
            events = self._fetch_events()
        except Exception as e:  # noqa: BLE001
            print(f"[ticketmaster] 提示：拉取 events 失败（{type(e).__name__}: {e}），本轮跳过、返回空。"
                  f" 端点：{_API_URL}")
            return []

        if not events:
            print("[ticketmaster] 提示：focus 窗口内无 event 返回（核对 apikey/品类/地区/时间窗），返回 []。")
            return []

        # (月末) → 该月各活动代表价（中点）列表 + 区间 min/max 汇总。
        mids_by_month: dict[date, list[float]] = defaultdict(list)
        lo_by_month: dict[date, float] = {}
        hi_by_month: dict[date, float] = {}
        n_priced = 0
        for ev in events:
            emb_dates = _pick(ev, "dates") or {}
            start = _pick(emb_dates, "start") if isinstance(emb_dates, dict) else None
            d = _parse_date(_pick(start, "localDate") if isinstance(start, dict) else None)
            if d is None and isinstance(start, dict):
                d = _parse_date(_pick(start, "dateTime"))
            if d is None:
                continue
            pr = self._event_price(ev, self._currency)
            if pr is None:
                continue
            lo, hi = pr
            me = _month_end(d)
            mids_by_month[me].append((lo + hi) / 2.0)   # 单活动代表价=区间中点
            lo_by_month[me] = min(lo_by_month.get(me, lo), lo)
            hi_by_month[me] = max(hi_by_month.get(me, hi), hi)
            n_priced += 1

        if not mids_by_month:
            print(f"[ticketmaster] 提示：{len(events)} 个 event 均无可用 priceRanges（"
                  f"多为未定价/免费/第三方售票，或币种不符 {self._currency}），返回 []。")
            return []

        rows: list[dict] = []
        for me in sorted(mids_by_month):
            mids = mids_by_month[me]
            avg = sum(mids) / len(mids)
            rows.append({
                "metric_key": _TARGET_METRIC,
                "source_id": "ticketmaster",
                "value": round(avg, 4),                     # 现场活动均价（位置性腿；派生层再除可复制品腿成比率）
                "value_low": round(lo_by_month[me], 4),     # 该月一级市场价格区间下界（min）
                "value_high": round(hi_by_month[me], 4),    # 该月一级市场价格区间上界（max）
                "unit": "ratio",                            # 与 registry FK 对齐（派生比率的位置性侧输入）
                "valid_time": me,                           # 月度点取月末
                "knowledge_time": now,                      # Discovery API 仅"当前在售"，无 vintage 快照
            })

        print(f"[ticketmaster] price.positional_authenticity_inversion（现场/位置性腿）："
              f"{len(rows)} 个月度均价点（{n_priced}/{len(events)} 活动含价），"
              f"{rows[0]['valid_time']} → {rows[-1]['valid_time']}。"
              "口径：仅一级市场票面价，不含二级转售溢价。")
        return rows


if __name__ == "__main__":
    run_id = TicketmasterConnector().run()
    print(f"✓ ticketmaster（positional_authenticity_inversion 之现场腿）已写入，ingest_run_id={run_id}")
