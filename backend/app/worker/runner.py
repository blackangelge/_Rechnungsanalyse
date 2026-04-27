# app/worker/runner.py
"""
Worker-Pool für die asynchrone Dokumentenverarbeitung.

Architektur
-----------
- Alle Worker sind GENERISCH — keine statische KI-Bindung, kein Container-Neustart
  nötig wenn KIs hinzugefügt, aktiviert oder deaktiviert werden.
- Aktive KI-Konfigurationen werden aus der DB geladen und für AI_CACHE_TTL Sekunden
  gecacht (Standard: 30 s). Neue KIs werden automatisch erkannt.
- Lastverteilung per Slot-Counter (_ai_slot_usage):
    Vor jedem Task-Claim prüft ein Worker welche KI freie Slots hat und wählt
    die am wenigsten ausgelastete. Der Counter wird atomar aktualisiert (kein
    await zwischen Check und Increment → keine Race Condition in asyncio).
    Ergebnis: Bei 2 aktiven KIs (je parallel_request=1) verarbeiten 2 Worker
    gleichzeitig je einen Task auf verschiedenen KI-Rechnern.
- Worker-Anzahl = Summe der parallel_request aller aktiven KIs beim Start.
  Werden nach dem Start neue KIs aktiviert, nutzen die vorhandenen Worker diese
  automatisch. Für mehr Parallelität (zusätzliche Worker-Slots) genügt es, die
  KI-Konfiguration zu speichern und den Container einmalig neu zu starten.
- Bei KI-Verbindungsfehlern: bis zu AI_MAX_FAILURES aufeinanderfolgende Fehler,
  dann AI_DISABLE_MINUTES Sperre. Task re-queued (attempts nicht verbraucht).
"""

import asyncio
import json
import logging
import os
import socket
import time
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal as async_session_factory
from app.models.workflow_task import TaskKind

logger = logging.getLogger(__name__)

# ── Konfiguration (überschreibbar via Environment-Variablen) ─────────────────
POLL_INTERVAL      = float(os.getenv("WORKER_POLL_INTERVAL",    "2.0"))   # s zwischen Polls wenn Queue leer
POLL_NO_AI         = float(os.getenv("WORKER_POLL_NO_AI",       "30.0"))  # s warten wenn keine KI verfügbar
LOCK_TIMEOUT       = timedelta(minutes=int(os.getenv("WORKER_LOCK_TIMEOUT_MIN", "10")))
AI_MAX_FAILURES    = int(os.getenv("WORKER_AI_MAX_FAILURES",    "3"))
AI_DISABLE_MINUTES = int(os.getenv("WORKER_AI_DISABLE_MIN",     "10"))
AI_CACHE_TTL       = float(os.getenv("WORKER_AI_CACHE_TTL",     "30.0"))  # s bis AI-Liste erneut aus DB geladen wird
WORKER_HOST        = socket.gethostname()

# ── In-Memory-Zustand ─────────────────────────────────────────────────────────

# Aufeinanderfolgende Verbindungsfehler pro KI (wird bei Erfolg auf 0 gesetzt)
_ai_failure_counts: dict[int, int] = {}

# Aktuelle Anzahl laufender Requests pro KI-Config-ID.
# Wird ATOMAR aktualisiert: zwischen dem Check (_ai_slot_usage[id] < limit)
# und dem Increment gibt es kein await → in asyncio's kooperativem Modell
# kann keine andere Coroutine dazwischenfunken.
_ai_slot_usage: dict[int, int] = {}

# Cache der aktiven KI-Konfigurationen (Format: list[dict mit id/name/parallel_request])
_ai_config_cache: list[dict] = []
_ai_config_cache_ts: float = 0.0   # monotonic-Zeitstempel der letzten DB-Abfrage
_ai_config_cache_lock: asyncio.Lock | None = None  # wird in WorkerPool.start() initialisiert


# ── Synchrone Hilfsfunktionen (laufen in Threads via asyncio.to_thread) ──────

