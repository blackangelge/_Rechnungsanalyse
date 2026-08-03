"""worker_concurrency, worker_poll_interval_seconds, worker_lock_timeout_minutes
auf automation_settings — Worker-Verhalten live über die Website einstellbar

Revision ID: 0006_worker_settings
Revises: 0005_ai_port_optional
Create Date: 2026-07-24 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_worker_settings"
down_revision = "0005_ai_port_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_settings",
        sa.Column("worker_concurrency", sa.Integer(), nullable=False, server_default="5"),
        # Maximale Anzahl gleichzeitiger Worker (Obergrenze, unabhängig von der
        # Summe aus parallel_request über alle aktiven KI-Configs).
    )
    op.add_column(
        "automation_settings",
        sa.Column("worker_poll_interval_seconds", sa.Integer(), nullable=False, server_default="20"),
        # Wie oft der Dispatcher die DB auf neue Tasks/geänderte KI-Configs prüft.
    )
    op.add_column(
        "automation_settings",
        sa.Column("worker_lock_timeout_minutes", sa.Integer(), nullable=False, server_default="30"),
        # Nach wie vielen Minuten ein 'in_progress'-Task als verwaist gilt (z.B.
        # Worker abgestürzt) und zurück auf 'pending' gesetzt wird.
    )
    op.create_check_constraint(
        "ck_automation_settings_worker_concurrency", "automation_settings", "worker_concurrency >= 1"
    )
    op.create_check_constraint(
        "ck_automation_settings_worker_poll_interval", "automation_settings", "worker_poll_interval_seconds >= 1"
    )
    op.create_check_constraint(
        "ck_automation_settings_worker_lock_timeout", "automation_settings", "worker_lock_timeout_minutes >= 1"
    )


def downgrade() -> None:
    op.drop_constraint("ck_automation_settings_worker_lock_timeout", "automation_settings")
    op.drop_constraint("ck_automation_settings_worker_poll_interval", "automation_settings")
    op.drop_constraint("ck_automation_settings_worker_concurrency", "automation_settings")
    op.drop_column("automation_settings", "worker_lock_timeout_minutes")
    op.drop_column("automation_settings", "worker_poll_interval_seconds")
    op.drop_column("automation_settings", "worker_concurrency")
