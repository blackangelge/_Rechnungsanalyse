"""ordner-sync und automatischer export

Revision ID: 0002_automation
Revises: 0001_initial
Create Date: 2026-07-05 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_automation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── import_batches: Ordner-Sync + Export-Tracking ─────────────────────────
    op.add_column(
        "import_batches",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        # Zeitpunkt der letzten Ordner-Sync-Prüfung (None = noch nie geprüft)
    )
    op.add_column(
        "import_batches",
        sa.Column("auto_export", sa.Boolean(), nullable=False, server_default="false"),
        # Opt-in: wöchentlicher automatischer Excel-Export für diesen Batch
    )
    op.add_column(
        "import_batches",
        sa.Column("last_exported_at", sa.DateTime(timezone=True), nullable=True),
        # Gemeinsamer Zähler für manuellen ("/export/new") und automatischen Export
    )

    # ── documents: updated_at für inkrementelle Exports ───────────────────────
    op.add_column(
        "documents",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── automation_settings: Singleton-Tabelle ────────────────────────────────
    op.create_table(
        "automation_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("folder_sync_interval_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("export_weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("export_hour", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("export_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_automation_settings_singleton"),
        sa.CheckConstraint("folder_sync_interval_minutes >= 1", name="ck_automation_settings_interval"),
        sa.CheckConstraint("export_weekday BETWEEN 0 AND 6", name="ck_automation_settings_weekday"),
        sa.CheckConstraint("export_hour BETWEEN 0 AND 23", name="ck_automation_settings_hour"),
        sa.CheckConstraint("export_minute BETWEEN 0 AND 59", name="ck_automation_settings_minute"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO automation_settings (id, folder_sync_interval_minutes, export_weekday, export_hour, export_minute) "
        "VALUES (1, 15, 0, 6, 0)"
    )


def downgrade() -> None:
    op.drop_table("automation_settings")
    op.drop_column("documents", "updated_at")
    op.drop_column("import_batches", "last_exported_at")
    op.drop_column("import_batches", "auto_export")
    op.drop_column("import_batches", "last_synced_at")
