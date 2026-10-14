"""PDF-Versionen + automatisches Kürzen

- document_versions (jede PDF-Bearbeitung = neue, unveränderliche Version)
- pdf_settings (Singleton: automatisches Kürzen)
- documents.pdf_version / documents.analyzed_pdf_version

Revision ID: 0009_pdf_versions
Revises: 0008_analysis_history
Create Date: 2026-10-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_pdf_versions"
down_revision = "0008_analysis_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("removed_pages", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
    )
    op.create_index("ix_document_versions_id", "document_versions", ["id"])
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.create_table(
        "pdf_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("auto_trim_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_trim_max_pages", sa.Integer(), nullable=False, server_default="3"),
    )

    op.add_column("documents", sa.Column("pdf_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("analyzed_pdf_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "analyzed_pdf_version")
    op.drop_column("documents", "pdf_version")
    op.drop_table("pdf_settings")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_index("ix_document_versions_id", table_name="document_versions")
    op.drop_table("document_versions")
