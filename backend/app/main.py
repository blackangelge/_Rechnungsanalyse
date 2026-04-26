"""
FastAPI-Anwendungsinstanz und Router-Registrierung.

Alle API-Endpunkte werden hier zentral registriert.
Die CORS-Middleware erlaubt Anfragen vom Next.js-Frontend.
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import ai_clients

#WorkerPool importieren, damit er beim Starten des Containers automatisch startet
from app.worker.runner import WorkerPool

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
    Führt ausstehende Alembic-Migrationen automatisch beim Start aus.
    Verhindert 500-Fehler durch fehlende Datenbankspalten nach Code-Updates.
    """
    try:
        from alembic import command as alembic_command
        from alembic.config import Config as AlembicConfig

        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        if not alembic_ini.exists():
            logger.warning("alembic.ini nicht gefunden unter %s — Migration übersprungen", alembic_ini)
            return

        cfg = AlembicConfig(str(alembic_ini))
        alembic_command.upgrade(cfg, "head")
        logger.info("✓ Datenbank-Migrationen erfolgreich angewendet")
    except Exception as exc:
        logger.error("✗ Fehler beim Ausführen der Datenbank-Migrationen: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  Rechnungsanalyse Backend wird gestartet")
    logger.info("=" * 60)
    _run_migrations()
    # Worker-Pool starten (führt Tasks im Hintergrund aus)
    pool = WorkerPool()
    await pool.start()
    app.state.worker_pool = pool
    logger.info("Backend bereit, Worker-Pool läuft im Hintergrund")
    try:
        yield
    finally:
        await pool.stop()
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
from app.routers import ai_clients, batch, documents, logs, settings, sse, vendors
from app.routers import imports as imports_router  # 'imports' ist ein Python-Keyword

app.include_router(ai_clients.router)                  # /api/ai-clients/*
app.include_router(imports_router.router)              # /api/imports/*
app.include_router(sse.router)                         # /api/imports/{id}/progress (SSE)
app.include_router(documents.router)                   # /api/documents/*
app.include_router(settings.router)                    # /api/settings/*
app.include_router(settings.doc_types_router)          # /api/document-types
app.include_router(logs.router)                        # /api/logs/*
app.include_router(vendors.router)                     # /api/vendors/*
app.include_router(batch.router)                       # /api/batch/*


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
