"""
Router für Import-Batches.

Endpunkte:
  GET  /api/imports/              — alle Batches (optional gefiltert)
  POST /api/imports/              — neuen Import starten
  GET  /api/imports/{id}          — Batch-Details mit Dokumentliste
  GET  /api/imports/{id}/export   — Excel-Export aller Dokumente des Batches
"""

import asyncio
import io
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import crud
from app.database import SessionLocal, get_db
from app.schemas.import_batch import ImportBatchCreate, ImportBatchRead, ImportBatchWithDocuments
from app.services.import_service import (
    _run_import_io,
    list_pdf_files,
    run_import,
    validate_import_path,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/imports", tags=["Imports"])


# ─── Sync DB-Hilfsfunktionen (laufen via asyncio.to_thread) ─────────────────

def _db_get_source_filenames(batch_id: int) -> list[str]:
    """
    Gibt die original_filename aller erfolgreich kopierten Dokumente zurück.
    Direkte Query statt Relationship-Zugriff für maximale Zuverlässigkeit.
    """
    from app.models.document import Document as _Doc
    db = SessionLocal()
    try:
        rows = (
            db.query(_Doc.original_filename)
            .filter(
                _Doc.batch_id == batch_id,
                _Doc.stored_filename.isnot(None),
                _Doc.original_filename.isnot(None),
            )
            .all()
        )
        names = [r.original_filename for r in rows]
        logger.info(
            "Batch #%d: %d Quelldatei(en) in DB gefunden", batch_id, len(names)
        )
        return names
    finally:
        db.close()


def _db_get_analyze_setup(
    batch_id: int,
    ai_config_id: int | None,
    system_prompt_id: int | None,
) -> dict | None:
    """
    Löst KI-Config, Systemprompt und Dokument-IDs für die KI-Analyse auf.
    Gibt None zurück, wenn keine KI-Konfiguration gefunden wurde.
    """
    db = SessionLocal()
    try:
        if ai_config_id:
            resolved_config = crud.ai_config.get_by_id(db, ai_config_id)
        else:
            resolved_config = crud.ai_config.get_default(db)

        if resolved_config is None:
            logger.warning(
                "Batch #%d: Keine KI-Konfiguration gefunden — KI-Analyse übersprungen",
                batch_id,
            )
            return None

        if system_prompt_id:
            sp = crud.system_prompt.get_by_id(db, system_prompt_id)
            system_prompt_text = sp.content if sp else None
        else:
            default_sp = crud.system_prompt.get_default(db)
            system_prompt_text = default_sp.content if default_sp else None

        batch = crud.import_batch.get_by_id(db, batch_id)
        doc_ids = [d.id for d in (batch.documents if batch else []) if d.stored_filename]

        return {
            "ai_config_id": resolved_config.id,
            "system_prompt_text": system_prompt_text,
            "doc_ids": doc_ids,
        }
    finally:
        db.close()


def _db_enqueue_for_analysis(doc_ids: list[int]) -> int:
    """
    Reiht Dokumente zur KI-Analyse in die Worker-Queue ein.
    Verhindert Duplikate: Dokumente mit bereits laufendem/wartendem Task werden übersprungen.
    Gibt die Anzahl neu eingereihter Dokumente zurück.
    """
    import uuid
    from sqlalchemy import text as _text
    from app.models.workflow_task import WorkflowTask
    db = SessionLocal()
    count = 0
    try:
        for doc_id in doc_ids:
            existing = db.execute(
                _text(
                    "SELECT id FROM workflow_tasks "
                    "WHERE payload->>'document_id' = :doc_id "
                    "AND status IN ('pending', 'in_progress') LIMIT 1"
                ),
                {"doc_id": str(doc_id)},
            ).first()
            if existing:
                logger.debug("Dokument #%d bereits in Worker-Queue — übersprungen", doc_id)
                continue
            task = WorkflowTask(
                workflow_id=str(uuid.uuid4()),
                payload={"kind": "process_document", "document_id": doc_id},
                status="pending",
            )
            db.add(task)
            count += 1
        db.commit()
        return count
    except Exception as exc:
        logger.error("Fehler beim Einreihen der Analyse-Tasks: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        db.close()


def _sync_delete_source_files(import_folder: str, original_names: list[str]) -> tuple[int, int]:
    """
    Sync: Löscht Quelldateien aus dem Import-Ordner.
    Gibt (deleted, failed) zurück.
    """
    folder = Path(import_folder)
    deleted, failed = 0, 0
    logger.info(
        "Lösche Quelldateien aus '%s': %d Datei(en)", import_folder, len(original_names)
    )
    for name in original_names:
        src = folder / name
        try:
            src.unlink()  # Direkt löschen — FileNotFoundError bei fehlendem File
            deleted += 1
            logger.info("Quelldatei gelöscht: %s", src)
        except FileNotFoundError:
            logger.warning("Quelldatei nicht gefunden (bereits gelöscht?): %s", src)
        except Exception as exc:
            failed += 1
            logger.error("Konnte Quelldatei nicht löschen %s: %s", src, exc)
    return deleted, failed


async def _delete_source_files(batch_id: int, import_folder: str) -> None:
    """
    Löscht die Original-PDFs aus dem Import-Ordner, die erfolgreich kopiert wurden.
    Es werden nur Dateien gelöscht, für die ein DB-Eintrag mit stored_filename existiert.
    """
    # DB-Abfrage im Thread
    original_names = await asyncio.to_thread(_db_get_source_filenames, batch_id)

    if not original_names:
        logger.warning(
            "Batch #%d: Keine Quelldateien in DB gefunden — nichts zu löschen", batch_id
        )
        return

    logger.info(
        "Batch #%d: %d Quelldatei(en) werden aus '%s' gelöscht",
        batch_id, len(original_names), import_folder,
    )

    # Filesystem-Operationen im Thread (einfaches asyncio.to_thread, kein spezieller Pool nötig)
    deleted, failed = await asyncio.to_thread(
        _sync_delete_source_files, import_folder, original_names
    )

    logger.info(
        "Batch #%d: Quelldateien gelöscht=%d, fehlgeschlagen=%d", batch_id, deleted, failed
    )


async def _import_and_delete(batch_id: int, import_folder: str) -> None:
    """Import durchführen und danach Quelldateien löschen (ohne KI-Analyse)."""
    try:
        await run_import(batch_id)
        await _delete_source_files(batch_id, import_folder)
    except Exception as exc:
        logger.error(
            "Batch #%d: Fehler in _import_and_delete: %s", batch_id, exc, exc_info=True
        )


async def _import_then_analyze(
    batch_id: int,
    import_folder: str,
    ai_config_id: int | None = None,
    system_prompt_id: int | None = None,
    delete_source_files: bool = False,
) -> None:
    """
    Führt zuerst den Import durch, reiht danach alle importierten Dokumente
    zur KI-Analyse in die Worker-Queue ein.

    Alle DB- und Filesystem-Operationen laufen in Thread-Pools (nicht-blockierend).
    Die Analyse wird vom WorkerPool übernommen — keine direkte _run_analysis-Ausführung.
    Das verhindert Race-Conditions zwischen Import-Pfad und Worker-Queue.
    """
    try:
        # 1. Import abwarten
        await run_import(batch_id)

        # 2. Quelldateien löschen (falls gewünscht), bevor KI läuft
        if delete_source_files:
            await _delete_source_files(batch_id, import_folder)

        # 3. Dokument-IDs für diesen Batch ermitteln (im Thread)
        setup = await asyncio.to_thread(_db_get_analyze_setup, batch_id, ai_config_id, system_prompt_id)
        if setup is None:
            return

        doc_ids: list[int] = setup["doc_ids"]
        if not doc_ids:
            logger.info("Batch #%d: Keine Dokumente für KI-Analyse", batch_id)
            return

        # 4. Dokumente in Worker-Queue einreihen (statt direkter Analyse)
        #    Verhindert Duplikate falls Dokumente bereits in der Queue sind.
        enqueued = await asyncio.to_thread(_db_enqueue_for_analysis, doc_ids)
        logger.info(
            "Batch #%d: %d/%d Dokument(e) zur KI-Analyse in Worker-Queue eingereiht",
            batch_id, enqueued, len(doc_ids),
        )

    except Exception as exc:
        logger.error(
            "Batch #%d: Fehler in _import_then_analyze: %s", batch_id, exc, exc_info=True
        )


@router.get("", response_model=list[ImportBatchRead])
def list_imports(
    company_name: str | None = Query(None, description="Firmenname (Teilstring-Suche)"),
    year: int | None = Query(None, description="Importjahr (exakt)"),
    db: Session = Depends(get_db),
):
    """
    Gibt alle Import-Batches zurück.
    Optional nach Firmenname (Teilstring) und/oder Jahr filtern.
    """
    return crud.import_batch.get_all(db, company_name=company_name, year=year)


@router.post("", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
async def start_import(payload: ImportBatchCreate, db: Session = Depends(get_db)):
    """
    Startet einen neuen Import-Vorgang.

    Der Ordnerpfad wird aus Firma + Jahr konstruiert: IMPORT_BASE_PATH/Firma_Jahr
    Falls der Ordner noch nicht existiert, wird er angelegt.
    Keine KI-Extraktion beim Import – nur Kopieren und Metadaten erfassen.
    """
    # ── Firma + Jahr bestimmen ─────────────────────────────────────────────
    if not payload.company_name or not payload.year:
        raise HTTPException(status_code=400, detail="Firmenname und Jahr sind erforderlich.")
    company_name = payload.company_name
    year = payload.year

    # ── Import-Pfad: IMPORT_BASE_PATH + optionaler Unterordner ──────────────
    from app.config import settings as _settings
    base = _settings.import_base_path
    subfolder = (payload.subfolder or "").strip().strip("/\\")
    folder_path = f"{base}/{subfolder}" if subfolder else base
    payload = payload.model_copy(update={"folder_path": folder_path})

    try:
        validated_path = validate_import_path(folder_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── PDF-Prüfung ────────────────────────────────────────────────────────
    pdf_files = list_pdf_files(validated_path)
    if not pdf_files:
        raise HTTPException(
            status_code=400,
            detail=f"Keine PDF-Dateien im Import-Ordner gefunden.",
        )

    # ── Batch in DB anlegen ────────────────────────────────────────────────
    batch = crud.import_batch.create(
        db=db,
        data=payload,
        company_name=company_name,
        year=year,
    )
    logger.info("Import-Batch #%d erstellt: %s_%d (%d PDFs)", batch.id, company_name, year, len(pdf_files))

    if payload.analyze_after_import:
        asyncio.create_task(_import_then_analyze(
            batch_id=batch.id,
            import_folder=folder_path,
            ai_config_id=payload.ai_config_id,
            system_prompt_id=payload.system_prompt_id,
            delete_source_files=payload.delete_source_files,
        ))
    elif payload.delete_source_files:
        asyncio.create_task(_import_and_delete(batch.id, folder_path))
    else:
        asyncio.create_task(run_import(batch.id))
    return batch


@router.get("/{batch_id}/status", response_model=ImportBatchRead)
def get_import_status(batch_id: int, db: Session = Depends(get_db)):
    """
    Gibt nur den Status eines Import-Batches zurück — OHNE Dokumentliste.
    Für leichtgewichtiges Polling während eines laufenden Imports.
    """
    from app.models.import_batch import ImportBatch as ImportBatchModel
    obj = db.get(ImportBatchModel, batch_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Import-Batch nicht gefunden")
    return obj


@router.get("/{batch_id}/ki-stats")
def get_batch_ki_stats(batch_id: int, db: Session = Depends(get_db)):
    """Aggregierte KI-Statistiken für alle Dokumente eines Import-Batches."""
    from sqlalchemy import func as sqlfunc
    from app.models.document_token_count import DocumentTokenCount as _TC
    from app.models.document import Document as _Doc

    row = (
        db.query(
            sqlfunc.sum(_TC.token_count).label("total_tokens"),
            sqlfunc.sum(_TC.time_spent_seconds).label("total_duration_seconds"),
        )
        .join(_Doc, _TC.document_id == _Doc.id)
        .filter(_Doc.batch_id == batch_id)
        .first()
    )

    return {
        "total_tokens": int(row.total_tokens or 0),
        "total_duration_seconds": float(row.total_duration_seconds or 0.0),
    }


@router.get("/{batch_id}", response_model=ImportBatchWithDocuments)
def get_import(batch_id: int, db: Session = Depends(get_db)):
    """Gibt einen Import-Batch mit vollständiger Dokumentliste zurück."""
    batch = crud.import_batch.get_by_id(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Import-Batch nicht gefunden")
    return batch


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import(batch_id: int, db: Session = Depends(get_db)):
    """Löscht einen Import-Batch, alle zugehörigen Dokumente und die PDF-Dateien vom Filesystem."""
    import shutil
    from pathlib import Path

    from app.config import settings as _settings

    # Batch mit Dokumenten laden
    batch = crud.import_batch.get_by_id(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Import-Batch nicht gefunden")

    # PDF-Dateien vom Filesystem löschen
    deleted_files = 0
    failed_files = 0
    deleted_folders: set[Path] = set()

    for doc in batch.documents:
        if doc.stored_filename:
            pdf_path = Path(batch.storage_folder_path) / doc.stored_filename
            if pdf_path.exists():
                try:
                    pdf_path.unlink()
                    deleted_files += 1
                    deleted_folders.add(pdf_path.parent)
                    logger.info("Datei gelöscht: %s", pdf_path)
                except Exception as exc:
                    failed_files += 1
                    logger.warning("Fehler beim Löschen von %s: %s", pdf_path, exc)

    # Leere Unterordner ebenfalls löschen
    for folder in deleted_folders:
        try:
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
                logger.info("Leerer Ordner gelöscht: %s", folder)
        except Exception as exc:
            logger.warning("Fehler beim Löschen des Ordners %s: %s", folder, exc)

    if deleted_files or failed_files:
        logger.info(
            "Batch #%d: %d Datei(en) gelöscht, %d Fehler",
            batch_id, deleted_files, failed_files,
        )

    # DB-Eintrag löschen (CASCADE zu Dokumenten, Extraktionen, Positionen)
    crud.import_batch.delete(db, batch_id)


# ─── GET /api/imports/{batch_id}/export ──────────────────────────────────────

@router.get("/{batch_id}/export")
def export_batch_excel(batch_id: int, db: Session = Depends(get_db)):
    """
    Exportiert alle nicht-gelöschten Dokumente eines Import-Batches als Excel-Datei.

    Sheet 1 „Rechnungen": Rechnungsdaten pro Dokument (Lieferant, Beträge, Daten …)
    Sheet 2 „Positionen": Alle Bestellpositionen aller Dokumente des Batches
    """
    from sqlalchemy.orm import joinedload as _jl
    from app.models.document import Document as _Doc
    from app.models.import_batch import ImportBatch as _Batch
    from app.models.invoice_extraction import InvoiceExtraction as _Ext

    batch = db.get(_Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Import-Batch nicht gefunden")

    docs = (
        db.query(_Doc)
        .options(
            _jl(_Doc.extraction),
            _jl(_Doc.order_positions),
        )
        .filter(_Doc.batch_id == batch_id, _Doc.soft_deleted == False)  # noqa: E712
        .order_by(_Doc.id)
        .all()
    )

    excel_bytes = _build_export_excel(batch, docs)
    safe = f"{batch.company_name}_{batch.year}".replace(" ", "_")
    filename = f"Export_{safe}.xlsx"

    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_export_excel(batch, docs) -> bytes:
    """
    Erstellt eine formatierte Excel-Datei mit zwei Sheets:
    - „Rechnungen": alle Rechnungsfelder pro Dokument inkl. Adresse (Straße/PLZ/Ort)
                    und Skonto-Details (Betrag, Prozent, Frist)
    - „Positionen": alle Bestellpositionen inkl. Steuersatz

    Adress-Einzelfelder kommen aus dem verknüpften Supplier-Datensatz.
    Steuersatz und Skonto-Details werden aus raw_response geparst.
    """
    import json as _json

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()

    # ── Styles ────────────────────────────────────────────────────────────────
    hdr_font  = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    hdr_fill  = PatternFill("solid", fgColor="2D3748")
    hdr_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    data_font = Font(name="Arial", size=10)
    fill_a    = PatternFill("solid", fgColor="FFFFFF")
    fill_b    = PatternFill("solid", fgColor="F0F4F8")
    center    = Alignment(horizontal="center", vertical="center")
    fmt_eur   = '#,##0.00 €'
    fmt_pct   = '0.00"%"'
    fmt_date  = 'DD.MM.YYYY'
    fmt_dt    = 'DD.MM.YYYY HH:MM'

    def _header_row(ws, headers: list[str], row: int = 1):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=row, column=c, value=h)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = hdr_align
        ws.row_dimensions[row].height = 28

    def _set_widths(ws, widths: list[int]):
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    def _row_fill(row_idx: int) -> PatternFill:
        return fill_a if row_idx % 2 == 0 else fill_b

    def _f(val):
        """Decimal / int → float, None bleibt None."""
        return float(val) if val is not None else None

    def _dt(val):
        """Timezone-aware datetime → naive (openpyxl verträgt keine tz-aware Objekte)."""
        return val.replace(tzinfo=None) if val and getattr(val, "tzinfo", None) else val

    def _parse_raw(raw_response: str | None) -> dict:
        """Parst raw_response sicher als Dict. Gibt {} bei Fehler zurück."""
        if not raw_response:
            return {}
        try:
            result = _json.loads(raw_response)
            return result if isinstance(result, dict) else {}
        except (_json.JSONDecodeError, TypeError):
            return {}

    # ── Sheet 1: Rechnungen ───────────────────────────────────────────────────
    # Spalten (26 = A..Z):
    # 1  Beleg-Nr.        9  Straße           17 Gesamtbetrag (€)   23 Skonto Frist
    # 2  Dateiname        10 PLZ              18 Rabatt (€)          24 Zahlungsbedingungen
    # 3  Status           11 Ort              19 Skonto (€)          25 Kommentar
    # 4  Seiten           12 USt-IdNr.        20 Skonto (%)          26 Importiert am
    # 5  Rechnungsnr.     13 Steuernr.        21 Skonto Frist (Tage)
    # 6  Rechnungsdatum   14 HRB-Nr.
    # 7  Fälligkeit       15 Kundennr.
    # 8  Lieferant        16 Bank / IBAN / BIC → 16, 17(IBAN), 18(BIC) … warte,
    #    neu gezählt:
    # 8  Lieferant        14 HRB-Nr.          20 Skonto (%)
    # 9  Straße           15 Kundennr.        21 Skonto Frist (Tage)
    # 10 PLZ              16 Bank             22 Zahlungsbedingungen
    # 11 Ort              17 IBAN             23 Kommentar
    # 12 USt-IdNr.        18 BIC              24 Importiert am
    # 13 Steuernr.        19 Gesamtbetrag (€)
    #                     20 Rabatt (€)
    #                     21 Skonto (€)
    #                     22 Skonto (%)
    #                     23 Skonto Frist (Tage)
    #                     24 Zahlungsbedingungen
    #                     25 Kommentar
    #                     26 Importiert am
    NUM_COLS_WS1 = 26

    ws1 = wb.active
    ws1.title = "Rechnungen"

    ws1.merge_cells(f"A1:{get_column_letter(NUM_COLS_WS1)}1")
    ws1["A1"] = f"Export: {batch.company_name} {batch.year}"
    ws1["A1"].font = Font(name="Arial", size=13, bold=True)
    ws1["A1"].alignment = Alignment(vertical="center")
    ws1.row_dimensions[1].height = 24

    _header_row(ws1, [
        "Beleg-Nr.", "Dateiname", "Status", "Seiten",
        "Rechnungsnr.", "Rechnungsdatum", "Fälligkeit",
        "Lieferant", "Straße", "PLZ", "Ort",
        "USt-IdNr.", "Steuernr.", "HRB-Nr.", "Kundennr.",
        "Bank", "IBAN", "BIC",
        "Gesamtbetrag (€)", "Rabatt (€)",
        "Skonto (€)", "Skonto (%)", "Skonto Frist (Tage)",
        "Zahlungsbedingungen", "Kommentar", "Importiert am",
    ], row=2)

    for r, doc in enumerate(docs, start=3):
        ex   = doc.extraction
        fill = _row_fill(r)

        # Skonto-Details aus raw_response
        raw      = _parse_raw(ex.raw_response if ex else None)
        skonto   = (raw.get("zahlungsinformationen") or {}).get("skonto") or {}
        skonto_p = _f(skonto.get("prozent"))
        skonto_t = skonto.get("frist_tage")
        if isinstance(skonto_t, float) and skonto_t.is_integer():
            skonto_t = int(skonto_t)

        row_vals = [
            doc.id,
            doc.original_filename,
            doc.status,
            doc.page_count or None,
            ex.invoice_number              if ex else None,
            ex.invoice_date                if ex else None,
            ex.due_date                    if ex else None,
            ex.vendor_id                   if ex else None,  # Lieferant (Freitext)
            None,  # Straße (kein Vendor-FK mehr)
            None,  # PLZ
            None,  # Ort
            None,  # USt-IdNr. (jetzt in vendor-Tabelle)
            None,  # Steuernr.
            None,  # HRB-Nr.
            None,  # Kundennr.
            None,  # Bank
            None,  # IBAN
            None,  # BIC
            _f(ex.total_amount_brutto)     if ex else None,
            _f(ex.discount_amount)         if ex else None,
            _f(ex.cash_discount_amount)    if ex else None,
            skonto_p,
            skonto_t,
            ex.payment_terms               if ex else None,
            doc.comment,
            _dt(doc.created_at),
        ]

        for c, val in enumerate(row_vals, 1):
            cell = ws1.cell(row=r, column=c, value=val)
            cell.font = data_font
            cell.fill = fill
            if c in (19, 20, 21):           # Beträge
                cell.number_format = fmt_eur
            elif c == 22:                    # Skonto %
                cell.number_format = fmt_pct
            elif c in (6, 7):               # Datumsfelder
                cell.number_format = fmt_date
            elif c == 26:                   # Importiert am
                cell.number_format = fmt_dt
            elif c == 4:                    # Seiten — zentriert
                cell.alignment = center

    _set_widths(ws1, [
        10, 36, 12, 8,          # Beleg, Dateiname, Status, Seiten
        22, 14, 14,             # Rechnungsnr., Datum, Fälligkeit
        30, 28, 8, 20,          # Lieferant, Straße, PLZ, Ort
        18, 16, 14, 14,         # USt-IdNr., Steuernr., HRB, Kundennr.
        24, 26, 12,             # Bank, IBAN, BIC
        17, 13, 13, 10, 12,     # Gesamtbetrag, Rabatt, Skonto€, Skonto%, Frist
        42, 30, 18,             # Zahlungsbedingungen, Kommentar, Importiert am
    ])
    ws1.freeze_panes = "A3"

    # ── Sheet 2: Positionen ───────────────────────────────────────────────────
    # Steuersatz wird aus raw_response geparst (nicht im ORM-Modell)
    ws2 = wb.create_sheet("Positionen")

    _header_row(ws2, [
        "Beleg-Nr.", "Rechnungsnr.", "Lieferant", "Pos.",
        "Artikelbezeichnung", "Artikelnummer",
        "Menge", "Einheit",
        "EP Netto (€)", "EP Brutto (€)", "Steuersatz (%)", "Nachlass",
    ])

    r = 2
    for doc in docs:
        ex = doc.extraction

        # Steuersätze aus raw_response: positionen[i].steuersatz
        raw = _parse_raw(ex.raw_response if ex else None)
        raw_pos_list = raw.get("positionen") or []
        # Mapping: position_index → steuersatz
        tax_map = {i: _f(p.get("steuersatz")) for i, p in enumerate(raw_pos_list)}

        for pos in doc.order_positions:
            fill = _row_fill(r)
            row_vals = [
                doc.id,
                ex.invoice_number    if ex else None,
                ex.vendor_id         if ex else None,
                pos.position_index + 1,
                pos.product_name or pos.product_description,
                pos.article_number,
                _f(pos.quantity),
                pos.unit,
                _f(pos.unit_price_netto),
                _f(pos.unit_price_brutto),
                tax_map.get(pos.position_index),
                pos.discount,
            ]
            for c, val in enumerate(row_vals, 1):
                cell = ws2.cell(row=r, column=c, value=val)
                cell.font = data_font
                cell.fill = fill
                if c in (9, 10):
                    cell.number_format = fmt_eur
                elif c == 11:               # Steuersatz %
                    cell.number_format = fmt_pct
                elif c == 4:
                    cell.alignment = center
            r += 1

    if r == 2:
        ws2.cell(row=2, column=1, value="Keine Positionen vorhanden").font = Font(
            name="Arial", size=10, color="888888", italic=True
        )

    _set_widths(ws2, [10, 22, 30, 8, 52, 20, 12, 12, 17, 17, 14, 22])
    ws2.freeze_panes = "A2"

    # ── Bytes zurückgeben ─────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()
