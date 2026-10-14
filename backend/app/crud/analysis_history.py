"""
Analyse-Historie: frühere Stände der KI-Auslesung eines Belegs.

`archive_current` hebt den aktuellen Stand (Extraktion + Positionen + verwendete
Prompts/Workflow) auf, bevor eine neue Analyse ihn überschreibt. `restore` macht
einen früheren Stand wieder zum aktuellen — der bisherige aktuelle Stand wird dabei
selbst aufgehoben, es geht also nie etwas verloren.

Fehler hier dürfen eine Analyse nie abbrechen: `archive_current` fängt alles ab
(z.B. wenn die Migration 0018 noch nicht gelaufen ist).
"""

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.models.analysis_history import AnalysisHistory
from app.models.document import Document
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition

logger = logging.getLogger(__name__)

_ERROR_PREFIXES = (
    "KI überlastet:", "KI-Fehler:", "KI-Timeout",
    "KI-Verbindungsfehler", "Unerwarteter KI-Fehler",
)
_SKIP_COLUMNS = {"id", "document_id", "created_at", "updated_at"}
# Laufzeit-Statistiken gehören nicht zur Identität eines Ergebnisses
_FINGERPRINT_SKIP = _SKIP_COLUMNS | {
    "ki_input_tokens", "ki_output_tokens", "ki_reasoning_tokens",
    "ki_tokens_per_second", "ki_time_to_first_token", "ki_total_duration",
}
# Felder des Belegs, die zum Stand gehören (verwendete Prompts, Typ, Seitenzahl). Spätere
# Erweiterungen (Workflow, PDF-Version) hängen sich über `register_meta_fields` ein.
_META_FIELDS: list[str] = [
    "used_system_prompt", "used_doc_type_prompt", "used_recheck_prompt",
    "document_type", "page_count",
]


# Workflow-Angaben gehören zum Stand (welcher Workflow hat dieses Ergebnis erzeugt?)
def _register_workflow_fields() -> None:
    register_meta_fields("used_workflow_id", "used_workflow_name", "used_workflow_graph", "workflow_trace")


def register_meta_fields(*names: str) -> None:
    """Nimmt weitere Dokument-Felder in den Snapshot auf (z.B. Workflow-Angaben)."""
    for name in names:
        if name not in _META_FIELDS:
            _META_FIELDS.append(name)


_register_workflow_fields()


class HistoryError(Exception):
    """Fachlicher Fehler (z.B. Eintrag nicht gefunden) → HTTP 4xx im Router."""


