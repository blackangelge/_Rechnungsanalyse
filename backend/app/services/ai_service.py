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
from app.services.ai_reasoning import build_reasoning_param
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

    active_system_prompt = with_unit_rules(system_prompt_text)
    endpoint_type = getattr(config, "endpoint_type", "openai") or "openai"
    base = config.api_url.rstrip("/")
    reasoning = getattr(config, "reasoning", "off") or "off"

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
            "stream": False,
            "messages": [
                {"role": "system", "content": active_system_prompt},
                {"role": "user", "content": content_parts},
            ],
        }
        reasoning_param = build_reasoning_param(reasoning)
        if reasoning_param is not None:
            request_body["reasoning"] = reasoning_param
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


UNIT_RULES = (
    "\n\nZUSATZREGELN ZU MENGEN, EINHEITEN, PREISBASIS UND NEBENKOSTEN (immer beachten):\n"
    "- Übernimm `menge` und `mengeneinheit` EXAKT so, wie sie in der Mengenspalte der Rechnung stehen. "
    "Rechne nichts um (z.B. Spalte \"Menge / kg\" mit \"25.880,00\" → menge 25880, mengeneinheit \"kg\"; "
    "nicht 25,88 und nicht \"t\").\n"
    "- Beachte deutsche Zahlen: \"25.880,00\" = 25880 (Punkt = Tausender, Komma = Dezimal).\n"
    "- Gib je Position zusätzlich `preiseinheit` an: die Einheit, auf die sich der Einzelpreis bezieht "
    "(z.B. \"t\" bei \"407,60 /t\", \"kg\" bei \"0,41 €/kg\"). Steht keine Preiseinheit da, lass sie leer.\n"
    "- `einzelpreis` ist der Preis wie gedruckt (bezogen auf die Preiseinheit), `gesamtpreis` der gedruckte Positionsbetrag.\n"
    "- PREISBASIS `preis_je_menge` (Zahl): Gilt der Einzelpreis nicht für 1 Einheit, sondern für mehrere, trage hier diese Zahl ein "
    "(\"90,00 € je 100 Stück\", \"Preis pro 1000\", \"€/100 St.\" → 100 bzw. 1000). Das steht manchmal in einer eigenen Spalte, "
    "manchmal nur im Artikeltext (\"Verkaufseinheit = 100 Stück\", \"VE 100\", \"VPE: 250\") oder in der Kopfzeile — suche an allen drei Stellen. "
    "Prüfe es mit der Rechnung: menge ÷ preis_je_menge × einzelpreis muss den Positionsbetrag ergeben "
    "(4400 Stück, 90,00 €, je 100 → 4400 ÷ 100 × 90 = 3960). Gilt der Preis je einzelne Einheit, lass `preis_je_menge` leer (oder 1).\n"
    "- VERPACKUNG `verpackungsinhalt` (Zahl) und `verpackungsinhalt_einheit` (Text): Wird in Verpackungs-/Verkaufseinheiten verkauft "
    "(VPE, VE, Pack, Karton, Gebinde, Sack, Palette …) und ist der Inhalt je Einheit erkennbar, trage ihn ein "
    "(\"44 VPE à 100 Stück\" → menge 44, mengeneinheit \"VPE\", verpackungsinhalt 100, verpackungsinhalt_einheit \"Stück\"). "
    "Sonst leer lassen. Rechne `menge` nie mit dem Inhalt um.\n"
    "- EINHEITENTYP `mengeneinheit_typ`: genau einer von stueck, verpackung, gewicht, volumen, laenge, flaeche, pauschal, dienstleistung, sonstige "
    "(Schrauben in Stück = stueck; VPE/Karton/Pack = verpackung; kg/t = gewicht; l/hl = volumen; m = laenge; m² = flaeche; "
    "Pauschalbeträge wie Fracht = pauschal; Arbeitszeit/Stunden = dienstleistung).\n"
    "- NEBENKOSTEN ALS POSITION: Stehen Versand-, Fracht-, Verpackungs-, Pfand-, Mindermengen- oder Energiezuschläge nur in der Summenzeile "
    "(nicht als eigene Zeile in der Positionstabelle), lege dafür eine eigene Position an: `artikelbezeichnung` z.B. \"Versandkosten\", "
    "`menge` 1, `mengeneinheit` \"pauschal\", `mengeneinheit_typ` \"pauschal\", `einzelpreis` = `gesamtpreis` = der Betrag (netto, ohne Steuer), "
    "`steuersatz` des Zuschlags. So stimmt die Summe der Positionen mit dem Nettobetrag überein. Erfinde nichts: nur Beträge, die auf der Rechnung stehen."
)

