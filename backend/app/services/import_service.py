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

_IMPORT_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=min(32, (os.cpu_count() or 4) * 4),
    thread_name_prefix="import_io",
)


async def _run_import_io(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_IMPORT_IO_EXECUTOR, func, *args)


# ─── Pfad-Hilfsfunktionen ────────────────────────────────────────────────────

def validate_import_path(folder_path: str) -> Path:
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
    import re
    match = re.match(r"^(.+)_(\d{4})$", folder_name)
    if not match:
        raise ValueError(f"Ordnername '{folder_name}' muss dem Format 'FirmaName_YYYY' entsprechen.")
    return match.group(1), int(match.group(2))


def list_pdf_files(folder_path: Path) -> list[Path]:
    all_files = sorted(
        set(folder_path.glob("*.pdf")) | set(folder_path.glob("*.PDF")),
        key=lambda p: p.name.lower(),
    )
    logger.info("Gefundene PDFs in '%s': %d", folder_path.name, len(all_files))
    return all_files


# ─── Sync DB-Hilfsfunktionen (laufen via asyncio.to_thread) ─────────────────

def _db_batch_start(batch_id: int) -> dict | None:
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
    db = SessionLocal()
    try:
        logger.error("Import-Fehler Batch #%d: %s", batch_id, message)
        crud.import_batch.update_status(db, batch_id, "error")
    except Exception as exc:
        logger.error("Konnte Batch-Fehler nicht schreiben: %s", exc)
    finally:
        db.close()


def _db_doc_create(batch_id: int, filename: str, file_size: int) -> int | None:
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
    await _run_import_io(storage_dir.mkdir, True, True)

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
            success = await _process_single_document(
                batch_id=batch_id,
                pdf_path=pdf_path,
                storage_dir=storage_dir,
            )
            async with lock:
                processed_count += 1
                if not success:
                    error_count += 1

    await asyncio.gather(*[process_with_semaphore(p) for p in pdf_files])

    await asyncio.to_thread(_db_batch_finish, batch_id, processed_count, error_count, len(pdf_files))
    logger.info("Batch #%d fertig: %d ok, %d Fehler",
                batch_id, processed_count - error_count, error_count)


async def _process_single_document(
    batch_id: int,
    pdf_path: Path,
    storage_dir: Path,
) -> bool:
    try:
        file_size = await _run_import_io(lambda: pdf_path.stat().st_size)
    except OSError:
        file_size = 0

    doc_id = await asyncio.to_thread(_db_doc_create, batch_id, pdf_path.name, file_size)
    if doc_id is None:
        return False

    stored_filename = f"{doc_id}.pdf"
    dest_path = storage_dir / stored_filename

    try:
        await _run_import_io(shutil.copy2, str(pdf_path), str(dest_path))
    except OSError as exc:
        logger.error("Kopierfehler für '%s': %s", pdf_path.name, exc)
        await asyncio.to_thread(_db_doc_error, doc_id, batch_id,
                                f"Kopierfehler: {exc}", pdf_path.name)
        return False

    await asyncio.to_thread(_db_doc_finish, doc_id, stored_filename, 0)
    logger.debug("Dokument #%d importiert: %s", doc_id, pdf_path.name)
    return True
