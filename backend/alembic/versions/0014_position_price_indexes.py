"""Indizes auf Preis-/Mengenspalten der Positionen (Artikelsuche: „alle Positionen über 200.000 €“)

Revision ID: 0014_position_price_indexes
Revises: 0013_users
Create Date: 2026-10-21 00:00:00.000000

Ohne diese Indizes musste jeder Preisfilter alle Positionen durchlesen (Sekunden bis Minuten bei Hunderttausenden Zeilen
→ Zeitüberschreitung); mit ihnen genügt ein Bereichsscan.
"""

from alembic import op

revision = "0014_position_price_indexes"
down_revision = "0013_users"
branch_labels = None
depends_on = None

COLUMNS = ["total_price", "unit_price_netto", "quantity"]


def upgrade() -> None:
    for col in COLUMNS:
        op.create_index(f"ix_order_positions_{col}", "order_positions", [col], if_not_exists=True)


def downgrade() -> None:
    for col in COLUMNS:
        op.drop_index(f"ix_order_positions_{col}", table_name="order_positions", if_exists=True)
