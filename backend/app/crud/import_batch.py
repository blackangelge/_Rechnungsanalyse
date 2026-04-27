"""
CRUD-Operationen für die import_batches-Tabelle.

Import-Batches gruppieren alle Dokumente eines Importvorgangs.
Status-Flow: pending → running → done | error

Hinweis: Die übergebene Session wird nicht geschlossen — das ist Aufgabe des Aufrufers.
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from app.models.import_batch import ImportBatch
from app.schemas.import_batch import ImportBatchCreate


def get_all(
    db: Session,
    company_name: str | None = None,
    year: int | None = None,
) -> list[ImportBatch]:
    """
    Gibt alle Import-Batches zurück, neueste zuerst.
    Optionale Filter nach Firmenname (ILIKE, Teilstring) und Jahr.
    """
    query = db.query(ImportBatch).order_by(ImportBatch.created_at.desc())
    if company_name:
        query = query.filter(ImportBatch.company_name.ilike(f"%{company_name}%"))
    if year:
        query = query.filter(ImportBatch.year == year)
    return query.all()


def get_by_id(db: Session, batch_id: int) -> ImportBatch | None:
    """
    Gibt einen Batch anhand seiner ID zurück, inklusive joinedload der Dokumente.
    Gibt None zurück wenn nicht gefunden.
    """
    return (
        db.query(ImportBatch)
        .options(joinedload(ImportBatch.documents))
        .filter(ImportBatch.id == batch_id)
        .first()
    )


def create(
    db: Session,
    data: ImportBatchCreate,
    company_name: str,
    year: int,
) -> ImportBatch:
    """
    Legt einen neuen Import-Batch an.
    storage_folder_path wird automatisch aus STORAGE_PATH + '{company}_{year}' berechnet.
    Status wird auf 'pending' gesetzt.
    """
    from app.config import settings
    storage_folder_path = str(Path(settings.storage_path) / f"{company_name}_{year}")

    obj = ImportBatch(
        import_folder_path=data.folder_path,
        storage_folder_path=storage_folder_path,
        company_name=company_name,
        year=year,
        comment=data.comment,
        folder_sync=data.folder_sync,
        status="pending",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, batch_id: int) -> bool:
    """
    Löscht einen Batch dauerhaft (inkl. aller Dokumente über CASCADE).
    Gibt False zurück wenn nicht gefunden.
    """
    obj = db.get(ImportBatch, batch_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True


def update_status(
    db: Session,
    batch_id: int,
    status: str,
) -> ImportBatch | None:
    """
    Setzt den Status eines Batches und aktualisiert Zeitstempel automatisch:
      'running' → started_at wird gesetzt (nur beim ersten Wechsel)
      'done' / 'error' → finished_at wird gesetzt
    Gibt None zurück wenn nicht gefunden.
    """
    obj = db.get(ImportBatch, batch_id)
    if obj is None:
        return None
    obj.status = status
    now = datetime.now(timezone.utc)
    if status == "running" and obj.started_at is None:
        obj.started_at = now
    elif status in ("done", "error"):
        obj.finished_at = now
    db.commit()
    db.refresh(obj)
    return obj
