"""
Import-Service: Orchestriert den PDF-Import-Prozess.

Ablauf für einen Import-Batch:
  1. Batch-Status → 'running' setzen (DB)
  2. Alle .pdf/.PDF-Dateien im Import-Ordner ermitteln (Filesystem)
  3. Pro Datei parallel (Semaphore, max 4 gleichzeitig):
     a. DB-Eintrag anlegen (documents, status=pending)
     b. PDF in den Storage-Ordner kopieren ({id}.pdf)
     c. DB-Eintrag aktualisieren (stored_filename, status=done)
  4. Batch-Status → 'done' setzen (DB)

Parallelität:
  - Datei-I/O läuft im dedizierten _IMPORT_IO_EXECUTOR (ThreadPoolExecutor)
  - DB-Operationen laufen via asyncio.to_thread (Standard-Pool)
  - Semaphore begrenzt gleichzeitige Kopier-Operationen auf 4 (NAS-verträglich)
  - KEINE Seitenanzahl beim Import — wird erst bei der KI-Analyse gesetzt

Sicherheit:
  - validate_import_path() prüft dass der Pfad unter IMPORT_BASE_PATH liegt
  - Quell-PDFs werden NUR gelöscht wenn ein stored_filename in der DB existiert
"""

import asyncio
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import crud
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# Dedizierter Thread-Pool für Datei-I/O (PDF-Kopieren, Verzeichnis-Operationen).
# Größerer Pool als der Standard-Asyncio-Pool, da NAS-I/O viele Threads blockieren kann.
_IMPORT_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=min(32, (os.cpu_count() or 4) * 4),
    thread_name_prefix="import_io",
)


