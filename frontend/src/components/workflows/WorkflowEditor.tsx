"use client";

import "@xyflow/react/dist/style.css";

import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import {
  AIConfig, DocumentType, SystemPrompt, Workflow, WorkflowGraph, WorkflowNodeConfig,
  WorkflowNodeType, WorkflowValidation, extractApiError, workflowsApi,
} from "@/lib/api";
import {
  ALLOWED_OPS, BRANCH_FIELDS, BRANCH_OPS, NODE_META, NODE_ORDER, VALUE_HINTS,
} from "./nodeMeta";

// ─── Typen / Kontext ─────────────────────────────────────────────────────────

type FlowData = { label: string; config: WorkflowNodeConfig };
type FlowNode = Node<FlowData>;

interface Lookups {
  prompts: SystemPrompt[];
  aiConfigs: AIConfig[];
}
const LookupContext = createContext<Lookups>({ prompts: [], aiConfigs: [] });

// ─── Hilfen: Graph ↔ React Flow ──────────────────────────────────────────────

function edgeStyle(handle: string | null | undefined): Partial<Edge> {
  const color = handle === "true" ? "#16a34a" : handle === "false" ? "#dc2626" : "#64748b";
  return {
    label: handle === "true" ? "Ja" : handle === "false" ? "Nein" : undefined,
    style: { stroke: color, strokeWidth: 2 },
    labelStyle: { fill: color, fontWeight: 700 },
    markerEnd: { type: MarkerType.ArrowClosed, color },
  };
}

function toFlow(graph: WorkflowGraph): { nodes: FlowNode[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: n.position,
      data: { label: n.data.label, config: n.data.config ?? {} },
      deletable: n.type !== "start",
    })),
    edges: graph.edges.map((e) => ({
      id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? null,
      ...edgeStyle(e.sourceHandle),
    })),
  };
}

function toGraph(nodes: FlowNode[], edges: Edge[]): WorkflowGraph {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type as WorkflowNodeType,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: { label: n.data.label, config: n.data.config },
    })),
    edges: edges.map((e) => ({
      id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle ?? null,
    })),
  };
}

/** Würde eine Kante source → target einen Zyklus erzeugen? (target erreicht source) */
function createsCycle(edges: Edge[], source: string, target: string): boolean {
  const adj = new Map<string, string[]>();
  edges.forEach((e) => adj.set(e.source, [...(adj.get(e.source) ?? []), e.target]));
  const seen = new Set<string>();
  const stack = [target];
  while (stack.length) {
    const n = stack.pop()!;
    if (n === source) return true;
    if (seen.has(n)) continue;
    seen.add(n);
    (adj.get(n) ?? []).forEach((x) => stack.push(x));
  }
  return false;
}

// ─── Knoten-Karte ────────────────────────────────────────────────────────────

function summary(type: WorkflowNodeType, cfg: WorkflowNodeConfig, lookups: Lookups): string {
  const promptName = (id?: number | null, fallback = "Standard-Prompt") =>
    id ? lookups.prompts.find((p) => p.id === id)?.name ?? `Prompt #${id} (fehlt!)` : fallback;
  switch (type) {
    case "trim_pages":
      return `nur die ersten ${cfg.max_pages ?? 1} Seite(n)`;
    case "detect_type":
      return promptName(cfg.system_prompt_id, "Dokumententyp-Prompt (Einstellungen)");
    case "extract": {
      const ai = cfg.ai_config_id
        ? lookups.aiConfigs.find((c) => c.id === cfg.ai_config_id)?.name ?? `KI #${cfg.ai_config_id} (fehlt!)`
        : null;
      return `${promptName(cfg.system_prompt_id)}${ai ? ` · ${ai}` : ""}`;
    }
    case "validate":
      return [cfg.auto_correct ? "Auto-Korrektur" : null, cfg.ai_recheck ? "KI-Nachprüfung" : null]
        .filter(Boolean).join(" · ") || "nur prüfen";
    case "normalize_units":
      return `Gewichte → ${cfg.target ?? "t"}${(cfg.target ?? "t") === "t" && (cfg.mode ?? "tonne_prices") === "tonne_prices" ? " (nur Preis je t)" : ""}`;
    case "ai_refine":
      return `${promptName(cfg.system_prompt_id, "Prompt fehlt!")}${cfg.include_images === false ? " · ohne Bilder" : ""}`;
    case "branch":
      return `${BRANCH_FIELDS[cfg.field ?? "document_type"]} ${BRANCH_OPS[cfg.op ?? "equals"]} ${cfg.value || "…"}`;
    default:
      return "";
  }
}

