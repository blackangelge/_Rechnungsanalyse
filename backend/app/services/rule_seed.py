"""Vorschläge für Prüfregeln auf Basis des Ausgangsschemas (Positionspreis, Summen, Steuer, Brutto)."""

from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.models.validation_rule import ValidationRule
from app.services import rule_engine
from app.services.rule_engine import RuleSpec

DEFAULT_RULES = [
    dict(name="Positionspreis = Menge × Einzelpreis", entity="position",
         description="Menge × Einzelpreis (bei „Preis je N Einheiten“ geteilt durch N, abzüglich Nachlass) muss dem Gesamtpreis der Position entsprechen.",
         left="menge * einzelpreis / COALESCE(preis_je_menge; 1) * (1 - COALESCE(preisnachlass_prozent; 0) / 100) - COALESCE(preisnachlass_betrag; 0)",
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
        return 0
    version = sd.get_active(db)
    if version is None:
        return 0
    n = 0
    for i, r in enumerate(DEFAULT_RULES):
        spec = RuleSpec(None, r["name"], r["entity"], r["left"], r["right"], "=", r["tolerance_abs"], 0.0, r["severity"])
        if rule_engine.check_rule_spec(spec, version):
            continue
        db.add(ValidationRule(
            name=r["name"], description=r["description"], entity_name=r["entity"], left_expr=r["left"], right_expr=r["right"],
            tolerance_abs=r["tolerance_abs"], severity=r["severity"], position=i,
        ))
        n += 1
    return n
