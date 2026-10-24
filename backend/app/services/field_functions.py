"""
Systemfunktionen für Felder mit Quelle `system`: benannte Python-Funktionen mit Parametern, die einen Wert aus dem Beleg selbst liefern
(Seitenanzahl, Dateiname, Datei-Hash …). Kein frei eingegebener Code — das Feld speichert nur Funktionsname und Parameter.

Die Werte entstehen im Programm, nicht in der KI; die Normalisierer-Kette des Feldes läuft danach wie bei KI-Feldern.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


class FunctionError(ValueError):
    """Die Funktion kann für diesen Beleg keinen Wert liefern."""


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoParams(_Params):
    pass


@dataclass
class DocumentContext:
    """Alles, was Systemfunktionen über den Beleg wissen dürfen (keine DB-Sitzung, keine Seiteneffekte)."""

    document_id: int
    file_name: str | None = None
    file_path: str | None = None            # aktuelle PDF-Datei (für Hash)
    page_count: int | None = None
    document_type_id: int | None = None
    received_at: datetime | None = None
    batch_company: str | None = None
    batch_year: int | None = None
    _hash_cache: str | None = field(default=None, repr=False)


def build_context(doc) -> DocumentContext:
    """Aus einem `Document` (ORM) den Kontext bauen. `document_type` 0 (unbekannt) → None."""
    batch = doc.batch
    path = None
    if batch is not None and doc.stored_filename:
        path = os.path.join(batch.storage_folder_path, doc.stored_filename)
    return DocumentContext(
        document_id=doc.id,
        file_name=doc.original_filename,
        file_path=path,
        page_count=doc.page_count or None,
        document_type_id=doc.document_type or None,
        received_at=doc.created_at,
        batch_company=getattr(batch, "company_name", None),
        batch_year=getattr(batch, "year", None),
    )


@dataclass(frozen=True)
class SystemFunction:
    name: str
    label: str
    description: str
    data_type: str                          # Typ des Ergebnisses: text | integer | date | decimal
    params: type[_Params]
    func: Callable[[DocumentContext, Any], Any]


FUNCTIONS: dict[str, SystemFunction] = {}


def _register(name: str, label: str, description: str, data_type: str, params: type[_Params] = NoParams):
    def deco(fn):
        FUNCTIONS[name] = SystemFunction(name, label, description, data_type, params, fn)
        return fn
    return deco


@_register("page_count", "Seitenanzahl", "Anzahl der Seiten der PDF", "integer")
def _page_count(ctx, p):
    return ctx.page_count


@_register("file_name", "Dateiname", "Ursprünglicher Dateiname beim Import", "text")
def _file_name(ctx, p):
    return ctx.file_name


@_register("file_hash", "Datei-Hash", "SHA-256 der aktuellen PDF-Datei (erkennt identische Dateien)", "text")
def _file_hash(ctx, p):
    if ctx._hash_cache:
        return ctx._hash_cache
    if not ctx.file_path or not os.path.isfile(ctx.file_path):
        raise FunctionError("PDF-Datei nicht gefunden")
    h = hashlib.sha256()
    with open(ctx.file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    ctx._hash_cache = h.hexdigest()
    return ctx._hash_cache


class _DateFmt(_Params):
    with_time: bool = Field(False, description="Datum und Uhrzeit statt nur Datum")


@_register("received_at", "Eingangsdatum", "Zeitpunkt des Imports", "date", _DateFmt)
def _received_at(ctx, p: _DateFmt):
    if ctx.received_at is None:
        return None
    return ctx.received_at.isoformat() if p.with_time else ctx.received_at.date().isoformat()


@_register("document_type_id", "Dokumenttyp-ID", "ID des erkannten Dokumenttyps (leer, solange nicht erkannt)", "integer")
def _doc_type(ctx, p):
    return ctx.document_type_id


@_register("batch_company", "Firma des Imports", "Firmenname, unter dem importiert wurde", "text")
def _batch_company(ctx, p):
    return ctx.batch_company


@_register("batch_year", "Jahr des Imports", "Jahr, unter dem importiert wurde", "integer")
def _batch_year(ctx, p):
    return ctx.batch_year


class _Constant(_Params):
    value: str | int | float | bool = Field(..., description="Fester Wert für alle Belege")


@_register("constant", "Fester Wert", "Immer derselbe Wert (z. B. Mandant oder Kostenstelle)", "text", _Constant)
def _constant(ctx, p: _Constant):
    return p.value


def validate_function(name: str | None, params: dict | None, data_type: str | None = None) -> list[str]:
    """Prüft Funktionsname, Parameter und (optional) ob der Ergebnistyp zum Feldtyp passt."""
    fn = FUNCTIONS.get(name or "")
    if fn is None:
        return [f"unbekannte Systemfunktion „{name}“"]
    errors: list[str] = []
    try:
        fn.params(**(params or {}))
    except Exception as exc:  # pydantic ValidationError / TypeError
        errors.append(f"Parameter von {name}: {exc}")
    if data_type and name != "constant" and not _compatible(fn.data_type, data_type):
        errors.append(f"{name} liefert {fn.data_type}, das Feld ist {data_type}")
    return errors


def _compatible(produced: str, wanted: str) -> bool:
    if produced == wanted:
        return True
    return (produced, wanted) in {("integer", "decimal"), ("integer", "text"), ("date", "text"), ("text", "enum")}


def run(name: str, params: dict | None, ctx: DocumentContext) -> Any:
    """Führt eine Systemfunktion aus. Fehler → FunctionError (der Aufrufer setzt den Wert auf None und vermerkt den Grund)."""
    fn = FUNCTIONS.get(name)
    if fn is None:
        raise FunctionError(f"unbekannte Systemfunktion „{name}“")
    try:
        return fn.func(ctx, fn.params(**(params or {})))
    except FunctionError:
        raise
    except Exception as exc:
        raise FunctionError(str(exc)) from exc


def describe() -> list[dict]:
    return [
        {"name": f.name, "label": f.label, "description": f.description, "data_type": f.data_type, "params_schema": f.params.model_json_schema()}
        for f in FUNCTIONS.values()
    ]
