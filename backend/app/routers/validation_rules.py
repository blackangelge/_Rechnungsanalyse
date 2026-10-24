"""
Router: Prüfregeln (frei definierbare Rechenregeln für extrahierte Datensätze).

  GET    /api/validation-rules                 — alle Regeln (mit Definitionsproblemen gegen das aktive Schema)
  GET    /api/validation-rules/meta            — Auswahl für das Menü: Entitäten, Zahlenfelder, Unter-Entitäten, Funktionen
  POST   /api/validation-rules                 — anlegen (Definition wird geprüft → 422)
  PUT    /api/validation-rules/{id}            — ändern
  DELETE /api/validation-rules/{id}
  POST   /api/validation-rules/seed-defaults   — Vorschläge anlegen (nur wenn noch keine Regel existiert)
  POST   /api/validation-rules/test            — Regel (ungespeichert) an einem Beleg ausprobieren
  GET    /api/validation-rules/document/{id}   — alle aktiven Regeln an einem Beleg auswerten (nichts gespeichert)
  POST   /api/validation-rules/run             — Belege neu prüfen und Ergebnis speichern ({document_ids?} — ohne Angabe alle)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.database import get_db
from app.models.document import Document
from app.models.schema_def import ExtractedRecord
from app.models.units import Unit
from app.models.validation_rule import ValidationRule
from app.services import rule_engine, rule_seed
from app.services.rule_engine import RuleSpec

router = APIRouter(prefix="/api/validation-rules", tags=["validation-rules"])


class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    entity_name: str
    left_expr: str
    alt_left_exprs: list[str] = Field(default_factory=list)
    right_expr: str
    comparator: str = "="
    tolerance_abs: float = 0.0
    tolerance_pct: float = 0.0
    severity: str = "error"
    enabled: bool = True


def _unit_keys(db: Session) -> set[str]:
    return rule_engine.known_unit_keys(rule_engine.load_unit_table(db))


def _spec(body: RuleIn, rule_id: int | None = None) -> RuleSpec:
    return RuleSpec(rule_id, body.name, body.entity_name, body.left_expr, body.right_expr, body.comparator,
                    body.tolerance_abs, body.tolerance_pct, body.severity,
                    [a for a in body.alt_left_exprs if a.strip()])


def _read(r: ValidationRule, problems: list[str] | None = None) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "entity_name": r.entity_name, "left_expr": r.left_expr,
            "right_expr": r.right_expr, "alt_left_exprs": list(r.alt_left_exprs or []), "comparator": r.comparator, "tolerance_abs": r.tolerance_abs, "tolerance_pct": r.tolerance_pct,
            "severity": r.severity, "enabled": r.enabled, "position": r.position, "problems": problems or []}


@router.get("")
def list_rules(db: Session = Depends(get_db)):
    version = sd.get_active(db)
    keys = _unit_keys(db)
    out = []
    for r in db.query(ValidationRule).order_by(ValidationRule.position, ValidationRule.id):
        spec = RuleSpec(r.id, r.name, r.entity_name, r.left_expr, r.right_expr, r.comparator, r.tolerance_abs, r.tolerance_pct, r.severity,
                        list(r.alt_left_exprs or []))
        out.append(_read(r, rule_engine.check_rule_spec(spec, version)))
    return out


@router.get("/meta")
def meta(db: Session = Depends(get_db)):
    version = sd.get_active(db)
    entities = []
    if version is not None:
        names = {e.id: e.name for e in version.entities}
        for e in version.entities:
            if e.is_master:
                continue
            entities.append({
                "name": e.name, "display_name": e.display_name,
                "fields": [{"name": f.name, "display_name": f.display_name, "data_type": f.data_type}
                           for f in e.fields if f.data_type in rule_engine.NUMERIC_TYPES],
                "children": [names[r.target_entity_id] for r in version.relations
                             if r.kind == "parent_child" and r.source_entity_id == e.id],
                "has_parent": any(r.kind == "parent_child" and r.target_entity_id == e.id for r in version.relations),
            })
    return {"price_unit_field": bool(version and rule_seed.has_price_unit_field(version)),
            "entities": entities, "functions": rule_engine.FUNCTION_HELP, "comparators": list(rule_engine.COMPARATORS),
            "units": sorted({u.symbol for u in db.query(Unit).all()}),
            "schema_version": version.version_number if version else None}


def _validated(db: Session, body: RuleIn, rule_id: int | None = None) -> None:
    problems = rule_engine.check_rule_spec(_spec(body, rule_id), sd.get_active(db), _unit_keys(db))
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))


@router.post("", status_code=201)
def create_rule(body: RuleIn, db: Session = Depends(get_db)):
    _validated(db, body)
    pos = (db.query(ValidationRule).count())
    data = body.model_dump()
    data["alt_left_exprs"] = [a.strip() for a in body.alt_left_exprs if a.strip()]
    r = ValidationRule(**data, position=pos)
    db.add(r)
    db.commit()
    return _read(r)


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: RuleIn, db: Session = Depends(get_db)):
    r = db.get(ValidationRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    _validated(db, body, rule_id)
    for k, v in body.model_dump().items():
        setattr(r, k, [a.strip() for a in v if a.strip()] if k == "alt_left_exprs" else v)
    db.commit()
    return _read(r)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    r = db.get(ValidationRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden")
    db.delete(r)
    db.commit()


@router.post("/seed-defaults")
def seed_defaults(db: Session = Depends(get_db)):
    n = rule_seed.seed_default_rules(db)
    db.commit()
    return {"created": n}


@router.post("/enable-price-unit")
def enable_price_unit(db: Session = Depends(get_db)):
    """
    Ergänzt das Schemafeld „Preiseinheit“ an der Position: neuer Entwurf aus dem aktiven Schema → Feld → aktivieren.
    Neue Auslesungen fragen die KI dann nach der Preiseinheit; bestehende Datensätze bleiben unverändert (ältere Schema-Version).
    Die unveränderte Standardregel „Positionspreis“ wird auf die strenge Fassung (ohne Varianten) umgestellt.
    """
    from app.services.schema_seed import PRICE_UNIT_DESCRIPTION
    version = sd.get_active(db)
    if version is None:
        raise HTTPException(status_code=409, detail="Es gibt noch kein aktives Schema.")
    if rule_seed.has_price_unit_field(version):
        return {"changed": False, "schema_version": version.version_number, "rules_updated": 0}
    if not any(e.name == "position" for e in version.entities):
        raise HTTPException(status_code=409, detail="Das aktive Schema hat keine Entität „position“ — das Feld bitte im Schema selbst ergänzen.")
    try:
        draft = sd.create_draft_from(db, version, "Feld „Preiseinheit“ ergänzt (Prüfregeln)")
        position = next(e for e in draft.entities if e.name == "position")
        sd.add_field(db, position, "preiseinheit", "Preiseinheit", "text", description=PRICE_UNIT_DESCRIPTION)
        sd.activate(db, draft)
    except sd.SchemaError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = rule_seed.sync_default_rules(db)
    db.commit()
    return {"changed": True, "schema_version": draft.version_number, "rules_updated": updated}


class TestIn(RuleIn):
    document_id: int


@router.post("/test")
def test_rule(body: TestIn, db: Session = Depends(get_db)):
    spec = _spec(body)
    problems = rule_engine.check_rule_spec(spec, sd.get_active(db), _unit_keys(db))
    if problems:
        return {"problems": problems, "results": []}
    root, _ = rule_engine.load_tree(db, body.document_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Zu diesem Beleg gibt es keine extrahierten Datensätze")
    return {"problems": [], "results": rule_engine.evaluate_tree(root, [spec], rule_engine.load_unit_table(db))}


@router.get("/document/{document_id}")
def document_checks(document_id: int, db: Session = Depends(get_db)):
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Beleg nicht gefunden")
    return rule_engine.evaluate_document(db, document_id, store=False)


class RunIn(BaseModel):
    document_ids: list[int] | None = None


@router.post("/run")
def run_checks(body: RunIn, db: Session = Depends(get_db)):
    specs = rule_engine.load_specs(db)
    units = rule_engine.load_unit_table(db)
    if body.document_ids is not None:
        ids = list(dict.fromkeys(body.document_ids))
    else:
        ids = [i for (i,) in db.query(ExtractedRecord.document_id).filter(ExtractedRecord.parent_record_id.is_(None)).distinct().all()]
    counts = {"ok": 0, "warn": 0, "needs_review": 0, "unchecked": 0}
    flagged: list[int] = []
    for n, doc_id in enumerate(ids, 1):
        status = rule_engine.evaluate_document(db, doc_id, specs, units=units)["summary"]["status"]
        counts[status] += 1
        if status in ("needs_review", "warn") and len(flagged) < 50:
            flagged.append(doc_id)
        if n % 200 == 0:
            db.commit()
    db.commit()
    return {"documents": len(ids), "rules": len(specs), "counts": counts, "flagged_sample": flagged}
