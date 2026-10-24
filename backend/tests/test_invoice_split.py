"""Seitenerkennung: Sicherheitsnetz (unsicher → Nachbearbeiten) und Abspalten mehrerer Rechnungen aus einer PDF."""

import asyncio

from app import crud
from app.models.document import Document
from app.models.workflow_task import WorkflowTask
from app.services import page_classifier as pc
from tests.test_pdf_state_and_pages import V, _PageStub, _analyze, _tpl, pages_env  # noqa: F401  (pages_env = Fixture)


# ─── reine Logik ─────────────────────────────────────────────────────────────

def _pages(spec):
    """spec: [(typ, nummer|None, neu?)] → PageVerdict-Liste (Seiten ab 1)"""
    return [V(i, t, new=new, nr=nr, sender="Agro GmbH" if nr else None) for i, (t, nr, new) in enumerate(spec, start=1)]


def test_groups_split_invoices_and_merge_same_number():
    spec = [("rechnung", "R-A", True), ("rechnung_folgeseite", None, False), ("lieferschein", None, True),
            ("rechnung", "R-B", True), ("rechnung_folgeseite", None, False), ("sonstiges", None, True),
            ("rechnung", "R-A", True)]                                  # R-A taucht später nochmal auf (z. B. Anlage dazwischen)
    pages = _pages(spec)
    groups = pc.invoice_groups(pages, pc.summarize(pages))
    assert [g.number for g in groups] == ["R-A", "R-B"]
    assert groups[0].pages == [1, 2, 7] and groups[1].pages == [4, 5]


def test_uncertainty_reasons():
    ok = _pages([("rechnung", "R-1", True), ("rechnung_folgeseite", None, False), ("lieferschein", None, True)])
    assert pc.uncertainty(ok, [], pc.summarize(ok)) == []
    # Hinweis der KI
    noted = list(ok)
    noted[2] = V(3, "lieferschein", new=True, note="vielleicht Anlage zur Rechnung")
    assert any("Unsichere Zuordnung auf Seite 3" in r for r in pc.uncertainty(noted, [], pc.summarize(noted)))
    # nicht klassifizierte Seiten
    assert any("nicht eindeutig" in r for r in pc.uncertainty(ok, ["Seite 3 nicht klassifiziert"], pc.summarize(ok)))
    # Rechnung beginnt mit Folgeseite
    folge = _pages([("sonstiges", None, False), ("rechnung_folgeseite", None, False)])
    assert any("Rechnungsbeginn fehlt" in r for r in pc.uncertainty(folge, [], pc.summarize(folge)))
    # mehrere Rechnungen ohne Nummer/Absender
    blind = [V(1, "rechnung", new=True), V(2, "rechnung", new=True)]
    assert any("weder Nummer noch Absender" in r for r in pc.uncertainty(blind, [], pc.summarize(blind)))


# ─── Workflow-Knoten ─────────────────────────────────────────────────────────

def _outcome(pages):
    return pc.ClassifyOutcome(pages=pages, stats={"input_tokens": 1, "output_tokens": 1})


def _split_workflow(env):
    g = _tpl("pages_schema")
    g["nodes"][1]["data"]["config"] = {"action": "split_invoices"}
    return crud.workflow.create(env.db, "Split", None, g)


def _plan(n=20, **kw):
    """Seitenplan für 20 Seiten; kw: Seite → (typ, nr, neu?, hinweis)"""
    pages = [V(i, "sonstiges") for i in range(1, n + 1)]
    for page, (typ, nr, new, note) in kw.items():
        pages[int(page[1:]) - 1] = V(int(page[1:]), typ, new=new, nr=nr, sender="Agro GmbH" if nr else None, note=note)
    return pages


