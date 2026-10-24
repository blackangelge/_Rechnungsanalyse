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


# Mit dem Schemafeld „preiseinheit“ (Text, an der Position) braucht die Positionsregel keine Varianten: die Menge wird in die gedruckte
# Preiseinheit umgerechnet (leer = Preis je gedruckter Einheit; Verpackungseinheit = Menge ÷ Inhalt je Verpackung, ohne Inhalt 1).
_STRICT_LEFT = f"menge.@preiseinheit * einzelpreis{_AFTER}"
_STRICT_DESCRIPTION = (
    "Menge in der Preiseinheit der Rechnung (Feld „Preiseinheit“, z. B. t, kg, VPE; leer = je gedruckter Einheit) × Einzelpreis "
    "(bei „Preis je N“ geteilt durch N, abzüglich Nachlass) muss dem Gesamtpreis der Position entsprechen."
)


def has_price_unit_field(version) -> bool:
    ent = next((e for e in version.entities if e.name == "position"), None)
    return bool(ent and any(f.name == "preiseinheit" and f.data_type == "text" for f in ent.fields))


def default_rules(version) -> list[dict]:
    """Standardregeln passend zum Schema: mit Feld „preiseinheit“ die strenge Positionsregel, sonst die Fassung mit Varianten."""
    rules = [dict(r) for r in DEFAULT_RULES]
    if version is not None and has_price_unit_field(version):
        rules[0].update(left=_STRICT_LEFT, alternatives=[], description=_STRICT_DESCRIPTION)
    return rules


def seed_default_rules(db: Session) -> int:
    """
    Legt die Standardregeln an, wenn es noch keine Regel gibt; sonst werden unveränderte Standardregeln auf die zum Schema
    passende Fassung gebracht (idempotent, wird beim Start und nach Schema-Änderungen aufgerufen). Rückgabe: Anzahl angelegt/geändert.
    """
    version = sd.get_active(db)
    if db.query(ValidationRule).first() is not None:
        return _upgrade_position_rule(db, version)
    if version is None:
        return 0
    n = 0
    for i, r in enumerate(default_rules(version)):
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


def _upgrade_position_rule(db: Session, version) -> int:
    """
    Die Standardregel „Positionspreis“ in einer ihrer bekannten Fassungen (v1 nur Menge × Preis → Varianten → streng mit Preiseinheit)
    wird auf die zum Schema passende Fassung gebracht — nur solange sie unverändert ist; Änderungen des Nutzers bleiben unberührt.
    """
    target = default_rules(version)[0]
    rule = db.query(ValidationRule).filter(ValidationRule.name == target["name"]).first()
    if rule is None or rule.left_expr not in (_OLD_POSITION_LEFT, _STRICT_LEFT):
        return 0
    alts = list(rule.alt_left_exprs) if rule.alt_left_exprs is not None else None
    known = (alts is None and rule.left_expr == _OLD_POSITION_LEFT) \
        or (alts == _POSITION_VARIANTS and rule.left_expr == _POSITION_LEFT) \
        or (alts == [] and rule.left_expr == _STRICT_LEFT)
    if not known or (rule.left_expr == target["left"] and (alts or []) == target["alternatives"]):
        return 0
    rule.left_expr, rule.alt_left_exprs, rule.description = target["left"], target["alternatives"], target["description"]
    db.flush()
    return 1


def sync_default_rules(db: Session) -> int:
    """Nur angleichen (nichts neu anlegen): für Schema-Änderungen, bei denen der Nutzer Regeln bewusst gelöscht haben kann."""
    return _upgrade_position_rule(db, sd.get_active(db))
