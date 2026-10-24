"""Übernahme aus dem MAIN-Projekt: Verbindung + Übersetzungstabelle (Konfiguration) und Protokoll je übernommenem Datensatz."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MainMigrationConfig(Base):
    """Singleton (id=1)."""
    __tablename__ = "main_migration_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)              # Verbindungs-URL der MAIN-Datenbank (nur lesend benutzt)
    source_storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)     # Ordner mit den kopierten MAIN-PDFs ({Firma}_{Jahr}/{id}.pdf)
    mapping: Mapped[list | None] = mapped_column(JSONB, nullable=True)               # Übersetzungstabelle (siehe services/main_migration.py)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MainMigrationItem(Base):
    """
    Ein übernommener Datensatz: alte (MAIN) ID → neue ID. Dient der Wiederholbarkeit (schon Übernommenes wird übersprungen)
    und als Bericht (Status je Beleg). `source_table`: batch | document.
    Status Beleg: imported (Datensätze angelegt) · no_data (Beleg übernommen, aber keine verwertbare KI-Antwort) · error (nicht übernommen).
    """
    __tablename__ = "main_migration_items"
    __table_args__ = (UniqueConstraint("source_table", "old_id", name="uq_main_migration_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_table: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    old_id: Mapped[int] = mapped_column(Integer, nullable=False)
    new_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
