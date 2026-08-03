"""port auf ai_clients optional machen (NULL = keine Portangabe in der URL)

Revision ID: 0005_ai_port_optional
Revises: 0004_ai_max_context_tokens
Create Date: 2026-07-24 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_ai_port_optional"
down_revision = "0004_ai_max_context_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ai_clients",
        "port",
        existing_type=sa.String(5),
        nullable=True,
        server_default=None,
    )


def downgrade() -> None:
    # Leere/NULL-Ports vor dem Zurücksetzen der NOT-NULL-Constraint auffüllen,
    # sonst schlägt die Migration bei bestehenden Zeilen ohne Port fehl.
    op.execute("UPDATE ai_clients SET port = '1234' WHERE port IS NULL OR port = ''")
    op.alter_column(
        "ai_clients",
        "port",
        existing_type=sa.String(5),
        nullable=False,
        server_default="1234",
    )
