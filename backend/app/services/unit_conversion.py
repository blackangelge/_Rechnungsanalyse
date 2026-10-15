"""
Einheiten-Umrechnung für Auswertungen (Artikelsuche): Gewicht → kg, Volumen → l, Länge → m, Fläche → m².

Quelle ist dieselbe Einheitentabelle wie in der Plausibilitätsprüfung (`plausibility_service._UNITS`), damit
Prüfung und Auswertung nie auseinanderlaufen. Stück/Verpackungen und unbekannte Einheiten werden nicht umgerechnet
(Basiswerte bleiben dann leer). Die Umrechnung geschieht zur Abfragezeit in SQL — es wird nichts gespeichert, also
gilt sie sofort auch für den gesamten Altbestand.
"""

from __future__ import annotations

from sqlalchemy import case, func

from app.services.plausibility_service import _UNITS

BASE_UNITS = {"mass": "kg", "volume": "l", "length": "m", "area": "m²"}
GROUP_LABELS = {"mass": "Gewicht", "volume": "Volumen", "length": "Länge", "area": "Fläche"}


def _convertible() -> dict[str, tuple[str, float]]:
    return {name: (dim, factor) for name, (dim, factor) in _UNITS.items() if dim in BASE_UNITS}


def unit_key(column):
    """Normalisierte Einheit wie `parse_unit`: klein, ohne Leerzeichen am Rand und ohne Punkte."""
    return func.replace(func.lower(func.trim(column)), ".", "")


def factor_expr(column):
    """Faktor zur Basiseinheit (NULL = nicht umrechenbar)."""
    key = unit_key(column)
    return case({name: f for name, (_, f) in _convertible().items()}, value=key, else_=None)


def base_unit_expr(column):
    """Name der Basiseinheit (NULL = nicht umrechenbar)."""
    key = unit_key(column)
    return case({name: BASE_UNITS[dim] for name, (dim, _) in _convertible().items()}, value=key, else_=None)


def group_names(group: str) -> list[str]:
    """Alle Schreibweisen einer Einheitengruppe ('mass', 'volume', …)."""
    return [name for name, (dim, _) in _convertible().items() if dim == group]


def reference_table() -> list[dict]:
    """Für die Anzeige: je Gruppe Basiseinheit und die Einheiten mit Faktor (nur Hauptschreibweisen)."""
    shown = {
        "mass": ["t", "kg", "g", "mg"],
        "volume": ["hl", "l", "dl", "cl", "ml", "m3"],
        "length": ["km", "m", "cm", "mm"],
        "area": ["m2"],
    }
    out = []
    for dim, names in shown.items():
        units = [{"unit": ("m³" if n == "m3" else "m²" if n == "m2" else n), "factor": _UNITS[n][1]}
                 for n in names if n in _UNITS]
        out.append({"group": dim, "label": GROUP_LABELS[dim], "base_unit": BASE_UNITS[dim], "units": units})
    return out
