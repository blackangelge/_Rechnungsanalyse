"""
Freie KI-Fragen an einen Beleg („Wie heißt die IBAN?“) und ihre Historie.

  ai_queries       — Verlauf der gestellten Fragen (je Frage + Zielfeld ein Eintrag; wird beim erneuten Stellen hochgezählt),
                     damit man eine frühere Abfrage später wieder auswählen kann.
  document_answers — jede Antwort der KI zu einem Beleg (Frage als Momentaufnahme, bereinigte Antwort, ob/was am Beleg geändert wurde).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIQuery(Base):
    __tablename__ = "ai_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    field_key: Mapped[str | None] = mapped_column(String(40), nullable=True)      # Zielfeld am Beleg (None = nur Antwort speichern)
    use_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class DocumentAnswer(Base):
    __tablename__ = "document_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer, nullable=True)           # bewusst keine FK (Backup/Import ohne Verlauf)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    field_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)                # bereinigter Wert; NULL = nicht gefunden/ungültig
    raw_answer: Mapped[str | None] = mapped_column(Text, nullable=True)            # Antwort der KI unverändert
    valid: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)   # am Beleg übernommen?
    note: Mapped[str | None] = mapped_column(Text, nullable=True)                  # z.B. „Wert ungültig“, „Beleg hat keine Rechnungsdaten“
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)      # Vorgang im Änderungsprotokoll (rückgängig machbar)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
