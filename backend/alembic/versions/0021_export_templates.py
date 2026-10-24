"""Export-Vorlagen für den schemagesteuerten Excel-Export

Revision ID: 0021_export_templates
Revises: 0020_batch_workflow
Create Date: 2026-10-26 09:00:00.000000

Nur eine neue Tabelle.
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_export_templates"
down_revision = "0020_batch_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("layout", sa.String(20), nullable=False),
        sa.Column("quantity_mode", sa.String(12), server_default="original", nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("export_templates")
