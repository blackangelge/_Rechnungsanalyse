"""API des Schema-Editors: Entwurf anlegen, Felder bearbeiten, Standardfelder gesperrt, Aktivieren."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(sqlite_session_factory):
    from app.database import get_db

    def override():
        db = sqlite_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_draft_edit_activate(client):
    v = client.post("/api/schema/versions/draft", json={}).json()
    vid = v["id"]
    assert client.post("/api/schema/versions/draft", json={}).status_code == 409
    v = client.post(f"/api/schema/versions/{vid}/entities", json={"name": "beleg", "display_name": "Beleg"}).json()
    ent = v["entities"][0]
    assert {f["name"] for f in ent["fields"]} == {"document_id", "vendor_id"}
    locked = next(f for f in ent["fields"] if f["name"] == "document_id")
    assert client.patch(f"/api/schema/fields/{locked['id']}", json={"display_name": "x"}).status_code == 400
    assert client.delete(f"/api/schema/fields/{locked['id']}").status_code == 400
    r = client.post(f"/api/schema/entities/{ent['id']}/fields", json={"name": "Bad Name", "display_name": "x", "data_type": "text"})
    assert r.status_code == 400
    v = client.post(f"/api/schema/entities/{ent['id']}/fields", json={"name": "rechnungsnummer", "display_name": "Rechnungsnr.", "data_type": "text",
                                                                      "normalizers": [{"name": "trim", "params": {}}]}).json()
    assert any(f["name"] == "rechnungsnummer" for f in v["entities"][0]["fields"])
    assert client.get(f"/api/schema/versions/{vid}/validate").status_code == 200
    assert client.post(f"/api/schema/versions/{vid}/activate").status_code == 200
    assert client.post(f"/api/schema/entities/{ent['id']}/fields", json={"name": "x", "display_name": "x", "data_type": "text"}).status_code == 400
    draft = client.post("/api/schema/versions/draft", json={}).json()
    assert draft["status"] == "draft" and draft["version_number"] == 2
    assert client.delete(f"/api/schema/versions/{draft['id']}").status_code == 204
