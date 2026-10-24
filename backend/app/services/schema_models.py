"""
Aus einer Schema-Version (Entitäten, Felder, Beziehungen) werden zur Laufzeit Pydantic-Modelle gebaut.

Dieselben Modelle liefern
  * das JSON-Schema für `response_format: json_schema` (vLLM Structured Outputs) und
  * die Validierung der Antwort vor dem Speichern.

Nur Felder mit Quelle `llm` und ohne Sperre gehen ins Schema; Standard- und Systemfelder setzt das Programm. Alle Schlüssel sind im Schema
Pflicht, dürfen aber `null` sein — so schreibt das Modell jedes Feld und lässt Unbekanntes bewusst leer. Zahlen dürfen als Zahl oder Text kommen
(„1.234,56“), damit die Normalisierer arbeiten können. Beziehungen: Eltern-Kind → Liste, Referenz auf Stammdaten → verschachteltes Objekt.
Modelle werden je Versions-ID zwischengespeichert (Versionen sind unveränderlich).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.models.schema_def import SchemaEntity, SchemaField, SchemaVersion


class SchemaModelError(ValueError):
    """Die Definition lässt sich nicht in ein Extraktionsschema übersetzen."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _pascal(name: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Entity"


def _describe(f: SchemaField) -> str:
    parts = [f.display_name]
    if f.description:
        parts.append(f.description.strip())
    if f.data_type == "date":
        parts.append("Format YYYY-MM-DD")
    elif f.data_type in ("decimal", "integer"):
        parts.append("Zahl; Dezimalpunkt oder wie gedruckt")
    return " — ".join(parts)


def llm_fields(entity: SchemaEntity) -> list[SchemaField]:
    return [f for f in entity.fields if f.source == "llm" and not f.is_locked]


_QUANTITY_MODELS: dict[str, type[BaseModel]] = {}


def _quantity_model() -> type[BaseModel]:
    if "q" not in _QUANTITY_MODELS:
        _QUANTITY_MODELS["q"] = create_model(
            "Quantity", __base__=_Strict,
            value=(Optional[float | str], Field(..., description="Zahl exakt wie gedruckt")),
            unit=(Optional[str], Field(..., description="Einheit exakt wie gedruckt, nichts umrechnen")),
        )
    return _QUANTITY_MODELS["q"]


def _field_type(f: SchemaField) -> Any:
    t = f.data_type
    if t == "text" or t == "date":
        return Optional[str]
    if t == "integer":
        return Optional[int | str]
    if t == "decimal":
        return Optional[float | str]
    if t == "boolean":
        return Optional[bool]
    if t == "enum":
        return Optional[Literal[tuple(f.enum_values or [])]]       # type: ignore[valid-type]
    if t == "quantity":
        return Optional[_quantity_model()]
    raise SchemaModelError(f"Unbekannter Datentyp {t} bei {f.name}")


def _build(entity: SchemaEntity, version: SchemaVersion, by_id: dict[int, SchemaEntity], stack: tuple[int, ...]) -> type[BaseModel]:
    if entity.id in stack:
        raise SchemaModelError(f"Zirkuläre Beziehung bei {entity.name}")
    defs: dict[str, Any] = {}
    for f in llm_fields(entity):
        defs[f.name] = (_field_type(f), Field(..., description=_describe(f)))
    for rel in version.relations:
        if rel.source_entity_id != entity.id:
            continue
        target = by_id[rel.target_entity_id]
        child = _build(target, version, by_id, stack + (entity.id,))
        desc = (rel.description or target.display_name).strip()
        if rel.kind == "parent_child":
            defs[rel.name] = (list[child], Field(..., description=f"{desc} (Liste, leer wenn keine)"))       # type: ignore[valid-type]
        else:
            defs[rel.name] = (Optional[child], Field(..., description=desc))                                  # type: ignore[valid-type]
    if not defs:
        raise SchemaModelError(f"Entität {entity.name} hat keine Felder für die KI")
    return create_model(f"{_pascal(entity.name)}Model", __base__=_Strict, __doc__=entity.description or entity.display_name, **defs)


def root_entity(version: SchemaVersion) -> SchemaEntity:
    """Die Wurzel: nicht Stammdaten und nicht Kind einer Eltern-Kind-Beziehung. Es muss genau eine geben."""
    children = {r.target_entity_id for r in version.relations if r.kind == "parent_child"}
    roots = [e for e in version.entities if not e.is_master and e.id not in children]
    if len(roots) != 1:
        raise SchemaModelError(f"Genau eine Wurzel-Entität erwartet, gefunden: {[e.name for e in roots] or 'keine'}")
    return roots[0]


@dataclass(frozen=True)
class ExtractionModel:
    version_id: int
    root: str
    model: type[BaseModel]
    json_schema: dict


def _inline_refs(schema: dict) -> dict:
    """Löst `$ref`/`$defs` auf — manche Grammatik-Backends kommen mit Referenzen schlechter zurecht."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return {**walk(copy.deepcopy(defs[name])), **walk(merged)}
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


_CACHE: dict[int, ExtractionModel] = {}


def build_extraction_model(version: SchemaVersion, use_cache: bool = True) -> ExtractionModel:
    if use_cache and version.status != "draft" and version.id in _CACHE:
        return _CACHE[version.id]
    by_id = {e.id: e for e in version.entities}
    root = root_entity(version)
    model = _build(root, version, by_id, ())
    schema = _inline_refs(model.model_json_schema())
    schema.pop("title", None)
    built = ExtractionModel(version.id, root.name, model, schema)
    if use_cache and version.status != "draft":
        _CACHE[version.id] = built
    return built


def clear_cache() -> None:
    _CACHE.clear()


def response_format(built: ExtractionModel, name: str = "extraction") -> dict:
    """OpenAI-kompatibles `response_format` (vLLM Structured Outputs)."""
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": built.json_schema}}
