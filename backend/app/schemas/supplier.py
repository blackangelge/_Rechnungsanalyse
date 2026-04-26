"""
Pydantic-Schemas für Lieferanten.
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SupplierBase(BaseModel):
    name: str
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    address: str | None = None
    hrb_number: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
    bank_name: str | None = None
    iban: str | None = None
    bic: str | None = None


class SupplierUpdate(SupplierBase):
    pass


class SupplierRead(SupplierBase):
    id: int
    document_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
