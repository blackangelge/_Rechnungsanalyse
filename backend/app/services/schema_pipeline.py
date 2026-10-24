"""
Verarbeitung der strukturierten KI-Antwort zu `extracted_records`:

    JSON-Antwort ──validate (Pydantic)──► Werte je Entität
        ──Normalisierer-Kette → Typ-Umwandlung (Zahl/Datum/…) → Mengen/Einheiten──►
        + Systemfelder (Seiten, Dateiname, …) + Standardfelder (document_id, vendor_id)
        ──Lieferant zuordnen (konfigurierbares Matching)──► speichern (alte Datensätze des Belegs werden ersetzt)

Probleme (ungültiger Wert, unbekannte Einheit, fehlendes Pflichtfeld) stehen als `_issues` am Datensatz; der Beleg wird trotzdem
gespeichert, nichts geht verloren. Schlüssel mit führendem „_“ sind reserviert (`_issues`, `_master`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import units as unit_crud
from app.models.schema_def import ExtractedRecord, SchemaEntity, SchemaField, SchemaVersion
from app.models.units import UnitReviewItem
from app.models.vendor import Vendor
from app.models.vendor_bank_account import VendorBankAccount
from app.services import field_functions, normalizers
from app.services.schema_models import ExtractionModel, root_entity
from app.services.units import UnitResolver, normalize_quantity

logger = logging.getLogger(__name__)


class SchemaValidationFailed(ValueError):
    """Die KI-Antwort entspricht nicht dem Schema (Meldungen in `messages`)."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages[:5]))
        self.messages = messages


