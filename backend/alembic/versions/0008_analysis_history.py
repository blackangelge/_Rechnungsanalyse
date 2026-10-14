"""Analyse-Historie

- Tabelle analysis_history (frühere Analyse-Stände je Beleg)
- automation_settings.analysis_history_keep (Standard 3)

Revision ID: 0008_analysis_history
Revises: 0007_plausibility
Create Date: 2026-10-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_analysis_history"
down_revision = "0007_plausibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("pdf_version", sa.Integer(), nullable=True),
        sa.Column("workflow_name", sa.String(255), nullable=True),
        sa.Column("validation_status", sa.String(30), nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("supplier_name", sa.String(255), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("position_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("snapshot", sa.Text(), nullable=False),
    )
    op.create_index("ix_analysis_history_id", "analysis_history", ["id"])
    op.create_index("ix_analysis_history_document_id", "analysis_history", ["document_id"])
    op.add_column(
        "automation_settings",
        sa.Column("analysis_history_keep", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_check_constraint(
        "ck_automation_settings_history_keep", "automation_settings",
        "analysis_history_keep BETWEEN 0 AND 20",
    )


def downgrade() -> None:
    op.drop_constraint("ck_automation_settings_history_keep", "automation_settings", type_="check")
    op.drop_column("automation_settings", "analysis_history_keep")
    op.drop_index("ix_analysis_history_document_id", table_name="analysis_history")
    op.drop_index("ix_analysis_history_id", table_name="analysis_history")
    op.drop_table("analysis_history")
