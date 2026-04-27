"""
Pydantic-Schemas für Bildkonvertierungseinstellungen.

ImageSettingsUpdate — Payload für PUT /api/settings/image-conversion
ImageSettingsRead   — Antwort-Schema (erbt Update + fügt id und Zeitstempel hinzu)

Pydantic-Validierung erzwingt gültige Wertebereiche (zusätzlich zu DB-CheckConstraints).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ImageSettingsUpdate(BaseModel):
    """
    Payload für das Aktualisieren der Bildkonvertierungseinstellungen.

    Validierungsregeln (entsprechen den DB-CheckConstraints):
      dpi:          72–600 (Standard: 150)
      image_format: "PNG" | "JPEG" (Standard: "PNG")
      jpeg_quality: 1–100 (Standard: 85, nur relevant bei image_format="JPEG")
      grayscale:    True/False (Standard: False)
    """

    dpi: int = Field(default=150, ge=72, le=600)
    image_format: str = Field(default="PNG", pattern="^(PNG|JPEG)$")
    jpeg_quality: int = Field(default=85, ge=1, le=100)
    grayscale: bool = False


class ImageSettingsRead(ImageSettingsUpdate):
    """Antwort-Schema für Bildkonvertierungseinstellungen — erbt Update + fügt id und Zeitstempel hinzu."""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
