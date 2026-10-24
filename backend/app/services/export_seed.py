"""Seed: zwei Export-Vorlagen auf Basis des Ausgangsschemas (idempotent — nur wenn es noch keine Vorlage gibt)."""

from sqlalchemy.orm import Session

from app.models.export_template import ExportTemplate


def seed_export_templates(db: Session) -> bool:
    if db.query(ExportTemplate).first() is not None:
        return False
    db.add(ExportTemplate(
        name="Belege und Positionen (Blatt je Tabelle)",
        description="Ein Blatt für Belege, eines für Positionen, eines für Lieferanten — verbunden über die ID-Spalten.",
        layout="sheet_per_entity", quantity_mode="original",
        config={"vendor": ["name", "ust_id_nr"]},
    ))
    db.add(ExportTemplate(
        name="Flach: eine Zeile je Position",
        description="Eine Zeile pro Position mit den Belegdaten davor. Mengen in Tonnen/Liter/… (normiert).",
        layout="flat", quantity_mode="normalized",
        config={"vendor": ["name"], "flat": {"root": "beleg", "child": "position"}},
    ))
    db.flush()
    return True
