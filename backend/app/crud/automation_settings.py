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
    """Gibt die Automatisierungseinstellungen zurück, legt sie bei Bedarf mit Standardwerten an."""
    obj = db.get(AutomationSettings, _SINGLETON_ID)

    if obj is None:
        obj = AutomationSettings(id=_SINGLETON_ID)
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

    db.commit()
    db.refresh(obj)
    return obj
