"""
Router für die PDF-Bearbeitung von Dokumenten (Seiten entfernen, Versionen).

Endpunkte (alle unter /api/documents):
  GET  /{id}/versions                  — Versionsverlauf (inkl. Original)
  GET  /{id}/versions/{n}/preview      — PDF einer bestimmten Version
  GET  /{id}/pages/{page}/thumbnail    — Seiten-Vorschaubild (PNG) für die Seitenauswahl
  POST /{id}/pages/remove              — Seiten entfernen / nur bestimmte behalten → neue Version
  POST /{id}/versions/{n}/restore      — frühere Version als neue Version wiederherstellen
  POST /auto-trim                      — mehrere Dokumente auf die ersten N Seiten kürzen
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.document import Document
from app.schemas.document_version import (
    AutoTrimRequest, AutoTrimResult, DocumentVersionRead, PageEditRequest,
)
from app.services import pdf_edit_service, pdf_service
from app.services.pdf_edit_service import PdfEditError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["PDF-Bearbeitung"])


def _get_doc(db: Session, doc_id: int, *, for_edit: bool = False) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    if for_edit and doc.status == "processing":
        raise HTTPException(
            status_code=409,
            detail="Das Dokument wird gerade von der KI analysiert — bitte nach Abschluss bearbeiten.",
        )
    return doc


def _to_read(version, current: int) -> DocumentVersionRead:
    out = DocumentVersionRead.model_validate(version)
    out.is_current = version.version_number == current
    return out


# ─── GET /{id}/versions ──────────────────────────────────────────────────────

@router.get("/{doc_id}/versions", response_model=list[DocumentVersionRead])
def list_versions(doc_id: int, db: Session = Depends(get_db)):
    """
    Versionsverlauf, älteste zuerst. Wurde das Dokument noch nie bearbeitet,
    wird das Original als (noch nicht gespeicherte) Version 1 ausgegeben.
    """
    doc = _get_doc(db, doc_id)
    versions = pdf_edit_service.list_versions(db, doc_id)
    if not versions:
        # Der Import liest die Seitenzahl nicht (page_count bleibt 0 bis zur KI-Analyse) —
        # für die Seitenauswahl wird sie hier aus der Datei nachgetragen.
        if doc.page_count == 0 and doc.stored_filename:
            try:
                path = pdf_edit_service.current_path(doc)
                doc.page_count = pdf_service.get_page_count(path)
                doc.file_size_bytes = path.stat().st_size
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning("Seitenzahl von #%d nicht lesbar: %s", doc_id, exc)
        return [DocumentVersionRead(
            version_number=1, page_count=doc.page_count, file_size_bytes=doc.file_size_bytes,
            source="original", description="Original (Import)", is_current=True,
        )]
    return [_to_read(v, doc.pdf_version) for v in versions]


# ─── GET /{id}/versions/{n}/preview ──────────────────────────────────────────

@router.get("/{doc_id}/versions/{number}/preview")
def preview_version(doc_id: int, number: int, db: Session = Depends(get_db)):
    """Streamt die PDF einer bestimmten Version (für Ansehen im Browser)."""
    doc = _get_doc(db, doc_id)
    version = pdf_edit_service.get_version(db, doc_id, number)
    if version is None:
        if number == 1 and doc.pdf_version == 1 and doc.stored_filename:
            path = pdf_edit_service.current_path(doc)   # unbearbeitetes Original
        else:
            raise HTTPException(status_code=404, detail=f"Version {number} nicht gefunden")
    else:
        path = pdf_edit_service.version_path(doc, version)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF-Datei nicht auf dem Server gefunden")
    return FileResponse(
        str(path), media_type="application/pdf",
        filename=f"v{number}_{doc.original_filename}", content_disposition_type="inline",
    )


# ─── GET /{id}/pages/{page}/thumbnail ────────────────────────────────────────

@router.get("/{doc_id}/pages/{page}/thumbnail")
def page_thumbnail(
    doc_id: int,
    page: int,
    version: int | None = Query(default=None, description="Version (Standard: aktuelle)"),
    width: int = Query(default=240, ge=60, le=800),
    db: Session = Depends(get_db),
):
    """PNG-Vorschaubild einer Seite (1-basiert). Wird vom Browser dauerhaft gecacht."""
    doc = _get_doc(db, doc_id)
    number = version or doc.pdf_version
    stored = pdf_edit_service.get_version(db, doc_id, number)
    try:
        path = (
            pdf_edit_service.version_path(doc, stored)
            if stored is not None else pdf_edit_service.current_path(doc)
        )
    except PdfEditError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF-Datei nicht auf dem Server gefunden")
    try:
        png = pdf_service.render_page_thumbnail(path, page, width)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.warning("Thumbnail #%d Seite %d fehlgeschlagen: %s", doc_id, page, exc)
        raise HTTPException(status_code=500, detail="Vorschaubild konnte nicht erzeugt werden")
    # Versionen sind unveränderlich → lange cachen (Version steckt in der URL)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


# ─── POST /{id}/pages/remove ─────────────────────────────────────────────────

@router.post("/{doc_id}/pages/remove", response_model=DocumentVersionRead)
def remove_pages(doc_id: int, payload: PageEditRequest, db: Session = Depends(get_db)):
    """
    Entfernt Seiten (`remove_pages`) oder behält nur die angegebenen (`keep_pages`).
    Seitenzahlen sind 1-basiert und beziehen sich auf die aktuelle Version.
    Es entsteht eine neue Version; das Original bleibt unverändert erhalten.
    Eine bestehende KI-Extraktion bleibt bestehen — danach ggf. neu analysieren.
    """
    doc = _get_doc(db, doc_id, for_edit=True)
    try:
        version = pdf_edit_service.remove_pages(
            db, doc, remove=payload.remove_pages, keep=payload.keep_pages,
            source="manual", description=payload.note or None,
        )
    except PdfEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_read(version, doc.pdf_version)


# ─── POST /{id}/versions/{n}/restore ─────────────────────────────────────────

@router.post("/{doc_id}/versions/{number}/restore", response_model=DocumentVersionRead)
def restore_version(doc_id: int, number: int, db: Session = Depends(get_db)):
    """Stellt eine frühere Version als NEUE aktuelle Version wieder her."""
    doc = _get_doc(db, doc_id, for_edit=True)
    try:
        version = pdf_edit_service.restore_version(db, doc, number)
    except PdfEditError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _to_read(version, doc.pdf_version)


# ─── POST /auto-trim ─────────────────────────────────────────────────────────

@router.post("/auto-trim", response_model=AutoTrimResult)
def auto_trim_documents(payload: AutoTrimRequest, db: Session = Depends(get_db)):
    """
    Automatisches Kürzen: Dokumente mit mehr als `max_pages` Seiten werden auf die
    ersten `max_pages` Seiten gekürzt (jeweils als neue Version).
    """
    result = AutoTrimResult()
    for doc_id in payload.document_ids:
        doc = db.get(Document, doc_id)
        if doc is None:
            result.errors[doc_id] = "Dokument nicht gefunden"
            continue
        if doc.status == "processing":
            result.errors[doc_id] = "wird gerade analysiert"
            continue
        try:
            version = pdf_edit_service.auto_trim(db, doc, payload.max_pages)
        except PdfEditError as exc:
            result.errors[doc_id] = str(exc)
            continue
        except Exception as exc:  # defekte PDFs o.ä. dürfen den Rest nicht stoppen
            logger.exception("Auto-Kürzen #%d fehlgeschlagen", doc_id)
            db.rollback()
            result.errors[doc_id] = f"Unerwarteter Fehler: {exc}"
            continue
        (result.trimmed if version else result.unchanged).append(doc_id)
    return result
