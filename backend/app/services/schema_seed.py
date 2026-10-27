"""
Seed: legt das bisherige feste Extraktionsschema als erste (aktive) Version an.

Idempotent — existiert schon eine Version, passiert nichts. Aufruf: `python -m app.scripts.seed_schema`.
Feldnamen entsprechen dem bisherigen KI-JSON (deutsch), damit Prompts und Export vertraut bleiben.
"""

from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.models.schema_def import SchemaVersion

# (name, Anzeigename, Typ, Beschreibung, Ausgabeformat)
BESTELLNUMMER = ("bestellnummer", "Bestellnr.", "text",
                 "Bestell-/Auftragsnummer des Kunden („Ihre Bestellnummer“, „Bestellnr.“, „Auftrags-Nr.“, „PO-Nummer“), sonst null", None)
EMPFAENGER_NAME = ("empfaenger_name", "Rechnungsempfänger", "text",
                   "Name des Rechnungsempfängers (Kunde, an den die Rechnung adressiert ist) wie gedruckt — NICHT der Aussteller", None)
EMPFAENGER_UST = ("empfaenger_ust_id_nr", "USt-IdNr. Empfänger", "text",
                  "USt-IdNr. des Rechnungsempfängers, nur wenn auf der Rechnung gedruckt (ohne Leerzeichen), sonst null", None)
POS_WAEHRUNG = ("waehrung", "Währung", "text", "Währung der Position als ISO-4217-Code (z. B. EUR)", None)
POS_NACHLASS_TEXT = ("preisnachlass_bezeichnung", "Nachlass-Bezeichnung", "text", "Bezeichnung des Nachlasses auf die Position (z. B. „Mengenrabatt“)", None)
# Steuerzusammenfassung: eine Zeile je Steuersatz
USTEUER_FIELDS = [
    ("steuersatz", "Steuersatz (%)", "decimal", "Steuersatz in Prozent (z. B. 19)", "0.00"),
    ("nettobetrag", "Nettobetrag (€)", "decimal", "Nettobetrag dieses Steuersatzes", "#,##0.00"),
    ("steuerbetrag", "Steuerbetrag (€)", "decimal", "Steuerbetrag dieses Steuersatzes", "#,##0.00"),
]
USTEUER_RELATION = "umsatzsteuer_zusammenfassung"
_BELEG = [
    ("rechnungsnummer", "Rechnungsnr.", "text", "Rechnungsnummer wie gedruckt", None),
    ("rechnungsdatum", "Rechnungsdatum", "date", "Rechnungsdatum (YYYY-MM-DD)", "DD.MM.YYYY"),
    ("faelligkeit", "Fälligkeit", "date", "Fälligkeitsdatum (YYYY-MM-DD)", "DD.MM.YYYY"),
    ("kundennummer", "Kundennr.", "text", "Kundennummer beim Lieferanten", None),
    BESTELLNUMMER,
    EMPFAENGER_NAME,
    EMPFAENGER_UST,
    ("gesamtbetrag_netto", "Netto (€)", "decimal", "Gesamtbetrag netto", "#,##0.00"),
    ("steuerbetrag", "Steuer (€)", "decimal", "Gesamter Steuerbetrag", "#,##0.00"),
    ("gesamtbetrag_brutto", "Brutto (€)", "decimal", "Gesamtbetrag brutto (Zahlbetrag)", "#,##0.00"),
    ("rabatt_betrag", "Rabatt (€)", "decimal", "Gesamtrabatt, falls ausgewiesen", "#,##0.00"),
    ("skonto_prozent", "Skonto (%)", "decimal", "Skonto in Prozent", "0.00"),
    ("skonto_betrag", "Skonto (€)", "decimal", "Skonto-Betrag", "#,##0.00"),
    ("skonto_frist_tage", "Skonto-Frist (Tage)", "integer", "Frist für Skonto in Tagen", "0"),
    ("zahlungsbedingungen", "Zahlungsbedingungen", "text", "Zahlungsbedingungen als Text", None),
    ("waehrung", "Währung", "text", "Währung (z. B. EUR)", None),
]
# Beschreibung geht ins LLM-Prompt; auch vom „Preiseinheit ergänzen“-Knopf der Prüfregeln verwendet
PRICE_UNIT_DESCRIPTION = (
    "Einheit, auf die sich der Einzelpreis bezieht, genau wie gedruckt (z. B. t, kg, Stück, m, VPE, Karton) — z. B. „420 €/t“ → t. "
    "Nur ausfüllen, wenn die Rechnung die Preiseinheit ausdrücklich nennt, sonst null. Keine Zahlen („je 100“ gehört in „Preis je (Einheiten)“)."
)

