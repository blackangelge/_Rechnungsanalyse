"""Tests für die Plausibilitätsprüfung (reine Rechenlogik, keine DB nötig)."""

from app.services.plausibility_service import (
    STATUS_CORRECTED, STATUS_NEEDS_REVIEW, STATUS_OK, STATUS_UNCHECKED,
    parse_unit, validate_invoice,
)


def _raw(net, tax_rate=19.0, positions=None):
    tax = round(net * tax_rate / 100, 2)
    return {
        "positionen": positions or [],
        "zahlungsinformationen": {
            "gesamtbetrag_netto": net,
            "umsatzsteuer_zusammenfassung": [
                {"steuersatz": tax_rate, "nettobetrag": net, "steuerbetrag": tax}
            ],
            "gesamtbetrag_brutto": round(net + tax, 2),
        },
    }


def test_parse_unit_variants():
    assert parse_unit("Tonne").factor == 1000
    assert parse_unit("kg").dimension == "mass"
    assert parse_unit("100 kg").factor == 100
    assert parse_unit("m³").dimension == "volume"
    assert parse_unit("Liter").dimension == "volume"
    assert parse_unit("Palette").dimension == "pack"
    assert parse_unit("Stück").dimension == "count"
    assert parse_unit("Foo").dimension is None
    assert parse_unit(None) is None and parse_unit("  ") is None


def test_consistent_invoice_is_ok():
    pos = [{"quantity": 2, "unit": "Stück", "unit_price": 10.0, "total_price": 20.0}]
    r = validate_invoice({"total_amount": 23.8}, pos, _raw(20.0))
    assert r.status == STATUS_OK and not r.issues


def test_nothing_to_check():
    assert validate_invoice({}, [], None).status == STATUS_UNCHECKED


def test_ton_price_with_kg_quantity_is_explained_not_flagged():
    """1 t = 50 €, 500 kg → Rechnung weist korrekt 25 € aus."""
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25.0}]
    raw = _raw(25.0, positions=[{"preiseinheit": "t"}])
    r = validate_invoice({"total_amount": 29.75}, pos, raw)
    assert r.status == STATUS_OK
    assert [i.code for i in r.issues] == ["price_unit_differs"]
    assert pos[0]["total_price"] == 25.0   # nichts verändert


def test_ton_price_with_kg_quantity_even_without_preiseinheit():
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25.0}]
    r = validate_invoice({"total_amount": 29.75}, pos, _raw(25.0))
    assert r.status == STATUS_OK


