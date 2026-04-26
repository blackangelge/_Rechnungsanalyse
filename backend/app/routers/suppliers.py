"""
Router für Lieferanten-Stammdaten.

Endpunkte:
  GET    /api/suppliers              — alle Lieferanten (mit Dokumentanzahl)
  GET    /api/suppliers/duplicates   — potenzielle Duplikate
  GET    /api/suppliers/{id}         — einzelner Lieferant
  PUT    /api/suppliers/{id}         — Lieferant aktualisieren
  DELETE /api/suppliers/{id}         — Lieferant löschen
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas.supplier import SupplierRead, SupplierUpdate

router = APIRouter(prefix="/api/suppliers", tags=["Lieferanten"])


def _with_count(db: Session, supplier) -> dict:
    """Ergänzt einen Lieferanten um die Dokumentanzahl."""
    data = {c.name: getattr(supplier, c.name) for c in supplier.__table__.columns}
    data["document_count"] = crud.supplier.get_document_count(db, supplier.id)
    return data


@router.get("", response_model=list[SupplierRead])
def list_suppliers(db: Session = Depends(get_db)):
    """Gibt alle Lieferanten mit Dokumentanzahl zurück."""
    suppliers = crud.supplier.get_all(db)
    return [_with_count(db, s) for s in suppliers]


@router.get("/duplicates", response_model=list[list[SupplierRead]])
def get_duplicates(db: Session = Depends(get_db)):
    """Findet potenzielle Duplikate (gleicher Name)."""
    groups = crud.supplier.find_duplicates(db)
    return [
        [_with_count(db, s) for s in group]
        for group in groups
    ]


@router.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
    """Gibt einen einzelnen Lieferanten zurück."""
    obj = crud.supplier.get_by_id(db, supplier_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return _with_count(db, obj)


@router.put("/{supplier_id}", response_model=SupplierRead)
def update_supplier(supplier_id: int, payload: SupplierUpdate, db: Session = Depends(get_db)):
    """Aktualisiert einen Lieferanten."""
    obj = crud.supplier.update(db, supplier_id, payload.model_dump())
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return _with_count(db, obj)


@router.delete("/{supplier_id}", status_code=204)
def delete_supplier(supplier_id: int, db: Session = Depends(get_db)):
    """Löscht einen Lieferanten (supplier_id in Extraktionen wird auf NULL gesetzt)."""
    if not crud.supplier.delete(db, supplier_id):
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
