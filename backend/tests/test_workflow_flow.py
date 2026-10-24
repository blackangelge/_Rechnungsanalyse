"""Workflow-Ausführung und -API (simulierte KI, echte PDFs, SQLite)."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import crud
from app.database import get_db
from app.main import app
from app.models.ai_clients import AIClients
from app.models.import_batch import ImportBatch
from app.models.system_prompt import SystemPrompt
from app.models.vendor import Vendor
from app.routers import documents as docs_router
from app.services import ai_service, workflow_service


def _tpl(key):
    return copy.deepcopy(next(t for t in workflow_service.templates() if t["key"] == key)["graph"])


def _write_pdf(path: Path, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=300)
    with open(path, "wb") as fh:
        w.write(fh)


INVOICE = {"positionen": [{}], "zahlungsinformationen": {
    "gesamtbetrag_netto": 20.0, "gesamtbetrag_brutto": 23.8,
    "umsatzsteuer_zusammenfassung": [{"steuersatz": 19.0, "nettobetrag": 20.0, "steuerbetrag": 3.8}]}}
GOOD_POS = [{"quantity": 2, "unit": "Stück", "unit_price": 10.0, "total_price": 20.0}]
BAD_POS = [{"quantity": 3, "unit": "Stück", "unit_price": 7.0, "total_price": 99.0}]


class FakeAI:
    """Ersetzt die KI-Aufrufe und protokolliert sie."""

    def __init__(self, monkeypatch, type_id=1, positions=GOOD_POS, extract_error=None):
        self.calls = []
        self.type_id, self.positions, self.extract_error = type_id, positions, extract_error

        def detect(images, cfg, types, prompt=None):
            self.calls.append(("detect", len(images), prompt))
            name = next(t["name"] for t in types if t["id"] == self.type_id)
            return self.type_id, name, "{}", {"input_tokens": 10, "output_tokens": 2, "total_duration": 1.0}

        def extract(images, cfg, prompt=None):
            self.calls.append(("extract", len(images), prompt, getattr(cfg, "model_name", None)))
            if self.extract_error:
                raise RuntimeError(self.extract_error)
            return ({"supplier_name": "ACME GmbH", "total_amount": 23.8},
                    [dict(p) for p in self.positions], json.dumps(INVOICE),
                    {"input_tokens": 100, "output_tokens": 50, "total_duration": 5.0})

        def recheck(images, cfg, base, issues, prev):
            self.calls.append(("recheck", len(images), base))
            return ({}, [], "KI-Fehler: HTTP 500", {}, "RECHECK-PROMPT")

        monkeypatch.setattr(ai_service, "detect_document_type", detect)
        monkeypatch.setattr(ai_service, "extract_invoice_data", extract)
        monkeypatch.setattr(ai_service, "recheck_invoice_data", recheck)

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture()
def env(db, monkeypatch, tmp_path):
    cfg = AIClients(name="Test-KI", ip_address="http://x", port="1234", model_name="test-modell", active=True)
    default_prompt = SystemPrompt(name="Standard", content="STANDARD-PROMPT", type=1)
    type_prompt = SystemPrompt(name="Typ-Erkennung", content="TYP-PROMPT", type=0)
    db.add_all([cfg, default_prompt, type_prompt])
    db.commit()

    class E:
        pass
    e = E()
    e.db, e.cfg_id, e.tmp = db, cfg.id, tmp_path

    def make_doc(pages=3):
        batch = ImportBatch(
            company_name="Test GmbH", year=2026, import_folder_path="/import/x",
            storage_folder_path=str(tmp_path / "Test GmbH_2026"),
        )
        db.add(batch)
        db.commit()
        doc = crud.document.create(db, batch.id, "r.pdf", 1)
        _write_pdf(tmp_path / "Test GmbH_2026" / f"{doc.id}.pdf", pages)
        crud.document.update_after_copy(db, doc.id, f"{doc.id}.pdf", pages)
        db.refresh(doc)
        return doc
    e.make_doc = make_doc

    e.last_wf = None

    def add_workflow(graph, name="WF", default=False):
        """`default` bleibt aus Kompatibilität ein Parameter: im neuen System gibt es keinen Standard — der zuletzt angelegte
        Workflow gilt in `analyze()` nur als bequeme Vorgabe der Tests (jede Analyse bekommt trotzdem ausdrücklich eine ID)."""
        wf = crud.workflow.create(db, name, None, graph)
        e.last_wf = wf.id
        return wf
    e.add_workflow = add_workflow

    def analyze(doc, workflow_id=None, prompt=None):
        """prompt = Text eines Extraktionsprompts (wird als Systemprompt angelegt; None = Standardprompt)."""
        prompt_id = None
        if prompt is not None:
            sp = SystemPrompt(name=f"P-{prompt[:12]}", content=prompt, type=2)
            db.add(sp)
            db.commit()
            prompt_id = sp.id
        asyncio.run(docs_router._analyze_single(doc.id, e.cfg_id, prompt_id, workflow_id or e.last_wf))
        db.expire_all()
        return db.get(type(doc), doc.id)
    e.analyze = analyze
    return e


# ─── Ablauf ───────────────────────────────────────────────────────────────────

def test_standard_workflow_invoice_end_to_end(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    wf = env.add_workflow(_tpl("standard"), "Standard", default=True)
    doc = env.analyze(env.make_doc(), prompt="MEIN-PROMPT")
    assert doc.status == "done" and doc.document_type == 1
    assert doc.extraction.vendor_id == "ACME GmbH" and len(doc.order_positions) == 1
    assert doc.validation_status == "ok"
    assert ai.names() == ["detect", "extract"]
    assert doc.used_system_prompt == "MEIN-PROMPT"
    assert doc.used_doc_type_prompt == "TYP-PROMPT"
    assert doc.used_workflow_id == wf.id and doc.used_workflow_name == "Standard"
    assert doc.used_workflow["nodes"] and doc.page_count == 3
    run = doc.workflow_run
    assert [t["type"] for t in run if t["type"] != "saved"] == ["start", "detect_type", "branch", "extract", "validate", "end"]
    assert run[2]["branch"] == "true" and all(t["status"] == "ok" for t in run)
    assert doc.ki_input_tokens == 110                   # Typ + Extraktion summiert


def test_standard_workflow_other_type_skips_extraction(env, monkeypatch):
    ai = FakeAI(monkeypatch, type_id=3)
    env.add_workflow(_tpl("standard"), default=True)
    doc = env.analyze(env.make_doc())
    assert doc.status == "done" and doc.document_type == 3 and doc.extraction is None
    assert ai.names() == ["detect"]
    assert [t["type"] for t in doc.workflow_run if t["type"] != "saved"] == ["start", "detect_type", "branch", "end"]
    assert doc.workflow_run[2]["branch"] == "false"
    assert doc.ki_input_tokens == 10


def test_direct_workflow_without_type_detection(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    env.add_workflow(_tpl("direct"), default=True)
    doc = env.analyze(env.make_doc())
    assert ai.names() == ["extract"] and doc.status == "done" and doc.document_type == 0


def test_trim_node_shortens_pdf_before_ai_call(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    env.add_workflow(_tpl("trimmed"), default=True)
    doc = env.analyze(env.make_doc(pages=8))
    assert doc.pdf_version == 2 and doc.analyzed_pdf_version == 2 and doc.page_count == 1
    assert [c[1] for c in ai.calls] == [1, 1]            # beide KI-Aufrufe sahen nur 1 Seite
    assert "gekürzt" in doc.workflow_run[1]["detail"]


def test_global_auto_trim_setting_is_ignored_in_workflow_mode(env, monkeypatch):
    from app.schemas.document_version import PdfSettingsUpdate
    crud.pdf_settings.update(env.db, PdfSettingsUpdate(auto_trim_enabled=True, auto_trim_max_pages=1))
    FakeAI(monkeypatch)
    env.add_workflow(_tpl("direct"), default=True)
    doc = env.analyze(env.make_doc(pages=5))
    assert doc.pdf_version == 1 and doc.page_count == 5


def test_node_prompt_and_ai_config_overrides(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    prompt = SystemPrompt(name="Lieferant X", content="SPEZIAL-PROMPT", type=2)
    other = AIClients(name="Andere KI", ip_address="http://y", port="1", model_name="anderes-modell", active=True)
    env.db.add_all([prompt, other])
    env.db.commit()
    g = _tpl("direct")
    g["nodes"][1]["data"]["config"] = {"system_prompt_id": prompt.id, "ai_config_id": other.id}
    env.add_workflow(g, default=True)
    doc = env.analyze(env.make_doc(), prompt="IGNORIERT")
    assert ai.calls[0][2].startswith("SPEZIAL-PROMPT") and ai.calls[0][3] == "anderes-modell"      # + automatisch angehängtes Antwortformat
    assert doc.used_system_prompt == "SPEZIAL-PROMPT"
    assert "anderes-modell" in doc.workflow_run[1]["detail"]


def test_missing_prompt_makes_document_error_with_trace(env, monkeypatch):
    FakeAI(monkeypatch)
    g = _tpl("direct")
    g["nodes"][1]["data"]["config"] = {"system_prompt_id": 999}
    env.add_workflow(g, default=True)
    doc = env.analyze(env.make_doc())
    assert doc.status == "error" and "Systemprompt #999" in doc.raw_response
    assert "Rechnungsdaten extrahieren" in doc.raw_response
    assert doc.workflow_run[-1]["status"] == "error"


def test_ai_exception_in_node_sets_error(env, monkeypatch):
    FakeAI(monkeypatch, extract_error="kaputt")
    env.add_workflow(_tpl("direct"), default=True)
    doc = env.analyze(env.make_doc())
    assert doc.status == "error" and "kaputt" in doc.raw_response


def test_validate_node_without_ai_recheck(env, monkeypatch):
    ai = FakeAI(monkeypatch, positions=BAD_POS)
    g = _tpl("direct")
    g["nodes"][2]["data"]["config"] = {"auto_correct": True, "ai_recheck": False}
    env.add_workflow(g, default=True)
    doc = env.analyze(env.make_doc())
    assert "recheck" not in ai.names() and doc.validation_status == "needs_review"
    # Gegenprobe: mit Recheck läuft die zweite KI-Abfrage
    ai2 = FakeAI(monkeypatch, positions=BAD_POS)
    env.add_workflow(_tpl("direct"), "Mit Recheck", default=True)
    doc2 = env.analyze(env.make_doc())
    assert "recheck" in ai2.names() and doc2.used_recheck_prompt == "RECHECK-PROMPT"


def test_validate_node_can_disable_auto_correct(env, monkeypatch):
    bad = [{"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25000.0},
           {"quantity": 1, "unit": "Stück", "unit_price": 10.0, "total_price": 10.0}]
    FakeAI(monkeypatch, positions=bad)
    g = _tpl("direct")
    g["nodes"][2]["data"]["config"] = {"auto_correct": False, "ai_recheck": False}
    env.add_workflow(g, default=True)
    doc = env.analyze(env.make_doc())
    assert doc.validation_status == "needs_review"
    assert float(doc.order_positions[0].total_price) == 25000.0


def test_workflow_only_trim_finishes_done(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    g = {"nodes": [workflow_service._node("s", "start", "Start", 0, 0),
                   workflow_service._node("t", "trim_pages", "Kürzen", 0, 0, {"max_pages": 1})],
         "edges": [workflow_service._edge("s", "t")]}
    env.add_workflow(g, default=True)
    doc = env.analyze(env.make_doc(pages=4))
    assert doc.status == "done" and doc.page_count == 1 and ai.calls == []


# ─── Auswahl des Workflows ────────────────────────────────────────────────────

def test_resolution_is_explicit_only(env, monkeypatch):
    """Keine automatische Auswahl: kein Standard, kein Lieferanten-Workflow — nur die ausdrücklich genannte ID."""
    FakeAI(monkeypatch)
    doc = env.make_doc()
    assert docs_router._db_resolve_workflow(doc.id, None) is None
    only = env.add_workflow(_tpl("direct"), "Irgendeiner")
    assert docs_router._db_resolve_workflow(doc.id, None) is None            # auch wenn es genau einen Workflow gibt
    assert docs_router._db_resolve_workflow(doc.id, only.id)["id"] == only.id
    assert docs_router._db_resolve_workflow(doc.id, 9999) is None
    crud.document.save_extraction(env.db, doc.id, {"supplier_name": "ACME"}, [], "{}")
    env.db.add(Vendor(name="ACME", workflow_id=only.id))
    env.db.commit()
    assert docs_router._db_resolve_workflow(doc.id, None) is None            # Lieferanten-Zuordnung wird ignoriert


def test_analysis_without_workflow_is_an_error_and_never_runs_classic_flow(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    doc = env.make_doc()
    asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, None))
    env.db.expire_all()
    doc = env.db.get(type(doc), doc.id)
    assert ai.calls == [] and doc.status == "error" and "Kein Workflow gewählt" in (doc.raw_response or "")
    wf = env.add_workflow(_tpl("direct"))
    crud.workflow.delete(env.db, wf.id)
    asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, wf.id))
    env.db.expire_all()
    assert ai.calls == [] and "existiert nicht mehr" in (env.db.get(type(doc), doc.id).raw_response or "")


def test_reanalysis_with_other_workflow_replaces_workflow_info(env, monkeypatch):
    FakeAI(monkeypatch)
    env.add_workflow(_tpl("direct"), "Erster")
    doc = env.analyze(env.make_doc())
    assert doc.used_workflow_name == "Erster"
    env.add_workflow(_tpl("direct"), "Zweiter")
    doc = env.analyze(doc)
    assert doc.used_workflow_name == "Zweiter" and doc.workflow_run


# ─── API ──────────────────────────────────────────────────────────────────────

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


def test_api_crud_and_templates(client):
    tpls = client.get("/api/workflows/templates").json()
    assert {t["key"] for t in tpls} == {"pages_schema", "schema", "schema_trimmed", "standard", "direct", "trimmed", "tonnes"}

    r = client.post("/api/workflows", json={"name": "Aus Vorlage", "template": "standard"})
    assert r.status_code == 201
    wf = r.json()
    assert wf["graph"]["nodes"][1]["data"]["config"] == {"system_prompt_id": None}   # normalisiert
    assert client.post("/api/workflows", json={"name": "X", "template": "gibtsnicht"}).status_code == 404

    empty = client.post("/api/workflows", json={"name": "Leer"}).json()
    assert [n["type"] for n in empty["graph"]["nodes"]] == ["start"]

    listing = client.get("/api/workflows").json()
    assert {w["name"]: w["node_count"] for w in listing} == {"Aus Vorlage": 7, "Leer": 1}

    wf["graph"]["nodes"][1]["data"]["label"] = "Typ prüfen"
    up = client.put(f"/api/workflows/{wf['id']}", json={"name": "Umbenannt", "description": "d", "graph": wf["graph"]})
    assert up.status_code == 200 and up.json()["name"] == "Umbenannt"
    assert client.get(f"/api/workflows/{wf['id']}").json()["graph"]["nodes"][1]["data"]["label"] == "Typ prüfen"

    dup = client.post(f"/api/workflows/{wf['id']}/duplicate").json()
    assert dup["name"] == "Umbenannt (Kopie)" and dup["id"] != wf["id"]
    assert client.delete(f"/api/workflows/{dup['id']}").status_code == 204
    assert client.get(f"/api/workflows/{dup['id']}").status_code == 404
    assert client.delete("/api/workflows/9999").status_code == 404


def test_api_rejects_invalid_graph_with_readable_error(client):
    wf = client.post("/api/workflows", json={"name": "A", "template": "direct"}).json()
    bad = copy.deepcopy(wf["graph"])
    bad["edges"].append({"id": "x", "source": "start", "target": "validate", "sourceHandle": None})
    r = client.put(f"/api/workflows/{wf['id']}", json={"name": "A", "graph": bad})
    assert r.status_code == 422 and "nur eine erlaubt" in r.json()["detail"]
    assert client.post("/api/workflows", json={"name": "B", "graph": {"nodes": []}}).status_code == 422
    assert client.post("/api/workflows", json={"name": "", "template": "direct"}).status_code == 422


def test_api_validate_endpoint_reports_warnings_and_missing_references(client):
    g = _tpl("direct")
    g["nodes"][1]["data"]["config"] = {"system_prompt_id": 42, "ai_config_id": 43}
    r = client.post("/api/workflows/validate", json={"graph": g}).json()
    assert r["ok"] and any("Systemprompt #42" in w for w in r["warnings"]) \
        and any("KI-Konfiguration #43" in w for w in r["warnings"])
    bad = client.post("/api/workflows/validate", json={"graph": {"nodes": [], "edges": []}}).json()
    assert not bad["ok"] and bad["errors"]


def test_no_default_workflow_endpoints(client):
    a = client.post("/api/workflows", json={"name": "A", "template": "direct"}).json()
    assert client.post(f"/api/workflows/{a['id']}/set-default").status_code in (404, 405)
    assert client.post(f"/api/workflows/{a['id']}/clear-default").status_code in (404, 405)


def test_vendor_workflow_assignment_is_gone_and_delete_clears_old_links(client, db):
    sup = Vendor(name="ACME")
    db.add(sup)
    db.commit()
    wf = client.post("/api/workflows", json={"name": "A", "template": "direct"}).json()
    assert client.put(f"/api/vendors/{sup.id}/workflow", json={"workflow_id": wf["id"]}).status_code in (404, 405)
    sup.workflow_id = wf["id"]                  # Altbestand
    db.commit()
    client.delete(f"/api/workflows/{wf['id']}")
    assert client.get(f"/api/vendors/{sup.id}").json()["workflow_id"] is None


def test_api_analyze_unknown_workflow_is_404(client, env):
    doc = env.make_doc()
    r = client.post("/api/documents/analyze", json={"document_ids": [doc.id], "workflow_id": 4711})
    assert r.status_code == 404


def test_api_enqueue_unknown_workflow_is_404(client, env):
    doc = env.make_doc()
    r = client.post("/api/documents/enqueue", json={"document_ids": [doc.id], "workflow_id": 4711})
    assert r.status_code == 404


def test_worker_payload_workflow_reaches_analysis(env, monkeypatch):
    """Der Dispatcher-Client reicht workflow_id (und system_prompt_id) aus dem Task-Payload an _analyze_single weiter."""
    from types import SimpleNamespace
    from app.services.ai_dispatcher_client import AIDispatcherClient

    seen = {}

    async def fake_analyze(doc_id, server_id, prompt_id, workflow_id=None, skip_type_detection=False):
        seen.update(doc=doc_id, server=server_id, prompt=prompt_id, workflow=workflow_id, skip=skip_type_detection)
        return "ok"

    monkeypatch.setattr(docs_router, "_analyze_single", fake_analyze)
    monkeypatch.setattr(docs_router, "_db_set_processing", lambda doc_id: None)
    server = SimpleNamespace(id=7, name="srv", raw={"endpoint_type": "openai"})
    client = AIDispatcherClient.__new__(AIDispatcherClient)
    result = asyncio.run(client._handle_process_document(
        server, {"kind": "process_document", "document_id": 5, "system_prompt_id": 3, "workflow_id": 9}))
    assert seen == {"doc": 5, "server": 7, "prompt": 3, "workflow": 9, "skip": False} and result["result_code"] == "ok"
    asyncio.run(client._handle_process_document(
        server, {"kind": "process_document", "document_id": 5, "skip_type_detection": True}))
    assert seen["skip"] is True


def test_ai_unavailable_in_workflow_keeps_document_for_retry(env, monkeypatch):
    """KI nicht erreichbar → "ai_unavailable" (Worker reiht neu ein), Beleg wird NICHT auf error gesetzt."""
    def conn_error(images, cfg, prompt=None):
        return {}, [], "KI-Verbindungsfehler: refused", {}
    monkeypatch.setattr(ai_service, "extract_invoice_data", conn_error)
    env.add_workflow(_tpl("direct"), default=True)
    doc = env.make_doc()
    crud.document.update_status(env.db, doc.id, "processing")
    result = asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, env.last_wf))
    assert result == "ai_unavailable"
    env.db.expire_all()
    assert env.db.get(type(doc), doc.id).status == "processing"          # unverändert → Worker versucht erneut


def test_api_document_detail_exposes_workflow_info(client, env, monkeypatch):
    FakeAI(monkeypatch)
    env.add_workflow(_tpl("direct"), "Mein Flow", default=True)
    doc = env.analyze(env.make_doc())
    d = client.get(f"/api/documents/{doc.id}").json()
    assert d["used_workflow_name"] == "Mein Flow" and len([t for t in d["workflow_run"] if t["type"] != "saved"]) == 4
    assert d["used_workflow"]["nodes"][0]["type"] == "start"


# ─── Worker: Kürzen vor der Kapazitätsprüfung (Workflow-Modus) ───────────────

def test_leading_trim_pages_only_counts_nodes_right_after_start():
    g = _tpl("trimmed")
    assert workflow_service.leading_trim_pages(g) == 1 or workflow_service.leading_trim_pages(g) >= 1
    assert workflow_service.leading_trim_pages(_tpl("direct")) is None
    # Kürzen hinter einer Weiche zählt nicht
    std = _tpl("standard")
    assert workflow_service.leading_trim_pages(std) is None


def test_worker_precheck_uses_workflow_trim_not_global_setting(env, monkeypatch):
    from app.schemas.document_version import PdfSettingsUpdate
    from app.worker import worker as worker_module

    crud.pdf_settings.update(env.db, PdfSettingsUpdate(auto_trim_enabled=True, auto_trim_max_pages=1))
    env.add_workflow(_tpl("direct"), default=True)           # Workflow ohne Kürzen-Knoten
    doc = env.make_doc(pages=6)
    asyncio.run(worker_module.prepare_pdf_for_task({"document_id": doc.id, "workflow_id": env.last_wf}))
    env.db.expire_all()
    assert env.db.get(type(doc), doc.id).page_count == 6     # globale Einstellung gilt im Workflow-Modus nicht

    wf = _tpl("trimmed")                                     # Workflow mit führendem Kürzen-Knoten
    limit = workflow_service.leading_trim_pages(wf)
    env.add_workflow(wf, "Mit Kürzen", default=True)
    doc2 = env.make_doc(pages=limit + 5)
    asyncio.run(worker_module.prepare_pdf_for_task({"document_id": doc2.id, "workflow_id": env.last_wf}))
    env.db.expire_all()
    assert env.db.get(type(doc2), doc2.id).page_count == limit


def _chain(*nodes):
    g = {"nodes": [workflow_service._node("start", "start", "Start", 0, 0)], "edges": []}
    prev = "start"
    for i, (ntype, cfg) in enumerate(nodes):
        nid = f"n{i}"
        g["nodes"].append(workflow_service._node(nid, ntype, ntype, 200 * (i + 1), 0, cfg))
        g["edges"].append(workflow_service._edge(prev, nid))
        prev = nid
    g["nodes"].append(workflow_service._node("end", "end", "Ende", 200 * (len(nodes) + 1), 0))
    g["edges"].append(workflow_service._edge(prev, "end"))
    return g


KG_POS = [{"quantity": 25880, "unit": "kg", "unit_price": 0.4211, "total_price": 10898.07}]


def test_normalize_node_converts_kg_to_tonnes_or_back(env, monkeypatch):
    FakeAI(monkeypatch, positions=KG_POS)
    env.add_workflow(_chain(("extract", {}), ("normalize_units", {"target": "t", "mode": "all"})), default=True)
    doc = env.analyze(env.make_doc())
    pos = doc.order_positions[0]
    assert (float(pos.quantity), pos.unit, round(float(pos.unit_price_netto), 4)) == (25.88, "t", 421.1)
    assert "1 Position(en) auf t umgerechnet" in doc.workflow_run[2]["detail"]


def test_normalize_node_target_kg_and_skip_without_extraction(env, monkeypatch):
    FakeAI(monkeypatch, positions=[{"quantity": 2, "unit": "t", "unit_price": 450.0, "total_price": 900.0}])
    env.add_workflow(_chain(("extract", {}), ("normalize_units", {"target": "kg"})), default=True)
    doc = env.analyze(env.make_doc())
    pos = doc.order_positions[0]
    assert (float(pos.quantity), pos.unit, float(pos.unit_price_netto)) == (2000.0, "kg", 0.45)
    wf = env.add_workflow(_chain(("normalize_units", {})), name="ohne Extraktion", default=True)
    assert "keine Extraktion" in env.analyze(env.make_doc(), workflow_id=wf.id).workflow_run[1]["detail"]


def test_refine_node_reprocesses_previous_result_with_own_prompt(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    sp = SystemPrompt(name="Nachbearbeitung", content="REFINE-PROMPT", type=2)
    env.db.add(sp)
    env.db.commit()
    env.add_workflow(_chain(("extract", {}), ("ai_refine", {"system_prompt_id": sp.id})), default=True)
    env.analyze(env.make_doc())
    assert ai.names() == ["extract", "extract"]
    assert ai.calls[1][2].startswith("REFINE-PROMPT") and "BISHERIGE AUSLESUNG" in ai.calls[1][2]


def test_refine_node_without_prompt_fails_clearly_and_new_nodes_validate(env, monkeypatch):
    FakeAI(monkeypatch)
    env.add_workflow(_chain(("extract", {}), ("ai_refine", {})), default=True)
    doc = env.analyze(env.make_doc())
    assert doc.status == "error"
    rep = workflow_service.validate_graph(_chain(("normalize_units", {}), ("extract", {})))
    assert rep.ok and any("liegt vor jeder Extraktion" in w for w in rep.warnings)


def test_normalize_node_default_mode_leaves_kg_priced_positions_alone(env, monkeypatch):
    FakeAI(monkeypatch, positions=KG_POS)          # Preis je kg (keine Preiseinheit t) → bleibt
    env.add_workflow(_chain(("extract", {}), ("normalize_units", {})), default=True)
    pos = env.analyze(env.make_doc()).order_positions[0]
    assert (float(pos.quantity), pos.unit) == (25880.0, "kg")


def test_trace_records_input_output_changes_prompts_and_saved_state(env, monkeypatch):
    FakeAI(monkeypatch, positions=[{"quantity": 25880, "unit": "kg", "unit_price": 421.10 / 1000, "total_price": 10898.07}])
    env.add_workflow(_chain(("extract", {}), ("normalize_units", {"mode": "all"})), default=True)
    doc = env.analyze(env.make_doc(), prompt="MEIN-PROMPT")
    run = {t["type"]: t for t in doc.workflow_run}
    ex, nu, saved = run["extract"], run["normalize_units"], run["saved"]
    assert ex["input"]["positions"] == [] and ex["output"]["positions"][0]["Einheit"] == "kg"
    assert "MEIN-PROMPT" in ex["io"]["prompt"] and ex["io"]["response"]
    assert any(c["path"] == "Position 1 · Einheit" and c["from"] == "kg" and c["to"] == "t" for c in nu["changes"])
    assert saved["output"]["positions"][0]["Einheit"] == "t" and saved["output"]["positions"][0]["Menge"] == 25.88


def test_trace_explains_why_normalization_did_nothing(env, monkeypatch):
    FakeAI(monkeypatch, positions=KG_POS)
    env.add_workflow(_chain(("extract", {}), ("normalize_units", {})), default=True)
    nu = next(t for t in env.analyze(env.make_doc()).workflow_run if t["type"] == "normalize_units")
    assert nu["changes"] == [] and any("nicht je Tonne" in n for n in nu["io"]["notes"])


# ─── Nur neu auslesen (Typ nicht erneut erkennen) ─────────────────────────────

def test_only_reextract_detects_type_when_none_is_stored(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    env.add_workflow(_tpl("standard"))
    doc = env.make_doc()
    asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, env.last_wf, True))
    assert ai.names() == ["detect", "extract"]                       # kein gespeicherter Typ → normal erkennen


def test_only_reextract_in_workflow_keeps_stored_type_without_ai_call(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    env.add_workflow(_tpl("standard"), default=True)
    doc = env.analyze(env.make_doc())
    assert ai.names() == ["detect", "extract"]
    ai.calls.clear()
    asyncio.run(docs_router._analyze_single(doc.id, env.cfg_id, None, env.last_wf, True))
    env.db.expire_all()
    doc = env.db.get(type(doc), doc.id)
    assert ai.names() == ["extract"] and doc.status == "done" and doc.document_type == 1
    detect_entry = next(t for t in doc.workflow_run if t["type"] == "detect_type")
    assert "übernommen" in (detect_entry.get("detail") or "")


def test_enqueue_stores_skip_flag_in_payload(client, env):
    from app.models.workflow_task import WorkflowTask
    doc = env.make_doc()
    wf = env.add_workflow(_tpl("direct"))
    r = client.post("/api/documents/enqueue", json={"document_ids": [doc.id], "workflow_id": wf.id, "skip_type_detection": True})
    assert r.status_code == 200
    payload = env.db.query(WorkflowTask).one().payload
    assert payload.get("skip_type_detection") is True and payload.get("workflow_id") == wf.id


def test_analyze_and_enqueue_require_a_workflow(client, env):
    doc = env.make_doc()
    assert client.post("/api/documents/enqueue", json={"document_ids": [doc.id]}).status_code == 422
    assert client.post("/api/documents/analyze", json={"document_ids": [doc.id]}).status_code == 422


# ─── Antwortformat ─────────────────────────────────────────────────────────────

def test_default_response_format_is_appended_when_prompt_has_no_structure():
    out = ai_service.with_response_format("Lies die Rechnung.")
    assert "artikelbezeichnung" in out and "positionen" in out
    full = "Antworte mit {\"positionen\": [{\"artikelbezeichnung\": ...}]}"
    assert ai_service.with_response_format(full) == full                       # beschreibt die Struktur schon
    assert ai_service.with_response_format("X", "MEIN FORMAT").endswith("MEIN FORMAT")


def test_extract_node_sends_prompt_with_default_or_chosen_format(env, monkeypatch):
    ai = FakeAI(monkeypatch)
    fmt = SystemPrompt(name="Format A", content="FORMAT-A-INHALT", type=3)
    env.db.add(fmt)
    env.db.commit()
    wf = env.add_workflow(_chain(("extract", {"format_prompt_id": fmt.id})), default=True)
    doc = env.analyze(env.make_doc(), prompt="Mein Prompt ohne Struktur")
    assert ai.calls[0][2].startswith("Mein Prompt ohne Struktur") and ai.calls[0][2].endswith("FORMAT-A-INHALT")
    assert doc.used_system_prompt == "Mein Prompt ohne Struktur"            # gespeichert wird der Prompt wie angelegt
    ai.calls.clear()
    crud.workflow.delete(env.db, wf.id)
    env.add_workflow(_chain(("extract", {})), default=True)
    env.analyze(doc, prompt="Mein Prompt ohne Struktur")
    assert "ANTWORTFORMAT" in ai.calls[0][2] and "artikelbezeichnung" in ai.calls[0][2]


def test_default_format_endpoint_and_type3_prompts_are_not_exclusive(client, db):
    r = client.get("/api/settings/system-prompts/default-format")
    assert r.status_code == 200 and "artikelbezeichnung" in r.json()["content"]
    for n in ("A", "B"):
        assert client.post("/api/settings/system-prompts", json={"name": n, "content": "x", "type": 3}).status_code == 201
    assert [p["type"] for p in client.get("/api/settings/system-prompts").json()].count(3) == 2
