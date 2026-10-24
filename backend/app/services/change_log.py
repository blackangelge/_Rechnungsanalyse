"""
Änderungsprotokoll mit Rückgängig-Funktion.

Nachrüst-Läufe (services/validation_backfill.py), der Daten-Browser und der KI-Assistent schreiben jede Feldänderung als
(Tabelle, Zeilen-ID, Feld, Alt, Neu) in `data_changes`, gruppiert in einem `data_change_runs`-Vorgang. `undo_run` setzt die Werte zurück —
aber nur dort, wo der aktuelle Wert noch dem protokollierten Neuwert entspricht (wurde danach erneut etwas geändert, bleibt es unberührt
und wird als „übersprungen" gezählt). Werte werden als kanonischer Text abgelegt (Zahlen ohne Nachkomma-Nullen, Datum als ISO).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, select
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal
from app.models.data_change import DataChange, DataChangeRun
from app.services import db_tools

logger = logging.getLogger(__name__)

# Felder, die Nachrüst-Läufe verändern können
EXTRACTION_FIELDS = ("total_amount_netto", "total_amount_brutto", "total_tax_value", "validation_status", "validation_details")
POSITION_FIELDS = ("quantity", "unit", "unit_price_netto", "total_price", "price_quantity", "pack_size", "pack_unit", "unit_type")
UNDO_CHUNK = 1000


# ─── Kanonische Werte ─────────────────────────────────────────────────────────

def canon(value: Any, scale: int | None = None) -> str | None:
    """Vergleichbarer Textwert. Zahlen: auf die Spaltengenauigkeit gerundet, ohne überflüssige Nullen."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Decimal, float, int)):
        d = Decimal(str(value))
        if scale is not None:
            d = d.quantize(Decimal(1).scaleb(-scale))
        text = format(d.normalize(), "f")
        return text if text != "-0" else "0"
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def _scale(table, field: str) -> int | None:
    col = table.columns.get(field)
    return getattr(col.type, "scale", None) if col is not None and isinstance(col.type, Numeric) else None


# ─── Vorgänge anlegen / abschließen ───────────────────────────────────────────

