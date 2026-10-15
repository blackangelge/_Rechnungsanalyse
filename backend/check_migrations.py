"""
Prüft vor `alembic upgrade head`, ob die Migrationskette linear ist (genau ein Head) — ohne Datenbank.

Aufruf (docker-compose, im Backend-Ordner):  python check_migrations.py

Bei mehreren Heads (typisch: eine veraltete Datei liegt noch in alembic/versions/, weil beim Einspielen einer
neuen Version nur kopiert, aber nichts gelöscht wird) werden ALLE Migrationsdateien mit ihrer Revision und
`down_revision` aufgelistet und die Heads markiert; Exit-Code 1. Sonst Exit-Code 0.
Es wird nichts verändert.
"""

import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BASE = Path(__file__).resolve().parent


def main() -> int:
    cfg = Config(str(BASE / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE / "alembic"))
    try:
        script_dir = ScriptDirectory.from_config(cfg)
        heads = script_dir.get_heads()
    except Exception as exc:                      # z.B. doppelte Revisions-IDs, fehlende Vorgänger
        print(f"✗ Migrationsdateien sind fehlerhaft: {exc}")
        return 1
    if len(heads) == 1:
        print(f"✓ Migrationskette in Ordnung (Head: {heads[0]})")
        return 0

    print(f"✗ Die Migrationskette ist verzweigt: {len(heads)} Heads statt 1 → {', '.join(heads)}")
    print("  Alle Migrationsdateien (Datei | revision | down_revision):")
    for rev in sorted(script_dir.walk_revisions(), key=lambda r: Path(r.path).name):
        marker = "  ← HEAD" if rev.revision in heads else ""
        print(f"    {Path(rev.path).name} | {rev.revision} | {rev.down_revision}{marker}")
    print("  → Dateien, die NICHT zum aktuellen Stand des Repos gehören, aus alembic/versions/ entfernen")
    print("    (zur Sicherheit in einen Ordner AUSSERHALB von versions/ verschieben), dann neu starten.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
