"""
Gemeinsamer Helfer: wartet auf die Erreichbarkeit von PostgreSQL.

Wird sowohl vom Backend (main.py, vor den Alembic-Migrationen) als auch vom
Worker-Container (worker/main.py, vor dem Aufbau des asyncpg-Pools) genutzt.
"""

import logging
import time

import psycopg2

logger = logging.getLogger(__name__)


def wait_for_db(database_url: str, retries: int = 30, delay: float = 2.0) -> bool:
    """Wartet bis PostgreSQL Verbindungen annimmt (z.B. während initdb bei frischer DB).

    Returns True sobald eine Verbindung gelingt, sonst False nach `retries` Versuchen.
    """
    for attempt in range(retries):
        try:
            conn = psycopg2.connect(database_url, connect_timeout=5)
            conn.close()
            logger.info("✓ Datenbank erreichbar")
            return True
        except Exception as exc:
            logger.info("Warte auf Datenbank... (Versuch %d/%d): %s", attempt + 1, retries, exc)
            time.sleep(delay)

    logger.error("✗ Datenbank nach %d Versuchen nicht erreichbar", retries)
    return False
