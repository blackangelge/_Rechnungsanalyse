"""Router für Excel-Export-Einstellungen."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.models.export_config import (
    ExportConfig,
    INVOICE_FIELDS_DEFAULT,
    POSITION_FIELDS_DEFAULT,
    INVOICE_FIELD_LABELS,
    POSITION_FIELD_LABELS,
)

router = APIRouter(prefix="/api/settings/export", tags=["Export-Einstellungen"])


class ExportConfigRead(BaseModel):
    invoice_fields: list[str]
    position_fields: list[str]
    invoice_field_labels: dict[str, str]
    position_field_labels: dict[str, str]
    invoice_fields_all: list[str]
    position_fields_all: list[str]
    model_config = {"from_attributes": True}


class ExportConfigUpdate(BaseModel):
    invoice_fields: list[str]
    position_fields: list[str]


def _get_or_create_config(db: Session) -> ExportConfig:
    cfg = db.get(ExportConfig, 1)
    if cfg is None:
        cfg = ExportConfig(
            id=1,
            invoice_fields=list(INVOICE_FIELDS_DEFAULT),
            position_fields=list(POSITION_FIELDS_DEFAULT),
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@router.get("", response_model=ExportConfigRead)
def get_export_config(db: Session = Depends(get_db)):
    cfg = _get_or_create_config(db)
    return {
        "invoice_fields": cfg.invoice_fields or list(INVOICE_FIELDS_DEFAULT),
        "position_fields": cfg.position_fields or list(POSITION_FIELDS_DEFAULT),
        "invoice_field_labels": INVOICE_FIELD_LABELS,
        "position_field_labels": POSITION_FIELD_LABELS,
        "invoice_fields_all": list(INVOICE_FIELDS_DEFAULT),
        "position_fields_all": list(POSITION_FIELDS_DEFAULT),
    }


@router.put("", response_model=ExportConfigRead)
def update_export_config(payload: ExportConfigUpdate, db: Session = Depends(get_db)):
    cfg = _get_or_create_config(db)
    cfg.invoice_fields = payload.invoice_fields
    cfg.position_fields = payload.position_fields
    db.commit()
    return {
        "invoice_fields": cfg.invoice_fields,
        "position_fields": cfg.position_fields,
        "invoice_field_labels": INVOICE_FIELD_LABELS,
        "position_field_labels": POSITION_FIELD_LABELS,
        "invoice_fields_all": list(INVOICE_FIELDS_DEFAULT),
        "position_fields_all": list(POSITION_FIELDS_DEFAULT),
    }
