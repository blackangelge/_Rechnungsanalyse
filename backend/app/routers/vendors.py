from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas.vendor import VendorRead, VendorUpdate

router = APIRouter(prefix="/api/vendors", tags=["Lieferanten"])


@router.get("", response_model=list[VendorRead])
def list_vendors(db: Session = Depends(get_db)):
    return crud.vendor.get_all(db)


@router.get("/{vendor_id}", response_model=VendorRead)
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    obj = crud.vendor.get_by_id(db, vendor_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return obj


@router.put("/{vendor_id}", response_model=VendorRead)
def update_vendor(vendor_id: int, payload: VendorUpdate, db: Session = Depends(get_db)):
    obj = crud.vendor.update(db, vendor_id, payload.model_dump())
    if obj is None:
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
    return obj


@router.delete("/{vendor_id}", status_code=204)
def delete_vendor(vendor_id: int, db: Session = Depends(get_db)):
    if not crud.vendor.delete(db, vendor_id):
        raise HTTPException(status_code=404, detail="Lieferant nicht gefunden")
