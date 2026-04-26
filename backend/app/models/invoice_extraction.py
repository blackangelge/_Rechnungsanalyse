from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InvoiceExtraction(Base):
    __tablename__ = "invoice_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Lieferantenname als Freitext (kein FK — Deduplication über vendor-Tabelle separat)
    vendor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_amount_netto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_amount_brutto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_tax_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    total_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    cash_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship("Document", back_populates="extraction")  # noqa: F821
