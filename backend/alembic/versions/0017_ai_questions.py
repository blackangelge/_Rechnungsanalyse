"""Freie KI-Fragen an Belege: ai_queries (Verlauf) und document_answers (Antworten je Beleg)

Revision ID: 0017_ai_questions
Revises: 0016_price_basis_pack
Create Date: 2026-10-24 12:00:00.000000

Nur neue Tabellen — bestehende Daten werden nicht angefasst.
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_ai_questions"
down_revision = "0016_price_basis_pack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("field_key", sa.String(40), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_queries_last_used_at", "ai_queries", ["last_used_at"])
    op.create_table(
        "document_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query_id", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("field_key", sa.String(40), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("raw_answer", sa.Text(), nullable=True),
        sa.Column("valid", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("applied", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("change_run_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_answers_document_id", "document_answers", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_answers_document_id", table_name="document_answers")
    op.drop_table("document_answers")
    op.drop_index("ix_ai_queries_last_used_at", table_name="ai_queries")
    op.drop_table("ai_queries")