def test_wrongly_multiplied_total_is_auto_corrected():
    """KI hat 500 × 50 = 25000 gerechnet, Rechnung weist aber 25 € aus."""
    pos = [
        {"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25000.0},
        {"quantity": 1, "unit": "Stück", "unit_price": 10.0, "total_price": 10.0},
    ]
    r = validate_invoice({"total_amount": 41.65}, pos, _raw(35.0))
    assert r.status == STATUS_CORRECTED
    assert pos[0]["total_price"] == 25.0
    assert r.corrections[0]["from"] == 25000.0 and r.corrections[0]["factor"] == 0.001


def test_auto_correct_disabled_only_reports():
    pos = [
        {"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25000.0},
        {"quantity": 1, "unit": "Stück", "unit_price": 10.0, "total_price": 10.0},
    ]
    r = validate_invoice({"total_amount": 41.65}, pos, _raw(35.0), auto_correct=False)
    assert r.status == STATUS_NEEDS_REVIEW and pos[0]["total_price"] == 25000.0
    assert not r.corrections


def test_liter_vs_hektoliter_factor():
    pos = [
        {"quantity": 2, "unit": "hl", "unit_price": 100.0, "total_price": 200.0},
        {"quantity": 1, "unit": "Stück", "unit_price": 5.0, "total_price": 5.0},
    ]
    # Rechnung: 2 hl à 100 € je 100 l?? → tatsächlich 2.00 € + 5 € = 7 € (Faktor 0.01)
    r = validate_invoice({}, pos, _raw(7.0))
    assert r.status == STATUS_CORRECTED and pos[0]["total_price"] == 2.0


def test_ambiguous_solution_is_not_guessed():
    # Zwei gleiche Positionen → beide Korrekturen wären gleich gut → nicht raten
    pos = [
        {"quantity": 1, "unit": "kg", "unit_price": 1000.0, "total_price": 1000.0},
        {"quantity": 1, "unit": "kg", "unit_price": 1000.0, "total_price": 1000.0},
    ]
    r = validate_invoice({}, pos, _raw(1001.0))
    assert r.status == STATUS_NEEDS_REVIEW
    assert [p["total_price"] for p in pos] == [1000.0, 1000.0]


def test_unexplained_position_mismatch_needs_review():
    pos = [{"quantity": 3, "unit": "Stück", "unit_price": 7.0, "total_price": 99.0}]
    r = validate_invoice({"total_amount": 117.81}, pos, _raw(99.0))
    assert r.status == STATUS_NEEDS_REVIEW
    assert r.issues[0].code == "position_mismatch"


def test_discount_percent_explains_difference():
    pos = [{"quantity": 10, "unit": "Stück", "unit_price": 10.0, "total_price": 90.0,
            "discount": "10%"}]
    r = validate_invoice({"total_amount": 107.1}, pos, _raw(90.0))
    assert r.status == STATUS_OK


def test_gross_net_tax_mismatch():
    raw = _raw(100.0)
    raw["zahlungsinformationen"]["gesamtbetrag_brutto"] = 190.0
    r = validate_invoice({"total_amount": 190.0}, [], raw)
    assert r.status == STATUS_NEEDS_REVIEW
    assert "gross_mismatch" in [i.code for i in r.issues]


def test_sum_matches_gross_when_positions_are_gross():
    pos = [{"quantity": 1, "unit": "Stück", "unit_price": 119.0, "total_price": 119.0}]
    r = validate_invoice({"total_amount": 119.0}, pos, None)
    assert r.status == STATUS_OK


# ═══ Durchrechnung: Artikel, Netto, Steuer, Brutto gegeneinander ═════════════════════════════

def _invoice(net, rows, positions_raw=None, gross=None):
    """raw-JSON mit USt-Zusammenfassung rows=[(satz, netto, steuer)]."""
    return {
        "positionen": positions_raw or [],
        "zahlungsinformationen": {
            "gesamtbetrag_netto": net,
            "umsatzsteuer_zusammenfassung": [
                {"steuersatz": r, "nettobetrag": b, "steuerbetrag": t} for r, b, t in rows],
            "gesamtbetrag_brutto": gross,
        },
    }


def _pos(total, qty=1, unit="Stück", price=None, **kw):
    return {"quantity": qty, "unit": unit, "unit_price": price if price is not None else total / qty,
            "total_price": total, **kw}


TWO_RATES_POS = [_pos(100.0), _pos(50.0)]
TWO_RATES_RAW = _invoice(150.0, [(19.0, 100.0, 19.0), (7.0, 50.0, 3.5)],
                         [{"steuersatz": 19.0}, {"steuersatz": 7.0}], gross=172.5)


def _labels(result):
    return {row["label"]: row["status"] for row in result.calc}


def test_full_calculation_ok_and_calc_rows_listed():
    r = validate_invoice({"total_amount": 172.5}, [dict(p) for p in TWO_RATES_POS], TWO_RATES_RAW)
    assert r.status == STATUS_OK and not r.issues
    labels = _labels(r)
    assert labels["Σ Positionspreise ↔ Netto (Rechnung)"] == "ok"
    assert labels["Steuer 19 %: Netto × Satz ↔ Steuerbetrag"] == "ok"
    assert labels["Steuer 7 %: Netto × Satz ↔ Steuerbetrag"] == "ok"
    assert labels["Netto 19 %: Σ Positionen ↔ USt-Zusammenfassung"] == "ok"
    assert labels["Netto 7 %: Σ Positionen ↔ USt-Zusammenfassung"] == "ok"
    assert labels["Steuer gesamt: aus Positionen berechnet ↔ ausgewiesen"] == "ok"
    assert labels["Netto + Steuer ↔ Brutto"] == "ok"
    assert any(l.startswith("Menge × Einzelpreis") and s == "ok" for l, s in labels.items())
    assert all(row["status"] == "ok" for row in r.calc)
    assert r.to_dict()["calc"] == r.calc                                   # wird mitgespeichert


def test_misread_gross_is_corrected_when_everything_else_confirms_each_other():
    fields = {"total_amount": 127.5}                                       # Brutto falsch gelesen
    r = validate_invoice(fields, [dict(p) for p in TWO_RATES_POS], TWO_RATES_RAW)
    assert r.status == STATUS_CORRECTED
    assert fields["total_amount"] == 172.5                                 # in-place korrigiert
    assert r.corrections == [{"field": "total_amount", "from": 127.5, "to": 172.5, "method": "net_plus_tax"}]
    assert [i.code for i in r.issues] == ["total_corrected"]
    assert _labels(r)["Netto + Steuer ↔ Brutto (korrigiert)"] == "ok"


def test_gross_correction_is_off_without_auto_correct():
    fields = {"total_amount": 127.5}
    r = validate_invoice(fields, [dict(p) for p in TWO_RATES_POS], TWO_RATES_RAW, auto_correct=False)
    assert r.status == STATUS_NEEDS_REVIEW and fields["total_amount"] == 127.5 and not r.corrections
    assert "gross_mismatch" in [i.code for i in r.issues]
    assert _labels(r)["Netto + Steuer ↔ Brutto"] == "warn"


def test_missing_gross_is_derived_from_net_plus_tax():
    fields = {"total_amount": None}
    r = validate_invoice(fields, [dict(p) for p in TWO_RATES_POS], TWO_RATES_RAW)
    assert fields["total_amount"] == 172.5 and r.status == STATUS_CORRECTED
    assert r.corrections[0] == {"field": "total_amount", "from": None, "to": 172.5, "method": "net_plus_tax"}
    assert [i.code for i in r.issues] == ["total_derived"]
    # ohne auto_correct wird nichts erfunden
    fields = {"total_amount": None}
    validate_invoice(fields, [dict(p) for p in TWO_RATES_POS], TWO_RATES_RAW, auto_correct=False)
    assert fields["total_amount"] is None


def test_gross_is_not_corrected_when_not_confirmed_by_positions():
    # Positionen passen NICHT zum Netto → es gibt keine gegenseitige Bestätigung → nur melden
    pos = [_pos(100.0), _pos(60.0)]
    fields = {"total_amount": 127.5}
    r = validate_invoice(fields, pos, TWO_RATES_RAW)
    assert fields["total_amount"] == 127.5
    assert r.status == STATUS_NEEDS_REVIEW and not any(c.get("field") for c in r.corrections)


def test_misread_net_is_reported_not_papered_over_by_scaling_positions():
    """Positionen = Brutto − Steuer, nur das Netto ist falsch gelesen (1500 statt 150)."""
    raw = _invoice(1500.0, [(19.0, 100.0, 19.0), (7.0, 50.0, 3.5)],
                   [{"steuersatz": 19.0}, {"steuersatz": 7.0}], gross=172.5)
    pos = [dict(p) for p in TWO_RATES_POS]
    r = validate_invoice({"total_amount": 172.5}, pos, raw)
    assert r.status == STATUS_NEEDS_REVIEW
    codes = [i.code for i in r.issues]
    assert "net_mismatch" in codes and "auto_corrected" not in codes and "sum_mismatch" not in codes
    assert [p["total_price"] for p in pos] == [100.0, 50.0]                 # Positionen unangetastet
    assert "Nettobetrag wurde wohl falsch gelesen" in next(i.message for i in r.issues if i.code == "net_mismatch")


def test_wrong_tax_rate_on_a_position_is_found():
    # Position 2 (50 €) trägt 19 % statt 7 % → je Steuersatz passt die USt-Zusammenfassung nicht
    raw = _invoice(150.0, [(19.0, 100.0, 19.0), (7.0, 50.0, 3.5)],
                   [{"steuersatz": 19.0}, {"steuersatz": 19.0}], gross=172.5)
    r = validate_invoice({"total_amount": 172.5}, [dict(p) for p in TWO_RATES_POS], raw)
    assert r.status == STATUS_NEEDS_REVIEW
    assert [i.code for i in r.issues].count("rate_net_mismatch") == 2          # 19 % zu viel, 7 % fehlt
    assert _labels(r)["Netto 19 %: Σ Positionen ↔ USt-Zusammenfassung"] == "warn"


def test_rate_missing_in_tax_summary():
    raw = _invoice(150.0, [(19.0, 150.0, 28.5)],
                   [{"steuersatz": 19.0}, {"steuersatz": 7.0}], gross=178.5)
    r = validate_invoice({"total_amount": 178.5}, [dict(p) for p in TWO_RATES_POS], raw)
    msgs = [i.message for i in r.issues if i.code == "rate_net_mismatch"]
    assert any("fehlen in der USt-Zusammenfassung" in m for m in msgs)


def test_positions_with_gross_prices_receipt():
    raw = _invoice(100.0, [(19.0, 100.0, 19.0)], gross=119.0)
    r = validate_invoice({"total_amount": 119.0}, [_pos(119.0)], raw)
    assert r.status == STATUS_OK
    assert _labels(r)["Σ Positionspreise ↔ Brutto (Positionen brutto)"] == "ok"


def test_gross_minus_tax_confirms_positions_when_net_is_missing():
    raw = _invoice(None, [(19.0, 100.0, 19.0)], gross=119.0)
    r = validate_invoice({"total_amount": 119.0}, [_pos(100.0)], raw)
    assert r.status == STATUS_OK
    assert _labels(r)["Σ Positionspreise ↔ Brutto − Steuer"] == "ok"


def test_invoice_level_discount_is_taken_into_account():
    raw = _invoice(90.0, [(19.0, 90.0, 17.1)], gross=107.1)
    r = validate_invoice({"total_amount": 107.1, "discount_amount": 10.0}, [_pos(100.0)], raw)
    assert r.status == STATUS_OK and not r.issues


def test_unit_example_still_works_with_the_new_rules():
    """1 t = 50 €, 500 kg → 25 €; Netto/Steuer/Brutto passen dazu."""
    raw = _invoice(25.0, [(19.0, 25.0, 4.75)], [{"steuersatz": 19.0, "preiseinheit": "t"}], gross=29.75)
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25.0}]
    r = validate_invoice({"total_amount": 29.75}, pos, raw)
    assert r.status == STATUS_OK and [i.code for i in r.issues] == ["price_unit_differs"]


