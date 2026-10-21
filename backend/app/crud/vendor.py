"""
CRUD-Operationen für die vendor- und vendor_bank_accounts-Tabellen.

Kernfunktion ist find_or_create(), die Lieferanten dedupliziert:
  1. Suche nach IBAN (stärkster Identifier)
  2. Suche nach VAT-ID
  3. Suche nach Name (Fallback)

Bestehende Felder werden nur ergänzt, nie überschrieben.

Hinweis: Die übergebene Session wird nicht geschlossen — das ist Aufgabe des Aufrufers.
"""

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.invoice_extraction import InvoiceExtraction
from app.models.vendor import Vendor
from app.models.vendor_bank_account import VendorBankAccount

logger = logging.getLogger(__name__)


def get_all(db: Session) -> list[Vendor]:
    """Gibt alle Lieferanten zurück, nach ID sortiert."""
    return db.query(Vendor).order_by(Vendor.id).all()


def get_by_id(db: Session, vendor_id: int) -> Vendor | None:
    """Gibt einen Lieferanten anhand seiner ID zurück. Gibt None zurück wenn nicht gefunden."""
    return db.get(Vendor, vendor_id)


def find_or_create(
    db: Session,
    name: str | None,
    street: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    country: str | None = None,
    hrb_number: str | None = None,
    tax_number: str | None = None,
    vat_id: str | None = None,
    bank_name: str | None = None,
    iban: str | None = None,
    bic: str | None = None,
) -> Vendor | None:
    """Findet oder erstellt einen Vendor. Deduplication nach IBAN → VAT-ID → Name."""
    if not name:
        return None

    existing: Vendor | None = None

    # 1. IBAN (stärkster Identifier)
    if iban:
        existing = (
            db.query(Vendor)
            .join(VendorBankAccount, Vendor.id == VendorBankAccount.vendor_id)
            .filter(VendorBankAccount.iban == iban)
            .first()
        )

    # 2. VAT-ID
    if existing is None and vat_id:
        existing = db.query(Vendor).filter(Vendor.vat_id == vat_id).first()

    # 3. Name
    if existing is None:
        existing = db.query(Vendor).filter(Vendor.name == name).first()

    if existing is not None:
        # Fehlende Felder ergänzen
        if street and not existing.street:
            existing.street = street
        if postal_code and not existing.postal_code:
            existing.postal_code = postal_code
        if city and not existing.city:
            existing.city = city
        if hrb_number and not existing.hrb_number:
            existing.hrb_number = hrb_number
        if tax_number and not existing.tax_number:
            existing.tax_number = tax_number
        if vat_id and not existing.vat_id:
            existing.vat_id = vat_id
        # Bankverbindung ergänzen falls noch nicht vorhanden
        if iban and not any(ba.iban == iban for ba in existing.bank_accounts):
            db.add(VendorBankAccount(
                vendor_id=existing.id, bank_name=bank_name, iban=iban, bic=bic
            ))
        db.commit()
        db.refresh(existing)
        return existing

    # Neu anlegen
    vendor = Vendor(
        name=name,
        street=street,
        postal_code=postal_code,
        city=city,
        country=country,
        hrb_number=hrb_number,
        tax_number=tax_number,
        vat_id=vat_id,
    )
    db.add(vendor)
    db.flush()  # vendor.id verfügbar machen

    if iban or bank_name:
        db.add(VendorBankAccount(
            vendor_id=vendor.id, bank_name=bank_name, iban=iban, bic=bic
        ))

    db.commit()
    db.refresh(vendor)
    return vendor


