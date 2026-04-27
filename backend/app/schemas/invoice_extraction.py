"""
Pydantic-Schemas für KI-extrahierte Rechnungsdaten.

OrderPositionRead      — Einzelne Rechnungsposition (Zeile)
InvoiceExtractionRead  — Vollständige Extraktion inkl. aller Felder und KI-Rohdaten

Hinweis: Diese Schemas werden in DocumentDetail eingebettet und
direkt über GET /api/documents/{id} ausgeliefert.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class OrderPositionRead(BaseModel):
    """
    Eine einzelne Rechnungsposition (Zeile) aus einer Eingangsrechnung.

    position_index (0-basiert) entspricht der Reihenfolge im Originaldokument
    und wird für die Zuordnung im raw_response-JSON verwendet (z.B. Steuersatz im Excel-Export).
    """

    id: int
    document_id: int
    position_index: int          # 0-basierte Reihenfolge im Dokument
    product_name: str | None
    product_description: str | None
    article_number: str | None   # Artikelnummer des Lieferanten
    unit_price_netto: Decimal | None
    unit_price_brutto: Decimal | None
    tax: Decimal | None          # Steuerbetrag dieser Position
    quantity: Decimal | None
    unit: str | None             # Mengeneinheit (z.B. "Stück", "kg")
    discount: str | None         # Nachlass-Angabe (Freitext)

    model_config = ConfigDict(from_attributes=True)


class InvoiceExtractionRead(BaseModel):
    """
    KI-extrahierte Daten einer Eingangsrechnung.

    vendor_id enthält den Lieferantennamen als Freitext (kein FK zur vendor-Tabelle).
    raw_response enthält das vollständige JSON der KI-Antwort für spätere Auswertung.
    """

    id: int
    document_id: int
    # Lieferant (Freitext-Name, kein FK)
    vendor_id: str | None
    # Rechnungsidentifikation
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    # Beträge
    total_amount_netto: Decimal | None
    total_amount_brutto: Decimal | None
    total_tax_value: Decimal | None     # Gesamtsteuerbetrag (€)
    total_tax: Decimal | None           # Steuersatz (%)
    discount_amount: Decimal | None     # Rabattbetrag
    cash_discount_amount: Decimal | None  # Skonto-Betrag
    # Zahlungsbedingungen
    payment_terms: str | None
    # KI-Rohdaten
    raw_response: str | None            # Vollständige KI-JSON-Antwort
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
