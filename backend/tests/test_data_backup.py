"""Daten-Backup und Import-Tool: Sichern, Wiederherstellen (Original-IDs / neue IDs), Dateien, Konflikte, Vorschau, API."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import crud
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.analysis_history import AnalysisHistory
from app.models.document import Document
from app.models.document_token_count import DocumentTokenCount
from app.models.document_version import DocumentVersion
from app.models.import_batch import ImportBatch
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition
from app.models.vendor import Vendor
from app.models.vendor_bank_account import VendorBankAccount
from app.services import data_backup as db_

ALL = (ImportBatch, Document, InvoiceExtraction, OrderPosition, DocumentVersion, AnalysisHistory, DocumentTokenCount)


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    storage, export = tmp_path / "storage", tmp_path / "export"
    storage.mkdir(), export.mkdir()
    monkeypatch.setattr(settings, "storage_path", str(storage))
    monkeypatch.setattr(settings, "export_base_path", str(export))
    return storage, export


@pytest.fixture()
def world(db, dirs):
    storage, _ = dirs
    folder = storage / "Firma_2025"
    (folder / "versions" / "1").mkdir(parents=True)
    b = ImportBatch(company_name="Firma", year=2025, import_folder_path="/import", storage_folder_path=str(folder), status="done",
                    folder_sync=True, auto_export=True)
    db.add(b)
    db.commit()
    d = crud.document.create(db, b.id, "a.pdf", 1)
    crud.document.save_extraction(
        db, d.id, {"supplier_name": "Agro GmbH", "invoice_number": "R-1", "total_amount": 123.45},
        [{"product_description": "KAS", "quantity": 25500, "unit": "kg", "unit_price": 412, "total_price": 10506},
         {"product_description": "Sack", "quantity": 3, "unit": "Stück", "unit_price": 1.5, "total_price": 4.5}],
        json.dumps({"positionen": [{"menge": 25500}]}),
    )
    d.status, d.stored_filename = "done", f"{d.id}.pdf"
    (folder / d.stored_filename).write_bytes(b"%PDF-main")
    db.add(DocumentVersion(document_id=d.id, version_number=1, stored_path=f"versions/{d.id}/v1.pdf", page_count=1, file_size_bytes=9, source="original"))
    (folder / "versions" / str(d.id) / "v1.pdf").write_bytes(b"%PDF-v1")
    db.add(AnalysisHistory(document_id=d.id, position_count=2, snapshot='{"x":1}'))
    db.add(DocumentTokenCount(document_id=d.id, output_token_count=5, input_token_count=7, reasoning_count=0, time_spent_seconds=1.5))
    v = Vendor(name="Agro GmbH", city="Köln")
    db.add(v)
    db.commit()
    db.add(VendorBankAccount(vendor_id=v.id, iban="DE02120300000000202051"))
    db.commit()
    return {"batch": b, "doc": d, "folder": folder}


def _counts(session):
    return {m.__tablename__: session.query(func.count()).select_from(m).scalar() for m in ALL}


def _fresh_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(db_, "SessionLocal", factory)
    return factory


def _backup(**kw):
    state = db_.create_backup(background=False, **kw)
    assert state["error"] is None, state
    return state["result"]["name"]


def test_backup_contains_everything_and_a_manifest(world, dirs):
    name = _backup(include_pdfs=True)
    with zipfile.ZipFile(db_.backup_path(name)) as zf:
        names = set(zf.namelist())
        m = json.loads(zf.read("manifest.json"))
    d = world["doc"].id
    assert {"manifest.json", "tables/import_batches.jsonl", "tables/documents.jsonl", "tables/order_positions.jsonl",
            "tables/analysis_history.jsonl", "tables/vendor.jsonl", f"files/docs/{d}.pdf", f"files/versions/{d}/v1.pdf"} <= names
    assert m["counts"]["order_positions"] == 2 and m["files"] == {"docs": 1, "versions": 1, "missing": 0}
    assert m["batches"][0]["company_name"] == "Firma" and m["batches"][0]["documents"] == 1
    listing = db_.list_backups()
    assert listing[0]["name"] == name and listing[0]["manifest"]["counts"]["documents"] == 1


def test_restore_with_original_ids_into_empty_database_including_files(world, db, dirs, monkeypatch):
    name = _backup(include_pdfs=True)
    original = _counts(db)
    folder = world["folder"]
    for f in folder.rglob("*.pdf"):
        f.unlink()                                             # Speicher „verloren“
    factory = _fresh_db(monkeypatch)
    state = db_.restore_backup(name, mode="preserve", dry_run=False, background=False)
    assert state["error"] is None, state
    s = factory()
    assert _counts(s) == original
    b = s.query(ImportBatch).one()
    assert (b.id, b.company_name, b.folder_sync, b.auto_export) == (world["batch"].id, "Firma", False, False)
    d = s.query(Document).one()
    assert d.id == world["doc"].id and d.status == "done"
    assert (folder / d.stored_filename).read_bytes() == b"%PDF-main"
    assert (folder / "versions" / str(d.id) / "v1.pdf").read_bytes() == b"%PDF-v1"
    pos = s.query(OrderPosition).order_by(OrderPosition.position_index).all()
    assert [float(p.quantity) for p in pos] == [25500.0, 3.0] and pos[0].unit == "kg"
    assert [v.name for v in s.query(Vendor)] == ["Agro GmbH"] and s.query(VendorBankAccount).one().iban.startswith("DE02")      # gleichnamige Lieferanten werden zusammengelegt
    assert state["result"]["files"]["written"] == 2


def test_dry_run_changes_nothing(world, db, dirs, monkeypatch):
    name = _backup()
    factory = _fresh_db(monkeypatch)
    state = db_.restore_backup(name, mode="preserve", dry_run=True, background=False)
    assert state["error"] is None and state["result"]["tables"]["order_positions"] == 2
    s = factory()
    assert all(v == 0 for v in _counts(s).values())


def test_repeating_a_restore_skips_already_present_imports(world, db, dirs):
    name = _backup()
    before = _counts(db)
    state = db_.restore_backup(name, mode="preserve", dry_run=False, background=False)
    assert state["error"] is None and state["result"]["skipped_batches"][0]["reason"].startswith("bereits vorhanden")
    db.expire_all()
    assert _counts(db) == before
    state = db_.restore_backup(name, mode="remap", dry_run=False, background=False)
    assert state["result"]["skipped_batches"] and _counts(db) == before


def test_remap_creates_new_ids_and_rewrites_file_paths(world, db, dirs):
    name = _backup(include_pdfs=True)
    vendors_before = db.query(Vendor).count()
    state = db_.restore_backup(name, mode="remap", dry_run=False, allow_duplicates=True, background=False)
    assert state["error"] is None, state
    db.expire_all()
    assert db.query(ImportBatch).count() == 2 and db.query(Document).count() == 2
    new_doc = db.query(Document).order_by(Document.id.desc()).first()
    assert new_doc.id != world["doc"].id and new_doc.batch_id != world["batch"].id
    assert new_doc.stored_filename == f"{new_doc.id}.pdf"
    assert db.query(OrderPosition).filter_by(document_id=new_doc.id).count() == 2
    v = db.query(DocumentVersion).filter_by(document_id=new_doc.id).one()
    assert v.stored_path == f"versions/{new_doc.id}/v1.pdf"
    folder = Path(db.get(ImportBatch, new_doc.batch_id).storage_folder_path)
    assert (folder / new_doc.stored_filename).read_bytes() == b"%PDF-main" and (folder / v.stored_path).read_bytes() == b"%PDF-v1"
    assert db.query(Vendor).count() == vendors_before            # Lieferanten existierten schon → nicht doppelt


def test_conflicting_ids_abort_without_changes(world, db, dirs, monkeypatch):
    name = _backup()
    factory = _fresh_db(monkeypatch)
    s = factory()
    other = ImportBatch(id=world["batch"].id, company_name="Andere", year=1999, import_folder_path="/x", storage_folder_path="/s", status="done")
    s.add(other)
    s.commit()
    state = db_.restore_backup(name, mode="preserve", dry_run=False, background=False)
    assert state["error"] and "Konflikt" in state["error"] and "Neue IDs" in state["error"]
    assert s.query(ImportBatch).count() == 1 and s.query(Document).count() == 0


def test_partial_restore_of_selected_imports(world, db, dirs, monkeypatch):
    b2 = ImportBatch(company_name="Zweite", year=2024, import_folder_path="/i", storage_folder_path=str(world["folder"]), status="done")
    db.add(b2)
    db.commit()
    crud.document.create(db, b2.id, "z.pdf", 1)
    name = _backup()
    factory = _fresh_db(monkeypatch)
    state = db_.restore_backup(name, mode="preserve", batch_ids=[b2.id], dry_run=False, background=False)
    assert state["error"] is None
    s = factory()
    assert [b.company_name for b in s.query(ImportBatch)] == ["Zweite"] and s.query(Document).count() == 1
    state = db_.restore_backup(name, mode="preserve", batch_ids=[9999], dry_run=False, background=False)
    assert "nicht im Backup" in state["error"]


def test_backup_of_selected_import_only(world, db, dirs):
    other = ImportBatch(company_name="Zweite", year=2024, import_folder_path="/i", storage_folder_path="/s", status="done")
    db.add(other)
    db.commit()
    name = _backup(batch_ids=[world["batch"].id])
    m = db_.read_manifest(db_.backup_path(name))
    assert m["scope"] == "selected" and [b["company_name"] for b in m["batches"]] == ["Firma"] and m["counts"]["import_batches"] == 1


def test_api_roundtrip_and_name_validation(world, db, dirs, sqlite_session_factory):
    def _get_db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db
    client = TestClient(app)
    try:
        assert client.post("/api/data-backup/create", json={"include_pdfs": True}).status_code == 200
        import time
        for _ in range(100):
            if not client.get("/api/data-backup/state").json()["running"]:
                break
            time.sleep(0.05)
        overview = client.get("/api/data-backup").json()
        name = overview["backups"][0]["name"]
        assert overview["state"]["result"]["name"] == name
        dl = client.get(f"/api/data-backup/{name}/download")
        assert dl.status_code == 200 and zipfile.is_zipfile(io.BytesIO(dl.content))
        assert client.get("/api/data-backup/..%2Fetc%2Fpasswd/download").status_code in (400, 404)
        assert client.get("/api/data-backup/nicht-da.zip/download").status_code == 404
        up = client.post("/api/data-backup/upload", files={"file": ("kopie.zip", dl.content, "application/zip")})
        assert up.status_code == 200
        assert client.post("/api/data-backup/upload", files={"file": ("kopie.zip", dl.content, "application/zip")}).status_code == 409
        assert client.post("/api/data-backup/upload", files={"file": ("schlecht.zip", b"kein zip", "application/zip")}).status_code == 400
        assert client.post("/api/data-backup/upload", files={"file": ("x y.txt", b"1", "text/plain")}).status_code == 400
        r = client.post(f"/api/data-backup/{name}/restore", json={"mode": "preserve", "dry_run": True})
        assert r.status_code == 200
        assert client.post(f"/api/data-backup/{name}/restore", json={"mode": "kaputt"}).status_code == 400
        assert client.delete("/api/data-backup/kopie.zip").status_code == 200
        assert client.delete("/api/data-backup/kopie.zip").status_code == 404
    finally:
        app.dependency_overrides.clear()
