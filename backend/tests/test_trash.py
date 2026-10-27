"""Nicht-Eingangsrechnungen: finden, in den Papierkorb legen, prüfen, endgültig löschen; Workflow-Knoten „Beleg verwerfen“."""

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.database import get_db
from app.main import app
from app.models.document import Document
from app.models.import_batch import ImportBatch
from app.models.workflow_task import WorkflowTask
from app.services import page_classifier as pc
from tests.test_invoice_split import _outcome, _plan
from tests.test_pdf_state_and_pages import V, _analyze, _tpl, pages_env  # noqa: F401  (pages_env = Fixture)


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
def docs(db, tmp_path):
    folder = tmp_path / "Firma_2026"
    folder.mkdir()
    batch = ImportBatch(company_name="Firma", year=2026, import_folder_path="/x", storage_folder_path=str(folder))
    db.add(batch)
    db.commit()
    out = {}
    for name, typ, status in [("rechnung", 1, "done"), ("lieferschein", 4, "done"), ("mahnung", 7, "done"), ("misch", 17, "done"),
                              ("neu", 0, "done"), ("laeuft", 4, "processing")]:
        d = crud.document.create(db, batch.id, f"{name}.pdf", 1)
        d.document_type, d.status, d.stored_filename = typ, status, f"{d.id}.pdf"
        (folder / f"{d.id}.pdf").write_bytes(b"%PDF-1.4")
        out[name] = d
    db.commit()
    return out


def test_filter_finds_only_recognized_non_invoices(client, docs):
    ids = client.get("/api/documents/ids", params={"not_invoice": "true"}).json()
    assert sorted(ids) == sorted([docs["lieferschein"].id, docs["mahnung"].id])      # nicht: Rechnung, Mischbeleg, unerkannt, in Verarbeitung
    s = client.get("/api/documents/non-invoices/summary").json()
    assert s["total"] == 2 and {r["name"] for r in s["by_type"]} == {"Lieferschein", "Mahnung"} and s["trash"] == 0


def test_trash_restore_and_two_step_purge(client, db, docs):
    ls, inv = docs["lieferschein"], docs["rechnung"]
    r = client.post("/api/documents/bulk-delete", json={"ids": [ls.id, inv.id, docs["laeuft"].id], "reason": "Keine Eingangsrechnung"}).json()
    assert r["changed"] == 2 and r["skipped"] == 1                                    # laufende Belege bleiben
    db.expire_all()
    assert db.get(Document, ls.id).soft_deleted and db.get(Document, ls.id).deleted_reason == "Keine Eingangsrechnung"
    assert db.get(Document, ls.id).deleted_at is not None
    assert ls.id not in client.get("/api/documents/ids").json()
    assert sorted(client.get("/api/documents/ids", params={"only_deleted": "true"}).json()) == sorted([ls.id, inv.id])
    # zurückholen
    assert client.post("/api/documents/bulk-restore", json={"ids": [inv.id]}).json()["changed"] == 1
    db.expire_all()
    assert db.get(Document, inv.id).soft_deleted is False and db.get(Document, inv.id).deleted_reason is None
    # endgültig löschen: nur Belege im Papierkorb
    ls_id, inv_id, folder = ls.id, inv.id, ls.batch.storage_folder_path
    path_ls, path_inv = f"{folder}/{ls_id}.pdf", f"{folder}/{inv_id}.pdf"
    db.add(WorkflowTask(workflow_id="w1", payload={"kind": "process_document", "document_id": ls_id}, status="pending"))
    db.commit()
    res = client.post("/api/documents/purge", json={"ids": [ls_id, inv_id]}).json()
    assert res["changed"] == 1 and res["skipped"] == 1                                # inv liegt nicht im Papierkorb → bleibt
    db.expire_all()
    from pathlib import Path
    assert db.get(Document, ls_id) is None and not Path(path_ls).exists()
    assert db.get(Document, inv_id) is not None and Path(path_inv).exists()
    assert db.query(WorkflowTask).count() == 0


def test_purge_removes_version_files_and_single_delete_sets_reason(client, db, docs):
    from pathlib import Path
    d = docs["mahnung"]
    vdir = Path(d.batch.storage_folder_path) / "versions" / str(d.id)
    vdir.mkdir(parents=True)
    (vdir / "v1.pdf").write_bytes(b"x")
    assert client.delete(f"/api/documents/{d.id}").status_code == 200
    client.post("/api/documents/purge", json={"ids": [d.id]})
    assert not vdir.exists()


