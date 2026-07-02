"""Genny Data Room: upload → list → download → delete API contract."""
from __future__ import annotations

import io


def _client():
    from starlette.testclient import TestClient
    from xar.api.app import app
    return TestClient(app)


def test_dataroom_upload_list_download_delete(seeded_db):
    c = _client()
    body = b"Data Room unit test.\nDistinctive marker: QUOKKA-7742 photonic memo.\n"
    files = {"file": ("memo.txt", io.BytesIO(body), "text/plain")}
    r = c.post("/api/genny/dataroom/upload", files=files,
               data={"theme": "ai_optical", "segment": "module_maker", "doc_type": "note",
                     "title": "Quokka Memo"})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    assert r.json()["theme"] == "ai_optical" and r.json()["size"] == len(body)

    listed = c.get("/api/genny/dataroom/docs", params={"segment": "module_maker"}).json()
    assert any(d["id"] == doc_id and d["title"] == "Quokka Memo" for d in listed)

    dl = c.get(f"/api/genny/dataroom/docs/{doc_id}/download")
    assert dl.status_code == 200 and dl.content == body
    assert "attachment" in dl.headers.get("content-disposition", "")

    assert c.delete(f"/api/genny/dataroom/docs/{doc_id}").json()["deleted"] is True
    listed2 = c.get("/api/genny/dataroom/docs", params={"segment": "module_maker"}).json()
    assert not any(d["id"] == doc_id for d in listed2)


def test_dataroom_rejects_unsupported_type(seeded_db):
    c = _client()
    files = {"file": ("x.bin", io.BytesIO(b"\x00\x01\x02binary"), "application/octet-stream")}
    r = c.post("/api/genny/dataroom/upload", files=files, data={"theme": "ai_optical"})
    assert r.status_code == 415
