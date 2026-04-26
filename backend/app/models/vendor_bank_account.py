from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class VendorBankAccount(Base):
    __tablename__ = "vendor_bank_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vendor.id", ondelete="CASCADE"), nullable=False
    )
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(20), nullable=True)

    vendor: Mapped["Vendor"] = relationship("Vendor", back_populates="bank_accounts")  # noqa: F821
