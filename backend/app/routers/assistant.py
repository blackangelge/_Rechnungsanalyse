"""
KI-Assistent (eigene Seite /assistent): gespeicherte Chats, Bilder, Token je Chat, Werkzeuge mit Freigabe.

  GET    /api/assistant/models                              — KI-Konfigurationen
  GET    /api/assistant/chats                               — Chats (neueste zuerst, ohne Nachrichten)
  POST   /api/assistant/chats                               — neuen Chat anlegen
  GET    /api/assistant/chats/{id}                          — Chat mit Nachrichten
  PATCH  /api/assistant/chats/{id}                          — Titel / KI-Konfiguration
  DELETE /api/assistant/chats/{id}
  POST   /api/assistant/chats/{id}/restart                  — Nachrichten und Token zurücksetzen
  POST   /api/assistant/chats/{id}/messages                 — Nachricht (+ Bilder) senden, Antwort der KI
  POST   /api/assistant/chats/{id}/messages/{mid}/proposals/{pid}/apply|dismiss
"""

import asyncio
import copy
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import crud
from app.database import get_db
from app.models.assistant_chat import AssistantChat, AssistantMessage
from app.services import assistant_service, assistant_tools as at, change_log, db_tools
from app.services.query_guard import clear_all_caches

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["Assistent"])

MAX_IMAGES = 6
MAX_IMAGE_CHARS = 8_000_000          # ≈ 6 MB Bild als Base64-Data-URL


def _chat(c: AssistantChat, with_messages: bool = False) -> dict:
    out = {
        "id": c.id, "title": c.title, "ai_config_id": c.ai_config_id,
        "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }
    if with_messages:
        out["messages"] = [_msg(m) for m in c.messages]
    return out


def _msg(m: AssistantMessage) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "images": m.images or [], "steps": m.steps or [],
            "proposals": m.proposals or [], "usage": m.usage, "created_at": m.created_at}


def _get(db: Session, chat_id: int) -> AssistantChat:
    c = db.get(AssistantChat, chat_id)
    if c is None:
        raise HTTPException(404, "Chat nicht gefunden")
    return c


@router.get("/models")
def models(db: Session = Depends(get_db)):
    default = crud.ai_config.get_default(db)
    items = crud.ai_config.get_all(db)
    return [{"id": c.id, "name": c.name, "model_name": c.model_name, "active": bool(c.active), "default": bool(default and c.id == default.id)}
            for c in sorted(items, key=lambda c: (not (default and c.id == default.id), not c.active, c.id))]


@router.get("/chats")
def list_chats(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(AssistantChat).order_by(AssistantChat.updated_at.desc(), AssistantChat.id.desc()).limit(min(max(limit, 1), 500)).all()
    return [_chat(c) for c in rows]


class ChatCreate(BaseModel):
    title: str | None = None
    ai_config_id: int | None = None


@router.post("/chats", status_code=201)
def create_chat(body: ChatCreate, db: Session = Depends(get_db)):
    c = AssistantChat(title=(body.title or "Neuer Chat").strip()[:200], ai_config_id=body.ai_config_id)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _chat(c, True)


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, db: Session = Depends(get_db)):
    return _chat(_get(db, chat_id), True)


class ChatPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    ai_config_id: int | None = None


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: int, body: ChatPatch, db: Session = Depends(get_db)):
    c = _get(db, chat_id)
    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"]:
        c.title = data["title"].strip()
    if "ai_config_id" in data:
        c.ai_config_id = data["ai_config_id"]
    db.commit()
    return _chat(c)


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, chat_id))
    db.commit()


@router.post("/chats/{chat_id}/restart")
def restart_chat(chat_id: int, db: Session = Depends(get_db)):
    c = _get(db, chat_id)
    for m in list(c.messages):
        db.delete(m)
    c.input_tokens = c.output_tokens = 0
    db.commit()
    db.refresh(c)
    return _chat(c, True)


