"""
KI-Konfigurationstest: Sendet ein Testdokument an eine KI-Konfiguration und gibt
die VOLLSTÄNDIGE Anfrage und Antwort zurück — zum Debuggen in den Einstellungen.

Unabhängig von app/services/ai_service.py (Produktions-Pipeline), damit Debug-
spezifische Anpassungen (volle Request/Response-Erfassung statt geparster Felder)
die echte Analyse nicht beeinflussen können. Schreibt NIE in die Datenbank und
setzt NIE ein Dokument/einen Import um — rein lesend/sendend.

Die KI-Konfiguration muss dafür NICHT aktiv (active=True) sein.
"""

import logging
import time
from typing import Any

import httpx

from app.models.ai_clients import AIClients
from app.services.ai_reasoning import build_reasoning_param
from app.services.ai_url import build_ai_url

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 120


def _truncate_images(body: dict) -> dict:
    """
    Gibt eine Kopie des Request-Bodys zurück, in der Bild-Base64-Daten durch
    einen kurzen Platzhalter ersetzt sind — die vollständigen Bilddaten sind für
    die Anzeige nicht relevant und würden die Debug-Ansicht unbenutzbar machen.
    Die tatsächlich GESENDETE Anfrage bleibt davon unberührt (separate Kopie).
    """
    import copy

    display = copy.deepcopy(body)

    def _shorten(data_url: str) -> str:
        return f"<Bilddaten, {len(data_url)} Zeichen> {data_url[:40]}…"

    # OpenAI-kompatibles Format: messages[].content[] mit type="image_url"
    for msg in display.get("messages", []) or []:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url")
                    if isinstance(url, str):
                        part["image_url"]["url"] = _shorten(url)

    # LM-Studio-Format: input[] mit type="image"
    for item in display.get("input", []) or []:
        if isinstance(item, dict) and item.get("type") == "image":
            data_url = item.get("data_url")
            if isinstance(data_url, str):
                item["data_url"] = _shorten(data_url)

    return display


def build_test_request(
    images_b64: list[str],
    config: AIClients,
    system_prompt_text: str | None,
    user_text: str,
) -> dict[str, Any]:
    """Baut Methode/URL/Headers/Body — exakt dieselbe Struktur wie ai_service.py
    für den jeweiligen endpoint_type, damit der Test realistisch ist."""
    endpoint_type = config.endpoint_type or "openai"
    base = build_ai_url(config.ip_address, config.port).rstrip("/")
    reasoning = config.reasoning or "off"

    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    if endpoint_type == "lmstudio":
        # LM Studios eigener /api/v1/chat-Endpunkt akzeptiert reasoning als
        # reinen String (eigenes Payload-Format, nicht OpenAI-kompatibel) —
        # anders als der openai-Pfad unten, siehe ai_reasoning.py.
        lmstudio_reasoning = "high" if reasoning == "on" else reasoning
        url = f"{base}/api/v1/chat"
        input_items: list[dict] = [{"type": "text", "text": user_text}]
        for img in images_b64:
            input_items.append({"type": "image", "data_url": img})
        body: dict[str, Any] = {
            "model": config.model_name,
            "input": input_items,
            "stream": False,
            "max_output_tokens": config.max_tokens,
            "reasoning": lmstudio_reasoning,
            "store": False,
            "previous_response_id": None,
            "temperature": config.temperature,
        }
        if system_prompt_text:
            body["system_prompt"] = system_prompt_text
    else:
        url = f"{base}/v1/chat/completions"
        content_parts: list[dict] = [{"type": "text", "text": user_text}]
        for img in images_b64:
            content_parts.append({"type": "image_url", "image_url": {"url": img}})
        body = {
            "model": config.model_name,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt_text},
                {"role": "user", "content": content_parts},
            ],
        }
        reasoning_param = build_reasoning_param(reasoning)
        if reasoning_param is not None:
            body["reasoning"] = reasoning_param

    return {"method": "POST", "url": url, "headers": headers, "body": body}


def run_ai_test(
    images_b64: list[str],
    config: AIClients,
    system_prompt_text: str | None,
    user_text: str,
) -> dict[str, Any]:
    """
    Sendet die Testanfrage synchron (muss über asyncio.to_thread aufgerufen werden,
    analog zu ai_service.extract_invoice_data). Wirft nie eine Exception — Fehler
    landen im Rückgabewert (result["error"]), damit der Router immer 200 liefert
    und das Frontend Anfrage + Fehler gemeinsam anzeigen kann.
    """
    req = build_test_request(images_b64, config, system_prompt_text, user_text)

    result: dict[str, Any] = {
        "request": {
            "method": req["method"],
            "url": req["url"],
            "headers": req["headers"],
            "body": _truncate_images(req["body"]),
        },
        "response": None,
        "error": None,
        "success": False,
    }

    logger.info("KI-Test: Sende Anfrage an %s (config_id=%d)", req["url"], config.id)

    try:
        t0 = time.monotonic()
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.request(
                req["method"], req["url"], json=req["body"], headers=req["headers"]
            )
        duration = time.monotonic() - t0

        try:
            body_text = resp.text
        except Exception:
            body_text = "<Antworttext konnte nicht gelesen werden>"

        result["response"] = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": body_text,
            "duration_seconds": round(duration, 3),
        }
        result["success"] = resp.is_success
        if not resp.is_success:
            result["error"] = f"HTTP {resp.status_code}"

    except httpx.TimeoutException as exc:
        result["error"] = f"Timeout nach {REQUEST_TIMEOUT_SECONDS}s: {exc}"
    except httpx.ConnectError as exc:
        result["error"] = f"Verbindungsfehler: {exc}"
    except Exception as exc:
        logger.exception("KI-Test: unerwarteter Fehler")
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result
