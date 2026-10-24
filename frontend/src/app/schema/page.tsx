"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Card, EmptyState, Icon, Modal, PageHeader, Spinner } from "@/components/ui";
import FieldModal, { TYPE_LABELS } from "@/components/schema/FieldModal";
import {
  SchemaEntity, SchemaField, SchemaFieldInput, SchemaVersionFull, SchemaVersionSummary, extractApiError, schemaApi,
} from "@/lib/api";

const STATUS: Record<string, { label: string; tone: "green" | "amber" | "gray" }> = {
  active: { label: "Aktiv", tone: "green" }, draft: { label: "Entwurf", tone: "amber" }, archived: { label: "Archiviert", tone: "gray" },
};

export default function SchemaPage() {
  const [versions, setVersions] = useState<SchemaVersionSummary[]>([]);
  const [version, setVersion] = useState<SchemaVersionFull | null>(null);
  const [entityId, setEntityId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldModal, setFieldModal] = useState<{ field: SchemaField | null } | null>(null);
  const [entityModal, setEntityModal] = useState(false);
  const [relationModal, setRelationModal] = useState(false);

  const load = useCallback(async (preferId?: number) => {
    setLoading(true);
    setError(null);
    try {
      const list = await schemaApi.versions();
      setVersions(list);
      const target = (preferId && list.find((v) => v.id === preferId)) || list.find((v) => v.status === "draft") || list.find((v) => v.status === "active") || list[0];
      if (target) setVersion(await schemaApi.version(target.id)); else setVersion(null);
    } catch (err) {
      setError(extractApiError(err, "Schema konnte nicht geladen werden"));
    } finally {
      setLoading(false);
      setLoaded(true);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const entity: SchemaEntity | undefined = useMemo(
    () => version?.entities.find((e) => e.id === entityId) ?? version?.entities[0], [version, entityId]);
  const editable = version?.status === "draft";

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    try { return await fn(); } catch (err) { setError(extractApiError(err, "Aktion fehlgeschlagen")); return undefined; }
  }

  async function createDraft() {
    const v = await run(() => schemaApi.createDraft(version?.id));
    if (v) await load(v.id);
  }
  async function activate() {
    if (!version || !confirm(`Version ${version.version_number} aktivieren? Sie ist danach unveränderlich; neue Analysen nutzen sie sofort.`)) return;
    const v = await run(() => schemaApi.activate(version.id));
    if (v) await load(v.id);
  }
  async function discard() {
    if (!version || !confirm("Entwurf verwerfen?")) return;
    if (await run(() => schemaApi.discard(version.id)) !== undefined) await load();
  }
  async function saveField(data: SchemaFieldInput) {
    if (!entity || !version) return;
    const next = fieldModal?.field ? await schemaApi.patchField(fieldModal.field.id, data) : await schemaApi.addField(entity.id, data);
    setVersion(next);
    setFieldModal(null);
  }
  async function removeField(f: SchemaField) {
    if (!confirm(`Feld „${f.display_name}“ löschen?`)) return;
    const next = await run(() => schemaApi.deleteField(f.id));
    if (next) setVersion(next);
  }
  async function removeEntity(e: SchemaEntity) {
    if (!confirm(`Entität „${e.display_name}“ samt Feldern löschen?`)) return;
    const next = await run(() => schemaApi.deleteEntity(e.id));
    if (next) { setVersion(next); setEntityId(null); }
  }

  const entityName = (id: number) => version?.entities.find((e) => e.id === id)?.display_name ?? `#${id}`;

  return (
    <div>
      <PageHeader title="Schema"
        subtitle="Welche Daten die KI aus einem Beleg liest: Entitäten (z. B. Beleg, Position), ihre Felder und Beziehungen. Aktive Versionen sind unveränderlich — Änderungen entstehen im Entwurf."
        actions={<>
          {versions.length > 0 && (
            <select aria-label="Version wählen" className="input w-auto" value={version?.id ?? ""} onChange={(e) => load(Number(e.target.value))}>
              {versions.map((v) => <option key={v.id} value={v.id}>Version {v.version_number} · {STATUS[v.status].label}</option>)}
            </select>
          )}
          {version && version.status !== "draft" && !versions.some((v) => v.status === "draft") && <button className="btn btn-secondary" onClick={createDraft}>Entwurf aus Version {version.version_number}</button>}
          {version?.status === "draft" && <>
            <button className="btn btn-danger-outline" onClick={discard}>Verwerfen</button>
            <button className="btn btn-primary" onClick={activate} disabled={version.problems.length > 0}>Aktivieren</button>
          </>}
        </>} />

      {error && <div className="mb-4"><Alert tone="error" onClose={() => setError(null)}>{error}</Alert></div>}
      {loading && !version && <Spinner />}
      {loaded && !version && !loading && (
        <EmptyState title="Noch kein Schema" text="Spiele das Ausgangsschema (Beleg, Position, Lieferant) samt Einheiten und Export-Vorlagen mit einem Klick ein — oder lege einen leeren Entwurf an und definiere alles selbst."
          action={<div className="flex flex-wrap justify-center gap-2">
            <button className="btn btn-primary" onClick={async () => { const r = await run(() => schemaApi.seed()); if (r) await load(); }}>Ausgangsschema und Einheiten einspielen</button>
            <button className="btn btn-secondary" onClick={createDraft}>Leeren Entwurf anlegen</button>
          </div>} />
      )}

      {version && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm text-gray-600">
            <Badge tone={STATUS[version.status].tone}>{STATUS[version.status].label}</Badge>
            <span>Version {version.version_number}</span>
            {version.comment && <span className="text-gray-400">· {version.comment}</span>}
            {!editable && <span className="inline-flex items-center gap-1 text-gray-400"><Icon name="lock" className="h-3.5 w-3.5" /> schreibgeschützt</span>}
          </div>
          {version.status === "draft" && version.problems.length > 0 && (
            <Alert tone="warn"><b>Vor dem Aktivieren beheben:</b><ul className="mt-1 list-disc pl-5">{version.problems.map((p) => <li key={p}>{p}</li>)}</ul></Alert>
          )}

          <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
            {/* Entitäten */}
            <Card title="Entitäten" pad={false} actions={editable && <button className="btn btn-ghost btn-sm" onClick={() => setEntityModal(true)}><Icon name="plus" /> Neu</button>}>
              <ul className="divide-y divide-gray-100">
                {version.entities.map((e) => (
                  <li key={e.id}>
                    <button type="button" onClick={() => setEntityId(e.id)} aria-current={entity?.id === e.id}
                      className={`flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left text-sm ${entity?.id === e.id ? "bg-blue-50 font-medium text-blue-800" : "hover:bg-gray-50"}`}>
                      <span className="truncate">{e.display_name}</span>
                      <span className="flex items-center gap-1">{e.master_table && <Badge tone="violet">Stammdaten</Badge>}<span className="text-xs text-gray-400">{e.fields.length}</span></span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="border-t border-gray-100 px-4 py-3">
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Beziehungen</p>
                  {editable && version.entities.length > 1 && <button className="btn btn-ghost btn-sm" onClick={() => setRelationModal(true)}><Icon name="plus" /></button>}
                </div>
                {version.relations.length === 0 && <p className="text-xs text-gray-400">Keine</p>}
                <ul className="space-y-1.5">
                  {version.relations.map((r) => (
                    <li key={r.id} className="flex items-start justify-between gap-2 text-xs text-gray-600">
                      <span><b className="text-gray-800">{r.name}</b><br />{entityName(r.source_entity_id)} {r.kind === "parent_child" ? "→ enthält" : "→ verweist auf"} {entityName(r.target_entity_id)}</span>
                      {editable && <button className="text-gray-400 hover:text-red-600" aria-label={`Beziehung ${r.name} löschen`}
                        onClick={async () => { const n = await run(() => schemaApi.deleteRelation(r.id)); if (n) setVersion(n); }}><Icon name="x" className="h-3.5 w-3.5" /></button>}
                    </li>
                  ))}
                </ul>
              </div>
            </Card>

            {/* Felder */}
            {entity ? (
              <Card title={<span>{entity.display_name} <span className="ml-1 font-mono text-xs font-normal text-gray-400">{entity.name}</span></span>} pad={false}
                actions={editable && <>
                  <button className="btn btn-ghost btn-sm text-red-600" onClick={() => removeEntity(entity)}>Entität löschen</button>
                  {!entity.master_table && <button className="btn btn-primary btn-sm" onClick={() => setFieldModal({ field: null })}><Icon name="plus" /> Feld</button>}
                </>}>
                {entity.master_table && <div className="px-5 pt-4"><Alert tone="info">Stammdaten-Entität (Tabelle „{entity.master_table}“). Zuordnung per Matching: {entity.match_fields?.join(" → ") || "—"}.</Alert></div>}
                <div className="overflow-x-auto">
                  <table className="table-ra">
                    <thead><tr><th>Feld</th><th>Typ</th><th>Quelle</th><th>Normalisierer</th><th className="w-24" /></tr></thead>
                    <tbody>
                      {entity.fields.map((f) => (
                        <tr key={f.id}>
                          <td>
                            <button type="button" className="text-left" onClick={() => setFieldModal({ field: f })}>
                              <span className="flex items-center gap-1.5 font-medium text-gray-900">{f.is_locked && <Icon name="lock" className="h-3.5 w-3.5 text-gray-400" />}{f.display_name}{f.required && <span className="text-red-500" title="Pflichtfeld">*</span>}</span>
                              <span className="font-mono text-xs text-gray-400">{f.name}</span>
                            </button>
                          </td>
                          <td>{TYPE_LABELS[f.data_type]}</td>
                          <td>{f.source === "system" ? <Badge tone="violet">{f.system_function ?? "System"}</Badge> : <Badge tone="blue">KI</Badge>}</td>
                          <td>{f.normalizers.length ? <Badge tone="gray">{f.normalizers.length} Schritt{f.normalizers.length > 1 ? "e" : ""}</Badge> : <span className="text-gray-300">—</span>}</td>
                          <td className="text-right">
                            {editable && !f.is_locked && <>
                              <button className="btn btn-ghost btn-sm" aria-label={`${f.display_name} bearbeiten`} onClick={() => setFieldModal({ field: f })}><Icon name="edit" /></button>
                              <button className="btn btn-ghost btn-sm text-red-600" aria-label={`${f.display_name} löschen`} onClick={() => removeField(f)}><Icon name="trash" /></button>
                            </>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            ) : <EmptyState title="Keine Entität" text="Lege links die erste Entität an." />}
          </div>
        </div>
      )}

      {fieldModal && <FieldModal field={fieldModal.field} readOnly={!editable} onClose={() => setFieldModal(null)} onSave={saveField} />}
      {entityModal && version && <EntityModal onClose={() => setEntityModal(false)} onSave={async (d) => { const n = await schemaApi.addEntity(version.id, d); setVersion(n); setEntityModal(false); setEntityId(n.entities[n.entities.length - 1].id); }} />}
      {relationModal && version && <RelationModal version={version} onClose={() => setRelationModal(false)} onSave={async (d) => { setVersion(await schemaApi.addRelation(version.id, d)); setRelationModal(false); }} />}
    </div>
  );
}

function EntityModal({ onClose, onSave }: { onClose: () => void; onSave: (d: { name: string; display_name: string; master_table?: string | null; match_fields?: string[] | null }) => Promise<void> }) {
  const [name, setName] = useState("");
  const [display, setDisplay] = useState("");
  const [master, setMaster] = useState(false);
  const [match, setMatch] = useState("iban, ust_id_nr, name");
  const [error, setError] = useState<string | null>(null);
  async function save() {
    try {
      await onSave({ name, display_name: display, master_table: master ? "vendor" : null, match_fields: master ? match.split(",").map((s) => s.trim()).filter(Boolean) : null });
    } catch (err) { setError(extractApiError(err, "Entität konnte nicht angelegt werden")); }
  }
  return (
    <Modal title="Neue Entität" onClose={onClose} footer={<><button className="btn btn-secondary" onClick={onClose}>Abbrechen</button><button className="btn btn-primary" onClick={save}>Anlegen</button></>}>
      <div className="space-y-4">
        {error && <Alert tone="error">{error}</Alert>}
        <div><label className="label" htmlFor="e-name">Technischer Name</label><input id="e-name" className="input font-mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="position" /></div>
        <div><label className="label" htmlFor="e-disp">Anzeigename</label><input id="e-disp" className="input" value={display} onChange={(e) => setDisplay(e.target.value)} placeholder="Position" /></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={master} onChange={(e) => setMaster(e.target.checked)} /> Stammdaten (Lieferant) — wird per Matching zugeordnet</label>
        {master && <div><label className="label" htmlFor="e-match">Matching-Felder in Reihenfolge</label><input id="e-match" className="input font-mono" value={match} onChange={(e) => setMatch(e.target.value)} /><p className="hint">Die Felder müssen in der Entität existieren (erst anlegen, dann zuordnen).</p></div>}
      </div>
    </Modal>
  );
}

function RelationModal({ version, onClose, onSave }: { version: SchemaVersionFull; onClose: () => void; onSave: (d: { name: string; kind: "parent_child" | "reference"; source_entity_id: number; target_entity_id: number }) => Promise<void> }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"parent_child" | "reference">("parent_child");
  const [source, setSource] = useState(version.entities[0]?.id ?? 0);
  const [target, setTarget] = useState(version.entities[1]?.id ?? 0);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    try { await onSave({ name, kind, source_entity_id: source, target_entity_id: target }); } catch (err) { setError(extractApiError(err, "Beziehung konnte nicht angelegt werden")); }
  }
  const opts = version.entities.map((e) => <option key={e.id} value={e.id}>{e.display_name}</option>);
  return (
    <Modal title="Neue Beziehung" onClose={onClose} footer={<><button className="btn btn-secondary" onClick={onClose}>Abbrechen</button><button className="btn btn-primary" onClick={save}>Anlegen</button></>}>
      <div className="space-y-4">
        {error && <Alert tone="error">{error}</Alert>}
        <div><label className="label" htmlFor="r-name">Name</label><input id="r-name" className="input font-mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="positionen" /></div>
        <div><label className="label" htmlFor="r-kind">Art</label>
          <select id="r-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as "parent_child" | "reference")}>
            <option value="parent_child">Eltern → Kind (z. B. Beleg enthält Positionen)</option>
            <option value="reference">Verweis (z. B. Beleg verweist auf Lieferant)</option>
          </select></div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="label" htmlFor="r-src">Von</label><select id="r-src" className="input" value={source} onChange={(e) => setSource(Number(e.target.value))}>{opts}</select></div>
          <div><label className="label" htmlFor="r-tgt">Nach</label><select id="r-tgt" className="input" value={target} onChange={(e) => setTarget(Number(e.target.value))}>{opts}</select></div>
        </div>
      </div>
    </Modal>
  );
}
