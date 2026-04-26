import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.database import SessionLocal, get_db
from app.schemas.document import DocumentCommentUpdate, DocumentDetail, DocumentListRead
from app.services import ai_service, pdf_service

logger = logging.getLogger(__name__)

_KI_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=min(16, (os.cpu_count() or 4) * 2),
    thread_name_prefix="ki_pdf",
)

router = APIRouter(prefix="/api/documents", tags=["Dokumente"])


async def _run_ki_io(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_KI_IO_EXECUTOR, func, *args)


def _set_error(doc_id: int, message: str) -> None:
    try:
        _db = SessionLocal()
        try:
            crud.document.update_status(_db, doc_id, "error")
        finally:
            _db.close()
    except Exception as exc:
        logger.error("Konnte Fehlerstatus für #%d nicht setzen: %s", doc_id, exc)


class AnalyzeRequest(BaseModel):
    document_ids: list[int]
    ai_config_id: Optional[int] = None
    system_prompt_id: Optional[int] = None


class AnalyzeResponse(BaseModel):
    started: int
    message: str


class EnqueueRequest(BaseModel):
    document_ids: list[int]


class EnqueueResponse(BaseModel):
    enqueued: int
    message: str


@router.post("/enqueue", response_model=EnqueueResponse)
def enqueue_documents(payload: EnqueueRequest, db: Session = Depends(get_db)):
    """
    Stellt Dokumente zur KI-Analyse in die Worker-Warteschlange.
    Pro Dokument-ID wird ein WorkflowTask (kind=process_document) angelegt.
    Dokumente mit bereits laufendem/wartendem Task werden übersprungen.
    """
    import uuid
    from sqlalchemy import text as _text
    from app.models.workflow_task import WorkflowTask

    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="Keine Dokument-IDs angegeben.")

    count = 0
    skipped = 0
    for doc_id in payload.document_ids:
        # Prüfen ob bereits ein aktiver Task vorhanden ist
        existing = db.execute(
            _text(
                "SELECT id FROM workflow_tasks "
                "WHERE payload->>'document_id' = :doc_id "
                "AND status IN ('pending', 'in_progress') LIMIT 1"
            ),
            {"doc_id": str(doc_id)},
        ).first()
        if existing:
            skipped += 1
            logger.debug("Dokument #%d: bereits in Warteschlange — übersprungen", doc_id)
            continue

        task = WorkflowTask(
            workflow_id=str(uuid.uuid4()),
            payload={"kind": "process_document", "document_id": doc_id},
            status="pending",
        )
        db.add(task)
        count += 1

    db.commit()
    logger.info("%d Dokument(e) in Worker-Warteschlange gestellt (%d übersprungen)", count, skipped)
    msg = f"{count} Dokument{'e' if count != 1 else ''} zur KI-Analyse eingereiht"
    if skipped:
        msg += f" ({skipped} bereits in Warteschlange)"
    return EnqueueResponse(enqueued=count, message=msg + ".")


