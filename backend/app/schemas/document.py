"""
Pydantic-Schemas für die Dokument-API.

Vererbungshierarchie:
  DocumentRead          — Basis: alle Dokument-Spalten + Batch-Properties (company, year)
    DocumentListRead    — Listenansicht: + Extraktion-Kurzfelder (total_amount, invoice_number,
                          supplier_name, has_extraction) und document_type-Name
    DocumentDetail      — Detailansicht: + extraction, order_positions, token_counts,
                          aggregierte ki_*-Felder

ACHTUNG: DocumentDetail erbt von DocumentRead (NICHT von DocumentListRead).
Die ki_*-Felder müssen daher explizit in DocumentDetail deklariert werden — sie
werden nicht automatisch vererbt. Das KI-Modal liest ki_* direkt aus DocumentDetail,
damit auch Nicht-Eingangsrechnungen (ohne InvoiceExtraction) Token-Stats haben.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    """Basis-Schema: alle Spalten der documents-Tabelle plus delegierte Batch-Properties."""

    id: int
    batch_id: int
    original_filename: str
    stored_filename: str | None
    file_size_bytes: int
    page_count: int
    # Kommen via Model-Properties aus ImportBatch
    company: str | None = None
    year: int | None = None
    # 0=Unbekannt, 1=Eingangsrechnung, 2=Ausgangsrechnung, …
    document_type: int = 0
    comment: str | None
    # pending | processing | done | error
    status: str
    soft_deleted: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListRead(DocumentRead):
    """
    Listenansicht — enthält zusätzliche Kurzfelder aus der verknüpften Extraktion.
    Diese Properties werden im Document-Model via @property-Delegation bereitgestellt.
    """

    total_amount: float | None = None      # InvoiceExtraction.total_amount_brutto
    invoice_number: str | None = None      # InvoiceExtraction.invoice_number
    supplier_name: str | None = None       # InvoiceExtraction.vendor_id (Freitext-Name)
    has_extraction: bool = False           # True wenn InvoiceExtraction vorhanden

    model_config = ConfigDict(from_attributes=True)


class DocumentCommentUpdate(BaseModel):
    """Request-Body für PATCH /api/documents/{id}/comment."""

    comment: str | None = None


class TokenCountRead(BaseModel):
    """
    Ein einzelner KI-Analyse-Durchlauf mit Token- und Zeitstatistiken.

    Jeder Aufruf von save_extraction() erzeugt einen neuen Eintrag in
    documents_token_counts (kein Überschreiben). So bleibt die vollständige
    Analyse-Historie erhalten und alle Durchläufe sind im KI-Modal sichtbar.
    """

    id: int
    input_token_count: int
    output_token_count: int
    reasoning_count: int          # nur manche Modelle (Reasoning-Modelle), sonst 0
    time_spent_seconds: float     # Gesamtdauer des HTTP-Requests
    created_at: datetime          # Zeitstempel des Durchlaufs

    model_config = ConfigDict(from_attributes=True)


class DocumentDetail(DocumentRead):
    """
    Detailansicht eines Dokuments — enthält alle Relationen und aggregierte KI-Statistiken.

    Wird von GET /api/documents/{id} zurückgegeben und für das KI-Modal und
    die Infos-Ansicht im Frontend verwendet.

    ki_*-Felder: Summen über alle DocumentTokenCount-Einträge (aggregiert via
    @property im Document-Model). Sie werden hier EXPLIZIT deklariert, weil
    DocumentDetail von DocumentRead (nicht DocumentListRead) erbt.
    """

    extraction: "InvoiceExtractionRead | None" = None
    order_positions: list["OrderPositionRead"] = []

    # Alle Analyse-Durchläufe — ein Eintrag pro KI-Aufruf (nie überschrieben)
    token_counts: list[TokenCountRead] = []

    # Aggregierte Werte über alle Durchläufe (kommen via @property aus Document-Model)
    ki_input_tokens: int | None = None       # Summe aller input_token_count-Einträge
    ki_output_tokens: int | None = None      # Summe aller output_token_count-Einträge
    ki_reasoning_tokens: int | None = None   # Summe aller reasoning_count-Einträge
    ki_total_duration: float | None = None   # Summe aller time_spent_seconds-Einträge

    model_config = ConfigDict(from_attributes=True)


# Zirkuläre Importe auflösen — InvoiceExtractionRead und OrderPositionRead
# referenzieren Document → müssen nach dieser Klassen-Definition importiert werden.
from app.schemas.invoice_extraction import InvoiceExtractionRead, OrderPositionRead  # noqa: E402
DocumentDetail.model_rebuild()
