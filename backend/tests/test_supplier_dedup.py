"""Tests der Lieferanten-Dublettensuche (reine Logik)."""

from types import SimpleNamespace as NS

import pytest

from app.services.supplier_dedup import (
    find_duplicate_groups, normalize_iban, normalize_name, normalize_tax_number, normalize_vat,
)


def S(id, name, iban=None, vat_id=None, tax_number=None):
    return NS(id=id, name=name, iban=iban, vat_id=vat_id, tax_number=tax_number)


@pytest.mark.parametrize("raw, expected", [
    ("Müller & Söhne GmbH & Co. KG", "mueller soehne"),
    ("ACME GmbH", "acme"),
    ("Acme  G.m.b.H.", "acme"),
    ("Foo Bar a.G.", "foo bar"),
    ("Schmidt e.K.", "schmidt"),
    ("Straße & Weg AG", "strasse weg"),
    ("GmbH", "gmbh"),                               # nur Rechtsform → nicht leer machen
    ("  Café  Crème S.A. ", "cafe creme"),
    (None, ""), ("", ""),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_identifiers():
    assert normalize_iban("de02 1203 0000 0000 2020 51") == "DE02120300000000202051"
    assert normalize_iban("DE12") == "" and normalize_iban(None) == ""
    assert normalize_vat("DE 123.456-789") == "DE123456789" and normalize_vat("12") == ""
    assert normalize_tax_number("12/345/67890") == "1234567890" and normalize_tax_number("0") == ""


def test_same_iban_is_high_confidence_even_with_different_names():
    g = find_duplicate_groups([
        S(1, "ACME GmbH", iban="DE02 1203 0000 0000 2020 51"),
        S(2, "Völlig anders AG", iban="DE02120300000000202051"),
        S(3, "Unbeteiligt"),
    ])
    assert len(g) == 1 and g[0].supplier_ids == [1, 2]
    assert g[0].reasons == ["iban"] and g[0].confidence == "high"


def test_vat_and_tax_number_matches():
    g = find_duplicate_groups([S(1, "A Firma", vat_id="DE 111 222 333"), S(2, "B Firma", vat_id="de111222333"),
                               S(3, "C Firma", tax_number="12/345/678"), S(4, "D Firma", tax_number="12345678")])
    assert {tuple(x.supplier_ids): x.reasons for x in g} == {(1, 2): ["vat_id"], (3, 4): ["tax_number"]}
    assert all(x.confidence == "high" for x in g)


def test_normalized_name_is_medium_confidence():
    g = find_duplicate_groups([S(1, "Müller GmbH"), S(2, "MUELLER  gmbh & co. kg"), S(3, "Meier GmbH")])
    assert len(g) == 1 and g[0].supplier_ids == [1, 2]
    assert g[0].reasons == ["name"] and g[0].confidence == "medium"


def test_similar_name_is_low_confidence_and_threshold_holds():
    g = find_duplicate_groups([S(1, "Baustoffhandel Hoffmann"), S(2, "Baustoffhandel Hofmann")])
    assert g[0].reasons == ["name_similar"] and g[0].confidence == "low"
    assert find_duplicate_groups([S(1, "Baustoffhandel Hoffmann"), S(2, "Gartenbau Hoffmann")]) == []
    assert find_duplicate_groups([S(1, "AB"), S(2, "AC")]) == []          # zu kurz → kein Ähnlichkeitsvergleich


def test_chain_groups_merge_and_collect_all_reasons():
    g = find_duplicate_groups([
        S(1, "Alpha GmbH", iban="DE02120300000000202051"),
        S(2, "Beta AG", iban="DE02 1203 0000 0000 2020 51", vat_id="DE999888777"),
        S(3, "Gamma KG", vat_id="DE999888777"),
        S(4, "Alpha"),                                  # gleicher Name wie 1 nach Normalisierung
    ])
    assert len(g) == 1 and g[0].supplier_ids == [1, 2, 3, 4]
    assert g[0].reasons == ["iban", "vat_id", "name"] and g[0].confidence == "high"


def test_sorting_high_first_and_no_duplicates_found():
    groups = find_duplicate_groups([
        S(1, "Name Eins"), S(2, "Name  Eins GmbH"),                      # mittel
        S(3, "X", iban="DE02120300000000202051"), S(4, "Y", iban="DE02120300000000202051"),   # hoch
    ])
    assert [x.confidence for x in groups] == ["high", "medium"]
    assert find_duplicate_groups([S(1, "A Firma"), S(2, "B Firma")]) == []
    assert find_duplicate_groups([]) == []


def test_empty_values_never_match():
    assert find_duplicate_groups([S(1, "Eins", iban="", vat_id="  "), S(2, "Zwei", iban=None, vat_id="")]) == []
