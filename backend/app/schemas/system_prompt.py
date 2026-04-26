from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SystemPromptCreate(BaseModel):
    name: str
    content: str
    # 0=Standard-Extraktionsprompt, 1=Dokumententyp-Erkennung
    type: int = 0


class SystemPromptUpdate(BaseModel):
    name: str
    content: str
    type: int = 0


class SystemPromptRead(BaseModel):
    id: int
    name: str
    content: str
    type: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
