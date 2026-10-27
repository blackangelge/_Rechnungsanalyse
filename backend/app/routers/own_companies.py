"""Eigene Gesellschaften (Rechnungsempfänger): Liste pflegen + Vorschläge aus den gelesenen Empfängern."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.own_company import OwnCompany
from app.services import own_companies as oc

router = APIRouter(prefix="/api/own-companies", tags=["Eigene Gesellschaften"])


class CompanyIn(BaseModel):
    """Alle Angaben sind optional; mindestens eine Kennung (Name, Schreibweise, USt-IdNr., Steuernummer oder IBAN) ist nötig."""
    name: str | None = None
    aliases: list[str] | None = None
    vat_ids: list[str] | None = None
    tax_numbers: list[str] | None = None
    ibans: list[str] | None = None
    note: str | None = None
    active: bool = True


def _out(c: OwnCompany) -> dict:
    return {"id": c.id, "name": c.name, "aliases": c.aliases or [], "vat_ids": c.vat_ids or [], "tax_numbers": c.tax_numbers or [],
            "ibans": c.ibans or [], "note": c.note, "active": c.active}


def _apply(c: OwnCompany, body: CompanyIn) -> None:
    c.name = (body.name or "").strip() or None
    c.aliases, c.vat_ids = oc.clean_list(body.aliases), oc.clean_list(body.vat_ids)
    c.tax_numbers, c.ibans = oc.clean_list(body.tax_numbers), oc.clean_list(body.ibans)
    c.note = (body.note or "").strip() or None
    c.active = body.active
    if not (c.name or c.aliases or c.vat_ids or c.tax_numbers or c.ibans):
        raise HTTPException(status_code=422, detail="Bitte mindestens einen Namen, eine USt-IdNr., Steuernummer oder IBAN angeben.")


@router.get("")
def list_companies(db: Session = Depends(get_db)):
    return [_out(c) for c in db.query(OwnCompany).order_by(OwnCompany.id).all()]


@router.get("/suggestions")
def get_suggestions(min_count: int = 2, db: Session = Depends(get_db)):
    """Häufig gelesene Rechnungsempfänger, die noch nicht in der Liste stehen."""
    return oc.suggestions(db, min_count=max(1, min_count))


@router.post("", status_code=201)
def create_company(body: CompanyIn, db: Session = Depends(get_db)):
    c = OwnCompany()
    _apply(c, body)
    db.add(c)
    db.commit()
    return _out(c)


@router.put("/{company_id}")
def update_company(company_id: int, body: CompanyIn, db: Session = Depends(get_db)):
    c = db.get(OwnCompany, company_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Gesellschaft nicht gefunden")
    _apply(c, body)
    db.commit()
    return _out(c)


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: int, db: Session = Depends(get_db)):
    c = db.get(OwnCompany, company_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Gesellschaft nicht gefunden")
    db.delete(c)
    db.commit()
