"""FastAPI-Abhängigkeit, die einen angemeldeten Benutzer verlangt."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services import auth_service

SESSION_COOKIE = "ra_session"


def require_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """
    401, wenn nicht angemeldet. Mit AUTH_ENABLED=false wird nichts geprüft (None).
    Die Einstellung wird bei jeder Anfrage gelesen, nicht beim Import.
    """
    if not settings.auth_enabled:
        return None
    user = auth_service.user_for_token(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return user
