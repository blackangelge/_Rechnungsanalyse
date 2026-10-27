"""Dokumenttypen neu nummeriert (Liste des Klassifizierer-Prompts; keine „Ausgangsrechnung“, Mischbeleg = 17)

Revision ID: 0029_doc_types
Revises: 0028_main_migration
Create Date: 2026-11-03 09:00:00.000000
"""

from alembic import op

revision = "0029_doc_types"
down_revision = "0028_main_migration"
branch_labels = None
depends_on = None

# alt → neu (Ausgangsrechnung zählt als Eingangsrechnung; Steuer-/Behördendokument und Sonstiges kaufm. Dokument → Sonstiges)
_MAP = {2: 1, 3: 4, 4: 5, 5: 6, 6: 3, 9: 10, 10: 11, 11: 15, 13: 9, 14: 15, 15: 17}


def _remap(mapping: dict[int, int]) -> None:
    case = " ".join(f"WHEN {a} THEN {b}" for a, b in mapping.items())
    op.execute(f"UPDATE documents SET document_type = CASE document_type {case} ELSE document_type END WHERE document_type IN ({', '.join(str(a) for a in mapping)})")


def upgrade() -> None:
    _remap(_MAP)


def downgrade() -> None:
    # nicht verlustfrei (Ausgangsrechnung und Sonstiges lassen sich nicht zurückholen)
    _remap({b: a for a, b in _MAP.items() if a not in (2, 11, 14)})
