"""
CRUD-Operationen für die documents-Tabelle.

Enthält alle Datenbankoperationen für Dokumente:
- Anlegen beim Import (create)
- Status-Updates (update_status, update_after_copy)
- Soft-Delete / Restore
- Kommentar und Dokumententyp aktualisieren
- Gefilterte Listen-Abfrage mit joins (get_all_filtered)
- Detail-Abfrage mit allen Relationen (get_by_id_with_details)
- Speichern von KI-Extraktionsergebnissen (save_extraction)

Alle Funktionen erwarten eine bereits geöffnete Session und schließen sie NICHT —
das Lifecycle-Management liegt beim Aufrufer.
"""

import logging
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models.document import Document
from app.models.import_batch import ImportBatch
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition
from app.models.document_token_count import DocumentTokenCount

logger = logging.getLogger(__name__)


def create(
    db: Session,
    batch_id: int,
    original_filename: str,
    file_size_bytes: int,
) -> Document:
    """Legt einen neuen Dokument-Eintrag an (status=pending). Wird beim Import aufgerufen."""
    obj = Document(
        batch_id=batch_id,
        original_filename=original_filename,
        file_size_bytes=file_size_bytes,
        status="pending",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_after_copy(
    db: Session,
    doc_id: int,
    stored_filename: str,
    page_count: int,
) -> Document | None:
    """
    Speichert stored_filename und setzt status → 'done' nach erfolgreichem Kopieren.
    page_count ist beim Import 0 — wird erst bei der KI-Analyse gesetzt.
    """
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.stored_filename = stored_filename
    obj.page_count = page_count
    obj.status = "done"
    db.commit()
    db.refresh(obj)
    return obj


def update_status(
    db: Session,
    doc_id: int,
    status: str,
    error_message: str | None = None,
) -> Document | None:
    """
    Setzt den Dokument-Status (pending/processing/done/error).
    error_message wird aktuell nur geloggt, nicht persistiert.
    """
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.status = status
    db.commit()
    db.refresh(obj)
    return obj


def soft_delete(db: Session, doc_id: int) -> Document | None:
    """Markiert das Dokument als gelöscht (soft_deleted=True). Keine Daten werden entfernt."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.soft_deleted = True
    db.commit()
    db.refresh(obj)
    return obj


def restore(db: Session, doc_id: int) -> Document | None:
    """Hebt den Soft-Delete wieder auf (soft_deleted=False)."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.soft_deleted = False
    db.commit()
    db.refresh(obj)
    return obj


def update_comment(db: Session, doc_id: int, comment: str | None) -> Document | None:
    """Speichert einen Freitext-Kommentar. None löscht den bestehenden Kommentar."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.comment = comment
    db.commit()
    db.refresh(obj)
    return obj


def update_document_type(db: Session, doc_id: int, type_id: int | None) -> Document | None:
    """Setzt den Dokumententyp (Integer-ID, z.B. 1=Eingangsrechnung). None → 0 (Unbekannt)."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.document_type = type_id or 0
    db.commit()
    db.refresh(obj)
    return obj


def get_by_id_with_details(db: Session, doc_id: int) -> Document | None:
    """
    Lädt ein Dokument mit allen Relationen in einer einzigen Query:
    - batch (für Firmenname, Jahr)
    - extraction (InvoiceExtraction)
    - order_positions (alle Rechnungspositionen)
    - token_counts (alle KI-Durchlauf-Statistiken)
    """
    return (
        db.query(Document)
        .options(
            joinedload(Document.batch),
            joinedload(Document.extraction),
            joinedload(Document.order_positions),
            joinedload(Document.token_counts),
        )
        .filter(Document.id == doc_id)
        .first()
    )


def get_all_filtered(
    db: Session,
    company: str | None = None,
    year: int | None = None,
    status: str | None = None,
    total_min: float | None = None,
    total_max: float | None = None,
    page_min: int | None = None,
    page_max: int | None = None,
    batch_ids: list[int] | None = None,
    include_deleted: bool = False,
    has_extraction: bool | None = None,
    supplier_name_filter: str | None = None,
    doc_id: int | None = None,
    document_type_ids: list[int] | None = None,
) -> list[Document]:
    """
    Gibt eine gefilterte und sortierte (ID desc) Dokumentliste zurück.

    Joins werden nur bei Bedarf durchgeführt (performance-optimiert):
    - InvoiceExtraction: nur wenn total_min/max, has_extraction oder supplier_name gesetzt
    - ImportBatch: nur wenn company oder year gesetzt

    Für die Listenansicht werden nur batch + extraction geladen (kein joinedload von
    order_positions oder token_counts — das käme nur bei get_by_id_with_details).
    """
    query = db.query(Document).options(
        joinedload(Document.batch),
        joinedload(Document.extraction),
    )

    if not include_deleted:
        query = query.filter(Document.soft_deleted == False)  # noqa: E712

    if batch_ids:
        query = query.filter(Document.batch_id.in_(batch_ids))

    needs_join = (
        total_min is not None
        or total_max is not None
        or has_extraction is not None
        or supplier_name_filter is not None
    )
    if needs_join:
        query = query.outerjoin(InvoiceExtraction, Document.id == InvoiceExtraction.document_id)

    if total_min is not None:
        query = query.filter(InvoiceExtraction.total_amount_brutto >= Decimal(str(total_min)))
    if total_max is not None:
        query = query.filter(InvoiceExtraction.total_amount_brutto <= Decimal(str(total_max)))

    if has_extraction is True:
        query = query.filter(InvoiceExtraction.id.isnot(None))
    elif has_extraction is False:
        query = query.filter(InvoiceExtraction.id.is_(None))

    # vendor_id ist der Lieferantenname-String in der neuen Extraktion
    if supplier_name_filter:
        query = query.filter(InvoiceExtraction.vendor_id.ilike(f"%{supplier_name_filter}%"))

    # company/year über Batch-Join filtern
    if company or year is not None:
        query = query.join(ImportBatch, Document.batch_id == ImportBatch.id)
        if company:
            query = query.filter(ImportBatch.company_name.ilike(f"%{company}%"))
        if year is not None:
            query = query.filter(ImportBatch.year == year)

    if status:
        query = query.filter(Document.status == status)
    if page_min is not None:
        query = query.filter(Document.page_count >= page_min)
    if page_max is not None:
        query = query.filter(Document.page_count <= page_max)
    if doc_id is not None:
        query = query.filter(Document.id == doc_id)
    if document_type_ids:
        query = query.filter(Document.document_type.in_(document_type_ids))

    return query.order_by(Document.id.desc()).all()


def save_extraction(
    db: Session,
    doc_id: int,
    extracted_data: dict,
    positions: list[dict],
    raw_response: str,
    ki_stats: dict | None = None,
) -> InvoiceExtraction:
    """Speichert extrahierte Rechnungsdaten. KI-Stats kommen in DocumentTokenCount."""
    db.query(InvoiceExtraction).filter(InvoiceExtraction.document_id == doc_id).delete()
    db.query(OrderPosition).filter(OrderPosition.document_id == doc_id).delete()
    # DocumentTokenCount wird NICHT gelöscht — jeder Analyse-Durchlauf bekommt
    # einen eigenen Eintrag, damit die Historie aller Durchläufe erhalten bleibt.

    # Vendor-Daten vor dem Filtern sichern (werden für find_or_create benötigt)
    _vendor_data = {
        "name":         extracted_data.get("supplier_name"),
        "street":       extracted_data.get("supplier_street"),
        "postal_code":  extracted_data.get("supplier_zip"),
        "city":         extracted_data.get("supplier_city"),
        "hrb_number":   extracted_data.get("hrb_number"),
        "tax_number":   extracted_data.get("tax_number"),
        "vat_id":       extracted_data.get("vat_id"),
        "bank_name":    extracted_data.get("bank_name"),
        "iban":         extracted_data.get("iban"),
        "bic":          extracted_data.get("bic"),
    }

    # Mapping alter Feldnamen → neue InvoiceExtraction-Felder
    _FIELD_MAP = {
        "supplier_name": "vendor_id",
        "total_amount": "total_amount_brutto",
    }
    # Felder die nicht in InvoiceExtraction existieren
    _SKIP_KEYS = {
        "supplier_address", "hrb_number", "tax_number", "vat_id",
        "bank_name", "iban", "bic", "customer_number", "order_number",
        "supplier_street", "supplier_zip", "supplier_city",
    }
    mapped = {}
    for k, v in extracted_data.items():
        if k in _SKIP_KEYS:
            continue
        mapped[_FIELD_MAP.get(k, k)] = v

    extraction = InvoiceExtraction(
        document_id=doc_id,
        raw_response=raw_response,
        **mapped,
    )
    db.add(extraction)

    for idx, pos in enumerate(positions):
        # Positions-Felder mappen
        pos_mapped = {}
        for k, v in pos.items():
            if k == "unit_price":
                pos_mapped["unit_price_netto"] = v
            elif k == "total_price":
                pass  # nicht mehr in Model
            elif k == "product_description" and "product_name" not in pos:
                pos_mapped["product_description"] = v
            else:
                pos_mapped[k] = v
        db.add(OrderPosition(document_id=doc_id, position_index=idx, **pos_mapped))

    try:
        db.commit()
    except Exception as exc:
        logger.warning("Extraction-Commit fehlgeschlagen, Retry ohne Stats: %s", exc)
        db.rollback()
        db.query(InvoiceExtraction).filter(InvoiceExtraction.document_id == doc_id).delete()
        db.query(OrderPosition).filter(OrderPosition.document_id == doc_id).delete()
        db.add(InvoiceExtraction(document_id=doc_id, raw_response=raw_response))
        db.commit()
        db.refresh(db.query(InvoiceExtraction).filter(
            InvoiceExtraction.document_id == doc_id
        ).first())
        return db.query(InvoiceExtraction).filter(
            InvoiceExtraction.document_id == doc_id
        ).first()

    # Lieferant in vendor-Tabelle anlegen / aktualisieren (Deduplication)
    if _vendor_data.get("name"):
        try:
            from app.crud import vendor as _vendor_crud
            _vendor_crud.find_or_create(db, **_vendor_data)
        except Exception as exc:
            logger.warning("Vendor find_or_create fehlgeschlagen (nicht kritisch): %s", exc)

    # KI-Stats in DocumentTokenCount speichern
    if ki_stats:
        token_count = DocumentTokenCount(
            document_id=doc_id,
            input_token_count=(ki_stats.get("input_tokens") or 0),
            output_token_count=(ki_stats.get("output_tokens") or 0),
            reasoning_count=ki_stats.get("reasoning_tokens") or 0,
            time_spent_seconds=ki_stats.get("total_duration") or 0.0,
        )
        db.add(token_count)
        try:
            db.commit()
        except Exception:
            db.rollback()

    try:
        db.refresh(extraction)
    except Exception:
        pass  # Refresh nach ki_stats-Rollback kann fehlschlagen; Extraktion wurde bereits committed
    return extraction
