"""
Zentrale Anwendungskonfiguration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Datenbank
    database_url: str

    # Ordner, aus dem neue PDFs importiert werden (Quelle)
    import_base_path: str = "/import"

    # Ordner, in dem importierte PDFs dauerhaft gespeichert werden (Ziel)
    storage_path: str = "/storage"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