_POSITION = [
    ("position_nr", "Pos.", "integer", "Positionsnummer", "0"),
    ("artikelbezeichnung", "Artikelbezeichnung", "text", "Bezeichnung des Artikels", None),
    ("artikelnummer", "Artikelnummer", "text", "Artikelnummer des Lieferanten", None),
    ("menge", "Menge", "quantity", "Menge mit Einheit wie gedruckt", "#,##0.####"),
    ("einzelpreis", "Einzelpreis (€)", "decimal", "Einzelpreis netto wie gedruckt", "#,##0.00##"),
    ("gesamtpreis", "Gesamtpreis (€)", "decimal", "Positionspreis", "#,##0.00"),
    ("steuersatz", "Steuersatz (%)", "decimal", "Steuersatz der Position", "0.00"),
    ("preisnachlass_betrag", "Nachlass (€)", "decimal", "Nachlass auf die Position, Betrag", "#,##0.00"),
    ("preisnachlass_prozent", "Nachlass (%)", "decimal", "Nachlass auf die Position, Prozent", "0.00"),
    POS_WAEHRUNG,
    POS_NACHLASS_TEXT,
    ("preiseinheit", "Preiseinheit", "text", PRICE_UNIT_DESCRIPTION, None),
    ("preis_je_menge", "Preis je (Einheiten)", "decimal", "Der Einzelpreis gilt je N Einheiten (z. B. 100)", "#,##0.####"),
    ("verpackungsinhalt", "Inhalt je Verpackung", "quantity", "Inhalt einer Verpackung (VPE/VE) mit Einheit", "#,##0.####"),
]
_LIEFERANT = [
    ("name", "Name", "text", "Firmenname des Lieferanten", None),
    ("strasse", "Straße", "text", "Straße und Hausnummer", None),
    ("plz", "PLZ", "text", "Postleitzahl", None),
    ("ort", "Ort", "text", "Ort", None),
    ("land", "Land", "text", "Land", None),
    ("hrb_nummer", "HRB-Nr.", "text", "Handelsregisternummer", None),
    ("steuernummer", "Steuernr.", "text", "Steuernummer", None),
    ("ust_id_nr", "USt-IdNr.", "text", "Umsatzsteuer-Identifikationsnummer", None),
    ("bank_name", "Bank", "text", "Name der Bank", None),
    ("iban", "IBAN", "text", "IBAN", None),
    ("bic", "BIC", "text", "BIC/SWIFT", None),
]
# Systemfelder des Belegs: Name, Anzeigename, Typ, Funktion
_BELEG_SYSTEM = [
    ("dateiname", "Dateiname", "text", "file_name"),
    ("datei_hash", "Datei-Hash", "text", "file_hash"),
    ("eingangsdatum", "Eingangsdatum", "date", "received_at"),
    ("dokumenttyp_id", "Dokumenttyp-ID", "integer", "document_type_id"),
]


def _fill(db: Session, entity, rows) -> None:
    for name, display, dtype, desc, fmt in rows:
        sd.add_field(db, entity, name, display, dtype, description=desc, output_format=fmt)