function WorkflowNodeCard({ type, data, selected }: NodeProps<FlowNode>) {
  const lookups = useContext(LookupContext);
  const t = type as WorkflowNodeType;
  const meta = NODE_META[t];
  const text = summary(t, data.config, lookups);
  return (
    <div
      className={`w-56 overflow-hidden rounded-lg border-2 bg-white shadow-md ${meta.border} ${
        selected ? "ring-2 ring-blue-500 ring-offset-2" : ""
      }`}
      data-testid={`node-${t}`}
    >
      {t !== "start" && (
        <Handle type="target" position={Position.Left} className="!h-3 !w-3 !border-2 !border-white !bg-slate-500" />
      )}
      <div className={`px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide ${meta.head}`}>
        {meta.label}
      </div>
      <div className="px-3 py-2">
        <div className="text-sm font-medium text-gray-900">{data.label}</div>
        {text && <div className="mt-0.5 text-xs text-gray-500">{text}</div>}
      </div>
      {t === "branch" ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: "30%" }}
            className="!h-3 !w-3 !border-2 !border-white !bg-green-600" />
          <Handle id="false" type="source" position={Position.Right} style={{ top: "75%" }}
            className="!h-3 !w-3 !border-2 !border-white !bg-red-600" />
          <span className="pointer-events-none absolute right-4 text-[10px] font-bold text-green-700" style={{ top: "22%" }}>Ja</span>
          <span className="pointer-events-none absolute right-4 text-[10px] font-bold text-red-700" style={{ top: "67%" }}>Nein</span>
        </>
      ) : (
        t !== "end" && (
          <Handle type="source" position={Position.Right} className="!h-3 !w-3 !border-2 !border-white !bg-slate-500" />
        )
      )}
    </div>
  );
}

const nodeTypes = Object.fromEntries(
  (Object.keys(NODE_META) as WorkflowNodeType[]).map((t) => [t, WorkflowNodeCard]),
);

// ─── Eigenschaften-Panel ─────────────────────────────────────────────────────

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="mb-3 block">
      <span className="mb-1 block text-xs font-medium text-gray-600">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-gray-400">{hint}</span>}
    </label>
  );
}

const inputCls = "w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none";

