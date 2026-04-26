"""
Router für KI-Konfigurationen.

Endpunkte:
  GET    /api/ai-clients/           — alle Konfigurationen auflisten
  POST   /api/ai-clients/           — neue Konfiguration erstellen
  GET    /api/ai-clients/{id}       — einzelne Konfiguration abrufen
  PUT    /api/ai-clients/{id}       — Konfiguration aktualisieren
  DELETE /api/ai-clients/{id}       — Konfiguration löschen
  POST   /api/ai-clients/{id}/set-default — als Standard setzen
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas.ai_clients import AIClientsCreate, AIClientsRead, AIClientsUpdate

# Alle Endpunkte dieses Routers beginnen mit /api/ai-clients
router = APIRouter(prefix="/api/ai-clients", tags=["KI-Konfigurationen"])


@router.get("", response_model=list[AIClientsRead])
def list_ai_configs(db: Session = Depends(get_db)):
    """Gibt alle konfigurierten KI-APIs zurück."""
    return crud.ai_config.get_all(db)


@router.post("", response_model=AIClientsRead, status_code=status.HTTP_201_CREATED)
def create_ai_config(payload: AIClientsCreate, db: Session = Depends(get_db)):
    """
    Erstellt eine neue KI-Konfiguration.
    Falls is_default=True, wird der Standard von allen anderen entfernt.
    """
    return crud.ai_config.create(db, payload)


@router.get("/{config_id}", response_model=AIClientsRead)
def get_ai_config(config_id: int, db: Session = Depends(get_db)):
    """Gibt eine einzelne KI-Konfiguration anhand ihrer ID zurück."""
    obj = crud.ai_config.get_by_id(db, config_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="KI-Konfiguration nicht gefunden")
    return obj


@router.put("/{config_id}", response_model=AIClientsRead)
def update_ai_config(
    config_id: int, payload: AIClientsUpdate, db: Session = Depends(get_db)
):
    """Aktualisiert eine KI-Konfiguration vollständig."""
    obj = crud.ai_config.update(db, config_id, payload)
    if obj is None:
        raise HTTPException(status_code=404, detail="KI-Konfiguration nicht gefunden")
    return obj


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_config(config_id: int, db: Session = Depends(get_db)):
    """Löscht eine KI-Konfiguration."""
    if not crud.ai_config.delete(db, config_id):
        raise HTTPException(status_code=404, detail="KI-Konfiguration nicht gefunden")


@router.post("/{config_id}/toggle-active", response_model=AIClientsRead)
def toggle_active_ai_config(config_id: int, db: Session = Depends(get_db)):
    """
    Schaltet den aktiv-Status einer KI-Konfiguration um.
    Mehrere Konfigurationen können gleichzeitig aktiv sein (Cluster-Betrieb).
    Beim Aktivieren wird eine eventuelle temporäre Sperre (timeout_at) aufgehoben.
    """
    obj = crud.ai_config.toggle_active(db, config_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="KI-Konfiguration nicht gefunden")
    return obj


@router.post("/{config_id}/clear-timeout", response_model=AIClientsRead)
def clear_timeout_ai_config(config_id: int, db: Session = Depends(get_db)):
    """
    Hebt eine temporäre Sperre (timeout_at) auf, ohne active zu ändern.
    Nützlich wenn eine KI vom Worker automatisch gesperrt wurde und
    wieder verfügbar gemacht werden soll.
    """
    obj = crud.ai_config.clear_timeout(db, config_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="KI-Konfiguration nicht gefunden")
    return obj
