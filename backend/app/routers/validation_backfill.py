"""
Router: Nachrüstung der Plausibilitätsprüfung für den Altbestand (ohne KI).

  GET  /api/validation-backfill          — Fortschritt/Ergebnis des letzten Laufs + Anzahl offener Belege
  POST /api/validation-backfill          — Lauf starten (dry_run = nur rechnen, nichts speichern)
  POST /api/validation-backfill/cancel   — laufenden Lauf abbrechen
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import validation_backfill

router = APIRouter(prefix="/api/validation-backfill", tags=["Nachrüstung Prüfung"])


class BackfillStart(BaseModel):
    dry_run: bool = False
    auto_correct: bool = True
    batch_ids: list[int] | None = None
    normalize_units: bool = False     # nur Gewichtseinheiten auf Tonnen vereinheitlichen (alle Belege)
    restore_originals: bool = False   # Mengen/Einheiten/Einzelpreise wieder auf den Wortlaut der Rechnung zurücksetzen


@router.get("")
def backfill_status(batch_ids: list[int] | None = Query(default=None), normalize_units: bool = False, restore_originals: bool = False):
    state = validation_backfill.get_state()
    state["pending"] = validation_backfill.count_pending(batch_ids, normalize_units, restore_originals)
    return state


@router.post("")
def backfill_start(payload: BackfillStart):
    try:
        return validation_backfill.start(payload.dry_run, payload.auto_correct, payload.batch_ids,
                                         normalize_units=payload.normalize_units, restore_originals=payload.restore_originals)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/cancel")
def backfill_cancel():
    return {"cancelled": validation_backfill.cancel()}
