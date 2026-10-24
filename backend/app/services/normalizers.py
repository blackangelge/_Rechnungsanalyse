"""
Normalisierer für extrahierte Feldwerte: benannte Bausteine mit Parametern, zu einer geordneten Kette pro Feld verknüpft.

Kein frei eingegebener Code — die Kette ist eine Liste `[{"name": "trim", "params": {...}}, …]`, die Bausteine stehen in `NORMALIZERS`.
Die Reihenfolge ist relevant und wird so gespeichert (z. B. erst Währungszeichen entfernen, dann Zahl parsen).

Fehler in der Kette brechen nie ab: `apply_chain` liefert `ChainResult(value, issues)`. Schlägt ein Schritt fehl, ist der Wert `None`
und der Fehler steht in `issues` (der Beleg wird später zur Prüfung markiert, nichts geht still verloren).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class NormalizeError(ValueError):
    """Ein Wert lässt sich mit diesem Baustein nicht verarbeiten."""


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoParams(_Params):
    pass


@dataclass(frozen=True)
class Normalizer:
    name: str
    label: str
    description: str
    params: type[_Params]
    func: Callable[[Any, Any], Any]
    output_type: str = "same"          # text | decimal | integer | date | same (Typ bleibt wie die Eingabe)


NORMALIZERS: dict[str, Normalizer] = {}


def _register(name: str, label: str, description: str, params: type[_Params] = NoParams, output_type: str = "same"):
    def deco(fn):
        NORMALIZERS[name] = Normalizer(name, label, description, params, fn, output_type)
        return fn
    return deco


# ─── Text ────────────────────────────────────────────────────────────────────

@_register("trim", "Trimmen", "Leerzeichen und Zeilenumbrüche am Anfang und Ende entfernen", output_type="text")
def _trim(value, p):
    return value.strip() if isinstance(value, str) else value


@_register("collapse_whitespace", "Leerraum zusammenfassen", "Mehrfache Leerzeichen/Umbrüche zu einem Leerzeichen", output_type="text")
def _collapse(value, p):
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


class _Case(_Params):
    mode: str = Field("upper", pattern="^(upper|lower|title)$")


@_register("case", "Groß-/Kleinschreibung", "In Großbuchstaben, Kleinbuchstaben oder Titelschreibweise umwandeln", _Case, "text")
def _case(value, p: _Case):
    if not isinstance(value, str):
        return value
    return {"upper": value.upper, "lower": value.lower, "title": value.title}[p.mode]()


class _Remove(_Params):
    chars: str = Field(..., min_length=1, max_length=40, description="Alle diese Zeichen entfernen")


@_register("remove_chars", "Zeichen entfernen", "Bestimmte Zeichen überall entfernen (z. B. Leerzeichen oder Punkte)", _Remove, "text")
def _remove_chars(value, p: _Remove):
    if not isinstance(value, str):
        return value
    return value.translate({ord(c): None for c in p.chars})


class _ThousandsParams(_Params):
    separator: str = Field(".", min_length=1, max_length=1)


@_register("remove_thousands", "Tausendertrenner entfernen", "Tausendertrennzeichen (Standard: Punkt) entfernen", _ThousandsParams, "text")
def _remove_thousands(value, p: _ThousandsParams):
    return value.replace(p.separator, "") if isinstance(value, str) else value


@_register("comma_to_point", "Komma durch Punkt ersetzen", "Dezimalkomma in Dezimalpunkt umwandeln", output_type="text")
def _comma_to_point(value, p):
    return value.replace(",", ".") if isinstance(value, str) else value


_MAX_REGEX_INPUT = 20_000


class _Regex(_Params):
    pattern: str = Field(..., min_length=1, max_length=200)
    replacement: str = Field("", max_length=200)
    ignore_case: bool = False


@_register("regex_replace", "Regex ersetzen", "Muster per regulärem Ausdruck ersetzen (\\1 für Gruppen)", _Regex, "text")
def _regex_replace(value, p: _Regex):
    if not isinstance(value, str):
        return value
    if len(value) > _MAX_REGEX_INPUT:
        raise NormalizeError("Text zu lang für Regex-Ersetzung")
    try:
        return re.sub(p.pattern, p.replacement, value, flags=re.IGNORECASE if p.ignore_case else 0)
    except re.error as exc:
        raise NormalizeError(f"Ungültiger regulärer Ausdruck: {exc}") from exc


class _Empty(_Params):
    values: list[str] = Field(default_factory=lambda: ["", "-", "n/a", "null", "keine"], max_length=20)


@_register("empty_to_null", "Leerwerte zu Null", "Platzhalter wie „-“, „n/a“ oder leere Texte als nicht vorhanden behandeln", _Empty)
def _empty_to_null(value, p: _Empty):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().casefold() in {v.casefold() for v in p.values}:
        return None
    return value


# ─── Zahlen ──────────────────────────────────────────────────────────────────

_CURRENCY = "€$£¥"


def parse_german_number(value: Any) -> Decimal | None:
    """
    „1.234,56 €“ → 1234.56, „719,99“ → 719.99, „(12,50)“ und „12,50-“ → negativ, „1.234“ → 1234, „1,5“ → 1.5.
    Beide Trenner: der letzte ist das Dezimalzeichen. Nur Punkte: mehrere = Tausender; ein einzelner Punkt mit genau drei Ziffern danach
    (und 1–3 Ziffern davor, nicht mit 0 beginnend) gilt als Tausenderpunkt, sonst als Dezimalpunkt („12.5“, „0.500“).
    Zahlen (int/float/Decimal) werden unverändert übernommen; leere Werte → None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if not isinstance(value, str):
        raise NormalizeError(f"Keine Zahl: {value!r}")
    s = value.strip().replace(" ", "").replace(" ", "").replace("'", "")
    for c in _CURRENCY:
        s = s.replace(c, "")
    s = re.sub(r"(?i)(eur|euro|usd|chf|gbp)$", "", s)
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.startswith("-") or s.startswith("−"):
        negative, s = True, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not re.fullmatch(r"[0-9.,]+", s) or not re.search(r"[0-9]", s):
        raise NormalizeError(f"Keine Zahl: {value!r}")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        if s.count(",") > 1:
            raise NormalizeError(f"Mehrdeutige Zahl: {value!r}")
        s = s.replace(",", ".")
    elif "." in s:
        if s.count(".") > 1:
            s = s.replace(".", "")
        else:
            head, tail = s.split(".")
            if len(tail) == 3 and 1 <= len(head) <= 3 and not head.startswith("0"):
                s = head + tail
    try:
        number = Decimal(s)
    except InvalidOperation as exc:
        raise NormalizeError(f"Keine Zahl: {value!r}") from exc
    return -number if negative else number


