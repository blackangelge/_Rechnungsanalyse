"""
Pydantic-Schema für Dokumententypen.

DocumentTypeRead wird von GET /api/document-types zurückgegeben
und im Frontend für den DocType-Filter und die Tabellenspalte verwendet.

Die 15 Dokumententypen sind in der DB vordefiniert (Migration 0011).
Typ ID=1 (Eingangsrechnung) löst die vollständige KI-Extraktion aus.
"""

from pydantic import BaseModel, ConfigDict


class DocumentTypeRead(BaseModel):
    """Leseschema für einen Dokumententyp (id + name)."""

    id: int    # 1=Eingangsrechnung, 2=Ausgangsrechnung, ... 15=Unbekannt
    name: str  # Anzeigename (z.B. "Eingangsrechnung")

    model_config = ConfigDict(from_attributes=True)
