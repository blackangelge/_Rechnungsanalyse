"""
KI-Extraktions-Service.

Sendet PDF-Seitenbilder an eine OpenAI-kompatible Vision-LLM-API und
parst die strukturierte JSON-Antwort in ein Python-Dict.

Unterstützte API-Formate:
- LM Studio (lokal)
- Ollama mit OpenAI-Kompatibilitätsmodus
- Jede andere API mit POST /v1/chat/completions + Vision-Unterstützung

Die KI wird angewiesen, ausschließlich ein JSON-Objekt zurückzugeben.
Bei Parse-Fehlern wird die Rohantwort trotzdem gespeichert (für Debugging).
Wirft nie eine Exception — gibt bei Fehlern leeres Dict zurück.
"""

import json
import logging
import re
import time
from typing import Any

import httpx

from app.models.ai_clients import AIClients
from app.services.lmstudioclient import LMStudioClient

logger = logging.getLogger(__name__)

# Timeout für einen einzelnen KI-API-Aufruf in Sekunden.
# Lokale Modelle können bei langen PDFs länger brauchen.
REQUEST_TIMEOUT_SECONDS = 900


def _send_via_lmstudio(
    images_b64: list[str],
    config: AIClients,
    system_prompt_text: str,
    user_text: str,
) -> tuple[str, dict, str | None]:
    """
    Sendet eine Anfrage über LMStudioClient (native LM Studio /api/v1/chat API).

    Zentraler Ersatz für den früheren, hier inline gebauten httpx-Request —
    wird von extract_invoice_data() und detect_document_type() gemeinsam
    genutzt, damit die LM-Studio-Logik nur an einer Stelle existiert.

    Läuft SYNCHRON (LMStudioClient.send_sync(), kein asyncio) — die
    aufrufenden Funktionen sind absichtlich synchron und werden selbst per
    asyncio.to_thread() aufgerufen (siehe Docstring von extract_invoice_data).

    Returns:
        (raw_text, ki_stats, error) — ist error nicht None, MUSS der Aufrufer
        sofort mit dem Fehlertext zurückkehren (raw_text/ki_stats sind dann
        leer/bedeutungslos). Die Fehlertext-Präfixe ("KI überlastet:",
        "KI-Timeout", "KI-Verbindungsfehler:", "KI-Fehler:") entsprechen den
        bestehenden Konventionen, die documents.py::_is_ai_conn_error() prüft.
    """
    client = LMStudioClient(
        base_url=config.api_url,
        model=config.model_name,
        access_token=config.api_key or None,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    reasoning = getattr(config, "reasoning", "off") or "off"
    reasoning_value = "high" if reasoning == "on" else reasoning
    client.set_generation_params(
        max_output_tokens=config.max_tokens,
        reasoning=reasoning_value,
        temperature=config.temperature,
    )
    client.set_system_prompt(system_prompt_text)
    client.set_multipart_prompt(user_text)
    for data_url in images_b64:
        client.add_image_base64(data_url)

    try:
        response = client.send_sync()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (429, 502, 503, 504):
            logger.warning("LM Studio überlastet (HTTP %d)", status)
            return "", {}, f"KI überlastet: HTTP {status}"
        logger.error("LM Studio Fehlerstatus (HTTP %d)", status)
        return "", {}, f"KI-Fehler: HTTP {status}"

    if response is None:
        if client.last_error_kind == "timeout":
            logger.error("LM Studio Timeout: %s", client.last_error)
            return "", {}, f"KI-Timeout nach {REQUEST_TIMEOUT_SECONDS}s: {client.last_error}"
        logger.error("LM Studio Verbindungsfehler: %s", client.last_error)
        return "", {}, f"KI-Verbindungsfehler: {client.last_error}"

    stats = response.stats
    ki_stats = {
        "input_tokens":        stats.input_tokens if stats else None,
        "output_tokens":       stats.total_output_tokens if stats else None,
        "reasoning_tokens":    stats.reasoning_output_tokens if stats else None,
        "tokens_per_second":   stats.tokens_per_second if stats else None,
        "time_to_first_token": stats.time_to_first_token_seconds if stats else None,
        "total_duration":      client.last_duration_seconds,
    }
    logger.info(
        "LM Studio Stats: %s In, %s Out, %s Reasoning, %.1f tok/s",
        ki_stats["input_tokens"], ki_stats["output_tokens"],
        ki_stats["reasoning_tokens"], ki_stats["tokens_per_second"] or 0,
    )
    return response.get_text(), ki_stats, None


def extract_invoice_data(
    images_b64: list[str],
    config: AIClients,
    system_prompt_text: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, dict]:
    """
    Sendet Rechnungsbilder an die Vision-LLM und gibt die extrahierten Daten zurück.

    WICHTIG: Diese Funktion ist SYNCHRON und muss immer über asyncio.to_thread()
    aufgerufen werden. Der gesamte HTTP-Request (inkl. JSON-Serialisierung,
    Netzwerk-I/O und Response-Parsing) läuft im Thread — der Event-Loop wird
    nie blockiert, egal wie groß die Payloads oder wie lang die KI braucht.

    Args:
        images_b64: Liste von Base64-kodierten PNG-Bildern (eine pro Seite).
        config: KI-Konfiguration mit API-URL, Modell-Name und Authentifizierung.
        system_prompt_text: Inhalt des Extraktionsprompts aus der system_prompts-
                             Tabelle. Es gibt KEINEN Code-Fallback mehr — fehlt
                             der Prompt, bricht die Funktion mit einer klaren
                             Fehlermeldung ab, statt einen unsichtbaren
                             Standardtext zu verwenden.

    Returns:
        Tuple: (extracted_fields, order_positions, raw_response, ki_stats)

    Raises nie eine Exception — gibt bei Fehlern leere Dicts zurück.
    """
    logger.info(
        "Starte KI-Extraktion: Modell='%s', Seiten=%d", config.model_name, len(images_b64)
    )

    if not system_prompt_text:
        msg = (
            "Kein Extraktionsprompt konfiguriert — bitte unter Einstellungen → "
            "Systemprompts einen Prompt vom Typ 'Standard-Extraktion' anlegen."
        )
        logger.error(msg)
        return {}, [], msg, {}

    active_system_prompt = system_prompt_text
    endpoint_type = getattr(config, "endpoint_type", "openai") or "openai"
    base = config.api_url.rstrip("/")
    reasoning = getattr(config, "reasoning", "off") or "off"
    reasoning_value = "high" if reasoning == "on" else reasoning

    user_text = (
        f"Die folgende Rechnung besteht aus {len(images_b64)} Seite(n). "
        "Analysiere alle Seiten und extrahiere die Daten gemäß der Anweisung."
    )

    logger.info("Sende Anfrage an: %s (Typ: %s)", base, endpoint_type)

    raw_text = ""
    ki_stats: dict = {}

    # ─── LM Studio: über LMStudioClient (siehe _send_via_lmstudio oben) ─────────
    if endpoint_type == "lmstudio":
        raw_text, ki_stats, error = _send_via_lmstudio(
            images_b64, config, active_system_prompt, user_text
        )
        if error:
            return {}, [], error, {}

    # ─── OpenAI-kompatibel: weiterhin Inline-httpx (kein Client für dieses Format) ─
    else:
        endpoint = base + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        content_parts: list[dict] = [{"type": "text", "text": user_text}]
        for idx, data_url in enumerate(images_b64):
            content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
            logger.debug("  Seite %d/%d eingebettet", idx + 1, len(images_b64))
        request_body = {
            "model": config.model_name,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "reasoning": reasoning_value,
            "stream": False,
            "messages": [
                {"role": "system", "content": active_system_prompt},
                {"role": "user", "content": content_parts},
            ],
        }
        del content_parts

        try:
            # JSON-Serialisierung: kann bei großen Bilddaten mehrere Sekunden dauern
            serialized_body = json.dumps(request_body).encode("utf-8")
            request_body.clear()  # Bilddaten sofort freigeben

            _t_start = time.monotonic()
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, content=serialized_body, headers=headers)
            _total_duration = time.monotonic() - _t_start
            del serialized_body

            status_code = response.status_code
            if status_code == 200:
                pass
            elif status_code in (429, 503, 502, 504):
                raw_text = f"KI überlastet: HTTP {status_code}"
                logger.warning("KI-API überlastet (HTTP %d)", status_code)
                return {}, [], raw_text, {}
            elif status_code == 500:
                raw_text = "KI-Fehler: HTTP 500"
                logger.error("KI-API interner Fehler (HTTP 500)")
                return {}, [], raw_text, {}
            else:
                raw_text = f"KI-Fehler: HTTP {status_code}"
                logger.error("KI-API unerwarteter Status (HTTP %d)", status_code)
                return {}, [], raw_text, {}

            # ─── Response-Parsing ────────────────────────────────────────────
            try:
                response_data = json.loads(response.content)
                raw_text = (
                    response_data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content") or ""
                )
                usage = response_data.get("usage") or {}
                details = usage.get("completion_tokens_details") or {}
                ki_stats = {
                    "input_tokens":        usage.get("prompt_tokens"),
                    "output_tokens":       usage.get("completion_tokens"),
                    "reasoning_tokens":    details.get("reasoning_tokens"),
                    "tokens_per_second":   None,
                    "time_to_first_token": None,
                    "total_duration":      _total_duration,
                }
            except Exception as parse_exc:
                raw_text = f"Antwort-Parse-Fehler: {parse_exc}"
                logger.error("Fehler beim Parsen der API-Antwort: %s", parse_exc)
                return {}, [], raw_text, {}

        except httpx.TimeoutException as exc:
            raw_text = f"KI-Timeout nach {REQUEST_TIMEOUT_SECONDS}s: {exc}"
            logger.error("KI-API Timeout: %s", exc)
            return {}, [], raw_text, {}
        except httpx.ConnectError as exc:
            raw_text = f"KI-Verbindungsfehler: {exc}"
            logger.error("KI-API Verbindungsfehler: %s", exc)
            return {}, [], raw_text, {}
        except Exception as exc:
            raw_text = f"Unerwarteter KI-Fehler: {exc}"
            logger.exception("Unerwarteter Fehler bei KI-API-Aufruf: %s", exc)
            return {}, [], raw_text, {}

    logger.debug("KI-Antwort (erste 300 Zeichen): %s", raw_text[:300])

    # ─── JSON-Parsing + Normalisierung ───────────────────────────────────────
    try:
        parsed = _parse_json_response(raw_text)
    except Exception as exc:
        logger.error("JSON-Parse-Fehler: %s", exc)
        return {}, [], raw_text, ki_stats

    parsed = _normalize_decimal_commas(parsed)
    raw_text = json.dumps(parsed, ensure_ascii=False, indent=2)

    try:
        if "lieferant" in parsed or "rechnungsdaten" in parsed or "zahlungsinformationen" in parsed:
            extracted_fields, order_positions = _map_new_format(parsed)
        else:
            order_positions = parsed.pop("order_positions", []) or []
            extracted_fields = _clean_flat_fields(parsed)
    except Exception as exc:
        logger.error("Fehler beim Verarbeiten der Felder: %s", exc)
        extracted_fields, order_positions = {}, []

    logger.info(
        "Extraktion erfolgreich: %d Felder, %d Positionen",
        len([v for v in extracted_fields.values() if v is not None]),
        len(order_positions),
    )
    return extracted_fields, order_positions, raw_text, ki_stats


