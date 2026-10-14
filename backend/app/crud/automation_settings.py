"""
CRUD-Operationen für die Automatisierungseinstellungen.

Singleton-Tabelle (immer id=1) — get_or_create() stellt sicher, dass
Standardwerte existieren, ohne dass der Nutzer sie manuell anlegen muss.
"""

from sqlalchemy.orm import Session

from app.models.automation_settings import AutomationSettings
from app.schemas.automation_settings import AutomationSettingsUpdate

_SINGLETON_ID = 1


def get_or_create(db: Session) -> AutomationSettings:
    """
    Gibt die Automatisierungseinstellungen zurück, legt sie bei Bedarf an.

    Die worker_*-Felder werden beim ERSTEN Anlegen dieser Singleton-Zeile aus den
    ENV-Variablen (WORKER_CONCURRENCY/WORKER_POLL_INTERVAL/WORKER_LOCK_TIMEOUT_MINUTES,
    siehe app.config.settings) übernommen — danach sind sie über die Website
    änderbar und die ENV-Werte spielen keine Rolle mehr (die DB ist ab dann die
    Quelle der Wahrheit, wie bei folder_sync_interval_minutes).
    """
    obj = db.get(AutomationSettings, _SINGLETON_ID)

    if obj is None:
        from app.config import settings as app_settings

        obj = AutomationSettings(
            id=_SINGLETON_ID,
            worker_concurrency=app_settings.worker_concurrency,
            worker_poll_interval_seconds=round(app_settings.worker_poll_interval),
            worker_lock_timeout_minutes=app_settings.worker_lock_timeout_minutes,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)

    return obj


def update(db: Session, data: AutomationSettingsUpdate) -> AutomationSettings:
    """Aktualisiert die Automatisierungseinstellungen vollständig. Legt den Datensatz bei Bedarf an."""
    obj = get_or_create(db)

    obj.folder_sync_interval_minutes = data.folder_sync_interval_minutes
    obj.export_weekday = data.export_weekday
    obj.export_hour = data.export_hour
    obj.export_minute = data.export_minute
    obj.worker_concurrency = data.worker_concurrency
    obj.worker_poll_interval_seconds = data.worker_poll_interval_seconds
    obj.worker_lock_timeout_minutes = data.worker_lock_timeout_minutes
    obj.analysis_history_keep = data.analysis_history_keep

    db.commit()
    db.refresh(obj)
    return obj
