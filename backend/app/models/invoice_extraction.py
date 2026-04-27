"""
ORM-Modell für KI-extrahierte Rechnungsdaten (Tabelle: invoice_extractions).

Eine InvoiceExtraction enthält alle strukturierten Felder, die die KI aus einem
Eingangsrechnungs-PDF extrahiert hat. Pro Dokument gibt es maximal einen Eintrag
(unique=True auf document_id).

raw_response speichert die vollständige JSON-Antwort der KI für spätere Auswertung
oder Neuparsing, ohne die KI erneut zu befragen.

Lieferanten werden über die vendor-Tabelle dedupliziert (find_or_create).
vendor_id hier enthält den Lieferantennamen als Freitext-Fallback.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InvoiceExtraction(Base):
    """
    KI-extrahierte Daten einer Eingangsrechnung.

    Wird nur für Dokumente vom Typ 'Eingangsrechnung' (document_type_id=1) angelegt.
    Für andere Dokumententypen (Lieferschein, Mahnung etc.) gibt es keinen
    InvoiceExtraction-Eintrag — die KI-Statistiken werden dort direkt im
    Document-Datensatz (doc_ki_*-Felder) gespeichert.
    """

    __tablename__ = "invoice_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Lieferantenname als Freitext (kein FK — Deduplication über vendor-Tabelle separat)
    vendor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)          # Lieferantenname (Freitext-Fallback)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)     # Rechnungsnummer
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)             # Rechnungsdatum (normalisiert auf ISO)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)                 # Fälligkeitsdatum
    total_amount_netto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)   # Nettobetrag
    total_amount_brutto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # Bruttobetrag (Gesamtbetrag)
    total_tax_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)      # Gesamtsteuerbetrag (€)
    total_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)            # Steuersatz (%)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)      # Rabattbetrag
    cash_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True) # Skonto-Betrag
    payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)             # Zahlungsbedingungen (Freitext)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)              # Vollständige KI-JSON-Antwort
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship("Document", back_populates="extraction")  # noqa: F821
