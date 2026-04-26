from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    id: int
    batch_id: int
    original_filename: str
    stored_filename: str | None
    file_size_bytes: int
    page_count: int
    # Kommen via Model-Properties aus ImportBatch
    company: str | None = None
    year: int | None = None
    document_type: int = 0
    comment: str | None
    status: str
    soft_deleted: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListRead(DocumentRead):
    """Listenansicht — enthält Kurzfelder aus der verknüpften Extraktion."""

    total_amount: float | None = None
    invoice_number: str | None = None
    supplier_name: str | None = None
    has_extraction: bool = False

    model_config = ConfigDict(from_attributes=True)


class DocumentCommentUpdate(BaseModel):
    comment: str | None = None


class DocumentDetail(DocumentRead):
    """Detailansicht inkl. Extraktion, Bestellpositionen und Token-Statistiken."""

    extraction: "InvoiceExtractionRead | None" = None
    order_positions: list["OrderPositionRead"] = []
    # Token-Statistiken (kommen via Model-Properties aus token_counts)
    ki_input_tokens: int | None = None
    ki_output_tokens: int | None = None
    ki_reasoning_tokens: int | None = None
    ki_total_duration: float | None = None

    model_config = ConfigDict(from_attributes=True)


from app.schemas.invoice_extraction import InvoiceExtractionRead, OrderPositionRead  # noqa: E402
DocumentDetail.model_rebuild()
