"""动态路由(router.route):按复杂度×相关性在能力层间升降。用真实 registry,不碰 DB。"""
from __future__ import annotations

from xar.models import router as R
from xar.models.registry import Capability


def test_complex_bulk_escalates_to_strong():
    """复杂/高价值 bulk 任务 → 升到 STRONG 层(仍订阅优先,成本有界)。"""
    by_complexity = R.route(R.TaskClass.KG_EXTRACT, complexity="high")
    assert by_complexity and Capability.STRONG in by_complexity[0].capabilities
    by_relevance = R.route(R.TaskClass.KG_EXTRACT, relevance="high")
    assert by_relevance and Capability.STRONG in by_relevance[0].capabilities
    # 长 prompt 自动推断为复杂 → 同样升层
    by_size = R.route(R.TaskClass.KG_EXTRACT, input_chars=50_000)
    assert Capability.STRONG in by_size[0].capabilities


def test_simple_bulk_stays_cheap():
    """简单 bulk 任务保持 CHEAP_BULK(本地/订阅零成本)。"""
    simple = R.route(R.TaskClass.KG_EXTRACT, complexity="low")
    assert simple and Capability.CHEAP_BULK in simple[0].capabilities
    short = R.route(R.TaskClass.KG_EXTRACT, input_chars=300)
    assert Capability.CHEAP_BULK in short[0].capabilities


def test_simple_strong_downgrades_to_fast():
    """简单的强任务(DEBATE)→ 降到 FAST 层省成本。"""
    chain = R.route(R.TaskClass.DEBATE, complexity="low")
    assert chain and Capability.FAST in chain[0].capabilities
    # 高价值时不降(relevance=high 保住强层)
    kept = R.route(R.TaskClass.DEBATE, complexity="low", relevance="high")
    assert Capability.STRONG in kept[0].capabilities


def test_no_signal_is_static_baseline():
    """无 complexity/relevance/size 信号 → 退化为静态 per-task 路由(向后兼容)。"""
    assert R.route(R.TaskClass.KG_EXTRACT) == R.resolve(R.TaskClass.KG_EXTRACT)
    assert Capability.CHEAP_BULK in R.route(R.TaskClass.KG_EXTRACT)[0].capabilities
