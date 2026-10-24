"""Tests der Workflow-Engine: Validierung, Bedingungen, Ablauf, Vorlagen."""

import asyncio
import copy

import pytest

from app.services import workflow_service as ws
from app.services.workflow_service import BranchConfig, NodeResult, WorkflowError


def _tpl(key):
    return copy.deepcopy(next(t for t in ws.templates() if t["key"] == key)["graph"])


def _g(nodes, edges):
    return {"nodes": nodes, "edges": edges}


# ─── Vorlagen ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["standard", "direct", "trimmed", "tonnes"])
def test_templates_are_valid_without_errors(key):
    rep = ws.validate_graph(_tpl(key))
    assert rep.ok, rep.errors
    assert rep.warnings == []


# ─── Validierung ──────────────────────────────────────────────────────────────

def test_validate_rejects_garbage_and_empty():
    assert not ws.validate_graph("x").ok
    assert not ws.validate_graph({"nodes": []}).ok
    assert not ws.validate_graph({"nodes": "x"}).ok


def test_validate_needs_exactly_one_start():
    g = _tpl("direct")
    g["nodes"] = [n for n in g["nodes"] if n["type"] != "start"]
    g["edges"] = [e for e in g["edges"] if e["source"] != "start"]
    assert any("genau einen Start" in e for e in ws.validate_graph(g).errors)
    g2 = _tpl("direct")
    g2["nodes"].append(ws._node("start2", "start", "Start 2", 0, 0))
    assert any("genau einen Start" in e for e in ws.validate_graph(g2).errors)


def test_validate_duplicate_ids_unknown_type_and_bad_edges():
    g = _tpl("direct")
    g["nodes"].append(ws._node("extract", "extract", "Doppelt", 0, 0))
    assert any("doppelt" in e for e in ws.validate_graph(g).errors)
    g = _tpl("direct")
    g["nodes"][1]["type"] = "magic"
    assert any("unbekannter Typ" in e for e in ws.validate_graph(g).errors)
    g = _tpl("direct")
    g["edges"].append(ws._edge("extract", "ghost"))
    assert any("nicht vorhandenen" in e for e in ws.validate_graph(g).errors)
    g = _tpl("direct")
    g["edges"].append(ws._edge("end", "extract"))
    assert any("Ende-Knoten" in e for e in ws.validate_graph(g).errors)


def test_validate_one_edge_per_output_and_branch_handles():
    g = _tpl("direct")
    g["edges"].append(ws._edge("start", "validate"))      # zweite Kante am selben Ausgang
    assert any("nur eine erlaubt" in e for e in ws.validate_graph(g).errors)
    g = _tpl("standard")
    g["edges"] = [e if e["source"] != "branch" else {**e, "sourceHandle": None} for e in g["edges"]]
    assert any("ungültiger Ausgang" in e for e in ws.validate_graph(g).errors)


def test_validate_detects_cycles():
    g = _tpl("direct")
    g["nodes"].append(ws._node("again", "extract", "Nochmal", 0, 0))
    g["edges"] = [e for e in g["edges"] if e["source"] != "validate"]
    g["edges"] += [ws._edge("validate", "again"), ws._edge("again", "validate")]
    assert any("Schleife" in e for e in ws.validate_graph(g).errors)


def test_validate_no_incoming_edge_into_start():
    g = _tpl("direct")
    g["edges"] = [e for e in g["edges"] if e["source"] != "validate"]
    g["edges"].append(ws._edge("validate", "start"))
    assert not ws.validate_graph(g).ok


def test_validate_config_errors():
    g = _tpl("trimmed")
    g["nodes"][1]["data"]["config"] = {"max_pages": 0}
    assert any("max_pages" in e for e in ws.validate_graph(g).errors)
    g = _tpl("standard")
    g["nodes"][2]["data"]["config"] = {"field": "page_count", "op": "contains", "value": "3"}
    assert any("passt nicht zu" in e for e in ws.validate_graph(g).errors)
    g["nodes"][2]["data"]["config"] = {"field": "page_count", "op": "gt", "value": "viele"}
    assert any("Zahl" in e for e in ws.validate_graph(g).errors)
    g["nodes"][2]["data"]["config"] = {"field": "document_type", "op": "equals", "value": " "}
    assert any("Vergleichswert" in e for e in ws.validate_graph(g).errors)