def seed_initial_schema(db: Session) -> SchemaVersion | None:
    """Legt Version 1 an (aktiv). Gibt None zurück, wenn schon eine Definition existiert."""
    if db.query(SchemaVersion).first() is not None:
        return None
    version = sd.create_version(db, "Ausgangsschema (bisherige feste Felder)")
    beleg = sd.add_entity(db, version, "beleg", "Beleg", "Eingangsrechnung / Kopfdaten")
    position = sd.add_entity(db, version, "position", "Position", "Artikelzeile eines Belegs")
    lieferant = sd.add_entity(
        db, version, "lieferant", "Lieferant", "Stammdaten des Lieferanten",
        master_table="vendor", match_fields=["iban", "ust_id_nr", "name"],
    )
    _fill(db, beleg, _BELEG)
    for name, display, dtype, func_name in _BELEG_SYSTEM:
        sd.add_field(db, beleg, name, display, dtype, source="system", system_function=func_name)
    _fill(db, position, _POSITION)
    sd.add_field(
        db, position, "mengeneinheit_typ", "Einheitentyp", "enum", enum_values=[
            "stueck", "verpackung", "gewicht", "volumen", "laenge", "flaeche", "pauschal", "dienstleistung", "sonstige",
        ], description="Art der Mengeneinheit",
    )
    _fill(db, lieferant, _LIEFERANT)
    steuer = sd.add_entity(db, version, "umsatzsteuer", "Umsatzsteuer", "Zeile der Steuerzusammenfassung (je Steuersatz)")
    _fill(db, steuer, USTEUER_FIELDS)
    sd.add_relation(db, version, USTEUER_RELATION, "parent_child", beleg, steuer, "Steuerzusammenfassung des Belegs")
    sd.add_relation(db, version, "positionen", "parent_child", beleg, position, "Positionen des Belegs")
    sd.add_relation(db, version, "lieferant", "reference", beleg, lieferant, "Lieferant des Belegs")
    sd.activate(db, version)
    return version


# ─── Ergänzung eines bestehenden Schemas um die Standardteile (Bestellnummer, Währung/Nachlass-Text, Umsatzsteuer) ──

def missing_standard_parts(version: SchemaVersion | None) -> list[str]:
    """Teile des Ausgangsschemas, die in der Version fehlen (leer = vollständig oder nicht anwendbar)."""
    if version is None:
        return []
    ents = {e.name: e for e in version.entities}
    out: list[str] = []
    beleg, pos = ents.get("beleg"), ents.get("position")
    if beleg is not None and not any(f.name == BESTELLNUMMER[0] for f in beleg.fields):
        out.append("beleg.bestellnummer")
    if beleg is not None:
        for row in (EMPFAENGER_NAME, EMPFAENGER_UST):
            if not any(f.name == row[0] for f in beleg.fields):
                out.append(f"beleg.{row[0]}")
    if pos is not None:
        for row in (POS_WAEHRUNG, POS_NACHLASS_TEXT):
            if not any(f.name == row[0] for f in pos.fields):
                out.append(f"position.{row[0]}")
    if beleg is not None and "umsatzsteuer" not in ents:
        out.append("umsatzsteuer (Steuerzusammenfassung)")
    return out


def apply_standard_parts(db: Session) -> SchemaVersion | None:
    """Neuer Entwurf aus dem aktiven Schema → fehlende Standardteile ergänzen → aktivieren. None, wenn nichts fehlt."""
    version = sd.get_active(db)
    missing = missing_standard_parts(version)
    if not missing:
        return None
    draft = sd.create_draft_from(db, version, "Standardfelder ergänzt (Bestellnummer, Empfänger, Währung, Nachlass-Text, Umsatzsteuer)")
    ents = {e.name: e for e in draft.entities}
    beleg, pos = ents.get("beleg"), ents.get("position")
    if "beleg.bestellnummer" in missing:
        _fill(db, beleg, [BESTELLNUMMER])
    for row in (EMPFAENGER_NAME, EMPFAENGER_UST):
        if f"beleg.{row[0]}" in missing:
            _fill(db, beleg, [row])
    for row in (POS_WAEHRUNG, POS_NACHLASS_TEXT):
        if f"position.{row[0]}" in missing:
            _fill(db, pos, [row])
    if any(m.startswith("umsatzsteuer") for m in missing):
        steuer = sd.add_entity(db, draft, "umsatzsteuer", "Umsatzsteuer", "Zeile der Steuerzusammenfassung (je Steuersatz)")
        _fill(db, steuer, USTEUER_FIELDS)
        sd.add_relation(db, draft, USTEUER_RELATION, "parent_child", beleg, steuer, "Steuerzusammenfassung des Belegs")
    sd.activate(db, draft)
    return draft
