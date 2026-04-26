from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ImportBatchCreate(BaseModel):
    folder_path: str = ""
    subfolder: str = ""         # optionaler Unterordner unter IMPORT_BASE_PATH
    comment: str | None = None
    company_name: str | None = None
    year: int | None = None
    ai_config_id: int | None = None
    system_prompt_id: int | None = None
    analyze_after_import: bool = False
    delete_source_files: bool = False
    folder_sync: bool = False    # Ordner-Sync aktivieren (nicht mit delete_source_files kombinierbar)


class ImportBatchRead(BaseModel):
    id: int
    import_folder_path: str
    storage_folder_path: str
    company_name: str
    year: int
    comment: str | None
    status: str
    folder_sync: bool | None = False
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImportBatchWithDocuments(ImportBatchRead):
    documents: list["DocumentRead"] = []

    model_config = ConfigDict(from_attributes=True)


from app.schemas.document import DocumentRead  # noqa: E402
ImportBatchWithDocuments.model_rebuild()
