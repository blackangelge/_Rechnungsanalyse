"""Tests: PDF-Bearbeitung mit Versionierung (echte PDFs in einem Temp-Verzeichnis)."""

import asyncio  # noqa: F401
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app import crud
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch
from app.routers import documents as docs_router
from app.schemas.document_version import PdfSettingsUpdate
from app.services import pdf_edit_service
from app.services.pdf_edit_service import PdfEditError


def _write_pdf(path: Path, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    for i in range(pages):
        w.add_blank_page(width=200, height=300 + i)   # Höhe kennzeichnet die Seite
    with open(path, "wb") as fh:
        w.write(fh)


def _heights(path: Path) -> list[int]:
    return [int(p.mediabox.height) for p in PdfReader(str(path)).pages]


@pytest.fixture()
def storage(tmp_path):
    return tmp_path


@pytest.fixture()
def make_doc(db, storage):
    def _make(pages=5):
        batch = ImportBatch(
            company_name="Test GmbH", year=2026, import_folder_path="/import/x",
            storage_folder_path=str(storage / "Test GmbH_2026"),
        )
        db.add(batch)
        db.commit()
        doc = crud.document.create(db, batch.id, "r.pdf", 1)
        _write_pdf(storage / "Test GmbH_2026" / f"{doc.id}.pdf", pages)
        crud.document.update_after_copy(db, doc.id, f"{doc.id}.pdf", 0)
        crud.document.update_status(db, doc.id, "done")
        db.refresh(doc)
        return doc
    return _make


def _current(storage, doc):
    return storage / "Test GmbH_2026" / f"{doc.id}.pdf"


def test_remove_pages_creates_original_and_new_version(db, make_doc, storage):
    doc = make_doc(5)
    v = pdf_edit_service.remove_pages(db, doc, remove=[2, 3, 5])
    assert v.version_number == 2 and v.page_count == 2 and v.source == "manual"
    assert v.parent_version == 1 and v.removed_pages == "[2, 3, 5]"
    assert "Seite(n) 2–3, 5" in v.description
    assert _heights(_current(storage, doc)) == [300, 303]       # Seiten 1 und 4 bleiben
    db.refresh(doc)
    assert doc.pdf_version == 2 and doc.page_count == 2
    # Original unverändert erhalten
    versions = pdf_edit_service.list_versions(db, doc.id)
    assert [x.version_number for x in versions] == [1, 2]
    original = storage / "Test GmbH_2026" / versions[0].stored_path
    assert len(_heights(original)) == 5 and versions[0].source == "original"


def test_keep_pages_and_versions_chain(db, make_doc, storage):
    doc = make_doc(6)
    pdf_edit_service.remove_pages(db, doc, keep=[1, 2, 3])           # v2: 3 Seiten
    v3 = pdf_edit_service.remove_pages(db, doc, remove=[1])          # v3 baut auf v2 auf
    assert v3.parent_version == 2 and v3.page_count == 2
    assert _heights(_current(storage, doc)) == [301, 302]


@pytest.mark.parametrize("kwargs, msg", [
    ({"remove": [1, 2, 3]}, "mindestens eine Seite"),
    ({"remove": [9]}, "zwischen 1 und 3"),
    ({"remove": [0]}, "zwischen 1 und 3"),
    ({"keep": [1, 2, 3]}, "keine Änderung"),
    ({}, "ODER"),
    ({"remove": [1], "keep": [2]}, "ODER"),
])
def test_invalid_edits_are_rejected_and_change_nothing(db, make_doc, storage, kwargs, msg):
    doc = make_doc(3)
    with pytest.raises(PdfEditError, match=msg):
        pdf_edit_service.remove_pages(db, doc, **kwargs)
    assert len(_heights(_current(storage, doc))) == 3
    db.refresh(doc)
    assert doc.pdf_version == 1


def test_restore_creates_new_version_and_keeps_history(db, make_doc, storage):
    doc = make_doc(4)
    pdf_edit_service.remove_pages(db, doc, keep=[1])
    v3 = pdf_edit_service.restore_version(db, doc, 1)
    assert v3.version_number == 3 and v3.source == "restore" and v3.page_count == 4
    assert "Version 1" in v3.description
    assert len(_heights(_current(storage, doc))) == 4
    assert [v.version_number for v in pdf_edit_service.list_versions(db, doc.id)] == [1, 2, 3]
    with pytest.raises(PdfEditError, match="bereits die aktuelle"):
        pdf_edit_service.restore_version(db, doc, 3)
    with pytest.raises(PdfEditError, match="nicht gefunden"):
        pdf_edit_service.restore_version(db, doc, 99)


def test_auto_trim(db, make_doc, storage):
    doc = make_doc(100)
    v = pdf_edit_service.auto_trim(db, doc, 1)
    assert v is not None and v.source == "auto" and v.page_count == 1
    assert "nur die ersten 1 von 100" in v.description
    assert pdf_edit_service.auto_trim(db, doc, 1) is None            # nichts mehr zu tun
    short = make_doc(2)
    assert pdf_edit_service.auto_trim(db, short, 3) is None
    assert pdf_edit_service.list_versions(db, short.id) == []        # keine unnötige Version
    db.refresh(short)
    assert short.page_count == 2                                     # Seitenzahl wird nachgetragen


def test_failed_commit_restores_current_pdf(db, make_doc, storage, monkeypatch):
    doc = make_doc(3)
    pdf_edit_service.ensure_original(db, doc)
    db.commit()
    real_commit = db.commit

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError):
        pdf_edit_service.remove_pages(db, doc, keep=[1])
    monkeypatch.setattr(db, "commit", real_commit)
    assert len(_heights(_current(storage, doc))) == 3                # zurückgesetzt
    assert not (storage / "Test GmbH_2026" / "versions" / str(doc.id) / "v2.pdf").exists()
    db.refresh(doc)
    assert doc.pdf_version == 1


