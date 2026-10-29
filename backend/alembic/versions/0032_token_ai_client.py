"""ai_client_id in documents_token_counts (Statistik je KI-Rechner)

Revision ID: 0032_token_ai_client
Revises: 0031_position_search_indexes
Create Date: 2026-10-29 00:00:00.000000

Speichert je KI-Durchlauf, welcher KI-Rechner (ai_clients.id) ihn verarbeitet hat —
Grundlage für die Durchsatz-Statistik je KI-Rechner (Live-Ansicht). Fuellt sich ab
Einbau vorwaerts; bereits vorhandene Eintraege bleiben NULL.
"""

from alembic import op
import sqlalchemy as sa

revision = "0032_token_ai_client"
down_revision = "0031_position_search_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents_token_counts", sa.Column("ai_client_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_dtc_ai_client", "documents_token_counts", "ai_clients", ["ai_client_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_dtc_client_created", "documents_token_counts", ["ai_client_id", "created_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_dtc_client_created", table_name="documents_token_counts")
    op.drop_constraint("fk_dtc_ai_client", "documents_token_counts", type_="foreignkey")
    op.drop_column("documents_token_counts", "ai_client_id")
