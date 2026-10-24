"""
Router für Lieferanten-Stammdaten.

Endpunkte:
  GET    /api/vendors          — alle Lieferanten auflisten
  GET    /api/vendors/duplicate-groups — Dublettensuche (IBAN, USt-IdNr., Steuernr., Name)
  POST   /api/vendors/merge    — Lieferanten zusammenführen
  GET    /api/vendors/export           — alle Belege/Positionen gewählter Lieferanten als Excel (?ids=1&ids=2)
  GET    /api/vendors/orphan-names     — Belege, deren Lieferantenname zu keinem Lieferanten gehört (mit Vorschlag)
  POST   /api/vendors/reassign-names   — bestätigte Zuordnungen umschreiben (protokolliert, rückgängig machbar)
  GET    /api/vendors/{id}     — einzelnen Lieferanten abrufen
  PUT    /api/vendors/{id}     — Lieferantendaten aktualisieren
  DELETE /api/vendors/{id}     — Lieferanten löschen (inkl. aller Bankkonten)

Lieferanten werden beim Anlegen automatisch dedupliziert (find_or_create in crud/vendor.py).
Diese Endpunkte dienen zur manuellen Verwaltung bestehender Einträge.
"""

import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas.vendor import (
    DuplicateGroupRead, OrphanNamesRead, OrphanReassignRequest, OrphanReassignResult,
    VendorMergeRequest, VendorMergeResult, VendorRead, VendorUpdate,
)
from app.schemas.workflow import SupplierWorkflowUpdate
from app.services import supplier_dedup, vendor_repair

router = APIRouter(prefix="/api/vendors", tags=["Lieferanten"])


def _read(vendor, counts: dict[str, int]) -> VendorRead:
    """VendorRead inkl. Belegzähler (Verknüpfung über den Lieferantennamen)."""
    out = VendorRead.model_validate(vendor)
    out.document_count = counts.get(crud.vendor.name_key(vendor.name), 0)
    return out


@router.get("", response_model=list[VendorRead])
def list_vendors(db: Session = Depends(get_db)):
    """
    Gibt alle Lieferanten zurück, nach Name sortiert.

    Falls die vendor-Tabelle leer ist (z.B. nach Migration von einer älteren Version),
    werden Lieferantennamen aus invoice_extractions automatisch in die vendor-Tabelle
    übertragen (Einmal-Backfill).
    """
    from sqlalchemy import distinct as _distinct
    from app.models.invoice_extraction import InvoiceExtraction as _Ext

    vendors = crud.vendor.get_all(db)

    if not vendors:
        # Backfill: alle eindeutigen Lieferantennamen aus invoice_extractions übernehmen
        names = (
            db.query(_distinct(_Ext.vendor_id))
            .filter(_Ext.vendor_id.isnot(None), _Ext.vendor_id != "")
            .all()
        )
        for (name,) in names:
            try:
                crud.vendor.find_or_create(db, name=name)
            except Exception:
                pass
        vendors = crud.vendor.get_all(db)

    counts = crud.vendor.get_document_counts(db)
    return [_read(v, counts) for v in sorted(vendors, key=lambda v: (v.name or "").lower())]


class _DedupView:
    """Schlanke Sicht auf einen Lieferanten für die Dublettensuche (mehrere IBANs)."""

    def __init__(self, vendor):
        self.id, self.name = vendor.id, vendor.name
        self.vat_id, self.tax_number = vendor.vat_id, vendor.tax_number
        self.ibans = [ba.iban for ba in vendor.bank_accounts if ba.iban]


