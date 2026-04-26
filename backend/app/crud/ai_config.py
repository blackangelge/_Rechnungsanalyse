import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.ai_clients import AIClients
from app.schemas.ai_clients import AIClientsCreate, AIClientsUpdate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Interner Filter-Helper
# ---------------------------------------------------------------------------

def _truly_active_filter(query):
    """
    Filter: active=True UND (timeout_at IS NULL ODER timeout_at <= jetzt).
    Temporär deaktivierte KIs (timeout_at in der Zukunft) werden ausgeschlossen.
    """
    return query.filter(
        AIClients.active == True,  # noqa: E712
        or_(
            AIClients.timeout_at == None,  # noqa: E711
            AIClients.timeout_at <= func.now(),
        ),
    )


# ---------------------------------------------------------------------------
# CRUD-Operationen
# ---------------------------------------------------------------------------

def get_all(db: Session) -> list[AIClients]:
    return db.query(AIClients).order_by(AIClients.id).all()


def get_by_id(db: Session, config_id: int) -> AIClients | None:
    return db.get(AIClients, config_id)


def get_active_list(db: Session, primary_type: int | None = None) -> list[AIClients]:
    """
    Gibt wirklich verfügbare KI-Konfigurationen zurück:
    active=True UND nicht temporär deaktiviert.
    """
    q = _truly_active_filter(db.query(AIClients))
    if primary_type is not None:
        q = q.filter(AIClients.primary_type == primary_type)
    return q.all()


def get_default(db: Session, primary_type: int | None = None) -> AIClients | None:
    """
    Gibt eine zufällig gewählte, wirklich verfügbare KI-Konfiguration zurück.
    Ignoriert temporär deaktivierte Clients (timeout_at in der Zukunft).
    Falls primary_type angegeben: bevorzuge passenden Typ, Fallback auf alle.
    """
    active = get_active_list(db, primary_type)
    if not active:
        active = get_active_list(db)
    return random.choice(active) if active else None


def get_worker_capacity(db: Session) -> int:
    """
    Berechnet die Gesamt-Worker-Kapazität aus allen aktiven (nicht temp. deaktivierten)
    KI-Konfigurationen: Summe der parallel_request-Werte.
    """
    active = get_active_list(db)
    return sum(max(1, c.parallel_request) for c in active)


def temporarily_disable(db: Session, config_id: int, minutes: int = 10) -> AIClients | None:
    """
    Deaktiviert eine KI-Konfiguration temporär für `minutes` Minuten.
    Setzt timeout_at = jetzt + minutes (active bleibt True, damit der Nutzer
    die KI manuell über toggle_active wieder aktivieren kann).
    """
    obj = db.get(AIClients, config_id)
    if obj is None:
        return None
    obj.timeout_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    db.commit()
    db.refresh(obj)
    logger.warning(
        "KI #%d '%s' temporär deaktiviert bis %s (%d min)",
        obj.id, obj.name, obj.timeout_at.strftime("%H:%M:%S"), minutes,
    )
    return obj


def create(db: Session, data: AIClientsCreate) -> AIClients:
    obj = AIClients(**data.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update(db: Session, config_id: int, data: AIClientsUpdate) -> AIClients | None:
    obj = db.get(AIClients, config_id)
    if obj is None:
        return None
    for field, value in data.model_dump().items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, config_id: int) -> bool:
    obj = db.get(AIClients, config_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True


def toggle_active(db: Session, config_id: int) -> AIClients | None:
    """
    Schaltet den aktiv-Status um.
    Beim Aktivieren wird timeout_at gelöscht (hebt temp. Deaktivierung auf).
    """
    obj = db.get(AIClients, config_id)
    if obj is None:
        return None
    obj.active = not obj.active
    if obj.active:
        obj.timeout_at = None  # Temporäre Sperre aufheben beim manuellen Aktivieren
    db.commit()
    db.refresh(obj)
    return obj


def clear_timeout(db: Session, config_id: int) -> AIClients | None:
    """
    Hebt eine temporäre Sperre auf (setzt timeout_at = NULL).
    active bleibt unverändert.
    """
    obj = db.get(AIClients, config_id)
    if obj is None:
        return None
    obj.timeout_at = None
    db.commit()
    db.refresh(obj)
    logger.info("Temporäre Sperre für KI #%d '%s' aufgehoben", obj.id, obj.name)
    return obj
