"""
LM Studio Async Client
======================

Asynchroner Client für die native LM Studio REST API.
Endpoint: POST /api/v1/chat
Doku:     https://lmstudio.ai/docs/developer/rest/chat

Features:
- Text- und Bild-Input (mehrere Base64-Bilder pro Request möglich)
- System Prompt
- Integrations: Plugins und Ephemeral MCP Server
- Reasoning-Modi (off / low / medium / high / on)
- Erreichbarkeitsprüfung (health_check + Status nach Request)
- Strukturiertes Response-Objekt
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


# ----------------------------------------------------------------------
# Response-Objekte
# ----------------------------------------------------------------------


@dataclass
class ChatStats:
    """Token- und Performance-Statistiken einer Antwort."""

    input_tokens: int = 0
    total_output_tokens: int = 0
    reasoning_output_tokens: int = 0
    tokens_per_second: float = 0.0
    time_to_first_token_seconds: float = 0.0
    model_load_time_seconds: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatStats":
        return cls(
            input_tokens=data.get("input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            reasoning_output_tokens=data.get("reasoning_output_tokens", 0),
            tokens_per_second=data.get("tokens_per_second", 0.0),
            time_to_first_token_seconds=data.get("time_to_first_token_seconds", 0.0),
            model_load_time_seconds=data.get("model_load_time_seconds"),
        )


@dataclass
class ChatResponse:
    """Strukturierte Antwort der LM-Studio-API."""

    model_instance_id: str = ""
    output: list[dict[str, Any]] = field(default_factory=list)
    stats: Optional[ChatStats] = None
    response_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatResponse":
        return cls(
            model_instance_id=data.get("model_instance_id", ""),
            output=data.get("output", []) or [],
            stats=ChatStats.from_dict(data["stats"]) if data.get("stats") else None,
            response_id=data.get("response_id"),
            raw=data,
        )

    # ---- Convenience Getter ----

    def get_messages(self) -> list[str]:
        """Alle Text-Messages der Response in Reihenfolge."""
        return [item["content"] for item in self.output if item.get("type") == "message"]

    def get_text(self) -> str:
        """Alle Messages zu einem String zusammengefügt."""
        return "\n".join(self.get_messages())

    def get_tool_calls(self) -> list[dict[str, Any]]:
        """Alle erfolgreichen Tool-Calls."""
        return [item for item in self.output if item.get("type") == "tool_call"]

    def get_invalid_tool_calls(self) -> list[dict[str, Any]]:
        """Alle ungültigen Tool-Calls."""
        return [item for item in self.output if item.get("type") == "invalid_tool_call"]

    def get_reasoning(self) -> list[str]:
        """Alle Reasoning-Blöcke."""
        return [item.get("content", "") for item in self.output if item.get("type") == "reasoning"]
    
    def get_response_id(self) -> Optional[str]:
        """Gibt die response_id zurück, falls vorhanden."""
        return self.response_id


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------


class LMStudioClient:
    """
    Asynchroner Client für die LM Studio /api/v1/chat API.

    Beispiel:
        client = LMStudioClient(
            base_url="http://192.168.178.170:1234",
            model="qwen/qwen3-vl-4b",
            access_token="optional-token",
        )
        client.set_text_prompt("Beschreibe das Bild.")
        client.add_image_base64(b64_data)
        client.add_image_base64(b64_data2, mime="image/png")

        response = await client.send()
        if response is None:
            print("Nicht erreichbar:", client.last_error)
        else:
            print(response.get_text())
    """

    REASONING_VALUES = {"off", "low", "medium", "high", "on"}

    def __init__(
        self,
        base_url: str,
        model: str,
        access_token: Optional[str] = None,
        timeout: float = 300.0,
        response_id: Optional[str] = None,
    ) -> None:
        # Verbindung
        self.base_url: str = base_url.rstrip("/")
        self.model: str = model
        self.access_token: Optional[str] = access_token
        self.timeout: float = timeout
        self.response_id: Optional[str] = response_id

        # Input
        self._text_prompt: Optional[str] = None
        self._input_items: list[dict[str, Any]] = []  # bei Bild- / Multipart-Modus
        self._has_images: bool = False

        # Optionale Felder
        self.system_prompt: Optional[str] = None
        self.integrations: list[Any] = []

        # Generation-Parameter mit den geforderten Defaults
        self.stream: bool = False
        # store ist bewusst NICHT konfigurierbar: LM Studio benötigt store=false
        # in jeder Anfrage. Kein Parameter dafür, damit das nie versehentlich
        # überschrieben werden kann.
        self.store: bool = False
        self.max_output_tokens: int = 32000
        self.reasoning: str = "off"
        self.temperature: Optional[float] = None
        self.top_p: Optional[float] = None
        self.top_k: Optional[int] = None
        self.min_p: Optional[float] = None
        self.repeat_penalty: Optional[float] = None
        self.context_length: Optional[int] = None

        # Status nach Request
        self.is_reachable: bool = False
        self.last_status_code: Optional[int] = None
        self.last_error: Optional[str] = None
        # "connect" | "timeout" | "http_status" | None — lässt Aufrufer zwischen
        # Verbindungsfehlern und Timeouts unterscheiden (z.B. für Retry-Texte).
        self.last_error_kind: Optional[str] = None
        # Gesamtdauer der letzten send()/send_sync()-Anfrage in Sekunden.
        self.last_duration_seconds: Optional[float] = None
        self.last_response: Optional[ChatResponse] = None

    # ------------------------------------------------------------------
    # Prompt-Methoden (zwei Arten)
    # ------------------------------------------------------------------

    def set_text_prompt(self, text: str) -> None:
        """
        Variante 1: Reiner Text-Prompt.

        Setzt 'input' als String. Falls später Bilder hinzugefügt werden,
        wird der Text automatisch als 'message'-Item vor die Bilder gesetzt
        (Multipart-Modus).
        """
        self._text_prompt = text
        # Falls bereits Bilder gesetzt sind, Text als erstes Item halten
        if self._input_items and self._input_items[0].get("type") == "text":
            self._input_items[0]["content"] = text

    def set_multipart_prompt(self, text: str) -> None:
        """
        Variante 2: Multipart-Prompt (Text + Bilder als Array).

        Initialisiert das Input-Array mit einem 'message'-Item. Bilder können
        anschließend über add_image_base64() hinzugefügt werden.
        """
        self._text_prompt = text
        # Bestehende Bilder behalten, Text-Item neu an Position 0 setzen
        images = [item for item in self._input_items if item.get("type") == "image"]
        self._input_items = [{"type": "text", "content": text}, *images]

    # ------------------------------------------------------------------
    # Bilder
    # ------------------------------------------------------------------

    def add_image_base64(self, base64_data: str, mime: str = "image/png") -> None:
        """
        Fügt ein Base64-Bild zum Input hinzu.

        Args:
            base64_data: Reiner Base64-String ODER fertige Data-URL.
            mime:        MIME-Type, falls noch keine Data-URL übergeben wurde.
        """
        if base64_data.startswith("data:"):
            data_url = base64_data
        else:
            data_url = f"data:{mime};base64,{base64_data}"

        # Wenn noch kein Multipart aufgebaut: ggf. mit Text-Prompt initialisieren
        if not self._input_items:
            if self._text_prompt is not None:
                self._input_items.append({"type": "text", "content": self._text_prompt})
            # Ohne Text-Prompt: nur Bilder im Array — laut Doku zulässig

        self._input_items.append({"type": "image", "data_url": data_url})
        self._has_images = True

    def clear_images(self) -> None:
        """Entfernt alle bisher hinzugefügten Bilder, behält den Text-Prompt."""
        self._input_items = [
            item for item in self._input_items if item.get("type") != "image"
        ]
        self._has_images = False

    def reset(self) -> None:
        """Setzt Prompt, Bilder und Integrations vollständig zurück."""
        self._text_prompt = None
        self._input_items = []
        self._has_images = False
        self.integrations = []

    # ------------------------------------------------------------------
    # System Prompt
    # ------------------------------------------------------------------

    def set_system_prompt(self, prompt: str) -> None:
        """Setzt das System-Prompt."""
        self.system_prompt = prompt

    # ------------------------------------------------------------------
    # Integrations: Plugins & Ephemeral MCP Server
    # ------------------------------------------------------------------

    def add_plugin(
        self,
        plugin_id: str,
        allowed_tools: Optional[list[str]] = None,
    ) -> None:
        """
        Fügt einen Plugin-Eintrag hinzu (z. B. 'mcp/playwright').

        Args:
            plugin_id:     Eindeutige Plugin-ID. Für installierte MCP-Server
                           üblicherweise 'mcp/<server_label>'.
            allowed_tools: Optionale Whitelist erlaubter Tool-Namen. None = alle.
        """
        if allowed_tools is None:
            # Shorthand: einfach die ID als String
            self.integrations.append(plugin_id)
        else:
            self.integrations.append(
                {
                    "type": "plugin",
                    "id": plugin_id,
                    "allowed_tools": allowed_tools,
                }
            )

    def add_ephemeral_mcp(
        self,
        server_label: str,
        server_url: str,
        allowed_tools: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Fügt einen Ephemeral MCP Server hinzu (on-the-fly, ohne mcp.json).

        Args:
            server_label:  Bezeichnung des MCP-Servers.
            server_url:    URL des MCP-Servers.
            allowed_tools: Optionale Whitelist erlaubter Tool-Namen. None = alle.
            headers:       Optionale HTTP-Header für Requests an den Server.
        """
        entry: dict[str, Any] = {
            "type": "ephemeral_mcp",
            "server_label": server_label,
            "server_url": server_url,
        }
        if allowed_tools is not None:
            entry["allowed_tools"] = allowed_tools
        if headers is not None:
            entry["headers"] = headers
        self.integrations.append(entry)

    def clear_integrations(self) -> None:
        """Entfernt alle Integrations."""
        self.integrations = []

    # ------------------------------------------------------------------
    # Generation-Parameter
    # ------------------------------------------------------------------

    def set_generation_params(
        self,
        max_output_tokens: Optional[int] = None,
        reasoning: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        min_p: Optional[float] = None,
        repeat_penalty: Optional[float] = None,
        context_length: Optional[int] = None,
        stream: Optional[bool] = None,
    ) -> None:
        """Setzt Generation-Parameter. Was None bleibt, wird nicht überschrieben.

        Kein store-Parameter: store ist fest auf False verdrahtet (siehe __init__)."""
        if max_output_tokens is not None:
            self.max_output_tokens = max_output_tokens
        if reasoning is not None:
            if reasoning not in self.REASONING_VALUES:
                raise ValueError(
                    f"reasoning muss einer von {sorted(self.REASONING_VALUES)} sein."
                )
            self.reasoning = reasoning
        if temperature is not None:
            self.temperature = temperature
        if top_p is not None:
            self.top_p = top_p
        if top_k is not None:
            self.top_k = top_k
        if min_p is not None:
            self.min_p = min_p
        if repeat_penalty is not None:
            self.repeat_penalty = repeat_penalty
        if context_length is not None:
            self.context_length = context_length
        if stream is not None:
            self.stream = stream

    # ------------------------------------------------------------------
    # HTTP / Erreichbarkeit
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _build_input(self) -> Any:
        """
        Baut das 'input'-Feld für den Payload.

        - Multipart (Bilder vorhanden oder set_multipart_prompt benutzt):
          Liste von Items.
        - Sonst: einfacher String aus _text_prompt.
        """
        if self._input_items:
            return self._input_items
        if self._text_prompt is not None:
            return self._text_prompt
        raise ValueError(
            "Kein Prompt gesetzt. Bitte set_text_prompt() oder "
            "set_multipart_prompt() / add_image_base64() aufrufen."
        )

    def _build_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._build_input(),
            "stream": self.stream,
            "max_output_tokens": self.max_output_tokens,
            "reasoning": self.reasoning,
            "store": self.store,
            "previous_response_id": self.response_id,
        }

        if self.system_prompt is not None:
            payload["system_prompt"] = self.system_prompt
        if self.integrations:
            payload["integrations"] = self.integrations

        optional = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "context_length": self.context_length,
        }
        for key, value in optional.items():
            if value is not None:
                payload[key] = value

        return payload

    async def health_check(self) -> bool:
        """
        Prüft die Erreichbarkeit des LM-Studio-Servers (GET /api/v0/models).
        Aktualisiert is_reachable, last_status_code, last_error.
        """
        url = f"{self.base_url}/api/v0/models"
        self.last_error = None
        self.last_status_code = None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.get(url, headers=self._build_headers())
                self.last_status_code = resp.status_code
                self.is_reachable = resp.is_success
                if not resp.is_success:
                    self.last_error = f"HTTP {resp.status_code}: {resp.text}"
                return self.is_reachable
        except httpx.ConnectError as exc:
            self.is_reachable = False
            self.last_status_code = 408
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        except (httpx.HTTPError) as exc:
            self.is_reachable = False
            self.last_status_code = 500
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def _reset_request_state(self) -> None:
        self.last_error = None
        self.last_error_kind = None
        self.last_status_code = None
        self.last_duration_seconds = None
        self.last_response = None

    async def send(self) -> Optional[ChatResponse]:
        """
        Sendet den Chat-Request an POST /api/v1/chat (non-streaming), asynchron.

        Aktualisiert nach Aufruf:
            - is_reachable, last_status_code, last_error, last_error_kind,
              last_duration_seconds, last_response

        Returns:
            ChatResponse-Objekt, oder None bei Verbindungsfehler / Timeout
            (last_error_kind unterscheidet dann "connect" von "timeout").

        Raises:
            httpx.HTTPStatusError: bei HTTP-Fehlerstatus (4xx/5xx).
        """
        url = f"{self.base_url}/api/v1/chat"
        payload = self._build_payload()
        self._reset_request_state()

        _t_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(url, headers=self._build_headers(), json=payload)
                self.last_status_code = resp.status_code
                self.is_reachable = True

                if not resp.is_success:
                    self.last_error = f"HTTP {resp.status_code}: {resp.text}"
                    self.last_error_kind = "http_status"
                    resp.raise_for_status()

                self.last_response = ChatResponse.from_dict(resp.json())
                return self.last_response

        except httpx.TimeoutException as exc:
            self.is_reachable = False
            self.last_error_kind = "timeout"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        except httpx.ConnectError as exc:
            self.is_reachable = False
            self.last_error_kind = "connect"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        except httpx.HTTPError:
            raise
        finally:
            self.last_duration_seconds = time.monotonic() - _t_start

    def send_sync(self) -> Optional[ChatResponse]:
        """
        Synchrones Pendant zu send() — nutzt httpx.Client statt httpx.AsyncClient.

        Für Aufrufer, die (wie app/services/ai_service.py) bewusst synchron bleiben
        müssen, um selbst per asyncio.to_thread() aufgerufen zu werden: die
        JSON-Serialisierung großer Base64-Bildpayloads soll den Event-Loop nicht
        blockieren. send() (async) würde diese Serialisierung direkt im Event-Loop
        ausführen — send_sync() lässt das dem Aufrufer-Thread überlassen.

        Verhalten (Rückgabe, Fehlerbehandlung, aktualisierte Attribute) ist
        identisch zu send().
        """
        url = f"{self.base_url}/api/v1/chat"
        payload = self._build_payload()
        self._reset_request_state()

        _t_start = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout) as http:
                resp = http.post(url, headers=self._build_headers(), json=payload)
                self.last_status_code = resp.status_code
                self.is_reachable = True

                if not resp.is_success:
                    self.last_error = f"HTTP {resp.status_code}: {resp.text}"
                    self.last_error_kind = "http_status"
                    resp.raise_for_status()

                self.last_response = ChatResponse.from_dict(resp.json())
                return self.last_response

        except httpx.TimeoutException as exc:
            self.is_reachable = False
            self.last_error_kind = "timeout"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        except httpx.ConnectError as exc:
            self.is_reachable = False
            self.last_error_kind = "connect"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        except httpx.HTTPError:
            raise
        finally:
            self.last_duration_seconds = time.monotonic() - _t_start


