"""Claude Max via Agent SDK: registry/routing wiring, availability gating, executor
branch + subscription billing. Offline — the real SDK/subprocess is monkeypatched, so
CI needs no `claude` CLI, credentials, or network."""
from __future__ import annotations

import pytest


# ── registry: subscription models via the agent_sdk executor ────────────────────
def test_claude_max_models_registered():
    from xar.models import registry
    from xar.models.registry import Billing

    for mid in ("claude-opus-max", "claude-sonnet-max"):
        m = registry.get(mid)
        assert m is not None, f"{mid} not registered"
        assert m.executor == "agent_sdk"
        assert m.billing == Billing.SUBSCRIPTION
        assert m.price_in == 0.0 and m.price_out == 0.0     # subscription → usd=0


def test_default_executor_is_litellm():
    from xar.models import registry

    assert registry.get("deepseek-v4-pro").executor == "litellm"
    assert registry.get("glm-5.2-sub").executor == "litellm"


# ── routing: peer/fallback in quality chains, absent from bulk ──────────────────
def test_claude_max_in_quality_chain_not_bulk():
    from xar.models import router
    from xar.models.router import TaskClass

    editor = [m.id for m in router.resolve(TaskClass.EDITOR)]
    assert "claude-opus-max" in editor                      # available for quality
    assert editor.index("deepseek-v4-pro") < editor.index("claude-opus-max")  # peer/fallback, not lead
    bulk = [m.id for m in router.resolve(TaskClass.KG_EXTRACT)]
    assert "claude-opus-max" not in bulk                    # bulk stays GLM/DeepSeek (cost+latency)


def test_claude_max_pin():
    from xar.models import llm

    assert llm.CLAUDE_MAX_PIN[0] == "claude-opus-max"       # force Opus...
    assert "glm-5.2-sub" in llm.CLAUDE_MAX_PIN              # ...degrade to GLM if host/sub unavailable


# ── availability gating (host-only; docker → False → llm skips) ─────────────────
def test_available_gated_by_flag(monkeypatch):
    from xar.config import get_settings
    from xar.models import agentsdk

    monkeypatch.setattr(get_settings(), "anthropic_max_enabled", False, raising=False)
    assert agentsdk.available() is False                    # disabled → never routes


def test_available_needs_cli_and_creds(monkeypatch):
    from xar.config import get_settings
    from xar.models import agentsdk

    monkeypatch.setattr(get_settings(), "anthropic_max_enabled", True, raising=False)
    monkeypatch.setattr(agentsdk, "_sdk_importable", lambda: True)
    monkeypatch.setattr(agentsdk, "_creds_present", lambda: True)
    monkeypatch.setattr(agentsdk.shutil, "which", lambda _: None)   # no `claude` CLI (docker)
    assert agentsdk.available() is False


def test_is_quota_error():
    from xar.models import agentsdk

    assert agentsdk.is_quota_error(RuntimeError("Usage limit reached; try again later"))
    assert agentsdk.is_quota_error(RuntimeError("429 too many requests"))
    assert not agentsdk.is_quota_error(RuntimeError("connection reset"))


def test_real_model_derivation():
    from xar.models import agentsdk, registry

    # 'anthropic-max/claude-opus-4-8' → real id after '/', config-overridable for opus
    assert agentsdk._real_model(registry.get("claude-sonnet-max")) == "claude-sonnet-4-6"


# ── executor branch: subscription billing + skip-when-unavailable ───────────────
def test_llm_routes_agent_sdk_and_bills_subscription(monkeypatch):
    from types import SimpleNamespace

    from xar.models import agentsdk, llm

    monkeypatch.setattr(agentsdk, "available", lambda: True)
    calls = {}

    def fake_complete(spec, *, system, prompt, max_tokens, want_strong):
        calls["spec"] = spec.id
        return "OPUS SAYS HELLO", SimpleNamespace(prompt_tokens=100, completion_tokens=20)

    monkeypatch.setattr(agentsdk, "complete", fake_complete)
    recorded = {}

    def fake_record(run_id, node, spec, usage, task_class, used_sub):
        recorded.update({"spec": spec.id, "used_sub": used_sub})

    monkeypatch.setattr(llm, "_record", fake_record)
    with llm.pinned(("claude-opus-max",)):
        out = llm.complete("hi", task="editor", node="t", max_tokens=100)
    assert out == "OPUS SAYS HELLO"
    assert calls["spec"] == "claude-opus-max"
    assert recorded["used_sub"] is True                     # billed subscription (usd=0)


def test_llm_skips_agent_sdk_when_unavailable(monkeypatch):
    """Host-only: when the Agent SDK isn't available (docker), the pinned chain rotates
    to the next candidate rather than failing."""
    from xar.models import agentsdk, llm

    monkeypatch.setattr(agentsdk, "available", lambda: False)
    called = {"agent": False}
    monkeypatch.setattr(agentsdk, "complete",
                        lambda *a, **k: called.update(agent=True) or ("x", None))
    # pin opus-max then a fake — opus-max skipped; with only opus-max pinned it raises cleanly
    with pytest.raises(Exception):  # noqa: PT011
        with llm.pinned(("claude-opus-max",)):
            llm.complete("hi", task="editor", node="t", max_tokens=50)
    assert called["agent"] is False                          # never invoked the unavailable executor