@router.get("", response_model=list[DocumentListRead])
def list_documents(
    company: Optional[str] = None,
    year: Optional[int] = None,
    status: Optional[str] = None,
    total_min: Optional[float] = None,
    total_max: Optional[float] = None,
    page_min: Optional[int] = None,
    page_max: Optional[int] = None,
    batch_ids: Optional[list[int]] = Query(default=None),
    include_deleted: bool = False,
    has_extraction: Optional[bool] = None,
    supplier_name: Optional[str] = None,
    doc_id: Optional[int] = None,
    document_type_ids: Optional[list[int]] = Query(default=None),
    db: Session = Depends(get_db),
):
    return crud.document.get_all_filtered(
        db,
        company=company,
        year=year,
        status=status,
        total_min=total_min,
        total_max=total_max,
        page_min=page_min,
        page_max=page_max,
        batch_ids=batch_ids,
        include_deleted=include_deleted,
        has_extraction=has_extraction,
        supplier_name_filter=supplier_name,
        doc_id=doc_id,
        document_type_ids=document_type_ids,
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_documents(
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
):
    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="Keine Dokument-IDs angegeben")

    ai_config = None
    if payload.ai_config_id:
        ai_config = crud.ai_config.get_by_id(db, payload.ai_config_id)
        if ai_config is None:
            raise HTTPException(status_code=404, detail=f"KI-Konfiguration #{payload.ai_config_id} nicht gefunden")
    else:
        ai_config = crud.ai_config.get_default(db)
        if ai_config is None:
            raise HTTPException(status_code=400, detail="Keine aktive KI-Konfiguration vorhanden.")

    system_prompt_text: str | None = None
    if payload.system_prompt_id:
        sp = crud.system_prompt.get_by_id(db, payload.system_prompt_id)
        if sp:
            system_prompt_text = sp.content
    else:
        default_sp = crud.system_prompt.get_default(db)
        if default_sp:
            system_prompt_text = default_sp.content

    from app.models.document import Document as DocModel
    valid_ids: list[int] = []
    for doc_id in payload.document_ids:
        doc = db.get(DocModel, doc_id)
        if doc is None:
            logger.warning("Dokument #%d nicht gefunden — übersprungen", doc_id)
            continue
        if not doc.stored_filename:
            logger.warning("Dokument #%d hat keine gespeicherte Datei — übersprungen", doc_id)
            continue
        valid_ids.append(doc_id)
        crud.document.update_status(db, doc_id, "processing")

    if not valid_ids:
        raise HTTPException(status_code=400, detail="Keine gültigen Dokumente gefunden")

    ai_config_id = ai_config.id
    asyncio.create_task(_run_analysis(
        document_ids=valid_ids,
        ai_config_id=ai_config_id,
        system_prompt_text=system_prompt_text,
    ))

    return AnalyzeResponse(started=len(valid_ids), message=f"KI-Analyse für {len(valid_ids)} Dokument(e) gestartet")


def _db_analyze_read(doc_id: int, ai_config_id: int) -> dict | None:
    from app.models.document import Document as DocModel
    db = SessionLocal()
    try:
        from sqlalchemy.orm import joinedload
        doc = (
            db.query(DocModel)
            .options(joinedload(DocModel.batch))
            .filter(DocModel.id == doc_id)
            .first()
        )
        if doc is None:
            logger.error("Dokument #%d nicht in DB gefunden", doc_id)
            return None

        ai_config = crud.ai_config.get_by_id(db, ai_config_id)
        if ai_config is None:
            logger.error("KI-Konfiguration #%d nicht in DB gefunden", ai_config_id)
            crud.document.update_status(db, doc_id, "error")
            return None

        # PDF-Pfad aus Batch-storage_folder_path ableiten
        storage_path = doc.batch.storage_folder_path if doc.batch else ""
        pdf_path = Path(storage_path) / doc.stored_filename

        if not pdf_path.exists():
            logger.error("PDF nicht gefunden: %s", pdf_path)
            crud.document.update_status(db, doc_id, "error")
            return None

        img_settings = crud.image_settings.get_or_create(db)

        # Dokumententyp-Prompt laden (type=1)
        doc_type_prompt = crud.system_prompt.get_doc_type_prompt(db)

        # Bekannte Dokumententypen als Liste (aus Integer-Mapping)
        document_types = _DOCUMENT_TYPE_LIST

        # API-URL aus ip_address + port konstruieren
        ip = ai_config.ip_address
        port = ai_config.port
        api_url = f"http://{ip}:{port}" if port else ip

        return {
            "pdf_path": pdf_path,
            "original_filename": doc.original_filename,
            "batch_id": doc.batch_id,
            "img_dpi": img_settings.dpi,
            "img_format": img_settings.image_format,
            "img_quality": img_settings.jpeg_quality,
            "ai_api_url": api_url,
            "ai_api_key": ai_config.api_key,
            "ai_model_name": ai_config.model_name,
            "ai_max_tokens": ai_config.max_tokens,
            "ai_temperature": ai_config.temperature,
            "ai_reasoning": ai_config.reasoning or "off",
            "ai_endpoint_type": ai_config.endpoint_type or "openai",
            "doc_type_prompt_text": doc_type_prompt.content if doc_type_prompt else None,
            "document_types": document_types,
            # Bereits gespeicherter Typ (0 = unbekannt/noch nicht erkannt)
            "existing_document_type": doc.document_type or 0,
        }
    except Exception as exc:
        logger.exception("Phase 1 DB-Fehler bei Dokument #%d: %s", doc_id, exc)
        try:
            db.rollback()
            crud.document.update_status(db, doc_id, "error")
        except Exception:
            pass
        return None
    finally:
        db.close()


