"""
Konfiguration für den Excel-Export.

Singleton-Tabelle (id=1) — speichert welche Spalten in den beiden
Excel-Sheets (Rechnungen, Positionen) erscheinen sollen.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON

from app.database import Base


# ── Verfügbare Felder (definitive Reihenfolge) ───────────────────────────────

INVOICE_FIELDS_DEFAULT: list[str] = [
    "beleg_nr", "dateiname", "status", "seiten",
    "rechnungsnr", "rechnungsdatum", "faelligkeit",
    "lieferant", "strasse", "plz", "ort",
    "ust_id", "steuernr", "hrb_nr", "kundennr", "bestellnr",
    "bank", "iban", "bic",
    "gesamtbetrag", "rabatt",
    "skonto_betrag", "skonto_prozent", "skonto_frist",
    "zahlungsbedingungen", "kommentar", "importiert_am",
]

POSITION_FIELDS_DEFAULT: list[str] = [
    "beleg_nr", "rechnungsnr", "lieferant", "pos_nr",
    "artikelbezeichnung", "artikelnummer", "menge", "einheit",
    "einzelpreis", "gesamtpreis", "steuersatz", "nachlass",
]

INVOICE_FIELD_LABELS: dict[str, str] = {
    "beleg_nr":            "Beleg-Nr.",
    "dateiname":           "Dateiname",
    "status":              "Status",
    "seiten":              "Seiten",
    "rechnungsnr":         "Rechnungsnr.",
    "rechnungsdatum":      "Rechnungsdatum",
    "faelligkeit":         "Fälligkeit",
    "lieferant":           "Lieferant",
    "strasse":             "Straße",
    "plz":                 "PLZ",
    "ort":                 "Ort",
    "ust_id":              "USt-IdNr.",
    "steuernr":            "Steuernr.",
    "hrb_nr":              "HRB-Nr.",
    "kundennr":            "Kundennr.",
    "bestellnr":           "Bestellnr.",
    "bank":                "Bank",
    "iban":                "IBAN",
    "bic":                 "BIC",
    "gesamtbetrag":        "Gesamtbetrag (€)",
    "rabatt":              "Rabatt (€)",
    "skonto_betrag":       "Skonto (€)",
    "skonto_prozent":      "Skonto (%)",
    "skonto_frist":        "Skonto Frist (Tage)",
    "zahlungsbedingungen": "Zahlungsbedingungen",
    "kommentar":           "Kommentar",
    "importiert_am":       "Importiert am",
}

POSITION_FIELD_LABELS: dict[str, str] = {
    "beleg_nr":           "Beleg-Nr.",
    "rechnungsnr":        "Rechnungsnr.",
    "lieferant":          "Lieferant",
    "pos_nr":             "Pos.",
    "artikelbezeichnung": "Artikelbezeichnung",
    "artikelnummer":      "Artikelnummer",
    "menge":              "Menge",
    "einheit":            "Einheit",
    "einzelpreis":        "Einzelpreis (€)",
    "gesamtpreis":        "Gesamtpreis (€)",
    "steuersatz":         "Steuersatz (%)",
    "nachlass":           "Nachlass",
}


# ── ORM-Modell ────────────────────────────────────────────────────────────────

class ExportConfig(Base):
    """
    Singleton-Zeile (id=1) mit den aktiven Export-Feldern.

    invoice_fields:  Liste der aktiven Spalten für Sheet „Rechnungen"
    position_fields: Liste der aktiven Spalten für Sheet „Positionen"
    """
    __tablename__ = "export_config"

    id              = Column(Integer, primary_key=True)
    invoice_fields  = Column(JSON, nullable=False, default=lambda: list(INVOICE_FIELDS_DEFAULT))
    position_fields = Column(JSON, nullable=False, default=lambda: list(POSITION_FIELDS_DEFAULT))
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
