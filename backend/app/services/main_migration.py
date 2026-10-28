"""
Übernahme der Daten aus dem MAIN-Projekt (Imports, Belege, Lieferanten) ohne KI.

Die MAIN-Datenbank wird nur LESEND benutzt. **Beleg- und Import-IDs bleiben unverändert** (die PDFs heißen `{id}.pdf`, Verweise und Ordner
bleiben so gültig); ist eine ID im neuen System schon belegt, wird der Beleg nicht übernommen, sondern gemeldet. Je Beleg wird die letzte KI-Antwort (`invoice_extractions.raw_response`) mit einer
**Übersetzungstabelle** in das JSON-Format des aktiven Schemas übersetzt und läuft danach durch dieselbe Pipeline wie eine echte
Analyse (Validierung, Normalisierer, Einheiten, Lieferanten-Zuordnung, Speichern) — danach rechnen die Prüfregeln.

Übersetzungstabelle (`main_migration_config.mapping`): Liste von Einträgen
  {"target": "position.einzelpreis", "sources": ["rawpos.einzelpreis", "pos.unit_price"], "transform": null}
  {"target": "position.menge", "sources": ["rawpos.menge", "pos.quantity"], "unit_sources": ["rawpos.mengeneinheit", "pos.unit"]}   (Mengenfelder)
`target` = `<Entität>.<Feld>` des aktiven Schemas; die erste nicht leere Quelle gewinnt. Quellen:
  doc.<Spalte>      documents (MAIN)            ext.<Spalte>     invoice_extractions        sup.<Spalte>   vendor/suppliers (automatisch erkannt: neu über ext.vendor_id=Name, alt über ext.supplier_id)
  raw.<Pfad>        KI-Antwort (JSON)           pos.<Spalte>     order_positions (je Position)
  rawpos.<Pfad>     Position i der KI-Antwort   const:<Text>     fester Wert
Pfade: `a.b.c`, `liste[*].feld` (alle Elemente), `liste[0].feld`. Transformationen: sum · first · join · price_unit.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings
from app.crud import schema_definition as sd
from app.models.document import Document
from app.models.import_batch import ImportBatch
from app.models.main_migration import MainMigrationConfig, MainMigrationItem
from app.models.schema_def import SchemaVersion
from app.services import field_functions, normalizers, rule_engine, schema_pipeline
from app.services.schema_models import build_extraction_model, root_entity
from app.services.units import UnitResolver

logger = logging.getLogger(__name__)

TRANSFORMS = ("sum", "first", "join", "price_unit")
NAMESPACES = ("doc", "ext", "sup", "raw", "pos", "rawpos")
BLOCK = 200


class MigrationError(ValueError):
    """Verbindung/Konfiguration/Übersetzungstabelle unbrauchbar."""


# ─── Konfiguration ───────────────────────────────────────────────────────────

def get_config(db: Session) -> MainMigrationConfig:
    cfg = db.get(MainMigrationConfig, 1)
    if cfg is None:
        cfg = MainMigrationConfig(id=1)
        db.add(cfg)
        db.flush()
    return cfg


def build_url(host: str, port: int | str | None, database: str, user: str, password: str | None) -> str:
    """PostgreSQL-URL aus einzelnen Angaben (Sonderzeichen im Passwort werden korrekt maskiert)."""
    from sqlalchemy.engine import URL
    host, database, user = (host or "").strip(), (database or "").strip(), (user or "").strip()
    if not host or not database or not user:
        raise MigrationError("Host/IP, Datenbankname und Benutzer müssen angegeben werden.")
    if "://" in host or "/" in host or " " in host or ":" in host:
        raise MigrationError("Im Feld „Host“ nur die IP-Adresse oder den Rechnernamen eintragen (ohne postgresql:// und ohne Port).")
    try:
        port_n = int(port) if str(port or "").strip() else 5432
    except ValueError as exc:
        raise MigrationError("Der Port muss eine Zahl sein (üblich: 5432).") from exc
    if not 1 <= port_n <= 65535:
        raise MigrationError("Der Port muss zwischen 1 und 65535 liegen.")
    return URL.create("postgresql", username=user, password=password or None, host=host, port=port_n, database=database).render_as_string(hide_password=False)


def parse_url(url: str | None) -> dict:
    """Bestandteile der gespeicherten URL für die Oberfläche (ohne Passwort)."""
    if not url:
        return {"host": "", "port": 5432, "database": "", "user": "", "has_password": False}
    from sqlalchemy.engine import make_url
    try:
        u = make_url(url)
    except Exception:
        return {"host": "", "port": 5432, "database": "", "user": "", "has_password": False}
    return {"host": u.host or "", "port": u.port or 5432, "database": u.database or "", "user": u.username or "", "has_password": bool(u.password)}


def explain_connection_error(exc: Exception) -> str:
    """Technische Datenbankfehler in verständliche Hinweise übersetzen (Original bleibt angehängt)."""
    raw = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0][:300] if str(exc).strip() else exc.__class__.__name__
    low = raw.lower()
    if "password authentication failed" in low or "authentication failed" in low:
        hint = "Benutzer oder Passwort stimmen nicht."
    elif "does not exist" in low and "database" in low:
        hint = "Die Datenbank mit diesem Namen gibt es dort nicht (Name prüfen, z. B. rechnungsanalyse)."
    elif "role" in low and "does not exist" in low:
        hint = "Der Benutzer existiert in der MAIN-Datenbank nicht."
    elif "could not translate host name" in low or "name or service not known" in low or "nodename nor servname" in low:
        hint = "Der Rechnername ist nicht auflösbar — die IP-Adresse des NAS verwenden."
    elif "timeout" in low or "timed out" in low:
        hint = "Keine Antwort — IP-Adresse/Port prüfen und ob eine Firewall den Zugriff blockiert."
    elif "connection refused" in low or "could not connect" in low or "no route to host" in low:
        hint = ("Auf diesem Host/Port nimmt niemand Verbindungen an. Die Datenbank von MAIN ist normalerweise nur intern erreichbar: "
                "In der MAIN-Compose-Datei beim Dienst „db“ einen Port nach außen freigeben (z. B. ports: \"5433:5432\"), das MAIN-Projekt neu starten "
                "und hier die NAS-IP mit Port 5433 eintragen.")
    elif "ssl" in low:
        hint = "SSL-Problem bei der Verbindung."
    else:
        hint = "Verbindung nicht möglich."
    return f"{hint} (Technisch: {raw})"


def open_source(url: str) -> Engine:
    """Verbindung zur MAIN-Datenbank — PostgreSQL wird auf „nur lesen“ gestellt."""
    if not url or not url.strip():
        raise MigrationError("Keine Verbindung zur MAIN-Datenbank eingetragen.")
    url = url.strip()
    if "://" not in url:
        raise MigrationError("Das ist keine Datenbank-URL. Bitte Host/IP, Port, Datenbank, Benutzer und Passwort in den Feldern eintragen.")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs["connect_args"] = {"options": "-c default_transaction_read_only=on", "connect_timeout": 8}
    try:
        return create_engine(url, **kwargs)
    except Exception as exc:                                      # ungültige URL / fehlender Treiber
        raise MigrationError(f"Die Verbindungsangaben sind ungültig: {str(exc)[:200]}") from exc


def check_connection(engine: Engine) -> None:
    """Probeverbindung mit verständlicher Fehlermeldung."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise MigrationError(explain_connection_error(exc)) from exc


