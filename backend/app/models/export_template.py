"""
Export-Vorlagen für den schemagesteuerten Excel-Export (Phase 5).

`config` (JSON):
    {
      "entities": [{"entity": "beleg", "fields": ["rechnungsnummer", …]}, {"entity": "position", "fields": […]}],   # Auswahl + Reihenfolge
      "vendor":   ["name", "ust_id_nr"],           # optional: Lieferanten-Stammdaten als Spalten am Beleg
      "flat":     {"root": "beleg", "child": "position"}    # nur Layout „flat“ (sonst aus den Beziehungen abgeleitet)
    }
Fehlt eine Entität in `entities`, kommt sie nicht in die Datei; fehlt `fields`, werden alle Felder der Entität ausgegeben.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

LAYOUTS = ("sheet_per_entity", "flat")
QUANTITY_MODES = ("original", "normalized")


class ExportTemplate(Base):
    __tablename__ = "export_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout: Mapped[str] = mapped_column(String(20), nullable=False, default="sheet_per_entity")
    quantity_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="original", server_default="original")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
