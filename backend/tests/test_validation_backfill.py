"""Nachrüstung der Plausibilitätsprüfung für den Altbestand (ohne KI)."""

import json

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition
from app.services import validation_backfill as vb


@pytest.fixture()
def client(sqlite_session_factory, monkeypatch):
    def _get_db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(vb, "SessionLocal", sqlite_session_factory)
    vb._state.clear()
    vb._state["running"] = False
    yield TestClient(app)
    app.dependency_overrides.clear()


def _raw(net, positions):
    return json.dumps({
        "positionen": [{"gesamtpreis": p} for p in positions],
        "zahlungsinformationen": {
            "gesamtbetrag_netto": net, "gesamtbetrag_brutto": round(net * 1.19, 2),
            "umsatzsteuer_zusammenfassung": [
                {"steuersatz": 19.0, "nettobetrag": net, "steuerbetrag": round(net * 0.19, 2)}],
        },
    })


@pytest.fixture()
def legacy(db):
    """Altbestand: Belege ohne Prüfstatus, ohne Positions-Gesamtpreis, ohne Netto/Steuer."""
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/i", storage_folder_path="/s")
    db.add(batch)
    db.commit()
    ids = {}

    def make(key, fields, positions, raw, status="done"):
        d = crud.document.create(db, batch.id, f"{key}.pdf", 1)
        crud.document.update_status(db, d.id, status)
        crud.document.save_extraction(db, d.id, fields, positions, raw)
        ids[key] = d.id

    # ok: 2 × 10 = 20, Netto 20
    make("ok", {"total_amount": 23.8},
         [{"quantity": 2, "unit": "Stück", "unit_price": 10.0, "total_price": 20.0}], _raw(20.0, [20.0]))
    # Einheitenfehler: 500 kg zu 50 €/t → 25 €, KI las 25000
    make("unit", {"total_amount": 41.65},
         [{"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25000.0},
          {"quantity": 1, "unit": "Stück", "unit_price": 10.0, "total_price": 10.0}],
         _raw(35.0, [25000.0, 10.0]))
    # unplausibel, nicht eindeutig korrigierbar
    make("bad", {"total_amount": 10.0},
         [{"quantity": 3, "unit": "Stück", "unit_price": 7.0, "total_price": 99.0}], _raw(99.0, [99.0]))
    # KI-Fehlerantwort → übersprungen
    make("err", {}, [], "KI-Fehler: Timeout")
    # noch in Bearbeitung → nicht angefasst
    make("busy", {"total_amount": 23.8},
         [{"quantity": 2, "unit": "Stück", "unit_price": 10.0, "total_price": 20.0}], _raw(20.0, [20.0]),
         status="processing")
    # Altbestand simulieren: Gesamtpreise, Netto/Steuer und Prüfstatus gibt es dort noch nicht
    db.query(OrderPosition).update({"total_price": None})
    db.query(InvoiceExtraction).update(
        {"validation_status": None, "validation_details": None, "total_amount_netto": None, "total_tax_value": None})
    db.commit()
    return ids


def run(**kw):
    """Lauf synchron ausführen (der Endpunkt startet einen Hintergrund-Thread)."""
    return vb.start(background=False, **kw)


def _ext(db, doc_id):
    db.expire_all()
    return db.query(InvoiceExtraction).filter_by(document_id=doc_id).one()


def test_backfill_fills_prices_checks_and_corrects(db, client, legacy):
    run()
    s = vb.get_state()
    assert s["running"] is False and s["error_count"] == 0
    assert s["processed"] == 4                                  # err zählt mit (übersprungen), busy nicht
    assert s["outcomes"] == {"ok": 1, "corrected": 1, "needs_review": 1, "skipped_no_data": 1}
    assert s["filled_prices"] >= 4

    ok = _ext(db, legacy["ok"])
    assert ok.validation_status == "ok"
    assert float(ok.total_amount_netto) == 20.0 and float(ok.total_tax_value) == 3.8
    assert json.loads(ok.validation_details)["backfill"] is True

    unit = _ext(db, legacy["unit"])
    assert unit.validation_status == "corrected"
    prices = [float(p.total_price) for p in db.query(OrderPosition).filter_by(document_id=legacy["unit"])
              .order_by(OrderPosition.position_index)]
    assert prices == [25.0, 10.0]                               # 25000 → 25 korrigiert
    assert json.loads(unit.validation_details)["corrections"][0]["from"] == 25000.0

    assert _ext(db, legacy["bad"]).validation_status == "needs_review"
    assert _ext(db, legacy["err"]).validation_status is None    # Fehlerantwort bleibt ungeprüft
    assert _ext(db, legacy["busy"]).validation_status is None   # in Bearbeitung → nicht angefasst


def test_dry_run_writes_nothing(db, client, legacy):
    run(dry_run=True)
    s = vb.get_state()
    assert s["dry_run"] is True and s["outcomes"]["corrected"] == 1
    db.expire_all()
    assert db.query(InvoiceExtraction).filter(InvoiceExtraction.validation_status.isnot(None)).count() == 0
    assert db.query(OrderPosition).filter(OrderPosition.total_price.isnot(None)).count() == 0
    assert client.get("/api/validation-backfill").json()["pending"] == 4


def test_without_auto_correct_values_stay(db, client, legacy):
    run(auto_correct=False)
    assert _ext(db, legacy["unit"]).validation_status == "needs_review"
    first = db.query(OrderPosition).filter_by(document_id=legacy["unit"], position_index=0).one()
    assert float(first.total_price) == 25000.0                  # aus der Rohantwort ergänzt, nicht korrigiert


def test_second_run_only_touches_unchecked(db, client, legacy):
    run()
    first = vb.get_state()
    run()
    second = vb.get_state()
    assert first["processed"] == 4 and second["processed"] == 1     # nur der Fehlerbeleg ohne Status
    assert client.get("/api/validation-backfill").json()["pending"] == 1


def test_batch_filter(db, client, legacy):
    other = ImportBatch(company_name="X", year=2026, import_folder_path="/i", storage_folder_path="/s2")
    db.add(other)
    db.commit()
    run(batch_ids=[other.id])
    assert vb.get_state()["processed"] == 0


def test_defective_document_does_not_stop_run(db, client, legacy, monkeypatch):
    real = vb.plausibility_service.validate_invoice
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("kaputt")
        return real(*a, **k)
    monkeypatch.setattr(vb.plausibility_service, "validate_invoice", flaky)
    run()
    s = vb.get_state()
    assert s["error_count"] == 1 and "kaputt" in s["errors"][0] and s["processed"] == 3


def test_cancel_and_conflict(client, legacy):
    vb._state.update({"running": True})
    assert client.post("/api/validation-backfill", json={}).status_code == 409
    assert client.post("/api/validation-backfill/cancel").json() == {"cancelled": True}
    assert vb._cancel.is_set()
    vb._state["running"] = False
    vb._cancel.clear()


def test_requires_login(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "auth_enabled", True)
    assert client.get("/api/validation-backfill").status_code == 401


def test_endpoint_runs_in_background_thread(db, client, legacy):
    import time
    r = client.post("/api/validation-backfill", json={})
    assert r.status_code == 200 and r.json()["total"] == 4
    for _ in range(100):
        status = client.get("/api/validation-backfill").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["running"] is False and status["processed"] == 4 and status["pending"] == 1
