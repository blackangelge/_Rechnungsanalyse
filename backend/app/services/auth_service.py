"""
Benutzeranmeldung: Passwort-Hashing, Sitzungen, Start-Benutzer aus der .env.

Bewusst nur Standardbibliothek (hashlib.scrypt, secrets) — keine neuen Pakete, damit ein
Update auf dem NAS keinen Paket-Download braucht.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User, UserSession

logger = logging.getLogger(__name__)

_N, _R, _P = 2**14, 8, 1          # scrypt-Kosten (≈ 16 MB, ~50 ms)
MIN_PASSWORD_LENGTH = 8


def normalize_username(name: str | None) -> str:
    return (name or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# Für Benutzer, die es nicht gibt: trotzdem rechnen, damit die Antwortzeit nichts verrät
_DUMMY_HASH = hash_password("dummy-password-for-timing")


# ── Sitzungen ────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(db: Session, user: User) -> str:
    """Legt eine Sitzung an und gibt das Token (für das Cookie) zurück."""
    token = secrets.token_urlsafe(32)
    db.add(UserSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=_now() + timedelta(days=settings.session_days),
    ))
    user.last_login_at = _now()
    db.query(UserSession).filter(UserSession.expires_at < _now()).delete()  # Aufräumen
    db.commit()
    return token


def user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.query(UserSession).filter(UserSession.token_hash == _hash_token(token)).first()
    if sess is None:
        return None
    expires = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=timezone.utc)
    if expires < _now():
        return None
    user = db.get(User, sess.user_id)
    return user if user is not None and user.is_active else None


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.query(UserSession).filter(UserSession.token_hash == _hash_token(token)).delete()
        db.commit()


def end_all_sessions(db: Session, user_id: int, except_token: str | None = None) -> None:
    q = db.query(UserSession).filter(UserSession.user_id == user_id)
    if except_token:
        q = q.filter(UserSession.token_hash != _hash_token(except_token))
    q.delete()
    db.commit()


# ── Anmelden (mit einfacher Bremse gegen Passwort-Raten) ─────────────────────

_MAX_FAILS, _LOCK_SECONDS = 5, 60
_fails: dict[str, tuple[int, float]] = {}   # username → (Fehlversuche, gesperrt bis)


def seconds_locked(username: str) -> int:
    count, until = _fails.get(username, (0, 0.0))
    return max(0, int(until - time.monotonic())) if count >= _MAX_FAILS else 0


def _register_fail(username: str) -> None:
    count, until = _fails.get(username, (0, 0.0))
    if count >= _MAX_FAILS and until < time.monotonic():
        count = 0                                  # Sperre abgelaufen → neu zählen
    count += 1
    _fails[username] = (count, time.monotonic() + _LOCK_SECONDS if count >= _MAX_FAILS else 0.0)


def authenticate(db: Session, username: str, password: str) -> User | None:
    name = normalize_username(username)
    user = db.query(User).filter(User.username == name).first()
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok or not user.is_active:
        _register_fail(name)
        return None
    _fails.pop(name, None)
    return user


# ── Start-Benutzer aus der .env ──────────────────────────────────────────────

def parse_initial_users() -> list[tuple[str, str]]:
    """[(Name, Passwort)] aus INITIAL_ADMIN_USER/-PASSWORD und INITIAL_USERS."""
    found: list[tuple[str, str]] = []
    if settings.initial_admin_user and settings.initial_admin_password:
        found.append((settings.initial_admin_user, settings.initial_admin_password))
    elif settings.initial_admin_user or settings.initial_admin_password:
        logger.warning("INITIAL_ADMIN_USER und INITIAL_ADMIN_PASSWORD müssen zusammen gesetzt sein — ignoriert")
    for part in (settings.initial_users or "").split(";"):
        part = part.strip()
        if not part:
            continue
        name, sep, pw = part.partition(":")
        if not sep or not name.strip() or not pw:
            logger.warning("INITIAL_USERS: Eintrag ohne 'name:passwort' übersprungen")
            continue
        found.append((name, pw))
    return found


def seed_initial_users(db: Session) -> dict:
    """
    Legt fehlende Benutzer aus der .env an. Bestehende Benutzer bleiben unverändert
    (damit ein in der Oberfläche geändertes Passwort nicht beim Neustart überschrieben wird) —
    außer FORCE_PASSWORD_RESET=true: dann wird das Passwort auf den .env-Wert gesetzt.
    """
    result = {"created": 0, "reset": 0}
    for raw_name, password in parse_initial_users():
        name = normalize_username(raw_name)
        if len(password) < MIN_PASSWORD_LENGTH:
            logger.warning("Passwort von '%s' (.env) hat weniger als %d Zeichen — bitte ändern",
                           name, MIN_PASSWORD_LENGTH)
        user = db.query(User).filter(User.username == name).first()
        if user is None:
            db.add(User(username=name, password_hash=hash_password(password)))
            db.commit()
            result["created"] += 1
            logger.info("Benutzer '%s' aus der .env angelegt", name)
        elif settings.force_password_reset:
            user.password_hash = hash_password(password)
            user.is_active = True
            db.commit()
            end_all_sessions(db, user.id)
            result["reset"] += 1
            logger.warning("Passwort von '%s' per FORCE_PASSWORD_RESET zurückgesetzt — "
                           "Variable jetzt aus der .env entfernen!", name)
    if settings.auth_enabled and db.query(User).count() == 0:
        logger.error("✗ Anmeldung ist aktiv, aber es existiert kein Benutzer. INITIAL_ADMIN_USER und "
                     "INITIAL_ADMIN_PASSWORD in der .env setzen (oder AUTH_ENABLED=false) und neu starten.")
    if not settings.auth_enabled:
        logger.warning("⚠ AUTH_ENABLED=false — die Anwendung ist OHNE Anmeldung erreichbar")
    return result
