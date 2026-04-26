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
    query = db.query(ImportBatch).order_by(ImportBatch.created_at.desc())
    if company_name:
        query = query.filter(ImportBatch.company_name.ilike(f"%{company_name}%"))
    if year:
        query = query.filter(ImportBatch.year == year)
    return query.all()


def get_by_id(db: Session, batch_id: int) -> ImportBatch | None:
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
