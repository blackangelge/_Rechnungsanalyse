from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ImageSettings(Base):
    """Globale Bildkonvertierungseinstellungen (Singleton, immer id=1)."""

    __tablename__ = "image_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_image_settings_singleton"),
        CheckConstraint("dpi BETWEEN 72 AND 600", name="ck_image_settings_dpi"),
        CheckConstraint("image_format IN ('PNG', 'JPEG')", name="ck_image_settings_format"),
        CheckConstraint("jpeg_quality BETWEEN 1 AND 100", name="ck_image_settings_jpeg_quality"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    dpi: Mapped[int] = mapped_column(Integer, default=150, server_default="150", nullable=False)
    image_format: Mapped[str] = mapped_column(String(10), default="PNG", server_default="PNG", nullable=False)
    jpeg_quality: Mapped[int] = mapped_column(Integer, default=85, server_default="85", nullable=False)
    grayscale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
