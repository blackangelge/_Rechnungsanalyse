"""
Pydantic-Schemas für die Artikelsuche.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class PositionSearchRead(BaseModel):
    """Eine Position inkl. der wichtigsten Angaben ihres Belegs."""

    id: int
    document_id: int
    position_index: int
    product_name: str | None = None
    product_description: str | None = None
    article_number: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    # Umrechnung in die Basiseinheit (kg, l, m, m²); leer bei Stück/Verpackungen/unbekannten Einheiten
    base_quantity: Decimal | None = None
    base_unit: str | None = None
    base_unit_price: Decimal | None = None
    total_price: Decimal | None = None
    discount: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    supplier_name: str | None = None
    validation_status: str | None = None
    company: str
    year: int
    batch_id: int
    original_filename: str


class UnitSummary(BaseModel):
    unit: str | None = None
    count: int
    quantity: float | None = None
    total_price: float | None = None


class PositionSummary(BaseModel):
    count: int
    documents: int
    total_price_sum: float | None = None
    unit_price_min: float | None = None
    unit_price_avg: float | None = None
    unit_price_max: float | None = None
    by_unit: list[UnitSummary] = []
