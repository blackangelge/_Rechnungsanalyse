"""
FastAPI-Anwendungsinstanz und Router-Registrierung.

Alle API-Endpunkte werden hier zentral registriert.
Die CORS-Middleware erlaubt Anfragen vom Next.js-Frontend.
"""

import asyncio
import concurrent.futures
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.db_wait import wait_for_db
from app.routers import ai_clients

# Der KI-Dispatcher (app.worker.worker.Dispatcher) läuft seit der Container-Trennung
# im eigenständigen Worker-Container (app/worker/main.py) — nicht mehr hier.

# ── Logging konfigurieren ────────────────────────────────────────────────────
# Einheitliches Format für Container Manager Protokoll-Ansicht
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# SQLAlchemy und httpx-Rauschen reduzieren
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ── Startup / Shutdown ───────────────────────────────────────────────────────

def _run_migrations() -> None:
    """
    Wartet auf PostgreSQL und führt dann ausstehende Alembic-Migrationen aus.
    Läuft in einem Thread (via asyncio.to_thread) um den Event-Loop nicht zu blockieren.

    Optimierung: Prüft zuerst per direktem SQL ob die DB bereits auf dem neuesten
    Stand ist. Falls ja, wird die Migration übersprungen — kein Alembic-Lock nötig.
    Nur bei tatsächlich ausstehenden Migrationen wird alembic upgrade aufgerufen.
    """
    import psycopg2

    # Auf PostgreSQL warten — bei frischer DB dauert initdb 10–30 s
    if not wait_for_db(settings.database_url):
        logger.error("✗ Datenbank nach 60 s nicht erreichbar — Migration übersprungen")
        return

    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        if not alembic_ini.exists():
            logger.warning("alembic.ini nicht gefunden unter %s — Migration übersprungen", alembic_ini)
            return

        cfg = AlembicConfig(str(alembic_ini))
        script_dir = ScriptDirectory.from_config(cfg)
        head_rev = script_dir.get_current_head()

        # Schnell-Check: aktuelle DB-Version per direktem SQL lesen (kein Alembic-Lock).
        # statement_timeout=3s: Falls alembic_version-Tabelle selbst gesperrt ist,
        # schlägt der Check fehl → wir versuchen die volle Migration mit Timeouts.
        current_rev: str | None = None
        try:
            check_conn = psycopg2.connect(
                settings.database_url,
                connect_timeout=5,
                options="-c statement_timeout=3000",  # 3s in ms
            )
            try:
                with check_conn.cursor() as cur:
                    cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                    row = cur.fetchone()
                    current_rev = row[0] if row else None
            finally:
                check_conn.close()
        except Exception as exc:
            logger.info("Version-Check übersprungen (%s) — Migration wird geprüft", exc)

        if current_rev == head_rev:
            logger.info("✓ Datenbank bereits aktuell (%s) — keine Migration nötig", current_rev)
            return

        logger.info("Migration ausstehend: %s → %s — starte Upgrade", current_rev, head_rev)

        # Volle Migration mit Lock- und Statement-Timeouts durchführen.
        from alembic import command as alembic_command
        from sqlalchemy import create_engine

        engine = create_engine(
            settings.database_url,
            connect_args={"options": "-c lock_timeout=15s -c statement_timeout=20s"},
        )
        alembic_conn = engine.connect()
        cfg.attributes["connection"] = alembic_conn
        try:
            alembic_command.upgrade(cfg, "head")
            logger.info("✓ Datenbank-Migrationen erfolgreich angewendet")
        finally:
            alembic_conn.close()
            engine.dispose()
    except Exception as exc:
        logger.error("✗ Fehler beim Ausführen der Datenbank-Migrationen: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  Rechnungsanalyse Backend wird gestartet")
    logger.info("=" * 60)

    # ── Thread-Pool explizit vergrößern ─────────────────────────────────────────
    # asyncio.to_thread() und FastAPIs sync-Router-Handler teilen sich denselben
    # Default-Executor. Auf NAS-Systemen mit wenigen CPUs ist der Default zu klein
    # (min(32, cpu_count+4) → meist 5-8 Threads). Wenn 2+ Worker gleichzeitig
    # 300s-lange KI-HTTP-Calls machen, bleiben keine Threads für FastAPI übrig
    # → Backend erscheint "nicht erreichbar".
    # Lösung: Default-Pool explizit auf 20 Threads setzen.
    _thread_pool_size = max(20, (os.cpu_count() or 2) * 4)
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=_thread_pool_size,
            thread_name_prefix="asyncio",
        )
    )
    logger.info("✓ Thread-Pool: %d Threads (cpu_count=%s)", _thread_pool_size, os.cpu_count())

    try:
        await asyncio.wait_for(asyncio.to_thread(_run_migrations), timeout=30.0)
    except asyncio.TimeoutError:
        logger.error("✗ Migration-Timeout nach 30 s — Backend startet trotzdem")
    logger.info("✓ Migration bereit")
    logger.info("✓ Backend bereit")
    yield
    logger.info("=" * 60)
    logger.info("  Rechnungsanalyse Backend wird beendet")
    logger.info("=" * 60)


