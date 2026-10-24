"""
Papierkorb für Belege: sammelweise in den Papierkorb legen, zurückholen und ENDGÜLTIG löschen (inkl. PDF-Dateien).

Endgültig gelöscht werden nur Belege, die schon im Papierkorb liegen (zweistufig: erst „In den Papierkorb“, dann prüfen, dann „Endgültig löschen“).
Beim endgültigen Löschen verschwinden: die Belegzeile samt Extraktion/Positionen/Datensätzen/Historie (per Cascade), wartende Worker-Tasks,
die PDF `{id}.pdf` und alle Versionsdateien. Das ist nicht umkehrbar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import bindparam, func, text
from sqlalchemy.orm import Session

from app.models.document import Document
from app.services import pdf_edit_service

logger = logging.getLogger(__name__)

CHUNK = 500


@dataclass
class TrashResult:
    requested: int
    changed: int = 0
    skipped: int = 0
    files_removed: int = 0
    errors: list[str] = field(default_factory=list)


def _chunks(ids: list[int]):
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), CHUNK):
        yield ids[i:i + CHUNK]


def trash(db: Session, ids: list[int], reason: str | None) -> TrashResult:
    """In den Papierkorb legen (umkehrbar). Belege in Verarbeitung werden übersprungen."""
    res = TrashResult(requested=len(set(ids)))
    for chunk in _chunks(ids):
        n = (db.query(Document)
             .filter(Document.id.in_(chunk), Document.soft_deleted == False, Document.status != "processing")  # noqa: E712
             .update({Document.soft_deleted: True, Document.deleted_reason: (reason or None) and reason[:300], Document.deleted_at: func.now()},
                     synchronize_session=False))
        res.changed += n
    res.skipped = res.requested - res.changed
    db.commit()
    return res


def restore(db: Session, ids: list[int]) -> TrashResult:
    res = TrashResult(requested=len(set(ids)))
    for chunk in _chunks(ids):
        res.changed += (db.query(Document).filter(Document.id.in_(chunk), Document.soft_deleted == True)  # noqa: E712
                        .update({Document.soft_deleted: False, Document.deleted_reason: None, Document.deleted_at: None}, synchronize_session=False))
    res.skipped = res.requested - res.changed
    db.commit()
    return res


def _remove_files(doc: Document) -> int:
    """PDF und Versionsdateien eines Belegs löschen. Gibt die Zahl gelöschter Dateien zurück (Fehler werden nur geloggt)."""
    removed = 0
    try:
        if doc.stored_filename:
            f = Path(doc.batch.storage_folder_path) / doc.stored_filename
            if f.exists():
                f.unlink()
                removed += 1
        versions = Path(doc.batch.storage_folder_path) / "versions" / str(doc.id)
        if versions.exists():
            removed += sum(1 for _ in versions.glob("*"))
        pdf_edit_service.delete_version_files(doc)
    except OSError as exc:
        logger.warning("Dateien von Beleg #%d nicht vollständig gelöscht: %s", doc.id, exc)
    return removed


def purge(db: Session, ids: list[int]) -> TrashResult:
    """Endgültig löschen — nur Belege im Papierkorb (alle anderen werden übersprungen)."""
    res = TrashResult(requested=len(set(ids)))
    for chunk in _chunks(ids):
        docs = db.query(Document).filter(Document.id.in_(chunk), Document.soft_deleted == True, Document.status != "processing").all()  # noqa: E712
        if not docs:
            continue
        for d in docs:
            try:
                res.files_removed += _remove_files(d)
            except Exception as exc:        # eine defekte Datei darf das Löschen der Zeile nicht verhindern
                res.errors.append(f"#{d.id}: {exc}")
        doc_ids = [d.id for d in docs]
        db.execute(text("DELETE FROM workflow_tasks WHERE CAST(payload->>'document_id' AS TEXT) IN :ids").bindparams(bindparam("ids", expanding=True)),
                   {"ids": [str(i) for i in doc_ids]})
        for d in docs:
            db.delete(d)
        db.commit()
        res.changed += len(docs)
    res.skipped = res.requested - res.changed
    return res


def non_invoice_summary(db: Session) -> dict:
    """Übersicht für die Prüfung: erkannte Nicht-Eingangsrechnungen (nicht im Papierkorb) je Dokumententyp."""
    from app.crud.document import NOT_INVOICE_EXCLUDED_TYPES
    rows = (db.query(Document.document_type, func.count(Document.id))
            .filter(Document.soft_deleted == False, Document.status == "done",  # noqa: E712
                    ~Document.document_type.in_(NOT_INVOICE_EXCLUDED_TYPES))
            .group_by(Document.document_type).all())
    from app.routers.settings import _DOCUMENT_TYPES
    names = {t["id"]: t["name"] for t in _DOCUMENT_TYPES}
    by_type = sorted(({"type_id": t, "name": names.get(t) or f"Typ {t}", "count": c} for t, c in rows), key=lambda r: -r["count"])
    trash_count = db.query(func.count(Document.id)).filter(Document.soft_deleted == True).scalar() or 0  # noqa: E712
    return {"total": sum(r["count"] for r in by_type), "by_type": by_type, "trash": int(trash_count)}
