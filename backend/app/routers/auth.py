"""Anmeldung: Login, Logout, aktueller Benutzer, Passwort ändern."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, require_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["Anmeldung"])


class LoginRequest(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=500)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=500)
    new_password: str = Field(min_length=auth_service.MIN_PASSWORD_LENGTH, max_length=500)


def _me(user: User | None) -> dict:
    return {
        "auth_enabled": settings.auth_enabled,
        "username": user.username if user else None,
    }


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    if not settings.auth_enabled:
        return _me(None)
    name = auth_service.normalize_username(payload.username)
    wait = auth_service.seconds_locked(name)
    if wait:
        raise HTTPException(status_code=429, detail=f"Zu viele Fehlversuche — bitte {wait} Sekunden warten")
    user = auth_service.authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Benutzername oder Passwort falsch")
    token = auth_service.create_session(db, user)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_days * 86400,
        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )
    return _me(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth_service.end_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(user: User | None = Depends(require_user)):
    return _me(user)


@router.post("/change-password", status_code=204)
def change_password(
    payload: PasswordChange,
    request: Request,
    user: User | None = Depends(require_user),
    db: Session = Depends(get_db),
):
    if user is None:
        raise HTTPException(status_code=400, detail="Anmeldung ist deaktiviert — kein Benutzer")
    if not auth_service.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    user.password_hash = auth_service.hash_password(payload.new_password)
    db.commit()
    # Alle anderen Geräte abmelden, die aktuelle Sitzung bleibt
    auth_service.end_all_sessions(db, user.id, except_token=request.cookies.get(SESSION_COOKIE))
