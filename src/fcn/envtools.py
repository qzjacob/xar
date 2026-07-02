"""Minimal, dependency-free ``.env`` loader.

The app reads API keys from ``os.environ`` (MASSIVE / FMP / FINNHUB / ANTHROPIC).
Previously those were only present if the launching shell had exported them, so the
documented ``uvicorn fcn.api.main:app`` command ran with no keys. This loads ``.env``
at server startup so keys in the project's ``.env`` are picked up regardless of how
the server is launched. It is intentionally NOT imported by ``fcn`` package init or
tests — only the API entrypoint calls it — so unit tests never pick up real keys.

Precedence: an already-set environment variable always wins over the ``.env`` value.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_dotenv(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (or cwd) to the filesystem root looking for a ``.env``."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        candidate = d / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(path: Path | None = None, *, override: bool = False) -> int:
    """Load ``KEY=VALUE`` pairs from ``.env`` into ``os.environ``.

    Returns the number of keys set. Existing env vars are preserved unless
    ``override`` is true. Lines that are blank, comments, or malformed are skipped;
    surrounding quotes and an optional ``export`` prefix are stripped.
    """
    dotenv = path or find_dotenv()
    if dotenv is None or not dotenv.is_file():
        return 0
    count = 0
    for raw in dotenv.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            count += 1
    return count
