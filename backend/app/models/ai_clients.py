from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIClients(Base):
    __tablename__ = "ai_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 0=Dokumententyp erkennen, 1=Eingangsrechnungsanalyse, 2=...
    primary_type: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=32000, server_default="32000", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.1, server_default="0.1", nullable=False)
    chat_response: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # Reasoning-Modus: "off" | "low" | "medium" | "high" | "on"
    reasoning: Mapped[str] = mapped_column(String(20), default="off", server_default="off", nullable=False)
    ip_address: Mapped[str] = mapped_column(String(20), default="", server_default="", nullable=False)
    endpoint_type: Mapped[str] = mapped_column(String(20), default="openai", server_default="openai", nullable=False)
    port: Mapped[str] = mapped_column(String(5), default="1234", server_default="1234", nullable=False)
    parallel_request: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