def _load_active_configs_sync() -> list[dict]:
    """
    Lädt alle wirklich verfügbaren KI-Konfigurationen aus der DB:
    active=True UND timeout_at nicht in der Zukunft.

    Gibt eine Liste von Dicts zurück (id, name, parallel_request).
    Keine SQLAlchemy-Objekte — die sind nicht thread-sicher über await-Grenzen.
    """
    from app import crud
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        configs = crud.ai_config.get_active_list(db)
        return [
            {
                "id":               c.id,
                "name":             c.name,
                "parallel_request": max(1, c.parallel_request),
            }
            for c in configs
        ]
    except Exception as exc:
        logger.error("Fehler beim Laden aktiver KI-Konfigurationen: %s", exc)
        return []
    finally:
        db.close()


def _load_worker_capacity_sync() -> int:
    """Berechnet die Gesamt-Worker-Anzahl beim Start (Summe der parallel_request)."""
    from app import crud
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return max(1, crud.ai_config.get_worker_capacity(db))
    except Exception as exc:
        logger.error("Fehler beim Berechnen der Worker-Kapazität: %s", exc)
        return 1
    finally:
        db.close()


def _prepare_document(document_id: int, ai_config_id: int) -> tuple[str, str | None]:
    """
    Prüft das Dokument, lädt den Systemprompt und setzt Status → 'processing'.

    Die KI-Konfiguration wurde bereits vom Worker ausgewählt (_pick_ai_slot).
    Diese Funktion prüft nur noch ob die KI noch aktiv ist (könnte sich seit
    der Cache-Abfrage geändert haben) und ob das Dokument verarbeitbar ist.

    Args:
        document_id:  ID des zu verarbeitenden Dokuments.
        ai_config_id: Vom Worker gewählte KI-Konfiguration.

    Rückgabe: (status_code, system_prompt_text)
      "ok"     — alles bereit, Dokument auf 'processing' gesetzt
      "no_ai"  — KI-Konfiguration inzwischen nicht mehr verfügbar
      "skip"   — Dokument nicht verarbeitbar (gelöscht, nicht gefunden, ...)
    """
    from app import crud
    from app.database import SessionLocal
    from app.models.document import Document as DocModel

    db = SessionLocal()
    try:
        doc = db.get(DocModel, document_id)
        if doc is None:
            logger.error("[worker] Dokument #%d nicht gefunden", document_id)
            return "skip", None
        if not doc.stored_filename:
            logger.error("[worker] Dokument #%d hat keine gespeicherte Datei", document_id)
            return "skip", None
        if doc.soft_deleted:
            logger.warning("[worker] Dokument #%d ist gelöscht — übersprungen", document_id)
            return "skip", None
        if doc.status == "processing":
            logger.warning(
                "[worker] Dokument #%d wird bereits verarbeitet — übersprungen", document_id
            )
            return "skip", None

        # KI-Config nochmals laden (könnte seit Cache-Refresh deaktiviert worden sein)
        ai_config = crud.ai_config.get_by_id(db, ai_config_id)
        if ai_config is None or not ai_config.active:
            logger.warning(
                "[worker] KI #%d nicht mehr aktiv für Dokument #%d",
                ai_config_id, document_id,
            )
            return "no_ai", None

        sp = crud.system_prompt.get_default(db)
        system_prompt_text: str | None = sp.content if sp else None

        crud.document.update_status(db, document_id, "processing")

        logger.info(
            "[worker] Dokument #%d → KI #%d '%s'",
            document_id, ai_config.id, ai_config.name,
        )
        return "ok", system_prompt_text

    except Exception as exc:
        logger.exception(
            "[worker] Fehler beim Vorbereiten von Dokument #%d: %s", document_id, exc
        )
        try:
            db.rollback()
        except Exception:
            pass
        return "skip", None
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


