"""Seitenanzahl im Hintergrund, verschlüsselte/zu große PDFs als Nachbearbeitung, Seitenerkennung (classify_pages)."""

import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import crud
from app.crud import schema_definition as sd
from app.database import get_db
from app.main import app
from app.models.ai_clients import AIClients
from app.models.document import Document
from app.models.document_page import DocumentPage
from app.models.import_batch import ImportBatch
from app.models.schema_def import ExtractedRecord
from app.routers import documents as docs_router
from app.services import page_classifier as pc
from app.services import page_count_service, schema_models, workflow_service
from app.services.schema_seed import seed_initial_schema
from app.services.units_seed import seed_default_units
from tests.test_workflow_flow import _write_pdf


def _tpl(key):
    return copy.deepcopy(next(t for t in workflow_service.templates() if t["key"] == key)["graph"])


def _encrypted(path, user_password, pages=2):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=300)
    w.encrypt(user_password=user_password, owner_password="owner")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        w.write(fh)


# ─── inspect_pdf ─────────────────────────────────────────────────────────────

def test_inspect_pdf_states(tmp_path):
    _write_pdf(tmp_path / "ok.pdf", 3)
    assert page_count_service.inspect_pdf(tmp_path / "ok.pdf") == ("ok", 3)
    _encrypted(tmp_path / "locked.pdf", "geheim")
    assert page_count_service.inspect_pdf(tmp_path / "locked.pdf") == ("encrypted", 0)
    _encrypted(tmp_path / "owner_only.pdf", "", pages=4)               # nur Bearbeitungsschutz → lesbar
    assert page_count_service.inspect_pdf(tmp_path / "owner_only.pdf") == ("ok", 4)
    (tmp_path / "kaputt.pdf").write_bytes(b"das ist kein pdf")
    assert page_count_service.inspect_pdf(tmp_path / "kaputt.pdf") == ("unreadable", 0)
    assert page_count_service.inspect_pdf(tmp_path / "fehlt.pdf")[0] == "unreadable"


# ─── Hintergrunddienst ───────────────────────────────────────────────────────

@pytest.fixture()
def lib(db, tmp_path):
    class L:
        pass
    l = L()
    l.db, l.tmp = db, tmp_path
    l.batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path=str(tmp_path / "T_2026"), status="running")
    db.add(l.batch)
    db.commit()

    def add(kind="ok", pages=3, status="pending"):
        doc = crud.document.create(db, l.batch.id, f"{kind}.pdf", 1)
        path = tmp_path / "T_2026" / f"{doc.id}.pdf"
        if kind == "ok":
            _write_pdf(path, pages)
        elif kind == "locked":
            _encrypted(path, "geheim")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"xxx")
        crud.document.update_after_copy(db, doc.id, f"{doc.id}.pdf", 0)
        if status != "pending":
            crud.document.update_status(db, doc.id, status)
        db.refresh(doc)
        return doc
    l.add = add
    return l


def test_scan_sets_page_count_and_state_and_flags_bad_pdfs(lib):
    ok, locked, broken = lib.add("ok", 5), lib.add("locked"), lib.add("broken")
    assert ok.page_count == 0 and ok.pdf_state is None                       # der Import liest keine Seiten
    assert asyncio.run(page_count_service.scan_once()) == 3
    lib.db.expire_all()
    ok, locked, broken = (lib.db.get(Document, d.id) for d in (ok, locked, broken))
    assert (ok.page_count, ok.pdf_state, ok.status, ok.error_code) == (5, "ok", "done", None)       # „done“ = kopiert
    assert (locked.pdf_state, locked.status, locked.error_code) == ("encrypted", "error", "encrypted")
    assert "passwortgeschützt" in locked.raw_response
    assert (broken.pdf_state, broken.status, broken.error_code) == ("unreadable", "error", "unreadable")
    assert asyncio.run(page_count_service.scan_once()) == 0                 # nichts doppelt prüfen


def test_scan_does_not_overwrite_status_of_analyzed_documents(lib):
    done = lib.add("locked")
    done.analyzed_pdf_version = 1                                            # Analyse lief schon
    lib.db.commit()
    asyncio.run(page_count_service.scan_once())
    lib.db.expire_all()
    d = lib.db.get(Document, done.id)
    assert d.pdf_state == "encrypted" and d.status == "done" and d.error_code is None


