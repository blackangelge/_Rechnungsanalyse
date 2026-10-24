"""Prüfregeln: Parser/Rechnen, Fehlwerte = nicht prüfbar, Einheiten-Umrechnung über Gruppen, Speichern an der Analyse, API."""

from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.schema_def import ExtractedRecord
from app.models.validation_rule import ValidationRule
from app.services import rule_engine as re_
from app.services.rule_engine import RuleError, RuleSpec
from app.services.rule_seed import seed_default_rules
from tests.test_schema_export import world  # noqa: F401
from tests.test_schema_extraction import SAMPLE


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


def _eval(expr, data=None, children=None):
    node = re_.Node(1, "beleg", data or {})
    for ent, kids in (children or {}).items():
        node.children[ent] = [re_.Node(10 + i, ent, d, i, node) for i, d in enumerate(kids)]
    return re_.Evaluator(node).eval(re_.parse(expr))


def test_arithmetic_precedence_and_functions():
    assert _eval("2 + 3 * 4") == 14
    assert _eval("(2 + 3) * 4") == 20
    assert _eval("-3 + 10 / 4") == Decimal("-0.5")
    assert _eval("ROUND(10 / 3; 2)") == Decimal("3.33")
    assert _eval("ABS(-1) + MAX(1; 7; 3) + MIN(4; 2)") == 10
    assert _eval("0.1 + 0.2") == Decimal("0.3")                                      # Decimal, kein Float-Rauschen


@pytest.mark.parametrize("bad", ["", "1 +", "foo(1)", "1,5 * 2", "2 $ 3", "(1", "COUNT(a.b)"])
def test_syntax_errors_are_reported(bad):
    with pytest.raises(RuleError):
        re_.parse(bad)


def test_no_eval_possible():
    with pytest.raises(RuleError):
        re_.parse("__import__('os').system('x')")


def test_missing_value_is_not_guessed_but_coalesce_helps():
    node = re_.Node(1, "position", {"menge": 5, "preis": None})
    with pytest.raises(re_.MissingValue) as exc:
        re_.Evaluator(node).eval(re_.parse("menge * preis"))
    assert exc.value.names == ["preis"]
    assert re_.Evaluator(node).eval(re_.parse("menge * COALESCE(preis; 2)")) == 10


def test_division_by_zero_makes_rule_unchecked():
    spec = RuleSpec(None, "x", "beleg", "1 / a", "1", )
    r = re_.evaluate_rule(spec, re_.Node(1, "beleg", {"a": 0}))
    assert r["status"] == "skipped" and "Division" in r["message"]


def test_sum_sumif_count():
    pos = [{"p": 10, "satz": 19}, {"p": 5, "satz": 7}, {"p": 20, "satz": 19}, {"p": None, "satz": 19}]
    kids = {"position": pos}
    assert _eval("SUM(position.p)", children=kids) == 35                              # Position ohne Wert wird ausgelassen
    assert _eval("SUMIF(position.p; position.satz; satz)", {"satz": 19}, kids) == 30
    assert _eval("SUMIF(position.p; position.satz; 7)", children=kids) == 5
    assert _eval("SUM(position.p * position.satz / 100)", children=kids) == Decimal("1.9") + Decimal("0.35") + Decimal("3.8")
    assert _eval("COUNT(position)", children=kids) == 4


def test_comparison_tolerances_and_comparators():
    node = re_.Node(1, "beleg", {"a": 100.0, "b": 100.04})
    assert re_.evaluate_rule(RuleSpec(None, "t", "beleg", "a", "b", tolerance_abs=0.05), node)["status"] == "ok"
    assert re_.evaluate_rule(RuleSpec(None, "t", "beleg", "a", "b", tolerance_abs=0.01), node)["status"] == "failed"
    assert re_.evaluate_rule(RuleSpec(None, "t", "beleg", "a", "b", tolerance_pct=0.1), node)["status"] == "ok"      # 0,1 % von 100
    assert re_.evaluate_rule(RuleSpec(None, "t", "beleg", "a", "b", comparator="<="), node)["status"] == "ok"
    assert re_.evaluate_rule(RuleSpec(None, "t", "beleg", "b", "a", comparator="<="), node)["status"] == "failed"


