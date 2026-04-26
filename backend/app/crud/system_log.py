"""
CRUD-Operationen für Systemlogs.
"""

import logging

from sqlalchemy.orm import Session

from app.models.system_log import SystemLog

logger = logging.getLogger(__name__)


def add(
    db: Session,
    category: str,
    level: str,
    message: str,
    batch_id: int | None = None,
    document_id: int | None = None,
) -> SystemLog | None:
    """
    Schreibt einen Log-Eintrag in die DB.
    Wirft nie eine Exception — Fehler werden nur geloggt.
    """
    try:
        obj = SystemLog(
            category=category,
            level=level,
            message=message,
            batch_id=batch_id,
            document_id=document_id,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except Exception as exc:
        logger.warning("Fehler beim Schreiben des Systemlogs: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return None


def get_all(
    db: Session,
    category: str | None = None,
    level: str | None = None,
    limit: int = 500,
) -> list[SystemLog]:
    """Gibt Log-Einträge zurück, neueste zuerst."""
    query = db.query(SystemLog)
    if category:
        query = query.filter(SystemLog.category == category)
    if level:
        query = query.filter(SystemLog.level == level)
    return query.order_by(SystemLog.created_at.desc()).limit(limit).all()


def clear(db: Session, category: str | None = None) -> int:
    """Löscht alle (oder kategoriegefilterte) Log-Einträge. Gibt Anzahl zurück."""
    query = db.query(SystemLog)
    if category:
        query = query.filter(SystemLog.category == category)
    count = query.delete(synchronize_session=False)
    db.commit()
    return count