def start_run(kind: str, label: str | None = None) -> int:
    """Legt einen Vorgang in eigener Transaktion an (existiert damit auch, wenn der Lauf später abbricht)."""
    db = SessionLocal()
    try:
        run = DataChangeRun(kind=kind, label=label)
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def finish_run(run_id: int, changed_rows: int, changed_fields: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(DataChangeRun, run_id)
        if run is not None:
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            run.changed_rows, run.changed_fields = changed_rows, changed_fields
            db.commit()
    finally:
        db.close()


def snapshot(extraction, positions) -> dict[tuple[str, int, str], tuple[str | None, int | None]]:
    """Momentaufnahme der veränderbaren Felder einer Extraktion samt Positionen → {(Tabelle, ID, Feld): (Wert, document_id)}."""
    snap: dict = {}
    ext_t = Base.metadata.tables["invoice_extractions"]
    pos_t = Base.metadata.tables["order_positions"]
    for f in EXTRACTION_FIELDS:
        snap[("invoice_extractions", extraction.id, f)] = (canon(getattr(extraction, f), _scale(ext_t, f)), extraction.document_id)
    for p in positions:
        for f in POSITION_FIELDS:
            snap[("order_positions", p.id, f)] = (canon(getattr(p, f), _scale(pos_t, f)), p.document_id)
    return snap


def diff_snapshots(run_id: int, before: dict, after: dict) -> list[DataChange]:
    out = []
    for key, (new, doc_id) in after.items():
        old = before.get(key, (None, doc_id))[0]
        if old != new:
            table, row_id, field = key
            out.append(DataChange(run_id=run_id, table_name=table, row_id=row_id, document_id=doc_id, field=field,
                                  old_value=old, new_value=new))
    return out


def log_manual_update(db: Session, kind: str, table: str, pk: Any, before: dict, after: dict, label: str | None = None) -> int | None:
    """Protokolliert eine einzelne Änderung (Daten-Browser / KI-Freigabe). Wirft nie — das Protokoll darf eine Änderung nicht verhindern."""
    try:
        row_id = int(pk)
    except (TypeError, ValueError):
        return None
    try:
        t = db_tools.get_table(table)
        changes = []
        for name, new in after.items():
            old = before.get(name)
            co, cn = canon(db_tools.json_safe(old), _scale(t, name)), canon(db_tools.json_safe(new), _scale(t, name))
            if co != cn and not db_tools._is_secret(table, name):
                changes.append((name, co, cn))
        if not changes:
            return None
        run = DataChangeRun(kind=kind, label=label or f"{table} #{row_id}", finished_at=dt.datetime.now(dt.timezone.utc),
                            changed_rows=1, changed_fields=len(changes))
        db.add(run)
        db.flush()
        doc_id = after.get("document_id") if isinstance(after.get("document_id"), int) else (row_id if table == "documents" else None)
        db.add_all([DataChange(run_id=run.id, table_name=table, row_id=row_id, document_id=doc_id, field=n, old_value=o, new_value=v)
                    for n, o, v in changes])
        db.commit()
        return run.id
    except Exception:                                   # noqa: BLE001
        db.rollback()
        logger.warning("Änderungsprotokoll konnte nicht geschrieben werden (%s #%s)", table, pk, exc_info=True)
        return None


# ─── Rückgängig ───────────────────────────────────────────────────────────────

_lock = threading.Lock()
_state: dict = {"running": False}


def get_undo_state() -> dict:
    with _lock:
        return dict(_state)


def _undo(run_id: int) -> None:
    reverted = skipped = 0
    try:
        last_id = 0
        while True:
            db = SessionLocal()
            try:
                chunk = (db.query(DataChange)
                         .filter(DataChange.run_id == run_id, DataChange.status == 0, DataChange.id > last_id)
                         .order_by(DataChange.id).limit(UNDO_CHUNK).all())
                if not chunk:
                    break
                last_id = chunk[-1].id
                by_table: dict[str, list[DataChange]] = {}
                for c in chunk:
                    by_table.setdefault(c.table_name, []).append(c)
                for tname, items in by_table.items():
                    table = Base.metadata.tables.get(tname)
                    pk = db_tools.single_pk(table) if table is not None else None
                    if table is None or pk is None:
                        for c in items:
                            c.status = 2
                        skipped += len(items)
                        continue
                    pkcol = table.columns[pk]
                    ids = list({c.row_id for c in items})
                    rows = {r._mapping[pkcol]: r._mapping for r in db.execute(select(table).where(pkcol.in_(ids))).all()}
                    per_row: dict[int, dict] = {}
                    for c in items:
                        row = rows.get(c.row_id)
                        current = canon(db_tools.json_safe(row[table.columns[c.field]]), _scale(table, c.field)) if row is not None else "\0missing"
                        if row is None or current != c.new_value:
                            c.status = 2
                            skipped += 1
                            continue
                        try:
                            per_row.setdefault(c.row_id, {})[c.field] = (
                                None if c.old_value is None else _coerce_any(table, c.field, c.old_value))
                            c.status = 1
                            reverted += 1
                        except db_tools.DbToolError:
                            c.status = 2
                            skipped += 1
                    for row_id, values in per_row.items():
                        db.execute(table.update().where(pkcol == row_id).values(**values))
                db.commit()
                with _lock:
                    _state.update({"reverted": reverted, "skipped": skipped})
            finally:
                db.close()
        db = SessionLocal()
        try:
            run = db.get(DataChangeRun, run_id)
            if run is not None:
                run.undone_at = dt.datetime.now(dt.timezone.utc)
                run.undo_summary = f"{reverted} Feld(er) zurückgesetzt, {skipped} übersprungen (danach erneut geändert)"
                db.commit()
        finally:
            db.close()
    except Exception as exc:                            # noqa: BLE001
        logger.error("Rückgängig machen von Vorgang #%s abgebrochen: %s", run_id, exc, exc_info=True)
        with _lock:
            _state["error"] = str(exc)
    finally:
        with _lock:
            _state.update({"running": False, "reverted": reverted, "skipped": skipped,
                           "finished_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        logger.warning("Vorgang #%s rückgängig gemacht: %d zurückgesetzt, %d übersprungen", run_id, reverted, skipped)


def _coerce_any(table, field: str, text: str):
    """Text → Spaltentyp. Wie db_tools._coerce, aber auch für Primärschlüssel-freie Felder aller Tabellen und ohne Geheimnis-Sperre-Umweg."""
    return db_tools._coerce(table, field, text)


def start_undo(run_id: int, background: bool = True) -> dict:
    db = SessionLocal()
    try:
        run = db.get(DataChangeRun, run_id)
        if run is None:
            raise LookupError(f"Vorgang #{run_id} nicht gefunden")
        if run.finished_at is None:
            raise RuntimeError("Der Vorgang läuft noch oder wurde unterbrochen — erst nach Abschluss rückgängig machen.")
        if run.undone_at is not None:
            raise RuntimeError("Dieser Vorgang wurde bereits rückgängig gemacht.")
    finally:
        db.close()
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Es läuft bereits ein Rückgängig-Vorgang")
        _state.clear()
        _state.update({"running": True, "run_id": run_id, "reverted": 0, "skipped": 0,
                       "started_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    if background:
        threading.Thread(target=_undo, args=(run_id,), name="change-undo", daemon=True).start()
    else:
        _undo(run_id)
    return get_undo_state()
