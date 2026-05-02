"""
Adapter zwischen dem Dispatcher (worker.py) und konkreten KI-Client-Implementierungen.

Implementiert das AIClient-Protocol des Dispatchers:
    async def send(self, server: AIServer, payload: dict) -> dict

Unterstützte Backends (gesteuert durch server.raw["endpoint_type"]):
    "lmstudio" → LMStudioBackend  (native LM Studio REST API /api/v1/chat)
    "openai"   → OpenAICompatibleBackend  (/v1/chat/completions)

Neues Backend hinzufügen:
    1. Unterklasse von BaseKIBackend erstellen
    2. register_backend("mein_typ", MeinBackend) aufrufen
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from app.services.lmstudioclient import ChatResponse, LMStudioClient

log = logging.getLogger(__name__)


# ── Backend-Abstraktion ───────────────────────────────────────────────────────

class BaseKIBackend(ABC):
    """Basisklasse für KI-Client-Backends."""

    @abstractmethod
    async def send_images(
        self,
        images_b64: list[str],
        system_prompt: str | None,
        user_prompt: str,
        server_raw: dict,
        base_url: str,
    ) -> tuple[str, dict]:
        """
        Sendet Bilder + Prompts an die KI.

        Args:
            images_b64:    Liste von Base64-kodierten Bildern (ohne Data-URL-Prefix).
            system_prompt: Optionaler System-Prompt.
            user_prompt:   Benutzer-Prompt / Aufgabenbeschreibung.
            server_raw:    Komplette DB-Zeile des ai_clients-Eintrags.
            base_url:      Fertige Basis-URL (schema://host:port).

        Returns:
            (raw_text_response, ki_stats)
            ki_stats-Felder: input_tokens, output_tokens, reasoning_tokens,
                             tokens_per_second, time_to_first_token (alle nullable)
        """


# ── LM Studio ─────────────────────────────────────────────────────────────────

class LMStudioBackend(BaseKIBackend):
    """LM Studio native REST API (POST /api/v1/chat)."""

    async def send_images(
        self,
        images_b64: list[str],
        system_prompt: str | None,
        user_prompt: str,
        server_raw: dict,
        base_url: str,
    ) -> tuple[str, dict]:
        client = LMStudioClient(
            base_url=base_url,
            model=server_raw["model_name"],
            access_token=server_raw.get("api_key") or None,
            timeout=300.0,
        )
        client.set_generation_params(
            max_output_tokens=server_raw.get("max_tokens", 32000),
            reasoning=server_raw.get("reasoning", "off"),
            temperature=server_raw.get("temperature", 0.1),
        )
        if system_prompt:
            client.set_system_prompt(system_prompt)

        client.set_multipart_prompt(user_prompt)
        for img in images_b64:
            client.add_image_base64(img)

        response: ChatResponse | None = await client.send()

        if response is None:
            raise ConnectionError(
                f"LM Studio nicht erreichbar ({base_url}): {client.last_error}"
            )

        ki_stats: dict = {}
        if response.stats:
            ki_stats = {
                "input_tokens": response.stats.input_tokens,
                "output_tokens": response.stats.total_output_tokens,
                "reasoning_tokens": response.stats.reasoning_output_tokens,
                "tokens_per_second": response.stats.tokens_per_second,
                "time_to_first_token": response.stats.time_to_first_token_seconds,
            }

        return response.get_text(), ki_stats


# ── OpenAI-kompatibel ─────────────────────────────────────────────────────────

class OpenAICompatibleBackend(BaseKIBackend):
    """OpenAI-kompatibler Client (POST /v1/chat/completions)."""

    async def send_images(
        self,
        images_b64: list[str],
        system_prompt: str | None,
        user_prompt: str,
        server_raw: dict,
        base_url: str,
    ) -> tuple[str, dict]:
        url = f"{base_url}/v1/chat/completions"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        content: list[dict] = [{"type": "text", "text": user_prompt}]
        for img in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img}"},
            })
        messages.append({"role": "user", "content": content})

        payload = {
            "model": server_raw["model_name"],
            "messages": messages,
            "max_tokens": server_raw.get("max_tokens", 32000),
            "temperature": server_raw.get("temperature", 0.1),
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if server_raw.get("api_key"):
            headers["Authorization"] = f"Bearer {server_raw['api_key']}"

        try:
            async with httpx.AsyncClient(timeout=300.0) as http:
                resp = await http.post(url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise ConnectionError(f"OpenAI-API nicht erreichbar ({url}): {exc}") from exc

        if not resp.is_success:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        usage = data.get("usage", {})
        ki_stats = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        }
        return text, ki_stats


# ── Registry ──────────────────────────────────────────────────────────────────

_BACKENDS: dict[str, type[BaseKIBackend]] = {
    "lmstudio": LMStudioBackend,
    "openai":   OpenAICompatibleBackend,
}


def register_backend(endpoint_type: str, cls: type[BaseKIBackend]) -> None:
    """Registriert ein neues Backend für einen endpoint_type."""
    _BACKENDS[endpoint_type] = cls
    log.info("KI-Backend '%s' registriert: %s", endpoint_type, cls.__name__)


# ── Haupt-Adapter (implementiert AIClient-Protocol) ───────────────────────────

class AIDispatcherClient:
    """
    Implementiert das AIClient-Protocol des Dispatchers.

    Wählt das Backend anhand von server.raw["endpoint_type"] und routet
    jeden Task-Typ an den passenden Handler.

    Neuen Task-Typ hinzufügen: Methode _handle_<kind>(server, payload)
    ergänzen und in send() eintragen.
    """

    def _get_backend(self, endpoint_type: str) -> BaseKIBackend:
        cls = _BACKENDS.get(endpoint_type)
        if cls is None:
            raise ValueError(
                f"Unbekannter endpoint_type: {endpoint_type!r}. "
                f"Bekannt: {sorted(_BACKENDS)}"
            )
        return cls()

    async def send(self, server, payload: dict) -> dict:
        """Dispatcher-Protocol: Task entgegennehmen und verarbeiten."""
        kind = payload.get("kind")

        if kind == "process_document":
            return await self._handle_process_document(server, payload)

        raise ValueError(f"Unbekannter Task-Typ: {kind!r}")

    async def _handle_process_document(self, server, payload: dict) -> dict:
        """
        Startet die KI-Analyse eines Dokuments über die bestehende Pipeline.

        Der server.id entspricht dem ai_config_id-Feld in der Analyse-Pipeline,
        da beide auf den gleichen ai_clients-Eintrag zeigen.

        Gibt ConnectionError zurück wenn die KI nicht erreichbar ist —
        der Worker re-queued den Task dann automatisch (Retry-Logik).
        """
        from app.routers.documents import _analyze_single, _db_set_processing

        document_id = payload.get("document_id")
        if not isinstance(document_id, int):
            raise ValueError(f"Ungültige document_id im Payload: {payload!r}")

        system_prompt_text: str | None = payload.get("system_prompt_text")

        # Dokument-Status auf "processing" setzen damit das Frontend den Spinner zeigt.
        # Läuft in einem Thread damit der Event-Loop nicht blockiert wird.
        await asyncio.to_thread(_db_set_processing, document_id)

        log.info(
            "Analyse Dokument #%d via Server '%s' (id=%d, endpoint_type=%s)",
            document_id,
            server.name,
            server.id,
            server.raw.get("endpoint_type", "openai"),
        )

        try:
            result_code = await _analyze_single(
                document_id,
                server.id,
                system_prompt_text,
            )
        except Exception as exc:
            log.error(
                "Analyse Dokument #%d fehlgeschlagen: %s: %s",
                document_id, type(exc).__name__, exc,
                exc_info=True,
            )
            raise

        # "ai_unavailable" signalisiert: KI war temporär nicht erreichbar.
        # Als ConnectionError weiterwerfen → Worker re-queued den Task (Retry).
        if result_code == "ai_unavailable":
            raise ConnectionError(
                f"KI nicht erreichbar für Dokument #{document_id} "
                f"(Server: {server.name})"
            )

        log.info(
            "Analyse Dokument #%d abgeschlossen (result_code=%s)",
            document_id, result_code,
        )
        return {"document_id": document_id, "result_code": result_code or "ok"}
