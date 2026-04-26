"""Pydantic-Schema für Dokumententypen."""

from pydantic import BaseModel, ConfigDict


class DocumentTypeRead(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)
