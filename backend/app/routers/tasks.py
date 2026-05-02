"""
API-Endpunkte für Workflow-Tasks und Dispatcher-Status.

    GET  /api/tasks             — Alle Tasks (filterbar nach Status, paginiert)
    GET  /api/tasks/workers     — Dispatcher-Status: Worker-Anzahl + Server
    DELETE /api/tasks/{id}      — Einzelnen Task löschen
    DELETE /api/tasks           — Tasks nach Status löschen (bulk)
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.workflow_task import WorkflowTask
from app.schemas.workflow_task import WorkflowTaskListResponse, WorkflowTaskRead
from app.worker.worker import Dispatcher

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

_VALID_STATUSES = {"pending", "in_progress", "completed", "failed"}


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────

def _get_dispatcher(request: Request) -> Dispatcher:
    d = getattr(request.app.state, "dispatcher", None)
    if d is None:
        raise HTTPException(status_code=503, detail="Dispatcher nicht verfügbar")
    return d


# ── Endpunkte ─────────────────────────────────────────────────────────────────

@router.get("/workers")
def get_worker_status(request: Request):
    """Dispatcher-Status: Anzahl laufender Worker und bekannte KI-Server."""
    d = _get_dispatcher(request)
    return {
        "worker_count": len(d.workers),
        "queue_size": d.queue.qsize(),
        "servers": [
            {
                "id": s.id,
                "name": s.name,
                "active": s.active,
                "is_down": s.is_down,
                "parallel_request": s.parallel_request,
                "url": s.url,
            }
            for s in d.pool._servers.values()
        ],
    }


@router.get("", response_model=WorkflowTaskListResponse)
def list_tasks(
    status: str | None = Query(None, description="Filter: pending | in_progress | completed | failed"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Gibt alle Workflow-Tasks zurück, optional gefiltert nach Status."""
    if status and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Ungültiger Status '{status}'. Erlaubt: {sorted(_VALID_STATUSES)}",
        )

    base = select(WorkflowTask)
    count_q = select(func.count()).select_from(WorkflowTask)

    if status:
        base = base.where(WorkflowTask.status == status)
        count_q = count_q.where(WorkflowTask.status == status)

    total: int = db.execute(count_q).scalar_one()
    items = db.execute(
        base.order_by(WorkflowTask.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()

    return WorkflowTaskListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    """Löscht einen einzelnen Task anhand seiner ID."""
    task = db.get(WorkflowTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task #{task_id} nicht gefunden")
    db.delete(task)
    db.commit()


@router.delete("", status_code=200)
def delete_tasks_by_status(
    status: Literal["completed", "failed"] = Query(
        ..., description="Zu löschender Status: completed oder failed"
    ),
    db: Session = Depends(get_db),
):
    """
    Löscht alle Tasks mit dem angegebenen Status (Bulk-Delete).
    Nur 'completed' und 'failed' sind erlaubt — laufende Tasks bleiben erhalten.
    """
    result = db.execute(
        delete(WorkflowTask).where(WorkflowTask.status == status)
    )
    db.commit()
    return {"deleted": result.rowcount, "status": status}
