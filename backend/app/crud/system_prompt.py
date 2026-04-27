"""
CRUD-Operationen für die system_prompts-Tabelle.

Prompt-Typen:
  _TYPE_DOC_TYPE = 0  — Dokumententyp-Erkennung (detect_document_type)
  _TYPE_DEFAULT  = 1  — Standard-Extraktion für Eingangsrechnungen (extract_invoice_data)

Hinweis: Es kann maximal einen Prompt pro Typ geben. Die CRUD-Funktionen
stellen das NICHT automatisch sicher — der Aufrufer muss ggf. alte Prompts
desselben Typs vorher löschen oder den Typ ändern.
"""

from sqlalchemy.orm import Session

from app.models.system_prompt import SystemPrompt
from app.schemas.system_prompt import SystemPromptCreate, SystemPromptUpdate

# Konstanten für die Prompt-Typen — zentrale Definition verhindert Magic Numbers
_TYPE_DOC_TYPE = 0      # Dokumententyp-Erkennungsprompt
_TYPE_DEFAULT  = 1      # Standard-Extraktionsprompt (Eingangsrechnung)


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
    return db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DEFAULT).first()


def get_doc_type_prompt(db: Session) -> SystemPrompt | None:
    """
    Gibt den Dokumententyp-Erkennungsprompt (type=0) zurück.
    Wenn None → zweistufige Analyse deaktiviert, direkte Extraktion ohne Typ-Erkennung.
    """
    return db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DOC_TYPE).first()


def create(db: Session, data: SystemPromptCreate) -> SystemPrompt:
    """Legt einen neuen Systemprompt an."""
    obj = SystemPrompt(name=data.name, content=data.content, type=data.type)
    db.add(obj)
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
    obj.type = data.type
    db.commit()
    db.refresh(obj)
    return obj


def set_default(db: Session, prompt_id: int) -> SystemPrompt | None:
    """Setzt einen Prompt als Standard-Extraktionsprompt (type → 1)."""
    obj = db.get(SystemPrompt, prompt_id)
    if obj is None:
        return None
    obj.type = _TYPE_DEFAULT
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
