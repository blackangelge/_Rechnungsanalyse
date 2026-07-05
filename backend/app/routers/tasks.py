"""
API-Endpunkte für Workflow-Tasks und Worker-Status.

    GET  /api/tasks             — Alle Tasks (filterbar nach Status, paginiert)
    GET  /api/tasks/workers     — Worker-Status: Anzahl + Server (Proxy zum Worker-Container)
    POST /api/tasks/pause       — Worker pausieren (Proxy)
    POST /api/tasks/resume      — Worker fortsetzen (Proxy)
    DELETE /api/tasks/{id}      — Einzelnen Task löschen
    DELETE /api/tasks           — Tasks nach Status löschen (bulk)

Der Dispatcher läuft im eigenen Worker-Container (app.worker.main) und ist von
hier aus nicht mehr im Prozessspeicher erreichbar — /workers, /pause und
/resume proxyen daher per HTTP zum Worker (settings.worker_api_url).
"""

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.workflow_task import WorkflowTask
from app.schemas.workflow_task import WorkflowTaskListResponse, WorkflowTaskRead

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

logger = logging.getLogger(__name__)

_VALID_STATUSES = {"pending", "in_progress", "completed", "failed"}

# Kurzes Timeout: interner Aufruf im selben Docker-Netz, soll die Antwort
# des Backends nicht blockieren falls der Worker-Container gerade neu startet.
_WORKER_HTTP_TIMEOUT = 3.0


# ── Hilfsfunktion ─────────────────────────────────────────────────────────────

async def _call_worker(method: str, path: str, db: Session) -> dict:
    """Ruft einen Endpunkt des Worker-Containers auf.

    Bei Nichterreichbarkeit (Container startet noch/ist down) wird ein
    DB-basierter Fallback zurückgegeben statt eines Fehlers — Queue-Länge
    lässt sich auch ohne laufenden Worker aus workflow_tasks ablesen.
    """
    try:
        async with httpx.AsyncClient(timeout=_WORKER_HTTP_TIMEOUT) as client:
            resp = await client.request(method, f"{settings.worker_api_url}{path}")
            resp.raise_for_status()
            data = resp.json()
            data["worker_online"] = True
            return data
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Worker-Container nicht erreichbar (%s %s): %s", method, path, exc)
        queue_size = db.execute(
            select(func.count()).select_from(WorkflowTask)
            .where(WorkflowTask.status.in_(["pending", "in_progress"]))
        ).scalar_one()
        return {
            "worker_count": 0,
            "queue_size": int(queue_size),
            "servers": [],
            "paused": None,
            "worker_online": False,
        }


# ── Endpunkte ─────────────────────────────────────────────────────────────────

@router.get("/workers")
async def get_worker_status(db: Session = Depends(get_db)):
    """Worker-Status: Anzahl laufender Worker und bekannte KI-Server (Proxy)."""
    return await _call_worker("GET", "/status", db)


@router.post("/pause")
async def pause_worker(db: Session = Depends(get_db)):
    """Weist den Worker-Container an, keine neuen Tasks mehr aus der DB zu laden."""
    return await _call_worker("POST", "/pause", db)


@router.post("/resume")
async def resume_worker(db: Session = Depends(get_db)):
    """Weist den Worker-Container an, wieder neue Tasks anzunehmen."""
    return await _call_worker("POST", "/resume", db)


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
