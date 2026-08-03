"""
ORM-Modell für globale Automatisierungseinstellungen (Tabelle: automation_settings).

Singleton-Tabelle: Es existiert immer genau ein Datensatz mit id=1.

Steuert mehrere unabhängige Hintergrund-Loops im Worker-Container:
  - folder_sync_interval_minutes: wie oft Import-Batches mit folder_sync=True
    auf neue PDFs im Import-Ordner geprüft werden (app/worker/folder_sync.py).
  - export_weekday/export_hour/export_minute: fester Wochentermin, zu dem der
    automatische Excel-Export für Batches mit auto_export=True geschrieben wird
    (app/worker/export_schedule.py).
  - worker_concurrency/worker_poll_interval_seconds/worker_lock_timeout_minutes:
    steuern den KI-Task-Dispatcher (app/worker/worker.py::Dispatcher). Werden bei
    jedem Poll-Zyklus frisch aus der DB gelesen — Änderungen über die Website
    wirken deshalb ohne Container-Neustart. Der allererste Wert (beim Anlegen
    dieser Singleton-Zeile) wird aus den ENV-Variablen WORKER_CONCURRENCY/
    WORKER_POLL_INTERVAL/WORKER_LOCK_TIMEOUT_MINUTES übernommen, siehe
    crud/automation_settings.py::get_or_create().
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
        CheckConstraint("worker_concurrency >= 1", name="ck_automation_settings_worker_concurrency"),
        CheckConstraint("worker_poll_interval_seconds >= 1", name="ck_automation_settings_worker_poll_interval"),
        CheckConstraint("worker_lock_timeout_minutes >= 1", name="ck_automation_settings_worker_lock_timeout"),
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
    # Maximale Anzahl gleichzeitiger Worker (Obergrenze, unabhängig von der Summe
    # aus parallel_request über alle aktiven KI-Configs — verhindert Thread-Pool-
    # Erschöpfung auf NAS-Systemen mit wenigen CPUs).
    worker_concurrency: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)
    # Poll-Intervall des Dispatchers in Sekunden (wie oft DB auf neue Tasks/
    # geänderte KI-Configs geprüft wird).
    worker_poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=20, server_default="20", nullable=False)
    # Nach wie vielen Minuten ein 'in_progress'-Task als verwaist gilt (z.B. Worker
    # abgestürzt) und automatisch zurück auf 'pending' gesetzt wird. Sollte deutlich
    # über der maximalen KI-Antwortzeit liegen (siehe ai_service.REQUEST_TIMEOUT_SECONDS).
    worker_lock_timeout_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
