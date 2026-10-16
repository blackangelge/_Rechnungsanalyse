"""
CRUD-Operationen für die system_prompts-Tabelle.

Prompt-Typen:
  _TYPE_DOC_TYPE = 0  — Dokumententyp-Erkennung (detect_document_type)
  _TYPE_DEFAULT  = 1  — Standard-Extraktion für Eingangsrechnungen (extract_invoice_data)
  _TYPE_WORKFLOW = 2  — Workflow-Baustein: wird NIE automatisch gewählt, nur ausdrücklich
                        (Workflow-Knoten, KI-Test, Auswahl in der Analyse). Beliebig viele möglich.

Es gibt höchstens je einen Prompt vom Typ 0 und 1 (die automatisch gewählten). Beim Anlegen, Typ-Wechsel
oder „Als Standard setzen“ werden andere Prompts desselben Typs zu Workflow-Bausteinen (Typ 2) herabgestuft.
`get_default()` ist deterministisch (kleinste ID), falls ältere Datenstände mehrere Typ-1-Prompts enthalten.
"""

from sqlalchemy.orm import Session

from app.models.system_prompt import SystemPrompt
from app.schemas.system_prompt import SystemPromptCreate, SystemPromptUpdate

# Konstanten für die Prompt-Typen — zentrale Definition verhindert Magic Numbers
_TYPE_DOC_TYPE = 0      # Dokumententyp-Erkennungsprompt
_TYPE_DEFAULT  = 1      # Standard-Extraktionsprompt (Eingangsrechnung)
_TYPE_WORKFLOW = 2      # Workflow-Baustein (nie automatisch gewählt)
_UNIQUE_TYPES = (_TYPE_DOC_TYPE, _TYPE_DEFAULT)


def _demote_others(db: Session, keep_id: int | None, ptype: int) -> int:
    """Stuft alle anderen Prompts des (eindeutigen) Typs zu Workflow-Bausteinen herab."""
    if ptype not in _UNIQUE_TYPES:
        return 0
    q = db.query(SystemPrompt).filter(SystemPrompt.type == ptype)
    if keep_id is not None:
        q = q.filter(SystemPrompt.id != keep_id)
    n = 0
    for other in q.all():
        other.type = _TYPE_WORKFLOW
        n += 1
    return n


def standard_count(db: Session) -> int:
    """Wie viele Typ-1-Prompts es gibt (mehr als einer = mehrdeutiger Standard)."""
    return db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DEFAULT).count()


def get_all(db: Session) -> list[SystemPrompt]:
    """Gibt alle Prompts zurück, nach ID sortiert."""
    return db.query(SystemPrompt).order_by(SystemPrompt.id).all()


def get_by_id(db: Session, prompt_id: int) -> SystemPrompt | None:
    """Gibt einen Prompt anhand seiner ID zurück."""
    return db.get(SystemPrompt, prompt_id)


def get_default(db: Session) -> SystemPrompt | None:
    """
    Gibt den Standard-Extraktionsprompt (type=1) zurück.
    Wird für die Eingangsrechnungs-Extraktion verwendet (extract_invoice_data).
    """
    return (db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DEFAULT)
            .order_by(SystemPrompt.id).first())


def get_doc_type_prompt(db: Session) -> SystemPrompt | None:
    """
    Gibt den Dokumententyp-Erkennungsprompt (type=0) zurück.
    Wenn None → zweistufige Analyse deaktiviert, direkte Extraktion ohne Typ-Erkennung.
    """
    return (db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DOC_TYPE)
            .order_by(SystemPrompt.id).first())


def create(db: Session, data: SystemPromptCreate) -> SystemPrompt:
    """Legt einen neuen Systemprompt an."""
    obj = SystemPrompt(name=data.name, content=data.content, type=data.type)
    db.add(obj)
    db.flush()
    _demote_others(db, obj.id, obj.type)
    db.commit()
    db.refresh(obj)
    return obj


def update(db: Session, prompt_id: int, data: SystemPromptUpdate) -> SystemPrompt | None:
    """Aktualisiert Name, Inhalt und Typ eines bestehenden Prompts."""
    obj = db.get(SystemPrompt, prompt_id)
    if obj is None:
        return None
    obj.name = data.name
    obj.content = data.content
    type_changed = obj.type != data.type
    obj.type = data.type
    if type_changed:
        _demote_others(db, obj.id, obj.type)
    db.commit()
    db.refresh(obj)
    return obj


def set_default(db: Session, prompt_id: int) -> SystemPrompt | None:
    """Setzt einen Prompt als Standard-Extraktionsprompt (type → 1)."""
    obj = db.get(SystemPrompt, prompt_id)
    if obj is None:
        return None
    obj.type = _TYPE_DEFAULT
    _demote_others(db, obj.id, _TYPE_DEFAULT)
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, prompt_id: int) -> bool:
    """Löscht einen Prompt dauerhaft. Gibt False zurück wenn nicht gefunden."""
    obj = db.get(SystemPrompt, prompt_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True
