"""Excel-Export darf an auffälligen KI-Daten nie scheitern (Steuerzeichen, Listen statt Objekten, Texte statt Zahlen, Sonderzeichen im Namen)."""

import json, pytest
from tests.test_import_hide_export import client, world  # noqa: F401
from app import crud

CASES = {
 "ctrl": dict(fields={"supplier_name": "Lief\x0bX\x00", "invoice_number": "R\x1f1"}, raw="{}"),
 "list_lieferant": dict(fields={}, raw=json.dumps({"lieferant": ["a"], "rechnungsdaten": "x"})),
 "list_value": dict(fields={}, raw=json.dumps({"lieferant": {"ust_id_nr": ["DE1","DE2"], "anschrift": {"plz": {"a":1}}}})),
 "pos_str": dict(fields={}, raw=json.dumps({"positionen": [{"steuersatz": "19%"}, "x"]})),
 "skonto_str": dict(fields={}, raw=json.dumps({"zahlungsinformationen": {"skonto": {"prozent": "2,0 %", "frist_tage": "14 Tage"}}})),
 "skonto_list": dict(fields={}, raw=json.dumps({"zahlungsinformationen": {"skonto": [1]}})),
}

@pytest.mark.parametrize("name", list(CASES))
def test_case(client, world, name):
    c = CASES[name]
    d = world.docs["B1"]
    crud.document.save_extraction(world.db, d.id, c["fields"], [], c["raw"])
    r = client.get(f"/api/imports/{world.batches['B'].id}/export")
    assert r.status_code == 200, (name, r.text[:200])

def test_filename(client, world):
    b = world.batches["B"]; b.company_name = "Zażółć €uro \"AG\""; world.db.commit()
    r = client.get(f"/api/imports/{b.id}/export")
    assert r.status_code == 200
