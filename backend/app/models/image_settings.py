"""
ORM-Modell für globale Bildkonvertierungseinstellungen (Tabelle: image_settings).

Singleton-Tabelle: Es existiert immer genau ein Datensatz mit id=1.
Diese Einstellungen steuern, wie PDFs vor der KI-Analyse in Bilder umgewandelt
werden (pdf_service.py: render_pdf_pages). Höhere DPI = bessere OCR-Qualität,
aber größere Payloads an die KI-API.

CheckConstraints in der DB verhindern ungültige Werte.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ImageSettings(Base):
    """
    Globale Bildkonvertierungseinstellungen (Singleton, immer id=1).

    Einstellungen:
      dpi (72–600):          Auflösung beim Rendern der PDF-Seiten zu Bildern.
                             Standard 150 DPI — guter Kompromiss aus Qualität und Payload-Größe.
      image_format (PNG|JPEG): PNG = verlustfrei, JPEG = kleiner (für LM Studio empfohlen).
      jpeg_quality (1–100): Kompressionsqualität bei JPEG (Standard 85).
      grayscale:             Graustufen reduzieren Payload-Größe, können aber
                             Farb-Informationen (z.B. Stempel) verlieren.
    """

    __tablename__ = "image_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_image_settings_singleton"),
        CheckConstraint("dpi BETWEEN 72 AND 600", name="ck_image_settings_dpi"),
        CheckConstraint("image_format IN ('PNG', 'JPEG')", name="ck_image_settings_format"),
        CheckConstraint("jpeg_quality BETWEEN 1 AND 100", name="ck_image_settings_jpeg_quality"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)  # Immer 1 (Singleton)
    dpi: Mapped[int] = mapped_column(Integer, default=150, server_default="150", nullable=False)                 # Render-Auflösung (DPI)
    image_format: Mapped[str] = mapped_column(String(10), default="PNG", server_default="PNG", nullable=False)   # Bildformat: "PNG" | "JPEG"
    jpeg_quality: Mapped[int] = mapped_column(Integer, default=85, server_default="85", nullable=False)          # JPEG-Kompressionsqualität
    grayscale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)       # Graustufen-Modus
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