# ─── Quellpfade ──────────────────────────────────────────────────────────────

def _plain(value: Any) -> Any:
    """Datenbankwerte → JSON-taugliche Werte. Zahlen bleiben Zahlen (kein Text: „1.234“ würde sonst als Tausender gelesen)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, tuple)) and not v)


_SEG = re.compile(r"^([^\[\]]+)((?:\[(?:\*|\d+)\])*)$")


def walk_path(obj: Any, path: str) -> Any:
    """`a.b[*].c` → Wert bzw. Liste aller Werte; fehlt etwas → None."""
    current = [obj]
    wildcard = False
    for seg in path.split("."):
        m = _SEG.match(seg)
        if not m:
            return None
        key, idx = m.group(1), re.findall(r"\[(\*|\d+)\]", m.group(2))
        nxt = []
        for node in current:
            if isinstance(node, dict) and key in node:
                nxt.append(node[key])
        current = nxt
        for i in idx:
            expanded = []
            for node in current:
                if not isinstance(node, list):
                    continue
                if i == "*":
                    wildcard = True
                    expanded.extend(node)
                elif int(i) < len(node):
                    expanded.append(node[int(i)])
            current = expanded
        if not current:
            return None
    if wildcard:
        return current
    return current[0] if current else None


@dataclass
class SourceCtx:
    doc: dict
    ext: dict
    sup: dict
    raw: dict
    pos: dict | None = None
    rawpos: dict | None = None

    def get(self, source: str) -> Any:
        if source.startswith("const:"):
            return source[len("const:"):]
        ns, _, path = source.partition(".")
        if ns not in NAMESPACES or not path:
            return None
        data = getattr(self, ns)
        if data is None:
            return None
        return walk_path(data, path)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        d = normalizers.parse_german_number(v)
    except Exception:
        return None
    return None if d is None else float(d)


_PRICE_UNIT_PREFIX = re.compile(r"^(?:\s*(?:je|pro|per|/|€|eur|euro)\b\s*|\s*[/€]\s*)+", re.IGNORECASE)


def price_unit(value: Any) -> str | None:
    """„je t“ / „€/t“ / „pro Stück“ → „t“ / „Stück“. Beginnt der Rest mit einer Zahl („je 100 Stück“), ist es eine Preisbasis, keine Einheit → None."""
    if not isinstance(value, str):
        return None
    rest = _PRICE_UNIT_PREFIX.sub("", value.strip()).strip(" ./")
    if not rest or re.match(r"^\d", rest):
        return None
    return rest


def apply_transform(value: Any, transform: str | None) -> Any:
    if not transform:
        return value
    items = value if isinstance(value, list) else [value]
    if transform == "sum":
        nums = [n for n in (_num(x) for x in items) if n is not None]
        return round(sum(nums), 6) if nums else None
    if transform == "first":
        return next((x for x in items if not _empty(x)), None)
    if transform == "join":
        parts = [str(x).strip() for x in items if not _empty(x)]
        return ", ".join(parts) if parts else None
    if transform == "price_unit":
        return price_unit(items[0] if items else None)
    raise MigrationError(f"Unbekannte Transformation: {transform}")


def pick(ctx: SourceCtx, sources: list[str], transform: str | None) -> Any:
    """Erste nicht leere Quelle (nach der Transformation)."""
    for s in sources or []:
        v = apply_transform(ctx.get(s), transform)
        if not _empty(v):
            return v
    return None


# ─── Übersetzung ─────────────────────────────────────────────────────────────

def _entries_for(mapping: list[dict], entity: str) -> list[dict]:
    return [e for e in mapping if e.get("target", "").partition(".")[0] == entity]


def _entity_dict(entity, entries: list[dict], ctx: SourceCtx) -> dict:
    out: dict = {}
    qty = {f.name for f in entity.fields if f.data_type == "quantity"}
    for e in entries:
        fname = e["target"].partition(".")[2]
        value = pick(ctx, e.get("sources", []), e.get("transform"))
        if fname in qty:
            unit = pick(ctx, e.get("unit_sources", []), None)
            out[fname] = None if (value is None and unit is None) else {"value": value, "unit": unit}
        else:
            out[fname] = value
    return out


def build_parsed(version: SchemaVersion, mapping: list[dict], doc: dict, ext: dict, sup: dict, raw: dict,
                 positions: list[dict]) -> dict:
    """MAIN-Beleg → Dict in der Form der Schema-Antwort (Wurzelfelder + je Beziehung ein Objekt bzw. eine Liste)."""
    by_id = {e.id: e for e in version.entities}
    root = root_entity(version)
    base = SourceCtx(doc, ext, sup, raw)
    parsed = _entity_dict(root, _entries_for(mapping, root.name), base)
    raw_positions = raw.get("positionen") if isinstance(raw.get("positionen"), list) else []
    for rel in version.relations:
        if rel.source_entity_id != root.id:
            continue
        target = by_id[rel.target_entity_id]
        entries = _entries_for(mapping, target.name)
        if rel.kind == "parent_child" and target.name in _CHILD_LISTS:
            items: list = []
            for path in _CHILD_LISTS[target.name]:
                found = walk_path(raw, path.partition(".")[2])
                if isinstance(found, list) and found:
                    items = found
                    break
            parsed[rel.name] = [_entity_dict(target, entries, SourceCtx(doc, ext, sup, raw, None, it)) for it in items if isinstance(it, dict)]
        elif rel.kind == "parent_child":
            rows = []
            for i in range(max(len(raw_positions), len(positions))):
                db_row = next((p for p in positions if p.get("position_index") == i), positions[i] if i < len(positions) else None)
                raw_row = raw_positions[i] if i < len(raw_positions) and isinstance(raw_positions[i], dict) else None
                rows.append(_entity_dict(target, entries, SourceCtx(doc, ext, sup, raw, db_row, raw_row)))
            parsed[rel.name] = rows
        elif target.is_master:
            parsed[rel.name] = _entity_dict(target, entries, base)
    return parsed


# ─── Standard-Übersetzung ────────────────────────────────────────────────────

_NEW = "raw.zahlungsinformationen"
_DEFAULT_BELEG = {
    "rechnungsnummer": (["raw.rechnungsdaten.rechnungsnummer", "raw.rechnungsnummer", "ext.invoice_number"], None),
    "rechnungsdatum": (["raw.rechnungsdaten.rechnungsdatum", "raw.rechnungsdatum", "ext.invoice_date"], None),
    "faelligkeit": (["raw.rechnungsdaten.faelligkeit", "raw.faelligkeit", "ext.due_date"], None),
    "kundennummer": (["raw.rechnungsdaten.kundennummer", "raw.kundennummer", "ext.customer_number"], None),
    "bestellnummer": (["raw.rechnungsdaten.bestellnummer", "raw.bestellnummer"], None),
    "empfaenger_name": (["raw.rechnungsempfaenger.name", "raw.empfaenger.name"], None),
    "empfaenger_ust_id_nr": (["raw.rechnungsempfaenger.ust_id_nr", "raw.empfaenger.ust_id_nr"], None),
    "gesamtbetrag_netto": ([f"{_NEW}.gesamtbetrag_netto", "raw.gesamtbetrag_netto", "ext.total_amount_netto"], None),
    "steuerbetrag": ([f"{_NEW}.umsatzsteuer_zusammenfassung[*].steuerbetrag", f"{_NEW}.steuerbetrag", "raw.steuerbetrag", "ext.total_tax_value"], "sum"),
    "gesamtbetrag_brutto": ([f"{_NEW}.gesamtbetrag_brutto", "raw.gesamtbetrag_brutto", "ext.total_amount_brutto", "ext.total_amount"], None),
    "rabatt_betrag": (["ext.discount_amount", f"{_NEW}.rabatt_betrag"], None),
    "skonto_prozent": ([f"{_NEW}.skonto.prozent"], None),
    "skonto_betrag": ([f"{_NEW}.skonto.betrag", "ext.cash_discount_amount"], None),
    "skonto_frist_tage": ([f"{_NEW}.skonto.frist_tage"], None),
    "zahlungsbedingungen": ([f"{_NEW}.zahlungsbedingungen", "ext.payment_terms"], None),
    "waehrung": ([f"{_NEW}.waehrung", "raw.waehrung"], None),
}
_DEFAULT_LIEFERANT = {
    "name": (["raw.lieferant.name", "ext.vendor_id", "ext.supplier_name", "sup.name"], None),
    "strasse": (["raw.lieferant.anschrift.strasse", "sup.street"], None),
    "plz": (["raw.lieferant.anschrift.plz", "sup.postal_code", "sup.zip_code"], None),
    "ort": (["raw.lieferant.anschrift.ort", "sup.city"], None),
    "land": (["raw.lieferant.anschrift.land", "sup.country"], None),
    "hrb_nummer": (["raw.lieferant.hrb_nummer", "ext.hrb_number", "sup.hrb_number"], None),
    "steuernummer": (["raw.lieferant.steuernummer", "ext.tax_number", "sup.tax_number"], None),
    "ust_id_nr": (["raw.lieferant.ust_id_nr", "ext.vat_id", "sup.vat_id"], None),
    "bank_name": (["raw.lieferant.bankverbindung.bank_name", "ext.bank_name", "sup.bank_name"], None),
    "iban": (["raw.lieferant.bankverbindung.iban", "ext.iban", "sup.iban"], None),
    "bic": (["raw.lieferant.bankverbindung.bic", "ext.bic", "sup.bic"], None),
}
_DEFAULT_POSITION = {
    "position_nr": (["rawpos.position_nr"], None),
    "artikelbezeichnung": (["rawpos.artikelbezeichnung", "pos.product_name", "pos.product_description"], None),
    "artikelnummer": (["rawpos.artikelnummer_lieferant", "pos.article_number"], None),
    "einzelpreis": (["rawpos.einzelpreis", "pos.unit_price_netto", "pos.unit_price"], None),
    "gesamtpreis": (["rawpos.gesamtpreis", "pos.total_price"], None),
    "steuersatz": (["rawpos.steuersatz"], None),
    "preisnachlass_betrag": (["rawpos.preisnachlass.betrag"], None),
    "preisnachlass_prozent": (["rawpos.preisnachlass.prozent"], None),
    "preisnachlass_bezeichnung": (["rawpos.preisnachlass.bezeichnung"], None),
    "waehrung": (["rawpos.waehrung", f"{_NEW}.waehrung"], None),
    "preis_je_menge": (["rawpos.preis_je_menge"], None),
    "preiseinheit": (["rawpos.preiseinheit"], "price_unit"),
    "mengeneinheit_typ": (["rawpos.mengeneinheit_typ"], None),
}
_DEFAULT_QUANTITY = {
    "menge": (["rawpos.menge", "pos.quantity"], ["rawpos.mengeneinheit", "pos.unit"]),
    "verpackungsinhalt": (["rawpos.verpackungsinhalt"], ["rawpos.verpackungsinhalt_einheit"]),
}
_DEFAULT_UMSATZSTEUER = {
    "steuersatz": (["rawpos.steuersatz"], None),
    "nettobetrag": (["rawpos.nettobetrag"], None),
    "steuerbetrag": (["rawpos.steuerbetrag"], None),
}
_DEFAULTS_BY_ENTITY = {"beleg": _DEFAULT_BELEG, "lieferant": _DEFAULT_LIEFERANT, "position": _DEFAULT_POSITION, "umsatzsteuer": _DEFAULT_UMSATZSTEUER}
# Listen in der KI-Antwort, aus denen die Datensätze weiterer Unter-Entitäten entstehen (Felder über „rawpos.“ = Eintrag der Liste)
_CHILD_LISTS = {"umsatzsteuer": [f"{_NEW}.umsatzsteuer_zusammenfassung", "raw.umsatzsteuer_zusammenfassung"]}


def default_mapping(version: SchemaVersion) -> list[dict]:
    """Vorschlag für die Übersetzungstabelle zum aktiven Schema (nur Felder, die es dort gibt; Entitäten mit den Namen beleg/position/lieferant)."""
    out: list[dict] = []
    for ent in version.entities:
        table = _DEFAULTS_BY_ENTITY.get(ent.name)
        if table is None:
            continue
        names = {f.name for f in ent.fields if f.source == "llm" and not f.is_locked}
        for fname, (sources, transform) in table.items():
            if fname in names:
                out.append({"target": f"{ent.name}.{fname}", "sources": list(sources), "transform": transform})
        if ent.name == "position":
            for fname, (vs, us) in _DEFAULT_QUANTITY.items():
                if fname in names:
                    out.append({"target": f"position.{fname}", "sources": list(vs), "unit_sources": list(us), "transform": None})
    return out


def check_mapping(version: SchemaVersion, mapping: list[dict]) -> list[str]:
    """Meldungen zur Übersetzungstabelle (leer = in Ordnung). Unbekannte Ziele/Quellen/Transformationen werden benannt."""
    problems: list[str] = []
    fields = {(e.name, f.name): f for e in version.entities for f in e.fields}
    seen: set[str] = set()
    for i, e in enumerate(mapping or [], 1):
        target = e.get("target", "")
        ent, _, fname = target.partition(".")
        if (ent, fname) not in fields:
            problems.append(f"Zeile {i}: Ziel „{target}“ gibt es im aktiven Schema nicht")
            continue
        f = fields[(ent, fname)]
        if f.source != "llm" or f.is_locked:
            problems.append(f"Zeile {i}: Ziel „{target}“ wird vom System gesetzt")
        if target in seen:
            problems.append(f"Zeile {i}: Ziel „{target}“ ist doppelt")
        seen.add(target)
        if e.get("transform") and e["transform"] not in TRANSFORMS:
            problems.append(f"Zeile {i}: Transformation „{e['transform']}“ ist unbekannt")
        for s in list(e.get("sources") or []) + list(e.get("unit_sources") or []):
            if not s.startswith("const:") and s.partition(".")[0] not in NAMESPACES:
                problems.append(f"Zeile {i}: Quelle „{s}“ beginnt nicht mit {', '.join(NAMESPACES)} oder const:")
        if f.data_type == "quantity" and not e.get("unit_sources"):
            problems.append(f"Zeile {i}: Mengenfeld „{target}“ hat keine Einheiten-Quelle")
    required = [f"{e.name}.{f.name}" for e in version.entities for f in e.fields if f.required and f.source == "llm" and not f.is_locked]
    problems += [f"Pflichtfeld „{r}“ hat keine Zeile in der Übersetzungstabelle" for r in required if r not in seen]
    return problems


# ─── Lesen aus MAIN ──────────────────────────────────────────────────────────

def _rows(conn, sql: str, **params) -> list[dict]:
    return [{k: _plain(v) for k, v in r._mapping.items()} for r in conn.execute(text(sql), params)]


def _in_clause(prefix: str, ids: list[int]) -> tuple[str, dict]:
    keys = {f"{prefix}{i}": v for i, v in enumerate(ids)}
    return ", ".join(f":{k}" for k in keys), keys


def _col(row: dict, *names: str) -> Any:
    """Erster vorhandener Schlüssel (auch wenn sein Wert None/0 ist) — für Spalten, die je MAIN-Version anders heißen."""
    for n in names:
        if n in row:
            return row[n]
    return None


def _probe(bind) -> dict:
    """Erkennt das MAIN-Schema, das je Version abweicht.
    Neuere MAIN: Lieferantentabelle `vendor`, Verknüpfung über `invoice_extractions.vendor_id` (= Lieferantenname, Text).
    Ältere MAIN: `suppliers`, Verknüpfung über `invoice_extractions.supplier_id` (Zahl-ID).
    Löschkennzeichen am Beleg: `soft_deleted` (neu) bzw. `deleted_at` (alt)."""
    from sqlalchemy import inspect
    insp = inspect(bind)

    def cols(t: str) -> set[str]:
        try:
            return {c["name"] for c in insp.get_columns(t)}
        except Exception:
            return set()

    try:
        tables = set(insp.get_table_names())
    except Exception:
        tables = set()
    ext = cols("invoice_extractions")
    docs = cols("documents")
    sup_table = "vendor" if "vendor" in tables else ("suppliers" if "suppliers" in tables else "vendor")
    if "supplier_id" in ext:
        ext_link, sup_key = "supplier_id", "id"                      # ältere MAIN: Zahl-FK auf suppliers.id
    else:
        ext_link, sup_key = "vendor_id", "name"                      # neuere MAIN: Lieferantenname als Text → vendor.name
    deleted = "soft_deleted" if "soft_deleted" in docs else ("deleted_at" if "deleted_at" in docs else "")
    return {"sup_table": sup_table, "sup_key": sup_key, "ext_link": ext_link, "deleted": deleted}


def count_source(engine: Engine) -> dict[str, int]:
    out: dict[str, int] = {}
    probe = _probe(engine)
    with engine.connect() as conn:
        for key, table in (("batches", "import_batches"), ("documents", "documents"), ("extractions", "invoice_extractions"),
                           ("positions", "order_positions")):
            try:
                out[key] = int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
            except Exception as exc:
                raise MigrationError(f"Tabelle {table} nicht lesbar — ist das die MAIN-Datenbank? ({exc})") from exc
        try:                                                         # Lieferantentabelle optional — fehlt sie, zählt sie als 0
            out["suppliers"] = int(conn.execute(text(f"SELECT COUNT(*) FROM {probe['sup_table']}")).scalar() or 0)
        except Exception:
            out["suppliers"] = 0
    return out


def discover_paths(engine: Engine, sample: int = 300) -> dict[str, list[str]]:
    """Spalten der MAIN-Tabellen + alle Pfade, die in den KI-Antworten einer Stichprobe vorkommen (für die Auswahl in der Oberfläche)."""
    out: dict[str, list[str]] = {}
    with engine.connect() as conn:
        for ns, table in (("doc", "documents"), ("ext", "invoice_extractions"), ("sup", _probe(engine)["sup_table"]), ("pos", "order_positions")):
            rows = _rows(conn, f"SELECT * FROM {table} LIMIT 1")
            cols = list(rows[0].keys()) if rows else [c["name"] for c in _columns(engine, table)]
            out[ns] = [f"{ns}.{c}" for c in cols if c not in ("raw_response",)]
        raws = _rows(conn, "SELECT raw_response FROM invoice_extractions WHERE raw_response IS NOT NULL ORDER BY id DESC LIMIT :n", n=sample)
    top: set[str] = set()
    pos: set[str] = set()
    for r in raws:
        data = _parse_raw(r.get("raw_response"))
        if not data:
            continue
        _collect("", data, top, pos)
    out["raw"] = sorted(f"raw.{p}" for p in top)
    out["rawpos"] = sorted(f"rawpos.{p}" for p in pos)
    return out


def _columns(engine: Engine, table: str) -> list[dict]:
    from sqlalchemy import inspect
    try:
        return inspect(engine).get_columns(table)
    except Exception:
        return []


def _collect(prefix: str, node: Any, top: set[str], pos: set[str], in_pos: bool = False) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            if k == "positionen" and isinstance(v, list) and not prefix:
                for item in v:
                    _collect("", item, top, pos, True)
                continue
            if isinstance(v, dict):
                _collect(path, v, top, pos, in_pos)
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                _collect(f"{path}[*]", v[0], top, pos, in_pos)
            else:
                (pos if in_pos else top).add(path)


def _parse_raw(raw: Any) -> dict | None:
    """KI-Antwort aus MAIN → Dict. Denk-Blöcke und Code-Zäune werden toleriert; Fehlertexte („KI-Fehler: …“) ergeben None."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        data = json.loads(s)
    except ValueError:
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            return None
        try:
            data = json.loads(s[a:b + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


# ─── Lauf ────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_state: dict = {"running": False}
_cancel = threading.Event()


def get_state() -> dict:
    with _lock:
        return json.loads(json.dumps(_state, default=str))


def cancel() -> bool:
    with _lock:
        if not _state.get("running"):
            return False
    _cancel.set()
    return True


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def _bump(key: str, n: int = 1) -> None:
    with _lock:
        _state["counts"][key] = _state["counts"].get(key, 0) + n


def _err(msg: str) -> None:
    with _lock:
        if len(_state["errors"]) < 30:
            _state["errors"].append(msg)


@dataclass
class RunOptions:
    dry_run: bool = False
    limit: int | None = None                    # höchstens so viele Belege in diesem Lauf (Stichprobe)
    old_batch_ids: list[int] | None = None
    old_document_ids: list[int] | None = None
    retry_errors: bool = True                   # Belege mit Status error erneut versuchen
    include_deleted: bool = True
    suppliers: bool = True                      # MAIN-Lieferanten-Stammdaten vorab übernehmen


_STATUS_MAP = {"done": "done", "error": "error"}


def _find_pdf(root: Path, doc: dict, old_id: int, company: str | None = None, year: Any = None) -> Path | None:
    company = doc.get("company") or company or ""            # MAIN speichert Firma/Jahr am Batch, nicht am Beleg → vom Batch übernehmen
    year = doc.get("year") if doc.get("year") is not None else year
    folder = root / f"{company}_{year}"
    names = [doc.get("stored_filename"), f"{old_id}.pdf"]
    for base in (folder, root):
        for n in names:
            if n and (base / n).is_file():
                return base / n
    return None


def _load_items(db: Session, table: str, ids: list[int]) -> dict[int, MainMigrationItem]:
    if not ids:
        return {}
    return {i.old_id: i for i in db.query(MainMigrationItem).filter(MainMigrationItem.source_table == table, MainMigrationItem.old_id.in_(ids)).all()}


def _remember(db: Session, table: str, old_id: int, new_id: int | None, status: str, message: str | None,
              existing: MainMigrationItem | None) -> MainMigrationItem:
    if existing is not None:
        existing.new_id, existing.status, existing.message = new_id, status, (message or None)
        item = existing
    else:
        item = MainMigrationItem(source_table=table, old_id=old_id, new_id=new_id, status=status, message=message or None)
        db.add(item)
    db.flush()
    return item


def _ensure_batch(db: Session, brow: dict, items: dict[int, MainMigrationItem], dry: bool) -> int:
    old_id = brow["id"]
    item = items.get(old_id) or _load_items(db, "batch", [old_id]).get(old_id)
    if item is not None and item.new_id:
        return item.new_id
    company, year = brow.get("company_name") or "Unbekannt", int(brow.get("year") or 0)
    hidden = brow.get("hidden_at")
    if db.get(ImportBatch, old_id) is not None:
        raise MigrationError(f"Import-ID {old_id} ist im neuen System schon belegt — die IDs aus MAIN müssen frei sein.")
    batch = ImportBatch(
        id=old_id, import_folder_path=_col(brow, "import_folder_path", "folder_path") or "", storage_folder_path=str(Path(settings.storage_path) / f"{company}_{year}"),
        company_name=company, year=year, comment=(brow.get("comment") or None), status="done", folder_sync=False, auto_export=False,
        hidden_at=_dt(hidden), started_at=_dt(brow.get("started_at")), finished_at=_dt(brow.get("finished_at")),
    )
    db.add(batch)
    db.flush()
    created = _dt(brow.get("created_at"))
    if created:
        batch.created_at = created
    items[old_id] = _remember(db, "batch", old_id, batch.id, "imported", None, item)
    return batch.id


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# Dokumenttyp-Namen (MAIN, normalisiert: klein, ohne Sonderzeichen) → ID in diesem System
_TYPE_NAMES = {
    "unbekannt": 0, "eingangsrechnung": 1, "ausgangsrechnung": 1, "rechnung": 1,
    "gutschrift": 3, "gutschriftstorno": 3, "gutschriftkreditnote": 3, "storno": 3, "kreditnote": 3,
    "lieferschein": 4, "bestellbestatigung": 5, "auftragsbestatigung": 5, "angebot": 6, "mahnung": 7, "kontoauszug": 8,
    "kassenbon": 9, "kassenbonquittung": 9, "quittung": 9, "vertrag": 10, "lohnabrechnung": 11, "reisekostenabrechnung": 12,
    "zollpapier": 13, "versicherungspolice": 14, "sonstiges": 15, "zahlungsavis": 16, "mischbeleg": 17,
}


def _type_key(name: str) -> str:
    n = unicodedata.normalize("NFKD", (name or "").lower()).encode("ascii", "ignore").decode()
    n = re.sub(r"\(.*?\)", "", n)
    return re.sub(r"[^a-z]", "", n)


def load_type_map(engine: Engine) -> dict[int, int]:
    """
    Dokumenttyp-IDs von MAIN → IDs dieses Systems. Über die Namen aus MAIN-Tabelle `document_types` (die IDs unterscheiden sich:
    hier gibt es keine „Ausgangsrechnung“, sie zählt als Eingangsrechnung). Unbekannte Namen → Sonstiges (15). Ohne diese Tabelle
    bleiben die IDs unverändert.
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM document_types")).mappings().all()
    except Exception:
        return {}
    out: dict[int, int] = {}
    for r in rows:
        key = _type_key(r["name"])
        if key in _TYPE_NAMES:
            out[int(r["id"])] = _TYPE_NAMES[key]
        else:
            out[int(r["id"])] = next((v for k, v in _TYPE_NAMES.items() if len(k) >= 7 and key.startswith(k)), 15)
    return out


def _map_type(old: Any, type_map: dict[int, int] | None) -> int:
    n = int(old or 0)
    return n if not type_map else type_map.get(n, n if n == 0 else 15)


def migrate_one(db: Session, version: SchemaVersion, built, resolver: UnitResolver, mapping: list[dict], doc: dict, ext: dict | None,
                sup: dict | None, positions: list[dict], batch_id: int, storage_root: Path | None, dry: bool,
                type_map: dict[int, int] | None = None) -> tuple[Document, str, str | None]:
    """Legt Beleg + Datensätze an. Rückgabe: (Beleg, Status imported|no_data, Hinweis). Wirft bei harten Fehlern (Aufrufer fängt)."""
    batch = db.get(ImportBatch, batch_id)
    old_id = doc["id"]
    mstatus = _STATUS_MAP.get(doc.get("status"), "pending")
    if db.get(Document, old_id) is not None:
        raise MigrationError(f"Beleg-ID {old_id} ist im neuen System schon belegt.")
    new = Document(
        id=old_id, batch_id=batch_id, original_filename=doc.get("original_filename") or f"beleg_{old_id}.pdf",
        file_size_bytes=int(doc.get("file_size_bytes") or 0), page_count=int(doc.get("page_count") or 0),
        document_type=_map_type(_col(doc, "document_type", "document_type_id"), type_map), comment=doc.get("comment") or None, status="pending",
        raw_response=(_col(doc, "error_message", "raw_response") or None) if mstatus == "error" else None,
    )
    db.add(new)
    db.flush()
    created = _dt(doc.get("created_at"))
    if created:
        new.created_at = created
    if doc.get("deleted_at") or doc.get("soft_deleted"):
        new.soft_deleted, new.deleted_at, new.deleted_reason = True, _dt(doc.get("deleted_at")), "Aus MAIN übernommen (dort gelöscht)"
    notes: list[str] = []

    dest = Path(batch.storage_folder_path) / f"{new.id}.pdf"
    pdf = _find_pdf(storage_root, doc, old_id, batch.company_name, batch.year) if storage_root else None
    if dest.is_file():                                        # schon im Speicher dieses Systems (z. B. Ordner direkt hineinkopiert)
        new.stored_filename = dest.name
        new.file_size_bytes = dest.stat().st_size
    elif pdf is None:
        notes.append("PDF nicht gefunden")
    elif not dry:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, dest)
        new.stored_filename = dest.name
        new.file_size_bytes = dest.stat().st_size
    else:
        new.stored_filename = f"{new.id}.pdf"

    raw = _parse_raw((ext or {}).get("raw_response")) if ext else None
    if mstatus == "error":
        new.status = "error"
        return new, "imported", "; ".join(notes) or None
    if raw is None:
        new.status = "done" if mstatus == "done" else "pending"
        if mstatus == "done" and new.document_type in (0, 1, 17):
            return new, "no_data", "; ".join(notes + ["keine verwertbare KI-Antwort"])
        return new, "imported", "; ".join(notes) or None

    parsed = build_parsed(version, mapping, doc, ext or {}, sup or {}, raw, positions)
    validated = schema_pipeline.validate_output(built, parsed)
    ctx = field_functions.build_context(new)
    root = schema_pipeline.build_drafts(db, version, validated, ctx, resolver)
    schema_pipeline.save_drafts(db, new.id, version, root)
    rule_engine.evaluate_document(db, new.id)
    new.status = "done"
    new.analyzed_at = _dt((ext or {}).get("updated_at") or (ext or {}).get("created_at")) or new.created_at
    return new, "imported", "; ".join(notes) or None


def run(session_factory: Callable[[], Session], options: RunOptions) -> dict:
    """Synchroner Lauf (läuft im Hintergrund-Thread; Tests rufen ihn direkt auf). Gibt den Endstand zurück."""
    _cancel.clear()
    with _lock:
        _state.clear()
        _state.update(running=True, dry_run=options.dry_run, started_at=datetime.now(timezone.utc).isoformat(), finished_at=None,
                      total=0, processed=0, counts={}, errors=[], message="Verbindung wird aufgebaut …", cancelled=False)
    db = session_factory()
    try:
        cfg = get_config(db)
        engine = open_source(cfg.source_url or "")
        version = sd.get_active(db)
        if version is None:
            raise MigrationError("Es gibt kein aktives Schema.")
        mapping = cfg.mapping or []
        problems = check_mapping(version, mapping)
        if problems:
            raise MigrationError("Übersetzungstabelle fehlerhaft: " + "; ".join(problems[:5]))
        built = build_extraction_model(version)
        resolver = UnitResolver(db)
        type_map = load_type_map(engine)
        probe = _probe(engine)
        storage_root = Path(cfg.source_storage_path) if cfg.source_storage_path else None
        if storage_root is not None and not storage_root.is_dir():
            raise MigrationError(f"Ordner der MAIN-PDFs nicht gefunden: {storage_root}")

        where, params = ["1=1"], {}
        if options.old_batch_ids:
            clause, keys = _in_clause("b", options.old_batch_ids)
            where.append(f"batch_id IN ({clause})")
            params.update(keys)
        if options.old_document_ids:
            clause, keys = _in_clause("d", options.old_document_ids)
            where.append(f"id IN ({clause})")
            params.update(keys)
        if not options.include_deleted and probe["deleted"] == "soft_deleted":
            where.append("NOT COALESCE(soft_deleted, FALSE)")
        elif not options.include_deleted and probe["deleted"] == "deleted_at":
            where.append("deleted_at IS NULL")
        cond = " AND ".join(where)

        with engine.connect() as conn:
            total = int(conn.execute(text(f"SELECT COUNT(*) FROM documents WHERE {cond}"), params).scalar() or 0)
            if options.limit:
                total = min(total, options.limit)
            _set(total=total, message="Übernahme läuft …")

            if options.suppliers and not options.old_document_ids:
                _migrate_suppliers(conn, db, version, mapping, options, probe)

            last, done_in_run = 0, 0
            batch_cache: dict[int, dict] = {}
            while not _cancel.is_set():
                block = _rows(conn, f"SELECT * FROM documents WHERE {cond} AND id > :last ORDER BY id LIMIT :n", **params, last=last, n=BLOCK)
                if not block:
                    break
                last = block[-1]["id"]
                ids = [d["id"] for d in block]
                clause, keys = _in_clause("i", ids)
                exts = {e["document_id"]: e for e in _rows(conn, f"SELECT * FROM invoice_extractions WHERE document_id IN ({clause})", **keys)}
                poss: dict[int, list[dict]] = {}
                for p in _rows(conn, f"SELECT * FROM order_positions WHERE document_id IN ({clause}) ORDER BY position_index, id", **keys):
                    poss.setdefault(p["document_id"], []).append(p)
                link_vals = sorted({e[probe["ext_link"]] for e in exts.values() if e.get(probe["ext_link"])})
                sups: dict = {}
                if link_vals:
                    sc, sk = _in_clause("s", link_vals)
                    sups = {s[probe["sup_key"]]: s for s in _rows(conn, f"SELECT * FROM {probe['sup_table']} WHERE {probe['sup_key']} IN ({sc})", **sk)}
                need_batches = sorted({d["batch_id"] for d in block if d["batch_id"] not in batch_cache})
                if need_batches:
                    bc, bk = _in_clause("x", need_batches)
                    batch_cache.update({b["id"]: b for b in _rows(conn, f"SELECT * FROM import_batches WHERE id IN ({bc})", **bk)})
                items = _load_items(db, "document", ids)
                batch_items = _load_items(db, "batch", sorted({d["batch_id"] for d in block}))

                for d in block:
                    if _cancel.is_set() or (options.limit and done_in_run >= options.limit):
                        break
                    prev = items.get(d["id"])
                    if prev is not None and prev.status in ("imported", "no_data") or (prev is not None and prev.status == "error" and not options.retry_errors):
                        _bump("skipped_done")
                        continue
                    done_in_run += 1
                    try:
                        with db.begin_nested():
                            bid = _ensure_batch(db, batch_cache.get(d["batch_id"]) or {"id": d["batch_id"]}, batch_items, options.dry_run)
                            new, status, note = migrate_one(db, version, built, resolver, mapping, d, exts.get(d["id"]),
                                                            sups.get((exts.get(d["id"]) or {}).get(probe["ext_link"])), poss.get(d["id"], []),
                                                            bid, storage_root, options.dry_run, type_map)
                            _remember(db, "document", d["id"], new.id, status, note, prev)
                        _bump(status)
                        if note and "PDF nicht gefunden" in note:
                            _bump("pdf_missing")
                        if status == "imported":
                            rs = _rule_status(db, new.id)
                            if rs:
                                _bump(f"rules_{rs}")
                    except Exception as exc:
                        _bump("error")
                        _err(f"Beleg {d['id']}: {str(exc)[:240]}")
                        try:
                            _remember(db, "document", d["id"], None, "error", str(exc)[:500], items.get(d["id"]))
                        except Exception:
                            pass
                    _set(processed=done_in_run)
                if options.dry_run:
                    db.rollback()
                else:
                    advance_sequences(db)
                    db.commit()
                if options.limit and done_in_run >= options.limit:
                    break
        _set(message="Abgebrochen" if _cancel.is_set() else ("Trockenlauf beendet — nichts gespeichert" if options.dry_run else "Fertig"),
             cancelled=_cancel.is_set())
    except MigrationError as exc:
        db.rollback()
        _set(message=str(exc), failed=True)
    except Exception as exc:
        db.rollback()
        logger.exception("Übernahme aus MAIN abgebrochen")
        _set(message=f"Abbruch: {exc}", failed=True)
    finally:
        db.close()
        _set(running=False, finished_at=datetime.now(timezone.utc).isoformat())
    return get_state()


def advance_sequences(db: Session) -> None:
    """PostgreSQL: Zähler (Sequenzen) hinter die übernommenen IDs setzen, damit neue Belege/Imports keine alte ID bekommen."""
    if db.get_bind().dialect.name != "postgresql":
        return
    for table in ("import_batches", "documents"):
        db.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT MAX(id) FROM {table}) IS NOT NULL)"))


def _rule_status(db: Session, document_id: int) -> str | None:
    from app.models.schema_def import ExtractedRecord
    row = db.query(ExtractedRecord.data["_validation"]).filter(
        ExtractedRecord.document_id == document_id, ExtractedRecord.parent_record_id.is_(None)).first()
    val = row[0] if row else None
    return val.get("status") if isinstance(val, dict) else None


def _migrate_suppliers(conn, db: Session, version: SchemaVersion, mapping: list[dict], options: RunOptions, probe: dict) -> None:
    """MAIN-Lieferanten-Stammdaten vorab übernehmen (über dieselbe Zuordnung wie bei den Belegen: IBAN → USt-IdNr. → Name)."""
    master = next((e for e in version.entities if e.is_master and e.master_table == "vendor"), None)
    if master is None:
        return
    entries = _entries_for(mapping, master.name)
    last = 0
    while not _cancel.is_set():
        rows = _rows(conn, f"SELECT * FROM {probe['sup_table']} WHERE id > :last ORDER BY id LIMIT :n", last=last, n=BLOCK)
        if not rows:
            break
        last = rows[-1]["id"]
        existing = _load_items(db, "supplier", [r["id"] for r in rows])
        for s in rows:
            if s["id"] in existing:
                continue
            try:
                with db.begin_nested():
                    values = _entity_dict(master, entries, SourceCtx({}, {}, s, {}))
                    vendor = schema_pipeline.match_vendor(db, master, values)
                    _remember(db, "supplier", s["id"], vendor.id if vendor else None, "imported" if vendor else "skipped", None, None)
                _bump("suppliers")
            except Exception as exc:
                _bump("supplier_error")
                _err(f"Lieferant {s['id']}: {str(exc)[:200]}")
        if options.dry_run:
            db.rollback()
        else:
            db.commit()


def start(session_factory: Callable[[], Session], options: RunOptions) -> dict:
    with _lock:
        if _state.get("running"):
            raise RuntimeError("Es läuft bereits eine Übernahme.")
        _state.clear()
        _state.update(running=True, message="Wird gestartet …", counts={}, errors=[], total=0, processed=0)
    threading.Thread(target=run, args=(session_factory, options), name="main-migration", daemon=True).start()
    return get_state()


def preview(db: Session, old_document_id: int) -> dict:
    """Ein MAIN-Beleg durch die Übersetzung schicken, ohne etwas zu speichern: zeigt das übersetzte JSON und die Schema-Prüfung."""
    cfg = get_config(db)
    engine = open_source(cfg.source_url or "")
    version = sd.get_active(db)
    if version is None:
        raise MigrationError("Es gibt kein aktives Schema.")
    with engine.connect() as conn:
        docs = _rows(conn, "SELECT * FROM documents WHERE id = :i", i=old_document_id)
        if not docs:
            raise MigrationError(f"Beleg {old_document_id} gibt es in MAIN nicht.")
        doc = docs[0]
        exts = _rows(conn, "SELECT * FROM invoice_extractions WHERE document_id = :i", i=old_document_id)
        ext = exts[0] if exts else None
        positions = _rows(conn, "SELECT * FROM order_positions WHERE document_id = :i ORDER BY position_index, id", i=old_document_id)
        sup = None
        probe = _probe(conn)
        link = (ext or {}).get(probe["ext_link"])
        if link:
            r = _rows(conn, f"SELECT * FROM {probe['sup_table']} WHERE {probe['sup_key']} = :i", i=link)
            sup = r[0] if r else None
    raw = _parse_raw((ext or {}).get("raw_response")) if ext else None
    out: dict = {"document": {k: doc.get(k) for k in ("id", "original_filename", "status", "document_type", "document_type_id", "company", "year")},
                 "has_raw_response": raw is not None, "problems": check_mapping(version, cfg.mapping or [])}
    if raw is None:
        out["parsed"], out["schema_errors"] = None, ["Keine verwertbare KI-Antwort — der Beleg würde ohne Daten übernommen."]
        return out
    parsed = build_parsed(version, cfg.mapping or [], doc, ext or {}, sup or {}, raw, positions)
    out["parsed"] = parsed
    try:
        schema_pipeline.validate_output(build_extraction_model(version), parsed)
        out["schema_errors"] = []
    except schema_pipeline.SchemaValidationFailed as exc:
        out["schema_errors"] = exc.messages[:20]
    return out
