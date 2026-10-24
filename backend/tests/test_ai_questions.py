"""Freie KI-Frage an einen Beleg: nur der Wert als Antwort, Beleg wird aktualisiert, Verlauf, Rückgängig, Mehrfachlauf."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import crud
from app.database import get_db
from app.main import app
from app.models.ai_clients import AIClients
from app.models.ai_query import AIQuery, DocumentAnswer
from app.models.import_batch import ImportBatch
from app.services import ai_question_service as svc, ai_service, change_log, pdf_service, workflow_runner

RAW = {"lieferant": {"name": "ACME", "bankverbindung": {"bank_name": "Bank", "iban": "DE00 0000 0000 0000 0000 00"}},
       "rechnungsdaten": {"rechnungsnummer": "R-1", "rechnungsdatum": "2025-01-02"},
       "zahlungsinformationen": {"gesamtbetrag_brutto": 100.0}, "positionen": []}


@pytest.fixture()
def env(db, sqlite_session_factory, monkeypatch, tmp_path):
    for target in ("app.services.ai_question_service.SessionLocal", "app.services.change_log.SessionLocal"):
        monkeypatch.setattr(target, sqlite_session_factory)
    cfg = AIClients(name="KI", ip_address="http://x", port="1", model_name="modell", active=True)
    db.add(cfg)
    db.commit()
    monkeypatch.setattr(workflow_runner, "_db_ai_proxy", lambda cid: type("P", (), {"model_name": "modell"})())
    monkeypatch.setattr(pdf_service, "pdf_to_base64_images", lambda *a, **k: ["data:image/png;base64,AAAA"])

    batch = ImportBatch(company_name="F", year=2025, import_folder_path="/x", storage_folder_path=str(tmp_path), status="done")
    db.add(batch)
    db.commit()

    def make_doc(raw=RAW):
        d = crud.document.create(db, batch.id, "a.pdf", 1)
        w = PdfWriter()
        w.add_blank_page(width=100, height=100)
        with open(Path(tmp_path) / f"{d.id}.pdf", "wb") as fh:
            w.write(fh)
        crud.document.update_after_copy(db, d.id, f"{d.id}.pdf", 1)
        crud.document.save_extraction(db, d.id, {"supplier_name": "ACME", "invoice_number": "R-1"}, [], json.dumps(raw) if raw is not None else "")
        d.status = "done"
        db.commit()
        return d

    class E:
        pass
    e = E()
    e.make_doc, e.answers, e.db = make_doc, [], db

    def fake_ask(images, config, question, hint=""):
        e.last = (question, hint)
        return e.answers.pop(0), None, {}
    monkeypatch.setattr(ai_service, "ask_question", fake_ask)
    return e


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


def test_clean_answer_variants():
    assert svc.clean_answer("  **Antwort: DE02 1203 0000 0000 2020 51** ", "iban") == ("DE02 1203 0000 0000 2020 51", None)
    assert svc.clean_answer("de02120300000000202051", "iban")[0] == "DE02 1203 0000 0000 2020 51"
    assert svc.clean_answer("Die IBAN lautet nicht erkennbar", "iban")[0] is None
    assert svc.clean_answer("NICHT GEFUNDEN")[0] is None
    assert svc.clean_answer("<think>hm</think>\n\"R-4711\"")[0] == "R-4711"
    assert svc.clean_answer("25.03.2025", "date")[0] == "2025-03-25"
    assert svc.clean_answer("1.234,56 €", "number")[0] == "1234.56"
    assert svc.clean_answer("irgendwas", "date")[0] is None


def test_ask_updates_json_and_history_and_can_be_undone(client, env, db):
    d = env.make_doc()
    env.answers = ["DE02 1203 0000 0000 2020 51"]
    r = client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Wie heißt die IBAN?", "field_key": "iban"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["answer"] == "DE02 1203 0000 0000 2020 51" and a["applied"] and a["old_value"] == "DE00 0000 0000 0000 0000 00" and a["change_run_id"]
    assert "IBAN" in env.last[1]
    db.expire_all()
    assert json.loads(d.extraction.raw_response)["lieferant"]["bankverbindung"]["iban"] == "DE02 1203 0000 0000 2020 51"
    assert json.loads(d.extraction.raw_response)["lieferant"]["name"] == "ACME"          # Rest unberührt
    # Verlauf
    h = client.get("/api/ai-questions").json()
    assert h[0]["question"] == "Wie heißt die IBAN?" and h[0]["field_key"] == "iban" and h[0]["use_count"] == 1
    env.answers = ["DE02 1203 0000 0000 2020 51"]
    client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "wie heißt  die IBAN?", "field_key": "iban"})
    assert len(client.get("/api/ai-questions").json()) == 1 and client.get("/api/ai-questions").json()[0]["use_count"] == 2
    assert len(client.get(f"/api/ai-questions/answers?document_id={d.id}").json()) == 2
    # Rückgängig über das Änderungsprotokoll
    change_log.start_undo(a["change_run_id"], background=False)
    db.expire_all()
    assert json.loads(d.extraction.raw_response)["lieferant"]["bankverbindung"]["iban"] == "DE00 0000 0000 0000 0000 00"


def test_ask_updates_column_and_json_for_dates_and_numbers(client, env, db):
    d = env.make_doc()
    env.answers = ["2025-03-25"]
    client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Rechnungsdatum?", "field_key": "invoice_date"})
    env.answers = ["1234.5"]
    client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Brutto?", "field_key": "total_gross"})
    db.expire_all()
    ext = d.extraction
    assert str(ext.invoice_date) == "2025-03-25" and float(ext.total_amount_brutto) == 1234.5
    raw = json.loads(ext.raw_response)
    assert raw["rechnungsdaten"]["rechnungsdatum"] == "2025-03-25" and raw["zahlungsinformationen"]["gesamtbetrag_brutto"] == 1234.5


def test_not_found_or_invalid_changes_nothing(client, env, db):
    d = env.make_doc()
    before = d.extraction.raw_response
    env.answers = ["NICHT GEFUNDEN"]
    a = client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Wie heißt die IBAN?", "field_key": "iban"}).json()
    assert a["answer"] is None and not a["applied"] and "gefunden" in a["note"]
    env.answers = ["kein iban text"]
    a = client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Wie heißt die IBAN?", "field_key": "iban"}).json()
    assert a["answer"] is None and not a["valid"]
    db.expire_all()
    assert d.extraction.raw_response == before


def test_free_question_only_stores_answer_and_apply_false_keeps_document(client, env, db):
    d = env.make_doc()
    before = d.extraction.raw_response
    env.answers = ["Der Beleg ist vom Montag"]
    a = client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Welcher Wochentag?"}).json()
    assert a["answer"] == "Der Beleg ist vom Montag" and not a["applied"]
    env.answers = ["DE02 1203 0000 0000 2020 51"]
    a = client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "IBAN?", "field_key": "iban", "apply": False}).json()
    assert a["answer"] and not a["applied"]
    db.expire_all()
    assert d.extraction.raw_response == before


def test_document_without_extraction_or_json_is_handled(client, env, db):
    plain = env.make_doc()
    db.query(type(plain.extraction)).filter_by(document_id=plain.id).delete()
    db.commit()
    env.answers = ["DE02 1203 0000 0000 2020 51"]
    a = client.post("/api/ai-questions/ask", json={"document_id": plain.id, "question": "IBAN?", "field_key": "iban"}).json()
    assert not a["applied"] and "keine Rechnungsdaten" in a["note"]
    broken = env.make_doc(raw=None)
    broken.extraction.raw_response = "KI-Fehler: HTTP 500"
    db.commit()
    env.answers = ["DE02 1203 0000 0000 2020 51"]
    a = client.post("/api/ai-questions/ask", json={"document_id": broken.id, "question": "IBAN?", "field_key": "iban"}).json()
    assert not a["applied"] and "kein lesbares JSON" in a["note"]


def test_ki_error_is_reported_and_unknown_field_rejected(client, env, monkeypatch):
    d = env.make_doc()
    monkeypatch.setattr(ai_service, "ask_question", lambda *a, **k: ("", "KI-Timeout: x", {}))
    r = client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "IBAN?", "field_key": "iban"})
    assert r.status_code == 502 and "Timeout" in r.json()["detail"]
    assert client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "IBAN?", "field_key": "gibtsnicht"}).status_code == 422


def test_batch_run_processes_documents_sequentially_and_reports(client, env, db):
    docs = [env.make_doc() for _ in range(3)]
    env.answers = ["DE02 1203 0000 0000 2020 51", "NICHT GEFUNDEN", "DE89 3704 0044 0532 0130 00"]
    st = svc.start([d.id for d in docs] + [99999], "IBAN?", "iban", None, True, None, background=False)
    assert st["total"] == 4 and st["processed"] == 4 and st["found"] == 2 and st["not_found"] == 1 and st["errors"] == 1 and st["applied"] == 2
    assert client.get("/api/ai-questions/state").json()["running"] is False


def test_history_delete_and_fields(client, env):
    assert any(f["key"] == "iban" for f in client.get("/api/ai-questions/fields").json())
    d = env.make_doc()
    env.answers = ["x y z"]
    client.post("/api/ai-questions/ask", json={"document_id": d.id, "question": "Was ist das?"})
    qid = client.get("/api/ai-questions").json()[0]["id"]
    assert client.delete(f"/api/ai-questions/{qid}").status_code == 204
    assert client.get("/api/ai-questions").json() == []
    assert len(client.get(f"/api/ai-questions/answers?document_id={d.id}").json()) == 1       # Antworten bleiben am Beleg
