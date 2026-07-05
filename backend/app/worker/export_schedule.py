"""
Automatischer wöchentlicher Excel-Export: periodischer Hintergrund-Task im Worker-Container.

Prüft alle 15 Minuten, ob der konfigurierte Wochentermin (automation_settings.export_weekday/
export_hour/export_minute) erreicht ist. Für jeden Import-Batch mit auto_export=True wird dann
— falls seit dem letzten Export (last_exported_at, gemeinsamer Zähler mit dem manuellen
"/export/new"-Endpunkt) noch nichts für dieses Wochenfenster exportiert wurde — eine
inkrementelle Excel-Datei erzeugt und nach EXPORT_BASE_PATH/{Firma}_{Jahr}/ geschrieben.

Wochentermin wird in der konfigurierten Zeitzone interpretiert (settings.timezone, nicht
Container-UTC) — "Montag 06:00" bedeutet 06:00 Uhr Ortszeit gemäß TIMEZONE-Einstellung.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta

from app.database import SessionLocal

log = logging.getLogger("export_schedule")

# Prüfintervall des Loops — unabhängig vom eigentlichen Wochentermin, nur wie oft
# nachgesehen wird, ob der Termin inzwischen erreicht ist.
_CHECK_INTERVAL_SECONDS = 15 * 60


def _get_export_schedule() -> dict:
    """Liest den aktuell konfigurierten Wochentermin aus automation_settings."""
    from app import crud

    db = SessionLocal()
    try:
        s = crud.automation_settings.get_or_create(db)
        return {
            "export_weekday": s.export_weekday,
            "export_hour": s.export_hour,
            "export_minute": s.export_minute,
        }
    finally:
        db.close()


def _db_load_auto_export_batches() -> list[dict]:
    """Lädt alle Import-Batches mit auto_export=True, die nicht mehr in Bearbeitung sind."""
    from app.models.import_batch import ImportBatch

    db = SessionLocal()
    try:
        batches = (
            db.query(ImportBatch)
            .filter(ImportBatch.auto_export.is_(True), ImportBatch.status == "done")
            .all()
        )
        return [{"id": b.id, "last_exported_at": b.last_exported_at} for b in batches]
    finally:
        db.close()


def _current_week_slot_start(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    """
    Berechnet den Beginn des aktuellen Wochenfensters für den konfigurierten Termin.

    Beispiel: weekday=0 (Montag), hour=6 → liefert das Datum/Uhrzeit des letzten
    (ggf. heutigen) Montags 06:00, das nicht in der Zukunft liegt.
    """
    days_since = (now.weekday() - weekday) % 7
    slot_date = (now - timedelta(days=days_since)).date()
    slot = datetime.combine(slot_date, time(hour=hour, minute=minute), tzinfo=now.tzinfo)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def _export_one_batch(batch_id: int) -> None:
    """
    Erzeugt die inkrementelle Excel-Datei für einen Batch und schreibt sie nach
    EXPORT_BASE_PATH/{Firma}_{Jahr}/. Aktualisiert last_exported_at danach immer —
    auch wenn keine neuen Dokumente gefunden wurden (keine Datei, aber der Zähler
    rückt trotzdem vor, damit dieselbe leere Prüfung nicht jede Woche wiederholt wird).
    """
    from pathlib import Path

    from app.config import settings as app_settings
    from app.models.import_batch import ImportBatch
    from app.routers.imports import (
        _build_export_excel,
        _get_export_config_fields,
        _query_batch_documents,
    )

    db = SessionLocal()
    try:
        batch = db.get(ImportBatch, batch_id)
        if batch is None:
            return

        docs = _query_batch_documents(db, batch.id, since=batch.last_exported_at)

        if docs:
            invoice_fields, position_fields = _get_export_config_fields(db)
            excel_bytes = _build_export_excel(batch, docs, invoice_fields, position_fields)

            from zoneinfo import ZoneInfo

            safe = f"{batch.company_name}_{batch.year}".replace(" ", "_")
            export_dir = Path(app_settings.export_base_path) / safe
            export_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(ZoneInfo(app_settings.timezone)).strftime("%Y%m%d_%H%M")
            file_path = export_dir / f"Export_{timestamp}.xlsx"
            file_path.write_bytes(excel_bytes)
            log.info(
                "Batch #%d: automatischer Export geschrieben (%s, %d Dokument(e))",
                batch.id, file_path, len(docs),
            )
        else:
            log.info("Batch #%d: automatischer Export übersprungen (keine neuen Dokumente)", batch.id)

        from datetime import timezone
        batch.last_exported_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


async def _check_and_export_all() -> None:
    from zoneinfo import ZoneInfo

    from app.config import settings as app_settings

    schedule = await asyncio.to_thread(_get_export_schedule)
    now_local = datetime.now(ZoneInfo(app_settings.timezone))  # aware, konfigurierte lokale Zeitzone
    slot_start = _current_week_slot_start(
        now_local, schedule["export_weekday"], schedule["export_hour"], schedule["export_minute"]
    )

    batches = await asyncio.to_thread(_db_load_auto_export_batches)
    for batch in batches:
        last_exported_at = batch["last_exported_at"]
        if last_exported_at is not None and last_exported_at >= slot_start:
            continue  # für dieses Wochenfenster schon exportiert
        await asyncio.to_thread(_export_one_batch, batch["id"])


async def run(stop_event: asyncio.Event) -> None:
    """
    Haupt-Loop: prüft alle 15 Minuten, ob der wöchentliche Export-Termin erreicht ist.

    stop_event ermöglicht sofortiges, sauberes Beenden statt das volle Intervall
    abzuwarten (siehe app/worker/main.py::lifespan).
    """
    log.info("Export-Zeitplan-Loop gestartet")
    while not stop_event.is_set():
        try:
            await _check_and_export_all()
        except Exception:
            log.exception("Fehler im Export-Zeitplan-Loop")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    log.info("Export-Zeitplan-Loop gestoppt")