async def _run_import_io(func, *args):
    """Führt eine blockierende Filesystem-Funktion im dedizierten Import-I/O-Pool aus."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_IMPORT_IO_EXECUTOR, func, *args)


# ─── Pfad-Hilfsfunktionen ────────────────────────────────────────────────────

def validate_import_path(folder_path: str) -> Path:
    """
    Prüft dass folder_path unter IMPORT_BASE_PATH liegt (Sicherheitscheck:
    verhindert Path-Traversal-Angriffe). Legt den Ordner an falls er noch
    nicht existiert. Gibt den aufgelösten Path zurück.
    """
    base = Path(settings.import_base_path).resolve()
    target = Path(folder_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Ungültiger Pfad: '{folder_path}' liegt nicht unter '{settings.import_base_path}'."
        )
    target.mkdir(parents=True, exist_ok=True)
    return target


def parse_folder_name(folder_name: str) -> tuple[str, int]:
    """
    Parst einen Ordnernamen im Format 'FirmaName_YYYY' in (firma, jahr).
    Wirft ValueError wenn das Format nicht passt.
    """
    import re
    match = re.match(r"^(.+)_(\d{4})$", folder_name)
    if not match:
        raise ValueError(f"Ordnername '{folder_name}' muss dem Format 'FirmaName_YYYY' entsprechen.")
    return match.group(1), int(match.group(2))


def list_pdf_files(folder_path: Path) -> list[Path]:
    """
    Gibt alle PDF-Dateien (*.pdf und *.PDF) im Ordner zurück — alphabetisch sortiert.
    Sucht nicht in Unterordnern (nur top-level).
    """
    all_files = sorted(
        set(folder_path.glob("*.pdf")) | set(folder_path.glob("*.PDF")),
        key=lambda p: p.name.lower(),
    )
    logger.info("Gefundene PDFs in '%s': %d", folder_path.name, len(all_files))
    return all_files


# ─── Sync DB-Hilfsfunktionen (laufen via asyncio.to_thread) ─────────────────
# Alle _db_* Funktionen öffnen ihre eigene Session und schließen sie im finally-Block.
# Das vermeidet Sessions die zu lange offen bleiben und den Connection-Pool erschöpfen.

def _db_batch_start(batch_id: int) -> dict | None:
    """
    Setzt Batch-Status → 'running' und gibt Metadaten für den Import zurück.
    Gibt None zurück wenn der Batch nicht gefunden wurde.
    """
    db = SessionLocal()
    try:
        batch = crud.import_batch.update_status(db, batch_id, "running")
        if batch is None:
            return None
        logger.info("Import gestartet: %s %d", batch.company_name, batch.year)
        return {
            "company_name": batch.company_name,
            "year": batch.year,
            "import_folder_path": batch.import_folder_path,
            "storage_folder_path": batch.storage_folder_path,
        }
    finally:
        db.close()


def _db_batch_finish(batch_id: int, processed: int, error_count: int, total: int) -> None:
    """Setzt Batch-Status → 'done' und loggt eine Zusammenfassung."""
    db = SessionLocal()
    try:
        ok = processed - error_count
        logger.info(
            "Import abgeschlossen: %d erfolgreich%s (von %d gesamt)",
            ok,
            f", {error_count} fehlerhaft" if error_count else "",
            total,
        )
        crud.import_batch.update_status(db, batch_id, "done")
    finally:
        db.close()


def _db_batch_error(batch_id: int, message: str) -> None:
    """Setzt Batch-Status → 'error' bei einem fatalen Import-Fehler."""
    db = SessionLocal()
    try:
        logger.error("Import-Fehler Batch #%d: %s", batch_id, message)
        crud.import_batch.update_status(db, batch_id, "error")
    except Exception as exc:
        logger.error("Konnte Batch-Fehler nicht schreiben: %s", exc)
    finally:
        db.close()


def _db_doc_create(batch_id: int, filename: str, file_size: int) -> int | None:
    """
    Legt einen neuen Document-Eintrag in der DB an (status=pending).
    Gibt die neue Dokument-ID zurück oder None bei einem Fehler.
    """
    db = SessionLocal()
    try:
        doc = crud.document.create(
            db=db,
            batch_id=batch_id,
            original_filename=filename,
            file_size_bytes=file_size,
        )
        return doc.id
    except Exception as exc:
        logger.error("DB-Fehler beim Anlegen von '%s': %s", filename, exc)
        return None
    finally:
        db.close()


def _db_doc_finish(doc_id: int, stored_filename: str, page_count: int) -> None:
    """
    Aktualisiert stored_filename und setzt status → 'done' nach erfolgreichem Kopieren.
    page_count ist beim Import immer 0 — wird erst bei der KI-Analyse gesetzt.
    """
    db = SessionLocal()
    try:
        crud.document.update_after_copy(db, doc_id, stored_filename, page_count)
    except Exception as exc:
        logger.error("DB-Fehler beim Abschließen von #%d: %s", doc_id, exc)
        try:
            db.rollback()
            crud.document.update_status(db, doc_id, "error")
        except Exception:
            pass
    finally:
        db.close()


def _db_doc_error(doc_id: int, batch_id: int, error_msg: str, filename: str) -> None:
    """Setzt Dokument-Status → 'error' nach einem Kopier- oder DB-Fehler."""
    db = SessionLocal()
    try:
        logger.error("Import-Fehler '%s' (#%d): %s", filename, doc_id, error_msg)
        crud.document.update_status(db, doc_id, "error")
    except Exception as exc:
        logger.error("Fehler beim Schreiben des Error-Status für #%d: %s", doc_id, exc)
    finally:
        db.close()


# ─── Haupt-Import-Funktion ────────────────────────────────────────────────────

async def run_import(batch_id: int) -> None:
    """
    Führt den vollständigen Import-Prozess für einen Batch durch.

    Liest Metadaten aus der DB, ermittelt alle PDFs im Import-Ordner
    und kopiert sie parallel (max. 4 gleichzeitig) in den Storage-Ordner.
    Status-Updates laufen asynchron via asyncio.to_thread.
    """
    logger.info("Import-Task gestartet für Batch #%d", batch_id)

    batch_info = await asyncio.to_thread(_db_batch_start, batch_id)
    if batch_info is None:
        logger.error("Batch #%d nicht gefunden", batch_id)
        return

    company_name: str = batch_info["company_name"]
    year: int = batch_info["year"]
    import_folder_path: str = batch_info["import_folder_path"]
    storage_folder_path: str = batch_info["storage_folder_path"]

    try:
        folder_path = await _run_import_io(validate_import_path, import_folder_path)
    except (ValueError, Exception) as exc:
        await asyncio.to_thread(_db_batch_error, batch_id, f"Ungültiger Ordnerpfad: {exc}")
        return

    pdf_files: list[Path] = await _run_import_io(list_pdf_files, folder_path)

    if not pdf_files:
        await asyncio.to_thread(_db_batch_error, batch_id, "Keine PDF-Dateien im Import-Ordner gefunden")
        return

    storage_dir = Path(storage_folder_path)
    # Lambda statt positionaler mkdir-Argumente: Path.mkdir(mode, parents, exist_ok)
    # ist positionsabhängig — mkdir(True, True) würde mode=True, parents=True setzen,
    # exist_ok bliebe False. Keyword-Args über Lambda vermeiden diese Falle.
    await _run_import_io(lambda: storage_dir.mkdir(parents=True, exist_ok=True))

    logger.info("Batch #%d: %d PDFs gefunden", batch_id, len(pdf_files))

    # Parallelität: fester Wert (processing_settings-Tabelle entfernt)
    concurrency = 4
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    processed_count = 0
    error_count = 0
    UPDATE_EVERY = max(1, min(10, len(pdf_files) // 10))

    async def process_with_semaphore(pdf_path: Path) -> None:
        nonlocal processed_count, error_count
        async with semaphore:
            doc_id = await _process_single_document(
                batch_id=batch_id,
                pdf_path=pdf_path,
                storage_dir=storage_dir,
            )
            async with lock:
                processed_count += 1
                if doc_id is None:
                    error_count += 1

    await asyncio.gather(*[process_with_semaphore(p) for p in pdf_files])

    await asyncio.to_thread(_db_batch_finish, batch_id, processed_count, error_count, len(pdf_files))
    logger.info("Batch #%d fertig: %d ok, %d Fehler",
                batch_id, processed_count - error_count, error_count)


async def _process_single_document(
    batch_id: int,
    pdf_path: Path,
    storage_dir: Path,
) -> int | None:
    """
    Verarbeitet ein einzelnes PDF:
      1. DB-Eintrag anlegen (original_filename, file_size, status=pending)
      2. PDF nach storage_dir/{id}.pdf kopieren
      3. DB aktualisieren (stored_filename, status=done)

    Gibt die neue Dokument-ID zurück bei Erfolg, None bei Fehler (auch von
    app/worker/folder_sync.py genutzt, das die ID braucht um das Dokument
    anschließend zur KI-Analyse einzureihen).
    page_count wird als 0 gespeichert — die KI-Analyse setzt den echten Wert später.
    """
    try:
        file_size = await _run_import_io(lambda: pdf_path.stat().st_size)
    except OSError:
        file_size = 0

    doc_id = await asyncio.to_thread(_db_doc_create, batch_id, pdf_path.name, file_size)
    if doc_id is None:
        return None

    stored_filename = f"{doc_id}.pdf"
    dest_path = storage_dir / stored_filename

    try:
        await _run_import_io(shutil.copy2, str(pdf_path), str(dest_path))
    except OSError as exc:
        logger.error("Kopierfehler für '%s': %s", pdf_path.name, exc)
        await asyncio.to_thread(_db_doc_error, doc_id, batch_id,
                                f"Kopierfehler: {exc}", pdf_path.name)
        return None

    await asyncio.to_thread(_db_doc_finish, doc_id, stored_filename, 0)
    logger.debug("Dokument #%d importiert: %s", doc_id, pdf_path.name)
    return doc_id
