"""
ORM-Modell für Import-Batches (Tabelle: import_batches).

Ein Import-Batch fasst alle PDFs zusammen, die in einem Import-Vorgang
aus einem Quellordner (import_folder_path) in den Speicherpfad (storage_folder_path)
kopiert wurden. Der Batch speichert Firmenname und Jahr als Metadaten.

Status-Flow: pending → running → done | error
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ImportBatch(Base):
    """
    Gruppiert alle Dokumente eines Import-Vorgangs.

    Der Import-Ordner (import_folder_path) ist der Quell-Pfad auf dem NAS,
    der Storage-Pfad (storage_folder_path) ist das Ziel unter STORAGE_PATH.
    Nach dem Import enthält documents alle kopierten PDFs als Document-Einträge.

    folder_sync=True bedeutet, dass dieser Batch periodisch auf neue PDFs
    im Import-Ordner geprüft wird (zukünftiges Feature).
    """

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    import_folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)   # Quellordner auf dem NAS
    storage_folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)  # Zielordner im STORAGE_PATH
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)          # Firmenname (aus Import-Formular)
    year: Mapped[int] = mapped_column(Integer, nullable=False)                      # Buchungsjahr (aus Import-Formular)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)                # Optionaler Kommentar
    # pending | running | done | error
    status: Mapped[str] = mapped_column(String(50), default="pending", server_default="pending", nullable=False)
    folder_sync: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default="false", nullable=True)  # Ordner-Sync aktiv
    # Zeitstempel der letzten Ordner-Sync-Prüfung (app/worker/folder_sync.py). None = noch nie geprüft.
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Opt-in: wöchentlicher automatischer Excel-Export für diesen Batch (app/worker/export_schedule.py)
    auto_export: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # Gemeinsamer Zähler für manuellen ("/export/new") und automatischen Export.
    # None = noch nie exportiert → beim nächsten inkrementellen Export gelten alle Dokumente als neu.
    last_exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # Zeitstempel Import-Start
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Zeitstempel Import-Ende
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(  # noqa: F821
        "Document", back_populates="batch", cascade="all, delete-orphan"
    )
