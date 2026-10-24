"""KI-Assistent: Chats und Nachrichten

Revision ID: 0023_assistant_chats
Revises: 0022_pdf_state_pages
Create Date: 2026-10-28 09:00:00.000000

Nur neue Tabellen.
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_assistant_chats"
down_revision = "0022_pdf_state_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_chats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("ai_config_id", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assistant_chats_updated_at", "assistant_chats", ["updated_at"])
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), sa.ForeignKey("assistant_chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("images", sa.JSON(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("proposals", sa.JSON(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_assistant_messages_chat_id", "assistant_messages", ["chat_id"])


def downgrade() -> None:
    op.drop_table("assistant_messages")
    op.drop_table("assistant_chats")
