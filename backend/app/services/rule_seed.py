"""Vorschläge für Prüfregeln auf Basis des Ausgangsschemas (Positionspreis, Summen, Steuer, Brutto)."""

from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.models.validation_rule import ValidationRule
from app.services import rule_engine
from app.services.rule_engine import RuleSpec

# Nachlass auf die Position und Preisbasis („Preis je N Einheiten“) gelten für jede Variante
_AFTER = " / COALESCE(preis_je_menge; 1) * (1 - COALESCE(preisnachlass_prozent; 0) / 100) - COALESCE(preisnachlass_betrag; 0)"


def _variant(qty: str) -> str:
    return f"{qty} * einzelpreis{_AFTER}"


# Der Einzelpreis kann sich auf verschiedene Einheiten beziehen — die Regel gilt als erfüllt, sobald EINE Variante passt.
# Ohne Verpackungsinhalt zählt 1 (COALESCE).
_POSITION_LEFT = _variant("menge")
_POSITION_VARIANTS = [
    _variant("menge.basis"),                              # Preis je Basiseinheit der Gruppe (Tonne, Liter, Meter, m², Stück)
    _variant("menge.kg"),                                 # Preis je kg bei Menge in t/g …
    _variant("menge / COALESCE(verpackungsinhalt; 1)"),   # Preis je Verpackung (VPE): Menge ÷ Inhalt je Verpackung
]
_OLD_POSITION_LEFT = ("menge * einzelpreis / COALESCE(preis_je_menge; 1) * (1 - COALESCE(preisnachlass_prozent; 0) / 100)"
                      " - COALESCE(preisnachlass_betrag; 0)")

DEFAULT_RULES = [
    dict(name="Positionspreis = Menge × Einzelpreis", entity="position",
         description="Menge × Einzelpreis (je nach Rechnung: je Einheit wie gedruckt, je Tonne/Basiseinheit, je kg oder je Verpackung; "
                     "bei „Preis je N“ geteilt durch N, abzüglich Nachlass) muss dem Gesamtpreis der Position entsprechen. "
                     "Ohne Verpackungsinhalt zählt 1.",
         left=_POSITION_LEFT, alternatives=_POSITION_VARIANTS,
         right="gesamtpreis", tolerance_abs=0.02, severity="warn"),
    dict(name="Summe der Positionen = Netto", entity="beleg",
         description="Die Gesamtpreise aller Positionen ergeben den Nettobetrag der Rechnung.",
         left="SUM(position.gesamtpreis)", right="gesamtbetrag_netto", tolerance_abs=0.05, severity="error"),
    dict(name="Netto + Steuer = Brutto", entity="beleg",
         description="Nettobetrag plus Steuerbetrag ergibt den Gesamtbetrag.",
         left="gesamtbetrag_netto + steuerbetrag", right="gesamtbetrag_brutto", tolerance_abs=0.02, severity="error"),
    dict(name="Steuer aus Positionen = Steuerbetrag", entity="beleg",
         description="Positionspreis × Steuersatz, über alle Positionen summiert, entspricht dem ausgewiesenen Steuerbetrag.",
         left="SUM(position.gesamtpreis * position.steuersatz / 100)", right="steuerbetrag", tolerance_abs=0.1, severity="warn"),
]


def seed_default_rules(db: Session) -> int:
    """Legt die Vorschläge an, wenn es noch keine Regel gibt und sie zum aktiven Schema passen. Rückgabe: Anzahl."""
    if db.query(ValidationRule).first() is not None:
        return _upgrade_old_position_rule(db)
    version = sd.get_active(db)
    if version is None:
        return 0
    n = 0
    for i, r in enumerate(DEFAULT_RULES):
        spec = RuleSpec(None, r["name"], r["entity"], r["left"], r["right"], "=", r["tolerance_abs"], 0.0, r["severity"],
                        r.get("alternatives", []))
        if rule_engine.check_rule_spec(spec, version):
            continue
        db.add(ValidationRule(
            name=r["name"], description=r["description"], entity_name=r["entity"], left_expr=r["left"], right_expr=r["right"],
            alt_left_exprs=r.get("alternatives") or None, tolerance_abs=r["tolerance_abs"], severity=r["severity"], position=i,
        ))
        n += 1
    return n


def _upgrade_old_position_rule(db: Session) -> int:
    """Die erste Fassung der Standardregel (nur „Menge × Einzelpreis“) bekommt die Varianten — nur wenn sie unverändert ist."""
    spec = DEFAULT_RULES[0]
    rule = (db.query(ValidationRule)
            .filter(ValidationRule.name == spec["name"], ValidationRule.left_expr == _OLD_POSITION_LEFT)
            .filter(ValidationRule.alt_left_exprs.is_(None)).first())
    if rule is None:
        return 0
    rule.left_expr, rule.alt_left_exprs, rule.description = spec["left"], spec["alternatives"], spec["description"]
    db.flush()
    return 1
