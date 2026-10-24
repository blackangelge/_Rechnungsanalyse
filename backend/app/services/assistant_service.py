"""
KI-Assistent (Chat) mit Werkzeugen — Nachfolger des reinen Daten-Assistenten.

Gleiches Grundprinzip wie `db_assistant_service`: Das Modell antwortet je Schritt mit genau EINEM JSON-Objekt (kein natives
Tool Calling nötig, läuft mit LM Studio / vLLM / Ollama / OpenRouter):
  {"tool": "<name>", "args": {…}, "reason": "…"}      Lesen → wird sofort ausgeführt, Ergebnis kommt zurück
                                                      Schreiben → wird als VORSCHLAG gespeichert (Freigabe durch den Nutzer)
  {"tool": "final", "answer": "…"}                    Antwort an den Nutzer
Werkzeuge: siehe `assistant_tools`. Bilder des Nutzers gehen als OpenAI-`image_url`-Teile an das Modell (sofern es Bilder versteht).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.ai_clients import AIClients
from app.services import assistant_tools as at
from app.services import db_assistant_service as legacy
from app.services import db_tools

logger = logging.getLogger(__name__)

MAX_STEPS = 12
MAX_RESULT_CHARS = 12_000          # so viel eines Werkzeug-Ergebnisses sieht die KI
PREVIEW_CHARS = 1_500              # so viel davon speichert/zeigt die Oberfläche
MAX_IMAGES_TO_MODEL = 4            # Bilder aus den letzten Nachrichten, die mitgeschickt werden (Kontext sparen)

PROTOCOL = """\
Du bist der Assistent der „Rechnungsanalyse“ (selbst gehostetes System zur KI-Auslesung von Rechnungen). Du kannst Daten recherchieren
und die Konfiguration des Systems lesen, ändern und erweitern: Einheiten und Gruppen, das Extraktionsschema (Entitäten, Felder, Beziehungen),
Export-Vorlagen, Workflows und Imports. Antworte auf Deutsch.

Antworte IMMER mit genau EINEM JSON-Objekt und sonst nichts (kein Text davor oder danach):
  Werkzeug aufrufen:  {"tool": "<name>", "args": {…}, "reason": "kurze Begründung"}
  Antwort geben:      {"tool": "final", "answer": "Antwort in Markdown"}
Auch ältere Kurzformen sind erlaubt: {"tool": "sql", "query": "…"}, {"tool": "propose_update", "table": …, "id": …, "changes": {…}, "reason": …},
{"tool": "propose_reanalysis", "query": "SELECT d.id …" | "document_ids": [...], "only_extract": true, "reason": …}.

Regeln:
- LESEN-Werkzeuge laufen sofort. Nutze sie, bevor du etwas vorschlägst: bestehende Gruppen/Einheiten, Schema, Vorlagen und Workflows erst ansehen, dann ändern.
- SCHREIBEN-Werkzeuge führen NICHTS selbst aus. Jeder Aufruf ist ein Vorschlag, den der Nutzer in der Oberfläche einzeln freigibt. Mehrere Vorschläge nacheinander
  sind möglich (sie werden in dieser Reihenfolge freigegeben, z. B. erst Entwurf anlegen, dann Felder, dann aktivieren). Verweise auf Neues per NAME (Gruppe „Masse“,
  Entität „position“), nicht per ID — IDs neuer Objekte kennst du erst nach der Freigabe.
- Schlage Änderungen nur vor, wenn der Nutzer sie wünscht. Wenn Angaben fehlen, frage in „final“ kurz nach. Nach dem Anlegen der Vorschläge antworte mit „final“ und fasse zusammen,
  was zur Freigabe bereitliegt.
