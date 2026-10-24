"""
Systemprompt aus der Schema-Definition. Aufbau bewusst „fest vorn, variabel hinten“, damit das Prefix-Caching von vLLM greift:

    [System: Regeln + Feldbeschreibung dieser Version + optionale Zusatzanweisung]  ← gleich für alle Belege einer Version
    [User:   feste Anweisung + Bilder des Belegs]                                    ← erst die Bilder unterscheiden sich

Die Zusatzanweisung (z. B. ein Systemprompt-Baustein des Workflow-Knotens) steht VOR den Bildern, aber hinter dem generierten Teil.
Die Ausgabe ist deterministisch (feste Reihenfolge, keine Zeitstempel) — gleiche Version ergibt gleichen Text.
"""

from __future__ import annotations

from app.models.schema_def import SchemaVersion
from app.services.schema_models import llm_fields, root_entity

RULES = """Du liest Geschäftsdokumente (Rechnungen) aus Bildern und gibst die Daten als JSON gemäß dem vorgegebenen Schema zurück.

REGELN:
- Antworte ausschließlich mit dem JSON-Objekt, ohne Text davor oder danach.
- Jeder Schlüssel des Schemas muss vorkommen. Ist ein Wert auf dem Dokument nicht zu finden, schreibe null — erfinde nichts und rechne nichts aus.
- Übernimm Werte so, wie sie gedruckt sind. Zahlen dürfen deutsch geschrieben sein (1.234,56); Datumsangaben bevorzugt als YYYY-MM-DD.
- Mengen: `value` und `unit` exakt wie in der Mengenspalte, ohne Umrechnung (z. B. 25.880,00 und kg, nicht 25,88 und t).
- Listen enthalten alle Einträge in der Reihenfolge des Dokuments; gibt es keine, ist die Liste leer."""

USER_TEXT = "Analysiere alle Seiten des folgenden Dokuments und extrahiere die Daten gemäß Schema."


def _entity_block(version: SchemaVersion, entity, by_id, depth: int = 0) -> list[str]:
    pad = "  " * depth
    lines = [f"{pad}{entity.display_name} ({entity.name}):"]
    for f in llm_fields(entity):
        extra = f" Erlaubte Werte: {', '.join(f.enum_values)}." if f.data_type == "enum" and f.enum_values else ""
        desc = f" — {f.description.strip()}" if f.description else ""
        req = " [Pflicht]" if f.required else ""
        lines.append(f"{pad}- {f.name}: {f.display_name}{desc}{extra}{req}")
    for rel in version.relations:
        if rel.source_entity_id == entity.id:
            target = by_id[rel.target_entity_id]
            kind = "Liste" if rel.kind == "parent_child" else "Objekt"
            lines.append(f"{pad}- {rel.name}: {kind} „{target.display_name}“" + (f" — {rel.description}" if rel.description else ""))
    for rel in version.relations:
        if rel.source_entity_id == entity.id:
            lines.extend(_entity_block(version, by_id[rel.target_entity_id], by_id, depth + 1))
    return lines


def build_system_prompt(version: SchemaVersion, extra_instruction: str | None = None) -> str:
    by_id = {e.id: e for e in version.entities}
    root = root_entity(version)
    parts = [RULES, "FELDER:\n" + "\n".join(_entity_block(version, root, by_id))]
    if extra_instruction and extra_instruction.strip():
        parts.append("ZUSATZANWEISUNG:\n" + extra_instruction.strip())
    return "\n\n".join(parts)