def test_delete_version_files(db, make_doc, storage):
    doc = make_doc(3)
    pdf_edit_service.remove_pages(db, doc, keep=[1])
    folder = storage / "Test GmbH_2026" / "versions"
    assert folder.exists()
    pdf_edit_service.delete_version_files(doc)
    assert not folder.exists()


# ─── Analyse-Hook ─────────────────────────────────────────────────────────────

def test_prepare_pdf_trims_when_enabled_and_records_version(db, make_doc, sqlite_session_factory, storage):
    doc = make_doc(10)
    crud.pdf_settings.update(db, PdfSettingsUpdate(auto_trim_enabled=True, auto_trim_max_pages=2))
    docs_router._db_prepare_pdf(doc.id, "r.pdf", doc.batch_id)
    db.expire_all()
    d = db.get(type(doc), doc.id)
    assert d.pdf_version == 2 and d.analyzed_pdf_version == 2 and d.page_count == 2
    assert len(_heights(_current(storage, d))) == 2


def test_prepare_pdf_disabled_only_records_version(db, make_doc, sqlite_session_factory, storage):
    doc = make_doc(10)
    docs_router._db_prepare_pdf(doc.id, "r.pdf", doc.batch_id)
    db.expire_all()
    d = db.get(type(doc), doc.id)
    assert d.pdf_version == 1 and d.analyzed_pdf_version == 1
    assert len(_heights(_current(storage, d))) == 10