def test_run_for_batch_keeps_scanning_until_import_finished(lib):
    first = lib.add("ok", 2)

    async def scenario():
        task = asyncio.create_task(page_count_service.run_for_batch(lib.batch.id, poll_seconds=0.05))
        await asyncio.sleep(0.3)
        second = lib.add("ok", 4)                 # kommt während des Imports dazu
        await asyncio.sleep(0.3)
        assert not task.done()                    # Import läuft noch
        b = lib.db.get(ImportBatch, lib.batch.id)
        b.status = "done"
        lib.db.commit()
        await asyncio.wait_for(task, timeout=5)
        return second
    second = asyncio.run(scenario())
    lib.db.expire_all()
    assert lib.db.get(Document, first.id).page_count == 2 and lib.db.get(Document, second.id).page_count == 4


def test_import_does_not_read_pages(lib, tmp_path, sqlite_session_factory, monkeypatch):
    from app.services.import_service import _process_single_document
    monkeypatch.setattr("app.services.import_service.SessionLocal", sqlite_session_factory)
    src = tmp_path / "in"
    _write_pdf(src / "a.pdf", 7)
    storage = tmp_path / "T_2026"
    storage.mkdir(parents=True, exist_ok=True)
    doc_id = asyncio.run(_process_single_document(lib.batch.id, src / "a.pdf", storage))
    lib.db.expire_all()
    d = lib.db.get(Document, doc_id)
    assert d.stored_filename == f"{doc_id}.pdf" and d.page_count == 0 and d.pdf_state is None


# ─── Vorprüfung vor der KI ───────────────────────────────────────────────────

def test_precheck_marks_encrypted_without_background_scan(lib):
    locked, fine = lib.add("locked"), lib.add("ok", 2)
    assert "passwortgeschützt" in docs_router._db_precheck_pdf(locked.id)
    assert docs_router._db_precheck_pdf(fine.id) is None
    lib.db.expire_all()
    assert lib.db.get(Document, locked.id).error_code == "encrypted" and lib.db.get(Document, fine.id).page_count == 2


def test_new_analysis_clears_error_code(lib):
    locked = lib.add("locked")
    docs_router._db_precheck_pdf(locked.id)
    lib.db.expire_all()                                       # frische Sicht wie in einer neuen Sitzung der Analyse
    crud.document.update_status(lib.db, locked.id, "processing")
    lib.db.expire_all()
    assert lib.db.get(Document, locked.id).error_code is None


def test_filter_documents_by_error_code(lib):
    lib.add("ok")
    locked = lib.add("locked")
    docs_router._db_precheck_pdf(locked.id)
    ids = [d.id for d in crud.document.get_all_filtered(lib.db, error_codes=["encrypted"])]
    assert ids == [locked.id]
    assert [d.id for d in crud.document.get_all_filtered(lib.db, error_codes=["any"])] == [locked.id]
    assert crud.document.get_all_filtered(lib.db, error_codes=["too_many_pages"]) == []


# ─── Worker: zu groß / verschlüsselt ─────────────────────────────────────────

def test_worker_marks_oversized_documents_as_rework_instead_of_waiting(lib, monkeypatch):
    from app.worker import worker as worker_module
    doc = lib.add("ok", 30)
    crud.document.update_after_copy(lib.db, doc.id, f"{doc.id}.pdf", 30)
    calls = {}

    class FakePool:
        def eligible_servers(self, pages=None):
            return [SimpleNamespace(max_pages=5)] if pages is None else []

    w = worker_module.Worker.__new__(worker_module.Worker)
    w.worker_id, w.pool = "w1", FakePool()
    task = SimpleNamespace(id=1, payload={"document_id": doc.id, "workflow_id": 1})

    async def noop(*a, **k):
        return None

    async def mark_failed(t, err):
        calls["failed"] = err

    async def page_count(t):
        return 30

    w._claim_task, w._get_document_page_count, w._mark_failed = noop, page_count, mark_failed
    monkeypatch.setattr(worker_module, "prepare_pdf_for_task", noop)
    asyncio.run(w._process(task))
    lib.db.expire_all()
    d = lib.db.get(Document, doc.id)
    assert d.status == "error" and d.error_code == "too_many_pages" and "30 Seiten" in d.raw_response
    assert "Nachbearbeitung nötig" in calls["failed"]


