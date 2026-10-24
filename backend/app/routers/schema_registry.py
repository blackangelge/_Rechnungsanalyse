"""
Router: verfügbare Bausteine für das dynamische Schema (nur lesend).

  GET /api/schema/normalizers — Normalisierer mit Parameter-Schema (für die Ketten-Oberfläche)
  GET /api/schema/functions   — Systemfunktionen mit Parameter-Schema
  POST /api/schema/normalizers/preview — Kette auf einen Beispielwert anwenden (ohne etwas zu speichern)
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import field_functions, normalizers

router = APIRouter(prefix="/api/schema", tags=["schema"])


class PreviewRequest(BaseModel):
    value: Any = None
    chain: list[dict] = []


@router.get("/normalizers")
def list_normalizers():
    return normalizers.describe()


@router.get("/functions")
def list_functions():
    return field_functions.describe()


@router.post("/normalizers/preview")
def preview_chain(req: PreviewRequest):
    errors = normalizers.validate_chain(req.chain)
    if errors:
        return {"ok": False, "value": None, "errors": errors, "issues": []}
    result = normalizers.apply_chain(req.value, req.chain)
    return {"ok": result.ok, "value": normalizers.to_jsonable(result.value), "errors": [], "issues": result.issues}
