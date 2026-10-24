"""
Router: Mengeneinheiten und Prüfliste.

  GET    /api/units                       — Gruppen mit Einheiten und Aliassen
  POST   /api/units/groups                — Gruppe anlegen
  PATCH  /api/units/groups/{id}           — Gruppe umbenennen
  DELETE /api/units/groups/{id}
  POST   /api/units/groups/{id}/units     — Einheit anlegen (Basis: is_base + Faktor 1)
  PATCH  /api/units/{unit_id}             — Name/Symbol/Faktor/Groß-Klein ändern
  DELETE /api/units/{unit_id}
  POST   /api/units/{unit_id}/aliases     — Alias hinzufügen (Konflikt → 409 mit Erklärung)
  DELETE /api/units/aliases/{alias_id}
  GET    /api/units/review                — offene unbekannte Einheiten, gruppiert
  POST   /api/units/review/assign         — Schreibweise einer Einheit zuordnen (rechnet Belege neu)
  POST   /api/units/review/ignore         — Schreibweise ignorieren
  POST   /api/units/convert               — Vorschau: Menge + Einheit → normierter Wert (nichts gespeichert)
"""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.crud import units as cu
from app.database import get_db
from app.models.units import Unit, UnitAlias, UnitGroup
from app.services.units import UnitResolver, normalize_quantity

router = APIRouter(prefix="/api/units", tags=["units"])


def _unit(u: Unit) -> dict:
    return {"id": u.id, "name": u.name, "symbol": u.symbol, "factor": float(u.factor),
            "is_base": u.is_base, "case_insensitive": u.case_insensitive,
            "aliases": [{"id": a.id, "alias": a.alias} for a in u.aliases]}


def _fail(exc: cu.UnitError, status: int = 409):
    raise HTTPException(status_code=status, detail=str(exc))


@router.get("")
def list_groups(db: Session = Depends(get_db)):
    return [{"id": g.id, "name": g.name, "description": g.description, "units": [_unit(u) for u in g.units]}
            for g in db.query(UnitGroup).order_by(UnitGroup.id)]


class GroupIn(BaseModel):
    name: str
    description: str | None = None


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, db: Session = Depends(get_db)):
    try:
        g = cu.create_group(db, body.name, body.description)
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)
    return {"id": g.id, "name": g.name}


class GroupPatch(BaseModel):
    name: str | None = None
    description: str | None = None


@router.patch("/groups/{group_id}")
def patch_group(group_id: int, body: GroupPatch, db: Session = Depends(get_db)):
    g = db.get(UnitGroup, group_id)
    if g is None:
        raise HTTPException(404, "Gruppe nicht gefunden")
    try:
        cu.update_group(db, g, body.name, body.description)
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)
    return {"id": g.id, "name": g.name, "description": g.description}


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(UnitGroup, group_id)
    if g is None:
        raise HTTPException(404, "Gruppe nicht gefunden")
    db.delete(g)
    db.commit()


class UnitIn(BaseModel):
    name: str
    symbol: str
    factor: Decimal = Decimal(1)
    is_base: bool = False
    case_insensitive: bool = False
    aliases: list[str] | None = None


@router.post("/groups/{group_id}/units", status_code=201)
def create_unit(group_id: int, body: UnitIn, db: Session = Depends(get_db)):
    g = db.get(UnitGroup, group_id)
    if g is None:
        raise HTTPException(404, "Gruppe nicht gefunden")
    try:
        u = cu.create_unit(db, g, body.name, body.symbol, body.factor, is_base=body.is_base,
                           case_insensitive=body.case_insensitive, aliases=body.aliases)
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)
    return _unit(u)


class UnitPatch(BaseModel):
    name: str | None = None
    symbol: str | None = None
    factor: Decimal | None = None
    case_insensitive: bool | None = None


@router.patch("/{unit_id}")
def patch_unit(unit_id: int, body: UnitPatch, db: Session = Depends(get_db)):
    u = db.get(Unit, unit_id)
    if u is None:
        raise HTTPException(404, "Einheit nicht gefunden")
    try:
        cu.update_unit(db, u, **body.model_dump(exclude_unset=True))
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)
    return _unit(u)


@router.delete("/{unit_id}", status_code=204)
def delete_unit(unit_id: int, db: Session = Depends(get_db)):
    u = db.get(Unit, unit_id)
    if u is None:
        raise HTTPException(404, "Einheit nicht gefunden")
    try:
        cu.delete_unit(db, u)
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)


class AliasIn(BaseModel):
    alias: str


@router.post("/{unit_id}/aliases", status_code=201)
def add_alias(unit_id: int, body: AliasIn, db: Session = Depends(get_db)):
    u = db.get(Unit, unit_id)
    if u is None:
        raise HTTPException(404, "Einheit nicht gefunden")
    try:
        a = cu.add_alias(db, u, body.alias)
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)
    return {"id": a.id, "alias": a.alias}


@router.delete("/aliases/{alias_id}", status_code=204)
def delete_alias(alias_id: int, db: Session = Depends(get_db)):
    a = db.get(UnitAlias, alias_id)
    if a is None:
        raise HTTPException(404, "Alias nicht gefunden")
    cu.remove_alias(db, a)
    db.commit()


@router.get("/review")
def review(db: Session = Depends(get_db)):
    return cu.review_groups(db)


@router.get("/review/count")
def review_count(db: Session = Depends(get_db)):
    """Kennzahl für das Menü: offene Schreibweisen und Vorkommen (billiger als die volle Liste)."""
    from sqlalchemy import func
    from app.models.units import UnitReviewItem
    row = db.query(func.count(func.distinct(UnitReviewItem.alias_key)), func.count(UnitReviewItem.id)).filter(UnitReviewItem.status == "open").one()
    return {"aliases": int(row[0] or 0), "occurrences": int(row[1] or 0)}


class AssignIn(BaseModel):
    alias: str
    unit_id: int


@router.post("/review/assign")
def assign(body: AssignIn, db: Session = Depends(get_db)):
    u = db.get(Unit, body.unit_id)
    if u is None:
        raise HTTPException(404, "Einheit nicht gefunden")
    try:
        result = cu.assign_review(db, body.alias, u)
        db.commit()
    except cu.UnitError as exc:
        db.rollback()
        _fail(exc)
    return result


class IgnoreIn(BaseModel):
    alias: str


@router.post("/review/ignore")
def ignore(body: IgnoreIn, db: Session = Depends(get_db)):
    n = cu.ignore_review(db, body.alias)
    db.commit()
    return {"ignored": n}


class ConvertIn(BaseModel):
    value: Any
    unit: str | None = None
    group_id: int | None = None


@router.post("/convert")
def convert(body: ConvertIn, db: Session = Depends(get_db)):
    result, issues = normalize_quantity(UnitResolver(db), {"value": body.value, "unit": body.unit}, body.group_id)
    return {"quantity": result, "issues": issues}