def test_wrongly_multiplied_total_still_auto_corrected_and_gross_untouched():
    raw = _invoice(35.0, [(19.0, 35.0, 6.65)], [{"steuersatz": 19.0}, {"steuersatz": 19.0}], gross=41.65)
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 50.0, "total_price": 25000.0}, _pos(10.0)]
    fields = {"total_amount": 41.65}
    r = validate_invoice(fields, pos, raw)
    assert r.status == STATUS_CORRECTED and pos[0]["total_price"] == 25.0 and fields["total_amount"] == 41.65
    assert [c["field"] for c in r.corrections] == ["total_price"]            # nur der Positionspreis, nicht das Brutto
    assert all(row["status"] == "ok" for row in r.calc)                        # nach der Korrektur stimmt alles


def test_quantity_unit_differs_from_price_unit_but_product_matches_is_corrected():
    """Rechnung: 25.880 kg zu 421,10 je t = 10.898,07. KI schrieb menge 25,88 mit Einheit kg."""
    pos = [{"quantity": 25.88, "unit": "kg", "unit_price": 421.10, "total_price": 10898.07}]
    raw = _raw(10898.07, positions=[{"preiseinheit": "t"}])
    r = validate_invoice({"total_amount": 10898.07}, pos, raw)
    assert pos[0]["unit"] == "t"
    assert r.status == STATUS_CORRECTED
    assert r.corrections[0]["field"] == "unit" and r.corrections[0]["from"] == "kg"
    assert "unit_corrected" in [i.code for i in r.issues]


