"""
Artikelsuche: Positionen (order_positions) über alle Belege durchsuchen, filtern, sortieren.

Die Abfragen verbinden Position → Beleg → Extraktion (Lieferant, Rechnungsnr., Datum) und lesen nur
die für die Liste nötigen Spalten (keine ORM-Objekte, keine schweren Textspalten wie raw_response).
Für die Teilstring-Suche (ILIKE '%…%') sorgen die pg_trgm-Indizes aus Migration 0011.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Query, Session

from app.models.document import Document
from app.models.import_batch import ImportBatch
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition

FILTER_KEYS = (
    "q", "article_number", "supplier_name", "company", "year", "batch_ids",
    "document_id", "invoice_number", "unit", "unit_price_min", "unit_price_max",
    "total_price_min", "total_price_max", "quantity_min", "quantity_max",
    "date_from", "date_to", "validation_statuses", "include_hidden", "include_deleted",
)

# Sortierbare Spalten (API-Name → SQL-Spalte)
SORT_COLUMNS = {
    "document_id": Document.id,
    "product_name": OrderPosition.product_name,
    "product_description": OrderPosition.product_description,
    "article_number": OrderPosition.article_number,
    "quantity": OrderPosition.quantity,
    "unit": OrderPosition.unit,
    "unit_price": OrderPosition.unit_price_netto,
    "total_price": OrderPosition.total_price,
    "supplier_name": InvoiceExtraction.vendor_id,
    "invoice_number": InvoiceExtraction.invoice_number,
    "invoice_date": InvoiceExtraction.invoice_date,
}


def _like(term: str) -> str:
    """'%term%' mit maskierten Sonderzeichen (Nutzer sucht nach "50%" oder "A_1", nicht nach Platzhaltern)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filtered(db: Session, entities: list, **f) -> Query:
    unknown = set(f) - set(FILTER_KEYS)
    if unknown:
        raise TypeError(f"Unbekannte Filter: {sorted(unknown)}")
    g = f.get
    query = (
        db.query(*entities)
        .select_from(OrderPosition)
        .join(Document, OrderPosition.document_id == Document.id)
        .join(ImportBatch, Document.batch_id == ImportBatch.id)
        .outerjoin(InvoiceExtraction, InvoiceExtraction.document_id == Document.id)
    )
    if not g("include_deleted"):
        query = query.filter(Document.soft_deleted == False)  # noqa: E712
    if g("batch_ids"):
        query = query.filter(Document.batch_id.in_(g("batch_ids")))
    elif not g("include_hidden") and g("document_id") is None:
        # wie in der Belegliste: Artikel ausgeblendeter Imports nur auf Wunsch (Ausnahmen: gewählter Import, Beleg-ID)
        query = query.filter(~Document.batch_id.in_(
            db.query(ImportBatch.id).filter(ImportBatch.hidden_at.isnot(None))))

    # Freitext: alle Wörter müssen in Bezeichnung/Beschreibung ODER Artikelnummer vorkommen (UND-verknüpft)
    for term in (g("q") or "").split():
        pattern = _like(term)
        query = query.filter(or_(
            OrderPosition.product_name.ilike(pattern, escape="\\"),
            OrderPosition.product_description.ilike(pattern, escape="\\"),
            OrderPosition.article_number.ilike(pattern, escape="\\"),
        ))
    if g("article_number"):
        query = query.filter(OrderPosition.article_number.ilike(_like(g("article_number").strip()), escape="\\"))
    if g("supplier_name"):
        query = query.filter(InvoiceExtraction.vendor_id.ilike(_like(g("supplier_name").strip()), escape="\\"))
    if g("company"):
        query = query.filter(ImportBatch.company_name.ilike(_like(g("company").strip()), escape="\\"))
    if g("year") is not None:
        query = query.filter(ImportBatch.year == g("year"))
    if g("document_id") is not None:
        query = query.filter(Document.id == g("document_id"))
    if g("invoice_number"):
        query = query.filter(InvoiceExtraction.invoice_number.ilike(_like(g("invoice_number").strip()), escape="\\"))
    if g("unit"):
        query = query.filter(func.lower(OrderPosition.unit) == g("unit").strip().lower())
    for key, column, op in (
        ("unit_price_min", OrderPosition.unit_price_netto, ">="), ("unit_price_max", OrderPosition.unit_price_netto, "<="),
        ("total_price_min", OrderPosition.total_price, ">="), ("total_price_max", OrderPosition.total_price, "<="),
        ("quantity_min", OrderPosition.quantity, ">="), ("quantity_max", OrderPosition.quantity, "<="),
    ):
        if g(key) is not None:
            value = Decimal(str(g(key)))
            query = query.filter(column >= value if op == ">=" else column <= value)
    if g("date_from") is not None:
        query = query.filter(InvoiceExtraction.invoice_date >= g("date_from"))
    if g("date_to") is not None:
        query = query.filter(InvoiceExtraction.invoice_date <= g("date_to"))
    if g("validation_statuses"):
        query = query.filter(InvoiceExtraction.validation_status.in_(g("validation_statuses")))
    return query


