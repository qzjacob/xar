"""Child worker for the Claude Max Agent-SDK executor (run as a subprocess).

Runs in a subprocess whose environment has **ANTHROPIC_API_KEY stripped**, so the
Claude Agent SDK authenticates against the Max subscription (the OAuth login in
~/.claude/.credentials.json) rather than the parent's metered key. Isolating this in
a child process keeps the parent's os.environ untouched — the app can serve metered
Anthropic calls concurrently without a global-env race.

Protocol: read one JSON request on stdin → run a single-shot (no-tools, max_turns=1)
Agent-SDK query → write one JSON result on stdout.
  request:  {"model","system","prompt","effort","max_turns"?}
  result:   {"ok":bool,"text":str,"usage":{...}|None,"subtype":str,"error":str|None}
"""
from __future__ import annotations

import asyncio
import json
import sys


async def _run(req: dict) -> dict:
    import claude_agent_sdk as c

    opts_kwargs = dict(
        model=req["model"],
        allowed_tools=[],                 # plain completion — not an agent loop
        max_turns=int(req.get("max_turns", 1)),
        permission_mode="bypassPermissions",
        setting_sources=[],               # ignore project/user CLAUDE.md etc. — clean completion
    )
    if req.get("system"):
        opts_kwargs["system_prompt"] = req["system"]
    if req.get("effort"):
        opts_kwargs["effort"] = req["effort"]
    opts = c.ClaudeAgentOptions(**opts_kwargs)

    text: list[str] = []
    result = None
    async for msg in c.query(prompt=req["prompt"], options=opts):
        if isinstance(msg, c.AssistantMessage):
            for b in msg.content:
                if isinstance(b, c.TextBlock):
                    text.append(b.text)
        elif isinstance(msg, c.ResultMessage):
            result = msg
    usage = getattr(result, "usage", None) if result is not None else None
    subtype = getattr(result, "subtype", "") if result is not None else ""
    # A single-shot result must be `success`; an error/limit/max-turns subtype (or no
    # ResultMessage at all) is a failed completion, not a valid one — surface it so the
    # caller rotates to GLM instead of accepting truncated/empty text as an answer.
    ok = subtype == "success"
    err = None if ok else f"agent result subtype={subtype or 'none'}"
    return {"ok": ok, "text": "".join(text), "usage": usage, "subtype": subtype, "error": err}


def main() -> int:
    try:
        req = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "text": "", "usage": None, "subtype": "",
                          "error": f"bad request: {e}"}))
        return 0
    try:
        out = asyncio.run(_run(req))
    except Exception as e:  # noqa: BLE001 — surface as a structured error, not a traceback
        out = {"ok": False, "text": "", "usage": None, "subtype": "",
               "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
