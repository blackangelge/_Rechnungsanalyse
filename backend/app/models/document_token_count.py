"""
SQLAlchemy-ORM-Modell für die documents_token_counts-Tabelle.

Speichert Token-Verbrauch und Dauer eines einzelnen KI-Analyse-Durchlaufs.

Beziehung zu documents: 1:n
  - Jeder Aufruf von save_extraction() erzeugt EINEN neuen Eintrag.
  - Bestehende Einträge werden NIE überschrieben oder gelöscht.
  - Dadurch bleibt die vollständige Analyse-Historie erhalten, auch wenn
    ein Dokument mehrfach (erneut) analysiert wird.

Die aggregierten Werte (Summe über alle Einträge) stellt das Document-Model
als @property bereit (ki_input_tokens, ki_output_tokens, ki_total_duration, …).
Diese Aggregationen werden im KI-Modal des Frontends angezeigt.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DocumentTokenCount(Base):
    __tablename__ = "documents_token_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # Token-Zähler — vom jeweiligen KI-Modell gemeldet
    output_token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    input_token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Reasoning-Token: nur von Reasoning-Modellen (z.B. DeepSeek-R1, Claude 3.5 Sonnet) gemeldet.
    # Bei normalen Modellen immer 0.
    reasoning_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Gesamtdauer des HTTP-Requests (inkl. Serialisierung, Netzwerk, Inferenz, Parsing)
    time_spent_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    # Zeitstempel des Durchlaufs — für die chronologische Darstellung im KI-Modal
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship("Document", back_populates="token_counts")  # noqa: F821