def _set_document_error_if_not_done(document_id: int, message: str) -> None:
    """
    Setzt Dokument-Status auf 'error' — aber nur wenn er nicht bereits 'done' ist.

    Verhindert, dass ein erfolgreicher Abschluss (doc='done') durch einen
    nachgelagerten Bookkeeping-Fehler (z.B. COMPLETE_SQL) überschrieben wird,
    was weitere Retry-Zyklen und doppelte KI-Aufrufe auslösen würde.
    """
    from app.database import SessionLocal
    from app.models.document import Document as DocModel
    db = SessionLocal()
    try:
        doc = db.get(DocModel, document_id)
        if doc is None or doc.status == "done":
            if doc and doc.status == "done":
                logger.info(
                    "[worker] Dokument #%d ist bereits 'done' — Fehlerstatus nicht gesetzt.",
                    document_id,
                )
            return
        from app import crud
        crud.document.update_status(db, document_id, "error")
        logger.error("[worker] Dokument #%d → error: %s", document_id, message)
    except Exception as exc:
        logger.error(
            "[worker] Konnte Fehlerstatus für Dokument #%d nicht setzen: %s", document_id, exc
        )
    finally:
        db.close()


# ── KI-Slot-Vergabe (atomar in asyncio) ──────────────────────────────────────

async def _refresh_ai_cache_if_needed() -> list[dict]:
    """
    Gibt die gecachte Liste aktiver KI-Konfigurationen zurück.
    Lädt sie aus der DB wenn der Cache abgelaufen ist (AI_CACHE_TTL Sekunden).

    Das asyncio.Lock stellt sicher, dass bei gleichzeitigen Cache-Misses nur
    ein Worker die DB abfragt (alle anderen warten und bekommen dann den frischen
    Cache zurück).
    """
    global _ai_config_cache, _ai_config_cache_ts

    now = time.monotonic()
    if now - _ai_config_cache_ts < AI_CACHE_TTL and _ai_config_cache:
        return _ai_config_cache   # Cache noch frisch

    # Cache abgelaufen — Lock holen und DB abfragen
    async with _ai_config_cache_lock:
        # Nochmals prüfen: ein anderer Worker könnte den Cache bereits erneuert haben
        now = time.monotonic()
        if now - _ai_config_cache_ts < AI_CACHE_TTL and _ai_config_cache:
            return _ai_config_cache

        configs = await asyncio.to_thread(_load_active_configs_sync)
        _ai_config_cache = configs
        _ai_config_cache_ts = time.monotonic()
        if configs:
            logger.debug(
                "AI-Cache aktualisiert: %d aktive KI(s): %s",
                len(configs),
                ", ".join(f"#{c['id']} {c['name']}({c['parallel_request']})" for c in configs),
            )
        return configs


def _pick_ai_slot(configs: list[dict]) -> int | None:
    """
    Wählt atomar eine KI mit freiem Slot und erhöht deren Nutzungszähler.

    "Atomar" weil diese Funktion kein await enthält — in asyncio's kooperativem
    Modell kann zwischen dem Check und dem Increment keine andere Coroutine laufen.

    Wahl-Strategie: KI mit dem größten Verhältnis freier/gesamt Slots (least-loaded).
    Bei Gleichstand: kleinste KI-ID (deterministisch).

    Args:
        configs: Liste aktiver KI-Configs (id, name, parallel_request).

    Returns:
        ai_config_id der gewählten KI, oder None wenn alle KIs voll ausgelastet sind.
    """
    available = [
        c for c in configs
        if _ai_slot_usage.get(c["id"], 0) < c["parallel_request"]
    ]
    if not available:
        return None

    # Least-loaded: kleinster Auslastungsgrad (aktiv / limit)
    chosen = min(
        available,
        key=lambda c: (_ai_slot_usage.get(c["id"], 0) / c["parallel_request"], c["id"]),
    )
    _ai_slot_usage[chosen["id"]] = _ai_slot_usage.get(chosen["id"], 0) + 1
    return chosen["id"]


