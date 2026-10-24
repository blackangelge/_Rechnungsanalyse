"""
Router: Übernahme aus dem MAIN-Projekt (Imports, Belege, Lieferanten) ohne KI — siehe services/main_migration.py.

  GET|PUT /api/main-migration/config          — Verbindungs-URL der MAIN-Datenbank (Passwort maskiert) + Ordner der kopierten PDFs
  POST    /api/main-migration/test            — Verbindung prüfen: Anzahl je Tabelle, PDF-Ordner
  GET     /api/main-migration/source-fields   — verfügbare Quellfelder (Spalten + Pfade der KI-Antworten) für die Übersetzungstabelle
  GET|PUT /api/main-migration/mapping         — Übersetzungstabelle (+ Meldungen); POST /mapping/default = Vorschlag zum aktiven Schema
  GET     /api/main-migration/targets         — Zielfelder des aktiven Schemas
  POST    /api/main-migration/preview         — ein MAIN-Beleg durch die Übersetzung (nichts gespeichert)
  POST    /api/main-migration/run             — Lauf starten (dry_run = Trockenlauf, limit = Stichprobe); GET /state, POST /cancel
  GET     /api/main-migration/summary         — Stand der Übernahme (Belege je Status, Prüfregel-Ergebnis)
  POST    /api/main-migration/reanalyze       — auffällige/ohne Daten übernommene Belege mit einem Workflow an die KI schicken
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.database import SessionLocal, get_db
from app.models.document import Document
from app.models.main_migration import MainMigrationItem
from app.models.schema_def import ExtractedRecord
from app.services import main_migration as mm

router = APIRouter(prefix="/api/main-migration", tags=["main-migration"])

_MASK = "••••••"


def _mask(url: str | None) -> str | None:
    """Passwort in der URL verbergen: postgresql://user:••••••@host/db."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", rf"\1{_MASK}@", url) if url else url


class ConfigIn(BaseModel):
    source_url: str | None = None
    source_storage_path: str | None = None


@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    cfg = mm.get_config(db)
    db.commit()
    return {"source_url": _mask(cfg.source_url), "has_url": bool(cfg.source_url), "source_storage_path": cfg.source_storage_path}


@router.put("/config")
def put_config(body: ConfigIn, db: Session = Depends(get_db)):
    cfg = mm.get_config(db)
    if body.source_url is not None and _MASK not in body.source_url:      # maskierte URL zurückgeschickt → unverändert lassen
        cfg.source_url = body.source_url.strip() or None
    if body.source_storage_path is not None:
        cfg.source_storage_path = body.source_storage_path.strip() or None
    db.commit()
    return {"source_url": _mask(cfg.source_url), "has_url": bool(cfg.source_url), "source_storage_path": cfg.source_storage_path}


@router.post("/test")
def test_connection(db: Session = Depends(get_db)):
    cfg = mm.get_config(db)
    try:
        engine = mm.open_source(cfg.source_url or "")
        counts = mm.count_source(engine)
    except mm.MigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Verbindung fehlgeschlagen: {str(exc)[:300]}") from exc
    storage = {"path": cfg.source_storage_path, "exists": False, "sample": None}
    if cfg.source_storage_path:
        root = Path(cfg.source_storage_path)
        storage["exists"] = root.is_dir()
        if root.is_dir():
            storage["sample"] = next((str(p.relative_to(root)) for p in root.rglob("*.pdf")), None)
    return {"ok": True, "counts": counts, "storage": storage}


@router.get("/source-fields")
def source_fields(db: Session = Depends(get_db)):
    cfg = mm.get_config(db)
    try:
        return mm.discover_paths(mm.open_source(cfg.source_url or ""))
    except mm.MigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Quelle nicht lesbar: {str(exc)[:300]}") from exc


@router.get("/targets")
def targets(db: Session = Depends(get_db)):
    version = sd.get_active(db)
    if version is None:
        return {"schema_version": None, "targets": []}
    out = []
    for e in version.entities:
        for f in e.fields:
            if f.source == "llm" and not f.is_locked:
                out.append({"target": f"{e.name}.{f.name}", "entity": e.name, "entity_label": e.display_name, "field": f.name,
                            "label": f.display_name, "data_type": f.data_type, "required": f.required})
    return {"schema_version": version.version_number, "targets": out, "transforms": list(mm.TRANSFORMS)}


class MappingEntry(BaseModel):
    target: str
    sources: list[str] = Field(default_factory=list)
    unit_sources: list[str] = Field(default_factory=list)
    transform: str | None = None


class MappingIn(BaseModel):
    mapping: list[MappingEntry]


def _mapping_out(db: Session, mapping: list[dict]) -> dict:
    version = sd.get_active(db)
    return {"mapping": mapping, "problems": mm.check_mapping(version, mapping) if version else ["Es gibt kein aktives Schema."]}