def test_unit_mismatch_is_only_reported_without_auto_correct():
    pos = [{"quantity": 25.88, "unit": "kg", "unit_price": 421.10, "total_price": 10898.07}]
    r = validate_invoice({"total_amount": 10898.07}, pos, _raw(10898.07, positions=[{"preiseinheit": "t"}]),
                         auto_correct=False)
    assert pos[0]["unit"] == "kg" and r.status == STATUS_NEEDS_REVIEW
    assert "unit_mismatch" in [i.code for i in r.issues]


def test_same_units_or_unknown_price_unit_are_untouched():
    pos = [{"quantity": 2, "unit": "t", "unit_price": 10.0, "total_price": 20.0}]
    assert validate_invoice({"total_amount": 23.8}, pos, _raw(20.0, positions=[{"preiseinheit": "t"}])).status == STATUS_OK
    pos = [{"quantity": 2, "unit": "t", "unit_price": 10.0, "total_price": 20.0}]
    assert validate_invoice({"total_amount": 23.8}, pos, _raw(20.0)).status == STATUS_OK


def test_kg_positions_are_normalized_to_tonnes_with_price_per_tonne():
    """Rechnung: 25.880 kg zu 421,10 je t = 10.898,07 → in der Datenbank 25,88 t zu 421,10 €/t."""
    pos = [{"quantity": 25880, "unit": "kg", "unit_price": 421.10, "total_price": 10898.07}]
    r = validate_invoice({"total_amount": 12968.7}, pos, _raw(10898.07, positions=[{"preiseinheit": "t"}]))
    assert (pos[0]["quantity"], pos[0]["unit"], pos[0]["unit_price"]) == (25.88, "t", 421.10)
    assert r.normalizations[0]["from"]["unit"] == "kg" and not r.corrections


