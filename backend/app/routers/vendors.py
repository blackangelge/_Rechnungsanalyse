"""
Router für Lieferanten-Stammdaten.

Endpunkte:
  GET    /api/vendors          — alle Lieferanten auflisten
  GET    /api/vendors/duplicate-groups — Dublettensuche (IBAN, USt-IdNr., Steuernr., Name)
  POST   /api/vendors/merge    — Lieferanten zusammenführen
  GET    /api/vendors/{id}     — einzelnen Lieferanten abrufen
  PUT    /api/vendors/{id}     — Lieferantendaten aktualisieren
  PUT    /api/vendors/{id}/workflow — Analyse-Workflow des Lieferanten festlegen (null = Standard)
  DELETE /api/vendors/{id}     — Lieferanten löschen (inkl. aller Bankkonten)

Lieferanten werden beim Anlegen automatisch dedupliziert (find_or_create in crud/vendor.py).
Diese Endpunkte dienen zur manuellen Verwaltung bestehender Einträge.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas.vendor import DuplicateGroupRead, VendorMergeRequest, VendorMergeResult, VendorRead, VendorUpdate
from app.schemas.workflow import SupplierWorkflowUpdate
from app.services import supplier_dedup

router = APIRouter(prefix="/api/vendors", tags=["Lieferanten"])


def _read(vendor, counts: dict[str, int]) -> VendorRead:
    """VendorRead inkl. Belegzähler (Verknüpfung über den Lieferantennamen)."""
    out = VendorRead.model_validate(vendor)
    out.document_count = counts.get(vendor.name, 0)
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
def get_duplicate_groups(db: Session = Depends(get_db)):
    """
    Dublettensuche: gleiche IBAN / USt-IdNr. / Steuernummer (hohe Konfidenz), gleicher Name ohne
    Rechtsform (mittel), sehr ähnlicher Name (niedrig). Es wird nichts verändert.
    """
    by_id = {v.id: v for v in crud.vendor.get_all(db)}
    counts = crud.vendor.get_document_counts(db)
    groups = supplier_dedup.find_duplicate_groups(_DedupView(v) for v in by_id.values())
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


@router.put("/{vendor_id}/workflow", response_model=VendorRead)
def set_vendor_workflow(vendor_id: int, payload: SupplierWorkflowUpdate, db: Session = Depends(get_db)):
    """
    Legt den Analyse-Workflow dieses Lieferanten fest (null = Standard). Er greift bei einer
    erneuten Analyse, sobald der Lieferant (über den extrahierten Namen) bekannt ist.
    """
    if payload.workflow_id is not None and crud.workflow.get_by_id(db, payload.workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    obj = crud.workflow.set_supplier_workflow(db, vendor_id, payload.workflow_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return obj


@router.delete("/{vendor_id}", status_code=204)
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    """Löscht einen Lieferanten dauerhaft (inkl. aller Bankkonten). 404 wenn nicht gefunden."""
    if not crud.vendor.delete(db, vendor_id):
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
