"""Chathy's turn engine: a streaming, multi-turn tool-calling loop.

`run_turn` persists the user message, then repeatedly streams an assistant turn; when the
model asks for tools it executes them in-process (`tools.execute`), appends the results, and
loops — up to `_MAX_ITERS` times — until the model answers with prose. Running out of tool
rounds is NOT a failure: one final turn runs with the tools withheld, so the model has to
land on an answer built from the evidence it already gathered. It yields UI events throughout
(`delta` / `tool_start` / `tool_result` / `done` / `error`) and persists every message —
failures included — so the conversation round-trips.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

from ..config import get_settings
from ..logging import get_logger
from ..models import llm
from ..models.router import TaskClass
from . import sessions
from .tools import execute, openai_tool_defs

log = get_logger("xar.chathy.agent")

_MAX_ITERS = 12          # tool rounds; round _MAX_ITERS+1 always runs with the tools withheld

_WRAPUP_NUDGE = """You have used up this turn's tool rounds — no tools are available to you \
now. Answer the user immediately from the tool output already in this conversation: give the \
best grounded answer the evidence supports, and state in one line which parts you could not \
finish verifying. Do not promise further lookups."""

SYSTEM_PROMPT = """You are Chathy, the analyst copilot for XAR — an industry-chain investment \
research terminal. You answer questions about companies, supply chains, catalysts and \
market regimes across 8 themes (ai_optical, ai_chip, ai_software, space_exploration, \
humanoid_robotics, internet, retail, restaurants) by CALLING THE PLATFORM'S TOOLS — never \
answer market-specific questions from memory when a tool can ground them.

Guidance:
- Resolve a company name/ticker to its id with `find_company` before calling company tools.
- Use `semantic_facts` for "what's happening / what changed", `search_documents` for \
research/filings, the dashboard tools (theme_overview, list_companies, company_detail, \
signals, catalysts, theme_landscape, supply_chain) for structured views.
- For money-flow / positioning / style-rotation / risk-on questions (资金流/仓位/风格轮动) \
use `capital_flow` (scope=market/theme/company); investor-type color (HF/LO/retail/CTA) \
beyond 13F & short interest comes from flow_insight events in `semantic_facts`.
- Ground every claim in tool output. When a fact or document carries a source, cite it \
inline as [title — url] (or the source name). If the tools don't cover something, say so \
plainly rather than guessing.
- Be concise and use GitHub-flavored markdown (tables for comparisons). You are a research \
aide, not a financial advisor — no buy/sell recommendations."""


def _tool_calls_from(msg: dict) -> list[dict]:
    return msg.get("tool_calls") or []


def _fail(session_id: str, message: str) -> Iterator[dict]:
    """Persist the failure into the transcript (so a reload still shows it) and emit the event."""
    sessions.append(session_id, role="assistant", content=f"⚠️ {message}")
    yield {"type": "error", "message": message}


def run_turn(session_id: str, user_text: str) -> Iterator[dict]:
    """Drive one user turn to completion, yielding UI events. Assumes the session exists."""
    sessions.append(session_id, role="user", content=user_text)
    sessions.touch(session_id, title=(user_text.strip()[:60] or None))

    s = get_settings()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + sessions.history_for_llm(session_id)
    tool_defs = openai_tool_defs()
    # per-turn run_id(msgs 长度每轮递增)—— 否则会话级 run_id 让 per-run 预算上限在整个会话
    # 生命周期累计,长会话超限后所有 token 候选被永久跳过、静默降级(K.2.2)。
    run_id = f"chat:{session_id}:{len(msgs)}"

    for i in range(_MAX_ITERS + 1):
        # 最后一轮不给工具 + 收尾指令:撞上限时也交出一份基于已取证据的答案,而不是把前 N 轮
        # 工具结果全丢掉、只回一句 cap 报错(宽问题——「选 5 家公司逐一分析」——很容易吃满轮数)。
        wrapup = i == _MAX_ITERS
        turn = (msgs + [{"role": "system", "content": _WRAPUP_NUDGE}]) if wrapup else msgs

        final: dict | None = None
        # 显式给满推理力度 + 足够输出预算:CHAT 链(kimi-k3/glm-5.2/minimax-m3/deepseek)全是
        # 思考模型,reasoning 与 content 分开吃 max_tokens —— 预算给小了就是空回复(Phase 4 实测)。
        for ev in llm.complete_stream(turn, tools=(None if wrapup else tool_defs),
                                      task=TaskClass.CHAT, node="chathy", run_id=run_id,
                                      max_tokens=s.chat_max_tokens,
                                      reasoning_effort=s.model_effort):
            kind = ev.get("type")
            if kind == "delta":
                yield ev
            elif kind == "error":
                yield from _fail(session_id, str(ev.get("message")))
                return
            elif kind == "final":
                final = ev

        if final is None:
            yield from _fail(session_id, "no response from model")
            return

        msg = final.get("message") or {"role": "assistant", "content": ""}
        usage = final.get("usage")
        calls = [] if wrapup else _tool_calls_from(msg)   # tools were withheld — ignore strays

        if not calls:
            content = msg.get("content") or ""
            if wrapup and not content.strip():
                yield from _fail(session_id, f"tool-iteration cap ({_MAX_ITERS}) reached and the "
                                             "wrap-up turn returned no summary")
                return
            sessions.append(session_id, role="assistant", content=content, usage=usage)
            yield {"type": "done", "usage": usage, **({"capped": True} if wrapup else {})}
            return

        # assistant asked for tools: persist the assistant(tool_calls) turn, run each tool,
        # append results, then loop for the model's next turn.
        sessions.append(session_id, role="assistant", content=msg.get("content") or None,
                        tool_calls=calls, usage=usage)
        msgs.append(msg)
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            call_id = call.get("id", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            yield {"type": "tool_start", "id": call_id, "name": name, "args": args}
            result = execute(name, args)
            sessions.append(session_id, role="tool", content=result, tool_call_id=call_id, name=name)
            msgs.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result})
            yield {"type": "tool_result", "id": call_id, "name": name,
                   "preview": result[:240]}

    # unreachable: the wrap-up round above always returns (answer, or a persisted failure).
