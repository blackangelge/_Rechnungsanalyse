"""
Ergebnis der Seitenklassifikation (Workflow-Knoten `classify_pages`): eine Zeile je Seite der klassifizierten PDF-Version.
Beim erneuten Lauf werden die Zeilen des Belegs ersetzt.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

PAGE_KINDS = (
    "rechnung", "rechnung_folgeseite", "lieferschein", "bestellung", "angebot", "mahnung",
    "kontoauszug", "vertrag", "sonstiges", "leer",
)


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "pdf_version", "page_no", name="uq_document_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    pdf_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")    # Version, auf die sich page_no bezieht
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)                                      # 1-basiert
    kind: Mapped[str] = mapped_column(String(30), nullable=False)                                      # siehe PAGE_KINDS
    starts_document: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