def test_worker_rejects_encrypted_before_touching_the_ai(lib, monkeypatch):
    from app.worker import worker as worker_module
    doc = lib.add("locked")
    calls = {}
    w = worker_module.Worker.__new__(worker_module.Worker)
    w.worker_id, w.pool = "w1", SimpleNamespace(eligible_servers=lambda pages=None: [object()])

    async def noop(*a, **k):
        return None

    async def mark_failed(t, err):
        calls["failed"] = err

    w._claim_task, w._mark_failed = noop, mark_failed
    monkeypatch.setattr(worker_module, "prepare_pdf_for_task", noop)
    asyncio.run(w._process(SimpleNamespace(id=2, payload={"document_id": doc.id})))
    lib.db.expire_all()
    d = lib.db.get(Document, doc.id)
    assert d.status == "error" and d.error_code == "encrypted" and "Nachbearbeitung nötig" in calls["failed"]


# ─── Seitenklassifikation: reine Logik ───────────────────────────────────────

def V(n, typ, new=False, nr=None, sender=None, note=None):
    return pc.PageVerdict(seite=n, typ=typ, beginnt_neues_dokument=new, rechnungsnummer=nr, absender=sender, hinweis=note)


def test_summarize_invoice_only_other_only_mixed_and_empty():
    inv = pc.summarize([V(1, "rechnung", True, "R1"), V(2, "rechnung_folgeseite"), V(3, "leer")])
    assert (inv.document_type_id, inv.mixed, inv.invoice_pages, inv.empty_pages) == (1, False, [1, 2], [3])
    other = pc.summarize([V(1, "lieferschein", True), V(2, "lieferschein"), V(3, "sonstiges")])
    assert (other.document_type_id, other.mixed, other.invoice_pages) == (3, False, [])
    mixed = pc.summarize([V(1, "lieferschein", True), V(2, "rechnung", True, "R9"), V(3, "rechnung_folgeseite"), V(4, "mahnung", True)])
    assert (mixed.document_type_id, mixed.mixed, mixed.invoice_pages, mixed.other_pages) == (15, True, [2, 3], [1, 4])
    assert [(s.first, s.last, s.kind, s.invoice_number) for s in mixed.segments] == [(1, 1, "lieferschein", None), (2, 3, "rechnung", "R9"), (4, 4, "mahnung", None)]
    nothing = pc.summarize([V(1, "leer"), V(2, "leer")])
    assert nothing.document_type_id == 0 and not nothing.mixed


def test_ranges_and_chunks_and_describe():
    assert pc.ranges_text([1, 2, 3, 7, 9, 10]) == "1–3, 7, 9–10" and pc.ranges_text([]) == ""
    assert pc.chunk_ranges(25, 10) == [(1, 10), (11, 20), (21, 25)] and pc.chunk_ranges(0, 10) == []
    lines = pc.describe([V(1, "lieferschein", True), V(2, "lieferschein"), V(3, "rechnung", True, "R1")])
    assert lines == ["Seite 1–2: lieferschein", "Seite 3: rechnung (Rechnungsnr. R1)"]


def test_build_user_content_labels_and_context_page():
    imgs = [f"data:image/jpeg;base64,{i}" for i in range(1, 8)]
    parts = pc.build_user_content(imgs, 4, 6)
    texts = [p["text"] for p in parts if p["type"] == "text"]
    assert texts[1].startswith("Kontext") and "Seite 3" in texts[1] and texts[2:] == ["Seite 4:", "Seite 5:", "Seite 6:"]
    assert [p["image_url"]["url"][-1] for p in parts if p["type"] == "image_url"] == ["3", "4", "5", "6"]
    assert not any("Kontext" in p.get("text", "") for p in pc.build_user_content(imgs, 1, 3))


