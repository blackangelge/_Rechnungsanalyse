"""
Dynamisches Extraktionsschema: Definitionen (Meta-Tabellen) und extrahierte Datensätze.

Es gibt kein Laufzeit-DDL. Die Struktur steht in festen Tabellen, die extrahierten Daten in `extracted_record.data` (JSONB).

  schema_version   — eine unveränderliche Fassung der gesamten Definition (Entitäten, Felder, Beziehungen).
                     Jede Änderung erzeugt eine neue Version (Kopie der alten); Datensätze verweisen auf die Version, mit der sie
                     erzeugt wurden — Altdaten bleiben lesbar.
  schema_entity    — „Tabelle“ (Beleg, Position, Lieferant …). Stammdaten-Entitäten (`master_table`) liegen nicht in
                     `extracted_record`, sondern in einer festen Tabelle (z. B. `vendor`) und werden per Matching zugeordnet.
  schema_field     — Feld einer Entität. Quelle `llm` (geht ins JSON-Schema) oder `system` (wird vom Programm gesetzt).
                     `document_id` und `vendor_id` sind gesperrte Standardfelder jeder Nicht-Stammdaten-Entität.
  schema_relation  — Beziehung zwischen Entitäten (Eltern-Kind oder Referenz).
  extracted_record — ein extrahierter Datensatz einer Entität zu einem Beleg.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

DATA_TYPES = ("text", "integer", "decimal", "date", "boolean", "enum", "quantity")
FIELD_SOURCES = ("llm", "system")
RELATION_KINDS = ("parent_child", "reference")
VERSION_STATUS = ("draft", "active", "archived")

# Standardfelder jeder Nicht-Stammdaten-Entität: immer vorhanden, nicht bearbeitbar, nie im LLM-Schema.
STANDARD_FIELDS = {
    "document_id": {"display_name": "Beleg-ID", "data_type": "integer", "description": "Beleg, aus dem der Datensatz stammt"},
    "vendor_id": {"display_name": "Lieferant-ID", "data_type": "integer", "description": "Zugeordneter Lieferant (Stammdaten)"},
}


class SchemaVersion(Base):
    __tablename__ = "schema_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft", nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entities: Mapped[list["SchemaEntity"]] = relationship(
        "SchemaEntity", back_populates="version", cascade="all, delete-orphan", order_by="SchemaEntity.position"
    )
    relations: Mapped[list["SchemaRelation"]] = relationship(
        "SchemaRelation", back_populates="version", cascade="all, delete-orphan"
    )


class SchemaEntity(Base):
    __tablename__ = "schema_entities"
    __table_args__ = (UniqueConstraint("version_id", "name", name="uq_schema_entity_version_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)                    # technischer Name (snake_case)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # Stammdaten: feste Tabelle statt extracted_record (z. B. "vendor"); match_fields = Feldnamen in Prioritätsreihenfolge
    master_table: Mapped[str | None] = mapped_column(String(60), nullable=True)
    match_fields: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    version: Mapped["SchemaVersion"] = relationship("SchemaVersion", back_populates="entities")
    fields: Mapped[list["SchemaField"]] = relationship(
        "SchemaField", back_populates="entity", cascade="all, delete-orphan", order_by="SchemaField.position"
    )

    @property
    def is_master(self) -> bool:
        return bool(self.master_table)


class SchemaField(Base):
    __tablename__ = "schema_fields"
    __table_args__ = (UniqueConstraint("entity_id", "name", name="uq_schema_field_entity_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)               # siehe DATA_TYPES
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    output_format: Mapped[str | None] = mapped_column(String(60), nullable=True)     # Excel-Format, z. B. "#,##0.00" oder "DD.MM.YYYY"
    description: Mapped[str | None] = mapped_column(Text, nullable=True)            # fließt ins LLM-Prompt
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    source: Mapped[str] = mapped_column(String(10), default="llm", server_default="llm", nullable=False)   # llm | system
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)    # Standardfeld: nicht änderbar
    enum_values: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    unit_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)        # Mengenfelder: Einheiten-Gruppe (Phase 3)
    system_function: Mapped[str | None] = mapped_column(String(60), nullable=True)   # Feldquelle system: Name aus der Registry (Phase 2)
    system_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalizers: Mapped[list | None] = mapped_column(JSONB, nullable=True)           # geordnete Kette [{"name": ..., "params": {...}}]

    entity: Mapped["SchemaEntity"] = relationship("SchemaEntity", back_populates="fields")


class SchemaRelation(Base):
    __tablename__ = "schema_relations"
    __table_args__ = (UniqueConstraint("version_id", "name", name="uq_schema_relation_version_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)                    # z. B. "positionen"
    kind: Mapped[str] = mapped_column(String(20), nullable=False)                    # parent_child | reference
    source_entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False)
    target_entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped["SchemaVersion"] = relationship("SchemaVersion", back_populates="relations")


class ExtractedRecord(Base):
    __tablename__ = "extracted_records"
    __table_args__ = (
        Index("ix_extracted_records_doc_entity", "document_id", "entity_id"),
        Index("ix_extracted_records_data", "data", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_entities.id", ondelete="RESTRICT"), nullable=False, index=True)
    schema_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("schema_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    parent_record_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("extracted_records.id", ondelete="CASCADE"), nullable=True, index=True)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendor.id", ondelete="SET NULL"), nullable=True, index=True)
    position_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)   # Reihenfolge unter dem Elternsatz
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    parent: Mapped["ExtractedRecord | None"] = relationship("ExtractedRecord", back_populates="children", remote_side=[id])
    children: Mapped[list["ExtractedRecord"]] = relationship(
        "ExtractedRecord", back_populates="parent", cascade="all, delete-orphan", order_by="ExtractedRecord.position_index"
    )
