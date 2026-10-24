"""Normalisierer: Einzelbausteine, Kettenreihenfolge, Fehlerverhalten, Validierung, Vorschau-API."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import normalizers as n


def chain(*steps):
    return [{"name": s} if isinstance(s, str) else {"name": s[0], "params": s[1]} for s in steps]


def run(value, *steps):
    return n.apply_chain(value, chain(*steps))


@pytest.mark.parametrize("raw,expected", [
    ("1.234,56", "1234.56"), ("1.234,56 €", "1234.56"), ("719,99", "719.99"), ("€ 12,50", "12.50"), ("1.234.567,89", "1234567.89"),
    ("1,5", "1.5"), ("1.234", "1234"), ("12.5", "12.5"), ("0.500", "0.500"), ("1.234.567", "1234567"), ("1,234.56", "1234.56"),
    ("(12,50)", "-12.50"), ("12,50-", "-12.50"), ("-3,2", "-3.2"), ("−3,2", "-3.2"), ("+7", "7"), ("1 234,56 EUR", "1234.56"),
    ("1 234,56", "1234.56"), (42, "42"), (3.5, "3.5"), (Decimal("2.10"), "2.10"),
])
def test_parse_german_number(raw, expected):
    assert n.parse_german_number(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["abc", "12,3,4", "1,2.3,4x", "--"])
def test_parse_german_number_rejects_garbage(raw):
    with pytest.raises(n.NormalizeError):
        n.parse_german_number(raw)


def test_parse_german_number_empty_and_none():
    assert n.parse_german_number(None) is None and n.parse_german_number("  ") is None and n.parse_german_number("€") is None


def test_chain_order_matters():
    # erst Währungszeichen/Leerraum bereinigen, dann parsen — andersherum würde "abc" … bleibt Text
    r = run(" 1.234,50 € ", "trim", "parse_german_number")
    assert r.ok and r.value == Decimal("1234.50")
    # Tausenderpunkt entfernen, dann Komma → Punkt ergibt dieselbe Zahl; umgekehrt entsteht ein anderer Text
    assert run("1.234,56", "remove_thousands", "comma_to_point").value == "1234.56"
    assert run("1.234,56", "comma_to_point", "remove_thousands").value == "123456"


def test_text_normalizers():
    assert run("  a  b \n c ", "collapse_whitespace").value == "a b c"
    assert run("de12 3456", ("remove_chars", {"chars": " "}), ("case", {"mode": "upper"})).value == "DE123456"
    assert run("ACME gmbh", ("case", {"mode": "title"})).value == "Acme Gmbh"
    assert run("acme", ("case", {"mode": "lower"})).value == "acme"
    assert run("RE-2024/0815", ("regex_replace", {"pattern": r"^RE-", "replacement": ""})).value == "2024/0815"
    assert run("a.b.c", ("remove_thousands", {"separator": "."})).value == "abc"
    assert run("Rechnung 12", ("regex_replace", {"pattern": "rechnung", "replacement": "R", "ignore_case": True})).value == "R 12"
    assert run("2024-05", ("regex_replace", {"pattern": r"(\d+)-(\d+)", "replacement": r"\2/\1"})).value == "05/2024"


def test_empty_to_null():
    assert run("n/a", "empty_to_null").value is None
    assert run(" - ", "empty_to_null").value is None
    assert run("Wert", "empty_to_null").value == "Wert"
    assert run("x", ("empty_to_null", {"values": ["x"]})).value is None


def test_round_and_to_integer():
    assert run("2,345", ("round", {"digits": 2})).value == Decimal("2.35")
    assert run(Decimal("2.5"), ("round", {"digits": 0})).value == Decimal("3")
    assert run("12,0", "to_integer").value == 12
    r = run("12,5", "to_integer")
    assert not r.ok and r.value is None and r.issues[0]["name"] == "to_integer"


@pytest.mark.parametrize("raw,iso", [
    ("25.03.2025", "2025-03-25"), ("2025-03-25", "2025-03-25"), ("25.03.25", "2025-03-25"), ("25/03/2025", "2025-03-25"),
    ("5.1.2025", "2025-01-05"), ("2025-03-25T10:11:12", "2025-03-25"), (date(2025, 3, 25), "2025-03-25"),
])
def test_parse_date(raw, iso):
    assert run(raw, "parse_date").value == iso


def test_parse_date_month_first_and_errors():
    assert run("03/25/2025", ("parse_date", {"month_first": True})).value == "2025-03-25"
    r = run("gestern", "parse_date")
    assert r.value is None and r.issues[0]["message"].startswith("Unbekanntes Datumsformat")
    assert run("", "parse_date").value is None


def test_chain_stops_at_first_error_and_never_raises():
    r = run("abc", "parse_german_number", "round")
    assert r.value is None and len(r.issues) == 1 and r.issues[0]["step"] == 1
    assert n.apply_chain("x", [{"name": "gibt_es_nicht"}]).issues[0]["message"] == "unbekannter Baustein"
    assert n.apply_chain("x", [{"name": "case", "params": {"mode": "kaputt"}}]).ok is False
    assert n.apply_chain("x", None).value == "x" and n.apply_chain("x", []).ok


def test_regex_guard_and_invalid_pattern():
    assert not run("a" * 30_000, ("regex_replace", {"pattern": "a", "replacement": "b"})).ok
    assert not run("abc", ("regex_replace", {"pattern": "(", "replacement": ""})).ok


def test_validate_chain():
    assert n.validate_chain(chain("trim", ("round", {"digits": 2}))) == []
    errs = n.validate_chain([{"name": "nope"}, {"name": "round", "params": {"digits": 99}}, {"name": "case", "params": {"x": 1}}, {}])
    assert len(errs) == 4
    assert n.validate_chain([{"name": "regex_replace", "params": {"pattern": "("}}])
    assert n.validate_chain([{"name": "trim", "bogus": 1}])


def test_to_jsonable():
    assert n.to_jsonable(Decimal("12")) == 12 and isinstance(n.to_jsonable(Decimal("12")), int)
    assert n.to_jsonable(Decimal("12.5")) == 12.5
    assert n.to_jsonable(date(2025, 3, 25)) == "2025-03-25"
    assert n.to_jsonable("x") == "x"


def test_describe_lists_every_normalizer_with_schema():
    info = {d["name"]: d for d in n.describe()}
    assert set(info) == set(n.NORMALIZERS) and "parse_german_number" in info
    assert "digits" in info["round"]["params_schema"]["properties"]


def test_preview_api():
    c = TestClient(app)
    r = c.post("/api/schema/normalizers/preview", json={"value": "1.234,56 €", "chain": chain("trim", "parse_german_number")})
    assert r.status_code == 200 and r.json()["value"] == 1234.56 and r.json()["ok"]
    bad = c.post("/api/schema/normalizers/preview", json={"value": "x", "chain": [{"name": "nope"}]}).json()
    assert not bad["ok"] and bad["errors"]
    assert {d["name"] for d in c.get("/api/schema/normalizers").json()} == set(n.NORMALIZERS)
