"""
Analyse-Historie eines Belegs: frühere KI-Auslesungen ansehen, vergleichen, wiederherstellen.

Wird beim Start jeder erneuten Analyse automatisch befüllt (crud.document.save_used_prompts);
die Anzahl aufbewahrter Stände steht in den Verarbeitungseinstellungen.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.crud.analysis_history import HistoryError
from app.database import get_db
from app.models.document import Document
from app.schemas.analysis_history import AnalysisHistoryDetail, AnalysisHistoryRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["Analyse-Historie"])


def _json_or_none(text):
    try:
        return json.loads(text) if text else None
    except (TypeError, ValueError):
        return None


def _doc_or_404(db: Session, doc_id: int) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return doc


@router.get("/{doc_id}/analysis-history", response_model=list[AnalysisHistoryRead])
def list_history(doc_id: int, db: Session = Depends(get_db)):
    """Frühere Analyse-Stände, neueste zuerst."""
    _doc_or_404(db, doc_id)
    return crud.analysis_history.list_for_document(db, doc_id)


@router.get("/{doc_id}/analysis-history/{history_id}", response_model=AnalysisHistoryDetail)
def get_history(doc_id: int, history_id: int, db: Session = Depends(get_db)):
    _doc_or_404(db, doc_id)
    entry = crud.analysis_history.get(db, doc_id, history_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Analyse-Stand nicht gefunden")
    snap = crud.analysis_history.parsed_snapshot(entry)
    meta = snap.get("meta", {})
    ext = snap.get("extraction", {})
    return {
        **AnalysisHistoryRead.model_validate(entry).model_dump(),
        "extraction": ext,
        "order_positions": snap.get("positions", []),
        "used_system_prompt": meta.get("used_system_prompt"),
        "used_doc_type_prompt": meta.get("used_doc_type_prompt"),
        "used_recheck_prompt": meta.get("used_recheck_prompt"),
        "workflow_run": _json_or_none(meta.get("workflow_trace")),
        "validation": _json_or_none(ext.get("validation_details")),
    }


@router.post("/{doc_id}/analysis-history/{history_id}/restore")
def restore_history(doc_id: int, history_id: int, db: Session = Depends(get_db)):
    """
    Macht einen früheren Stand wieder zum aktuellen. Der bisherige Stand wird dabei
    selbst in die Historie aufgenommen.
    """
    doc = _doc_or_404(db, doc_id)
    if doc.status == "processing":
        raise HTTPException(status_code=409, detail="Beleg wird gerade analysiert")
    try:
        crud.analysis_history.restore(db, doc_id, history_id)
    except HistoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    logger.info("Früherer Analyse-Stand #%d von Dokument #%d wiederhergestellt (%s)",
                history_id, doc_id, doc.original_filename)
    return {"restored": history_id}


@router.delete("/{doc_id}/analysis-history/{history_id}", status_code=204)
def delete_history(doc_id: int, history_id: int, db: Session = Depends(get_db)):
    _doc_or_404(db, doc_id)
    if not crud.analysis_history.delete(db, doc_id, history_id):
        raise HTTPException(status_code=404, detail="Analyse-Stand nicht gefunden")
