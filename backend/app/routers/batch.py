"""
Router für Worker-Batch-Verwaltung (Platzhalter).

Aktuell keine aktiven Endpunkte — zukünftig geplant für:
  - Batch-weite KI-Analyse-Steuerung (alle pending-Dokumente eines Batches)
  - Prioritätsverwaltung in der Worker-Queue

Vorhandene Batch-Endpunkte befinden sich in routers/imports.py.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas.ai_clients import AIClientsRead
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db

# Alle Endpunkte dieses Routers beginnen mit /api/batch
router = APIRouter(prefix="/api/batch", tags=["Worker-Batch"])


