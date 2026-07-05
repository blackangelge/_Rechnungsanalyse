"""
ORM-Modell für globale Automatisierungseinstellungen (Tabelle: automation_settings).

Singleton-Tabelle: Es existiert immer genau ein Datensatz mit id=1.

Steuert zwei unabhängige Hintergrund-Loops im Worker-Container:
  - folder_sync_interval_minutes: wie oft Import-Batches mit folder_sync=True
    auf neue PDFs im Import-Ordner geprüft werden (app/worker/folder_sync.py).
  - export_weekday/export_hour/export_minute: fester Wochentermin, zu dem der
    automatische Excel-Export für Batches mit auto_export=True geschrieben wird
    (app/worker/export_schedule.py).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AutomationSettings(Base):
    """
    Globale Einstellungen für Ordner-Sync und automatischen Export (Singleton, immer id=1).
    """

    __tablename__ = "automation_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_automation_settings_singleton"),
        CheckConstraint("folder_sync_interval_minutes >= 1", name="ck_automation_settings_interval"),
        CheckConstraint("export_weekday BETWEEN 0 AND 6", name="ck_automation_settings_weekday"),
        CheckConstraint("export_hour BETWEEN 0 AND 23", name="ck_automation_settings_hour"),
        CheckConstraint("export_minute BETWEEN 0 AND 59", name="ck_automation_settings_minute"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)  # Immer 1 (Singleton)
    # Scan-Intervall für den Ordner-Sync-Loop (Minuten)
    folder_sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=15, server_default="15", nullable=False
    )
    # Wochentag für den automatischen Export: 0=Montag … 6=Sonntag
    export_weekday: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    export_hour: Mapped[int] = mapped_column(Integer, default=6, server_default="6", nullable=False)
    export_minute: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
