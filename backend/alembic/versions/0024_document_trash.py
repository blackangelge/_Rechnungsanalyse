"""Papierkorb: Grund und Zeitpunkt des Löschens

Revision ID: 0024_document_trash
Revises: 0023_assistant_chats
Create Date: 2026-10-29 09:00:00.000000

Zwei nullable Spalten an documents (bestehende Belege bleiben unverändert). Kein neuer Index (soft_deleted ist seit 0011 indiziert).
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
    # Index auf soft_deleted gibt es schon (0011_search_indexes: ix_documents_soft_deleted)


def downgrade() -> None:
    op.drop_column("documents", "deleted_at")
    op.drop_column("documents", "deleted_reason")
