"""
Pydantic-Schemas für Lieferanten-Stammdaten.

VendorBankAccountRead — Bankverbindung eines Lieferanten (eingebettet in VendorRead)
VendorRead            — Vollständige Lieferantendaten inkl. Bankkonten
VendorUpdate          — Payload für PUT /api/vendors/{id} (ohne Bankkonten)

Bankkonten können derzeit nur über die KI-Extraktion (find_or_create) hinzugefügt werden,
nicht manuell über die API.
"""

from pydantic import BaseModel, ConfigDict


class VendorBankAccountRead(BaseModel):
    """Eine einzelne Bankverbindung eines Lieferanten (IBAN/BIC/Bankname)."""

    id: int
    bank_name: str | None = None  # Name der Bank
    iban: str | None = None        # IBAN (stärkster Dedup-Identifier)
    bic: str | None = None         # BIC/SWIFT-Code

    model_config = ConfigDict(from_attributes=True)


class VendorRead(BaseModel):
    """Vollständige Lieferantendaten inkl. aller Bankverbindungen."""

    id: int
    name: str                          # Firmenname (Pflichtfeld)
    street: str | None = None          # Straße und Hausnummer
    postal_code: str | None = None     # Postleitzahl
    city: str | None = None            # Stadt
    country: str | None = None         # Land
    hrb_number: str | None = None      # Handelsregisternummer
    tax_number: str | None = None      # Steuernummer
    vat_id: str | None = None          # USt-IdNr.
    bank_accounts: list[VendorBankAccountRead] = []  # Alle Bankverbindungen

    model_config = ConfigDict(from_attributes=True)


class VendorUpdate(BaseModel):
    """Payload für das manuelle Aktualisieren eines Lieferanten (ohne Bankkonten)."""

    name: str
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    hrb_number: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
