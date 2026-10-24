"""Dynamisches Extraktionsschema: Meta-Tabellen und extracted_records

Revision ID: 0018_dynamic_schema
Revises: 0017_ai_questions
Create Date: 2026-10-25 10:00:00.000000

Nur neue Tabellen — bestehende Daten werden nicht angefasst.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0018_dynamic_schema"
down_revision = "0017_ai_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schema_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_number", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_schema_versions_status", "schema_versions", ["status"])

    op.create_table(
        "schema_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("schema_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("master_table", sa.String(60), nullable=True),
        sa.Column("match_fields", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("version_id", "name", name="uq_schema_entity_version_name"),
    )
    op.create_index("ix_schema_entities_version_id", "schema_entities", ["version_id"])

    op.create_table(
        "schema_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("output_format", sa.String(60), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source", sa.String(10), server_default="llm", nullable=False),
        sa.Column("is_locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enum_values", postgresql.JSONB(), nullable=True),
        sa.Column("unit_group_id", sa.Integer(), nullable=True),
        sa.Column("system_function", sa.String(60), nullable=True),
        sa.Column("system_params", postgresql.JSONB(), nullable=True),
        sa.Column("normalizers", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("entity_id", "name", name="uq_schema_field_entity_name"),
    )
    op.create_index("ix_schema_fields_entity_id", "schema_fields", ["entity_id"])

    op.create_table(
        "schema_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("schema_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_entity_id", sa.Integer(), sa.ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_entity_id", sa.Integer(), sa.ForeignKey("schema_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.UniqueConstraint("version_id", "name", name="uq_schema_relation_version_name"),
    )
    op.create_index("ix_schema_relations_version_id", "schema_relations", ["version_id"])

    op.create_table(
        "extracted_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_id", sa.Integer(), sa.ForeignKey("schema_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schema_version_id", sa.Integer(), sa.ForeignKey("schema_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("parent_record_id", sa.Integer(), sa.ForeignKey("extracted_records.id", ondelete="CASCADE"), nullable=True),
        sa.Column("vendor_id", sa.Integer(), sa.ForeignKey("vendor.id", ondelete="SET NULL"), nullable=True),
        sa.Column("position_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_extracted_records_doc_entity", "extracted_records", ["document_id", "entity_id"])
    op.create_index("ix_extracted_records_entity_id", "extracted_records", ["entity_id"])
    op.create_index("ix_extracted_records_schema_version_id", "extracted_records", ["schema_version_id"])
    op.create_index("ix_extracted_records_parent_record_id", "extracted_records", ["parent_record_id"])
    op.create_index("ix_extracted_records_vendor_id", "extracted_records", ["vendor_id"])
    op.create_index("ix_extracted_records_data", "extracted_records", ["data"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("extracted_records")
    op.drop_table("schema_relations")
    op.drop_table("schema_fields")
    op.drop_table("schema_entities")
    op.drop_table("schema_versions")
