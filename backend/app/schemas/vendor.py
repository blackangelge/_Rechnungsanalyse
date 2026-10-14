"""
Pydantic-Schemas für Lieferanten-Stammdaten.

VendorBankAccountRead — Bankverbindung eines Lieferanten (eingebettet in VendorRead)
VendorRead            — Vollständige Lieferantendaten inkl. Bankkonten
VendorUpdate          — Payload für PUT /api/vendors/{id} (ohne Bankkonten)

Bankkonten können derzeit nur über die KI-Extraktion (find_or_create) hinzugefügt werden,
nicht manuell über die API.
"""

from pydantic import BaseModel, ConfigDict, Field


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
    workflow_id: int | None = None     # Eigener Analyse-Workflow (None = Standard)
    bank_accounts: list[VendorBankAccountRead] = []  # Alle Bankverbindungen
    document_count: int = 0            # Belege mit diesem Lieferantennamen (nicht gelöschte)

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


class DuplicateGroupRead(BaseModel):
    """Gruppe von Dublettenkandidaten mit den gefundenen Übereinstimmungen."""

    suppliers: list[VendorRead]
    reasons: list[str]            # iban | vat_id | tax_number | name | name_similar
    reason_labels: list[str]
    confidence: str               # high | medium | low


class VendorMergeRequest(BaseModel):
    target_id: int
    source_ids: list[int] = Field(min_length=1)
    # Ausdrücklich gewählte Endwerte einzelner Stammdatenfelder (sonst: Ziel behalten, Leeres auffüllen)
    fields: dict[str, str | None] | None = None
    # Belege der zusammengeführten Lieferanten auf den Namen des Ziels umschreiben (nur dann gehören sie danach zum Ziel)
    update_document_names: bool = True


class VendorMergeResult(BaseModel):
    supplier: VendorRead
    merged_ids: list[int]
    moved_documents: int
    moved_bank_accounts: int
