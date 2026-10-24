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

import json
import logging
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session, defer, joinedload

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
    error_code: str | None = None,
) -> Document | None:
    """
    Setzt den Dokument-Status (pending/processing/done/error/waiting_capacity).

    error_message wird — falls angegeben — zusätzlich in raw_response persistiert
    (für Status-Übergänge VOR einem echten KI-Aufruf gibt es sonst keine KI-Antwort
    zu speichern, z.B. "Dokument zu groß für alle konfigurierten KI-Server" bei
    waiting_capacity; dieselbe Stelle wird im Frontend über den "KI"-Button
    angezeigt). Wird KEIN error_message übergeben, bleibt raw_response unverändert.
    """
    obj = db.get(Document, doc_id)
    if obj is None:
        return None
    obj.status = status
    if error_message is not None:
        obj.raw_response = error_message
    # Der Fehlergrund gilt nur, solange der Beleg im Fehlerzustand ist (neue Analyse/Erfolg setzt ihn zurück)
    obj.error_code = error_code if status == "error" else None
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


_FILTER_KEYS = (
    "company", "year", "status", "total_min", "total_max", "page_min", "page_max", "batch_ids",
    "include_deleted", "has_extraction", "supplier_name_filter", "doc_id", "document_type_ids",
    "validation_statuses", "include_hidden", "error_codes",
)


def _filtered_query(db: Session, **f):
    """
    Basisabfrage mit allen Filtern (ohne Eager-Loading, Sortierung und Paginierung) — gemeinsam
    für Liste, Zähler und ID-Liste, damit alle drei garantiert dieselben Treffer meinen.

    Für Betrags-, KI-, Lieferanten- und Prüfstatus-Filter wird ein Outer Join auf
    invoice_extractions gemacht (1:1 → keine Zeilenvervielfachung); Firma/Jahr kommen aus dem Import.
    """
    unknown = set(f) - set(_FILTER_KEYS)
    if unknown:
        raise TypeError(f"Unbekannte Filter: {sorted(unknown)}")
    g = f.get
    query = db.query(Document)

    if not g("include_deleted"):
        query = query.filter(Document.soft_deleted == False)  # noqa: E712

    if g("batch_ids"):
        query = query.filter(Document.batch_id.in_(g("batch_ids")))
    elif not g("include_hidden") and g("doc_id") is None:
        # Belege ausgeblendeter Imports nur auf Wunsch (Ausnahmen: ausdrücklich gewählter Import, Beleg-ID-Suche)
        query = query.filter(~Document.batch_id.in_(
            db.query(ImportBatch.id).filter(ImportBatch.hidden_at.isnot(None))))

    needs_join = (
        g("total_min") is not None
        or g("total_max") is not None
        or g("has_extraction") is not None
        or g("supplier_name_filter") is not None
        or bool(g("validation_statuses"))
    )
    if needs_join:
        query = query.outerjoin(InvoiceExtraction, Document.id == InvoiceExtraction.document_id)

    if g("total_min") is not None:
        query = query.filter(InvoiceExtraction.total_amount_brutto >= Decimal(str(g("total_min"))))
    if g("total_max") is not None:
        query = query.filter(InvoiceExtraction.total_amount_brutto <= Decimal(str(g("total_max"))))
    if g("has_extraction") is True:
        query = query.filter(InvoiceExtraction.id.isnot(None))
    elif g("has_extraction") is False:
        query = query.filter(InvoiceExtraction.id.is_(None))
    if g("validation_statuses"):
        query = query.filter(InvoiceExtraction.validation_status.in_(g("validation_statuses")))
    if g("error_codes"):
        codes = g("error_codes")
        query = query.filter(Document.error_code.isnot(None) if "any" in codes else Document.error_code.in_(codes))
    # vendor_id ist der Lieferantenname-String in der Extraktion
    if g("supplier_name_filter"):
        query = query.filter(InvoiceExtraction.vendor_id.ilike(f"%{g('supplier_name_filter')}%"))

    if g("company") or g("year") is not None:
        query = query.join(ImportBatch, Document.batch_id == ImportBatch.id)
        if g("company"):
            query = query.filter(ImportBatch.company_name.ilike(f"%{g('company')}%"))
        if g("year") is not None:
            query = query.filter(ImportBatch.year == g("year"))

    if g("status"):
        query = query.filter(Document.status == g("status"))
    if g("page_min") is not None:
        query = query.filter(Document.page_count >= g("page_min"))
    if g("page_max") is not None:
        query = query.filter(Document.page_count <= g("page_max"))
    if g("doc_id") is not None:
        query = query.filter(Document.id == g("doc_id"))
    if g("document_type_ids"):
        query = query.filter(Document.document_type.in_(g("document_type_ids")))
    return query