def test_definition_check_against_schema(world):
    v = world.version
    ok = RuleSpec(None, "t", "position", "menge * einzelpreis", "gesamtpreis")
    assert re_.check_rule_spec(ok, v) == []
    assert any("Feld „kaputt“" in p for p in re_.check_rule_spec(RuleSpec(None, "t", "position", "kaputt", "gesamtpreis"), v))
    assert any("Entität" in p for p in re_.check_rule_spec(RuleSpec(None, "t", "gibtsnicht", "1", "1"), v))
    assert any("kein Zahlenfeld" in p for p in re_.check_rule_spec(RuleSpec(None, "t", "position", "artikelbezeichnung", "1"), v))
    assert re_.check_rule_spec(RuleSpec(None, "t", "beleg", "SUM(lieferant.iban)", "1"), v)
    assert re_.check_rule_spec(RuleSpec(None, "t", "beleg", "SUM(position.gesamtpreis)", "gesamtbetrag_netto"), v) == []
    assert re_.check_rule_spec(RuleSpec(None, "t", "position", "eltern.gesamtbetrag_netto", "1"), v) == []
    assert re_.check_rule_spec(RuleSpec(None, "t", "beleg", "gesamtbetrag_netto", "1", comparator="!="), v)


def test_default_rules_on_analysed_document(world):
    seed_default_rules(world.db)
    world.db.commit()
    doc = world.add_doc()
    res = re_.evaluate_document(world.db, doc.id, store=False)
    by_name = {}
    for r in res["results"]:
        by_name.setdefault(r["name"], []).append(r)
    assert by_name["Summe der Positionen = Netto"][0]["status"] == "ok"               # 3.960 = 3.960
    assert by_name["Netto + Steuer = Brutto"][0]["status"] == "ok"
    pos = {r["position_index"]: r for r in by_name["Positionspreis = Menge × Einzelpreis"]}
    assert pos[0]["status"] == "ok"                                                   # 4400 × 90 / 100 = 3960
    assert pos[1]["status"] == "skipped" and "gesamtpreis" in pos[1]["message"]       # kein Positionspreis → nicht prüfbar
    assert res["summary"]["status"] == "ok"


def test_results_are_stored_during_analysis_and_wrong_totals_flag_review(world):
    seed_default_rules(world.db)
    world.db.commit()
    bad = deepcopy(SAMPLE)
    bad["gesamtbetrag_brutto"] = "5.000,00"
    doc = world.add_doc(bad)
    root = world.db.query(ExtractedRecord).filter(ExtractedRecord.document_id == doc.id, ExtractedRecord.parent_record_id.is_(None)).one()
    assert root.data["_validation"]["status"] == "needs_review"
    failed = [c for c in root.data["_checks"] if c["status"] == "failed"]
    assert [c["name"] for c in failed] == ["Netto + Steuer = Brutto"] and failed[0]["diff"] == pytest.approx(-287.6)


def test_unit_conversion_through_groups(world):
    doc = world.add_doc()
    # Position 2: 25.880 kg → 25,88 t (Gruppe Masse, Basis t)
    root, _ = re_.load_tree(world.db, doc.id)
    kg_pos = root.children["position"][1]
    units = re_.load_unit_table(world.db)
    assert re_.Evaluator(kg_pos, units).eval(re_.parse("menge")) == Decimal("25880")
    assert re_.Evaluator(kg_pos, units).eval(re_.parse("menge.t")) == Decimal("25.88")
    assert re_.Evaluator(kg_pos, units).eval(re_.parse("menge.basis")) == Decimal("25.88")
    assert re_.Evaluator(kg_pos, units).eval(re_.parse("menge.g")) == Decimal("25880000")
    # Stück-Gruppe hat kein „t“ → nicht prüfbar statt falsch gerechnet
    piece = root.children["position"][0]
    with pytest.raises(re_.MissingValue):
        re_.Evaluator(piece, units).eval(re_.parse("menge.t"))
    spec = RuleSpec(None, "Tonnenpreis", "position", "menge.t * 412", "10662.56")
    assert re_.evaluate_rule(spec, kg_pos, units)["status"] == "ok"
    assert any("nicht angelegt" in p for p in re_.check_rule_spec(RuleSpec(None, "t", "position", "menge.furlong", "1"), world.version,
                                                                   re_.known_unit_keys(units)))


