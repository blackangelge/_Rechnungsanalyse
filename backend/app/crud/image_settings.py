"""
CRUD-Operationen für die Bildkonvertierungseinstellungen.

Da es sich um eine Singleton-Tabelle handelt (immer id=1), gibt es
kein Create im üblichen Sinne — get_or_create() stellt sicher, dass
die Standardwerte existieren, ohne dass der Nutzer sie manuell anlegen muss.
"""

from sqlalchemy.orm import Session

from app.models.image_settings import ImageSettings
from app.schemas.image_settings import ImageSettingsUpdate

# Feste ID des einzigen Datensatzes
_SINGLETON_ID = 1


def get_or_create(db: Session) -> ImageSettings:
    """
    Gibt die Bildeinstellungen zurück.
    Falls noch kein Datensatz existiert (Erstinstallation), wird einer
    mit Standardwerten angelegt und zurückgegeben.
    """
    obj = db.get(ImageSettings, _SINGLETON_ID)

    if obj is None:
        # Erstinstallation: Standardwerte in DB schreiben
        obj = ImageSettings(id=_SINGLETON_ID, dpi=150, image_format="PNG", jpeg_quality=85)
        db.add(obj)
        db.commit()
        db.refresh(obj)

    return obj


def update(db: Session, data: ImageSettingsUpdate) -> ImageSettings:
    """
    Aktualisiert die Bildeinstellungen vollständig.
    Legt den Datensatz an, falls er noch nicht existiert.
    """
    obj = get_or_create(db)

    obj.dpi = data.dpi
    obj.image_format = data.image_format
    obj.jpeg_quality = data.jpeg_quality
    obj.grayscale = data.grayscale

    db.commit()
    db.refresh(obj)
    return obj