# Statische Dokumententypen (keine eigene Tabelle mehr)
_DOCUMENT_TYPE_LIST = [
    {"id": 0, "name": "Unbekannt"},
    {"id": 1, "name": "Eingangsrechnung"},
    {"id": 2, "name": "Ausgangsrechnung"},
    {"id": 3, "name": "Lieferschein"},
    {"id": 4, "name": "Bestellbestätigung"},
    {"id": 5, "name": "Angebot"},
    {"id": 6, "name": "Gutschrift / Storno"},
    {"id": 7, "name": "Mahnung"},
    {"id": 8, "name": "Kontoauszug"},
    {"id": 9, "name": "Vertrag"},
    {"id": 10, "name": "Lohnabrechnung"},
    {"id": 11, "name": "Steuer- / Behördendokument"},
    {"id": 12, "name": "Reisekostenabrechnung"},
    {"id": 13, "name": "Kassenbon / Quittung"},
    {"id": 14, "name": "Sonstiges kaufmännisches Dokument"},
]


def _db_save_document_type(doc_id: int, type_id: int | None) -> None:
    db = SessionLocal()
    try:
        crud.document.update_document_type(db, doc_id, type_id)
    except Exception as exc:
        logger.error("Fehler beim Speichern des Dokumententyps für #%d: %s", doc_id, exc)
    finally:
        db.close()


def _db_type_only_finish(
    doc_id: int,
    type_id: int | None,
    type_name: str | None,
    page_count: int,
    batch_id: int | None,
    original_filename: str,
    ki_stats: dict | None = None,
) -> None:
    """Schließt Nicht-Eingangsrechnungen ohne Rechnungsdaten-Extraktion ab."""
    from app.models.document import Document as _DocModel
    from app.models.document_token_count import DocumentTokenCount
    db = SessionLocal()
    try:
        doc = db.get(_DocModel, doc_id)
        if doc is not None:
            doc.document_type = type_id or 0
            if page_count > 0:
                doc.page_count = page_count
            doc.status = "done"
            db.commit()

        if ki_stats:
            tc = DocumentTokenCount(
                document_id=doc_id,
                input_token_count=(ki_stats.get("input_tokens") or 0),
                output_token_count=(ki_stats.get("output_tokens") or 0),
                reasoning_count=ki_stats.get("reasoning_tokens") or 0,
                time_spent_seconds=ki_stats.get("total_duration") or 0.0,
            )
            db.add(tc)
            try:
                db.commit()
            except Exception:
                db.rollback()

        logger.info("Dokument #%d: Typ erkannt als '%s' — keine Rechnungsextraktion", doc_id, type_name)
    except Exception as exc:
        logger.error("Fehler beim Abschluss ohne Extraktion für #%d: %s", doc_id, exc)
        try:
            db.rollback()
            _set_error(doc_id, f"Abschlussfehler: {exc}")
        except Exception:
            pass
    finally:
        db.close()


