"""swap system_prompt type values: 0=Dokumententyp-Erkennung, 1=Standard-Extraktion

Revision ID: 0002_swap_prompt_types
Revises: 0001_initial
Create Date: 2026-04-26 00:00:00.000000
"""

from alembic import op

revision = "0002_swap_prompt_types"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bisherige Belegung: 0=Standard, 1=Dokumententyp
    # Neue Belegung:      0=Dokumententyp, 1=Standard
    # Swap via temporären Wert 99 um Kollisionen zu vermeiden
    op.execute("UPDATE system_prompts SET type = 99 WHERE type = 0")
    op.execute("UPDATE system_prompts SET type = 0  WHERE type = 1")
    op.execute("UPDATE system_prompts SET type = 1  WHERE type = 99")


def downgrade() -> None:
    # Rückgängig: gleiche Swap-Logik
    op.execute("UPDATE system_prompts SET type = 99 WHERE type = 0")
    op.execute("UPDATE system_prompts SET type = 0  WHERE type = 1")
    op.execute("UPDATE system_prompts SET type = 1  WHERE type = 99")
