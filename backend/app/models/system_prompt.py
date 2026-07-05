"""
SQLAlchemy-ORM-Modell für die system_prompts-Tabelle.

System-Prompts steuern das Verhalten der KI bei der Dokumentenanalyse.
Es gibt zwei Prompt-Typen:

  type=0  Dokumententyp-Erkennung (detect_document_type)
          Die KI soll den Typ des Dokuments bestimmen und im JSON-Format antworten:
          {"dokumententyp_id": 1, "dokumententyp_name": "Eingangsrechnung"}
          Es kann maximal EINEN aktiven Dokumententyp-Prompt geben.

  type=1  Standard-Extraktion (extract_invoice_data)
          Vollständige Rechnungsdaten-Extraktion für Eingangsrechnungen.
          Enthält die JSON-Struktur mit allen erwarteten Feldern.

Es gibt keinen Code-Fallback mehr: Ist für einen Typ kein Prompt konfiguriert,
bricht der jeweilige ai_service-Aufruf mit einer klaren Fehlermeldung ab,
statt einen unsichtbaren Standardtext zu verwenden.

CRUD-Operationen: app/crud/system_prompt.py
  get_default()       — Gibt type=1 Prompt zurück
  get_doc_type_prompt() — Gibt type=0 Prompt zurück
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SystemPrompt(Base):
    __tablename__ = "system_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 0=Dokumententyp-Erkennung, 1=Standard-Extraktionsprompt (Eingangsrechnung)
    # server_default="1": neue Prompts sind standardmäßig Extraktionsprompts
    type: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
