"""
Pydantic-Schemas für Analyse-Workflows.
"""

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowListRead(BaseModel):
    """Workflow in der Übersicht (ohne Graph)."""

    id: int
    name: str
    description: str | None = None
    is_default: bool = False
    node_count: int = 0
    supplier_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkflowRead(BaseModel):
    id: int
    name: str
    description: str | None = None
    graph: dict
    is_default: bool = False
    supplier_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("graph", mode="before")
    @classmethod
    def _parse_graph(cls, v):
        return json.loads(v) if isinstance(v, str) else v


class WorkflowCreate(BaseModel):
    """Neuen Workflow anlegen — mit eigenem Graph oder aus einer Vorlage (`template`)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    graph: dict | None = None
    template: str | None = Field(default=None, description="Schlüssel einer Vorlage (GET /templates)")


class WorkflowUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    graph: dict


class WorkflowValidateRequest(BaseModel):
    graph: dict


class WorkflowValidateResult(BaseModel):
    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class WorkflowTemplate(BaseModel):
    key: str
    name: str
    description: str
    graph: dict


class SupplierWorkflowUpdate(BaseModel):
    workflow_id: int | None = None
