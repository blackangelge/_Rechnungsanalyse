"""
Orchestrierung der schemagesteuerten Extraktion für einen Beleg.

Die Schritte sind getrennt, damit der asynchrone Workflow-Lauf nur die jeweils blockierende Arbeit in Threads schiebt:
  prepare()          — Version laden, Pydantic-Modell + Prompt + response_format bauen        (DB, kurz)
  call_structured()  — KI-Aufruf mit Structured Outputs                                         (Netz, lang, ohne DB)
  finish()           — validieren, normalisieren, Lieferant zuordnen, speichern                  (DB)
`run_sync()` fügt alles für Skripte und Tests zusammen.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.crud import schema_definition as sd
from app.models.schema_def import SchemaVersion
from app.services import schema_models, schema_prompt, structured_ai
from app.services.schema_models import ExtractionModel
from app.services.schema_pipeline import SaveResult, SchemaValidationFailed, process_and_save


class ExtractionSetupError(ValueError):
    """Kein nutzbares Schema (keine aktive Version, nicht übersetzbar …)."""


@dataclass
class Prepared:
    version_id: int
    version_number: int
    built: ExtractionModel
    system_prompt: str
    user_text: str
    response_format: dict


def load_version(db: Session, version_id: int | None) -> SchemaVersion:
    version = db.get(SchemaVersion, version_id) if version_id else sd.get_active(db)
    if version is None:
        raise ExtractionSetupError("Kein aktives Schema vorhanden — erst das Ausgangsschema anlegen (Seed) oder eine Version aktivieren.")
    if version.status == "draft":
        raise ExtractionSetupError("Ein Entwurf kann nicht für die Extraktion verwendet werden — erst aktivieren.")
    return version


def prepare(db: Session, version_id: int | None = None, extra_instruction: str | None = None) -> Prepared:
    version = load_version(db, version_id)
    try:
        built = schema_models.build_extraction_model(version)
        prompt = schema_prompt.build_system_prompt(version, extra_instruction)
    except schema_models.SchemaModelError as exc:
        raise ExtractionSetupError(str(exc)) from exc
    return Prepared(version.id, version.version_number, built, prompt, schema_prompt.USER_TEXT, schema_models.response_format(built))


def finish(db: Session, document, prepared: Prepared, parsed: dict) -> SaveResult:
    version = db.get(SchemaVersion, prepared.version_id)
    return process_and_save(db, document, version, prepared.built, parsed)


@dataclass
class Outcome:
    save: SaveResult | None
    result: structured_ai.StructuredResult
    prepared: Prepared
    validation_errors: list[str]


def run_sync(db: Session, document, images_b64: list[str], config, version_id: int | None = None,
             extra_instruction: str | None = None, tools: list[dict] | None = None) -> Outcome:
    prepared = prepare(db, version_id, extra_instruction)
    result = structured_ai.call_structured(config, prepared.system_prompt, prepared.user_text, images_b64, prepared.response_format, tools)
    if result.error or result.parsed is None:
        return Outcome(None, result, prepared, [])
    try:
        return Outcome(finish(db, document, prepared, result.parsed), result, prepared, [])
    except SchemaValidationFailed as exc:
        return Outcome(None, result, prepared, exc.messages)
