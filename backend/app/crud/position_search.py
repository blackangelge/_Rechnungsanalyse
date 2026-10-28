"""
Artikelsuche über das neue dynamische Schema: Positionen sind ``extracted_records`` der Entität
"position" (JSONB ``data``). Je Position werden Beleg (Wurzel-Datensatz) + Import verbunden.

Die Mengen sind bereits normiert gespeichert (``menge.value``/``menge.unit`` aus dem Einheiten-
System; das normierte Symbol ist die Basiseinheit der Gruppe, z.B. t/l/m/m²). Dadurch braucht die
Suche keine eigene Umrechnungslogik mehr: ``base_*`` kommt direkt aus den gespeicherten Werten und
wird nur für umrechenbare Dimensionen (Masse/Volumen/Länge/Fläche → t/l/m/m²) ausgewiesen.

Gelesen werden nur die für die Liste nötigen JSON-Felder (kein ganzes data-JSON der Positionen).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Query, Session, aliased

from app.models.document import Document
from app.models.import_batch import ImportBatch
from app.models.schema_def import ExtractedRecord, SchemaEntity
from app.services import unit_conversion

FILTER_KEYS = (
    "q", "article_number", "supplier_name", "supplier_exact", "company", "year", "batch_ids",
    "document_id", "invoice_number", "unit", "unit_group", "unit_price_min", "unit_price_max",
    "total_price_min", "total_price_max", "quantity_min", "quantity_max",
    "date_from", "date_to", "validation_statuses", "include_hidden", "include_deleted",
)

# Aliase: POS = Positions-Datensatz, ROOT = zugehöriger Beleg-Wurzel-Datensatz
POS = aliased(ExtractedRecord, name="pos")
ROOT = aliased(ExtractedRecord, name="root")

# ── JSON-Zugriffe (Position) ──────────────────────────────────────────────────
_artbez = POS.data["artikelbezeichnung"].as_string()
_artnr  = POS.data["artikelnummer"].as_string()
_qty    = POS.data["menge"]["original_value"].as_float()     # Menge wie gedruckt
_unit   = POS.data["menge"]["original_unit"].as_string()
_nval   = POS.data["menge"]["value"].as_float()              # normiert (Basiseinheit der Gruppe)
_nunit  = POS.data["menge"]["unit"].as_string()
_price  = POS.data["einzelpreis"].as_float()
_total  = POS.data["gesamtpreis"].as_float()
_priceq = POS.data["preis_je_menge"].as_float()
_packs  = POS.data["verpackungsinhalt"]["original_value"].as_float()
_packu  = POS.data["verpackungsinhalt"]["original_unit"].as_string()
_utype  = POS.data["mengeneinheit_typ"].as_string()
_disc   = POS.data["preisnachlass_bezeichnung"].as_string()

# ── JSON-Zugriffe (Beleg-Wurzel) ──────────────────────────────────────────────
_invnr   = ROOT.data["rechnungsnummer"].as_string()
_invdate = ROOT.data["rechnungsdatum"].as_string()           # ISO-String -> chronologisch vergleichbar/sortierbar
_supp    = ROOT.data["_master"]["lieferant"]["name"].as_string()
_valstat = ROOT.data["_validation"]["status"].as_string()

# Verpackung/Preisbasis: Hat die Position einen Verpackungsinhalt (z.B. 44 VPE à 100 Stück),
# rechnet die Auswertung mit dem Inhalt (Menge × Inhalt, Einheit des Inhalts).
_pack = and_(_packs.isnot(None), _packs > 0)
_cunit = case((and_(_pack, func.trim(func.coalesce(_packu, "")) != ""), _packu), else_=_unit)
_cqty = case((_pack, _qty * _packs), else_=_qty)
_cfactor = unit_conversion.factor_expr(_cunit)
_has_basis = or_(_pack, _priceq > 1)

# Umgerechnete Werte (Gewicht → t, Volumen → l …): Inhaltsmenge × Faktor; Preis je Basiseinheit = Positionspreis ÷ Basismenge
_base_qty = (_cqty * _cfactor).label("base_quantity")
_base_unit = unit_conversion.base_unit_expr(_cunit).label("base_unit")
_base_price = case(
    (and_(_cfactor.isnot(None), _cqty != 0, _total.isnot(None)), _total / (_cqty * _cfactor)),
    else_=None,
).label("base_unit_price")
# Inhalts-/Stückwerte nur, wenn Verpackung oder Preisbasis angegeben sind
_content_qty = case((_has_basis, _cqty), else_=None).label("content_quantity")
_content_unit = case((_has_basis, _cunit), else_=None).label("content_unit")
_content_price = case(
    (and_(_has_basis, _cqty != 0),
     func.coalesce(_total / _cqty, _price / (func.coalesce(_priceq, 1) * func.coalesce(_packs, 1)))),
    else_=None,
).label("content_unit_price")

# Sortierbare Spalten (API-Name → Ausdruck)
SORT_COLUMNS = {
    "document_id": Document.id,
    "product_name": _artbez,
    "product_description": _artbez,
    "article_number": _artnr,
    "quantity": _qty,
    "unit": _unit,
    "unit_price": _price,
    "total_price": _total,
    "supplier_name": _supp,
    "invoice_number": _invnr,
    "invoice_date": _invdate,
}

_LIST_COLUMNS = [
    POS.id, POS.document_id, func.coalesce(POS.position_index, 0).label("position_index"),
    _artbez.label("product_name"), _artbez.label("product_description"), _artnr.label("article_number"),
    _qty.label("quantity"), _unit.label("unit"), _price.label("unit_price"),
    _base_qty, _base_unit, _base_price,
    _total.label("total_price"), _disc.label("discount"),
    _priceq.label("price_quantity"), _packs.label("pack_size"), _packu.label("pack_unit"), _utype.label("unit_type"),
    _content_qty, _content_unit, _content_price,
    _invnr.label("invoice_number"), _invdate.label("invoice_date"),
    _supp.label("supplier_name"), _valstat.label("validation_status"),
    ImportBatch.company_name.label("company"), ImportBatch.year, Document.batch_id, Document.original_filename,
]


def _like(term: str) -> str:
    """'%term%' mit maskierten Sonderzeichen (Nutzer sucht nach "50%" oder "A_1", nicht nach Platzhaltern)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filtered(db: Session, entities: list, **f) -> Query:
    unknown = set(f) - set(FILTER_KEYS)
    if unknown:
        raise TypeError(f"Unbekannte Filter: {sorted(unknown)}")
    g = f.get
    position_entities = db.query(SchemaEntity.id).filter(SchemaEntity.name == "position")
    query = (
        db.query(*entities)
        .select_from(POS)
        .join(Document, POS.document_id == Document.id)
        .join(ImportBatch, Document.batch_id == ImportBatch.id)
        .outerjoin(ROOT, ROOT.id == POS.parent_record_id)
        .filter(POS.entity_id.in_(position_entities))
    )
    if not g("include_deleted"):
        query = query.filter(Document.soft_deleted == False)  # noqa: E712
    if g("batch_ids"):
        query = query.filter(Document.batch_id.in_(g("batch_ids")))
    elif not g("include_hidden") and g("document_id") is None:
        query = query.filter(~Document.batch_id.in_(
            db.query(ImportBatch.id).filter(ImportBatch.hidden_at.isnot(None))))

    # Freitext: alle Wörter müssen in Bezeichnung ODER Artikelnummer vorkommen (UND-verknüpft)
    for term in (g("q") or "").split():
        pattern = _like(term)
        query = query.filter(or_(_artbez.ilike(pattern, escape="\\"), _artnr.ilike(pattern, escape="\\")))
    if g("article_number"):
        query = query.filter(_artnr.ilike(_like(g("article_number").strip()), escape="\\"))
    if g("supplier_name"):
        name = g("supplier_name").strip()
        if g("supplier_exact"):
            query = query.filter(_supp == name)
        else:
            query = query.filter(_supp.ilike(_like(name), escape="\\"))
    if g("company"):
        query = query.filter(ImportBatch.company_name.ilike(_like(g("company").strip()), escape="\\"))
    if g("year") is not None:
        query = query.filter(ImportBatch.year == g("year"))
    if g("document_id") is not None:
        query = query.filter(Document.id == g("document_id"))
    if g("invoice_number"):
        query = query.filter(_invnr.ilike(_like(g("invoice_number").strip()), escape="\\"))
    if g("unit"):
        query = query.filter(func.lower(_unit) == g("unit").strip().lower())
    if g("unit_group"):
        names = unit_conversion.group_names(g("unit_group"))
        query = query.filter(unit_conversion.unit_key(_unit).in_(names or [""]))
    for key, column, op in (
        ("unit_price_min", _price, ">="), ("unit_price_max", _price, "<="),
        ("total_price_min", _total, ">="), ("total_price_max", _total, "<="),
        ("quantity_min", _qty, ">="), ("quantity_max", _qty, "<="),
    ):
        if g(key) is not None:
            value = float(g(key))
            query = query.filter(column >= value if op == ">=" else column <= value)
    if g("date_from") is not None:
        query = query.filter(_invdate >= g("date_from").isoformat())      # ISO-Strings vergleichen sich chronologisch
    if g("date_to") is not None:
        query = query.filter(_invdate <= g("date_to").isoformat())
    if g("validation_statuses"):
        query = query.filter(_valstat.in_(g("validation_statuses")))
    return query


