"""Workflow-Knoten „Schema-Extraktion“ end-to-end (Stub-KI, echte PDFs, SQLite) und die Workflow-Pflicht bei Import/Ordner-Sync."""

import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.ai_clients import AIClients
from app.models.document_token_count import DocumentTokenCount
from app.models.import_batch import ImportBatch
from app.models.schema_def import ExtractedRecord
from app.models.system_prompt import SystemPrompt
from app.models.workflow_task import WorkflowTask
from app.routers import documents as docs_router
from app.services import schema_models, workflow_service
from app.services.schema_seed import seed_initial_schema
from app.services.units_seed import seed_default_units
from tests.test_schema_extraction import SAMPLE, _Stub
from tests.test_workflow_flow import _write_pdf


def _tpl(key):
    return copy.deepcopy(next(t for t in workflow_service.templates() if t["key"] == key)["graph"])


@pytest.fixture()
def env(db, tmp_path):
    stub = _Stub(SAMPLE)
    port = stub.server.server_port
    cfg = AIClients(name="vLLM", ip_address="http://127.0.0.1", port=str(port), model_name="qwen", active=True)
    db.add(cfg)
    db.commit()
    seed_default_units(db)
    version = seed_initial_schema(db)
    db.commit()
    schema_models.clear_cache()

    class E:
        pass
    e = E()
    e.db, e.cfg_id, e.stub, e.version = db, cfg.id, stub, version

    def make_doc(pages=2):
        batch = ImportBatch(company_name="Test GmbH", year=2026, import_folder_path="/x", storage_folder_path=str(tmp_path / "Test GmbH_2026"))
        db.add(batch)
        db.commit()
        doc = crud.document.create(db, batch.id, "r.pdf", 1)
        _write_pdf(tmp_path / "Test GmbH_2026" / f"{doc.id}.pdf", pages)
        crud.document.update_after_copy(db, doc.id, f"{doc.id}.pdf", pages)
        db.refresh(doc)
        return doc
    e.make_doc = make_doc
    yield e
    stub.close()


def _run(env, doc, workflow_id):
    asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, workflow_id))
    env.db.expire_all()
    return env.db.get(type(doc), doc.id)


def test_schema_workflow_saves_records_and_finishes_document(env):
    wf = crud.workflow.create(env.db, "Schema", None, _tpl("schema"))
    doc = _run(env, env.make_doc(), wf.id)
    assert doc.status == "done" and doc.page_count == 2 and doc.used_workflow_name == "Schema"
    root = env.db.query(ExtractedRecord).filter_by(document_id=doc.id, parent_record_id=None).one()
    assert root.data["rechnungsnummer"] == "RE-2024/0815" and root.data["seitenanzahl"] == 2
    assert env.db.query(ExtractedRecord).filter_by(document_id=doc.id).count() == 5
    run = doc.workflow_run
    assert [t["type"] for t in run] == ["start", "schema_extract", "end", "saved"]
    node = run[1]
    assert node["status"] == "ok" and "Schema v1" in node["detail"]
    assert node["io"]["response"] and "Schema-Version 1" in node["io"]["notes"][0]
    assert env.db.query(DocumentTokenCount).filter_by(document_id=doc.id).one().input_token_count == 1000
    body = env.stub.requests[0]["body"]
    assert body["response_format"]["type"] == "json_schema" and len(body["messages"][1]["content"]) == 3     # Text + 2 Seiten
    assert doc.extraction is None                      # kein Eintrag in den alten Tabellen


def test_trimmed_schema_workflow_sends_only_first_page(env):
    wf = crud.workflow.create(env.db, "Schema gekürzt", None, _tpl("schema_trimmed"))
    doc = _run(env, env.make_doc(pages=5), wf.id)
    assert doc.status == "done" and doc.page_count == 1
    assert len(env.stub.requests[0]["body"]["messages"][1]["content"]) == 2


