"""Durchsatz-Kennzahlen: Importe dürfen nicht als Analyse zählen; Live-Ansicht je KI-Rechner."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.document import Document
from app.models.import_batch import ImportBatch


@pytest.fixture()
def client(sqlite_session_factory):
    def override():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def batch(db, tmp_path):
    b = ImportBatch(company_name="F", year=2026, import_folder_path="/x", storage_folder_path=str(tmp_path))
    db.add(b)
    db.commit()
    return b


def _stats(client):
    return client.get("/api/logs/ki-stats").json()


def test_import_does_not_count_as_analysis(client, db, batch):
    for i in range(5):                                   # Import: pending → done (wie update_after_copy) — mehrfach, auch Seitenzahl-Update
        d = crud.document.create(db, batch.id, f"{i}.pdf", 1)
        crud.document.update_after_copy(db, d.id, f"{d.id}.pdf", 0)
        d.page_count = 3
        db.commit()
    s = _stats(client)
    assert s["analyzed_last_24h"] == 0 and s["processed_last_60min"] == 0


def test_analysis_end_is_counted_once_per_outcome(client, db, batch):
    ok = crud.document.create(db, batch.id, "ok.pdf", 1)
    bad = crud.document.create(db, batch.id, "bad.pdf", 1)
    waiting = crud.document.create(db, batch.id, "w.pdf", 1)
    for d in (ok, bad, waiting):
        crud.document.update_status(db, d.id, "processing")
    crud.document.update_status(db, ok.id, "done")                       # processing → done (update_status)
    bad_obj = db.get(Document, bad.id)
    bad_obj.status = "error"                                              # direkte Zuweisung (Analyse-Pfade) zählt ebenso
    db.commit()
    crud.document.update_status(db, waiting.id, "waiting_capacity")       # kein Abschluss
    s = _stats(client)
    assert s["analyzed_last_24h"] == 2 and s["processed_last_60min"] == 1


def test_windows_and_unrelated_edits(client, db, batch):
    d = crud.document.create(db, batch.id, "a.pdf", 1)
    crud.document.update_status(db, d.id, "processing")
    crud.document.update_status(db, d.id, "done")
    obj = db.get(Document, d.id)
    obj.analyzed_at = datetime.now(timezone.utc) - timedelta(hours=2)    # vor 2 Stunden analysiert
    db.commit()
    assert _stats(client) | {} and _stats(client)["analyzed_last_24h"] == 1 and _stats(client)["processed_last_60min"] == 0
    crud.document.update_comment(db, d.id, "Kommentar ändert updated_at, aber nicht analyzed_at")
    crud.document.update_after_copy(db, d.id, "x.pdf", 2)                 # pending-unabhängiger Import-Aufruf
    obj = db.get(Document, d.id)
    assert obj.analyzed_at is not None and (datetime.now(timezone.utc) - obj.analyzed_at.replace(tzinfo=timezone.utc)) > timedelta(hours=1, minutes=59)
    obj.analyzed_at = datetime.now(timezone.utc) - timedelta(hours=30)
    db.commit()
    assert _stats(client)["analyzed_last_24h"] == 0


# ─── Live-Ansicht ────────────────────────────────────────────────────────────

def _worker_stub(payload):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_live_view_shows_running_documents_per_ai_server(client, db, batch, monkeypatch):
    from app.config import settings
    from app.models.ai_clients import AIClients
    ai1 = AIClients(name="Rechner A", ip_address="10.0.0.1", port="1234", model_name="qwen", active=True, parallel_request=2)
    ai2 = AIClients(name="Rechner B", ip_address="10.0.0.2", port="1234", model_name="llama", active=True, parallel_request=1)
    db.add_all([ai1, ai2])
    d = crud.document.create(db, batch.id, "rechnung_17.pdf", 1)
    d.page_count = 4
    db.commit()
    srv = _worker_stub({"now": 1000.0, "worker_count": 3, "queue_size": 7, "paused": False, "servers": [
        {"id": ai1.id, "name": "Rechner A", "active": True, "is_down": False, "parallel_request": 2, "url": "http://10.0.0.1:1234",
         "running": [{"task_id": 11, "document_id": d.id, "started_at": 900.0, "worker_id": "w1", "elapsed_seconds": 100.0}]},
        {"id": ai2.id, "name": "Rechner B", "active": True, "is_down": False, "parallel_request": 1, "url": "http://10.0.0.2:1234", "running": []}]})
    monkeypatch.setattr(settings, "worker_api_url", f"http://127.0.0.1:{srv.server_port}")
    db.execute(__import__("sqlalchemy").text("INSERT INTO workflow_tasks (workflow_id, payload, status, attempts, max_attempts) VALUES ('a','{}','pending',0,3),('b','{}','in_progress',0,3),('c','{}','in_progress',0,3)"))
    db.commit()
    r = client.get("/api/logs/ki-live").json()
    srv.shutdown()
    assert r["worker_online"] and r["queue_pending"] == 1 and r["in_progress"] == 2 and r["running_total"] == 1 and r["waiting_for_slot"] == 1
    a, b = r["servers"]
    assert a["name"] == "Rechner A" and a["busy"] == 1 and a["parallel_request"] == 2
    run = a["running"][0]
    assert run["filename"] == "rechnung_17.pdf" and run["pages"] == 4 and run["company"] == "F" and run["elapsed_seconds"] == 100.0
    assert b["busy"] == 0 and b["running"] == []


def test_live_view_without_worker_still_lists_ai_servers(client, db, monkeypatch):
    from app.config import settings
    from app.models.ai_clients import AIClients
    db.add(AIClients(name="Rechner A", ip_address="10.0.0.1", port="1234", model_name="qwen", active=True, parallel_request=2))
    db.commit()
    monkeypatch.setattr(settings, "worker_api_url", "http://127.0.0.1:1")
    r = client.get("/api/logs/ki-live").json()
    assert r["worker_online"] is False and r["servers"][0]["running"] == [] and r["waiting_for_slot"] == 0


def test_worker_status_lists_running_tasks_per_server():
    import time
    from types import SimpleNamespace
    from fastapi import FastAPI
    from app.worker.routers import status
    from app.worker.worker import AIServer

    s = AIServer(id=1, name="A", ip_address="10.0.0.1", port="1234", parallel_request=2, active=True, timeout_at=None)
    s.running[5] = {"task_id": 5, "document_id": 42, "started_at": time.time() - 30, "worker_id": "w1"}
    s.running[6] = {"task_id": 6, "document_id": 43, "started_at": time.time() - 10, "worker_id": "w2"}
    app_ = FastAPI()
    app_.include_router(status.router)
    app_.state.dispatcher = SimpleNamespace(workers=[1, 2], queue=SimpleNamespace(qsize=lambda: 3), paused=False, pool=SimpleNamespace(_servers={1: s}))
    out = TestClient(app_).get("/status").json()
    run = out["servers"][0]["running"]
    assert [r["document_id"] for r in run] == [42, 43]                        # älteste zuerst
    assert 29 <= run[0]["elapsed_seconds"] <= 33 and out["now"] > 0 and out["queue_size"] == 3
