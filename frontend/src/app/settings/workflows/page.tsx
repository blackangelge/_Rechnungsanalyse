"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  WorkflowListItem, WorkflowTemplate, extractApiError, workflowsApi,
} from "@/lib/api";

export default function WorkflowsPage() {
  const router = useRouter();
  const [items, setItems] = useState<WorkflowListItem[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTemplate, setNewTemplate] = useState("standard");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, tpls] = await Promise.all([workflowsApi.list(), workflowsApi.templates()]);
      setItems(list);
      setTemplates(tpls);
      setError(null);
    } catch (err) {
      setError(extractApiError(err, "Workflows konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try { await action(); await load(); }
    catch (err) { setError(extractApiError(err, "Aktion fehlgeschlagen")); }
    finally { setBusy(false); }
  }

  async function createWorkflow() {
    if (!newName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const wf = await workflowsApi.create({
        name: newName.trim(),
        template: newTemplate === "__empty" ? undefined : newTemplate,
      });
      router.push(`/settings/workflows/${wf.id}`);
    } catch (err) {
      setError(extractApiError(err, "Workflow konnte nicht angelegt werden"));
      setBusy(false);
    }
  }

  const hasDefault = items.some((w) => w.is_default);

  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Workflows</h1>
        <button onClick={() => { setShowNew(true); setNewName(""); }}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
          + Neuer Workflow
        </button>
      </div>
      <p className="mb-4 max-w-3xl text-sm text-gray-500">
        Ein Workflow legt fest, welche Schritte die KI-Analyse eines Belegs durchläuft und welche
        Systemprompts dabei verwendet werden — wie in n8n als Ablaufdiagramm. Für einzelne Lieferanten
        kannst du einen eigenen Workflow festlegen (Seite „Lieferanten").
      </p>

      <div className="mb-5 max-w-3xl rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm text-blue-900">
        <b>Welcher Workflow wird verwendet?</b>
        <ol className="ml-5 mt-1 list-decimal text-blue-800">
          <li>der in der Analyse ausdrücklich gewählte Workflow,</li>
          <li>sonst der Workflow des Lieferanten (sobald dieser bekannt ist, also z.B. bei erneuter Analyse),</li>
          <li>sonst der <b>Standard-Workflow</b>,</li>
          <li>gibt es keinen, läuft der klassische Ablauf wie bisher.</li>
        </ol>
        {!hasDefault && items.length > 0 && (
          <p className="mt-2 text-amber-800">Derzeit ist kein Standard-Workflow gesetzt — es läuft der klassische Ablauf.</p>
        )}
      </div>

      {error && <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {showNew && (
        <div className="mb-5 rounded-lg border bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-base font-semibold text-gray-800">Neuer Workflow</h2>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[16rem] flex-1">
              <label className="text-xs font-medium text-gray-600">Name</label>
              <input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && createWorkflow()}
                placeholder="z.B. Lieferant ACME"
                className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none" />
            </div>
            <div className="min-w-[16rem] flex-1">
              <label className="text-xs font-medium text-gray-600">Startpunkt</label>
              <select value={newTemplate} onChange={(e) => setNewTemplate(e.target.value)}
                className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none">
                {templates.map((t) => <option key={t.key} value={t.key}>Vorlage: {t.name}</option>)}
                <option value="__empty">Leer (nur Start)</option>
              </select>
            </div>
            <button onClick={createWorkflow} disabled={busy || !newName.trim()}
              className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40">
              Anlegen &amp; bearbeiten
            </button>
            <button onClick={() => setShowNew(false)}
              className="rounded border border-gray-300 px-4 py-1.5 text-sm text-gray-600 hover:bg-gray-100">Abbrechen</button>
          </div>
          {templates.find((t) => t.key === newTemplate) && (
            <p className="mt-2 text-xs text-gray-500">{templates.find((t) => t.key === newTemplate)!.description}</p>
          )}
        </div>
      )}

      <div className="overflow-hidden rounded-lg border bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Beschreibung</th>
              <th className="px-4 py-3 text-right">Knoten</th>
              <th className="px-4 py-3 text-right">Lieferanten</th>
              <th className="px-4 py-3 text-right">Aktionen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">Lade…</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">
                Noch kein Workflow. Lege einen aus der Vorlage „Standard (wie bisher)" an und setze ihn als Standard.
              </td></tr>
            )}
            {items.map((w) => (
              <tr key={w.id} data-testid={`wf-row-${w.id}`}>
                <td className="px-4 py-3 font-medium text-gray-900">
                  <Link href={`/settings/workflows/${w.id}`} className="hover:text-blue-700 hover:underline">{w.name}</Link>
                  {w.is_default && (
                    <span className="ml-2 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">Standard</span>
                  )}
                </td>
                <td className="px-4 py-3 text-gray-600">{w.description ?? "–"}</td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-700">{w.node_count}</td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-700">{w.supplier_count}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap justify-end gap-1.5">
                    <Link href={`/settings/workflows/${w.id}`}
                      className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700">Bearbeiten</Link>
                    {w.is_default ? (
                      <button disabled={busy} onClick={() => run(() => workflowsApi.clearDefault(w.id))}
                        className="rounded border border-gray-300 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-100">Standard aufheben</button>
                    ) : (
                      <button disabled={busy} onClick={() => run(() => workflowsApi.setDefault(w.id))}
                        className="rounded border border-green-300 px-2.5 py-1 text-xs text-green-700 hover:bg-green-50">Als Standard</button>
                    )}
                    <button disabled={busy} onClick={() => run(() => workflowsApi.duplicate(w.id))}
                      className="rounded border border-gray-300 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-100">Duplizieren</button>
                    <button disabled={busy}
                      onClick={() => {
                        const extra = w.supplier_count > 0 ? ` ${w.supplier_count} Lieferant(en) fallen dann auf den Standard zurück.` : "";
                        if (confirm(`Workflow „${w.name}" löschen?${extra}`)) run(() => workflowsApi.delete(w.id));
                      }}
                      className="rounded border border-red-300 px-2.5 py-1 text-xs text-red-600 hover:bg-red-50">Löschen</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
