import type { WorkflowNodeConfig, WorkflowNodeType } from "@/lib/api";

/** Darstellung und Standardwerte der Knotentypen im Workflow-Editor */
export interface NodeMeta {
  label: string;
  description: string;
  /** Tailwind-Klassen für Kopfzeile / Rand der Karte */
  head: string;
  border: string;
  defaultConfig: WorkflowNodeConfig;
}

export const NODE_META: Record<WorkflowNodeType, NodeMeta> = {
  start: {
    label: "Start",
    description: "Hier beginnt jede Analyse.",
    head: "bg-emerald-600 text-white", border: "border-emerald-400",
    defaultConfig: {},
  },
  trim_pages: {
    label: "PDF kürzen",
    description: "Behält nur die ersten N Seiten (neue PDF-Version, Original bleibt erhalten).",
    head: "bg-amber-500 text-white", border: "border-amber-400",
    defaultConfig: { max_pages: 1 },
  },
  detect_type: {
    label: "Dokumententyp erkennen",
    description: "Die KI bestimmt den Dokumententyp (Rechnung, Lieferschein, …).",
    head: "bg-violet-600 text-white", border: "border-violet-400",
    defaultConfig: { system_prompt_id: null },
  },
  branch: {
    label: "Verzweigung",
    description: "Ja/Nein-Weiche, z.B. nach Dokumententyp, Lieferant oder Seitenzahl.",
    head: "bg-sky-600 text-white", border: "border-sky-400",
    defaultConfig: { field: "document_type", op: "equals", value: "1" },
  },
  extract: {
    label: "Rechnungsdaten extrahieren",
    description: "Die KI liest Lieferant, Positionen und Beträge aus. Hier wählst du den Systemprompt.",
    head: "bg-blue-700 text-white", border: "border-blue-400",
    defaultConfig: { system_prompt_id: null, ai_config_id: null },
  },
  validate: {
    label: "Plausibilität prüfen",
    description: "Rechnerische Prüfung, Einheiten-Korrektur, optional zweite KI-Abfrage.",
    head: "bg-rose-600 text-white", border: "border-rose-400",
    defaultConfig: { auto_correct: true, ai_recheck: true },
  },
  normalize_units: {
    label: "Einheiten vereinheitlichen",
    description: "Rechnet Gewichte (kg, g, t) auf eine Einheit um — Menge, Einheit und Einzelpreis (€ je Einheit).",
    head: "bg-teal-600 text-white", border: "border-teal-400",
    defaultConfig: { target: "t" },
  },
  ai_refine: {
    label: "KI-Nachbearbeitung",
    description: "Die KI bearbeitet das bisherige Ergebnis mit einem eigenen Prompt erneut (z. B. Einheiten korrigieren, Felder ergänzen).",
    head: "bg-fuchsia-600 text-white", border: "border-fuchsia-400",
    defaultConfig: { system_prompt_id: null, ai_config_id: null, include_images: true },
  },
  end: {
    label: "Ende",
    description: "Hier endet der Ablauf; das Ergebnis wird gespeichert.",
    head: "bg-gray-600 text-white", border: "border-gray-400",
    defaultConfig: {},
  },
};

export const NODE_ORDER: WorkflowNodeType[] = [
  "trim_pages", "detect_type", "branch", "extract", "validate", "normalize_units", "ai_refine", "end",
];

export const BRANCH_FIELDS: Record<string, string> = {
  document_type: "Dokumententyp",
  supplier_name: "Lieferant",
  page_count: "Seitenzahl",
  validation_status: "Prüfstatus",
};

export const BRANCH_OPS: Record<string, string> = {
  equals: "ist gleich",
  not_equals: "ist nicht",
  contains: "enthält",
  in: "ist eines von",
  gt: "größer als",
  lt: "kleiner als",
};

/** Welche Operatoren je Feld sinnvoll sind (Spiegel der Backend-Regeln) */
export const ALLOWED_OPS: Record<string, string[]> = {
  document_type: ["equals", "not_equals", "in", "contains"],
  supplier_name: ["equals", "not_equals", "contains", "in"],
  page_count: ["equals", "not_equals", "gt", "lt", "in"],
  validation_status: ["equals", "not_equals", "in"],
};

export const VALUE_HINTS: Record<string, string> = {
  document_type: "ID oder Name, z.B. 1 oder Eingangsrechnung (mehrere mit Komma)",
  supplier_name: "Teil des Namens, z.B. ACME (Groß-/Kleinschreibung egal)",
  page_count: "Zahl, z.B. 3",
  validation_status: "ok, corrected, ai_corrected, needs_review, manual_ok",
};