def _merge_ki_stats(stats1: dict, stats2: dict) -> dict:
    def _add(a, b):
        if a is None and b is None:
            return None
        return (a or 0) + (b or 0)

    return {
        "input_tokens":        _add(stats1.get("input_tokens"),  stats2.get("input_tokens")),
        "output_tokens":       _add(stats1.get("output_tokens"), stats2.get("output_tokens")),
        "reasoning_tokens":    _add(stats1.get("reasoning_tokens"), stats2.get("reasoning_tokens")),
        "tokens_per_second":   stats2.get("tokens_per_second") or stats1.get("tokens_per_second"),
        "time_to_first_token": stats1.get("time_to_first_token"),
        "total_duration":      _add(stats1.get("total_duration"), stats2.get("total_duration")),
    }


def _db_analyze_write(
    doc_id: int,
    original_filename: str,
    batch_id: int | None,
    ai_model_name: str,
    page_count: int,
    extracted_fields: dict,
    order_positions: list,
    raw_response: str,
    ki_stats: dict | None = None,
    document_type_id: int | None = None,
) -> None:
    """
    Speichert Extraktionsergebnisse und setzt den Dokumentenstatus.

    Verwendet ZWEI getrennte Sessions:
    - Phase 1: save_extraction (InvoiceExtraction, OrderPosition, TokenCount)
    - Phase 2: Document-Metadaten (page_count, document_type, status)

    Dadurch beeinflusst der Session-Zustand aus Phase 1 (mehrere Commits/Rollbacks
    innerhalb von save_extraction) nicht die kritische Status-Aktualisierung in Phase 2.
    """
    # ── Phase 1: Extraktion speichern ────────────────────────────────────────
    db1 = SessionLocal()
    extraction_ok = False
    try:
        crud.document.save_extraction(
            db=db1,
            doc_id=doc_id,
            extracted_data=extracted_fields,
            positions=order_positions,
            raw_response=raw_response,
            ki_stats=ki_stats,
        )
        extraction_ok = True
    except Exception as exc:
        logger.exception("Phase 4 (Extraktion) DB-Fehler bei Dokument #%d: %s", doc_id, exc)
        try:
            db1.rollback()
        except Exception:
            pass
    finally:
        db1.close()

    # ── Phase 2: Dokument-Metadaten + Status (frische Session) ───────────────
    is_ki_error = any(raw_response.startswith(p) for p in (
        "KI überlastet:", "KI-Fehler:", "KI-Timeout",
        "KI-Verbindungsfehler", "Unerwarteter KI-Fehler",
    ))
    final_status = "error" if (is_ki_error or not extraction_ok) else "done"

    from app.models.document import Document as _DocModel
    db2 = SessionLocal()
    try:
        _doc = db2.get(_DocModel, doc_id)
        if _doc is not None:
            if page_count > 0:
                _doc.page_count = page_count
            if document_type_id is not None:
                _doc.document_type = document_type_id
            _doc.status = final_status
            db2.commit()

        if final_status == "done":
            logger.info("Dokument #%d erfolgreich analysiert (%d Seiten)", doc_id, page_count)
        else:
            logger.warning("Dokument #%d: Status=%s — %s", doc_id, final_status, raw_response[:120])

    except Exception as exc:
        logger.exception("Phase 4 (Status) DB-Fehler bei Dokument #%d: %s", doc_id, exc)
        try:
            db2.rollback()
        except Exception:
            pass
        # Letzter Ausweg: eigene Session in _set_error
        _set_error(doc_id, f"Speicherfehler: {exc}")
    finally:
        db2.close()


async def _run_analysis(
    document_ids: list[int],
    ai_config_id: int,
    system_prompt_text: str | None,
) -> None:
    logger.info("KI-Analyse gestartet: %d Dokument(e)", len(document_ids))
    for doc_id in document_ids:
        try:
            await _analyze_single(doc_id, ai_config_id, system_prompt_text)
        except Exception as exc:
            logger.exception("Unbehandelter Fehler bei Dokument #%d: %s", doc_id, exc)
            await asyncio.to_thread(_set_error, doc_id, f"Unerwarteter Fehler: {exc}")
    logger.info("KI-Analyse abgeschlossen (%d Dokumente)", len(document_ids))