@_register("parse_german_number", "Deutsche Zahl parsen", "„1.234,56 €“ → 1234.56 (Tausenderpunkt, Dezimalkomma, Währungszeichen, Klammer-/Nachstellminus)", output_type="decimal")
def _parse_german_number(value, p):
    return parse_german_number(value)


class _Round(_Params):
    digits: int = Field(2, ge=0, le=8)


@_register("round", "Runden", "Auf eine Anzahl Nachkommastellen runden (kaufmännisch)", _Round, "decimal")
def _round(value, p: _Round):
    if value is None:
        return None
    number = parse_german_number(value) if not isinstance(value, Decimal) else value
    from decimal import ROUND_HALF_UP
    return number.quantize(Decimal(1).scaleb(-p.digits), rounding=ROUND_HALF_UP)


@_register("to_integer", "Ganzzahl", "In eine Ganzzahl umwandeln (nur wenn ohne Nachkommaanteil)", output_type="integer")
def _to_integer(value, p):
    if value is None:
        return None
    number = parse_german_number(value)
    if number != number.to_integral_value():
        raise NormalizeError(f"Keine Ganzzahl: {value!r}")
    return int(number)


# ─── Datum ───────────────────────────────────────────────────────────────────

_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


class _DateParams(_Params):
    month_first: bool = Field(False, description="Bei „03/25/2025“: Monat zuerst (US)")