def detect_document_type(
    images_b64: list[str],
    config,
    document_types: list[dict],
    system_prompt_text: str | None = None,
) -> tuple[int | None, str | None, str, dict]:
    """
    Erkennt den Dokumententyp eines Dokuments anhand der Seitenbilder.

    WICHTIG: Synchron — immer via asyncio.to_thread() aufrufen.

    Args:
        images_b64: Base64-kodierte PNG-Bilder (eine pro Seite).
        config: KI-Konfiguration.
        document_types: Liste von Dicts [{"id": 1, "name": "Eingangsrechnung"}, ...].
        system_prompt_text: Inhalt des Dokumententyp-Erkennungsprompts aus der
                             system_prompts-Tabelle. Es gibt KEINEN Code-Fallback
                             mehr — fehlt der Prompt, bricht die Funktion mit
                             einer klaren Fehlermeldung ab.

    Returns:
        (type_id, type_name, raw_response, ki_stats)
        type_id/type_name sind None wenn die Klassifikation fehlschlägt.
    """
    logger.info(
        "Starte Dokumententyp-Erkennung: Modell='%s', Seiten=%d",
        config.model_name, len(images_b64),
    )

    if not system_prompt_text:
        msg = (
            "Kein Dokumententyp-Erkennungsprompt konfiguriert — bitte unter "
            "Einstellungen → Systemprompts einen Prompt vom Typ "
            "'Dokumententyp-Erkennung' anlegen."
        )
        logger.error(msg)
        return None, None, msg, {}

    active_system_prompt = system_prompt_text
    endpoint_type = getattr(config, "endpoint_type", "openai") or "openai"
    base = config.api_url.rstrip("/")
    reasoning = getattr(config, "reasoning", "off") or "off"
    reasoning_value = "high" if reasoning == "on" else reasoning

    # Typliste als Text aufbauen
    type_list_text = "\n".join(f"{dt['id']}: {dt['name']}" for dt in document_types)
    user_text = (
        f"Identifiziere den Typ des folgenden Dokuments ({len(images_b64)} Seite(n)).\n\n"
        f"Mögliche Dokumententypen:\n{type_list_text}\n\n"
        f"Antworte NUR mit dem JSON-Objekt: "
        f'{{\"dokumententyp_id\": <Zahl>, \"dokumententyp_name\": \"<Name>\"}}'
    )

    logger.info("Sende Dokumententyp-Anfrage an: %s (Typ: %s)", base, endpoint_type)

    raw_text = ""
    ki_stats: dict = {}

    # ─── LM Studio: über LMStudioClient (siehe _send_via_lmstudio oben) ─────────
    if endpoint_type == "lmstudio":
        raw_text, ki_stats, error = _send_via_lmstudio(
            images_b64, config, active_system_prompt, user_text
        )
        if error:
            return None, None, error, {}

    # ─── OpenAI-kompatibel: weiterhin Inline-httpx (kein Client für dieses Format) ─
    else:
        endpoint = base + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"

        content_parts: list[dict] = [{"type": "text", "text": user_text}]
        for data_url in images_b64:
            content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
        request_body = {
            "model": config.model_name,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "reasoning": reasoning_value,
            "stream": False,
            "messages": [
                {"role": "system", "content": active_system_prompt},
                {"role": "user", "content": content_parts},
            ],
        }
        del content_parts

        try:
            serialized_body = json.dumps(request_body).encode("utf-8")
            request_body.clear()

            _t_start = time.monotonic()
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.post(endpoint, content=serialized_body, headers=headers)
            _total_duration = time.monotonic() - _t_start
            del serialized_body

            if response.status_code != 200:
                raw_text = f"KI-Fehler: HTTP {response.status_code}"
                logger.error("Dokumententyp-API Fehler (HTTP %d)", response.status_code)
                return None, None, raw_text, {}

            try:
                response_data = json.loads(response.content)
                raw_text = (
                    response_data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content") or ""
                )
                usage = response_data.get("usage") or {}
                details = usage.get("completion_tokens_details") or {}
                ki_stats = {
                    "input_tokens":        usage.get("prompt_tokens"),
                    "output_tokens":       usage.get("completion_tokens"),
                    "reasoning_tokens":    details.get("reasoning_tokens"),
                    "tokens_per_second":   None,
                    "time_to_first_token": None,
                    "total_duration":      _total_duration,
                }
            except Exception as parse_exc:
                raw_text = f"Antwort-Parse-Fehler: {parse_exc}"
                logger.error("Fehler beim Parsen der Dokumententyp-Antwort: %s", parse_exc)
                return None, None, raw_text, {}

        except httpx.TimeoutException as exc:
            raw_text = f"KI-Timeout: {exc}"
            logger.error("Dokumententyp-API Timeout: %s", exc)
            return None, None, raw_text, {}
        except httpx.ConnectError as exc:
            raw_text = f"KI-Verbindungsfehler: {exc}"
            logger.error("Dokumententyp-API Verbindungsfehler: %s", exc)
            return None, None, raw_text, {}
        except Exception as exc:
            raw_text = f"Unerwarteter KI-Fehler: {exc}"
            logger.exception("Unerwarteter Fehler bei Dokumententyp-Erkennung: %s", exc)
            return None, None, raw_text, {}

    # JSON parsen
    try:
        parsed = _parse_json_response(raw_text)
    except Exception:
        logger.warning("Dokumententyp-Antwort nicht als JSON parsbar: %s", raw_text[:200])
        return None, None, raw_text, ki_stats

    type_id = parsed.get("dokumententyp_id")
    type_name = parsed.get("dokumententyp_name")

    # Validierung: type_id muss eine gültige ID sein
    valid_ids = {dt["id"] for dt in document_types}
    if type_id is not None:
        try:
            type_id = int(type_id)
            if type_id not in valid_ids:
                logger.warning("Unbekannte Dokumententyp-ID %d — ignoriert", type_id)
                type_id = None
                type_name = None
        except (ValueError, TypeError):
            type_id = None
            type_name = None

    logger.info("Dokumententyp erkannt: ID=%s, Name=%s", type_id, type_name)
    return type_id, type_name, raw_text, ki_stats


