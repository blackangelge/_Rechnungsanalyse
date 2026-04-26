from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    import_folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    storage_folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | running | done | error
    status: Mapped[str] = mapped_column(String(50), default="pending", server_default="pending", nullable=False)
    folder_sync: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default="false", nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(  # noqa: F821
        "Document", back_populates="batch", cascade="all, delete-orphan"
    )
