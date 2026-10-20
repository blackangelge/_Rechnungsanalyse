"""
Plausibilitätsprüfung für extrahierte Rechnungsdaten.

Reine Rechenlogik ohne Datenbank- oder Netzwerkzugriff (gut testbar).

Geprüft wird (alle Werte werden gegeneinander durchgerechnet):
  1. Position:  Menge × Einzelpreis  ≙  Positionspreis   (Rabatt und abweichende
                Preiseinheit wie "je t" bei Menge in kg werden berücksichtigt)
  2. Summe:     Σ Positionspreise  ≙  Netto  (oder Brutto − Steuer, oder Brutto)
  3. Beträge:   Netto + Steuer  ≙  Brutto,   Steuerbetrag  ≙  Netto × Steuersatz
  4. Steuersätze: Σ Positionen je Steuersatz  ≙  Nettobetrag je Steuersatz der USt-Zusammenfassung,
                Steuer aus den Positionen berechnet  ≙  ausgewiesene Steuer

Das Ergebnis enthält zusätzlich die „Durchrechnung" (`calc`): jede Rechnung mit erwartetem und
tatsächlichem Wert, damit nachvollziehbar ist, was geprüft wurde.

Einheitenfehler (Tonne ↔ kg, Liter ↔ ml/hl, je 100 / je 1000 Stück …) werden
erkannt. Lässt sich der Fehler eindeutig einer Position zuordnen, wird der
Positionspreis automatisch korrigiert (Originalwert bleibt in `corrections`). Bestätigen sich
Positionen, Netto und Steuer gegenseitig, das Brutto aber abweicht, wird das Brutto
(`total_amount`) auf Netto + Steuer korrigiert; fehlt es, wird es berechnet.
Alles Weitere meldet `status = "needs_review"` zur weiteren Behandlung
(zweite KI-Abfrage bzw. manuelle Prüfung).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

# ─── Status-Werte (in invoice_extractions.validation_status gespeichert) ──────
STATUS_OK = "ok"
STATUS_CORRECTED = "corrected"            # automatisch (rechnerisch) korrigiert
STATUS_AI_CORRECTED = "ai_corrected"      # durch zweite KI-Abfrage korrigiert
STATUS_NEEDS_REVIEW = "needs_review"      # manuelle Prüfung nötig
STATUS_MANUAL_OK = "manual_ok"            # vom Nutzer als geprüft bestätigt
STATUS_UNCHECKED = "unchecked"            # nichts prüfbar (z.B. keine Positionen)

# ─── Einheiten ────────────────────────────────────────────────────────────────
# Schlüssel: normalisierte Schreibweise → (Dimension, Faktor zur Basiseinheit)
# Basiseinheiten: Masse = kg, Volumen = l, Länge = m, Fläche = m², Anzahl = 1
_UNITS: dict[str, tuple[str, float]] = {}


def _register(dimension: str, factor: float, *names: str) -> None:
    for n in names:
        _UNITS[n] = (dimension, factor)


_register("mass", 1000.0, "t", "to", "tonne", "tonnen", "tonn", "tn")
_register("mass", 1.0, "kg", "kilo", "kilogramm")
_register("mass", 0.001, "g", "gramm")
_register("mass", 0.000001, "mg")
_register("volume", 1.0, "l", "ltr", "liter", "litre")
_register("volume", 0.001, "ml", "milliliter")
_register("volume", 0.01, "cl")
_register("volume", 0.1, "dl")
_register("volume", 100.0, "hl", "hektoliter")
_register("volume", 1000.0, "m3", "cbm", "kubikmeter")
_register("length", 1.0, "m", "meter", "lfm", "lm")
_register("length", 0.01, "cm")
_register("length", 0.001, "mm")
_register("length", 1000.0, "km")
_register("area", 1.0, "m2", "qm", "quadratmeter")
_register("count", 1.0, "stk", "st", "stueck", "stück", "pcs", "pc", "ea", "x", "einheit", "e")
_register(
    "pack", 1.0,
    "palette", "pal", "paletten", "karton", "kartons", "ktn", "kiste", "kisten",
    "gebinde", "sack", "säcke", "saecke", "sk", "kanister", "kan", "fass", "fässer",
    "ibc", "eimer", "flasche", "flaschen", "fl", "dose", "dosen", "rolle", "rollen",
    "bund", "paket", "pakete", "pkt", "packung", "set", "container", "beutel", "box",
    "bag", "big-bag", "bigbag", "kasten", "tüte", "tube", "pack",
)

_UNIT_PREFIX_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(.*)$")


@dataclass(frozen=True)
class UnitInfo:
    """Aufgelöste Einheit: Dimension + Faktor zur Basiseinheit (inkl. Vorzahl)."""

    raw: str
    dimension: str | None   # None = unbekannt
    factor: float           # z.B. "100 kg" → 100.0, "t" → 1000.0


def parse_unit(raw: str | None) -> UnitInfo | None:
    """
    Normalisiert eine Einheit ("Tonne", "kg", "100 kg", "m³", "Stück" …).
    Gibt None zurück, wenn kein Text vorhanden ist.
    """
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip().lower()
    mult = 1.0
    m = _UNIT_PREFIX_RE.match(text)
    if m and m.group(2):
        mult = float(m.group(1).replace(",", "."))
        text = m.group(2)
    text = text.replace("³", "3").replace("²", "2").replace(".", "").strip()
    if text.startswith("je "):
        text = text[3:].strip()
    if text.startswith("/"):
        text = text[1:].strip()
    dim_factor = _UNITS.get(text)
    if dim_factor is None:
        return UnitInfo(raw=str(raw), dimension=None, factor=mult)
    return UnitInfo(raw=str(raw), dimension=dim_factor[0], factor=dim_factor[1] * mult)


def unit_factors(unit: UnitInfo | None) -> list[float]:
    """
    Plausible Umrechnungsfaktoren für Positionen mit dieser Einheit:
    Verhältnis zu allen anderen Einheiten derselben Dimension plus die
    typischen Preisbezüge "je 10 / 100 / 1000".
    """
    factors: set[float] = {10.0, 100.0, 1000.0, 0.1, 0.01, 0.001}
    if unit is not None and unit.dimension in ("mass", "volume", "length", "area"):
        for dim, f in _UNITS.values():
            if dim == unit.dimension and f > 0:
                ratio = unit.factor / f
                if abs(ratio - 1.0) > 1e-9:
                    factors.add(round(ratio, 9))
    return sorted(factors)


# ─── Datentypen ───────────────────────────────────────────────────────────────

@dataclass
class Issue:
    code: str
    severity: str          # "info" | "warning" | "error"
    message: str
    position: int | None = None   # 0-basierter Positionsindex
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "severity": self.severity, "message": self.message,
            "position": self.position, "details": self.details,
        }


@dataclass
class ValidationResult:
    status: str
    issues: list[Issue] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)
    # Vereinheitlichung der Einheiten (Gewicht → t): keine Fehlerkorrektur, zählt nicht für den Prüfstatus
    normalizations: list[dict] = field(default_factory=list)
    # Einzelschritt-Berechnung je Position: [{position, label, status: ok|warn|skip, steps: [Text]}]
    position_calcs: list[dict] = field(default_factory=list)
    checks_run: int = 0
    # „Durchrechnung": jede geprüfte Rechnung mit erwartetem/tatsächlichem Wert
    calc: list[dict] = field(default_factory=list)

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.severity in ("warning", "error")]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "issues": [i.to_dict() for i in self.issues],
            "corrections": self.corrections,
            "normalizations": self.normalizations,
            "position_calcs": self.position_calcs,
            "checks_run": self.checks_run,
            "calc": self.calc,
        }


# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tol(value: float) -> float:
    """Toleranz für Rundungsdifferenzen: min. 2 Cent, sonst 0,5 %."""
    return max(0.02, abs(value) * 0.005)


def _close(a: float, b: float, extra: float = 0.0) -> bool:
    return abs(a - b) <= _tol(max(abs(a), abs(b))) + extra


def _fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|eur|euro)", re.IGNORECASE)


def _discounted_values(expected: float, discount: str | None) -> list[float]:
    """Mögliche Positionspreise nach Abzug des Positionsrabatts."""
    if not discount:
        return []
    values: list[float] = []
    for m in _PCT_RE.finditer(discount):
        values.append(expected * (1 - float(m.group(1).replace(",", ".")) / 100))
    for m in _AMOUNT_RE.finditer(discount):
        values.append(expected - float(m.group(1).replace(",", ".")))
    return values


def _raw_positions(raw: dict | None) -> list[dict]:
    pos = (raw or {}).get("positionen")
    return [p if isinstance(p, dict) else {} for p in pos] if isinstance(pos, list) else []


# ─── Hauptfunktion ────────────────────────────────────────────────────────────

def validate_invoice(
    fields: dict,
    positions: list[dict],
    raw: dict | None = None,
    auto_correct: bool = True,
) -> ValidationResult:
    """
    Prüft Rechnungsfelder + Positionen auf Plausibilität.

    Args:
        fields:    Extraktionsfelder (benötigt wird `total_amount` = Brutto).
        positions: Positionen als Dicts (quantity, unit, unit_price, total_price,
                   discount …). Bei auto_correct wird `total_price` in-place korrigiert.
        raw:       Geparstes KI-JSON im verschachtelten Format (optional). Liefert
                   Netto/Steuer sowie `preiseinheit` je Position.
        auto_correct: False = nur prüfen, nichts verändern.
    """
    result = ValidationResult(status=STATUS_UNCHECKED)
    raw_pos = _raw_positions(raw)
    zahlung = (raw or {}).get("zahlungsinformationen") or {}
    originals = [dict(p) for p in positions]        # Ausgangswerte für die Einzelschritt-Berechnung

    # ── 1. Positionsprüfung ──────────────────────────────────────────────────
    pos_scale_notes: dict[int, float] = {}
    for idx, pos in enumerate(positions):
        qty, price, total = _f(pos.get("quantity")), _f(pos.get("unit_price")), _f(pos.get("total_price"))
        if qty is None or price is None or total is None:
            continue
        result.checks_run += 1
        expected = qty * price
        if _close(expected, total):
            _check_unit_vs_price_unit(result, idx, pos, raw_pos, auto_correct)
            continue
        if any(_close(v, total) for v in _discounted_values(expected, pos.get("discount"))):
            continue

        unit = parse_unit(pos.get("unit"))
        price_unit = parse_unit((raw_pos[idx] if idx < len(raw_pos) else {}).get("preiseinheit"))
        scale = _find_scale(expected, total, unit, price_unit)
        if scale is not None:
            pos_scale_notes[idx] = scale
            result.issues.append(Issue(
                "price_unit_differs", "info",
                (f"Position {idx + 1}: Menge × Einzelpreis ({_fmt(expected)}) weicht um Faktor "
                 f"{_fmt_factor(scale)} vom Positionspreis ({_fmt(total)}) ab — der Einzelpreis "
                 f"bezieht sich vermutlich auf eine andere Einheit als die Menge "
                 f"({pos.get('unit') or '?'})."),
                position=idx,
                details={"expected": expected, "total": total, "factor": scale},
            ))
        else:
            result.issues.append(Issue(
                "position_mismatch", "warning",
                (f"Position {idx + 1}: Menge × Einzelpreis ({_fmt(expected)}) passt nicht zum "
                 f"Positionspreis ({_fmt(total)})."),
                position=idx,
                details={"expected": expected, "total": total},
            ))

    # ── Ausgangswerte ────────────────────────────────────────────────────────
    gross = _f(fields.get("total_amount"))
    net = _f(zahlung.get("gesamtbetrag_netto"))
    discount = _f(fields.get("discount_amount"))
    totals = [_f(p.get("total_price")) for p in positions]
    priced = [t for t in totals if t is not None]
    tax_rows = [r for r in (zahlung.get("umsatzsteuer_zusammenfassung") or []) if isinstance(r, dict)]
    tax_total = sum(_f(r.get("steuerbetrag")) or 0.0 for r in tax_rows) if tax_rows else None

    def calc(label: str, expected: float | None, actual: float | None, note: str | None = None,
             extra: float = 0.0) -> bool | None:
        """Hängt eine Zeile an die Durchrechnung an. Gibt True/False zurück (None = nicht prüfbar)."""
        if expected is None or actual is None:
            return None
        ok = _close(expected, actual, extra=extra)
        result.calc.append({
            "label": label, "expected": round(expected, 2), "actual": round(actual, 2),
            "status": "ok" if ok else "warn", "note": note,
        })
        return ok

    # Positionen: Menge × Preis (Ergebnis von Schritt 1 zusammenfassen)
    checked = [i for i, p in enumerate(positions)
               if _f(p.get("quantity")) is not None and _f(p.get("unit_price")) is not None
               and _f(p.get("total_price")) is not None]
    if checked:
        bad = [i for i in checked if any(x.position == i and x.code == "position_mismatch" for x in result.issues)]
        result.calc.append({
            "label": f"Menge × Einzelpreis ↔ Positionspreis ({len(checked)} Position(en))",
            "expected": None, "actual": None,
            "status": "warn" if bad else "ok",
            "note": ("Abweichung bei Position " + ", ".join(str(i + 1) for i in bad)) if bad else "alle stimmig",
        })

    # ── 2. Summenprüfung ─────────────────────────────────────────────────────
    # Positionssumme gilt als stimmig, wenn sie zum Netto, zum Brutto (Positionen brutto, z.B.
    # Kassenbon) oder zu Brutto − Steuer passt; ein ausgewiesener Gesamtrabatt wird berücksichtigt.
    targets = [v for v in (net, gross) if v is not None]
    if gross is not None and tax_total is not None:
        targets.append(gross - tax_total)

    def sum_ok(total: float) -> bool:
        candidates = [total] + ([total - discount] if discount else [])
        return any(_sum_matches(c, targets) for c in candidates)

    if priced and targets:
        result.checks_run += 1
        if not sum_ok(sum(priced)):
            corrected = (
                _try_auto_correct(positions, totals, targets, raw_pos)
                if auto_correct else None
            )
            if corrected:
                for c in corrected:
                    result.corrections.append(c)
                    i = c["position"]
                    result.issues.append(Issue(
                        "auto_corrected", "info",
                        (f"Position {i + 1}: Positionspreis automatisch von {_fmt(c['from'])} "
                         f"auf {_fmt(c['to'])} korrigiert (Faktor {_fmt_factor(c['factor'])}), "
                         f"damit die Summe zum Rechnungsbetrag passt."),
                        position=i, details=c,
                    ))
            else:
                result.issues.append(Issue(
                    "sum_mismatch", "warning",
                    (f"Summe der Positionen ({_fmt(sum(priced))}) passt weder zum Netto- "
                     f"noch zum Brutto-Rechnungsbetrag "
                     f"({' / '.join(_fmt(v) for v in (net, gross) if v is not None)})."),
                    details={"positions_sum": sum(priced), "net": net, "gross": gross},
                ))
        priced = [_f(p.get("total_price")) for p in positions if _f(p.get("total_price")) is not None]  # nach Korrektur
    positions_sum = sum(priced) if priced else None

    # Netto ↔ Σ Positionen (nur sinnvoll, wenn die Positionen netto geführt sind)
    positions_are_gross = (
        positions_sum is not None and gross is not None and _close(positions_sum, gross)
        and (net is None or not _close(positions_sum, net))
    )
    if positions_sum is not None and net is not None and not positions_are_gross:
        result.checks_run += 1
        ok = calc("Σ Positionspreise ↔ Netto (Rechnung)", positions_sum, net, extra=0.01)
        if ok is False and not (discount and _close(positions_sum - discount, net)):
            # Bestätigen Brutto − Steuer die Positionssumme, ist der ausgewiesene Netto-Wert das Problem
            hint = ""
            if gross is not None and tax_total is not None and _close(positions_sum, gross - tax_total):
                hint = (f" Positionen und Brutto − Steuer ({_fmt(gross - tax_total)}) stimmen überein — "
                        f"der Nettobetrag wurde wohl falsch gelesen.")
            if not any(x.code == "sum_mismatch" for x in result.issues):
                result.issues.append(Issue(
                    "net_mismatch", "warning",
                    f"Summe der Positionen ({_fmt(positions_sum)}) passt nicht zum Nettobetrag ({_fmt(net)}).{hint}",
                    details={"positions_sum": positions_sum, "net": net, "implied_net": (gross - tax_total) if (gross is not None and tax_total is not None) else None},
                ))
    elif positions_sum is not None and positions_are_gross:
        calc("Σ Positionspreise ↔ Brutto (Positionen brutto)", positions_sum, gross, extra=0.01)
    elif positions_sum is not None and net is None and gross is not None and tax_total is not None:
        calc("Σ Positionspreise ↔ Brutto − Steuer", positions_sum, gross - tax_total, extra=0.01)

    # ── 3. Steuer je Steuersatz, Netto + Steuer = Brutto ─────────────────────
    for row in tax_rows:
        rate, base, amount = _f(row.get("steuersatz")), _f(row.get("nettobetrag")), _f(row.get("steuerbetrag"))
        if rate is None or base is None or amount is None:
            continue
        result.checks_run += 1
        if calc(f"Steuer {rate:g} %: Netto × Satz ↔ Steuerbetrag", base * rate / 100, amount) is False:
            result.issues.append(Issue(
                "tax_mismatch", "warning",
                (f"Steuerbetrag {_fmt(amount)} passt nicht zu {_fmt(base)} × {rate:g} % "
                 f"({_fmt(base * rate / 100)})."),
                details={"rate": rate, "net": base, "tax": amount},
            ))

    # Positionen je Steuersatz ↔ USt-Zusammenfassung (nur wenn jede bepreiste Position einen Satz hat)
    rated: dict[float, float] = {}
    all_rated = bool(priced)
    for i, p in enumerate(positions):
        total = _f(p.get("total_price"))
        if total is None:
            continue
        rate = _f((raw_pos[i] if i < len(raw_pos) else {}).get("steuersatz"))
        if rate is None:
            all_rated = False
            break
        rated[rate] = rated.get(rate, 0.0) + total
    rate_issue = False
    if all_rated and rated and tax_rows and not positions_are_gross:
        by_rate = {_f(r.get("steuersatz")): r for r in tax_rows if _f(r.get("steuersatz")) is not None}
        for rate, total in sorted(rated.items()):
            row = by_rate.get(rate)
            base = _f(row.get("nettobetrag")) if row else None
            if row is None:
                result.checks_run += 1
                rate_issue = True
                result.calc.append({"label": f"Netto {rate:g} %: Σ Positionen ↔ USt-Zusammenfassung",
                                    "expected": round(total, 2), "actual": None, "status": "warn",
                                    "note": f"Steuersatz {rate:g} % fehlt in der USt-Zusammenfassung"})
                result.issues.append(Issue(
                    "rate_net_mismatch", "warning",
                    f"Positionen mit {rate:g} % Steuer ({_fmt(total)}) fehlen in der USt-Zusammenfassung.",
                    details={"rate": rate, "positions_net": total},
                ))
            elif base is not None:
                result.checks_run += 1
                if calc(f"Netto {rate:g} %: Σ Positionen ↔ USt-Zusammenfassung", total, base, extra=0.01) is False:
                    rate_issue = True
                    result.issues.append(Issue(
                        "rate_net_mismatch", "warning",
                        (f"Positionen mit {rate:g} % Steuer ergeben {_fmt(total)}, die USt-Zusammenfassung "
                         f"weist {_fmt(base)} aus."),
                        details={"rate": rate, "positions_net": total, "summary_net": base},
                    ))
        # Umgekehrt: Steuersätze der USt-Zusammenfassung, zu denen keine Position gehört
        for rate, row in sorted(by_rate.items()):
            base = _f(row.get("nettobetrag"))
            if rate not in rated and base:
                result.checks_run += 1
                rate_issue = True
                result.calc.append({"label": f"Netto {rate:g} %: Σ Positionen ↔ USt-Zusammenfassung",
                                    "expected": None, "actual": round(base, 2), "status": "warn",
                                    "note": f"keine Position mit {rate:g} % Steuer"})
                result.issues.append(Issue(
                    "rate_net_mismatch", "warning",
                    f"Die USt-Zusammenfassung weist {_fmt(base)} mit {rate:g} % aus, aber keine Position hat diesen Steuersatz.",
                    details={"rate": rate, "summary_net": base},
                ))
        if not rate_issue and tax_total is not None:
            tax_calc = sum(round(t * r / 100, 2) for r, t in rated.items())
            result.checks_run += 1
            if calc("Steuer gesamt: aus Positionen berechnet ↔ ausgewiesen", tax_calc, tax_total, extra=0.01) is False:
                result.issues.append(Issue(
                    "tax_from_positions_mismatch", "warning",
                    (f"Aus den Positionen berechnet sich eine Steuer von {_fmt(tax_calc)}, "
                     f"ausgewiesen sind {_fmt(tax_total)}."),
                    details={"computed_tax": tax_calc, "tax": tax_total},
                ))

    # Netto + Steuer ↔ Brutto — bei Abweichung ggf. Brutto korrigieren/berechnen
    if net is not None and tax_total is not None:
        expected_gross = round(net + tax_total, 2)
        result.checks_run += 1
        if gross is None:
            if auto_correct:
                fields["total_amount"] = expected_gross
                gross = expected_gross
                result.corrections.append({"field": "total_amount", "from": None, "to": expected_gross,
                                           "method": "net_plus_tax"})
                result.issues.append(Issue(
                    "total_derived", "info",
                    f"Gesamtbetrag (Brutto) fehlte und wurde aus Netto + Steuer berechnet: {_fmt(expected_gross)}.",
                    details={"to": expected_gross},
                ))
                calc("Netto + Steuer ↔ Brutto", expected_gross, gross)
        elif not _close(expected_gross, gross):
            # Gegenseitig bestätigt: Positionen ≙ Netto und Steuer ≙ Netto × Satz → das Brutto ist der Ausreißer
            tax_consistent = all(
                _close((_f(r.get("nettobetrag")) or 0) * (_f(r.get("steuersatz")) or 0) / 100, _f(r.get("steuerbetrag")) or 0)
                for r in tax_rows if _f(r.get("steuersatz")) is not None and _f(r.get("nettobetrag")) is not None
            )
            confirmed = (positions_sum is not None and _close(positions_sum, net, extra=0.01) and tax_consistent)
            if auto_correct and confirmed:
                old = gross
                fields["total_amount"] = expected_gross
                gross = expected_gross
                result.corrections.append({"field": "total_amount", "from": old, "to": expected_gross,
                                           "method": "net_plus_tax"})
                result.issues.append(Issue(
                    "total_corrected", "info",
                    (f"Gesamtbetrag (Brutto) von {_fmt(old)} auf {_fmt(expected_gross)} korrigiert: Positionen, "
                     f"Netto ({_fmt(net)}) und Steuer ({_fmt(tax_total)}) bestätigen sich gegenseitig."),
                    details={"from": old, "to": expected_gross},
                ))
                calc("Netto + Steuer ↔ Brutto (korrigiert)", expected_gross, gross)
            else:
                calc("Netto + Steuer ↔ Brutto", expected_gross, gross)
                result.issues.append(Issue(
                    "gross_mismatch", "warning",
                    (f"Netto ({_fmt(net)}) + Steuer ({_fmt(tax_total)}) ergibt "
                     f"{_fmt(net + tax_total)}, Brutto laut Rechnung: {_fmt(gross)}."),
                    details={"net": net, "tax": tax_total, "gross": gross},
                ))
        else:
            calc("Netto + Steuer ↔ Brutto", expected_gross, gross)

    # Mengen und Einheiten bleiben so gespeichert, wie sie auf der Rechnung stehen (Datenbank, Beleg-Ansicht, Artikelsuche und
    # Excel zeigen überall dieselben Originalwerte). Eine Umrechnung auf Tonnen gibt es nur ausdrücklich: Workflow-Knoten
    # „Einheiten vereinheitlichen“ bzw. die Nachrüstung „Einheiten auf Tonnen vereinheitlichen“.

    result.position_calcs = _build_position_calcs(originals, positions, raw_pos, result)

    # ── Status ableiten ──────────────────────────────────────────────────────
    if result.blocking_issues:
        result.status = STATUS_NEEDS_REVIEW
    elif result.corrections:
        result.status = STATUS_CORRECTED
    elif result.checks_run > 0:
        result.status = STATUS_OK
    return result


# ─── interne Helfer ───────────────────────────────────────────────────────────

def _normalize_mass_to_tonnes(
    result: "ValidationResult", positions: list[dict], raw_pos: list[dict], target: str = "t",
    mode: str = "tonne_prices", notes: list[str] | None = None,
) -> None:
    """
    Vereinheitlicht Gewichtspositionen auf eine Einheit — Menge, Einheit und Einzelpreis (€ je Zieleinheit), damit Datenbank,
    Excel-Export und Auswertungen einheitlich vorliegen. Originalwerte stehen in `normalizations` (und im `raw_response`).
    Es wird nur umgerechnet, wenn Menge × neuer Preis weiterhin zum Positionspreis passt (Positionsrabatt berücksichtigt);
    sonst bleibt die Position unverändert.

    Schreibweisen wie „Tonnen“, „TO“, „to“ gelten als Tonne und werden zu „t“ (bzw. „kg“) vereinheitlicht.

    mode (nur für Zieleinheit t):
      • "tonne_prices" (Standard): nur Positionen, die **je Tonne bepreist** sind (Preiseinheit t, oder Menge ohnehin in t).
        Positionen mit Preis je kg/g (oder unbekannter Preiseinheit bei Menge in kg/g) bleiben unverändert — dort wäre
        „0,0005 t zu 12.000 €/t“ unsinnig.
      • "all": alle Gewichtspositionen.
    Für die Zieleinheit kg wird immer alles umgerechnet.
    """
    tf = 1000.0 if target == "t" else 1.0          # Faktor der Zieleinheit in kg

    def note(i: int, text: str) -> None:
        if notes is not None:
            notes.append(f"Position {i + 1}: {text}")

    for idx, pos in enumerate(positions):
        unit = parse_unit(pos.get("unit"))
        qty = _f(pos.get("quantity"))
        if unit is None or unit.dimension != "mass" or qty is None:
            note(idx, f"keine Gewichtseinheit/Menge (Einheit „{pos.get('unit')}“, Menge {pos.get('quantity')}) — nicht angefasst")
            continue
        price_unit = parse_unit((raw_pos[idx] if idx < len(raw_pos) else {}).get("preiseinheit"))
        if target == "t" and mode == "tonne_prices":
            ref = price_unit if price_unit is not None else unit
            if ref.dimension != "mass" or abs(ref.factor - 1000.0) > 1e-9:
                note(idx, f"Preis nicht je Tonne (Preiseinheit „{(price_unit.raw if price_unit else pos.get('unit'))}“) — "
                          "im Modus „nur Preis je Tonne“ nicht umgerechnet")
                continue                                                   # Preis je kg/g/… → nicht anfassen
        if abs(unit.factor - tf) < 1e-9 and not re.match(r"^\s*\d", str(pos.get("unit"))):
            if str(pos.get("unit")).strip() != target:                      # „Tonnen“, „TO“ … → „t“
                result.normalizations.append({
                    "position": idx, "field": "normalize",
                    "from": {"quantity": qty, "unit": pos.get("unit"), "unit_price": _f(pos.get("unit_price"))},
                    "to": {"quantity": qty, "unit": target, "unit_price": _f(pos.get("unit_price"))},
                })
                pos["unit"] = target
            else:
                note(idx, f"steht bereits in {target}")
            continue                                                       # steht schon in der Zieleinheit
        qty_t = qty * unit.factor / tf
        price, total = _f(pos.get("unit_price")), _f(pos.get("total_price"))
        new_price: float | None = None
        if price is not None:
            pu = price_unit or unit
            candidates = []
            if pu.dimension == "mass":
                candidates.append(price * tf / pu.factor)               # Preis bezog sich auf pu
            candidates.append(price)                                      # Preis galt bereits je Zieleinheit
            if total is None:
                new_price = candidates[0]
            else:
                for c in candidates:
                    exp = qty_t * c
                    if _close(exp, total) or any(_close(v, total) for v in _discounted_values(exp, pos.get("discount"))):
                        new_price = c
                        break
                if new_price is None:
                    note(idx, f"Menge × Preis ({qty_t:g} × {price:g}) passt nicht zum Positionspreis ({total:g}) — nicht umgerechnet")
                    continue                                              # passt nicht → nicht anfassen
        before = {"quantity": qty, "unit": pos.get("unit"), "unit_price": price}
        pos["quantity"] = round(qty_t, 6)
        pos["unit"] = target
        if new_price is not None:
            pos["unit_price"] = round(new_price, 6)
        result.normalizations.append({
            "position": idx, "field": "normalize", "from": before,
            "to": {"quantity": pos["quantity"], "unit": target, "unit_price": pos.get("unit_price")},
        })


def _check_unit_vs_price_unit(
    result: "ValidationResult", idx: int, pos: dict, raw_pos: list[dict], auto_correct: bool,
) -> None:
    """
    Menge × Einzelpreis ergibt den Positionspreis, aber Mengen- und Preiseinheit sind verschieden
    (z.B. "25,88 kg" bei Preis "421,10 je t"). Dann kann die Menge zahlenmäßig nur in der Preiseinheit
    gemeint sein — die KI hat z.B. 25.880 kg zu 25,88 (t) umgerechnet, aber "kg" stehen lassen.
    Auswertungen nach Einheit wären sonst falsch.
    """
    price_raw = (raw_pos[idx] if idx < len(raw_pos) else {}).get("preiseinheit")
    unit, price_unit = parse_unit(pos.get("unit")), parse_unit(price_raw)
    if not (unit and price_unit and unit.dimension and unit.dimension == price_unit.dimension):
        return
    if abs(unit.factor - price_unit.factor) < 1e-9:
        return
    old = pos.get("unit")
    if auto_correct:
        pos["unit"] = str(price_raw).strip()
        result.corrections.append({
            "position": idx, "field": "unit", "from": old, "to": pos["unit"],
            "method": "price_unit",
        })
        result.issues.append(Issue(
            "unit_corrected", "info",
            (f"Position {idx + 1}: Mengeneinheit von „{old}“ auf „{pos['unit']}“ korrigiert — "
             f"Menge × Einzelpreis geht nur auf, wenn die Menge in der Preiseinheit steht."),
            position=idx, details={"from": old, "to": pos["unit"]},
        ))
    else:
        result.issues.append(Issue(
            "unit_mismatch", "warning",
            (f"Position {idx + 1}: Menge ist in „{old}“ angegeben, der Preis gilt aber je "
             f"„{price_raw}“, und Menge × Preis geht ohne Umrechnung auf — die Mengeneinheit stimmt vermutlich nicht."),
            position=idx, details={"unit": old, "price_unit": price_raw},
        ))


def _n(v: Any) -> str:
    """Zahl für Rechenschritte: deutsch, bis zu 4 Nachkommastellen, ohne überflüssige Nullen."""
    f = _f(v)
    if f is None:
        return "–"
    text = f"{f:,.4f}".rstrip("0").rstrip(".") if "." in f"{f:,.4f}" else f"{f:,.4f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _build_position_calcs(
    originals: list[dict], positions: list[dict], raw_pos: list[dict], result: "ValidationResult",
) -> list[dict]:
    """
    Einzelschritt-Berechnung je Position: wie aus Menge, Einheit, Einzelpreis, Nachlass und Preiseinheit der
    Positionspreis entsteht, ob er zur Rechnung passt, und welche Korrekturen/Vereinheitlichungen danach griffen.
    """
    out: list[dict] = []
    for idx, (o, now) in enumerate(zip(originals, positions)):
        if idx >= 300:
            break
        label = str(o.get("product_description") or o.get("product_name") or f"Position {idx + 1}")[:80]
        qty, price, total = _f(o.get("quantity")), _f(o.get("unit_price")), _f(o.get("total_price"))
        unit = o.get("unit") or ""
        pu_raw = (raw_pos[idx] if idx < len(raw_pos) else {}).get("preiseinheit")
        steps: list[str] = []
        status = "ok"
        if qty is None or price is None or total is None:
            missing = [n for n, v in (("Menge", qty), ("Einzelpreis", price), ("Gesamtpreis", total)) if v is None]
            steps.append(f"Nicht prüfbar — es fehlt: {', '.join(missing)}.")
            status = "skip"
        else:
            expected = qty * price
            per = f" je {pu_raw}" if pu_raw else ""
            steps.append(f"Menge × Einzelpreis = {_n(qty)} {unit} × {_n(price)} €{per} = {_fmt(expected)} €".replace("  ", " "))
            result_value, explained = expected, False
            if _close(expected, total):
                explained = True
            else:
                for v in _discounted_values(expected, o.get("discount")):
                    if _close(v, total):
                        steps.append(f"Nachlass „{o.get('discount')}“ abgezogen = {_fmt(v)} €")
                        result_value, explained = v, True
                        break
            if not explained:
                u, pu = parse_unit(o.get("unit")), parse_unit(pu_raw)
                scale = _find_scale(expected, total, u, pu)
                if scale is not None:
                    result_value, explained = expected * scale, True
                    steps.append(
                        f"Mengeneinheit „{unit}“ und Preiseinheit „{pu_raw or '?'}“ unterscheiden sich → "
                        f"Faktor {_fmt_factor(scale)}: {_fmt(expected)} € × {_fmt_factor(scale)} = {_fmt(result_value)} €")
            diff = result_value - total
            if explained:
                steps.append(f"Positionspreis laut Rechnung: {_fmt(total)} € → ✓ stimmt")
            else:
                status = "warn"
                steps.append(f"Positionspreis laut Rechnung: {_fmt(total)} € → ✗ weicht um {_fmt(abs(diff))} € ab")
        for c in result.corrections:
            if c.get("position") != idx:
                continue
            if c.get("field") == "unit":
                steps.append(f"Korrektur: Mengeneinheit „{c.get('from')}“ → „{c.get('to')}“ (Menge × Preis geht nur in der Preiseinheit auf)")
            elif "factor" in c:
                steps.append(f"Korrektur: Positionspreis {_fmt(_f(c.get('from')) or 0)} € → {_fmt(_f(c.get('to')) or 0)} € "
                             f"(Faktor {_fmt_factor(c['factor'])}, damit die Summe zur Rechnung passt)")
        for n in result.normalizations:
            if n.get("position") == idx:
                a, b = n["from"], n["to"]
                steps.append(
                    f"Vereinheitlicht: {_n(a.get('quantity'))} {a.get('unit') or ''} × {_n(a.get('unit_price'))} € → "
                    f"{_n(b.get('quantity'))} {b.get('unit')} × {_n(b.get('unit_price'))} € je {b.get('unit')}")
        nq, np_, nt = _f(now.get("quantity")), _f(now.get("unit_price")), _f(now.get("total_price"))
        if nq is not None and np_ is not None and nt is not None:
            steps.append(f"Gespeichert: {_n(nq)} {now.get('unit') or ''} × {_n(np_)} € = {_fmt(nq * np_)} € (Positionspreis {_fmt(nt)} €)")
        out.append({"position": idx, "label": label, "status": status, "steps": steps})
    return out


def _fmt_factor(f: float) -> str:
    return f"{f:g}".replace(".", ",")


def _find_scale(
    expected: float, total: float, unit: UnitInfo | None, price_unit: UnitInfo | None,
) -> float | None:
    """
    Findet einen Faktor, der die Differenz Menge×Preis ↔ Positionspreis erklärt
    (total ≈ expected × Faktor). Bevorzugt den Faktor aus Mengen-/Preiseinheit.
    """
    if expected == 0 or total == 0:
        return None
    candidates: list[float] = []
    if unit and price_unit and unit.dimension and unit.dimension == price_unit.dimension:
        candidates.append(unit.factor / price_unit.factor)
    candidates.extend(1 / f for f in unit_factors(unit))
    for f in candidates:
        if _close(expected * f, total):
            return round(f, 9)
    return None


def _sum_matches(positions_sum: float, targets: list[float]) -> bool:
    # Mehrere Positionen runden einzeln → Toleranz wächst mit der Anzahl leicht mit
    return any(_close(positions_sum, t, extra=0.01) for t in targets)


def _try_auto_correct(
    positions: list[dict],
    totals: list[float | None],
    targets: list[float],
    raw_pos: list[dict],
) -> list[dict] | None:
    """
    Sucht die kleinste Menge von Positionen (1, dann 2), deren Positionspreis
    mit einem plausiblen Einheitenfaktor multipliziert die Summe passend macht.
    Wird nur angewendet, wenn die Lösung eindeutig ist.
    """
    idxs = [i for i, t in enumerate(totals) if t is not None and t != 0]
    current_sum = sum(t for t in totals if t is not None)
    options: dict[int, list[float]] = {}
    for i in idxs:
        unit = parse_unit(positions[i].get("unit"))
        options[i] = unit_factors(unit)

    max_size = 2 if len(idxs) <= 40 else 1   # Paarsuche begrenzen (Laufzeit)
    for size in range(1, max_size + 1):
        solutions: list[list[tuple[int, float]]] = []
        for combo in itertools.combinations(idxs, size):
            for factors in itertools.product(*(options[i] for i in combo)):
                new_sum = current_sum + sum(totals[i] * (f - 1) for i, f in zip(combo, factors))  # type: ignore[operator]
                if any(_close(new_sum, t, extra=0.01) for t in targets):
                    solutions.append(list(zip(combo, factors)))
        if len(solutions) == 1:
            corrections = []
            for i, f in solutions[0]:
                old = totals[i]
                new = round(old * f, 2)  # type: ignore[operator]
                positions[i]["total_price"] = new
                corrections.append({
                    "position": i, "field": "total_price",
                    "from": old, "to": new, "factor": f, "method": "unit_factor",
                })
            return corrections
        if solutions:
            return None  # mehrdeutig → nicht raten
    return None
