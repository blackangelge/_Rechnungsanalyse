"""
Ordner-Sync: periodischer Hintergrund-Task im Worker-Container.

Prüft in konfigurierbarem Intervall (automation_settings.folder_sync_interval_minutes,
Standard 15) alle Import-Batches mit folder_sync=True auf neue PDFs im jeweiligen
import_folder_path. Neu gefundene Dateien werden importiert (dieselbe Logik wie ein
normaler Import — app/services/import_service.py wird wiederverwendet) und automatisch
mit dem Standard-Systemprompt zur KI-Analyse eingereiht (kein pro-Batch-Override:
Ordner-Sync ist für unbeaufsichtigten Betrieb gedacht).

Läuft als eigener asyncio.create_task() neben dem Dispatcher (app/worker/worker.py),
mit eigenem Poll-Intervall und eigenem stop_event für sauberes Herunterfahren.
"""

import asyncio
import logging
from pathlib import Path

from app.database import SessionLocal

log = logging.getLogger("folder_sync")


def _db_load_sync_batches() -> list[dict]:
    """Lädt alle Import-Batches mit folder_sync=True, die nicht mehr in Bearbeitung sind."""
    from app.models.import_batch import ImportBatch

    db = SessionLocal()
    try:
        batches = (
            db.query(ImportBatch)
            .filter(ImportBatch.folder_sync.is_(True), ImportBatch.status == "done")
            .all()
        )
        return [
            {
                "id": b.id,
                "import_folder_path": b.import_folder_path,
                "storage_folder_path": b.storage_folder_path,
                "company_name": b.company_name,
                "year": b.year,
            }
            for b in batches
        ]
    finally:
        db.close()


def _db_mark_synced(batch_id: int) -> None:
    """Setzt last_synced_at auf jetzt, unabhängig davon ob neue Dateien gefunden wurden."""
    from datetime import datetime, timezone

    from app.models.import_batch import ImportBatch

    db = SessionLocal()
    try:
        batch = db.get(ImportBatch, batch_id)
        if batch is not None:
            batch.last_synced_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def _get_interval_minutes() -> int:
    """Liest das aktuelle Scan-Intervall aus automation_settings (frisch bei jedem Zyklus)."""
    from app import crud

    db = SessionLocal()
    try:
        return crud.automation_settings.get_or_create(db).folder_sync_interval_minutes
    finally:
        db.close()


async def _sync_one_batch(batch: dict) -> None:
    """Prüft einen einzelnen Batch auf neue PDFs, importiert + reiht sie zur KI-Analyse ein."""
    from app.routers.imports import _db_enqueue_for_analysis, _db_get_source_filenames
    from app.services.import_service import (
        _process_single_document,
        _run_import_io,
        list_pdf_files,
        validate_import_path,
    )

    batch_id = batch["id"]
    try:
        folder_path = await _run_import_io(validate_import_path, batch["import_folder_path"])
    except Exception as exc:
        log.warning("Batch #%d: Ordner-Sync übersprungen (%s)", batch_id, exc)
        return

    pdf_files = await _run_import_io(list_pdf_files, folder_path)
    if not pdf_files:
        return

    known_names = set(await asyncio.to_thread(_db_get_source_filenames, batch_id))
    new_files = [p for p in pdf_files if p.name not in known_names]
    if not new_files:
        return

    log.info("Batch #%d: %d neue Datei(en) via Ordner-Sync gefunden", batch_id, len(new_files))

    storage_dir = Path(batch["storage_folder_path"])
    # Keyword-Args über Lambda (siehe import_service.py::run_import für Begründung).
    await _run_import_io(lambda: storage_dir.mkdir(parents=True, exist_ok=True))

    # Sequenziell statt parallel: Ordner-Sync läuft nebenbei zur laufenden KI-Analyse,
    # soll keine zusätzliche Kopier-Last auf dem NAS erzeugen.
    new_doc_ids: list[int] = []
    for pdf_path in new_files:
        doc_id = await _process_single_document(batch_id, pdf_path, storage_dir)
        if doc_id is not None:
            new_doc_ids.append(doc_id)

    if new_doc_ids:
        enqueued = await asyncio.to_thread(_db_enqueue_for_analysis, new_doc_ids, None)
        log.info(
            "Batch #%d: %d/%d neu importierte(s) Dokument(e) zur KI-Analyse eingereiht",
            batch_id, enqueued, len(new_doc_ids),
        )


# Kurzer Poll-Takt, damit eine Intervalländerung zeitnah greift (siehe run()).
_CHECK_TICK_SECONDS = 30


async def run(stop_event: asyncio.Event) -> None:
    """
    Haupt-Loop: prüft alle folder_sync-Batches im konfigurierten Intervall.

    Pollt intern alle _CHECK_TICK_SECONDS (30s) und vergleicht die seit dem letzten
    Durchlauf verstrichene Zeit mit dem AKTUELL konfigurierten Intervall — dieses wird
    bei jedem Tick frisch aus automation_settings gelesen. Ändert der Nutzer das
    Intervall während der Loop wartet, greift die neue Einstellung dadurch innerhalb
    von ~30s, statt erst nachdem die mit dem alten (evtl. deutlich längeren) Intervall
    gestartete Wartezeit vollständig abgelaufen ist.

    stop_event ermöglicht sofortiges, sauberes Beenden statt das volle Intervall
    abzuwarten (siehe app/worker/main.py::lifespan).
    """
    log.info("Ordner-Sync-Loop gestartet")
    loop = asyncio.get_event_loop()
    last_run = 0.0  # 0 → erster Durchlauf sofort beim Start

    while not stop_event.is_set():
        interval_minutes = await asyncio.to_thread(_get_interval_minutes)
        now = loop.time()

        if now - last_run >= interval_minutes * 60:
            try:
                batches = await asyncio.to_thread(_db_load_sync_batches)
                for batch in batches:
                    await _sync_one_batch(batch)
                    await asyncio.to_thread(_db_mark_synced, batch["id"])
            except Exception:
                log.exception("Fehler im Ordner-Sync-Loop")
            last_run = loop.time()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_CHECK_TICK_SECONDS)
        except asyncio.TimeoutError:
            pass

    log.info("Ordner-Sync-Loop gestoppt")
