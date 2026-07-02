"""Genny Data Room — upload / browse / download report documents per theme·segment.

An upload becomes an ordinary `documents` row (source='upload') via the existing
`ingestion.base.Doc` → object-store + `parse_pending` chunk/embed pipeline, then tagged
with theme/segment (the additive columns). So uploads are searchable by Andy (hybrid_search)
and by the retrieval layer with zero new indexing code.
"""
from __future__ import annotations

import io

from ..ingestion import base
from ..logging import get_logger
from ..storage import db, objects

log = get_logger("xar.dataroom")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DOC_TYPES = {"report", "note", "transcript", "filing", "other"}
_SUFFIX = {"application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md"}


def _extract_text(data: bytes, content_type: str, filename: str) -> str:
    name = (filename or "").lower()
    if content_type == "application/pdf" or name.endswith(".pdf"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    if name.endswith((".md", ".markdown", ".txt", ".text")) or content_type.startswith("text/"):
        return data.decode("utf-8", errors="replace").strip()
    raise ValueError(f"unsupported file type: {content_type or filename}")


def ingest_upload(*, data: bytes, filename: str, content_type: str, theme: str,
                  segment: str | None = None, company_id: str | None = None,
                  doc_type: str = "report", title: str | None = None) -> dict:
    """Persist an uploaded document + tag it to theme/segment. Returns its row summary.
    (Chunk/embed is triggered separately by the caller via parse_pending.)"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file too large ({len(data)} bytes > {MAX_UPLOAD_BYTES})")
    dt = doc_type if doc_type in DOC_TYPES else "other"
    text = _extract_text(data, content_type, filename)
    if not text:
        raise ValueError("no extractable text in file")
    doc = base.Doc(
        company_id=company_id or None,
        source="upload",
        doc_type=dt,
        title=(title or filename or "untitled").strip()[:300],
        text=text,
        permission="grey",              # third-party research, self-use
        license_tag="uploaded",
        raw=data,
        meta={"filename": filename, "content_type": content_type, "size": len(data),
              "uploaded": True},
    )
    doc_id = base.save(doc)
    db.execute("UPDATE documents SET theme=%s, segment=%s WHERE id=%s",
               (theme or None, segment or None, doc_id))
    log.info("dataroom upload %s theme=%s segment=%s (%d chars)", doc_id, theme, segment, len(text))
    return {"id": doc_id, "title": doc.title, "theme": theme, "segment": segment,
            "doc_type": dt, "size": len(data), "chunk_count": 0}


def list_docs(theme: str | None = None, segment: str | None = None,
              company_id: str | None = None, q: str | None = None, limit: int = 200) -> list[dict]:
    sql = ["SELECT d.id, d.title, d.doc_type, d.source, d.theme, d.segment, d.company_id, "
           "d.published_at, d.meta, "
           "(SELECT count(*) FROM chunks c WHERE c.doc_id = d.id) AS chunk_count "
           "FROM documents d WHERE TRUE"]
    params: list = []
    if theme:
        sql.append("AND d.theme = %s")
        params.append(theme)
    if segment:
        sql.append("AND d.segment = %s")
        params.append(segment)
    if company_id:
        sql.append("AND d.company_id = %s")
        params.append(company_id)
    if q:
        sql.append("AND d.title ILIKE %s")
        params.append(f"%{q}%")
    sql.append("ORDER BY d.published_at DESC NULLS LAST, d.id DESC LIMIT %s")
    params.append(limit)
    return db.query(" ".join(sql), params)


def get_download(doc_id: str) -> tuple[bytes, str, str] | None:
    rows = db.query("SELECT object_key, title, meta FROM documents WHERE id=%s", (doc_id,))
    if not rows or not rows[0]["object_key"]:
        return None
    meta = rows[0]["meta"] or {}
    data = objects.get(rows[0]["object_key"])
    filename = meta.get("filename") or f"{rows[0]['title']}"
    content_type = meta.get("content_type") or "application/octet-stream"
    return data, content_type, filename


def delete_doc(doc_id: str) -> bool:
    db.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
    rows = db.query("DELETE FROM documents WHERE id=%s RETURNING id", (doc_id,))
    return bool(rows)