# Einheitentypen, die gespeichert werden (`order_positions.unit_type`); Synonyme der KI werden darauf abgebildet
UNIT_TYPES = ("stueck", "verpackung", "gewicht", "volumen", "laenge", "flaeche", "pauschal", "dienstleistung", "sonstige")
_UNIT_TYPE_ALIASES = {
    "stück": "stueck", "stuck": "stueck", "piece": "stueck", "pieces": "stueck", "stk": "stueck",
    "packung": "verpackung", "vpe": "verpackung", "ve": "verpackung", "pack": "verpackung", "verpackungseinheit": "verpackung",
    "gewichtseinheit": "gewicht", "masse": "gewicht", "weight": "gewicht",
    "volume": "volumen", "länge": "laenge", "length": "laenge", "fläche": "flaeche", "area": "flaeche",
    "pauschale": "pauschal", "lump_sum": "pauschal", "service": "dienstleistung", "leistung": "dienstleistung",
    "other": "sonstige", "sonstiges": "sonstige",
}


def _unit_type(val) -> str | None:
    text = _str(val)
    if not text:
        return None
    key = text.strip().lower()
    key = _UNIT_TYPE_ALIASES.get(key, key)
    return key if key in UNIT_TYPES else None


_BASIS_TEXT_RE = re.compile(r"^\s*(?:je|pro|per|/|€\s*/)?\s*(\d+(?:[.,]\d+)?)\s*(?:st\.?|stk\.?|stück|stueck|stuck|pcs\.?|einheiten|ve|vpe)?\s*$", re.IGNORECASE)


def _price_basis(pos: dict) -> float | None:
    """Preisbasis einer Position: `preis_je_menge`, sonst eine reine Zählangabe in `preiseinheit` („100 Stück“, „je 100“). >1 oder None."""
    n = _num(pos.get("preis_je_menge"))
    if n is None:
        m = _BASIS_TEXT_RE.match(str(pos.get("preiseinheit") or ""))
        n = _num(m.group(1)) if m else None
    return n if n is not None and n > 0 and n != 1 else None


# Standard-Antwortformat für Extraktionsprompts in Workflows (Prompt-Typ 3 „Antwortformat“ kann es ersetzen).
# Genau diese Struktur liest `extract_invoice_data` (`_map_new_format`) — fehlt sie im Prompt, kann die Antwort nicht verarbeitet werden.
DEFAULT_RESPONSE_FORMAT = """\
ANTWORTFORMAT (zwingend): Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Objekt — kein Text davor oder danach, keine Markdown-Zeichen.
Unbekannte Werte: null. Zahlen als Zahl mit Punkt als Dezimaltrenner (1234.56), Datumsangaben als YYYY-MM-DD. `positionen` ist immer eine Liste (leer, wenn es keine gibt).
{
  "lieferant": {
    "name": "...",
    "anschrift": { "strasse": "...", "plz": "...", "ort": "...", "land": "..." },
    "hrb_nummer": "...", "steuernummer": "...", "ust_id_nr": "...",
    "bankverbindung": { "bank_name": "...", "iban": "...", "bic": "..." }
  },
  "rechnungsdaten": { "rechnungsnummer": "...", "rechnungsdatum": "YYYY-MM-DD", "faelligkeit": "YYYY-MM-DD", "kundennummer": "..." },
  "positionen": [
    {
      "position_nr": 1, "artikelbezeichnung": "...", "artikelnummer_lieferant": "...",
      "menge": 1, "mengeneinheit": "Stück", "einzelpreis": 0.0, "gesamtpreis": 0.0, "waehrung": "EUR", "steuersatz": 19.0,
      "preiseinheit": null, "preis_je_menge": null, "verpackungsinhalt": null, "verpackungsinhalt_einheit": null, "mengeneinheit_typ": "stueck",
      "preisnachlass": { "betrag": null, "prozent": null, "bezeichnung": null }
    }
  ],
  "zahlungsinformationen": {
    "gesamtbetrag_netto": 0.0,
    "umsatzsteuer_zusammenfassung": [{ "steuersatz": 19.0, "nettobetrag": 0.0, "steuerbetrag": 0.0 }],
    "gesamtbetrag_brutto": 0.0, "waehrung": "EUR",
    "skonto": { "prozent": null, "betrag": null, "frist_tage": null }, "zahlungsbedingungen": "..."
  }
}
"""


