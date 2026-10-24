"""
Einheiten auflösen und Mengen normieren (reine Logik + ein kleiner Resolver über die DB-Tabellen aus models/units.py).

Mengenfelder werden im JSONB immer so gespeichert:

    {"original_value": 25880, "original_unit": "kg",
     "value": 25.88, "unit": "t", "unit_id": 1, "group_id": 1, "status": "ok"}

`status`: ok · no_unit (keine Einheit angegeben) · unknown_unit (Einheit unbekannt → Prüfliste) · wrong_group (Einheit passt nicht zur Gruppe des Feldes) · invalid (Wert keine Zahl).
Nichts wird stillschweigend umgedeutet: bei unbekannter Einheit bleiben Originalwert und -einheit erhalten, `value`/`unit` sind leer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.orm import Session

from app.models.units import Unit, UnitAlias
from app.services.normalizers import NormalizeError, parse_german_number, to_jsonable

_QUANT = Decimal("0.000000000001")        # 12 Nachkommastellen reichen für mg → t


def alias_key(text: str) -> str:
    """Normalisierte Schreibweise für den Vergleich: getrimmt, Leerraum zusammengefasst, ohne Schluss-Punkt."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    return s[:-1].rstrip() if s.endswith(".") and len(s) > 1 else s


@dataclass(frozen=True)
class ResolvedUnit:
    unit_id: int
    group_id: int
    symbol: str
    factor: Decimal
    base_symbol: str


class UnitResolver:
    """Lädt Einheiten und Aliasse einmal und löst Schreibweisen auf (exakt, sonst ohne Groß/Klein bei entsprechend markierten Einheiten)."""

    def __init__(self, db: Session):
        self._exact: dict[str, ResolvedUnit] = {}
        self._folded: dict[str, ResolvedUnit] = {}
        bases = {u.group_id: u.symbol for u in db.query(Unit).filter(Unit.is_base.is_(True)).all()}
        units = {u.id: u for u in db.query(Unit).all()}
        for al in db.query(UnitAlias).all():
            u = units.get(al.unit_id)
            if u is None:
                continue
            res = ResolvedUnit(u.id, u.group_id, u.symbol, Decimal(u.factor), bases.get(u.group_id, u.symbol))
            key = alias_key(al.alias)
            self._exact[key] = res
            if u.case_insensitive:
                self._folded[key.casefold()] = res
        self.units_by_id = units

    def resolve(self, text: str | None) -> ResolvedUnit | None:
        if not text or not text.strip():
            return None
        key = alias_key(text)
        return self._exact.get(key) or self._folded.get(key.casefold())


def normalize_quantity(resolver: UnitResolver, raw: Any, group_id: int | None = None) -> tuple[dict, list[dict]]:
    """
    `raw`: {"value": …, "unit": …} (aus dem LLM-Schema), eine nackte Zahl/Zeichenkette oder None.
    Rückgabe: (Mengen-Dict wie oben, issues). Wirft nie.
    """
    value, unit_text = (raw.get("value"), raw.get("unit")) if isinstance(raw, dict) else (raw, None)
    unit_text = unit_text.strip() if isinstance(unit_text, str) else (str(unit_text) if unit_text is not None else None)
    out = {"original_value": None, "original_unit": unit_text or None, "value": None, "unit": None, "unit_id": None, "group_id": None, "status": "ok"}
    issues: list[dict] = []
    try:
        number = parse_german_number(value)
    except NormalizeError as exc:
        out["status"] = "invalid"
        issues.append({"kind": "invalid_quantity", "message": str(exc)})
        return out, issues
    if number is None:
        out["status"] = "no_value" if not unit_text else "invalid"
        return out, issues
    out["original_value"] = to_jsonable(number)
    if not unit_text:
        out["status"] = "no_unit"
        return out, issues
    res = resolver.resolve(unit_text)
    if res is None:
        out["status"] = "unknown_unit"
        issues.append({"kind": "unknown_unit", "unit": unit_text, "message": f"Einheit „{unit_text}“ ist unbekannt"})
        return out, issues
    if group_id is not None and res.group_id != group_id:
        out["status"] = "wrong_group"
        issues.append({"kind": "wrong_group", "unit": unit_text, "message": f"Einheit „{unit_text}“ gehört nicht zur Gruppe des Feldes"})
        return out, issues
    out["value"] = to_jsonable((number / res.factor).quantize(_QUANT, rounding=ROUND_HALF_UP).normalize())
    out["unit"], out["unit_id"], out["group_id"] = res.base_symbol, res.unit_id, res.group_id
    return out, issues
