from sqlalchemy.orm import Session

from app.models.vendor import Vendor
from app.models.vendor_bank_account import VendorBankAccount


def get_all(db: Session) -> list[Vendor]:
    return db.query(Vendor).order_by(Vendor.id).all()


def get_by_id(db: Session, vendor_id: int) -> Vendor | None:
    return db.get(Vendor, vendor_id)


def find_or_create(
    db: Session,
    name: str | None,
    street: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    country: str | None = None,
    hrb_number: str | None = None,
    tax_number: str | None = None,
    vat_id: str | None = None,
    bank_name: str | None = None,
    iban: str | None = None,
    bic: str | None = None,
) -> Vendor | None:
    """Findet oder erstellt einen Vendor. Deduplication nach IBAN → VAT-ID → Name."""
    if not name:
        return None

    existing: Vendor | None = None

    # 1. IBAN (stärkster Identifier)
    if iban:
        existing = (
            db.query(Vendor)
            .join(VendorBankAccount, Vendor.id == VendorBankAccount.vendor_id)
            .filter(VendorBankAccount.iban == iban)
            .first()
        )

    # 2. VAT-ID
    if existing is None and vat_id:
        existing = db.query(Vendor).filter(Vendor.vat_id == vat_id).first()

    # 3. Name
    if existing is None:
        existing = db.query(Vendor).filter(Vendor.name == name).first()

    if existing is not None:
        # Fehlende Felder ergänzen
        if street and not existing.street:
            existing.street = street
        if postal_code and not existing.postal_code:
            existing.postal_code = postal_code
        if city and not existing.city:
            existing.city = city
        if hrb_number and not existing.hrb_number:
            existing.hrb_number = hrb_number
        if tax_number and not existing.tax_number:
            existing.tax_number = tax_number
        if vat_id and not existing.vat_id:
            existing.vat_id = vat_id
        # Bankverbindung ergänzen falls noch nicht vorhanden
        if iban and not any(ba.iban == iban for ba in existing.bank_accounts):
            db.add(VendorBankAccount(
                vendor_id=existing.id, bank_name=bank_name, iban=iban, bic=bic
            ))
        db.commit()
        db.refresh(existing)
        return existing

    # Neu anlegen
    vendor = Vendor(
        name=name,
        street=street,
        postal_code=postal_code,
        city=city,
        country=country,
        hrb_number=hrb_number,
        tax_number=tax_number,
        vat_id=vat_id,
    )
    db.add(vendor)
    db.flush()  # vendor.id verfügbar machen

    if iban or bank_name:
        db.add(VendorBankAccount(
            vendor_id=vendor.id, bank_name=bank_name, iban=iban, bic=bic
        ))

    db.commit()
    db.refresh(vendor)
    return vendor


def update(db: Session, vendor_id: int, data: dict) -> Vendor | None:
    obj = db.get(Vendor, vendor_id)
    if obj is None:
        return None
    for k, v in data.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, vendor_id: int) -> bool:
    obj = db.get(Vendor, vendor_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True