- Das Schema ist versioniert: Änderungen gehen nur an einem Entwurf (create_schema_draft), danach activate_schema_draft. Aktive Versionen sind unveränderlich.
- Workflows: zuerst workflow_node_types und workflow_templates lesen; Ausgänge von „branch“ heißen true/false; es gibt keinen Standard-Workflow.
- Imports lesen die PDFs aus dem Import-Ordner (list_import_folders). Bei analyze_after_import ist ein Workflow Pflicht.
- Erfinde keine Daten oder IDs: stütze Aussagen auf Werkzeug-Ergebnisse. Nach einem Fehler korrigiere den Aufruf, antworte nicht sofort mit „final“.
- Bilder des Nutzers (Screenshots, Rechnungen) darfst du beschreiben und als Grundlage für Vorschläge nutzen.
- Tabellen users/user_sessions und Zugangsdaten sind nicht zugänglich.
"""


def build_system_prompt() -> str:
    return (f"{PROTOCOL}\nWerkzeuge:\n{at.tools_prompt()}\n\n"
            f"Datenbankschema für „sql“ (Tabelle(Spalte typ …), PK = Primärschlüssel, →x.y = Fremdschlüssel):\n{db_tools.schema_text()}\n\n{legacy.DOMAIN_NOTES}")


# ─── Verlauf → Modell-Nachrichten ────────────────────────────────────────────

def _proposal_note(p: dict) -> str:
    status = p.get("status", "pending")
    label = {"pending": "wartet auf Freigabe", "applied": "FREIGEGEBEN und ausgeführt", "dismissed": "vom Nutzer verworfen", "failed": "Ausführung fehlgeschlagen"}.get(status, status)
    out = f"- {p.get('summary') or p.get('tool')}: {label}"
    if p.get("result") is not None:
        out += f" → {json.dumps(p['result'], ensure_ascii=False)[:300]}"
    if p.get("error"):
        out += f" ({p['error']})"
    return out


def history_for_model(messages: list[dict]) -> list[dict]:
    """
    messages: [{role, content, images?, proposals?}]. Assistenten-Nachrichten bekommen den Stand ihrer Vorschläge angehängt,
    damit die KI nach Freigaben weiß, was passiert ist (inkl. neuer IDs). Nur die letzten Bilder werden mitgeschickt.
    """
    with_images = [i for i, m in enumerate(messages) if m.get("role") == "user" and m.get("images")]
    keep_img = set(with_images[-MAX_IMAGES_TO_MODEL:])
    out: list[dict] = []
    for i, m in enumerate(messages):
        role, text = m.get("role"), str(m.get("content") or "")
        if role not in ("user", "assistant"):
            continue
        if role == "assistant" and m.get("proposals"):
            text += "\n\n[Stand der Vorschläge]\n" + "\n".join(_proposal_note(p) for p in m["proposals"])
        if role == "user" and i in keep_img:
            parts: list[dict] = [{"type": "text", "text": text or "(Bild)"}]
            parts += [{"type": "image_url", "image_url": {"url": url}} for url in (m.get("images") or [])]
            out.append({"role": "user", "content": parts})
        else:
            if role == "user" and m.get("images"):
                text += f"\n[{len(m['images'])} Bild(er) angehängt — nicht mehr im Kontext]"
            out.append({"role": role, "content": text})
    return legacy._clip_history(out) if all(isinstance(x["content"], str) for x in out) else _clip_mixed(out)


def _clip_mixed(messages: list[dict]) -> list[dict]:
    """Wie `legacy._clip_history`, lässt aber Nachrichten mit Bildteilen unangetastet."""
    total, kept = 0, []
    for m in reversed(messages):
        total += len(m["content"]) if isinstance(m["content"], str) else 500
        if total > legacy.MAX_HISTORY_CHARS and kept:
            break
        kept.append(m)
    return list(reversed(kept))


# ─── Hauptschleife ───────────────────────────────────────────────────────────

def _short(obj: Any, limit: int) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f" … [gekürzt, {len(s)} Zeichen]"


def _normalize_action(action: dict) -> tuple[str, dict, str]:
    tool = str(action.get("tool", "")).lower()
    reason = str(action.get("reason") or "")
    args = action.get("args")
    if not isinstance(args, dict):
        args = {k: v for k, v in action.items() if k not in ("tool", "reason", "args")}
    return tool, args, reason


def run_chat(db: Session, config: AIClients, history: list[dict], allow_proposals: bool = True) -> dict:
    """
    history: Chat-Verlauf inkl. der neuen Nutzernachricht (letzte). Rückgabe:
      {"answer", "steps": [...], "proposals": [...], "usage": {input_tokens, output_tokens, seconds, calls}}
    """
    messages: list[dict] = [{"role": "system", "content": build_system_prompt()}] + history_for_model(history)
    steps: list[dict] = []
    proposals: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "seconds": 0.0, "calls": 0}
    answer: str | None = None

    def feedback(text: str) -> None:
        messages.append({"role": "user", "content": text})

    for _ in range(MAX_STEPS):
        text, stats = legacy._chat(config, messages)
        usage["calls"] += 1
        usage["input_tokens"] += stats.get("input_tokens") or 0
        usage["output_tokens"] += stats.get("output_tokens") or 0
        usage["seconds"] = round(usage["seconds"] + (stats.get("seconds") or 0), 1)
        action = legacy.parse_action(text)
        messages.append({"role": "assistant", "content": text})

        if action is None:       # kein JSON → als Antwort behandeln
            answer = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip() or "(leere Antwort)"
            break
        tool, args, reason = _normalize_action(action)
        if tool == "final" or ("answer" in action and not tool):
            answer = str(action.get("answer") or "").strip() or "(leere Antwort)"
            break

        # ── Kurzformen aus dem Daten-Assistenten ──
        if tool == "propose_update":
            if not allow_proposals:
                feedback("Vorschläge sind ausgeschaltet. Antworte mit tool=final.")
                continue
            try:
                table, pk, changes = str(args.get("table") or ""), args.get("id"), args.get("changes")
                if not isinstance(changes, dict) or not changes:
                    raise db_tools.DbToolError("'changes' muss ein Objekt mit mindestens einem Feld sein.")
                tbl = db_tools.get_table(table)
                current = db_tools.get_row(db, table, pk)
                for name, value in changes.items():
                    db_tools._coerce(tbl, name, value)
                proposals.append({
                    "id": uuid.uuid4().hex[:8], "tool": "propose_update", "args": {"table": table, "id": pk, "changes": changes},
                    "summary": f"{table} #{pk} ändern: " + ", ".join(f"{k} → {v}" for k, v in changes.items()),
                    "reason": reason, "current": {k: current.get(k) for k in changes}, "status": "pending",
                })
                feedback(f"Vorschlag für {table} #{pk} gespeichert (wartet auf Freigabe). Weiter oder tool=final.")
            except (db_tools.DbToolError, LookupError) as exc:
                feedback(f"FEHLER beim Vorschlag: {exc}")
            continue

        if tool == "propose_reanalysis":
            if not allow_proposals:
                feedback("Vorschläge sind ausgeschaltet. Antworte mit tool=final.")
                continue
            try:
                prop = legacy.build_reanalysis_proposal(db, {**args, "reason": reason or args.get("reason")})
                proposals.append({
                    "id": uuid.uuid4().hex[:8], "tool": "propose_reanalysis",
                    "args": {"document_ids": prop["document_ids"], "skip_type_detection": prop["skip_type_detection"]},
                    "summary": f"{prop['count']} Beleg(e) erneut zur KI-Analyse senden" + (" (nur neu auslesen)" if prop["skip_type_detection"] else ""),
                    "reason": prop["reason"], "reanalysis": {k: prop[k] for k in ("count", "skipped", "truncated", "sample")}, "status": "pending",
                })
                feedback(f"Vorschlag für {prop['count']} Beleg(e) gespeichert ({prop['skipped']} nicht geeignet). Der Nutzer wählt bei der Freigabe den Workflow. Antworte jetzt mit tool=final.")
            except db_tools.DbToolError as exc:
                feedback(f"FEHLER beim Vorschlag: {exc}")
            continue

        spec = at.TOOLS.get(tool)
        if spec is None:
            feedback(f'Unbekanntes Werkzeug „{tool}“. Erlaubt: {", ".join(list(at.TOOLS) + ["propose_update", "propose_reanalysis", "final"])}.')
            continue

        if spec.kind == "read":
            try:
                result = at.run_read(db, tool, args)
                body = _short(result, MAX_RESULT_CHARS)
                step: dict = {"tool": tool, "args": args, "ok": True, "preview": _short(result, PREVIEW_CHARS)}
                if tool == "sql" and isinstance(result, dict):
                    step.update(sql=str(args.get("query") or ""), row_count=result.get("row_count"), columns=result.get("columns"),
                                rows=(result.get("rows") or [])[:20], truncated=result.get("truncated"))
                    body = legacy._result_message(result)[:MAX_RESULT_CHARS]
                steps.append(step)
                feedback(f"Ergebnis von {tool}:\n{body}")
            except at.ToolError as exc:
                steps.append({"tool": tool, "args": args, "ok": False, "error": str(exc)})
                feedback(f"FEHLER bei {tool}: {exc}\nKorrigiere den Aufruf.")
            continue

        # ── Schreib-Werkzeug → Vorschlag ──
        if not allow_proposals:
            feedback("Vorschläge sind ausgeschaltet. Antworte mit tool=final.")
            continue
        proposals.append({
            "id": uuid.uuid4().hex[:8], "tool": tool, "args": args, "summary": at.summarize(tool, args),
            "reason": reason, "status": "pending",
        })
        feedback(f"Vorschlag „{at.summarize(tool, args)}“ gespeichert; er wartet auf die Freigabe durch den Nutzer. "
                 "Mache mit weiteren Vorschlägen weiter (Reihenfolge beachten) oder antworte mit tool=final.")

    if answer is None:
        answer = ("Ich konnte in der erlaubten Anzahl von Schritten keine endgültige Antwort finden"
                  + (f", habe aber {len(proposals)} Vorschlag/Vorschläge angelegt." if proposals else ".")
                  + " Bitte formuliere die Anfrage enger oder teile sie auf.")
    return {"answer": answer, "steps": steps, "proposals": proposals, "usage": usage}
