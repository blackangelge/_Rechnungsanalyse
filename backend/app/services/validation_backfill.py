"""
Nachrüstung der Plausibilitätsprüfung für den Altbestand — ohne KI.

Belege, die vor der Plausibilitätsprüfung analysiert wurden, haben keinen Prüfstatus und keinen
Positions-Gesamtpreis (`order_positions.total_price`). Beides lässt sich aus der gespeicherten
KI-Antwort (`invoice_extractions.raw_response`) rekonstruieren:

  1. fehlenden Positions-Gesamtpreis aus `positionen[i].gesamtpreis` eintragen (i = position_index),
     ebenso fehlendes Netto / Steuerbetrag aus `zahlungsinformationen`
  2. die Regelprüfung (`plausibility_service.validate_invoice`, inkl. automatischer Korrektur
     eindeutiger Fehler) laufen lassen und das Ergebnis speichern

Zusätzlicher Modus `normalize_units`: rechnet bei ALLEN fertigen Belegen die Gewichtspositionen auf Tonnen um (Menge, Einheit,
Einzelpreis je t), ohne Prüfstatus/-details anzufassen.

Es gibt keine zweite KI-Abfrage: Belege mit Status „Prüfen!" können danach gezielt neu analysiert werden.
Der Lauf arbeitet in einem Hintergrund-Thread in Blöcken (Keyset über die Extraktions-ID), hält den
Fortschritt im Speicher und lässt sich abbrechen; ein abgebrochener oder unterbrochener Lauf wird einfach
neu gestartet, weil nur Belege OHNE Prüfstatus bearbeitet werden. `dry_run` rechnet alles durch, schreibt aber nichts.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.document import Document
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition
from app.services import ai_service, plausibility_service

logger = logging.getLogger(__name__)

CHUNK_SIZE = 200
_KI_ERROR_PREFIXES = (
    "KI überlastet:", "KI-Fehler:", "KI-Timeout", "KI-Verbindungsfehler", "Unerwarteter KI-Fehler",
)
MAX_ERRORS_KEPT = 20


def _parse_raw(raw_response: str | None) -> dict | None:
    if not raw_response:
        return None
    try:
        data = json.loads(raw_response)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _dec(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


# ─── Ein Beleg ────────────────────────────────────────────────────────────────

def backfill_extraction(
    db: Session,
    extraction: InvoiceExtraction,
    positions: list[OrderPosition],
    *,
    auto_correct: bool = True,
) -> dict:
    """
    Rekonstruiert fehlende Werte und prüft einen Beleg. Verändert nur die Session (kein Commit);
    der Aufrufer schreibt per commit() bzw. verwirft per rollback() (Vorschau).

    Rückgabe: {"outcome": <Prüfstatus | "skipped_no_data">, "filled_prices": n, "corrections": n}
    """
    raw_text = extraction.raw_response or ""
    raw = _parse_raw(raw_text)
    if raw is None or raw_text.startswith(_KI_ERROR_PREFIXES):
        return {"outcome": "skipped_no_data", "filled_prices": 0, "corrections": 0}

    # 1) fehlende Werte aus der Rohantwort ergänzen
    raw_pos = raw.get("positionen") if isinstance(raw.get("positionen"), list) else []
    filled = 0
    for pos in positions:
        idx = pos.position_index
        if pos.total_price is None and 0 <= idx < len(raw_pos) and isinstance(raw_pos[idx], dict):
            value = ai_service._num(raw_pos[idx].get("gesamtpreis"))
            if value is not None:
                pos.total_price = _dec(value)
                filled += 1
    zahlung = raw.get("zahlungsinformationen") or {}
    if isinstance(zahlung, dict):
        if extraction.total_amount_netto is None:
            extraction.total_amount_netto = _dec(ai_service._num(zahlung.get("gesamtbetrag_netto")))
        if extraction.total_tax_value is None:
            extraction.total_tax_value = _dec(ai_service._sum_tax(zahlung.get("umsatzsteuer_zusammenfassung")))

    # 2) Regelprüfung
    pos_dicts = [
        {
            "quantity": p.quantity, "unit": p.unit, "unit_price": p.unit_price_netto,
            "total_price": p.total_price, "discount": p.discount,
        }
        for p in positions
    ]
    fields = {"total_amount": extraction.total_amount_brutto, "discount_amount": extraction.discount_amount}
    result = plausibility_service.validate_invoice(fields, pos_dicts, raw, auto_correct)
    validation = result.to_dict()
    validation["method"] = "rules"
    validation["backfill"] = True

    from app.crud import document as crud_document     # lokal: vermeidet Importzyklus beim App-Start
    crud_document.update_validation(
        db, extraction.document_id, validation, corrected_positions=result.corrections + result.normalizations, commit=False
    )
    return {"outcome": result.status, "filled_prices": filled, "corrections": len(result.corrections)}


def normalize_extraction(extraction: InvoiceExtraction, positions: list[OrderPosition]) -> int:
    """
    Nur Einheiten vereinheitlichen (Gewicht → t) — Prüfstatus und -details bleiben unberührt.
    Rückgabe: Anzahl umgerechneter Positionen. Kein Commit.
    """
    raw = _parse_raw(extraction.raw_response) or {}
    raw_pos = raw.get("positionen") if isinstance(raw.get("positionen"), list) else []
    raw_pos = [p if isinstance(p, dict) else {} for p in raw_pos]
    pos_dicts = [
        {"quantity": p.quantity, "unit": p.unit, "unit_price": p.unit_price_netto,
         "total_price": p.total_price, "discount": p.discount}
        for p in positions
    ]
    result = plausibility_service.ValidationResult(status=plausibility_service.STATUS_UNCHECKED)
    plausibility_service._normalize_mass_to_tonnes(result, pos_dicts, raw_pos)
    for n in result.normalizations:
        pos, to = positions[n["position"]], n["to"]
        pos.quantity = _dec(to["quantity"])
        pos.unit = to["unit"]
        if to.get("unit_price") is not None:
            pos.unit_price_netto = _dec(to["unit_price"])
    return len(result.normalizations)


# ─── Hintergrund-Lauf ─────────────────────────────────────────────────────────

_lock = threading.Lock()
_cancel = threading.Event()
_state: dict = {"running": False}


def _empty_state(dry_run: bool, auto_correct: bool, batch_ids: list[int] | None, total: int,
                 normalize_units: bool = False) -> dict:
    return {
        "running": True, "normalize_units": normalize_units, "dry_run": dry_run, "auto_correct": auto_correct, "batch_ids": batch_ids,
        "total": total, "processed": 0, "filled_prices": 0, "corrections": 0,
        "outcomes": {}, "error_count": 0, "errors": [],
        "cancelled": False, "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
    }


def get_state() -> dict:
    with _lock:
        return json.loads(json.dumps(_state))


def _base_query(db: Session, batch_ids: list[int] | None, normalize_units: bool = False):
    """Fertig analysierte, nicht gelöschte Belege mit Extraktion; ohne Prüfstatus (außer im Modus „Einheiten“)."""
    q = (
        db.query(InvoiceExtraction.id)
        .join(Document, Document.id == InvoiceExtraction.document_id)
        .filter(
            Document.soft_deleted == False,  # noqa: E712
            Document.status == "done",
        )
    )
    if not normalize_units:
        q = q.filter(InvoiceExtraction.validation_status.is_(None))
    if batch_ids:
        q = q.filter(Document.batch_id.in_(batch_ids))
    return q


def count_pending(batch_ids: list[int] | None = None, normalize_units: bool = False) -> int:
    db = SessionLocal()
    try:
        return _base_query(db, batch_ids, normalize_units).with_entities(func.count(InvoiceExtraction.id)).scalar() or 0
    finally:
        db.close()


def _run(dry_run: bool, auto_correct: bool, batch_ids: list[int] | None, normalize_units: bool = False) -> None:
    last_id = 0
    try:
        while not _cancel.is_set():
            db = SessionLocal()
            try:
                ids = [
                    r[0] for r in _base_query(db, batch_ids, normalize_units)
                    .filter(InvoiceExtraction.id > last_id)
                    .order_by(InvoiceExtraction.id).limit(CHUNK_SIZE).all()
                ]
                if not ids:
                    break
                last_id = ids[-1]
                extractions = db.query(InvoiceExtraction).filter(InvoiceExtraction.id.in_(ids)).all()
                doc_ids = [e.document_id for e in extractions]
                by_doc: dict[int, list[OrderPosition]] = {}
                for p in (db.query(OrderPosition).filter(OrderPosition.document_id.in_(doc_ids))
                          .order_by(OrderPosition.position_index).all()):
                    by_doc.setdefault(p.document_id, []).append(p)

                for ext in extractions:
                    if _cancel.is_set():
                        break
                    try:
                        if normalize_units:
                            n = normalize_extraction(ext, by_doc.get(ext.document_id, []))
                            info = {"outcome": "normalized" if n else "unchanged", "filled_prices": 0, "corrections": n}
                        else:
                            info = backfill_extraction(
                                db, ext, by_doc.get(ext.document_id, []), auto_correct=auto_correct
                            )
                        with _lock:
                            _state["outcomes"][info["outcome"]] = _state["outcomes"].get(info["outcome"], 0) + 1
                            _state["filled_prices"] += info["filled_prices"]
                            _state["corrections"] += info["corrections"]
                    except Exception as exc:                    # ein defekter Beleg stoppt nie den Lauf
                        db.rollback()
                        logger.warning("Nachrüstung Beleg #%s: %s", ext.document_id, exc)
                        with _lock:
                            _state["error_count"] += 1
                            if len(_state["errors"]) < MAX_ERRORS_KEPT:
                                _state["errors"].append(f"Beleg #{ext.document_id}: {exc}")
                        continue
                    with _lock:
                        _state["processed"] += 1
                if dry_run:
                    db.rollback()
                else:
                    db.commit()
            finally:
                db.close()
    except Exception as exc:
        logger.error("Nachrüstung der Prüfung abgebrochen: %s", exc, exc_info=True)
        with _lock:
            _state["errors"].append(f"Abbruch: {exc}")
    finally:
        with _lock:
            _state["running"] = False
            _state["cancelled"] = _cancel.is_set()
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Nachrüstung der Prüfung %s: %d Beleg(e), Ergebnisse %s, %d Gesamtpreise ergänzt, %d Korrekturen%s",
            "abgebrochen" if _cancel.is_set() else "beendet", _state["processed"], _state["outcomes"],
            _state["filled_prices"], _state["corrections"], " (Vorschau, nichts gespeichert)" if dry_run else "",
        )


def start(dry_run: bool = False, auto_correct: bool = True, batch_ids: list[int] | None = None,
          background: bool = True, normalize_units: bool = False) -> dict:
    """Startet einen Lauf. Läuft schon einer, wird er nicht gestartet (RuntimeError)."""
    total = count_pending(batch_ids, normalize_units)
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Es läuft bereits eine Nachrüstung")
        _cancel.clear()
        _state.clear()
        _state.update(_empty_state(dry_run, auto_correct, batch_ids, total, normalize_units))
    args = (dry_run, auto_correct, batch_ids, normalize_units)
    if background:
        threading.Thread(target=_run, args=args, name="validation-backfill", daemon=True).start()
    else:
        _run(*args)
    return get_state()


def cancel() -> bool:
    with _lock:
        running = bool(_state.get("running"))
    if running:
        _cancel.set()
    return running
