"""AlphaPai provider 离线测试(monkeypatch httpx + get_settings + save)。

验证:recall JSON 解析→doc(contextInfo 元数据 + chunks、doc_type 映射、doc_id、published_at 正则)、
SSE agent 聚合→one_pager、限流 code 203/204 识别、available() 门控、幂等 doc_id。零网络。
"""
from __future__ import annotations

from xar.providers import alphapai


class _S:
    alphapai_api_key = "k-test"
    alphapai_base_url = "https://open-api.rabyte.cn"
    alphapai_recall_types = "roadShow,report,comment"
    alphapai_lookback_days = 30
    alphapai_agent_modes = "2,7"
    enable_alphapai = True


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _post_returning(payload):
    def post(url, headers=None, content=None, timeout=None):   # noqa: A002
        return _Resp(payload)
    return post


class _Stream:
    def __init__(self, chunks):
        self._c = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, n=4096):
        yield from self._c


_RECALL = {"code": 200000, "message": "success", "data": [
    {"id": "HCMT00000000839881", "type": "comment",
     "contextInfo": "(发布时间为:2025-03-18 08:53:40)行业: 通信,机构: 天风证券,标题: 中际旭创业绩快报点评",
     "chunks": ["chatGPT带动光器件需求提升,CPO有望在2025年逐步起量。" * 2], "contextText": "", "answer": ""},
    {"id": "RS0000000012345", "type": "roadShow",
     "contextInfo": "发布时间:2025-03-01 10:00:00,标题: 某公司路演纪要",
     "chunks": ["管理层交流:800G放量,1.6T在验证。" * 2], "contextText": "", "answer": ""},
]}


def test_doctype_map():
    m = alphapai._DOCTYPE_MAP
    assert m["roadShow"] == "meeting_minutes" and m["roadShow_us"] == "meeting_minutes"
    assert m["report"] == "broker_report" and m["comment"] == "broker_report"
    assert m["ann"] == "announcement" and m["social_media"] == "news"


def test_pub_regex():
    d = alphapai._pub("(发布时间为:2025-03-18 08:53:40)标题:x")
    assert d is not None and d.year == 2025 and d.month == 3 and d.day == 18
    assert alphapai._pub("发布时间:2025-03-01").day == 1
    assert alphapai._pub("无日期") is None


def test_available_gating(monkeypatch):
    class _Empty:
        alphapai_api_key = ""
    monkeypatch.setattr(alphapai, "get_settings", lambda: _Empty())
    assert alphapai.available() is False
    assert alphapai.pull_recall("x", scope="company") == 0


def test_recall_parses_and_saves(monkeypatch):
    monkeypatch.setattr(alphapai, "get_settings", lambda: _S())
    monkeypatch.setattr(alphapai.httpx, "post", _post_returning(_RECALL))
    import xar.ingestion.base as base
    saved = []
    monkeypatch.setattr(base, "save", lambda doc: saved.append(doc) or doc.id)

    n = alphapai.pull_recall("中际旭创 CPO", ["comment", "roadShow"],
                             company_id="innolight", scope="company")
    assert n == 2
    by_id = {d.id: d for d in saved}
    assert "alphapai:comment:HCMT00000000839881" in by_id
    d = by_id["alphapai:comment:HCMT00000000839881"]
    assert d.source == "alphapai" and d.doc_type == "broker_report" and d.permission == "grey"
    assert d.company_id == "innolight" and d.meta["alphapai_type"] == "comment"
    assert d.meta["institution"] == "天风证券" and d.meta["industry"] == "通信"
    assert d.published_at is not None and d.published_at.year == 2025
    assert "天风证券" in (by_id["alphapai:comment:HCMT00000000839881"].title or "") or d.title
    # roadShow → meeting_minutes
    assert by_id["alphapai:roadShow:RS0000000012345"].doc_type == "meeting_minutes"

    # 幂等:同批再跑 doc_id 不变
    saved.clear()
    alphapai.pull_recall("中际旭创 CPO", ["comment", "roadShow"], company_id="innolight", scope="company")
    assert sorted(d.id for d in saved) == ["alphapai:comment:HCMT00000000839881",
                                           "alphapai:roadShow:RS0000000012345"]


def test_rate_limit_detected(monkeypatch):
    monkeypatch.setattr(alphapai, "get_settings", lambda: _S())
    monkeypatch.setattr(alphapai.httpx, "post", _post_returning({"code": 203, "message": "用户当日超过限制"}))
    import xar.ingestion.base as base
    saved = []
    monkeypatch.setattr(base, "save", lambda doc: saved.append(doc))
    assert alphapai.pull_recall("x", ["comment"], scope="company") == 0
    assert saved == []


def test_agent_sse_saves_onepager(monkeypatch):
    monkeypatch.setattr(alphapai, "get_settings", lambda: _S())
    chunks = [b'data: {"data":{"answer":"# ',
              "中际".encode(),                          # split multi-byte CJK across chunks
              ("旭创（300308.SZ）公司一页纸\\n\\n核心逻辑:AI 算力驱动光模块需求高增,公司为 800G/1.6T "
               "光模块龙头,海外云厂 capex 上行,业绩确定性强,估值处于合理区间。风险:价格战与技术迭代。"
               '"}}\n\n').encode(),
              b'data: {"data":{"references":[{"title":"t","type":"report"}]}}\n\n']
    monkeypatch.setattr(alphapai.httpx, "stream",
                        lambda method, url, **k: _Stream(chunks))
    import xar.ingestion.base as base
    saved = []
    monkeypatch.setattr(base, "save", lambda doc: saved.append(doc) or doc.id)

    n = alphapai.pull_agent("innolight", 2)     # innolight has CN ticker 300308.SZ
    assert n == 1 and len(saved) == 1
    d = saved[0]
    assert d.source == "alphapai" and d.doc_type == "one_pager"
    assert d.doc_id == "alphapai:agent2:300308.SZ" and "中际旭创" in d.text


def test_agent_skips_non_cn(monkeypatch):
    monkeypatch.setattr(alphapai, "get_settings", lambda: _S())
    # a US-only name without a CN ticker → _cn_stock None → agent no-op
    monkeypatch.setattr(alphapai, "company_by_id",
                        lambda cid: {"name": "NVIDIA", "tickers": ["NVDA"], "region": "US"})
    assert alphapai.pull_agent("nvda", 2) == 0
