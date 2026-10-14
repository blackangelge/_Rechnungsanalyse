"""
CRUD-Operationen für Analyse-Workflows.
"""

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.vendor import Vendor
from app.models.workflow import Workflow


def get_all(db: Session) -> list[Workflow]:
    return db.query(Workflow).order_by(Workflow.name).all()


def get_by_id(db: Session, workflow_id: int) -> Workflow | None:
    return db.get(Workflow, workflow_id)


def get_default(db: Session) -> Workflow | None:
    return db.query(Workflow).filter(Workflow.is_default.is_(True)).first()


def supplier_count(db: Session, workflow_id: int) -> int:
    """Anzahl Lieferanten, denen dieser Workflow zugewiesen ist."""
    return db.query(func.count(Vendor.id)).filter(Vendor.workflow_id == workflow_id).scalar() or 0


def create(db: Session, name: str, description: str | None, graph: dict) -> Workflow:
    obj = Workflow(name=name.strip(), description=description,
                   graph=json.dumps(graph, ensure_ascii=False), is_default=False)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update(db: Session, workflow_id: int, name: str, description: str | None, graph: dict) -> Workflow | None:
    obj = db.get(Workflow, workflow_id)
    if obj is None:
        return None
    obj.name = name.strip()
    obj.description = description
    obj.graph = json.dumps(graph, ensure_ascii=False)
    db.commit()
    db.refresh(obj)
    return obj


def set_default(db: Session, workflow_id: int, value: bool = True) -> Workflow | None:
    """Markiert einen Workflow als Standard (es gibt höchstens einen) bzw. hebt die Markierung auf."""
    obj = db.get(Workflow, workflow_id)
    if obj is None:
        return None
    if value:
        db.query(Workflow).filter(Workflow.id != workflow_id).update({Workflow.is_default: False})
    obj.is_default = value
    db.commit()
    db.refresh(obj)
    return obj


def delete(db: Session, workflow_id: int) -> bool:
    """Löscht einen Workflow; Lieferanten-Zuordnungen werden auf NULL gesetzt."""
    obj = db.get(Workflow, workflow_id)
    if obj is None:
        return False
    db.query(Vendor).filter(Vendor.workflow_id == workflow_id).update({Vendor.workflow_id: None})
    db.delete(obj)
    db.commit()
    return True


def set_supplier_workflow(db: Session, vendor_id: int, workflow_id: int | None) -> Vendor | None:
    """Weist einem Lieferanten (Vendor) einen Workflow zu (None = Standard)."""
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return None
    vendor.workflow_id = workflow_id
    db.commit()
    db.refresh(vendor)
    return vendor
