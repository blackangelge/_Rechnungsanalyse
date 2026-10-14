"""
CRUD für die PDF-Einstellungen (Singleton, id=1): automatisches Kürzen.
"""

from sqlalchemy.orm import Session

from app.models.pdf_settings import PdfSettings
from app.schemas.document_version import PdfSettingsUpdate

_SINGLETON_ID = 1


def get_or_create(db: Session) -> PdfSettings:
    obj = db.get(PdfSettings, _SINGLETON_ID)
    if obj is None:
        obj = PdfSettings(id=_SINGLETON_ID, auto_trim_enabled=False, auto_trim_max_pages=3)
        db.add(obj)
        db.commit()
        db.refresh(obj)
    return obj


def update(db: Session, data: PdfSettingsUpdate) -> PdfSettings:
    obj = get_or_create(db)
    obj.auto_trim_enabled = data.auto_trim_enabled
    obj.auto_trim_max_pages = data.auto_trim_max_pages
    db.commit()
    db.refresh(obj)
    return obj
