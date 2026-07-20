"""GLM 常驻工人:钉扎机制 / 额度识别 / 状态机 / 单轮流程(全离线,LLM 全打桩)。"""
from __future__ import annotations

import pytest

from xar.models import llm, registry
from xar.orchestration import glm_worker as gw


def test_pinned_restricts_chain_to_registry_specs():
    with llm.pinned(["glm-5.2-sub", "glm-4.6-sub"]):
        chain = llm._apply_pin([registry.get("kimi-k2-sub")])
        assert [m.id for m in chain] == ["glm-5.2-sub", "glm-4.6-sub"]
    # 出上下文后恢复原链
    chain = llm._apply_pin([registry.get("kimi-k2-sub")])
    assert [m.id for m in chain] == ["kimi-k2-sub"]


def test_pinned_unknown_ids_dropped():
    with llm.pinned(["no-such-model", "glm-5.2-sub"]):
        chain = llm._apply_pin([])
        assert [m.id for m in chain] == ["glm-5.2-sub"]


def test_glm52_leads_subscription_chains():
    from xar.models.router import TaskClass, resolve

    assert [m.id for m in resolve(TaskClass.KG_EXTRACT)][:2] == ["glm-5.2-sub", "glm-4.6-sub"]


def test_glm4_local_spec_registered():
    """本地 ollama 模型的注册纪律:订阅计费 + 同名占位 key(used_sub→usd=0)+ 无 reasoning。"""
    spec = registry.get("glm4-local")
    assert spec is not None and spec.provider == "ollama"
    assert spec.billing is registry.Billing.SUBSCRIPTION
    assert not spec.supports_reasoning
    assert spec.capabilities == ()   # 仅钉扎:绝不进默认路由链(否则接住批量回退流量)
    prov = registry.PROVIDERS["ollama"]
    assert prov.sub_key_env == "OLLAMA_API_KEY"
    assert prov.api_base and "11434/v1" in prov.api_base


def test_local_candidate_gets_short_timeout():
    """本地候选带短超时(防挂死);云候选不受影响。"""
    from xar.config import get_settings

    s = get_settings()
    kw = llm._build_kwargs(registry.get("glm4-local"), [], 100, False, False, s, "http://x/v1", None)
    assert kw["timeout"] == s.llm_local_timeout_s
    kw2 = llm._build_kwargs(registry.get("glm-5.2-sub"), [], 100, True, False, s, None, None)
    assert "timeout" not in kw2


def test_fetchy_pin_local_first(monkeypatch):
    """本地优先头:开关+占位 key+云订阅 key 三者齐备才前插;Fetchy 显式选型压过它;
    云订阅 key 缺位不前插(零计量回退不变量)。"""
    from xar.config import get_settings

    # 锁住 _ensure_keys 的一次性闩:否则它会在本测试的假 key 环境下被触发并永久锁死,
    # 把 .env 真实密钥的镜像挡在门外,污染同进程内后续测试(fetchy 三连挂实证)。
    monkeypatch.setattr(llm, "_KEYS_SYNCED", True)
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama")
    monkeypatch.setenv("GLM_SUB_API_KEY", "test-sub")
    monkeypatch.setenv("XAR_GLM_WORKER_LOCAL_FIRST", "true")
    get_settings.cache_clear()
    try:
        assert gw._fetchy_pin({}) == (gw.LOCAL_MODEL_ID, *gw.GLM_PIN)
        # Fetchy 显式选型 = 操作员意图,本地头让位
        explicit = gw._fetchy_pin({"model": "kimi-k2-sub"})
        assert explicit[0] == "kimi-k2-sub" and gw.LOCAL_MODEL_ID not in explicit
        # 云订阅 key 缺位(设空压过 .env)→ 不前插
        monkeypatch.setenv("GLM_SUB_API_KEY", "")
        get_settings.cache_clear()
        assert gw._fetchy_pin({}) == gw.GLM_PIN
        # 开关关(默认)→ 不前插
        monkeypatch.setenv("GLM_SUB_API_KEY", "test-sub")
        monkeypatch.setenv("XAR_GLM_WORKER_LOCAL_FIRST", "false")
        get_settings.cache_clear()
        assert gw._fetchy_pin({}) == gw.GLM_PIN
    finally:
        get_settings.cache_clear()


def test_is_quota_error_patterns():
    from xar.models.llm import BudgetExceeded

    assert gw.is_quota_error(RuntimeError("余额不足或无可用资源包,请充值。"))
    assert gw.is_quota_error(RuntimeError("Rate limit exceeded: 429"))

    class RateLimitError(Exception):
        pass

    assert gw.is_quota_error(RateLimitError("anything"))
    assert not gw.is_quota_error(RuntimeError("connection reset by peer"))
    # 审核修复钉住:预算帽与超长上下文不是订阅额度耗尽
    assert not gw.is_quota_error(BudgetExceeded("run kg-abc exceeded $20.0"))
    assert not gw.is_quota_error(RuntimeError(
        "litellm.ContextWindowExceededError: prompt is too long"))


@pytest.fixture
def state_db(seeded_db):
    from xar.storage import db

    db.execute("DELETE FROM glm_worker_state")
    return db


def test_state_roundtrip(state_db):
    gw.save_state("quota", {"status": "ok", "n": 1})
    assert gw.get_state("quota")["status"] == "ok"
    gw.save_state("quota", {"status": "exhausted"})
    assert gw.get_state("quota")["status"] == "exhausted"
    assert gw.get_state("nonexistent", {"d": 1}) == {"d": 1}


