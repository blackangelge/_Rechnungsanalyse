"""Workflow am Import (Pflicht für Analyse nach Import und Ordner-Sync)

Revision ID: 0020_batch_workflow
Revises: 0019_units
Create Date: 2026-10-25 18:00:00.000000

Nur eine neue, nullable Spalte — bestehende Imports bleiben unverändert (ohne Workflow lässt sich nichts mehr analysieren,
bis einer gewählt ist).
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_batch_workflow"
down_revision = "0019_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batches", sa.Column("workflow_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_import_batches_workflow_id", "import_batches", "workflows", ["workflow_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_import_batches_workflow_id", "import_batches", type_="foreignkey")
    op.drop_column("import_batches", "workflow_id")
