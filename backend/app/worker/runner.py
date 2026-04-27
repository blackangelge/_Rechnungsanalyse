# app/worker/runner.py
"""
Worker-Pool für die asynchrone Dokumentenverarbeitung.

Architektur
-----------
- Worker-Anzahl = Summe der parallel_request aller aktiven KI-Konfigurationen
  (wird beim Start des Pools aus der DB berechnet).
- Worker sind NICHT an eine spezifische KI gebunden: Sie wählen bei jedem Task
  dynamisch eine verfügbare KI aus (Load Balancing, berücksichtigt temp. Sperren).
- Bei KI-Verbindungsfehlern: bis zu AI_MAX_FAILURES aufeinanderfolgende Fehler,
  dann 10-Minuten-Sperre für diese KI. Der Task wird re-queued und von einem
  anderen Worker/einer anderen KI übernommen.
- Wenn keine KI verfügbar ist, warten alle Worker und prüfen alle 30 s erneut.
"""

import asyncio
import logging
import os
import socket
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal as async_session_factory
from app.models.workflow_task import TaskKind

logger = logging.getLogger(__name__)

# ── Konfiguration (überschreibbar via Environment-Variablen) ─────────────────
POLL_INTERVAL        = float(os.getenv("WORKER_POLL_INTERVAL",    "2.0"))   # Sekunden zwischen Polls wenn Queue leer
POLL_NO_AI           = float(os.getenv("WORKER_POLL_NO_AI",       "30.0"))  # Sekunden warten wenn keine KI verfügbar
LOCK_TIMEOUT         = timedelta(minutes=int(os.getenv("WORKER_LOCK_TIMEOUT_MIN", "10")))  # Wann ein "in_progress"-Task als hängend gilt
AI_MAX_FAILURES      = int(os.getenv("WORKER_AI_MAX_FAILURES",    "3"))     # Aufeinanderfolgende Fehler bis KI gesperrt wird
AI_DISABLE_MINUTES   = int(os.getenv("WORKER_AI_DISABLE_MIN",     "10"))    # Sperrdauer der KI in Minuten
WORKER_HOST          = socket.gethostname()                                  # Hostname für worker_id (Identifikation im Log)

# Zählt aufeinanderfolgende KI-Verbindungsfehler pro KI-Konfiguration.
# Wird auf 0 zurückgesetzt wenn ein Task erfolgreich abgeschlossen wird.
# Format: {ai_config_id: aufeinanderfolgende_fehler_anzahl}
_ai_failure_counts: dict[int, int] = {}

# Semaphoren pro KI-Konfiguration — begrenzen gleichzeitige Anfragen auf parallel_request.
# Werden beim ersten Zugriff auf eine KI-Config dynamisch erstellt.
# Format: {ai_config_id: asyncio.Semaphore(parallel_request)}
_ai_semaphores: dict[int, asyncio.Semaphore] = {}


# ── Synchrone Hilfsfunktionen (laufen in Threads) ────────────────────────────

def _get_worker_capacity() -> int:
    """Berechnet die Gesamt-Worker-Anzahl aus aktiven KI-Konfigurationen."""
    from app import crud
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        capacity = crud.ai_config.get_worker_capacity(db)
        return max(1, capacity)
    except Exception as exc:
        logger.error("Fehler beim Berechnen der Worker-Kapazität: %s", exc)
        return 1
    finally:
        db.close()


def _is_ai_available() -> bool:
    """Prüft ob mindestens eine aktive KI verfügbar ist (nicht temp. deaktiviert)."""
    from app import crud
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return crud.ai_config.get_default(db) is not None
    except Exception:
        return False
    finally:
        db.close()


