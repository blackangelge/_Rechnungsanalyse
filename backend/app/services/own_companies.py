"""
Eigene Gesellschaften und Rollenprüfung Lieferant/Empfänger (ohne KI, nur Vergleich — es wird nie etwas verändert, nur markiert).

Abgleich eines gelesenen Namens/einer Kennung mit der Liste:
  · USt-IdNr., Steuernummer, IBAN (Schreibweise egal) → sicher,
  · Name nach Normalisierung (ohne Rechtsform, Umlaute/Satzzeichen egal), auch die weiteren Schreibweisen → sicher,
  · hohe Namensähnlichkeit (≥ 90 %) → wahrscheinlich.
Prüfungen je Beleg (`party_checks`, Ergebnis im Format der Prüfregeln, damit Badge, Filter, Excel und Infos-Ansicht sie mitzeigen):
  1. Lieferant ist keine eigene Gesellschaft (Fehler) — nur, wenn die Liste Einträge hat.
  2. Lieferant ≠ Rechnungsempfänger (Fehler) — braucht keine Liste.
  3. Rechnungsempfänger ist eine eigene Gesellschaft (Hinweis) — nur, wenn die Liste Einträge hat.
  4. Lieferant passt zum bekannten Lieferanten, dessen IBAN/USt-IdNr. er trägt (Hinweis).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.own_company import OwnCompany
from app.services.supplier_dedup import name_similarity, normalize_iban, normalize_name, normalize_tax_number, normalize_vat

SIMILAR = 0.9
REASONS = {"ust_id": "gleiche USt-IdNr.", "steuernummer": "gleiche Steuernummer", "iban": "gleiche IBAN", "name": "gleicher Name",
           "aehnlich": "sehr ähnlicher Name"}


def clean_list(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


@dataclass
class CompanyEntry:
    id: int
    label: str
    names: list[str]
    vat: set[str]
    tax: set[str]
    iban: set[str]


@dataclass
class Parties:
    """Eigene Gesellschaften (aktive) + Namenscache bekannter Lieferanten für den Abgleich."""
    db: Session | None = None
    companies: list[CompanyEntry] = field(default_factory=list)
    vendor_names: dict[int, str | None] = field(default_factory=dict)

    def vendor_name(self, vendor_id: int) -> str | None:
        if vendor_id not in self.vendor_names:
            from app.models.vendor import Vendor
            v = self.db.get(Vendor, vendor_id) if self.db is not None else None
            self.vendor_names[vendor_id] = v.name if v is not None else None
        return self.vendor_names[vendor_id]


def entry_from_row(c: OwnCompany) -> CompanyEntry:
    names = [n for n in [normalize_name(x) for x in [c.name, *(c.aliases or [])]] if n]
    return CompanyEntry(
        c.id, c.name or (c.aliases or [""])[0] or (c.vat_ids or [""])[0] or (c.ibans or [""])[0] or f"#{c.id}", names,
        {v for v in map(normalize_vat, c.vat_ids or []) if v}, {v for v in map(normalize_tax_number, c.tax_numbers or []) if v},
        {v for v in map(normalize_iban, c.ibans or []) if v},
    )


def load_parties(db: Session) -> Parties:
    rows = db.query(OwnCompany).filter(OwnCompany.active.is_(True)).order_by(OwnCompany.id).all()
    return Parties(db, [entry_from_row(c) for c in rows])


def match(parties: Parties, name: str | None = None, vat: str | None = None, tax: str | None = None,
          iban: str | None = None) -> tuple[CompanyEntry, str] | None:
    """Bester Treffer in der Liste (Reihenfolge der Sicherheit) oder None."""
    nvat, ntax, niban, nname = normalize_vat(vat), normalize_tax_number(tax), normalize_iban(iban), normalize_name(name)
    for key, val, attr in (("ust_id", nvat, "vat"), ("iban", niban, "iban"), ("steuernummer", ntax, "tax")):
        if val:
            for c in parties.companies:
                if val in getattr(c, attr):
                    return c, key
    if nname:
        for c in parties.companies:
            if nname in c.names:
                return c, "name"
        best: tuple[float, CompanyEntry] | None = None
        for c in parties.companies:
            for n in c.names:
                s = name_similarity(nname, n)
                if s >= SIMILAR and (best is None or s > best[0]):
                    best = (s, c)
        if best:
            return best[1], "aehnlich"
    return None


def _res(name: str, severity: str, status: str, message: str, root_id: int) -> dict:
    return {"rule_id": None, "name": name, "severity": severity, "entity": "beleg", "record_id": root_id, "position_index": 0,
            "formula": "", "status": status, "message": message}


def role_results(parties: Parties, sup: dict, r_name, r_vat, root_id: int = 0) -> list[dict]:
    """Die beiden Fehler-Prüfungen (Lieferant = eigene Gesellschaft / Lieferant = Empfänger) aus gelesenen Werten (Dict `lieferant`)."""
    s_name, s_vat, s_tax, s_iban = sup.get("name"), sup.get("ust_id_nr"), sup.get("steuernummer"), sup.get("iban")
    out: list[dict] = []
    has_supplier = bool(normalize_name(s_name) or normalize_vat(s_vat) or normalize_iban(s_iban))
    if parties.companies and has_supplier:
        m = match(parties, s_name, s_vat, s_tax, s_iban)
        if m:
            out.append(_res("Lieferant ist keine eigene Gesellschaft", "error", "failed",
                            f"Lieferant „{s_name or s_vat or s_iban}“ entspricht der eigenen Gesellschaft „{m[0].label}“ ({REASONS[m[1]]}) — "
                            "vermutlich wurde der Empfänger statt des Lieferanten gelesen.", root_id))
        else:
            out.append(_res("Lieferant ist keine eigene Gesellschaft", "error", "ok", "", root_id))
    if has_supplier and (normalize_name(r_name) or normalize_vat(r_vat)):
        same = (normalize_vat(s_vat) and normalize_vat(s_vat) == normalize_vat(r_vat)) or (
            normalize_name(s_name) and normalize_name(r_name) and name_similarity(s_name, r_name) >= SIMILAR)
        out.append(_res("Lieferant ist nicht der Rechnungsempfänger", "error", "failed" if same else "ok",
                        f"Lieferant „{s_name}“ und Rechnungsempfänger „{r_name}“ sind dieselbe Firma — Rollen vermutlich vertauscht oder doppelt gelesen." if same else "",
                        root_id))
    return out


def role_errors(parties: Parties, parsed: dict) -> list[str]:
    """Meldungen der Rollenfehler in einer (noch ungespeicherten) KI-Antwort — für die zweite Auslesung."""
    sup = parsed.get("lieferant") if isinstance(parsed.get("lieferant"), dict) else {}
    return [r["message"] for r in role_results(parties, sup, parsed.get("empfaenger_name"), parsed.get("empfaenger_ust_id_nr")) if r["status"] == "failed"]


def party_checks(root, parties: Parties) -> list[dict]:
    """Rollenprüfungen für den Beleg (`root` = Wurzel-Knoten aus `rule_engine.load_tree`)."""
    data = root.data or {}
    sup = (data.get("_master") or {}).get("lieferant") or {}
    s_name = sup.get("name")
    r_name, r_vat = data.get("empfaenger_name"), data.get("empfaenger_ust_id_nr")
    out = role_results(parties, sup, r_name, r_vat, root.record_id)

    if parties.companies and (normalize_name(r_name) or normalize_vat(r_vat)):
        m = match(parties, r_name, r_vat)
        out.append(_res("Rechnungsempfänger ist eine eigene Gesellschaft", "warn", "ok" if m else "failed",
                        "" if m else f"Rechnungsempfänger „{r_name or r_vat}“ steht nicht in der Liste eigener Gesellschaften "
                                     "(falsch gelesen, an eine andere Firma gerichtet oder noch nicht erfasst).", root.record_id))

    vendor_id = data.get("vendor_id")
    if vendor_id and normalize_name(s_name):
        known = parties.vendor_name(int(vendor_id))
        if known and name_similarity(known, s_name) < 0.5:
            out.append(_res("Lieferant passt zum bekannten Lieferanten", "warn", "failed",
                            f"IBAN/USt-IdNr. gehören zum bekannten Lieferanten „{known}“, gelesen wurde aber „{s_name}“ — Name oder Kennung falsch gelesen?",
                            root.record_id))
        elif known:
            out.append(_res("Lieferant passt zum bekannten Lieferanten", "warn", "ok", "", root.record_id))
    return out


def suggestions(db: Session, limit: int = 30, min_count: int = 2) -> list[dict]:
    """Häufige Rechnungsempfänger, die noch nicht in der Liste stehen (vermutlich eigene Gesellschaften)."""
    from app.models.schema_def import ExtractedRecord
    name_col = ExtractedRecord.data["empfaenger_name"].as_string()
    vat_col = ExtractedRecord.data["empfaenger_ust_id_nr"].as_string()
    rows = (db.query(name_col, vat_col, func.count(ExtractedRecord.id))
            .filter(ExtractedRecord.parent_record_id.is_(None), name_col.isnot(None))
            .group_by(name_col, vat_col).order_by(func.count(ExtractedRecord.id).desc()).limit(5000).all())
    parties = load_parties(db)
    groups: list[dict] = []
    for name, vat, n in rows:
        if not name or not name.strip():
            continue
        key = normalize_name(name)
        g = next((x for x in groups if x["key"] == key or name_similarity(x["key"], key) >= SIMILAR), None)
        if g is None:
            g = {"key": key, "count": 0, "names": Counter(), "vats": Counter()}
            groups.append(g)
        g["count"] += n
        g["names"][name.strip()] += n
        if vat and vat.strip():
            g["vats"][vat.strip()] += n
    out = []
    for g in groups:
        if g["count"] < min_count:
            continue
        top = g["names"].most_common(1)[0][0]
        vat = g["vats"].most_common(1)[0][0] if g["vats"] else None
        if match(parties, top, vat):
            continue
        out.append({"name": top, "aliases": [n for n in g["names"] if n != top][:5], "vat_id": vat, "count": g["count"]})
    return out[:limit]
