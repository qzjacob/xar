"""Embeddings via fastembed (ONNX, CPU — no GPU, no extra service).

Pluggable: set XAR_EMBED_MODEL / XAR_EMBED_DIM. Defaults to bge-small-en (384d,
fast turnkey). For a bilingual corpus (Chinese WeChat/cninfo + English filings) run
`xar reembed` → jinaai/jina-embeddings-v2-base-zh (768d, mixed Chinese-English) —
fastembed has no bge-m3, and this jina model is purpose-built for mixed CN-EN."""
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


def _is_e5() -> bool:
    """e5 系列(如 intfloat/multilingual-e5-large)必须给文档/查询加不对称前缀,
    否则检索质量大幅下降。"""
    return "e5" in get_settings().embed_model.lower()


def embed_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    payload = [f"passage: {t}" for t in texts] if _is_e5() else texts
    with _LOCK:  # fastembed model is not guaranteed thread-safe
        return [list(map(float, v)) for v in _model().embed(payload)]


def embed_query(text: str) -> list[float]:
    payload = f"query: {text}" if _is_e5() else text
    with _LOCK:
        return list(map(float, next(iter(_model().embed([payload])))))