def test_validate_warnings():
    g = _tpl("standard")
    g["edges"] = [e for e in g["edges"] if e["sourceHandle"] != "false"]
    g["nodes"].append(ws._node("lost", "end", "Verloren", 0, 0))
    warns = ws.validate_graph(g).warnings
    assert any("nicht erreichbar" in w for w in warns)
    assert any("Ausgang 'Nein'" in w for w in warns)
    only_start = _g([ws._node("s", "start", "Start", 0, 0)], [])
    assert any("keine Rechnungsextraktion" in w for w in ws.validate_graph(only_start).warnings)
    early = _g([ws._node("s", "start", "S", 0, 0), ws._node("v", "validate", "V", 0, 0),
                ws._node("x", "extract", "X", 0, 0)], [ws._edge("s", "v"), ws._edge("v", "x")])
    assert any("vor jeder Extraktion" in w for w in ws.validate_graph(early).warnings)


# ─── Bedingungen ──────────────────────────────────────────────────────────────

def _cond(field, op, value, **facts):
    return ws.evaluate_condition(BranchConfig(field=field, op=op, value=value), facts)


def test_condition_document_type_by_id_or_name():
    facts = dict(document_type_id=1, document_type_name="Eingangsrechnung")
    assert _cond("document_type", "equals", "1", **facts)
    assert _cond("document_type", "equals", "eingangsrechnung", **facts)
    assert not _cond("document_type", "equals", "3", **facts)
    assert _cond("document_type", "not_equals", "3", **facts)
    assert _cond("document_type", "in", "3, 1", **facts)
    assert _cond("document_type", "contains", "rechnung", **facts)
    assert not _cond("document_type", "equals", "1")                  # unbekannt
    assert _cond("document_type", "not_equals", "1")


def test_condition_page_count():
    assert _cond("page_count", "gt", "3", page_count=10)
    assert not _cond("page_count", "gt", "10", page_count=10)
    assert _cond("page_count", "lt", "2", page_count=1)
    assert _cond("page_count", "equals", "10", page_count=10)
    assert _cond("page_count", "in", "1,2,10", page_count=10)
    assert not _cond("page_count", "gt", "x", page_count=10)
    assert not _cond("page_count", "gt", "1")                         # unbekannt


def test_condition_supplier_and_status_case_insensitive():
    assert _cond("supplier_name", "contains", "acme", supplier_name="ACME GmbH & Co. KG")
    assert _cond("supplier_name", "equals", "acme gmbh", supplier_name="ACME GmbH")
    assert not _cond("supplier_name", "contains", "", supplier_name="ACME")
    assert _cond("supplier_name", "not_equals", "x")
    assert _cond("validation_status", "in", "needs_review, corrected", validation_status="corrected")


# ─── Ablauf ───────────────────────────────────────────────────────────────────

class Ctx:
    def __init__(self):
        self.log = []


def _handlers(branch_to="true", fail_on=None):
    async def plain(cfg, ctx, name=None):
        ctx.log.append(name)

    def make(name):
        async def h(cfg, ctx):
            if name == fail_on:
                raise RuntimeError("kaputt")
            ctx.log.append(name)
            if name == "branch":
                return NodeResult(branch_to, "geprüft")
        return h
    return {t: make(t) for t in ws.NODE_TYPES}


def test_run_graph_follows_branch_true_and_false():
    for branch_to, expected in (("true", ["start", "detect_type", "branch", "extract", "validate", "end"]),
                                ("false", ["start", "detect_type", "branch", "discard"])):
        ctx = Ctx()
        trace = asyncio.run(ws.run_graph(_tpl("standard"), _handlers(branch_to), ctx))
        assert ctx.log == expected
        assert [t["type"] for t in trace] == expected
        assert trace[2]["branch"] == branch_to and trace[2]["detail"] == "geprüft"
        assert all(t["status"] == "ok" for t in trace)


def test_run_graph_stops_when_edge_missing():
    g = _tpl("standard")
    g["edges"] = [e for e in g["edges"] if e["sourceHandle"] != "false"]
    ctx = Ctx()
    asyncio.run(ws.run_graph(g, _handlers("false"), ctx))
    assert ctx.log[-1] == "branch"


def test_run_graph_error_carries_trace():
    ctx = Ctx()
    with pytest.raises(WorkflowError) as exc:
        asyncio.run(ws.run_graph(_tpl("direct"), _handlers(fail_on="extract"), ctx))
    assert exc.value.node_id == "extract" and "kaputt" in str(exc.value)
    assert [t["status"] for t in exc.value.trace] == ["ok", "error"]


def test_run_graph_passes_parsed_config():
    seen = {}

    async def trim(cfg, ctx):
        seen["cfg"] = cfg
    handlers = _handlers()
    handlers["trim_pages"] = trim
    asyncio.run(ws.run_graph(_tpl("trimmed"), handlers, Ctx()))
    assert seen["cfg"].max_pages == 1
