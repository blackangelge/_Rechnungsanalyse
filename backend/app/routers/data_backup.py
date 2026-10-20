"""
Daten-Backup und Import-Tool (Imports, Belege, KI-Auswertungen, optional PDFs und Lieferanten).

  GET    /api/data-backup                      — vorhandene Backups (+ Auftragsstatus, Backup-Ordner)
  GET    /api/data-backup/state                — Fortschritt des laufenden/letzten Auftrags
  POST   /api/data-backup/create               — Backup erstellen (Hintergrundauftrag)
  POST   /api/data-backup/{name}/restore       — Wiederherstellen / Importieren (dry_run = nur Vorschau)
  POST   /api/data-backup/cancel               — laufenden Auftrag abbrechen
  GET    /api/data-backup/{name}/download      — Backup herunterladen
  POST   /api/data-backup/upload               — Backup-Datei hochladen (landet im Backup-Ordner)
  DELETE /api/data-backup/{name}               — Backup löschen
"""

import logging


from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services import data_backup

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data-backup", tags=["Daten-Backup"])


class CreateBody(BaseModel):
    include_pdfs: bool = False
    include_vendors: bool = True
    batch_ids: list[int] | None = None


class RestoreBody(BaseModel):
    mode: str = "remap"                 # "preserve" (Original-IDs) | "remap" (neue IDs)
    batch_ids: list[int] | None = None
    restore_files: bool = True
    include_vendors: bool = True
    allow_duplicates: bool = False
    dry_run: bool = True


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc).strip("'\""))
    if isinstance(exc, data_backup.BackupError) and "läuft bereits" in str(exc):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def overview():
    return {"path": str(data_backup.backup_dir()), "backups": data_backup.list_backups(), "state": data_backup.get_state()}


@router.get("/state")
def state():
    return data_backup.get_state()


@router.post("/create")
def create(body: CreateBody):
    try:
        return data_backup.create_backup(body.include_pdfs, body.batch_ids, body.include_vendors)
    except data_backup.BackupError as exc:
        raise _err(exc)


@router.post("/cancel")
def cancel():
    return {"cancelled": data_backup.cancel()}


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not data_backup.NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Dateiname muss auf .zip enden und darf nur Buchstaben, Ziffern, Punkt, - und _ enthalten")
    target = data_backup.backup_dir() / name
    if target.exists():
        raise HTTPException(status_code=409, detail=f"'{name}' gibt es im Backup-Ordner bereits")
    part = target.with_suffix(".zip.part")
    try:
        with open(part, "wb") as out:
            while chunk := await file.read(1 << 20):
                out.write(chunk)
        data_backup.read_manifest(part)                         # gültiges Daten-Backup?
        part.replace(target)
    except data_backup.BackupError as exc:
        part.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return {"name": name, "size": target.stat().st_size}


@router.get("/{name}/download")
def download(name: str):
    try:
        path = data_backup.backup_path(name)
    except (data_backup.BackupError, LookupError) as exc:
        raise _err(exc)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/{name}/restore")
def restore(name: str, body: RestoreBody):
    try:
        out = data_backup.restore_backup(
            name, body.mode, body.batch_ids, body.restore_files, body.dry_run, body.include_vendors, body.allow_duplicates)
    except (data_backup.BackupError, LookupError) as exc:
        raise _err(exc)
    if not body.dry_run:
        logger.warning("Daten-Import aus %s gestartet (Modus %s, Imports %s)", name, body.mode, body.batch_ids or "alle")
    return out


@router.delete("/{name}")
def delete(name: str):
    try:
        path = data_backup.backup_path(name)
    except (data_backup.BackupError, LookupError) as exc:
        raise _err(exc)
    path.unlink()
    logger.warning("Daten-Backup gelöscht: %s", name)
    return {"deleted": name}
