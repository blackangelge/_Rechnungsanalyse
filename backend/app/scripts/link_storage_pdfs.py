"""
Kopierte PDFs in der Datenbank verlinken.

Nach dem Nachkopieren der PDF-Dateien in den Storage (z.B. mit copy_missing_pdfs.ps1) steht an
den betroffenen Belegen noch ``stored_filename = NULL`` — dann liefert die Vorschau 404. Dieses
Skript setzt für jeden nicht gelöschten Beleg, dessen PDF jetzt unter
``{ImportBatch.storage_folder_path}/{id}.pdf`` liegt, aber ``stored_filename`` noch leer ist,
den Dateinamen und die Dateigröße. Die Seitenzahl bleibt wie aus der Übernahme übernommen.

Nur Belege mit leerem ``stored_filename`` werden angefasst; beliebig oft wiederholbar.
Mit ``--dry-run`` wird nichts geschrieben, nur gezählt.

Aufruf (im Backend-Container):
    /venv/bin/python -m app.scripts.link_storage_pdfs            # verlinken
    /venv/bin/python -m app.scripts.link_storage_pdfs --dry-run  # nur anzeigen
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from app.database import SessionLocal
from app.models.document import Document
from app.models.import_batch import ImportBatch


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    db = SessionLocal()
    try:
        rows = (
            db.query(Document, ImportBatch.company_name, ImportBatch.year, ImportBatch.storage_folder_path)
            .join(ImportBatch, Document.batch_id == ImportBatch.id)
            .filter(Document.soft_deleted.is_(False), Document.stored_filename.is_(None))
            .all()
        )
        linked = 0
        still_missing = 0
        by_group: Counter = Counter()
        for doc, company, year, folder in rows:
            pdf = Path(folder or "") / f"{doc.id}.pdf"
            if not pdf.is_file():
                still_missing += 1
                continue
            by_group[(company, year)] += 1
            if not dry_run:
                doc.stored_filename = pdf.name
                try:
                    doc.file_size_bytes = pdf.stat().st_size
                except OSError:
                    pass
            linked += 1
            if not dry_run and linked % 500 == 0:
                db.commit()
        if not dry_run:
            db.commit()
    finally:
        db.close()

    verb = "Würde verlinken" if dry_run else "Verlinkt"
    print(f"Belege mit leerem stored_filename gesamt: {len(rows)}")
    print(f"{verb} (PDF jetzt vorhanden): {linked}")
    print(f"PDF weiterhin nicht vorhanden: {still_missing}")
    if by_group:
        print("\nJe Import:")
        for (company, year), n in sorted(by_group.items(), key=lambda kv: -kv[1]):
            print(f"  {company} {year}: {n}")
    if dry_run:
        print("\n(Trockenlauf — nichts geschrieben.)")


if __name__ == "__main__":
    main()
