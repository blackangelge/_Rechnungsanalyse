"""Übernahme aus dem MAIN-Projekt: Übersetzung, IDs bleiben gleich, PDFs, Wiederholbarkeit, Prüfregeln, API."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text

from app.database import get_db
from app.main import app
from app.models.document import Document
from app.models.import_batch import ImportBatch
from app.models.main_migration import MainMigrationItem
from app.models.schema_def import ExtractedRecord
from app.services import main_migration as mm
from app.services.rule_seed import seed_default_rules
from app.services.schema_seed import seed_initial_schema
from app.services.units_seed import seed_default_units
from app.services import schema_models

DDL = [
    "CREATE TABLE import_batches (id INTEGER PRIMARY KEY, folder_path TEXT, company_name TEXT, year INTEGER, comment TEXT, status TEXT,"
    " started_at TEXT, finished_at TEXT, hidden_at TEXT, created_at TEXT)",
    "CREATE TABLE documents (id INTEGER PRIMARY KEY, batch_id INTEGER, original_filename TEXT, stored_filename TEXT, file_size_bytes INTEGER,"
    " page_count INTEGER, company TEXT, year INTEGER, comment TEXT, status TEXT, error_message TEXT, created_at TEXT, deleted_at TEXT, document_type_id INTEGER)",
    "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT, street TEXT, zip_code TEXT, city TEXT, hrb_number TEXT, tax_number TEXT,"
    " vat_id TEXT, bank_name TEXT, iban TEXT, bic TEXT)",
    "CREATE TABLE invoice_extractions (id INTEGER PRIMARY KEY, document_id INTEGER, supplier_id INTEGER, supplier_name TEXT, vat_id TEXT, iban TEXT,"
    " invoice_number TEXT, invoice_date TEXT, total_amount REAL, raw_response TEXT, created_at TEXT, updated_at TEXT)",
    "CREATE TABLE order_positions (id INTEGER PRIMARY KEY, document_id INTEGER, position_index INTEGER, product_description TEXT, article_number TEXT,"
    " unit_price REAL, total_price REAL, quantity REAL, unit TEXT)",
]

RAW_OK = {
    "lieferant": {"name": "ACME GmbH", "anschrift": {"strasse": "Hauptstr. 1", "plz": "12345", "ort": "Berlin"}, "ust_id_nr": "DE123456789",
                  "bankverbindung": {"bank_name": "Sparkasse", "iban": "DE02120300000000202051", "bic": "BYLADEM1001"}},
    "rechnungsdaten": {"rechnungsnummer": "RE-1", "rechnungsdatum": "25.03.2025", "faelligkeit": "2025-04-25", "kundennummer": "K-1"},
    "positionen": [
        {"position_nr": 1, "artikelbezeichnung": "Schrott", "menge": "25.000", "mengeneinheit": "kg", "einzelpreis": "420,00",
         "gesamtpreis": "10.500,00", "steuersatz": 19.0, "preiseinheit": "je t"},
        {"position_nr": 2, "artikelbezeichnung": "Schrauben", "menge": 4230, "mengeneinheit": "Stück", "einzelpreis": 86, "gesamtpreis": 4042,
         "steuersatz": 19.0, "preiseinheit": "VPE", "verpackungsinhalt": 90, "verpackungsinhalt_einheit": "Stück"},
    ],
    "zahlungsinformationen": {"gesamtbetrag_netto": "14.542,00", "umsatzsteuer_zusammenfassung": [{"steuersatz": 19.0, "nettobetrag": 14542.0, "steuerbetrag": 2762.98}],
                              "gesamtbetrag_brutto": "17.304,98", "waehrung": "EUR", "zahlungsbedingungen": "14 Tage"},
}


@pytest.fixture()
def main_db(tmp_path):
    path = tmp_path / "main.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as c:
        for stmt in DDL:
            c.execute(text(stmt))
        c.execute(text("INSERT INTO import_batches VALUES (7, '/import/a', 'Muster', 2024, 'Test', 'done', NULL, NULL, NULL, '2024-05-01T10:00:00')"))
        c.execute(text("INSERT INTO suppliers VALUES (1, 'ACME GmbH', 'Hauptstr. 1', '12345', 'Berlin', NULL, NULL, 'DE123456789', 'Sparkasse', 'DE02120300000000202051', NULL),"
                       " (2, 'Nur Stammdaten KG', NULL, NULL, NULL, NULL, NULL, 'DE999', NULL, NULL, NULL)"))
        docs = [
            (101, 7, "a.pdf", "101.pdf", 10, 2, "Muster", 2024, None, "done", None, "2024-05-01T10:00:00", None, 1),
            (102, 7, "b.pdf", "102.pdf", 10, 1, "Muster", 2024, "Kommentar", "done", None, "2024-05-01T10:00:00", None, 3),      # Lieferschein
            (103, 7, "c.pdf", "103.pdf", 10, 1, "Muster", 2024, None, "done", None, "2024-05-01T10:00:00", None, 1),               # Rechnung ohne brauchbare KI-Antwort
            (104, 7, "d.pdf", "104.pdf", 10, 1, "Muster", 2024, None, "error", "KI-Fehler", "2024-05-01T10:00:00", None, 0),
            (105, 7, "e.pdf", "105.pdf", 10, 1, "Muster", 2024, None, "done", None, "2024-05-01T10:00:00", "2024-06-01T00:00:00", 1),  # in MAIN gelöscht
        ]
        for d in docs:
            c.execute(text("INSERT INTO documents VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j,:k,:l,:m,:n)"),
                      dict(zip("abcdefghijklmn", d)))
        c.execute(text("INSERT INTO invoice_extractions VALUES (1, 101, 1, 'ACME GmbH', 'DE123456789', 'DE02120300000000202051', 'RE-1', '2025-03-25', 17304.98, :raw, '2024-05-01T11:00:00', '2024-05-01T11:00:00')"),
                  {"raw": json.dumps(RAW_OK)})
        c.execute(text("INSERT INTO invoice_extractions VALUES (2, 103, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'KI-Fehler: Timeout', NULL, NULL)"))
        c.execute(text("INSERT INTO invoice_extractions VALUES (3, 105, NULL, 'X', NULL, NULL, NULL, NULL, NULL, :raw, NULL, NULL)"), {"raw": json.dumps(RAW_OK)})
        c.execute(text("INSERT INTO order_positions VALUES (1, 101, 0, 'Schrott', NULL, 420.0, 10500.0, 25000.0, 'kg'), (2, 101, 1, 'Schrauben', NULL, 86.0, 4042.0, 4230.0, 'Stück')"))
    storage = tmp_path / "main_storage" / "Muster_2024"
    storage.mkdir(parents=True)
    for i in (101, 102, 103, 105):                                    # 104 fehlt absichtlich
        (storage / f"{i}.pdf").write_bytes(b"%PDF-1.4 " + str(i).encode())
    return type("M", (), {"url": f"sqlite:///{path}", "storage": tmp_path / "main_storage", "engine": engine})


@pytest.fixture()
def world(db, sqlite_session_factory, main_db, tmp_path, monkeypatch):
    # pysqlite ohne Korrektur committet bei SAVEPOINT implizit (SQLAlchemy-Empfehlung) — PostgreSQL verhält sich nicht so
    engine = db.get_bind()
    engine.raw_connection().driver_connection.isolation_level = None
    event.listen(engine, "begin", lambda conn: conn.exec_driver_sql("BEGIN"))
    monkeypatch.setattr("app.config.settings.storage_path", str(tmp_path / "new_storage"))
    seed_default_units(db)
    seed_initial_schema(db)
    seed_default_rules(db)
    cfg = mm.get_config(db)
    cfg.source_url, cfg.source_storage_path = main_db.url, str(main_db.storage)
    from app.crud import schema_definition as sd
    cfg.mapping = mm.default_mapping(sd.get_active(db))
    db.commit()
    schema_models.clear_cache()
    return type("W", (), {"db": db, "factory": sqlite_session_factory, "main": main_db, "tmp": tmp_path})


def _run(w, **kw):
    return mm.run(w.factory, mm.RunOptions(dry_run=False, **kw))


def test_default_mapping_has_no_problems_for_seed_schema(world):
    from app.crud import schema_definition as sd
    version = sd.get_active(world.db)
    mapping = mm.default_mapping(version)
    assert mm.check_mapping(version, mapping) == []
    targets = {m["target"] for m in mapping}
    assert {"beleg.rechnungsnummer", "position.menge", "position.preiseinheit", "lieferant.iban"} <= targets


def test_path_walking_and_transforms():
    raw = {"a": {"b": [{"c": 1}, {"c": "2,5"}]}, "x": "je t", "n": "€/t", "p": "je 100 Stück"}
    assert mm.walk_path(raw, "a.b[*].c") == [1, "2,5"] and mm.walk_path(raw, "a.b[0].c") == 1 and mm.walk_path(raw, "a.zzz") is None
    assert mm.apply_transform([1, "2,5"], "sum") == 3.5 and mm.apply_transform(["", "x"], "first") == "x"
    assert mm.price_unit(raw["x"]) == "t" and mm.price_unit(raw["n"]) == "t" and mm.price_unit("pro Stück") == "Stück"
    assert mm.price_unit(raw["p"]) is None and mm.price_unit("") is None


def test_run_keeps_ids_copies_pdfs_and_runs_rules(world):
    state = _run(world)
    assert not state.get("failed"), state
    c = state["counts"]
    assert c["imported"] == 4 and c["no_data"] == 1 and c.get("error", 0) == 0          # 101, 102, 104 (in MAIN fehlerhaft), 105 · 103 ohne Daten
    db = world.db
    db.expire_all()
    # IDs bleiben, Batch-ID auch
    assert {d.id for d in db.query(Document)} == {101, 102, 103, 104, 105}
    batch = db.get(ImportBatch, 7)
    assert batch.company_name == "Muster" and batch.year == 2024 and Path(batch.storage_folder_path).name == "Muster_2024"
    # PDF liegt unter {id}.pdf im neuen Speicher; 104 fehlt
    for i in (101, 102, 103, 105):
        assert (Path(batch.storage_folder_path) / f"{i}.pdf").is_file()
    assert db.get(Document, 104).stored_filename is None and db.get(Document, 104).status == "error"
    assert db.get(Document, 105).soft_deleted is True and db.get(Document, 102).comment == "Kommentar" and db.get(Document, 102).document_type == 3
    # Datensätze aus der KI-Antwort
    root = db.query(ExtractedRecord).filter(ExtractedRecord.document_id == 101, ExtractedRecord.parent_record_id.is_(None)).one()
    assert root.data["rechnungsnummer"] == "RE-1" and root.data["rechnungsdatum"] == "2025-03-25" and root.data["steuerbetrag"] == 2762.98
    kids = db.query(ExtractedRecord).filter(ExtractedRecord.parent_record_id == root.id).order_by(ExtractedRecord.position_index).all()
    assert len(kids) == 2 and kids[0].data["menge"]["original_value"] == 25000 and kids[0].data["menge"]["original_unit"] == "kg"
    assert kids[0].data["preiseinheit"] == "t" and kids[1].data["preiseinheit"] == "VPE"
    assert kids[1].data["verpackungsinhalt"]["original_value"] == 90
    # Prüfregeln liefen: beide Positionen stimmen über die Preiseinheit, Netto + Steuer = Brutto
    assert root.data["_validation"]["status"] in ("ok", "warn") and all(c["status"] != "failed" for k in kids for c in k.data.get("_checks", []))
    # Lieferant über das Matching, Stammdaten-Lieferant ohne Belege auch
    from app.models.vendor import Vendor
    assert {v.name for v in db.query(Vendor)} == {"ACME GmbH", "Nur Stammdaten KG"}
    assert root.vendor_id is not None
    # Protokoll
    st = {i.old_id: i.status for i in db.query(MainMigrationItem).filter_by(source_table="document")}
    assert st == {101: "imported", 102: "imported", 103: "no_data", 104: "imported", 105: "imported"}


def test_second_run_skips_everything_and_new_ids_do_not_collide(world):
    _run(world)
    state = _run(world)
    assert state["counts"].get("skipped_done") == 5 and "imported" not in state["counts"]
    db = world.db
    db.expire_all()
    assert db.query(Document).count() == 5 and db.query(ExtractedRecord).filter_by(document_id=101).count() == 3


def test_occupied_id_is_reported_not_overwritten(world):
    db = world.db
    batch = ImportBatch(id=7, import_folder_path="/x", storage_folder_path="/s", company_name="Fremd", year=1, status="done")
    db.add(batch)
    db.commit()
    state = _run(world, old_document_ids=[101])
    assert state["counts"].get("error") == 1 and "schon belegt" in state["errors"][0]
    db.expire_all()
    assert db.get(ImportBatch, 7).company_name == "Fremd" and db.query(Document).count() == 0


def test_dry_run_changes_nothing(world):
    state = mm.run(world.factory, mm.RunOptions(dry_run=True))
    assert state["counts"]["imported"] == 4
    db = world.db
    db.expire_all()
    assert db.query(Document).count() == 0 and db.query(MainMigrationItem).count() == 0 and not (world.tmp / "new_storage").exists()


def test_sample_limit_and_retry(world):
    state = _run(world, limit=2)
    assert state["total"] == 2 and state["processed"] == 2
    state = _run(world)
    assert state["counts"]["skipped_done"] == 2 and state["counts"]["imported"] + state["counts"]["no_data"] == 3


def test_api_flow(world):
    def override():
        s = world.factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    try:
        cfg = client.get("/api/main-migration/config").json()
        assert cfg["has_url"] and cfg["source_url"].startswith("sqlite:")
        t = client.post("/api/main-migration/test").json()
        assert t["ok"] and t["counts"]["documents"] == 5 and t["storage"]["exists"] and t["storage"]["sample"].endswith(".pdf")
        fields = client.get("/api/main-migration/source-fields").json()
        assert "ext.invoice_number" in fields["ext"] and "raw.rechnungsdaten.rechnungsnummer" in fields["raw"] \
            and "rawpos.artikelbezeichnung" in fields["rawpos"] and "raw.zahlungsinformationen.umsatzsteuer_zusammenfassung[*].steuerbetrag" in fields["raw"]
        tg = client.get("/api/main-migration/targets").json()
        assert any(x["target"] == "position.menge" and x["data_type"] == "quantity" for x in tg["targets"])
        m = client.post("/api/main-migration/mapping/default").json()
        assert m["problems"] == []
        saved = client.put("/api/main-migration/mapping", json={"mapping": m["mapping"] + [{"target": "beleg.gibtsnicht", "sources": ["raw.x"]}]}).json()
        assert any("gibtsnicht" in p for p in saved["problems"])
        client.put("/api/main-migration/mapping", json={"mapping": m["mapping"]})
        pv = client.post("/api/main-migration/preview", json={"old_document_id": 101}).json()
        assert pv["schema_errors"] == [] and pv["parsed"]["rechnungsnummer"] == "RE-1" and len(pv["parsed"]["positionen"]) == 2
        assert client.post("/api/main-migration/preview", json={"old_document_id": 103}).json()["parsed"] is None
        r = _run(world)
        assert r["counts"]["imported"] == 4
        s = client.get("/api/main-migration/summary").json()
        assert s["documents"]["imported"] == 4 and s["documents"]["no_data"] == 1 and s["batches"] == 1 and s["suppliers"] == 2
        assert s["rules"]["ok"] + s["rules"]["warn"] >= 1
        # Passwort wird nicht ausgeliefert, maskierte URL überschreibt nichts
        client.put("/api/main-migration/config", json={"source_url": "postgresql://u:••••••@h/db"})
        assert client.get("/api/main-migration/config").json()["source_url"].startswith("sqlite:")
    finally:
        app.dependency_overrides.clear()


def test_mask_hides_password():
    from app.routers.main_migration import _mask
    assert _mask("postgresql://appuser:geheim@db:5432/x") == "postgresql://appuser:••••••@db:5432/x" and _mask(None) is None


def test_reanalyze_selects_no_data_and_flagged_documents(world):
    from app.models.workflow import Workflow
    from app.models.workflow_task import WorkflowTask
    from app.routers.main_migration import migrated_ids
    _run(world)
    db = world.db
    db.expire_all()
    # 103 ohne Daten; der Beleg 101 stimmt (Regeln ok) → nur 103 gehört zur KI
    assert migrated_ids(db, ["no_data"]) == [103]
    assert 101 not in migrated_ids(db, ["needs_review", "warn", "unchecked"])
    # Beleg 101 absichtlich auffällig machen → wird über den Regel-Status gefunden
    root = db.query(ExtractedRecord).filter(ExtractedRecord.document_id == 101, ExtractedRecord.parent_record_id.is_(None)).one()
    root.data = {**root.data, "_validation": {**root.data["_validation"], "status": "needs_review"}}
    db.commit()
    assert migrated_ids(db, ["needs_review"]) == [101] and migrated_ids(db, ["needs_review", "no_data"]) == [101, 103]

    wf = Workflow(name="W", graph=json.dumps({"nodes": [], "edges": []}))
    db.add(wf)
    db.commit()
    wf_id = wf.id
    db.close()                                      # geteilte SQLite-Verbindung im Test: nicht zwei offene Transaktionen gleichzeitig

    def override():
        s = world.factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        r = TestClient(app).post("/api/main-migration/reanalyze", json={"workflow_id": wf_id, "include": ["needs_review", "no_data"]})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200 and r.json()["enqueued"] == 2, r.text
    check = world.factory()
    assert check.query(WorkflowTask).count() == 2
    check.close()
