"use client";

import type { WorkflowSnapshot, WorkflowTraceEntry } from "@/lib/api";

const show = (v: unknown) =>
  v === null || v === undefined || v === "" ? "–"
    : typeof v === "number" ? v.toLocaleString("de-DE", { maximumFractionDigits: 6 })
    : String(v);

function Snapshot({ snap, title }: { snap?: WorkflowSnapshot; title: string }) {
  if (!snap) return null;
  const fields = Object.entries(snap.fields ?? {});
  const positions = snap.positions ?? [];
  const cols = positions.length ? Object.keys(positions[0]) : [];
  return (
    <details className="rounded border border-gray-200 bg-white">
      <summary className="cursor-pointer px-2 py-1 text-xs font-medium text-gray-700">
        {title} <span className="font-normal text-gray-400">
          · {positions.length}{snap.positions_total && snap.positions_total > positions.length ? ` von ${snap.positions_total}` : ""} Position(en)
          {snap.validation_status ? ` · Prüfung: ${snap.validation_status}` : ""}
          {snap.document_type ? ` · ${snap.document_type}` : ""}
        </span>
      </summary>
      <div className="space-y-2 border-t border-gray-100 p-2">
        {fields.length > 0 && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {fields.map(([k, v]) => (<><dt key={`${k}-k`} className="text-gray-500">{k}</dt><dd key={`${k}-v`} className="text-gray-800">{show(v)}</dd></>))}
          </dl>
        )}
        {positions.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead><tr className="text-left text-gray-500">
                <th className="pr-2">#</th>{cols.map((c) => <th key={c} className="pr-3 font-medium">{c}</th>)}
              </tr></thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-t border-gray-100">
                    <td className="pr-2 text-gray-400">{i + 1}</td>
                    {cols.map((c) => <td key={c} className="whitespace-nowrap pr-3 text-gray-800">{show(p[c])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {snap.validation && (snap.validation.issues.length > 0 || snap.validation.normalizations.length > 0) && (
          <div className="text-xs text-gray-600">
            {snap.validation.issues.map((m, i) => <p key={i}>• {m}</p>)}
            {snap.validation.normalizations.length > 0 && <p>• {snap.validation.normalizations.length} Einheiten-Vereinheitlichung(en) (Details unter „Änderungen“)</p>}
          </div>
        )}
      </div>
    </details>
  );
}

function Text({ title, text }: { title: string; text?: string | null }) {
  if (!text) return null;
  let pretty = text;
  if (title === "Antwort der KI") {
    try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* Rohtext */ }
  }
  return (
    <details className="rounded border border-gray-200 bg-white">
      <summary className="cursor-pointer px-2 py-1 text-xs font-medium text-gray-700">
        {title} <span className="font-normal text-gray-400">· {text.length.toLocaleString("de-DE")} Zeichen</span>
      </summary>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-gray-100 p-2 text-xs text-gray-700">{pretty}</pre>
    </details>
  );
}

/** Ablaufprotokoll eines Workflows mit Datenstand, Änderungen, Prompts und Antworten je Knoten */
export default function WorkflowTraceView({ run }: { run: WorkflowTraceEntry[] }) {
  return (
    <ol className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm" data-testid="workflow-trace">
      {run.map((t, i) => {
        const changed = t.changes?.length ?? 0;
        const hasDetail = !!(t.input || t.output || t.io || t.config);
        return (
          <li key={`${t.node_id}-${i}`} data-testid="trace-node">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className={t.status === "error" ? "text-red-600" : "text-green-600"}>{t.status === "error" ? "✕" : "✓"}</span>
              <span className="font-medium text-gray-800">{t.label}</span>
              {t.branch && (
                <span className={`rounded px-1.5 text-xs font-semibold ${t.branch === "true" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                  {t.branch === "true" ? "Ja" : "Nein"}
                </span>
              )}
              <span className="text-xs text-gray-400">{t.duration_s.toLocaleString("de-DE")} s</span>
              {t.detail && <span className={`text-xs ${t.status === "error" ? "text-red-600" : "text-gray-500"}`}>— {t.detail}</span>}
              {t.input && t.output && t.type !== "saved" && (
                <span className={`rounded-full px-2 text-xs ${changed ? "bg-amber-100 text-amber-800" : "bg-gray-200 text-gray-600"}`}>
                  {changed ? `${changed} Änderung(en)` : "keine Änderung"}
                </span>
              )}
            </div>
            {hasDetail && (
              <details className="ml-5 mt-1" open={t.status === "error"}>
                <summary className="cursor-pointer text-xs text-blue-700">Details anzeigen</summary>
                <div className="mt-1 space-y-1">
                  {t.io?.notes && t.io.notes.length > 0 && (
                    <div className="rounded border border-blue-100 bg-blue-50 p-2 text-xs text-blue-900">
                      {t.io.notes.map((n, k) => <p key={k}>• {n}</p>)}
                    </div>
                  )}
                  {changed > 0 && (
                    <div className="overflow-x-auto rounded border border-amber-200 bg-amber-50 p-2">
                      <table className="min-w-full text-xs">
                        <thead><tr className="text-left text-gray-500"><th className="pr-3">Was</th><th className="pr-3">Vorher</th><th>Nachher</th></tr></thead>
                        <tbody>
                          {t.changes!.map((c, k) => (
                            <tr key={k} className="border-t border-amber-100">
                              <td className="pr-3 text-gray-700">{c.path}</td>
                              <td className="pr-3 text-red-700">{show(c.from)}</td>
                              <td className="text-green-700">{show(c.to)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <Text title="Prompt an die KI" text={t.io?.prompt} />
                  <Text title="Antwort der KI" text={t.io?.response} />
                  <Snapshot snap={t.input} title={t.type === "saved" ? "Datenstand im Ablauf (vor dem Speichern)" : "Eingang (Daten vor dem Knoten)"} />
                  <Snapshot snap={t.output} title={t.type === "saved" ? "In der Datenbank gespeichert" : "Ausgang (Daten nach dem Knoten)"} />
                  {t.config && Object.keys(t.config).length > 0 && (
                    <p className="text-xs text-gray-500">Einstellung: {Object.entries(t.config).map(([k, v]) => `${k} = ${show(v)}`).join(" · ")}</p>
                  )}
                </div>
              </details>
            )}
          </li>
        );
      })}
    </ol>
  );
}