# Prefixe die auf einen nicht erreichbaren KI-Endpunkt hinweisen
_AI_CONN_ERROR_PREFIXES = (
    "KI-Verbindungsfehler",
    "KI-Timeout",
    "KI überlastet:",
)


def _is_ai_conn_error(raw: str | None) -> bool:
    return bool(raw and any(raw.startswith(p) for p in _AI_CONN_ERROR_PREFIXES))


async def _analyze_single(
    doc_id: int,
    ai_config_id: int,
    system_prompt_text: str | None,
) -> str:
    """
    Führt die KI-Analyse für ein einzelnes Dokument durch.

    Rückgabewert:
      "ok"              — Analyse abgeschlossen (auch bei Dokument-Fehlern)
      "ai_unavailable"  — KI nicht erreichbar (Verbindung/Timeout/Überlast)
    """
    data = await asyncio.to_thread(_db_analyze_read, doc_id, ai_config_id)
    if data is None:
        return "ok"

    pdf_path: Path = data["pdf_path"]
    original_filename: str = data["original_filename"]
    batch_id: int | None = data["batch_id"]
    doc_type_prompt_text: str | None = data["doc_type_prompt_text"]
    document_types: list[dict] = data["document_types"]

    logger.info("Rendere PDF für Dokument #%d: %s", doc_id, pdf_path.name)
    try:
        images_b64: list = await _run_ki_io(
            pdf_service.pdf_to_base64_images,
            pdf_path,
            data["img_dpi"],
            data["img_format"],
            data["img_quality"],
        )
    except Exception as exc:
        logger.error("Fehler beim Rendern von #%d: %s", doc_id, exc)
        _set_error(doc_id, f"PDF-Rendering-Fehler: {exc}")
        return "ok"

    if not images_b64:
        _set_error(doc_id, "PDF konnte nicht gerendert werden")
        return "ok"

    page_count = len(images_b64)

    class _ConfigProxy:
        def __init__(self):
            self.api_url = data["ai_api_url"]
            self.api_key = data["ai_api_key"]
            self.model_name = data["ai_model_name"]
            self.max_tokens = data["ai_max_tokens"]
            self.temperature = data["ai_temperature"]
            self.reasoning = data["ai_reasoning"]
            self.endpoint_type = data["ai_endpoint_type"]

    config_proxy = _ConfigProxy()
    existing_document_type: int = data.get("existing_document_type", 0)
    document_type_id: int | None = None

    # ── Pfad A: Typ bereits bekannt (> 0) → Erkennung überspringen ──────────
    if existing_document_type > 1:
        # Kein Eingangsrechnung-Typ → direkt abschließen ohne Extraktion
        type_name = next(
            (t["name"] for t in document_types if t["id"] == existing_document_type),
            "Unbekannt",
        )
        logger.info(
            "Dokument #%d: Typ bereits bekannt (%d – %s) — überspringe KI-Erkennung",
            doc_id, existing_document_type, type_name,
        )
        del images_b64
        await asyncio.to_thread(
            _db_type_only_finish,
            doc_id, existing_document_type, type_name, page_count, batch_id, original_filename, None,
        )
        return "ok"

    # ── Pfad B: Typ unbekannt → Erkennung durchführen (wenn Prompt vorhanden) ─
    if existing_document_type == 0 and doc_type_prompt_text and document_types:
        logger.info("Starte Dokumententyp-Erkennung für #%d (%d Seite(n))", doc_id, page_count)
        type_id, type_name, _type_raw, type_stats = await asyncio.to_thread(
            ai_service.detect_document_type,
            images_b64,
            config_proxy,
            document_types,
            doc_type_prompt_text,
        )

        # KI nicht erreichbar → sofort abbrechen (kein Dokument-Fehler setzen,
        # damit die Aufgabe von einem anderen Worker erneut versucht werden kann)
        if _is_ai_conn_error(_type_raw):
            logger.warning("Dokument #%d: KI-Verbindungsfehler bei Typ-Erkennung", doc_id)
            _set_error(doc_id, f"KI nicht erreichbar: {_type_raw[:200]}")
            return "ai_unavailable"

        document_type_id = type_id
        await asyncio.to_thread(_db_save_document_type, doc_id, type_id)

        if type_id != 1:
            del images_b64
            await asyncio.to_thread(
                _db_type_only_finish,
                doc_id, type_id, type_name, page_count, batch_id, original_filename, type_stats,
            )
            return "ok"

        logger.info("Dokument #%d: Eingangsrechnung erkannt — starte Extraktion", doc_id)
        extracted_fields, order_positions, raw_response, inv_stats = await asyncio.to_thread(
            ai_service.extract_invoice_data,
            images_b64,
            config_proxy,
            system_prompt_text,
        )
        del images_b64

        if _is_ai_conn_error(raw_response):
            logger.warning("Dokument #%d: KI-Verbindungsfehler bei Extraktion", doc_id)
            _set_error(doc_id, f"KI nicht erreichbar: {raw_response[:200]}")
            return "ai_unavailable"

        ki_stats = _merge_ki_stats(type_stats, inv_stats)

    else:
        # ── Pfad C: Typ bekannt (1 = Eingangsrechnung) ODER kein Typ-Prompt ──
        # → Extraktion direkt, ohne vorherige Typ-Erkennung
        if existing_document_type == 1:
            logger.info(
                "Dokument #%d: Typ bereits bekannt (Eingangsrechnung) — Extraktion direkt",
                doc_id,
            )
            document_type_id = 1
        else:
            logger.info("Starte KI-Extraktion für Dokument #%d (%d Seite(n))", doc_id, page_count)

        extracted_fields, order_positions, raw_response, ki_stats = await asyncio.to_thread(
            ai_service.extract_invoice_data,
            images_b64,
            config_proxy,
            system_prompt_text,
        )
        del images_b64

        if _is_ai_conn_error(raw_response):
            logger.warning("Dokument #%d: KI-Verbindungsfehler bei Extraktion", doc_id)
            _set_error(doc_id, f"KI nicht erreichbar: {raw_response[:200]}")
            return "ai_unavailable"

    await asyncio.to_thread(
        _db_analyze_write,
        doc_id, original_filename, batch_id, data["ai_model_name"],
        page_count, extracted_fields, order_positions, raw_response, ki_stats, document_type_id,
    )
    return "ok"


