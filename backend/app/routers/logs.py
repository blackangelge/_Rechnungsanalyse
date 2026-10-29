"""
Router für Log- und Statistik-Endpunkte.

Endpunkte:
  GET /api/logs/ki-stats      — aggregierte KI-Tokenstatistiken über alle Analysen
  GET /api/logs/worker-stats  — aktueller Worker-Pool-Status und Queue-Länge

worker-stats fragt worker_count/max_capacity per HTTP beim Worker-Container ab
(settings.worker_api_url) — der Dispatcher läuft dort, nicht mehr im Backend-Prozess.
"""

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/logs", tags=["Logs"])

logger = logging.getLogger(__name__)


@router.get("/ki-stats")
def get_ki_stats(db: Session = Depends(get_db)):
    """Aggregierte KI-Statistiken aus documents_token_counts + zwei rollierende
    Durchsatz-Kennzahlen aus documents.analyzed_at (Abschluss der letzten Analyse)."""
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

    # Durchsatz: gezählt wird der ABSCHLUSS EINER ANALYSE (documents.analyzed_at, gesetzt bei processing → done/error).
    # Nicht updated_at: das ändert sich auch beim Import, bei der Seitenzahl-Ermittlung, bei Kommentaren usw. und hat
    # die Zahlen früher um jeden Import aufgebläht. Ein mehrfach analysierter Beleg zählt einmal (letzter Abschluss).
    from datetime import timedelta
    from app.models.document import Document
    now = datetime.now(timezone.utc)
    processed_last_60min = db.query(func.count(Document.id)).filter(
        Document.status == "done", Document.analyzed_at >= now - timedelta(minutes=60)).scalar() or 0
    processed_last_120min = db.query(func.count(Document.id)).filter(
        Document.status == "done", Document.analyzed_at >= now - timedelta(minutes=120)).scalar() or 0
    analyzed_last_24h = db.query(func.count(Document.id)).filter(
        Document.status.in_(("done", "error")), Document.analyzed_at >= now - timedelta(hours=24)).scalar() or 0

    return {
        "total_entries":      _int(row.total_entries),
        "sum_input_tokens":   _int(row.sum_input_tokens),
        "sum_output_tokens":  _int(row.sum_output_tokens),
        "sum_reasoning":      _int(row.sum_reasoning),
        "sum_duration_seconds": _float(row.sum_duration),
        "avg_input_tokens":   _float(row.avg_input_tokens),
        "avg_duration_seconds": _float(row.avg_duration),
        "processed_last_60min": int(processed_last_60min),
        "processed_last_120min": int(processed_last_120min),
        "analyzed_last_24h":    int(analyzed_last_24h),
    }


@router.get("/ki-server-stats")
def get_ki_server_stats(db: Session = Depends(get_db)):
    """
    Durchsatz JE KI-Rechner über mehrere Zeitfenster: wie viele Dokumente der jeweilige
    KI-Rechner in den letzten 1/5/10/60 Min und 4/24/48 Std verarbeitet hat (aus
    documents_token_counts.ai_client_id, gefüllt ab Einbau — ältere Durchläufe ohne Rechner).
    """
    from datetime import timedelta
    from sqlalchemy import case
    from app.models.ai_clients import AIClients
    from app.models.document_token_count import DocumentTokenCount

    now = datetime.now(timezone.utc)
    windows = [("m1", timedelta(minutes=1)), ("m5", timedelta(minutes=5)), ("m10", timedelta(minutes=10)),
               ("m60", timedelta(minutes=60)), ("h4", timedelta(hours=4)), ("h24", timedelta(hours=24)), ("h48", timedelta(hours=48))]
    cols = [DocumentTokenCount.ai_client_id]
    for key, delta in windows:
        cols.append(func.count(func.distinct(
            case((DocumentTokenCount.created_at >= now - delta, DocumentTokenCount.document_id)))).label(key))
    rows = (db.query(*cols)
            .filter(DocumentTokenCount.ai_client_id.isnot(None),
                    DocumentTokenCount.created_at >= now - windows[-1][1])      # nur das größte Fenster laden
            .group_by(DocumentTokenCount.ai_client_id).all())

    names = {c.id: c.name for c in db.query(AIClients.id, AIClients.name).all()}
    servers = {}
    for r in rows:
        cid = r[0]
        servers[str(cid)] = {"name": names.get(cid), **{windows[i][0]: int(r[i + 1] or 0) for i in range(len(windows))}}
    return {"windows": [k for k, _ in windows], "servers": servers, "generated_at": now.isoformat()}


