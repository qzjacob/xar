"""Thin Anthropic Messages API client for the Market Read narrative.

Deliberately minimal and stdlib-only (mirrors the marketdata HTTP adapters). The
contract is: numbers are computed deterministically elsewhere — this only writes the
*prose* around them. Any failure (no key, network, bad response) returns ``None`` so
the caller falls back to a deterministic template and the module never breaks.

The POST is injectable (``poster``) so the wrapper is unit-testable offline.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Off by default → standalone `fcn` (and its test suite) behave exactly as upstream:
# generate() uses the Anthropic key or returns None. `xar.api.fenny_mount` sets this True
# when Fenny is mounted into XAR, routing prose through XAR's task manager instead.
route_via_xar = False


def is_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if route_via_xar:  # mounted into XAR: the task manager may have GLM/Kimi/DeepSeek
        try:
            from xar.config import get_settings
            return bool(get_settings().has_llm)
        except Exception:  # noqa: BLE001
            return False
    return False


def _post(url: str, payload: dict, headers: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 700,
    api_key: str | None = None,
    poster: Callable[[str, dict, dict], dict] | None = None,
    narrative: bool = False,
) -> str | None:
    """Return Claude's text for ``prompt``, or ``None`` on any failure / missing key.

    When vendored into XAR (no explicit ``poster``/``api_key``), prose generation is
    routed through XAR's task manager (`xar.models.llm.complete`, task=adhoc_strong) so
    Fenny shares the platform's multi-provider fallback + billing. Any failure returns
    ``None`` so the caller falls back to its deterministic template — contract preserved.

    ``narrative=True`` (the Market Read client-facing prose) pins the XAR fallback order to
    Claude Opus → Codex(gpt-5.5) → GLM-5.2 → DeepSeek — wording quality first, graceful
    rotation when the host-only leaders are unavailable. Structured/advise calls leave it
    False so they keep the platform's default adhoc_strong routing.
    """
    if route_via_xar and poster is None and not api_key:
        try:
            from xar.models.llm import complete
            if narrative:
                from xar.models.llm import FENNY_NARRATIVE_PIN, pinned
                with pinned(FENNY_NARRATIVE_PIN):
                    text = complete(prompt, system=system, task="adhoc_strong",
                                    node="fenny", max_tokens=max_tokens)
            else:
                text = complete(prompt, system=system, task="adhoc_strong",
                                node="fenny", max_tokens=max_tokens)
            return (text or "").strip() or None
        except Exception:  # noqa: BLE001 - any failure -> deterministic template fallback
            return None
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    post = poster or _post
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        data = post(ANTHROPIC_URL, payload, headers)
        blocks = data.get("content", []) if isinstance(data, dict) else []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text.strip() or None
    except Exception:  # noqa: BLE001 - any failure -> deterministic template fallback
        return None


def generate_structured(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1500,
    api_key: str | None = None,
    poster: Callable[[str, dict, dict], dict] | None = None,
) -> dict | None:
    """Return Claude's response parsed as JSON, or ``None`` on any failure.

    The prompt should require a JSON-only response; we additionally strip
    markdown fences before parsing. The caller (advisor) decides whether to
    fall back to a deterministic template.
    """
    raw = generate(
        prompt, system=system, model=model, max_tokens=max_tokens,
        api_key=api_key, poster=poster,
    )
    if raw is None:
        return None
    # Strip ```json ... ``` fences if present.
    txt = raw.strip()
    if txt.startswith("```"):
        lines = txt.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        txt = "\n".join(lines)
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        return None
