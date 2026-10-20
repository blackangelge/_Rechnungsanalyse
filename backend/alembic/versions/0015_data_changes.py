"""Änderungsprotokoll: data_change_runs, data_changes

Revision ID: 0015_data_changes
Revises: 0014_position_price_indexes
Create Date: 2026-10-22 00:00:00.000000

Nur neue Tabellen — bestehende Daten werden nicht angefasst.
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_data_changes"
down_revision = "0014_position_price_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_change_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("changed_fields", sa.Integer(), server_default="0", nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undo_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_data_change_runs_kind", "data_change_runs", ["kind"])
    op.create_table(
        "data_changes",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("data_change_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_name", sa.String(60), nullable=False),
        sa.Column("row_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("field", sa.String(60), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("status", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index("ix_data_changes_run_id", "data_changes", ["run_id"])
    op.create_index("ix_data_changes_document_id", "data_changes", ["document_id"])


def downgrade() -> None:
    op.drop_table("data_changes")
    op.drop_table("data_change_runs")
