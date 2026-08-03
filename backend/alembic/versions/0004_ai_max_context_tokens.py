"""max_context_tokens auf ai_clients (Kapazitätsgrenze für Dokumentgröße)

Revision ID: 0004_ai_max_context_tokens
Revises: 0003_ip_address_length
Create Date: 2026-07-06 08:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_ai_max_context_tokens"
down_revision = "0003_ip_address_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_clients",
        sa.Column("max_context_tokens", sa.Integer(), nullable=True),
        # NULL = keine Beschränkung (bestehende Configs bleiben unverändert nutzbar).
    )


def downgrade() -> None:
    op.drop_column("ai_clients", "max_context_tokens")