def _fake_call(plan, fail_chunks=()):
    """plan: seite → (typ, new, nr). Antwortet je Block mit den angefragten Seiten."""
    calls = []

    def call(config, system, content, fmt):
        pages = [int(p["text"].split()[1].rstrip(":")) for p in content if p.get("type") == "text" and p["text"].startswith("Seite ")]
        calls.append(pages)
        if len(calls) in fail_chunks:
            return SimpleNamespace(parsed=None, raw="kaputt", error="Antwort ist kein gültiges JSON-Objekt", ki_stats={"input_tokens": 10})
        seiten = [{"seite": n, "typ": plan.get(n, ("sonstiges", False, None))[0], "beginnt_neues_dokument": plan.get(n, ("", False, None))[1],
                   "rechnungsnummer": plan.get(n, ("", False, None))[2], "absender": None, "hinweis": None} for n in pages]
        return SimpleNamespace(parsed={"seiten": seiten}, raw=json.dumps({"seiten": seiten}), error=None,
                               ki_stats={"input_tokens": 100, "output_tokens": 20, "total_duration": 1.5})
    call.calls = calls
    return call


def test_classify_chunks_pages_merges_stats_and_fills_gaps():
    plan = {1: ("lieferschein", True, None), 12: ("rechnung", True, "R-7"), 13: ("rechnung_folgeseite", False, None)}
    call = _fake_call(plan)
    out = pc.classify([f"img{i}" for i in range(1, 26)], object(), chunk_size=10, call=call)
    assert call.calls == [list(range(1, 11)), list(range(11, 21)), list(range(21, 26))]
    assert out.error is None and len(out.pages) == 25 and out.stats["input_tokens"] == 300 and out.stats["total_duration"] == 4.5
    assert out.pages[11].typ == "rechnung" and out.pages[11].rechnungsnummer == "R-7"
    assert pc.summarize(out.pages).invoice_pages == [12, 13]


def test_classify_survives_one_bad_chunk_but_not_all_and_reports_connection_errors():
    out = pc.classify([f"i{i}" for i in range(1, 21)], object(), 10, call=_fake_call({}, fail_chunks=(1,)))
    assert out.error is None and out.pages[0].typ == "sonstiges" and out.pages[0].hinweis == "nicht klassifiziert" and out.warnings
    out = pc.classify([f"i{i}" for i in range(1, 21)], object(), 10, call=_fake_call({}, fail_chunks=(1, 2)))
    assert out.error and "keine verwertbare" in out.error

    def conn(config, system, content, fmt):
        return SimpleNamespace(parsed=None, raw="KI-Verbindungsfehler: refused", error="KI-Verbindungsfehler: refused", ki_stats={})
    assert pc.classify(["i1"], object(), 10, call=conn).error.startswith("KI-Verbindungsfehler")


def test_classify_ignores_out_of_range_and_duplicate_answers():
    def call(config, system, content, fmt):
        s = [{"seite": n, "typ": "rechnung", "beginnt_neues_dokument": False, "rechnungsnummer": None, "absender": None, "hinweis": None} for n in (1, 1, 99)]
        s[1]["typ"] = "mahnung"
        return SimpleNamespace(parsed={"seiten": s}, raw="{}", error=None, ki_stats={})
    out = pc.classify(["i1", "i2"], object(), 10, call=call)
    assert [p.typ for p in out.pages] == ["rechnung", "sonstiges"]           # erste Antwort zählt, 99 verworfen, Seite 2 offen


def test_response_format_is_strict_json_schema():
    fmt = pc.response_format()
    schema = fmt["json_schema"]["schema"]
    assert fmt["json_schema"]["strict"] and "$defs" in schema or "properties" in schema
    assert "rechnung_folgeseite" in json.dumps(schema)


# ─── Workflow-Knoten end-to-end ──────────────────────────────────────────────