def search(db: Session, limit: int, offset: int = 0, sort: str | None = None,
           descending: bool = True, **filters) -> list:
    """Eine Seite Treffer. Standard: neueste Belege zuerst, innerhalb eines Belegs in Positionsreihenfolge."""
    query = _filtered(db, _LIST_COLUMNS, **filters)
    column = SORT_COLUMNS.get(sort or "")
    if column is None:
        query = query.order_by(Document.id.desc(), func.coalesce(POS.position_index, 0).asc(), POS.id.asc())
    else:
        # NULLs ans Ende, unabhängig von der Richtung; id als stabiler Tie-Breaker für das Blättern
        query = query.order_by(column.is_(None), column.desc() if descending else column.asc(), POS.id.asc())
    return query.offset(offset).limit(limit).all()


COUNT_CAP = 20_000


def count(db: Session, cap: int | None = None, **filters) -> int:
    """
    Trefferzahl. Mit ``cap`` wird nur bis zu ``cap + 1`` gezählt (Rückgabe > cap ⇒ „mehr als cap Treffer").
    """
    if cap is None:
        return _filtered(db, [func.count(POS.id)], **filters).scalar() or 0
    limited = _filtered(db, [POS.id], **filters).limit(cap + 1).subquery()
    return db.query(func.count()).select_from(limited).scalar() or 0