def _release_ai_slot(ai_config_id: int) -> None:
    """
    Gibt einen Slot der KI frei (nach Abschluss oder Fehler des Tasks).
    Wird immer im finally-Block aufgerufen, damit kein Slot verloren geht.
    """
    current = _ai_slot_usage.get(ai_config_id, 0)
    _ai_slot_usage[ai_config_id] = max(0, current - 1)


# ── Task-Handler ─────────────────────────────────────────────────────────────

async def handle_process_document(
    payload: dict,
    _session: AsyncSession,
    ai_config_id: int,
) -> tuple[dict, str, int | None]:
    """
    Verarbeitet ein Dokument über die KI-Pipeline.

    Args:
        payload:       Task-Payload (muss document_id enthalten).
        _session:      Async-Session (nicht direkt genutzt, für einheitliche Signatur).
        ai_config_id:  Vom Worker gewählte KI-Konfiguration.

    Returns: (result_dict, result_code, ai_config_id_used)
      result_code:
        "ok"             — fertig (Dokument done oder error — beides ist ein Abschluss)
        "ai_unavailable" — KI nicht erreichbar → re-queuen, KI-Fehler zählen
        "no_ai"          — KI inzwischen deaktiviert → re-queuen, warten
        "skip"           — Dokument nicht verarbeitbar → Task als completed markieren
    """
    document_id = payload.get("document_id")
    if not isinstance(document_id, int):
        raise ValueError(f"Ungültige document_id im Payload: {payload!r}")

    logger.info("[worker] Starte Verarbeitung Dokument #%d (KI #%d)", document_id, ai_config_id)

    prep_code, system_prompt_text = await asyncio.to_thread(
        _prepare_document, document_id, ai_config_id
    )

    if prep_code == "skip":
        return {"document_id": document_id, "skipped": True}, "skip", None
    if prep_code == "no_ai":
        return {"document_id": document_id}, "no_ai", None

    from app.routers.documents import _analyze_single
    result_code = await _analyze_single(document_id, ai_config_id, system_prompt_text)

    logger.info(
        "[worker] Dokument #%d abgeschlossen (code=%s, ki=#%d)",
        document_id, result_code, ai_config_id,
    )
    return {"document_id": document_id, "status": "done"}, result_code or "ok", ai_config_id


# ── SQL-Statements ────────────────────────────────────────────────────────────

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

# result als JSON-String (json.dumps): asyncpg serialisiert Python-dicts bei
# text()-Queries nicht immer automatisch zu JSONB → explizit serialisieren.
COMPLETE_SQL = text("""
    UPDATE workflow_tasks
    SET status = 'completed', result = :result, error = NULL, updated_at = now()
    WHERE id = :id
""")

FAIL_SQL = text("""
    UPDATE workflow_tasks
    SET status     = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'pending' END,
        error      = :error,
        worker_id  = NULL,
        locked_at  = NULL,
        updated_at = now()
    WHERE id = :id
""")

# REQUEUE: re-queuen OHNE attempt zu verbrauchen (CLAIM_SQL-Inkrement rückgängig)
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

CLEANUP_SQL = text("""
    UPDATE workflow_tasks
    SET status = 'pending', worker_id = NULL, locked_at = NULL, updated_at = now()
    WHERE status = 'in_progress'
      AND locked_at < now() - make_interval(secs => :timeout_secs)
""")


# ── Worker-Coroutine ──────────────────────────────────────────────────────────