class _PageStub:
    """Antwortet je Block mit der Klassifikation aus `plan` (Seitennummern stehen in den Beschriftungen) — und beim Schema-Aufruf mit SAMPLE."""

    def __init__(self, plan, extraction):
        outer = self
        self.plan, self.extraction, self.requests = plan, extraction, []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                name = body["response_format"]["json_schema"]["name"]
                if name == "page_classification":
                    user = body["messages"][1]["content"]
                    pages = [int(p["text"].split()[1].rstrip(":")) for p in user if p["type"] == "text" and p["text"].startswith("Seite ")]
                    content = {"seiten": [{"seite": n, "typ": outer.plan.get(n, "sonstiges"), "beginnt_neues_dokument": outer.plan.get(n) == "rechnung",
                                           "rechnungsnummer": "R-1" if outer.plan.get(n) == "rechnung" else None, "absender": None, "hinweis": None} for n in pages]}
                else:
                    content = outer.extraction
                payload = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}],
                                      "usage": {"prompt_tokens": 50, "completion_tokens": 10}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def pages_env(db, tmp_path):
    from tests.test_schema_extraction import SAMPLE
    plan = {n: "lieferschein" for n in range(1, 13)}
    plan.update({13: "rechnung", 14: "rechnung_folgeseite"})
    plan.update({n: "sonstiges" for n in range(15, 21)})
    stub = _PageStub(plan, SAMPLE)
    cfg = AIClients(name="vLLM", ip_address="http://127.0.0.1", port=str(stub.port), model_name="qwen", active=True, max_context_tokens=4500 * 4)
    db.add(cfg)
    db.commit()
    seed_default_units(db)
    seed_initial_schema(db)
    db.commit()
    schema_models.clear_cache()
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path=str(tmp_path / "T_2026"))
    db.add(batch)
    db.commit()
    doc = crud.document.create(db, batch.id, "sammel.pdf", 1)
    _write_pdf(tmp_path / "T_2026" / f"{doc.id}.pdf", 20)
    crud.document.update_after_copy(db, doc.id, f"{doc.id}.pdf", 20)
    db.refresh(doc)
    yield SimpleNamespace(db=db, stub=stub, cfg=cfg, doc=doc)
    stub.close()


def _analyze(env, workflow_id):
    asyncio.run(docs_router._analyze_single(env.doc.id, env.cfg.id, None, workflow_id))
    env.db.expire_all()
    return env.db.get(Document, env.doc.id)


def test_classify_node_keeps_only_invoice_pages_and_extracts_them(pages_env):
    env = pages_env
    wf = crud.workflow.create(env.db, "Seiten", None, _tpl("pages_schema"))
    doc = _analyze(env, wf.id)
    assert doc.status == "done", doc.raw_response
    assert doc.page_count == 2 and doc.pdf_version == 2 and doc.document_type == 15               # 20 → 2 Seiten, Mischbeleg
    pages = env.db.query(DocumentPage).filter_by(document_id=doc.id).order_by(DocumentPage.page_no).all()
    assert len(pages) == 20 and pages[12].kind == "rechnung" and pages[12].invoice_number == "R-1" and pages[0].kind == "lieferschein"
    assert all(p.pdf_version == 1 for p in pages)                                                # bezieht sich auf das Original
    run = doc.workflow_run
    assert [t["type"] for t in run if t["type"] != "saved"] == ["start", "classify_pages", "branch", "schema_extract", "end"]
    node = run[1]
    assert "2 Rechnung" in node["detail"] or "2 Seite" in node["detail"] and "gekürzt" in node["detail"]
    assert any(n.startswith("Seite 13–14: rechnung") or "Seite 13" in n for n in node["io"]["notes"])
    # Klassifikation lief in 2 Blöcken à 10 Seiten, die Extraktion bekam nur noch 2 Bilder
    cls = [r for r in env.stub.requests if r["response_format"]["json_schema"]["name"] == "page_classification"]
    assert len(cls) == 2
    ext = [r for r in env.stub.requests if r["response_format"]["json_schema"]["name"] == "extraction"]
    assert len(ext) == 1 and sum(1 for p in ext[0]["messages"][1]["content"] if p["type"] == "image_url") == 2
    root = env.db.query(ExtractedRecord).filter_by(document_id=doc.id, parent_record_id=None).one()
    assert root.data["seitenanzahl"] == 2 and root.data["rechnungsnummer"] == "RE-2024/0815"


def test_oversized_pdf_is_allowed_to_start_when_workflow_reduces_pages(pages_env):
    from app.routers.documents import _db_workflow_reduces_pages
    wf = crud.workflow.create(pages_env.db, "Seiten", None, _tpl("pages_schema"))
    plain = crud.workflow.create(pages_env.db, "Nur Schema", None, _tpl("schema"))
    assert _db_workflow_reduces_pages(wf.id) is True
    assert _db_workflow_reduces_pages(plain.id) is False and _db_workflow_reduces_pages(None) is False


