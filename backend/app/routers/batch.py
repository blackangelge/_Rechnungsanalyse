"""
Router für KI-Konfigurationen.

Endpunkte:

"""

from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas.ai_clients import AIClientsRead
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db

# Alle Endpunkte dieses Routers beginnen mit /api/batch
router = APIRouter(prefix="/api/batch", tags=["Worker-Batch"])