def has_response_format(prompt: str | None) -> bool:
    """Beschreibt der Prompt die erwartete JSON-Struktur schon? (Erkennung an den Schlüsselnamen der Positionen)"""
    low = (prompt or "").lower()
    return "artikelbezeichnung" in low and "positionen" in low


def with_response_format(prompt: str | None, format_text: str | None = None) -> str:
    """
    Hängt das Antwortformat an einen Extraktionsprompt:
      • `format_text` (ein Prompt vom Typ „Antwortformat“) wird angehängt, wenn angegeben;
      • sonst das Standardformat — aber nur, wenn der Prompt die Struktur nicht schon selbst beschreibt.
    """
    base = (prompt or "").rstrip()
    if format_text and format_text.strip():
        return f"{base}\n\n{format_text.strip()}"
    return base if has_response_format(base) else f"{base}\n\n{DEFAULT_RESPONSE_FORMAT}"



def with_unit_rules(prompt: str | None) -> str:
    """Hängt die Zusatzregeln (Einheiten, Preisbasis, Verpackung, Nebenkosten) einmalig an den Extraktionsprompt — nicht, wenn der Prompt `preis_je_menge` schon behandelt."""
    base = prompt or ""
    return base if "preis_je_menge" in base.lower() else base + UNIT_RULES


def build_recheck_prompt(
    base_prompt: str | None,
    issues: list[str],
    previous_raw: str | None,
) -> str:
    """
    Baut den Systemprompt für die zweite KI-Abfrage (Plausibilitäts-Nachprüfung):
    der normale Extraktionsprompt plus die gefundenen Auffälligkeiten.
    """
    parts = [base_prompt or "", "", "PRÜFHINWEIS — NACHPRÜFUNG:"]
    parts.append(
        "Eine automatische Plausibilitätsprüfung hat bei einer ersten Auslesung dieser "
        "Rechnung Unstimmigkeiten gefunden. Lies die Rechnung erneut sorgfältig aus "
        "und achte besonders auf Mengeneinheiten (t/kg/g, l/ml/hl, Stück, Preis je 100 "
        "oder je 1000) sowie auf Rechnungssummen. Übernimm Preise und Summen exakt so, "
        "wie sie auf der Rechnung stehen. Antworte im gleichen JSON-Format wie zuvor."
    )
    parts.append("Gefundene Auffälligkeiten:")
    parts.extend(f"- {msg}" for msg in issues[:20])
    if previous_raw:
        parts.append("")
        parts.append("Erste Auslesung (zur Orientierung, kann fehlerhaft sein):")
        parts.append(previous_raw[:6000])
    return "\n".join(parts)


def recheck_invoice_data(
    images_b64: list[str],
    config,
    base_prompt: str | None,
    issues: list[str],
    previous_raw: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, dict, str]:
    """
    Zweite KI-Abfrage nach fehlgeschlagener Plausibilitätsprüfung.

    SYNCHRON (via asyncio.to_thread aufrufen), wirft nie eine Exception.
    Returns: (extracted_fields, order_positions, raw_response, ki_stats, prompt_used)
    """
    prompt = build_recheck_prompt(base_prompt, issues, previous_raw)
    fields, positions, raw, stats = extract_invoice_data(images_b64, config, prompt)
    return fields, positions, raw, stats, prompt


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
            "stream": False,
            "messages": [
                {"role": "system", "content": active_system_prompt},
                {"role": "user", "content": content_parts},
            ],
        }
        reasoning_param = build_reasoning_param(reasoning)
        if reasoning_param is not None:
            request_body["reasoning"] = reasoning_param
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


def _sum_tax(summary) -> float | None:
    """Summe der Steuerbeträge aus der USt-Zusammenfassung (None, wenn keine Beträge vorhanden)."""
    total, found = 0.0, False
    for row in summary or []:
        if not isinstance(row, dict):
            continue
        amount = _num(row.get("steuerbetrag"))
        if amount is not None:
            total += amount
            found = True
    return round(total, 2) if found else None


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
        "total_amount_netto": _num(zahlung.get("gesamtbetrag_netto")),
        "total_tax_value":    _sum_tax(zahlung.get("umsatzsteuer_zusammenfassung")),
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
            "price_quantity":      _price_basis(pos),
            "pack_size":           (lambda n: n if n is not None and n > 0 else None)(_num(pos.get("verpackungsinhalt"))),
            "pack_unit":           _str(pos.get("verpackungsinhalt_einheit")),
            "unit_type":           _unit_type(pos.get("mengeneinheit_typ")),
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