def test_exhaust_resume_state_machine(state_db):
    q = gw._mark_exhausted({"status": "ok"}, "余额不足")
    assert q["status"] == "exhausted" and q["exhaust_count"] == 1
    q = gw._mark_exhausted(q, "still out")     # 幂等:重复标记不再计数
    assert q["exhaust_count"] == 1
    q = gw._mark_ok(q)
    assert q["status"] == "ok" and q["resume_count"] == 1 and q.get("resumed_at")


def test_cadence_gate(state_db):
    assert gw._due("unit_test_key", 3600) is True
    assert gw._due("unit_test_key", 3600) is True    # 只读:未 stamp 前保持 due
    gw._stamp("unit_test_key", 3600, ok=True)
    assert gw._due("unit_test_key", 3600) is False   # 成功后满间隔
    gw._stamp("unit_test_key2", 3600, ok=False)      # 失败:1/4 间隔后重试
    assert gw._due("unit_test_key2", 3600) is False
    assert gw._due("unit_test_key2", 800) is True


def test_run_once_exhausted_probes_and_skips(state_db, monkeypatch):
    gw.save_state("quota", {"status": "exhausted", "exhaust_count": 1})
    monkeypatch.setattr(gw, "_sub_ready", lambda: True)
    monkeypatch.setattr(gw, "probe", lambda: False)
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: {"stub": True})
    monkeypatch.setattr(gw, "_backfill", lambda units: {"done_units": units})
    monkeypatch.setattr("xar.parsing.parse.parse_pending", lambda limit=200: 0)
    monkeypatch.setattr(gw, "_alt_correction", lambda q, rebuilds, pin=None: {"stub": True})
    monkeypatch.setattr(gw, "_research_audit_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_earnings_step", lambda: {"stub": True})
    out = gw.run_once(batch_docs=1, backfill_units=2)
    assert out["quota"] == "exhausted"
    assert out["extract"] == {"skipped": "quota exhausted — waiting for window reset"}
    assert out["backfill"] == {"done_units": 2}
    assert gw.get_state("quota")["status"] == "exhausted"


def test_run_once_ok_extracts_without_probe(state_db, monkeypatch):
    """ok 态零探针开销(审核修复):直接抽取,probe 不得被调用。"""
    gw.save_state("quota", {"status": "ok"})
    monkeypatch.setattr(gw, "_sub_ready", lambda: True)
    monkeypatch.setattr(gw, "probe",
                        lambda: (_ for _ in ()).throw(AssertionError("probe must not run")))
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: {})
    monkeypatch.setattr(gw, "_backfill", lambda units: {})
    monkeypatch.setattr("xar.parsing.parse.parse_pending", lambda limit=200: 0)
    monkeypatch.setattr(gw, "_alt_correction", lambda q, rebuilds, pin=None: {"stub": True})
    monkeypatch.setattr(gw, "_research_audit_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_earnings_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_llm_stage",
                        lambda batch, q, pin=None: ({"kg": {"docs": 2}}, q))
    out = gw.run_once(batch_docs=5, backfill_units=0)
    assert out["extract"]["kg"]["docs"] == 2


def test_run_once_recovery_resumes_extraction(state_db, monkeypatch):
    gw.save_state("quota", {"status": "exhausted", "exhaust_count": 1})
    monkeypatch.setattr(gw, "_sub_ready", lambda: True)
    monkeypatch.setattr(gw, "probe", lambda: True)
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: {})
    monkeypatch.setattr(gw, "_backfill", lambda units: {})
    monkeypatch.setattr("xar.parsing.parse.parse_pending", lambda limit=200: 0)
    monkeypatch.setattr(gw, "_alt_correction", lambda q, rebuilds, pin=None: {"stub": True})
    monkeypatch.setattr(gw, "_research_audit_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_earnings_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_llm_stage",
                        lambda batch, q, pin=None: ({"kg": {"docs": 3}, "expert": {"processed": 1}}, q))
    out = gw.run_once(batch_docs=5, backfill_units=0)
    assert out["quota"] == "ok"
    assert out["extract"]["kg"]["docs"] == 3
    q = gw.get_state("quota")
    assert q["status"] == "ok" and q["resume_count"] == 1


def test_run_once_refuses_metered_fallback(state_db, monkeypatch):
    """订阅 key 缺位时拒绝抽取(零账单保证),而非静默落到按 token 计费。"""
    monkeypatch.setattr(gw, "_sub_ready", lambda: False)
    monkeypatch.setattr(gw, "_pull_fresh", lambda cfg=None: {})
    monkeypatch.setattr(gw, "_backfill", lambda units: {})
    monkeypatch.setattr("xar.parsing.parse.parse_pending", lambda limit=200: 0)
    monkeypatch.setattr(gw, "_alt_correction", lambda q, rebuilds, pin=None: {"stub": True})
    monkeypatch.setattr(gw, "_research_audit_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_earnings_step", lambda: {"stub": True})
    monkeypatch.setattr(gw, "_llm_stage",
                        lambda batch, q, pin=None: (_ for _ in ()).throw(AssertionError("must not extract")))
    out = gw.run_once(batch_docs=1, backfill_units=0)
    assert "GLM_SUB_API_KEY" in out["extract"]["skipped"]
