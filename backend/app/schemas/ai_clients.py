"""
Pydantic-Schemas für KI-Konfigurationen (ai_clients).

AIClientsBase     — gemeinsame Felder für Create/Update/Read
AIClientsCreate   — Payload für POST /api/ai-clients
AIClientsUpdate   — Payload für PUT /api/ai-clients/{id}
AIClientsRead     — Antwort-Schema (inkl. id, created_at, updated_at)
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ReasoningLevel = Literal["off", "low", "medium", "high", "on"]
EndpointType = Literal["openai", "lmstudio"]


class AIClientsBase(BaseModel):
    """
    Gemeinsame Felder einer KI-Konfiguration.

    primary_type: 0=Dokumententyp-Erkennung, 1=Eingangsrechnungs-Extraktion.
    endpoint_type: Steuert URL-Bildung ('openai' vs. 'lmstudio').
    parallel_request: Maximale gleichzeitige KI-Anfragen für diese Konfiguration.
    timeout_at: Wenn gesetzt und in der Zukunft → KI gilt als temporär deaktiviert.
    """

    name: str                               # Anzeigename
    api_key: str | None = None              # API-Schlüssel (optional)
    model_name: str                         # Modellbezeichnung (z.B. "gpt-4o")
    # 0=Dokumententyp erkennen, 1=Eingangsrechnungsanalyse
    primary_type: int = 0
    max_tokens: int = 32000                 # Maximale Ausgabe-Token
    temperature: float = 0.1               # Kreativität (0.0=deterministisch)
    chat_response: bool = False             # Antwortformat-Modus
    active: bool = False                    # Aktiviert für Analysen
    reasoning: ReasoningLevel = "off"       # Reasoning-Stufe: off|low|medium|high|on
    # IP-Adresse oder Hostname der API (z.B. "192.168.1.100" oder "api.openai.com")
    ip_address: str = ""
    endpoint_type: EndpointType = "openai"  # API-Protokoll: "openai" | "lmstudio"
    port: str = "1234"                      # API-Port
    parallel_request: int = 1              # Parallele Anfragen an diese KI
    timeout_at: datetime | None = None     # Temporäre Sperre bis zu diesem Zeitpunkt


class AIClientsCreate(AIClientsBase):
    """Payload für das Anlegen einer neuen KI-Konfiguration (POST /api/ai-clients)."""
    pass


class AIClientsUpdate(AIClientsBase):
    """Payload für das vollständige Aktualisieren einer KI-Konfiguration (PUT /api/ai-clients/{id})."""
    pass


class AIClientsRead(AIClientsBase):
    """Antwort-Schema für KI-Konfigurationen — enthält zusätzlich id und Zeitstempel."""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
