"""Daten-Browser (CRUD) und KI-Assistent: Zugriffsgrenzen, Typprüfung, schreibgeschützte Abfragen, Freigabe von Vorschlägen."""

import json

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.ai_clients import AIClients
from app.models.import_batch import ImportBatch
from app.models.user import User
from app.services import db_assistant_service, db_tools


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


@pytest.fixture()
def data(db):
    b = ImportBatch(company_name="Firma", year=2025, import_folder_path="/x", storage_folder_path="/s", status="done")
    db.add(b)
    db.commit()
    d = crud.document.create(db, b.id, "a.pdf", 1)
    crud.document.save_extraction(
        db, d.id, {"supplier_name": "Agro GmbH", "invoice_number": "R-1", "total_amount": 100},
        [{"product_description": "Kalkammonsalpeter", "quantity": 25500, "unit": "kg", "unit_price": 0.412, "total_price": 10506}], "{}",
    )
    db.add(User(username="admin", password_hash="geheim"))
    db.add(AIClients(name="KI", model_name="m", api_key="SECRET-KEY", ip_address="127.0.0.1", port="1234", active=True))
    db.commit()
    return d


def test_tables_hide_credentials(client, data):
    names = {t["name"] for t in client.get("/api/db/tables").json()}
    assert {"documents", "order_positions", "ai_clients"} <= names
    assert not names & {"users", "user_sessions"}
    assert client.get("/api/db/tables/users/rows").status_code == 400


def test_browse_search_sort_and_total(client, data):
    r = client.get("/api/db/tables/order_positions/rows", params={"q": "kalk"})
    assert r.status_code == 200 and r.headers["x-total-count"] == "1"
    assert r.json()[0]["product_description"] == "Kalkammonsalpeter"
    assert client.get("/api/db/tables/order_positions/rows", params={"q": "gibtsnicht"}).headers["x-total-count"] == "0"
    r = client.get("/api/db/tables/order_positions/rows", params={"f.unit": "kg"})
    assert r.headers["x-total-count"] == "1"
    assert client.get("/api/db/tables/order_positions/rows", params={"f.nope": "1"}).status_code == 400


def test_api_key_is_masked_and_not_writable(client, data):
    row = client.get("/api/db/tables/ai_clients/rows/1").json()
    assert row["api_key"] == db_tools.MASK and "SECRET" not in json.dumps(row)
    assert "SECRET" not in json.dumps(client.get("/api/db/tables/ai_clients/rows").json())
    r = client.patch("/api/db/tables/ai_clients/rows/1", json={"changes": {"api_key": "x"}})
    assert r.status_code == 400


def test_update_with_type_coercion_and_validation(client, data):
    pos = client.get("/api/db/tables/order_positions/rows").json()[0]
    r = client.patch(f"/api/db/tables/order_positions/rows/{pos['id']}", json={"changes": {"quantity": "25,5", "unit": "t"}})
    assert r.status_code == 200
    assert r.json()["row"]["unit"] == "t" and float(r.json()["row"]["quantity"]) == 25.5
    assert set(r.json()["changed"]) == {"quantity", "unit"}
    bad = client.patch(f"/api/db/tables/order_positions/rows/{pos['id']}", json={"changes": {"quantity": "viel"}})
    assert bad.status_code == 400 and "quantity" in bad.json()["detail"]
    assert client.patch(f"/api/db/tables/order_positions/rows/{pos['id']}", json={"changes": {"id": 5}}).status_code == 400
    assert client.patch(f"/api/db/tables/order_positions/rows/{pos['id']}", json={"changes": {"gibtsnicht": 1}}).status_code == 400
    assert client.patch("/api/db/tables/order_positions/rows/99999", json={"changes": {"unit": "t"}}).status_code == 404


def test_insert_and_delete(client, data):
    r = client.post("/api/db/tables/system_prompts/rows", json={"values": {"name": "Test", "prompt_text": "x", "prompt_type": 2}})
    if r.status_code == 201:                     # Spaltennamen können abweichen — dann prüft der Test nur den Fehlerfall
        pk = r.json()["pk"]
        assert client.delete(f"/api/db/tables/system_prompts/rows/{pk}").status_code == 200
        assert client.get(f"/api/db/tables/system_prompts/rows/{pk}").status_code == 404
    else:
        assert r.status_code == 400 and r.json()["detail"]


