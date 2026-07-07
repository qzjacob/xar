"""OpenAI Codex CLI subscription (executor="codex_cli"): registry/routing wiring,
availability gating, executor branch + subscription billing. Offline — the real CLI/subprocess
is monkeypatched, so CI needs no `codex` CLI, login, or network."""
from __future__ import annotations

import pytest


# ── registry: subscription model via the codex_cli executor ─────────────────────
def test_codex_model_registered():
    from xar.models import registry
    from xar.models.registry import Billing, Capability

    m = registry.get("codex-sub")
    assert m is not None, "codex-sub not registered"
    assert m.executor == "codex_cli"
    assert m.billing == Billing.SUBSCRIPTION
    assert m.price_in == 0.0 and m.price_out == 0.0            # subscription → usd=0
    assert Capability.FAST not in m.capabilities              # slow subprocess never leads FAST
    assert Capability.STRONG in m.capabilities and Capability.REASONING in m.capabilities


# ── routing: peer/fallback in deep-research chains, absent from bulk ─────────────
def test_codex_in_strong_chain_not_bulk():
    from xar.models import router
    from xar.models.router import TaskClass

    audit = [m.id for m in router.resolve(TaskClass.AUDIT)]     # STRONG/token deep-research task
    assert "codex-sub" in audit                                # available as a candidate
    assert audit.index("deepseek-v4-pro") < audit.index("codex-sub")  # peer/fallback, not lead
    bulk = [m.id for m in router.resolve(TaskClass.KG_EXTRACT)]
    assert "codex-sub" not in bulk                             # bulk stays GLM/DeepSeek


def test_codex_pin():
    from xar.models import llm

    assert llm.CODEX_PIN[0] == "codex-sub"                     # force Codex first...
    assert "glm-5.2-sub" in llm.CODEX_PIN                      # ...degrade to GLM if unavailable


# ── availability gating (host-only + OFF by default; docker → False → llm skips) ─
def test_available_off_by_default(monkeypatch):
    from xar.config import get_settings
    from xar.models import codex_cli

    monkeypatch.setattr(codex_cli, "_host_ready", lambda: True)   # pretend CLI+auth present
    monkeypatch.setattr(get_settings(), "codex_enabled", False, raising=False)
    assert codex_cli.available() is False                     # disabled → never routes (opt-in)
    monkeypatch.setattr(get_settings(), "codex_enabled", True, raising=False)
    assert codex_cli.available() is True                      # armed + host ready → available


def test_available_needs_cli_and_auth(monkeypatch):
    from xar.config import get_settings
    from xar.models import codex_cli

    codex_cli._host_ready.cache_clear()
    monkeypatch.setattr(get_settings(), "codex_enabled", True, raising=False)
    monkeypatch.setattr(codex_cli, "_auth_present", lambda: True)
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: None)   # no `codex` CLI (docker)
    assert codex_cli.available() is False


def test_is_quota_error():
    from xar.models import codex_cli

    assert codex_cli.is_quota_error(RuntimeError("Usage limit reached; try again later"))
    assert codex_cli.is_quota_error(RuntimeError("429 too many requests"))
    assert not codex_cli.is_quota_error(RuntimeError("connection reset"))


def test_real_model_from_config(monkeypatch):
    from xar.config import get_settings
    from xar.models import codex_cli, registry

    monkeypatch.setattr(get_settings(), "codex_model", "gpt-5.5-turbo", raising=False)
    assert codex_cli._real_model(registry.get("codex-sub")) == "gpt-5.5-turbo"


# ── executor branch: subscription billing + skip-when-unavailable ───────────────
def test_llm_routes_codex_and_bills_subscription(monkeypatch):
    from types import SimpleNamespace

    from xar.models import codex_cli, llm

    monkeypatch.setattr(codex_cli, "available", lambda: True)
    calls = {}

    def fake_complete(spec, *, system, prompt, max_tokens, want_strong):
        calls["spec"] = spec.id
        return "CODEX SAYS HELLO", SimpleNamespace(prompt_tokens=100, completion_tokens=20)

    monkeypatch.setattr(codex_cli, "complete", fake_complete)
    recorded = {}
    monkeypatch.setattr(llm, "_record",
                        lambda run_id, node, spec, usage, task_class, used_sub:
                        recorded.update({"spec": spec.id, "used_sub": used_sub}))
    with llm.pinned(("codex-sub",)):
        out = llm.complete("hi", task="audit", node="t", max_tokens=100)
    assert out == "CODEX SAYS HELLO"
    assert calls["spec"] == "codex-sub"
    assert recorded["used_sub"] is True                       # billed subscription (usd=0)


def test_llm_skips_codex_when_unavailable(monkeypatch):
    """Host-only: when the Codex CLI isn't available (docker/undialed), the pinned chain
    rotates to the next candidate rather than invoking it."""
    from xar.models import codex_cli, llm

    monkeypatch.setattr(codex_cli, "available", lambda: False)
    called = {"codex": False}
    monkeypatch.setattr(codex_cli, "complete",
                        lambda *a, **k: called.update(codex=True) or ("x", None))
    with pytest.raises(Exception):  # noqa: PT011 — only codex-sub pinned + unavailable → raises
        with llm.pinned(("codex-sub",)):
            llm.complete("hi", task="audit", node="t", max_tokens=50)
    assert called["codex"] is False                           # never invoked the unavailable executor
