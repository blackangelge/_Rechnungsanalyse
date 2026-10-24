"""Prüfregeln (frei definierbare Rechenregeln für extrahierte Datensätze)

Revision ID: 0026_validation_rules
Revises: 0025_document_analyzed_at
Create Date: 2026-10-31 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_validation_rules"
down_revision = "0025_document_analyzed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("entity_name", sa.String(60), nullable=False),
        sa.Column("left_expr", sa.Text(), nullable=False),
        sa.Column("right_expr", sa.Text(), nullable=False),
        sa.Column("comparator", sa.String(2), nullable=False, server_default="="),
        sa.Column("tolerance_abs", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tolerance_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("severity", sa.String(10), nullable=False, server_default="error"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("validation_rules")
