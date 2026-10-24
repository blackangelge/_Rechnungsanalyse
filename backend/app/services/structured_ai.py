"""
Strukturierter KI-Aufruf (OpenAI-kompatibel, vLLM): das generierte JSON-Schema geht als `response_format` vom Typ `json_schema`.

SYNCHRON (httpx.Client) — immer über `asyncio.to_thread()` aufrufen (wie `ai_service.extract_invoice_data`). Wirft nie: Fehler stehen in
`error` im selben Wortlaut wie in der übrigen Pipeline („KI-Verbindungsfehler: …“, „KI überlastet: HTTP 503“, „KI-Timeout …“), damit
`_is_ai_conn_error` sie erkennt und der Beleg später erneut versucht wird.

Erweiterungspunkt Tools: `tools` (Liste von OpenAI-Tool-Definitionen) wird nur mitgeschickt, wenn nicht leer. Die Tool-Schleife
(Aufrufe ausführen, Antworten zurückgeben) folgt später.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services.ai_reasoning import build_reasoning_param

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 900


@dataclass
class StructuredResult:
    parsed: dict | None = None
    raw: str = ""
    error: str | None = None
    ki_stats: dict = field(default_factory=dict)
    request_body_preview: dict | None = None        # ohne Bilddaten (für Protokoll/Tests)


def build_request(
    model_name: str, system_prompt: str, user_text: str, images_b64: list[str], response_format: dict, *,
    max_tokens: int | None = None, temperature: float | None = None, reasoning: str = "off", tools: list[dict] | None = None,
) -> dict:
    """Request-Body. Feste Teile (System, Anweisung) stehen vorn, die Bilder dahinter → Prefix-Caching."""
    content: list[dict] = [{"type": "text", "text": user_text}]
    for url in images_b64:
        content.append({"type": "image_url", "image_url": {"url": url}})
    body: dict[str, Any] = {
        "model": model_name,
        "stream": False,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}],
        "response_format": response_format,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    param = build_reasoning_param(reasoning)
    if param is not None:
        body["reasoning"] = param
    if tools:
        body["tools"] = tools
    return body


def _parse(raw: str) -> dict | None:
    for candidate in (raw.strip(), *(m.group(1) for m in [re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)] if m)):
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            continue
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def call_structured(config, system_prompt: str, user_text: str, images_b64: list[str], response_format: dict,
                    tools: list[dict] | None = None) -> StructuredResult:
    base = config.api_url.rstrip("/")
    endpoint = base + "/v1/chat/completions"        # auch LM Studio versteht diesen Pfad inkl. response_format
    headers = {"Content-Type": "application/json"}
    if getattr(config, "api_key", None):
        headers["Authorization"] = f"Bearer {config.api_key}"
    body = build_request(
        config.model_name, system_prompt, user_text, images_b64, response_format,
        max_tokens=getattr(config, "max_tokens", None), temperature=getattr(config, "temperature", None),
        reasoning=getattr(config, "reasoning", "off") or "off", tools=tools,
    )
    preview = {k: v for k, v in body.items() if k != "messages"}
    preview["messages"] = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text + f" [+{len(images_b64)} Bild(er)]"}]
    result = StructuredResult(request_body_preview=preview)
    try:
        payload = json.dumps(body).encode("utf-8")
        del body
        t0 = time.monotonic()
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(endpoint, content=payload, headers=headers)
        duration = time.monotonic() - t0
        del payload
    except httpx.TimeoutException as exc:
        result.error = result.raw = f"KI-Timeout nach {REQUEST_TIMEOUT_SECONDS}s: {exc}"
        return result
    except httpx.ConnectError as exc:
        result.error = result.raw = f"KI-Verbindungsfehler: {exc}"
        return result
    except Exception as exc:  # noqa: BLE001
        result.error = result.raw = f"Unerwarteter KI-Fehler: {exc}"
        logger.exception("Strukturierter KI-Aufruf fehlgeschlagen")
        return result

    if response.status_code in (429, 502, 503, 504):
        result.error = result.raw = f"KI überlastet: HTTP {response.status_code}"
        return result
    if response.status_code != 200:
        detail = ""
        try:
            detail = " — " + str(response.json().get("error", {}).get("message") or response.text)[:300]
        except Exception:  # noqa: BLE001
            detail = " — " + response.text[:300]
        result.error = result.raw = f"KI-Fehler: HTTP {response.status_code}{detail}"
        return result
    try:
        data = json.loads(response.content)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        result.raw = msg.get("content") or ""
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        result.ki_stats = {
            "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"), "tokens_per_second": None,
            "time_to_first_token": None, "total_duration": duration,
        }
    except Exception as exc:  # noqa: BLE001
        result.error = result.raw = f"Antwort-Parse-Fehler: {exc}"
        return result
    result.parsed = _parse(result.raw)
    if result.parsed is None:
        result.error = "Antwort ist kein gültiges JSON-Objekt"
    return result