def get_all_filtered(
    db: Session,
    limit: int | None = None,
    offset: int = 0,
    **filters,
) -> list[Document]:
    """
    Gibt Dokumente (neueste zuerst) zurück, optional gefiltert und paginiert.

    Für die Listenansicht werden die schweren Textspalten NICHT geladen (KI-Rohantwort,
    Prüfdetails, Prompt-/Workflow-Snapshots) — bei tausenden Belegen machen sie sonst
    hunderte MB aus, obwohl die Liste sie nie anzeigt. Wer in der Liste neue Felder braucht,
    darf sie nicht aus `raw_response` lesen (dafür gibt es GET /{id}).
    """
    query = _filtered_query(db, **filters).options(
        joinedload(Document.batch),
        joinedload(Document.extraction)
        .defer(InvoiceExtraction.raw_response)
        .defer(InvoiceExtraction.validation_details),
        defer(Document.raw_response),
        defer(Document.used_system_prompt),
        defer(Document.used_doc_type_prompt),
        defer(Document.used_recheck_prompt),
        defer(Document.used_workflow_graph),
        defer(Document.workflow_trace),
    ).order_by(Document.id.desc())
    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(limit)
    return query.all()


def count_filtered(db: Session, **filters) -> int:
    """Anzahl der Treffer (ohne Eager-Loading → schnell)."""
    return _filtered_query(db, **filters).with_entities(func.count(Document.id)).scalar() or 0


def get_ids_filtered(db: Session, **filters) -> list[int]:
    """Alle Treffer-IDs (neueste zuerst) — z.B. für „alle Treffer auswählen"."""
    rows = _filtered_query(db, **filters).with_entities(Document.id).order_by(Document.id.desc()).all()
    return [r[0] for r in rows]


def get_filter_options(db: Session, include_hidden: bool = False) -> dict:
    """
    Werte für die Filter-Dropdowns (früher aus den geladenen Belegen abgeleitet — mit Paginierung
    nicht mehr möglich): Jahre und Firmen der Imports mit nicht gelöschten Belegen, Lieferantennamen.
    """
    from app.models.vendor import Vendor

    batch_ids = db.query(Document.batch_id).filter(Document.soft_deleted == False).distinct()  # noqa: E712
    if not include_hidden:
        batch_ids = batch_ids.filter(~Document.batch_id.in_(
            db.query(ImportBatch.id).filter(ImportBatch.hidden_at.isnot(None))))
    years = [r[0] for r in db.query(ImportBatch.year).filter(ImportBatch.id.in_(batch_ids))
             .distinct().order_by(ImportBatch.year.desc()).all()]
    companies = [r[0] for r in db.query(ImportBatch.company_name).filter(ImportBatch.id.in_(batch_ids))
                 .distinct().order_by(ImportBatch.company_name).all()]
    suppliers = [r[0] for r in db.query(Vendor.name).distinct().order_by(Vendor.name).all()]
    return {"years": years, "companies": companies, "suppliers": suppliers}


def save_extraction(
    db: Session,
    doc_id: int,
    extracted_data: dict,
    positions: list[dict],
    raw_response: str,
    ki_stats: dict | None = None,
    validation: dict | None = None,
) -> InvoiceExtraction:
    """
    Speichert extrahierte Rechnungsdaten. KI-Stats kommen in DocumentTokenCount.

    validation: optionales Ergebnis der Plausibilitätsprüfung (ValidationResult.to_dict() +
                Zusatzinfos) → invoice_extractions.validation_status / validation_details.
    """
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

    validation_cols = (
        {
            "validation_status": validation.get("status"),
            "validation_details": json.dumps(validation, ensure_ascii=False),
        }
        if validation else {}
    )
    extraction = InvoiceExtraction(
        document_id=doc_id,
        raw_response=raw_response,
        **validation_cols,
        **mapped,
    )
    db.add(extraction)

    for idx, pos in enumerate(positions):
        # Positions-Felder mappen
        pos_mapped = {}
        for k, v in pos.items():
            if k == "unit_price":
                pos_mapped["unit_price_netto"] = v
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


# ─── Plausibilitätsprüfung ────────────────────────────────────────────────────

