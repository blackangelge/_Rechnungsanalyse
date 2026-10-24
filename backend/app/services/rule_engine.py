"""
Prüfregeln für extrahierte Datensätze (schemagesteuert, ohne KI).

Eine Regel vergleicht zwei Rechenausdrücke (links/rechts) im Kontext einer Entität, z. B. je Position
  `menge * einzelpreis / COALESCE(preis_je_menge, 1)`  =  `gesamtpreis`
oder je Beleg
  `SUM(position.gesamtpreis)`  =  `gesamtbetrag_netto`.

Ausdrücke werden mit einem eigenen kleinen Parser ausgewertet (kein eval): Zahlen, + − × ÷, Klammern, Feldnamen und Funktionen.

  feld                     Feld des Datensatzes (Mengenfelder: Wert wie gedruckt)
  menge.t / menge.kg       Mengenfeld automatisch in die Einheit umgerechnet (über die Einheiten-Gruppen); `menge.basis` = Basiseinheit der Gruppe
  eltern.feld              Feld des übergeordneten Datensatzes (z. B. Beleg-Kopf aus einer Position)
  SUM(position.feld * …)   Summe über alle Unter-Datensätze der Entität „position“ (Feldnamen mit Entitätsnamen davor)
  SUMIF(wert; bedingung; vergleich)
                           Summe von `wert` über die Unter-Datensätze, bei denen `bedingung` den Wert `vergleich` hat
                           (z. B. je Steuersatz: SUMIF(position.gesamtpreis; position.steuersatz; steuersatz))
  COUNT(position)          Anzahl der Unter-Datensätze
  ROUND(x; n), ABS(x), MIN(a; b …), MAX(a; b …), COALESCE(x; ersatz)   (deutsche Namen: SUMME, SUMMEWENN, ANZAHL, RUNDEN, MIN, MAX, WENNLEER)

Fehlt ein Wert, wird die Regel **nicht** geraten, sondern als „nicht prüfbar“ gemeldet (mit dem Namen des fehlenden Feldes).
Alles Zahlenmäßige läuft in `Decimal`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, DivisionByZero
from typing import Any

from sqlalchemy.orm import Session

from app.models.schema_def import ExtractedRecord, SchemaEntity, SchemaVersion

COMPARATORS = ("=", "<=", ">=")
SEVERITIES = ("warn", "error")
NUMERIC_TYPES = ("integer", "decimal", "quantity")
PARENT_NAMES = ("eltern", "parent")
FUNCTION_ALIASES = {
    "SUM": "SUM", "SUMME": "SUM", "SUMIF": "SUMIF", "SUMMEWENN": "SUMIF", "COUNT": "COUNT", "ANZAHL": "COUNT",
    "ROUND": "ROUND", "RUNDEN": "ROUND", "ABS": "ABS", "MIN": "MIN", "MAX": "MAX", "COALESCE": "COALESCE", "WENNLEER": "COALESCE",
}
FUNCTION_HELP = [
    {"name": "SUM", "example": "SUM(position.gesamtpreis)", "text": "Summe über alle Positionen"},
    {"name": "SUMIF", "example": "SUMIF(position.gesamtpreis; position.steuersatz; steuersatz)", "text": "Summe der Positionen, bei denen die Bedingung stimmt"},
    {"name": "COUNT", "example": "COUNT(position)", "text": "Anzahl der Positionen"},
    {"name": "ROUND", "example": "ROUND(menge * einzelpreis; 2)", "text": "Runden auf n Nachkommastellen"},
    {"name": "ABS", "example": "ABS(gesamtpreis)", "text": "Betrag (ohne Vorzeichen)"},
    {"name": "MIN", "example": "MIN(a; b)", "text": "Kleinerer Wert"},
    {"name": "MAX", "example": "MAX(a; b)", "text": "Größerer Wert"},
    {"name": "COALESCE", "example": "COALESCE(preis_je_menge; 1)", "text": "Wert, ersatzweise ein Standardwert, wenn das Feld leer ist"},
]


class RuleError(ValueError):
    """Ausdruck/Regel ist ungültig (Syntax, unbekanntes Feld …)."""


class MissingValue(Exception):
    """Ein Wert fehlt → Regel nicht prüfbar."""

    def __init__(self, names: list[str]):
        super().__init__(", ".join(names))
        self.names = names


class EvalError(Exception):
    """Rechenfehler (z. B. Division durch 0) → Regel nicht prüfbar."""


# ─── Parser ───────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<id>[A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß]*(?:\.[A-Za-zÄÖÜäöüß_][\wÄÖÜäöüß]*)*)|(?P<op>[-+*/(),;×÷]))")


def _tokenize(text: str) -> list[tuple[str, str]]:
    if re.search(r"\d,\d", text):
        raise RuleError("Dezimalzahlen bitte mit Punkt schreiben (0.5 statt 0,5); Argumente mit Semikolon trennen.")
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise RuleError(f"Unerwartetes Zeichen bei Position {pos + 1}: „{text[pos:pos + 10].strip()}“")
        kind = "num" if m.group("num") else "id" if m.group("id") else "op"
        value = m.group(kind)
        tokens.append((kind, {"×": "*", "÷": "/", ";": ","}.get(value, value)))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.tokens = _tokenize(text)
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise RuleError("Ausdruck endet unerwartet")
        self.i += 1
        return tok

    def expect(self, op: str) -> None:
        tok = self.take()
        if tok != ("op", op):
            raise RuleError(f"„{op}“ erwartet, gefunden „{tok[1]}“")

    def parse(self):
        if not self.tokens:
            raise RuleError("Ausdruck ist leer")
        node = self.expr()
        if self.peek() is not None:
            raise RuleError(f"Unerwartet: „{self.peek()[1]}“")
        return node

    def expr(self):
        node = self.term()
        while (t := self.peek()) and t[0] == "op" and t[1] in "+-":
            self.i += 1
            node = ("bin", t[1], node, self.term())
        return node

    def term(self):
        node = self.factor()
        while (t := self.peek()) and t[0] == "op" and t[1] in "*/":
            self.i += 1
            node = ("bin", t[1], node, self.factor())
        return node

    def factor(self):
        kind, value = self.take()
        if kind == "num":
            return ("num", Decimal(value))
        if kind == "op" and value == "-":
            return ("neg", self.factor())
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect(")")
            return node
        if kind == "id":
            nxt = self.peek()
            if nxt == ("op", "("):
                func = FUNCTION_ALIASES.get(value.upper())
                if func is None:
                    raise RuleError(f"Unbekannte Funktion „{value}“")
                self.i += 1
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.arg(func, 0))
                    while self.peek() == ("op", ","):
                        self.i += 1
                        args.append(self.arg(func, len(args)))
                self.expect(")")
                return ("call", func, args)
            return ("ref", value)
        raise RuleError(f"Unerwartet: „{value}“")

    def arg(self, func: str, index: int):
        # COUNT(position) bekommt einen reinen Entitätsnamen
        if func == "COUNT":
            kind, value = self.take()
            if kind != "id" or "." in value:
                raise RuleError("COUNT erwartet einen Entitätsnamen, z. B. COUNT(position)")
            return ("entity", value)
        return self.expr()


def parse(text: str):
    return _Parser(text or "").parse()


def walk(node):
    yield node
    if node[0] == "bin":
        yield from walk(node[2])
        yield from walk(node[3])
    elif node[0] == "neg":
        yield from walk(node[1])
    elif node[0] == "call":
        for a in node[2]:
            yield from walk(a)


def references(text: str) -> list[str]:
    """Alle Feld-/Entitätsbezüge eines Ausdrucks (für Prüfung beim Speichern)."""
    return [n[1] for n in walk(parse(text)) if n[0] == "ref"]


# ─── Baum der Datensätze ──────────────────────────────────────────────────────

@dataclass
class Node:
    record_id: int
    entity: str
    data: dict
    position_index: int = 0
    parent: "Node | None" = None
    children: dict[str, list["Node"]] = field(default_factory=dict)


def load_tree(db: Session, document_id: int) -> tuple[Node | None, dict[int, int]]:
    """Baut den Datensatz-Baum eines Belegs. Rückgabe: (Wurzel, Schema-Versions-ID je Datensatz-ID)."""
    rows = (
        db.query(ExtractedRecord, SchemaEntity.name)
        .join(SchemaEntity, SchemaEntity.id == ExtractedRecord.entity_id)
        .filter(ExtractedRecord.document_id == document_id)
        .order_by(ExtractedRecord.position_index, ExtractedRecord.id)
        .all()
    )
    nodes = {r.id: Node(r.id, name, r.data or {}, r.position_index) for r, name in rows}
    root: Node | None = None
    for r, _ in rows:
        n = nodes[r.id]
        parent = nodes.get(r.parent_record_id) if r.parent_record_id else None
        if parent is None:
            root = root or n
        else:
            n.parent = parent
            parent.children.setdefault(n.entity, []).append(n)
    return root, {r.id: r.schema_version_id for r, _ in rows}


# ─── Auswertung ───────────────────────────────────────────────────────────────

def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


UnitTable = dict[int, dict[str, Decimal]]     # Gruppen-ID → {Name/Symbol/Alias (klein): Faktor je 1 Basiseinheit}


def load_unit_table(db: Session) -> UnitTable:
    from app.models.units import Unit
    table: UnitTable = {}
    for u in db.query(Unit).all():
        keys = {u.name, u.symbol, *[a.alias for a in u.aliases]}
        grp = table.setdefault(u.group_id, {})
        for k in keys:
            if k:
                grp[k.strip().lower()] = Decimal(u.factor)
    return table


def known_unit_keys(table: UnitTable) -> set[str]:
    return {k for grp in table.values() for k in grp}


def field_value(data: dict, name: str, units: UnitTable | None = None) -> Decimal | None:
    """
    Zahlenwert eines Feldes. Mengenfelder: `feld` = Wert wie gedruckt, `feld.basis` = in die Basiseinheit der Gruppe umgerechnet,
    `feld.kg` / `feld.t` / … = in diese Einheit der Gruppe umgerechnet (automatisch über die Einheiten-Gruppen).
    """
    base, _, mode = name.partition(".")
    raw = data.get(base)
    if isinstance(raw, dict) and ("original_value" in raw or "value" in raw):
        if not mode:
            return _num(raw.get("original_value"))
        value = _num(raw.get("value"))                 # normiert (Basiseinheit); leer bei unbekannter Einheit
        if mode == "basis" or value is None:
            return value
        factor = (units or {}).get(raw.get("group_id"), {}).get(mode.lower())
        return None if factor is None else value * factor
    return None if mode else _num(raw)


class Evaluator:
    def __init__(self, node: Node, units: UnitTable | None = None):
        self.node = node
        self.units = units
        self.missing: list[str] = []

    # Auflösen eines Bezugs: Feld des aktuellen Datensatzes, eltern.feld, oder entity.feld im Kind-Bereich
    def _ref(self, name: str, scope: dict[str, Node]) -> Decimal:
        head, _, rest = name.partition(".")
        if rest and head in scope:                    # Kind-Bereich (innerhalb SUM/SUMIF)
            return self._value(scope[head], rest, name)
        if rest and head.lower() in PARENT_NAMES:
            if self.node.parent is None:
                self.missing.append(name)
                raise MissingValue(self.missing)
            return self._value(self.node.parent, rest, name)
        return self._value(self.node, name, name)

    def _value(self, node: Node, field_name: str, label: str) -> Decimal:
        v = field_value(node.data, field_name, self.units)
        if v is None:
            self.missing.append(label)
            raise MissingValue(self.missing)
        return v

    def eval(self, node, scope: dict[str, Node] | None = None) -> Decimal:
        scope = scope or {}
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "neg":
            return -self.eval(node[1], scope)
        if kind == "ref":
            return self._ref(node[1], scope)
        if kind == "bin":
            a, b = self.eval(node[2], scope), self.eval(node[3], scope)
            op = node[1]
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if b == 0:
                raise EvalError("Division durch 0")
            return a / b
        if kind == "call":
            return self._call(node[1], node[2], scope)
        raise EvalError("Ungültiger Ausdruck")

    def _call(self, func: str, args: list, scope: dict[str, Node]) -> Decimal:
        if func == "COUNT":
            return Decimal(len(self.node.children.get(args[0][1], [])))
        if func == "SUM":
            if len(args) != 1:
                raise EvalError("SUM erwartet genau einen Ausdruck")
            ent = self._child_entity(args[0])
            total, seen = Decimal(0), 0
            for child in self.node.children.get(ent, []):
                try:
                    total += self.eval(args[0], {**scope, ent: child})
                    seen += 1
                except MissingValue:
                    self.missing.clear()          # Position ohne Wert: ausgelassen
            if seen == 0:
                raise MissingValue([f"{ent} (keine Werte)"])
            return total
        if func == "SUMIF":
            if len(args) != 3:
                raise EvalError("SUMIF erwartet (wert; bedingung; vergleich)")
            ent = self._child_entity(args[0], args[1])
            target = self.eval(args[2], scope)
            total, seen = Decimal(0), 0
            for child in self.node.children.get(ent, []):
                try:
                    cond = self.eval(args[1], {**scope, ent: child})
                    if abs(cond - target) <= Decimal("0.005"):
                        total += self.eval(args[0], {**scope, ent: child})
                        seen += 1
                except MissingValue:
                    self.missing.clear()
            return total
        if func == "COALESCE":
            if len(args) < 2:
                raise EvalError("COALESCE erwartet (x; ersatz)")
            for a in args[:-1]:
                mark = len(self.missing)
                try:
                    return self.eval(a, scope)
                except MissingValue:
                    del self.missing[mark:]
            return self.eval(args[-1], scope)
        vals = [self.eval(a, scope) for a in args]
        if func == "ROUND":
            if not 1 <= len(vals) <= 2:
                raise EvalError("ROUND erwartet (x; n)")
            digits = int(vals[1]) if len(vals) == 2 else 0
            return vals[0].quantize(Decimal(1).scaleb(-digits))
        if func == "ABS":
            if len(vals) != 1:
                raise EvalError("ABS erwartet einen Wert")
            return abs(vals[0])
        if func in ("MIN", "MAX"):
            if not vals:
                raise EvalError(f"{func} erwartet Werte")
            return min(vals) if func == "MIN" else max(vals)
        raise EvalError(f"Unbekannte Funktion {func}")

    def _child_entity(self, *exprs) -> str:
        ents = {n[1].partition(".")[0] for e in exprs for n in walk(e)
                if n[0] == "ref" and "." in n[1] and n[1].partition(".")[0].lower() not in PARENT_NAMES}
        if len(ents) != 1:
            raise EvalError("SUM/SUMIF braucht Felder genau einer Unter-Entität, z. B. position.gesamtpreis")
        return ents.pop()


# ─── Regeln ───────────────────────────────────────────────────────────────────

@dataclass
class RuleSpec:
    id: int | None
    name: str
    entity: str
    left: str
    right: str
    comparator: str = "="
    tolerance_abs: float = 0.0
    tolerance_pct: float = 0.0
    severity: str = "error"


def check_rule_spec(spec: RuleSpec, version: SchemaVersion | None, unit_keys: set[str] | None = None) -> list[str]:
    """Prüft die Definition (Syntax, Entität, Felder). Leere Liste = in Ordnung. Ohne Version wird nur die Syntax geprüft."""
    problems: list[str] = []
    if spec.comparator not in COMPARATORS:
        problems.append(f"Vergleich muss einer von {', '.join(COMPARATORS)} sein")
    if spec.severity not in SEVERITIES:
        problems.append(f"Schweregrad muss einer von {', '.join(SEVERITIES)} sein")
    if spec.tolerance_abs < 0 or spec.tolerance_pct < 0:
        problems.append("Toleranz darf nicht negativ sein")
    trees = {}
    for label, text in (("links", spec.left), ("rechts", spec.right)):
        try:
            trees[label] = parse(text)
        except RuleError as exc:
            problems.append(f"Ausdruck {label}: {exc}")
    if version is None or problems:
        return problems
    entities = {e.name: e for e in version.entities if not e.is_master}
    ent = entities.get(spec.entity)
    if ent is None:
        return problems + [f"Entität „{spec.entity}“ gibt es im aktiven Schema nicht"]
    children = {e.name: e for e in version.entities}
    rel_children = {
        next((x.name for x in version.entities if x.id == r.target_entity_id), None)
        for r in version.relations if r.kind == "parent_child" and r.source_entity_id == ent.id
    }
    parent_ent = next(
        (x for r in version.relations if r.kind == "parent_child" and r.target_entity_id == ent.id
         for x in version.entities if x.id == r.source_entity_id), None,
    )
    for label, tree in trees.items():
        for n in walk(tree):
            if n[0] == "entity" and n[1] not in rel_children:
                problems.append(f"Ausdruck {label}: „{n[1]}“ ist keine Unter-Entität von „{ent.name}“")
            if n[0] != "ref":
                continue
            head, _, rest = n[1].partition(".")
            if rest and head in rel_children:
                target, fname = children[head], rest
            elif rest and head.lower() in PARENT_NAMES:
                if parent_ent is None:
                    problems.append(f"Ausdruck {label}: „{ent.name}“ hat keinen übergeordneten Datensatz")
                    continue
                target, fname = parent_ent, rest
            else:
                target, fname = ent, n[1]
            base, _, mode = fname.partition(".")
            fld = next((f for f in target.fields if f.name == base), None)
            if fld is None:
                problems.append(f"Ausdruck {label}: Feld „{n[1]}“ gibt es in „{target.name}“ nicht")
            elif fld.data_type not in NUMERIC_TYPES:
                problems.append(f"Ausdruck {label}: „{n[1]}“ ist kein Zahlenfeld (Typ {fld.data_type})")
            elif mode and fld.data_type != "quantity":
                problems.append(f"Ausdruck {label}: „.{mode}“ gibt es nur bei Mengenfeldern")
            elif mode and mode != "basis" and unit_keys is not None and mode.lower() not in unit_keys:
                problems.append(f"Ausdruck {label}: Einheit „{mode}“ ist in den Einheiten-Gruppen nicht angelegt")
    return problems


def _tolerance(spec: RuleSpec, expected: Decimal) -> Decimal:
    return max(Decimal(str(spec.tolerance_abs)), abs(expected) * Decimal(str(spec.tolerance_pct)) / 100)


def _fmt(value: Decimal | None) -> float | None:
    return None if value is None else float(round(value, 6))


def evaluate_rule(spec: RuleSpec, node: Node, units: UnitTable | None = None) -> dict:
    """Wertet eine Regel an einem Datensatz aus. Ergebnis: {status: ok|failed|skipped, left, right, diff, message …}."""
    out: dict = {
        "rule_id": spec.id, "name": spec.name, "severity": spec.severity, "entity": node.entity,
        "record_id": node.record_id, "position_index": node.position_index,
        "formula": f"{spec.left} {spec.comparator} {spec.right}",
    }
    try:
        tree_l, tree_r = parse(spec.left), parse(spec.right)
        ev = Evaluator(node, units)
        left, right = ev.eval(tree_l), ev.eval(tree_r)
    except MissingValue as exc:
        names = list(dict.fromkeys(exc.names))
        return {**out, "status": "skipped", "message": "nicht prüfbar — Wert fehlt: " + ", ".join(names), "missing": names}
    except (EvalError, RuleError, DivisionByZero, InvalidOperation) as exc:
        return {**out, "status": "skipped", "message": f"nicht prüfbar — {exc}"}
    tol = _tolerance(spec, right)
    diff = left - right
    if spec.comparator == "=":
        ok = abs(diff) <= tol
    elif spec.comparator == "<=":
        ok = diff <= tol
    else:
        ok = diff >= -tol
    out.update(status="ok" if ok else "failed", left=_fmt(left), right=_fmt(right), diff=_fmt(diff), tolerance=_fmt(tol))
    if not ok:
        out["message"] = f"links {_fmt(left)} {spec.comparator} rechts {_fmt(right)} — Abweichung {_fmt(diff)} (Toleranz {_fmt(tol)})"
    return out


def _iter_nodes(node: Node):
    yield node
    for kids in node.children.values():
        for k in kids:
            yield from _iter_nodes(k)


def evaluate_tree(root: Node, specs: list[RuleSpec], units: UnitTable | None = None) -> list[dict]:
    results = []
    for n in _iter_nodes(root):
        for spec in specs:
            if spec.entity == n.entity:
                results.append(evaluate_rule(spec, n, units))
    return results


def summarize(results: list[dict]) -> dict:
    failed_err = sum(1 for r in results if r["status"] == "failed" and r["severity"] == "error")
    failed_warn = sum(1 for r in results if r["status"] == "failed" and r["severity"] != "error")
    ok = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    if failed_err:
        status = "needs_review"
    elif failed_warn:
        status = "warn"
    elif ok:
        status = "ok"
    else:
        status = "unchecked"
    return {"status": status, "ok": ok, "failed": failed_err + failed_warn, "skipped": skipped,
            "checked_at": datetime.now(timezone.utc).isoformat()}


def load_specs(db: Session, only_enabled: bool = True) -> list[RuleSpec]:
    from app.models.validation_rule import ValidationRule
    q = db.query(ValidationRule).order_by(ValidationRule.position, ValidationRule.id)
    if only_enabled:
        q = q.filter(ValidationRule.enabled.is_(True))
    return [RuleSpec(r.id, r.name, r.entity_name, r.left_expr, r.right_expr, r.comparator, r.tolerance_abs, r.tolerance_pct, r.severity)
            for r in q.all()]


def evaluate_document(db: Session, document_id: int, specs: list[RuleSpec] | None = None, store: bool = True,
                      units: UnitTable | None = None) -> dict:
    """Prüft alle Datensätze eines Belegs und legt das Ergebnis (`_checks` je Datensatz, `_validation` an der Wurzel) ab. Kein Commit."""
    specs = load_specs(db) if specs is None else specs
    root, _ = load_tree(db, document_id)
    if root is None:
        return {"summary": {"status": "unchecked", "ok": 0, "failed": 0, "skipped": 0}, "results": []}
    results = evaluate_tree(root, specs, load_unit_table(db) if units is None else units)
    summary = summarize(results)
    if store:
        by_record: dict[int, list[dict]] = {}
        for r in results:
            by_record.setdefault(r["record_id"], []).append(
                {k: v for k, v in r.items() if k not in ("entity", "record_id", "position_index")})
        for rec in db.query(ExtractedRecord).filter(ExtractedRecord.document_id == document_id).all():
            data = dict(rec.data or {})
            data.pop("_checks", None)
            data.pop("_validation", None)
            if rec.id in by_record:
                data["_checks"] = by_record[rec.id]
            if rec.id == root.record_id:
                data["_validation"] = summary
            if data != (rec.data or {}):
                rec.data = data
    return {"summary": summary, "results": results}
