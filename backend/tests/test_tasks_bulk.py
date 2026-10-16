"""Bulk-Aktionen der Task-Liste: alle abgeschlossenen erneut senden, Liste leeren; Prompt-Typen."""

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch
from app.models.system_prompt import SystemPrompt
from app.models.workflow_task import WorkflowTask
from app.schemas.system_prompt import SystemPromptCreate


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
def tasks(db):
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path="/s")
    db.add(batch)
    db.commit()
    out = {}
    for status in ("completed", "completed", "failed", "pending", "in_progress"):
        doc = crud.document.create(db, batch.id, f"{status}.pdf", 1)
        doc.status = "done" if status == "completed" else "error" if status == "failed" else "processing"
        t = WorkflowTask(workflow_id=f"w{doc.id}", payload={"kind": "process_document", "document_id": doc.id},
                         status=status, attempts=2, error="x" if status == "failed" else None)
        db.add(t)
        out.setdefault(status, []).append((t, doc))
    db.commit()
    return out


def test_restart_all_completed_only_by_default(client, db, tasks):
    r = client.post("/api/tasks/restart-all")
    assert r.status_code == 200 and r.json()["restarted"] == 2
    db.expire_all()
    for t, d in tasks["completed"]:
        assert db.get(WorkflowTask, t.id).status == "pending" and db.get(WorkflowTask, t.id).attempts == 0
        assert db.get(type(d), d.id).status == "pending"
    failed_task, failed_doc = tasks["failed"][0]
    assert db.get(WorkflowTask, failed_task.id).status == "failed"          # unberührt


def test_restart_all_with_failed_and_delete_all(client, db, tasks):
    assert client.post("/api/tasks/restart-all", params={"include_failed": "true"}).json()["restarted"] == 3
    r = client.delete("/api/tasks", params={"status": "all"})
    assert r.status_code == 200 and r.json()["deleted"] == 5
    db.expire_all()
    assert db.query(WorkflowTask).count() == 0
    assert client.delete("/api/tasks", params={"status": "pending"}).status_code == 422


def _p(db, name, ptype):
    return crud.system_prompt.create(db, SystemPromptCreate(name=name, content=name, type=ptype))


def test_only_one_standard_prompt_others_become_workflow_building_blocks(db):
    a, b = _p(db, "A", 1), _p(db, "B", 1)
    db.expire_all()
    assert db.get(SystemPrompt, a.id).type == 2 and db.get(SystemPrompt, b.id).type == 1
    c = _p(db, "C", 2)
    assert crud.system_prompt.get_default(db).id == b.id                       # Workflow-Baustein wird nie Standard
    crud.system_prompt.set_default(db, c.id)
    db.expire_all()
    assert crud.system_prompt.get_default(db).id == c.id and db.get(SystemPrompt, b.id).type == 2


def test_get_default_is_deterministic_for_legacy_duplicates(db):
    first = SystemPrompt(name="alt", content="x", type=1)
    db.add(first)
    db.commit()
    db.add_all([SystemPrompt(name="neu1", content="y", type=1), SystemPrompt(name="neu2", content="z", type=1)])
    db.commit()
    assert all(crud.system_prompt.get_default(db).id == first.id for _ in range(5))
    assert crud.system_prompt.standard_count(db) == 3
