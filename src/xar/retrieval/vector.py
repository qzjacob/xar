"""Hybrid retrieval over pgvector: dense (cosine) + lexical (trigram), fused via
Reciprocal Rank Fusion. Returns chunks with full provenance for citations."""
from __future__ import annotations

from dataclasses import dataclass

from ..models import embeddings
from ..storage import db


@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    company_id: str | None
    text: str
    tie_out_ok: bool
    source: str
    doc_type: str
    title: str
    url: str | None
    score: float

    def citation(self) -> dict:
        return {
            "chunk_id": self.chunk_id, "doc_id": self.doc_id, "title": self.title,
            "source": self.source, "doc_type": self.doc_type, "url": self.url,
            "tie_out_ok": self.tie_out_ok,
        }


def _rows_to_hits(rows: list[dict], scores: dict[int, float]) -> list[Hit]:
    hits = []
    for r in rows:
        hits.append(Hit(
            chunk_id=r["id"], doc_id=r["doc_id"], company_id=r["company_id"],
            text=r["text"], tie_out_ok=r["tie_out_ok"], source=r["source"],
            doc_type=r["doc_type"], title=r["title"], url=r["url"],
            score=scores.get(r["id"], 0.0),
        ))
    return hits


def hybrid_search(query: str, company_id: str | None = None, k: int = 12,
                  numeric: bool = False) -> list[Hit]:
    """numeric=True restricts to chunks that passed the tie-out gate."""
    qvec = embeddings.embed_query(query)
    where = ["1=1"]
    params: list = []
    if company_id:
        where.append("c.company_id = %s")
        params.append(company_id)
    if numeric:
        where.append("c.tie_out_ok = TRUE")
    wsql = " AND ".join(where)

    # dense
    dense = db.query(
        f"""SELECT c.id FROM chunks c
            WHERE {wsql} AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> %s::vector LIMIT %s""",
        params + [qvec, k * 3],
    )
    # lexical (trigram similarity)
    lexical = db.query(
        f"""SELECT c.id FROM chunks c
            WHERE {wsql}
            ORDER BY similarity(c.text, %s) DESC LIMIT %s""",
        params + [query, k * 3],
    )

    # Reciprocal Rank Fusion
    fused: dict[int, float] = {}
    for rank, row in enumerate(dense):
        fused[row["id"]] = fused.get(row["id"], 0.0) + 1.0 / (60 + rank)
    for rank, row in enumerate(lexical):
        fused[row["id"]] = fused.get(row["id"], 0.0) + 1.0 / (60 + rank)
    top_ids = [cid for cid, _ in sorted(fused.items(), key=lambda x: -x[1])[:k]]
    if not top_ids:
        return []

    rows = db.query(
        """SELECT c.id, c.doc_id, c.company_id, c.text, c.tie_out_ok,
                  d.source, d.doc_type, d.title, d.url
           FROM chunks c JOIN documents d ON d.id = c.doc_id
           WHERE c.id = ANY(%s)""",
        (top_ids,),
    )
    hits = _rows_to_hits(rows, fused)
    hits.sort(key=lambda h: -h.score)
    return hits