@router.get("/duplicate-groups", response_model=list[DuplicateGroupRead])
def get_duplicate_groups(
    criteria: list[str] | None = Query(
        default=None, description="Gesuchte Merkmale: iban, vat_id, tax_number, name, name_similar (leer = alle)"),
    name_check: bool = Query(True, description="Nummern-Treffer nur bei ähnlichem Namen werten"),
    min_name_similarity: float = Query(0.5, ge=0, le=1, description="Mindest-Namensähnlichkeit für name_check"),
    similarity: float = Query(supplier_dedup.SIMILARITY_THRESHOLD, ge=0.5, le=1,
                              description="Schwelle für „ähnlicher Name“"),
    max_key_share: int = Query(10, ge=2, le=1000, description="Nummern, die bei mehr Lieferanten vorkommen, ignorieren"),
    db: Session = Depends(get_db),
):
    """
    Dublettensuche: gleiche IBAN / USt-IdNr. / Steuernummer (hohe Konfidenz), gleicher Name ohne
    Rechtsform (mittel), sehr ähnlicher Name (niedrig). Es wird nichts verändert.
    """
    by_id = {v.id: v for v in crud.vendor.get_all(db)}
    counts = crud.vendor.get_document_counts(db)
    unknown = set(criteria or []) - set(supplier_dedup.REASON_LABELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unbekannte Merkmale: {sorted(unknown)}")
    groups = supplier_dedup.find_duplicate_groups(
        (_DedupView(v) for v in by_id.values()),
        criteria=criteria or None,
        min_name_similarity=min_name_similarity if name_check else 0.0,
        max_key_share=max_key_share,
        similarity_threshold=similarity,
    )
    return [
        DuplicateGroupRead(
            suppliers=[_read(by_id[i], counts) for i in g.supplier_ids],
            reasons=g.reasons,
            reason_labels=[supplier_dedup.REASON_LABELS[r] for r in g.reasons],
            confidence=g.confidence,
        )
        for g in groups
    ]


@router.post("/merge", response_model=VendorMergeResult)
def merge_vendors(payload: VendorMergeRequest, db: Session = Depends(get_db)):
    """
    Führt Lieferanten zusammen: leere Stammdaten des Ziels werden aufgefüllt, Bankverbindungen übernommen,
    die Quellen gelöscht und (Standard) die Belege auf den Namen des Ziels umgeschrieben. Eine Transaktion.
    """
    try:
        result = crud.vendor.merge(
            db, payload.target_id, payload.source_ids,
            fields=payload.fields, update_document_names=payload.update_document_names,
        )
    except crud.vendor.MergeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return VendorMergeResult(
        supplier=_read(result.vendor, crud.vendor.get_document_counts(db)),
        merged_ids=result.merged_ids,
        moved_documents=result.moved_documents,
        moved_bank_accounts=result.moved_bank_accounts,
    )


@router.get("/export")
def export_vendors_excel(
    ids: list[int] = Query(..., description="Lieferanten-IDs (mehrfach angeben)"),
    include_hidden: bool = Query(False, description="Belege ausgeblendeter Imports einbeziehen"),
    db: Session = Depends(get_db),
):
    """
    Exportiert ALLE Belege (Sheet „Rechnungen") und Positionen (Sheet „Positionen") der gewählten Lieferanten in eine Excel-Datei
    — über alle sichtbaren Imports hinweg (ausgeblendete Imports nur mit include_hidden=true), nicht gelöschte Belege, gleiche Spalten wie der Import-Export.
    Die Zuordnung läuft über den extrahierten Lieferantennamen (Groß/Klein und Leerzeichen egal); liegen die Belege in mehreren
    Imports, kommt am Ende die Spalte „Import" dazu.
    """
    from app.models.document import Document
    from app.models.import_batch import ImportBatch
    from app.models.invoice_extraction import InvoiceExtraction
    from app.routers.imports import _build_export_excel, _content_disposition, _get_export_config_fields
    from sqlalchemy.orm import joinedload

    ids = list(dict.fromkeys(ids))
    vendors = [crud.vendor.get_by_id(db, i) for i in ids]
    missing = [i for i, v in zip(ids, vendors) if v is None]
    if missing:
        raise HTTPException(status_code=404, detail="Lieferant(en) nicht gefunden: " + ", ".join(f"#{i}" for i in missing))
    keys = {crud.vendor.name_key(v.name) for v in vendors}
    names = [n for (n,) in db.query(InvoiceExtraction.vendor_id).filter(InvoiceExtraction.vendor_id.isnot(None)).distinct()
             if crud.vendor.name_key(n) in keys]
    base = (
        db.query(Document.id).join(InvoiceExtraction, InvoiceExtraction.document_id == Document.id)
        .join(ImportBatch, ImportBatch.id == Document.batch_id)
        .filter(True if include_hidden else ImportBatch.hidden_at.is_(None))
        .filter(InvoiceExtraction.vendor_id.in_(names), Document.soft_deleted == False)  # noqa: E712
    ) if names else None
    batch_ids = sorted({r[0] for r in db.query(Document.batch_id).filter(Document.id.in_(base.subquery().select()))}) if base is not None else []
    if not batch_ids:
        raise HTTPException(status_code=404, detail="Zu den gewählten Lieferanten gibt es keine Belege.")
    batches = db.query(ImportBatch).filter(ImportBatch.id.in_(batch_ids)).order_by(ImportBatch.id).all()

    def docs():
        last = 0
        while True:
            chunk = (
                db.query(Document).join(InvoiceExtraction, InvoiceExtraction.document_id == Document.id)
                .join(ImportBatch, ImportBatch.id == Document.batch_id)
                .filter(True if include_hidden else ImportBatch.hidden_at.is_(None))
                .options(joinedload(Document.extraction), joinedload(Document.order_positions))
                .filter(InvoiceExtraction.vendor_id.in_(names), Document.soft_deleted == False, Document.id > last)  # noqa: E712
                .order_by(Document.id).limit(300).all()
            )
            if not chunk:
                break
            last = chunk[-1].id
            yield from chunk
            db.expunge_all()

    invoice_fields, position_fields = _get_export_config_fields(db)
    title = f"Export: {vendors[0].name}" if len(vendors) == 1 else f"Export: {len(vendors)} Lieferanten"
    excel = _build_export_excel(batches[0], docs(), invoice_fields, position_fields,
                                batches=batches if len(batches) > 1 else None, title=title)
    label = (vendors[0].name if len(vendors) == 1 else f"{len(vendors)}_Lieferanten").replace(" ", "_")[:60]
    return StreamingResponse(
        io.BytesIO(excel),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": _content_disposition(f"Export_Lieferant_{label}_{date.today().isoformat()}.xlsx")},
    )


@router.get("/orphan-names", response_model=OrphanNamesRead)
def get_orphan_names(limit: int = Query(vendor_repair.DEFAULT_LIMIT, ge=1, le=2000), db: Session = Depends(get_db)):
    """Verwaiste Belegnamen (z. B. nach früherem Zusammenführen) mit Zuordnungsvorschlag. Verändert nichts."""
    return vendor_repair.find_orphans(db, limit)


@router.post("/reassign-names", response_model=OrphanReassignResult)
def reassign_orphan_names(payload: OrphanReassignRequest, db: Session = Depends(get_db)):
    """Schreibt ausdrücklich bestätigte Zuordnungen um (eine Transaktion, im Änderungsprotokoll rückgängig machbar)."""
    try:
        return vendor_repair.apply(db, [(a.name, a.target_id) for a in payload.assignments])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{vendor_id}", response_model=VendorRead)
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    """Gibt einen Lieferanten anhand seiner ID zurück. 404 wenn nicht gefunden."""
    obj = crud.vendor.get_by_id(db, vendor_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return obj


@router.put("/{vendor_id}", response_model=VendorRead)
def update_vendor(vendor_id: int, payload: VendorUpdate, db: Session = Depends(get_db)):
    """Aktualisiert Felder eines Lieferanten. 404 wenn nicht gefunden."""
    obj = crud.vendor.update(db, vendor_id, payload.model_dump())
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return obj


@router.delete("/{vendor_id}", status_code=204)
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    """Löscht einen Lieferanten dauerhaft (inkl. aller Bankkonten). 404 wenn nicht gefunden."""
    if not crud.vendor.delete(db, vendor_id):
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
