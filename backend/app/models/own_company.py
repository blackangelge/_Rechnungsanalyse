"""Eigene Gesellschaften (mögliche Rechnungsempfänger). Alle Angaben sind optional — mindestens eine Kennung muss vorhanden sein."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OwnCompany(Base):
    __tablename__ = "own_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)          # weitere Schreibweisen / frühere Namen
    vat_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)          # USt-IdNrn. (mehrere möglich)
    tax_numbers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    ibans: Mapped[list | None] = mapped_column(JSONB, nullable=True)            # mehrere IBANs möglich
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
