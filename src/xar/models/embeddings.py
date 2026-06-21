"""Embeddings via fastembed (ONNX, CPU — no GPU, no extra service).

Pluggable: set XAR_EMBED_MODEL / XAR_EMBED_DIM. Defaults to bge-small (384d,
fast turnkey); set BAAI/bge-m3 (1024d) for best Chinese+English quality."""
from __future__ import annotations

import threading
from functools import lru_cache

from ..config import get_settings
from ..logging import get_logger

log = get_logger("xar.embed")
_LOCK = threading.Lock()


@lru_cache
def _model():
    from fastembed import TextEmbedding

    s = get_settings()
    log.info("loading embedding model %s (dim=%s)", s.embed_model, s.embed_dim)
    return TextEmbedding(model_name=s.embed_model)


def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    with _LOCK:  # fastembed model is not guaranteed thread-safe
        return [list(map(float, v)) for v in _model().embed(texts)]


def embed_query(text: str) -> list[float]:
    return embed_documents([text])[0]
