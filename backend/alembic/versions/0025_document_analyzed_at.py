"""Zeitpunkt der letzten abgeschlossenen KI-Analyse je Beleg (für saubere Durchsatz-Statistiken)

Revision ID: 0025_document_analyzed_at
Revises: 0024_document_trash
Create Date: 2026-10-30 09:00:00.000000

`documents.updated_at` ändert sich auch bei Import, Seitenzahl-Nachlesen, Kommentar usw. — die Kennzahlen „letzte 60 Min./24 Std.“
zählten dadurch Importe mit. `analyzed_at` wird nur beim Abschluss einer Analyse gesetzt (processing → done/error).
Bestehende Belege: aus dem letzten Eintrag in documents_token_counts nachgetragen.
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_document_analyzed_at"
down_revision = "0024_document_trash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_analyzed_at", "documents", ["analyzed_at"])
    op.execute(
        "UPDATE documents SET analyzed_at = (SELECT MAX(t.created_at) FROM documents_token_counts t WHERE t.document_id = documents.id) "
        "WHERE status IN ('done', 'error') AND EXISTS (SELECT 1 FROM documents_token_counts t WHERE t.document_id = documents.id)"
    )


def downgrade() -> None:
    op.drop_index("ix_documents_analyzed_at", table_name="documents")
    op.drop_column("documents", "analyzed_at")
