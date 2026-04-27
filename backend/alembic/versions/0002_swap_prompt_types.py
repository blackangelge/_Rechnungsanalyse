"""
Tauscht die type-Werte in der system_prompts-Tabelle.

Hintergrund:
  In der initialen Implementierung war die Belegung umgekehrt:
    0 = Standard-Extraktionsprompt (Eingangsrechnung)
    1 = Dokumententyp-Erkennungsprompt

  Die neue, konsistentere Belegung ist:
    0 = Dokumententyp-Erkennungsprompt
    1 = Standard-Extraktionsprompt (Eingangsrechnung)

  Das passt zu ai_clients.primary_type (0=Typ-Erkennung, 1=Extraktion) und
  zur Logik in crud/system_prompt.py (_TYPE_DOC_TYPE=0, _TYPE_DEFAULT=1).

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
    # Swap via temporären Wert 99, um Kollisionen beim 0→1 / 1→0 Tausch zu vermeiden.
    op.execute("UPDATE system_prompts SET type = 99 WHERE type = 0")  # alte Standards auf Temp
    op.execute("UPDATE system_prompts SET type = 0  WHERE type = 1")  # alte Dok-Typ → neue 0
    op.execute("UPDATE system_prompts SET type = 1  WHERE type = 99") # Temp → neue Standards 1


def downgrade() -> None:
    # Rückgängig: gleiche Swap-Logik (symmetrisch)
    op.execute("UPDATE system_prompts SET type = 99 WHERE type = 0")
    op.execute("UPDATE system_prompts SET type = 0  WHERE type = 1")
    op.execute("UPDATE system_prompts SET type = 1  WHERE type = 99")
