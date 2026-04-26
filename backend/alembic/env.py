"""
Alembic-Migrationskonfiguration.

Liest die Datenbank-URL aus den Anwendungseinstellungen (nicht aus alembic.ini),
damit nur eine einzige .env-Datei benötigt wird.
Importiert alle ORM-Modelle über app.models, damit Alembic sie beim
Autogenerieren von Migrationen erkennt.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic-Konfigurationsobjekt (liest alembic.ini)
config = context.config

# Python-Logging entsprechend alembic.ini konfigurieren
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Anwendungseinstellungen und Datenbankbasis laden
from app.config import settings
from app.database import Base

# ALLE Modelle importieren, damit ihre Tabellen in Base.metadata erscheinen.
# Das models-Paket (__init__.py) importiert alle Modelle zentral.
import app.models  # noqa: F401 — registriert alle Modelle in Base.metadata

# Datenbank-URL aus Umgebungsvariablen setzen (überschreibt alembic.ini)
config.set_main_option("sqlalchemy.url", settings.database_url)

# Metadaten aller ORM-Modelle — Alembic nutzt sie für Autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Migrationen im Offline-Modus ausführen (ohne aktive DB-Verbindung).
    Erzeugt SQL-Skripte statt sie direkt auszuführen.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Migrationen im Online-Modus ausführen (mit aktiver DB-Verbindung).
    Standard-Modus beim Aufruf via 'alembic upgrade head'.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
