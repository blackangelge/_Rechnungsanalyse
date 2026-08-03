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

    # Ausgangswerte für die worker_*-Felder in automation_settings — werden NUR
    # beim allerersten Anlegen dieser Singleton-Zeile übernommen (siehe
    # crud/automation_settings.py::get_or_create()). Danach ist die DB die Quelle
    # der Wahrheit und über die Website (Einstellungen → Automatisierung) änderbar,
    # ohne dass diese ENV-Werte noch eine Rolle spielen oder ein Neustart nötig ist.

    # Maximale Anzahl gleichzeitiger Worker im Worker-Container (Obergrenze,
    # unabhängig von der Summe aus parallel_request über alle aktiven KI-Configs —
    # verhindert Thread-Pool-Erschöpfung auf NAS-Systemen mit wenigen CPUs).
    # War früher hart auf 2 kodiert.
    worker_concurrency: int = 5

    # Poll-Intervall des Dispatchers in Sekunden (wie oft DB auf neue Tasks/
    # geänderte KI-Configs geprüft wird). Feldname muss exakt WORKER_POLL_INTERVAL
    # entsprechen (pydantic-settings mappt Umgebungsvariablen case-insensitiv 1:1).
    worker_poll_interval: float = 20.0

    # Nach wie vielen Minuten ein 'in_progress'-Task als verwaist gilt und
    # automatisch zurück auf 'pending' gesetzt wird.
    worker_lock_timeout_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
