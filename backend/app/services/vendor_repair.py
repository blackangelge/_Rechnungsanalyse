"""
Reparatur verwaister Belege: Belege, deren Lieferantenname zu keinem Lieferanten (mehr) gehört.

Belege hängen nur über den Namen (`invoice_extractions.vendor_id`) am Lieferanten. Wurden Lieferanten früher zusammengeführt,
bevor Schreibvarianten mitwanderten, blieben Belege mit dem alten Namen zurück. `find_orphans` sucht solche Namen und schlägt je
Name einen Lieferanten vor (USt-IdNr./Steuernr./IBAN aus der gespeicherten KI-Antwort → hoch, gleicher Name ohne Rechtsform → mittel,
ähnlicher Name → niedrig). Es wird NIE automatisch umgeschrieben: `apply` setzt nur ausdrücklich bestätigte Zuordnungen um und
protokolliert jeden Beleg im Änderungsprotokoll (rückgängig machbar). Keine KI, die Rohantwort bleibt unverändert.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import vendor as vendor_crud
from app.models.data_change import DataChange, DataChangeRun
from app.models.document import Document
from app.models.invoice_extraction import InvoiceExtraction
from app.models.vendor import Vendor
from app.services import supplier_dedup as dd

logger = logging.getLogger(__name__)

SIMILARITY = 0.88
DEFAULT_LIMIT = 300


def _identifiers(raw_response: str | None) -> tuple[str, set[str], str]:
    """(USt-IdNr., IBANs, Steuernr.) aus der gespeicherten KI-Antwort — normalisiert, leer wenn nicht vorhanden."""
    try:
        data = json.loads(raw_response or "")
    except ValueError:
        return "", set(), ""
    lief = data.get("lieferant") if isinstance(data, dict) else None
    if not isinstance(lief, dict):
        return "", set(), ""
    bank = lief.get("bankverbindung")
    ibans: set[str] = set()
    for b in (bank if isinstance(bank, list) else [bank]):
        if isinstance(b, dict) and b.get("iban"):
            n = dd.normalize_iban(str(b["iban"]))
            if n:
                ibans.add(n)
    return dd.normalize_vat(str(lief.get("ust_id_nr") or "")), ibans, dd.normalize_tax_number(str(lief.get("steuernummer") or ""))


def find_orphans(db: Session, limit: int = DEFAULT_LIMIT) -> dict:
    """Verwaiste Namen (häufigste zuerst) mit Vorschlag; `total` = Anzahl aller verwaisten Namen."""
    vendors = db.query(Vendor).all()
    known_keys = {vendor_crud.name_key(v.name) for v in vendors}
    by_vat: dict[str, list[Vendor]] = {}
    by_iban: dict[str, list[Vendor]] = {}
    by_tax: dict[str, list[Vendor]] = {}
    norm: list[tuple[str, Vendor]] = []
    for v in vendors:
        if dd.normalize_vat(v.vat_id):
            by_vat.setdefault(dd.normalize_vat(v.vat_id), []).append(v)
        if dd.normalize_tax_number(v.tax_number):
            by_tax.setdefault(dd.normalize_tax_number(v.tax_number), []).append(v)
        for ba in v.bank_accounts:
            if dd.normalize_iban(ba.iban):
                by_iban.setdefault(dd.normalize_iban(ba.iban), []).append(v)
        norm.append((dd.normalize_name(v.name), v))

    rows = (
        db.query(InvoiceExtraction.vendor_id, func.count(InvoiceExtraction.id), func.min(InvoiceExtraction.id))
        .join(Document, Document.id == InvoiceExtraction.document_id)
        .filter(InvoiceExtraction.vendor_id.isnot(None), InvoiceExtraction.vendor_id != "", Document.soft_deleted == False)  # noqa: E712
        .group_by(InvoiceExtraction.vendor_id)
        .all()
    )
    orphans = sorted(((n, c, i) for n, c, i in rows if vendor_crud.name_key(n) not in known_keys), key=lambda r: -r[1])
    total = len(orphans)
    orphans = orphans[:limit]

    raws = {}
    ids = [i for _, _, i in orphans]
    for k in range(0, len(ids), 200):
        for eid, raw in db.query(InvoiceExtraction.id, InvoiceExtraction.raw_response).filter(InvoiceExtraction.id.in_(ids[k:k + 200])):
            raws[eid] = raw

    result = []
    for name, count, sample_id in orphans:
        suggestion = None
        vat, ibans, tax = _identifiers(raws.get(sample_id))
        nname = dd.normalize_name(name)
        cands: dict[int, tuple[int, str, Vendor]] = {}      # Vendor-ID → (Rang, Grund, Vendor); kleiner Rang = besser

        def offer(v: Vendor, rank: int, reason: str) -> None:
            if v.id not in cands or rank < cands[v.id][0]:
                cands[v.id] = (rank, reason, v)

        for v in by_vat.get(vat, []):
            offer(v, 0, "gleiche USt-IdNr.")
        for v in by_tax.get(tax, []):
            offer(v, 0, "gleiche Steuernummer")
        for iban in ibans:
            for v in by_iban.get(iban, []):
                offer(v, 0, "gleiche IBAN")
        if nname:
            for vn, v in norm:
                if vn and vn == nname:
                    offer(v, 1, "gleicher Name (ohne Rechtsform)")
                elif vn and vn[0] == nname[0] and dd.name_similarity(name, v.name) >= SIMILARITY:
                    offer(v, 2, "ähnlicher Name")
        if cands:
            rank, reason, v = min(cands.values(), key=lambda t: (t[0], t[2].id))
            suggestion = {
                "vendor_id": v.id, "vendor_name": v.name, "reason": reason,
                "confidence": "high" if rank == 0 else "medium" if rank == 1 else "low",
                "ambiguous": sum(1 for t in cands.values() if t[0] == rank) > 1,
            }
        result.append({"name": name, "document_count": count, "suggestion": suggestion})
    return {"total": total, "orphans": result}


def apply(db: Session, assignments: list[tuple[str, int]]) -> dict:
    """
    Schreibt bestätigte Zuordnungen (verwaister Name → Ziel-Lieferant) um — eine Transaktion, im Änderungsprotokoll festgehalten.
    Rückgabe: {"moved_documents": n, "run_id": id | None}. Fehler (unbekannter Lieferant) → ValueError, nichts wird geändert.
    """
    targets: dict[int, Vendor] = {}
    for _, vid in assignments:
        if vid not in targets:
            v = db.get(Vendor, vid)
            if v is None:
                raise ValueError(f"Lieferant #{vid} nicht gefunden")
            targets[vid] = v
    try:
        run = DataChangeRun(kind="vendor_reassign", label=f"Verwaiste Belege zugeordnet ({len(assignments)} Name(n))")
        db.add(run)
        db.flush()
        moved = 0
        for name, vid in assignments:
            new = targets[vid].name
            if name == new:
                continue
            rows = db.query(InvoiceExtraction.id, InvoiceExtraction.document_id).filter(InvoiceExtraction.vendor_id == name).all()
            if not rows:
                continue
            db.add_all([DataChange(run_id=run.id, table_name="invoice_extractions", row_id=eid, document_id=did,
                                   field="vendor_id", old_value=name, new_value=new) for eid, did in rows])
            db.query(InvoiceExtraction).filter(InvoiceExtraction.vendor_id == name).update(
                {InvoiceExtraction.vendor_id: new}, synchronize_session=False)
            moved += len(rows)
        run.finished_at = dt.datetime.now(dt.timezone.utc)
        run.changed_rows = run.changed_fields = moved
        if moved == 0:
            db.delete(run)
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Verwaiste Belege zugeordnet: %d Beleg(e), %d Name(n)", moved, len(assignments))
    return {"moved_documents": moved, "run_id": run.id if moved else None}
