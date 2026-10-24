"""Prüfregeln: Varianten der linken Seite (Regel gilt als erfüllt, sobald eine Variante passt)

Revision ID: 0027_rule_variants
Revises: 0026_validation_rules
Create Date: 2026-11-01 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0027_rule_variants"
down_revision = "0026_validation_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("validation_rules", sa.Column("alt_left_exprs", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("validation_rules", "alt_left_exprs")
