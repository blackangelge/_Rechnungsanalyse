"""Nachrüstung „Originalwerte wiederherstellen“: früher auf Tonnen umgerechnete Positionen zurück auf den Wortlaut der Rechnung."""

import json
from decimal import Decimal

from app import crud
from app.models.import_batch import ImportBatch
from app.models.invoice_extraction import InvoiceExtraction
from app.models.order_position import OrderPosition
from app.services import validation_backfill as vb


def _doc(db, positions, raw_positions):
    b = ImportBatch(company_name="F", year=2025, import_folder_path="/x", storage_folder_path="/s", status="done")
    db.add(b)
    db.commit()
    d = crud.document.create(db, b.id, "a.pdf", 1)
    crud.document.save_extraction(db, d.id, {"supplier_name": "L"}, positions, json.dumps({"positionen": raw_positions}))
    ext = db.query(InvoiceExtraction).filter_by(document_id=d.id).one()
    return ext, db.query(OrderPosition).filter_by(document_id=d.id).order_by(OrderPosition.position_index).all()


def test_tonne_conversion_is_reverted_to_invoice_wording(db):
    ext, pos = _doc(
        db,
        [{"product_description": "KAS", "quantity": 25.5, "unit": "t", "unit_price": 412, "total_price": 10506}],
        [{"menge": 25500, "mengeneinheit": "kg", "einzelpreis": 412, "gesamtpreis": 10506}],
    )
    assert vb.restore_original_units(ext, pos) == 1
    assert (float(pos[0].quantity), pos[0].unit, float(pos[0].unit_price_netto)) == (25500.0, "kg", 412.0)
    assert float(pos[0].total_price) == 10506.0                                          # Gesamtpreis unberührt
    assert vb.restore_original_units(ext, pos) == 0                                      # idempotent


def test_spelling_only_change_is_restored_but_real_corrections_stay(db):
    ext, pos = _doc(
        db,
        [{"product_description": "A", "quantity": 2, "unit": "t", "unit_price": 450, "total_price": 900},
         {"product_description": "B", "quantity": 25.88, "unit": "t", "unit_price": 421.1, "total_price": 10898.07}],
        [{"menge": 2, "mengeneinheit": "Tonnen", "einzelpreis": 450},
         {"menge": 25.88, "mengeneinheit": "kg", "einzelpreis": 421.1}],     # Einheit war falsch beschriftet → echte Korrektur
    )
    assert vb.restore_original_units(ext, pos) == 1
    assert pos[0].unit == "Tonnen"
    assert pos[1].unit == "t" and float(pos[1].quantity) == 25.88                        # nicht zurückgesetzt


def test_non_weight_and_missing_raw_are_left_alone(db):
    ext, pos = _doc(
        db,
        [{"product_description": "A", "quantity": 3, "unit": "Stück", "unit_price": 1, "total_price": 3},
         {"product_description": "B", "quantity": 1, "unit": "t", "unit_price": 1, "total_price": 1}],
        [{"menge": 3, "mengeneinheit": "Stück"}],
    )
    assert vb.restore_original_units(ext, pos) == 0
