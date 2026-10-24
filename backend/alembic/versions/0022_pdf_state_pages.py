"""PDF-Zustand, Fehlercode (Nachbearbeitung) und Seitenklassifikation

Revision ID: 0022_pdf_state_pages
Revises: 0021_export_templates
Create Date: 2026-10-27 09:00:00.000000

Neue nullable Spalten an documents und die Tabelle document_pages — bestehende Daten bleiben unverändert.
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_pdf_state_pages"
down_revision = "0021_export_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("pdf_state", sa.String(20), nullable=True))
    op.add_column("documents", sa.Column("error_code", sa.String(30), nullable=True))
    op.create_index("ix_documents_pdf_state", "documents", ["pdf_state"])
    op.create_index("ix_documents_error_code", "documents", ["error_code"])
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pdf_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("starts_document", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("sender", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "pdf_version", "page_no", name="uq_document_page"),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])


def downgrade() -> None:
    op.drop_table("document_pages")
    op.drop_index("ix_documents_error_code", table_name="documents")
    op.drop_index("ix_documents_pdf_state", table_name="documents")
    op.drop_column("documents", "error_code")
    op.drop_column("documents", "pdf_state")