class MessageIn(BaseModel):
    content: str = ""
    images: list[str] = Field(default_factory=list)
    ai_config_id: int | None = None


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: int, body: MessageIn, db: Session = Depends(get_db)):
    c = _get(db, chat_id)
    text = body.content.strip()
    if not text and not body.images:
        raise HTTPException(400, "Nachricht ist leer.")
    if len(body.images) > MAX_IMAGES:
        raise HTTPException(400, f"Höchstens {MAX_IMAGES} Bilder je Nachricht.")
    for img in body.images:
        if not img.startswith("data:image/") or len(img) > MAX_IMAGE_CHARS:
            raise HTTPException(400, "Bilder müssen als Bild-Datei (PNG/JPEG/WebP …, höchstens ca. 6 MB) angehängt werden.")
    if body.ai_config_id is not None:
        c.ai_config_id = body.ai_config_id
    config = crud.ai_config.get_by_id(db, c.ai_config_id) if c.ai_config_id else crud.ai_config.get_default(db)
    if config is None:
        raise HTTPException(409, "Keine KI-Konfiguration vorhanden/aktiv — bitte unter Einstellungen → KI-Einstellungen anlegen.")

    first = not c.messages
    user = AssistantMessage(chat_id=c.id, role="user", content=text, images=body.images or None)
    db.add(user)
    if first and c.title == "Neuer Chat":
        c.title = (text or "Bild-Anfrage").replace("\n", " ")[:60]
    db.commit()
    db.refresh(c)

    history = [{"role": m.role, "content": m.content, "images": m.images, "proposals": m.proposals} for m in c.messages]
    try:
        result = await asyncio.to_thread(assistant_service.run_chat, db, config, history)
    except RuntimeError as exc:                         # KI nicht erreichbar / Fehlerstatus
        db.rollback()
        raise HTTPException(502, str(exc))
    usage = result["usage"]
    reply = AssistantMessage(chat_id=c.id, role="assistant", content=result["answer"], steps=result["steps"] or None,
                             proposals=result["proposals"] or None, usage=usage)
    db.add(reply)
    c.input_tokens = (c.input_tokens or 0) + int(usage.get("input_tokens") or 0)
    c.output_tokens = (c.output_tokens or 0) + int(usage.get("output_tokens") or 0)
    db.commit()
    db.refresh(c)
    db.refresh(reply)
    logger.info("Assistent: Chat #%d, %d Werkzeug-Aufruf(e), %d Vorschlag/Vorschläge, %s Token ein / %s aus",
                c.id, len(result["steps"]), len(result["proposals"]), usage["input_tokens"], usage["output_tokens"])
    return {"chat": _chat(c), "user_message_id": user.id, "message": _msg(reply)}


# ─── Vorschläge ──────────────────────────────────────────────────────────────

class ApplyIn(BaseModel):
    workflow_id: int | None = None          # nur für „erneute KI-Analyse“


def _proposal(db: Session, chat_id: int, message_id: int, pid: str) -> tuple[AssistantMessage, dict]:
    m = db.get(AssistantMessage, message_id)
    if m is None or m.chat_id != chat_id:
        raise HTTPException(404, "Nachricht nicht gefunden")
    for p in m.proposals or []:
        if p.get("id") == pid:
            return m, p
    raise HTTPException(404, "Vorschlag nicht gefunden")


def _save(db: Session, m: AssistantMessage, mutate) -> None:
    proposals = copy.deepcopy(m.proposals or [])
    mutate(proposals)
    m.proposals = proposals
    flag_modified(m, "proposals")
    db.commit()


@router.post("/chats/{chat_id}/messages/{message_id}/proposals/{pid}/dismiss")
def dismiss(chat_id: int, message_id: int, pid: str, db: Session = Depends(get_db)):
    m, p = _proposal(db, chat_id, message_id, pid)
    if p.get("status") != "pending":
        raise HTTPException(409, "Der Vorschlag ist nicht mehr offen.")
    _save(db, m, lambda ps: [x.update(status="dismissed") for x in ps if x["id"] == pid])
    return _msg(m)


@router.post("/chats/{chat_id}/messages/{message_id}/proposals/{pid}/apply")
async def apply(chat_id: int, message_id: int, pid: str, body: ApplyIn | None = None, db: Session = Depends(get_db)):
    """Gibt EINEN Vorschlag frei und führt ihn aus (dieselben Prüfungen wie in der Oberfläche)."""
    m, p = _proposal(db, chat_id, message_id, pid)
    if p.get("status") != "pending":
        raise HTTPException(409, "Der Vorschlag ist nicht mehr offen.")
    tool, args = p["tool"], dict(p.get("args") or {})
    try:
        if tool == "propose_update":
            try:
                result = db_tools.update_row(db, args["table"], args["id"], args["changes"])
            except db_tools.DbToolError as exc:
                raise at.ToolError(str(exc)) from exc
            except LookupError as exc:
                raise at.ToolError(str(exc).strip("'\"")) from exc
            change_log.log_manual_update(db, "assistant", args["table"], args["id"], result.pop("before"), result["row"], label=p.get("reason") or None)
            db.commit()
            clear_all_caches()
            outcome = {"changed": result["changed"]}
        elif tool == "propose_reanalysis":
            if not body or not body.workflow_id:
                raise at.ToolError("Bitte einen Workflow für die Analyse wählen.")
            from app.routers.documents import EnqueueRequest, enqueue_documents
            valid = assistant_service.legacy.filter_reanalysis_ids(db, list(args.get("document_ids") or []))
            if not valid:
                raise at.ToolError("Keiner der Belege kommt (mehr) in Frage (gelöscht oder in Verarbeitung).")
            res = enqueue_documents(EnqueueRequest(document_ids=valid, workflow_id=body.workflow_id,
                                                   skip_type_detection=bool(args.get("skip_type_detection"))), db)
            outcome = {"enqueued": res.enqueued, "message": res.message}
        else:
            outcome = await at.execute_write(db, tool, args)
            clear_all_caches()
    except at.ToolError as exc:
        logger.warning("Assistent: Vorschlag %s (%s) fehlgeschlagen: %s", pid, tool, exc)
        _save(db, m, lambda ps: [x.update(status="failed", error=str(exc)) for x in ps if x["id"] == pid])
        raise HTTPException(400, str(exc))
    logger.warning("Assistent: Vorschlag FREIGEGEBEN %s %s → %s", tool, args, outcome)
    _save(db, m, lambda ps: [x.update(status="applied", result=outcome, error=None) for x in ps if x["id"] == pid])
    return _msg(m)
