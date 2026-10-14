"""
Pydantic-Schemas für PDF-Versionen und PDF-Bearbeitung.
"""

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentVersionRead(BaseModel):
    """Eine PDF-Version eines Dokuments (id=None → noch nicht persistiertes Original)."""

    id: int | None = None
    version_number: int
    page_count: int
    file_size_bytes: int
    source: str                      # original | manual | auto | restore
    description: str | None = None
    parent_version: int | None = None
    removed_pages: list[int] | None = None
    created_at: datetime | None = None
    is_current: bool = False

    model_config = ConfigDict(from_attributes=True)

    @field_validator("removed_pages", mode="before")
    @classmethod
    def _parse_removed(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return None
        return v


class PageEditRequest(BaseModel):
    """Seiten entfernen ODER nur bestimmte Seiten behalten (1-basiert)."""

    remove_pages: list[int] | None = Field(default=None, description="Zu entfernende Seiten")
    keep_pages: list[int] | None = Field(default=None, description="Einzig zu behaltende Seiten")
    note: str | None = Field(default=None, max_length=500, description="Optionale Beschreibung")


class AutoTrimRequest(BaseModel):
    """Automatisches Kürzen für mehrere Dokumente: nur die ersten N Seiten behalten."""

    document_ids: list[int]
    max_pages: int = Field(default=1, ge=1, le=500)


class AutoTrimResult(BaseModel):
    trimmed: list[int] = []
    unchanged: list[int] = []
    errors: dict[int, str] = {}


class PdfSettingsUpdate(BaseModel):
    auto_trim_enabled: bool = False
    auto_trim_max_pages: int = Field(default=3, ge=1, le=500)


class PdfSettingsRead(PdfSettingsUpdate):
    id: int

    model_config = ConfigDict(from_attributes=True)