async def worker_loop(worker_name: str, shutdown: asyncio.Event) -> None:
    """
    Haupt-Loop eines einzelnen Workers.

    Ablauf pro Iteration:
      1. Aktive KI-Konfigurationen laden (gecacht, max. alle AI_CACHE_TTL s aus DB)
      2. Freien KI-Slot atomar reservieren (_pick_ai_slot)
         → kein Slot frei: kurz warten, nächste Iteration
      3. Task aus Queue holen (CLAIM_SQL mit FOR UPDATE SKIP LOCKED)
         → kein Task: Slot freigeben, kurz warten
      4. Task verarbeiten (handle_process_document mit reservierter KI)
      5. Slot freigeben (immer im finally-Block)
      6. Task als completed/failed/requeued markieren

    Der Slot-Reserve-vor-Claim-Ansatz stellt sicher, dass nie zwei Worker
    dieselbe KI auf einmal über ihr parallel_request-Limit hinaus belasten.
    """
    logger.info("[%s] gestartet", worker_name)

    while not shutdown.is_set():

        # ── Schritt 1: Aktive KIs laden (aus Cache oder DB) ──────────────────
        configs = await _refresh_ai_cache_if_needed()

        if not configs:
            logger.debug("[%s] Keine aktiven KIs — warte %.0fs", worker_name, POLL_NO_AI)
            await _sleep_or_shutdown(shutdown, POLL_NO_AI)
            continue

        # ── Schritt 2: KI-Slot reservieren (atomar, kein await) ─────────────
        ai_config_id = _pick_ai_slot(configs)
        if ai_config_id is None:
            # Alle KI-Slots belegt — kurz warten ohne Task zu claimen
            await _sleep_or_shutdown(shutdown, POLL_INTERVAL)
            continue

        # Ab hier MUSS _release_ai_slot(ai_config_id) aufgerufen werden
        task_id = None
        try:
            # ── Schritt 3: Task aus Queue holen ─────────────────────────────
            row = None
            async with async_session_factory() as session:
                try:
                    row = (await session.execute(CLAIM_SQL, {"worker_id": worker_name})).first()
                    await session.commit()
                except Exception:
                    logger.exception("[%s] DB-Fehler beim Claim", worker_name)
                    await session.rollback()
                    # Slot freigeben und kurz warten
                    _release_ai_slot(ai_config_id)
                    await _sleep_or_shutdown(shutdown, POLL_INTERVAL)
                    continue

            if row is None:
                # Queue leer — Slot freigeben, warten
                _release_ai_slot(ai_config_id)
                await _sleep_or_shutdown(shutdown, POLL_INTERVAL)
                continue

            task_id, payload, attempts, max_attempts = row
            kind = payload.get("kind")

            # ── Schritt 4: Task verarbeiten ──────────────────────────────────
            try:
                if kind != TaskKind.PROCESS_DOCUMENT:
                    raise ValueError(f"Unbekannter Task-Typ: {kind!r}")

                logger.info(
                    "[%s] Task #%d (%s, Versuch %d/%d, KI #%d)",
                    worker_name, task_id, kind, attempts, max_attempts, ai_config_id,
                )

                async with async_session_factory() as session:
                    result_data, result_code, ai_used = await handle_process_document(
                        payload, session, ai_config_id
                    )

                # ── Schritt 5: Ergebnis buchen ───────────────────────────────
                if result_code == "ai_unavailable":
                    await _handle_ai_failure(worker_name, task_id, payload, ai_used)

                elif result_code == "no_ai":
                    async with async_session_factory() as s:
                        await s.execute(
                            REQUEUE_SQL,
                            {"id": task_id, "error": f"KI #{ai_config_id} nicht verfügbar"},
                        )
                        await s.commit()
                    # Cache sofort invalidieren damit nächste Iteration frische Daten hat
                    _ai_config_cache_ts = 0.0
                    await _sleep_or_shutdown(shutdown, POLL_NO_AI)

                else:
                    # "ok" oder "skip" → abgeschlossen
                    if result_code == "ok" and ai_used is not None:
                        _ai_failure_counts[ai_used] = 0

                    async with async_session_factory() as s:
                        await s.execute(
                            COMPLETE_SQL,
                            {"id": task_id, "result": json.dumps(result_data)},
                        )
                        await s.commit()

            except Exception as exc:
                logger.exception("[%s] Task #%d: unbehandelter Fehler", worker_name, task_id)
                doc_id = payload.get("document_id")
                if isinstance(doc_id, int):
                    await asyncio.to_thread(
                        _set_document_error_if_not_done,
                        doc_id, f"Task #{task_id} fehlgeschlagen: {exc}",
                    )
                async with async_session_factory() as s:
                    await s.execute(FAIL_SQL, {"id": task_id, "error": str(exc)[:5000]})
                    await s.commit()

        finally:
            # Slot IMMER freigeben — auch bei Exceptions
            if ai_config_id is not None:
                _release_ai_slot(ai_config_id)

    logger.info("[%s] beendet", worker_name)