@router.get("/worker-stats")
async def get_worker_stats(db: Session = Depends(get_db)):
    """
    Liefert den aktuellen Status des Worker-Pools:
    - Anzahl gestarteter Worker
    - Max-Kapazität (laut Einstellungen beim Start)
    - Länge der Warteschlange (pending tasks)
    - KI-Konfigurationen mit Status (aktiv / temp. deaktiviert)
    """
    from app import crud
    from app.models.ai_clients import AIClients

    # Live-Status per HTTP vom Worker-Container abfragen (Dispatcher läuft dort).
    worker_count = 0
    max_capacity = 0
    worker_online = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.worker_api_url}/status")
            resp.raise_for_status()
            data = resp.json()
            worker_count = data.get("worker_count", 0)
            max_capacity = sum(
                s.get("parallel_request", 0)
                for s in data.get("servers", [])
                if s.get("active") and not s.get("is_down")
            )
            worker_online = True
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Worker-Container nicht erreichbar (/status): %s", exc)

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

    # Anzahl Dokumente, die mindestens einmal von der KI verarbeitet wurden
    # (mindestens ein Eintrag in documents_token_counts — Typenerkennung oder Extraktion)
    documents_processed_total = db.execute(
        text("SELECT COUNT(DISTINCT document_id) FROM documents_token_counts")
    ).scalar() or 0

    # Alle KI-Konfigurationen mit Verfügbarkeitsstatus
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
        "worker_online":      worker_online,
        "ai_configs":         configs,
        "documents_processed_total": int(documents_processed_total),
    }


@router.get("/ki-live")
async def get_ki_live(db: Session = Depends(get_db)):
    """
    Live-Ansicht je KI-Rechner: welche Belege gerade dort laufen und wie lange schon.

    Quelle ist der Worker-Container (`/status` → servers[].running); Dateiname/Seiten/Import kommen aus der Datenbank.
    Ist der Worker nicht erreichbar, werden die KI-Konfigurationen ohne laufende Aufgaben geliefert (`worker_online: false`).
    Die Oberfläche ruft das alle paar Sekunden ab und lässt die Laufzeit zwischen den Abrufen selbst weiterzählen.
    Hinweis: Die Dauer ist die Laufzeit der ganzen Aufgabe auf diesem Rechner (alle KI-Anfragen des Workflows zusammen).
    """
    from app import crud
    from app.models.document import Document
    from app.models.import_batch import ImportBatch

    data: dict = {}
    worker_online = False
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"{settings.worker_api_url}/status")
            resp.raise_for_status()
            data = resp.json()
            worker_online = True
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Worker-Container nicht erreichbar (/status): %s", exc)

    configs = {c.id: c for c in crud.ai_config.get_all(db)}
    live = {s["id"]: s for s in data.get("servers", [])} if worker_online else {}

    doc_ids = [r["document_id"] for s in live.values() for r in s.get("running", []) if r.get("document_id")]
    docs: dict[int, dict] = {}
    if doc_ids:
        for d, b in (db.query(Document, ImportBatch).join(ImportBatch, Document.batch_id == ImportBatch.id)
                     .filter(Document.id.in_(doc_ids)).all()):
            docs[d.id] = {"filename": d.original_filename, "pages": d.page_count or None, "company": b.company_name, "year": b.year,
                          "pdf_version": d.pdf_version}

    servers = []
    for cid, c in sorted(configs.items()):
        srv = live.get(cid, {})
        running = [{
            "task_id": r["task_id"], "document_id": r.get("document_id"), "elapsed_seconds": r.get("elapsed_seconds", 0.0),
            **(docs.get(r.get("document_id")) or {}),
        } for r in srv.get("running", [])]
        ta = c.timeout_at
        if ta is not None and ta.tzinfo is None:
            ta = ta.replace(tzinfo=timezone.utc)
        servers.append({
            "id": cid, "name": c.name, "model_name": c.model_name, "url": srv.get("url"),
            "active": bool(c.active), "is_down": bool(srv.get("is_down")) or bool(ta and ta > datetime.now(timezone.utc)),
            "timeout_at": c.timeout_at.isoformat() if c.timeout_at else None,
            "parallel_request": c.parallel_request, "busy": len(running), "running": running,
        })

    queue_pending = db.execute(text("SELECT COUNT(*) FROM workflow_tasks WHERE status = 'pending'")).scalar() or 0
    in_progress = db.execute(text("SELECT COUNT(*) FROM workflow_tasks WHERE status = 'in_progress'")).scalar() or 0
    on_servers = sum(len(s["running"]) for s in servers)
    return {
        "worker_online": worker_online,
        "paused": bool(data.get("paused")),
        "queue_pending": int(queue_pending),
        "in_progress": int(in_progress),
        "running_total": on_servers,
        "waiting_for_slot": max(0, int(in_progress) - on_servers) if worker_online else 0,   # vom Worker übernommen, aber noch ohne freien Slot / in Vorbereitung
        "servers": servers,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