def test_second_invoice_becomes_its_own_document_and_is_queued(pages_env, monkeypatch):
    env = pages_env
    pages = _plan(p1=("rechnung", "R-A", True, None), p2=("rechnung_folgeseite", None, False, None),
                  p3=("lieferschein", None, True, None),
                  p6=("rechnung", "R-B", True, None), p7=("rechnung_folgeseite", None, False, None))
    monkeypatch.setattr(pc, "classify", lambda *a, **k: _outcome(pages))
    wf = _split_workflow(env)
    doc = _analyze(env, wf.id)
    assert doc.status == "done", doc.raw_response
    assert doc.page_count == 2 and doc.pdf_version == 2                       # Original-Beleg behält nur Rechnung A (Seite 1–2)
    children = env.db.query(Document).filter(Document.id != doc.id, Document.batch_id == doc.batch_id).all()
    assert len(children) == 1
    child = children[0]
    assert child.status == "pending" and child.page_count == 2 and child.original_filename == "sammel_Rechnung2.pdf"
    from pathlib import Path
    from app.services import pdf_service
    path = Path(doc.batch.storage_folder_path) / child.stored_filename
    assert path.exists() and pdf_service.get_page_count(path) == 2 and child.file_size_bytes == path.stat().st_size
    tasks = [t for t in env.db.query(WorkflowTask).all() if t.payload.get("document_id") == child.id]
    assert len(tasks) == 1 and tasks[0].payload["workflow_id"] == wf.id
    node = doc.workflow_run[1]
    assert "1 weitere Rechnung" in node["detail"] and any(f"Beleg #{child.id}" in n for n in node["io"]["notes"])


def test_same_invoice_number_is_not_split(pages_env, monkeypatch):
    env = pages_env
    pages = _plan(p1=("rechnung", "R-A", True, None), p3=("lieferschein", None, True, None), p5=("rechnung", "R-A", True, None))
    monkeypatch.setattr(pc, "classify", lambda *a, **k: _outcome(pages))
    doc = _analyze(env, _split_workflow(env).id)
    assert doc.status == "done" and doc.page_count == 2                       # Seiten 1 und 5 zusammen = eine Rechnung
    assert env.db.query(Document).filter(Document.id != doc.id).count() == 0


def test_unsure_recognition_leaves_pdf_untouched_and_asks_for_rework(pages_env, monkeypatch):
    env = pages_env
    pages = _plan(p1=("rechnung", "R-A", True, None), p2=("sonstiges", None, False, "Anlage zur Rechnung oder AGB?"),
                  p6=("rechnung", "R-B", True, None))
    monkeypatch.setattr(pc, "classify", lambda *a, **k: _outcome(pages))
    doc = _analyze(env, _split_workflow(env).id)
    assert doc.status == "error" and doc.error_code == "pages_unsure"
    assert "PDF bleibt unverändert" in doc.raw_response and "Seite 2" in doc.raw_response
    assert doc.page_count == 20 and doc.pdf_version == 1                       # nichts beschnitten
    assert env.db.query(Document).filter(Document.id != doc.id).count() == 0   # nichts abgespalten


def test_keep_invoice_pages_also_refuses_when_unsure(pages_env, monkeypatch):
    env = pages_env
    pages = _plan(p3=("rechnung_folgeseite", None, False, None))              # nur Folgeseite, kein Rechnungsbeginn
    monkeypatch.setattr(pc, "classify", lambda *a, **k: _outcome(pages))
    wf = crud.workflow.create(env.db, "Keep", None, _tpl("pages_schema"))
    doc = _analyze(env, wf.id)
    assert doc.status == "error" and doc.error_code == "pages_unsure" and doc.pdf_version == 1


# ─── Prüf-Skript ─────────────────────────────────────────────────────────────

def test_bench_pages_scoring(tmp_path):
    from app.scripts import bench_pages
    csv_file = tmp_path / "erwartet.csv"
    csv_file.write_text("datei;seite;typ\na.pdf;1;rechnung\na.pdf;2;lieferschein\na.pdf;3;lieferschein\nzzz.pdf;1;rechnung\n", encoding="utf-8")
    expected = bench_pages.load_expected(csv_file)
    assert expected[("a.pdf", 2)] == "lieferschein" and len(expected) == 4
    res = bench_pages.score({"a.pdf": _pages([("rechnung", "R", True), ("lieferschein", None, True), ("sonstiges", None, False)])}, expected)
    assert res["per_type"] == {"rechnung": [1, 1], "lieferschein": [1, 2]}      # zzz.pdf fehlt → nicht gewertet
    assert res["wrong"] == [("a.pdf", 3, "lieferschein", "sonstiges")]
