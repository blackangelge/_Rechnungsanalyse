"""Dynamisches Schema: Versionierung, gesperrte Standardfelder, Seed, extracted_records."""

import pytest

from app import crud
from app.crud import schema_definition as sd
from app.models.import_batch import ImportBatch
from app.models.schema_def import ExtractedRecord, SchemaEntity
from app.services.schema_seed import seed_initial_schema


@pytest.fixture()
def seeded(db):
    version = seed_initial_schema(db)
    db.commit()
    return version


def _ent(version, name):
    return next(e for e in version.entities if e.name == name)


def test_seed_creates_active_version_with_entities_and_relations(db, seeded):
    active = sd.get_active(db)
    assert active.id == seeded.id and active.version_number == 1
    assert [e.name for e in active.entities] == ["beleg", "position", "lieferant"]
    assert {r.name for r in active.relations} == {"positionen", "lieferant"}
    assert sd.validate_version(active) == []


def test_seed_is_idempotent(db, seeded):
    assert seed_initial_schema(db) is None
    assert len(sd.list_versions(db)) == 1


def test_standard_fields_exist_and_are_locked(db, seeded):
    for name in ("beleg", "position"):
        fields = {f.name: f for f in _ent(seeded, name).fields}
        for std in ("document_id", "vendor_id"):
            assert fields[std].is_locked and fields[std].source == "system"
    # Stammdaten haben keinen Belegbezug
    assert "document_id" not in {f.name for f in _ent(seeded, "lieferant").fields}


def test_active_version_is_immutable(db, seeded):
    beleg = _ent(seeded, "beleg")
    with pytest.raises(sd.SchemaError, match="unveränderlich"):
        sd.add_field(db, beleg, "neues_feld", "Neu", "text")
    with pytest.raises(sd.SchemaError, match="unveränderlich"):
        sd.update_field(db, beleg.fields[-1], display_name="x")


def test_draft_copy_edit_and_activate_keeps_old_version(db, seeded):
    draft = sd.create_draft_from(db, seeded, "Kostenstelle ergänzen")
    assert draft.version_number == 2 and draft.status == "draft"
    beleg = _ent(draft, "beleg")
    sd.add_field(db, beleg, "kostenstelle", "Kostenstelle", "text", description="Kostenstelle falls gedruckt")
    sd.activate(db, draft)
    db.commit()
    assert sd.get_active(db).id == draft.id
    old = sd.get_version(db, seeded.id)
    assert old.status == "archived"
    assert "kostenstelle" not in {f.name for f in _ent(old, "beleg").fields}
    assert "kostenstelle" in {f.name for f in _ent(draft, "beleg").fields}
    # Kopie ist vollständig und von der alten unabhängig
    assert len(_ent(draft, "position").fields) == len(_ent(old, "position").fields)
    assert {r.name for r in draft.relations} == {"positionen", "lieferant"}
    assert all(r.source_entity_id in {e.id for e in draft.entities} for r in draft.relations)


def test_only_one_draft_at_a_time_and_discard(db, seeded):
    draft = sd.create_draft_from(db, seeded)
    with pytest.raises(sd.SchemaError, match="bereits einen Entwurf"):
        sd.create_draft_from(db, seeded)
    sd.discard_draft(db, draft)
    assert len(sd.list_versions(db)) == 1
    sd.create_draft_from(db, seeded)


def test_locked_standard_fields_cannot_be_changed_or_deleted(db, seeded):
    draft = sd.create_draft_from(db, seeded)
    doc_id = next(f for f in _ent(draft, "beleg").fields if f.name == "document_id")
    with pytest.raises(sd.SchemaError, match="Standardfeld"):
        sd.update_field(db, doc_id, display_name="Anderer Name")
    with pytest.raises(sd.SchemaError, match="Standardfeld"):
        sd.delete_field(db, doc_id)
    with pytest.raises(sd.SchemaError, match="reserviert"):
        sd.add_field(db, _ent(draft, "beleg"), "vendor_id", "Lieferant", "text")


def test_field_validation(db, seeded):
    beleg = _ent(sd.create_draft_from(db, seeded), "beleg")
    with pytest.raises(sd.SchemaError, match="Name muss"):
        sd.add_field(db, beleg, "Ungültig Name", "x", "text")
    with pytest.raises(sd.SchemaError, match="Datentyp"):
        sd.add_field(db, beleg, "abc", "x", "float")
    with pytest.raises(sd.SchemaError, match="Enum"):
        sd.add_field(db, beleg, "abc", "x", "enum")
    with pytest.raises(sd.SchemaError, match="Funktion"):
        sd.add_field(db, beleg, "abc", "x", "text", source="system")
    with pytest.raises(sd.SchemaError, match="existiert bereits"):
        sd.add_field(db, beleg, "rechnungsnummer", "x", "text")


def test_match_field_cannot_be_deleted(db, seeded):
    lieferant = _ent(sd.create_draft_from(db, seeded), "lieferant")
    iban = next(f for f in lieferant.fields if f.name == "iban")
    with pytest.raises(sd.SchemaError, match="Matching"):
        sd.delete_field(db, iban)


def test_new_entity_gets_standard_fields_and_relation_rules(db, seeded):
    draft = sd.create_draft_from(db, seeded)
    ent = sd.add_entity(db, draft, "steuerzeile", "Steuerzeile")
    assert {"document_id", "vendor_id"} <= {f.name for f in ent.fields}
    sd.add_relation(db, draft, "steuerzeilen", "parent_child", _ent(draft, "beleg"), ent)
    with pytest.raises(sd.SchemaError, match="Stammdaten"):
        sd.add_relation(db, draft, "bad", "parent_child", _ent(draft, "lieferant"), ent)
    with pytest.raises(sd.SchemaError, match="verwendet"):
        sd.delete_entity(db, ent)
    sd.activate(db, draft)


def test_extracted_records_link_version_parent_and_vendor(db, seeded):
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path="/s/x")
    db.add(batch)
    db.flush()
    doc = crud.document.create(db, batch.id, "a.pdf", 1)
    vendor = crud.vendor.find_or_create(db, "ACME GmbH", iban="DE02120300000000202051")
    beleg_ent, pos_ent = _ent(seeded, "beleg"), _ent(seeded, "position")
    beleg = ExtractedRecord(
        document_id=doc.id, entity_id=beleg_ent.id, schema_version_id=seeded.id, vendor_id=vendor.id,
        data={"document_id": doc.id, "vendor_id": vendor.id, "rechnungsnummer": "R-1"},
    )
    db.add(beleg)
    db.flush()
    for i in range(2):
        db.add(ExtractedRecord(
            document_id=doc.id, entity_id=pos_ent.id, schema_version_id=seeded.id, parent_record_id=beleg.id,
            vendor_id=vendor.id, position_index=i, data={"artikelbezeichnung": f"A{i}"},
        ))
    db.commit()
    rows = db.query(ExtractedRecord).filter(ExtractedRecord.parent_record_id == beleg.id).order_by(ExtractedRecord.position_index).all()
    assert [r.data["artikelbezeichnung"] for r in rows] == ["A0", "A1"]
    assert all(r.schema_version_id == seeded.id for r in rows)
    # Löschen des Belegs löscht die Datensätze mit (CASCADE über documents bzw. parent)
    db.delete(beleg)
    db.commit()
    assert db.query(ExtractedRecord).count() == 0
