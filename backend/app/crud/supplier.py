"""
CRUD-Operationen für Lieferanten-Stammdaten.

Dedup-Logik:
  1. Match nach IBAN (wenn vorhanden und nicht leer)
  2. Match nach VAT-ID (wenn vorhanden und nicht leer)
  3. Match nach Name + Steuernummer
Bei einem Match werden vorhandene Felder nur durch nicht-leere neue Werte aktualisiert.
"""

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.invoice_extraction import InvoiceExtraction
from app.models.supplier import Supplier

logger = logging.getLogger(__name__)


def _is_set(value: str | None) -> bool:
    """Gibt True zurück, wenn der Wert gesetzt und nicht leer ist."""
    return bool(value and value.strip())


def _update_if_better(existing_val: str | None, new_val: str | None) -> str | None:
    """Gibt den neuen Wert zurück, wenn er nicht leer ist — sonst den bestehenden."""
    if _is_set(new_val):
        return new_val
    return existing_val


def get_all(db: Session) -> list[Supplier]:
    """Gibt alle Lieferanten zurück, sortiert nach Name."""
    return db.query(Supplier).order_by(Supplier.name).all()


def get_by_id(db: Session, supplier_id: int) -> Supplier | None:
    """Gibt einen Lieferanten anhand seiner ID zurück oder None."""
    return db.get(Supplier, supplier_id)


def get_document_count(db: Session, supplier_id: int) -> int:
    """Gibt die Anzahl der Dokumente zurück, die diesem Lieferanten zugeordnet sind."""
    return (
        db.query(func.count(InvoiceExtraction.id))
        .filter(InvoiceExtraction.supplier_id == supplier_id)
        .scalar()
        or 0
    )


def update(db: Session, supplier_id: int, data: dict) -> Supplier | None:
    """Aktualisiert einen Lieferanten mit den übergebenen Feldern."""
    obj = db.get(Supplier, supplier_id)
    if obj is None:
        return None
    for field, value in data.items():
        if hasattr(obj, field):
            setattr(obj, field, value if value != "" else None)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, supplier_id: int) -> bool:
    """Löscht einen Lieferanten (setzt supplier_id in Extraktionen auf NULL)."""
    obj = db.get(Supplier, supplier_id)
    if obj is None:
        return False
    # Zugehörige Extraktionen auf supplier_id=NULL setzen
    db.query(InvoiceExtraction).filter(
        InvoiceExtraction.supplier_id == supplier_id
    ).update({"supplier_id": None})
    db.delete(obj)
    db.commit()
    return True


def find_duplicates(db: Session) -> list[list[Supplier]]:
    """
    Findet potenzielle Duplikate anhand von Name-Ähnlichkeit.
    Gibt Gruppen von Lieferanten zurück, die denselben bereinigten Namen haben.
    """
    all_suppliers = db.query(Supplier).all()
    groups: dict[str, list[Supplier]] = {}
    for s in all_suppliers:
        key = s.name.strip().lower() if s.name else ""
        groups.setdefault(key, []).append(s)
    return [group for group in groups.values() if len(group) > 1]


def find_or_create(
    db: Session,
    name: str | None,
    address: str | None = None,
    street: str | None = None,
    zip_code: str | None = None,
    city: str | None = None,
    hrb_number: str | None = None,
    tax_number: str | None = None,
    vat_id: str | None = None,
    bank_name: str | None = None,
    iban: str | None = None,
    bic: str | None = None,
) -> Supplier | None:
    """
    Sucht nach einem passenden Lieferanten und erstellt ihn bei Bedarf.

    Dedup-Priorität:
      1. IBAN (eindeutig, stärkster Indikator)
      2. VAT-ID (USt-IdNr., ebenfalls eindeutig)
      3. Name (wenn gesetzt)

    Bei einem Fund werden leere bestehende Felder mit neuen Werten befüllt,
    aber bestehende Werte werden nicht überschrieben.

    Returns:
        Supplier-Objekt oder None, wenn name leer ist.
    """
    if not _is_set(name):
        logger.debug("Kein Lieferantenname — überspringe Supplier-Anlage")
        return None

    supplier: Supplier | None = None

    # ─── Suche nach IBAN ───────────────────────────────────────────────────
    if _is_set(iban):
        supplier = db.query(Supplier).filter(Supplier.iban == iban.strip()).first()
        if supplier:
            logger.debug("Lieferant via IBAN gefunden: #%d '%s'", supplier.id, supplier.name)

    # ─── Suche nach VAT-ID ─────────────────────────────────────────────────
    if supplier is None and _is_set(vat_id):
        supplier = db.query(Supplier).filter(Supplier.vat_id == vat_id.strip()).first()
        if supplier:
            logger.debug("Lieferant via VAT-ID gefunden: #%d '%s'", supplier.id, supplier.name)

    # ─── Suche nach Name ───────────────────────────────────────────────────
    if supplier is None and _is_set(name):
        supplier = db.query(Supplier).filter(Supplier.name == name.strip()).first()
        if supplier:
            logger.debug("Lieferant via Name gefunden: #%d '%s'", supplier.id, supplier.name)

    if supplier is not None:
        # Vorhandene leere Felder mit neuen Werten befüllen (nicht überschreiben)
        supplier.name = _update_if_better(supplier.name, name)
        supplier.address = _update_if_better(supplier.address, address)
        supplier.street = _update_if_better(supplier.street, street)
        supplier.zip_code = _update_if_better(supplier.zip_code, zip_code)
        supplier.city = _update_if_better(supplier.city, city)
        supplier.hrb_number = _update_if_better(supplier.hrb_number, hrb_number)
        supplier.tax_number = _update_if_better(supplier.tax_number, tax_number)
        supplier.vat_id = _update_if_better(supplier.vat_id, vat_id)
        supplier.bank_name = _update_if_better(supplier.bank_name, bank_name)
        supplier.iban = _update_if_better(supplier.iban, iban)
        supplier.bic = _update_if_better(supplier.bic, bic)
        db.commit()
        db.refresh(supplier)
        return supplier

    # ─── Neuen Lieferanten anlegen ────────────────────────────────────────
    supplier = Supplier(
        name=name.strip(),
        address=address if _is_set(address) else None,
        street=street if _is_set(street) else None,
        zip_code=zip_code if _is_set(zip_code) else None,
        city=city if _is_set(city) else None,
        hrb_number=hrb_number if _is_set(hrb_number) else None,
        tax_number=tax_number if _is_set(tax_number) else None,
        vat_id=vat_id if _is_set(vat_id) else None,
        bank_name=bank_name if _is_set(bank_name) else None,
        iban=iban if _is_set(iban) else None,
        bic=bic if _is_set(bic) else None,
    )
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    logger.info("Neuer Lieferant angelegt: #%d '%s'", supplier.id, supplier.name)
    return supplier
