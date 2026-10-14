"""Workflows (Analyse-Ablauf pro Lieferant) + Workflow-Snapshot am Beleg

- workflows (Graph als JSON)
- vendor.workflow_id (eigener Workflow je Lieferant, FK SET NULL)
- documents.used_workflow_id/_name/_graph + workflow_trace

Revision ID: 0010_workflows
Revises: 0009_pdf_versions
Create Date: 2026-10-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_workflows"
down_revision = "0009_pdf_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("graph", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_workflows_id", "workflows", ["id"])

    op.add_column("vendor", sa.Column("workflow_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_vendor_workflow_id", "vendor", "workflows", ["workflow_id"], ["id"], ondelete="SET NULL"
    )

    op.add_column("documents", sa.Column("used_workflow_id", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("used_workflow_name", sa.String(255), nullable=True))
    op.add_column("documents", sa.Column("used_workflow_graph", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("workflow_trace", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "workflow_trace")
    op.drop_column("documents", "used_workflow_graph")
    op.drop_column("documents", "used_workflow_name")
    op.drop_column("documents", "used_workflow_id")
    op.drop_constraint("fk_vendor_workflow_id", "vendor", type_="foreignkey")
    op.drop_column("vendor", "workflow_id")
    op.drop_index("ix_workflows_id", table_name="workflows")
    op.drop_table("workflows")