# ── FastAPI-Instanz ─────────────────────────────────────────────────────────
app = FastAPI(
    title="Rechnungsanalyse API",
    version="0.3.0",
    docs_url="/docs",    # Swagger UI
    redoc_url="/redoc",  # ReDoc UI
    redirect_slashes=False,
    lifespan=lifespan,
)


# ── Request-Logging Middleware ───────────────────────────────────────────────
# Loggt alle Requests mit Statuscode und Dauer — sichtbar im Container Manager

_SKIP_LOG_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    path = request.url.path
    if path not in _SKIP_LOG_PATHS:
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        logger.log(
            level,
            "HTTP %d  %s %s  (%.0f ms)",
            response.status_code,
            request.method,
            path,
            duration_ms,
        )
    return response


# ── Globaler Fehler-Handler ──────────────────────────────────────────────────
# Fängt alle unbehandelten Exceptions ab, loggt sie vollständig und gibt eine
# strukturierte JSON-Antwort zurück statt des Standard-HTML-500.

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # HTTPException normal weiterleiten (FastAPI behandelt sie selbst)
    if isinstance(exc, HTTPException):
        raise exc

    logger.exception(
        "UNBEHANDELTE AUSNAHME  %s %s\n  %s: %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": str(request.url.path),
            "method": request.method,
        },
    )


# ── CORS-Middleware ─────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Router registrieren ─────────────────────────────────────────────────────
from app.routers import ai_clients, batch, documents, logs, sse, vendors
from app.routers import settings as settings_router
from app.routers import imports as imports_router  # 'imports' ist ein Python-Keyword
from app.routers import tasks
from app.routers import export_settings as export_settings_router

app.include_router(ai_clients.router)                  # /api/ai-clients/*
app.include_router(imports_router.router)              # /api/imports/*
app.include_router(sse.router)                         # /api/imports/{id}/progress (SSE)
app.include_router(documents.router)                   # /api/documents/*
app.include_router(settings_router.router)             # /api/settings/*
app.include_router(settings_router.doc_types_router)   # /api/document-types
app.include_router(logs.router)                        # /api/logs/*
app.include_router(vendors.router)                     # /api/vendors/*
app.include_router(batch.router)                       # /api/batch/*
app.include_router(tasks.router)                       # /api/tasks/*
app.include_router(export_settings_router.router)      # /api/settings/export


# ── Health-Check ────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    """Einfacher Health-Check-Endpunkt — prüft auch die DB-Verbindung."""
    from app.database import engine
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        logger.error("Health-Check: DB nicht erreichbar: %s", exc)
        db_status = f"error: {exc}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": "0.3.0",
        "database": db_status,
    }