@router.delete("/{doc_id}", response_model=DocumentDetail)
def soft_delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = crud.document.soft_delete(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return crud.document.get_by_id_with_details(db, doc_id)


@router.post("/{doc_id}/restore", response_model=DocumentDetail)
def restore_document(doc_id: int, db: Session = Depends(get_db)):
    doc = crud.document.restore(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return crud.document.get_by_id_with_details(db, doc_id)


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = crud.document.get_by_id_with_details(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return doc


@router.get("/{doc_id}/preview")
def preview_document(doc_id: int, db: Session = Depends(get_db)):
    from sqlalchemy.orm import joinedload as _jl
    from app.models.document import Document as _DocModel
    doc = (
        db.query(_DocModel)
        .options(_jl(_DocModel.batch))
        .filter(_DocModel.id == doc_id)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    if not doc.stored_filename:
        raise HTTPException(status_code=404, detail="PDF-Datei noch nicht verfügbar.")

    storage_path = doc.batch.storage_folder_path if doc.batch else ""
    pdf_path = Path(storage_path) / doc.stored_filename

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF-Datei nicht auf dem Server gefunden.")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=doc.original_filename,
        content_disposition_type="inline",
    )


@router.patch("/{doc_id}/comment", response_model=DocumentDetail)
def update_document_comment(
    doc_id: int,
    payload: DocumentCommentUpdate,
    db: Session = Depends(get_db),
):
    doc = crud.document.update_comment(db, doc_id, payload.comment)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return crud.document.get_by_id_with_details(db, doc_id)
