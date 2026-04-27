"""
ORM-Modell für Lieferanten-Stammdaten (Tabelle: vendor).

Lieferanten werden über crud/vendor.py → find_or_create() dedupliziert.
Dabei wird zuerst nach IBAN gesucht, dann nach VAT-ID, dann nach Name.
Fehlende Felder werden beim ersten Fund ergänzt.

Bankverbindungen sind als separate VendorBankAccount-Einträge (1:N) gespeichert,
da ein Lieferant mehrere Bankkonten haben kann.
"""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Vendor(Base):
    """
    Lieferanten-Stammdatensatz mit Adresse und optionalen Steuer-/Handelsregisterdaten.

    Wird bei jeder KI-Extraktion über find_or_create() gesucht oder neu angelegt.
    Bestehende Felder werden nur ergänzt (nie überschrieben), wenn die neue
    Extraktion einen nicht-leeren Wert liefert.

    Bankverbindungen werden in der separaten Tabelle vendor_bank_accounts gespeichert
    (cascade delete: beim Löschen des Vendors werden alle Bankkonten mitgelöscht).
    """

    __tablename__ = "vendor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Interne Dokumentreferenz (optional)
    name: Mapped[str] = mapped_column(String(255), nullable=False)               # Firmenname (Pflichtfeld, Dedup-Fallback)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)       # Straße und Hausnummer
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)   # Postleitzahl
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)         # Stadt
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)      # Land
    hrb_number: Mapped[str | None] = mapped_column(String(100), nullable=True)   # Handelsregisternummer (HRB)
    tax_number: Mapped[str | None] = mapped_column(String(100), nullable=True)   # Steuernummer
    vat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)       # USt-IdNr. (Dedup-Priorität 2)

    bank_accounts: Mapped[list["VendorBankAccount"]] = relationship(  # noqa: F821
        "VendorBankAccount", back_populates="vendor", cascade="all, delete-orphan"
    )
