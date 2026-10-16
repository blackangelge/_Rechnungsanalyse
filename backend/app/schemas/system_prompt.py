"""
Pydantic-Schemas für Systemprompts.

Systemprompts steuern die KI-Extraktion:
  type=0 — Dokumententyp-Erkennungsprompt (detect_document_type)
  type=1 — Standard-Extraktionsprompt für Eingangsrechnungen (extract_invoice_data)
  type=2 — Workflow-Baustein (wird nie automatisch gewählt, beliebig viele)

Typ 0 und 1 gibt es höchstens einmal; das CRUD stuft andere Prompts desselben Typs zu Typ 2 herab.

SystemPromptCreate — Payload für POST /api/settings/system-prompts
SystemPromptUpdate — Payload für PUT /api/settings/system-prompts/{id}
SystemPromptRead   — Antwort-Schema (inkl. id und Zeitstempel)
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SystemPromptCreate(BaseModel):
    """
    Payload für das Anlegen eines neuen Systemprompts.

    type=0 → Dokumententyp-Erkennungsprompt (ersetzt bisherigen type=0-Prompt).
    type=1 → Standard-Extraktionsprompt für Eingangsrechnungen.
    """

    name: str
    content: str
    # 0=Dokumententyp-Erkennungsprompt, 1=Standard-Extraktionsprompt
    type: int = 0


class SystemPromptUpdate(BaseModel):
    """Payload für das vollständige Aktualisieren eines Systemprompts."""

    name: str
    content: str
    type: int = 0  # 0=Dokumententyp-Erkennung, 1=Standard-Extraktion


class SystemPromptRead(BaseModel):
    """Antwort-Schema für einen Systemprompt — enthält zusätzlich id und Zeitstempel."""

    id: int
    name: str
    content: str
    type: int        # 0=Dokumententyp-Erkennung, 1=Standard-Extraktion
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
