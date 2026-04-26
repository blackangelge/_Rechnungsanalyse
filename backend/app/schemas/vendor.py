from pydantic import BaseModel, ConfigDict


class VendorBankAccountRead(BaseModel):
    id: int
    bank_name: str | None = None
    iban: str | None = None
    bic: str | None = None

    model_config = ConfigDict(from_attributes=True)


class VendorRead(BaseModel):
    id: int
    name: str
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    hrb_number: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
    bank_accounts: list[VendorBankAccountRead] = []

    model_config = ConfigDict(from_attributes=True)


class VendorUpdate(BaseModel):
    name: str
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    hrb_number: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
