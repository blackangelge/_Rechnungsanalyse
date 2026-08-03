"""ip_address spalte auf 100 zeichen erweitern

Revision ID: 0003_ip_address_length
Revises: 0002_automation
Create Date: 2026-07-06 08:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_ip_address_length"
down_revision = "0002_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # War String(20) — zu kurz für lange Hostnamen (FQDNs), Ports-Fehler bei der KI-Config.
    op.alter_column(
        "ai_clients",
        "ip_address",
        existing_type=sa.String(20),
        type_=sa.String(100),
        existing_nullable=False,
        existing_server_default="",
    )


def downgrade() -> None:
    op.alter_column(
        "ai_clients",
        "ip_address",
        existing_type=sa.String(100),
        type_=sa.String(20),
        existing_nullable=False,
        existing_server_default="",
    )
