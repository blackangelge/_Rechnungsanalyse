"""Benutzeranmeldung: Hashing, Sitzungen, Schutz der Endpunkte, Start-Benutzer aus der .env."""

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.user import User, UserSession
from app.services import auth_service


@pytest.fixture()
def client(sqlite_session_factory, monkeypatch):
    def _get_db():
        s = sqlite_session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(settings, "auth_enabled", True)
    auth_service._fails.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(db, name="anna", pw="geheim123"):
    u = User(username=name, password_hash=auth_service.hash_password(pw))
    db.add(u)
    db.commit()
    return u


# ── Hashing ──────────────────────────────────────────────────────────────────

def test_password_hash_roundtrip_and_salt():
    h1, h2 = auth_service.hash_password("geheim123"), auth_service.hash_password("geheim123")
    assert h1 != h2 and "geheim123" not in h1          # Salz, kein Klartext
    assert auth_service.verify_password("geheim123", h1)
    assert not auth_service.verify_password("falsch", h1)
    assert not auth_service.verify_password("x", "kaputt")


# ── Schutz der Endpunkte ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/documents", "/api/imports", "/api/vendors", "/api/positions", "/api/workflows",
    "/api/settings/export", "/api/logs/ki-stats", "/api/ai-clients", "/api/tasks", "/api/tasks/workers", "/api/auth/me",
])
def test_data_endpoints_require_login(client, path):
    assert client.get(path).status_code == 401


def test_task_actions_require_login(client):
    assert client.post("/api/tasks/pause").status_code == 401


def test_health_and_login_are_open(client):
    assert client.get("/health").status_code == 200
    r = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert r.status_code == 401        # nicht 403/404 → Endpunkt erreichbar, nur Daten falsch


def test_login_flow_cookie_and_logout(client, db):
    _user(db)
    r = client.post("/api/auth/login", json={"username": "Anna", "password": "geheim123"})  # Groß/Klein egal
    assert r.status_code == 200 and r.json()["username"] == "anna"
    set_cookie = r.headers["set-cookie"].lower()
    assert SESSION_COOKIE in set_cookie and "httponly" in set_cookie and "samesite=lax" in set_cookie

    assert client.get("/api/auth/me").json()["username"] == "anna"
    assert client.get("/api/documents").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/documents").status_code == 401
    assert db.query(UserSession).count() == 0


def test_wrong_password_and_inactive_user(client, db):
    u = _user(db)
    assert client.post("/api/auth/login", json={"username": "anna", "password": "nope"}).status_code == 401
    u.is_active = False
    db.commit()
    assert client.post("/api/auth/login", json={"username": "anna", "password": "geheim123"}).status_code == 401


def test_deactivating_user_ends_access_immediately(client, db):
    u = _user(db)
    client.post("/api/auth/login", json={"username": "anna", "password": "geheim123"})
    u.is_active = False
    db.commit()
    assert client.get("/api/documents").status_code == 401


def test_expired_session_is_rejected(client, db):
    _user(db)
    client.post("/api/auth/login", json={"username": "anna", "password": "geheim123"})
    from datetime import datetime, timedelta, timezone
    db.query(UserSession).update({"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)})
    db.commit()
    assert client.get("/api/documents").status_code == 401


def test_bruteforce_lock(client, db):
    _user(db)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "anna", "password": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "anna", "password": "geheim123"})
    assert r.status_code == 429          # gesperrt, auch mit dem richtigen Passwort


def test_change_password(client, db):
    _user(db)
    client.post("/api/auth/login", json={"username": "anna", "password": "geheim123"})
    other = TestClient(app)              # zweites Gerät
    other.post("/api/auth/login", json={"username": "anna", "password": "geheim123"})

    bad = client.post("/api/auth/change-password", json={"current_password": "falsch", "new_password": "neuesPasswort1"})
    assert bad.status_code == 400
    short = client.post("/api/auth/change-password", json={"current_password": "geheim123", "new_password": "kurz"})
    assert short.status_code == 422
    ok = client.post("/api/auth/change-password", json={"current_password": "geheim123", "new_password": "neuesPasswort1"})
    assert ok.status_code == 204

    assert client.get("/api/documents").status_code == 200       # aktuelle Sitzung bleibt
    assert other.get("/api/documents").status_code == 401        # andere Geräte abgemeldet
    assert TestClient(app).post("/api/auth/login", json={"username": "anna", "password": "neuesPasswort1"}).status_code == 200


def test_auth_disabled_lets_everything_through(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    assert client.get("/api/documents").status_code == 200
    assert client.get("/api/auth/me").json() == {"auth_enabled": False, "username": None}


# ── Start-Benutzer aus der .env ──────────────────────────────────────────────

def test_seed_creates_users_from_env(db, monkeypatch):
    monkeypatch.setattr(settings, "initial_admin_user", "Admin")
    monkeypatch.setattr(settings, "initial_admin_password", "admin-pass-1")
    monkeypatch.setattr(settings, "initial_users", "anna:pw-anna-123 ; bert:pa:ss;word ;ohnedoppelpunkt")
    res = auth_service.seed_initial_users(db)
    names = {u.username for u in db.query(User)}
    assert names == {"admin", "anna", "bert"}        # kaputter Eintrag übersprungen
    assert res["created"] == 3
    bert = db.query(User).filter_by(username="bert").one()
    assert auth_service.verify_password("pa:ss", bert.password_hash)    # Passwort darf ':' enthalten


def test_seed_keeps_existing_passwords_unless_forced(db, monkeypatch):
    monkeypatch.setattr(settings, "initial_admin_user", "admin")
    monkeypatch.setattr(settings, "initial_admin_password", "erstes-passwort")
    auth_service.seed_initial_users(db)
    admin = db.query(User).filter_by(username="admin").one()
    admin.password_hash = auth_service.hash_password("in-oberflaeche-geaendert")
    db.commit()

    auth_service.seed_initial_users(db)              # Neustart: darf NICHT überschreiben
    db.refresh(admin)
    assert auth_service.verify_password("in-oberflaeche-geaendert", admin.password_hash)

    token = auth_service.create_session(db, admin)
    monkeypatch.setattr(settings, "force_password_reset", True)
    assert auth_service.seed_initial_users(db)["reset"] == 1
    db.refresh(admin)
    assert auth_service.verify_password("erstes-passwort", admin.password_hash)
    assert auth_service.user_for_token(db, token) is None    # Sitzungen beendet


def test_half_configured_admin_is_ignored(db, monkeypatch):
    monkeypatch.setattr(settings, "initial_admin_user", "admin")
    monkeypatch.setattr(settings, "initial_admin_password", None)
    assert auth_service.seed_initial_users(db)["created"] == 0
