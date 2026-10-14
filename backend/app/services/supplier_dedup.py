"""
Dublettensuche für Lieferanten (reine Logik, keine DB).

Lieferanten dürfen mehrere IBANs haben (Attribut `ibans`, sonst `iban`).

Zwei Lieferanten gelten als Dublettenkandidaten, wenn sie in einem Merkmal übereinstimmen:

  Merkmal        Konfidenz   Beschreibung
  iban           hoch        gleiche IBAN (Leerzeichen / Groß-Kleinschreibung egal)
  vat_id         hoch        gleiche USt-IdNr.
  tax_number     hoch        gleiche Steuernummer (Trennzeichen egal)
  name           mittel      gleicher Name nach Normalisierung (ohne Rechtsform, Umlaute/Satzzeichen)
  name_similar   niedrig     sehr ähnlicher Name (≥ 88 % Übereinstimmung)

Über alle Merkmale hinweg werden die Paare zu Gruppen verbunden (Union-Find). Es wird nie
automatisch zusammengeführt — der Nutzer entscheidet.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

SIMILARITY_THRESHOLD = 0.88

REASON_LABELS = {
    "iban": "gleiche IBAN",
    "vat_id": "gleiche USt-IdNr.",
    "tax_number": "gleiche Steuernummer",
    "name": "gleicher Name (ohne Rechtsform)",
    "name_similar": "ähnlicher Name",
}

# Rechtsformen und Füllwörter, die beim Namensvergleich ignoriert werden
_LEGAL_TOKENS = {
    "gmbh", "mbh", "ag", "kg", "ohg", "ug", "gbr", "eg", "se", "kgaa", "partg", "mbb",
    "co", "und", "haftungsbeschraenkt", "inc", "ltd", "llc", "sarl", "bv", "nv", "sa", "srl",
    "spa", "oy",
}
# Rechtsformen mit Punkten (e.K., G.m.b.H., S.A. …) — vor der Tokenisierung entfernen
_SHORT_FORMS = re.compile(
    r"\b(e\.\s?k\.?|e\.\s?v\.?|e\.\s?g\.?|e\.\s?kfr\.?|g\.\s?m\.\s?b\.\s?h\.?|a\.\s?g\.?|"
    r"k\.\s?g\.?|u\.\s?g\.?|s\.\s?a\.?|s\.\s?r\.\s?l\.?|s\.\s?p\.\s?a\.?)(?=\s|$|,)",
    re.IGNORECASE,
)


def normalize_name(name: str | None) -> str:
    """'Müller & Söhne GmbH & Co. KG' → 'mueller soehne'. Leerer String, wenn nichts übrig bleibt."""
    if not name:
        return ""
    s = _SHORT_FORMS.sub(" ", name.casefold())
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    tokens = re.findall(r"[a-z0-9]+", s)
    kept = [t for t in tokens if t not in _LEGAL_TOKENS]
    return " ".join(kept or tokens)


def _alnum_upper(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def normalize_iban(value: str | None) -> str:
    v = _alnum_upper(value)
    return v if len(v) >= 15 else ""


def normalize_vat(value: str | None) -> str:
    v = _alnum_upper(value)
    return v if len(v) >= 6 else ""


def normalize_tax_number(value: str | None) -> str:
    v = _alnum_upper(value)
    return v if len(v) >= 6 else ""


@dataclass
class DuplicateGroup:
    supplier_ids: list[int]
    reasons: list[str] = field(default_factory=list)       # Schlüssel aus REASON_LABELS
    confidence: str = "low"                                # high | medium | low


def _confidence(reasons: Iterable[str]) -> str:
    rs = set(reasons)
    if rs & {"iban", "vat_id", "tax_number"}:
        return "high"
    return "medium" if "name" in rs else "low"


def find_duplicate_groups(suppliers: Iterable[Any]) -> list[DuplicateGroup]:
    """
    Gruppiert Lieferanten (Objekte mit id, name, iban, vat_id, tax_number) nach Dublettenmerkmalen.
    Gruppen sind nach Konfidenz (hoch zuerst), dann nach kleinster ID sortiert.
    """
    items = list(suppliers)
    parent: dict[int, int] = {s.id: s.id for s in items}
    reasons_of_root: dict[int, set[str]] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int, reason: str) -> None:
        ra, rb = find(a), find(b)
        merged = reasons_of_root.pop(ra, set()) | reasons_of_root.pop(rb, set()) | {reason}
        if ra != rb:
            parent[rb] = ra
        reasons_of_root[find(a)] = merged

    # 1) exakte Schlüssel
    def _ibans(s: Any) -> list[str]:
        raw = getattr(s, "ibans", None)
        if raw is None:
            raw = [getattr(s, "iban", None)]
        return [k for k in (normalize_iban(v) for v in raw) if k]

    for reason, keys_of in (("iban", _ibans),
                            ("vat_id", lambda s: [normalize_vat(s.vat_id)]),
                            ("tax_number", lambda s: [normalize_tax_number(s.tax_number)]),
                            ("name", lambda s: [normalize_name(s.name)])):
        buckets: dict[str, list[int]] = {}
        for s in items:
            for key in set(keys_of(s)):
                if key:
                    buckets.setdefault(key, []).append(s.id)
        for ids in buckets.values():
            for other in ids[1:]:
                union(ids[0], other, reason)

    # 2) ähnliche Namen (nur innerhalb gleicher Anfangsbuchstaben-Blöcke → bleibt auch bei vielen Lieferanten schnell)
    named = [(s.id, normalize_name(s.name)) for s in items]
    blocks: dict[str, list[tuple[int, str]]] = {}
    for sid, n in named:
        if len(n) >= 5:
            blocks.setdefault(n[:3], []).append((sid, n))
    for members in blocks.values():
        for i, (id_a, a) in enumerate(members):
            for id_b, b in members[i + 1:]:
                if a != b and find(id_a) != find(id_b) \
                        and SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD:
                    union(id_a, id_b, "name_similar")

    groups: dict[int, list[int]] = {}
    for s in items:
        groups.setdefault(find(s.id), []).append(s.id)
    result = []
    for root, ids in groups.items():
        if len(ids) < 2:
            continue
        reasons = sorted(reasons_of_root.get(root, set()), key=list(REASON_LABELS).index)
        result.append(DuplicateGroup(sorted(ids), reasons, _confidence(reasons)))
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(result, key=lambda g: (order[g.confidence], g.supplier_ids[0]))