@_register("parse_date", "Datum parsen", "„25.03.2025“, „2025-03-25“, „25/03/25“ → ISO-Datum (YYYY-MM-DD); unbekanntes Format ist ein Fehler", _DateParams, "date")
def _parse_date(value, p: _DateParams):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise NormalizeError(f"Kein Datum: {value!r}")
    s = value.strip()
    if not s:
        return None
    formats = list(_DATE_FORMATS)
    if p.month_first:
        formats = ["%m/%d/%Y", "%m/%d/%y"] + formats
    else:
        formats += ["%d/%m/%y"]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise NormalizeError(f"Unbekanntes Datumsformat: {value!r}")


# ─── Kette ───────────────────────────────────────────────────────────────────

@dataclass
class ChainResult:
    value: Any
    issues: list[dict] = field(default_factory=list)       # [{"step": 1, "name": "parse_date", "message": "…"}]

    @property
    def ok(self) -> bool:
        return not self.issues


def validate_chain(chain: list[dict] | None) -> list[str]:
    """Prüft Namen und Parameter einer gespeicherten Kette; gibt Fehlermeldungen zurück (leer = in Ordnung)."""
    errors: list[str] = []
    for i, step in enumerate(chain or [], start=1):
        if not isinstance(step, dict) or "name" not in step:
            errors.append(f"Schritt {i}: Name fehlt")
            continue
        norm = NORMALIZERS.get(step["name"])
        if norm is None:
            errors.append(f"Schritt {i}: unbekannter Baustein „{step['name']}“")
            continue
        unknown = set(step) - {"name", "params"}
        if unknown:
            errors.append(f"Schritt {i}: unbekannte Angaben {sorted(unknown)}")
        try:
            norm.params(**(step.get("params") or {}))
        except ValidationError as exc:
            errors.append(f"Schritt {i} ({step['name']}): " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))
        except TypeError as exc:
            errors.append(f"Schritt {i} ({step['name']}): {exc}")
        if norm.name == "regex_replace":
            try:
                re.compile((step.get("params") or {}).get("pattern", ""))
            except re.error as exc:
                errors.append(f"Schritt {i} (regex_replace): ungültiger regulärer Ausdruck: {exc}")
    return errors


def apply_chain(value: Any, chain: list[dict] | None) -> ChainResult:
    """Wendet die Kette in der gespeicherten Reihenfolge an. Der erste Fehler beendet die Kette: Wert None + Eintrag in `issues`."""
    result = ChainResult(value)
    for i, step in enumerate(chain or [], start=1):
        norm = NORMALIZERS.get(step.get("name", ""))
        if norm is None:
            result.value = None
            result.issues.append({"step": i, "name": step.get("name"), "message": "unbekannter Baustein"})
            return result
        try:
            params = norm.params(**(step.get("params") or {}))
            result.value = norm.func(result.value, params)
        except (NormalizeError, ValidationError, TypeError) as exc:
            result.value = None
            result.issues.append({"step": i, "name": norm.name, "message": str(exc)})
            return result
    return result


def to_jsonable(value: Any) -> Any:
    """Für JSONB: Decimal → int (wenn ganzzahlig) oder float; date → ISO-Text."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def describe() -> list[dict]:
    """Metadaten für die Oberfläche (Auswahl + Parameterformular)."""
    return [
        {"name": n.name, "label": n.label, "description": n.description, "output_type": n.output_type, "params_schema": n.params.model_json_schema()}
        for n in NORMALIZERS.values()
    ]
