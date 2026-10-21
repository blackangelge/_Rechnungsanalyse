"""
KI-Assistent für die Datenbank (Chat). Liest und analysiert selbstständig (nur SELECT), Änderungen kommen als
VORSCHLÄGE zurück und werden erst durch `POST /apply` (Klick des Nutzers) geschrieben.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.services import change_log, db_assistant_service, db_tools
from app.services.query_guard import clear_all_caches

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/db-assistant", tags=["Daten-Assistent"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1)
    ai_config_id: int | None = None
    allow_proposals: bool = True


class ApplyRequest(BaseModel):
    table: str
    id: Any
    changes: dict[str, Any]
    reason: str = ""


class ReanalysisRequest(BaseModel):
    document_ids: list[int] = Field(..., min_length=1)
    workflow_id: int | None = None
    reason: str = ""


@router.get("/models")
def models(db: Session = Depends(get_db)):
    """KI-Konfigurationen, die der Assistent nutzen kann (die als Standard aktive zuerst)."""
    default = crud.ai_config.get_default(db)
    items = crud.ai_config.get_all(db)
    return [
        {"id": c.id, "name": c.name, "model_name": c.model_name, "active": bool(c.active), "default": bool(default and c.id == default.id)}
        for c in sorted(items, key=lambda c: (not (default and c.id == default.id), not c.active, c.id))
    ]


@router.post("/chat")
def chat(body: ChatRequest, db: Session = Depends(get_db)):
    config = crud.ai_config.get_by_id(db, body.ai_config_id) if body.ai_config_id else crud.ai_config.get_default(db)
    if config is None:
        raise HTTPException(status_code=409, detail="Keine KI-Konfiguration vorhanden/aktiv — bitte unter Einstellungen → KI-Einstellungen anlegen.")
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="Die letzte Nachricht muss vom Nutzer sein.")
    try:
        result = db_assistant_service.run_chat(
            db, config, [m.model_dump() for m in body.messages], allow_proposals=body.allow_proposals
        )
    except RuntimeError as exc:                         # KI nicht erreichbar / Fehlerstatus
        raise HTTPException(status_code=502, detail=str(exc))
    logger.info("Daten-Assistent: %d Abfrage(n), %d Vorschlag/Vorschläge, %s Token ein / %s aus",
                len(result["steps"]), len(result["proposals"]), result["usage"]["input_tokens"], result["usage"]["output_tokens"])
    return result


@router.post("/apply")
def apply(body: ApplyRequest, db: Session = Depends(get_db)):
    """Gibt EINEN Änderungsvorschlag frei und schreibt ihn (gleiche Prüfungen wie im Daten-Browser)."""
    try:
        result = db_tools.update_row(db, body.table, body.id, body.changes)
    except db_tools.DbToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\""))
    change_log.log_manual_update(db, "assistant", body.table, body.id, result.pop("before"), result["row"], label=body.reason or None)
    logger.warning("Daten-Assistent: Vorschlag FREIGEGEBEN %s #%s: %s (Begründung: %s)", body.table, body.id, result["changed"], body.reason)
    clear_all_caches()
    return result


@router.post("/apply-reanalysis")
def apply_reanalysis(body: ReanalysisRequest, db: Session = Depends(get_db)):
    """
    Gibt einen Vorschlag „erneut zur KI-Analyse senden“ frei: reiht die Belege in die Worker-Warteschlange ein
    (wie „KI-Analyse starten“ auf der Belege-Seite). Vorher werden die IDs erneut geprüft (vorhanden, nicht gelöscht,
    nicht gerade in Verarbeitung); Belege mit wartendem Task werden übersprungen. Höchstens MAX_REANALYSIS je Freigabe.
    """
    from app.routers.documents import EnqueueRequest, enqueue_documents

    ids = list(dict.fromkeys(body.document_ids))
    if len(ids) > db_assistant_service.MAX_REANALYSIS:
        raise HTTPException(status_code=400, detail=f"Höchstens {db_assistant_service.MAX_REANALYSIS} Belege je Freigabe.")
    valid = db_assistant_service.filter_reanalysis_ids(db, ids)
    if not valid:
        raise HTTPException(status_code=400, detail="Keiner der Belege kommt in Frage (nicht vorhanden, gelöscht oder gerade in Verarbeitung).")
    result = enqueue_documents(EnqueueRequest(document_ids=valid, workflow_id=body.workflow_id), db)
    logger.warning("Daten-Assistent: erneute KI-Analyse FREIGEGEBEN für %d Beleg(e), %d eingereiht (Begründung: %s)",
                   len(valid), result.enqueued, body.reason)
    return {"requested": len(ids), "eligible": len(valid), "enqueued": result.enqueued, "message": result.message}
