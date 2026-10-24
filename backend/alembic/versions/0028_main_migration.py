"""Übernahme aus dem MAIN-Projekt (Konfiguration/Übersetzungstabelle + Protokoll)

Revision ID: 0028_main_migration
Revises: 0027_rule_variants
Create Date: 2026-11-02 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028_main_migration"
down_revision = "0027_rule_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "main_migration_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_storage_path", sa.Text(), nullable=True),
        sa.Column("mapping", JSONB(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "main_migration_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_table", sa.String(20), nullable=False),
        sa.Column("old_id", sa.Integer(), nullable=False),
        sa.Column("new_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_table", "old_id", name="uq_main_migration_item"),
    )
    op.create_index("ix_main_migration_items_source_table", "main_migration_items", ["source_table"])
    op.create_index("ix_main_migration_items_new_id", "main_migration_items", ["new_id"])
    op.create_index("ix_main_migration_items_status", "main_migration_items", ["status"])


def downgrade() -> None:
    op.drop_table("main_migration_items")
    op.drop_table("main_migration_config")
