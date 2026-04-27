"""
ORM-Modell für den asynchronen Workflow-Task-Queue (Tabelle: workflow_tasks).

Alle blockierenden Operationen (KI-Analyse, Import) werden als Tasks in diese
Tabelle eingetragen. Der Worker (runner.py) holt Tasks per SKIP LOCKED ab und
verarbeitet sie sequenziell, um den asyncio-Event-Loop nicht zu blockieren.

Status-Flow: pending → in_progress → completed | failed

Bei Fehlern wird der Task bis zu max_attempts-mal wiederholt (Standard: 3).
Nach Erschöpfung der Versuche verbleibt der Task im Status 'failed'.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskKind(StrEnum):
    """
    Bekannte Task-Typen im Workflow-System.

    PROCESS_DOCUMENT: KI-Analyse eines einzelnen Dokuments.
      payload enthält: {"document_id": int, "batch_id": int | None,
                        "ai_config_id": int | None, "system_prompt_id": int | None}
    """

    PROCESS_DOCUMENT = "process_document"


class WorkflowTask(Base):
    """
    Ein einzelner asynchroner Task in der Verarbeitungs-Queue.

    Der Worker beansprucht Tasks per SELECT … FOR UPDATE SKIP LOCKED,
    setzt status='in_progress' und verarbeitet sie. Nach Abschluss wird
    status='completed' (oder 'failed' bei Fehler). Zwischen-Ergebnisse
    werden in result (JSONB) gespeichert.

    workflow_id ist eine UUID, die mehrere zusammengehörige Tasks gruppiert
    (z.B. alle Dokumente eines Import-Batches).

    locked_at und worker_id dokumentieren, welcher Worker-Prozess den Task
    gerade bearbeitet — nützlich für Diagnose nach Absturz.
    """

    __tablename__ = "workflow_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)  # UUID der zugehörigen Workflow-Gruppe
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)                      # Task-Parameter (document_id, ai_config_id etc.)
    # pending | in_progress | completed | failed
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)     # Bisherige Verarbeitungsversuche
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3", nullable=False) # Maximale Versuche bevor 'failed'
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)                 # Ergebnis nach erfolgreicher Verarbeitung
    error: Mapped[str | None] = mapped_column(Text, nullable=True)                   # Fehlermeldung bei 'failed'
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)        # ID des bearbeitenden Workers
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Zeitpunkt der Übernahme durch Worker
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
