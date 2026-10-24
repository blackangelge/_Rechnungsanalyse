"""Belegliste: serverseitige Paginierung, Zähler, ID-Liste, schlanke Abfrage."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import crud
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


@pytest.fixture()
def many(db):
    """120 Belege in 2 Importen; jeder 5. hat Status error, jeder 10. needs_review."""
    batches = []
    for i in range(2):
        b = ImportBatch(
            company_name=f"Firma {i}", year=2025 + i,
            import_folder_path="/import/x", storage_folder_path=f"/storage/Firma_{i}",
        )
        db.add(b)
        batches.append(b)
    db.commit()
    ids = []
    for n in range(120):
        b = batches[n % 2]
        d = crud.document.create(db, b.id, f"r{n}.pdf", 1)
        crud.document.update_status(db, d.id, "error" if n % 5 == 0 else "done")
        crud.document.save_extraction(
            db, d.id, {"supplier_name": f"Lieferant {n % 6}", "total_amount": n},
            [], "X" * 5000,
            validation={"status": "needs_review" if n % 10 == 0 else "ok", "issues": [], "corrections": []},
        )
        ids.append(d.id)
    return ids


def test_pagination_and_total_header(client, many):
    r = client.get("/api/documents", params={"limit": 50, "offset": 100})
    assert r.status_code == 200
    assert r.headers["x-total-count"] == "120"
    assert "x-total-count" in r.headers["access-control-expose-headers"].lower()
    body = r.json()
    assert len(body) == 20
    assert [d["id"] for d in body] == sorted(many, reverse=True)[100:]          # neueste zuerst


def test_pages_are_disjoint_and_complete(client, many):
    seen = []
    for page in range(3):
        seen += [d["id"] for d in client.get("/api/documents", params={"limit": 50, "offset": page * 50}).json()]
    assert seen == sorted(many, reverse=True)
    assert client.get("/api/documents", params={"limit": 50, "offset": 500}).json() == []


def test_without_limit_still_returns_everything_without_header(client, many):
    r = client.get("/api/documents")
    assert len(r.json()) == 120 and "x-total-count" not in r.headers


def test_total_matches_filters(client, many):
    def total(**p):
        r = client.get("/api/documents", params={"limit": 10, **p})
        return int(r.headers["x-total-count"]), len(r.json())
    assert total(status="error") == (24, 10)
    assert total(supplier_name="lieferant 3") == (20, 10)
    assert total(validation_status="needs_review") == (12, 10)
    assert total(company="Firma 1", year=2026) == (60, 10)
    assert total(total_min=100) == (20, 10)
    assert total(status="error", supplier_name="Lieferant 0", validation_status="needs_review") == (4, 4)
    assert total(status="gibtsnicht") == (0, 0)


def test_ids_endpoint_matches_filter(client, many):
    ids = client.get("/api/documents/ids", params={"status": "error"}).json()
    assert len(ids) == 24 and ids == sorted(ids, reverse=True)
    assert len(client.get("/api/documents/ids").json()) == 120
    paged = client.get("/api/documents", params={"status": "error", "limit": 100}).json()
    assert ids == [d["id"] for d in paged]


def test_filter_options(client, many):
    o = client.get("/api/documents/filter-options").json()
    assert o["years"] == [2026, 2025] and o["companies"] == ["Firma 0", "Firma 1"]
    # Lieferantennamen kommen aus der Lieferantentabelle (beim Speichern der Extraktion angelegt)
    assert o["suppliers"] == [f"Lieferant {i}" for i in range(6)]


def test_limit_bounds_are_validated(client, many):
    assert client.get("/api/documents", params={"limit": 0}).status_code == 422
    assert client.get("/api/documents", params={"limit": 50001}).status_code == 422
    assert client.get("/api/documents", params={"limit": 10, "offset": -1}).status_code == 422


def test_list_query_does_not_load_heavy_columns(db, many):
    statements = []

    def capture(conn, cursor, statement, *a, **k):
        statements.append(statement)
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        docs = crud.document.get_all_filtered(db, limit=20)
        _ = [(d.total_amount, d.supplier_name, d.validation_status, d.company, d.year) for d in docs]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    select = " ".join(statements).lower()
    for heavy in ("raw_response", "validation_details", "used_system_prompt", "used_workflow_graph", "workflow_trace"):
        assert heavy not in select, f"{heavy} darf in der Listenabfrage nicht geladen werden"
    # eine Listenabfrage + eine feste Abfrage für das Prüfregel-Ergebnis (nur data._validation) — kein N+1
    assert len(statements) == 2 and "extracted_records" in statements[1].lower()


def test_detail_still_has_heavy_columns(client, many):
    d = client.get(f"/api/documents/{many[0]}").json()
    assert d["extraction"]["raw_response"] == "X" * 5000
    assert d["validation"]["status"] in ("ok", "needs_review")
