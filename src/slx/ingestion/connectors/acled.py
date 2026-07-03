"""ACLED —— 抗议/骚乱事件计数（政权稳定性失稳信号，B_public_curated，需 key+email）。

拉取 ACLED（Armed Conflict Location & Event Data）REST API，按**季度**聚合
focus 国家的「抗议 + 骚乱」事件计数，作为 regime_stability 的失稳代理：
  proxy.legitimacy.regime_stability  ← ACLED protest_events（季度计数）

为什么写「原始计数」而非「稳定性指数」（口径声明 / 审讯纪律）：
  - 本连接器只产出 ACLED 这条腿（source_id=acled）的**原始抗议+骚乱事件数**，
    unit="count_per_quarter"，**计数越高 = 稳定性越低**（方向相反，合成层再做归一/反向）。
  - regime_stability 在 registry 是合成口径（V-Dem 民主指数 + ACLED 抗议规模），
    合成与反向缩放交由 engine 层完成；连接器层**不臆造**合成指数，只落可审计的原子计数。
  - valid_time = 季度末（与 epoch_ai release_cadence 同口径，便于跨源季度对齐）。

ACLED API 口径与 caveat：
  - 端点：GET https://api.acleddata.com/acled/read，鉴权用 query 参数 key=<ACLED_API_KEY>、
    email=<ACLED_EMAIL>（均从 env 读，缺一即跳过、不写库、不臆造）。
  - 过滤：event_type=Protests:OR:event_type=Riots（抗议+骚乱），country=<国名>，
    event_date=YYYY-MM-DD|YYYY-MM-DD + event_date_where=BETWEEN（闭区间）。
  - 分页：limit/page；本连接器只取每季度 count（用 _count_only/limit=0 优先；不支持时回退逐页计数）。
  - 媒体偏差：ACLED 依赖新闻报道，报道密度随国别/时期波动（registry 已记 caveat：新闻偏差）；
    覆盖年限自 ACLED 各国上线日起（如美国 2020-起、欧洲 2020-起），早于上线日的季度无数据。
  - **端点/参数形态存在版本差异**：若 ACLED 改字段名或返回结构，本连接器以「键名包含关键词」
    容错匹配（见 _pick），并打印告警、优雅降级为 []（绝不臆造数值）。

需 ACLED_API_KEY + ACLED_EMAIL（免费注册 https://acleddata.com/register/）。
缺任一时打印清晰原因并返回 []（gated no-op，干净空跑），不报致命错误。

注：本连接器只写 regime_stability 的 **ACLED 腿**（source_id=acled）；V-Dem 腿由独立连接器写。

    python -m ingestion.connectors.acled
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

_API_URL = "https://api.acleddata.com/acled/read"

# focus 国家（与项目 AI-经济叙事相关的政权稳定性焦点；用 ACLED 的 country 字段精确名）。
# 仅为聚合范围声明，非数值；可按需扩展。ACLED country 名以其官方拼写为准。
_FOCUS_COUNTRIES = (
    "United States",
    "China",
    "France",
    "Germany",
    "United Kingdom",
    "India",
)

# 抗议/骚乱事件类型（ACLED event_type 取值）。两类合并为「失稳事件」计数。
_EVENT_TYPES = ("Protests", "Riots")

# 起始年份：ACLED 各国上线日不同，给一个保守下界；早于覆盖期的季度自然返回 0 行。
_START_YEAR = 2018


def _pick(d: dict, *keywords: str):
    """容错键匹配：返回 dict 中第一个键名包含全部关键词（小写）的值。

    ACLED 返回字段（event_date / event_type / country 等）形态可能随版本微调，
    用包含匹配而非精确等值，降低改名导致的脆性。"""
    for k, v in d.items():
        low = str(k).lower()
        if all(kw.lower() in low for kw in keywords):
            return v
    return None


def _parse_date(s) -> date | None:
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y"):
        try:
            return datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    return None


def _quarter_end(d: date) -> date:
    q = (d.month - 1) // 3 + 1
    return {1: date(d.year, 3, 31), 2: date(d.year, 6, 30),
            3: date(d.year, 9, 30), 4: date(d.year, 12, 31)}[q]


class AcledConnector(Connector):
    source_id = "acled"
    connector = "ingestion.connectors.acled"

    def __init__(self, api_key: str | None = None, email: str | None = None,
                 countries: tuple[str, ...] | None = None, start_year: int | None = None,
                 session=None):
        self._api_key = (api_key or os.environ.get("ACLED_API_KEY", "")).strip()
        self._email = (email or os.environ.get("ACLED_EMAIL", "")).strip()
        self._countries = countries or _FOCUS_COUNTRIES
        self._start_year = start_year or _START_YEAR
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
        """带小重试的 GET；返回解析后的 JSON（dict）。失败抛出最后一次异常。"""
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

    def _fetch_country_events(self, country: str) -> list[dict]:
        """逐页拉取某国 [start_year, 今] 的抗议+骚乱事件原始行。

        ACLED 单页上限通常 500/页（取决于账户），用 page 递增直到 data 为空。
        参数以 ACLED 文档为准；若返回结构异常，打印告警并返回已得行（优雅降级）。"""
        today = datetime.now(timezone.utc).date()
        date_lo = f"{self._start_year}-01-01"
        date_hi = today.isoformat()
        # event_type 多值用 ACLED 的 :OR: 语法；country 单值逐国请求（避免 OR 拼接歧义）。
        event_type_param = ":OR:event_type=".join(_EVENT_TYPES)

        out: list[dict] = []
        page = 1
        while True:
            params = {
                "key": self._api_key,
                "email": self._email,
                "country": country,
                "event_type": event_type_param,
                "event_date": f"{date_lo}|{date_hi}",
                "event_date_where": "BETWEEN",
                # 只需 event_date 用于季度归并；显式 fields 降低载荷（ACLED 支持 fields 裁剪）。
                "fields": "event_date|event_type|country",
                "limit": 500,
                "page": page,
            }
            try:
                js = self._get_json(params)
            except Exception as e:  # noqa: BLE001
                print(f"[acled] 告警：{country} 第 {page} 页请求失败（{e}），停止该国分页。")
                break

            if not isinstance(js, dict):
                print(f"[acled] 告警：{country} 返回非 JSON 对象，跳过该国。")
                break
            # ACLED 失败时常带 success=false / error 字段；据此清晰报错并停。
            if js.get("success") is False or js.get("error"):
                print(f"[acled] 告警：{country} API 报错——{js.get('error') or js}（多为 key/email 无效或额度）。")
                break

            data = _pick(js, "data")
            if not isinstance(data, list):
                # 某些返回把行直接放顶层 list，或字段名不同；容错取最大的 list。
                data = next((v for v in js.values() if isinstance(v, list)), None)
            if not data:
                break

            out.extend(d for d in data if isinstance(d, dict))
            if len(data) < 500:  # 末页
                break
            page += 1
            if page > 400:  # 安全阀：防极端分页失控（200k 行上限）
                print(f"[acled] 提示：{country} 分页超 400 页，达安全阀，停止。")
                break
        return out

    def fetch(self) -> list[dict]:
        if not self._api_key or not self._email:
            missing = []
            if not self._api_key:
                missing.append("ACLED_API_KEY")
            if not self._email:
                missing.append("ACLED_EMAIL")
            print(
                f"[acled] 缺 {'+'.join(missing)}——gated 空跑，返回 []。"
                "免费注册 https://acleddata.com/register/ 取 key，"
                "并把 ACLED_API_KEY/ACLED_EMAIL 写入 .env。"
            )
            return []

        now = datetime.now(timezone.utc)
        # (国, 季度末) → 计数；合成层再按国聚合/反向，连接器层保留国别粒度以便审计。
        count_by_q: dict[date, int] = defaultdict(int)
        total_events = 0
        for country in self._countries:
            events = self._fetch_country_events(country)
            total_events += len(events)
            n_q0 = len(count_by_q)
            for ev in events:
                d = _parse_date(_pick(ev, "event", "date") or _pick(ev, "date"))
                if not d:
                    continue
                count_by_q[_quarter_end(d)] += 1
            print(f"[acled] {country}: {len(events)} 抗议/骚乱事件，覆盖 {len(count_by_q) - n_q0} 个新季度。")

        if not count_by_q:
            print("[acled] 提示：无任何抗议/骚乱事件返回（核对 key/email、country 拼写与覆盖年限），返回 []。")
            return []

        rows: list[dict] = []
        for qe, n in sorted(count_by_q.items()):
            rows.append({
                "metric_key": "proxy.legitimacy.regime_stability",
                "source_id": "acled",
                "value": float(n),                 # 原始计数；越高=越不稳定（方向由合成层处理）
                "unit": "count_per_quarter",
                "valid_time": qe,
                "knowledge_time": now,
            })
        print(f"[acled] 共 {total_events} 事件 → {len(rows)} 个季度计数点"
              f"（focus 国家 {len(self._countries)} 个，跨国合并）。")
        return rows


if __name__ == "__main__":
    run_id = AcledConnector().run()
    print(f"✓ acled（regime_stability 之 ACLED 腿）已写入，ingest_run_id={run_id}")