def update(db: Session, vendor_id: int, data: dict) -> Vendor | None:
    """
    Aktualisiert beliebige Felder eines Lieferanten anhand eines dicts.
    Unbekannte Keys werden ignoriert (hasattr-Check). Gibt None zurück wenn nicht gefunden.
    """
    obj = db.get(Vendor, vendor_id)
    if obj is None:
        return None
    for k, v in data.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, vendor_id: int) -> bool:
    """
    Löscht einen Lieferanten dauerhaft (inkl. aller Bankkonten über CASCADE).
    Gibt False zurück wenn nicht gefunden.
    """
    obj = db.get(Vendor, vendor_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True


def name_key(name: str | None) -> str:
    """Vergleichsschlüssel für Lieferantennamen: ohne Groß/Klein und mit vereinheitlichten Leerzeichen."""
    return " ".join((name or "").split()).casefold()


def get_document_counts(db: Session) -> dict[str, int]:
    """
    Anzahl nicht gelöschter Belege je Lieferant, Schlüssel = `name_key(name)`. Die Verknüpfung Beleg ↔ Lieferant
    läuft in diesem System über den extrahierten Namen (invoice_extractions.vendor_id), nicht über einen Fremdschlüssel;
    Schreibvarianten (Groß/Klein, Leerzeichen) zählen zum selben Lieferanten. Abfrage mit `counts.get(name_key(v.name), 0)`.
    """
    rows = (
        db.query(InvoiceExtraction.vendor_id, func.count(InvoiceExtraction.id))
        .join(Document, Document.id == InvoiceExtraction.document_id)
        .filter(InvoiceExtraction.vendor_id.isnot(None), Document.soft_deleted == False)  # noqa: E712
        .group_by(InvoiceExtraction.vendor_id)
        .all()
    )
    counts: dict[str, int] = {}
    for name, n in rows:
        key = name_key(name)
        counts[key] = counts.get(key, 0) + n
    return counts


# ─── Zusammenführen ───────────────────────────────────────────────────────────

MERGE_FIELDS = (
    "name", "street", "postal_code", "city", "country", "hrb_number", "tax_number", "vat_id",
)


class MergeError(ValueError):
    """Fachlicher Fehler beim Zusammenführen; `status` ist der passende HTTP-Status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class MergeResult:
    vendor: Vendor
    merged_ids: list[int] = field(default_factory=list)
    moved_documents: int = 0
    moved_bank_accounts: int = 0


def _is_set(value: str | None) -> bool:
    return bool(value and value.strip())


def _iban_key(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def merge(
    db: Session,
    target_id: int,
    source_ids: list[int],
    fields: dict[str, str | None] | None = None,
    update_document_names: bool = True,
) -> MergeResult:
    """
    Führt die Lieferanten `source_ids` in `target_id` zusammen (alles in einer Transaktion):

      • Leere Stammdatenfelder des Ziels werden aus den Quellen aufgefüllt (in ID-Reihenfolge);
        `fields` legt einzelne Felder ausdrücklich fest (Vorrang vor dem Auffüllen).
      • Bankverbindungen der Quellen wandern zum Ziel (IBANs, die das Ziel schon hat, entfallen).
      • Hat das Ziel keinen Analyse-Workflow, wird der erste der Quellen übernommen.
      • Belege hängen an Lieferanten nur über den extrahierten Namen (invoice_extractions.vendor_id).
        Mit `update_document_names` (Standard) bekommen alle Belege der Quellen — und des Ziels, falls
        sich dessen Name ändert — den endgültigen Namen; nur dann gehören sie danach zum Ziel (Belegzähler,
        Workflow-Auswahl). Der Originalwortlaut bleibt in `raw_response` erhalten.
      • Die Quell-Lieferanten werden gelöscht.
    """
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        raise MergeError("Keine Lieferanten zum Zusammenführen ausgewählt")
    if target_id in ids:
        raise MergeError("Der Ziel-Lieferant darf nicht gleichzeitig zusammengeführt werden")
    target = db.get(Vendor, target_id)
    if target is None:
        raise MergeError(f"Ziel-Lieferant #{target_id} nicht gefunden", 404)
    sources = [db.get(Vendor, i) for i in ids]
    missing = [i for i, s in zip(ids, sources) if s is None]
    if missing:
        raise MergeError("Lieferant(en) nicht gefunden: " + ", ".join(f"#{i}" for i in missing), 404)
    sources = sorted(sources, key=lambda s: s.id)           # type: ignore[arg-type, return-value]

    overrides = {k: (v.strip() if isinstance(v, str) else v) for k, v in (fields or {}).items()}
    unknown = [k for k in overrides if k not in MERGE_FIELDS]
    if unknown:
        raise MergeError("Unbekannte Felder: " + ", ".join(unknown))
    if "name" in overrides and not _is_set(overrides["name"]):
        raise MergeError("Der Name darf nicht leer sein")

    old_names = {target.name} | {s.name for s in sources}
    try:
        for fname in MERGE_FIELDS:                          # 1) Leeres auffüllen
            if not _is_set(getattr(target, fname)):
                for s in sources:
                    if _is_set(getattr(s, fname)):
                        setattr(target, fname, getattr(s, fname))
                        break
        for fname, value in overrides.items():              # 2) ausdrückliche Wahl
            setattr(target, fname, value if _is_set(value) else None)
        if target.workflow_id is None:
            target.workflow_id = next((s.workflow_id for s in sources if s.workflow_id), None)

        known = {_iban_key(ba.iban) for ba in target.bank_accounts if _iban_key(ba.iban)}
        moved_banks = 0
        for s in sources:                                   # 3) Bankverbindungen
            for ba in list(s.bank_accounts):
                key = _iban_key(ba.iban)
                if key and key in known:
                    continue                                # Duplikat → entfällt mit der Quelle
                if key:
                    known.add(key)
                s.bank_accounts.remove(ba)
                target.bank_accounts.append(ba)
                moved_banks += 1

        moved = 0
        if update_document_names:                           # 4) Belege dem Ziel zuordnen
            old_keys = {name_key(n) for n in old_names}
            variants = [                                     # exakte Namen UND Schreibvarianten (Groß/Klein, Leerzeichen)
                n for (n,) in db.query(InvoiceExtraction.vendor_id).filter(InvoiceExtraction.vendor_id.isnot(None)).distinct()
                if n != target.name and name_key(n) in old_keys
            ]
            for i in range(0, len(variants), 500):
                moved += (
                    db.query(InvoiceExtraction)
                    .filter(InvoiceExtraction.vendor_id.in_(variants[i:i + 500]))
                    .update({InvoiceExtraction.vendor_id: target.name}, synchronize_session=False)
                )
        for s in sources:
            db.delete(s)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(target)
    logger.info("Lieferanten %s in #%d '%s' zusammengeführt (%d Beleg(e) umbenannt, %d Bankverbindung(en) übernommen)",
                ids, target.id, target.name, moved, moved_banks)
    return MergeResult(target, ids, moved, moved_banks)
