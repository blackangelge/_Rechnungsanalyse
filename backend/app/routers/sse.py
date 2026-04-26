"""
Server-Sent Events (SSE) Router für den Import-Fortschritt.

Endpunkt:
  GET /api/imports/{id}/progress

Der Client (Browser) verbindet sich mit EventSource und empfängt
sekündlich aktualisierte Fortschrittsdaten, bis der Import abgeschlossen ist.

Technischer Ablauf:
1. Client öffnet EventSource-Verbindung
2. Server liest jede Sekunde den aktuellen BatchStatus aus der DB
3. Fortschrittsevent als JSON wird gesendet
4. Bei Status "done" oder "error" wird ein abschließendes Event gesendet
5. Generator endet → Browser schließt EventSource automatisch

Warum DB-Polling statt In-Memory-Queue?
- Einfacher, funktioniert nach Container-Neustarts
- Für die erwarteten Datenmengen (< 100 Batches gleichzeitig) absolut ausreichend
- 1-Sekunden-Polling ist für die UX vollkommen ausreichend
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sse_starlette.sse import EventSourceResponse

from app.database import get_async_db
from app.models.import_batch import ImportBatch
from app.models.document import Document

logger = logging.getLogger(__name__)

# SSE-Endpunkte werden unter /api/imports registriert (gleicher Prefix wie imports.py)
router = APIRouter(prefix="/api/imports", tags=["Import-Fortschritt (SSE)"])

# Maximale Zeit in Sekunden, die der SSE-Generator aktiv bleibt.
# Schützt vor hängenden Verbindungen bei fehlgeschlagenen Batches.
MAX_STREAM_DURATION_SECONDS = 3600  # 1 Stunde


@router.get("/{batch_id}/progress")
async def stream_import_progress(
    batch_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    SSE-Endpunkt für den Echtzeit-Fortschritt eines Imports.

    Sendet sekündlich ein JSON-Event mit:
    - total: Gesamtanzahl der Dokumente
    - processed: Verarbeitete Dokumente
    - percent: Fortschritt in Prozent
    - elapsed_seconds: Vergangene Zeit seit Importstart
    - docs_per_minute: Verarbeitungsgeschwindigkeit
    - current_status: aktueller Batch-Status
    """
    # Batch-Existenz vorab prüfen (synchron über async-Session)
    result = await db.execute(select(ImportBatch).where(ImportBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Import-Batch nicht gefunden")

    async def event_generator():
        """
        Generator-Funktion, die SSE-Events erzeugt.
        Läuft, bis der Import abgeschlossen ist oder der Client die Verbindung trennt.
        """
        stream_start = time.monotonic()

        while True:
            # ── Verbindungs-Check: Hat der Client die Verbindung getrennt? ────
            if await request.is_disconnected():
                logger.info("Client hat SSE-Verbindung für Batch #%d getrennt", batch_id)
                break

            # ── Timeout-Check ─────────────────────────────────────────────────
            if time.monotonic() - stream_start > MAX_STREAM_DURATION_SECONDS:
                logger.warning("SSE-Timeout für Batch #%d", batch_id)
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "SSE-Stream-Timeout"}),
                }
                break

            # ── Aktuellen Batch-Status aus DB lesen ───────────────────────────
            await db.refresh(batch)

            # Fortschritt aus Dokumenten berechnen
            total_result = await db.execute(
                select(func.count()).where(
                    Document.batch_id == batch_id,
                    Document.soft_deleted == False,  # noqa: E712
                )
            )
            total = total_result.scalar() or 0

            processed_result = await db.execute(
                select(func.count()).where(
                    Document.batch_id == batch_id,
                    Document.soft_deleted == False,  # noqa: E712
                    Document.status.in_(["done", "error"]),
                )
            )
            processed = processed_result.scalar() or 0

            percent = round((processed / total * 100) if total > 0 else 0.0, 1)

            # Vergangene Zeit berechnen (ab started_at, falls gesetzt)
            if batch.started_at:
                from datetime import datetime, timezone
                elapsed = (
                    datetime.now(timezone.utc) - batch.started_at
                ).total_seconds()
            else:
                elapsed = 0.0

            # Verarbeitungsgeschwindigkeit berechnen
            docs_per_minute = (
                round((processed / elapsed * 60), 1)
                if elapsed > 5 and processed > 0
                else 0.0
            )

            # ── Event-Daten zusammenbauen ─────────────────────────────────────
            event_data = {
                "total": total,
                "processed": processed,
                "percent": percent,
                "elapsed_seconds": round(elapsed),
                "docs_per_minute": docs_per_minute,
                "status": batch.status,
            }

            # ── Event senden ──────────────────────────────────────────────────
            yield {
                "event": "progress",
                "data": json.dumps(event_data),
            }

            # ── Abschluss-Check ───────────────────────────────────────────────
            if batch.status in ("done", "error"):
                # Abschließendes Event senden, dann Stream beenden
                yield {
                    "event": "done",
                    "data": json.dumps({
                        **event_data,
                        "message": (
                            "Import erfolgreich abgeschlossen"
                            if batch.status == "done"
                            else "Import mit Fehler abgeschlossen"
                        ),
                    }),
                }
                logger.info(
                    "SSE-Stream für Batch #%d beendet (Status: %s)", batch_id, batch.status
                )
                break

            # 1 Sekunde warten bis zum nächsten Poll
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())
