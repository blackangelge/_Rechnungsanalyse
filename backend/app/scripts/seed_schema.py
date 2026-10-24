"""Legt das Ausgangsschema an (idempotent): `python -m app.scripts.seed_schema`."""

from app.database import SessionLocal
from app.services.schema_seed import seed_initial_schema
from app.services.units_seed import seed_default_units
from app.services.export_seed import seed_export_templates


def main() -> None:
    db = SessionLocal()
    try:
        units_new = seed_default_units(db)
        version = seed_initial_schema(db)
        templates_new = seed_export_templates(db)
        db.commit()
        print("Schema Version %d angelegt." % version.version_number if version else "Schema existiert bereits — nichts zu tun.")
        print("Einheiten angelegt." if units_new else "Einheiten existieren bereits — nichts zu tun.")
        print("Export-Vorlagen angelegt." if templates_new else "Export-Vorlagen existieren bereits — nichts zu tun.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
