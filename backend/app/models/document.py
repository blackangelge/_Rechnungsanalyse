from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # 0=unbekannt, 1=Eingangsrechnung, 2=Ausgangsrechnung, ...
    document_type: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | processing | done | error
    status: Mapped[str] = mapped_column(String(50), default="pending", server_default="pending", nullable=False)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True, server_default="{}")
    soft_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="documents")  # noqa: F821
    extraction: Mapped["InvoiceExtraction | None"] = relationship(  # noqa: F821
        "InvoiceExtraction", back_populates="document", uselist=False
    )
    order_positions: Mapped[list["OrderPosition"]] = relationship(  # noqa: F821
        "OrderPosition", back_populates="document", cascade="all, delete-orphan"
    )
    token_counts: Mapped[list["DocumentTokenCount"]] = relationship(  # noqa: F821
        "DocumentTokenCount", back_populates="document", cascade="all, delete-orphan"
    )

    # Convenience properties — delegieren an batch/extraction für Schema-Kompatibilität
    @property
    def company(self) -> str | None:
        return self.batch.company_name if self.batch else None

    @property
    def year(self) -> int | None:
        return self.batch.year if self.batch else None

    @property
    def total_amount(self):
        return self.extraction.total_amount_brutto if self.extraction else None

    @property
    def invoice_number(self) -> str | None:
        return self.extraction.invoice_number if self.extraction else None

    @property
    def supplier_name(self) -> str | None:
        return self.extraction.vendor_id if self.extraction else None

    @property
    def has_extraction(self) -> bool:
        return self.extraction is not None

    # ── Token-Statistiken aus dem neuesten DocumentTokenCount-Eintrag ────────
    @property
    def ki_input_tokens(self) -> int | None:
        if self.token_counts:
            v = self.token_counts[-1].input_token_count
            return int(v) if v else None
        return None

    @property
    def ki_output_tokens(self) -> int | None:
        if self.token_counts:
            v = self.token_counts[-1].output_token_count
            return int(v) if v else None
        return None

    @property
    def ki_reasoning_tokens(self) -> int | None:
        if self.token_counts:
            v = self.token_counts[-1].reasoning_count
            return int(v) if v else None
        return None

    @property
    def ki_total_duration(self) -> float | None:
        if self.token_counts:
            v = self.token_counts[-1].time_spent_seconds
            return float(v) if v else None
        return None
