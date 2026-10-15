"""Platzhalter für eine bereits angewendete Migration (KI-Client am Token-Zähler)

Revision ID: 0008_token_count_ai_client
Revises: 0007_export_config_table

Wie ``0007_export_config_table``: stammt aus der lokalen Weiterentwicklung der Produktiv-Datenbank und war nie im
Repo; die Datenbank ist bereits auf diese Revision gestempelt. Der Code im Repo nutzt die dort ergänzte Spalte
nicht — nichts zu tun. Frische Datenbanken bekommen sie nicht und brauchen sie nicht.
"""

revision = "0008_token_count_ai_client"
down_revision = "0007_export_config_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
