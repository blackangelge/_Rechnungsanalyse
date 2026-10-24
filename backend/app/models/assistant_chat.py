"""KI-Assistent: gespeicherte Chats (Verlauf rechts in der Oberfläche) und ihre Nachrichten."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssistantChat(Base):
    __tablename__ = "assistant_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Neuer Chat")
    ai_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    messages: Mapped[list["AssistantMessage"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", order_by="AssistantMessage.id")


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("assistant_chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(12), nullable=False)          # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    images: Mapped[list | None] = mapped_column(JSON, nullable=True)        # Data-URLs der angehängten Bilder (nur user)
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)         # Abfragen/Werkzeugaufrufe (nur assistant)
    proposals: Mapped[list | None] = mapped_column(JSON, nullable=True)     # Änderungsvorschläge mit Status (nur assistant)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # Token/Dauer dieser Antwort
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    chat: Mapped[AssistantChat] = relationship(back_populates="messages")
