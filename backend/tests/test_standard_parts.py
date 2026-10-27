"""Standardteile nachtragen (Bestellnummer, Währung/Nachlass-Text, Umsatzsteuer), mitgelieferte Prompts, Dokumenttypen."""

from sqlalchemy import create_engine, text

from app.crud import schema_definition as sd
from app.models.system_prompt import SystemPrompt
from app.routers.settings import _DOCUMENT_TYPES
from app.services import main_migration as mm
from app.services import prompt_seed, schema_seed
from app.services.schema_seed import apply_standard_parts, missing_standard_parts, seed_initial_schema


def _old_schema(db):
    """Ausgangsschema ohne die später ergänzten Teile (so sieht eine bestehende Installation aus)."""
    v = seed_initial_schema(db)
    draft = sd.create_draft_from(db, v, "alt")
    beleg = next(e for e in draft.entities if e.name == "beleg")
    pos = next(e for e in draft.entities if e.name == "position")
    for ent, names in ((beleg, ["bestellnummer"]), (pos, ["waehrung", "preisnachlass_bezeichnung"])):
        for f in list(ent.fields):
            if f.name in names:
                sd.delete_field(db, f)
    steuer = next(e for e in draft.entities if e.name == "umsatzsteuer")
    for r in list(draft.relations):
        if r.name == "umsatzsteuer_zusammenfassung":
            db.delete(r)
    db.flush()
    db.refresh(draft)
    sd.delete_entity(db, steuer)
    sd.activate(db, draft)
    db.commit()


def test_fresh_schema_is_complete(db):
    seed_initial_schema(db)
    assert missing_standard_parts(sd.get_active(db)) == []
    assert apply_standard_parts(db) is None


def test_existing_schema_gets_missing_parts_in_new_version(db):
    _old_schema(db)
    old = sd.get_active(db)
    assert len(missing_standard_parts(old)) == 4
    new = apply_standard_parts(db)
    db.commit()
    assert new.version_number == old.version_number + 1 and sd.get_active(db).id == new.id
    assert missing_standard_parts(new) == []
    assert {r.name for r in new.relations} >= {"umsatzsteuer_zusammenfassung"}
    assert apply_standard_parts(db) is None                      # idempotent


def test_prompts_seeded_once_and_never_overwritten(db):
    assert len(prompt_seed.seed_prompts(db)) == 2
    p = db.query(SystemPrompt).filter_by(name=prompt_seed.CLASSIFIER_NAME).one()
    assert p.type == 2 and "ID 2 existiert NICHT" in p.content
    ex = db.query(SystemPrompt).filter_by(name=prompt_seed.EXTRACTION_NAME).one()
    assert "preiseinheit" in ex.content and "JSON-Ausgabestruktur" not in ex.content
    p.content = "angepasst"
    db.flush()
    assert prompt_seed.seed_prompts(db) == [] and db.query(SystemPrompt).filter_by(name=prompt_seed.CLASSIFIER_NAME).one().content == "angepasst"


def test_document_types_follow_classifier_list():
    ids = [t["id"] for t in _DOCUMENT_TYPES]
    assert 2 not in ids and ids[:2] == [0, 1] and {3, 15, 16, 17} <= set(ids)
    names = {t["id"]: t["name"] for t in _DOCUMENT_TYPES}
    assert names[3].startswith("Gutschrift") and names[15] == "Sonstiges" and names[16] == "Zahlungsavis" and names[17].startswith("Mischbeleg")


def test_main_type_names_are_translated(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE document_types (id INTEGER, name TEXT)"))
        for i, n in [(1, "Eingangsrechnung"), (2, "Ausgangsrechnung"), (3, "Lieferschein"), (6, "Gutschrift / Storno"), (11, "Steuer- / Behördendokument"),
                     (13, "Kassenbon / Quittung"), (14, "Sonstiges kaufmännisches Dokument"), (15, "Unbekannt")]:
            c.execute(text("INSERT INTO document_types VALUES (:i, :n)"), {"i": i, "n": n})
    m = mm.load_type_map(eng)
    assert m == {1: 1, 2: 1, 3: 4, 6: 3, 11: 15, 13: 9, 14: 15, 15: 0}
    assert mm._map_type(2, m) == 1 and mm._map_type(99, m) == 15 and mm._map_type(0, m) == 0 and mm._map_type(7, {}) == 7


def test_unchanged_old_extraction_prompt_is_upgraded_but_edited_one_is_kept(db):
    db.add(SystemPrompt(name=prompt_seed.EXTRACTION_NAME, content=prompt_seed._EXTRACTION_BASE, type=2))
    db.flush()
    assert prompt_seed.prompts_outdated(db) == [prompt_seed.EXTRACTION_NAME]
    done = prompt_seed.seed_prompts(db)
    assert any("neue Fassung" in n for n in done)
    p = db.query(SystemPrompt).filter_by(name=prompt_seed.EXTRACTION_NAME).one()
    assert "17. Lieferant und Empfänger NIEMALS verwechseln" in p.content and "18. Menge je Position" in p.content
    assert prompt_seed.prompts_outdated(db) == []
    p.content = prompt_seed._EXTRACTION_BASE + "\n99. eigene Regel"
    db.flush()
    assert prompt_seed.prompts_outdated(db) == [] and prompt_seed.seed_prompts(db) == []
    assert "99. eigene Regel" in db.query(SystemPrompt).filter_by(name=prompt_seed.EXTRACTION_NAME).one().content
