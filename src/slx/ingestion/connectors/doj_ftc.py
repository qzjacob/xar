"""DOJ/FTC 大型科技公司反垄断行动 —— 合法性代理（HTML 抓取，无 API，C_proxy_fragile）。

抓取 FTC / DOJ 公开案件清单页，按季度计数"标题点名大型科技公司"的反垄断/执法行动，
产出（registry sources 标 source_id=doj_ftc）：
  proxy.legitimacy.antitrust_intensity  ← FTC legal-library cases-proceedings（+ DOJ ATR 案件清单，尽力）。
      series_id=bigtech_cases，季度频率，geo=US，unit=count；valid_time=季度末，value=该季命中案件数。

数据来源（**均无 API，仅 HTML，结构会漂移**）：
  - FTC：https://www.ftc.gov/legal-library/browse/cases-proceedings  （?type[]=case 过滤为"案件"，&page=N 翻页）
      每个案件节点形如 <article class="node node--type-case ...">，块内含
      <h3 class="node-title"><a ...>标题</a></h3> 与 <time datetime="YYYY-MM-DDThh:mm:ssZ">。
      解析以"块内首个 node-title + 首个 <time>"配对（容错正则，见 _RE_*）。
  - DOJ Antitrust：https://www.justice.gov/atr/antitrust-case-filings  （案件归档列表）
      DOJ 列表的日期/标题结构更松散，本连接器对 DOJ 做**尽力**抓取：
      从 <a> 链接文本里匹配大型科技公司名，日期取链接近邻的 YYYY 或 MM/DD/YYYY；
      若结构不可识别则跳过 DOJ（不臆造），仅以 FTC 入库。

大型科技公司命中词（标题/链接文本，小写包含匹配）：
  google / alphabet、apple、amazon、meta / facebook、microsoft、nvidia。
  （刻意只覆盖规格指定的 6 家；命中以"标题点名"为准，避免泛化误伤。）

口径声明（审讯纪律）：
  - **案件数 ≠ 执行力度**：一纸和解与一场十年诉讼在此都记 1。本指标只是"立案/行动频次"的
    粗代理（proxy.legitimacy.*），衡量监管对大型科技公司的"关注/动作强度"，非胜诉、非罚没金额、
    非市场影响。解读须配合质性背景。
  - **这是脆弱的爬虫**：FTC/DOJ 随时改版页面 DOM、改 URL、改分页参数。一旦解析失败或抓不到任何
    案件节点，本连接器**打印明确原因并返回 []（干净 no-op）**，绝不编造计数。
  - **覆盖不完整**：默认只翻有限页数（max_pages），仅覆盖最近若干年的案件；早期季度可能缺。
    FTC <time> 多为"清单展示日"（≈立案/更新日），非严格立案日；季度归并按该日期。DOJ 日期更粗。
  - 去重：同一标题在同季多次出现只计一次（按 (季度, 规范化标题) 去重），降低翻页/改版重复计数。

    python -m ingestion.connectors.doj_ftc
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from slx.ingestion.base import Connector

# ── 默认页面 URL / 抓取参数（均为可覆盖默认参，便于将来改版替换）──────────────────────
# FTC：?type[0]=case 过滤为"案件"节点；翻页用 &page=N（0 基）。
_FTC_CASES_URL = (
    "https://www.ftc.gov/legal-library/browse/cases-proceedings"
    "?search=&type%5B0%5D=case"
)
# DOJ Antitrust 案件归档（尽力抓取；结构松散，失败即跳过）。
_DOJ_CASES_URL = "https://www.justice.gov/atr/antitrust-case-filings"

# 大型科技公司命中词（小写包含匹配；刻意限定规格给定的 6 家）。
_BIGTECH = (
    "google", "alphabet", "apple", "amazon",
    "meta", "facebook", "microsoft", "nvidia",
)

# FTC 案件块切分：以 "node node--type-case" 类名作为每个案件节点的起点锚。
_RE_FTC_CASE_BLOCK = re.compile(r'class="node node--type-case')
# 块内标题：<h3 class="node-title"...> 内首个 <a> 文本。
_RE_NODE_TITLE = re.compile(
    r'class="node-title"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S | re.I
)
# 块内时间：<time ... datetime="YYYY-MM-DDThh:mm:ssZ">。
_RE_TIME_DT = re.compile(r'<time[^>]*datetime="([^"]+)"', re.I)
# DOJ 尽力：任意 <a>文本</a>（用于在链接文本里找大型科技公司名）。
_RE_ANY_LINK = re.compile(r'<a[^>]*>(.*?)</a>', re.S | re.I)
# DOJ 日期尽力：MM/DD/YYYY 或裸 4 位年份。
_RE_DATE_MDY = re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b')
_RE_YEAR = re.compile(r'\b(19|20)\d{2}\b')


def _strip_html(s: str) -> str:
    """去标签 + 反转义 + 折叠空白（标题里可能嵌 <em> 等）。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _is_bigtech(title: str) -> bool:
    low = (title or "").lower()
    return any(k in low for k in _BIGTECH)


