"""Phase 4: Pydantic-Modelle aus der Definition, Prompt, strukturierter Aufruf (lokaler Stub-Server), Normalisierung, Speichern."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from app import crud
from app.crud import schema_definition as sd
from app.models.import_batch import ImportBatch
from app.models.schema_def import ExtractedRecord
from app.models.units import UnitReviewItem
from app.models.vendor import Vendor
from app.services import schema_extraction as se
from app.services import schema_models, schema_prompt, structured_ai
from app.services.schema_pipeline import SchemaValidationFailed
from app.services.schema_seed import seed_initial_schema
from app.services.units_seed import seed_default_units

SAMPLE = {
    "lieferant": {
        "name": "ACME GmbH", "strasse": "Hauptstr. 1", "plz": "12345", "ort": "Berlin", "land": "DE", "hrb_nummer": None,
        "steuernummer": None, "ust_id_nr": "DE 123 456 789", "bank_name": "Sparkasse", "iban": "DE02 1203 0000 0000 2020 51", "bic": "BYLADEM1001",
    },
    "rechnungsnummer": " RE-2024/0815 ", "rechnungsdatum": "25.03.2025", "faelligkeit": "2025-04-25", "kundennummer": "K-1", "bestellnummer": "B-77",
    "gesamtbetrag_netto": "3.960,00 €", "steuerbetrag": 752.4, "gesamtbetrag_brutto": "4.712,40", "rabatt_betrag": None,
    "skonto_prozent": 2, "skonto_betrag": None, "skonto_frist_tage": "14", "zahlungsbedingungen": "14 Tage 2 % Skonto", "waehrung": "EUR",
    "umsatzsteuer_zusammenfassung": [{"steuersatz": 19, "nettobetrag": "3.960,00", "steuerbetrag": "752,40"}],
    "positionen": [
        {"position_nr": 1, "artikelbezeichnung": "Schrauben M8", "artikelnummer": "S-8", "menge": {"value": "4.400", "unit": "Stück"},
         "einzelpreis": "90,00", "gesamtpreis": "3.960,00", "steuersatz": 19, "preisnachlass_betrag": None, "preisnachlass_prozent": None,
         "waehrung": "EUR", "preisnachlass_bezeichnung": None, "preiseinheit": None, "preis_je_menge": 100, "verpackungsinhalt": None, "mengeneinheit_typ": "stueck"},
        {"position_nr": 2, "artikelbezeichnung": "Splitt", "artikelnummer": None, "menge": {"value": "25.880,00", "unit": "kg"},
         "einzelpreis": "0,41", "gesamtpreis": None, "steuersatz": None, "preisnachlass_betrag": None, "preisnachlass_prozent": None,
         "waehrung": "EUR", "preisnachlass_bezeichnung": None, "preiseinheit": None, "preis_je_menge": None, "verpackungsinhalt": None, "mengeneinheit_typ": "gewicht"},
        {"position_nr": 3, "artikelbezeichnung": "Fassware", "artikelnummer": None, "menge": {"value": 5, "unit": "Fass"},
         "einzelpreis": None, "gesamtpreis": None, "steuersatz": None, "preisnachlass_betrag": None, "preisnachlass_prozent": None,
         "waehrung": "EUR", "preisnachlass_bezeichnung": None, "preiseinheit": None, "preis_je_menge": None, "verpackungsinhalt": None, "mengeneinheit_typ": None},
    ],
}


@pytest.fixture()
def world(db):
    seed_default_units(db)
    version = seed_initial_schema(db)
    batch = ImportBatch(company_name="T", year=2026, import_folder_path="/x", storage_folder_path="/s/x")
    db.add(batch)
    db.flush()
    doc = crud.document.create(db, batch.id, "re.pdf", 1)
    doc.page_count = 2
    doc.stored_filename = f"{doc.id}.pdf"
    db.commit()
    schema_models.clear_cache()
    return SimpleNamespace(db=db, version=version, doc=doc)


# ─── Modell + Schema ─────────────────────────────────────────────────────────

def test_model_contains_only_llm_fields_and_relations(world):
    built = schema_models.build_extraction_model(world.version)
    props = built.json_schema["properties"]
    assert built.root == "beleg"
    assert "document_id" not in props and "vendor_id" not in props           # Standardfelder nie im Schema
    assert "seitenanzahl" not in props and "dateiname" not in props           # Systemfelder auch nicht
    assert {"rechnungsnummer", "gesamtbetrag_brutto", "positionen", "lieferant"} <= set(props)
    pos = props["positionen"]["items"]["properties"]
    assert "artikelbezeichnung" in pos and "document_id" not in pos
    assert set(built.json_schema["required"]) == set(props)                   # alle Schlüssel Pflicht (aber nullable)
    assert built.json_schema["additionalProperties"] is False


def test_schema_is_inlined_without_refs(world):
    text = json.dumps(schema_models.build_extraction_model(world.version).json_schema)
    assert "$ref" not in text and "$defs" not in text


def test_enum_and_quantity_types(world):
    schema = schema_models.build_extraction_model(world.version).json_schema
    pos = schema["properties"]["positionen"]["items"]["properties"]
    enum_variants = pos["mengeneinheit_typ"]["anyOf"]
    assert any(v.get("enum") and "gewicht" in v["enum"] for v in enum_variants) and {"type": "null"} in enum_variants
    q = next(v for v in pos["menge"]["anyOf"] if v.get("type") == "object")
    assert set(q["properties"]) == {"value", "unit"}


def test_response_format_shape_and_cache(world):
    built = schema_models.build_extraction_model(world.version)
    rf = schema_models.response_format(built)
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["schema"] is built.json_schema
    assert schema_models.build_extraction_model(world.version) is built       # Cache je Version


def test_new_version_changes_schema_old_stays(world):
    draft = sd.create_draft_from(world.db, world.version)
    beleg = next(e for e in draft.entities if e.name == "beleg")
    sd.add_field(world.db, beleg, "kostenstelle", "Kostenstelle", "text", description="falls gedruckt")
    sd.activate(world.db, draft)
    new = schema_models.build_extraction_model(draft)
    old = schema_models.build_extraction_model(world.version)
    assert "kostenstelle" in new.json_schema["properties"] and "kostenstelle" not in old.json_schema["properties"]


def test_root_must_be_unique(world):
    draft = sd.create_draft_from(world.db, world.version)
    sd.add_entity(world.db, draft, "zweite", "Zweite")
    with pytest.raises(schema_models.SchemaModelError, match="Wurzel"):
        schema_models.root_entity(draft)


# ─── Prompt ──────────────────────────────────────────────────────────────────

def test_prompt_is_deterministic_and_contains_descriptions(world):
    p1 = schema_prompt.build_system_prompt(world.version)
    assert p1 == schema_prompt.build_system_prompt(world.version)
    assert "rechnungsnummer: Rechnungsnr. — Rechnungsnummer wie gedruckt" in p1
    assert "mengeneinheit_typ" in p1 and "Erlaubte Werte: stueck" in p1
    assert "document_id" not in p1 and "datei_hash" not in p1
    assert "ZUSATZANWEISUNG" not in p1
    p2 = schema_prompt.build_system_prompt(world.version, "Beträge sind in CHF.")
    assert p2.startswith(p1) and p2.endswith("Beträge sind in CHF.")          # variabler Teil immer hinten → Prefix gleich


# ─── Stub-Server ─────────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, content=None, status=200):
        outer = self
        self.requests: list[dict] = []
        self.content, self.status = content, status

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                payload = json.dumps({
                    "choices": [{"message": {"content": outer.content if isinstance(outer.content, str) else json.dumps(outer.content)}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "completion_tokens_details": {"reasoning_tokens": 0}},
                }).encode() if outer.status == 200 else b'{"error": {"message": "kaputt"}}'
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()          # Port wirklich freigeben (sonst hängen Verbindungen bis zum Timeout)


@pytest.fixture()
def stub():
    s = _Stub(SAMPLE)
    yield s
    s.close()


def _config(stub):
    return SimpleNamespace(api_url=stub.url, api_key="sk-test", model_name="Qwen3.6", max_tokens=4096, temperature=0, reasoning="off")


def test_request_has_response_format_prefix_order_and_no_tools(world, stub):
    out = se.run_sync(world.db, world.doc, ["data:image/png;base64,AAA", "data:image/png;base64,BBB"], _config(stub))
    assert out.save is not None
    req = stub.requests[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer sk-test"
    body = req["body"]
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in body and body["stream"] is False and body["model"] == "Qwen3.6"
    system, user = body["messages"]
    assert system["role"] == "system" and system["content"] == out.prepared.system_prompt
    parts = user["content"]
    assert parts[0] == {"type": "text", "text": schema_prompt.USER_TEXT}               # fester Text VOR den Bildern
    assert [p["type"] for p in parts[1:]] == ["image_url", "image_url"]
    # zweiter Beleg: identischer Präfix (System + feste Anweisung) → Prefix Caching
    out2 = se.run_sync(world.db, world.doc, ["data:image/png;base64,CCC"], _config(stub))
    b2 = stub.requests[1]["body"]
    assert b2["messages"][0] == system and b2["messages"][1]["content"][0] == parts[0] and b2["response_format"] == body["response_format"]
    assert out2.prepared.system_prompt == out.prepared.system_prompt


def test_tools_extension_point(world, stub):
    tools = [{"type": "function", "function": {"name": "rechne", "parameters": {"type": "object", "properties": {}}}}]
    se.run_sync(world.db, world.doc, [], _config(stub), tools=tools)
    assert stub.requests[0]["body"]["tools"] == tools


# ─── Pipeline ────────────────────────────────────────────────────────────────

def _records(db, doc):
    root = db.query(ExtractedRecord).filter_by(document_id=doc.id, parent_record_id=None).one()
    from app.models.schema_def import SchemaEntity
    pos_ids = [e.id for e in db.query(SchemaEntity).filter_by(name="position").all()]
    kids = (db.query(ExtractedRecord).filter(ExtractedRecord.parent_record_id == root.id, ExtractedRecord.entity_id.in_(pos_ids))
            .order_by(ExtractedRecord.position_index).all())
    return root, kids


def test_full_pipeline_normalizes_and_saves(world, stub):
    out = se.run_sync(world.db, world.doc, [], _config(stub))
    assert out.save.record_count == 5 and out.result.ki_stats["input_tokens"] == 1000
    root, kids = _records(world.db, world.doc)
    d = root.data
    assert d["rechnungsnummer"] == "RE-2024/0815"                                    # getrimmt
    assert d["rechnungsdatum"] == "2025-03-25" and d["faelligkeit"] == "2025-04-25"   # ISO
    assert d["gesamtbetrag_netto"] == 3960 and d["gesamtbetrag_brutto"] == 4712.4 and d["steuerbetrag"] == 752.4
    assert d["skonto_frist_tage"] == 14 and d["skonto_prozent"] == 2
    assert d["document_id"] == world.doc.id and d["seitenanzahl"] == 2 and d["dateiname"] == "re.pdf"   # Systemfelder
    assert d["eingangsdatum"] is not None and d["dokumenttyp_id"] is None
    assert root.schema_version_id == world.version.id
    # Positionen: Reihenfolge, Mengen normiert, Originalwerte erhalten
    assert [k.data["artikelbezeichnung"] for k in kids] == ["Schrauben M8", "Splitt", "Fassware"]
    q = kids[1].data["menge"]
    assert q["original_value"] == 25880 and q["original_unit"] == "kg" and q["value"] == 25.88 and q["unit"] == "t" and q["status"] == "ok"
    assert kids[0].data["menge"]["value"] == 4400 and kids[0].data["menge"]["unit"] == "Stk"
    assert kids[0].data["einzelpreis"] == 90 and kids[0].data["preis_je_menge"] == 100
    assert all(k.data["document_id"] == world.doc.id and k.vendor_id == root.vendor_id for k in kids)


def test_vendor_matched_by_configured_fields_not_duplicated(world, stub):
    se.run_sync(world.db, world.doc, [], _config(stub))
    vendor = world.db.query(Vendor).one()
    assert vendor.name == "ACME GmbH" and vendor.vat_id == "DE 123 456 789" and vendor.city == "Berlin"
    assert [b.iban for b in vendor.bank_accounts] == ["DE02 1203 0000 0000 2020 51"]
    # zweiter Beleg: anderer Name, gleiche IBAN (anders geschrieben) → derselbe Lieferant (iban hat Vorrang vor name)
    other = json.loads(json.dumps(SAMPLE))
    other["lieferant"].update({"name": "A.C.M.E. Gesellschaft", "iban": "de02120300000000202051", "ust_id_nr": None})
    stub.content = other
    se.run_sync(world.db, world.doc, [], _config(stub))
    assert world.db.query(Vendor).count() == 1
    root, _ = _records(world.db, world.doc)
    assert root.vendor_id == vendor.id and root.data["_master"]["lieferant"]["name"] == "A.C.M.E. Gesellschaft"
    # neuer Lieferant bei unbekannten Merkmalen
    third = json.loads(json.dumps(SAMPLE))
    third["lieferant"].update({"name": "Ganz Anders KG", "iban": None, "ust_id_nr": "DE999999999"})
    stub.content = third
    se.run_sync(world.db, world.doc, [], _config(stub))
    assert world.db.query(Vendor).count() == 2


def test_reanalysis_replaces_records_and_review_items(world, stub):
    se.run_sync(world.db, world.doc, [], _config(stub))
    se.run_sync(world.db, world.doc, [], _config(stub))
    assert world.db.query(ExtractedRecord).filter_by(document_id=world.doc.id).count() == 5
    assert world.db.query(UnitReviewItem).filter_by(document_id=world.doc.id, status="open").count() == 1


def test_unknown_unit_goes_to_review_list_and_record_still_saved(world, stub):
    out = se.run_sync(world.db, world.doc, [], _config(stub))
    assert out.save.unknown_units == 1 and out.save.issue_count >= 1
    _, kids = _records(world.db, world.doc)
    q = kids[2].data["menge"]
    assert q["status"] == "unknown_unit" and q["original_unit"] == "Fass" and q["original_value"] == 5 and q["value"] is None
    assert kids[2].data["_issues"][0]["kind"] == "unknown_unit" and kids[2].data["_issues"][0]["field"] == "menge"
    item = world.db.query(UnitReviewItem).one()
    assert item.original_unit == "Fass" and item.status == "open" and item.document_id == world.doc.id


def test_invalid_values_become_issues_not_crashes(world, stub):
    bad = json.loads(json.dumps(SAMPLE))
    bad["rechnungsdatum"] = "irgendwann"
    bad["gesamtbetrag_netto"] = "viel"
    bad["skonto_frist_tage"] = "14,5"
    stub.content = bad
    out = se.run_sync(world.db, world.doc, [], _config(stub))
    root, _ = _records(world.db, world.doc)
    assert root.data["rechnungsdatum"] is None and root.data["gesamtbetrag_netto"] is None and root.data["skonto_frist_tage"] is None
    fields = {i["field"] for i in root.data["_issues"]}
    assert {"rechnungsdatum", "gesamtbetrag_netto", "skonto_frist_tage"} <= fields
    assert out.save.issue_count >= 3


def test_required_field_missing_is_flagged(world, stub):
    draft = sd.create_draft_from(world.db, world.version)
    f = next(f for e in draft.entities if e.name == "beleg" for f in e.fields if f.name == "rechnungsnummer")
    sd.update_field(world.db, f, required=True)
    sd.activate(world.db, draft)
    world.db.commit()
    schema_models.clear_cache()
    body = json.loads(json.dumps(SAMPLE))
    body["rechnungsnummer"] = None
    stub.content = body
    se.run_sync(world.db, world.doc, [], _config(stub))
    root, _ = _records(world.db, world.doc)
    assert root.schema_version_id == draft.id                                  # aktive Version wird genommen
    assert any(i["kind"] == "missing_required" and i["field"] == "rechnungsnummer" for i in root.data["_issues"])


def test_normalizer_chain_runs_before_type_coercion(world, stub):
    draft = sd.create_draft_from(world.db, world.version)
    f = next(f for e in draft.entities if e.name == "beleg" for f in e.fields if f.name == "kundennummer")
    sd.update_field(world.db, f, normalizers=[{"name": "remove_chars", "params": {"chars": "-"}}, {"name": "case", "params": {"mode": "lower"}}])
    sd.activate(world.db, draft)
    world.db.commit()
    schema_models.clear_cache()
    body = json.loads(json.dumps(SAMPLE))
    body["kundennummer"] = "AB-12-CD"
    stub.content = body
    se.run_sync(world.db, world.doc, [], _config(stub))
    root, _ = _records(world.db, world.doc)
    assert root.data["kundennummer"] == "ab12cd"


def test_schema_violation_saves_nothing(world, stub):
    se.run_sync(world.db, world.doc, [], _config(stub))
    broken = json.loads(json.dumps(SAMPLE))
    broken["positionen"][0]["mengeneinheit_typ"] = "kiste"          # nicht im Enum
    del broken["rechnungsnummer"]                                    # Schlüssel fehlt
    broken["unbekannt"] = 1
    stub.content = broken
    out = se.run_sync(world.db, world.doc, [], _config(stub))
    assert out.save is None and len(out.validation_errors) >= 3
    assert world.db.query(ExtractedRecord).filter_by(document_id=world.doc.id).count() == 5    # alter Stand bleibt


def test_validate_output_direct(world):
    built = schema_models.build_extraction_model(world.version)
    from app.services.schema_pipeline import validate_output
    assert validate_output(built, SAMPLE)["rechnungsnummer"] == " RE-2024/0815 "
    with pytest.raises(SchemaValidationFailed) as exc:
        validate_output(built, {"lieferant": None})
    assert exc.value.messages


# ─── Fehlerfälle des Aufrufs ─────────────────────────────────────────────────

def test_http_errors_are_returned_not_raised(world):
    for status, expect in ((503, "KI überlastet"), (500, "KI-Fehler: HTTP 500")):
        s = _Stub(SAMPLE, status)
        out = se.run_sync(world.db, world.doc, [], _config(s))
        s.close()
        assert out.save is None and out.result.error.startswith(expect)
    dead = SimpleNamespace(api_url="http://127.0.0.1:9", api_key=None, model_name="m", max_tokens=10, temperature=0, reasoning="off")
    out = se.run_sync(world.db, world.doc, [], dead)
    assert out.result.error.startswith("KI-Verbindungsfehler")


def test_non_json_answer(world):
    s = _Stub("das ist kein json")
    out = se.run_sync(world.db, world.doc, [], _config(s))
    s.close()
    assert out.save is None and "kein gültiges JSON" in out.result.error
    s = _Stub("```json\n" + json.dumps(SAMPLE) + "\n```")
    out = se.run_sync(world.db, world.doc, [], _config(s))
    s.close()
    assert out.save is not None                                                # Markdown-Zaun wird toleriert


def test_no_active_schema(db):
    with pytest.raises(se.ExtractionSetupError, match="Kein aktives Schema"):
        se.prepare(db)
    version = sd.create_version(db)
    db.commit()
    with pytest.raises(se.ExtractionSetupError, match="Entwurf"):
        se.prepare(db, version.id)
