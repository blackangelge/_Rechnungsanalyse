"""
ORM-Modell für Bankverbindungen eines Lieferanten (Tabelle: vendor_bank_accounts).

Ein Lieferant (Vendor) kann mehrere Bankverbindungen haben (1:N-Beziehung).
Die IBAN ist der stärkste Deduplication-Identifier: find_or_create() sucht
zuerst nach IBAN, bevor Name oder VAT-ID geprüft werden.

Beim Löschen des übergeordneten Vendor-Eintrags werden alle zugehörigen
Bankkonten über CASCADE automatisch mitgelöscht.
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class VendorBankAccount(Base):
    """
    Eine einzelne Bankverbindung (IBAN/BIC/Bankname) eines Lieferanten.

    Neue Bankverbindungen werden in find_or_create() nur hinzugefügt,
    wenn die IBAN noch nicht im System existiert — es werden keine
    Duplikate angelegt, selbst wenn Bank- oder BIC-Daten abweichen.
    """

    __tablename__ = "vendor_bank_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vendor.id", ondelete="CASCADE"), nullable=False
    )
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Name der Bank (Freitext)
    iban: Mapped[str | None] = mapped_column(String(50), nullable=True)         # IBAN (stärkster Dedup-Identifier)
    bic: Mapped[str | None] = mapped_column(String(20), nullable=True)          # BIC/SWIFT-Code

    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="bank_accounts")  # noqa: F821