def _parse_iso_date(s: str) -> date | None:
    """从 <time datetime> 取日期；接受带/不带时间与时区的 ISO 串。"""
    s = (s or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _quarter_end(d: date) -> date:
    q = (d.month - 1) // 3 + 1
    return {1: date(d.year, 3, 31), 2: date(d.year, 6, 30),
            3: date(d.year, 9, 30), 4: date(d.year, 12, 31)}[q]


class DojFtcAntitrustConnector(Connector):
    source_id = "doj_ftc"
    connector = "ingestion.connectors.doj_ftc"

    def __init__(
        self,
        session=None,
        ftc_url: str = _FTC_CASES_URL,
        doj_url: str = _DOJ_CASES_URL,
        max_pages: int = 40,
    ):
        # max_pages：FTC 翻页上界（每页约 20 条），限制抓取规模；可按需放大覆盖更早季度。
        self._session = session
        self._ftc_url = ftc_url
        self._doj_url = doj_url
        self._max_pages = max_pages

    # ── HTTP（超时 / 重试 / User-Agent，镜像 epoch_ai）────────────────────────────
    def _get(self, url: str, *, retries: int = 3, timeout: int = 40) -> str | None:
        import requests  # 延迟导入：模块导入不依赖第三方库

        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {"User-Agent": "Silicon-Index research qzjacob@gmail.com"}
            )
        last = None
        for i in range(retries):
            try:
                r = self._session.get(url, timeout=timeout)
                r.raise_for_status()
                return r.text
            except Exception as e:  # noqa: BLE001
                last = e
                import time
                time.sleep(0.8 * (i + 1))
        print(f"[doj_ftc] 告警：抓取失败（{retries} 次重试后）：{url} —— {last}")
        return None

    # ── FTC：翻页抓取案件清单，配对 (标题, 日期)，按季度计大型科技公司命中数 ──────────────
    def _ftc_hits(self) -> dict[date, set[str]]:
        """返回 {季度末: {规范化命中标题}}（用 set 去重，避免翻页/改版重复计数）。"""
        by_q: dict[date, set[str]] = defaultdict(set)
        any_block = False
        for page in range(self._max_pages):
            sep = "&" if "?" in self._ftc_url else "?"
            url = self._ftc_url if page == 0 else f"{self._ftc_url}{sep}page={page}"
            text = self._get(url)
            if text is None:
                break  # 网络层失败：停止翻页（已抓到的照常入库）
            # 以案件节点锚切块；块内取首个 node-title 与首个 <time> 配对。
            anchors = list(_RE_FTC_CASE_BLOCK.finditer(text))
            if not anchors:
                # 没有任何案件块：要么到尾页，要么 DOM 改版。
                break
            page_cases = 0
            for idx, a in enumerate(anchors):
                start = a.start()
                end = anchors[idx + 1].start() if idx + 1 < len(anchors) else len(text)
                block = text[start:end]
                mt = _RE_NODE_TITLE.search(block)
                md = _RE_TIME_DT.search(block)
                if not (mt and md):
                    continue
                title = _strip_html(mt.group(1))
                d = _parse_iso_date(md.group(1))
                if not (title and d):
                    continue
                page_cases += 1
                any_block = True
                if _is_bigtech(title):
                    by_q[_quarter_end(d)].add(title.lower())
            if page_cases == 0:
                break  # 本页无可解析案件：判定到尾或结构变化，停止。
        if not any_block:
            print("[doj_ftc] 告警：FTC 案件页未解析出任何案件节点"
                  "（疑似 DOM 改版或网络不可达）——FTC 部分判为空。")
        return by_q

    # ── DOJ：尽力抓取（结构松散，失败即跳过，不臆造）──────────────────────────────────
    def _doj_hits(self) -> dict[date, set[str]]:
        """DOJ ATR 列表无稳定 DOM：从链接文本找大型科技公司名，日期取近邻 MM/DD/YYYY 或年份。
        命中不到日期或链接结构不可识别则跳过该项（不入库）。"""
        by_q: dict[date, set[str]] = defaultdict(set)
        text = self._get(self._doj_url)
        if text is None:
            print("[doj_ftc] 提示：DOJ 反垄断案件页不可达，跳过 DOJ（仅以 FTC 入库）。")
            return by_q
        links = list(_RE_ANY_LINK.finditer(text))
        if not links:
            print("[doj_ftc] 提示：DOJ 页未解析出链接结构（疑似改版），跳过 DOJ。")
            return by_q
        for m in links:
            title = _strip_html(m.group(1))
            if not _is_bigtech(title):
                continue
            # 在链接文本附近窗口里找日期（先精确 MM/DD/YYYY，否则裸年份→落到 Q4 末）。
            ctx = text[max(0, m.start() - 200): m.end() + 200]
            dm = _RE_DATE_MDY.search(ctx)
            d: date | None = None
            if dm:
                mm, dd, yy = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
                try:
                    d = date(yy, mm, dd)
                except ValueError:
                    d = None
            if d is None:
                ym = _RE_YEAR.search(ctx)
                if ym:
                    try:
                        # 仅年份：保守归到该年 Q4 末（日期粒度不足，避免假精确）。
                        d = date(int(ym.group(0)), 12, 31)
                    except ValueError:
                        d = None
            if d is None:
                continue  # 无可信日期：跳过，不臆造。
            by_q[_quarter_end(d)].add(("doj::" + title.lower()))
        return by_q

    def fetch(self) -> list[dict]:
        now = datetime.now(timezone.utc)

        # 防御性包裹：任何解析异常都降级为"空贡献"，绝不抛出导致整轮崩溃 / 也绝不编造。
        try:
            ftc = self._ftc_hits()
        except Exception as e:  # noqa: BLE001
            print(f"[doj_ftc] 告警：FTC 解析异常，FTC 部分判为空：{e}")
            ftc = {}
        try:
            doj = self._doj_hits()
        except Exception as e:  # noqa: BLE001
            print(f"[doj_ftc] 告警：DOJ 解析异常，DOJ 部分判为空：{e}")
            doj = {}

        # 合并两源命中集合（同季内按规范化标题去重；DOJ 加前缀避免与 FTC 误合）。
        merged: dict[date, set[str]] = defaultdict(set)
        for src in (ftc, doj):
            for qe, titles in src.items():
                merged[qe] |= titles

        if not merged:
            print("[doj_ftc] 提示：FTC/DOJ 均未抓到任何大型科技公司案件命中——"
                  "本轮无行（不臆造计数）。请检查页面 DOM 与到 ftc.gov/justice.gov 的连通性。")
            return []

        rows: list[dict] = []
        for qe, titles in sorted(merged.items()):
            rows.append({
                "metric_key": "proxy.legitimacy.antitrust_intensity",
                "source_id": "doj_ftc",
                "value": float(len(titles)),  # 该季去重后的命中案件数
                "unit": "count",
                "valid_time": qe,              # 季度末
                "knowledge_time": now,         # 清单页快照=摄取时刻（页面无逐项申报时戳保证）
            })
        print(f"[doj_ftc] antitrust_intensity {len(rows)} 季点"
              f"（FTC {sum(len(v) for v in ftc.values())} 命中 + "
              f"DOJ {sum(len(v) for v in doj.values())} 命中，去重后合并）。")
        return rows


if __name__ == "__main__":
    run_id = DojFtcAntitrustConnector().run()
    print(f"✓ doj_ftc 大型科技反垄断强度已写入，ingest_run_id={run_id}")
