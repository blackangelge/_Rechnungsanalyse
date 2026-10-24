"""
Mengeneinheiten: Gruppen (Dimension), Einheiten mit Faktor zur Basiseinheit, Aliasse und die Prüfliste für unbekannte Einheiten.

  unit_groups       — z. B. „Masse“. Jede Gruppe hat genau eine Basiseinheit (Faktor 1).
  units             — Faktor = Anzahl dieser Einheit pro 1 Basiseinheit (Basis Tonne: kg = 1000, g = 1 000 000).
                      Normierter Wert = Originalwert ÷ Faktor.
  unit_aliases      — Schreibweisen („kg“, „KG“, „Kilo“); global eindeutig. `units.case_insensitive` = Groß-/Kleinschreibung egal.
  unit_review_items — Vorkommen unbekannter Einheiten (je Datensatz und Feld); werden einer Einheit zugeordnet oder ignoriert.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UnitGroup(Base):
    __tablename__ = "unit_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    units: Mapped[list["Unit"]] = relationship("Unit", back_populates="group", cascade="all, delete-orphan", order_by="Unit.id")


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("group_id", "name", name="uq_unit_group_name"),
        # genau eine Basiseinheit je Gruppe
        Index("uq_unit_one_base_per_group", "group_id", unique=True, postgresql_where=text("is_base"), sqlite_where=text("is_base")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("unit_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)                    # Anzeigename, z. B. „Kilogramm“
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)                  # Kurzzeichen, z. B. „kg“
    factor: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False, default=Decimal(1))
    is_base: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    case_insensitive: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    group: Mapped["UnitGroup"] = relationship("UnitGroup", back_populates="units")
    aliases: Mapped[list["UnitAlias"]] = relationship("UnitAlias", back_populates="unit", cascade="all, delete-orphan", order_by="UnitAlias.id")


class UnitAlias(Base):
    __tablename__ = "unit_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(Integer, ForeignKey("units.id", ondelete="CASCADE"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    unit: Mapped["Unit"] = relationship("Unit", back_populates="aliases")


class UnitReviewItem(Base):
    __tablename__ = "unit_review_items"
    __table_args__ = (UniqueConstraint("record_id", "field_name", name="uq_unit_review_record_field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)   # normalisierte Schreibweise (gruppiert Vorkommen)
    original_unit: Mapped[str] = mapped_column(String(100), nullable=False)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("extracted_records.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open", nullable=False, index=True)   # open | assigned | ignored
    unit_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("units.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
