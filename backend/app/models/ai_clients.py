"""
ORM-Modell für KI-Konfigurationen (Tabelle: ai_clients).

Jeder Eintrag beschreibt eine OpenAI-kompatible KI-Instanz (z.B. LM Studio, Ollama,
OpenAI) mit Verbindungsparametern, Modellname und Einsatzzweck (primary_type).

Mehrere aktive Konfigurationen sind möglich — der Worker wählt zufällig aus
den verfügbaren aus (Load-Balancing). Temporäre Deaktivierung via timeout_at.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIClients(Base):
    """
    KI-Konfiguration für eine OpenAI-kompatible Schnittstelle.

    primary_type bestimmt den Einsatzzweck:
      0 = Dokumententyp-Erkennung (detect_document_type)
      1 = Eingangsrechnungs-Extraktion (extract_invoice_data)

    endpoint_type steuert die URL-Bildung:
      'openai'   → {ip_address}:{port}/v1/chat/completions
      'lmstudio' → {ip_address}:{port}/api/v1/chat

    timeout_at ermöglicht temporäre Deaktivierung (z.B. bei 429-Fehlern),
    ohne active=False zu setzen — bei Ablauf wird die KI automatisch wieder verwendet.
    """

    __tablename__ = "ai_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)           # Anzeigename der KI-Konfiguration
    api_key: Mapped[str | None] = mapped_column(String(200), nullable=True)  # API-Schlüssel (optional, z.B. für OpenAI)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)     # Modellbezeichnung (z.B. "gpt-4o", "llava")
    # 0=Dokumententyp erkennen, 1=Eingangsrechnungsanalyse
    primary_type: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=32000, server_default="32000", nullable=False)  # Maximale Ausgabe-Token
    temperature: Mapped[float] = mapped_column(Float, default=0.1, server_default="0.1", nullable=False)     # Kreativität (0.0=deterministisch)
    chat_response: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)  # Antwortformat (chat vs. completion)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)         # Aktiviert für Analysen
    # Reasoning-Modus: "off" | "low" | "medium" | "high" | "on"
    reasoning: Mapped[str] = mapped_column(String(20), default="off", server_default="off", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(20), default="", server_default="", nullable=False)           # IP oder Hostname der KI-API
    endpoint_type: Mapped[str] = mapped_column(String(20), default="openai", server_default="openai", nullable=False)  # "openai" | "lmstudio"
    port: Mapped[str] = mapped_column(String(5), default="1234", server_default="1234", nullable=False)          # Port der KI-API
    parallel_request: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)        # Maximale parallele Anfragen an diese KI
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Bis wann temporär deaktiviert (NULL = aktiv)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
