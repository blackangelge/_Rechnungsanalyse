"""Seed: Standard-Einheiten (idempotent — nur wenn noch keine Gruppe existiert). Basis je Gruppe wie bisher: t, l, m, m², Stück."""

from sqlalchemy.orm import Session

from app.crud import units as cu
from app.models.units import UnitGroup

# Gruppe → [(Name, Symbol, Faktor, Basis?, ohne Groß/Klein?, Aliasse)]
_DEFAULTS = {
    "Masse": [
        ("Tonne", "t", 1, True, False, ["t", "T", "Tonne", "Tonnen", "to"]),
        ("Kilogramm", "kg", 1000, False, False, ["kg", "KG", "Kilo", "kilo", "Kilogramm", "kgs"]),
        ("Gramm", "g", 1_000_000, False, False, ["g", "Gramm", "gr"]),
        ("Milligramm", "mg", 1_000_000_000, False, False, ["mg", "Milligramm"]),
    ],
    "Volumen": [
        ("Liter", "l", 1, True, True, ["l", "Liter", "ltr"]),
        ("Hektoliter", "hl", "0.01", False, True, ["hl", "Hektoliter"]),
        ("Milliliter", "ml", 1000, False, True, ["ml", "Milliliter"]),
        ("Kubikmeter", "m³", "0.001", False, True, ["m³", "m3", "cbm", "Kubikmeter"]),
    ],
    "Länge": [
        ("Meter", "m", 1, True, False, ["m", "Meter", "lfm", "Laufmeter"]),
        ("Kilometer", "km", "0.001", False, True, ["km", "Kilometer"]),
        ("Zentimeter", "cm", 100, False, True, ["cm", "Zentimeter"]),
        ("Millimeter", "mm", 1000, False, True, ["mm", "Millimeter"]),
    ],
    "Fläche": [
        ("Quadratmeter", "m²", 1, True, False, ["m²", "m2", "qm", "Quadratmeter"]),
    ],
    "Anzahl": [
        ("Stück", "Stk", 1, True, True, ["Stk", "St", "Stück", "Stueck", "pcs", "pc", "x", "Stck"]),
    ],
    "Verpackung": [
        ("Verpackung", "VPE", 1, True, True, ["VPE", "VE", "Pack", "Packung", "Karton", "Sack", "Säcke", "Palette", "Pal", "Paket", "Gebinde", "Bund", "Rolle", "Kiste"]),
    ],
}


def seed_default_units(db: Session) -> bool:
    if db.query(UnitGroup).first() is not None:
        return False
    for group_name, units in _DEFAULTS.items():
        group = cu.create_group(db, group_name)
        for name, symbol, factor, is_base, ci, aliases in units:
            cu.create_unit(db, group, name, symbol, factor, is_base=is_base, case_insensitive=ci, aliases=aliases)
    return True
