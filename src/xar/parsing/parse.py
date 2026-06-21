"""Parse + chunk + tie-out + embed + index. Documents arrive with extracted
text (connectors) or raw PDFs (object store). Born-digital PDFs use pdfplumber;
deep parse (Docling) is an optional swap via `pip install '.[parse-deep]'`."""
from __future__ import annotations

import re

from ..logging import get_logger
from ..models import embeddings
from ..storage import db, objects
from . import tie_out

log = get_logger("xar.parse")

_CHUNK_CHARS = 1600
_OVERLAP = 200


def chunk_text(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    # paragraph-aware packing into ~1600-char windows with overlap
    paras = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= _CHUNK_CHARS:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= _CHUNK_CHARS:
                buf = p
            else:  # hard-split very long paragraph
                for i in range(0, len(p), _CHUNK_CHARS - _OVERLAP):
                    chunks.append(p[i : i + _CHUNK_CHARS])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def pdf_to_text(raw: bytes) -> str:
    # Optional deep parser (Docling) if installed, else pdfplumber.
    try:
        import tempfile

        from docling.document_converter import DocumentConverter  # type: ignore

        with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
            tf.write(raw)
            tf.flush()
            return DocumentConverter().convert(tf.name).document.export_to_markdown()
    except Exception:
        pass
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        log.warning("pdf parse failed: %s", e)
        return ""


def parse_document(doc_id: str) -> int:
    """Chunk, tie-out-check, embed, and index one document. Returns #chunks."""
    rows = db.query("SELECT id, company_id, text, object_key, doc_type FROM documents WHERE id=%s",
                    (doc_id,))
    if not rows:
        return 0
    d = rows[0]
    text = d["text"] or ""
    if not text and d["object_key"]:
        text = pdf_to_text(objects.get(d["object_key"]))
    chunks = chunk_text(text)
    if not chunks:
        return 0

    # drop existing chunks for idempotency
    db.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
    vectors = embeddings.embed_documents(chunks)
    with db.conn() as c:
        cur = c.cursor()
        for i, (ch, vec) in enumerate(zip(chunks, vectors)):
            ok, _reason = tie_out.check(ch)
            cur.execute(
                "INSERT INTO chunks(doc_id, company_id, ordinal, text, tie_out_ok, embedding) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (doc_id, d["company_id"], i, ch, ok, vec),
            )
        c.commit()
    log.info("parsed %s -> %d chunks", doc_id, len(chunks))
    return len(chunks)


def parse_pending(limit: int | None = None) -> int:
    """Parse all documents that have no chunks yet."""
    sql = ("SELECT d.id FROM documents d LEFT JOIN chunks c ON c.doc_id=d.id "
           "WHERE c.id IS NULL GROUP BY d.id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    total = 0
    for row in db.query(sql):
        total += parse_document(row["id"])
    db.ensure_vector_index()
    return total
