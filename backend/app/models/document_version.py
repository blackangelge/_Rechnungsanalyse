"""
ORM-Modell für PDF-Versionen eines Dokuments.

Jede Bearbeitung der PDF (Seiten entfernen — manuell oder automatisch) legt eine
neue, unveränderliche Version an. Version 1 ist immer das Original. Die jeweils
aktuelle Version liegt zusätzlich als `{id}.pdf` im Storage-Ordner (damit
Vorschau, KI-Analyse und Export unverändert funktionieren); alle Versionen
liegen unter `versions/{doc_id}/v{n}.pdf`.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocumentVersion(Base):
    """Eine gespeicherte PDF-Version eines Dokuments."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number", name="uq_doc_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 1 = Original, danach fortlaufend
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Dateipfad relativ zum Firmen/Jahr-Ordner: "versions/{doc_id}/v{n}.pdf"
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)

    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # original | manual | auto | restore
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # Freitext (z.B. "Seiten 2–100 entfernt", "Wiederhergestellt aus Version 1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Version, aus der diese entstanden ist (None beim Original)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # JSON-Liste der entfernten Seiten (1-basiert, bezogen auf die Elternversion)
    removed_pages: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
