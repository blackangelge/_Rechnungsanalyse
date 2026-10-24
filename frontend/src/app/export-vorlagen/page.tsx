"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Card, EmptyState, Icon, Modal, PageHeader, Spinner } from "@/components/ui";
import { downloadLinkHandler } from "@/lib/download";
import {
  ExportColumnsPreview, ExportTemplate, ExportTemplateInput, ImportBatch, SchemaVersionFull,
  exportTemplatesApi, extractApiError, importsApi, schemaApi,
} from "@/lib/api";

const LAYOUTS = { sheet_per_entity: "Ein Blatt je Entität", flat: "Flach (eine Zeile je Position)" } as const;
const QTY = { original: "Wie gedruckt (Originaleinheit)", normalized: "Normiert (Basiseinheit)" } as const;

export default function ExportTemplatesPage() {
  const [templates, setTemplates] = useState<ExportTemplate[]>([]);
  const [schema, setSchema] = useState<SchemaVersionFull | null>(null);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<ExportTemplate | "new" | null>(null);
  const [columns, setColumns] = useState<{ t: ExportTemplate; cols: ExportColumnsPreview[] } | null>(null);
  const [exportFor, setExportFor] = useState<ExportTemplate | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [t, versions, b] = await Promise.all([exportTemplatesApi.list(), schemaApi.versions(), importsApi.list({ include_hidden: true })]);
      setTemplates(t);
      setBatches(b);
      const active = versions.find((v) => v.status === "active");
      setSchema(active ? await schemaApi.version(active.id) : null);
    } catch (err) {
      setError(extractApiError(err, "Vorlagen konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function showColumns(t: ExportTemplate) {
    try { setColumns({ t, cols: await exportTemplatesApi.columns(t.id) }); } catch (err) { setError(extractApiError(err, "Spalten konnten nicht ermittelt werden")); }
  }

  return (
    <div>
      <PageHeader title="Export-Vorlagen"
        subtitle="Spalten, Reihenfolge und Zellformate kommen aus dem aktiven Schema; die Vorlage wählt Entitäten, Felder und das Layout der Excel-Datei."
        actions={<button className="btn btn-primary" disabled={!schema} onClick={() => setEdit("new")}><Icon name="plus" /> Neue Vorlage</button>} />
      {error && <div className="mb-4"><Alert tone="error" onClose={() => setError(null)}>{error}</Alert></div>}
      {!loading && !schema && <div className="mb-4"><Alert tone="warn">Es gibt noch kein aktives Schema. Aktiviere zuerst eine Version unter „Schema“.</Alert></div>}
      {loading && templates.length === 0 && <Spinner />}
      {!loading && schema && templates.length === 0 && <EmptyState title="Noch keine Vorlage" text="Lege eine Vorlage an, um Belege als Excel zu exportieren." />}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templates.map((t) => (
          <Card key={t.id} title={t.name} actions={<Badge tone="blue">{LAYOUTS[t.layout]}</Badge>}>
            <p className="min-h-[2.5rem] text-sm text-gray-600">{t.description || <span className="text-gray-400">Keine Beschreibung</span>}</p>
            <p className="mt-2 text-xs text-gray-500">Mengen: {QTY[t.quantity_mode]}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="btn btn-primary btn-sm" onClick={() => setExportFor(t)}><Icon name="download" /> Exportieren</button>
              <button className="btn btn-secondary btn-sm" onClick={() => showColumns(t)}>Spalten</button>
              <button className="btn btn-secondary btn-sm" onClick={() => setEdit(t)}><Icon name="edit" /> Bearbeiten</button>
              <button className="btn btn-ghost btn-sm text-red-600" aria-label={`${t.name} löschen`}
                onClick={async () => { if (confirm(`Vorlage „${t.name}“ löschen?`)) { await exportTemplatesApi.delete(t.id); await load(); } }}><Icon name="trash" /></button>
            </div>
          </Card>
        ))}
      </div>

      {edit && schema && <TemplateModal schema={schema} template={edit === "new" ? null : edit} onClose={() => setEdit(null)}
        onSave={async (data) => { if (edit === "new") await exportTemplatesApi.create(data); else await exportTemplatesApi.update(edit.id, data); setEdit(null); await load(); }} />}

      {columns && (
        <Modal title={`Spalten · ${columns.t.name}`} onClose={() => setColumns(null)} wide footer={<button className="btn btn-secondary" onClick={() => setColumns(null)}>Schließen</button>}>
          <div className="space-y-4">
            {columns.cols.map((c) => (
              <div key={c.entity}>
                <p className="mb-1.5 text-sm font-semibold text-gray-800">{c.display_name}</p>
                <div className="flex flex-wrap gap-1.5">{c.columns.map((col, i) => <span key={i} className="badge badge-gray" title={col.format ?? undefined}>{col.header}</span>)}</div>
              </div>
            ))}
          </div>
        </Modal>
      )}

      {exportFor && <ExportModal template={exportFor} batches={batches} onClose={() => setExportFor(null)} />}
    </div>
  );
}

