"""
CRUD für Mengeneinheiten mit den Regeln: genau eine Basiseinheit (Faktor 1) je Gruppe, Aliasse global eindeutig
(bei Einheiten ohne Groß-/Kleinschreibung auch unabhängig von der Schreibweise), unbekannte Einheiten in der Prüfliste.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.schema_def import ExtractedRecord
from app.models.units import Unit, UnitAlias, UnitGroup, UnitReviewItem
from app.services.units import UnitResolver, alias_key, normalize_quantity


class UnitError(ValueError):
    """Verstoß gegen eine Einheiten-Regel (HTTP 400/409)."""


# ─── Gruppen / Einheiten ─────────────────────────────────────────────────────

def create_group(db: Session, name: str, description: str | None = None) -> UnitGroup:
    name = (name or "").strip()
    if not name:
        raise UnitError("Gruppenname fehlt")
    if db.query(UnitGroup).filter(UnitGroup.name == name).first():
        raise UnitError(f"Gruppe „{name}“ existiert bereits")
    g = UnitGroup(name=name, description=description)
    db.add(g)
    db.flush()
    return g


def create_unit(
    db: Session, group: UnitGroup, name: str, symbol: str, factor: Decimal | float | str = 1, *,
    is_base: bool = False, case_insensitive: bool = False, aliases: list[str] | None = None,
) -> Unit:
    factor = Decimal(str(factor))
    base = next((u for u in group.units if u.is_base), None)
    if is_base:
        if base is not None:
            raise UnitError(f"Gruppe „{group.name}“ hat schon die Basiseinheit „{base.name}“")
        if factor != 1:
            raise UnitError("Die Basiseinheit hat den Faktor 1")
    else:
        if base is None:
            raise UnitError("Zuerst die Basiseinheit der Gruppe anlegen")
        if factor <= 0:
            raise UnitError("Der Faktor muss größer als 0 sein")
    if any(u.name == name for u in group.units):
        raise UnitError(f"Einheit „{name}“ existiert in der Gruppe schon")
    unit = Unit(group_id=group.id, name=name, symbol=symbol, factor=factor, is_base=is_base, case_insensitive=case_insensitive)
    db.add(unit)
    db.flush()
    db.refresh(group)
    for al in (aliases if aliases is not None else [symbol]):
        add_alias(db, unit, al)
    return unit


def update_unit(db: Session, unit: Unit, *, name: str | None = None, symbol: str | None = None,
                factor: Decimal | float | str | None = None, case_insensitive: bool | None = None) -> Unit:
    if factor is not None:
        factor = Decimal(str(factor))
        if unit.is_base and factor != 1:
            raise UnitError("Die Basiseinheit hat den Faktor 1")
        if factor <= 0:
            raise UnitError("Der Faktor muss größer als 0 sein")
        unit.factor = factor
    if name is not None:
        unit.name = name
    if symbol is not None:
        unit.symbol = symbol
    if case_insensitive is not None and case_insensitive != unit.case_insensitive:
        old = unit.case_insensitive
        unit.case_insensitive = case_insensitive
        db.flush()
        for al in unit.aliases:
            conflict = _conflict(db, al.alias, unit, exclude_alias_id=al.id)
            if conflict is not None:
                unit.case_insensitive = old
                raise UnitError(f"Alias „{al.alias}“ kollidiert dann mit „{conflict.alias}“ ({conflict.unit.name})")
    db.flush()
    return unit


def delete_unit(db: Session, unit: Unit) -> None:
    if unit.is_base and len(unit.group.units) > 1:
        raise UnitError("Die Basiseinheit lässt sich erst löschen, wenn die Gruppe sonst leer ist")
    db.delete(unit)
    db.flush()


# ─── Aliasse ─────────────────────────────────────────────────────────────────

def _conflict(db: Session, alias: str, unit: Unit, exclude_alias_id: int | None = None) -> UnitAlias | None:
    key = alias_key(alias)
    for other in db.query(UnitAlias).all():
        if other.id == exclude_alias_id:
            continue
        okey = alias_key(other.alias)
        if okey == key:
            return other
        if okey.casefold() == key.casefold() and (unit.case_insensitive or other.unit.case_insensitive):
            return other
    return None


def add_alias(db: Session, unit: Unit, alias: str) -> UnitAlias:
    alias = alias_key(alias)
    if not alias:
        raise UnitError("Alias fehlt")
    if len(alias) > 50:
        raise UnitError("Alias zu lang")
    clash = _conflict(db, alias, unit)
    if clash is not None:
        raise UnitError(f"Alias „{alias}“ ist bereits vergeben: „{clash.alias}“ gehört zu {clash.unit.name} ({clash.unit.group.name})")
    al = UnitAlias(unit_id=unit.id, alias=alias)
    db.add(al)
    db.flush()
    db.refresh(unit)
    return al


def remove_alias(db: Session, alias: UnitAlias) -> None:
    db.delete(alias)
    db.flush()


# ─── Prüfliste ───────────────────────────────────────────────────────────────

def add_review_item(db: Session, *, record_id: int, document_id: int, field_name: str, original_unit: str) -> UnitReviewItem:
    """Idempotent je Datensatz und Feld."""
    item = db.query(UnitReviewItem).filter_by(record_id=record_id, field_name=field_name).first()
    if item is None:
        item = UnitReviewItem(alias_key=alias_key(original_unit)[:60], original_unit=original_unit[:100], record_id=record_id,
                              document_id=document_id, field_name=field_name, status="open")
        db.add(item)
        db.flush()
    return item


def review_groups(db: Session) -> list[dict]:
    """Offene Vorkommen, gruppiert nach Schreibweise (für die Zuordnungsliste)."""
    rows: dict[str, dict] = {}
    for it in db.query(UnitReviewItem).filter(UnitReviewItem.status == "open").order_by(UnitReviewItem.id):
        g = rows.setdefault(it.alias_key, {"alias": it.alias_key, "count": 0, "documents": set(), "examples": []})
        g["count"] += 1
        g["documents"].add(it.document_id)
        if len(g["examples"]) < 5:
            g["examples"].append({"record_id": it.record_id, "document_id": it.document_id, "field": it.field_name})
    out = [{**g, "documents": len(g["documents"])} for g in rows.values()]
    return sorted(out, key=lambda g: -g["count"])


def _requantify(db: Session, resolver: UnitResolver, item: UnitReviewItem) -> bool:
    rec = db.get(ExtractedRecord, item.record_id)
    if rec is None or not isinstance(rec.data, dict):
        return False
    q = rec.data.get(item.field_name)
    if not isinstance(q, dict) or q.get("original_value") is None:
        return False
    new, _ = normalize_quantity(resolver, {"value": q["original_value"], "unit": q.get("original_unit")})
    data = dict(rec.data)
    data[item.field_name] = new
    issues = [i for i in data.get("_issues", []) if not (i.get("field") == item.field_name and i.get("kind") == "unknown_unit")]
    if issues:
        data["_issues"] = issues
    else:
        data.pop("_issues", None)
    rec.data = data
    return new["status"] == "ok"


def assign_review(db: Session, alias: str, unit: Unit) -> dict:
    """Ordnet eine Schreibweise einer Einheit zu (neuer Alias) und rechnet alle offenen Vorkommen neu."""
    key = alias_key(alias)
    items = db.query(UnitReviewItem).filter(UnitReviewItem.alias_key == key, UnitReviewItem.status == "open").all()
    if not items:
        raise UnitError(f"Keine offenen Vorkommen für „{key}“")
    if UnitResolver(db).resolve(key) is None:
        add_alias(db, unit, key)
    resolver = UnitResolver(db)
    updated = 0
    for it in items:
        if _requantify(db, resolver, it):
            updated += 1
        it.status, it.unit_id = "assigned", unit.id
    db.flush()
    return {"alias": key, "items": len(items), "updated": updated}


def ignore_review(db: Session, alias: str) -> int:
    items = db.query(UnitReviewItem).filter(UnitReviewItem.alias_key == alias_key(alias), UnitReviewItem.status == "open").all()
    for it in items:
        it.status = "ignored"
    db.flush()
    return len(items)
