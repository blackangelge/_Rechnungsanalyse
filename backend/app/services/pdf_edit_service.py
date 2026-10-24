"""
PDF-Bearbeitung mit Versionierung: Seiten entfernen (manuell / automatisch).

Jede Änderung erzeugt eine neue, unveränderliche Version unter
`{Firma_Jahr}/versions/{doc_id}/v{n}.pdf`. Version 1 ist das Original (wird beim
ersten Bearbeiten angelegt). Die aktuelle Version wird zusätzlich als `{id}.pdf`
bereitgehalten, damit Vorschau, KI-Analyse und Export unverändert funktionieren.

Alle Funktionen sind SYNCHRON (Dateizugriff + DB) und werden aus Routen über eine
eigene Session bzw. aus der Analyse per `asyncio.to_thread` aufgerufen.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_version import DocumentVersion

logger = logging.getLogger(__name__)


class PdfEditError(Exception):
    """Fachlicher Fehler bei der PDF-Bearbeitung (wird als HTTP 400 gemeldet)."""


# ─── Pfade ────────────────────────────────────────────────────────────────────

def doc_dir(doc: Document) -> Path:
    """Speicherordner des Imports (STORAGE_PATH/{Firma}_{Jahr}, siehe ImportBatch.storage_folder_path)."""
    return Path(doc.batch.storage_folder_path)


def current_path(doc: Document) -> Path:
    if not doc.stored_filename:
        raise PdfEditError("PDF-Datei noch nicht verfügbar (Import läuft noch?)")
    return doc_dir(doc) / doc.stored_filename


def version_rel_path(doc_id: int, version_number: int) -> str:
    return f"versions/{doc_id}/v{version_number}.pdf"


def version_path(doc: Document, version: DocumentVersion) -> Path:
    return doc_dir(doc) / version.stored_path


def delete_version_files(doc: Document) -> None:
    """Löscht alle Versionsdateien eines Dokuments (beim Löschen eines Imports)."""
    folder = doc_dir(doc) / "versions" / str(doc.id)
    try:
        if folder.exists():
            shutil.rmtree(folder)
        parent = folder.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:
        logger.warning("Versionsdateien von #%d nicht vollständig gelöscht: %s", doc.id, exc)


# ─── Hilfen ───────────────────────────────────────────────────────────────────

def _open_reader(path: Path) -> PdfReader:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfEditError("PDF ist passwortgeschützt und kann nicht bearbeitet werden")
        return reader
    except PdfEditError:
        raise
    except Exception as exc:
        raise PdfEditError(f"PDF konnte nicht gelesen werden: {exc}") from exc


def _write_pages(src: Path, dst: Path, keep_pages: list[int]) -> None:
    """Schreibt die gewählten Seiten (1-basiert) atomar nach dst."""
    reader = _open_reader(src)
    writer = PdfWriter()
    for number in keep_pages:
        writer.add_page(reader.pages[number - 1])
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    os.replace(tmp, dst)


def _atomic_copy(src: Path, dst: Path) -> Path:
    """Kopiert src → dst.tmp und gibt den tmp-Pfad zurück (Umbenennen macht der Aufrufer)."""
    tmp = dst.with_suffix(".swap")
    shutil.copy2(src, tmp)
    return tmp


def get_version(db: Session, doc_id: int, number: int) -> DocumentVersion | None:
    return (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == doc_id, DocumentVersion.version_number == number)
        .first()
    )


def list_versions(db: Session, doc_id: int) -> list[DocumentVersion]:
    return (
        db.query(DocumentVersion)
        .filter(DocumentVersion.document_id == doc_id)
        .order_by(DocumentVersion.version_number)
        .all()
    )


def ensure_original(db: Session, doc: Document) -> DocumentVersion:
    """Legt Version 1 (Original) an, falls das Dokument noch nie bearbeitet wurde."""
    existing = get_version(db, doc.id, 1)
    if existing is not None:
        return existing
    src = current_path(doc)
    if not src.exists():
        raise PdfEditError("PDF-Datei nicht auf dem Server gefunden")
    rel = version_rel_path(doc.id, 1)
    dst = doc_dir(doc) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    version = DocumentVersion(
        document_id=doc.id, version_number=1, stored_path=rel,
        page_count=len(_open_reader(dst).pages), file_size_bytes=dst.stat().st_size,
        source="original", description="Original (Import)",
    )
    db.add(version)
    db.flush()
    return version


def _activate_new_version(
    db: Session,
    doc: Document,
    parent: DocumentVersion,
    produce,                       # Callable[[Path], None]: schreibt die neue Datei
    source: str,
    description: str,
    removed_pages: list[int] | None,
) -> DocumentVersion:
    """Gemeinsamer Ablauf: Datei schreiben → Version speichern → aktuelle PDF austauschen."""
    max_n = (
        db.query(func.max(DocumentVersion.version_number))
        .filter(DocumentVersion.document_id == doc.id)
        .scalar()
    ) or 1
    new_n = max_n + 1
    rel = version_rel_path(doc.id, new_n)
    dst = doc_dir(doc) / rel
    cur = current_path(doc)
    swap: Path | None = None
    replaced = False
    try:
        produce(dst)
        pages = len(_open_reader(dst).pages)
        swap = _atomic_copy(dst, cur)
        version = DocumentVersion(
            document_id=doc.id, version_number=new_n, stored_path=rel, page_count=pages,
            file_size_bytes=dst.stat().st_size, source=source, description=description,
            parent_version=parent.version_number,
            removed_pages=json.dumps(removed_pages) if removed_pages else None,
        )
        db.add(version)
        doc.pdf_version = new_n
        doc.page_count = pages
        doc.file_size_bytes = dst.stat().st_size
        db.flush()
        os.replace(swap, cur)
        replaced = True
        db.commit()
        db.refresh(version)
        return version
    except Exception:
        db.rollback()
        if replaced:   # aktuelle PDF auf die Elternversion zurücksetzen
            try:
                shutil.copy2(version_path(doc, parent), cur)
            except OSError:
                logger.exception("Konnte aktuelle PDF von #%d nicht zurücksetzen", doc.id)
        for leftover in (dst, swap):
            if leftover is not None and leftover.exists():
                leftover.unlink(missing_ok=True)
        raise


# ─── Öffentliche Operationen ──────────────────────────────────────────────────

def remove_pages(
    db: Session,
    doc: Document,
    *,
    remove: list[int] | None = None,
    keep: list[int] | None = None,
    source: str = "manual",
    description: str | None = None,
) -> DocumentVersion:
    """
    Entfernt Seiten (`remove`) bzw. behält nur die angegebenen (`keep`) — 1-basiert,
    bezogen auf die aktuelle Version — und legt daraus eine neue Version an.
    """
    if bool(remove) == bool(keep):
        raise PdfEditError("Bitte entweder Seiten zum Entfernen ODER zum Behalten angeben")
    ensure_original(db, doc)
    parent = get_version(db, doc.id, doc.pdf_version)
    if parent is None:
        raise PdfEditError(f"Aktuelle Version {doc.pdf_version} nicht gefunden")
    total = parent.page_count or len(_open_reader(version_path(doc, parent)).pages)

    selected = sorted(set(remove or keep or []))
    if selected[0] < 1 or selected[-1] > total:
        raise PdfEditError(f"Seitenzahlen müssen zwischen 1 und {total} liegen")
    kept = [p for p in range(1, total + 1) if p not in selected] if remove else selected
    removed = [p for p in range(1, total + 1) if p not in kept]
    if not removed:
        raise PdfEditError("Es würde keine Seite entfernt — keine Änderung")
    if not kept:
        raise PdfEditError("Es muss mindestens eine Seite übrig bleiben")

    src = version_path(doc, parent)
    desc = description or f"{_ranges(removed)} entfernt ({len(kept)} von {total} Seite(n) behalten)"
    return _activate_new_version(
        db, doc, parent, lambda dst: _write_pages(src, dst, kept), source, desc, removed,
    )


def create_child_document(db: Session, doc: Document, pages: list[int], label: str) -> Document:
    """
    Legt aus den Seiten `pages` (1-basiert, bezogen auf die aktuelle Version) einen NEUEN Beleg im selben Import an
    (Status `pending`, eigene PDF `{id}.pdf`). Das Original bleibt unverändert. Wird zum Abspalten mehrerer Rechnungen
    aus einer Sammel-PDF genutzt. Commit macht der Aufrufer.
    """
    if not pages:
        raise PdfEditError("Keine Seiten für den neuen Beleg")
    src = current_path(doc)
    stem = Path(doc.original_filename).stem
    child = Document(batch_id=doc.batch_id, original_filename=f"{stem}_{label}.pdf", file_size_bytes=0, status="pending", page_count=len(pages))
    db.add(child)
    db.flush()
    dst = doc_dir(doc) / f"{child.id}.pdf"
    _write_pages(src, dst, sorted(pages))
    child.stored_filename = dst.name
    child.file_size_bytes = dst.stat().st_size
    db.flush()
    return child


def restore_version(db: Session, doc: Document, number: int) -> DocumentVersion:
    """Macht eine frühere Version wieder aktuell — als NEUE Version (Verlauf bleibt erhalten)."""
    target = get_version(db, doc.id, number)
    if target is None:
        raise PdfEditError(f"Version {number} nicht gefunden")
    if number == doc.pdf_version:
        raise PdfEditError(f"Version {number} ist bereits die aktuelle")
    parent = get_version(db, doc.id, doc.pdf_version) or target
    src = version_path(doc, target)
    if not src.exists():
        raise PdfEditError(f"Datei von Version {number} nicht gefunden")
    return _activate_new_version(
        db, doc, parent, lambda dst: (dst.parent.mkdir(parents=True, exist_ok=True),
                                      shutil.copy2(src, dst)),
        "restore", f"Wiederhergestellt aus Version {number}", None,
    )


def auto_trim(db: Session, doc: Document, max_pages: int) -> DocumentVersion | None:
    """
    Automatisches Kürzen: hat das aktuelle PDF mehr als `max_pages` Seiten,
    werden nur die ersten `max_pages` behalten. Gibt None zurück, wenn nichts zu tun war.
    """
    if max_pages < 1:
        raise PdfEditError("max_pages muss mindestens 1 sein")
    total = len(_open_reader(current_path(doc)).pages)
    if total <= max_pages:
        if doc.page_count != total:
            doc.page_count = total
            db.commit()
        return None
    return remove_pages(
        db, doc, keep=list(range(1, max_pages + 1)), source="auto",
        description=f"Automatisch gekürzt: nur die ersten {max_pages} von {total} Seite(n) behalten",
    )


def _ranges(pages: list[int]) -> str:
    """[2,3,4,7] → 'Seite(n) 2–4, 7'"""
    parts: list[str] = []
    start = prev = pages[0]
    for p in pages[1:] + [None]:  # type: ignore[list-item]
        if p is not None and p == prev + 1:
            prev = p
            continue
        parts.append(str(start) if start == prev else f"{start}–{prev}")
        if p is not None:
            start = prev = p
    return "Seite(n) " + ", ".join(parts)
