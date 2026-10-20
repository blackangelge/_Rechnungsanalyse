"""
Änderungsprotokoll: Vorgänge (Nachrüst-Läufe, Daten-Browser, KI-Freigaben) ansehen und rückgängig machen.

  GET  /api/change-log/runs                   — Liste der Vorgänge (neueste zuerst)
  GET  /api/change-log/runs/{id}/changes      — einzelne Feldänderungen (paginiert, X-Total-Count)
  POST /api/change-log/runs/{id}/undo         — Vorgang rückgängig machen (Hintergrundlauf)
  GET  /api/change-log/undo-state             — Fortschritt des Rückgängig-Laufs
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.data_change import DataChange, DataChangeRun
from app.services import change_log

router = APIRouter(prefix="/api/change-log", tags=["Änderungsprotokoll"])


def _run_dict(r: DataChangeRun) -> dict:
    return {
        "id": r.id, "kind": r.kind, "label": r.label,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "changed_rows": r.changed_rows, "changed_fields": r.changed_fields,
        "undone_at": r.undone_at.isoformat() if r.undone_at else None, "undo_summary": r.undo_summary,
    }


@router.get("/runs")
def runs(kind: str | None = None, limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    q = db.query(DataChangeRun)
    if kind:
        q = q.filter(DataChangeRun.kind.like(f"{kind}%"))
    return [_run_dict(r) for r in q.order_by(DataChangeRun.id.desc()).limit(limit).all()]


@router.get("/runs/{run_id}/changes")
def changes(run_id: int, response: Response, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
            db: Session = Depends(get_db)):
    if db.get(DataChangeRun, run_id) is None:
        raise HTTPException(status_code=404, detail="Vorgang nicht gefunden")
    q = db.query(DataChange).filter(DataChange.run_id == run_id)
    response.headers["X-Total-Count"] = str(db.query(func.count(DataChange.id)).filter(DataChange.run_id == run_id).scalar() or 0)
    rows = q.order_by(DataChange.id).offset(offset).limit(limit).all()
    return [
        {"id": c.id, "table": c.table_name, "row_id": c.row_id, "document_id": c.document_id, "field": c.field,
         "old": c.old_value if c.old_value is None or len(c.old_value) < 300 else c.old_value[:300] + "…",
         "new": c.new_value if c.new_value is None or len(c.new_value) < 300 else c.new_value[:300] + "…",
         "status": c.status}
        for c in rows
    ]


@router.post("/runs/{run_id}/undo")
def undo(run_id: int):
    try:
        return change_log.start_undo(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\""))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/undo-state")
def undo_state():
    return change_log.get_undo_state()