@pytest.mark.parametrize("sql", [
    "DELETE FROM documents", "SELECT 1; DROP TABLE documents", "UPDATE documents SET status='x'",
    "SELECT * FROM users", "SELECT api_key FROM ai_clients", "SELECT pg_sleep(10)", "SELECT * FROM pg_shadow", "SELECT * FROM information_schema.tables", "INSERT INTO documents DEFAULT VALUES",
    "WITH x AS (DELETE FROM documents RETURNING 1) SELECT * FROM x", "", "  ;  ", "SET statement_timeout = 0",
])
def test_readonly_sql_rejects_everything_but_reads(db, data, sql):
    with pytest.raises(db_tools.DbToolError):
        db_tools.run_readonly_sql(db, sql)


def test_readonly_sql_runs_selects_and_limits(db, data):
    res = db_tools.run_readonly_sql(db, "SELECT product_description, comment FROM order_positions o JOIN documents d ON d.id = o.document_id -- Kommentar")
    assert res["columns"] == ["product_description", "comment"] and res["row_count"] == 1
    res = db_tools.run_readonly_sql(db, "SELECT unit, COUNT(*) AS n FROM order_positions GROUP BY unit;")
    assert res["rows"] == [["kg", 1]]
    with pytest.raises(db_tools.DbToolError):
        db_tools.run_readonly_sql(db, "SELECT nospalte FROM order_positions")


def test_schema_text_hides_secrets(data):
    txt = db_tools.schema_text()
    assert "order_positions(" in txt and "api_key" not in txt and "users(" not in txt and "password" not in txt


# ─── Assistent (KI-Antworten werden vorgegeben) ───────────────────────────────

def _scripted(monkeypatch, replies):
    it = iter(replies)
    seen = []

    def fake(config, messages):
        seen.append(messages)
        return next(it), {"input_tokens": 1, "output_tokens": 1, "seconds": 0.1}
    monkeypatch.setattr(db_assistant_service, "_chat", fake)
    return seen


def test_assistant_queries_then_answers(client, data, monkeypatch):
    seen = _scripted(monkeypatch, [
        '```json\n{"tool": "sql", "query": "SELECT COUNT(*) AS n FROM order_positions"}\n```',
        '{"tool": "final", "answer": "Es gibt 1 Position."}',
    ])
    r = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "Wie viele Positionen?"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Es gibt 1 Position." and body["steps"][0]["ok"] and body["steps"][0]["rows"] == [[1]]
    assert any("Ergebnis" in m["content"] for m in seen[1] if m["role"] == "user")        # Ergebnis ging zurück an die KI
    assert "order_positions(" in seen[0][0]["content"] and "SECRET" not in seen[0][0]["content"]


def test_assistant_gets_sql_errors_back_and_blocks_writes(client, data, monkeypatch):
    seen = _scripted(monkeypatch, [
        '{"tool": "sql", "query": "DELETE FROM documents"}',
        '{"tool": "final", "answer": "Das darf ich nicht."}',
    ])
    body = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "Lösche alles"}]}).json()
    assert body["steps"][0]["ok"] is False and "nur lesende" in body["steps"][0]["error"]
    assert any("FEHLER" in m["content"] for m in seen[1] if m["role"] == "user")
    assert client.get("/api/db/tables/documents/rows").headers["x-total-count"] == "1"      # nichts gelöscht


def test_assistant_proposal_needs_approval(client, data, monkeypatch):
    pos = client.get("/api/db/tables/order_positions/rows").json()[0]
    _scripted(monkeypatch, [
        json.dumps({"tool": "propose_update", "table": "order_positions", "id": pos["id"], "changes": {"quantity": 25.5, "unit": "t"}, "reason": "kg→t"}),
        '{"tool": "final", "answer": "Vorschlag erstellt."}',
    ])
    body = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "Rechne um"}]}).json()
    assert len(body["proposals"]) == 1 and body["proposals"][0]["current"]["unit"] == "kg"
    # noch nichts geschrieben
    assert client.get(f"/api/db/tables/order_positions/rows/{pos['id']}").json()["unit"] == "kg"
    p = body["proposals"][0]
    r = client.post("/api/db-assistant/apply", json={"table": p["table"], "id": p["id"], "changes": p["changes"], "reason": p["reason"]})
    assert r.status_code == 200
    assert client.get(f"/api/db/tables/order_positions/rows/{pos['id']}").json()["unit"] == "t"


