"""
Router: Export-Vorlagen und schemagesteuerter Excel-Export.

  GET    /api/export-templates                 — alle Vorlagen
  POST   /api/export-templates                  — anlegen
  GET    /api/export-templates/{id}             — eine Vorlage
  PUT    /api/export-templates/{id}             — ändern
  DELETE /api/export-templates/{id}
  GET    /api/export-templates/{id}/columns     — Spalten, die die Vorlage erzeugt (Vorschau, ohne Daten)
  GET    /api/export-templates/{id}/export      — Excel-Datei (?batch_ids=…&document_ids=…&vendor_ids=…&include_hidden=)
"""

import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.database import get_db
from app.models.export_template import ExportTemplate
from app.services import schema_export
from app.services.schema_export import ExportError, Filters

router = APIRouter(prefix="/api/export-templates", tags=["export-templates"])


class TemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    layout: str = "sheet_per_entity"
    quantity_mode: str = "original"
    config: dict = Field(default_factory=dict)


def _read(t: ExportTemplate) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description, "layout": t.layout, "quantity_mode": t.quantity_mode,
            "config": t.config or {}, "created_at": t.created_at, "updated_at": t.updated_at}


def _active(db: Session):
    version = sd.get_active(db)
    if version is None:
        raise HTTPException(409, "Kein aktives Schema vorhanden")
    return version


def _check(db: Session, body: TemplateIn) -> None:
    try:
        schema_export.validate_template(_active(db), body.layout, body.quantity_mode, body.config)
    except ExportError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def list_templates(db: Session = Depends(get_db)):
    return [_read(t) for t in db.query(ExportTemplate).order_by(ExportTemplate.name)]


@router.post("", status_code=201)
def create_template(body: TemplateIn, db: Session = Depends(get_db)):
    _check(db, body)
    if db.query(ExportTemplate).filter(ExportTemplate.name == body.name.strip()).first():
        raise HTTPException(409, f"Eine Vorlage „{body.name}“ gibt es schon")
    t = ExportTemplate(name=body.name.strip(), description=body.description, layout=body.layout,
                       quantity_mode=body.quantity_mode, config=body.config)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _read(t)


def _get(db: Session, template_id: int) -> ExportTemplate:
    t = db.get(ExportTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Vorlage nicht gefunden")
    return t


@router.get("/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    return _read(_get(db, template_id))


@router.put("/{template_id}")
def update_template(template_id: int, body: TemplateIn, db: Session = Depends(get_db)):
    t = _get(db, template_id)
    _check(db, body)
    clash = db.query(ExportTemplate).filter(ExportTemplate.name == body.name.strip(), ExportTemplate.id != t.id).first()
    if clash:
        raise HTTPException(409, f"Eine Vorlage „{body.name}“ gibt es schon")
    t.name, t.description, t.layout, t.quantity_mode, t.config = body.name.strip(), body.description, body.layout, body.quantity_mode, body.config
    db.commit()
    db.refresh(t)
    return _read(t)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, template_id))
    db.commit()


@router.get("/{template_id}/columns")
def template_columns(template_id: int, db: Session = Depends(get_db)):
    t = _get(db, template_id)
    version = _active(db)
    try:
        schema_export.validate_template(version, t.layout, t.quantity_mode, t.config)
    except ExportError as exc:
        raise HTTPException(422, str(exc)) from exc
    by_name = {e.name: e for e in version.entities}
    out = []
    for sel in (t.config or {}).get("entities") or [{"entity": e.name} for e in version.entities]:
        ent = by_name[sel["entity"]]
        out.append({"entity": ent.name, "display_name": ent.display_name,
                    "columns": [{"header": c.header, "kind": c.kind, "format": c.fmt} for c in schema_export.entity_columns(ent, sel.get("fields"), t.quantity_mode)]})
    return out


@router.get("/{template_id}/export")
def export(
    template_id: int,
    batch_ids: list[int] | None = Query(None),
    document_ids: list[int] | None = Query(None),
    vendor_ids: list[int] | None = Query(None),
    include_hidden: bool = Query(False, description="Belege ausgeblendeter Imports einbeziehen (außer bei ausdrücklich gewählten Imports)"),
    db: Session = Depends(get_db),
):
    from app.routers.imports import _content_disposition

    t = _get(db, template_id)
    version = _active(db)
    try:
        data = schema_export.build_workbook(db, version, t, Filters(batch_ids, document_ids, vendor_ids, include_hidden))
    except ExportError as exc:
        raise HTTPException(404 if "keine Datensätze" in str(exc) else 422, str(exc)) from exc
    name = f"Export_{t.name}_{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(name)},
    )
