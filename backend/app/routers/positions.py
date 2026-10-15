"""
Router für die Artikelsuche (Positionen über alle Belege).

Endpunkte:
  GET /api/positions                 — Suche/Filter/Sortierung, paginiert (Gesamtzahl in X-Total-Count)
  GET /api/positions/summary         — Kennzahlen zu denselben Filtern (Anzahl, Summe, Einzelpreis min/Ø/max, je Einheit)
  GET /api/positions/filter-options  — Einheiten für das Filter-Dropdown
"""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.crud import position_search
from app.database import get_db
from app.schemas.position_search import PositionSearchRead, PositionSummary
from app.services.query_guard import QueryTooSlow, TTLCache, statement_timeout

router = APIRouter(prefix="/api/positions", tags=["Artikel"])

# Zeitlimit je Abfrage (Sekunden) — danach klare Meldung statt minutenlangem Warten
SEARCH_TIMEOUT = 30
SUMMARY_TIMEOUT = 45
_summary_cache = TTLCache(ttl_seconds=60)
_options_cache = TTLCache(ttl_seconds=300)

_TOO_SLOW = ("Die Suche dauert zu lange (sehr viele Daten). Bitte mehr Filter setzen — z.B. Lieferant, Jahr "
             "oder ein genaueres Suchwort.")


def _filters(
    q: Optional[str] = Query(None, description="Suchwörter (alle müssen in Bezeichnung oder Artikelnummer vorkommen)"),
    article_number: Optional[str] = None,
    supplier_name: Optional[str] = None,
    supplier_exact: bool = Query(False, description="Lieferantenname exakt statt Teilstring (aus der Auswahlliste)"),
    company: Optional[str] = None,
    year: Optional[int] = None,
    batch_ids: Optional[list[int]] = Query(default=None),
    document_id: Optional[int] = None,
    invoice_number: Optional[str] = None,
    unit: Optional[str] = Query(None, description="Einheit (exakt, Groß-/Kleinschreibung egal)"),
    unit_price_min: Optional[float] = None,
    unit_price_max: Optional[float] = None,
    total_price_min: Optional[float] = None,
    total_price_max: Optional[float] = None,
    quantity_min: Optional[float] = None,
    quantity_max: Optional[float] = None,
    date_from: Optional[date] = Query(None, description="Rechnungsdatum von (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Rechnungsdatum bis (YYYY-MM-DD)"),
    validation_status: Optional[list[str]] = Query(default=None),
    include_hidden: bool = False,
    include_deleted: bool = False,
) -> dict:
    return dict(
        q=q, article_number=article_number, supplier_name=supplier_name, supplier_exact=supplier_exact,
        company=company, year=year, batch_ids=batch_ids, document_id=document_id,
        invoice_number=invoice_number, unit=unit,
        unit_price_min=unit_price_min, unit_price_max=unit_price_max,
        total_price_min=total_price_min, total_price_max=total_price_max,
        quantity_min=quantity_min, quantity_max=quantity_max,
        date_from=date_from, date_to=date_to, validation_statuses=validation_status,
        include_hidden=include_hidden, include_deleted=include_deleted,
    )


@router.get("", response_model=list[PositionSearchRead])
def search_positions(
    response: Response,
    filters: dict = Depends(_filters),
    limit: int = Query(50, ge=1, le=500, description="Seitengröße"),
    offset: int = Query(0, ge=0),
    sort: Optional[Literal[tuple(position_search.SORT_COLUMNS)]] = Query(  # type: ignore[valid-type]
        None, description="Sortierung (Standard: neueste Belege zuerst)"),
    order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
):
    """
    Durchsucht alle Positionen aller Belege. Belege ausgeblendeter Imports und gelöschte Belege
    werden nur auf Wunsch (`include_hidden`, `include_deleted`) einbezogen; ein ausdrücklich gewählter
    Import (`batch_ids`) bzw. eine Beleg-ID (`document_id`) gelten immer.
    """
    try:
        with statement_timeout(db, SEARCH_TIMEOUT):
            total = position_search.count(db, cap=position_search.COUNT_CAP, **filters)
            rows = position_search.search(db, limit=limit, offset=offset, sort=sort,
                                          descending=(order == "desc"), **filters)
    except QueryTooSlow:
        raise HTTPException(status_code=504, detail=_TOO_SLOW)
    capped = total > position_search.COUNT_CAP
    response.headers["X-Total-Count"] = str(position_search.COUNT_CAP if capped else total)
    response.headers["X-Total-Capped"] = "true" if capped else "false"
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count, X-Total-Capped"
    return rows


@router.get("/summary", response_model=PositionSummary)
def positions_summary(filters: dict = Depends(_filters), db: Session = Depends(get_db)):
    """Kennzahlen zu den gefilterten Positionen (z.B. Preisentwicklung eines Artikels). Kurz zwischengespeichert."""
    key = tuple(sorted((k, str(v)) for k, v in filters.items() if v not in (None, "", False)))

    def compute():
        with statement_timeout(db, SUMMARY_TIMEOUT):
            return position_search.summary(db, **filters)

    try:
        return _summary_cache.get_or_compute(key, compute)
    except QueryTooSlow:
        raise HTTPException(status_code=504, detail=_TOO_SLOW)


@router.get("/filter-options")
def positions_filter_options(db: Session = Depends(get_db)):
    """Einheiten für das Dropdown — über alle Positionen gruppiert, daher 5 Minuten zwischengespeichert."""
    return _options_cache.get_or_compute("units", lambda: position_search.filter_options(db))
