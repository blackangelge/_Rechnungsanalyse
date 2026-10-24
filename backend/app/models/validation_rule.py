"""Prüfregeln für extrahierte Datensätze (siehe services/rule_engine.py). Gelten schemaübergreifend: Bezug sind Entitäts- und Feldnamen."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ValidationRule(Base):
    __tablename__ = "validation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_name: Mapped[str] = mapped_column(String(60), nullable=False)               # Entität, an deren Datensätzen geprüft wird
    left_expr: Mapped[str] = mapped_column(Text, nullable=False)
    right_expr: Mapped[str] = mapped_column(Text, nullable=False)
    comparator: Mapped[str] = mapped_column(String(2), default="=", server_default="=", nullable=False)   # = | <= | >=
    tolerance_abs: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    tolerance_pct: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    severity: Mapped[str] = mapped_column(String(10), default="error", server_default="error", nullable=False)   # warn | error
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
