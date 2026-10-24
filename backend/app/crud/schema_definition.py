"""
CRUD für das dynamische Extraktionsschema (Meta-Tabellen, siehe models/schema_def.py).

Regeln:
- Eine Version ist nach dem Aktivieren unveränderlich. Änderungen laufen über `create_draft_from()` (Kopie) → bearbeiten → `activate()`.
- Die Standardfelder `document_id` und `vendor_id` entstehen automatisch mit jeder Nicht-Stammdaten-Entität und sind gesperrt.
- Namen sind technische Bezeichner (snake_case), eindeutig je Version bzw. Entität.
"""

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.schema_def import (
    DATA_TYPES,
    FIELD_SOURCES,
    RELATION_KINDS,
    STANDARD_FIELDS,
    SchemaEntity,
    SchemaField,
    SchemaRelation,
    SchemaVersion,
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_RESERVED = {"id", "data", "parent_record_id", "entity_id", "schema_version_id", "position_index"}


class SchemaError(ValueError):
    """Verstoß gegen eine Schema-Regel (wird vom Router als HTTP 400/409 gemeldet)."""


# ─── Versionen ───────────────────────────────────────────────────────────────

def get_version(db: Session, version_id: int) -> SchemaVersion | None:
    return db.get(SchemaVersion, version_id)


def get_active(db: Session) -> SchemaVersion | None:
    return db.query(SchemaVersion).filter(SchemaVersion.status == "active").order_by(SchemaVersion.version_number.desc()).first()


def list_versions(db: Session) -> list[SchemaVersion]:
    return db.query(SchemaVersion).order_by(SchemaVersion.version_number.desc()).all()


def _next_number(db: Session) -> int:
    return (db.query(func.max(SchemaVersion.version_number)).scalar() or 0) + 1


def create_version(db: Session, comment: str | None = None) -> SchemaVersion:
    version = SchemaVersion(version_number=_next_number(db), status="draft", comment=comment)
    db.add(version)
    db.flush()
    return version


def create_draft_from(db: Session, base: SchemaVersion, comment: str | None = None) -> SchemaVersion:
    """Kopiert Entitäten, Felder und Beziehungen in eine neue Entwurfs-Version."""
    if db.query(SchemaVersion).filter(SchemaVersion.status == "draft").first():
        raise SchemaError("Es gibt bereits einen Entwurf — erst aktivieren oder verwerfen.")
    draft = create_version(db, comment)
    mapping: dict[int, SchemaEntity] = {}
    for ent in base.entities:
        new_ent = SchemaEntity(
            version_id=draft.id, name=ent.name, display_name=ent.display_name, description=ent.description,
            position=ent.position, master_table=ent.master_table,
            match_fields=list(ent.match_fields) if ent.match_fields else None,
        )
        db.add(new_ent)
        db.flush()
        mapping[ent.id] = new_ent
        for f in ent.fields:
            db.add(SchemaField(
                entity_id=new_ent.id, name=f.name, display_name=f.display_name, data_type=f.data_type, required=f.required,
                output_format=f.output_format, description=f.description, position=f.position, source=f.source,
                is_locked=f.is_locked, enum_values=list(f.enum_values) if f.enum_values else None,
                unit_group_id=f.unit_group_id, system_function=f.system_function,
                system_params=dict(f.system_params) if f.system_params else None,
                normalizers=[dict(n) for n in f.normalizers] if f.normalizers else None,
            ))
    for rel in base.relations:
        db.add(SchemaRelation(
            version_id=draft.id, name=rel.name, kind=rel.kind, description=rel.description,
            source_entity_id=mapping[rel.source_entity_id].id, target_entity_id=mapping[rel.target_entity_id].id,
        ))
    db.flush()
    return draft


def activate(db: Session, version: SchemaVersion) -> SchemaVersion:
    """Macht einen Entwurf zur aktiven Version; die bisherige wird archiviert (bleibt für Altdaten lesbar)."""
    if version.status == "active":
        return version
    if version.status != "draft":
        raise SchemaError("Nur ein Entwurf kann aktiviert werden.")
    problems = validate_version(version)
    if problems:
        raise SchemaError("Schema ungültig: " + "; ".join(problems))
    for old in db.query(SchemaVersion).filter(SchemaVersion.status == "active").all():
        old.status = "archived"
    version.status = "active"
    version.activated_at = func.now()
    db.flush()
    return version


def discard_draft(db: Session, version: SchemaVersion) -> None:
    if version.status != "draft":
        raise SchemaError("Nur ein Entwurf kann verworfen werden.")
    db.delete(version)
    db.flush()


def validate_version(version: SchemaVersion) -> list[str]:
    """Prüft die Gesamtdefinition; leere Liste = in Ordnung."""
    problems: list[str] = []
    if not version.entities:
        problems.append("keine Entität definiert")
    ids = {e.id for e in version.entities}
    for ent in version.entities:
        names = {f.name for f in ent.fields}
        if not ent.is_master:
            for std in STANDARD_FIELDS:
                if std not in names:
                    problems.append(f"{ent.name}: Standardfeld {std} fehlt")
        elif ent.match_fields:
            for m in ent.match_fields:
                if m not in names:
                    problems.append(f"{ent.name}: Matching-Feld {m} existiert nicht")
        for f in ent.fields:
            if f.data_type == "enum" and not f.enum_values:
                problems.append(f"{ent.name}.{f.name}: Enum ohne Werte")
            if f.source == "system" and not f.is_locked and not f.system_function:
                problems.append(f"{ent.name}.{f.name}: Systemfeld ohne Funktion")
    for rel in version.relations:
        if rel.source_entity_id not in ids or rel.target_entity_id not in ids:
            problems.append(f"Beziehung {rel.name}: Entität gehört zu einer anderen Version")
    return problems


# ─── Bearbeiten (nur Entwurf) ────────────────────────────────────────────────

def _require_draft(version: SchemaVersion) -> None:
    if version.status != "draft":
        raise SchemaError("Aktive und archivierte Versionen sind unveränderlich — erst einen Entwurf anlegen.")


def _check_name(name: str, what: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise SchemaError(f"{what}: Name muss aus Kleinbuchstaben, Ziffern und _ bestehen und mit einem Buchstaben beginnen")


def add_entity(
    db: Session, version: SchemaVersion, name: str, display_name: str, description: str | None = None,
    master_table: str | None = None, match_fields: list[str] | None = None,
) -> SchemaEntity:
    _require_draft(version)
    _check_name(name, "Entität")
    if any(e.name == name for e in version.entities):
        raise SchemaError(f"Entität {name} existiert bereits")
    ent = SchemaEntity(
        version_id=version.id, name=name, display_name=display_name, description=description,
        position=len(version.entities), master_table=master_table, match_fields=match_fields,
    )
    db.add(ent)
    db.flush()
    db.refresh(version)
    if not master_table:
        for pos, (fname, spec) in enumerate(STANDARD_FIELDS.items()):
            db.add(SchemaField(
                entity_id=ent.id, name=fname, display_name=spec["display_name"], data_type=spec["data_type"],
                description=spec["description"], position=pos, source="system", is_locked=True,
            ))
        db.flush()
    db.refresh(ent)
    return ent


def delete_entity(db: Session, entity: SchemaEntity) -> None:
    _require_draft(entity.version)
    used = (
        db.query(SchemaRelation)
        .filter((SchemaRelation.source_entity_id == entity.id) | (SchemaRelation.target_entity_id == entity.id))
        .first()
    )
    if used:
        raise SchemaError(f"Entität {entity.name} wird von der Beziehung {used.name} verwendet")
    db.delete(entity)
    db.flush()


def add_field(
    db: Session, entity: SchemaEntity, name: str, display_name: str, data_type: str, *,
    required: bool = False, output_format: str | None = None, description: str | None = None,
    source: str = "llm", enum_values: list[str] | None = None, unit_group_id: int | None = None,
    system_function: str | None = None, system_params: dict | None = None, normalizers: list[dict] | None = None,
) -> SchemaField:
    _require_draft(entity.version)
    _check_name(name, "Feld")
    if name in STANDARD_FIELDS or name in _RESERVED:
        raise SchemaError(f"Feldname {name} ist reserviert")
    if data_type not in DATA_TYPES:
        raise SchemaError(f"Unbekannter Datentyp: {data_type}")
    if source not in FIELD_SOURCES:
        raise SchemaError(f"Unbekannte Quelle: {source}")
    if source == "system" and not system_function:
        raise SchemaError("Systemfelder brauchen eine Funktion")
    if data_type == "enum" and not enum_values:
        raise SchemaError("Enum-Felder brauchen Werte")
    if any(f.name == name for f in entity.fields):
        raise SchemaError(f"Feld {name} existiert bereits in {entity.name}")
    field = SchemaField(
        entity_id=entity.id, name=name, display_name=display_name, data_type=data_type, required=required,
        output_format=output_format, description=description, position=len(entity.fields), source=source,
        enum_values=enum_values, unit_group_id=unit_group_id, system_function=system_function,
        system_params=system_params, normalizers=normalizers,
    )
    db.add(field)
    db.flush()
    db.refresh(entity)
    return field


_EDITABLE = {
    "display_name", "data_type", "required", "output_format", "description", "source", "enum_values",
    "unit_group_id", "system_function", "system_params", "normalizers", "name",
}


def update_field(db: Session, field: SchemaField, **changes) -> SchemaField:
    _require_draft(field.entity.version)
    if field.is_locked:
        raise SchemaError(f"{field.name} ist ein Standardfeld und kann nicht geändert werden")
    unknown = set(changes) - _EDITABLE
    if unknown:
        raise SchemaError("Nicht änderbar: " + ", ".join(sorted(unknown)))
    if "name" in changes:
        _check_name(changes["name"], "Feld")
        if changes["name"] in STANDARD_FIELDS or changes["name"] in _RESERVED:
            raise SchemaError(f"Feldname {changes['name']} ist reserviert")
        if any(f.name == changes["name"] and f.id != field.id for f in field.entity.fields):
            raise SchemaError(f"Feld {changes['name']} existiert bereits")
    if changes.get("data_type") and changes["data_type"] not in DATA_TYPES:
        raise SchemaError(f"Unbekannter Datentyp: {changes['data_type']}")
    if changes.get("source") and changes["source"] not in FIELD_SOURCES:
        raise SchemaError(f"Unbekannte Quelle: {changes['source']}")
    for key, value in changes.items():
        setattr(field, key, value)
    db.flush()
    return field


def delete_field(db: Session, field: SchemaField) -> None:
    _require_draft(field.entity.version)
    if field.is_locked:
        raise SchemaError(f"{field.name} ist ein Standardfeld und kann nicht gelöscht werden")
    ent = field.entity
    if ent.match_fields and field.name in ent.match_fields:
        raise SchemaError(f"{field.name} wird als Matching-Feld verwendet")
    db.delete(field)
    db.flush()


def add_relation(
    db: Session, version: SchemaVersion, name: str, kind: str, source: SchemaEntity, target: SchemaEntity,
    description: str | None = None,
) -> SchemaRelation:
    _require_draft(version)
    _check_name(name, "Beziehung")
    if kind not in RELATION_KINDS:
        raise SchemaError(f"Unbekannte Beziehungsart: {kind}")
    if source.version_id != version.id or target.version_id != version.id:
        raise SchemaError("Beide Entitäten müssen zur selben Version gehören")
    if kind == "parent_child" and source.is_master:
        raise SchemaError("Stammdaten-Entitäten können keine Kind-Datensätze haben")
    if any(r.name == name for r in version.relations):
        raise SchemaError(f"Beziehung {name} existiert bereits")
    rel = SchemaRelation(version_id=version.id, name=name, kind=kind, source_entity_id=source.id, target_entity_id=target.id, description=description)
    db.add(rel)
    db.flush()
    db.refresh(version)
    return rel