def validate_output(built: ExtractionModel, parsed: dict) -> dict:
    try:
        return built.model.model_validate(parsed).model_dump()
    except ValidationError as exc:
        raise SchemaValidationFailed(
            [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        ) from exc


# ─── Werte ───────────────────────────────────────────────────────────────────

def _coerce(f: SchemaField, value: Any) -> tuple[Any, list[str]]:
    """Typ-Umwandlung nach der Kette. Rückgabe (Wert, Fehlermeldungen)."""
    if value is None:
        return None, []
    try:
        if f.data_type == "text":
            s = str(value).strip()
            return (s or None), []
        if f.data_type == "integer":
            n = normalizers.parse_german_number(value)
            if n is None:
                return None, []
            if n != n.to_integral_value():
                return None, [f"Keine Ganzzahl: {value!r}"]
            return int(n), []
        if f.data_type == "decimal":
            n = normalizers.parse_german_number(value)
            return normalizers.to_jsonable(n) if n is not None else None, []
        if f.data_type == "date":
            return normalizers._parse_date(value, normalizers._DateParams()), []      # noqa: SLF001
        return value, []
    except normalizers.NormalizeError as exc:
        return None, [str(exc)]


def normalize_value(f: SchemaField, value: Any, resolver: UnitResolver) -> tuple[Any, list[dict]]:
    """Kette → Typ-Umwandlung bzw. Mengen-Normierung. Rückgabe (gespeicherter Wert, Probleme mit `field`)."""
    issues: list[dict] = []
    if f.data_type == "quantity":
        q, qi = normalize_quantity(resolver, value, f.unit_group_id)
        return q, [{"field": f.name, **i} for i in qi]
    if f.normalizers:
        res = normalizers.apply_chain(value, f.normalizers)
        issues += [{"field": f.name, "kind": "normalizer", **i} for i in res.issues]
        if res.issues:
            return None, issues
        value = res.value
    value, errs = _coerce(f, value)
    issues += [{"field": f.name, "kind": "invalid_value", "message": m} for m in errs]
    return value, issues


# ─── Entwurf ─────────────────────────────────────────────────────────────────

@dataclass
class RecordDraft:
    entity: SchemaEntity
    data: dict
    position_index: int = 0
    children: list["RecordDraft"] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    vendor_id: int | None = None


_VENDOR_ATTRS = {
    "name": "name", "strasse": "street", "plz": "postal_code", "ort": "city", "land": "country",
    "hrb_nummer": "hrb_number", "steuernummer": "tax_number", "ust_id_nr": "vat_id",
}


def _norm_id(text: str | None) -> str:
    return re.sub(r"[\s]+", "", text or "").upper()


def match_vendor(db: Session, entity: SchemaEntity, values: dict) -> Vendor | None:
    """Findet (nach `match_fields` in dieser Reihenfolge) oder legt an; ergänzt nur leere Felder. Kein Commit."""
    name = (values.get("name") or "").strip() if isinstance(values.get("name"), str) else values.get("name")
    if not name:
        return None
    found: Vendor | None = None
    for fname in entity.match_fields or ["name"]:
        val = values.get(fname)
        if val in (None, ""):
            continue
        if fname == "iban":
            found = (
                db.query(Vendor).join(VendorBankAccount, Vendor.id == VendorBankAccount.vendor_id)
                .filter(func.upper(func.replace(VendorBankAccount.iban, " ", "")) == _norm_id(str(val))).first()
            )
        elif fname == "name":
            found = db.query(Vendor).filter(func.lower(func.trim(Vendor.name)) == str(val).strip().lower()).first()
        elif fname in _VENDOR_ATTRS:
            col = getattr(Vendor, _VENDOR_ATTRS[fname])
            found = db.query(Vendor).filter(func.upper(func.replace(col, " ", "")) == _norm_id(str(val))).first()
        if found is not None:
            break
    if found is None:
        found = Vendor(name=str(name).strip())
        db.add(found)
        db.flush()
    for fname, attr in _VENDOR_ATTRS.items():
        v = values.get(fname)
        if v not in (None, "") and not getattr(found, attr):
            setattr(found, attr, v)
    iban = values.get("iban")
    if iban and not any(_norm_id(b.iban) == _norm_id(str(iban)) for b in found.bank_accounts):
        db.add(VendorBankAccount(vendor_id=found.id, bank_name=values.get("bank_name"), iban=str(iban), bic=values.get("bic")))
    elif not iban and values.get("bank_name") and not found.bank_accounts:
        db.add(VendorBankAccount(vendor_id=found.id, bank_name=values.get("bank_name"), iban=None, bic=values.get("bic")))
    db.flush()
    db.refresh(found)
    return found


def _entity_values(entity: SchemaEntity, raw: dict, resolver: UnitResolver) -> tuple[dict, list[dict]]:
    data: dict = {}
    issues: list[dict] = []
    for f in entity.fields:
        if f.source != "llm" or f.is_locked:
            continue
        value, iss = normalize_value(f, raw.get(f.name), resolver)
        data[f.name] = value
        issues += iss
        if f.required and (value is None or (isinstance(value, dict) and value.get("original_value") is None)):
            issues.append({"field": f.name, "kind": "missing_required", "message": f"Pflichtfeld {f.display_name} fehlt"})
    return data, issues


def _system_values(entity: SchemaEntity, ctx: field_functions.DocumentContext) -> tuple[dict, list[dict]]:
    data: dict = {}
    issues: list[dict] = []
    for f in entity.fields:
        if f.source != "system" or (f.is_locked and not f.system_function):
            continue
        try:
            value = field_functions.run(f.system_function, f.system_params, ctx)
        except field_functions.FunctionError as exc:
            issues.append({"field": f.name, "kind": "system_function", "message": str(exc)})
            data[f.name] = None
            continue
        value, errs = _coerce(f, value)
        data[f.name] = value
        issues += [{"field": f.name, "kind": "invalid_value", "message": m} for m in errs]
    return data, issues


def build_drafts(
    db: Session, version: SchemaVersion, validated: dict, ctx: field_functions.DocumentContext, resolver: UnitResolver,
) -> RecordDraft:
    by_id = {e.id: e for e in version.entities}
    vendor_holder: dict[str, int | None] = {"id": None}

    def build(entity: SchemaEntity, raw: dict, index: int) -> RecordDraft:
        data, issues = _entity_values(entity, raw, resolver)
        sdata, sissues = _system_values(entity, ctx)
        data.update(sdata)
        data["document_id"] = ctx.document_id
        draft = RecordDraft(entity, data, index, issues=issues + sissues)
        for rel in version.relations:
            if rel.source_entity_id != entity.id:
                continue
            target = by_id[rel.target_entity_id]
            sub = raw.get(rel.name)
            if rel.kind == "parent_child":
                for i, item in enumerate(sub or []):
                    draft.children.append(build(target, item, i))
            elif target.is_master and isinstance(sub, dict) and target.master_table == "vendor":
                values, v_issues = _entity_values(target, sub, resolver)
                draft.issues += v_issues
                draft.data.setdefault("_master", {})[rel.name] = values
                vendor = match_vendor(db, target, values)
                if vendor is not None:
                    vendor_holder["id"] = vendor.id
        return draft

    root = build(root_entity(version), validated, 0)

    def stamp(d: RecordDraft) -> None:
        d.vendor_id = vendor_holder["id"]
        d.data["vendor_id"] = vendor_holder["id"]
        for c in d.children:
            stamp(c)

    stamp(root)
    return root


# ─── Speichern ───────────────────────────────────────────────────────────────

@dataclass
class SaveResult:
    record_count: int
    issue_count: int
    root_id: int
    vendor_id: int | None
    unknown_units: int


def save_drafts(db: Session, document_id: int, version: SchemaVersion, root: RecordDraft) -> SaveResult:
    """Ersetzt alle Datensätze des Belegs. Kein Commit (der Aufrufer entscheidet)."""
    db.query(UnitReviewItem).filter(UnitReviewItem.document_id == document_id).delete(synchronize_session=False)
    db.query(ExtractedRecord).filter(ExtractedRecord.document_id == document_id).delete(synchronize_session=False)
    db.expire_all()
    counts = {"records": 0, "issues": 0, "units": 0}

    def store(d: RecordDraft, parent_id: int | None) -> ExtractedRecord:
        data = dict(d.data)
        if d.issues:
            data["_issues"] = d.issues
        rec = ExtractedRecord(
            document_id=document_id, entity_id=d.entity.id, schema_version_id=version.id, parent_record_id=parent_id,
            vendor_id=d.vendor_id, position_index=d.position_index, data=data,
        )
        db.add(rec)
        db.flush()
        counts["records"] += 1
        counts["issues"] += len(d.issues)
        for iss in d.issues:
            if iss.get("kind") == "unknown_unit" and iss.get("unit"):
                unit_crud.add_review_item(db, record_id=rec.id, document_id=document_id, field_name=iss["field"], original_unit=iss["unit"])
                counts["units"] += 1
        for c in d.children:
            store(c, rec.id)
        return rec

    root_rec = store(root, None)
    return SaveResult(counts["records"], counts["issues"], root_rec.id, root.vendor_id, counts["units"])


def process_and_save(
    db: Session, document, version: SchemaVersion, built: ExtractionModel, parsed: dict,
) -> SaveResult:
    """Validieren → Entwürfe → speichern → commit. Bei SchemaValidationFailed wird nichts verändert."""
    validated = validate_output(built, parsed)
    ctx = field_functions.build_context(document)
    root = build_drafts(db, version, validated, ctx, UnitResolver(db))
    result = save_drafts(db, document.id, version, root)
    try:                                   # Prüfregeln: ein Fehler hier darf die Analyse nie abbrechen
        from app.services import rule_engine
        with db.begin_nested():
            rule_engine.evaluate_document(db, document.id)
    except Exception:
        logger.warning("Prüfregeln für Beleg %s fehlgeschlagen", document.id, exc_info=True)
    db.commit()
    return result
