"""
ORM-Modell für die PDF-Einstellungen (Singleton, id=1).

Steuert das automatische Kürzen langer PDFs vor der KI-Analyse.
Eigene Tabelle (statt Spalten in processing_settings), damit die
Import-/Analyse-Parallelität unabhängig davon bleibt.
"""

from sqlalchemy import Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PdfSettings(Base):
    __tablename__ = "pdf_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # Automatisch kürzen, bevor die KI-Analyse startet
    auto_trim_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Hat ein PDF mehr Seiten als dieser Wert, werden nur die ersten N behalten
    auto_trim_max_pages: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