def update_validation(
    db: Session,
    doc_id: int,
    validation: dict,
    corrected_positions: list[dict] | None = None,
    commit: bool = True,
) -> InvoiceExtraction | None:
    """
    Schreibt ein (neues) Prüfergebnis in die Extraktion und übernimmt die dabei entstandenen
    Korrekturen in die Daten (`corrected_positions` = ValidationResult.corrections):
      • {"position": idx, "to": wert}            → order_positions.total_price
      • {"position": idx, "field": "unit", "to": einheit} → order_positions.unit
      • {"position": idx, "field": "normalize", "to": {quantity, unit, unit_price}} → Menge/Einheit/Preis in Tonnen
      • {"field": "total_amount", "to": wert}    → invoice_extractions.total_amount_brutto
    """
    extraction = (
        db.query(InvoiceExtraction).filter(InvoiceExtraction.document_id == doc_id).first()
    )
    if extraction is None:
        return None
    for corr in corrected_positions or []:
        if "position" in corr:
            pos = (
                db.query(OrderPosition)
                .filter(
                    OrderPosition.document_id == doc_id,
                    OrderPosition.position_index == corr["position"],
                )
                .first()
            )
            if pos is not None:
                if corr.get("field") == "normalize":
                    to = corr["to"]
                    pos.quantity = Decimal(str(to["quantity"]))
                    pos.unit = to["unit"]
                    if to.get("unit_price") is not None:
                        pos.unit_price_netto = Decimal(str(to["unit_price"]))
                elif corr.get("field") == "unit":
                    pos.unit = str(corr["to"])
                else:
                    pos.total_price = Decimal(str(corr["to"]))
        elif corr.get("field") == "total_amount":
            extraction.total_amount_brutto = Decimal(str(corr["to"]))
    extraction.validation_status = validation.get("status")
    extraction.validation_details = json.dumps(validation, ensure_ascii=False)
    if commit:
        db.commit()
        db.refresh(extraction)
    return extraction


def confirm_validation(db: Session, doc_id: int) -> InvoiceExtraction | None:
    """Markiert eine Prüfung ("Prüfen!") manuell als erledigt → Status `manual_ok`."""
    extraction = (
        db.query(InvoiceExtraction).filter(InvoiceExtraction.document_id == doc_id).first()
    )
    if extraction is None:
        return None
    details: dict = {}
    if extraction.validation_details:
        try:
            details = json.loads(extraction.validation_details) or {}
        except (TypeError, ValueError):
            details = {}
    details["status"] = "manual_ok"
    details["confirmed_manually"] = True
    extraction.validation_status = "manual_ok"
    extraction.validation_details = json.dumps(details, ensure_ascii=False)
    db.commit()
    db.refresh(extraction)
    return extraction


def save_used_prompts(
    db: Session,
    doc_id: int,
    system_prompt: str | None = None,
    doc_type_prompt: str | None = None,
) -> None:
    """
    Speichert den Wortlaut der bei der Analyse verwendeten Systemprompts am Dokument.
    Wird zu Beginn jeder (Neu-)Analyse aufgerufen und überschreibt die vorherigen Prompts;
    der Nachprüfungs-Prompt wird dabei zurückgesetzt.
    """
    obj = db.get(Document, doc_id)
    if obj is None:
        return
    # Bisherigen Stand (Extraktion + Prompts) aufheben, bevor er ersetzt wird.
    # Wirft nie — die Historie darf die Analyse nicht stören (siehe crud/analysis_history.py).
    from app.crud import analysis_history
    analysis_history.archive_current(db, doc_id)
    obj = db.get(Document, doc_id)
    obj.used_system_prompt = system_prompt
    obj.used_doc_type_prompt = doc_type_prompt
    obj.used_recheck_prompt = None
    # Workflow-Angaben der vorherigen Analyse zurücksetzen (neue Analyse = neuer Stand)
    obj.used_workflow_id = None
    obj.used_workflow_name = None
    obj.used_workflow_graph = None
    obj.workflow_trace = None
    db.commit()


def save_recheck_prompt(db: Session, doc_id: int, recheck_prompt: str | None) -> None:
    """Speichert den Prompt der zweiten KI-Abfrage (Plausibilitäts-Nachprüfung)."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return
    obj.used_recheck_prompt = recheck_prompt
    db.commit()


def set_used_prompt(
    db: Session,
    doc_id: int,
    system_prompt: str | None = None,
    doc_type_prompt: str | None = None,
) -> None:
    """Setzt nur die übergebenen Prompts (None = unverändert) — für Workflow-Knoten."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return
    if system_prompt is not None:
        obj.used_system_prompt = system_prompt
    if doc_type_prompt is not None:
        obj.used_doc_type_prompt = doc_type_prompt
    db.commit()


def save_workflow_run(
    db: Session,
    doc_id: int,
    workflow_id: int | None,
    workflow_name: str,
    graph: dict,
    trace: list[dict],
) -> None:
    """Speichert Workflow-Snapshot und Ablaufprotokoll der Analyse am Dokument."""
    obj = db.get(Document, doc_id)
    if obj is None:
        return
    obj.used_workflow_id = workflow_id
    obj.used_workflow_name = workflow_name
    obj.used_workflow_graph = json.dumps(graph, ensure_ascii=False)
    obj.workflow_trace = json.dumps(trace, ensure_ascii=False)
    db.commit()