_LIST_COLUMNS = [
    OrderPosition.id, OrderPosition.document_id, OrderPosition.position_index,
    OrderPosition.product_name, OrderPosition.product_description, OrderPosition.article_number,
    OrderPosition.quantity, OrderPosition.unit, OrderPosition.unit_price_netto.label("unit_price"),
    OrderPosition.total_price, OrderPosition.discount,
    InvoiceExtraction.invoice_number, InvoiceExtraction.invoice_date,
    InvoiceExtraction.vendor_id.label("supplier_name"), InvoiceExtraction.validation_status,
    ImportBatch.company_name.label("company"), ImportBatch.year, Document.batch_id, Document.original_filename,
]


def search(db: Session, limit: int, offset: int = 0, sort: str | None = None,
           descending: bool = True, **filters) -> list:
    """Eine Seite Treffer. Standard: neueste Belege zuerst, innerhalb eines Belegs in Positionsreihenfolge."""
    query = _filtered(db, _LIST_COLUMNS, **filters)
    column = SORT_COLUMNS.get(sort or "")
    if column is None:
        query = query.order_by(Document.id.desc(), OrderPosition.position_index.asc())
    else:
        # NULLs ans Ende, unabhängig von der Richtung; id als stabiler Tie-Breaker für das Blättern
        query = query.order_by(
            column.is_(None), column.desc() if descending else column.asc(), OrderPosition.id.asc()
        )
    return query.offset(offset).limit(limit).all()


def count(db: Session, **filters) -> int:
    return _filtered(db, [func.count(OrderPosition.id)], **filters).scalar() or 0


def summary(db: Session, **filters) -> dict:
    """Kennzahlen zu den Treffern: Anzahl, Summe Gesamtpreis, Einzelpreis min/Ø/max, Aufschlüsselung nach Einheit."""
    total = _filtered(
        db,
        [func.count(OrderPosition.id), func.sum(OrderPosition.total_price),
         func.min(OrderPosition.unit_price_netto), func.avg(OrderPosition.unit_price_netto),
         func.max(OrderPosition.unit_price_netto), func.count(func.distinct(Document.id))],
        **filters,
    ).one()
    unit_key = func.lower(func.trim(OrderPosition.unit))
    by_unit = (
        _filtered(db, [unit_key, func.count(OrderPosition.id), func.sum(OrderPosition.quantity),
                       func.sum(OrderPosition.total_price)], **filters)
        .group_by(unit_key).order_by(func.count(OrderPosition.id).desc()).limit(10).all()
    )
    f = lambda v: float(v) if v is not None else None      # noqa: E731
    return {
        "count": total[0] or 0,
        "documents": total[5] or 0,
        "total_price_sum": f(total[1]),
        "unit_price_min": f(total[2]),
        "unit_price_avg": f(total[3]),
        "unit_price_max": f(total[4]),
        "by_unit": [{"unit": u or None, "count": c, "quantity": f(q), "total_price": f(t)} for u, c, q, t in by_unit],
    }


def filter_options(db: Session) -> dict:
    """Einheiten (häufigste zuerst) für das Filter-Dropdown."""
    unit_key = func.lower(func.trim(OrderPosition.unit))
    rows = (
        db.query(unit_key, func.count(OrderPosition.id))
        .filter(OrderPosition.unit.isnot(None), func.trim(OrderPosition.unit) != "")
        .group_by(unit_key).order_by(func.count(OrderPosition.id).desc()).limit(60).all()
    )
    return {"units": [{"unit": u, "count": c} for u, c in rows]}
