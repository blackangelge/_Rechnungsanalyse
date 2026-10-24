"""
Workflow-Engine für die KI-Analyse (reine Logik, keine DB/Netzwerk).

Ein Workflow ist ein gerichteter Graph (JSON, kompatibel zu React Flow):

    {"nodes": [{"id": "n1", "type": "start", "position": {"x": 0, "y": 0},
                "data": {"label": "Start", "config": {...}}}, ...],
     "edges": [{"id": "e1", "source": "n1", "target": "n2", "sourceHandle": null}, ...]}

Knotentypen: start · trim_pages · detect_type · branch · extract · validate · normalize_units · ai_refine · end.
Der Ablauf ist sequenziell: ab "start" wird immer genau ein Knoten nach dem anderen
ausgeführt; nur "branch" verzweigt (Ausgang "true"/"false"). Jeder Ausgang darf höchstens
eine ausgehende Kante haben, Zyklen sind verboten. Fehlt die Kante, endet der Ablauf.

Die Knoten-Implementierungen (Handler) kommen von außen (siehe workflow_runner.py);
hier stehen Validierung, Bedingungsauswertung, Vorlagen und die Ablaufschleife.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

# ─── Knotentypen und ihre Konfiguration ───────────────────────────────────────

NODE_TYPES = ("start", "trim_pages", "detect_type", "branch", "extract", "schema_extract", "classify_pages", "validate", "normalize_units", "ai_refine", "end")
# Knoten, die Extraktionsdaten bearbeiten (ohne vorherige Extraktion wirkungslos bzw. nur eingeschränkt)
DATA_NODES = ("validate", "normalize_units")
MAX_STEPS = 100   # Schutz gegen Endlosschleifen (Zyklen werden zusätzlich beim Speichern verboten)


class EmptyConfig(BaseModel):
    pass


class TrimConfig(BaseModel):
    max_pages: int = Field(default=1, ge=1, le=500)


class DetectConfig(BaseModel):
    system_prompt_id: int | None = None      # None = Dokumententyp-Prompt aus den Einstellungen


class ExtractConfig(BaseModel):
    system_prompt_id: int | None = None      # None = in der Analyse gewählter / Standard-Prompt
    ai_config_id: int | None = None          # None = in der Analyse gewählte / Standard-KI
    format_prompt_id: int | None = None      # Prompt vom Typ „Antwortformat“ (None = Standardformat, falls der Prompt keins beschreibt)


class SchemaExtractConfig(BaseModel):
    """Schemagesteuerte Extraktion: JSON-Schema und Prompt entstehen aus der Schema-Definition (kein eigener Prompt nötig)."""

    ai_config_id: int | None = None          # None = in der Analyse gewählte KI
    schema_version_id: int | None = None     # None = aktive Schema-Version
    system_prompt_id: int | None = None      # optionaler Baustein: Zusatzanweisung hinter dem generierten Prompt


class ClassifyPagesConfig(BaseModel):
    """Seitenerkennung: die KI ordnet jede Seite einem Typ zu (Rechnung, Lieferschein …) — auch bei gescannten PDFs."""

    ai_config_id: int | None = None          # None = in der Analyse gewählte KI
    dpi: int = Field(80, ge=40, le=200)      # niedrige Auflösung genügt zum Erkennen und spart Token
    chunk_size: int = Field(10, ge=1, le=30) # Seiten je KI-Anfrage
    action: Literal["classify_only", "keep_invoice_pages", "split_invoices"] = "classify_only"
    # keep: PDF-Version nur mit den Rechnungsseiten anlegen; split: zusätzlich jede weitere Rechnung der PDF als eigenen Beleg abspalten
    # (beides nur, wenn die Erkennung eindeutig ist — sonst „Nachbearbeiten“, nichts wird verändert)
    system_prompt_id: int | None = None      # optionaler Baustein: Zusatzanweisung (z. B. Besonderheiten Ihrer Lieferanten)


class ValidateConfig(BaseModel):
    auto_correct: bool = True                # Einheitenfehler automatisch korrigieren
    ai_recheck: bool = True                  # bei Auffälligkeit zweite KI-Abfrage


class NormalizeConfig(BaseModel):
    target: Literal["t", "kg"] = "t"         # Zieleinheit für Gewichte (Menge, Einheit und Einzelpreis werden umgerechnet)
    # nur für Ziel t: "tonne_prices" = nur Positionen mit Preis je Tonne, "all" = alle Gewichtspositionen
    mode: Literal["tonne_prices", "all"] = "tonne_prices"


class RefineConfig(BaseModel):
    system_prompt_id: int | None = None      # Prompt für die Nachbearbeitung (nötig)
    ai_config_id: int | None = None          # None = bisherige/Standard-KI
    include_images: bool = True              # PDF-Seiten mitsenden (sonst nur die bisherigen Daten)
    format_prompt_id: int | None = None      # Prompt vom Typ „Antwortformat“ (None = Standardformat)


BranchField = Literal["document_type", "supplier_name", "page_count", "invoice_page_count", "validation_status"]
BranchOp = Literal["equals", "not_equals", "contains", "gt", "lt", "in"]

# Welche Operatoren je Feld sinnvoll sind
ALLOWED_OPS: dict[str, tuple[str, ...]] = {
    "document_type": ("equals", "not_equals", "in", "contains"),
    "supplier_name": ("equals", "not_equals", "contains", "in"),
    "page_count": ("equals", "not_equals", "gt", "lt", "in"),
    "invoice_page_count": ("equals", "not_equals", "gt", "lt", "in"),
    "validation_status": ("equals", "not_equals", "in"),
}


class BranchConfig(BaseModel):
    field: BranchField = "document_type"
    op: BranchOp = "equals"
    value: str = ""


CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "start": EmptyConfig, "end": EmptyConfig, "trim_pages": TrimConfig,
    "detect_type": DetectConfig, "extract": ExtractConfig, "schema_extract": SchemaExtractConfig, "classify_pages": ClassifyPagesConfig,
    "validate": ValidateConfig, "branch": BranchConfig,
    "normalize_units": NormalizeConfig, "ai_refine": RefineConfig,
}

# Ausgänge je Knotentyp
def output_handles(node_type: str) -> tuple[str, ...]:
    if node_type == "end":
        return ()
    return ("true", "false") if node_type == "branch" else ("out",)


# ─── Bedingungen ──────────────────────────────────────────────────────────────

def _norm(v: Any) -> str:
    return str(v).strip().casefold()


def _split(value: str) -> list[str]:
    return [p.strip().casefold() for p in value.split(",") if p.strip()]


def evaluate_condition(cfg: BranchConfig, facts: dict[str, Any]) -> bool:
    """
    Wertet eine Verzweigungs-Bedingung aus.

    facts: document_type_id, document_type_name, supplier_name, page_count, validation_status.
    Fehlt der Wert (None): equals/contains/gt/lt/in → False, not_equals → True.
    """
    op, value = cfg.op, cfg.value

    if cfg.field == "document_type":
        candidates = [_norm(x) for x in (facts.get("document_type_id"), facts.get("document_type_name"))
                      if x is not None and str(x).strip()]
        return _text_op(op, candidates, value)

    if cfg.field in ("page_count", "invoice_page_count"):
        actual = facts.get(cfg.field)
        if actual is None:
            return op == "not_equals"
        try:
            if op in ("gt", "lt"):
                limit = float(value.replace(",", "."))
                return actual > limit if op == "gt" else actual < limit
            if op in ("in",):
                return any(float(p) == actual for p in _split(value))
            return (float(value.replace(",", ".")) == actual) == (op == "equals")
        except ValueError:
            return False

    fact = facts.get("supplier_name" if cfg.field == "supplier_name" else "validation_status")
    return _text_op(op, [_norm(fact)] if fact is not None and str(fact).strip() else [], value)


def _text_op(op: str, candidates: list[str], value: str) -> bool:
    if not candidates:
        return op == "not_equals"
    wanted = _norm(value)
    if op == "equals":
        return wanted in candidates
    if op == "not_equals":
        return wanted not in candidates
    if op == "contains":
        return bool(wanted) and any(wanted in c for c in candidates)
    if op == "in":
        return any(c in _split(value) for c in candidates)
    return False


# ─── Validierung ──────────────────────────────────────────────────────────────

@dataclass
class GraphReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _label(node: dict) -> str:
    data = node.get("data") or {}
    return str(data.get("label") or node.get("type") or node.get("id"))


def parse_config(node_type: str, raw: Any) -> BaseModel:
    """Validiert/ergänzt die Konfiguration eines Knotens (Standardwerte). Wirft ValidationError."""
    return CONFIG_MODELS[node_type].model_validate(raw or {})


def validate_graph(graph: Any) -> GraphReport:
    """Prüft Struktur, Konfiguration, Verbindungen und Zyklen eines Workflow-Graphen."""
    rep = GraphReport()
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) \
            or not isinstance(graph.get("edges", []), list):
        rep.errors.append("Ungültiges Format: erwartet {nodes: [...], edges: [...]}")
        return rep
    nodes, edges = graph["nodes"], graph.get("edges", [])
    if not nodes:
        rep.errors.append("Der Workflow enthält keine Knoten")
        return rep

    by_id: dict[str, dict] = {}
    for node in nodes:
        nid = node.get("id") if isinstance(node, dict) else None
        if not nid or not isinstance(nid, str):
            rep.errors.append("Ein Knoten hat keine gültige ID")
            continue
        if nid in by_id:
            rep.errors.append(f"Knoten-ID '{nid}' ist doppelt vergeben")
            continue
        by_id[nid] = node
        ntype = node.get("type")
        if ntype not in NODE_TYPES:
            rep.errors.append(f"Knoten '{_label(node)}': unbekannter Typ '{ntype}'")
            continue
        cfg_raw = (node.get("data") or {}).get("config")
        try:
            cfg = parse_config(ntype, cfg_raw)
        except ValidationError as exc:
            for err in exc.errors():
                where = ".".join(str(p) for p in err["loc"])
                rep.errors.append(f"Knoten '{_label(node)}': {where}: {err['msg']}")
            continue
        if ntype == "branch":
            if cfg.op not in ALLOWED_OPS[cfg.field]:           # type: ignore[attr-defined]
                rep.errors.append(
                    f"Verzweigung '{_label(node)}': Operator '{cfg.op}' passt nicht zu "  # type: ignore[attr-defined]
                    f"Feld '{cfg.field}'")                                                # type: ignore[attr-defined]
            if cfg.op in ("gt", "lt") or cfg.field == "page_count":                       # type: ignore[attr-defined]
                try:
                    [float(p.replace(",", ".")) for p in (_split(cfg.value) or [""])]       # type: ignore[attr-defined]
                except ValueError:
                    rep.errors.append(f"Verzweigung '{_label(node)}': Wert muss eine Zahl sein")
            elif not cfg.value.strip():                                                    # type: ignore[attr-defined]
                rep.errors.append(f"Verzweigung '{_label(node)}': Bitte einen Vergleichswert angeben")

    starts = [n for n in by_id.values() if n.get("type") == "start"]
    if len(starts) != 1:
        rep.errors.append(f"Es muss genau einen Start-Knoten geben (gefunden: {len(starts)})")

    # Kanten
    outgoing: dict[tuple[str, str], str] = {}
    incoming_count: dict[str, int] = {}
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        if src not in by_id or tgt not in by_id:
            rep.errors.append("Eine Verbindung zeigt auf einen nicht vorhandenen Knoten")
            continue
        if src == tgt:
            rep.errors.append(f"'{_label(by_id[src])}' ist mit sich selbst verbunden")
            continue
        stype = by_id[src].get("type")
        handle = edge.get("sourceHandle") or "out"
        if stype == "end":
            rep.errors.append("Vom Ende-Knoten darf keine Verbindung ausgehen")
            continue
        if handle not in output_handles(stype):
            rep.errors.append(f"Verbindung von '{_label(by_id[src])}': ungültiger Ausgang '{handle}'")
            continue
        if (src, handle) in outgoing:
            rep.errors.append(
                f"'{_label(by_id[src])}' hat mehrere Verbindungen am Ausgang "
                f"'{handle}' — es ist nur eine erlaubt")
            continue
        outgoing[(src, handle)] = tgt
        incoming_count[tgt] = incoming_count.get(tgt, 0) + 1

    if len(starts) == 1 and incoming_count.get(starts[0]["id"]):
        rep.errors.append("In den Start-Knoten darf keine Verbindung hineinführen")

    if rep.errors:
        return rep

    # Zyklen (Iterative DFS über die bereits geprüften Kanten)
    adjacency: dict[str, list[str]] = {}
    for (src, _h), tgt in outgoing.items():
        adjacency.setdefault(src, []).append(tgt)
    state: dict[str, int] = {}
    for root in by_id:
        if state.get(root):
            continue
        stack = [(root, iter(adjacency.get(root, [])))]
        state[root] = 1
        while stack:
            node_id, it = stack[-1]
            for nxt in it:
                if state.get(nxt) == 1:
                    rep.errors.append(f"Der Workflow enthält eine Schleife bei '{_label(by_id[nxt])}'")
                    return rep
                if not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(adjacency.get(nxt, []))))
                    break
            else:
                state[node_id] = 2
                stack.pop()

    # Warnungen
    start_id = starts[0]["id"]
    reachable = {start_id}
    todo = [start_id]
    while todo:
        for nxt in adjacency.get(todo.pop(), []):
            if nxt not in reachable:
                reachable.add(nxt)
                todo.append(nxt)
    for nid, node in by_id.items():
        if nid not in reachable:
            rep.warnings.append(f"'{_label(node)}' ist vom Start aus nicht erreichbar und wird nie ausgeführt")
    for nid, node in by_id.items():
        if node.get("type") == "branch":
            for handle in ("true", "false"):
                if (nid, handle) not in outgoing:
                    rep.warnings.append(
                        f"Verzweigung '{_label(node)}': Ausgang '{'Ja' if handle == 'true' else 'Nein'}' "
                        "ist nicht verbunden — der Ablauf endet dort")
    if not any(n.get("type") in ("extract", "schema_extract") and nid in reachable for nid, n in by_id.items()):
        rep.warnings.append("Es wird keine Rechnungsextraktion ausgeführt")
    _warn_validate_before_extract(rep, by_id, outgoing, start_id)
    return rep


def _warn_validate_before_extract(rep: GraphReport, by_id: dict, outgoing: dict, start_id: str) -> None:
    """Warnt, wenn auf einem Pfad eine Plausibilitätsprüfung ohne vorherige Extraktion liegt."""
    seen: set[tuple[str, bool]] = set()
    stack = [(start_id, False)]
    flagged: set[str] = set()
    while stack:
        nid, extracted = stack.pop()
        if (nid, extracted) in seen:
            continue
        seen.add((nid, extracted))
        ntype = by_id[nid].get("type")
        if ntype in DATA_NODES and not extracted and nid not in flagged:
            flagged.add(nid)
            rep.warnings.append(f"'{_label(by_id[nid])}' liegt vor jeder Extraktion und wird übersprungen")
        extracted = extracted or ntype in ("extract", "schema_extract")
        for handle in output_handles(ntype):
            nxt = outgoing.get((nid, handle))
            if nxt:
                stack.append((nxt, extracted))


# ─── Ablauf ───────────────────────────────────────────────────────────────────

@dataclass
class NodeResult:
    handle: str = "out"           # "true"/"false" bei Verzweigungen
    detail: str | None = None     # Kurzbeschreibung für das Ablaufprotokoll


Handler = Callable[[BaseModel, Any], Awaitable["NodeResult | None"]]


class WorkflowError(Exception):
    """Fehler in einem Knoten; `trace` enthält den Ablauf bis einschließlich des Fehlers."""

    def __init__(self, node: dict, message: str, trace: list[dict]):
        super().__init__(message)
        self.node_id = node.get("id")
        self.node_label = _label(node)
        self.trace = trace


def diff_snapshots(before: dict, after: dict, limit: int = 200) -> list[dict]:
    """
    Vergleicht zwei Daten-Schnappschüsse (Felder, Positionen, Prüfstatus …) und liefert die Änderungen eines Knotens
    als [{path, from, to}] — leer = der Knoten hat nichts verändert.
    """
    changes: list[dict] = []

    def add(path: str, a: Any, b: Any) -> None:
        if a != b and len(changes) < limit:
            changes.append({"path": path, "from": a, "to": b})

    for key in ("document_type", "page_count", "validation_status"):
        add(key, before.get(key), after.get(key))
    bf, af = before.get("fields") or {}, after.get("fields") or {}
    for k in sorted(set(bf) | set(af)):
        add(f"Feld {k}", bf.get(k), af.get(k))
    bp, ap = before.get("positions") or [], after.get("positions") or []
    if len(bp) != len(ap):
        add("Anzahl Positionen", len(bp), len(ap))
    for i in range(min(len(bp), len(ap))):
        for k in sorted(set(bp[i]) | set(ap[i])):
            add(f"Position {i + 1} · {k}", bp[i].get(k), ap[i].get(k))
    return changes


async def run_graph(
    graph: dict, handlers: dict[str, Handler], ctx: Any,
    snapshot: Callable[[Any], dict] | None = None,
) -> list[dict]:
    """
    Führt den Workflow aus und gibt das Ablaufprotokoll zurück
    ([{node_id, type, label, status, duration_s, detail, branch, input, output, changes, io}, ...]).
    Mit `snapshot` (ctx → dict) wird je Knoten der Datenstand davor/danach und die Differenz festgehalten;
    Prompts/Antworten und Hinweise, die ein Knoten in `ctx.node_io` ablegt, landen unter `io`.
    Wirft WorkflowError, wenn ein Knoten eine Exception auslöst.
    """
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges: dict[tuple[str, str], str] = {}
    for e in graph.get("edges", []):
        edges[(e["source"], e.get("sourceHandle") or "out")] = e["target"]
    current: str | None = next(n["id"] for n in graph["nodes"] if n["type"] == "start")
    trace: list[dict] = []

    for _ in range(MAX_STEPS):
        if current is None:
            return trace
        node = nodes[current]
        ntype = node["type"]
        cfg = parse_config(ntype, (node.get("data") or {}).get("config"))
        entry: dict[str, Any] = {
            "node_id": current, "type": ntype, "label": _label(node),
            "status": "ok", "duration_s": 0.0, "detail": None, "branch": None,
        }
        before = _safe_snapshot(snapshot, ctx)
        if hasattr(ctx, "node_io"):
            ctx.node_io = {}
        entry["config"] = cfg.model_dump()
        started = time.monotonic()
        try:
            result = await handlers[ntype](cfg, ctx)
        except Exception as exc:
            entry.update(status="error", detail=str(exc), duration_s=round(time.monotonic() - started, 2))
            _attach_io(entry, before, _safe_snapshot(snapshot, ctx), ctx)
            trace.append(entry)
            raise WorkflowError(node, str(exc), trace) from exc
        entry["duration_s"] = round(time.monotonic() - started, 2)
        _attach_io(entry, before, _safe_snapshot(snapshot, ctx), ctx)
        result = result or NodeResult()
        entry["detail"] = result.detail
        if ntype == "branch":
            entry["branch"] = result.handle
        trace.append(entry)
        current = edges.get((current, result.handle))
    raise WorkflowError(nodes[current] if current else {}, f"Abbruch nach {MAX_STEPS} Schritten", trace)


def _safe_snapshot(snapshot: Callable[[Any], dict] | None, ctx: Any) -> dict | None:
    if snapshot is None:
        return None
    try:
        return snapshot(ctx)
    except Exception:           # Protokoll darf den Ablauf nie stören
        return None


def _attach_io(entry: dict, before: dict | None, after: dict | None, ctx: Any) -> None:
    if before is not None and after is not None:
        entry["input"], entry["output"] = before, after
        entry["changes"] = diff_snapshots(before, after)
    io = getattr(ctx, "node_io", None)
    if io:
        entry["io"] = io


# ─── Vorlagen ─────────────────────────────────────────────────────────────────

def _node(nid: str, ntype: str, label: str, x: int, y: int, config: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "position": {"x": x, "y": y},
            "data": {"label": label, "config": config or {}}}


def _edge(src: str, tgt: str, handle: str | None = None) -> dict:
    return {"id": f"e-{src}-{handle or 'out'}-{tgt}", "source": src, "target": tgt, "sourceHandle": handle}


def templates() -> list[dict]:
    """Mitgelieferte Vorlagen: [{key, name, description, graph}]."""
    standard = {
        "nodes": [
            _node("start", "start", "Start", 0, 160),
            _node("detect", "detect_type", "Dokumententyp erkennen", 220, 160),
            _node("branch", "branch", "Eingangsrechnung?", 480, 160,
                  {"field": "document_type", "op": "equals", "value": "1"}),
            _node("extract", "extract", "Rechnungsdaten extrahieren", 740, 60),
            _node("validate", "validate", "Plausibilität prüfen", 1000, 60),
            _node("end", "end", "Fertig", 1240, 60),
            _node("end-other", "end", "Keine Rechnung — fertig", 740, 260),
        ],
        "edges": [
            _edge("start", "detect"), _edge("detect", "branch"),
            _edge("branch", "extract", "true"), _edge("branch", "end-other", "false"),
            _edge("extract", "validate"), _edge("validate", "end"),
        ],
    }
    direct = {
        "nodes": [
            _node("start", "start", "Start", 0, 60),
            _node("extract", "extract", "Rechnungsdaten extrahieren", 240, 60),
            _node("validate", "validate", "Plausibilität prüfen", 500, 60),
            _node("end", "end", "Fertig", 740, 60),
        ],
        "edges": [_edge("start", "extract"), _edge("extract", "validate"), _edge("validate", "end")],
    }
    trimmed = {
        "nodes": [
            _node("start", "start", "Start", 0, 160),
            _node("trim", "trim_pages", "Auf erste Seite kürzen", 220, 160, {"max_pages": 1}),
            _node("detect", "detect_type", "Dokumententyp erkennen", 480, 160),
            _node("branch", "branch", "Eingangsrechnung?", 740, 160,
                  {"field": "document_type", "op": "equals", "value": "1"}),
            _node("extract", "extract", "Rechnungsdaten extrahieren", 1000, 60),
            _node("validate", "validate", "Plausibilität prüfen", 1260, 60),
            _node("end", "end", "Fertig", 1500, 60),
            _node("end-other", "end", "Keine Rechnung — fertig", 1000, 260),
        ],
        "edges": [
            _edge("start", "trim"), _edge("trim", "detect"), _edge("detect", "branch"),
            _edge("branch", "extract", "true"), _edge("branch", "end-other", "false"),
            _edge("extract", "validate"), _edge("validate", "end"),
        ],
    }
    tonnes = {
        "nodes": [
            _node("start", "start", "Start", 0, 60),
            _node("extract", "extract", "Rechnungsdaten extrahieren", 240, 60),
            _node("validate", "validate", "Plausibilität prüfen", 500, 60),
            _node("units", "normalize_units", "Gewichte in Tonnen", 760, 60, {"target": "t"}),
            _node("end", "end", "Fertig", 1000, 60),
        ],
        "edges": [_edge("start", "extract"), _edge("extract", "validate"), _edge("validate", "units"),
                  _edge("units", "end")],
    }
    schema = {
        "nodes": [
            _node("start", "start", "Start", 0, 60),
            _node("extract", "schema_extract", "Schema-Extraktion", 240, 60),
            _node("end", "end", "Fertig", 500, 60),
        ],
        "edges": [_edge("start", "extract"), _edge("extract", "end")],
    }
    schema_trimmed = {
        "nodes": [
            _node("start", "start", "Start", 0, 60),
            _node("trim", "trim_pages", "Auf erste Seite kürzen", 220, 60, {"max_pages": 1}),
            _node("extract", "schema_extract", "Schema-Extraktion", 480, 60),
            _node("end", "end", "Fertig", 740, 60),
        ],
        "edges": [_edge("start", "trim"), _edge("trim", "extract"), _edge("extract", "end")],
    }
    pages = {
        "nodes": [
            _node("start", "start", "Start", 0, 160),
            _node("pages", "classify_pages", "Seiten erkennen", 220, 160, {"action": "split_invoices"}),
            _node("branch", "branch", "Rechnungsseiten gefunden?", 480, 160,
                  {"field": "invoice_page_count", "op": "gt", "value": "0"}),
            _node("extract", "schema_extract", "Schema-Extraktion", 740, 60),
            _node("end", "end", "Fertig", 1000, 60),
            _node("end-other", "end", "Keine Rechnung — fertig", 740, 260),
        ],
        "edges": [
            _edge("start", "pages"), _edge("pages", "branch"),
            _edge("branch", "extract", "true"), _edge("branch", "end-other", "false"),
            _edge("extract", "end"),
        ],
    }
    return [
        {"key": "pages_schema", "name": "Seiten erkennen + Schema-Extraktion",
         "description": "Erkennt je Seite, ob sie zur Rechnung gehört, behält nur die Rechnungsseiten (weitere Rechnungen der PDF werden eigene Belege) und extrahiert nach dem Schema. Bei unsicherer Erkennung: Nachbearbeiten. Für gescannte Sammel-PDFs.",
         "graph": pages},
        {"key": "schema", "name": "Schema-Extraktion",
         "description": "Extrahiert nach der aktiven Schema-Definition (Structured Outputs) und speichert die Datensätze.",
         "graph": schema},
        {"key": "schema_trimmed", "name": "Schema-Extraktion, erst kürzen",
         "description": "Kürzt die PDF auf die erste Seite und extrahiert dann nach dem Schema.",
         "graph": schema_trimmed},
        {"key": "standard", "name": "Standard (wie bisher)",
         "description": "Dokumententyp erkennen → nur bei Eingangsrechnung extrahieren und prüfen.",
         "graph": standard},
        {"key": "direct", "name": "Nur Extraktion",
         "description": "Ohne Typerkennung: sofort extrahieren und prüfen.",
         "graph": direct},
        {"key": "tonnes", "name": "Extraktion + Gewichte in Tonnen",
         "description": "Extrahieren, prüfen und alle Gewichte (kg/g) einheitlich auf Tonnen umrechnen.",
         "graph": tonnes},
        {"key": "trimmed", "name": "Erst kürzen, dann analysieren",
         "description": "Für lange PDFs, bei denen nur die erste Seite die Rechnung enthält.",
         "graph": trimmed},
    ]


def has_page_reduction(graph: dict) -> bool:
    """True, wenn ein Knoten „Seiten erkennen“ mit Aktion „nur Rechnungsseiten behalten“ im Ablauf liegt."""
    for node in graph.get("nodes", []):
        if node.get("type") == "classify_pages" and ((node.get("data") or {}).get("config") or {}).get("action") in ("keep_invoice_pages", "split_invoices"):
            return True
    return False


def leading_trim_pages(graph: dict) -> int | None:
    """
    Seitenlimit der „PDF kürzen"-Knoten direkt am Anfang des Ablaufs (Start → Kürzen → …), sonst None.

    Der Worker braucht das vor der Kapazitätsprüfung: eine 100-seitige PDF, die der Workflow gleich auf
    3 Seiten kürzt, darf nicht als „zu groß für alle KI-Server" hängen bleiben. Knoten hinter einer Weiche
    zählen nicht (es ist nicht sicher, dass sie laufen).
    """
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = {(e["source"], e.get("sourceHandle") or "out"): e["target"] for e in graph.get("edges", [])}
    start = next((n["id"] for n in graph.get("nodes", []) if n.get("type") == "start"), None)
    current = edges.get((start, "out")) if start else None
    limit: int | None = None
    seen: set[str] = set()
    while current and current not in seen and nodes.get(current, {}).get("type") == "trim_pages":
        seen.add(current)
        cfg = parse_config("trim_pages", (nodes[current].get("data") or {}).get("config"))
        limit = cfg.max_pages if limit is None else min(limit, cfg.max_pages)
        current = edges.get((current, "out"))
    return limit
