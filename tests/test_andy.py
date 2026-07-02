"""Andy chat agent: tool registry validity + a fake-LLM tool round-trip with persistence."""
from __future__ import annotations

import json


def test_tool_registry_schemas_valid():
    from xar.andy import tools
    defs = tools.openai_tool_defs()
    assert defs and all(d["type"] == "function" for d in defs)
    names = set()
    for t in tools.TOOLS:
        assert t.name not in names, f"duplicate tool {t.name}"
        names.add(t.name)
        p = t.parameters
        assert p["type"] == "object" and "properties" in p
        for req in p.get("required", []):
            assert req in p["properties"], f"{t.name}: required '{req}' not in properties"
        assert callable(t.fn)
    # the semantic-facts + resolve + dashboard tools we depend on are present
    for must in ("find_company", "semantic_facts", "search_documents", "theme_overview",
                 "company_detail", "supply_chain"):
        assert must in names


def test_execute_unknown_tool_returns_error_json():
    from xar.andy import tools
    out = json.loads(tools.execute("nope", {}))
    assert "error" in out


def test_execute_coverage_tool_runs(seeded_db):  # seeded_db fixture ensures companies exist
    from xar.andy import tools
    out = json.loads(tools.execute("coverage", {"theme": "ai_optical"}))
    assert isinstance(out, dict) and "themes" in out


def test_run_turn_tool_loop_and_persistence(monkeypatch, seeded_db):
    """Fake a two-step LLM stream: turn 1 asks for a tool, turn 2 answers — assert the
    tool ran, events fired, and the whole exchange persisted to chat_messages."""
    from xar.andy import agent, sessions
    from xar.models import llm

    sess = sessions.create("t")
    sid = sess["id"]

    calls = {"n": 0}

    def fake_stream(messages, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # first turn: a tool call for coverage
            yield {"type": "delta", "text": "Let me check. "}
            yield {"type": "final", "message": {
                "role": "assistant", "content": "Let me check. ",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "coverage",
                                             "arguments": json.dumps({"theme": "ai_optical"})}}],
            }, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        else:
            # second turn: final prose (must have seen the tool result in messages)
            assert any(m.get("role") == "tool" for m in messages)
            yield {"type": "delta", "text": "There are 8 themes."}
            yield {"type": "final", "message": {"role": "assistant", "content": "There are 8 themes."},
                   "usage": {"prompt_tokens": 20, "completion_tokens": 8}}

    monkeypatch.setattr(llm, "complete_stream", fake_stream)

    events = list(agent.run_turn(sid, "How many themes are there?"))
    kinds = [e["type"] for e in events]
    assert "delta" in kinds
    assert "tool_start" in kinds and "tool_result" in kinds
    assert kinds[-1] == "done"
    assert any(e.get("name") == "coverage" for e in events if e["type"] == "tool_start")

    msgs = sessions.messages(sid)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant"]  # exact ordered log
    assert msgs[1]["tool_calls"] and msgs[-1]["content"] == "There are 8 themes."