def _normalize_decimal_commas(obj):
    """
    Normalisiert Dezimalkommas in Zahlenwerten rekursiv im gesamten geparsten JSON.

    Wandelt Strings wie "79,99" → 79.99, "1.234,56" → 1234.56,
    und auch "719,99 €" / "€ 719,99" → 719.99 um (Währungssymbol wird ignoriert).
    Freitexte wie "Musterstraße 1, Ort" bleiben unberührt.
    """
    if isinstance(obj, dict):
        return {k: _normalize_decimal_commas(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_decimal_commas(item) for item in obj]
    if isinstance(obj, str):
        s = obj.strip()
        # Währungssymbole und Leerzeichen entfernen (€, $, £, ¥)
        cleaned = re.sub(r'[€$£¥\s]', '', s)
        # Muster: optional Tausender-Trennpunkte, dann Komma + 1–2 Dezimalstellen
        # Beispiele: "79,99" | "1.234,56" | "719,99 €" (nach Bereinigung)
        # Kein Match: "Muster,Text" | "Straße 1, Ort"
        if _DECIMAL_COMMA_RE.match(cleaned):
            try:
                return float(cleaned.replace(".", "").replace(",", "."))
            except ValueError:
                pass
    return obj


_DECIMAL_COMMA_RE = re.compile(r'^\d{1,3}(?:\.\d{3})*,\d{1,2}$')


def _parse_json_response(raw_text: str) -> dict:
    """
    Extrahiert JSON aus der KI-Antwort.

    Versucht zunächst direktes Parsing. Falls die KI Markdown-Blöcke
    (```json ... ```) zurückgibt, wird der JSON-Teil herausgefiltert.

    Returns:
        Geparste Dict-Struktur oder leeres Dict bei Fehlern.
    """
    # Versuch 1: Direkt als JSON parsen
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # Versuch 2: JSON aus Markdown-Codeblock extrahieren
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Versuch 3: Erstes { ... } in der Antwort suchen
    match = re.search(r"\{[\s\S]*\}", raw_text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("KI-Antwort konnte nicht als JSON geparst werden")
    return {}


def _str(val) -> str | None:
    """Gibt None zurück bei leeren Strings, sonst den getrimmten Wert."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _date(val) -> str | None:
    """
    Normalisiert ein Datum auf ISO-Format (YYYY-MM-DD) oder gibt None zurück.
    Akzeptiert: "2024-01-15", "15.01.2024", "01/15/2024".
    Unbekannte Formate → None (verhindert DB-Fehler durch ungültige Strings).
    """
    s = _str(val)
    if s is None:
        return None
    # Bereits ISO
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    # Deutsches Format DD.MM.YYYY
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', s)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    # US-Format MM/DD/YYYY
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
    if m:
        return f"{m.group(3)}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    logger.warning("Unbekanntes Datumsformat ignoriert: '%s'", s)
    return None


def _num(val) -> float | None:
    """Konvertiert einen Wert in float, normalisiert europäische Formate."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(" ", "")
    # Europäisches Format: "1.234,56" → "1234.56"
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _map_new_format(data: dict) -> tuple[dict, list[dict]]:
    """
    Mappt das neue verschachtelte KI-JSON-Format auf die flachen DB-Felder.
    Gibt (extracted_fields, order_positions) zurück.
    """
    lieferant = data.get("lieferant") or {}
    anschrift = lieferant.get("anschrift") or {}
    bank = lieferant.get("bankverbindung") or {}
    rechnung = data.get("rechnungsdaten") or {}
    zahlung = data.get("zahlungsinformationen") or {}
    skonto = zahlung.get("skonto") or {}

    # Anschrift zusammensetzen
    adress_parts = [
        _str(anschrift.get("strasse")),
        " ".join(filter(None, [_str(anschrift.get("plz")), _str(anschrift.get("ort"))])) or None,
        _str(anschrift.get("land")),
    ]
    supplier_address = "\n".join(p for p in adress_parts if p) or None

    extracted_fields = {
        "supplier_name":      _str(lieferant.get("name")),
        "supplier_address":   supplier_address,
        "hrb_number":         _str(lieferant.get("hrb_nummer")),
        "tax_number":         _str(lieferant.get("steuernummer")),
        "vat_id":             _str(lieferant.get("ust_id_nr")),
        "bank_name":          _str(bank.get("bank_name")),
        "iban":               _str(bank.get("iban")),
        "bic":                _str(bank.get("bic")),
        "supplier_street":    _str(anschrift.get("strasse")),
        "supplier_zip":       _str(anschrift.get("plz")),
        "supplier_city":      _str(anschrift.get("ort")),
        "customer_number":    _str(rechnung.get("kundennummer")),
        "order_number":       _str(rechnung.get("bestellnummer")),
        "invoice_number":     _str(rechnung.get("rechnungsnummer")),
        "invoice_date":       _date(rechnung.get("rechnungsdatum")),
        "due_date":           _date(rechnung.get("faelligkeit")),
        "total_amount":       _num(zahlung.get("gesamtbetrag_brutto")),
        "discount_amount":    None,  # nicht im neuen Format vorhanden
        "cash_discount_amount": _num(skonto.get("betrag")),
        "payment_terms":      _str(zahlung.get("zahlungsbedingungen")),
    }

    # Positionen mappen
    order_positions = []
    for pos in (data.get("positionen") or []):
        nachlass = pos.get("preisnachlass") or {}
        # Preisnachlass als lesbaren String zusammenfassen
        discount_parts = []
        if nachlass.get("betrag") is not None:
            discount_parts.append(f"{nachlass['betrag']} {pos.get('waehrung', 'EUR')}")
        if nachlass.get("prozent") is not None:
            discount_parts.append(f"{nachlass['prozent']}%")
        if nachlass.get("bezeichnung"):
            discount_parts.append(str(nachlass["bezeichnung"]))
        discount_str = " / ".join(discount_parts) if discount_parts else None

        order_positions.append({
            "product_description": _str(pos.get("artikelbezeichnung")),
            "article_number":      _str(pos.get("artikelnummer_lieferant")),
            "quantity":            _num(pos.get("menge")),
            "unit":                _str(pos.get("mengeneinheit")),
            "unit_price":          _num(pos.get("einzelpreis")),
            "total_price":         _num(pos.get("gesamtpreis")),
            "discount":            discount_str,
        })

    return extracted_fields, order_positions


def _clean_flat_fields(data: dict) -> dict:
    """
    Bereinigt das alte flache KI-Format (Rückwärtskompatibilität).
    - Leere Strings → None
    - Zahlenfelder: Kommas durch Punkte ersetzen
    - Datumsfelder: auf ISO-Format normalisieren
    - Nur bekannte Felder durchlassen
    """
    allowed_fields = {
        "supplier_name", "supplier_address", "hrb_number", "tax_number",
        "vat_id", "bank_name", "iban", "bic", "customer_number",
        "invoice_number", "invoice_date", "due_date", "total_amount",
        "discount_amount", "cash_discount_amount", "payment_terms",
    }
    date_fields = {"invoice_date", "due_date"}
    cleaned = {}
    for key in allowed_fields:
        value = data.get(key)
        if isinstance(value, str) and not value.strip():
            value = None
        elif key in date_fields:
            value = _date(value)
        elif isinstance(value, str) and key.endswith(("_amount",)):
            value = _num(value)
        cleaned[key] = value
    return cleaned
