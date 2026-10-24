"""
Router für Analyse-Workflows.

Endpunkte:
  GET    /api/workflows                  — alle Workflows (ohne Graph)
  GET    /api/workflows/templates        — mitgelieferte Vorlagen
  POST   /api/workflows/validate         — Graph prüfen (ohne zu speichern)
  POST   /api/workflows                  — anlegen (eigener Graph oder Vorlage)
  GET    /api/workflows/{id}             — Workflow mit Graph
  PUT    /api/workflows/{id}             — speichern (Graph wird geprüft)
  DELETE /api/workflows/{id}             — löschen (Lieferanten-Zuordnungen werden aufgehoben)
  (Es gibt keinen Standard-Workflow: jede Analyse nennt ihren Workflow ausdrücklich.)
  POST   /api/workflows/{id}/duplicate   — duplizieren
"""

import copy
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas.workflow import (
    WorkflowCreate, WorkflowListRead, WorkflowRead, WorkflowTemplate, WorkflowUpdate,
    WorkflowValidateRequest, WorkflowValidateResult,
)
from app.services import workflow_service

router = APIRouter(prefix="/api/workflows", tags=["Workflows"])


def _check_references(db: Session, graph: dict) -> list[str]:
    """Warnungen für Systemprompts / KI-Konfigurationen, die es nicht (mehr) gibt."""
    warnings: list[str] = []
    for node in graph.get("nodes", []):
        cfg = (node.get("data") or {}).get("config") or {}
        label = (node.get("data") or {}).get("label") or node.get("type")
        pid, cid = cfg.get("system_prompt_id"), cfg.get("ai_config_id")
        if pid is not None and crud.system_prompt.get_by_id(db, pid) is None:
            warnings.append(f"'{label}': Systemprompt #{pid} existiert nicht")
        if cid is not None and crud.ai_config.get_by_id(db, cid) is None:
            warnings.append(f"'{label}': KI-Konfiguration #{cid} existiert nicht")
    return warnings


def _normalized(graph: dict) -> dict:
    """Ergänzt Standardwerte der Knoten-Konfigurationen (nur gültige Graphen)."""
    out = copy.deepcopy(graph)
    for node in out["nodes"]:
        data = node.setdefault("data", {})
        data["config"] = workflow_service.parse_config(node["type"], data.get("config")).model_dump()
        data.setdefault("label", node["type"])
    out.setdefault("edges", [])
    return out


def _validated_or_422(db: Session, graph: dict) -> dict:
    report = workflow_service.validate_graph(graph)
    if not report.ok:
        raise HTTPException(status_code=422, detail="Workflow ungültig: " + "; ".join(report.errors))
    return _normalized(graph)


def _read(db: Session, obj) -> WorkflowRead:
    out = WorkflowRead.model_validate(obj)
    out.supplier_count = crud.workflow.supplier_count(db, obj.id)
    return out


@router.get("", response_model=list[WorkflowListRead])
def list_workflows(db: Session = Depends(get_db)):
    result = []
    for wf in crud.workflow.get_all(db):
        try:
            nodes = len(json.loads(wf.graph).get("nodes", []))
        except ValueError:
            nodes = 0
        result.append(WorkflowListRead(
            id=wf.id, name=wf.name, description=wf.description, is_default=wf.is_default,
            node_count=nodes, supplier_count=crud.workflow.supplier_count(db, wf.id),
            created_at=wf.created_at, updated_at=wf.updated_at,
        ))
    return result


@router.get("/templates", response_model=list[WorkflowTemplate])
def list_templates():
    return workflow_service.templates()


@router.post("/validate", response_model=WorkflowValidateResult)
def validate_workflow(payload: WorkflowValidateRequest, db: Session = Depends(get_db)):
    """Prüft einen Graphen (Struktur, Konfiguration, Verbindungen) ohne zu speichern."""
    report = workflow_service.validate_graph(payload.graph)
    warnings = list(report.warnings)
    if report.ok:
        warnings += _check_references(db, payload.graph)
    return WorkflowValidateResult(ok=report.ok, errors=report.errors, warnings=warnings)


@router.post("", response_model=WorkflowRead, status_code=201)
def create_workflow(payload: WorkflowCreate, db: Session = Depends(get_db)):
    if payload.graph is not None:
        graph = payload.graph
    elif payload.template:
        tpl = next((t for t in workflow_service.templates() if t["key"] == payload.template), None)
        if tpl is None:
            raise HTTPException(status_code=404, detail=f"Vorlage '{payload.template}' nicht gefunden")
        graph = tpl["graph"]
    else:   # leerer Workflow: nur Start
        graph = {"nodes": [workflow_service._node("start", "start", "Start", 0, 80)], "edges": []}
    graph = _validated_or_422(db, graph)
    return _read(db, crud.workflow.create(db, payload.name, payload.description, graph))


@router.get("/{workflow_id}", response_model=WorkflowRead)
def get_workflow(workflow_id: int, db: Session = Depends(get_db)):
    obj = crud.workflow.get_by_id(db, workflow_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return _read(db, obj)


@router.put("/{workflow_id}", response_model=WorkflowRead)
def update_workflow(workflow_id: int, payload: WorkflowUpdate, db: Session = Depends(get_db)):
    if crud.workflow.get_by_id(db, workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    graph = _validated_or_422(db, payload.graph)
    obj = crud.workflow.update(db, workflow_id, payload.name, payload.description, graph)
    return _read(db, obj)


@router.delete("/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: int, db: Session = Depends(get_db)):
    if not crud.workflow.delete(db, workflow_id):
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    return Response(status_code=204)


@router.post("/{workflow_id}/duplicate", response_model=WorkflowRead, status_code=201)
def duplicate_workflow(workflow_id: int, db: Session = Depends(get_db)):
    obj = crud.workflow.get_by_id(db, workflow_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Workflow nicht gefunden")
    copy_ = crud.workflow.create(db, f"{obj.name} (Kopie)", obj.description, json.loads(obj.graph))
    return _read(db, copy_)
