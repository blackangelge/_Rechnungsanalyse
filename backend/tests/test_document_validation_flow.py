"""Integrationstests: Prüfung speichern, erneut prüfen, zweite KI-Abfrage, Endpunkte."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch
from app.routers import documents as docs_router


def _make_doc(db):
    batch = ImportBatch(
        company_name="Test GmbH", year=2026,
        import_folder_path="/import/x", storage_folder_path="/storage/Test_GmbH_2026",
    )
    db.add(batch)
    db.commit()
    doc = crud.document.create(db, batch.id, "r.pdf", 1)
    return doc


def _raw(net):
    return json.dumps({
        "positionen": [{}, {}],
        "zahlungsinformationen": {
            "gesamtbetrag_netto": net, "gesamtbetrag_brutto": round(net * 1.19, 2),
            "umsatzsteuer_zusammenfassung": [
                {"steuersatz": 19.0, "nettobetrag": net, "steuerbetrag": round(net * 0.19, 2)}
            ],
        },
    })


POSITIONS_WRONG = [
    {"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25000.0},
    {"quantity": 1, "unit": "Stück", "unit_price": 10.0, "total_price": 10.0},
]


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


def test_revalidate_corrects_and_keeps_history(db, client):
    doc = _make_doc(db)
    crud.document.save_extraction(
        db, doc.id, {"total_amount": 41.65}, [dict(p) for p in POSITIONS_WRONG], _raw(35.0),
    )
    r = client.post(f"/api/documents/{doc.id}/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["validation_status"] == "corrected"
    assert body["order_positions"][0]["total_price"] == "25.00" or float(body["order_positions"][0]["total_price"]) == 25.0
    assert body["validation"]["corrections"][0]["from"] == 25000.0

    # zweiter Lauf: Daten jetzt stimmig, Korrekturhistorie bleibt erhalten
    r2 = client.post(f"/api/documents/{doc.id}/validate").json()
    assert r2["validation_status"] == "corrected"
    assert len(r2["validation"]["corrections"]) == 1


def test_confirm_sets_manual_ok_and_filter(db, client):
    doc = _make_doc(db)
    crud.document.save_extraction(
        db, doc.id, {"total_amount": 10.0},
        [{"quantity": 3, "unit": "Stück", "unit_price": 7.0, "total_price": 99.0}], _raw(99.0),
    )
    client.post(f"/api/documents/{doc.id}/validate")
    flagged = client.get("/api/documents", params={"validation_status": "needs_review"}).json()
    assert [d["id"] for d in flagged] == [doc.id]
    assert flagged[0]["validation_status"] == "needs_review"

    done = client.post(f"/api/documents/{doc.id}/validation/confirm").json()
    assert done["validation_status"] == "manual_ok"
    assert client.get("/api/documents", params={"validation_status": "needs_review"}).json() == []


def test_validate_without_extraction_is_400(db, client):
    doc = _make_doc(db)
    assert client.post(f"/api/documents/{doc.id}/validate").status_code == 400
    assert client.post("/api/documents/9999/validate").status_code == 404


def _run_pass(monkeypatch, db, recheck_result, fields, positions, raw):
    doc = _make_doc(db)
    monkeypatch.setattr(
        docs_router.ai_service, "recheck_invoice_data",
        lambda images, cfg, base, issues, prev: recheck_result,
    )
    return doc, asyncio.run(docs_router._plausibility_pass(
        doc.id, ["img"], object(), None, fields, positions, raw, {"input_tokens": 1}
    ))


def test_second_ai_pass_fixes_and_marks_ai_corrected(monkeypatch, db):
    bad = [dict(p, total_price=99.0) for p in POSITIONS_WRONG]      # nicht automatisch lösbar
    good = [
        {"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25.0},
        {"quantity": 1, "unit": "Stück", "unit_price": 10.0, "total_price": 10.0},
    ]
    recheck = ({"total_amount": 41.65}, good, _raw(35.0), {"input_tokens": 5}, "PROMPT")
    doc, (fields, positions, raw, stats, validation) = _run_pass(
        monkeypatch, db, recheck, {"total_amount": 41.65}, bad, _raw(35.0))
    assert validation["status"] == "ai_corrected"
    assert positions is good
    assert stats["input_tokens"] == 6
    assert validation["recheck"]["outcome"] == "corrected"
    db.refresh(doc)
    assert doc.used_recheck_prompt == "PROMPT"


def test_second_ai_pass_still_bad_marks_needs_review_and_keeps_original(monkeypatch, db):
    bad = [dict(p, total_price=99.0) for p in POSITIONS_WRONG]
    recheck = ({"total_amount": 41.65}, [dict(p) for p in bad], _raw(35.0), {}, "PROMPT")
    _, (fields, positions, raw, stats, validation) = _run_pass(
        monkeypatch, db, recheck, {"total_amount": 41.65}, bad, _raw(35.0))
    assert validation["status"] == "needs_review"
    assert positions is bad
    assert validation["recheck"]["outcome"] == "still_inconsistent"


def test_second_ai_pass_ki_error_marks_needs_review(monkeypatch, db):
    bad = [dict(p, total_price=99.0) for p in POSITIONS_WRONG]
    recheck = ({}, [], "KI-Fehler: HTTP 500", {}, "PROMPT")
    _, (_, positions, _, _, validation) = _run_pass(
        monkeypatch, db, recheck, {"total_amount": 41.65}, bad, _raw(35.0))
    assert validation["status"] == "needs_review"
    assert validation["recheck"]["outcome"] == "failed"
    assert positions is bad


def test_no_ai_call_when_rules_are_enough(monkeypatch, db):
    def boom(*a, **k):
        raise AssertionError("zweite KI-Abfrage darf nicht laufen")
    monkeypatch.setattr(docs_router.ai_service, "recheck_invoice_data", boom)
    pos = [dict(p) for p in POSITIONS_WRONG]
    _, _, _, _, validation = asyncio.run(docs_router._plausibility_pass(
        1, ["img"], object(), None, {"total_amount": 41.65}, pos, _raw(35.0), {}))
    assert validation["status"] == "corrected" and pos[0]["total_price"] == 25.0


def test_ki_error_response_is_not_validated(monkeypatch, db):
    out = asyncio.run(docs_router._plausibility_pass(
        1, [], object(), None, {}, [], "KI-Fehler: HTTP 500", {}))
    assert out[4] is None


def test_db_analyze_write_persists_validation_and_prompts(db, sqlite_session_factory):
    doc = _make_doc(db)
    crud.document.save_used_prompts(db, doc.id, "SYSTEM", "TYP")
    validation = {"status": "corrected", "issues": [], "corrections": [{"position": 0}],
                  "checks_run": 1}
    docs_router._db_analyze_write(
        doc.id, "r.pdf", doc.batch_id, "modell", 1,
        {"total_amount": 41.65, "supplier_name": "ACME"},
        [dict(p) for p in POSITIONS_WRONG], _raw(35.0), {}, None, validation,
    )
    db.expire_all()
    d = crud.document.get_by_id_with_details(db, doc.id)
    assert d.status == "done"
    assert d.validation_status == "corrected"
    assert d.validation["corrections"] == [{"position": 0}]
    assert (d.used_system_prompt, d.used_doc_type_prompt) == ("SYSTEM", "TYP")


# ─── Durchrechnung: korrigiertes Brutto landet in der Datenbank ───────────────

TWO_RATES_RAW = json.dumps({
    "positionen": [{"steuersatz": 19.0}, {"steuersatz": 7.0}],
    "zahlungsinformationen": {
        "gesamtbetrag_netto": 150.0, "gesamtbetrag_brutto": 172.5,
        "umsatzsteuer_zusammenfassung": [
            {"steuersatz": 19.0, "nettobetrag": 100.0, "steuerbetrag": 19.0},
            {"steuersatz": 7.0, "nettobetrag": 50.0, "steuerbetrag": 3.5},
        ],
    },
})
TWO_POS = [
    {"quantity": 1, "unit": "Stück", "unit_price": 100.0, "total_price": 100.0},
    {"quantity": 1, "unit": "Stück", "unit_price": 50.0, "total_price": 50.0},
]


def test_revalidate_persists_corrected_gross_and_keeps_history(db, client):
    doc = _make_doc(db)
    crud.document.save_extraction(db, doc.id, {"total_amount": 127.5}, [dict(p) for p in TWO_POS], TWO_RATES_RAW)
    body = client.post(f"/api/documents/{doc.id}/validate").json()
    assert body["validation_status"] == "corrected"
    assert float(body["extraction"]["total_amount_brutto"]) == 172.5
    corr = body["validation"]["corrections"]
    assert corr == [{"field": "total_amount", "from": 127.5, "to": 172.5, "method": "net_plus_tax"}]
    assert [r["status"] for r in body["validation"]["calc"]].count("warn") == 0
    # zweiter Lauf: Daten stimmen jetzt, Korrekturhistorie bleibt erhalten
    again = client.post(f"/api/documents/{doc.id}/validate").json()
    assert again["validation_status"] == "corrected" and len(again["validation"]["corrections"]) == 1
    assert float(again["extraction"]["total_amount_brutto"]) == 172.5


def test_revalidate_derives_missing_gross(db, client):
    doc = _make_doc(db)
    crud.document.save_extraction(db, doc.id, {"total_amount": None}, [dict(p) for p in TWO_POS], TWO_RATES_RAW)
    body = client.post(f"/api/documents/{doc.id}/validate").json()
    assert float(body["extraction"]["total_amount_brutto"]) == 172.5
    assert body["validation"]["issues"][0]["code"] == "total_derived"


def test_analysis_pass_corrects_gross_without_second_ai_call(monkeypatch, db):
    def boom(*a, **k):
        raise AssertionError("zweite KI-Abfrage darf nicht laufen")
    monkeypatch.setattr(docs_router.ai_service, "recheck_invoice_data", boom)
    doc = _make_doc(db)
    fields = {"total_amount": 127.5, "supplier_name": "ACME"}
    f, p, raw, stats, validation = asyncio.run(docs_router._plausibility_pass(
        doc.id, ["img"], object(), None, fields, [dict(x) for x in TWO_POS], TWO_RATES_RAW, {}))
    assert validation["status"] == "corrected" and f["total_amount"] == 172.5
    docs_router._db_analyze_write(doc.id, "r.pdf", doc.batch_id, "modell", 1, f, p, raw, None, None, validation)
    db.expire_all()
    d = crud.document.get_by_id_with_details(db, doc.id)
    assert float(d.extraction.total_amount_brutto) == 172.5 and d.validation_status == "corrected"
