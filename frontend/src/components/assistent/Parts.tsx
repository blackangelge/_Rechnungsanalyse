"use client";

import { useState } from "react";
import { Icon } from "@/components/ui";
import { ChatProposal, ChatStep, WorkflowListItem } from "@/lib/api";

const TOOL_LABEL: Record<string, string> = {
  sql: "Datenbank-Abfrage", list_units: "Einheiten lesen", list_unit_review: "Prüfliste lesen", get_schema: "Schema lesen",
  list_export_templates: "Export-Vorlagen lesen", export_template_columns: "Vorlagen-Spalten lesen", list_workflows: "Workflows lesen",
  get_workflow: "Workflow lesen", workflow_node_types: "Knotentypen lesen", workflow_templates: "Workflow-Vorlagen lesen",
  list_prompts: "Systemprompts lesen", list_ai_clients: "KI-Konfigurationen lesen", list_imports: "Imports lesen", list_import_folders: "Import-Ordner lesen",
};

/** Aufgeklappte Werkzeug-Aufrufe einer Antwort (was hat die KI nachgeschlagen?). */
export function StepsView({ steps }: { steps: ChatStep[] }) {
  if (!steps.length) return null;
  return (
    <details className="group my-2 rounded-xl border border-gray-200 bg-gray-50/70 text-xs">
      <summary className="flex cursor-pointer select-none items-center gap-1.5 px-3 py-2 text-gray-600">
        <Icon name="chevron" className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
        {steps.length} Werkzeug-Aufruf{steps.length === 1 ? "" : "e"}
        {steps.some((s) => !s.ok) && <span className="text-red-600">· mit Fehler</span>}
      </summary>
      <div className="space-y-2 border-t border-gray-200 p-3">
        {steps.map((s, i) => (
          <div key={i}>
            <div className="font-medium text-gray-700">
              {s.ok ? "✓" : "✕"} {TOOL_LABEL[s.tool] ?? s.tool}
              {s.sql ? ` · ${s.row_count ?? 0} Zeile(n)${s.truncated ? " (gekürzt)" : ""}` : ""}
            </div>
            {s.sql && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] text-gray-700">{s.sql}</pre>}
            {s.sql && s.columns && s.rows && s.rows.length > 0 && (
              <div className="mt-1 overflow-x-auto rounded bg-white">
                <table className="min-w-full text-[11px]">
                  <thead><tr>{s.columns.map((c) => <th key={c} className="px-2 py-1 text-left font-medium text-gray-600">{c}</th>)}</tr></thead>
                  <tbody>
                    {s.rows.map((r, ri) => (
                      <tr key={ri} className="border-t border-gray-100">
                        {r.map((v, ci) => <td key={ci} className="max-w-[16rem] truncate px-2 py-0.5" title={String(v ?? "")}>{v === null ? <span className="text-gray-300">NULL</span> : String(v)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!s.sql && s.args && Object.keys(s.args).length > 0 && <div className="mt-0.5 font-mono text-[11px] text-gray-500">{JSON.stringify(s.args)}</div>}
            {!s.sql && s.ok && s.preview && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] text-gray-600">{s.preview}</pre>}
            {!s.ok && <div className="mt-1 text-red-700">{s.error}</div>}
          </div>
        ))}
      </div>
    </details>
  );
}

const STATUS: Record<ChatProposal["status"], { label: string; cls: string }> = {
  pending: { label: "wartet auf Freigabe", cls: "border-amber-300 bg-amber-50 text-amber-900" },
  applied: { label: "ausgeführt", cls: "border-emerald-300 bg-emerald-50 text-emerald-900" },
  dismissed: { label: "verworfen", cls: "border-gray-200 bg-gray-50 text-gray-500" },
  failed: { label: "fehlgeschlagen", cls: "border-red-300 bg-red-50 text-red-900" },
};

export function ProposalCard({ p, busy, workflows, onApply, onDismiss }: {
  p: ChatProposal;
  busy: boolean;
  workflows: WorkflowListItem[];
  onApply: (workflowId?: number) => void;
  onDismiss: () => void;
}) {
  const [wf, setWf] = useState<string>("");
  const st = STATUS[p.status];
  const needsWorkflow = p.tool === "propose_reanalysis";
  return (
    <div className={`my-2 rounded-xl border p-3 text-sm ${st.cls}`} data-testid="proposal">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 font-medium">{p.summary}</div>
        <span className="shrink-0 rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-medium">{st.label}</span>
      </div>
      {p.reason && <div className="mt-1 text-xs opacity-80">{p.reason}</div>}
      {p.current && p.args.changes != null && (
        <table className="mt-2 text-xs">
          <tbody>
            {Object.entries(p.args.changes as Record<string, unknown>).map(([k, v]) => (
              <tr key={k}>
                <td className="pr-3 font-medium">{k}</td>
                <td className="pr-2 line-through opacity-60">{p.current![k] == null ? "NULL" : String(p.current![k])}</td>
                <td>→ {v == null ? "NULL" : String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {p.reanalysis && (
        <div className="mt-1 text-xs opacity-80">
          Beleg-IDs: {p.reanalysis.sample.map((d) => `#${d.id}`).join(", ")}{p.reanalysis.count > p.reanalysis.sample.length ? ` … (+${p.reanalysis.count - p.reanalysis.sample.length})` : ""}
          {p.reanalysis.skipped > 0 && ` · ${p.reanalysis.skipped} nicht geeignet`}
        </div>
      )}
      {!p.reanalysis && p.current == null && (
        <details className="mt-1.5 text-xs">
          <summary className="cursor-pointer opacity-70">Details</summary>
          <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-white/70 p-2 font-mono text-[11px]">{JSON.stringify(p.args, null, 2)}</pre>
        </details>
      )}
      {p.status === "applied" && p.result != null && <div className="mt-1.5 break-words font-mono text-[11px] opacity-80">→ {JSON.stringify(p.result)}</div>}
      {p.status === "failed" && p.error && <div className="mt-1.5 text-xs font-medium">{p.error}</div>}
      {(p.status === "pending" || p.status === "failed") && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {needsWorkflow && (
            <select aria-label="Workflow für die Analyse" className="input w-auto py-1 text-xs" value={wf} onChange={(e) => setWf(e.target.value)}>
              <option value="">— Workflow wählen —</option>
              {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          )}
          {p.status === "pending" && (
            <>
              <button type="button" disabled={busy || (needsWorkflow && !wf)} onClick={() => onApply(wf ? Number(wf) : undefined)} data-testid="apply-proposal"
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50">Freigeben &amp; ausführen</button>
              <button type="button" disabled={busy} onClick={onDismiss} className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50">Verwerfen</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
