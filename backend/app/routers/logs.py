"""
Router für Log- und Statistik-Endpunkte.

Endpunkte:
  GET /api/logs/ki-stats      — aggregierte KI-Tokenstatistiken über alle Analysen
  GET /api/logs/worker-stats  — aktueller Worker-Pool-Status und Queue-Länge
"""

from datetime import timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/api/logs", tags=["Logs"])


@router.get("/ki-stats")
def get_ki_stats(db: Session = Depends(get_db)):
    """Aggregierte KI-Statistiken aus documents_token_counts."""
    from app.models.document_token_count import DocumentTokenCount

    row = db.query(
        func.count(DocumentTokenCount.id).label("total_entries"),
        func.sum(DocumentTokenCount.input_token_count).label("sum_input_tokens"),
        func.sum(DocumentTokenCount.output_token_count).label("sum_output_tokens"),
        func.sum(DocumentTokenCount.reasoning_count).label("sum_reasoning"),
        func.sum(DocumentTokenCount.time_spent_seconds).label("sum_duration"),
        func.avg(DocumentTokenCount.input_token_count).label("avg_input_tokens"),
        func.avg(DocumentTokenCount.time_spent_seconds).label("avg_duration"),
    ).one()

    def _int(v) -> int | None:
        return int(v) if v is not None else None

    def _float(v) -> float | None:
        return round(float(v), 2) if v is not None else None

    return {
        "total_entries":      _int(row.total_entries),
        "sum_input_tokens":   _int(row.sum_input_tokens),
        "sum_output_tokens":  _int(row.sum_output_tokens),
        "sum_reasoning":      _int(row.sum_reasoning),
        "sum_duration_seconds": _float(row.sum_duration),
        "avg_input_tokens":   _float(row.avg_input_tokens),
        "avg_duration_seconds": _float(row.avg_duration),
    }


@router.get("/worker-stats")
def get_worker_stats(request: Request, db: Session = Depends(get_db)):
    """
    Liefert den aktuellen Status des Worker-Pools:
    - Anzahl gestarteter Worker
    - Max-Kapazität (laut Einstellungen beim Start)
    - Länge der Warteschlange (pending tasks)
    - KI-Konfigurationen mit Status (aktiv / temp. deaktiviert)
    """
    from app import crud
    from app.models.ai_clients import AIClients

    # Worker-Pool-Infos aus app.state
    pool = getattr(request.app.state, "worker_pool", None)
    worker_count = pool.worker_count if pool else 0
    # max_capacity wurde durch worker_count ersetzt (beide identisch seit Refactor)
    max_capacity = worker_count

    # Warteschlange
    queue_length = db.execute(
        text("SELECT COUNT(*) FROM workflow_tasks WHERE status = 'pending'")
    ).scalar() or 0

    in_progress = db.execute(
        text("SELECT COUNT(*) FROM workflow_tasks WHERE status = 'in_progress'")
    ).scalar() or 0

    failed = db.execute(
        text("SELECT COUNT(*) FROM workflow_tasks WHERE status = 'failed'")
    ).scalar() or 0

    # Alle KI-Konfigurationen mit Verfügbarkeitsstatus
    from datetime import datetime
    now = datetime.now(timezone.utc)
    configs = []
    for c in crud.ai_config.get_all(db):
        # timeout_at ist TIMESTAMPTZ → timezone-aware; direkter Vergleich mit now() möglich
        ta = c.timeout_at
        if ta is not None and ta.tzinfo is None:
            ta = ta.replace(tzinfo=timezone.utc)
        temp_disabled = bool(c.active and ta is not None and ta > now)
        configs.append({
            "id":               c.id,
            "name":             c.name,
            "active":           c.active,
            "temp_disabled":    temp_disabled,
            "timeout_at":       c.timeout_at.isoformat() if c.timeout_at else None,
            "parallel_request": c.parallel_request,
        })

    # Kapazität laut aktuellen Einstellungen (kann von worker_count abweichen,
    # wenn Konfigurationen seit dem Start geändert wurden)
    current_capacity = crud.ai_config.get_worker_capacity(db)
    no_ai_available  = current_capacity == 0

    return {
        "worker_count":       worker_count,
        "max_capacity":       max_capacity,
        "current_capacity":   current_capacity,
        "queue_length":       int(queue_length),
        "in_progress":        int(in_progress),
        "failed_tasks":       int(failed),
        "no_ai_available":    no_ai_available,
        "ai_configs":         configs,
    }
