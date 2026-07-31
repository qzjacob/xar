"""提示词版本身份必须贯穿到构建快照(2026-07-31 审核 P2-2 的回归)。

背景:`models/prompts` 设计的模板身份是双轨的 ——「人工 version + 源码 sha」。
`replay._verify_prompt` 判漂移用的是 `注册表版本 != 快照记录的版本`。
而各调用点此前把 `template_ver=1` **写死**:一旦谁 bump 了某模板的 version,
新构建仍记 1,回放就会对**本该正确**的构建误报「模板已从 v1 升到 v2」——
校验位反向失真,比没有这个字段更糟。

修法是把取值权威收到注册表一处:`snap_call` 在调用方不显式给 `template_ver` 时
自行现取。本测试守住这个不变量。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from xar.models import prompts
from xar.phanny import snapshots


def test_registry_render_returns_version():
    """模块级 render 的三元返回是版本身份的来源,不能被改成只返文本。"""
    text, key, ver = prompts.render("phanny.critic.system")
    assert isinstance(text, str) and text
    assert key == "phanny.critic.system"
    assert isinstance(ver, int) and ver >= 1


def test_snap_call_derives_version_from_registry(monkeypatch):
    """不传 template_ver → 必须从注册表现取,而不是落 NULL 或写死。"""
    captured: dict = {}

    def fake_execute(sql, params=None):
        captured["params"] = params

    monkeypatch.setattr(snapshots.db, "execute", fake_execute)
    monkeypatch.setattr(snapshots, "save_artifact", lambda *a, **k: "sha-stub")

    snapshots.snap_call("b1", "now", stage="critic", template="phanny.critic.user",
                        capture={"prompt_sha": "x"})
    params = captured["params"]
    expected = prompts.REGISTRY["phanny.critic.user"].version
    assert expected in params, f"快照未写入注册表版本 {expected}: {params}"


def test_bumping_a_template_version_flows_into_snapshots(monkeypatch):
    """核心回归:bump 版本后,新快照必须记新版本 —— 这正是写死 1 时会坏掉的地方。"""
    captured: dict = {}
    monkeypatch.setattr(snapshots.db, "execute",
                        lambda sql, params=None: captured.update(params=params))
    monkeypatch.setattr(snapshots, "save_artifact", lambda *a, **k: "sha-stub")

    # PromptTemplate 是 frozen dataclass(刻意:模板身份不该被运行时改写),
    # 所以模拟 bump 要换掉注册表里的整个条目,而不是改字段。
    import dataclasses
    bumped = dataclasses.replace(prompts.REGISTRY["phanny.rebut.user"], version=7)
    monkeypatch.setitem(prompts.REGISTRY, "phanny.rebut.user", bumped)

    snapshots.snap_call("b1", "now", stage="rebut", template="phanny.rebut.user",
                        capture={"prompt_sha": "x"})
    assert 7 in captured["params"], "bump 后快照仍记旧版本 —— 回放会误报模板漂移"


def test_explicit_version_still_wins(monkeypatch):
    """显式传值仍然生效(测试构造反例、或回填历史快照时需要)。"""
    captured: dict = {}
    monkeypatch.setattr(snapshots.db, "execute",
                        lambda sql, params=None: captured.update(params=params))
    monkeypatch.setattr(snapshots, "save_artifact", lambda *a, **k: "sha-stub")

    snapshots.snap_call("b1", "now", stage="critic", template="phanny.critic.user",
                        template_ver=99, capture={"prompt_sha": "x"})
    assert 99 in captured["params"]


def test_unknown_template_does_not_raise(monkeypatch):
    """模板已从注册表移除时,取版本失败不得炸掉快照写入(观测面 never-raise 契约)。"""
    captured: dict = {}
    monkeypatch.setattr(snapshots.db, "execute",
                        lambda sql, params=None: captured.update(params=params))
    monkeypatch.setattr(snapshots, "save_artifact", lambda *a, **k: "sha-stub")

    snapshots.snap_call("b1", "now", stage="critic", template="does.not.exist",
                        capture={"prompt_sha": "x"})
    assert "params" in captured


@pytest.mark.parametrize("path", ["src/xar/phanny/engine.py", "src/xar/phanny/debate.py"])
def test_no_hardcoded_template_version_at_call_sites(path):
    """护栏:调用点不得再出现 `template_ver=<字面量>`。

    取值权威只能有一处(注册表)。任何在调用点写死版本号的做法,都会在下次 bump 时
    悄悄让回放校验位失真 —— 而失真的校验位不会报错,只会给出**错误的**结论。
    """
    src = pathlib.Path(path).read_text()
    hits = re.findall(r"template_ver\s*=\s*\d+", src)
    assert not hits, f"{path} 出现硬编码模板版本: {hits}"
