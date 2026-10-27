"""Eigene Gesellschaften: Abgleich (Schreibweisen), Rollenprüfung Lieferant/Empfänger, Liste (alles optional), Vorschläge."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.own_company import OwnCompany
from app.models.schema_def import ExtractedRecord
from app.services import own_companies as oc
from app.services import rule_engine as re_
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


def _parties(db, *rows):
    for r in rows:
        db.add(OwnCompany(**r))
    db.commit()
    return oc.load_parties(db)


def test_match_tolerates_spelling_variants_and_uses_any_identifier(db):
    p = _parties(db, {"name": "Muster Handels GmbH", "aliases": ["Muster-Handel GmbH & Co. KG"], "vat_ids": ["DE 999 888 777"],
                      "ibans": ["DE02 1203 0000 0000 2020 51", "DE89 3704 0044 0532 0130 00"]},
                 {"name": None, "ibans": ["DE75 5121 0800 1245 1261 99"]})                # Gesellschaft nur mit IBAN
    assert oc.match(p, name="MUSTER HANDELS G.m.b.H.")[1] == "name"
    assert oc.match(p, name="Muster-Handel GmbH & Co KG")[1] == "name"                    # weitere Schreibweise
    assert oc.match(p, name="Mustr Handels GmbH")[1] == "aehnlich"                        # Tippfehler
    assert oc.match(p, name="Ganz anders", vat="de999888777")[1] == "ust_id"
    assert oc.match(p, iban="DE89370400440532013000")[0].id == p.companies[0].id          # zweite IBAN
    assert oc.match(p, iban="DE75 5121 0800 1245 1261 99")[0].id == p.companies[1].id
    assert oc.match(p, name="Mueller Bau GmbH") is None
    assert oc.match(oc.Parties(db), name="Muster Handels GmbH") is None                   # leere Liste → nie ein Treffer


def _check(world, parties, sample):
    doc = world.add_doc(sample)
    res = re_.evaluate_document(world.db, doc.id, [], store=False, parties=parties)
    return doc, {r["name"]: r for r in res["results"]}, res["summary"]


def test_supplier_equal_to_own_company_is_flagged(world):
    p = _parties(world.db, {"name": "ACME GmbH"})                                          # der gelesene Lieferant ist „ACME GmbH“
    _, by, summary = _check(world, p, SAMPLE)
    r = by["Lieferant ist keine eigene Gesellschaft"]
    assert r["status"] == "failed" and "ACME GmbH" in r["message"] and "Empfänger" in r["message"]
    assert summary["status"] == "needs_review"


def test_clean_invoice_passes_and_unknown_recipient_only_warns(world):
    p = _parties(world.db, {"name": "Muster Handels GmbH"})
    _, by, summary = _check(world, p, SAMPLE)
    assert by["Lieferant ist keine eigene Gesellschaft"]["status"] == "ok"
    assert by["Rechnungsempfänger ist eine eigene Gesellschaft"]["status"] == "ok"
    assert summary["status"] == "ok"
    other = deepcopy(SAMPLE)
    other["empfaenger_name"] = "Fremde Firma AG"
    _, by, summary = _check(world, p, other)
    assert by["Rechnungsempfänger ist eine eigene Gesellschaft"]["status"] == "failed" and summary["status"] == "warn"


def test_supplier_equals_recipient_needs_no_list(world):
    swapped = deepcopy(SAMPLE)
    swapped["empfaenger_name"] = "ACME GmbH"                                                # Lieferant und Empfänger gleich
    _, by, summary = _check(world, oc.Parties(world.db), swapped)
    assert by["Lieferant ist nicht der Rechnungsempfänger"]["status"] == "failed" and summary["status"] == "needs_review"
    assert "Lieferant ist keine eigene Gesellschaft" not in by                              # ohne Liste keine Listenprüfung


def test_known_vendor_with_other_name_warns(world):
    _check(world, oc.Parties(world.db), SAMPLE)                                             # legt den Lieferanten „ACME GmbH“ an
    other = deepcopy(SAMPLE)
    other["lieferant"]["name"] = "Völlig Andere Firma"                                      # dieselbe IBAN, anderer Name
    _, by, _ = _check(world, oc.Parties(world.db), other)
    assert by["Lieferant passt zum bekannten Lieferanten"]["status"] == "failed"


def test_results_stored_with_document(world):
    p = _parties(world.db, {"name": "ACME GmbH"})
    doc = world.add_doc(SAMPLE)
    re_.evaluate_document(world.db, doc.id, [], parties=p)
    world.db.commit()
    root = world.db.query(ExtractedRecord).filter(ExtractedRecord.document_id == doc.id, ExtractedRecord.parent_record_id.is_(None)).one()
    assert root.data["_validation"]["status"] == "needs_review"
    assert any(c["name"] == "Lieferant ist keine eigene Gesellschaft" and c["status"] == "failed" for c in root.data["_checks"])


def test_api_all_fields_optional_but_one_identifier_and_lists(client):
    assert client.post("/api/own-companies", json={}).status_code == 422
    assert client.post("/api/own-companies", json={"note": "nur Notiz"}).status_code == 422
    a = client.post("/api/own-companies", json={"name": "Alpha GmbH"}).json()                 # nur Name
    b = client.post("/api/own-companies", json={"ibans": ["DE02 1203 0000 0000 2020 51", " ", "DE89 3704 0044 0532 0130 00"]}).json()   # nur IBANs
    assert b["name"] is None and len(b["ibans"]) == 2 and a["ibans"] == []
    c = client.put(f"/api/own-companies/{a['id']}", json={"name": "Alpha GmbH", "aliases": ["Alpha"], "vat_ids": ["DE1", "DE1"], "active": False}).json()
    assert c["aliases"] == ["Alpha"] and c["vat_ids"] == ["DE1"] and c["active"] is False
    assert len(client.get("/api/own-companies").json()) == 2
    assert client.delete(f"/api/own-companies/{b['id']}").status_code == 204
    assert client.put("/api/own-companies/999", json={"name": "x"}).status_code == 404


def test_inactive_company_is_ignored(db):
    p = _parties(db, {"name": "Alpha GmbH", "active": False})
    assert p.companies == [] and oc.match(p, name="Alpha GmbH") is None


def test_suggestions_group_frequent_recipients_and_skip_known(world):
    for name in ("Muster Handels GmbH", "MUSTER HANDELS GMBH", "Muster Handels G.m.b.H.", "Einmalig AG"):
        s = deepcopy(SAMPLE)
        s["empfaenger_name"] = name
        world.add_doc(s)
    sug = oc.suggestions(world.db)
    assert len(sug) == 1 and sug[0]["count"] == 3 and sug[0]["vat_id"] == "DE999888777"      # „Einmalig AG“ (1×) nicht
    _parties(world.db, {"name": "Muster Handels GmbH"})
    assert oc.suggestions(world.db) == []
