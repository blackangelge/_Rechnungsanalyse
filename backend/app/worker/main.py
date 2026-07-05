"""
FastAPI-Anwendungsinstanz des Worker-Containers.

Läuft als eigener Container (siehe docker-compose.yml, Service "worker") und
übernimmt die KI-Task-Verarbeitung, die früher im Backend-Prozess lief
(app.worker.worker.Dispatcher). Kommuniziert mit dem Backend ausschließlich
über die gemeinsame PostgreSQL-Datenbank (workflow_tasks, ai_clients) sowie
über die Status-/Steuerungs-Endpunkte in app.worker.routers.status, die das
Backend intern abfragt (http://worker:8000/...).

Führt KEINE Alembic-Migrationen aus — das bleibt Aufgabe des Backend-Containers.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from app.config import settings
from app.db_wait import wait_for_db
from app.services.ai_dispatcher_client import AIDispatcherClient
from app.worker import export_schedule, folder_sync
from app.worker.worker import Dispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Registriert JSONB/JSON-Codecs — asyncpg gibt sie sonst als rohen String zurück."""
    await conn.set_type_codec(
        "jsonb", schema="pg_catalog",
        encoder=json.dumps, decoder=json.loads,
    )
    await conn.set_type_codec(
        "json", schema="pg_catalog",
        encoder=json.dumps, decoder=json.loads,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("  Rechnungsanalyse Worker wird gestartet")
    logger.info("=" * 60)

    # Gleiche Thread-Pool-Vergrößerung wie im Backend: _analyze_single/_db_set_processing
    # laufen über asyncio.to_thread und würden sonst den kleinen Default-Pool erschöpfen.
    _thread_pool_size = max(20, (os.cpu_count() or 2) * 4)
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=_thread_pool_size,
            thread_name_prefix="asyncio",
        )
    )
    logger.info("✓ Thread-Pool: %d Threads (cpu_count=%s)", _thread_pool_size, os.cpu_count())

    await asyncio.to_thread(wait_for_db, settings.database_url)

    db_pool = await asyncpg.create_pool(
        settings.database_url, min_size=2, max_size=10, init=_init_conn,
    )
    logger.info("✓ DB-Pool bereit")

    ai_client = AIDispatcherClient()
    dispatcher = Dispatcher(db_pool=db_pool, ai_client=ai_client, poll_interval=20, max_workers_cap=2)
    dispatcher_task = asyncio.create_task(dispatcher.run())
    app.state.dispatcher = dispatcher

    # Ordner-Sync + Export-Zeitplan: eigene Hintergrund-Loops, unabhängig vom
    # Dispatcher (siehe app/worker/folder_sync.py, app/worker/export_schedule.py).
    # Eigene stop_events statt Task-Cancel für sauberes, sofortiges Beenden ohne
    # das volle Poll-Intervall abzuwarten.
    folder_sync_stop = asyncio.Event()
    folder_sync_task = asyncio.create_task(folder_sync.run(folder_sync_stop))

    export_schedule_stop = asyncio.Event()
    export_schedule_task = asyncio.create_task(export_schedule.run(export_schedule_stop))

    logger.info("✓ Dispatcher läuft (Polling alle 20s, max 2 parallele Worker)")
    logger.info("✓ Ordner-Sync-Loop gestartet")
    logger.info("✓ Export-Zeitplan-Loop gestartet")
    logger.info("✓ Worker bereit")
    try:
        yield
    finally:
        await dispatcher.shutdown()
        try:
            await asyncio.wait_for(dispatcher_task, timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("Dispatcher-Task antwortet nicht — wird abgebrochen")
            dispatcher_task.cancel()

        folder_sync_stop.set()
        try:
            await asyncio.wait_for(folder_sync_task, timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("Ordner-Sync-Task antwortet nicht — wird abgebrochen")
            folder_sync_task.cancel()

        export_schedule_stop.set()
        try:
            await asyncio.wait_for(export_schedule_task, timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("Export-Zeitplan-Task antwortet nicht — wird abgebrochen")
            export_schedule_task.cancel()

        await db_pool.close()

    logger.info("=" * 60)
    logger.info("  Rechnungsanalyse Worker wird beendet")
    logger.info("=" * 60)


app = FastAPI(
    title="Rechnungsanalyse Worker",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    redirect_slashes=False,
    lifespan=lifespan,
)

from app.worker.routers import status  # noqa: E402

app.include_router(status.router)
