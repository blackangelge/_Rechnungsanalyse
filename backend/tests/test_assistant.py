"""KI-Assistent mit Chats und Werkzeugen (KI-Antworten werden vorgegeben)."""

import json

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.ai_clients import AIClients
from app.services import db_assistant_service
from app.services import assistant_tools as at


@pytest.fixture()
def client(sqlite_session_factory):
    def override():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def ai(db):
    db.add(AIClients(name="KI", model_name="m", ip_address="127.0.0.1", port="1234", active=True))
    db.commit()


def _scripted(monkeypatch, replies):
    it = iter(replies)
    seen = []

    def fake(config, messages):
        seen.append(list(messages))
        return json.dumps(next(it)) if not isinstance(replies[0], str) else next(it), {"input_tokens": 10, "output_tokens": 5, "seconds": 0.1}
    monkeypatch.setattr(db_assistant_service, "_chat", fake)
    return seen


def _new_chat(client):
    return client.post("/api/assistant/chats", json={}).json()["id"]


def test_chat_lifecycle_tokens_restart_delete(client, ai, monkeypatch):
    _scripted(monkeypatch, [{"tool": "final", "answer": "Hallo!"}, {"tool": "final", "answer": "Nochmal"}])
    cid = _new_chat(client)
    r = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "Wie geht es dir heute?"})
    assert r.status_code == 200
    assert r.json()["message"]["content"] == "Hallo!"
    assert r.json()["chat"]["title"].startswith("Wie geht es")
    assert r.json()["chat"]["input_tokens"] == 10 and r.json()["chat"]["output_tokens"] == 5
    client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "und?"})
    chat = client.get(f"/api/assistant/chats/{cid}").json()
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "user", "assistant"]
    assert chat["input_tokens"] == 20
    assert client.get("/api/assistant/chats").json()[0]["id"] == cid
    # Neustart: Nachrichten und Token weg, Chat bleibt
    again = client.post(f"/api/assistant/chats/{cid}/restart").json()
    assert again["messages"] == [] and again["input_tokens"] == 0
    assert client.patch(f"/api/assistant/chats/{cid}", json={"title": "Mein Chat"}).json()["title"] == "Mein Chat"
    assert client.delete(f"/api/assistant/chats/{cid}").status_code == 204
    assert client.get(f"/api/assistant/chats/{cid}").status_code == 404


def test_images_are_sent_as_content_parts_and_validated(client, ai, monkeypatch):
    seen = _scripted(monkeypatch, [{"tool": "final", "answer": "Ich sehe ein Bild."}])
    cid = _new_chat(client)
    png = "data:image/png;base64,iVBORw0KGgo="
    assert client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "x", "images": ["http://evil/x.png"]}).status_code == 400
    assert client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "", "images": []}).status_code == 400
    r = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "Was ist das?", "images": [png]})
    assert r.status_code == 200
    last_user = seen[0][-1]
    assert isinstance(last_user["content"], list)
    assert {"type": "image_url", "image_url": {"url": png}} in last_user["content"]
    stored = client.get(f"/api/assistant/chats/{cid}").json()["messages"][0]
    assert stored["images"] == [png]


def test_read_tool_runs_and_write_tool_is_only_a_proposal(client, ai, db, monkeypatch):
    seen = _scripted(monkeypatch, [
        {"tool": "list_units", "args": {}},
        {"tool": "create_unit_group", "args": {"name": "Zeit"}, "reason": "Neue Gruppe"},
        {"tool": "final", "answer": "Vorschlag liegt bereit."},
    ])
    cid = _new_chat(client)
    r = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "Lege die Gruppe Zeit an"}).json()["message"]
    assert r["steps"][0]["tool"] == "list_units" and r["steps"][0]["ok"]
    assert len(r["proposals"]) == 1 and r["proposals"][0]["status"] == "pending"
    assert client.get("/api/units").json() == []               # noch nichts angelegt
    assert "Ergebnis von list_units" in seen[1][-1]["content"]
    # Freigeben
    pid = r["proposals"][0]["id"]
    done = client.post(f"/api/assistant/chats/{cid}/messages/{r['id']}/proposals/{pid}/apply").json()
    assert done["proposals"][0]["status"] == "applied"
    assert [g["name"] for g in client.get("/api/units").json()] == ["Zeit"]
    # zweites Freigeben ist nicht möglich
    assert client.post(f"/api/assistant/chats/{cid}/messages/{r['id']}/proposals/{pid}/apply").status_code == 409


