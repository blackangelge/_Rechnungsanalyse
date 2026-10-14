"""
ORM-Modell für die Analyse-Historie.

Vor jeder erneuten KI-Analyse wird der bisherige Stand eines Belegs
(Extraktion, Positionen, verwendete Prompts/Workflow) als Momentaufnahme
aufgehoben, damit Ergebnisse verglichen und alte Stände wiederhergestellt
werden können. Die Anzahl aufbewahrter Stände je Beleg ist begrenzt
(processing_settings.analysis_history_keep).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalysisHistory(Base):
    __tablename__ = "analysis_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Zeitpunkt, zu dem dieser Stand ersetzt (archiviert) wurde
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Kurzfelder für die Listenansicht (ohne das große Snapshot-JSON zu laden)
    pdf_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workflow_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    position_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Prüfsumme des Inhalts → identische Stände werden nicht doppelt aufgehoben
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # JSON: {"extraction": {...}, "positions": [...], "meta": {...}}
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
