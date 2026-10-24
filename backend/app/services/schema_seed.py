"""
Seed: legt das bisherige feste Extraktionsschema als erste (aktive) Version an.

Idempotent — existiert schon eine Version, passiert nichts. Aufruf: `python -m app.scripts.seed_schema`.
Feldnamen entsprechen dem bisherigen KI-JSON (deutsch), damit Prompts und Export vertraut bleiben.
"""

from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.models.schema_def import SchemaVersion

# (name, Anzeigename, Typ, Beschreibung, Ausgabeformat)
_BELEG = [
    ("rechnungsnummer", "Rechnungsnr.", "text", "Rechnungsnummer wie gedruckt", None),
    ("rechnungsdatum", "Rechnungsdatum", "date", "Rechnungsdatum (YYYY-MM-DD)", "DD.MM.YYYY"),
    ("faelligkeit", "Fälligkeit", "date", "Fälligkeitsdatum (YYYY-MM-DD)", "DD.MM.YYYY"),
    ("kundennummer", "Kundennr.", "text", "Kundennummer beim Lieferanten", None),
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
    sd.add_relation(db, version, "positionen", "parent_child", beleg, position, "Positionen des Belegs")
    sd.add_relation(db, version, "lieferant", "reference", beleg, lieferant, "Lieferant des Belegs")
    sd.activate(db, version)
    return version