async def _handle_ai_failure(
    worker_name: str,
    task_id: int,
    payload: dict,
    ai_config_id: int | None,
) -> None:
    """
    Behandelt KI-Verbindungsfehler: Fehlerzähler erhöhen, bei Limit KI sperren,
    Task re-queuen (ohne attempt zu verbrauchen).
    """
    error_msg = "KI nicht erreichbar"

    if ai_config_id is not None:
        prev = _ai_failure_counts.get(ai_config_id, 0)
        new  = prev + 1
        _ai_failure_counts[ai_config_id] = new
        error_msg = f"KI #{ai_config_id} nicht erreichbar (Fehler {new}/{AI_MAX_FAILURES})"

        if new >= AI_MAX_FAILURES:
            logger.error(
                "[%s] KI #%d: %d Fehler in Folge → temporär gesperrt (%d min)",
                worker_name, ai_config_id, new, AI_DISABLE_MINUTES,
            )
            await asyncio.to_thread(_temporarily_disable_ai, ai_config_id)
            _ai_failure_counts[ai_config_id] = 0
            # Cache invalidieren damit gesperrte KI sofort aus der Auswahl fällt
            global _ai_config_cache_ts
            _ai_config_cache_ts = 0.0
        else:
            logger.warning(
                "[%s] KI #%d: Verbindungsfehler %d/%d",
                worker_name, ai_config_id, new, AI_MAX_FAILURES,
            )

    async with async_session_factory() as s:
        await s.execute(REQUEUE_SQL, {"id": task_id, "error": error_msg})
        await s.commit()

    logger.info("[%s] Task #%d re-queued (%s)", worker_name, task_id, error_msg)


async def cleanup_loop(shutdown: asyncio.Event) -> None:
    """Gibt hängende 'in_progress'-Tasks nach LOCK_TIMEOUT zurück in die Queue."""
    while not shutdown.is_set():
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    CLEANUP_SQL,
                    {"timeout_secs": LOCK_TIMEOUT.total_seconds()},
                )
                await session.commit()
                if result.rowcount:
                    logger.warning("Cleanup: %d hängende Tasks zurückgesetzt", result.rowcount)
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
    """
    Verwaltet den Lebenszyklus des Worker-Pools.

    Beim Start werden worker_count generische Worker gestartet (Anzahl =
    Summe aller parallel_request aktiver KIs). Die KI-Auswahl erfolgt
    dynamisch pro Task — neue KIs werden ohne Neustart erkannt (Cache-TTL: 30s).

    Für zusätzliche Worker-Slots nach dem Hinzufügen neuer KIs ist ein
    einmaliger Container-Neustart nötig.
    """

    def __init__(self) -> None:
        self.shutdown     = asyncio.Event()
        self.tasks:        list[asyncio.Task] = []
        self.worker_count: int = 0

    async def start(self) -> None:
        global _ai_config_cache_lock
        # Lock für Cache-Refresh muss im laufenden Event-Loop erstellt werden
        _ai_config_cache_lock = asyncio.Lock()

        self.worker_count = await asyncio.to_thread(_load_worker_capacity_sync)

        logger.info(
            "Starte Worker-Pool: host=%s worker=%d (KI-Auswahl dynamisch)",
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
        logger.info("Stoppe Worker-Pool...")
        self.shutdown.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Worker-Pool beendet")