def test_dismiss_and_failed_proposal(client, ai, monkeypatch):
    _scripted(monkeypatch, [
        {"tool": "rename_unit_group", "args": {"group": "Gibtesnicht", "name": "X"}},
        {"tool": "create_unit_group", "args": {"name": "A"}},
        {"tool": "final", "answer": "ok"},
    ])
    cid = _new_chat(client)
    m = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "los"}).json()["message"]
    bad, good = m["proposals"]
    r = client.post(f"/api/assistant/chats/{cid}/messages/{m['id']}/proposals/{bad['id']}/apply")
    assert r.status_code == 400 and "Gibtesnicht" in r.json()["detail"]
    msgs = client.get(f"/api/assistant/chats/{cid}").json()["messages"]
    assert msgs[-1]["proposals"][0]["status"] == "failed"
    out = client.post(f"/api/assistant/chats/{cid}/messages/{m['id']}/proposals/{good['id']}/dismiss").json()
    assert out["proposals"][1]["status"] == "dismissed"
    assert client.get("/api/units").json() == []


def test_proposal_status_is_visible_to_the_model_next_turn(client, ai, monkeypatch):
    seen = _scripted(monkeypatch, [
        {"tool": "create_unit_group", "args": {"name": "Zeit"}}, {"tool": "final", "answer": "bereit"}, {"tool": "final", "answer": "weiter"},
    ])
    cid = _new_chat(client)
    m = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "a"}).json()["message"]
    client.post(f"/api/assistant/chats/{cid}/messages/{m['id']}/proposals/{m['proposals'][0]['id']}/apply")
    client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "weiter"})
    history = " ".join(str(x["content"]) for x in seen[-1])
    assert "FREIGEGEBEN" in history and "Zeit" in history


def test_unit_tools_via_names(db):
    import asyncio
    from app.services.units_seed import seed_default_units
    seed_default_units(db)
    db.commit()
    asyncio.run(at.execute_write(db, "add_unit_alias", {"unit": "kg", "alias": "Kilogr."}))
    asyncio.run(at.execute_write(db, "rename_unit_group", {"group": "Masse", "name": "Gewicht"}))
    asyncio.run(at.execute_write(db, "create_unit", {"group": "Gewicht", "name": "Zentner", "symbol": "Ztr", "factor": 20}))
    groups = {g["name"]: g for g in at.run_read(db, "list_units", {})}
    assert "Gewicht" in groups and "Masse" not in groups
    zentner = next(u for u in groups["Gewicht"]["units"] if u["symbol"] == "Ztr")
    assert float(zentner["factor"]) == 20
    with pytest.raises(at.ToolError):
        asyncio.run(at.execute_write(db, "create_unit", {"group": "Gewicht", "name": "Dup", "symbol": "Ztr2", "factor": 2, "is_base": True}))


def test_schema_tools_draft_field_activate(db):
    import asyncio
    from app.services.schema_seed import seed_initial_schema
    from app.services.units_seed import seed_default_units
    seed_default_units(db)
    seed_initial_schema(db)
    db.commit()
    run = lambda tool, args: asyncio.run(at.execute_write(db, tool, args))     # noqa: E731
    with pytest.raises(at.ToolError):                                           # ohne Entwurf nicht möglich
        run("add_schema_field", {"entity": "position", "name": "lagerort", "display_name": "Lagerort", "data_type": "text"})
    run("create_schema_draft", {"comment": "Lagerort"})
    run("add_schema_field", {"entity": "position", "name": "lagerort", "display_name": "Lagerort", "data_type": "text"})
    run("add_schema_field", {"entity": "position", "name": "gewicht", "display_name": "Gewicht", "data_type": "quantity", "unit_group": "Masse"})
    draft = at.run_read(db, "get_schema", {"version": "draft"})
    names = [f["name"] for e in draft["entities"] if e["name"] == "position" for f in e["fields"]]
    assert "lagerort" in names and "gewicht" in names
    run("activate_schema_draft", {})
    assert at.run_read(db, "get_schema", {})["status"] == "active"


def test_workflow_tools_build_validate_and_reject(db):
    import asyncio
    run = lambda tool, args: asyncio.run(at.execute_write(db, tool, args))     # noqa: E731
    types = {t["type"] for t in at.run_read(db, "workflow_node_types", {})}
    assert {"start", "extract", "branch", "end"} <= types
    wf = run("create_workflow", {
        "name": "Test", "nodes": [{"id": "start", "type": "start"}, {"id": "ex", "type": "extract"}, {"id": "end", "type": "end"}],
        "edges": [{"source": "start", "target": "ex"}, {"source": "ex", "target": "end"}],
    })
    got = at.run_read(db, "get_workflow", {"id": wf["id"]})
    assert [n["type"] for n in got["nodes"]] == ["start", "extract", "end"]
    # zwei Kanten aus einem Ausgang → Regel des Editors greift
    with pytest.raises(at.ToolError):
        run("update_workflow", {"id": wf["id"], "nodes": [{"id": "start", "type": "start"}, {"id": "a", "type": "end"}, {"id": "b", "type": "end"}],
                                "edges": [{"source": "start", "target": "a"}, {"source": "start", "target": "b"}]})
    # aus Vorlage
    key = at.run_read(db, "workflow_templates", {})[0]["key"]
    assert run("create_workflow", {"name": "Aus Vorlage", "template": key})["id"]


