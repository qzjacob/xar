"""Rich-backed logging used across the package."""
from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def get_logger(name: str = "xar") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)
        _CONFIGURED = True
    return logging.getLogger(name)