def test_price_per_kg_is_not_converted_by_default_but_with_mode_all():
    """Preis je kg (Kleinmengen): nicht in Tonnen umwandeln — außer ausdrücklich gewünscht (mode "all")."""
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 0.40, "total_price": 200.0}]
    validate_invoice({"total_amount": 238.0}, pos, _raw(200.0))
    assert (pos[0]["quantity"], pos[0]["unit"], pos[0]["unit_price"]) == (500, "kg", 0.40)
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 12.0, "total_price": 6.0}]
    validate_invoice({"total_amount": 7.14}, pos, _raw(6.0, positions=[{"preiseinheit": "g"}]))    # Preis je g (falsch erfasst o. Ä.)
    assert pos[0]["unit"] == "kg"
    from app.services import plausibility_service as ps
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 0.40, "total_price": 200.0}]
    res = ps.ValidationResult(status=STATUS_UNCHECKED)
    ps._normalize_mass_to_tonnes(res, pos, [], "t", "all")
    assert (pos[0]["quantity"], pos[0]["unit"], pos[0]["unit_price"]) == (0.5, "t", 400.0)


def test_tonne_spellings_are_unified_to_t():
    for spelling in ("Tonnen", "TO", "to", "Tonne", "To.", "tn"):
        pos = [{"quantity": 2, "unit": spelling, "unit_price": 450.0, "total_price": 900.0}]
        r = validate_invoice({"total_amount": 1071.0}, pos, _raw(900.0))
        assert pos[0]["unit"] == "t" and pos[0]["quantity"] == 2, spelling
        assert r.normalizations and r.normalizations[0]["from"]["unit"] == spelling


def test_normalization_skipped_when_product_does_not_match_and_off_without_auto_correct():
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 0.40, "total_price": 777.0}]
    validate_invoice({"total_amount": 100.0}, pos, _raw(777.0))
    assert pos[0]["unit"] == "kg"
    pos = [{"quantity": 500, "unit": "kg", "unit_price": 0.40, "total_price": 200.0}]
    validate_invoice({"total_amount": 238.0}, pos, _raw(200.0), auto_correct=False)
    assert pos[0]["unit"] == "kg"


def test_tonnes_and_non_mass_units_are_left_alone():
    pos = [{"quantity": 2, "unit": "t", "unit_price": 10.0, "total_price": 20.0},
           {"quantity": 3, "unit": "Stück", "unit_price": 1.0, "total_price": 3.0}]
    r = validate_invoice({"total_amount": 27.37}, pos, _raw(23.0))
    assert r.normalizations == [] and pos[0]["unit"] == "t" and pos[1]["unit"] == "Stück"


def test_position_calcs_show_single_step_calculation():
    pos = [{"product_description": "Zementsack", "quantity": 25880, "unit": "kg", "unit_price": 421.10, "total_price": 10898.07},
           {"product_description": "Rabattartikel", "quantity": 2, "unit": "Stück", "unit_price": 10.0, "total_price": 18.0, "discount": "10%"},
           {"product_description": "Ohne Preis", "quantity": 1, "unit": "Stück", "unit_price": None, "total_price": None}]
    r = validate_invoice({"total_amount": 12968.7}, pos, _raw(10916.07, positions=[{"preiseinheit": "t"}, {}, {}]))
    c = r.to_dict()["position_calcs"]
    assert [x["position"] for x in c] == [0, 1, 2]
    s0 = " | ".join(c[0]["steps"])
    assert "25.880 kg × 421,1 € je t" in s0 and "Faktor 0,001" in s0 and "✓ stimmt" in s0 and "Gespeichert" in s0
    assert any("Nachlass" in t for t in c[1]["steps"]) and c[1]["status"] == "ok"
    assert c[2]["status"] == "skip" and "Einzelpreis" in c[2]["steps"][0]
