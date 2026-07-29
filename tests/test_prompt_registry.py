"""M5:提示词注册表 —— 「这条裁决是哪一版提示词产出的」必须可回答。

钉扎表是本文件的核心:改了模板字面量却忘了 bump version,下面第一个测试立刻变红。
没有这道机械约束,version 就只是个装饰性数字,回放/AB 评测都建立在沙上。
"""
from __future__ import annotations

import pytest

from xar.models import prompts

# (key, version, source_sha) —— 改模板必须同时改这里,且 version 要递增。
PINNED = {
    "phanny.critic.system":  (1, "c6aebce4ee950d84c700fb6a65277b0759154aa0d1418e1d25355a3527e8337f"),
    "phanny.critic.user":    (1, "e1c03793fd2fb25ec41f691b52f4f1a11d295a3b2ae95f6a64c458558081b979"),
    "phanny.proposer.retry": (1, "e34b979e8441b157f11e65e04075239bd8c3eac260b84ec84c14149be8ad1a69"),
    "phanny.proposer.system": (1, "4eed4e45486ac5c1d58798d5a9d58068e82a22c82f0c8b0ff2a8fe427e66c026"),
    "phanny.proposer.user":  (1, "62697d64b621987dceac932a1f854931ef4ee901cac13e852509b8c70f4cb4df"),
    "phanny.rebut.user":     (1, "4af413716fb3779a4e5bfb45431e96a020f90e00d5454ad95b6aaa0018809728"),
}


def test_registry_covers_every_phanny_prompt():
    """六处内联提示词全部收编 —— 漏一处就有一段提示词仍无版本身份。"""
    assert set(prompts.REGISTRY) == set(PINNED)


@pytest.mark.parametrize("key", sorted(PINNED))
def test_version_and_sha_pinned(key):
    """改了模板措辞就必须 bump version 并更新此表 —— 这是 version 不沦为装饰的唯一机械保证。"""
    t = prompts.get(key)
    want_ver, want_sha = PINNED[key]
    assert t.version == want_ver, f"{key} 版本变了却没更新钉扎表"
    assert t.sha() == want_sha, (
        f"{key} 模板源码变了 —— 若是有意改动,请 bump version 并把新 sha 写进 PINNED")


def test_manifest_shape():
    m = prompts.manifest()
    assert set(m) == set(PINNED)
    for k, v in m.items():
        assert v["version"] >= 1 and len(v["sha"]) == 64, k


def test_sha_tracks_source_edits():
    """source_sha 必须真的跟着 render 源码走 —— 否则「忘了 bump version」抓不到。"""
    a = prompts.PromptTemplate("x", 1, lambda: "hello")
    b = prompts.PromptTemplate("x", 1, lambda: "hello world")
    assert prompts.template_sha(a) != prompts.template_sha(b)


def test_render_returns_identity():
    text, key, ver = prompts.render("phanny.proposer.user", "2026-07-29", "DOSSIER", "")
    assert key == "phanny.proposer.user" and ver == 1
    assert "2026-07-29" in text and "DOSSIER" in text


def test_dimensions_flow_into_rendered_prompt():
    """PHANNY_DIMENSIONS 作为 render 参数 —— 它的内容进入渲染结果(因而进 prompt_sha),
    而不是藏在模板源码里。改了维度定义,提示词指纹随之改变,回放能察觉。"""
    from xar.ontology.phanny_events import PHANNY_DIMENSIONS
    out = prompts.get("phanny.proposer.system").render(tuple(PHANNY_DIMENSIONS))
    for d in PHANNY_DIMENSIONS:
        assert d in out


def test_engine_and_debate_use_the_registry():
    """引擎侧不得再留内联 f-string 提示词(否则注册表形同虚设)。"""
    import inspect

    from xar.phanny import debate, engine
    assert "prompts.get(" in inspect.getsource(engine._system_phanny)
    assert "prompts.get(" in inspect.getsource(engine.propose)
    assert "prompts.get(" in inspect.getsource(debate._critic_prompt)
    assert "prompts.get(" in inspect.getsource(debate._rebut_prompt)


def test_migration_preserved_prompt_text():
    """迁移必须是**行为等价**的:渲染结果与迁移前逐字一致(这里钉住关键铁律句)。"""
    from xar.ontology.phanny_events import PHANNY_DIMENSIONS
    sysmsg = prompts.get("phanny.proposer.system").render(tuple(PHANNY_DIMENSIONS))
    assert "**方向只能 long 或 short**" in sysmsg
    assert "**不要输出 size**" in sysmsg
    critic = prompts.get("phanny.critic.system").render()
    assert "**禁止反射式同意**" in critic
    rebut = prompts.get("phanny.rebut.user").render("nvidia", "D", "long", 7, "V")
    assert "**严禁仅靠降低 conviction 来平息分歧**" in rebut
