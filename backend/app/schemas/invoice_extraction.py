from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class OrderPositionRead(BaseModel):
    id: int
    document_id: int
    position_index: int
    product_name: str | None
    product_description: str | None
    article_number: str | None
    unit_price_netto: Decimal | None
    unit_price_brutto: Decimal | None
    tax: Decimal | None
    quantity: Decimal | None
    unit: str | None
    discount: str | None

    model_config = ConfigDict(from_attributes=True)


class InvoiceExtractionRead(BaseModel):
    id: int
    document_id: int
    # Lieferant (Freitext-Name, kein FK)
    vendor_id: str | None
    # Rechnungsidentifikation
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    # Beträge
    total_amount_netto: Decimal | None
    total_amount_brutto: Decimal | None
    total_tax_value: Decimal | None
    total_tax: Decimal | None
    discount_amount: Decimal | None
    cash_discount_amount: Decimal | None
    # Zahlungsbedingungen
    payment_terms: str | None
    # KI-Rohdaten
    raw_response: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
