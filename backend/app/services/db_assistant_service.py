"""
KI-Assistent für die Datenbank: Rückfragen, Recherche, Auswertung — und Änderungs-VORSCHLÄGE.

Funktioniert mit jedem OpenAI-kompatiblen Chat-Endpunkt (auch LM Studio / Ollama), ohne dass das Modell
„Tool Calling“ beherrschen muss: Die KI antwortet in jedem Schritt mit genau einem JSON-Objekt
  {"tool": "sql", "query": "SELECT …"}                                        → Abfrage (nur lesen), Ergebnis kommt zurück
  {"tool": "propose_update", "table": …, "id": …, "changes": {…}, "reason": …} → Änderungsvorschlag
  {"tool": "propose_reanalysis", "query": "SELECT id FROM documents …", "reason": …} → Vorschlag: Belege erneut zur KI-Analyse senden
  {"tool": "final", "answer": "…"}                                            → Antwort an den Nutzer
Die Schleife führt Abfragen aus und gibt die Ergebnisse zurück, bis die KI „final“ liefert (max. MAX_STEPS).

Die KI SCHREIBT NIE selbst und stößt nichts selbst an: Änderungen und erneute Analysen sind nur Vorschläge, die der Nutzer in der Oberfläche freigeben muss
(`routers/db_assistant.py::apply`). Abfragen laufen in einer schreibgeschützten Transaktion (siehe db_tools).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.ai_clients import AIClients
from app.services import db_tools
from app.services.ai_reasoning import build_reasoning_param
from app.services.ai_url import build_ai_url

logger = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_REANALYSIS = 2000               # höchstens so viele Belege je Freigabe zur erneuten KI-Analyse
REQUEST_TIMEOUT_SECONDS = 600
MAX_HISTORY_CHARS = 60_000          # ältere Nachrichten werden gekürzt, damit der Kontext nicht überläuft

DOMAIN_NOTES = """\
Fachliches zur Datenbank (Rechnungsanalyse):
- import_batches = ein Import (Firma + Jahr); documents = ein Beleg (PDF) je Import; invoice_extractions = die von der KI gelesenen
  Rechnungsdaten (1:1 zum Beleg; vendor_id enthält den LIEFERANTENNAMEN als Text, keine Fremdschlüssel-ID; raw_response = KI-JSON);
  order_positions = die Positionen (Artikel) eines Belegs; vendor = Lieferanten-Stammdaten (Verbindung zum Beleg nur über den Namen).
- documents.soft_deleted = true → Beleg gilt als gelöscht (nicht mitzählen, außer ausdrücklich gewünscht). import_batches.hidden_at != NULL → ausgeblendeter Import.
- Gewichte: Menge/Einheit stehen teils in kg, teils in t (Tonnen). Preis je t = Gesamtpreis / (Menge in t). Umrechnung 1 t = 1000 kg.
- documents.status: pending, processing, done, error, waiting_capacity. invoice_extractions.validation_status: ok, corrected, ai_corrected, needs_review, manual_ok.
- Beträge: total_amount_brutto/-netto, order_positions.total_price (Positionspreis), unit_price_netto (Einzelpreis).
"""

PROTOCOL = """\
Du bist ein Datenbank-Assistent. Du hilfst beim Suchen, Recherchieren und Auswerten in einer PostgreSQL-Datenbank (Rechnungsdaten).
Antworte IMMER mit genau EINEM JSON-Objekt und sonst nichts (kein Fließtext davor oder danach). Erlaubt sind:

1. Abfrage (nur lesen):      {"tool": "sql", "query": "SELECT ... LIMIT 50"}
2. Änderungsvorschlag:       {"tool": "propose_update", "table": "order_positions", "id": 123, "changes": {"quantity": 25.5, "unit": "t"}, "reason": "kurze Begründung"}
3. Belege erneut zur KI-Analyse vorschlagen:
   {"tool": "propose_reanalysis", "query": "SELECT d.id FROM documents d WHERE ...", "reason": "kurze Begründung"}
   (die Abfrage liefert genau EINE Spalte mit Beleg-IDs, höchstens 2000; alternativ {"tool": "propose_reanalysis", "document_ids": [1, 2, 3], "reason": "…"})
