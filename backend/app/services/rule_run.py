"""
Hintergrundlauf „Alle Belege mit den Prüfregeln neu prüfen“ (mit Fortschritt, abbrechbar, optional parallel).

Die Belege sind voneinander unabhängig; jeder Arbeiter öffnet eine eigene Sitzung und bearbeitet einen Block von Beleg-IDs
(Commit je Block). Standard: mehrere Arbeiter (die Auswertung ist überwiegend Datenbank-Ein-/Ausgabe, deshalb helfen Threads);
`workers=1` rechnet streng nacheinander (Tests, SQLite). Ein defekter Beleg stoppt den Lauf nicht (Fehlerzähler + erste Meldungen).
Ein Lauf gleichzeitig. Das Ergebnis je Beleg wird wie bei der Analyse gespeichert (`_checks`/`_validation`).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.schema_def import ExtractedRecord
from app.services import own_companies, rule_engine

logger = logging.getLogger(__name__)

BLOCK = 100
MAX_WORKERS = 8
MAX_ERRORS_KEPT = 20

_lock = threading.Lock()
_cancel = threading.Event()
_state: dict = {"running": False}


def get_state() -> dict:
    with _lock:
        return {**_state, "counts": dict(_state.get("counts", {})), "errors": list(_state.get("errors", []))}


def cancel() -> bool:
    with _lock:
        running = bool(_state.get("running"))
    if running:
        _cancel.set()
    return running


def _check_block(session_factory: Callable[[], Session], ids: list[int], specs, units) -> None:
    db = session_factory()
    try:
        parties = own_companies.load_parties(db)
        for doc_id in ids:
            if _cancel.is_set():
                break
            try:
                status = rule_engine.evaluate_document(db, doc_id, specs, units=units, parties=parties)["summary"]["status"]
                with _lock:
                    _state["counts"][status] = _state["counts"].get(status, 0) + 1
                    if status in ("needs_review", "warn") and len(_state["flagged"]) < 50:
                        _state["flagged"].append(doc_id)
            except Exception as exc:                                  # ein defekter Beleg stoppt den Lauf nicht
                db.rollback()
                with _lock:
                    _state["error_count"] += 1
                    if len(_state["errors"]) < MAX_ERRORS_KEPT:
                        _state["errors"].append(f"Beleg #{doc_id}: {exc}")
            with _lock:
                _state["processed"] += 1
        db.commit()
    finally:
        db.close()


def run(session_factory: Callable[[], Session], document_ids: list[int] | None, workers: int) -> dict:
    """Synchroner Lauf (läuft im Hintergrund-Thread; Tests rufen ihn direkt auf)."""
    _cancel.clear()
    try:
        db = session_factory()
        try:
            specs = rule_engine.load_specs(db)
            units = rule_engine.load_unit_table(db)
            if document_ids is None:
                ids = [i for (i,) in db.query(ExtractedRecord.document_id).filter(ExtractedRecord.parent_record_id.is_(None))
                       .distinct().order_by(ExtractedRecord.document_id).all()]
            else:
                ids = list(dict.fromkeys(document_ids))
        finally:
            db.close()
        with _lock:
            _state.update(total=len(ids), rules=len(specs), workers=workers)
        blocks = [ids[i:i + BLOCK] for i in range(0, len(ids), BLOCK)]
        if workers <= 1 or len(blocks) <= 1:
            for b in blocks:
                if _cancel.is_set():
                    break
                _check_block(session_factory, b, specs, units)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rule-run") as pool:
                for f in [pool.submit(_check_block, session_factory, b, specs, units) for b in blocks]:
                    f.result()
    except Exception as exc:
        logger.exception("Prüfregel-Lauf abgebrochen")
        with _lock:
            _state["errors"].append(f"Abbruch: {exc}")
    finally:
        with _lock:
            _state["running"] = False
            _state["cancelled"] = _cancel.is_set()
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Prüfregel-Lauf %s: %s Belege, %s", "abgebrochen" if _cancel.is_set() else "beendet", _state.get("processed"), _state.get("counts"))
    return get_state()


def start(session_factory: Callable[[], Session], document_ids: list[int] | None = None, workers: int = 4) -> dict:
    workers = max(1, min(int(workers or 1), MAX_WORKERS))
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Es läuft bereits eine Prüfung.")
        _state.clear()
        _state.update(running=True, total=0, processed=0, counts={}, flagged=[], errors=[], error_count=0, cancelled=False,
                      started_at=datetime.now(timezone.utc).isoformat(), finished_at=None, workers=workers, rules=0)
    threading.Thread(target=run, args=(session_factory, document_ids, workers), name="rule-run", daemon=True).start()
    return get_state()
