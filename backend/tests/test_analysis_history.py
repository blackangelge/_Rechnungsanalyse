"""Analyse-Historie: Aufheben vor Neuanalyse, Dedup, Begrenzung, Wiederherstellen, Endpunkte."""

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch
from app.models.order_position import OrderPosition


def _doc(db):
    batch = ImportBatch(
        company_name="Test GmbH", year=2026,
        import_folder_path="/import/x", storage_folder_path="/storage/Test_GmbH_2026",
    )
    db.add(batch)
    db.commit()
    return crud.document.create(db, batch.id, "r.pdf", 1)


def _analyze(db, doc_id, number, total, raw=None):
    """Simuliert eine (Neu-)Analyse: Start (save_used_prompts) + Ergebnis speichern."""
    crud.document.save_used_prompts(db, doc_id, f"Prompt {number}", None)
    crud.document.save_extraction(
        db, doc_id,
        {"invoice_number": number, "total_amount": total, "supplier_name": "ACME"},
        [{"product_description": f"Artikel {number}", "quantity": 1, "unit": "Stück",
          "unit_price": total, "total_price": total}],
        raw or json.dumps({"n": number}),
    )


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


def test_first_analysis_creates_no_history(db):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    assert crud.analysis_history.list_for_document(db, doc.id) == []


def test_reanalysis_archives_previous_state_with_prompt(db):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    _analyze(db, doc.id, "R2", 20)

    entries = crud.analysis_history.list_for_document(db, doc.id)
    assert len(entries) == 1
    e = entries[0]
    assert (e.invoice_number, e.total_amount, e.supplier_name) == ("R1", Decimal("10.00"), "ACME")
    assert e.position_count == 1
    snap = crud.analysis_history.parsed_snapshot(e)
    assert snap["meta"]["used_system_prompt"] == "Prompt R1"   # Prompt der ALTEN Analyse
    assert snap["positions"][0]["product_description"] == "Artikel R1"
    # aktueller Stand ist der neue
    db.expire_all()
    assert db.get(type(doc), doc.id).extraction.invoice_number == "R2"


def test_identical_state_is_not_archived_twice(db):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    # Analyse startet, scheitert aber vor dem Speichern → Start zweimal
    crud.document.save_used_prompts(db, doc.id, "P", None)
    crud.document.save_used_prompts(db, doc.id, "P", None)
    assert len(crud.analysis_history.list_for_document(db, doc.id)) == 1


def test_ki_error_state_is_never_archived(db):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    _analyze(db, doc.id, "R2", 20, raw="KI-Fehler: Timeout")     # R1 archiviert, aktuell = Fehler
    _analyze(db, doc.id, "R3", 30)                                 # Fehlerstand wird NICHT archiviert
    nums = [e.invoice_number for e in crud.analysis_history.list_for_document(db, doc.id)]
    assert nums == ["R1"]


def test_history_is_limited_by_setting(db):
    doc = _doc(db)
    s = crud.automation_settings.get_or_create(db)
    s.analysis_history_keep = 2
    db.commit()
    for i in range(1, 6):
        _analyze(db, doc.id, f"R{i}", i * 10)
    nums = [e.invoice_number for e in crud.analysis_history.list_for_document(db, doc.id)]
    assert nums == ["R4", "R3"]      # neueste zuerst, nur 2 behalten


def test_history_off_with_zero(db):
    doc = _doc(db)
    s = crud.automation_settings.get_or_create(db)
    s.analysis_history_keep = 0
    db.commit()
    _analyze(db, doc.id, "R1", 10)
    _analyze(db, doc.id, "R2", 20)
    assert crud.analysis_history.list_for_document(db, doc.id) == []


def test_restore_swaps_states_and_loses_nothing(db):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    _analyze(db, doc.id, "R2", 20)
    old = crud.analysis_history.list_for_document(db, doc.id)[0]

    crud.analysis_history.restore(db, doc.id, old.id)
    db.expire_all()
    d = db.get(type(doc), doc.id)
    assert d.extraction.invoice_number == "R1"
    assert d.extraction.total_amount_brutto == Decimal("10.00")
    assert [p.product_description for p in d.order_positions] == ["Artikel R1"]
    assert d.used_system_prompt == "Prompt R1"
    assert d.status == "done"
    # der bisherige Stand (R2) ist jetzt in der Historie, der wiederhergestellte nicht mehr
    nums = [e.invoice_number for e in crud.analysis_history.list_for_document(db, doc.id)]
    assert nums == ["R2"]
    assert db.query(OrderPosition).filter_by(document_id=doc.id).count() == 1


def test_restore_unknown_entry_raises(db):
    doc = _doc(db)
    with pytest.raises(crud.analysis_history.HistoryError):
        crud.analysis_history.restore(db, doc.id, 999)


def test_history_endpoints(db, client):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    _analyze(db, doc.id, "R2", 20)

    r = client.get(f"/api/documents/{doc.id}/analysis-history")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1 and items[0]["invoice_number"] == "R1"
    assert "snapshot" not in items[0]

    hid = items[0]["id"]
    d = client.get(f"/api/documents/{doc.id}/analysis-history/{hid}").json()
    assert d["extraction"]["invoice_number"] == "R1"
    assert d["order_positions"][0]["product_description"] == "Artikel R1"
    assert d["used_system_prompt"] == "Prompt R1"

    assert client.get(f"/api/documents/{doc.id}/analysis-history/999").status_code == 404
    assert client.get("/api/documents/9999/analysis-history").status_code == 404

    assert client.post(f"/api/documents/{doc.id}/analysis-history/{hid}/restore").status_code == 200
    assert client.get(f"/api/documents/{doc.id}").json()["extraction"]["invoice_number"] == "R1"

    # nach dem Wiederherstellen steht R2 in der Historie → löschen
    new_id = client.get(f"/api/documents/{doc.id}/analysis-history").json()[0]["id"]
    assert client.delete(f"/api/documents/{doc.id}/analysis-history/{new_id}").status_code == 204
    assert client.get(f"/api/documents/{doc.id}/analysis-history").json() == []


def test_restore_blocked_while_processing(db, client):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)
    _analyze(db, doc.id, "R2", 20)
    hid = crud.analysis_history.list_for_document(db, doc.id)[0].id
    crud.document.update_status(db, doc.id, "processing")
    assert client.post(f"/api/documents/{doc.id}/analysis-history/{hid}/restore").status_code == 409


def test_archive_failure_never_breaks_analysis(db, monkeypatch):
    doc = _doc(db)
    _analyze(db, doc.id, "R1", 10)

    def boom(*a, **k):
        raise RuntimeError("Tabelle fehlt")

    monkeypatch.setattr(crud.analysis_history, "_archive", boom)
    _analyze(db, doc.id, "R2", 20)          # darf nicht werfen
    db.expire_all()
    assert db.get(type(doc), doc.id).extraction.invoice_number == "R2"
