"""Mengeneinheiten: Gruppen, Einheiten, Aliasse, Prüfliste

Revision ID: 0019_units
Revises: 0018_dynamic_schema
Create Date: 2026-10-25 14:00:00.000000

Nur neue Tabellen.
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_units"
down_revision = "0018_dynamic_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unit_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_table(
        "units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("unit_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("factor", sa.Numeric(30, 12), nullable=False),
        sa.Column("is_base", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("case_insensitive", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.UniqueConstraint("group_id", "name", name="uq_unit_group_name"),
    )
    op.create_index("ix_units_group_id", "units", ["group_id"])
    op.create_index("uq_unit_one_base_per_group", "units", ["group_id"], unique=True, postgresql_where=sa.text("is_base"))
    op.create_table(
        "unit_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(50), nullable=False, unique=True),
    )
    op.create_index("ix_unit_aliases_unit_id", "unit_aliases", ["unit_id"])
    op.create_table(
        "unit_review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alias_key", sa.String(60), nullable=False),
        sa.Column("original_unit", sa.String(100), nullable=False),
        sa.Column("record_id", sa.Integer(), sa.ForeignKey("extracted_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(60), nullable=False),
        sa.Column("status", sa.String(12), server_default="open", nullable=False),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("record_id", "field_name", name="uq_unit_review_record_field"),
    )
    op.create_index("ix_unit_review_items_alias_key", "unit_review_items", ["alias_key"])
    op.create_index("ix_unit_review_items_record_id", "unit_review_items", ["record_id"])
    op.create_index("ix_unit_review_items_document_id", "unit_review_items", ["document_id"])
    op.create_index("ix_unit_review_items_status", "unit_review_items", ["status"])


def downgrade() -> None:
    op.drop_table("unit_review_items")
    op.drop_table("unit_aliases")
    op.drop_table("units")
    op.drop_table("unit_groups")
