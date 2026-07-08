"""Market Read — deterministic metrics + suitability + narrative fallback (offline)."""

from __future__ import annotations

import pytest

from fcn.marketdata.provider import ManualProvider
from fcn.marketdata.volsurface import ParametricSkewSurface
from fcn.service.market_read import build_market_read, compute_metrics, suitability


def _prov(spy_atm, qqq_atm, rate=0.043):
    surf = {
        "SPY": ParametricSkewSurface(atm=spy_atm, slope=-0.6, curv=0.8),
        "QQQ": ParametricSkewSurface(atm=qqq_atm, slope=-0.7, curv=0.9),
    }
    return ManualProvider(spots={"SPY": 540, "QQQ": 470}, surfaces=surf, rate=rate)


def test_metrics_deterministic_and_vix_proxy():
    prov = _prov(0.15, 0.21)
    m1, m2 = compute_metrics(prov), compute_metrics(prov)
    assert m1 == m2  # deterministic
    assert m1["vix_proxy"] == pytest.approx(15.0, abs=0.5)  # SPY 1M ATM * 100 (flat term)
    assert m1["vol_level"] == pytest.approx((0.15 + 0.21) / 2, abs=0.02)
    assert m1["skew"] > 0  # downside put skew positive


def test_suitability_regime_flips():
    low = suitability(compute_metrics(_prov(0.13, 0.15)))
    high = suitability(compute_metrics(_prov(0.40, 0.46)))
    # High vol favours income (sell downside vol), hurts participation (pricey upside).
    assert high["FCN"]["score"] > low["FCN"]["score"]
    assert high["SharkFin"]["score"] < low["SharkFin"]["score"]
    for fam in ("FCN", "Phoenix", "Snowball", "SharkFin", "Booster"):
        assert fam in high and 0 <= high[fam]["score"] <= 100
        assert high[fam]["label"] in ("favorable", "neutral", "unfavorable")


def test_narrative_template_fallback_when_no_llm():
    r = build_market_read(_prov(0.20, 0.24), llm_caller=lambda *a, **k: None)
    assert r["narrative_source"] == "template"
    assert isinstance(r["narrative"], str) and len(r["narrative"]) > 40


def test_narrative_uses_llm_when_available():
    r = build_market_read(_prov(0.20, 0.24), llm_caller=lambda *a, **k: "AI market read.")
    assert r["narrative_source"] == "llm"
    assert r["narrative"] == "AI market read."


def test_default_narrative_pins_opus_codex_glm_deepseek(monkeypatch):
    """默认(未注入 caller)的市场解读经 XAR 路由时,钉扎 Opus→Codex→GLM→DeepSeek。"""
    import fcn.service.llm as flm
    from xar.models import llm as xllm

    captured = {}

    def fake_complete(prompt, *, system=None, task=None, node=None, max_tokens=None, **k):
        captured["pin"] = xllm._PIN.get()
        return "pinned narrative."

    monkeypatch.setattr(xllm, "complete", fake_complete)
    monkeypatch.setattr(flm, "route_via_xar", True)
    r = build_market_read(_prov(0.20, 0.24))          # no llm_caller → default narrative path
    assert r["narrative_source"] == "llm" and r["narrative"] == "pinned narrative."
    assert captured["pin"] == xllm.FENNY_NARRATIVE_PIN == (
        "claude-opus-max", "codex-sub", "glm-5.2-sub", "deepseek-v4-pro")


def test_no_surfaces_raises():
    prov = ManualProvider(spots={}, surfaces={}, rate=0.04)
    with pytest.raises(ValueError):
        compute_metrics(prov, indices=("SPY",))