4. Endgültige Antwort:       {"tool": "final", "answer": "Antwort in Markdown, auf Deutsch"}

Regeln:
- Es ist nur EINE SELECT-/WITH-Abfrage pro Schritt erlaubt (kein ';'). Ergebnisse sind auf 200 Zeilen begrenzt — nutze LIMIT, Aggregate (COUNT, SUM, AVG, GROUP BY) und gezielte Spalten statt SELECT *.
- Die Spalte raw_response ist sehr groß: nur gezielt lesen (z.B. mit LEFT(raw_response, 500) oder JSON-Operatoren), nicht komplett.
- Du kannst Daten NIE selbst ändern. Wenn etwas geändert werden soll, schlage es mit propose_update vor (je Zeile ein Vorschlag, mit der Spalte `id` der Zeile);
  der Nutzer gibt jeden Vorschlag in der Oberfläche frei. Prüfe vorher per Abfrage, dass die Zeile existiert und der Wert wirklich falsch ist. Ändere nie ohne Auftrag des Nutzers.
- Eine erneute KI-Analyse kostet KI-Zeit und ersetzt die gespeicherte Auslesung des Belegs (der alte Stand bleibt in der Analyse-Historie). Schlage sie nur auf ausdrücklichen Wunsch des Nutzers vor,
  zähle die Belege vorher per Abfrage und nimm gelöschte (soft_deleted) sowie gerade verarbeitete Belege (status = 'processing') nicht auf. Der Nutzer gibt den Vorschlag frei.
