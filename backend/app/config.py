"""
Zentrale Anwendungskonfiguration via pydantic-settings.

Werte werden aus Umgebungsvariablen oder der .env-Datei geladen.
Unbekannte Variablen werden ignoriert (extra="ignore").

Pflichtfelder:
  DATABASE_URL  — PostgreSQL-Verbindungs-URL

Optionale Felder mit Standardwerten:
  IMPORT_BASE_PATH — Quellordner für neue PDFs (Standard: /import)
  STORAGE_PATH     — Zielordner für kopierte PDFs (Standard: /storage)

Auf dem NAS:
  IMPORT_BASE_PATH=/volume1/docker/_rechnungsanalyse/import
  STORAGE_PATH=/volume1/docker/_rechnungsanalyse/storage
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Liest alle Konfigurationswerte aus Umgebungsvariablen / .env-Datei.

    Wird als Singleton-Instanz `settings` exportiert und überall im Backend importiert.
    """

    # Datenbank
    database_url: str  # Pflichtfeld: z.B. postgresql://user:pass@db:5432/dbname

    # Ordner, aus dem neue PDFs importiert werden (Quelle)
    import_base_path: str = "/import"

    # Ordner, in dem importierte PDFs dauerhaft gespeichert werden (Ziel)
    storage_path: str = "/storage"

    # Basis-Ordner für automatisch geschriebene Excel-Exports
    # (app/worker/export_schedule.py) — eigener Pfad, unabhängig von Import/Storage.
    export_base_path: str = "/export"

    # Basis-URL des Worker-Containers (Docker-interner Service-Name), den das
    # Backend für Status-/Pause-/Resume-Proxy-Endpunkte anfragt
    worker_api_url: str = "http://worker:8000"

    # Zeitzone für alle nutzerseitig sichtbaren Zeitstempel (Excel-Export-Spalten,
    # automatische Export-Dateinamen). Container laufen intern auf UTC — ohne
    # explizite Umrechnung würden Zeitstempel fälschlich als UTC angezeigt.
    timezone: str = "Europe/Berlin"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
