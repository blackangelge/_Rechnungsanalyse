from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DocumentTokenCount(Base):
    __tablename__ = "documents_token_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    output_token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    input_token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    reasoning_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    time_spent_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship("Document", back_populates="token_counts")  # noqa: F821
