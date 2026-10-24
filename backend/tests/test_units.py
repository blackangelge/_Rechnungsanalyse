"""Mengeneinheiten: Regeln (Basis, Aliasse), Normierung, Prüfliste mit Zuordnung, API."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.crud import units as cu
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch
from app.models.schema_def import ExtractedRecord
from app.models.units import Unit, UnitGroup
from app.services.schema_seed import seed_initial_schema
from app.services.units import UnitResolver, alias_key, normalize_quantity
from app.services.units_seed import seed_default_units


@pytest.fixture()
def seeded(db):
    seed_default_units(db)
    db.commit()
    return db


def unit(db, symbol):
    return db.query(Unit).filter(Unit.symbol == symbol).one()


def test_seed_has_one_base_per_group_and_is_idempotent(seeded):
    assert seed_default_units(seeded) is False
    for g in seeded.query(UnitGroup):
        bases = [u for u in g.units if u.is_base]
        assert len(bases) == 1 and bases[0].factor == 1, g.name


def test_normalize_mass_to_tonnes(seeded):
    r = UnitResolver(seeded)
    q, issues = normalize_quantity(r, {"value": "25.880,00", "unit": "kg"})
    assert not issues and q["status"] == "ok"
    assert q["original_value"] == 25880 and q["original_unit"] == "kg" and q["value"] == 25.88 and q["unit"] == "t"
    q, _ = normalize_quantity(r, {"value": 2, "unit": "Tonnen"})
    assert q["value"] == 2 and q["unit"] == "t"
    q, _ = normalize_quantity(r, {"value": 500, "unit": "g"})
    assert q["value"] == 0.0005
    q, _ = normalize_quantity(r, {"value": 1, "unit": "mg"})
    assert q["value"] == 1e-9


def test_case_rules(seeded):
    r = UnitResolver(seeded)
    # Tonne: "T" ist ausdrücklich Alias; kg-Einheit ist case-sensitiv, aber "KG"/"Kilo"/"kilo" sind Aliasse
    assert r.resolve("T").symbol == "t" and r.resolve("KG").symbol == "kg" and r.resolve("kilo").symbol == "kg"
    assert r.resolve("Kg") is None                      # nicht angelegt und Einheit unterscheidet Groß/Klein
    assert r.resolve("ML").symbol == "ml"               # Einheit ignoriert Groß/Klein
    assert r.resolve("STK.").symbol == "Stk"            # Schluss-Punkt egal, Stück ignoriert Groß/Klein
    assert r.resolve("") is None and r.resolve(None) is None


def test_volume_length_count(seeded):
    r = UnitResolver(seeded)
    assert normalize_quantity(r, {"value": 250, "unit": "ml"})[0]["value"] == 0.25
    assert normalize_quantity(r, {"value": 2, "unit": "hl"})[0]["value"] == 200
    assert normalize_quantity(r, {"value": 150, "unit": "cm"})[0]["value"] == 1.5
    q = normalize_quantity(r, {"value": 4400, "unit": "Stück"})[0]
    assert q["value"] == 4400 and q["unit"] == "Stk"


def test_unknown_no_unit_invalid_and_wrong_group(seeded):
    r = UnitResolver(seeded)
    q, issues = normalize_quantity(r, {"value": 5, "unit": "Fass"})
    assert q["status"] == "unknown_unit" and q["value"] is None and q["original_unit"] == "Fass" and q["original_value"] == 5
    assert issues[0]["kind"] == "unknown_unit"
    q, issues = normalize_quantity(r, {"value": 5, "unit": None})
    assert q["status"] == "no_unit" and q["original_value"] == 5 and not issues
    q, issues = normalize_quantity(r, {"value": "abc", "unit": "kg"})
    assert q["status"] == "invalid" and issues
    q, _ = normalize_quantity(r, {"value": None, "unit": None})
    assert q["status"] == "no_value"
    mass = seeded.query(UnitGroup).filter_by(name="Masse").one()
    q, issues = normalize_quantity(r, {"value": 5, "unit": "l"}, group_id=mass.id)
    assert q["status"] == "wrong_group" and q["value"] is None and issues
    assert normalize_quantity(r, {"value": 5, "unit": "kg"}, group_id=mass.id)[0]["status"] == "ok"
    assert normalize_quantity(r, 7)[0]["status"] == "no_unit"           # nackte Zahl


def test_group_rules(db):
    g = cu.create_group(db, "Zeit")
    with pytest.raises(cu.UnitError, match="Basiseinheit"):
        cu.create_unit(db, g, "Minute", "min", 60)                      # erst Basis
    base = cu.create_unit(db, g, "Stunde", "h", 1, is_base=True)
    with pytest.raises(cu.UnitError, match="schon die Basiseinheit"):
        cu.create_unit(db, g, "Tag", "d", 1, is_base=True)
    with pytest.raises(cu.UnitError, match="Faktor 1"):
        cu.create_unit(db, cu.create_group(db, "X"), "A", "a", 2, is_base=True)
    with pytest.raises(cu.UnitError, match="größer als 0"):
        cu.create_unit(db, g, "Tag", "d", 0)
    mins = cu.create_unit(db, g, "Minute", "min", 60)
    assert mins.factor == 60
    with pytest.raises(cu.UnitError, match="existiert in der Gruppe"):
        cu.create_unit(db, g, "Minute", "min2", 60)
    with pytest.raises(cu.UnitError, match="Faktor 1"):
        cu.update_unit(db, base, factor=2)
    with pytest.raises(cu.UnitError, match="erst"):
        cu.delete_unit(db, base)
    with pytest.raises(cu.UnitError, match="existiert bereits"):
        cu.create_group(db, "Zeit")


def test_alias_conflicts_are_global_and_case_aware(seeded):
    kg, ml, l = unit(seeded, "kg"), unit(seeded, "ml"), unit(seeded, "l")
    with pytest.raises(cu.UnitError, match="bereits vergeben"):
        cu.add_alias(seeded, kg, "t")                                   # gehört zur Tonne
    with pytest.raises(cu.UnitError, match="bereits vergeben"):
        cu.add_alias(seeded, ml, "L")                                   # Liter ignoriert Groß/Klein → "L" = "l"
    with pytest.raises(cu.UnitError, match="bereits vergeben"):
        cu.add_alias(seeded, l, "ML")                                   # umgekehrt
    cu.add_alias(seeded, kg, "Kgm")                                     # frei
    # Groß/Klein-sensitive Einheiten dürfen sich nur in der Schreibweise unterscheiden
    t = unit(seeded, "t")
    with pytest.raises(cu.UnitError, match="bereits vergeben"):
        cu.add_alias(seeded, t, "kg")
    with pytest.raises(cu.UnitError, match="Alias fehlt"):
        cu.add_alias(seeded, t, "  ")
    # Umschalten auf "ohne Groß/Klein" wird abgelehnt, wenn Aliasse dann kollidieren (t / T)
    with pytest.raises(cu.UnitError, match="kollidiert"):
        cu.update_unit(seeded, t, case_insensitive=True)
    assert t.case_insensitive is False


def _record(db):
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path="/s/x")
    db.add(batch)
    db.flush()
    from app import crud
    doc = crud.document.create(db, batch.id, "a.pdf", 1)
    version = seed_initial_schema(db)
    pos = next(e for e in version.entities if e.name == "position")
    rec = ExtractedRecord(document_id=doc.id, entity_id=pos.id, schema_version_id=version.id, data={})
    db.add(rec)
    db.flush()
    return doc, rec


def test_review_assign_requantifies_records(seeded):
    doc, rec = _record(seeded)
    q, issues = normalize_quantity(UnitResolver(seeded), {"value": "5", "unit": "Fass"})
    rec.data = {"menge": q, "_issues": [{"field": "menge", **issues[0]}]}
    cu.add_review_item(seeded, record_id=rec.id, document_id=doc.id, field_name="menge", original_unit="Fass")
    cu.add_review_item(seeded, record_id=rec.id, document_id=doc.id, field_name="menge", original_unit="Fass")     # idempotent
    seeded.commit()
    groups = cu.review_groups(seeded)
    assert groups == [{"alias": "Fass", "count": 1, "documents": 1, "examples": [{"record_id": rec.id, "document_id": doc.id, "field": "menge"}]}]
    liter = unit(seeded, "l")
    result = cu.assign_review(seeded, "Fass", liter)
    seeded.commit()
    assert result == {"alias": "Fass", "items": 1, "updated": 1}
    seeded.refresh(rec)
    assert rec.data["menge"]["status"] == "ok" and rec.data["menge"]["value"] == 5 and rec.data["menge"]["unit"] == "l"
    assert "_issues" not in rec.data
    assert cu.review_groups(seeded) == []
    assert UnitResolver(seeded).resolve("Fass").symbol == "l"           # Alias ist jetzt dauerhaft angelegt
    with pytest.raises(cu.UnitError, match="Keine offenen"):
        cu.assign_review(seeded, "Fass", liter)


def test_review_ignore(seeded):
    doc, rec = _record(seeded)
    cu.add_review_item(seeded, record_id=rec.id, document_id=doc.id, field_name="menge", original_unit="Zzz")
    assert cu.ignore_review(seeded, "Zzz") == 1
    assert cu.review_groups(seeded) == []


def test_alias_key():
    assert alias_key("  Stk.  ") == "Stk" and alias_key("m  2") == "m 2" and alias_key(".") == "."


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


def test_api_flow(client, sqlite_session_factory):
    s = sqlite_session_factory()
    seed_default_units(s)
    s.commit()
    s.close()
    groups = client.get("/api/units").json()
    mass = next(g for g in groups if g["name"] == "Masse")
    kg = next(u for u in mass["units"] if u["symbol"] == "kg")
    assert kg["factor"] == 1000 and "Kilo" in [a["alias"] for a in kg["aliases"]]
    r = client.post(f"/api/units/{kg['id']}/aliases", json={"alias": "t"})
    assert r.status_code == 409 and "bereits vergeben" in r.json()["detail"]
    assert client.post(f"/api/units/{kg['id']}/aliases", json={"alias": "kgg"}).status_code == 201
    r = client.post("/api/units/convert", json={"value": "1.500,5", "unit": "kgg"})
    assert r.json()["quantity"]["value"] == 1.5005
    assert client.post("/api/units/groups", json={"name": "Masse"}).status_code == 409
    gid = client.post("/api/units/groups", json={"name": "Zeit"}).json()["id"]
    assert client.post(f"/api/units/groups/{gid}/units", json={"name": "Min", "symbol": "min", "factor": 60}).status_code == 409
    base = client.post(f"/api/units/groups/{gid}/units", json={"name": "Stunde", "symbol": "h", "is_base": True})
    assert base.status_code == 201
    assert client.patch(f"/api/units/{base.json()['id']}", json={"factor": 3}).status_code == 409
    assert client.get("/api/units/review").json() == []
    assert client.delete(f"/api/units/groups/{gid}").status_code == 204
    assert client.delete("/api/units/999").status_code == 404
