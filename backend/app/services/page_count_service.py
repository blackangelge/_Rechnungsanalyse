"""
Seitenanzahl und PDF-Zustand im Hintergrund ermitteln.

Der Import kopiert nur noch (schnell, ohne PDF zu öffnen). Dieser Dienst läuft parallel dazu als asynchrone Hintergrundaufgabe,
liest je Beleg Seitenanzahl und Zustand (ok · encrypted · unreadable) und trägt sie am Beleg ein (`documents.page_count`, `documents.pdf_state`).
Ein Beleg, der verschlüsselt oder nicht lesbar ist, gilt als Fehler zur Nachbearbeitung (`status=error`, `error_code`) — aber nur,
wenn er noch nicht analysiert wurde.

Zwei Auslöser: der Import selbst (`run_for_batch`, läuft bis der Import fertig ist und liefert die letzten Belege nach) und eine
Nachholschleife im Worker (`app/worker/page_counts.py`, deckt Ordner-Sync und Neustarts ab).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from pypdf import PdfReader

from app import crud
from app.database import SessionLocal
from app.models.document import Document
from app.models.import_batch import ImportBatch

logger = logging.getLogger(__name__)

CONCURRENCY = 4
SCAN_LIMIT = 400

MESSAGES = {
    "encrypted": "PDF ist passwortgeschützt/verschlüsselt — bitte ohne Passwortschutz neu speichern oder ersetzen",
    "unreadable": "PDF ist nicht lesbar (defekt oder kein gültiges PDF) — bitte ersetzen",
}


def inspect_pdf(path: str | os.PathLike) -> tuple[str, int]:
    """(Zustand, Seitenzahl). Eine Verschlüsselung ohne Benutzerpasswort (nur Bearbeitungsschutz) gilt als lesbar."""
    try:
        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001 — defekte Dateien melden, nicht abstürzen
        return "unreadable", 0
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return "encrypted", 0
        except Exception:  # noqa: BLE001 — z. B. fehlende Krypto-Bibliothek oder unbekanntes Verfahren
            return "encrypted", 0
    try:
        return "ok", len(reader.pages)
    except Exception:  # noqa: BLE001
        return "unreadable", 0


@dataclass
class _Job:
    doc_id: int
    path: str


def _select_pending(batch_id: int | None, limit: int) -> list[_Job]:
    db = SessionLocal()
    try:
        q = (
            db.query(Document).join(ImportBatch, ImportBatch.id == Document.batch_id)
            .filter(Document.stored_filename.isnot(None), Document.pdf_state.is_(None), Document.soft_deleted.is_(False))
        )
        if batch_id is not None:
            q = q.filter(Document.batch_id == batch_id)
        jobs = []
        for d in q.order_by(Document.id).limit(limit):
            jobs.append(_Job(d.id, os.path.join(d.batch.storage_folder_path, d.stored_filename)))
        return jobs
    finally:
        db.close()


def _write(results: list[tuple[int, str, int]]) -> None:
    db = SessionLocal()
    try:
        for doc_id, state, pages in results:
            doc = db.get(Document, doc_id)
            if doc is None:
                continue
            doc.pdf_state = state
            if state == "ok" and pages > 0:
                doc.page_count = pages
            # Nach dem Import steht ein Beleg auf „done“ (= kopiert). Noch nicht analysiert (keine Analyse-Version) und
            # nicht lesbar/verschlüsselt → Fehler zur Nachbearbeitung. Bereits analysierte Belege bleiben unberührt.
            if state != "ok" and doc.analyzed_pdf_version is None and doc.status in ("pending", "done"):
                doc.status, doc.error_code, doc.raw_response = "error", state, MESSAGES[state]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def scan_once(batch_id: int | None = None, limit: int = SCAN_LIMIT) -> int:
    """Ein Durchgang über noch ungeprüfte Belege. Gibt die Anzahl geprüfter Belege zurück."""
    jobs = await asyncio.to_thread(_select_pending, batch_id, limit)
    if not jobs:
        return 0
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(job: _Job) -> tuple[int, str, int]:
        async with sem:
            state, pages = await asyncio.to_thread(inspect_pdf, job.path)
            return job.doc_id, state, pages

    results = await asyncio.gather(*(one(j) for j in jobs))
    await asyncio.to_thread(_write, list(results))
    bad = sum(1 for _, s, _ in results if s != "ok")
    logger.info("PDF-Prüfung: %d Beleg(e) geprüft%s", len(results), f", {bad} nicht lesbar/verschlüsselt" if bad else "")
    return len(results)


def _batch_running(batch_id: int) -> bool | None:
    db = SessionLocal()
    try:
        b = db.get(ImportBatch, batch_id)
        return None if b is None else b.status in ("pending", "running")
    finally:
        db.close()


async def run_for_batch(batch_id: int, poll_seconds: float = 1.0) -> None:
    """Parallel zum Import: prüft laufend neu kopierte Belege, nach Importende ein letzter Durchgang."""
    try:
        while True:
            running = await asyncio.to_thread(_batch_running, batch_id)
            n = await scan_once(batch_id)
            if running is None or (running is False and n == 0):
                return
            await asyncio.sleep(0 if n else poll_seconds)
    except Exception as exc:  # noqa: BLE001 — der Import darf davon nie abhängen
        logger.error("PDF-Prüfung für Import #%d abgebrochen: %s", batch_id, exc)