def _json_default(value):
    if isinstance(value, (Decimal, date, datetime)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    raise TypeError(f"nicht serialisierbar: {type(value)}")


def _row_dict(obj, skip: set[str]) -> dict:
    return {
        c.key: getattr(obj, c.key)
        for c in sa_inspect(obj).mapper.column_attrs
        if c.key not in skip
    }


def _coerce(model, data: dict) -> dict:
    """JSON-Werte zurück in Python-Typen (Datum, Decimal) — nur Spalten, die es gibt."""
    cols = {c.key: c for c in sa_inspect(model).mapper.column_attrs}
    out = {}
    for key, value in data.items():
        col = cols.get(key)
        if col is None or key in _SKIP_COLUMNS:
            continue
        ctype = col.columns[0].type
        if value is not None:
            if isinstance(ctype, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(ctype, Date):
                value = date.fromisoformat(value)
            elif isinstance(ctype, Numeric):
                value = Decimal(str(value))
        out[key] = value
    return out


def keep_limit(db: Session) -> int:
    """Wie viele frühere Stände je Beleg aufbewahrt werden (Einstellungen → Automatisierung, 0 = aus)."""
    from app.crud import automation_settings
    return int(automation_settings.get_or_create(db).analysis_history_keep or 0)


def _build_snapshot(doc: Document) -> dict:
    ext = doc.extraction
    positions = [
        _row_dict(p, _SKIP_COLUMNS | {"position_index"}) | {"position_index": p.position_index}
        for p in sorted(doc.order_positions, key=lambda p: p.position_index)
    ]
    return {
        "extraction": _row_dict(ext, _SKIP_COLUMNS),
        "positions": positions,
        "meta": {k: getattr(doc, k, None) for k in _META_FIELDS},
    }


def _fingerprint(snapshot: dict) -> str:
    ext = {k: v for k, v in snapshot["extraction"].items() if k not in _FINGERPRINT_SKIP}
    payload = json.dumps({"e": ext, "p": snapshot["positions"]},
                         sort_keys=True, default=_json_default, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _prune(db: Session, doc_id: int, keep: int) -> None:
    ids = db.scalars(
        select(AnalysisHistory.id)
        .where(AnalysisHistory.document_id == doc_id)
        .order_by(AnalysisHistory.created_at.desc(), AnalysisHistory.id.desc())
    ).all()
    for old_id in ids[keep:]:
        db.query(AnalysisHistory).filter(AnalysisHistory.id == old_id).delete()


def _archive(db: Session, doc: Document, keep: int) -> AnalysisHistory | None:
    ext = doc.extraction
    if ext is None or keep <= 0:
        return None
    raw = (ext.raw_response or "").strip()
    if not raw or raw.startswith(_ERROR_PREFIXES):
        return None  # nichts Brauchbares aufzuheben

    snapshot = _build_snapshot(doc)
    fp = _fingerprint(snapshot)
    latest = db.scalars(
        select(AnalysisHistory)
        .where(AnalysisHistory.document_id == doc.id)
        .order_by(AnalysisHistory.created_at.desc(), AnalysisHistory.id.desc())
        .limit(1)
    ).first()
    if latest is not None and latest.fingerprint == fp:
        return None  # identischer Stand bereits aufgehoben

    entry = AnalysisHistory(
        document_id=doc.id,
        pdf_version=getattr(doc, "analyzed_pdf_version", None),
        workflow_name=getattr(doc, "used_workflow_name", None),
        validation_status=ext.validation_status,
        invoice_number=ext.invoice_number,
        supplier_name=ext.vendor_id,
        total_amount=ext.total_amount_brutto,
        position_count=len(doc.order_positions),
        fingerprint=fp,
        snapshot=json.dumps(snapshot, default=_json_default, ensure_ascii=False),
    )
    db.add(entry)
    db.flush()
    _prune(db, doc.id, keep)
    return entry


def archive_current(db: Session, doc_id: int) -> AnalysisHistory | None:
    """
    Hebt den aktuellen Stand des Belegs auf (vor einer neuen Analyse).
    Gibt None zurück, wenn nichts aufzuheben ist — und wirft nie.
    """
    try:
        keep = keep_limit(db)
        doc = db.get(Document, doc_id)
        if doc is None:
            return None
        entry = _archive(db, doc, keep)
        db.commit()
        return entry
    except Exception as exc:  # noqa: BLE001 — Historie darf die Analyse nie stören
        logger.warning("Analyse-Historie für #%s nicht gespeichert: %s", doc_id, exc)
        db.rollback()
        return None


def list_for_document(db: Session, doc_id: int) -> list[AnalysisHistory]:
    from sqlalchemy.orm import defer
    return list(db.scalars(
        select(AnalysisHistory)
        .options(defer(AnalysisHistory.snapshot))
        .where(AnalysisHistory.document_id == doc_id)
        .order_by(AnalysisHistory.created_at.desc(), AnalysisHistory.id.desc())
    ).all())


def get(db: Session, doc_id: int, history_id: int) -> AnalysisHistory | None:
    return db.scalars(
        select(AnalysisHistory).where(
            AnalysisHistory.id == history_id, AnalysisHistory.document_id == doc_id
        )
    ).first()


def parsed_snapshot(entry: AnalysisHistory) -> dict:
    return json.loads(entry.snapshot)


def delete(db: Session, doc_id: int, history_id: int) -> bool:
    entry = get(db, doc_id, history_id)
    if entry is None:
        return False
    db.delete(entry)
    db.commit()
    return True


def restore(db: Session, doc_id: int, history_id: int) -> Document:
    """
    Macht einen früheren Stand wieder zum aktuellen. Der bisherige aktuelle Stand wird
    vorher aufgehoben; der wiederhergestellte Eintrag verlässt die Historie (er IST
    jetzt der aktuelle Stand). Alles in einer Transaktion.
    """
    doc = db.get(Document, doc_id)
    entry = get(db, doc_id, history_id)
    if doc is None or entry is None:
        raise HistoryError("Analyse-Stand nicht gefunden")
    snap = parsed_snapshot(entry)
    ext_data = _coerce(InvoiceExtraction, snap["extraction"])

    try:
        # Aktuellen Stand sichern (auch wenn die Historie sonst aus wäre: Wiederherstellen
        # soll nie etwas vernichten) — mindestens 1 Platz, dazu der noch vorhandene Rest.
        keep = max(keep_limit(db), 1)
        entry_pk = entry.id
        _archive(db, doc, keep + 1)  # +1: der Eintrag unten fällt gleich weg

        db.query(InvoiceExtraction).filter(InvoiceExtraction.document_id == doc_id).delete()
        db.query(OrderPosition).filter(OrderPosition.document_id == doc_id).delete()
        db.add(InvoiceExtraction(document_id=doc_id, **ext_data))
        for pos in snap["positions"]:
            db.add(OrderPosition(document_id=doc_id, **_coerce(OrderPosition, pos)))

        meta = snap.get("meta", {})
        for field in _META_FIELDS:
            if field in meta and hasattr(type(doc), field):
                setattr(doc, field, meta[field])
        doc.status = "done"

        db.query(AnalysisHistory).filter(AnalysisHistory.id == entry_pk).delete()
        _prune(db, doc_id, keep)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(doc)
    return doc
