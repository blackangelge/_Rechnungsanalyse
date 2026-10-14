"""
Router für die Artikelsuche (Positionen über alle Belege).

Endpunkte:
  GET /api/positions                 — Suche/Filter/Sortierung, paginiert (Gesamtzahl in X-Total-Count)
  GET /api/positions/summary         — Kennzahlen zu denselben Filtern (Anzahl, Summe, Einzelpreis min/Ø/max, je Einheit)
  GET /api/positions/filter-options  — Einheiten für das Filter-Dropdown
"""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.crud import position_search
from app.database import get_db
from app.schemas.position_search import PositionSearchRead, PositionSummary

router = APIRouter(prefix="/api/positions", tags=["Artikel"])


def _filters(
    q: Optional[str] = Query(None, description="Suchwörter (alle müssen in Bezeichnung oder Artikelnummer vorkommen)"),
    article_number: Optional[str] = None,
    supplier_name: Optional[str] = None,
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
    include_deleted: bool = False,
) -> dict:
    return dict(
        q=q, article_number=article_number, supplier_name=supplier_name,
        company=company, year=year, batch_ids=batch_ids, document_id=document_id,
        invoice_number=invoice_number, unit=unit,
        unit_price_min=unit_price_min, unit_price_max=unit_price_max,
        total_price_min=total_price_min, total_price_max=total_price_max,
        quantity_min=quantity_min, quantity_max=quantity_max,
        date_from=date_from, date_to=date_to, validation_statuses=validation_status,
        include_deleted=include_deleted,
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
    Durchsucht alle Positionen aller Belege. Gelöschte Belege werden nur auf Wunsch
    (`include_deleted`) einbezogen.
    """
    response.headers["X-Total-Count"] = str(position_search.count(db, **filters))
    response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
    return position_search.search(db, limit=limit, offset=offset, sort=sort,
                                  descending=(order == "desc"), **filters)


@router.get("/summary", response_model=PositionSummary)
def positions_summary(filters: dict = Depends(_filters), db: Session = Depends(get_db)):
    """Kennzahlen zu den gefilterten Positionen (z.B. Preisentwicklung eines Artikels)."""
    return position_search.summary(db, **filters)


@router.get("/filter-options")
def positions_filter_options(db: Session = Depends(get_db)):
    return position_search.filter_options(db)