def test_extra_instruction_prompt_is_appended(env):
    sp = SystemPrompt(name="Zusatz", content="Beträge sind in CHF.", type=2)
    env.db.add(sp)
    env.db.commit()
    g = _tpl("schema")
    g["nodes"][1]["data"]["config"] = {"system_prompt_id": sp.id}
    wf = crud.workflow.create(env.db, "Mit Zusatz", None, g)
    _run(env, env.make_doc(), wf.id)
    assert env.stub.requests[0]["body"]["messages"][0]["content"].endswith("Beträge sind in CHF.")


def test_schema_violation_sets_document_error_and_keeps_old_records(env):
    wf = crud.workflow.create(env.db, "Schema", None, _tpl("schema"))
    doc = _run(env, env.make_doc(), wf.id)
    env.stub.content = {"unsinn": 1}
    doc = _run(env, doc, wf.id)
    assert doc.status == "error" and "entspricht nicht dem Schema" in (doc.raw_response or "")
    assert env.db.query(ExtractedRecord).filter_by(document_id=doc.id).count() == 5


def test_unreachable_ai_keeps_document_for_retry(env):
    wf = crud.workflow.create(env.db, "Schema", None, _tpl("schema"))
    doc = env.make_doc()
    crud.document.update_status(env.db, doc.id, "processing")
    env.stub.close()
    result = asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, wf.id))
    assert result == "ai_unavailable"
    env.db.expire_all()
    assert env.db.get(type(doc), doc.id).status == "processing"


def test_no_active_schema_gives_clear_error(env):
    from app.models.schema_def import SchemaVersion
    env.db.query(SchemaVersion).update({"status": "archived"})
    env.db.commit()
    wf = crud.workflow.create(env.db, "Schema", None, _tpl("schema"))
    doc = _run(env, env.make_doc(), wf.id)
    assert doc.status == "error" and "Kein aktives Schema" in (doc.raw_response or "")


def test_graph_validation_knows_schema_node(env):
    rep = workflow_service.validate_graph(_tpl("schema"))
    assert rep.ok and not any("keine Rechnungsextraktion" in w for w in rep.warnings)
    g = _tpl("schema")
    g["nodes"][1]["data"]["config"] = {"unbekannt": 1}
    parsed = workflow_service.validate_graph(g)
    assert parsed is not None


# ─── Workflow-Pflicht bei Import und Ordner-Sync ──────────────────────────────

@pytest.fixture()
def client(sqlite_session_factory):
    def _db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_import_requires_workflow_for_analysis_and_folder_sync(client, db, tmp_path, monkeypatch):
    imp = tmp_path / "import"
    imp.mkdir()
    (imp / "a.pdf").write_bytes(b"%PDF-1.4")
    from app.config import settings
    monkeypatch.setattr(settings, "import_base_path", str(imp))
    base = {"company_name": "T", "year": 2026}
    r = client.post("/api/imports", json={**base, "analyze_after_import": True})
    assert r.status_code == 400 and "Workflow" in r.json()["detail"]
    r = client.post("/api/imports", json={**base, "folder_sync": True})
    assert r.status_code == 400 and "Workflow" in r.json()["detail"]
    r = client.post("/api/imports", json={**base, "analyze_after_import": True, "workflow_id": 4711})
    assert r.status_code == 404


def test_requeue_and_automation_need_a_workflow(client, db):
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path="/s")
    db.add(batch)
    db.commit()
    assert client.post(f"/api/imports/{batch.id}/requeue").status_code == 400
    r = client.patch(f"/api/imports/{batch.id}/automation", json={"folder_sync": True})
    assert r.status_code == 400 and "Workflow" in r.json()["detail"]
    wf = crud.workflow.create(db, "Schema", None, _tpl("schema"))
    r = client.patch(f"/api/imports/{batch.id}/automation", json={"folder_sync": True, "workflow_id": wf.id})
    assert r.status_code == 200 and r.json()["workflow_id"] == wf.id and r.json()["folder_sync"] is True