def test_extract_node_flags_too_many_pages_for_the_ai(pages_env):
    env = pages_env
    wf = crud.workflow.create(env.db, "Nur Schema", None, _tpl("schema"))
    doc = _analyze(env, wf.id)                                           # 20 Seiten, KI schafft 4
    assert doc.status == "error" and doc.error_code == "too_many_pages" and "höchstens 4" in doc.raw_response
    assert not [r for r in env.stub.requests]                            # die KI wurde gar nicht belastet


def test_classify_only_keeps_pdf_and_sets_type(pages_env):
    env = pages_env
    g = _tpl("pages_schema")
    g["nodes"][1]["data"]["config"] = {"action": "classify_only"}
    g["nodes"][3]["type"] = "end"                                        # nur klassifizieren
    g["nodes"][3]["data"]["config"] = {}
    g["edges"] = [e for e in g["edges"] if e["target"] != "extract"]
    g["edges"].append({"id": "e-b-end", "source": "branch", "target": "extract", "sourceHandle": "true"})
    wf = crud.workflow.create(env.db, "Nur erkennen", None, g)
    doc = _analyze(env, wf.id)
    assert doc.status == "done" and doc.page_count == 20 and doc.pdf_version == 1 and doc.document_type == 15
    assert env.db.query(DocumentPage).filter_by(document_id=doc.id).count() == 20


def test_branch_on_invoice_page_count_and_document_type_mixed():
    from app.services.workflow_service import BranchConfig, evaluate_condition
    cfg = BranchConfig(field="invoice_page_count", op="gt", value="0")
    assert evaluate_condition(cfg, {"invoice_page_count": 2}) and not evaluate_condition(cfg, {"invoice_page_count": 0})
    assert not evaluate_condition(cfg, {"invoice_page_count": None})
    mixed = BranchConfig(field="document_type", op="equals", value="15")
    assert evaluate_condition(mixed, {"document_type_id": 15, "document_type_name": "Mischbeleg"})
    assert workflow_service.validate_graph(_tpl("pages_schema")).ok


def test_document_types_list_contains_mixed():
    c = TestClient(app)
    names = {t["id"]: t["name"] for t in c.get("/api/document-types").json()}
    assert names[15].startswith("Mischbeleg")


# ─── Standardfeld Seitenanzahl ───────────────────────────────────────────────

def test_seitenanzahl_is_a_locked_standard_field_on_the_root_entity(db):
    version = seed_initial_schema(db)
    beleg = next(e for e in version.entities if e.name == "beleg")
    f = next(f for f in beleg.fields if f.name == "seitenanzahl")
    assert f.is_locked and f.source == "system" and f.system_function == "page_count"
    assert not any(f.name == "seitenanzahl" for e in version.entities if e.name != "beleg" for f in e.fields)
    draft = sd.create_draft_from(db, version)
    row = next(f for e in draft.entities if e.name == "beleg" for f in e.fields if f.name == "seitenanzahl")
    with pytest.raises(sd.SchemaError, match="Standardfeld"):
        sd.update_field(db, row, display_name="Anders")
    with pytest.raises(sd.SchemaError, match="Standardfeld"):
        sd.delete_field(db, row)
    with pytest.raises(sd.SchemaError, match="reserviert"):
        sd.add_field(db, next(e for e in draft.entities if e.name == "beleg"), "seitenanzahl", "S", "integer")


def test_standard_field_is_restored_on_activation_even_for_hand_built_schemas(db):
    version = sd.create_version(db)
    beleg = sd.add_entity(db, version, "beleg", "Beleg")
    pos = sd.add_entity(db, version, "position", "Position")
    sd.add_field(db, beleg, "nummer", "Nr.", "text")
    sd.add_field(db, pos, "name", "Name", "text")
    sd.add_relation(db, version, "positionen", "parent_child", beleg, pos)
    assert "seitenanzahl" in {f.name for f in beleg.fields} and "seitenanzahl" not in {f.name for f in pos.fields}
    sd.activate(db, version)
