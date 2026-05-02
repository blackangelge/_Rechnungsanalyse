from datetime import datetime

from pydantic import BaseModel


class WorkflowTaskRead(BaseModel):
    id: int
    workflow_id: str
    payload: dict
    status: str
    attempts: int
    max_attempts: int
    result: dict | None
    error: str | None
    worker_id: str | None
    locked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowTaskListResponse(BaseModel):
    items: list[WorkflowTaskRead]
    total: int
    limit: int
    offset: int
