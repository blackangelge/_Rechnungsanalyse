"""
Router: freie KI-Frage an Belege mit Verlauf.

  GET    /api/ai-questions/fields        — Felder, die mit der Antwort aktualisiert werden können
  GET    /api/ai-questions               — Verlauf der gestellten Fragen (zuletzt benutzt zuerst)
  DELETE /api/ai-questions/{id}          — Frage aus dem Verlauf entfernen
  POST   /api/ai-questions/ask           — Frage zu EINEM Beleg (sofortige Antwort)
  POST   /api/ai-questions/run           — dieselbe Frage zu mehreren Belegen (Hintergrundlauf, nacheinander)
  GET    /api/ai-questions/state         — Fortschritt/Ergebnis des Mehrfach-Laufs
  POST   /api/ai-questions/cancel        — Lauf abbrechen
  GET    /api/ai-questions/answers       — bisherige Antworten zu einem Beleg (?document_id=)
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ai_query import AIQuery, DocumentAnswer
from app.services import ai_question_service as svc

router = APIRouter(prefix="/api/ai-questions", tags=["KI-Fragen"])


class AskRequest(BaseModel):
    document_id: int
    question: str = Field(min_length=3, max_length=2000)
    field_key: str | None = None          # Zielfeld am Beleg (None = nur Antwort speichern)
    ai_config_id: int | None = None
    apply: bool = True                    # Beleg mit der Antwort aktualisieren (nur mit Zielfeld)


class RunRequest(BaseModel):
    document_ids: list[int] = Field(min_length=1)
    question: str = Field(min_length=3, max_length=2000)
    field_key: str | None = None
    ai_config_id: int | None = None
    apply: bool = True


def _check(question: str, field_key: str | None) -> str:
    if field_key is not None and field_key not in svc.FIELDS:
        raise HTTPException(status_code=422, detail=f"Unbekanntes Feld: {field_key}")
    return " ".join(question.split())


@router.get("/fields")
def fields():
    return svc.field_catalog()


@router.get("")
def history(limit: int = Query(30, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.query(AIQuery).order_by(AIQuery.last_used_at.desc(), AIQuery.id.desc()).limit(limit).all()
    return [{"id": q.id, "question": q.question, "field_key": q.field_key, "use_count": q.use_count,
             "last_used_at": q.last_used_at.isoformat() if q.last_used_at else None} for q in rows]


@router.delete("/{query_id}", status_code=204)
def delete_query(query_id: int, db: Session = Depends(get_db)):
    q = db.get(AIQuery, query_id)
    if q is None:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    db.delete(q)
    db.commit()


@router.post("/ask")
async def ask(body: AskRequest, db: Session = Depends(get_db)):
    question = _check(body.question, body.field_key)
    q = svc.record_query(db, question, body.field_key)
    try:
        return await asyncio.to_thread(svc.ask_one, body.document_id, question, body.field_key, body.ai_config_id, body.apply, q.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502 if str(exc).startswith(("KI-", "Unerwarteter KI")) else 409, detail=str(exc))


@router.post("/run")
def run(body: RunRequest, db: Session = Depends(get_db)):
    question = _check(body.question, body.field_key)
    q = svc.record_query(db, question, body.field_key)
    try:
        return svc.start(body.document_ids, question, body.field_key, body.ai_config_id, body.apply, q.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/state")
def state():
    return svc.get_state()


@router.post("/cancel")
def cancel():
    return {"cancelled": svc.cancel()}


@router.get("/answers")
def answers(document_id: int, db: Session = Depends(get_db)):
    rows = db.query(DocumentAnswer).filter(DocumentAnswer.document_id == document_id).order_by(DocumentAnswer.id.desc()).limit(50).all()
    return [svc.answer_dict(a) for a in rows]
