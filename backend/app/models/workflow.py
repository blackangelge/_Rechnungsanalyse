"""
ORM-Modell für Analyse-Workflows.

Ein Workflow beschreibt als Graph (JSON, siehe services/workflow_service.py),
welche Schritte die KI-Analyse eines Belegs in welcher Reihenfolge durchläuft
und welche Systemprompts dabei verwendet werden. Lieferanten können einen
eigenen Workflow zugewiesen bekommen (vendor.workflow_id).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Graph als JSON-Text: {"nodes": [...], "edges": [...]}
    graph: Mapped[str] = mapped_column(Text, nullable=False)

    # Standard-Workflow: gilt, wenn weder ein Workflow gewählt noch für den Lieferanten hinterlegt ist
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
