"""Eigene Gesellschaften (Rechnungsempfänger) für die Prüfung „Lieferant ist eigene Firma“

Revision ID: 0030_own_companies
Revises: 0029_doc_types
Create Date: 2026-11-04 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0030_own_companies"
down_revision = "0029_doc_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "own_companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("aliases", JSONB(), nullable=True),
        sa.Column("vat_ids", JSONB(), nullable=True),
        sa.Column("tax_numbers", JSONB(), nullable=True),
        sa.Column("ibans", JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("own_companies")
