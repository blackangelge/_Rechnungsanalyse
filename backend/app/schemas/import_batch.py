"""
Pydantic-Schemas für Import-Batches.

ImportBatchCreate        — Payload für POST /api/imports (neuer Import)
ImportBatchRead          — Antwort ohne Dokumentliste (für Listenansicht)
ImportBatchWithDocuments — Antwort mit eingebetteter Dokumentliste (für Detailansicht)
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImportBatchCreate(BaseModel):
    """
    Payload für das Starten eines neuen Imports.

    folder_path:          Vollständiger Pfad zum Import-Quellordner.
    subfolder:            Optionaler Unterordner unter IMPORT_BASE_PATH.
    company_name / year:  Werden zur Pfadberechnung des Speicherordners verwendet.
    analyze_after_import: KI-Analyse direkt nach dem Import starten.
    delete_source_files:  Original-PDFs nach erfolgreichem Kopieren löschen.
    folder_sync:          Ordner regelmäßig auf neue PDFs prüfen (Zukunftsfunktion).
    """

    folder_path: str = ""
    subfolder: str = ""         # optionaler Unterordner unter IMPORT_BASE_PATH
    comment: str | None = None
    company_name: str | None = None
    year: int | None = None
    ai_config_id: int | None = None       # KI-Konfiguration für analyze_after_import (None = Standard)
    system_prompt_id: int | None = None   # Systemprompt für analyze_after_import (None = Standard)
    analyze_after_import: bool = False
    delete_source_files: bool = False
    folder_sync: bool = False    # Ordner-Sync aktivieren (nicht mit delete_source_files kombinierbar)


class ImportBatchRead(BaseModel):
    """Antwort-Schema für einen Import-Batch (ohne eingebettete Dokumente)."""

    id: int
    import_folder_path: str
    storage_folder_path: str
    company_name: str
    year: int
    comment: str | None
    status: str           # pending | running | done | error
    folder_sync: bool | None = False
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImportBatchWithDocuments(ImportBatchRead):
    """
    Antwort-Schema für einen Import-Batch inkl. aller zugehörigen Dokumente.
    Wird für die Import-Detailseite verwendet.
    """

    documents: list["DocumentRead"] = []

    model_config = ConfigDict(from_attributes=True)


from app.schemas.document import DocumentRead  # noqa: E402
ImportBatchWithDocuments.model_rebuild()
