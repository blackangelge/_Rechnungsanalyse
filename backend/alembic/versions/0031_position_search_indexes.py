"""Indizes für die Artikelsuche über das neue Schema (extracted_records JSONB)

Revision ID: 0031_position_search_indexes
Revises: 0030_own_companies
Create Date: 2026-10-28 00:00:00.000000

Die Artikelsuche liest die Positionen jetzt aus ``extracted_records`` (Entität "position",
JSONB ``data``). Ohne Indizes scannt jede Suche hunderttausende JSONB-Zeilen. Angelegt werden:
  - B-Tree auf entity_id und parent_record_id (Positions-/Wurzel-Verknüpfung),
  - B-Tree-Ausdrucksindizes (PostgreSQL) auf Lieferant, Einheit, Rechnungsdatum,
  - pg_trgm-GIN auf Artikelbezeichnung/-nummer und Lieferantenname (Teilstring-Suche).
Schlägt pg_trgm fehl, werden nur die Trigram-Indizes übersprungen (Suche bleibt nutzbar,
nur langsamer) — wie in Migration 0011. SQLite-Tests brauchen die JSONB-Indizes nicht.
"""

import logging

from alembic import op
import sqlalchemy as sa

revision = "0031_position_search_indexes"
down_revision = "0030_own_companies"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_BTREE = [("ix_er_entity_id", "entity_id"), ("ix_er_parent", "parent_record_id")]

_PG_EXPR = [
    "CREATE INDEX IF NOT EXISTS ix_er_supplier ON extracted_records ((data->'_master'->'lieferant'->>'name'))",
    "CREATE INDEX IF NOT EXISTS ix_er_unit_low ON extracted_records (lower(data->'menge'->>'original_unit'))",
    # zusammengesetzt: Einheitenfilter + Sortierung nach Beleg (neueste zuerst) ohne volle Sortierung
    "CREATE INDEX IF NOT EXISTS ix_er_unit_doc ON extracted_records (lower(data->'menge'->>'original_unit'), document_id)",
    "CREATE INDEX IF NOT EXISTS ix_er_invdate ON extracted_records ((data->>'rechnungsdatum'))",
]

_PG_TRGM = [
    "CREATE INDEX IF NOT EXISTS ix_er_artbez_trgm ON extracted_records USING gin ((data->>'artikelbezeichnung') gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_er_artnr_trgm ON extracted_records USING gin ((data->>'artikelnummer') gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_er_supplier_trgm ON extracted_records USING gin ((data->'_master'->'lieferant'->>'name') gin_trgm_ops)",
]


def upgrade() -> None:
    for name, col in _BTREE:
        op.create_index(name, "extracted_records", [col], if_not_exists=True)

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for stmt in _PG_EXPR:
        bind.execute(sa.text(stmt))
    try:
        with bind.begin_nested():      # Savepoint: ein Fehler macht die Migration nicht ungültig
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception as exc:           # noqa: BLE001
        logger.warning("pg_trgm nicht verfügbar — Artikelsuche-Teilstring-Indizes übersprungen: %s", exc)
        return
    for stmt in _PG_TRGM:
        bind.execute(sa.text(stmt))


def downgrade() -> None:
    bind = op.get_bind()
    names = ["ix_er_entity_id", "ix_er_parent"]
    if bind.dialect.name == "postgresql":
        names += ["ix_er_supplier", "ix_er_unit_low", "ix_er_unit_doc", "ix_er_invdate",
                  "ix_er_artbez_trgm", "ix_er_artnr_trgm", "ix_er_supplier_trgm"]
    for n in names:
        op.execute(f"DROP INDEX IF EXISTS {n}")
