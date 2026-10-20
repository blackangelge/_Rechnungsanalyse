"""
Daten-Browser: Tabellen der Datenbank ansehen, durchsuchen und (bewusst) bearbeiten.

Alle Routen sind angemeldeten Nutzern vorbehalten (in main.py mit `_protected` eingebunden). Zugangsdaten-Tabellen und
API-Schlüssel sind ausgenommen (siehe services/db_tools.py). Jede Änderung wird im Backend-Log festgehalten.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import change_log, db_tools
from app.services.query_guard import clear_all_caches

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/db", tags=["Daten-Browser"])


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc).strip("'\""))
    return HTTPException(status_code=400, detail=str(exc))


class RowChanges(BaseModel):
    changes: dict[str, Any]


class RowValues(BaseModel):
    values: dict[str, Any]


@router.get("/tables")
def tables(db: Session = Depends(get_db)):
    return db_tools.list_tables(db)


@router.get("/tables/{table}")
def table_info(table: str):
    try:
        t = db_tools.get_table(table)
    except db_tools.DbToolError as exc:
        raise _http(exc)
    return db_tools.describe_table(t)


@router.get("/tables/{table}/rows")
def rows(
    table: str,
    request: Request,
    response: Response,
    limit: int = Query(50, ge=1, le=db_tools.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    sort: str | None = None,
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    """Zeilen mit Freitext `q` (alle Wörter, alle Textspalten) und Spaltenfiltern `f.<spalte>=wert` (leer = NULL)."""
    filters = {k[2:]: v for k, v in request.query_params.items() if k.startswith("f.")}
    try:
        items, total = db_tools.browse(db, table, limit, offset, q, sort, order, filters)
    except db_tools.DbToolError as exc:
        raise _http(exc)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/tables/{table}/rows/{pk}")
def row(table: str, pk: str, db: Session = Depends(get_db)):
    try:
        return db_tools.get_row(db, table, pk)
    except (db_tools.DbToolError, LookupError) as exc:
        raise _http(exc)


@router.patch("/tables/{table}/rows/{pk}")
def update_row(table: str, pk: str, body: RowChanges, db: Session = Depends(get_db)):
    try:
        result = db_tools.update_row(db, table, pk, body.changes)
    except (db_tools.DbToolError, LookupError) as exc:
        raise _http(exc)
    logger.info("Daten-Browser: %s #%s geändert: %s", table, pk, result["changed"])
    change_log.log_manual_update(db, "browser", table, pk, result.pop("before"), result["row"])
    clear_all_caches()
    return result


@router.post("/tables/{table}/rows", status_code=201)
def insert_row(table: str, body: RowValues, db: Session = Depends(get_db)):
    try:
        result = db_tools.insert_row(db, table, body.values)
    except (db_tools.DbToolError, LookupError) as exc:
        raise _http(exc)
    logger.info("Daten-Browser: %s #%s angelegt", table, result.get("pk"))
    clear_all_caches()
    return result


@router.delete("/tables/{table}/rows/{pk}")
def delete_row(table: str, pk: str, db: Session = Depends(get_db)):
    try:
        result = db_tools.delete_row(db, table, pk)
    except (db_tools.DbToolError, LookupError) as exc:
        raise _http(exc)
    logger.warning("Daten-Browser: %s #%s GELÖSCHT: %s", table, pk, result["deleted"])
    clear_all_caches()
    return result
