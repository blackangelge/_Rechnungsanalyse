"""Änderungsprotokoll: Nachrüst-Läufe und manuelle Änderungen werden mit Alt-/Neuwert festgehalten und lassen sich zurücknehmen."""

import json

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.data_change import DataChange, DataChangeRun
from app.models.import_batch import ImportBatch
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition
from app.services import change_log, validation_backfill as vb


@pytest.fixture()
def client(sqlite_session_factory, monkeypatch):
    for target in ("app.services.change_log.SessionLocal", "app.services.validation_backfill.SessionLocal"):
        monkeypatch.setattr(target, sqlite_session_factory)

    def _get_db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def tonne_doc(db):
    """Beleg, dessen Position früher auf Tonnen umgerechnet wurde (Original: 25.500 kg)."""
    b = ImportBatch(company_name="F", year=2025, import_folder_path="/x", storage_folder_path="/s", status="done")
    db.add(b)
    db.commit()
    d = crud.document.create(db, b.id, "a.pdf", 1)
    crud.document.save_extraction(
        db, d.id, {"supplier_name": "L"},
        [{"product_description": "KAS", "quantity": 25.5, "unit": "t", "unit_price": 412, "total_price": 10506}],
        json.dumps({"positionen": [{"menge": 25500, "mengeneinheit": "kg", "einzelpreis": 412, "gesamtpreis": 10506}]}),
    )
    d.status = "done"
    db.commit()
    return d


def _pos(db):
    db.expire_all()
    return db.query(OrderPosition).one()


def test_restore_run_is_logged_and_can_be_undone(client, db, tonne_doc):
    vb.start(restore_originals=True, background=False)
    p = _pos(db)
    assert (float(p.quantity), p.unit) == (25500.0, "kg")
    run = db.query(DataChangeRun).one()
    assert run.kind == "backfill_restore" and run.finished_at is not None and run.changed_rows == 1
    fields = {c.field: (c.old_value, c.new_value) for c in db.query(DataChange).filter_by(run_id=run.id)}
    assert fields["quantity"] == ("25.5", "25500") and fields["unit"] == ("t", "kg")
    assert all(c.document_id == tonne_doc.id for c in db.query(DataChange).all())

    r = client.get("/api/change-log/runs").json()
    assert r[0]["id"] == run.id and r[0]["changed_fields"] >= 2
    ch = client.get(f"/api/change-log/runs/{run.id}/changes")
    assert ch.headers["x-total-count"] == str(len(fields)) and {c["field"] for c in ch.json()} >= {"quantity", "unit"}

    change_log.start_undo(run.id, background=False)
    p = _pos(db)
    assert (float(p.quantity), p.unit) == (25.5, "t")
    db.expire_all()
    assert db.get(DataChangeRun, run.id).undone_at is not None
    assert client.post(f"/api/change-log/runs/{run.id}/undo").status_code == 409          # nicht doppelt


def test_preview_run_writes_neither_data_nor_log(client, db, tonne_doc):
    vb.start(dry_run=True, restore_originals=True, background=False)
    assert (float(_pos(db).quantity), _pos(db).unit) == (25.5, "t")
    assert db.query(DataChangeRun).count() == 0 and db.query(DataChange).count() == 0


def test_undo_skips_values_changed_afterwards(client, db, tonne_doc):
    vb.start(restore_originals=True, background=False)
    run = db.query(DataChangeRun).one()
    client.patch(f"/api/db/tables/order_positions/rows/{_pos(db).id}", json={"changes": {"unit": "Tonnen"}})   # danach manuell geändert
    change_log.start_undo(run.id, background=False)
    p = _pos(db)
    assert p.unit == "Tonnen"                                  # nicht überschrieben
    assert float(p.quantity) == 25.5                           # unveränderte Felder wurden zurückgesetzt
    db.expire_all()
    assert "übersprungen" in db.get(DataChangeRun, run.id).undo_summary
    assert db.query(DataChange).filter_by(run_id=run.id, field="unit").one().status == 2


def test_browser_and_assistant_edits_are_logged_and_undoable(client, db, tonne_doc):
    pid = _pos(db).id
    assert client.patch(f"/api/db/tables/order_positions/rows/{pid}", json={"changes": {"quantity": "30", "unit": "kg"}}).status_code == 200
    run = db.query(DataChangeRun).filter_by(kind="browser").one()
    assert {(c.field, c.old_value, c.new_value) for c in db.query(DataChange).filter_by(run_id=run.id)} == {
        ("quantity", "25.5", "30"), ("unit", "t", "kg")}
    r = client.post("/api/db-assistant/apply", json={"table": "order_positions", "id": pid, "changes": {"unit": "g"}, "reason": "Test"})
    assert r.status_code == 200 and db.query(DataChangeRun).filter_by(kind="assistant").count() == 1
    change_log.start_undo(db.query(DataChangeRun).filter_by(kind="browser").one().id, background=False)
    p = _pos(db)
    assert p.unit == "g"                                        # Einheit wurde danach (Assistent) erneut geändert → übersprungen
    assert float(p.quantity) == 25.5                            # Menge unverändert seit der Browser-Änderung → zurückgesetzt


def test_canon_values():
    from decimal import Decimal
    assert change_log.canon(Decimal("10.5000"), 4) == "10.5" and change_log.canon(25500.0) == "25500"
    assert change_log.canon(Decimal("10898.073"), 2) == "10898.07" and change_log.canon(None) is None
    assert change_log.canon(True) == "true" and change_log.canon("x") == "x"