function Inspector({
  node, onChange, docTypes,
}: {
  node: FlowNode;
  onChange: (patch: { label?: string; config?: Partial<WorkflowNodeConfig> }) => void;
  docTypes: DocumentType[];
}) {
  const { prompts, aiConfigs } = useContext(LookupContext);
  const t = node.type as WorkflowNodeType;
  const cfg = node.data.config;
  const meta = NODE_META[t];
  const num = (v: string) => (v === "" ? null : Number(v));

  return (
    <div>
      <div className={`mb-3 rounded px-2 py-1 text-xs font-semibold uppercase tracking-wide ${meta.head}`}>{meta.label}</div>
      <p className="mb-4 text-xs text-gray-500">{meta.description}</p>

      <Field label="Bezeichnung">
        <input className={inputCls} value={node.data.label} onChange={(e) => onChange({ label: e.target.value })} />
      </Field>

      {t === "trim_pages" && (
        <Field label="Nur die ersten … Seiten behalten" hint="Hat die PDF weniger Seiten, passiert nichts.">
          <input type="number" min={1} max={500} className={inputCls} value={cfg.max_pages ?? 1}
            onChange={(e) => onChange({ config: { max_pages: Math.max(1, Number(e.target.value) || 1) } })} />
        </Field>
      )}

      {t === "detect_type" && (
        <Field label="Systemprompt" hint="Leer = Dokumententyp-Prompt aus den Einstellungen.">
          <select className={inputCls} value={cfg.system_prompt_id ?? ""}
            onChange={(e) => onChange({ config: { system_prompt_id: num(e.target.value) } })}>
            <option value="">Standard (Dokumententyp-Prompt)</option>
            {prompts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </Field>
      )}

      {t === "extract" && (
        <>
          <Field label="Systemprompt" hint="Leer = in der Analyse gewählter bzw. Standard-Prompt.">
            <select className={inputCls} value={cfg.system_prompt_id ?? ""}
              onChange={(e) => onChange({ config: { system_prompt_id: num(e.target.value) } })}>
              <option value="">Wie in der Analyse (Standard)</option>
              {prompts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="Antwortformat" hint="Prompt vom Typ „Antwortformat“ (Einstellungen → Systemprompts). Leer = Standardformat, falls der Prompt die JSON-Struktur nicht selbst beschreibt.">
            <select className={inputCls} value={cfg.format_prompt_id ?? ""}
              onChange={(e) => onChange({ config: { format_prompt_id: num(e.target.value) } })}>
              <option value="">Automatisch (Standardformat)</option>
              {prompts.filter((p) => p.type === 3).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="KI-Konfiguration" hint="Leer = in der Analyse gewählte bzw. Standard-KI.">
            <select className={inputCls} value={cfg.ai_config_id ?? ""}
              onChange={(e) => onChange({ config: { ai_config_id: num(e.target.value) } })}>
              <option value="">Wie in der Analyse (Standard)</option>
              {aiConfigs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
        </>
      )}

      {t === "schema_extract" && (
        <>
          <Field label="Schema-Version" hint="Leer = aktive Version. Eine feste Version macht den Workflow reproduzierbar.">
            <input type="number" min={1} className={inputCls} value={cfg.schema_version_id ?? ""} placeholder="aktive Version"
              onChange={(e) => onChange({ config: { schema_version_id: num(e.target.value) } })} />
          </Field>
          <Field label="Zusatzanweisung (Baustein)" hint="Optionaler Systemprompt, der hinter den generierten Prompt gehängt wird (z. B. Besonderheiten Ihrer Lieferanten).">
            <select className={inputCls} value={cfg.system_prompt_id ?? ""}
              onChange={(e) => onChange({ config: { system_prompt_id: num(e.target.value) } })}>
              <option value="">Keine</option>
              {prompts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="KI-Konfiguration" hint="Leer = in der Analyse gewählte KI.">
            <select className={inputCls} value={cfg.ai_config_id ?? ""}
              onChange={(e) => onChange({ config: { ai_config_id: num(e.target.value) } })}>
              <option value="">Wie in der Analyse</option>
              {aiConfigs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
        </>
      )}

      {t === "classify_pages" && (
        <>
          <Field label="Ergebnis" hint="„Behalten“ und „Trennen“ legen eine neue PDF-Version an (das Original bleibt erhalten) — aber nur, wenn die Erkennung eindeutig ist. Sonst bleibt die PDF unverändert und der Beleg kommt zur Nachbearbeitung.">
            <select className={inputCls} value={cfg.action ?? "classify_only"}
              onChange={(e) => onChange({ config: { action: e.target.value as "classify_only" | "keep_invoice_pages" | "split_invoices" } })}>
              <option value="classify_only">Nur erkennen (PDF bleibt)</option>
              <option value="keep_invoice_pages">Nur Rechnungsseiten behalten</option>
              <option value="split_invoices">Rechnungen trennen (jede weitere Rechnung wird ein eigener Beleg)</option>
            </select>
          </Field>
          <Field label="Auflösung (dpi)" hint="Niedrig genügt zum Erkennen und spart Token.">
            <input type="number" min={40} max={200} className={inputCls} value={cfg.dpi ?? 80}
              onChange={(e) => onChange({ config: { dpi: Math.min(200, Math.max(40, Number(e.target.value) || 80)) } })} />
          </Field>
          <Field label="Seiten je KI-Anfrage">
            <input type="number" min={1} max={30} className={inputCls} value={cfg.chunk_size ?? 10}
              onChange={(e) => onChange({ config: { chunk_size: Math.min(30, Math.max(1, Number(e.target.value) || 10)) } })} />
          </Field>
          <Field label="KI-Konfiguration" hint="Leer = in der Analyse gewählte KI.">
            <select className={inputCls} value={cfg.ai_config_id ?? ""}
              onChange={(e) => onChange({ config: { ai_config_id: num(e.target.value) } })}>
              <option value="">Wie in der Analyse</option>
              {aiConfigs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
        </>
      )}

      {t === "validate" && (
        <>
          <label className="mb-2 flex items-start gap-2 text-sm text-gray-700">
            <input type="checkbox" className="mt-0.5" checked={cfg.auto_correct ?? true}
              onChange={(e) => onChange({ config: { auto_correct: e.target.checked } })} />
            <span>Einheitenfehler automatisch korrigieren<span className="block text-xs text-gray-400">z.B. Tonne ↔ kg, nur wenn eindeutig</span></span>
          </label>
          <label className="mb-2 flex items-start gap-2 text-sm text-gray-700">
            <input type="checkbox" className="mt-0.5" checked={cfg.ai_recheck ?? true}
              onChange={(e) => onChange({ config: { ai_recheck: e.target.checked } })} />
            <span>Bei Auffälligkeit zweite KI-Abfrage<span className="block text-xs text-gray-400">sonst Markierung zur manuellen Prüfung</span></span>
          </label>
        </>
      )}

      {t === "normalize_units" && (
        <Field label="Gewichte umrechnen auf" hint="Menge, Einheit und Einzelpreis (€ je Einheit) der Positionen werden umgerechnet. Wirkt nach einer Extraktion.">
          <select className={inputCls} value={cfg.target ?? "t"}
            onChange={(e) => onChange({ config: { target: e.target.value as "t" | "kg" } })}>
            <option value="t">Tonnen (t)</option>
            <option value="kg">Kilogramm (kg)</option>
          </select>
        </Field>
      )}
      {t === "normalize_units" && (cfg.target ?? "t") === "t" && (
        <Field label="Welche Positionen?" hint="Preise je kg oder je g sollten nicht in Tonnen umgerechnet werden (z. B. Kleinmengen).">
          <select className={inputCls} value={cfg.mode ?? "tonne_prices"}
            onChange={(e) => onChange({ config: { mode: e.target.value as "tonne_prices" | "all" } })}>
            <option value="tonne_prices">Nur Positionen mit Preis je Tonne (empfohlen)</option>
            <option value="all">Alle Gewichtspositionen</option>
          </select>
        </Field>
      )}

      {t === "ai_refine" && (
        <>
          <Field label="Systemprompt (Anweisung)" hint="Pflicht. Das bisherige Ergebnis (JSON) wird automatisch angehängt; die KI soll im selben Format antworten.">
            <select className={inputCls} value={cfg.system_prompt_id ?? ""}
              onChange={(e) => onChange({ config: { system_prompt_id: num(e.target.value) } })}>
              <option value="">— bitte wählen —</option>
              {prompts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="Antwortformat" hint="Prompt vom Typ „Antwortformat“ (Einstellungen → Systemprompts). Leer = Standardformat, falls der Prompt die JSON-Struktur nicht selbst beschreibt.">
            <select className={inputCls} value={cfg.format_prompt_id ?? ""}
              onChange={(e) => onChange({ config: { format_prompt_id: num(e.target.value) } })}>
              <option value="">Automatisch (Standardformat)</option>
              {prompts.filter((p) => p.type === 3).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="KI-Konfiguration" hint="Leer = bisherige bzw. Standard-KI.">
            <select className={inputCls} value={cfg.ai_config_id ?? ""}
              onChange={(e) => onChange({ config: { ai_config_id: num(e.target.value) } })}>
              <option value="">Wie bisher (Standard)</option>
              {aiConfigs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </Field>
          <label className="mb-2 flex items-start gap-2 text-sm text-gray-700">
            <input type="checkbox" className="mt-0.5" checked={cfg.include_images ?? true}
              onChange={(e) => onChange({ config: { include_images: e.target.checked } })} />
            <span>PDF-Seiten mitsenden<span className="block text-xs text-gray-400">aus = nur die bisherigen Daten, schneller und günstiger</span></span>
          </label>
        </>
      )}

      {t === "branch" && (() => {
        const field = cfg.field ?? "document_type";
        const ops = ALLOWED_OPS[field];
        return (
          <>
            <Field label="Wenn …">
              <select className={inputCls} value={field}
                onChange={(e) => {
                  const f = e.target.value as WorkflowNodeConfig["field"];
                  const allowed = ALLOWED_OPS[f as string];
                  onChange({ config: { field: f, op: allowed.includes(cfg.op ?? "") ? cfg.op : (allowed[0] as WorkflowNodeConfig["op"]) } });
                }}>
                {Object.entries(BRANCH_FIELDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="…">
              <select className={inputCls} value={cfg.op ?? "equals"}
                onChange={(e) => onChange({ config: { op: e.target.value as WorkflowNodeConfig["op"] } })}>
                {ops.map((o) => <option key={o} value={o}>{BRANCH_OPS[o]}</option>)}
              </select>
            </Field>
            <Field label="Wert" hint={VALUE_HINTS[field]}>
              <input className={inputCls} value={cfg.value ?? ""} list={field === "document_type" ? "doctype-options" : undefined}
                onChange={(e) => onChange({ config: { value: e.target.value } })} />
              {field === "document_type" && (
                <datalist id="doctype-options">
                  {docTypes.map((d) => <option key={d.id} value={String(d.id)}>{d.name}</option>)}
                </datalist>
              )}
            </Field>
            <p className="rounded bg-gray-50 p-2 text-xs text-gray-500">
              Grüner Ausgang (<b className="text-green-700">Ja</b>): Bedingung trifft zu.{" "}
              Roter Ausgang (<b className="text-red-700">Nein</b>): trifft nicht zu.
            </p>
          </>
        );
      })()}
    </div>
  );
}

// ─── Editor ──────────────────────────────────────────────────────────────────

export interface WorkflowEditorProps {
  workflow: Workflow;
  prompts: SystemPrompt[];
  aiConfigs: AIConfig[];
  docTypes: DocumentType[];
  onSaved: (wf: Workflow) => void;
}

function Editor({ workflow, prompts, aiConfigs, docTypes, onSaved }: WorkflowEditorProps) {
  const initial = useMemo(() => toFlow(workflow.graph), [workflow.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);
  const [name, setName] = useState(workflow.name);
  const [description, setDescription] = useState(workflow.description ?? "");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [report, setReport] = useState<WorkflowValidation | null>(null);
  const idCounter = useRef(1);
  const wrapper = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();

  const selected = nodes.find((n) => n.selected) ?? null;
  const lookups = useMemo(() => ({ prompts, aiConfigs }), [prompts, aiConfigs]);

  // Ungespeicherte Änderungen: Browser-Warnung beim Verlassen
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  // Live-Prüfung (entprellt)
  useEffect(() => {
    const timer = setTimeout(async () => {
      try { setReport(await workflowsApi.validate(toGraph(nodes, edges))); } catch { /* Hinweis nicht kritisch */ }
    }, 400);
    return () => clearTimeout(timer);
  }, [nodes, edges]);

  const markDirty = useCallback(() => { setDirty(true); setSaveMsg(null); }, []);

  const handleNodesChange: typeof onNodesChange = (changes) => {
    onNodesChange(changes);
    if (changes.some((c) => c.type !== "select" && c.type !== "dimensions")) markDirty();
  };
  const handleEdgesChange: typeof onEdgesChange = (changes) => {
    onEdgesChange(changes);
    if (changes.some((c) => c.type !== "select")) markDirty();
  };

  const isValidConnection = useCallback(
    (c: Connection | Edge) => {
      if (!c.source || !c.target || c.source === c.target) return false;
      const target = nodes.find((n) => n.id === c.target);
      if (!target || target.type === "start") return false;
      return !createsCycle(edges, c.source, c.target);
    },
    [nodes, edges],
  );

  const onConnect = useCallback(
    (c: Connection) => {
      const handle = c.sourceHandle ?? null;
      setEdges((eds) => [
        // pro Ausgang nur eine Verbindung: bestehende vom selben Ausgang ersetzen
        ...eds.filter((e) => !(e.source === c.source && (e.sourceHandle ?? null) === handle)),
        {
          id: `e-${c.source}-${handle ?? "out"}-${c.target}`,
          source: c.source!, target: c.target!, sourceHandle: handle, ...edgeStyle(handle),
        },
      ]);
      markDirty();
    },
    [setEdges, markDirty],
  );

  function addNode(type: WorkflowNodeType, position?: { x: number; y: number }) {
    const meta = NODE_META[type];
    let id = "";
    do { id = `${type}-${idCounter.current++}`; } while (nodes.some((n) => n.id === id));
    const rect = wrapper.current?.getBoundingClientRect();
    const pos = position ?? screenToFlowPosition({
      x: (rect?.left ?? 0) + (rect?.width ?? 600) / 2 + (Math.random() - 0.5) * 80,
      y: (rect?.top ?? 0) + (rect?.height ?? 400) / 2 + (Math.random() - 0.5) * 80,
    });
    setNodes((nds) => [
      ...nds.map((n) => ({ ...n, selected: false })),
      { id, type, position: pos, selected: true, data: { label: meta.label, config: { ...meta.defaultConfig } } },
    ]);
    markDirty();
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    const type = e.dataTransfer.getData("application/workflow-node") as WorkflowNodeType;
    if (!type || !NODE_META[type]) return;
    addNode(type, screenToFlowPosition({ x: e.clientX, y: e.clientY }));
  }

  function patchSelected(patch: { label?: string; config?: Partial<WorkflowNodeConfig> }) {
    setNodes((nds) => nds.map((n) =>
      n.selected
        ? { ...n, data: { label: patch.label ?? n.data.label, config: { ...n.data.config, ...(patch.config ?? {}) } } }
        : n,
    ));
    markDirty();
  }

  async function save() {
    setSaving(true);
    setSaveMsg(null);
    try {
      const saved = await workflowsApi.update(workflow.id, {
        name: name.trim() || workflow.name, description: description.trim() || null,
        graph: toGraph(nodes, edges),
      });
      setDirty(false);
      setSaveMsg({ ok: true, text: "Gespeichert." });
      onSaved(saved);
    } catch (err) {
      setSaveMsg({ ok: false, text: extractApiError(err, "Speichern fehlgeschlagen") });
    } finally {
      setSaving(false);
    }
  }

  return (
    <LookupContext.Provider value={lookups}>
      {/* Kopfzeile */}
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <label className="text-xs font-medium text-gray-600">Name</label>
          <input value={name} onChange={(e) => { setName(e.target.value); markDirty(); }}
            className="w-full rounded border border-gray-300 px-3 py-1.5 text-base font-semibold focus:border-blue-500 focus:outline-none" />
        </div>
        <div className="min-w-[16rem] flex-[2]">
          <label className="text-xs font-medium text-gray-600">Beschreibung (optional)</label>
          <input value={description} onChange={(e) => { setDescription(e.target.value); markDirty(); }}
            placeholder="z.B. Für Lieferant XY: erst kürzen, spezieller Prompt"
            className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none" />
        </div>
        <button onClick={save} disabled={saving || !dirty}
          className="rounded bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40">
          {saving ? "Speichere…" : "Speichern"}
        </button>
        {dirty && <span className="pb-2 text-xs text-amber-700">● ungespeicherte Änderungen</span>}
        {saveMsg && <span className={`pb-2 text-sm ${saveMsg.ok ? "text-green-700" : "text-red-600"}`}>{saveMsg.text}</span>}
      </div>

      <div className="flex gap-3" style={{ height: "calc(100vh - 15rem)", minHeight: 460 }}>
        {/* Palette */}
        <aside className="w-52 shrink-0 overflow-y-auto card p-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Knoten hinzufügen</h3>
          <p className="mb-3 text-xs text-gray-400">Klicken oder auf die Fläche ziehen.</p>
          <div className="space-y-2">
            {NODE_ORDER.map((t) => (
              <button
                key={t} type="button" draggable
                onDragStart={(e) => { e.dataTransfer.setData("application/workflow-node", t); e.dataTransfer.effectAllowed = "move"; }}
                onClick={() => addNode(t)}
                title={NODE_META[t].description}
                className={`block w-full cursor-grab rounded border-2 bg-white px-2 py-1.5 text-left text-sm font-medium text-gray-800 hover:bg-gray-50 ${NODE_META[t].border}`}
                data-testid={`palette-${t}`}
              >
                {NODE_META[t].label}
              </button>
            ))}
          </div>
          <p className="mt-4 text-xs text-gray-400">
            Verbinden: vom runden Anschluss rechts eines Knotens zum Anschluss links des nächsten ziehen.
            Entfernen: Knoten oder Verbindung anklicken und <kbd className="rounded border px-1">Entf</kbd> drücken.
          </p>
        </aside>

        {/* Zeichenfläche */}
        <div ref={wrapper} className="relative min-w-0 flex-1 overflow-hidden rounded-lg border bg-slate-50 shadow-sm"
          onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} onDrop={onDrop}
          data-testid="canvas">
          <ReactFlow
            nodes={nodes} edges={edges} nodeTypes={nodeTypes}
            onNodesChange={handleNodesChange} onEdgesChange={handleEdgesChange}
            onConnect={onConnect} isValidConnection={isValidConnection}
            deleteKeyCode={["Backspace", "Delete"]} fitView fitViewOptions={{ padding: 0.2 }}
            minZoom={0.3} proOptions={{ hideAttribution: true }}
          >
            <Background gap={18} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable className="!hidden md:!block" />
          </ReactFlow>
        </div>

        {/* Eigenschaften + Prüfung */}
        <aside className="w-72 shrink-0 overflow-y-auto card p-4">
          {selected ? (
            <Inspector node={selected} onChange={patchSelected} docTypes={docTypes} />
          ) : (
            <p className="text-sm text-gray-400">Knoten anklicken, um Einstellungen (z.B. den Systemprompt) zu ändern.</p>
          )}

          <div className="mt-5 border-t pt-3" data-testid="validation-report">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Prüfung</h3>
            {!report && <p className="text-xs text-gray-400">…</p>}
            {report?.ok && report.warnings.length === 0 && <p className="text-xs text-green-700">✓ Workflow ist gültig.</p>}
            {report?.errors.map((m, i) => <p key={`e${i}`} className="mb-1 text-xs text-red-700">✕ {m}</p>)}
            {report?.warnings.map((m, i) => <p key={`w${i}`} className="mb-1 text-xs text-amber-700">⚠ {m}</p>)}
          </div>
        </aside>
      </div>
    </LookupContext.Provider>
  );
}

export default function WorkflowEditor(props: WorkflowEditorProps) {
  return (
    <ReactFlowProvider>
      <Editor {...props} />
    </ReactFlowProvider>
  );
}