def test_evaluation_never_changes_stored_quantities(world):
    """Umrechnung gilt nur innerhalb der Formel; gespeicherte Mengen (Original + Einheit des LLM) bleiben unangetastet."""
    seed_default_rules(world.db)
    world.db.add(ValidationRule(name="t", entity_name="position", left_expr="menge.t", right_expr="menge.kg / 1000"))
    world.db.commit()
    doc = world.add_doc()

    def quantities():
        rows = world.db.query(ExtractedRecord).filter(ExtractedRecord.document_id == doc.id, ExtractedRecord.parent_record_id.isnot(None)).all()
        return {r.id: dict(r.data["menge"]) for r in rows}
    before = quantities()
    re_.evaluate_document(world.db, doc.id)
    world.db.commit()
    world.db.expire_all()
    assert quantities() == before
    assert any(q["original_unit"] == "kg" and q["original_value"] == 25880 for q in before.values())


def test_rules_api_crud_test_and_run(client, world):
    doc = world.add_doc()
    assert client.post("/api/validation-rules/seed-defaults").json()["created"] == 4
    assert client.post("/api/validation-rules/seed-defaults").json()["created"] == 0                      # nur wenn leer
    meta = client.get("/api/validation-rules/meta").json()
    pos = next(e for e in meta["entities"] if e["name"] == "position")
    assert {"menge", "einzelpreis", "gesamtpreis"} <= {f["name"] for f in pos["fields"]} and "kg" in meta["units"]
    beleg = next(e for e in meta["entities"] if e["name"] == "beleg")
    assert beleg["children"] == ["position"] and all(e["name"] != "lieferant" for e in meta["entities"])

    body = {"name": "Eigene", "entity_name": "position", "left_expr": "menge.kg * 2", "right_expr": "einzelpreis", "severity": "warn"}
    bad = client.post("/api/validation-rules", json={**body, "left_expr": "nixda"})
    assert bad.status_code == 422 and "nixda" in bad.json()["detail"]
    rid = client.post("/api/validation-rules", json=body).json()["id"]
    t = client.post("/api/validation-rules/test", json={**body, "document_id": doc.id}).json()
    assert len(t["results"]) == 3 and {r["status"] for r in t["results"]} <= {"ok", "failed", "skipped"}
    assert client.put(f"/api/validation-rules/{rid}", json={**body, "enabled": False}).json()["enabled"] is False
    assert client.delete(f"/api/validation-rules/{rid}").status_code == 204

    assert client.get(f"/api/validation-rules/document/{doc.id}").json()["summary"]["status"] in ("ok", "warn")
    run = client.post("/api/validation-rules/run", json={}).json()
    assert run["documents"] == 1 and run["rules"] == 4 and sum(run["counts"].values()) == 1
    assert len(client.get("/api/validation-rules").json()) == 4


def test_document_list_shows_rule_status_and_filters(client, world):
    seed_default_rules(world.db)
    world.db.commit()
    good = world.add_doc()
    bad = deepcopy(SAMPLE)
    bad["gesamtbetrag_brutto"] = "5.000,00"
    flagged = world.add_doc(bad)
    rows = {r["id"]: r for r in client.get("/api/documents").json()}
    assert rows[good.id]["rule_status"] == "ok" and rows[flagged.id]["rule_status"] == "needs_review"
    assert rows[flagged.id]["rule_failed"] == 1 and rows[good.id]["rule_skipped"] >= 1
    ids = client.get("/api/documents/ids", params={"validation_status": ["needs_review"]}).json()
    assert ids == [flagged.id]
    assert client.get("/api/documents/ids", params={"validation_status": ["ok"]}).json() == [good.id]