@router.get("/mapping")
def get_mapping(db: Session = Depends(get_db)):
    cfg = mm.get_config(db)
    db.commit()
    return _mapping_out(db, cfg.mapping or [])


@router.put("/mapping")
def put_mapping(body: MappingIn, db: Session = Depends(get_db)):
    cfg = mm.get_config(db)
    cfg.mapping = [{k: v for k, v in e.model_dump().items() if v not in (None, [])} | {"transform": e.transform} for e in body.mapping]
    db.commit()
    return _mapping_out(db, cfg.mapping)


@router.post("/mapping/default")
def default_mapping(db: Session = Depends(get_db)):
    version = sd.get_active(db)
    if version is None:
        raise HTTPException(status_code=409, detail="Es gibt kein aktives Schema.")
    return _mapping_out(db, mm.default_mapping(version))


class PreviewIn(BaseModel):
    old_document_id: int


@router.post("/preview")
def preview(body: PreviewIn, db: Session = Depends(get_db)):
    try:
        return mm.preview(db, body.old_document_id)
    except mm.MigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Vorschau nicht möglich: {str(exc)[:300]}") from exc


class RunIn(BaseModel):
    dry_run: bool = True
    limit: int | None = Field(default=None, ge=1)
    old_batch_ids: list[int] | None = None
    old_document_ids: list[int] | None = None
    retry_errors: bool = True
    include_deleted: bool = True
    suppliers: bool = True


@router.post("/run")
def run(body: RunIn):
    try:
        return mm.start(SessionLocal, mm.RunOptions(**body.model_dump()))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/state")
def state():
    return mm.get_state()


@router.post("/cancel")
def cancel():
    return {"cancelled": mm.cancel()}


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    by_status = dict(db.query(MainMigrationItem.status, func.count()).filter(MainMigrationItem.source_table == "document")
                     .group_by(MainMigrationItem.status).all())
    ids = [i for (i,) in db.query(MainMigrationItem.new_id).filter(MainMigrationItem.source_table == "document", MainMigrationItem.status == "imported",
                                                                  MainMigrationItem.new_id.isnot(None)).all()]
    rules = {"ok": 0, "warn": 0, "needs_review": 0, "unchecked": 0, "none": 0}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        got = {d: (v.get("status") if isinstance(v, dict) else None) for d, v in db.query(
            ExtractedRecord.document_id, ExtractedRecord.data["_validation"]).filter(
            ExtractedRecord.document_id.in_(chunk), ExtractedRecord.parent_record_id.is_(None)).all()}
        for d in chunk:
            rules[got.get(d) or "none"] = rules.get(got.get(d) or "none", 0) + 1
    return {"documents": by_status, "batches": db.query(MainMigrationItem).filter_by(source_table="batch").count(),
            "suppliers": db.query(MainMigrationItem).filter_by(source_table="supplier").count(), "rules": rules}


class ReanalyzeIn(BaseModel):
    workflow_id: int
    include: list[str] = Field(default_factory=lambda: ["needs_review", "warn", "no_data"])
    limit: int | None = Field(default=None, ge=1)


def migrated_ids(db: Session, include: list[str]) -> list[int]:
    """Neue IDs übernommener Belege, die zur KI sollen: no_data (keine Daten) und/oder nach Prüfregel-Status (needs_review|warn|unchecked)."""
    out: list[int] = []
    if "no_data" in include:
        out += [i for (i,) in db.query(MainMigrationItem.new_id).filter(
            MainMigrationItem.source_table == "document", MainMigrationItem.status == "no_data", MainMigrationItem.new_id.isnot(None)).all()]
    wanted = {s for s in include if s in ("needs_review", "warn", "unchecked")}
    if wanted:
        q = (db.query(ExtractedRecord.document_id)
             .join(MainMigrationItem, (MainMigrationItem.new_id == ExtractedRecord.document_id) & (MainMigrationItem.source_table == "document"))
             .filter(ExtractedRecord.parent_record_id.is_(None), MainMigrationItem.status == "imported",
                     ExtractedRecord.data["_validation"]["status"].as_string().in_(wanted)))
        out += [i for (i,) in q.all()]
    return sorted(set(out))


@router.post("/reanalyze")
def reanalyze(body: ReanalyzeIn, db: Session = Depends(get_db)):
    from app.routers.documents import EnqueueRequest, enqueue_documents
    ids = migrated_ids(db, body.include)
    if body.limit:
        ids = ids[:body.limit]
    ids = [i for (i,) in db.query(Document.id).filter(Document.id.in_(ids), Document.soft_deleted.is_(False), Document.status != "processing").all()] if ids else []
    if not ids:
        return {"enqueued": 0, "message": "Keine passenden Belege."}
    res = enqueue_documents(EnqueueRequest(document_ids=ids, workflow_id=body.workflow_id), db)
    return {"enqueued": getattr(res, "enqueued", None) or getattr(res, "count", None) or len(ids), "candidates": len(ids)}
