"""Platzhalter für eine bereits angewendete Migration (Export-Konfiguration)

Revision ID: 0007_export_config_table
Revises: 0006_worker_settings

Diese Revision stammt aus der lokalen Weiterentwicklung der Produktiv-Datenbank (NAS) und war nie im Repo.
Die Datenbank ist bereits auf ``0008_token_count_ai_client`` gestempelt, also wurde sie dort schon angewendet —
Alembic muss die Revision aber kennen, sonst bricht jedes Upgrade mit „Can't locate revision" ab.
Deshalb gibt es sie hier als leere Platzhalter-Migration, die in der Kette vor ``0007_plausibility`` liegt.

Die Tabelle ``export_config`` legt für frische Datenbanken bereits ``0001_initial`` an; hier ist daher nichts zu tun.
"""

revision = "0007_export_config_table"
down_revision = "0006_worker_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
