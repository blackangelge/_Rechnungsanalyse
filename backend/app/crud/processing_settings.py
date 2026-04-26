"""
CRUD-Operationen für die Verarbeitungseinstellungen.

Singleton-Tabelle (id=1): get_or_create() stellt sicher, dass
immer Standardwerte vorhanden sind.
"""

from sqlalchemy.orm import Session

from app.models.processing_settings import ProcessingSettings
from app.schemas.processing_settings import ProcessingSettingsUpdate

_SINGLETON_ID = 1


def get_or_create(db: Session) -> ProcessingSettings:
    """
    Gibt die Verarbeitungseinstellungen zurück.
    Legt Standardwerte an, falls noch kein Datensatz existiert.
    """
    obj = db.get(ProcessingSettings, _SINGLETON_ID)

    if obj is None:
        obj = ProcessingSettings(
            id=_SINGLETON_ID,
            import_concurrency=10,
            ai_concurrency=4,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)

    return obj


def update(db: Session, data: ProcessingSettingsUpdate) -> ProcessingSettings:
    """
    Aktualisiert die Verarbeitungseinstellungen vollständig.
    Legt den Datensatz an, falls er noch nicht existiert.
    """
    obj = get_or_create(db)

    obj.import_concurrency = data.import_concurrency
    obj.ai_concurrency = data.ai_concurrency

    db.commit()
    db.refresh(obj)
    return obj