def test_prepare_pdf_trim_error_does_not_abort(db, make_doc, sqlite_session_factory, storage, monkeypatch):
    doc = make_doc(10)
    crud.pdf_settings.update(db, PdfSettingsUpdate(auto_trim_enabled=True, auto_trim_max_pages=2))
    monkeypatch.setattr(pdf_edit_service, "auto_trim", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaputt")))
    docs_router._db_prepare_pdf(doc.id, "r.pdf", doc.batch_id)
    db.expire_all()
    d = db.get(type(doc), doc.id)
    assert d.pdf_version == 1 and d.analyzed_pdf_version == 1


# ─── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(sqlite_session_factory):
    def _get_db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_flow(db, make_doc, client, storage):
    doc = make_doc(5)
    base = f"/api/documents/{doc.id}"

    # unbearbeitet: virtuelles Original
    vs = client.get(f"{base}/versions").json()
    assert len(vs) == 1 and vs[0]["version_number"] == 1 and vs[0]["is_current"] and vs[0]["id"] is None
    assert client.get(f"{base}/versions/1/preview").headers["content-type"] == "application/pdf"

    # Seiten entfernen
    r = client.post(f"{base}/pages/remove", json={"remove_pages": [2, 3], "note": "Anlagen"})
    assert r.status_code == 200, r.text
    assert r.json()["version_number"] == 2 and r.json()["removed_pages"] == [2, 3]
    assert r.json()["description"] == "Anlagen" and r.json()["is_current"]

    vs = client.get(f"{base}/versions").json()
    assert [(v["version_number"], v["is_current"]) for v in vs] == [(1, False), (2, True)]
    assert client.get(f"/api/documents/{doc.id}").json()["pdf_version"] == 2

    # Fehler → 400
    bad = client.post(f"{base}/pages/remove", json={"remove_pages": [99]})
    assert bad.status_code == 400 and "zwischen 1 und 3" in bad.json()["detail"]
    assert client.post(f"{base}/pages/remove", json={}).status_code == 400

    # Wiederherstellen
    r = client.post(f"{base}/versions/1/restore")
    assert r.status_code == 200 and r.json()["version_number"] == 3 and r.json()["page_count"] == 5
    assert client.post(f"{base}/versions/3/restore").status_code == 400

    # Thumbnails
    t = client.get(f"{base}/pages/1/thumbnail")
    assert t.status_code == 200 and t.content[:4] == b"\x89PNG"
    assert "max-age" in t.headers["cache-control"]
    assert client.get(f"{base}/pages/1/thumbnail", params={"version": 2}).status_code == 200
    assert client.get(f"{base}/pages/99/thumbnail").status_code == 404

    assert client.get("/api/documents/9999/versions").status_code == 404


def test_api_edit_blocked_while_processing(db, make_doc, client):
    doc = make_doc(3)
    crud.document.update_status(db, doc.id, "processing")
    r = client.post(f"/api/documents/{doc.id}/pages/remove", json={"remove_pages": [1]})
    assert r.status_code == 409


def test_api_auto_trim(db, make_doc, client, storage):
    long_doc, short_doc, busy = make_doc(8), make_doc(1), make_doc(8)
    crud.document.update_status(db, busy.id, "processing")
    r = client.post("/api/documents/auto-trim", json={
        "document_ids": [long_doc.id, short_doc.id, busy.id, 9999], "max_pages": 1})
    body = r.json()
    assert body["trimmed"] == [long_doc.id] and body["unchanged"] == [short_doc.id]
    assert set(body["errors"]) == {str(busy.id), "9999"}
    assert client.post("/api/documents/auto-trim", json={"document_ids": [1], "max_pages": 0}).status_code == 422


def test_api_pdf_settings_roundtrip(client):
    assert client.get("/api/settings/pdf").json()["auto_trim_enabled"] is False
    r = client.put("/api/settings/pdf", json={"auto_trim_enabled": True, "auto_trim_max_pages": 2})
    assert r.json()["auto_trim_enabled"] is True and r.json()["auto_trim_max_pages"] == 2
    assert client.put("/api/settings/pdf", json={"auto_trim_enabled": True, "auto_trim_max_pages": 0}).status_code == 422


def test_api_versions_fills_in_missing_page_count(db, make_doc, client):
    """Nach dem Import ist page_count 0 — die Seitenauswahl braucht trotzdem die echte Zahl."""
    doc = make_doc(7)
    assert doc.page_count == 0
    vs = client.get(f"/api/documents/{doc.id}/versions").json()
    assert vs[0]["page_count"] == 7 and vs[0]["file_size_bytes"] > 0
    db.refresh(doc)
    assert doc.page_count == 7


# ─── Worker: Kürzen vor der Kapazitätsprüfung ────────────────────────────────

def test_worker_trims_before_capacity_check(db, make_doc, sqlite_session_factory, storage):
    from app.worker import worker as worker_module

    from app.services import workflow_service

    doc = make_doc(10)
    tpl = next(t for t in workflow_service.templates() if t["key"] == "trimmed")
    tpl["graph"]["nodes"][1]["data"]["config"] = {"max_pages": 2}          # führender „PDF kürzen“-Knoten
    wf = crud.workflow.create(db, "Kürzen", None, tpl["graph"])
    asyncio.run(worker_module.prepare_pdf_for_task({"document_id": doc.id, "workflow_id": wf.id}))
    db.expire_all()
    d = db.get(type(doc), doc.id)
    assert d.page_count == 2 and d.pdf_version == 2     # die Kapazitätsprüfung sieht jetzt 2 statt 10 Seiten
    assert len(_heights(_current(storage, d))) == 2


def test_worker_prepare_ignores_foreign_payloads(db):
    from app.worker import worker as worker_module

    asyncio.run(worker_module.prepare_pdf_for_task({}))
    asyncio.run(worker_module.prepare_pdf_for_task({"document_id": "x"}))
    asyncio.run(worker_module.prepare_pdf_for_task(None))