# ─── Workflow-Knoten „Beleg verwerfen“ ───────────────────────────────────────

def test_workflow_validation_for_discard_node():
    from app.services import workflow_service as ws
    g = _tpl("pages_schema")
    assert ws.validate_graph(g).ok
    assert any(n["type"] == "discard" for n in g["nodes"]) and any(n["type"] == "discard" for n in _tpl("standard")["nodes"])
    g["edges"].append({"id": "x", "source": "end-other", "target": "end", "sourceHandle": None})
    assert not ws.validate_graph(g).ok                                                   # vom Verwerfen-Knoten geht nichts mehr weiter


def test_non_invoice_pdf_is_discarded_into_the_trash(pages_env, monkeypatch):
    env = pages_env
    pages = [V(i, "lieferschein", new=(i == 1)) for i in range(1, 21)]
    monkeypatch.setattr(pc, "classify", lambda *a, **k: _outcome(pages))
    wf = crud.workflow.create(env.db, "Seiten", None, _tpl("pages_schema"))
    doc = _analyze(env, wf.id)
    assert doc.soft_deleted is True and doc.deleted_reason == "Keine Eingangsrechnung" and doc.deleted_at is not None
    assert doc.status == "done" and doc.document_type == 4                              # Typ bleibt für die Prüfung sichtbar
    run = doc.workflow_run
    assert [t["type"] for t in run if t["type"] != "saved"] == ["start", "classify_pages", "branch", "discard"]
    assert "Papierkorb" in run[3]["detail"]
    # Schutz: keine Extraktion, keine Datensätze
    assert env.db.query(Document).filter_by(id=doc.id).one().extraction is None


def test_invoice_is_never_discarded_even_if_discard_is_reached(pages_env, monkeypatch):
    env = pages_env
    pages = _plan(p1=("rechnung", "R-1", True, None))
    monkeypatch.setattr(pc, "classify", lambda *a, **k: _outcome(pages))
    g = _tpl("pages_schema")                                                             # Weiche absichtlich falsch verdrahtet: Rechnung → verwerfen
    for e in g["edges"]:
        if e["source"] == "branch" and e["sourceHandle"] == "true":
            e["target"] = "end-other"
    wf = crud.workflow.create(env.db, "Falsch verdrahtet", None, g)
    doc = _analyze(env, wf.id)
    assert doc.soft_deleted is False and doc.status == "done"
    assert "nicht verworfen" in doc.workflow_run[3]["detail"]


def test_discard_without_type_detection_does_nothing(pages_env):
    env = pages_env
    g = {"nodes": [{"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {"label": "Start", "config": {}}},
                   {"id": "d", "type": "discard", "position": {"x": 1, "y": 0}, "data": {"label": "Verwerfen", "config": {}}}],
         "edges": [{"id": "e", "source": "start", "target": "d", "sourceHandle": None}]}
    wf = crud.workflow.create(env.db, "Nur verwerfen", None, g)
    doc = _analyze(env, wf.id)
    assert doc.soft_deleted is False and "nicht verworfen" in doc.workflow_run[1]["detail"]


# ─── Assistent ───────────────────────────────────────────────────────────────

def test_assistant_trash_tools(db, docs):
    import asyncio
    from app.services import assistant_tools as at
    s = at.run_read(db, "non_invoice_summary", {})
    assert s["total"] == 2
    res = asyncio.run(at.execute_write(db, "trash_documents", {"not_invoice": True, "reason": "Keine Eingangsrechnung"}))
    assert res["in_papierkorb"] == 2
    db.expire_all()
    assert db.get(Document, docs["lieferschein"].id).soft_deleted and not db.get(Document, docs["rechnung"].id).soft_deleted
    assert at.run_read(db, "non_invoice_summary", {})["trash"] == 2
    assert asyncio.run(at.execute_write(db, "restore_documents", {"all": True}))["zurueckgeholt"] == 2
    with pytest.raises(at.ToolError):
        asyncio.run(at.execute_write(db, "trash_documents", {}))
    assert "purge" not in at.tools_prompt().lower() and "endgültig" in at.tools_prompt().lower()
