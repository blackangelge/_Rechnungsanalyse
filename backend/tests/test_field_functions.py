"""Systemfunktionen: Kontext, jede Funktion, Parameter, Typ-Passung und Einbindung in die Schema-Validierung."""

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.crud import schema_definition as sd
from app.main import app
from app.models.import_batch import ImportBatch
from app.services import field_functions as ff
from app.services import normalizers
from app.services.schema_seed import seed_initial_schema


def ctx(**kw):
    base = dict(document_id=7, file_name="re.pdf", page_count=3, document_type_id=1, batch_company="ACME", batch_year=2025,
                received_at=datetime(2025, 3, 25, 10, 30, tzinfo=timezone.utc))
    base.update(kw)
    return ff.DocumentContext(**base)


def test_simple_functions():
    c = ctx()
    assert ff.run("page_count", None, c) == 3
    assert ff.run("file_name", None, c) == "re.pdf"
    assert ff.run("document_type_id", {}, c) == 1
    assert ff.run("batch_company", None, c) == "ACME" and ff.run("batch_year", None, c) == 2025
    assert ff.run("received_at", None, c) == "2025-03-25"
    assert ff.run("received_at", {"with_time": True}, c).startswith("2025-03-25T10:30")
    assert ff.run("constant", {"value": "K-100"}, c) == "K-100"


def test_missing_values_are_none_not_errors():
    c = ctx(page_count=None, document_type_id=None, received_at=None)
    assert ff.run("page_count", None, c) is None and ff.run("document_type_id", None, c) is None and ff.run("received_at", None, c) is None


def test_file_hash(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-demo")
    c = ctx(file_path=str(f))
    assert ff.run("file_hash", None, c) == hashlib.sha256(b"%PDF-demo").hexdigest()
    f.write_bytes(b"changed")                    # zweiter Aufruf nutzt den Zwischenspeicher
    assert ff.run("file_hash", None, c) == hashlib.sha256(b"%PDF-demo").hexdigest()
    with pytest.raises(ff.FunctionError, match="nicht gefunden"):
        ff.run("file_hash", None, ctx(file_path=str(tmp_path / "fehlt.pdf")))
    with pytest.raises(ff.FunctionError):
        ff.run("file_hash", None, ctx(file_path=None))


def test_unknown_function_and_bad_params():
    with pytest.raises(ff.FunctionError, match="unbekannt"):
        ff.run("rm_rf", None, ctx())
    with pytest.raises(ff.FunctionError):
        ff.run("constant", {"wert": 1}, ctx())          # falscher Parametername
    with pytest.raises(ff.FunctionError):
        ff.run("page_count", {"extra": 1}, ctx())       # keine Parameter erlaubt


def test_validate_function_types():
    assert ff.validate_function("page_count", None, "integer") == []
    assert ff.validate_function("page_count", None, "decimal") == []
    assert ff.validate_function("file_name", None, "integer")          # Text → Ganzzahl passt nicht
    assert ff.validate_function("received_at", None, "date") == []
    assert ff.validate_function("constant", {"value": 1}, "integer") == []
    assert ff.validate_function("constant", {}, "text")                # Pflichtparameter fehlt
    assert ff.validate_function("nope", None, "text")


def test_build_context_from_document(db):
    from app import crud
    batch = ImportBatch(company_name="ACME", year=2025, import_folder_path="/x", storage_folder_path="/s/ACME_2025")
    db.add(batch)
    db.flush()
    doc = crud.document.create(db, batch.id, "a.pdf", 1)
    doc.stored_filename = f"{doc.id}.pdf"
    doc.page_count = 4
    db.commit()
    c = ff.build_context(doc)
    assert c.document_id == doc.id and c.file_name == "a.pdf" and c.page_count == 4
    assert c.file_path == f"/s/ACME_2025/{doc.id}.pdf" and c.batch_company == "ACME" and c.batch_year == 2025
    assert c.document_type_id is None                  # 0 = noch nicht erkannt


def test_seeded_system_fields_use_valid_functions(db):
    version = seed_initial_schema(db)
    for ent in version.entities:
        for f in ent.fields:
            if f.source == "system" and not f.is_locked:
                assert ff.validate_function(f.system_function, f.system_params, f.data_type) == [], f.name


def test_schema_crud_validates_pipeline(db):
    draft = sd.create_draft_from(db, seed_initial_schema(db))
    beleg = next(e for e in draft.entities if e.name == "beleg")
    with pytest.raises(sd.SchemaError, match="unbekannte Systemfunktion"):
        sd.add_field(db, beleg, "x", "X", "text", source="system", system_function="rm_rf")
    with pytest.raises(sd.SchemaError, match="liefert integer"):
        sd.add_field(db, beleg, "x", "X", "date", source="system", system_function="page_count")
    with pytest.raises(sd.SchemaError, match="Normalisierer"):
        sd.add_field(db, beleg, "y", "Y", "decimal", normalizers=[{"name": "nope"}])
    ok = sd.add_field(
        db, beleg, "z", "Z", "decimal",
        normalizers=[{"name": "trim"}, {"name": "parse_german_number"}, {"name": "round", "params": {"digits": 2}}],
    )
    assert [s["name"] for s in ok.normalizers] == ["trim", "parse_german_number", "round"]       # Reihenfolge bleibt erhalten
    with pytest.raises(sd.SchemaError, match="Normalisierer"):
        sd.update_field(db, ok, normalizers=[{"name": "round", "params": {"digits": 50}}])
    sd.update_field(db, ok, normalizers=[{"name": "parse_german_number"}, {"name": "trim"}])
    assert [s["name"] for s in ok.normalizers] == ["parse_german_number", "trim"]
    # Typwechsel auf ein Systemfeld prüft die Funktion mit
    sys_field = next(f for f in beleg.fields if f.name == "dateiname")
    with pytest.raises(sd.SchemaError, match="liefert text"):
        sd.update_field(db, sys_field, data_type="date")
    assert sd.validate_version(draft) == []


def test_functions_api():
    c = TestClient(app)
    names = {d["name"] for d in c.get("/api/schema/functions").json()}
    assert names == set(ff.FUNCTIONS) and {"page_count", "file_name", "file_hash", "received_at", "document_type_id"} <= names
