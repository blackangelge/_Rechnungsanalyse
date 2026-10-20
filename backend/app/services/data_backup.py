"""
Daten-Backup und Import-Tool für alles, was zu den Imports gehört: Imports, Belege, KI-Auswertungen (Extraktionen, Positionen,
Analyse-Historie, PDF-Versionen, Token-Statistik) und auf Wunsch Lieferanten-Stammdaten und die PDF-Dateien selbst.

Format (ZIP, `daten-backup_YYYYMMDD-HHMMSS.zip` in `EXPORT_BASE_PATH/backups`):
  manifest.json                    — Version, Zeitpunkt, Optionen, Zählungen, Liste der Imports
  tables/<tabelle>.jsonl           — eine Zeile = eine Datenbankzeile (JSON; Zahlen/Datum als Text)
  files/docs/<beleg-id>.pdf        — aktuelle PDF eines Belegs (optional)
  files/versions/<beleg-id>/v<n>.pdf — gespeicherte PDF-Versionen (optional)
Alle Namen im ZIP werden aus Zahlen gebildet, nie aus Namen des Archivs übernommen (kein Pfad-Trick möglich).

Backup und Wiederherstellung laufen als Hintergrund-Thread (ein Auftrag gleichzeitig) mit Fortschritt im Speicher. Die Wiederherstellung ist
EINE Datenbank-Transaktion: schlägt etwas fehl, wird nichts geändert. Zwei Modi:
  • "preserve" — Original-IDs behalten (Wiederherstellung nach Datenverlust; PDF-Dateien im Speicher passen weiter). Bereits vorhandene
    IDs sind ein Konflikt, ein bereits wiederhergestellter Import (gleiche ID, Firma, Jahr) wird übersprungen.
  • "remap"    — neue IDs vergeben (Zusammenführen in einen laufenden Bestand; nichts wird überschrieben). Dateien kommen aus dem Archiv.
Die Vorschau (`dry_run`) führt alles aus und verwirft es am Ende.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import shutil
import threading
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, Text, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.types import JSON

from app.config import settings
from app.database import Base, SessionLocal

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
CHUNK = 2000
IMPORT_TABLES = [
    "import_batches", "documents", "invoice_extractions", "order_positions",
    "document_versions", "analysis_history", "documents_token_counts",
]
VENDOR_TABLES = ["vendor", "vendor_bank_accounts"]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,120}\.zip$")


class BackupError(Exception):
    """Verständlicher Fehler (wird als HTTP 400/409 gemeldet)."""


class Cancelled(Exception):
    pass


def backup_dir() -> Path:
    p = Path(settings.export_base_path) / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_path(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise BackupError("Ungültiger Dateiname")
    p = backup_dir() / name
    if not p.is_file():
        raise LookupError(f"Backup '{name}' nicht gefunden")
    return p


# ─── Auftragsstatus ───────────────────────────────────────────────────────────

_lock = threading.Lock()
_cancel = threading.Event()
_job: dict = {"running": False}


def get_state() -> dict:
    with _lock:
        return json.loads(json.dumps(_job, default=str))


def _set(**kw) -> None:
    with _lock:
        _job.update(kw)


def _begin(kind: str, **extra) -> None:
    with _lock:
        if _job.get("running"):
            raise BackupError("Es läuft bereits ein Backup- oder Import-Auftrag")
        _cancel.clear()
        _job.clear()
        _job.update({"running": True, "kind": kind, "phase": "Starte …", "done": 0, "total": 0, "files_done": 0,
                     "warnings": [], "result": None, "error": None, "cancelled": False,
                     "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "finished_at": None, **extra})


def _end(**kw) -> None:
    with _lock:
        _job.update({"running": False, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(), **kw})


def _check_cancel() -> None:
    if _cancel.is_set():
        raise Cancelled()


def cancel() -> bool:
    with _lock:
        running = bool(_job.get("running"))
    if running:
        _cancel.set()
    return running


def _warn(msg: str) -> None:
    with _lock:
        if len(_job["warnings"]) < 30:
            _job["warnings"].append(msg)


# ─── Werte ↔ JSON ─────────────────────────────────────────────────────────────

def _ser(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str, dict, list)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return None
    return str(v)


def _parse(col, v: Any) -> Any:
    if v is None:
        return None
    t = col.type
    try:
        if isinstance(t, Boolean):
            return bool(v)
        if isinstance(t, Integer):
            return int(v)
        if isinstance(t, Numeric):
            return Decimal(str(v))
        if isinstance(t, Float):
            return float(v)
        if isinstance(t, DateTime):
            return dt.datetime.fromisoformat(str(v))
        if isinstance(t, Date):
            return dt.date.fromisoformat(str(v)[:10])
        if isinstance(t, JSON) or t.__class__.__name__ in ("JSONB", "JSON"):
            return v
    except (ValueError, ArithmeticError):
        raise BackupError(f"Wert '{str(v)[:40]}' passt nicht zur Spalte {col.table.name}.{col.name}") from None
    return str(v)


def _missing_default(col) -> Any:
    t = col.type
    if isinstance(t, Boolean):
        return False
    if isinstance(t, (Integer, Numeric, Float)):
        return 0
    if isinstance(t, DateTime):
        return dt.datetime.now(dt.timezone.utc)
    return ""


# ─── Backup ───────────────────────────────────────────────────────────────────

def _scope(tables: dict, batch_ids: list[int] | None):
    """Filterbedingung je Tabelle für die gewählten Imports (None = alle)."""
    docs = tables["documents"]
    if not batch_ids:
        return lambda name: None
    doc_ids = select(docs.c.id).where(docs.c.batch_id.in_(batch_ids))
    def cond(name: str):
        if name == "import_batches":
            return tables[name].c.id.in_(batch_ids)
        if name == "documents":
            return docs.c.batch_id.in_(batch_ids)
        if name in VENDOR_TABLES:
            return None
        return tables[name].c.document_id.in_(doc_ids)
    return cond


def _iter_rows(db: Session, table, cond, chunk: int = CHUNK):
    last = 0
    pk = table.c.id
    while True:
        q = select(table).where(pk > last).order_by(pk).limit(chunk)
        if cond is not None:
            q = q.where(cond)
        rows = db.execute(q).all()
        if not rows:
            return
        for r in rows:
            yield r._mapping
        last = rows[-1]._mapping[pk]


def create_backup(include_pdfs: bool = False, batch_ids: list[int] | None = None, include_vendors: bool = True,
                  background: bool = True) -> dict:
    _begin("backup", include_pdfs=include_pdfs)
    if background:
        threading.Thread(target=_backup, args=(include_pdfs, batch_ids, include_vendors), name="data-backup", daemon=True).start()
    else:
        _backup(include_pdfs, batch_ids, include_vendors)
    return get_state()


def _backup(include_pdfs: bool, batch_ids: list[int] | None, include_vendors: bool) -> None:
    db = SessionLocal()
    tmp: Path | None = None
    try:
        tables = {n: Base.metadata.tables[n] for n in IMPORT_TABLES + VENDOR_TABLES}
        names = IMPORT_TABLES + (VENDOR_TABLES if include_vendors else [])
        cond = _scope(tables, batch_ids)
        if batch_ids:
            known = {r[0] for r in db.execute(select(tables["import_batches"].c.id).where(tables["import_batches"].c.id.in_(batch_ids)))}
            if known != set(batch_ids):
                raise BackupError("Import(s) nicht gefunden: " + ", ".join(f"#{i}" for i in sorted(set(batch_ids) - known)))

        counts = {}
        for n in names:
            q = select(func.count()).select_from(tables[n])
            if cond(n) is not None:
                q = q.where(cond(n))
            counts[n] = db.execute(q).scalar_one()
        _set(phase="Zähle …", total=sum(counts.values()))

        batches = [dict(r._mapping) for r in db.execute(select(tables["import_batches"]).where(*([cond("import_batches")] if batch_ids else [])).order_by(tables["import_batches"].c.id))]
        doc_counts = dict(db.execute(select(tables["documents"].c.batch_id, func.count()).where(*([cond("documents")] if batch_ids else []))
                                     .group_by(tables["documents"].c.batch_id)).all())
        folder_of = {b["id"]: b["storage_folder_path"] for b in batches}

        # PDF-Dateien: Platzbedarf prüfen, bevor etwas geschrieben wird
        files_docs: list[tuple[int, Path]] = []
        files_versions: list[tuple[int, int, Path]] = []
        if include_pdfs:
            _set(phase="Suche PDF-Dateien …")
            doc_batch: dict[int, int] = {}
            for r in _iter_rows(db, tables["documents"], cond("documents")):
                doc_batch[r["id"]] = r["batch_id"]
                if r["stored_filename"] and r["batch_id"] in folder_of:
                    files_docs.append((r["id"], Path(folder_of[r["batch_id"]]) / r["stored_filename"]))
            for r in _iter_rows(db, tables["document_versions"], cond("document_versions")):
                bid = doc_batch.get(r["document_id"])
                if bid in folder_of:
                    files_versions.append((r["document_id"], r["version_number"], Path(folder_of[bid]) / r["stored_path"]))
            del doc_batch
            need = sum(p.stat().st_size for _, p in files_docs if p.exists()) + sum(p.stat().st_size for _, _, p in files_versions if p.exists())
            free = shutil.disk_usage(backup_dir()).free
            if need * 1.05 > free:
                raise BackupError(f"Zu wenig Speicherplatz im Backup-Ordner: benötigt ≈ {need / 1e9:.1f} GB, frei {free / 1e9:.1f} GB")
            _set(total=sum(counts.values()) + len(files_docs) + len(files_versions))

        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        final = backup_dir() / f"daten-backup_{stamp}.zip"
        tmp = final.with_suffix(".zip.part")
        done = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for n in names:
                _set(phase=f"Sichere {n} …")
                t = tables[n]
                with zf.open(f"tables/{n}.jsonl", "w", force_zip64=True) as out:
                    for row in _iter_rows(db, t, cond(n)):
                        _check_cancel()
                        out.write((json.dumps({k: _ser(v) for k, v in row.items()}, ensure_ascii=False) + "\n").encode("utf-8"))
                        done += 1
                        if done % 500 == 0:
                            _set(done=done)
            missing = 0
            written_docs = written_versions = 0
            if include_pdfs:
                _set(phase="Sichere PDF-Dateien …")
                for doc_id, path in files_docs:
                    _check_cancel()
                    if path.is_file():
                        zf.write(path, f"files/docs/{doc_id}.pdf", compress_type=zipfile.ZIP_STORED)
                        written_docs += 1
                    else:
                        missing += 1
                    done += 1
                    _set(done=done, files_done=written_docs + written_versions)
                for doc_id, n_, path in files_versions:
                    _check_cancel()
                    if path.is_file():
                        zf.write(path, f"files/versions/{doc_id}/v{n_}.pdf", compress_type=zipfile.ZIP_STORED)
                        written_versions += 1
                    else:
                        missing += 1
                    done += 1
                    _set(done=done, files_done=written_docs + written_versions)
                if missing:
                    _warn(f"{missing} PDF-Datei(en) fehlen im Speicher und konnten nicht gesichert werden")
            alembic_rev = None
            try:
                alembic_rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:                           # noqa: BLE001
                db.rollback()
            manifest = {
                "format_version": FORMAT_VERSION, "app": "Rechnungsanalyse",
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "alembic_revision": alembic_rev,
                "include_pdfs": include_pdfs, "include_vendors": include_vendors, "scope": "selected" if batch_ids else "all",
                "counts": counts, "files": {"docs": written_docs, "versions": written_versions, "missing": missing},
                "batches": [{"id": b["id"], "company_name": b["company_name"], "year": b["year"], "status": b["status"],
                             "created_at": _ser(b["created_at"]), "documents": doc_counts.get(b["id"], 0)} for b in batches],
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        tmp.replace(final)
        tmp = None
        _set(done=done)
        logger.warning("Daten-Backup erstellt: %s (%.1f MB, %s)", final.name, final.stat().st_size / 1e6, counts)
        _end(phase="Fertig", result={"name": final.name, "size": final.stat().st_size, "counts": counts, "files": manifest["files"]})
    except Cancelled:
        _end(phase="Abgebrochen", cancelled=True)
    except Exception as exc:                            # noqa: BLE001
        logger.error("Daten-Backup fehlgeschlagen: %s", exc, exc_info=not isinstance(exc, BackupError))
        _end(phase="Fehlgeschlagen", error=str(exc))
    finally:
        db.close()
        if tmp is not None:
            tmp.unlink(missing_ok=True)


# ─── Backups auflisten / lesen ────────────────────────────────────────────────

def read_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise BackupError(f"Kein gültiges Daten-Backup: {exc}") from None
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError(f"Backup-Format {manifest.get('format_version')} wird nicht unterstützt (erwartet {FORMAT_VERSION})")
    return manifest


def list_backups() -> list[dict]:
    out = []
    for p in sorted(backup_dir().glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        item = {"name": p.name, "size": p.stat().st_size, "modified": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(), "manifest": None, "error": None}
        try:
            m = read_manifest(p)
            item["manifest"] = {k: m.get(k) for k in ("created_at", "include_pdfs", "include_vendors", "scope", "counts", "files", "batches")}
        except BackupError as exc:
            item["error"] = str(exc)
        out.append(item)
    return out


# ─── Wiederherstellung ────────────────────────────────────────────────────────

def restore_backup(name: str, mode: str = "remap", batch_ids: list[int] | None = None, restore_files: bool = True,
                   dry_run: bool = True, include_vendors: bool = True, allow_duplicates: bool = False,
                   background: bool = True) -> dict:
    if mode not in ("preserve", "remap"):
        raise BackupError("Modus muss 'preserve' oder 'remap' sein")
    path = backup_path(name)
    read_manifest(path)                                 # früh prüfen
    _begin("restore", name=name, mode=mode, dry_run=dry_run)
    args = (path, mode, batch_ids, restore_files, dry_run, include_vendors, allow_duplicates)
    if background:
        threading.Thread(target=_restore, args=args, name="data-restore", daemon=True).start()
    else:
        _restore(*args)
    return get_state()


def _iter_jsonl(zf: zipfile.ZipFile, table: str):
    member = f"tables/{table}.jsonl"
    try:
        f = zf.open(member)
    except KeyError:
        return
    with f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _allocate(db: Session, table, n: int, state: dict) -> list[int]:
    if n == 0:
        return []
    if db.get_bind().dialect.name == "postgresql":
        res = db.execute(text(f"SELECT nextval(pg_get_serial_sequence('{table.name}', 'id')) FROM generate_series(1, :n)"), {"n": n})
        return [r[0] for r in res]
    nxt = state.get(table.name)
    if nxt is None:
        nxt = (db.execute(select(func.max(table.c.id))).scalar() or 0) + 1
    state[table.name] = nxt + n
    return list(range(nxt, nxt + n))


def _insert(db: Session, table, rows: list[dict]) -> None:
    if rows:
        db.execute(table.insert(), rows)


def _safe_folder(batch: dict) -> str:
    root = Path(settings.storage_path).resolve()
    given = Path(batch.get("storage_folder_path") or "")
    try:
        if given.resolve().is_relative_to(root):
            return str(given)
    except (OSError, ValueError):
        pass
    safe = re.sub(r"[^\w.\-]+", "_", f"{batch.get('company_name', 'Import')}_{batch.get('year', '')}")
    return str(root / safe)


def _restore(path: Path, mode: str, batch_ids: list[int] | None, restore_files: bool, dry_run: bool,
             include_vendors: bool, allow_duplicates: bool) -> None:
    db = SessionLocal()
    report: dict = {"tables": {}, "skipped_batches": [], "files": {"written": 0, "existing": 0, "missing": 0}}
    pending_files: list[tuple[str, Path]] = []        # (Name im ZIP, Zielpfad)
    try:
        tables = {n: Base.metadata.tables[n] for n in IMPORT_TABLES + VENDOR_TABLES}
        seq_state: dict = {}
        with zipfile.ZipFile(path) as zf:
            manifest = read_manifest(path)
            wanted = set(batch_ids) if batch_ids else None
            in_zip = {b["id"] for b in manifest["batches"]}
            if wanted and not wanted <= in_zip:
                raise BackupError("Import(s) nicht im Backup: " + ", ".join(f"#{i}" for i in sorted(wanted - in_zip)))
            total = sum(manifest["counts"].get(n, 0) for n in IMPORT_TABLES + (VENDOR_TABLES if include_vendors else []))
            _set(phase="Prüfe Imports …", total=total, done=0)

            # 1) Imports
            batch_map: dict[int, int] = {}
            b_rows = [r for r in _iter_jsonl(zf, "import_batches") if wanted is None or r["id"] in wanted]
            bt = tables["import_batches"]
            new_ids = iter(_allocate(db, bt, len(b_rows), seq_state) if mode == "remap" else [])
            to_insert = []
            for r in b_rows:
                _check_cancel()
                created = dt.datetime.fromisoformat(r["created_at"]) if r.get("created_at") else None
                q = select(bt.c.id, bt.c.company_name, bt.c.year, bt.c.created_at)
                if mode == "preserve":
                    ex = db.execute(q.where(bt.c.id == r["id"])).first()
                    if ex is not None:
                        if ex.company_name == r["company_name"] and ex.year == r["year"]:
                            report["skipped_batches"].append({"id": r["id"], "reason": "bereits vorhanden (gleiche ID, Firma, Jahr)"})
                            continue
                        raise BackupError(f"Konflikt: Import #{r['id']} existiert bereits mit anderen Daten ({ex.company_name} {ex.year}). "
                                          "Für ein Zusammenführen den Modus „Neue IDs vergeben“ wählen.")
                    batch_map[r["id"]] = r["id"]
                else:
                    if not allow_duplicates:
                        dup = [x for x in db.execute(q.where(bt.c.company_name == r["company_name"], bt.c.year == r["year"])).all()
                               if created is not None and x.created_at is not None and abs((x.created_at.replace(tzinfo=None) - created.replace(tzinfo=None)).total_seconds()) < 1]
                        if dup:
                            report["skipped_batches"].append({"id": r["id"], "reason": f"bereits vorhanden als Import #{dup[0].id}"})
                            continue
                    batch_map[r["id"]] = next(new_ids)
                row = {c.name: _parse(c, r[c.name]) for c in bt.columns if c.name in r}
                row["id"] = batch_map[r["id"]]
                row["storage_folder_path"] = _safe_folder(r)
                row["folder_sync"], row["auto_export"] = False, False       # keine Hintergrund-Aktionen auf wiederhergestellten Imports
                for c in bt.columns:
                    if c.name not in row and not c.nullable:
                        row[c.name] = _missing_default(c)
                to_insert.append(row)
            _insert(db, bt, to_insert)
            report["tables"]["import_batches"] = len(to_insert)
            folder_of = {batch_map[r["id"]]: _safe_folder(r) for r in b_rows if r["id"] in batch_map}
            done = len(b_rows)

            # 2) Belege, 3) Kinder
            doc_map: dict[int, int] = {}
            doc_folder: dict[int, str] = {}
            for table_name in IMPORT_TABLES[1:]:
                _check_cancel()
                _set(phase=f"Stelle {table_name} wieder her …", done=done)
                t = tables[table_name]
                buf: list[dict] = []
                inserted = 0

                def flush() -> None:
                    nonlocal inserted
                    if not buf:
                        return
                    ids = [b["_old"] for b in buf]
                    if mode == "remap":
                        for b, nid in zip(buf, _allocate(db, t, len(buf), seq_state)):
                            b["row"]["id"] = nid
                            if table_name == "documents":
                                doc_map[b["_old"]] = nid
                    else:
                        taken = [r_[0] for r_ in db.execute(select(t.c.id).where(t.c.id.in_(ids)))]
                        if taken:
                            raise BackupError(f"Konflikt: {table_name} #{taken[0]} existiert bereits. Für ein Zusammenführen den Modus „Neue IDs vergeben“ wählen.")
                        if table_name == "documents":
                            for i in ids:
                                doc_map[i] = i
                    rows = [b["row"] for b in buf]
                    if table_name == "documents":
                        for b in buf:
                            new_id = b["row"]["id"]
                            if mode == "remap" and b["row"].get("stored_filename"):
                                b["row"]["stored_filename"] = f"{new_id}.pdf"
                            doc_folder[new_id] = folder_of[b["row"]["batch_id"]]
                            if b["row"].get("status") in ("processing", "waiting_capacity"):
                                b["row"]["status"] = "pending"          # die Aufgaben der Warteschlange gehören nicht zum Backup
                    if table_name == "document_versions" and mode == "remap":
                        for b in buf:
                            b["row"]["stored_path"] = f"versions/{b['row']['document_id']}/v{b['row']['version_number']}.pdf"
                    for b in buf:
                        if b.get("_arc"):
                            r_ = b["row"]
                            rel = r_["stored_filename"] if table_name == "documents" else r_["stored_path"]
                            folder = doc_folder[r_["id"] if table_name == "documents" else r_["document_id"]]
                            pending_files.append((b["_arc"], Path(folder) / rel))
                    _insert(db, t, rows)
                    inserted += len(rows)
                    buf.clear()

                for r in _iter_jsonl(zf, table_name):
                    _check_cancel()
                    if table_name == "documents":
                        if r["batch_id"] not in batch_map:
                            continue
                    elif r["document_id"] not in doc_map:
                        continue
                    row = {c.name: _parse(c, r[c.name]) for c in t.columns if c.name in r}
                    for c in t.columns:
                        if c.name not in row and not c.nullable and c.name != "id":
                            row[c.name] = _missing_default(c)
                    if table_name == "documents":
                        row["batch_id"] = batch_map[r["batch_id"]]
                    else:
                        row["document_id"] = doc_map[r["document_id"]]
                    if mode == "preserve":
                        row["id"] = r["id"]
                    item = {"_old": r["id"], "row": row}
                    if restore_files:
                        # Name im ZIP aus den ALTEN IDs (vor dem Umschreiben der Pfade/IDs merken)
                        if table_name == "documents" and r.get("stored_filename"):
                            item["_arc"] = f"files/docs/{r['id']}.pdf"
                        elif table_name == "document_versions":
                            item["_arc"] = f"files/versions/{r['document_id']}/v{r['version_number']}.pdf"
                    buf.append(item)
                    if len(buf) >= CHUNK:
                        flush()
                        done += CHUNK
                        _set(done=done)
                flush()
                report["tables"][table_name] = inserted
                _set(done=done)

            # 4) Lieferanten-Stammdaten
            if include_vendors and "vendor" in manifest["counts"]:
                _check_cancel()
                _set(phase="Stelle Lieferanten wieder her …")
                report["tables"].update(_restore_vendors(db, zf, tables, mode, seq_state))

            # 5) Sequenzen nachziehen (nur PostgreSQL, nur wenn Original-IDs eingefügt wurden)
            if mode == "preserve" and not dry_run and db.get_bind().dialect.name == "postgresql":
                for n in IMPORT_TABLES + (VENDOR_TABLES if include_vendors else []):
                    db.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{n}', 'id'), COALESCE((SELECT MAX(id) FROM {n}), 1), (SELECT MAX(id) FROM {n}) IS NOT NULL)"))

            _check_cancel()
            if dry_run:
                db.rollback()
                report["files"] = {"would_write": len(pending_files)} if restore_files else {}
            else:
                db.commit()

            # 6) Dateien (nach dem Commit; Fehler hier machen die Datenbank nicht ungültig)
            if not dry_run and restore_files and pending_files:
                _set(phase="Schreibe PDF-Dateien …", total=len(pending_files), done=0)
                for i, (arc, target) in enumerate(pending_files):
                    _check_cancel()
                    try:
                        zinfo = zf.getinfo(arc)
                    except KeyError:
                        report["files"]["missing"] += 1
                        continue
                    if target.exists() and target.stat().st_size == zinfo.file_size:
                        report["files"]["existing"] += 1
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    part = target.with_name(target.name + ".part")
                    with zf.open(zinfo) as src, open(part, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    part.replace(target)
                    report["files"]["written"] += 1
                    if i % 50 == 0:
                        _set(done=i, files_done=report["files"]["written"])
        if not dry_run and report["files"].get("missing"):
            _warn(f"{report['files']['missing']} PDF-Datei(en) waren nicht im Backup")
        if mode == "remap" and not restore_files and not dry_run:
            _warn("Ohne Dateien aus dem Backup fehlen die PDFs der neu angelegten Belege (neue Beleg-IDs).")
        logger.warning("Daten-Wiederherstellung %s (%s, %s): %s", "Vorschau" if dry_run else "abgeschlossen", path.name, mode, report)
        _end(phase="Vorschau fertig (nichts gespeichert)" if dry_run else "Fertig", result=report)
    except Cancelled:
        db.rollback()
        _end(phase="Abgebrochen — nichts wurde geändert", cancelled=True)
    except Exception as exc:                            # noqa: BLE001
        db.rollback()
        logger.error("Daten-Wiederherstellung fehlgeschlagen: %s", exc, exc_info=not isinstance(exc, BackupError))
        _end(phase="Fehlgeschlagen — nichts wurde geändert", error=str(exc))
    finally:
        db.close()


def _restore_vendors(db: Session, zf: zipfile.ZipFile, tables: dict, mode: str, seq_state: dict) -> dict:
    """Lieferanten-Stammdaten: nur NEUE Namen werden angelegt (Beleg und Lieferant hängen nur über den Namen zusammen);
    zu bereits vorhandenen Lieferanten kommen fehlende Bankverbindungen (IBAN) dazu."""
    vt, bt = tables["vendor"], tables["vendor_bank_accounts"]
    existing = {(n or "").strip().lower(): i for i, n in db.execute(select(vt.c.id, vt.c.name))}
    workflows = {r[0] for r in db.execute(text("SELECT id FROM workflows"))} if "workflows" in Base.metadata.tables else set()
    vendor_map: dict[int, int] = {}
    first_new: dict[str, int] = {}
    alias: dict[int, int] = {}
    new_rows: list[dict] = []
    for r in _iter_jsonl(zf, "vendor"):
        key = (r.get("name") or "").strip().lower()
        if key in existing:
            vendor_map[r["id"]] = existing[key]
            continue
        row = {c.name: _parse(c, r[c.name]) for c in vt.columns if c.name in r}
        if row.get("workflow_id") not in workflows:
            row["workflow_id"] = None
        for c in vt.columns:
            if c.name not in row and not c.nullable and c.name != "id":
                row[c.name] = _missing_default(c)
        if key in first_new:                                # gleicher Name zweimal im Backup → zusammenlegen
            alias[r["id"]] = first_new[key]
            continue
        first_new[key] = r["id"]
        new_rows.append({"_old": r["id"], "row": row})
    ids = _allocate(db, vt, len(new_rows), seq_state)
    for item, nid in zip(new_rows, ids):
        item["row"]["id"] = nid
        vendor_map[item["_old"]] = nid
    for old, first_old in alias.items():
        vendor_map[old] = vendor_map[first_old]
    _insert(db, vt, [i["row"] for i in new_rows])

    have = {(v, (i or "").replace(" ", "").upper()) for v, i in db.execute(select(bt.c.vendor_id, bt.c.iban))}
    acc_rows = []
    for r in _iter_jsonl(zf, "vendor_bank_accounts"):
        vid = vendor_map.get(r["vendor_id"])
        key = (vid, (r.get("iban") or "").replace(" ", "").upper())
        if vid is None or key in have:
            continue
        have.add(key)
        row = {c.name: _parse(c, r[c.name]) for c in bt.columns if c.name in r and c.name != "id"}
        row["vendor_id"] = vid
        acc_rows.append(row)
    ids = _allocate(db, bt, len(acc_rows), seq_state)
    for row, nid in zip(acc_rows, ids):
        row["id"] = nid
    _insert(db, bt, acc_rows)
    return {"vendor": len(new_rows), "vendor_bank_accounts": len(acc_rows)}