def _prepare_document(document_id: int) -> tuple[int | None, str | None, str, int]:
    """
    Prüft das Dokument, wählt eine aktive KI und setzt Status → 'processing'.

    Rückgabe: (ai_config_id, system_prompt_text, status_code, parallel_request)
    status_code:
      "ok"       — alles bereit
      "no_ai"    — keine aktive KI verfügbar
      "skip"     — Dokument kann nicht verarbeitet werden (nicht gefunden, gelöscht etc.)
    """
    from app import crud
    from app.database import SessionLocal
    from app.models.document import Document as DocModel

    db = SessionLocal()
    try:
        doc = db.get(DocModel, document_id)
        if doc is None:
            logger.error("[worker] Dokument #%d nicht gefunden", document_id)
            return None, None, "skip", 1
        if not doc.stored_filename:
            logger.error("[worker] Dokument #%d hat keine gespeicherte Datei", document_id)
            return None, None, "skip", 1
        if doc.soft_deleted:
            logger.warning("[worker] Dokument #%d ist gelöscht — übersprungen", document_id)
            return None, None, "skip", 1
        if doc.status == "processing":
            # Bereits durch einen anderen Worker oder den direkten Analyse-Pfad in Bearbeitung.
            # _analyze_single hat zusätzlich einen In-Memory-Guard (_analyzing_docs).
            logger.warning(
                "[worker] Dokument #%d wird bereits verarbeitet (status=processing) — übersprungen",
                document_id,
            )
            return None, None, "skip", 1

        ai_config = crud.ai_config.get_default(db)
        if ai_config is None:
            logger.warning("[worker] Keine aktive KI verfügbar für Dokument #%d", document_id)
            return None, None, "no_ai", 1

        sp = crud.system_prompt.get_default(db)
        system_prompt_text: str | None = sp.content if sp else None

        crud.document.update_status(db, document_id, "processing")

        logger.info(
            "[worker] Dokument #%d → KI-Config #%d '%s' (parallel=%d)",
            document_id, ai_config.id, ai_config.name, ai_config.parallel_request,
        )
        return ai_config.id, system_prompt_text, "ok", max(1, ai_config.parallel_request)

    except Exception as exc:
        logger.exception(
            "[worker] Fehler beim Vorbereiten von Dokument #%d: %s", document_id, exc
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None, None, "skip", 1
    finally:
        db.close()


def _temporarily_disable_ai(ai_config_id: int) -> None:
    """Setzt timeout_at für die KI-Konfiguration (AI_DISABLE_MINUTES Minuten)."""
    from app import crud
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        crud.ai_config.temporarily_disable(db, ai_config_id, AI_DISABLE_MINUTES)
    except Exception as exc:
        logger.error(
            "[worker] Fehler beim temporären Deaktivieren von KI #%d: %s", ai_config_id, exc
        )
    finally:
        db.close()


def _set_document_error(document_id: int, message: str) -> None:
    """Setzt den Dokument-Status auf 'error'."""
    from app import crud
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        crud.document.update_status(db, document_id, "error")
    except Exception as exc:
        logger.error(
            "[worker] Konnte Fehlerstatus für Dokument #%d nicht setzen: %s", document_id, exc
        )
    finally:
        db.close()


def _set_document_error_if_not_done(document_id: int, message: str) -> None:
    """
    Setzt den Dokument-Status auf 'error' — aber NUR wenn er nicht bereits 'done' ist.

    Hintergrund: Wenn die KI-Analyse erfolgreich abgeschlossen wurde und das Dokument
    auf 'done' gesetzt wurde, aber danach ein technischer Fehler (z.B. COMPLETE_SQL-Fehler)
    auftritt, darf 'done' nicht mit 'error' überschrieben werden. Andernfalls würde der
    Task neu versucht und die KI erneut aufgerufen — obwohl das Ergebnis bereits gespeichert ist.
    """
    from app.database import SessionLocal
    from app.models.document import Document as DocModel
    db = SessionLocal()
    try:
        doc = db.get(DocModel, document_id)
        if doc is None:
            return
        if doc.status == "done":
            logger.info(
                "[worker] Dokument #%d ist bereits 'done' — Fehlerstatus wird NICHT gesetzt"
                " (Analyse war erfolgreich, nur Bookkeeping-Fehler).",
                document_id,
            )
            return
        from app import crud
        crud.document.update_status(db, document_id, "error")
        logger.error("[worker] Dokument #%d: Status → error. Grund: %s", document_id, message)
    except Exception as exc:
        logger.error(
            "[worker] Konnte Fehlerstatus für Dokument #%d nicht setzen: %s", document_id, exc
        )
    finally:
        db.close()


# ── Task-Handler ─────────────────────────────────────────────────────────────

async def handle_process_document(
    payload: dict,
    _session: AsyncSession,
) -> tuple[dict, str, int | None]:
    """
    Verarbeitet ein Dokument über die KI-Pipeline.

    Rückgabe: (result_dict, result_code, ai_config_id_used)

    result_code:
      "ok"            — Fertig (auch wenn das Dokument einen Fehler hat)
      "ai_unavailable"— KI nicht erreichbar → Task re-queuen, KI-Fehler zählen
      "no_ai"         — Keine aktive KI → Task re-queuen, warten
      "skip"          — Dokument nicht verarbeitbar → Task als completed markieren
    """
    document_id = payload.get("document_id")
    if not isinstance(document_id, int):
        raise ValueError(f"Ungültige document_id im Payload: {payload!r}")

    logger.info("[worker] Starte Verarbeitung Dokument #%d", document_id)

    ai_config_id, system_prompt_text, prep_code, parallel_request = await asyncio.to_thread(
        _prepare_document, document_id
    )

    if prep_code == "skip":
        return {"document_id": document_id, "skipped": True}, "skip", None
    if prep_code == "no_ai":
        return {"document_id": document_id}, "no_ai", None

    # Semaphor für diese KI holen/anlegen — begrenzt gleichzeitige Anfragen auf parallel_request
    sem = _ai_semaphores.get(ai_config_id)
    if sem is None:
        sem = asyncio.Semaphore(parallel_request)
        _ai_semaphores[ai_config_id] = sem
        logger.debug(
            "[worker] Semaphore für KI #%d angelegt (slots=%d)", ai_config_id, parallel_request
        )

    # Eigentliche Analyse via _analyze_single — max. parallel_request gleichzeitig pro KI
    from app.routers.documents import _analyze_single
    async with sem:
        result_code = await _analyze_single(document_id, ai_config_id, system_prompt_text)

    logger.info(
        "[worker] Dokument #%d abgeschlossen (code=%s, ki=%d)",
        document_id, result_code, ai_config_id,
    )
    return {"document_id": document_id, "status": "done"}, result_code or "ok", ai_config_id


HANDLERS = {
    TaskKind.PROCESS_DOCUMENT: handle_process_document,
}


# ── SQL ───────────────────────────────────────────────────────────────────────

# ── SQL-Statements für die Worker-Queue ──────────────────────────────────────

# CLAIM_SQL: Atomares Beanspruchen des nächsten wartenden Tasks.
# FOR UPDATE SKIP LOCKED verhindert, dass zwei Worker denselben Task beanspruchen.
# Inkrementiert attempts — wichtig für die FAIL_SQL-Logik.
CLAIM_SQL = text("""
    UPDATE workflow_tasks
    SET status    = 'in_progress',
        worker_id = :worker_id,
        locked_at = now(),
        attempts  = attempts + 1,
        updated_at = now()
    WHERE id = (
        SELECT id FROM workflow_tasks
        WHERE  status = 'pending' AND attempts < max_attempts
        ORDER  BY id
        FOR UPDATE SKIP LOCKED
        LIMIT  1
    )
    RETURNING id, payload, attempts, max_attempts
""")

# COMPLETE_SQL: Markiert einen Task als erfolgreich abgeschlossen.
COMPLETE_SQL = text("""
    UPDATE workflow_tasks
    SET status = 'completed', result = :result, error = NULL, updated_at = now()
    WHERE id = :id
""")

# FAIL_SQL: Markiert einen Task als fehlgeschlagen oder stellt ihn zurück in die Queue.
# Wenn attempts >= max_attempts → 'failed' (kein weiterer Versuch).
# Sonst → zurück auf 'pending' für einen neuen Versuch.
# VERBRAUCHT einen attempt (CLAIM_SQL hat bereits inkrementiert).
FAIL_SQL = text("""
    UPDATE workflow_tasks
    SET status     = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
        error      = :error,
        worker_id  = NULL,
        locked_at  = NULL,
        updated_at = now()
    WHERE id = :id
""")

# REQUEUE_SQL: Stellt einen Task zurück in die Queue OHNE einen Versuch zu verbrauchen.
# Wird bei KI-Verbindungsfehlern verwendet — der Task soll von einer anderen KI
# oder nach einer Pause erneut versucht werden, ohne das attempts-Budget zu belasten.
# attempts - 1 macht das CLAIM_SQL-Inkrement rückgängig.
REQUEUE_SQL = text("""
    UPDATE workflow_tasks
    SET status     = 'pending',
        worker_id  = NULL,
        locked_at  = NULL,
        attempts   = GREATEST(attempts - 1, 0),
        error      = :error,
        updated_at = now()
    WHERE id = :id
""")

# CLEANUP_SQL: Setzt hängende 'in_progress'-Tasks zurück auf 'pending'.
# Wird alle 60 Sekunden vom cleanup_loop ausgeführt.
# Tasks die länger als LOCK_TIMEOUT in 'in_progress' stecken, werden freigegeben
# (z.B. nach Container-Neustart während einer Analyse).
CLEANUP_SQL = text("""
    UPDATE workflow_tasks
    SET status = 'pending', worker_id = NULL, locked_at = NULL, updated_at = now()
    WHERE status = 'in_progress' AND locked_at < now() - :timeout
""")


# ── Worker-Coroutine ──────────────────────────────────────────────────────────

async def worker_loop(worker_name: str, shutdown: asyncio.Event) -> None:
    logger.info("[%s] gestartet", worker_name)

    while not shutdown.is_set():

        # ── Schritt 1: Prüfen ob KI verfügbar ist ────────────────────────────
        ai_ok = await asyncio.to_thread(_is_ai_available)
        if not ai_ok:
            logger.debug("[%s] Keine aktive KI — warte %.0fs", worker_name, POLL_NO_AI)
            await _sleep_or_shutdown(shutdown, POLL_NO_AI)
            continue

        # ── Schritt 2: Task aus Queue holen ──────────────────────────────────
        row = None
        async with async_session_factory() as session:
            try:
                row = (await session.execute(CLAIM_SQL, {"worker_id": worker_name})).first()
                await session.commit()
            except Exception:
                logger.exception("[%s] DB-Fehler beim Claim", worker_name)
                await session.rollback()
                await _sleep_or_shutdown(shutdown, POLL_INTERVAL)
                continue

        if row is None:
            await _sleep_or_shutdown(shutdown, POLL_INTERVAL)
            continue

        task_id, payload, attempts, max_attempts = row
        kind = payload.get("kind")
        handler = HANDLERS.get(kind)

        # ── Schritt 3: Task verarbeiten ───────────────────────────────────────
        try:
            if handler is None:
                raise ValueError(f"Unbekannter Task-Typ: {kind!r}")

            logger.info(
                "[%s] Task #%d (%s, Versuch %d/%d)",
                worker_name, task_id, kind, attempts, max_attempts,
            )

            async with async_session_factory() as session:
                result_data, result_code, ai_config_id_used = await handler(payload, session)

            # ── Ergebnis auswerten ────────────────────────────────────────────
            if result_code == "ai_unavailable":
                await _handle_ai_failure(worker_name, task_id, payload, ai_config_id_used)

            elif result_code == "no_ai":
                # Keine KI → re-queuen, länger warten
                async with async_session_factory() as s:
                    await s.execute(
                        REQUEUE_SQL,
                        {"id": task_id, "error": "Keine aktive KI verfügbar"},
                    )
                    await s.commit()
                await _sleep_or_shutdown(shutdown, POLL_NO_AI)

            else:
                # "ok" oder "skip" → abgeschlossen
                if result_code == "ok" and ai_config_id_used is not None:
                    # Erfolg: Fehlerzähler für diese KI zurücksetzen
                    _ai_failure_counts[ai_config_id_used] = 0

                async with async_session_factory() as s:
                    await s.execute(COMPLETE_SQL, {"id": task_id, "result": result_data})
                    await s.commit()

        except Exception as exc:
            logger.exception("[%s] Task #%d: unbehandelter Fehler", worker_name, task_id)
            doc_id = payload.get("document_id")
            if isinstance(doc_id, int):
                # Nicht überschreiben, wenn die Analyse schon erfolgreich war (status='done').
                # Verhindert: Analyse ok → COMPLETE_SQL-Fehler → 'done' wird zu 'error' → Retry → 3x KI.
                await asyncio.to_thread(
                    _set_document_error_if_not_done, doc_id, f"Task #{task_id} fehlgeschlagen: {exc}"
                )
            async with async_session_factory() as s:
                await s.execute(FAIL_SQL, {"id": task_id, "error": str(exc)[:5000]})
                await s.commit()

    logger.info("[%s] beendet", worker_name)


async def _handle_ai_failure(
    worker_name: str,
    task_id: int,
    payload: dict,
    ai_config_id: int | None,
) -> None:
    """
    Behandelt einen KI-Verbindungsfehler:
    1. Fehlerzähler für diese KI erhöhen
    2. Bei AI_MAX_FAILURES aufeinanderfolgenden Fehlern: KI temporär sperren
    3. Task re-queuen (ohne attempts zu verbrauchen)
    """
    error_msg = "KI nicht erreichbar"

    if ai_config_id is not None:
        prev_count = _ai_failure_counts.get(ai_config_id, 0)
        new_count  = prev_count + 1
        _ai_failure_counts[ai_config_id] = new_count

        error_msg = f"KI #{ai_config_id} nicht erreichbar (Fehler {new_count}/{AI_MAX_FAILURES})"

        if new_count >= AI_MAX_FAILURES:
            logger.error(
                "[%s] KI #%d: %d aufeinanderfolgende Fehler → temporär gesperrt für %d min",
                worker_name, ai_config_id, new_count, AI_DISABLE_MINUTES,
            )
            await asyncio.to_thread(_temporarily_disable_ai, ai_config_id)
            _ai_failure_counts[ai_config_id] = 0
        else:
            logger.warning(
                "[%s] KI #%d: Verbindungsfehler %d/%d",
                worker_name, ai_config_id, new_count, AI_MAX_FAILURES,
            )

    # Task re-queuen
    async with async_session_factory() as s:
        await s.execute(REQUEUE_SQL, {"id": task_id, "error": error_msg})
        await s.commit()

    logger.info("[%s] Task #%d re-queued (%s)", worker_name, task_id, error_msg)


async def cleanup_loop(shutdown: asyncio.Event) -> None:
    """Gibt hängende 'in_progress'-Tasks nach LOCK_TIMEOUT zurück in die Queue."""
    while not shutdown.is_set():
        try:
            async with async_session_factory() as session:
                result = await session.execute(CLEANUP_SQL, {"timeout": LOCK_TIMEOUT})
                await session.commit()
                if result.rowcount:
                    logger.warning(
                        "Cleanup: %d hängende Tasks zurückgesetzt", result.rowcount
                    )
        except Exception:
            logger.exception("Cleanup-Fehler")
        await _sleep_or_shutdown(shutdown, 60)


async def _sleep_or_shutdown(shutdown: asyncio.Event, seconds: float) -> None:
    """Schläft `seconds` Sekunden, wacht aber sofort auf wenn Shutdown gesetzt wird."""
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


# ── WorkerPool (Lifecycle) ────────────────────────────────────────────────────

class WorkerPool:
    def __init__(self) -> None:
        self.shutdown       = asyncio.Event()
        self.tasks:          list[asyncio.Task] = []
        self.worker_count:   int = 0   # tatsächlich gestartete Worker
        self.max_capacity:   int = 0   # Kapazität laut DB beim Start

    async def start(self) -> None:
        # Kapazität aus DB berechnen
        self.max_capacity = await asyncio.to_thread(_get_worker_capacity)
        self.worker_count = self.max_capacity

        logger.info(
            "Starte Worker-Pool: host=%s worker=%d",
            WORKER_HOST, self.worker_count,
        )

        for i in range(self.worker_count):
            self.tasks.append(
                asyncio.create_task(
                    worker_loop(f"{WORKER_HOST}-w{i:02d}", self.shutdown)
                )
            )
        self.tasks.append(asyncio.create_task(cleanup_loop(self.shutdown)))

    async def stop(self) -> None:
        logger.info("Stoppe Worker-Pool, warte auf laufende Tasks...")
        self.shutdown.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Worker-Pool beendet")
