from sqlalchemy.orm import Session

from app.models.system_prompt import SystemPrompt
from app.schemas.system_prompt import SystemPromptCreate, SystemPromptUpdate

# type-Werte: 0 = Dokumententyp-Erkennung, 1 = Standard-Extraktion (Eingangsrechnung)
_TYPE_DOC_TYPE = 0      # Dokumententyp-Erkennung
_TYPE_DEFAULT  = 1      # Standard-Extraktionsprompt (Eingangsrechnung)


def get_all(db: Session) -> list[SystemPrompt]:
    return db.query(SystemPrompt).order_by(SystemPrompt.id).all()


def get_by_id(db: Session, prompt_id: int) -> SystemPrompt | None:
    return db.get(SystemPrompt, prompt_id)


def get_default(db: Session) -> SystemPrompt | None:
    """Gibt den Standard-Extraktionsprompt (type=0) zurück."""
    return db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DEFAULT).first()


def get_doc_type_prompt(db: Session) -> SystemPrompt | None:
    """Gibt den Dokumententyp-Erkennungsprompt (type=1) zurück."""
    return db.query(SystemPrompt).filter(SystemPrompt.type == _TYPE_DOC_TYPE).first()


def create(db: Session, data: SystemPromptCreate) -> SystemPrompt:
    obj = SystemPrompt(name=data.name, content=data.content, type=data.type)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update(db: Session, prompt_id: int, data: SystemPromptUpdate) -> SystemPrompt | None:
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
    obj = db.get(SystemPrompt, prompt_id)
    if obj is None:
        return None
    obj.type = _TYPE_DEFAULT
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, prompt_id: int) -> bool:
    obj = db.get(SystemPrompt, prompt_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True
