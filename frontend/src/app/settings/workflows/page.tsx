"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Alert, Card, EmptyState, Icon, Modal, PageHeader, Spinner } from "@/components/ui";
import { WorkflowListItem, WorkflowTemplate, extractApiError, workflowsApi } from "@/lib/api";

export default function WorkflowsPage() {
  const router = useRouter();
  const [items, setItems] = useState<WorkflowListItem[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTemplate, setNewTemplate] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, tpls] = await Promise.all([workflowsApi.list(), workflowsApi.templates()]);
      setItems(list);
      setTemplates(tpls);
      setNewTemplate((cur) => cur || tpls[0]?.key || "__empty");
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
      const wf = await workflowsApi.create({ name: newName.trim(), template: newTemplate === "__empty" ? undefined : newTemplate });
      router.push(`/settings/workflows/${wf.id}`);
    } catch (err) {
      setError(extractApiError(err, "Workflow konnte nicht angelegt werden"));
      setBusy(false);
    }
  }

  const tpl = templates.find((t) => t.key === newTemplate);

  return (
    <div>
      <PageHeader title="Workflows"
        subtitle="Ein Workflow legt als Ablaufdiagramm fest, welche Schritte die KI-Analyse durchläuft (Seiten erkennen, Schema-Extraktion, Prüfung …). Es gibt keinen Standard: Jede Analyse, jeder Import mit Analyse und jede Ordner-Überwachung nennt ihren Workflow ausdrücklich."
        actions={<button className="btn btn-primary" onClick={() => { setShowNew(true); setNewName(""); }}><Icon name="plus" /> Neuer Workflow</button>} />
      {error && <div className="mb-4"><Alert tone="error" onClose={() => setError(null)}>{error}</Alert></div>}
      {loading && items.length === 0 && <Spinner />}
      {!loading && items.length === 0 && <EmptyState title="Noch kein Workflow" text="Lege einen aus einer Vorlage an, z. B. „Schema-Extraktion“." />}

      {items.length > 0 && (
        <Card pad={false}>
          <div className="overflow-x-auto">
            <table className="table-ra">
              <thead><tr><th>Name</th><th>Beschreibung</th><th className="text-right">Knoten</th><th className="text-right">Aktionen</th></tr></thead>
              <tbody>
                {items.map((w) => (
                  <tr key={w.id} data-testid={`wf-row-${w.id}`}>
                    <td className="font-medium"><Link href={`/settings/workflows/${w.id}`} className="text-gray-900 hover:text-blue-700 hover:underline">{w.name}</Link></td>
                    <td className="text-gray-600">{w.description ?? "–"}</td>
                    <td className="text-right tabular-nums">{w.node_count}</td>
                    <td>
                      <div className="flex flex-wrap justify-end gap-1.5">
                        <Link href={`/settings/workflows/${w.id}`} className="btn btn-primary btn-sm">Bearbeiten</Link>
                        <button disabled={busy} className="btn btn-secondary btn-sm" onClick={() => run(() => workflowsApi.duplicate(w.id))}>Duplizieren</button>
                        <button disabled={busy} className="btn btn-danger-outline btn-sm"
                          onClick={() => { if (confirm(`Workflow „${w.name}“ löschen?`)) run(() => workflowsApi.delete(w.id)); }}>Löschen</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {showNew && (
        <Modal title="Neuer Workflow" onClose={() => setShowNew(false)}
          footer={<><button className="btn btn-secondary" onClick={() => setShowNew(false)}>Abbrechen</button>
            <button className="btn btn-primary" disabled={busy || !newName.trim()} onClick={createWorkflow}>Anlegen &amp; bearbeiten</button></>}>
          <div className="space-y-4">
            <div><label className="label" htmlFor="wf-name">Name</label>
              <input id="wf-name" autoFocus className="input" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createWorkflow()} placeholder="z. B. Rechnungen mit Seitenerkennung" /></div>
            <div><label className="label" htmlFor="wf-tpl">Startpunkt</label>
              <select id="wf-tpl" className="input" value={newTemplate} onChange={(e) => setNewTemplate(e.target.value)}>
                {templates.map((t) => <option key={t.key} value={t.key}>Vorlage: {t.name}</option>)}
                <option value="__empty">Leer (nur Start)</option>
              </select>
              {tpl && <p className="hint">{tpl.description}</p>}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