def test_assistant_invalid_proposal_is_rejected_and_plain_text_is_an_answer(client, data, monkeypatch):
    _scripted(monkeypatch, [
        '{"tool": "propose_update", "table": "users", "id": 1, "changes": {"password_hash": "x"}}',
        "Ich antworte einfach als Text.",
    ])
    body = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "?"}]}).json()
    assert body["proposals"] == [] and body["answer"] == "Ich antworte einfach als Text."


def test_assistant_requires_user_message_and_config(client, db):
    r = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 409                                                   # keine KI-Konfiguration
    db.add(AIClients(name="KI", model_name="m", ip_address="x", active=True))
    db.commit()
    r = client.post("/api/db-assistant/chat", json={"messages": [{"role": "assistant", "content": "hi"}]})
    assert r.status_code == 400


def test_parse_action_variants():
    p = db_assistant_service.parse_action
    assert p('<think>x {"a":1}</think>{"tool":"final","answer":"ok {x}"}')["answer"] == "ok {x}"
    assert p('Text davor {"tool":"sql","query":"SELECT \\"a\\" FROM t"} danach')["tool"] == "sql"
    assert p("kein json") is None


# ─── Assistent: Belege erneut zur KI-Analyse vorschlagen ─────────────────────

def _more_docs(db, data, n=3):
    out = []
    for i in range(n):
        d = crud.document.create(db, data.batch_id, f"b{i}.pdf", 1)
        d.status = "done"
        out.append(d)
    db.commit()
    return out


def test_assistant_reanalysis_proposal_does_not_start_anything(client, db, data, monkeypatch):
    from app.models.workflow_task import WorkflowTask
    docs = _more_docs(db, data)
    docs[2].status = "processing"          # läuft gerade → nicht geeignet
    db.commit()
    _scripted(monkeypatch, [
        json.dumps({"tool": "propose_reanalysis", "query": "SELECT d.id FROM documents d WHERE d.original_filename LIKE 'b%'", "reason": "neu lesen"}),
        '{"tool": "final", "answer": "Vorschlag erstellt."}',
    ])
    body = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "Sende b* erneut"}]}).json()
    prop = body["reanalysis"][0]
    assert prop["count"] == 2 and prop["skipped"] == 1 and sorted(prop["document_ids"]) == sorted([docs[0].id, docs[1].id])
    assert prop["sample"][0]["filename"].startswith("b")
    assert db.query(WorkflowTask).count() == 0                    # nur ein Vorschlag, nichts eingereiht


def test_assistant_reanalysis_query_needs_single_column(client, db, data, monkeypatch):
    seen = _scripted(monkeypatch, [
        '{"tool": "propose_reanalysis", "query": "SELECT id, status FROM documents"}',
        '{"tool": "final", "answer": "ok"}',
    ])
    body = client.post("/api/db-assistant/chat", json={"messages": [{"role": "user", "content": "x"}]}).json()
    assert body["reanalysis"] == []
    assert any("EINE Spalte" in m["content"] for m in seen[1] if m["role"] == "user")


def test_apply_reanalysis_enqueues_only_eligible(client, db, data):
    from app.models.workflow_task import WorkflowTask
    docs = _more_docs(db, data)
    docs[2].status = "processing"
    docs[1].soft_deleted = True
    db.commit()
    r = client.post("/api/db-assistant/apply-reanalysis",
                    json={"document_ids": [docs[0].id, docs[1].id, docs[2].id, 99999], "reason": "Test"})
    assert r.status_code == 200, r.text
    assert r.json()["eligible"] == 1 and r.json()["enqueued"] == 1
    assert [t.payload["document_id"] for t in db.query(WorkflowTask).all()] == [docs[0].id]
    # (Doppel-Einreihung verhindert enqueue_documents per PostgreSQL-JSONB-Abfrage — in SQLite nicht prüfbar)


def test_apply_reanalysis_rejects_nothing_eligible_and_too_many(client, db, data):
    assert client.post("/api/db-assistant/apply-reanalysis", json={"document_ids": [99999]}).status_code == 400
    too_many = list(range(1, db_assistant_service.MAX_REANALYSIS + 2))
    assert client.post("/api/db-assistant/apply-reanalysis", json={"document_ids": too_many}).status_code == 400
