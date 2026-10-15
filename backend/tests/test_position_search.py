"""Artikelsuche: Suche, Filter, Sortierung, Paginierung, Kennzahlen."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import crud
from app.crud import position_search
from app.database import get_db
from app.main import app
from app.models.import_batch import ImportBatch


@pytest.fixture()
def client(sqlite_session_factory):
    def _get_db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def P(desc, art, qty, unit, price, total, discount=None):
    return {"product_name": desc, "product_description": None, "article_number": art, "quantity": qty, "unit": unit,
            "unit_price": price, "total_price": total, "discount": discount}


@pytest.fixture()
def world(db):
    class W:
        pass
    w = W()
    w.db, w.batches, w.docs = db, {}, {}
    for name, year in (("A", 2024), ("B", 2025)):
        b = ImportBatch(company_name=f"Firma {name}", year=year, import_folder_path="/x",
                        storage_folder_path=f"/s/{name}", status="done")
        db.add(b)
        db.commit()
        w.batches[name] = b

    def doc(key, batch, supplier, inv_no, inv_date, positions):
        d = crud.document.create(db, w.batches[batch].id, f"{key}.pdf", 1)
        crud.document.update_status(db, d.id, "done")
        crud.document.save_extraction(
            db, d.id, {"supplier_name": supplier, "invoice_number": inv_no, "invoice_date": inv_date},
            positions, "{}",
            validation={"status": "ok", "issues": [], "corrections": []},
        )
        w.docs[key] = d
    doc("d1", "A", "Agro GmbH", "R-001", date(2024, 3, 1), [
        P("Kalkammonsalpeter 27% N", "KAS-27", 500, "kg", 0.40, 200.0),
        P("Harnstoff 46%", "HS-46", 2, "t", 450.0, 900.0, "5%"),
    ])
    doc("d2", "A", "Bau AG", "R-002", date(2024, 6, 15), [
        P("Zement CEM II 25kg Sack", "Z-25", 40, "Stück", 6.5, 260.0),
        P("Kalkstein gemahlen", "KS-1", 1000, "KG", 0.12, 120.0),
        P(None, None, None, None, None, None),                                     # leere Position
    ])
    doc("d3", "B", "Agro GmbH", "R-003", date(2025, 2, 10), [
        P("Kalkammonsalpeter 27% N", "KAS-27", 1000, "kg", 0.45, 450.0),
        P("Rabatt_Aktion 50% Sonder", "RA-50", 1, "Stück", 10.0, 10.0),
    ])
    return w


def get(client, **p):
    r = client.get("/api/positions", params=p)
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["x-total-count"])


def names(items):
    return [i["product_name"] for i in items]


def test_default_order_newest_document_first_and_positions_in_order(client, world):
    items, total = get(client, limit=100)
    assert total == 7
    assert [i["original_filename"] for i in items] == ["d3.pdf"] * 2 + ["d2.pdf"] * 3 + ["d1.pdf"] * 2
    assert names(items)[-2:] == ["Kalkammonsalpeter 27% N", "Harnstoff 46%"]
    first = items[-2]
    assert first["supplier_name"] == "Agro GmbH" and first["invoice_number"] == "R-001"
    assert first["invoice_date"] == "2024-03-01" and first["company"] == "Firma A" and first["year"] == 2024
    assert first["article_number"] == "KAS-27" and float(first["unit_price"]) == 0.4 and first["validation_status"] == "ok"


def test_pagination_and_total_header(client, world):
    page1, total = get(client, limit=3, offset=0)
    page2, _ = get(client, limit=3, offset=3)
    page3, _ = get(client, limit=3, offset=6)
    assert total == 7 and [len(page1), len(page2), len(page3)] == [3, 3, 1]
    ids = [i["id"] for i in page1 + page2 + page3]
    assert len(set(ids)) == 7
    assert client.get("/api/positions", params={"limit": 0}).status_code == 422
    assert client.get("/api/positions", params={"limit": 501}).status_code == 422


def test_free_text_all_words_must_match_description_or_article_number(client, world):
    items, total = get(client, q="kalkammon")
    assert total == 2 and set(names(items)) == {"Kalkammonsalpeter 27% N"}
    assert get(client, q="KAS-27")[1] == 2                                      # Artikelnummer
    assert get(client, q="kalk 27")[1] == 2                                     # beide Wörter (UND)
    assert get(client, q="kalk zement")[1] == 0
    assert get(client, q="  ")[1] == 7                                          # leere Suche = alles
    assert get(client, q="HARNSTOFF")[1] == 1                                   # Groß-/Kleinschreibung egal


def test_special_characters_are_literal_not_wildcards(client, world):
    assert get(client, q="50%")[1] == 1                                         # nur "Rabatt_Aktion 50% Sonder"
    assert get(client, q="%")[1] == 4                                           # nur Bezeichnungen mit literalem % (27%, 27%, 46%, 50%)
    assert get(client, q="n_a")[1] == 0                                         # "_" ist kein Platzhalter
    assert get(client, q="Rabatt_Aktion")[1] == 1


def test_filters_supplier_company_year_invoice_unit_article(client, world):
    assert get(client, supplier_name="agro")[1] == 4
    assert get(client, supplier_name="Bau AG")[1] == 3
    assert get(client, company="firma b")[1] == 2
    assert get(client, year=2024)[1] == 5
    assert get(client, invoice_number="R-00")[1] == 7 and get(client, invoice_number="R-002")[1] == 3
    assert get(client, unit="KG")[1] == 3 and get(client, unit="kg")[1] == 3        # Groß-/Kleinschreibung egal, exakt
    assert get(client, unit="k")[1] == 0                                            # kein Teilstring
    assert get(client, article_number="kas")[1] == 2
    assert get(client, document_id=world.docs["d2"].id)[1] == 3
    assert get(client, batch_ids=[world.batches["B"].id])[1] == 2
    assert get(client, validation_status="ok")[1] == 7 and get(client, validation_status="needs_review")[1] == 0


def test_numeric_and_date_ranges(client, world):
    assert get(client, unit_price_min=1)[1] == 3                                    # 450 / 6,5 / 10
    assert names(get(client, unit_price_min=100)[0]) == ["Harnstoff 46%"]
    assert get(client, unit_price_max=0.45)[1] == 3
    assert get(client, total_price_min=200, total_price_max=450)[1] == 3            # 200, 260, 450
    assert get(client, quantity_min=1000)[1] == 2
    assert get(client, quantity_max=2)[1] == 2
    assert get(client, date_from="2024-06-01")[1] == 5
    assert get(client, date_to="2024-03-31")[1] == 2
    assert get(client, date_from="2024-06-01", date_to="2024-12-31")[1] == 3
    assert get(client, q="kalkammon", unit_price_max=0.41, year=2024)[1] == 1         # kombiniert (UND)
    assert client.get("/api/positions", params={"date_from": "kein-datum"}).status_code == 422


def test_sorting_and_stable_paging_with_nulls_last(client, world):
    asc, _ = get(client, sort="unit_price", order="asc", limit=100)
    prices = [float(i["unit_price"]) if i["unit_price"] is not None else None for i in asc]
    assert prices == [0.12, 0.4, 0.45, 6.5, 10.0, 450.0, None]                        # leere Werte immer am Ende
    desc, _ = get(client, sort="unit_price", order="desc", limit=100)
    assert [float(i["unit_price"]) if i["unit_price"] is not None else None for i in desc] == [450.0, 10.0, 6.5, 0.45, 0.4, 0.12, None]
    by_name, _ = get(client, sort="product_name", order="asc", limit=100)
    assert names(by_name)[:2] == ["Harnstoff 46%", "Kalkammonsalpeter 27% N"] and names(by_name)[-1] is None
    # Blättern über Seitengrenzen: gleiche Reihenfolge wie in einem Stück, keine Doppelten
    paged = []
    for off in range(0, 7, 2):
        paged += [i["id"] for i in get(client, sort="supplier_name", order="asc", limit=2, offset=off)[0]]
    assert paged == [i["id"] for i in get(client, sort="supplier_name", order="asc", limit=100)[0]] and len(set(paged)) == 7
    assert client.get("/api/positions", params={"sort": "raw_response"}).status_code == 422


def test_deleted_documents(client, world):
    crud.document.soft_delete(world.db, world.docs["d3"].id)
    assert get(client)[1] == 5 and get(client, include_deleted=True)[1] == 7


def test_summary(client, world):
    s = client.get("/api/positions/summary", params={"q": "kalkammon"}).json()
    assert s["count"] == 2 and s["documents"] == 2
    assert s["total_price_sum"] == 650.0
    assert s["unit_price_min"] == 0.4 and s["unit_price_max"] == 0.45 and round(s["unit_price_avg"], 3) == 0.425
    assert s["by_unit"] == [{"unit": "kg", "count": 2, "quantity": 1500.0, "total_price": 650.0}]
    everything = client.get("/api/positions/summary").json()
    assert everything["count"] == 7 and everything["documents"] == 3
    assert everything["total_price_sum"] == 200 + 900 + 260 + 120 + 450 + 10
    assert [u["unit"] for u in everything["by_unit"]][:1] == ["kg"]                    # kg + KG + t zusammengefasst
    assert everything["by_unit"][0]["count"] == 4 and everything["by_unit"][0]["quantity"] == 500 + 1000 + 1000 + 2000
    empty = client.get("/api/positions/summary", params={"q": "gibtsnicht"}).json()
    assert empty["count"] == 0 and empty["total_price_sum"] is None and empty["by_unit"] == []


def test_filter_options_units(client, world):
    units = client.get("/api/positions/filter-options").json()["units"]
    assert units[0] == {"unit": "kg", "count": 3}
    assert {u["unit"] for u in units} == {"kg", "t", "stück"}


def test_hidden_imports_excluded_unless_requested(client, world):
    client.post("/api/imports/visibility", json={"ids": [world.batches["A"].id], "hidden": True})
    assert get(client)[1] == 2                                                        # nur Import B
    assert get(client, include_hidden=True)[1] == 7
    assert get(client, batch_ids=[world.batches["A"].id])[1] == 5                    # ausdrücklich gewählt
    assert get(client, document_id=world.docs["d1"].id)[1] == 2                       # per Beleg-ID
    assert client.get("/api/positions/summary").json()["count"] == 2


def test_exact_supplier_filter_does_not_match_substrings(client, world):
    assert get(client, supplier_name="Agro")[1] == 4                              # Teilstring (Freitext)
    assert get(client, supplier_name="Agro", supplier_exact=True)[1] == 0         # exakt: nur voller Name
    assert get(client, supplier_name="Agro GmbH", supplier_exact=True)[1] == 4


def test_total_count_is_capped_for_broad_searches(client, world, monkeypatch):
    monkeypatch.setattr(position_search, "COUNT_CAP", 3)
    r = client.get("/api/positions", params={"limit": 2})
    assert r.headers["x-total-count"] == "3" and r.headers["x-total-capped"] == "true"
    assert len(r.json()) == 2
    r = client.get("/api/positions", params={"q": "harnstoff"})
    assert r.headers["x-total-count"] == "1" and r.headers["x-total-capped"] == "false"


def test_too_slow_query_gives_a_clear_504(client, world, monkeypatch):
    from app.services.query_guard import QueryTooSlow

    def boom(*a, **k):
        raise QueryTooSlow("x")
    monkeypatch.setattr(position_search, "count", boom)
    r = client.get("/api/positions")
    assert r.status_code == 504 and "mehr Filter" in r.json()["detail"]


def test_summary_and_options_are_cached_but_cleared_when_imports_change(client, world):
    first = client.get("/api/positions/summary").json()["count"]
    world.db.query(world.docs["d1"].__class__).filter_by(id=world.docs["d1"].id).update({"soft_deleted": True})
    world.db.commit()
    assert client.get("/api/positions/summary").json()["count"] == first          # noch aus dem Zwischenspeicher
    client.post("/api/imports/visibility", json={"ids": [world.batches["A"].id], "hidden": True})
    assert client.get("/api/positions/summary").json()["count"] < first           # Cache wurde geleert


def test_units_are_converted_to_base_unit_in_list_and_filter(client, world):
    items, _ = get(client, unit="t", limit=100)
    row = items[0]
    assert row["unit"] == "t" and row["base_unit"] == "kg" and float(row["base_quantity"]) == 2000.0
    assert round(float(row["base_unit_price"]), 4) == 0.45        # 900 € ÷ 2000 kg (Positionspreis ÷ Basismenge)
    piece = next(i for i in get(client, limit=100)[0] if i["unit"] == "Stück")
    assert piece["base_unit"] is None and piece["base_quantity"] is None
    mass = next(g for g in client.get("/api/positions/unit-table").json() if g["group"] == "mass")
    assert mass["base_unit"] == "kg" and {"unit": "t", "factor": 1000.0} in mass["units"]
    assert get(client, unit_group="mass")[1] == 4                  # kg, KG, kg, t
    assert get(client, unit_group="volume")[1] == 0
    assert client.get("/api/positions", params={"unit_group": "bogus"}).status_code == 422
