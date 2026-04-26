from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderPosition(Base):
    __tablename__ = "order_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    position_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit_price_netto: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    unit_price_brutto: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    discount: Mapped[str | None] = mapped_column(String(100), nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="order_positions")  # noqa: F821
