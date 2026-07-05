"""
SQLAlchemy-ORM-Modell für die documents-Tabelle.

Ein Document repräsentiert eine einzelne importierte PDF-Datei.

Relationen:
  batch           — ImportBatch (n:1, Kaskade-Löschung)
  extraction      — InvoiceExtraction (1:1, nur für Eingangsrechnungen)
  order_positions — OrderPosition (1:n, Rechnungspositionen)
  token_counts    — DocumentTokenCount (1:n, Token-Statistiken pro KI-Durchlauf)

Convenience-Properties:
  company, year   — delegieren an batch.company_name / batch.year
  total_amount    — delegiert an extraction.total_amount_brutto
  invoice_number  — delegiert an extraction.invoice_number
  supplier_name   — delegiert an extraction.vendor_id
  has_extraction  — True wenn extraction nicht None ist
  ki_input_tokens, ki_output_tokens, ki_reasoning_tokens, ki_total_duration
                  — Summen über ALLE DocumentTokenCount-Einträge (alle Durchläufe)

Status-Flow:
  pending → processing → done
                      → error
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # Gespeicherter Dateiname im Storage-Ordner (Format: {id}.pdf).
    # Null bis das PDF erfolgreich kopiert wurde.
    stored_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    # Seitenanzahl — wird erst bei der KI-Analyse gesetzt (beim Import immer 0).
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Dokumententyp-Integer: 0=Unbekannt, 1=Eingangsrechnung, 2=Ausgangsrechnung, …
    # Nur Eingangsrechnungen (1) erhalten eine vollständige InvoiceExtraction.
    document_type: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Status-Flow: pending → processing → done | error
    status: Mapped[str] = mapped_column(String(50), default="pending", server_default="pending", nullable=False)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True, server_default="{}")
    # Soft-Delete: Dokument bleibt in der DB, wird aber nicht mehr angezeigt.
    # Kann über POST /{id}/restore wiederhergestellt werden.
    soft_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Zeitpunkt der letzten Änderung (v.a. Status-Übergänge wie processing→done).
    # Wichtig für inkrementelle Exports: created_at ist der Import-Zeitpunkt, nicht
    # der Zeitpunkt, an dem die KI-Analyse abgeschlossen wurde.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # ── Relationen ────────────────────────────────────────────────────────────
    batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="documents")  # noqa: F821
    extraction: Mapped["InvoiceExtraction | None"] = relationship(  # noqa: F821
        "InvoiceExtraction", back_populates="document", uselist=False, cascade="all, delete-orphan"
    )
    order_positions: Mapped[list["OrderPosition"]] = relationship(  # noqa: F821
        "OrderPosition", back_populates="document", cascade="all, delete-orphan"
    )
    # Jeder KI-Analyse-Durchlauf erzeugt einen eigenen Eintrag (kein Überschreiben).
    token_counts: Mapped[list["DocumentTokenCount"]] = relationship(  # noqa: F821
        "DocumentTokenCount", back_populates="document", cascade="all, delete-orphan"
    )

    # ── Convenience-Properties — delegieren an Relationen ────────────────────
    # Diese Properties ermöglichen es, Pydantic-Schemas flach zu halten (keine
    # verschachtelten Objekte für simple Felder wie Firmenname oder Jahr).

    @property
    def company(self) -> str | None:
        """Firmenname aus dem verknüpften ImportBatch."""
        return self.batch.company_name if self.batch else None

    @property
    def year(self) -> int | None:
        """Importjahr aus dem verknüpften ImportBatch."""
        return self.batch.year if self.batch else None

    @property
    def total_amount(self):
        """Gesamtbetrag brutto aus der InvoiceExtraction (None wenn keine Extraktion)."""
        return self.extraction.total_amount_brutto if self.extraction else None

    @property
    def invoice_number(self) -> str | None:
        """Rechnungsnummer aus der InvoiceExtraction (None wenn keine Extraktion)."""
        return self.extraction.invoice_number if self.extraction else None

    @property
    def supplier_name(self) -> str | None:
        """Lieferantenname (vendor_id-Freitext) aus der InvoiceExtraction."""
        return self.extraction.vendor_id if self.extraction else None

    @property
    def has_extraction(self) -> bool:
        """True wenn eine InvoiceExtraction für dieses Dokument existiert."""
        return self.extraction is not None

    # ── Token-Statistiken: Summe aller KI-Analyse-Durchläufe ─────────────────
    # Jeder Analyse-Durchlauf erzeugt einen eigenen DocumentTokenCount-Eintrag.
    # Diese Properties liefern die Gesamtsumme aller Einträge.
    # Gibt None zurück wenn noch kein einziger Durchlauf stattgefunden hat.

    @property
    def ki_input_tokens(self) -> int | None:
        """Summe der Eingabe-Token über alle KI-Analyse-Durchläufe."""
        if not self.token_counts:
            return None
        total = sum(tc.input_token_count for tc in self.token_counts)
        return int(total) if total else None

    @property
    def ki_output_tokens(self) -> int | None:
        """Summe der Ausgabe-Token über alle KI-Analyse-Durchläufe."""
        if not self.token_counts:
            return None
        total = sum(tc.output_token_count for tc in self.token_counts)
        return int(total) if total else None

    @property
    def ki_reasoning_tokens(self) -> int | None:
        """Summe der Reasoning-Token über alle Durchläufe (nur bei Reasoning-Modellen > 0)."""
        if not self.token_counts:
            return None
        total = sum(tc.reasoning_count for tc in self.token_counts)
        return int(total) if total else None

    @property
    def ki_total_duration(self) -> float | None:
        """Summe der Analysedauer (Sekunden) über alle KI-Analyse-Durchläufe."""
        if not self.token_counts:
            return None
        total = sum(tc.time_spent_seconds for tc in self.token_counts)
        return float(total) if total else None
