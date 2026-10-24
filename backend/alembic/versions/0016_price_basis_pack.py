"""Preisbasis und Verpackungsangaben je Position (Preis je N Einheiten, Inhalt je Verpackungseinheit, Einheitentyp)

Revision ID: 0016_price_basis_pack
Revises: 0015_data_changes
Create Date: 2026-10-24 00:00:00.000000

Nur neue, NULL-fähige Spalten — bestehende Daten (auch Hunderttausende Positionen) werden nicht angefasst und die Tabelle nicht
umgeschrieben. Zusätzlich werden die neuen Excel-Spalten in eine bereits gespeicherte Export-Konfiguration eingetragen.
"""

import json

from alembic import op
import sqlalchemy as sa

revision = "0016_price_basis_pack"
down_revision = "0015_data_changes"
branch_labels = None
depends_on = None

NEW_EXPORT_FIELDS = ["preis_je_menge", "verpackungsinhalt", "verpackungsinhalt_einheit", "einheitentyp"]


def upgrade() -> None:
    op.add_column("order_positions", sa.Column("price_quantity", sa.Numeric(12, 4), nullable=True))
    op.add_column("order_positions", sa.Column("pack_size", sa.Numeric(12, 4), nullable=True))
    op.add_column("order_positions", sa.Column("pack_unit", sa.String(50), nullable=True))
    op.add_column("order_positions", sa.Column("unit_type", sa.String(20), nullable=True))

    # Excel-Export: gespeicherte Spaltenauswahl um die neuen Felder ergänzen; die Spalten stehen im Export ganz hinten, bestehende Spaltenpositionen verschieben sich nicht
    bind = op.get_bind()
    try:
        row = bind.execute(sa.text("SELECT position_fields FROM export_config WHERE id = 1")).first()
    except Exception:                                   # Tabelle noch nicht vorhanden → Standardwerte greifen
        return
    if row is None or row[0] is None:
        return
    fields = row[0] if isinstance(row[0], list) else json.loads(row[0])
    merged = list(fields) + [f for f in NEW_EXPORT_FIELDS if f not in fields]
    if merged != fields:
        bind.execute(sa.text("UPDATE export_config SET position_fields = :v WHERE id = 1"), {"v": json.dumps(merged)})


def downgrade() -> None:
    for col in ("unit_type", "pack_unit", "pack_size", "price_quantity"):
        op.drop_column("order_positions", col)
