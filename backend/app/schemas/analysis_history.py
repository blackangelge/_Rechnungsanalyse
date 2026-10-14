"""Pydantic-Schemas für die Analyse-Historie."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AnalysisHistoryRead(BaseModel):
    """Eintrag in der Liste (ohne das große Snapshot-JSON)."""

    id: int
    document_id: int
    created_at: datetime
    pdf_version: int | None = None
    workflow_name: str | None = None
    validation_status: str | None = None
    invoice_number: str | None = None
    supplier_name: str | None = None
    total_amount: Decimal | None = None
    position_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class AnalysisHistoryDetail(AnalysisHistoryRead):
    """Vollständiger früherer Stand — Felder wie in der Detailansicht eines Belegs."""

    extraction: dict
    order_positions: list[dict]
    # verwendete Prompts/Workflow zum Zeitpunkt dieses Stands
    used_system_prompt: str | None = None
    used_doc_type_prompt: str | None = None
    used_recheck_prompt: str | None = None
    workflow_run: list[dict] | None = None
    validation: dict | None = None
