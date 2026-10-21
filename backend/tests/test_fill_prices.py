"""Nachrüstung „Gesamtpreise ergänzen“: nur fehlende Werte aus der KI-Antwort, auch bei vorhandenem Prüfstatus."""
import json

import pytest

from app import crud
from app.models.import_batch import ImportBatch
from app.models.order_position import OrderPosition
from app.services import validation_backfill as vb


@pytest.fixture(autouse=True)
def _session(sqlite_session_factory, monkeypatch):
    monkeypatch.setattr("app.services.validation_backfill.SessionLocal", sqlite_session_factory)
    monkeypatch.setattr("app.services.change_log.SessionLocal", sqlite_session_factory)


@pytest.fixture
def doc(db):
    b = ImportBatch(company_name="F", year=2024, import_folder_path="/x", storage_folder_path="/s", status="done")
    db.add(b)
    db.commit()
    d = crud.document.create(db, b.id, "a.pdf", 1)
    raw = {"positionen": [{"gesamtpreis": "1.234,56 €"}, {"gesamtpreis": 20}, {"gesamtpreis": None}],
           "zahlungsinformationen": {"gesamtbetrag_netto": 1254.56}}
    crud.document.save_extraction(
        db, d.id, {"supplier_name": "L", "validation_status": "ok"},
        [{"product_description": f"P{i}", "quantity": 2, "unit": "Stück", "unit_price": 5} for i in range(3)],
        json.dumps(raw),
    )
    d.status = "done"
    db.commit()
    return d


def test_fills_only_missing_prices(db, doc):
    db.query(OrderPosition).filter(OrderPosition.position_index == 1).update({"total_price": 99})
    db.commit()
    assert vb.count_pending(fill_prices=True) == 1
    vb.start(fill_prices=True, background=False)
    db.expire_all()
    pos = {p.position_index: p.total_price for p in db.query(OrderPosition).all()}
    assert float(pos[0]) == 1234.56
    assert float(pos[1]) == 99.0            # vorhandener Wert bleibt
    assert pos[2] is None                   # nichts in der KI-Antwort → nicht berechnet
    ext = doc.extraction
    db.refresh(ext)
    assert ext.validation_status == "ok" and float(ext.total_amount_netto) == 1254.56
