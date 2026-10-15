"""Plausibilitätsprüfung + gespeicherte Systemprompts

- order_positions.total_price (Gesamtpreis der Position; bisher nur im raw_response)
- invoice_extractions.validation_status / validation_details
- documents.used_system_prompt / used_doc_type_prompt / used_recheck_prompt

Revision ID: 0007_plausibility
Revises: 0006_worker_settings
Create Date: 2026-10-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_plausibility"
down_revision = "0008_token_count_ai_client"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_positions", sa.Column("total_price", sa.Numeric(12, 2), nullable=True))

    op.add_column("invoice_extractions", sa.Column("validation_status", sa.String(30), nullable=True))
    op.add_column("invoice_extractions", sa.Column("validation_details", sa.Text(), nullable=True))
    op.create_index(
        "ix_invoice_extractions_validation_status", "invoice_extractions", ["validation_status"]
    )

    op.add_column("documents", sa.Column("used_system_prompt", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("used_doc_type_prompt", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("used_recheck_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "used_recheck_prompt")
    op.drop_column("documents", "used_doc_type_prompt")
    op.drop_column("documents", "used_system_prompt")
    op.drop_index("ix_invoice_extractions_validation_status", table_name="invoice_extractions")
    op.drop_column("invoice_extractions", "validation_details")
    op.drop_column("invoice_extractions", "validation_status")
    op.drop_column("order_positions", "total_price")
