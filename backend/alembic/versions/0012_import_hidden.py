"""Imports ausblenden (archivieren)

Revision ID: 0012_import_hidden
Revises: 0011_search_indexes
Create Date: 2026-10-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_import_hidden"
down_revision = "0011_search_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batches", sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_import_batches_hidden_at", "import_batches", ["hidden_at"])


def downgrade() -> None:
    op.drop_index("ix_import_batches_hidden_at", table_name="import_batches")
    op.drop_column("import_batches", "hidden_at")
