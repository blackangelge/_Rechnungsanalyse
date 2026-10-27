"""
Werkzeug-Katalog des KI-Assistenten.

LESEN-Werkzeuge laufen sofort und liefern Daten an die KI zurück. SCHREIBEN-Werkzeuge werden NIE sofort ausgeführt:
die KI legt einen Vorschlag an (`summary` für die Oberfläche), der Nutzer gibt ihn frei (`execute`).

Die Werkzeuge rufen die bestehenden Router-Funktionen bzw. CRUD-Funktionen direkt auf — damit gelten dieselben Prüfungen
wie in der Oberfläche (Schema-Regeln, Workflow-Validierung, Pflicht-Workflow beim Import …). Namen statt IDs sind erlaubt
(Gruppe „Masse“, Einheit „kg“, Entität „position“), weil die KI neue IDs vor der Freigabe noch nicht kennt.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import crud
from app.crud import schema_definition as sd
from app.crud import units as cu
from app.models.export_template import ExportTemplate
from app.models.import_batch import ImportBatch
from app.models.schema_def import SchemaEntity, SchemaField, SchemaVersion
from app.models.units import Unit, UnitAlias, UnitGroup
from app.services import db_tools, workflow_service

logger = logging.getLogger(__name__)

MAX_LIST = 200


class ToolError(Exception):
    """Verständliche Fehlermeldung, die an die KI (bzw. den Nutzer) zurückgegeben wird."""


@dataclass
class Tool:
    name: str
    kind: str                       # "read" | "write"
    args: str                       # Kurzbeschreibung der Argumente (für den Prompt)
    doc: str
    handler: Callable[..., Any]
    summary: Callable[[dict], str] | None = None


TOOLS: dict[str, Tool] = {}


def _register(name: str, kind: str, args: str, doc: str, summary: Callable[[dict], str] | None = None):
    def deco(fn):
        TOOLS[name] = Tool(name, kind, args, doc, fn, summary)
        return fn
    return deco


def _http(fn: Callable, *a, **k):
    """Router-Funktion aufrufen; HTTPException/Validierungsfehler → ToolError."""
    try:
        return fn(*a, **k)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc
    except ValidationError as exc:
        raise ToolError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc
    except (cu.UnitError, sd.SchemaError) as exc:
        raise ToolError(str(exc)) from exc


def _need(args: dict, *names: str) -> None:
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError("Angabe fehlt: " + ", ".join(missing))


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=_default, ensure_ascii=False))


def _default(o: Any):
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


# ─── Auflösen von Namen ──────────────────────────────────────────────────────

def _group(db: Session, ref: Any) -> UnitGroup:
    if isinstance(ref, int) or str(ref).isdigit():
        g = db.get(UnitGroup, int(ref))
    else:
        g = db.query(UnitGroup).filter(UnitGroup.name == str(ref).strip()).first()
        if g is None:
            g = next((x for x in db.query(UnitGroup) if x.name.casefold() == str(ref).strip().casefold()), None)
    if g is None:
        raise ToolError(f"Einheiten-Gruppe „{ref}“ gibt es nicht.")
    return g


def _unit(db: Session, ref: Any) -> Unit:
    if isinstance(ref, int) or str(ref).isdigit():
        u = db.get(Unit, int(ref))
        if u:
            return u
    key = str(ref).strip()
    hits = [u for u in db.query(Unit) if key in (u.symbol, u.name) or key.casefold() in (u.symbol.casefold(), u.name.casefold())]
    exact = [u for u in hits if key in (u.symbol, u.name)]
    hits = exact or hits
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ToolError(f"Einheit „{ref}“ gibt es nicht.")
    raise ToolError(f"Einheit „{ref}“ ist nicht eindeutig: " + ", ".join(f"{u.group.name}/{u.name} (#{u.id})" for u in hits))


def _draft(db: Session) -> SchemaVersion:
    v = next((x for x in sd.list_versions(db) if x.status == "draft"), None)
    if v is None:
        raise ToolError("Es gibt keinen Schema-Entwurf. Lege zuerst einen an (create_schema_draft).")
    return v


def _entity(db: Session, ref: Any, version: SchemaVersion | None = None) -> SchemaEntity:
    version = version or _draft(db)
    for e in version.entities:
        if str(e.id) == str(ref) or e.name == str(ref) or e.display_name == str(ref):
            return e
    raise ToolError(f"Entität „{ref}“ gibt es im Entwurf nicht (vorhanden: {', '.join(e.name for e in version.entities) or '—'}).")


def _field(entity: SchemaEntity, ref: Any) -> SchemaField:
    for f in entity.fields:
        if str(f.id) == str(ref) or f.name == str(ref):
            return f
    raise ToolError(f"Feld „{ref}“ gibt es in „{entity.name}“ nicht.")


# ═══ LESEN ════════════════════════════════════════════════════════════════════

@_register("sql", "read", '{"query": "SELECT … LIMIT 50"}', "Nur-lesende SQL-Abfrage (eine SELECT-/WITH-Anweisung, max. 200 Zeilen).")
def t_sql(db: Session, a: dict):
    _need(a, "query")
    try:
        return db_tools.run_readonly_sql(db, str(a["query"]))
    except db_tools.DbToolError as exc:
        raise ToolError(str(exc)) from exc


@_register("list_units", "read", "{}", "Alle Einheiten-Gruppen mit Einheiten, Faktoren (Anzahl pro 1 Basiseinheit) und Aliassen.")
def t_list_units(db: Session, a: dict):
    from app.routers import units as r
    return _jsonable(r.list_groups(db))


@_register("list_unit_review", "read", "{}", "Prüfliste: Schreibweisen von Einheiten, die in Belegen vorkamen, aber keiner Einheit zugeordnet sind.")
def t_unit_review(db: Session, a: dict):
    return _jsonable(cu.review_groups(db))


def _schema_brief(v: SchemaVersion) -> dict:
    ents = {e.id: e for e in v.entities}
    return {
        "id": v.id, "version": v.version_number, "status": v.status, "comment": v.comment,
        "entities": [{
            "id": e.id, "name": e.name, "display_name": e.display_name, "description": e.description,
            "master_table": e.master_table, "match_fields": e.match_fields,
            "fields": [{k: val for k, val in {
                "name": f.name, "display_name": f.display_name, "type": f.data_type, "source": f.source,
                "required": f.required or None, "locked": f.is_locked or None, "enum": f.enum_values,
                "unit_group_id": f.unit_group_id, "function": f.system_function, "format": f.output_format,
                "description": f.description,
            }.items() if val not in (None, [], "")} for f in e.fields],
        } for e in v.entities],
        "relations": [{"name": r.name, "kind": r.kind, "from": ents[r.source_entity_id].name if r.source_entity_id in ents else r.source_entity_id,
                       "to": ents[r.target_entity_id].name if r.target_entity_id in ents else r.target_entity_id} for r in v.relations],
        "problems": sd.validate_version(v),
    }


@_register("get_schema", "read", '{"version": "active"|"draft"|<id>}  (Standard: active)',
           "Schema-Definition: Entitäten, Felder (Typ, Quelle llm/system, Einheiten-Gruppe …), Beziehungen und Probleme. Datentypen: "
           "text, integer, decimal, boolean, date, datetime, enum, quantity.")
def t_get_schema(db: Session, a: dict):
    ref = a.get("version", "active")
    versions = sd.list_versions(db)
    if ref == "active":
        v = sd.get_active(db)
    elif ref == "draft":
        v = next((x for x in versions if x.status == "draft"), None)
    else:
        v = sd.get_version(db, int(ref))
    if v is None:
        return {"error": "Keine solche Schema-Version vorhanden.", "versions": [{"id": x.id, "version": x.version_number, "status": x.status} for x in versions]}
    return _schema_brief(v)


@_register("list_export_templates", "read", "{}", "Alle Export-Vorlagen (Layout, Mengenmodus, Konfiguration).")
def t_list_templates(db: Session, a: dict):
    from app.routers import export_templates as r
    return _jsonable(r.list_templates(db))


@_register("export_template_columns", "read", '{"id": 1}', "Spalten, die eine Export-Vorlage erzeugt (Vorschau).")
def t_template_columns(db: Session, a: dict):
    _need(a, "id")
    from app.routers import export_templates as r
    return _jsonable(_http(r.template_columns, int(a["id"]), db))


@_register("list_workflows", "read", "{}", "Alle Workflows (id, Name, Knotenanzahl).")
def t_list_workflows(db: Session, a: dict):
    from app.routers import workflows as r
    return _jsonable([{"id": w.id, "name": w.name, "description": w.description, "nodes": w.node_count} for w in r.list_workflows(db)])


def _graph_brief(graph: dict) -> dict:
    return {
        "nodes": [{"id": n["id"], "type": n["type"], "label": (n.get("data") or {}).get("label"), "config": (n.get("data") or {}).get("config") or {}}
                  for n in graph.get("nodes", [])],
        "edges": [{"source": e["source"], "target": e["target"], **({"handle": e["sourceHandle"]} if e.get("sourceHandle") else {})}
                  for e in graph.get("edges", [])],
    }


@_register("get_workflow", "read", '{"id": 1}', "Ein Workflow als Knoten- und Kantenliste.")
def t_get_workflow(db: Session, a: dict):
    _need(a, "id")
    wf = crud.workflow.get_by_id(db, int(a["id"]))
    if wf is None:
        raise ToolError("Workflow nicht gefunden.")
    return {"id": wf.id, "name": wf.name, "description": wf.description, **_graph_brief(json.loads(wf.graph))}


@_register("workflow_node_types", "read", "{}", "Verfügbare Workflow-Knotentypen mit Konfigurationsfeldern und Ausgängen.")
def t_node_types(db: Session, a: dict):
    out = []
    for ntype in workflow_service.NODE_TYPES:
        model = workflow_service.CONFIG_MODELS.get(ntype)
        props = {}
        if model is not None:
            for k, v in model.model_json_schema().get("properties", {}).items():
                props[k] = {x: v[x] for x in ("type", "default", "enum", "description", "anyOf") if x in v}
        out.append({"type": ntype, "config": props, "handles": list(workflow_service.output_handles(ntype)) if hasattr(workflow_service, "output_handles") else None})
    return out


@_register("workflow_templates", "read", "{}", "Mitgelieferte Workflow-Vorlagen (key, Name, Graph) — gut als Ausgangspunkt für create_workflow.")
def t_wf_templates(db: Session, a: dict):
    return [{"key": t["key"], "name": t["name"], "description": t.get("description"), **_graph_brief(t["graph"])} for t in workflow_service.templates()]


@_register("list_prompts", "read", "{}", "Systemprompts (id, Name, Typ 0=Dokumententyp 1=Extraktion 2=Baustein 3=Antwortformat) — IDs für Workflow-Knoten.")
def t_prompts(db: Session, a: dict):
    return [{"id": p.id, "name": p.name, "type": p.type} for p in crud.system_prompt.get_all(db)]


@_register("list_ai_clients", "read", "{}", "KI-Konfigurationen (id, Name, Modell) — IDs für Workflow-Knoten und Importe.")
def t_ai_clients(db: Session, a: dict):
    return [{"id": c.id, "name": c.name, "model": c.model_name, "active": bool(c.active)} for c in crud.ai_config.get_all(db)]


@_register("list_imports", "read", '{"limit": 30}', "Letzte Imports (Firma, Jahr, Status, Belegzahl, Workflow).")
def t_imports(db: Session, a: dict):
    from sqlalchemy import func
    from app.models.document import Document
    limit = min(int(a.get("limit") or 30), MAX_LIST)
    rows = db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(limit).all()
    counts = dict(db.query(Document.batch_id, func.count(Document.id)).filter(Document.batch_id.in_([r.id for r in rows] or [0])).group_by(Document.batch_id).all())
    return [{"id": b.id, "company": b.company_name, "year": b.year, "status": b.status, "documents": counts.get(b.id, 0),
             "workflow_id": getattr(b, "workflow_id", None), "hidden": bool(getattr(b, "hidden_at", None)),
             "created_at": b.created_at.isoformat() if b.created_at else None} for b in rows]


@_register("list_import_folders", "read", "{}", "Unterordner von IMPORT_BASE_PATH mit Anzahl PDF-Dateien (für create_import → subfolder).")
def t_folders(db: Session, a: dict):
    from app.config import settings
    base = settings.import_base_path
    out = []
    try:
        pdf_root = sum(1 for f in os.listdir(base) if f.lower().endswith(".pdf"))
        out.append({"subfolder": "", "pdf_files": pdf_root})
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isdir(p):
                out.append({"subfolder": name, "pdf_files": sum(1 for f in os.listdir(p) if f.lower().endswith(".pdf"))})
    except OSError as exc:
        raise ToolError(f"Import-Ordner nicht lesbar: {exc}") from exc
    return {"import_base_path": base, "folders": out[:MAX_LIST]}


@_register("non_invoice_summary", "read", "{}", "Wie viele erkannte Nicht-Eingangsrechnungen (je Dokumententyp) es gibt und wie viele Belege im Papierkorb liegen.")
def t_non_invoices(db: Session, a: dict):
    from app.services import document_trash
    return document_trash.non_invoice_summary(db)


# ═══ SCHREIBEN (nur als Vorschlag) ═══════════════════════════════════════════

# ── Einheiten ──
@_register("create_unit_group", "write", '{"name": "Masse", "description": ""}', "Einheiten-Gruppe anlegen.",
           lambda a: f"Einheiten-Gruppe „{a.get('name')}“ anlegen")
def w_create_group(db: Session, a: dict):
    _need(a, "name")
    g = cu.create_group(db, a["name"], a.get("description"))
    return {"id": g.id, "name": g.name}


@_register("rename_unit_group", "write", '{"group": "Masse", "name": "Gewicht"}', "Gruppe umbenennen.",
           lambda a: f"Gruppe „{a.get('group')}“ in „{a.get('name')}“ umbenennen")
def w_rename_group(db: Session, a: dict):
    _need(a, "group", "name")
    g = cu.update_group(db, _group(db, a["group"]), a["name"], a.get("description"))
    return {"id": g.id, "name": g.name}


@_register("delete_unit_group", "write", '{"group": "Masse"}', "Gruppe samt Einheiten löschen.",
           lambda a: f"Gruppe „{a.get('group')}“ mit allen Einheiten LÖSCHEN")
def w_delete_group(db: Session, a: dict):
    _need(a, "group")
    g = _group(db, a["group"])
    name = g.name
    db.delete(g)
    db.flush()
    return {"deleted": name}


@_register("create_unit", "write",
           '{"group": "Masse", "name": "Kilogramm", "symbol": "kg", "factor": 1000, "is_base": false, "case_insensitive": false, "aliases": ["kg","Kilo"]}',
           "Einheit anlegen. factor = Anzahl dieser Einheit pro 1 Basiseinheit (Basis Tonne: kg = 1000). Die Basiseinheit hat Faktor 1 und is_base=true.",
           lambda a: f"Einheit „{a.get('name')}“ ({a.get('symbol')}) in Gruppe „{a.get('group')}“ anlegen, Faktor {a.get('factor', 1)}{' (Basis)' if a.get('is_base') else ''}")
def w_create_unit(db: Session, a: dict):
    _need(a, "group", "name", "symbol")
    u = cu.create_unit(db, _group(db, a["group"]), a["name"], a["symbol"], a.get("factor", 1), is_base=bool(a.get("is_base")),
                       case_insensitive=bool(a.get("case_insensitive")), aliases=a.get("aliases"))
    return {"id": u.id, "name": u.name}


@_register("update_unit", "write", '{"unit": "kg", "name": "…", "symbol": "…", "factor": 1000, "case_insensitive": true}', "Einheit ändern (nur angegebene Felder).",
           lambda a: f"Einheit „{a.get('unit')}“ ändern: " + ", ".join(f"{k}={v}" for k, v in a.items() if k != "unit"))
def w_update_unit(db: Session, a: dict):
    _need(a, "unit")
    changes = {k: a[k] for k in ("name", "symbol", "factor", "case_insensitive") if k in a}
    u = cu.update_unit(db, _unit(db, a["unit"]), **changes)
    return {"id": u.id, "name": u.name}


@_register("delete_unit", "write", '{"unit": "kg"}', "Einheit löschen (die Basiseinheit erst, wenn sie die letzte der Gruppe ist).",
           lambda a: f"Einheit „{a.get('unit')}“ löschen")
def w_delete_unit(db: Session, a: dict):
    _need(a, "unit")
    u = _unit(db, a["unit"])
    name = u.name
    cu.delete_unit(db, u)
    return {"deleted": name}


@_register("add_unit_alias", "write", '{"unit": "kg", "alias": "Kilo"}', "Schreibweise (Alias) zu einer Einheit hinzufügen.",
           lambda a: f"Alias „{a.get('alias')}“ zur Einheit „{a.get('unit')}“ hinzufügen")
def w_add_alias(db: Session, a: dict):
    _need(a, "unit", "alias")
    from app.routers import units as r
    return _jsonable(_http(r.add_alias, _unit(db, a["unit"]).id, r.AliasIn(alias=a["alias"]), db))


@_register("assign_review_alias", "write", '{"alias": "Ztr", "unit": "kg"}',
           "Schreibweise aus der Prüfliste einer bestehenden Einheit zuordnen (legt den Alias an und rechnet offene Belege neu).",
           lambda a: f"Prüflisten-Schreibweise „{a.get('alias')}“ der Einheit „{a.get('unit')}“ zuordnen")
def w_assign(db: Session, a: dict):
    _need(a, "alias", "unit")
    from app.routers import units as r
    return _jsonable(_http(r.assign, r.AssignIn(alias=a["alias"], unit_id=_unit(db, a["unit"]).id), db))


# ── Schema ──
@_register("create_schema_draft", "write", '{"comment": "…"}', "Neuen Schema-Entwurf aus der aktiven Version anlegen (nötig, bevor Entitäten/Felder geändert werden).",
           lambda a: "Schema-Entwurf aus der aktiven Version anlegen")
def w_draft(db: Session, a: dict):
    if any(v.status == "draft" for v in sd.list_versions(db)):
        raise ToolError("Es gibt bereits einen Entwurf — erst aktivieren oder verwerfen.")
    base = sd.get_active(db)
    d = sd.create_draft_from(db, base, a.get("comment")) if base else sd.create_version(db, a.get("comment"))
    return {"id": d.id, "version": d.version_number}


@_register("add_schema_entity", "write", '{"name": "kostenstelle", "display_name": "Kostenstelle", "description": "", "master_table": null, "match_fields": null}',
           "Entität im Entwurf anlegen.", lambda a: f"Entität „{a.get('display_name') or a.get('name')}“ im Schema-Entwurf anlegen")
def w_add_entity(db: Session, a: dict):
    _need(a, "name", "display_name")
    e = sd.add_entity(db, _draft(db), a["name"], a["display_name"], a.get("description"), master_table=a.get("master_table"), match_fields=a.get("match_fields"))
    return {"id": e.id, "name": e.name}


@_register("add_schema_field", "write",
           '{"entity": "position", "name": "lagerort", "display_name": "Lagerort", "data_type": "text", "required": false, "description": "…", '
           '"output_format": null, "enum_values": null, "unit_group": "Masse" (nur bei data_type quantity), "normalizers": [{"name":"trim","params":{}}]}',
           "Feld einer Entität im Entwurf anlegen (Quelle: die KI liest es). Datentypen: text, integer, decimal, boolean, date, datetime, enum, quantity.",
           lambda a: f"Feld „{a.get('name')}“ ({a.get('data_type')}) in Entität „{a.get('entity')}“ anlegen")
def w_add_field(db: Session, a: dict):
    _need(a, "entity", "name", "display_name", "data_type")
    ent = _entity(db, a["entity"])
    kw = {k: a[k] for k in ("required", "output_format", "description", "enum_values", "normalizers", "source", "system_function", "system_params") if k in a}
    if a.get("unit_group"):
        kw["unit_group_id"] = _group(db, a["unit_group"]).id
    f = sd.add_field(db, ent, a["name"], a["display_name"], a["data_type"], **kw)
    return {"id": f.id, "name": f.name}


@_register("update_schema_field", "write", '{"entity": "position", "field": "lagerort", "display_name": "…", "description": "…", "data_type": "…", …}',
           "Feld im Entwurf ändern (nur angegebene Angaben).", lambda a: f"Feld „{a.get('field')}“ in „{a.get('entity')}“ ändern")
def w_update_field(db: Session, a: dict):
    _need(a, "entity", "field")
    f = _field(_entity(db, a["entity"]), a["field"])
    kw = {k: a[k] for k in ("name", "display_name", "data_type", "required", "output_format", "description", "enum_values", "normalizers",
                            "source", "system_function", "system_params") if k in a}
    if a.get("unit_group"):
        kw["unit_group_id"] = _group(db, a["unit_group"]).id
    sd.update_field(db, f, **kw)
    return {"id": f.id, "name": f.name}


@_register("delete_schema_field", "write", '{"entity": "position", "field": "lagerort"}', "Feld im Entwurf löschen.",
           lambda a: f"Feld „{a.get('field')}“ aus „{a.get('entity')}“ löschen")
def w_delete_field(db: Session, a: dict):
    _need(a, "entity", "field")
    sd.delete_field(db, _field(_entity(db, a["entity"]), a["field"]))
    return {"deleted": a["field"]}


@_register("delete_schema_entity", "write", '{"entity": "kostenstelle"}', "Entität im Entwurf löschen.",
           lambda a: f"Entität „{a.get('entity')}“ aus dem Entwurf löschen")
def w_delete_entity(db: Session, a: dict):
    _need(a, "entity")
    sd.delete_entity(db, _entity(db, a["entity"]))
    return {"deleted": a["entity"]}


@_register("add_schema_relation", "write", '{"name": "positionen", "kind": "parent_child"|"reference", "source": "beleg", "target": "position", "description": ""}',
           "Beziehung im Entwurf anlegen.", lambda a: f"Beziehung „{a.get('name')}“ ({a.get('kind')}) {a.get('source')} → {a.get('target')} anlegen")
def w_add_relation(db: Session, a: dict):
    _need(a, "name", "kind", "source", "target")
    v = _draft(db)
    r = sd.add_relation(db, v, a["name"], a["kind"], _entity(db, a["source"], v), _entity(db, a["target"], v), a.get("description"))
    return {"id": r.id}


@_register("activate_schema_draft", "write", "{}", "Den Entwurf aktivieren (danach unveränderlich; neue Analysen nutzen ihn).",
           lambda a: "Schema-Entwurf AKTIVIEREN (danach unveränderlich)")
def w_activate(db: Session, a: dict):
    v = sd.activate(db, _draft(db))
    return {"version": v.version_number}


@_register("discard_schema_draft", "write", "{}", "Den Entwurf verwerfen.", lambda a: "Schema-Entwurf verwerfen")
def w_discard(db: Session, a: dict):
    sd.discard_draft(db, _draft(db))
    return {"discarded": True}


# ── Export-Vorlagen ──
def _template_body(db: Session, a: dict, current: ExportTemplate | None = None):
    from app.routers import export_templates as r
    return r.TemplateIn(
        name=a.get("name", current.name if current else ""),
        description=a.get("description", current.description if current else None),
        layout=a.get("layout", current.layout if current else "sheet_per_entity"),
        quantity_mode=a.get("quantity_mode", current.quantity_mode if current else "original"),
        config=a.get("config", dict(current.config or {}) if current else {}),
    )


@_register("create_export_template", "write",
           '{"name": "…", "description": "", "layout": "sheet_per_entity"|"flat", "quantity_mode": "original"|"normalized", '
           '"config": {"entities": [{"entity": "beleg", "fields": ["rechnungsnummer"]}], "vendor": ["name"], "flat": {"root": "beleg", "child": "position"}}}',
           "Export-Vorlage anlegen (Entitäten/Felder müssen im aktiven Schema existieren; fehlendes fields = alle Felder).",
           lambda a: f"Export-Vorlage „{a.get('name')}“ anlegen ({a.get('layout', 'sheet_per_entity')})")
def w_create_template(db: Session, a: dict):
    _need(a, "name")
    from app.routers import export_templates as r
    return _jsonable(_http(r.create_template, _template_body(db, a), db))


@_register("update_export_template", "write", '{"id": 1, "name": "…", "layout": "…", "quantity_mode": "…", "config": {…}}  (nur Geändertes angeben)',
           "Export-Vorlage ändern.", lambda a: f"Export-Vorlage #{a.get('id')} ändern: " + ", ".join(k for k in a if k != "id"))
def w_update_template(db: Session, a: dict):
    _need(a, "id")
    from app.routers import export_templates as r
    t = db.get(ExportTemplate, int(a["id"]))
    if t is None:
        raise ToolError("Vorlage nicht gefunden.")
    return _jsonable(_http(r.update_template, t.id, _template_body(db, a, t), db))


@_register("delete_export_template", "write", '{"id": 1}', "Export-Vorlage löschen.", lambda a: f"Export-Vorlage #{a.get('id')} löschen")
def w_delete_template(db: Session, a: dict):
    _need(a, "id")
    from app.routers import export_templates as r
    _http(r.delete_template, int(a["id"]), db)
    return {"deleted": int(a["id"])}


# ── Workflows ──
def build_graph(nodes: list[dict], edges: list[dict]) -> dict:
    """
    Vereinfachte Knoten-/Kantenliste → vollständiger Graph mit Layout.
    nodes: [{"id","type","label"?,"config"?}], edges: [{"source","target","handle"?}] (handle: "true"/"false" bei branch).
    """
    if not isinstance(nodes, list) or not nodes:
        raise ToolError("'nodes' muss eine Liste mit mindestens einem Knoten sein (beginnend mit einem start-Knoten).")
    ids = [str(n.get("id")) for n in nodes]
    if len(set(ids)) != len(ids):
        raise ToolError("Knoten-IDs müssen eindeutig sein.")
    nxt: dict[str, list[str]] = {}
    for e in edges or []:
        nxt.setdefault(str(e.get("source")), []).append(str(e.get("target")))
    depth = {ids[0]: 0}
    queue = [ids[0]]
    for cur in queue:
        for t in nxt.get(cur, []):
            if t not in depth:
                depth[t] = depth[cur] + 1
                queue.append(t)
    rows: dict[int, int] = {}
    out_nodes = []
    for n in nodes:
        nid = str(n["id"])
        d = depth.get(nid, 0)
        row = rows.get(d, 0)
        rows[d] = row + 1
        if "type" not in n:
            raise ToolError(f"Knoten „{nid}“ hat keinen 'type'.")
        out_nodes.append(workflow_service._node(nid, n["type"], n.get("label") or n["type"], d * 260, 60 + row * 200, n.get("config") or {}))
    out_edges = [workflow_service._edge(str(e["source"]), str(e["target"]), e.get("handle") or e.get("sourceHandle")) for e in edges or []]
    return {"nodes": out_nodes, "edges": out_edges}


def _wf_graph(a: dict) -> dict | None:
    if a.get("nodes") is not None:
        return build_graph(a["nodes"], a.get("edges") or [])
    return None


@_register("create_workflow", "write",
           '{"name": "…", "description": "", "template": "<key aus workflow_templates>" ODER "nodes": [{"id":"start","type":"start"},{"id":"x","type":"extract","label":"…","config":{}}], '
           '"edges": [{"source":"start","target":"x"},{"source":"b","target":"y","handle":"true"}]}',
           "Workflow anlegen. Ohne template/nodes entsteht ein leerer Workflow. Der Graph wird mit denselben Regeln wie im Editor geprüft "
           "(genau ein start, je Ausgang höchstens eine Kante, keine Zyklen).",
           lambda a: f"Workflow „{a.get('name')}“ anlegen" + (f" (Vorlage {a['template']})" if a.get("template") else f" ({len(a.get('nodes') or [])} Knoten)"))
def w_create_wf(db: Session, a: dict):
    _need(a, "name")
    from app.routers import workflows as r
    graph = _wf_graph(a)
    payload = r.WorkflowCreate(name=a["name"], description=a.get("description"), graph=graph, template=a.get("template"))
    return _jsonable(_http(r.create_workflow, payload, db).model_dump(include={"id", "name"}))


@_register("update_workflow", "write", '{"id": 1, "name": "…", "description": "…", "nodes": […], "edges": […]}  (nodes/edges immer vollständig angeben, wenn der Ablauf geändert wird)',
           "Workflow ändern. Aktuellen Stand vorher mit get_workflow lesen.",
           lambda a: f"Workflow #{a.get('id')} ändern" + (f" (neuer Ablauf mit {len(a['nodes'])} Knoten)" if a.get("nodes") else ""))
def w_update_wf(db: Session, a: dict):
    _need(a, "id")
    from app.routers import workflows as r
    wf = crud.workflow.get_by_id(db, int(a["id"]))
    if wf is None:
        raise ToolError("Workflow nicht gefunden.")
    graph = _wf_graph(a) or json.loads(wf.graph)
    payload = r.WorkflowUpdate(name=a.get("name", wf.name), description=a.get("description", wf.description), graph=graph)
    return _jsonable(_http(r.update_workflow, wf.id, payload, db).model_dump(include={"id", "name"}))


@_register("delete_workflow", "write", '{"id": 1}', "Workflow löschen.", lambda a: f"Workflow #{a.get('id')} löschen")
def w_delete_wf(db: Session, a: dict):
    _need(a, "id")
    from app.routers import workflows as r
    _http(r.delete_workflow, int(a["id"]), db)
    return {"deleted": int(a["id"])}


# ── Eigene Gesellschaften (Rechnungsempfänger) ──
_COMPANY_ARGS = '"name": "Muster GmbH", "aliases": ["Muster Handel"], "vat_ids": ["DE123456789"], "tax_numbers": [], "ibans": ["DE02 …"], "note": "", "active": true'


def _company(db: Session, ref: Any):
    from app.models.own_company import OwnCompany
    from app.services.supplier_dedup import normalize_name
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        c = db.get(OwnCompany, int(ref))
        if c is not None:
            return c
    key = normalize_name(str(ref))
    found = [c for c in db.query(OwnCompany).all() if key and (normalize_name(c.name) == key or key in [normalize_name(x) for x in (c.aliases or [])])]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ToolError(f"„{ref}“ ist nicht eindeutig — bitte die ID nennen ({', '.join(f'#{c.id}' for c in found)}).")
    raise ToolError(f"Gesellschaft „{ref}“ gibt es nicht (list_own_companies zeigt die vorhandenen).")


@_register("list_own_companies", "read", "{}",
           "Eigene Gesellschaften (mögliche Rechnungsempfänger): id, Name, weitere Schreibweisen, USt-IdNrn., Steuernummern, IBANs, aktiv. "
           "Wird zur Prüfung genutzt, ob als Lieferant fälschlich eine eigene Firma gelesen wurde.")
def t_list_companies(db: Session, a: dict):
    from app.routers import own_companies as r
    return [r._out(c) for c in db.query(r.OwnCompany).order_by(r.OwnCompany.id).all()]


@_register("own_company_suggestions", "read", '{"min_count": 2}',
           "Häufig gelesene Rechnungsempfänger, die noch nicht in der Liste eigener Gesellschaften stehen (Vorschläge zum Anlegen).")
def t_company_suggestions(db: Session, a: dict):
    from app.services import own_companies as oc
    return oc.suggestions(db, min_count=max(1, int(a.get("min_count") or 2)))


@_register("create_own_company", "write", "{" + _COMPANY_ARGS + "}",
           "Eigene Gesellschaft anlegen. Alle Angaben sind optional, mindestens eine Kennung (Name, Schreibweise, USt-IdNr., Steuernummer oder IBAN) ist nötig; USt-IdNr./Steuernummer/IBAN sind Listen (mehrere möglich).",
           lambda a: f"Eigene Gesellschaft anlegen: {a.get('name') or (a.get('vat_ids') or a.get('ibans') or ['(ohne Namen)'])[0]}")
def w_create_company(db: Session, a: dict):
    from app.routers import own_companies as r
    body = r.CompanyIn(**{k: a.get(k) for k in ("name", "aliases", "vat_ids", "tax_numbers", "ibans", "note") if k in a}, active=a.get("active", True))
    return _jsonable(_http(r.create_company, body, db))


@_register("update_own_company", "write", '{"company": "Muster GmbH" oder ID, ' + _COMPANY_ARGS + '}  (nur Geändertes angeben; Listen ersetzen die bisherigen komplett)',
           "Eigene Gesellschaft ändern. Angegebene Felder ersetzen die bisherigen; nicht genannte bleiben.",
           lambda a: f"Eigene Gesellschaft ändern: {a.get('company')}")
def w_update_company(db: Session, a: dict):
    _need(a, "company")
    from app.routers import own_companies as r
    c = _company(db, a["company"])
    cur = r._out(c)
    for k in ("name", "aliases", "vat_ids", "tax_numbers", "ibans", "note", "active"):
        if k in a:
            cur[k] = a[k]
    return _jsonable(_http(r.update_company, c.id, r.CompanyIn(**{k: cur[k] for k in ("name", "aliases", "vat_ids", "tax_numbers", "ibans", "note", "active")}), db))


@_register("delete_own_company", "write", '{"company": "Muster GmbH" oder ID}', "Eigene Gesellschaft aus der Liste löschen.",
           lambda a: f"Eigene Gesellschaft löschen: {a.get('company')}")
def w_delete_company(db: Session, a: dict):
    _need(a, "company")
    from app.routers import own_companies as r
    c = _company(db, a["company"])
    _http(r.delete_company, c.id, db)
    return {"deleted": c.id}


# ── Papierkorb ──
def _trash_ids(db: Session, a: dict) -> list[int]:
    from app import crud
    if a.get("not_invoice"):
        return crud.document.get_ids_filtered(db, not_invoice=True, include_hidden=True)
    ids = a.get("document_ids")
    if not isinstance(ids, list) or not ids:
        raise ToolError("Angabe fehlt: 'not_invoice': true (alle erkannten Nicht-Eingangsrechnungen) ODER 'document_ids': [...]")
    try:
        return [int(i) for i in ids][:20000]
    except (TypeError, ValueError):
        raise ToolError("document_ids muss eine Liste von Beleg-IDs sein") from None


@_register("trash_documents", "write", '{"not_invoice": true, "reason": "Keine Eingangsrechnung"} ODER {"document_ids": [1, 2], "reason": "…"}',
           "Belege in den Papierkorb legen (umkehrbar). not_invoice=true = alle erkannten Nicht-Eingangsrechnungen (Mischbelege und noch nicht erkannte Belege nie). "
           "Endgültig löschen kann nur der Nutzer auf der Seite „Papierkorb“.",
           lambda a: ("Alle erkannten Nicht-Eingangsrechnungen" if a.get("not_invoice") else f"{len(a.get('document_ids') or [])} Beleg(e)") + " in den Papierkorb legen")
def w_trash(db: Session, a: dict):
    from app.services import document_trash
    ids = _trash_ids(db, a)
    if not ids:
        raise ToolError("Keine passenden Belege gefunden.")
    res = document_trash.trash(db, ids, str(a.get("reason") or "Vom Assistenten aussortiert")[:300])
    return {"in_papierkorb": res.changed, "uebersprungen": res.skipped}


@_register("restore_documents", "write", '{"document_ids": [1, 2]} ODER {"all": true}', "Belege aus dem Papierkorb zurückholen.",
           lambda a: ("Alle Belege" if a.get("all") else f"{len(a.get('document_ids') or [])} Beleg(e)") + " aus dem Papierkorb zurückholen")
def w_restore(db: Session, a: dict):
    from app import crud
    from app.services import document_trash
    ids = crud.document.get_ids_filtered(db, only_deleted=True, include_hidden=True) if a.get("all") else _trash_ids(db, {"document_ids": a.get("document_ids")})
    return {"zurueckgeholt": document_trash.restore(db, ids).changed}


# ── Import ──
@_register("create_import", "write",
           '{"company_name": "Muster GmbH", "year": 2025, "subfolder": "Muster_2025", "analyze_after_import": true, "workflow_id": 3, "ai_config_id": null, '
           '"delete_source_files": false, "folder_sync": false, "comment": ""}',
           "Neuen Import aus einem Unterordner von IMPORT_BASE_PATH starten (Ordner vorher mit list_import_folders prüfen). "
           "Bei analyze_after_import/folder_sync ist workflow_id Pflicht.",
           lambda a: f"Import „{a.get('company_name')} {a.get('year')}“ aus Ordner „{a.get('subfolder') or '(Basisordner)'}“ starten"
                     + (f", danach KI-Analyse mit Workflow #{a.get('workflow_id')}" if a.get("analyze_after_import") else "")
                     + (", Quelldateien danach löschen" if a.get("delete_source_files") else ""))
async def w_create_import(db: Session, a: dict):
    _need(a, "company_name", "year")
    from app.routers import imports as r
    from app.schemas.import_batch import ImportBatchCreate
    fields = {k: a[k] for k in ("company_name", "year", "subfolder", "comment", "ai_config_id", "system_prompt_id", "workflow_id",
                                "analyze_after_import", "delete_source_files", "folder_sync", "auto_export") if k in a}
    try:
        payload = ImportBatchCreate(**fields)
        batch = await r.start_import(payload, db)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc
    except ValidationError as exc:
        raise ToolError("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc
    return {"id": batch.id, "company": batch.company_name, "year": batch.year}


# ─── Zugriff für den Dienst ──────────────────────────────────────────────────

def read_tools() -> list[Tool]:
    return [t for t in TOOLS.values() if t.kind == "read"]


def write_tools() -> list[Tool]:
    return [t for t in TOOLS.values() if t.kind == "write"]


def summarize(tool: str, args: dict) -> str:
    t = TOOLS[tool]
    try:
        return t.summary(args) if t.summary else tool
    except Exception:
        return tool


def run_read(db: Session, tool: str, args: dict) -> Any:
    """Lesewerkzeug ausführen (ToolError bei Fehlern)."""
    return TOOLS[tool].handler(db, args)


async def execute_write(db: Session, tool: str, args: dict) -> Any:
    """Freigegebenen Vorschlag ausführen. Commit bei Erfolg, Rollback bei Fehler (→ ToolError)."""
    t = TOOLS.get(tool)
    if t is None or t.kind != "write":
        raise ToolError(f"Unbekanntes Schreib-Werkzeug „{tool}“.")
    try:
        res = t.handler(db, args)
        if inspect.isawaitable(res):
            res = await res
        db.commit()
        return _jsonable(res)
    except ToolError:
        db.rollback()
        raise
    except (cu.UnitError, sd.SchemaError) as exc:
        db.rollback()
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Assistent-Werkzeug %s fehlgeschlagen", tool)
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


def tools_prompt() -> str:
    lines = ["LESEN (laufen sofort, Ergebnis kommt zurück):"]
    for t in read_tools():
        lines.append(f'- {{"tool": "{t.name}", "args": {t.args}}} — {t.doc}')
    lines.append("\nSCHREIBEN (nur Vorschlag — der Nutzer gibt jeden einzeln frei; du siehst das Ergebnis erst in der nächsten Nachricht):")
    for t in write_tools():
        lines.append(f'- {{"tool": "{t.name}", "args": {t.args}, "reason": "kurze Begründung"}} — {t.doc}')
    return "\n".join(lines)