def test_export_template_tool(db):
    import asyncio
    from app.services.schema_seed import seed_initial_schema
    seed_initial_schema(db)
    db.commit()
    run = lambda tool, args: asyncio.run(at.execute_write(db, tool, args))     # noqa: E731
    t = run("create_export_template", {"name": "Kurz", "layout": "sheet_per_entity",
                                       "config": {"entities": [{"entity": "beleg", "fields": ["rechnungsnummer"]}]}})
    assert any(x["name"] == "Kurz" for x in at.run_read(db, "list_export_templates", {}))
    cols = at.run_read(db, "export_template_columns", {"id": t["id"]})
    assert cols and cols[0]["entity"] == "beleg"
    with pytest.raises(at.ToolError):
        run("create_export_template", {"name": "Kaputt", "config": {"entities": [{"entity": "gibtsnicht"}]}})


def test_create_import_requires_workflow_and_folder(client, db, tmp_path, monkeypatch):
    import asyncio
    from app.config import settings
    monkeypatch.setattr(settings, "import_base_path", str(tmp_path))
    (tmp_path / "Muster_2025").mkdir()
    (tmp_path / "Muster_2025" / "a.pdf").write_bytes(b"%PDF-1.4")
    folders = at.run_read(db, "list_import_folders", {})
    assert {"subfolder": "Muster_2025", "pdf_files": 1} in folders["folders"]
    with pytest.raises(at.ToolError):
        asyncio.run(at.execute_write(db, "create_import", {"company_name": "Muster", "year": 2025, "subfolder": "Muster_2025", "analyze_after_import": True}))
    with pytest.raises(at.ToolError):
        asyncio.run(at.execute_write(db, "create_import", {"company_name": "Muster", "year": 2025, "subfolder": "Gibtsnicht"}))


def test_every_tool_has_summary_or_is_read():
    for t in at.TOOLS.values():
        assert t.kind in ("read", "write")
        if t.kind == "write":
            assert t.summary is not None, t.name
    assert "create_unit" in at.tools_prompt()


def test_own_company_tools_read_create_update_delete(client, ai, db, monkeypatch):
    _scripted(monkeypatch, [
        {"tool": "list_own_companies", "args": {}},
        {"tool": "create_own_company", "args": {"name": "Alpha GmbH", "ibans": ["DE02 1203 0000 0000 2020 51", "DE89 3704 0044 0532 0130 00"]}},
        {"tool": "final", "answer": "Vorschlag liegt bereit."},
    ])
    cid = _new_chat(client)
    m = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "Lege Alpha GmbH an"}).json()["message"]
    assert m["steps"][0]["tool"] == "list_own_companies" and len(m["proposals"]) == 1
    assert "Alpha GmbH" in m["proposals"][0]["summary"]
    assert client.get("/api/own-companies").json() == []                       # erst nach Freigabe
    client.post(f"/api/assistant/chats/{cid}/messages/{m['id']}/proposals/{m['proposals'][0]['id']}/apply")
    created = client.get("/api/own-companies").json()
    assert len(created) == 1 and created[0]["name"] == "Alpha GmbH" and len(created[0]["ibans"]) == 2

    _scripted(monkeypatch, [
        {"tool": "update_own_company", "args": {"company": "alpha gmbh", "vat_ids": ["DE111222333"], "aliases": ["Alpha"]}},
        {"tool": "delete_own_company", "args": {"company": created[0]["id"]}},
        {"tool": "final", "answer": "ok"},
    ])
    m2 = client.post(f"/api/assistant/chats/{cid}/messages", json={"content": "ändern, dann löschen"}).json()["message"]
    upd, dele = m2["proposals"]
    client.post(f"/api/assistant/chats/{cid}/messages/{m2['id']}/proposals/{upd['id']}/apply")
    c = client.get("/api/own-companies").json()[0]
    assert c["vat_ids"] == ["DE111222333"] and c["aliases"] == ["Alpha"] and len(c["ibans"]) == 2     # nicht genannte Felder bleiben
    client.post(f"/api/assistant/chats/{cid}/messages/{m2['id']}/proposals/{dele['id']}/apply")
    assert client.get("/api/own-companies").json() == []
