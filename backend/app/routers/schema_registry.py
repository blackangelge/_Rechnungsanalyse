"""
Router: verfügbare Bausteine für das dynamische Schema (nur lesend).

  GET /api/schema/normalizers — Normalisierer mit Parameter-Schema (für die Ketten-Oberfläche)
  GET /api/schema/functions   — Systemfunktionen mit Parameter-Schema
  POST /api/schema/normalizers/preview — Kette auf einen Beispielwert anwenden (ohne etwas zu speichern)
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import field_functions, normalizers

router = APIRouter(prefix="/api/schema", tags=["schema"])


class PreviewRequest(BaseModel):
    value: Any = None
    chain: list[dict] = []


@router.get("/normalizers")
def list_normalizers():
    return normalizers.describe()


@router.get("/functions")
def list_functions():
    return field_functions.describe()


@router.post("/normalizers/preview")
def preview_chain(req: PreviewRequest):
    errors = normalizers.validate_chain(req.chain)
    if errors:
        return {"ok": False, "value": None, "errors": errors, "issues": []}
    result = normalizers.apply_chain(req.value, req.chain)
    return {"ok": result.ok, "value": normalizers.to_jsonable(result.value), "errors": [], "issues": result.issues}


# ─── Schema-Definition (Editor) ──────────────────────────────────────────────
#
#  GET    /api/schema/versions                      — alle Versionen (Kurzform)
#  GET    /api/schema/versions/{id}                 — vollständige Definition
#  POST   /api/schema/versions/draft                — Entwurf aus der aktiven (oder angegebenen) Version anlegen
#  POST   /api/schema/versions/{id}/activate|discard, GET …/validate
#  POST   /api/schema/versions/{id}/entities        — Entität
#  DELETE /api/schema/entities/{id}
#  POST   /api/schema/entities/{id}/fields          — Feld;  PATCH|DELETE /api/schema/fields/{id}
#  POST   /api/schema/versions/{id}/relations;  DELETE /api/schema/relations/{id}
from fastapi import Depends, HTTPException  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.crud import schema_definition as sd  # noqa: E402
from app.database import get_db  # noqa: E402
from app.models.schema_def import SchemaEntity, SchemaField, SchemaRelation, SchemaVersion  # noqa: E402


def _field(f: SchemaField) -> dict:
    return {
        "id": f.id, "name": f.name, "display_name": f.display_name, "data_type": f.data_type, "required": f.required,
        "output_format": f.output_format, "description": f.description, "position": f.position, "source": f.source,
        "is_locked": f.is_locked, "enum_values": f.enum_values, "unit_group_id": f.unit_group_id,
        "system_function": f.system_function, "system_params": f.system_params, "normalizers": f.normalizers or [],
    }


def _version(v: SchemaVersion, full: bool = True) -> dict:
    out = {"id": v.id, "version_number": v.version_number, "status": v.status, "comment": v.comment,
           "created_at": v.created_at, "activated_at": v.activated_at,
           "entity_count": len(v.entities), "relation_count": len(v.relations)}
    if full:
        out["entities"] = [{"id": e.id, "name": e.name, "display_name": e.display_name, "description": e.description,
                            "master_table": e.master_table, "match_fields": e.match_fields,
                            "fields": [_field(f) for f in e.fields]} for e in v.entities]
        out["relations"] = [{"id": r.id, "name": r.name, "kind": r.kind, "source_entity_id": r.source_entity_id,
                             "target_entity_id": r.target_entity_id, "description": r.description} for r in v.relations]
        out["problems"] = sd.validate_version(v)
    return out


def _run(db: Session, fn):
    try:
        result = fn()
        db.commit()
        return result
    except sd.SchemaError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


def _ver(db: Session, version_id: int) -> SchemaVersion:
    v = sd.get_version(db, version_id)
    if v is None:
        raise HTTPException(404, "Version nicht gefunden")
    return v


@router.get("/versions")
def list_versions(db: Session = Depends(get_db)):
    return [_version(v, full=False) for v in sd.list_versions(db)]


@router.get("/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db)):
    return _version(_ver(db, version_id))


class DraftIn(BaseModel):
    base_version_id: int | None = None
    comment: str | None = None


@router.post("/versions/draft", status_code=201)
def create_draft(body: DraftIn, db: Session = Depends(get_db)):
    if any(v.status == "draft" for v in sd.list_versions(db)):
        raise HTTPException(409, "Es gibt bereits einen Entwurf — erst aktivieren oder verwerfen")
    base = _ver(db, body.base_version_id) if body.base_version_id else sd.get_active(db)

    def make():
        draft = sd.create_draft_from(db, base, body.comment) if base else sd.create_version(db, body.comment)
        return _version(draft)
    return _run(db, make)


@router.get("/versions/{version_id}/validate")
def validate(version_id: int, db: Session = Depends(get_db)):
    return {"problems": sd.validate_version(_ver(db, version_id))}


@router.post("/versions/{version_id}/activate")
def activate(version_id: int, db: Session = Depends(get_db)):
    v = _ver(db, version_id)
    return _run(db, lambda: _version(sd.activate(db, v)))


@router.delete("/versions/{version_id}", status_code=204)
def discard(version_id: int, db: Session = Depends(get_db)):
    v = _ver(db, version_id)
    _run(db, lambda: sd.discard_draft(db, v))


class EntityIn(BaseModel):
    name: str
    display_name: str
    description: str | None = None
    master_table: str | None = None
    match_fields: list[str] | None = None


@router.post("/versions/{version_id}/entities", status_code=201)
def add_entity(version_id: int, body: EntityIn, db: Session = Depends(get_db)):
    v = _ver(db, version_id)
    _run(db, lambda: sd.add_entity(db, v, **body.model_dump()))
    return _version(v)


@router.delete("/entities/{entity_id}")
def delete_entity(entity_id: int, db: Session = Depends(get_db)):
    e = db.get(SchemaEntity, entity_id)
    if e is None:
        raise HTTPException(404, "Entität nicht gefunden")
    v = e.version
    _run(db, lambda: sd.delete_entity(db, e))
    return _version(v)


class FieldIn(BaseModel):
    name: str
    display_name: str
    data_type: str
    required: bool = False
    output_format: str | None = None
    description: str | None = None
    source: str = "llm"
    enum_values: list[str] | None = None
    unit_group_id: int | None = None
    system_function: str | None = None
    system_params: dict | None = None
    normalizers: list[dict] | None = None


@router.post("/entities/{entity_id}/fields", status_code=201)
def add_field(entity_id: int, body: FieldIn, db: Session = Depends(get_db)):
    e = db.get(SchemaEntity, entity_id)
    if e is None:
        raise HTTPException(404, "Entität nicht gefunden")
    data = body.model_dump()
    _run(db, lambda: sd.add_field(db, e, data.pop("name"), data.pop("display_name"), data.pop("data_type"), **data))
    return _version(e.version)


class FieldPatch(BaseModel):
    name: str | None = None
    display_name: str | None = None
    data_type: str | None = None
    required: bool | None = None
    output_format: str | None = None
    description: str | None = None
    source: str | None = None
    enum_values: list[str] | None = None
    unit_group_id: int | None = None
    system_function: str | None = None
    system_params: dict | None = None
    normalizers: list[dict] | None = None


@router.patch("/fields/{field_id}")
def patch_field(field_id: int, body: FieldPatch, db: Session = Depends(get_db)):
    f = db.get(SchemaField, field_id)
    if f is None:
        raise HTTPException(404, "Feld nicht gefunden")
    v = f.entity.version
    _run(db, lambda: sd.update_field(db, f, **body.model_dump(exclude_unset=True)))
    return _version(v)


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, db: Session = Depends(get_db)):
    f = db.get(SchemaField, field_id)
    if f is None:
        raise HTTPException(404, "Feld nicht gefunden")
    v = f.entity.version
    _run(db, lambda: sd.delete_field(db, f))
    return _version(v)


class RelationIn(BaseModel):
    name: str
    kind: str
    source_entity_id: int
    target_entity_id: int
    description: str | None = None


@router.post("/versions/{version_id}/relations", status_code=201)
def add_relation(version_id: int, body: RelationIn, db: Session = Depends(get_db)):
    v = _ver(db, version_id)
    src, tgt = db.get(SchemaEntity, body.source_entity_id), db.get(SchemaEntity, body.target_entity_id)
    if src is None or tgt is None:
        raise HTTPException(404, "Entität nicht gefunden")
    _run(db, lambda: sd.add_relation(db, v, body.name, body.kind, src, tgt, body.description))
    return _version(v)


@router.delete("/relations/{relation_id}")
def delete_relation(relation_id: int, db: Session = Depends(get_db)):
    r = db.get(SchemaRelation, relation_id)
    if r is None:
        raise HTTPException(404, "Beziehung nicht gefunden")
    v = r.version
    if v.status != "draft":
        raise HTTPException(400, "Aktive und archivierte Versionen sind unveränderlich")
    db.delete(r)
    db.commit()
    db.refresh(v)
    return _version(v)