function TemplateModal({ schema, template, onClose, onSave }: {
  schema: SchemaVersionFull; template: ExportTemplate | null; onClose: () => void; onSave: (d: ExportTemplateInput) => Promise<void>;
}) {
  const [name, setName] = useState(template?.name ?? "");
  const [description, setDescription] = useState(template?.description ?? "");
  const [layout, setLayout] = useState<ExportTemplate["layout"]>(template?.layout ?? "sheet_per_entity");
  const [qty, setQty] = useState<ExportTemplate["quantity_mode"]>(template?.quantity_mode ?? "original");
  // Auswahl: Entität → Menge gewählter Felder (leer = alle). Entität nicht in der Map = nicht exportiert.
  const [sel, setSel] = useState<Record<string, Set<string>>>(() => {
    const init: Record<string, Set<string>> = {};
    const cfg = template?.config.entities;
    if (cfg) cfg.forEach((e) => { init[e.entity] = new Set(e.fields ?? []); });
    else schema.entities.forEach((e) => { init[e.name] = new Set(); });
    return init;
  });
  const [error, setError] = useState<string | null>(null);

  function toggleEntity(n: string) {
    setSel((s) => { const c = { ...s }; if (n in c) delete c[n]; else c[n] = new Set(); return c; });
  }
  function toggleField(en: string, fn: string, all: string[]) {
    setSel((s) => {
      const cur = new Set(s[en].size === 0 ? all : s[en]);
      if (cur.has(fn)) cur.delete(fn); else cur.add(fn);
      return { ...s, [en]: cur.size === all.length ? new Set() : cur };
    });
  }

  async function save() {
    if (!name.trim()) { setError("Name fehlt."); return; }
    const entities = schema.entities.filter((e) => e.name in sel).map((e) => ({ entity: e.name, fields: sel[e.name].size ? [...sel[e.name]] : null }));
    if (entities.length === 0) { setError("Mindestens eine Entität wählen."); return; }
    try {
      await onSave({ name: name.trim(), description: description || null, layout, quantity_mode: qty, config: { ...(template?.config ?? {}), entities } });
    } catch (err) { setError(extractApiError(err, "Vorlage konnte nicht gespeichert werden")); }
  }

  return (
    <Modal title={template ? "Vorlage bearbeiten" : "Neue Vorlage"} onClose={onClose} wide
      footer={<><button className="btn btn-secondary" onClick={onClose}>Abbrechen</button><button className="btn btn-primary" onClick={save}>Speichern</button></>}>
      <div className="space-y-4">
        {error && <Alert tone="error" onClose={() => setError(null)}>{error}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <div><label className="label" htmlFor="t-name">Name</label><input id="t-name" className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div><label className="label" htmlFor="t-desc">Beschreibung</label><input id="t-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
          <div><label className="label" htmlFor="t-layout">Layout</label>
            <select id="t-layout" className="input" value={layout} onChange={(e) => setLayout(e.target.value as ExportTemplate["layout"])}>{Object.entries(LAYOUTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
          <div><label className="label" htmlFor="t-qty">Mengenfelder</label>
            <select id="t-qty" className="input" value={qty} onChange={(e) => setQty(e.target.value as ExportTemplate["quantity_mode"])}>{Object.entries(QTY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        </div>
        <div>
          <p className="label">Entitäten und Felder (nichts angehakt = alle Felder)</p>
          <div className="space-y-3">
            {schema.entities.map((e) => {
              const on = e.name in sel;
              const all = e.fields.map((f) => f.name);
              return (
                <div key={e.id} className={`rounded-lg border p-3 ${on ? "border-blue-200 bg-blue-50/40" : "border-gray-200"}`}>
                  <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={on} onChange={() => toggleEntity(e.name)} /> {e.display_name}</label>
                  {on && !e.master_table && (
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-6">
                      {e.fields.map((f) => (
                        <label key={f.id} className="flex items-center gap-1.5 text-xs text-gray-700">
                          <input type="checkbox" checked={sel[e.name].size === 0 || sel[e.name].has(f.name)} onChange={() => toggleField(e.name, f.name, all)} /> {f.display_name}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function ExportModal({ template, batches, onClose }: { template: ExportTemplate; batches: ImportBatch[]; onClose: () => void }) {
  const [ids, setIds] = useState<Set<number>>(new Set());
  const [hidden, setHidden] = useState(false);
  const visible = batches.filter((b) => hidden || !b.hidden_at);
  const url = exportTemplatesApi.exportUrl(template.id, { batch_ids: [...ids], include_hidden: hidden });
  return (
    <Modal title={`Exportieren · ${template.name}`} onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Schließen</button>
        <a href={url} onClick={downloadLinkHandler(url, `${template.name}.xlsx`)} className="btn btn-primary"><Icon name="download" /> Excel herunterladen</a></>}>
      <div className="space-y-3">
        <p className="text-sm text-gray-600">Welche Imports sollen in die Datei? Ohne Auswahl werden alle sichtbaren Belege exportiert.</p>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} /> Ausgeblendete Imports einbeziehen</label>
        <ul className="max-h-72 divide-y divide-gray-100 overflow-y-auto rounded-lg border border-gray-200">
          {visible.map((b) => (
            <li key={b.id}>
              <label className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
                <input type="checkbox" checked={ids.has(b.id)} onChange={() => setIds((s) => { const n = new Set(s); if (n.has(b.id)) n.delete(b.id); else n.add(b.id); return n; })} />
                {b.company_name} {b.year} <span className="text-xs text-gray-400">#{b.id}</span>{b.hidden_at && <Badge tone="gray">ausgeblendet</Badge>}
              </label>
            </li>
          ))}
          {visible.length === 0 && <li className="px-3 py-3 text-sm text-gray-400">Keine Imports.</li>}
        </ul>
      </div>
    </Modal>
  );
}
