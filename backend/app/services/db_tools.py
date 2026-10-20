"""
Datenbank-Werkzeuge für den Daten-Browser (CRUD) und den KI-Assistenten.

Alles läuft über die ORM-Metadaten (`Base.metadata`), nicht über freies SQL — nur die
KI darf zusätzlich *lesende* SELECT-Abfragen stellen (`run_readonly_sql`, mehrfach abgesichert).

Sicherheitsregeln:
- Tabellen mit Zugangsdaten (`users`, `user_sessions`) sind nirgends sichtbar.
- Spalten mit Geheimnissen (`ai_clients.api_key`) werden nie ausgegeben und nie geschrieben.
- Schreiben geht nur über Tabellen mit genau einem Primärschlüssel und nur auf bekannte Spalten
  (Typen werden geprüft/umgewandelt). Primärschlüssel lassen sich nicht ändern.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, Table, Text, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.types import JSON

from app.database import Base

HIDDEN_TABLES = {"users", "user_sessions", "alembic_version"}
SECRET_COLUMNS = {("ai_clients", "api_key")}
MASK = "••••••"
CELL_LIMIT = 300              # Zellen in Listen werden gekürzt (Volltext über die Einzelzeile)
MAX_LIMIT = 500


class DbToolError(ValueError):
    """Verständlicher Fehler für Nutzer/KI (wird zu HTTP 400/404)."""


# ─── Metadaten ────────────────────────────────────────────────────────────────

def visible_tables() -> dict[str, Table]:
    return {t.name: t for t in Base.metadata.sorted_tables if t.name not in HIDDEN_TABLES}


def get_table(name: str) -> Table:
    t = visible_tables().get(name)
    if t is None:
        raise DbToolError(f"Tabelle '{name}' gibt es nicht (oder sie ist nicht freigegeben).")
    return t


def _type_name(col) -> str:
    t = col.type
    if isinstance(t, Boolean):
        return "bool"
    if isinstance(t, Integer):
        return "int"
    if isinstance(t, (Numeric, Float)):
        return "zahl"
    if isinstance(t, DateTime):
        return "datetime"
    if isinstance(t, Date):
        return "date"
    if isinstance(t, JSON) or t.__class__.__name__ in ("JSONB", "JSON"):
        return "json"
    if isinstance(t, (String, Text)):
        return "text"
    return t.__class__.__name__.lower()


def _is_secret(table: str, col: str) -> bool:
    return (table, col) in SECRET_COLUMNS


def single_pk(table: Table) -> str | None:
    cols = list(table.primary_key.columns)
    return cols[0].name if len(cols) == 1 else None


def describe_table(table: Table, count: int | None = None) -> dict:
    fks = {}
    for fk in table.foreign_keys:
        fks[fk.parent.name] = f"{fk.column.table.name}.{fk.column.name}"
    return {
        "name": table.name,
        "count": count,
        "primary_key": single_pk(table),
        "columns": [
            {
                "name": c.name,
                "type": _type_name(c),
                "nullable": bool(c.nullable),
                "primary_key": bool(c.primary_key),
                "references": fks.get(c.name),
                "secret": _is_secret(table.name, c.name),
            }
            for c in table.columns
        ],
    }


def list_tables(db: Session, with_counts: bool = True) -> list[dict]:
    out = []
    for name, t in visible_tables().items():
        count = None
        if with_counts:
            try:
                count = db.execute(select(func.count()).select_from(t)).scalar_one()
            except Exception:
                db.rollback()
        out.append(describe_table(t, count))
    return out


def schema_text() -> str:
    """Kompakte Schemabeschreibung für den KI-Prompt."""
    lines = []
    for name, t in visible_tables().items():
        fks = {fk.parent.name: f"{fk.column.table.name}.{fk.column.name}" for fk in t.foreign_keys}
        cols = []
        for c in t.columns:
            if _is_secret(name, c.name):
                continue
            s = f"{c.name} {_type_name(c)}"
            if c.primary_key:
                s += " PK"
            if c.name in fks:
                s += f" →{fks[c.name]}"
            cols.append(s)
        lines.append(f"- {name}({', '.join(cols)})")
    return "\n".join(lines)


# ─── Werte ────────────────────────────────────────────────────────────────────

def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} Bytes>"
    if isinstance(value, (dict, list)):
        return value
    return str(value)


def _shorten(value: Any, limit: int = CELL_LIMIT) -> Any:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… (+{len(value) - limit} Zeichen)"
    return value


def _row_dict(table: Table, row, shorten: bool) -> dict:
    out = {}
    for col in table.columns:
        v = row._mapping[col]
        if _is_secret(table.name, col.name):
            v = MASK if v else None
        else:
            v = json_safe(v)
            if shorten:
                v = _shorten(v)
        out[col.name] = v
    return out


def _coerce(table: Table, col_name: str, value: Any) -> Any:
    """Wandelt Eingaben (meist Text aus dem Formular) in den Spaltentyp um; Leertext bei Nullable → NULL."""
    col = table.columns.get(col_name)
    if col is None:
        raise DbToolError(f"Spalte '{col_name}' gibt es in '{table.name}' nicht.")
    if _is_secret(table.name, col_name):
        raise DbToolError(f"Spalte '{col_name}' ist geschützt und nicht bearbeitbar.")
    if col.primary_key:
        raise DbToolError("Der Primärschlüssel lässt sich nicht ändern.")
    if value is None or (isinstance(value, str) and value.strip() == "" and not isinstance(col.type, (String, Text))):
        if value is None or col.nullable:
            if not col.nullable:
                raise DbToolError(f"'{col_name}' darf nicht leer sein.")
            return None
        raise DbToolError(f"'{col_name}' darf nicht leer sein.")
    kind = _type_name(col)
    try:
        if kind == "bool":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("1", "true", "ja", "wahr", "yes", "t"):
                return True
            if s in ("0", "false", "nein", "falsch", "no", "f"):
                return False
            raise ValueError
        if kind == "int":
            return int(str(value).strip()) if not isinstance(value, int) else value
        if kind == "zahl":
            if isinstance(value, (int, float, Decimal)):
                return value
            s = str(value).strip().replace(" ", "")
            if "," in s:
                s = s.replace(".", "").replace(",", ".")
            return Decimal(s) if isinstance(col.type, Numeric) else float(s)
        if kind == "datetime":
            if isinstance(value, dt.datetime):
                return value
            return dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if kind == "date":
            if isinstance(value, dt.date):
                return value
            return dt.date.fromisoformat(str(value).strip()[:10])
        if kind == "json":
            return json.loads(value) if isinstance(value, str) else value
    except (ValueError, ArithmeticError, json.JSONDecodeError):
        raise DbToolError(f"Ungültiger Wert für '{col_name}' ({kind}): {value!r}") from None
    s = str(value)
    maxlen = getattr(col.type, "length", None)
    if maxlen and len(s) > maxlen:
        raise DbToolError(f"'{col_name}': höchstens {maxlen} Zeichen erlaubt (sind {len(s)}).")
    return s


def _pk_value(table: Table, pk: Any):
    name = single_pk(table)
    if name is None:
        raise DbToolError(f"'{table.name}' hat keinen einfachen Primärschlüssel — nur lesbar.")
    col = table.columns[name]
    try:
        return col, (int(pk) if isinstance(col.type, Integer) else pk)
    except (TypeError, ValueError):
        raise DbToolError(f"Ungültiger Primärschlüssel: {pk!r}") from None


# ─── Lesen ────────────────────────────────────────────────────────────────────

def browse(
    db: Session,
    table_name: str,
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    sort: str | None = None,
    order: str = "asc",
    filters: dict[str, str] | None = None,
) -> tuple[list[dict], int]:
    """Zeilen einer Tabelle: Freitext über alle Textspalten (alle Wörter), Spaltenfilter (Gleichheit), Sortierung."""
    table = get_table(table_name)
    limit = max(1, min(limit, MAX_LIMIT))
    conds = []
    if q and q.strip():
        text_cols = [c for c in table.columns if isinstance(c.type, (String, Text)) and not _is_secret(table.name, c.name)]
        id_cols = [c for c in table.columns if isinstance(c.type, Integer) and c.primary_key]
        for word in q.split():
            like = f"%{word.replace('%', '').replace('_', ' ')}%"
            ors = [func.lower(c).like(like.lower()) for c in text_cols]
            if word.isdigit():
                ors += [c == int(word) for c in id_cols]
            if ors:
                from sqlalchemy import or_
                conds.append(or_(*ors))
    for col_name, val in (filters or {}).items():
        col = table.columns.get(col_name)
        if col is None or _is_secret(table.name, col_name):
            raise DbToolError(f"Filter: Spalte '{col_name}' gibt es nicht.")
        if val == "":
            conds.append(col.is_(None))
        else:
            try:
                conds.append(col == _coerce_filter(col, val))
            except DbToolError:
                raise
    base = select(table)
    count_q = select(func.count()).select_from(table)
    for c in conds:
        base, count_q = base.where(c), count_q.where(c)
    total = db.execute(count_q).scalar_one()
    sort_col = table.columns.get(sort) if sort else None
    if sort_col is None:
        pk = single_pk(table)
        sort_col = table.columns[pk] if pk else list(table.columns)[0]
        order = "desc" if sort is None else order
    base = base.order_by(sort_col.desc() if order == "desc" else sort_col.asc()).limit(limit).offset(max(0, offset))
    rows = [_row_dict(table, r, shorten=True) for r in db.execute(base).all()]
    return rows, total


def _coerce_filter(col, val: str):
    kind = _type_name(col)
    try:
        if kind == "int":
            return int(val)
        if kind == "bool":
            return val.strip().lower() in ("1", "true", "ja")
        if kind == "zahl":
            return Decimal(val.replace(",", "."))
    except (ValueError, ArithmeticError):
        raise DbToolError(f"Filter: '{val}' passt nicht zu Spalte '{col.name}' ({kind}).") from None
    return val


def get_row(db: Session, table_name: str, pk: Any) -> dict:
    table = get_table(table_name)
    col, value = _pk_value(table, pk)
    row = db.execute(select(table).where(col == value)).first()
    if row is None:
        raise LookupError(f"{table_name} #{pk} nicht gefunden")
    return _row_dict(table, row, shorten=False)


# ─── Schreiben ────────────────────────────────────────────────────────────────

def _diff(before: dict, after: dict) -> dict:
    return {k: {"alt": _shorten(before.get(k), 120), "neu": _shorten(after.get(k), 120)} for k in after if before.get(k) != after.get(k)}


def update_row(db: Session, table_name: str, pk: Any, changes: dict) -> dict:
    table = get_table(table_name)
    col, value = _pk_value(table, pk)
    if not changes:
        raise DbToolError("Keine Änderungen angegeben.")
    values = {name: _coerce(table, name, v) for name, v in changes.items()}
    before = get_row(db, table_name, pk)
    result = db.execute(table.update().where(col == value).values(**values))
    if result.rowcount == 0:
        db.rollback()
        raise LookupError(f"{table_name} #{pk} nicht gefunden")
    db.commit()
    after = get_row(db, table_name, pk)
    return {"row": after, "changed": _diff(before, after), "before": before}


def insert_row(db: Session, table_name: str, values: dict) -> dict:
    table = get_table(table_name)
    pk_name = single_pk(table)
    data = {}
    for name, v in values.items():
        col = table.columns.get(name)
        if col is not None and col.primary_key:
            continue                               # Primärschlüssel vergibt die Datenbank
        if v == "" and col is not None and col.nullable:
            continue
        data[name] = _coerce(table, name, v)
    try:
        res = db.execute(table.insert().values(**data))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise DbToolError(f"Speichern nicht möglich: {str(exc.__cause__ or exc).splitlines()[0]}") from None
    new_pk = res.inserted_primary_key[0] if pk_name and res.inserted_primary_key else None
    return {"row": get_row(db, table_name, new_pk) if new_pk is not None else data, "pk": new_pk}


def delete_row(db: Session, table_name: str, pk: Any) -> dict:
    table = get_table(table_name)
    col, value = _pk_value(table, pk)
    before = get_row(db, table_name, pk)
    try:
        db.execute(table.delete().where(col == value))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise DbToolError(
            f"Löschen nicht möglich (wird noch von anderen Daten verwendet?): {str(exc.__cause__ or exc).splitlines()[0]}"
        ) from None
    return {"deleted": before}


# ─── Lesende SQL-Abfragen (nur KI) ────────────────────────────────────────────

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|vacuum|analyze|reindex|"
    r"set|reset|listen|notify|prepare|execute|merge|cluster|discard|"
    r"pg_sleep|pg_read_file|pg_ls_dir|lo_import|lo_export|dblink|pg_terminate_backend|pg_cancel_backend|set_config)\b",
    re.IGNORECASE,
)
_HIDDEN_NAMES = re.compile(
    r"\b(" + "|".join(sorted(HIDDEN_TABLES)) + r"|password_hash|api_key|information_schema|pg_\w+)\b", re.IGNORECASE
)
SQL_ROW_LIMIT = 200


def check_readonly_sql(sql: str) -> str:
    """Prüft, dass es genau EINE lesende Abfrage ist. Gibt die bereinigte Abfrage zurück."""
    s = (sql or "").strip()
    s = re.sub(r"--[^\n]*", " ", s)                      # Zeilenkommentare
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)    # Blockkommentare
    s = s.strip().rstrip(";").strip()
    if not s:
        raise DbToolError("Leere Abfrage.")
    if ";" in s:
        raise DbToolError("Nur eine einzelne Abfrage ist erlaubt (kein ';').")
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        raise DbToolError("Erlaubt sind nur lesende Abfragen (SELECT / WITH).")
    m = _FORBIDDEN.search(s)
    if m:
        raise DbToolError(f"'{m.group(0)}' ist in Abfragen nicht erlaubt (nur lesen).")
    m = _HIDDEN_NAMES.search(s)
    if m:
        raise DbToolError(f"'{m.group(0)}' ist geschützt und nicht abfragbar.")
    return s


def run_readonly_sql(db: Session, sql: str, max_rows: int = SQL_ROW_LIMIT, timeout_s: int = 20) -> dict:
    s = check_readonly_sql(sql)
    wrapped = f"SELECT * FROM ({s}) AS q LIMIT {int(max_rows) + 1}"
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(text(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'"))
        result = db.execute(text(wrapped))
        columns = list(result.keys())
        rows = result.fetchall()
    except Exception as exc:
        db.rollback()
        msg = str(getattr(exc, "orig", None) or exc).splitlines()[0]
        raise DbToolError(f"SQL-Fehler: {msg}") from None
    finally:
        try:
            db.rollback()                                  # nie etwas festschreiben
        except Exception:
            pass
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return {
        "columns": columns,
        "rows": [[_shorten(json_safe(v)) for v in r] for r in rows],
        "truncated": truncated,
        "row_count": len(rows),
    }
