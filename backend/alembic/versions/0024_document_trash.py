"""Papierkorb: Grund und Zeitpunkt des Löschens

Revision ID: 0024_document_trash
Revises: 0023_assistant_chats
Create Date: 2026-10-29 09:00:00.000000

Zwei nullable Spalten an documents (bestehende Belege bleiben unverändert).
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_document_trash"
down_revision = "0023_assistant_chats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("deleted_reason", sa.String(300), nullable=True))
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_soft_deleted", "documents", ["soft_deleted"])


def downgrade() -> None:
    op.drop_index("ix_documents_soft_deleted", table_name="documents")
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "deleted_reason")