- Tabellen users/user_sessions und Zugangsdaten sind nicht zugänglich. Erfinde keine Daten: stütze jede Aussage auf Abfrageergebnisse, nenne Beleg-/Positions-IDs.
- Wenn die Frage unklar ist, stelle in "final" eine kurze Rückfrage.
- Antworte nach einem Fehler einer Abfrage nicht mit "final", sondern korrigiere die Abfrage.
"""


def build_system_prompt() -> str:
    return f"{PROTOCOL}\nDatenbankschema (Tabelle(Spalte typ …), PK = Primärschlüssel, →x.y = Fremdschlüssel):\n{db_tools.schema_text()}\n\n{DOMAIN_NOTES}"


# ─── KI-Aufruf ────────────────────────────────────────────────────────────────

def _chat(config: AIClients, messages: list[dict]) -> tuple[str, dict]:
    """Ein Chat-Aufruf (nur Text). Gibt (Antworttext, Token-Statistik) zurück; wirft RuntimeError mit verständlicher Meldung."""
    base = build_ai_url(config.ip_address, config.port)
    # LM Studio bietet unter /v1/chat/completions ebenfalls die OpenAI-kompatible Schnittstelle an
    endpoint = base.rstrip("/") + "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    body: dict[str, Any] = {
        "model": config.model_name,
        "max_tokens": config.max_tokens,
        "temperature": min(config.temperature, 0.3),
        "stream": False,
        "messages": messages,
    }
    if (getattr(config, "endpoint_type", "openai") or "openai") == "openai":
        reasoning = build_reasoning_param(getattr(config, "reasoning", "off") or "off")
        if reasoning is not None:
            body["reasoning"] = reasoning
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.post(endpoint, json=body, headers=headers)
    except httpx.TimeoutException:
        raise RuntimeError(f"KI-Timeout nach {REQUEST_TIMEOUT_SECONDS}s") from None
    except httpx.HTTPError as exc:
        raise RuntimeError(f"KI nicht erreichbar: {exc}") from None
    if resp.status_code != 200:
        detail = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"KI-Fehler: HTTP {resp.status_code} {detail}")
    try:
        data = resp.json()
        text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content") or ""
    except Exception as exc:
        raise RuntimeError(f"Antwort der KI nicht lesbar: {exc}") from None
    usage = data.get("usage") or {}
    return text, {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "seconds": round(time.monotonic() - t0, 1),
    }


# ─── Antwort lesen ────────────────────────────────────────────────────────────

def parse_action(text: str) -> dict | None:
    """Findet das erste JSON-Objekt in der Antwort (auch in ```json-Blöcken oder nach <think>…</think>)."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, flags=re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    start = t.find("{")
    if start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(t[start:i + 1])
                    break
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _clip_history(messages: list[dict]) -> list[dict]:
    """Nur user/assistant-Texte der bisherigen Unterhaltung; zu lange Historie wird von vorn gekürzt."""
    clean = [{"role": m["role"], "content": str(m.get("content", ""))} for m in messages if m.get("role") in ("user", "assistant")]
    total = 0
    kept: list[dict] = []
    for m in reversed(clean):
        total += len(m["content"])
        if total > MAX_HISTORY_CHARS and kept:
            break
        kept.append(m)
    return list(reversed(kept))


def _result_message(res: dict) -> str:
    head = f"Ergebnis ({res['row_count']} Zeilen{', gekürzt auf die ersten 200' if res['truncated'] else ''}):\n"
    return head + json.dumps({"columns": res["columns"], "rows": res["rows"]}, ensure_ascii=False)


def build_reanalysis_proposal(db: Session, action: dict) -> dict:
    """
    Prüft einen Vorschlag „Belege erneut analysieren“ (IDs aus `document_ids` oder aus einer SELECT-Abfrage mit einer ID-Spalte).
    Es wird nichts gestartet. Rückgabe: {document_ids, count, skipped, truncated, sample, reason}.
    """
    ids: list[int] = []
    truncated = False
    if isinstance(action.get("document_ids"), list):
        raw_ids = action["document_ids"]
    elif action.get("query"):
        res = db_tools.run_readonly_sql(db, str(action["query"]), max_rows=MAX_REANALYSIS, timeout_s=30)
        if len(res["columns"]) != 1:
            raise db_tools.DbToolError("Die Abfrage muss genau EINE Spalte mit Beleg-IDs liefern (z. B. SELECT d.id FROM documents d …).")
        raw_ids = [r[0] for r in res["rows"]]
        truncated = res["truncated"]
    else:
        raise db_tools.DbToolError("Angabe fehlt: 'query' (SELECT mit Beleg-IDs) oder 'document_ids'.")
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            raise db_tools.DbToolError(f"Keine gültige Beleg-ID: {v!r}") from None
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_REANALYSIS:
        ids, truncated = ids[:MAX_REANALYSIS], True
    valid = filter_reanalysis_ids(db, ids)
    if not valid:
        raise db_tools.DbToolError("Keiner der Belege kommt in Frage (nicht vorhanden, gelöscht oder gerade in Verarbeitung).")
    from app.models.document import Document
    sample = [
        {"id": d.id, "filename": d.original_filename, "status": d.status}
        for d in db.query(Document).filter(Document.id.in_(valid[:10])).order_by(Document.id).all()
    ]
    return {
        "document_ids": valid, "count": len(valid), "skipped": len(ids) - len(valid), "truncated": truncated,
        "sample": sample, "reason": str(action.get("reason") or ""),
    }


def filter_reanalysis_ids(db: Session, ids: list[int]) -> list[int]:
    """Nur vorhandene, nicht gelöschte Belege, die nicht gerade verarbeitet werden (Reihenfolge bleibt)."""
    from app.models.document import Document
    ok: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ok.update(r[0] for r in db.query(Document.id).filter(
            Document.id.in_(chunk), Document.soft_deleted == False, Document.status != "processing"))  # noqa: E712
    return [i for i in ids if i in ok]


# ─── Hauptschleife ────────────────────────────────────────────────────────────

def run_chat(db: Session, config: AIClients, history: list[dict], allow_proposals: bool = True) -> dict:
    """
    Führt eine Unterhaltungsrunde aus. Rückgabe:
      {"answer": str, "steps": [{"sql", "ok", "rows", "error"}], "proposals": [...], "reanalysis": [...], "usage": {...}}
    """
    messages: list[dict] = [{"role": "system", "content": build_system_prompt()}] + _clip_history(history)
    steps: list[dict] = []
    proposals: list[dict] = []
    reanalysis: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "seconds": 0.0, "calls": 0}
    answer: str | None = None

    for _ in range(MAX_STEPS):
        text, stats = _chat(config, messages)
        usage["calls"] += 1
        usage["input_tokens"] += stats.get("input_tokens") or 0
        usage["output_tokens"] += stats.get("output_tokens") or 0
        usage["seconds"] = round(usage["seconds"] + (stats.get("seconds") or 0), 1)
        action = parse_action(text)
        messages.append({"role": "assistant", "content": text})

        if action is None:
            # Kein JSON: als Antwort behandeln (viele kleine Modelle halten das Format nicht strikt ein)
            answer = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip() or "(leere Antwort)"
            break

        tool = str(action.get("tool", "")).lower()
        if tool == "final" or ("answer" in action and not tool):
            answer = str(action.get("answer") or "").strip() or "(leere Antwort)"
            break

        if tool == "sql":
            query = str(action.get("query") or "")
            try:
                res = db_tools.run_readonly_sql(db, query)
                steps.append({"sql": query, "ok": True, "row_count": res["row_count"], "columns": res["columns"],
                              "rows": res["rows"][:20], "truncated": res["truncated"]})
                messages.append({"role": "user", "content": _result_message(res)})
            except db_tools.DbToolError as exc:
                steps.append({"sql": query, "ok": False, "error": str(exc)})
                messages.append({"role": "user", "content": f"FEHLER: {exc}\nKorrigiere die Abfrage."})
            continue

        if tool == "propose_update":
            if not allow_proposals:
                messages.append({"role": "user", "content": "Änderungsvorschläge sind ausgeschaltet. Antworte mit tool=final."})
                continue
            try:
                table = str(action.get("table") or "")
                pk = action.get("id")
                changes = action.get("changes")
                if not isinstance(changes, dict) or not changes:
                    raise db_tools.DbToolError("'changes' muss ein Objekt mit mindestens einem Feld sein.")
                tbl = db_tools.get_table(table)
                current = db_tools.get_row(db, table, pk)
                for name, value in changes.items():      # vorab prüfen (Spalte vorhanden, Typ passt), noch nichts schreiben
                    db_tools._coerce(tbl, name, value)
                proposals.append({
                    "table": table, "id": pk, "changes": changes, "reason": str(action.get("reason") or ""),
                    "current": {k: current.get(k) for k in changes},
                })
                messages.append({"role": "user", "content": f"Vorschlag für {table} #{pk} gespeichert; er wartet auf die Freigabe durch den Nutzer. "
                                                            "Mache mit weiteren Vorschlägen weiter oder antworte mit tool=final."})
            except (db_tools.DbToolError, LookupError) as exc:
                messages.append({"role": "user", "content": f"FEHLER beim Vorschlag: {exc}"})
            continue

        if tool == "propose_reanalysis":
            if not allow_proposals:
                messages.append({"role": "user", "content": "Vorschläge sind ausgeschaltet. Antworte mit tool=final."})
                continue
            try:
                prop = build_reanalysis_proposal(db, action)
                reanalysis.append(prop)
                messages.append({"role": "user", "content": f"Vorschlag für {prop['count']} Beleg(e) gespeichert ({prop['skipped']} nicht geeignet"
                                                            f"{', auf %d begrenzt' % MAX_REANALYSIS if prop['truncated'] else ''}); er wartet auf die Freigabe "
                                                            "durch den Nutzer. Antworte jetzt mit tool=final und fasse zusammen."})
            except db_tools.DbToolError as exc:
                messages.append({"role": "user", "content": f"FEHLER beim Vorschlag: {exc}"})
            continue

        messages.append({"role": "user", "content": 'Unbekanntes Werkzeug. Erlaubt: "sql", "propose_update", "propose_reanalysis", "final".'})

    if answer is None:
        answer = "Ich konnte in der erlaubten Anzahl von Schritten keine endgültige Antwort finden. Bitte formuliere die Frage enger oder teile sie auf."
    return {"answer": answer, "steps": steps, "proposals": proposals, "reanalysis": reanalysis, "usage": usage}
