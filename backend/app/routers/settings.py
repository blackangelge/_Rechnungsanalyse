from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.schemas.image_settings import ImageSettingsRead, ImageSettingsUpdate
from app.schemas.system_prompt import SystemPromptCreate, SystemPromptRead, SystemPromptUpdate

router = APIRouter(prefix="/api/settings", tags=["Einstellungen"])

# Statische Dokumententypen (in DB als Integer gespeichert)
_DOCUMENT_TYPES = [
    {"id": 0,  "name": "Unbekannt"},
    {"id": 1,  "name": "Eingangsrechnung"},
    {"id": 2,  "name": "Ausgangsrechnung"},
    {"id": 3,  "name": "Lieferschein"},
    {"id": 4,  "name": "Bestellbestätigung"},
    {"id": 5,  "name": "Angebot"},
    {"id": 6,  "name": "Gutschrift / Storno"},
    {"id": 7,  "name": "Mahnung"},
    {"id": 8,  "name": "Kontoauszug"},
    {"id": 9,  "name": "Vertrag"},
    {"id": 10, "name": "Lohnabrechnung"},
    {"id": 11, "name": "Steuer- / Behördendokument"},
    {"id": 12, "name": "Reisekostenabrechnung"},
    {"id": 13, "name": "Kassenbon / Quittung"},
    {"id": 14, "name": "Sonstiges kaufmännisches Dokument"},
]

doc_types_router = APIRouter(prefix="/api", tags=["Dokumententypen"])

@doc_types_router.get("/document-types")
def list_document_types():
    """Gibt alle statischen Dokumententypen zurück."""
    return _DOCUMENT_TYPES


@router.get("/paths")
def get_paths():
    return {
        "import_base_path": settings.import_base_path,
        "storage_path": settings.storage_path,
    }


@router.get("/image-conversion", response_model=ImageSettingsRead)
def get_image_settings(db: Session = Depends(get_db)):
    return crud.image_settings.get_or_create(db)


@router.put("/image-conversion", response_model=ImageSettingsRead)
def update_image_settings(payload: ImageSettingsUpdate, db: Session = Depends(get_db)):
    return crud.image_settings.update(db, payload)


# ── Systemprompts ─────────────────────────────────────────────────────────────

@router.get("/system-prompts", response_model=list[SystemPromptRead])
def list_system_prompts(db: Session = Depends(get_db)):
    return crud.system_prompt.get_all(db)


@router.post("/system-prompts", response_model=SystemPromptRead, status_code=201)
def create_system_prompt(payload: SystemPromptCreate, db: Session = Depends(get_db)):
    return crud.system_prompt.create(db, payload)


@router.put("/system-prompts/{prompt_id}", response_model=SystemPromptRead)
def update_system_prompt(prompt_id: int, payload: SystemPromptUpdate, db: Session = Depends(get_db)):
    obj = crud.system_prompt.update(db, prompt_id, payload)
    if obj is None:
        raise HTTPException(status_code=404, detail="Systemprompt nicht gefunden")
    return obj


@router.post("/system-prompts/{prompt_id}/set-default", response_model=SystemPromptRead)
def set_default_system_prompt(prompt_id: int, db: Session = Depends(get_db)):
    obj = crud.system_prompt.set_default(db, prompt_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Systemprompt nicht gefunden")
    return obj


@router.delete("/system-prompts/{prompt_id}", status_code=204)
def delete_system_prompt(prompt_id: int, db: Session = Depends(get_db)):
    if not crud.system_prompt.delete(db, prompt_id):
        raise HTTPException(status_code=404, detail="Systemprompt nicht gefunden")


# ── Backup / Restore ──────────────────────────────────────────────────────────

import json as _json
from datetime import datetime as _dt

from fastapi import File, UploadFile
from fastapi.responses import JSONResponse


@router.get("/backup")
def download_backup(db: Session = Depends(get_db)):
    """Exportiert KI-Konfigurationen, Systemprompts und Bildeinstellungen als JSON."""
    ai_configs = crud.ai_config.get_all(db)
    prompts = crud.system_prompt.get_all(db)
    img = crud.image_settings.get_or_create(db)

    def _obj(o):
        return {c.name: getattr(o, c.name) for c in o.__table__.columns}

    backup = {
        "version": 2,
        "exported_at": _dt.utcnow().isoformat(),
        "ai_configs": [_obj(c) for c in ai_configs],
        "system_prompts": [_obj(p) for p in prompts],
        "image_settings": _obj(img),
    }

    from fastapi.responses import Response
    content = _json.dumps(backup, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=rechnungsanalyse-backup.json"},
    )


@router.post("/restore")
async def upload_restore(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Importiert Einstellungen aus einer Backup-JSON-Datei."""
    try:
        content = await file.read()
        backup = _json.loads(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Ungültige JSON-Datei: {exc}")

    if backup.get("version") not in (1, 2):
        raise HTTPException(status_code=400, detail="Unbekanntes Backup-Format")

    restored = {"ai_configs": 0, "system_prompts": 0}

    from app.models.ai_clients import AIClients
    db.query(AIClients).delete()
    for c in (backup.get("ai_configs") or []):
        c.pop("id", None)
        c.pop("created_at", None)
        c.pop("updated_at", None)
        # v1-Backup-Kompatibilität: is_default → active
        if "is_default" in c:
            c["active"] = c.pop("is_default")
        if "api_url" in c:
            c.pop("api_url")
        db.add(AIClients(**c))
        restored["ai_configs"] += 1

    from app.models.system_prompt import SystemPrompt
    db.query(SystemPrompt).delete()
    for p in (backup.get("system_prompts") or []):
        p.pop("id", None)
        p.pop("created_at", None)
        p.pop("updated_at", None)
        # v1-Backup-Kompatibilität: is_document_type_prompt → type=1, is_default → type=0
        if "is_document_type_prompt" in p:
            p["type"] = 1 if p.pop("is_document_type_prompt") else p.pop("is_default", 0) and 0
        elif "is_default" in p:
            p.pop("is_default")
        db.add(SystemPrompt(**p))
        restored["system_prompts"] += 1

    img_data = backup.get("image_settings")
    if img_data:
        from app.models.image_settings import ImageSettings
        img_data.pop("id", None)
        img_data.pop("created_at", None)
        img_data.pop("updated_at", None)
        db.query(ImageSettings).delete()
        db.add(ImageSettings(**img_data))

    db.commit()
    return {"restored": restored, "message": "Backup erfolgreich wiederhergestellt"}
