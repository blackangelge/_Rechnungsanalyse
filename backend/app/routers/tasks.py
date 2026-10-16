"""
API-Endpunkte für Workflow-Tasks und Worker-Status.

    GET  /api/tasks             — Alle Tasks (filterbar nach Status, paginiert)
    GET  /api/tasks/workers     — Worker-Status: Anzahl + Server (Proxy zum Worker-Container)
    POST /api/tasks/pause       — Worker pausieren (Proxy)
    POST /api/tasks/resume      — Worker fortsetzen (Proxy)
    POST /api/tasks/{id}/restart — Task zurücksetzen auf pending (inkl. Dokument)
    DELETE /api/tasks/{id}      — Einzelnen Task löschen
    DELETE /api/tasks           — Tasks nach Status löschen (bulk; status=all leert die gesamte Liste)
    POST /api/tasks/restart-all — alle abgeschlossenen (und optional fehlgeschlagenen) Tasks erneut an die KI senden

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


@router.post("/{task_id}/restart", status_code=200)
def restart_task(task_id: int, db: Session = Depends(get_db)):
    """
    Setzt einen abgeschlossenen oder fehlgeschlagenen Task zurück auf 'pending'.
    Setzt auch das verknüpfte Dokument zurück auf 'pending'.
    """
    task = db.get(WorkflowTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task #{task_id} nicht gefunden")
    if task.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Task #{task_id} kann nicht neugestartet werden (Status: {task.status})"
        )

    # Task zurücksetzen
    task.status = "pending"
    task.attempts = 0
    task.worker_id = None
    task.locked_at = None
    task.error = None

    # Verknüpftes Dokument zurücksetzen
    doc_id = task.payload.get("document_id") if task.payload else None
    if doc_id is not None:
        from app.models.document import Document
        doc = db.get(Document, int(doc_id))
        if doc is not None:
            doc.status = "pending"

    db.commit()
    return {"task_id": task_id, "status": "pending"}


@router.post("/restart-all", status_code=200)
def restart_all_tasks(
    include_failed: bool = Query(False, description="auch fehlgeschlagene Tasks erneut senden"),
    db: Session = Depends(get_db),
):
    """
    Sendet ALLE abgeschlossenen Tasks (optional auch fehlgeschlagene) erneut an die KI: Task und zugehöriges
    Dokument werden auf 'pending' zurückgesetzt; der Worker nimmt sie in der Reihenfolge der Anlage wieder auf.
    Die bisherigen Ergebnisse bleiben über die Analyse-Historie erhalten.
    """
    from app.models.document import Document

    statuses = ["completed", "failed"] if include_failed else ["completed"]
    restarted = 0
    while True:
        tasks = db.execute(
            select(WorkflowTask).where(WorkflowTask.status.in_(statuses)).order_by(WorkflowTask.id).limit(500)
        ).scalars().all()
        if not tasks:
            break
        doc_ids: list[int] = []
        for task in tasks:
            task.status = "pending"
            task.attempts = 0
            task.worker_id = None
            task.locked_at = None
            task.error = None
            doc_id = (task.payload or {}).get("document_id")
            if doc_id is not None:
                doc_ids.append(int(doc_id))
        if doc_ids:
            db.execute(
                Document.__table__.update().where(Document.id.in_(doc_ids)).values(status="pending")
            )
        db.commit()                      # nach jedem Block: die Tasks verlassen die Auswahl (status = pending)
        restarted += len(tasks)
    logger.info("%d Task(s) erneut an die KI gesendet (include_failed=%s)", restarted, include_failed)
    return {"restarted": restarted, "include_failed": include_failed}


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
    status: Literal["completed", "failed", "all"] = Query(
        ..., description="Zu löschender Status: completed, failed oder all (leert die gesamte Task-Liste)"
    ),
    db: Session = Depends(get_db),
):
    """
    Löscht alle Tasks mit dem angegebenen Status (Bulk-Delete).
    'all' leert die komplette Liste — auch wartende und laufende Tasks. Ein bereits laufender KI-Aufruf wird
    nicht abgebrochen; sein Ergebnis wird noch ins Dokument geschrieben, nur der Task-Eintrag ist dann weg.
    Dokumente behalten ihren Status (z. B. 'pending'); sie lassen sich über „Erneut senden“ wieder einreihen.
    """
    stmt = delete(WorkflowTask)
    if status != "all":
        stmt = stmt.where(WorkflowTask.status == status)
    result = db.execute(stmt)
    db.commit()
    return {"deleted": result.rowcount, "status": status}