def summary(db: Session, **filters) -> dict:
    """Kennzahlen zu den Treffern: Anzahl, Summe Gesamtpreis, Einzelpreis min/Ø/max, Aufschlüsselung nach Einheit."""
    total = _filtered(
        db,
        [func.count(POS.id), func.sum(_total), func.min(_price), func.avg(_price), func.max(_price),
         func.count(func.distinct(Document.id)), func.sum(case((_total.is_(None), 1), else_=0))],
        **filters,
    ).one()
    # umrechenbare Einheiten (t, kg, g …) werden in der Basiseinheit zusammengefasst, Rest nach Schreibweise
    _factor = unit_conversion.factor_expr(_unit)
    unit_key = func.coalesce(unit_conversion.base_unit_expr(_unit), func.lower(func.trim(_unit)))
    by_unit = (
        _filtered(db, [unit_key, func.count(POS.id), func.sum(_qty * func.coalesce(_factor, 1)), func.sum(_total)], **filters)
        .group_by(unit_key).order_by(func.count(POS.id).desc()).limit(10).all()
    )
    fnum = lambda v: float(v) if v is not None else None      # noqa: E731
    return {
        "count": total[0] or 0,
        "documents": total[5] or 0,
        "without_total_price": int(total[6] or 0),
        "total_price_sum": fnum(total[1]),
        "unit_price_min": fnum(total[2]),
        "unit_price_avg": fnum(total[3]),
        "unit_price_max": fnum(total[4]),
        "by_unit": [{"unit": u or None, "count": c, "quantity": fnum(q), "total_price": fnum(t)} for u, c, q, t in by_unit],
    }


def filter_options(db: Session) -> dict:
    """Einheiten (häufigste zuerst) für das Filter-Dropdown."""
    position_entities = db.query(SchemaEntity.id).filter(SchemaEntity.name == "position")
    unit_key = func.lower(func.trim(_unit))
    rows = (
        db.query(unit_key, func.count(POS.id))
        .select_from(POS)
        .filter(POS.entity_id.in_(position_entities), _unit.isnot(None), func.trim(_unit) != "")
        .group_by(unit_key).order_by(func.count(POS.id).desc()).limit(60).all()
    )
    return {"units": [{"unit": u, "count": c} for u, c in rows]}
