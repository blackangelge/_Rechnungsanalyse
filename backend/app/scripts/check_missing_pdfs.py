"""
Fehlende PDF-Dateien finden.

Prüft für jeden nicht gelöschten Beleg, ob die zugehörige PDF-Datei im Speicher liegt
(``{ImportBatch.storage_folder_path}/{id}.pdf`` bzw. ``stored_filename``). Schreibt NICHTS
in die Datenbank — nur eine CSV-Liste der fehlenden PDFs nach ``EXPORT_BASE_PATH`` und eine
Zusammenfassung (gesamt + je Import) auf die Konsole.

Nützlich z.B. nach einer Übernahme, wenn die PDFs separat kopiert wurden und einzelne fehlen:
die CSV nennt Beleg-ID, Firma/Jahr, Originaldateiname und den erwarteten Pfad — damit lassen
sich gezielt die fehlenden Dateien nachkopieren. Das Skript ist beliebig oft wiederholbar.

Aufruf (im Backend-Container):
    /venv/bin/python -m app.scripts.check_missing_pdfs
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models.document import Document
from app.models.import_batch import ImportBatch


def find_missing() -> list[tuple]:
    """Liste der Belege ohne vorhandene PDF: (id, firma, jahr, original_dateiname, erwarteter_pfad)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(
                Document.id, Document.original_filename, Document.stored_filename,
                ImportBatch.company_name, ImportBatch.year, ImportBatch.storage_folder_path,
            )
            .join(ImportBatch, Document.batch_id == ImportBatch.id)
            .filter(Document.soft_deleted.is_(False))
            .order_by(ImportBatch.company_name, ImportBatch.year, Document.id)
            .all()
        )
    finally:
        db.close()

    missing: list[tuple] = []
    for doc_id, original, stored, company, year, folder in rows:
        base = Path(folder or "")
        # mögliche Dateinamen: {id}.pdf (Konvention) und ein abweichender stored_filename
        candidates = [name for name in (f"{doc_id}.pdf", stored) if name]
        if not any((base / name).is_file() for name in candidates):
            missing.append((doc_id, company, year, original, str(base / f"{doc_id}.pdf")))
    return missing, len(rows)


def main() -> None:
    missing, total = find_missing()
    print(f"Geprüft: {total} Belege  |  PDF fehlt: {len(missing)}")

    if missing:
        by_group = Counter((c, y) for _, c, y, _, _ in missing)
        print("\nFehlend je Import (Firma Jahr):")
        for (company, year), n in sorted(by_group.items(), key=lambda kv: -kv[1]):
            print(f"  {company} {year}: {n}")

    out_dir = Path(settings.export_base_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"fehlende_pdfs_{datetime.now():%Y%m%d-%H%M%S}.csv"
    # utf-8-sig + Semikolon → öffnet sauber in Excel (deutsche Umlaute/Trenner)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["beleg_id", "firma", "jahr", "original_dateiname", "erwarteter_pfad"])
        writer.writerows(missing)
    print(f"\nListe geschrieben: {out}")


if __name__ == "__main__":
    main()
