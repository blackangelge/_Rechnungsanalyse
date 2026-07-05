"""
Pydantic-Schemas für Automatisierungseinstellungen (Ordner-Sync-Intervall, Export-Zeitplan).

AutomationSettingsUpdate — Payload für PUT /api/settings/automation
AutomationSettingsRead   — Antwort-Schema (erbt Update + fügt id und Zeitstempel hinzu)
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AutomationSettingsUpdate(BaseModel):
    """
    Payload für das Aktualisieren der Automatisierungseinstellungen.

    folder_sync_interval_minutes: wie oft (in Minuten) Ordner-Sync-Imports auf
                                   neue PDFs geprüft werden.
    export_weekday:  0=Montag … 6=Sonntag — Wochentag des automatischen Exports.
    export_hour/export_minute: Uhrzeit des automatischen Exports.
    """

    folder_sync_interval_minutes: int = Field(default=15, ge=1)
    export_weekday: int = Field(default=0, ge=0, le=6)
    export_hour: int = Field(default=6, ge=0, le=23)
    export_minute: int = Field(default=0, ge=0, le=59)


class AutomationSettingsRead(AutomationSettingsUpdate):
    """Antwort-Schema — erbt Update + fügt id und Zeitstempel hinzu."""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
