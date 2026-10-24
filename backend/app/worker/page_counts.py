"""Nachholschleife: prüft Belege ohne PDF-Zustand (Ordner-Sync, Neustart während eines Imports, Altbestand) im Hintergrund."""

from __future__ import annotations

import asyncio
import logging

from app.services import page_count_service

log = logging.getLogger("page_counts")

INTERVAL_SECONDS = 20


async def run(stop_event: asyncio.Event) -> None:
    log.info("PDF-Prüfung (Seitenanzahl/Verschlüsselung) gestartet, Intervall %ds", INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            while await page_count_service.scan_once(None) >= page_count_service.SCAN_LIMIT and not stop_event.is_set():
                pass                                           # weiter, solange der Block voll war
        except Exception as exc:  # noqa: BLE001
            log.error("PDF-Prüfung fehlgeschlagen: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
