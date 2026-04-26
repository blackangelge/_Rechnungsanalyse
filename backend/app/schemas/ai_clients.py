from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ReasoningLevel = Literal["off", "low", "medium", "high", "on"]
EndpointType = Literal["openai", "lmstudio"]


class AIClientsBase(BaseModel):
    name: str
    api_key: str | None = None
    model_name: str
    # 0=Dokumententyp erkennen, 1=Eingangsrechnungsanalyse
    primary_type: int = 0
    max_tokens: int = 32000
    temperature: float = 0.1
    chat_response: bool = False
    active: bool = False
    reasoning: ReasoningLevel = "off"
    # IP-Adresse oder Hostname der API (z.B. "192.168.1.100" oder "api.openai.com")
    ip_address: str = ""
    endpoint_type: EndpointType = "openai"
    port: str = "1234"
    parallel_request: int = 1
    timeout_at: datetime | None = None


class AIClientsCreate(AIClientsBase):
    pass


class AIClientsUpdate(AIClientsBase):
    pass



class AIClientsRead(AIClientsBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
