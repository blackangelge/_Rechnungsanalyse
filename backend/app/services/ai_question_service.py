"""
Freie KI-Frage an einen Beleg („Wie heißt die IBAN?“): Die KI antwortet NUR mit dem Wert, der Beleg wird aktualisiert,
jede Frage landet im Verlauf (`ai_queries`) und jede Antwort in `document_answers`.

Aktualisiert wird der gespeicherte Beleg an den Stellen, die Anzeige und Export lesen: das KI-JSON (`invoice_extractions.raw_response`,
Pfad im Feldkatalog) und — wo es eine Spalte gibt — die Spalte. Jede Änderung steht im Änderungsprotokoll (Art `ai_question`) und
lässt sich dort rückgängig machen. Ohne Zielfeld wird die Antwort nur gespeichert (Verlauf je Beleg).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import crud
from app.database import SessionLocal
from app.models.ai_query import AIQuery, DocumentAnswer
from app.models.document import Document
from app.models.invoice_extraction import InvoiceExtraction
from app.services import ai_service, change_log, pdf_service

logger = logging.getLogger(__name__)

NOT_FOUND = "NICHT GEFUNDEN"
MAX_BATCH = 500

# key → Beschriftung, Pfad im KI-JSON, optionale Spalte an invoice_extractions, Art des Werts (text | iban | date | number)
FIELDS: dict[str, dict] = {
    "iban":            {"label": "IBAN",                        "path": ("lieferant", "bankverbindung", "iban"), "column": None, "kind": "iban"},
    "bic":             {"label": "BIC",                         "path": ("lieferant", "bankverbindung", "bic"), "column": None, "kind": "text"},
    "bank_name":       {"label": "Bankname",                    "path": ("lieferant", "bankverbindung", "bank_name"), "column": None, "kind": "text"},
    "vat_id":          {"label": "USt-IdNr.",                   "path": ("lieferant", "ust_id_nr"), "column": None, "kind": "text"},
    "tax_number":      {"label": "Steuernummer",                "path": ("lieferant", "steuernummer"), "column": None, "kind": "text"},
    "hrb_number":      {"label": "HRB-Nummer",                  "path": ("lieferant", "hrb_nummer"), "column": None, "kind": "text"},
    "supplier_name":   {"label": "Lieferant (Name)",            "path": ("lieferant", "name"), "column": "vendor_id", "kind": "text"},
    "invoice_number":  {"label": "Rechnungsnummer",             "path": ("rechnungsdaten", "rechnungsnummer"), "column": "invoice_number", "kind": "text"},
    "invoice_date":    {"label": "Rechnungsdatum",              "path": ("rechnungsdaten", "rechnungsdatum"), "column": "invoice_date", "kind": "date"},
    "due_date":        {"label": "Fälligkeitsdatum",            "path": ("rechnungsdaten", "faelligkeit"), "column": "due_date", "kind": "date"},
    "customer_number": {"label": "Kundennummer",                "path": ("rechnungsdaten", "kundennummer"), "column": None, "kind": "text"},
    "payment_terms":   {"label": "Zahlungsbedingungen",         "path": ("zahlungsinformationen", "zahlungsbedingungen"), "column": "payment_terms", "kind": "text"},
    "total_gross":     {"label": "Gesamtbetrag brutto",         "path": ("zahlungsinformationen", "gesamtbetrag_brutto"), "column": "total_amount_brutto", "kind": "number"},
    "total_net":       {"label": "Gesamtbetrag netto",          "path": ("zahlungsinformationen", "gesamtbetrag_netto"), "column": "total_amount_netto", "kind": "number"},
}
_COLUMN_LIMITS = {"vendor_id": 255, "invoice_number": 100}
_HINTS = {
    "iban": "Gib nur die IBAN aus (z.B. DE02 1203 0000 0000 2020 51).",
    "date": "Gib das Datum im Format YYYY-MM-DD aus.",
    "number": "Gib nur die Zahl aus, Punkt als Dezimaltrenner, ohne Währung und Tausenderpunkte (z.B. 1234.56).",
}


def field_catalog() -> list[dict]:
    return [{"key": k, "label": v["label"], "kind": v["kind"]} for k, v in FIELDS.items()]


# ─── Antwort bereinigen ───────────────────────────────────────────────────────

def clean_answer(raw: str | None, kind: str = "text") -> tuple[str | None, str | None]:
    """
    Gibt (Wert, Hinweis) zurück. Wert None = nichts Verwertbares (Hinweis sagt warum).
    Entfernt Denk-Blöcke, Anführungszeichen, Markdown und typische Präfixe („Antwort:“), nimmt die erste nicht leere Zeile.
    """
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    if not text:
        return None, "Leere Antwort der KI"
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(antwort|answer|ergebnis)\s*[:\-]\s*", "", line.strip("`*_ \"'„“”‚‘’"), flags=re.IGNORECASE).strip("`*_ \"'„“”‚‘’")
    if not line or NOT_FOUND.lower() in line.lower() or line.lower() in ("null", "none", "n/a"):
        return None, "Nicht auf dem Beleg gefunden"
    if kind == "iban":
        iban = re.sub(r"[^A-Za-z0-9]", "", line).upper()
        if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
            return None, f"Keine gültige IBAN: {line[:60]}"
        return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4)), None
    if kind == "date":
        iso = ai_service._date(line)
        return (iso, None) if iso else (None, f"Kein gültiges Datum: {line[:60]}")
    if kind == "number":
        num = ai_service._num(re.sub(r"[€$£¥\s]", "", line))
        if num is None:
            return None, f"Keine Zahl: {line[:60]}"
        return (f"{num:.4f}".rstrip("0").rstrip(".") or "0"), None
    return line[:2000], None


# ─── Beleg aktualisieren ──────────────────────────────────────────────────────

def _set_path(data: dict, path: tuple[str, ...], value) -> bool:
    node = data
    for key in path[:-1]:
        nxt = node.get(key)
        if isinstance(nxt, list):                       # z.B. bankverbindung als Liste: erstes Objekt
            if not nxt or not isinstance(nxt[0], dict):
                nxt = {}
                node[key] = nxt
            else:
                nxt = nxt[0]
        elif not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    node[path[-1]] = value
    return True


def _get_path(data: dict, path: tuple[str, ...]):
    node = data
    for key in path:
        if isinstance(node, list):
            node = node[0] if node and isinstance(node[0], dict) else None
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def apply_to_document(db: Session, doc: Document, field_key: str, value: str, question: str) -> tuple[bool, str | None, str | None, int | None]:
    """Schreibt `value` ins KI-JSON und (falls vorhanden) in die Spalte. Rückgabe: (angewendet, Hinweis, alter Wert, Protokoll-Vorgang)."""
    spec = FIELDS[field_key]
    ext: InvoiceExtraction | None = doc.extraction
    if ext is None:
        return False, "Der Beleg hat keine Rechnungsdaten (nicht als Eingangsrechnung ausgelesen)", None, None
    before = {"document_id": doc.id, "raw_response": ext.raw_response}
    after = {"document_id": doc.id}
    old_value = None

    raw_text = ext.raw_response or ""
    parsed = None
    if not raw_text.strip():
        parsed = {}
    else:
        try:
            parsed = json.loads(raw_text)
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        old_json = _get_path(parsed, spec["path"])
        old_value = None if old_json is None else str(old_json)
        json_value = ai_service._num(value) if spec["kind"] == "number" else value
        _set_path(parsed, spec["path"], json_value)
        ext.raw_response = json.dumps(parsed, ensure_ascii=False)
        after["raw_response"] = ext.raw_response
    elif not spec["column"]:
        return False, "Die gespeicherte KI-Antwort ist kein lesbares JSON — dieses Feld kann dort nicht ergänzt werden", None, None

    col = spec["column"]
    if col:
        cur = getattr(ext, col)
        if old_value is None and cur is not None:
            old_value = str(cur)
        before[col] = cur
        if spec["kind"] == "date":
            new_val = datetime.strptime(value, "%Y-%m-%d").date()
        elif spec["kind"] == "number":
            from decimal import Decimal
            new_val = Decimal(value)
        else:
            new_val = value[:_COLUMN_LIMITS.get(col, 100000)]
        setattr(ext, col, new_val)
        after[col] = new_val
    db.commit()
    run_id = change_log.log_manual_update(
        db, "ai_question", "invoice_extractions", ext.id, before, after, label=f"KI-Frage: {question[:120]}"
    )
    return True, None, old_value, run_id


# ─── Eine Frage an einen Beleg ────────────────────────────────────────────────

def record_query(db: Session, question: str, field_key: str | None) -> AIQuery:
    """Frage im Verlauf festhalten (gleiche Frage + Zielfeld wird hochgezählt statt doppelt angelegt)."""
    norm = " ".join(question.split()).casefold()
    for q in db.query(AIQuery).filter(AIQuery.field_key.is_(field_key) if field_key is None else AIQuery.field_key == field_key).all():
        if " ".join(q.question.split()).casefold() == norm:
            q.use_count = (q.use_count or 0) + 1
            q.last_used_at = datetime.now(timezone.utc)
            db.commit()
            return q
    q = AIQuery(question=" ".join(question.split()), field_key=field_key)
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def ask_one(doc_id: int, question: str, field_key: str | None, ai_config_id: int | None, apply: bool = True,
            query_id: int | None = None) -> dict:
    """Stellt die Frage zu einem Beleg. Wirft RuntimeError bei KI-/Datei-Fehlern, sonst Ergebnis-Dict (auch bei „nicht gefunden“)."""
    from app.services import workflow_runner      # KI-Proxy-Aufbau (lokal: vermeidet Importzyklus)
    if field_key is not None and field_key not in FIELDS:
        raise ValueError(f"Unbekanntes Feld: {field_key}")
    kind = FIELDS[field_key]["kind"] if field_key else "text"

    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None or doc.soft_deleted:
            raise RuntimeError("Beleg nicht gefunden")
        if doc.status == "processing":
            raise RuntimeError("Der Beleg wird gerade analysiert")
        if not doc.stored_filename:
            raise RuntimeError("Der Beleg hat keine PDF-Datei")
        config = crud.ai_config.get_by_id(db, ai_config_id) if ai_config_id else crud.ai_config.get_default(db)
        if config is None:
            raise RuntimeError("Keine KI-Konfiguration vorhanden/aktiv")
        cfg_id = config.id
        img = crud.image_settings.get_or_create(db)
        dpi, fmt, quality = img.dpi, img.image_format, img.jpeg_quality
        from pathlib import Path
        pdf_path = Path(doc.batch.storage_folder_path) / doc.stored_filename
        if not pdf_path.exists():
            raise RuntimeError("PDF-Datei nicht gefunden")
    finally:
        db.close()

    proxy = workflow_runner._db_ai_proxy(cfg_id)
    images = pdf_service.pdf_to_base64_images(pdf_path, dpi, fmt, quality)
    if not images:
        raise RuntimeError("PDF konnte nicht gerendert werden")
    raw, error, _stats = ai_service.ask_question(images, proxy, question, _HINTS.get(kind, ""))
    del images
    if error:
        raise RuntimeError(error)
    value, note = clean_answer(raw, kind)

    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        applied, old_value, run_id = False, None, None
        if value is not None and field_key and apply:
            applied, apply_note, old_value, run_id = apply_to_document(db, doc, field_key, value, question)
            note = apply_note or note
        elif value is not None and field_key and not apply:
            note = "Nicht am Beleg übernommen (nur Antwort speichern)"
        ans = DocumentAnswer(
            document_id=doc_id, query_id=query_id, question=question, field_key=field_key, answer=value,
            raw_answer=(raw or "")[:4000], valid=value is not None, applied=applied, note=note,
            old_value=old_value, change_run_id=run_id, model=getattr(proxy, "model_name", None),
        )
        db.add(ans)
        db.commit()
        db.refresh(ans)
        return answer_dict(ans)
    finally:
        db.close()


def answer_dict(a: DocumentAnswer) -> dict:
    return {
        "id": a.id, "document_id": a.document_id, "query_id": a.query_id, "question": a.question, "field_key": a.field_key,
        "answer": a.answer, "raw_answer": a.raw_answer, "valid": bool(a.valid), "applied": bool(a.applied), "note": a.note,
        "old_value": a.old_value, "change_run_id": a.change_run_id, "model": a.model,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ─── Mehrere Belege (Hintergrund-Thread, nacheinander) ────────────────────────

_lock = threading.Lock()
_cancel = threading.Event()
_state: dict = {"running": False}


def get_state() -> dict:
    with _lock:
        return json.loads(json.dumps(_state, default=str))


def _run(doc_ids: list[int], question: str, field_key: str | None, ai_config_id: int | None, apply: bool, query_id: int | None) -> None:
    try:
        for doc_id in doc_ids:
            if _cancel.is_set():
                break
            try:
                res = ask_one(doc_id, question, field_key, ai_config_id, apply, query_id)
                with _lock:
                    _state["results"].append({"document_id": doc_id, "answer": res["answer"], "applied": res["applied"], "note": res["note"], "change_run_id": res["change_run_id"]})
                    _state["found" if res["valid"] else "not_found"] += 1
                    _state["applied"] += 1 if res["applied"] else 0
            except Exception as exc:                                   # noqa: BLE001 — ein Beleg stoppt nie den Lauf
                logger.warning("KI-Frage Beleg #%s: %s", doc_id, exc)
                with _lock:
                    _state["errors"] += 1
                    _state["results"].append({"document_id": doc_id, "answer": None, "applied": False, "note": f"Fehler: {exc}"})
            with _lock:
                _state["processed"] += 1
    finally:
        with _lock:
            _state["running"] = False
            _state["cancelled"] = _cancel.is_set()
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()


def start(doc_ids: list[int], question: str, field_key: str | None, ai_config_id: int | None, apply: bool,
          query_id: int | None, background: bool = True) -> dict:
    ids = list(dict.fromkeys(doc_ids))[:MAX_BATCH]
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Es läuft bereits eine KI-Frage an mehrere Belege")
        _cancel.clear()
        _state.clear()
        _state.update({
            "running": True, "question": question, "field_key": field_key, "apply": apply, "total": len(ids), "processed": 0,
            "found": 0, "not_found": 0, "applied": 0, "errors": 0, "results": [], "cancelled": False,
            "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
        })
    args = (ids, question, field_key, ai_config_id, apply, query_id)
    if background:
        threading.Thread(target=_run, args=args, name="ai-question", daemon=True).start()
    else:
        _run(*args)
    return get_state()


def cancel() -> bool:
    with _lock:
        running = bool(_state.get("running"))
    if running:
        _cancel.set()
    return running
