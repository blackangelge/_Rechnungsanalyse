"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Card, EmptyState, Icon, Modal, PageHeader, Spinner, Tabs } from "@/components/ui";
import { UnitGroupItem, UnitItem, UnitReviewGroup, extractApiError, schemaApi, unitsApi } from "@/lib/api";

export default function UnitsPage() {
  const [tab, setTab] = useState<"groups" | "review">("groups");
  const [groups, setGroups] = useState<UnitGroupItem[]>([]);
  const [review, setReview] = useState<UnitReviewGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [groupModal, setGroupModal] = useState(false);
  const [unitModal, setUnitModal] = useState<UnitGroupItem | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [g, r] = await Promise.all([unitsApi.list(), unitsApi.review()]);
      setGroups(g);
      setReview(r);
    } catch (err) {
      setError(extractApiError(err, "Einheiten konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); await load(); } catch (err) { setError(extractApiError(err, "Aktion fehlgeschlagen")); }
  }

  async function seedDefaults() {
    setSeeding(true);
    setError(null);
    try {
      const r = await schemaApi.seed();
      setInfo([
        r.units_created ? "Standard-Einheiten angelegt" : null,
        r.schema_created ? `Ausgangsschema (Version ${r.schema_version}) angelegt und aktiviert` : null,
        r.templates_created ? "Export-Vorlagen angelegt" : null,
      ].filter(Boolean).join(" · ") || "Es war schon alles vorhanden — nichts geändert.");
      await load();
    } catch (err) {
      setError(extractApiError(err, "Einspielen fehlgeschlagen"));
    } finally {
      setSeeding(false);
    }
  }

  const allUnits = groups.flatMap((g) => g.units.map((u) => ({ ...u, group: g.name })));

  return (
    <div>
      <PageHeader title="Einheiten"
        subtitle="Gruppen mit genau einer Basiseinheit (Faktor 1). Der Faktor sagt, wie viele dieser Einheit auf 1 Basiseinheit gehen (Basis Tonne: kg = 1000). Unbekannte Schreibweisen werden nie geraten, sondern landen auf der Prüfliste."
        actions={tab === "groups" ? <button className="btn btn-primary" onClick={() => setGroupModal(true)}><Icon name="plus" /> Gruppe</button> : undefined} />
      {error && <div className="mb-4"><Alert tone="error" onClose={() => setError(null)}>{error}</Alert></div>}
      {info && <div className="mb-4"><Alert tone="success" onClose={() => setInfo(null)}>{info}</Alert></div>}
      <Tabs tabs={[{ id: "groups", label: "Gruppen & Einheiten" }, { id: "review", label: "Prüfliste", badge: review.length ? <span className="badge badge-amber">{review.length}</span> : null }]} value={tab} onChange={setTab} />
      {loading && groups.length === 0 && <Spinner />}

      {tab === "groups" && (
        <div className="space-y-4">
          {!loading && groups.length === 0 && (
            <EmptyState title="Keine Einheiten-Gruppen"
              text="Spiele die Standard-Einheiten ein (Masse, Volumen, Länge, Fläche, Anzahl, Verpackung). Dabei werden, falls noch nicht vorhanden, auch das Ausgangsschema und zwei Export-Vorlagen angelegt."
              action={<button className="btn btn-primary" disabled={seeding} onClick={seedDefaults}>{seeding ? "Wird eingespielt …" : "Standard-Einheiten und Schema einspielen"}</button>} />
          )}
          {groups.map((g) => (
            <Card key={g.id} title={g.name} pad={false}
              actions={<>
                <button className="btn btn-secondary btn-sm" onClick={() => setUnitModal(g)}><Icon name="plus" /> Einheit</button>
                <button className="btn btn-ghost btn-sm text-red-600" aria-label={`Gruppe ${g.name} löschen`} onClick={() => confirm(`Gruppe „${g.name}“ löschen?`) && run(() => unitsApi.deleteGroup(g.id))}><Icon name="trash" /></button>
              </>}>
              <div className="overflow-x-auto">
                <table className="table-ra">
                  <thead><tr><th>Einheit</th><th>Symbol</th><th>Faktor</th><th>Schreibweisen (Aliasse)</th><th className="w-12" /></tr></thead>
                  <tbody>
                    {g.units.map((u) => <UnitRow key={u.id} unit={u} onChange={run} />)}
                    {g.units.length === 0 && <tr><td colSpan={5} className="text-gray-400">Noch keine Einheit.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      )}

      {tab === "review" && (
        <Card title="Unbekannte Einheiten" pad={false}>
          {review.length === 0 ? <div className="p-5"><p className="text-sm text-gray-500">Nichts zu prüfen — alle gelesenen Einheiten sind bekannt.</p></div> : (
            <table className="table-ra">
              <thead><tr><th>Schreibweise</th><th>Vorkommen</th><th>Belege</th><th>Zuordnen zu</th><th className="w-24" /></tr></thead>
              <tbody>
                {review.map((r) => <ReviewRow key={r.alias} item={r} units={allUnits} onRun={run} />)}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {groupModal && <NameModal title="Neue Gruppe" onClose={() => setGroupModal(false)} onSave={async (name) => { await unitsApi.createGroup(name); setGroupModal(false); await load(); }} />}
      {unitModal && <UnitModal group={unitModal} onClose={() => setUnitModal(null)} onSave={async (d) => { await unitsApi.createUnit(unitModal.id, d); setUnitModal(null); await load(); }} />}
    </div>
  );
}

function UnitRow({ unit, onChange }: { unit: UnitItem; onChange: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [alias, setAlias] = useState("");
  return (
    <tr>
      <td className="font-medium">{unit.name} {unit.is_base && <Badge tone="blue">Basis</Badge>}</td>
      <td className="font-mono">{unit.symbol}</td>
      <td className="font-mono">{unit.factor}</td>
      <td>
        <div className="flex flex-wrap items-center gap-1.5">
          {unit.aliases.map((a) => (
            <span key={a.id} className="badge badge-gray">{a.alias}
              <button type="button" aria-label={`Alias ${a.alias} entfernen`} className="ml-0.5 text-gray-400 hover:text-red-600" onClick={() => onChange(() => unitsApi.deleteAlias(a.id))}>×</button>
            </span>
          ))}
          <form className="inline-flex" onSubmit={(e) => { e.preventDefault(); if (alias.trim()) { const a = alias.trim(); setAlias(""); onChange(() => unitsApi.addAlias(unit.id, a)); } }}>
            <input aria-label={`Neuer Alias für ${unit.name}`} className="input w-28 py-1 text-xs" placeholder="+ Alias" value={alias} onChange={(e) => setAlias(e.target.value)} />
          </form>
        </div>
      </td>
      <td className="text-right">
        {!unit.is_base && <button className="btn btn-ghost btn-sm text-red-600" aria-label={`${unit.name} löschen`} onClick={() => confirm(`Einheit „${unit.name}“ löschen?`) && onChange(() => unitsApi.deleteUnit(unit.id))}><Icon name="trash" /></button>}
      </td>
    </tr>
  );
}

function ReviewRow({ item, units, onRun }: { item: UnitReviewGroup; units: (UnitItem & { group: string })[]; onRun: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [unitId, setUnitId] = useState("");
  return (
    <tr>
      <td className="font-mono font-medium">{item.alias || "(leer)"}</td>
      <td>{item.count}</td>
      <td>{item.documents}</td>
      <td>
        <select aria-label={`Einheit für ${item.alias}`} className="input py-1" value={unitId} onChange={(e) => setUnitId(e.target.value)}>
          <option value="">— Einheit wählen —</option>
          {units.map((u) => <option key={u.id} value={u.id}>{u.group}: {u.name} ({u.symbol})</option>)}
        </select>
      </td>
      <td className="whitespace-nowrap text-right">
        <button className="btn btn-primary btn-sm" disabled={!unitId} onClick={() => onRun(() => unitsApi.assign(item.alias, Number(unitId)))}>Zuordnen</button>
        <button className="btn btn-ghost btn-sm" title="Schreibweise ignorieren" onClick={() => onRun(() => unitsApi.ignore(item.alias))}>Ignorieren</button>
      </td>
    </tr>
  );
}

function NameModal({ title, onClose, onSave }: { title: string; onClose: () => void; onSave: (name: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal title={title} onClose={onClose} footer={<><button className="btn btn-secondary" onClick={onClose}>Abbrechen</button>
      <button className="btn btn-primary" disabled={!name.trim()} onClick={async () => { try { await onSave(name.trim()); } catch (e) { setError(extractApiError(e, "Fehler")); } }}>Anlegen</button></>}>
      {error && <div className="mb-3"><Alert tone="error">{error}</Alert></div>}
      <label className="label" htmlFor="n">Name</label><input id="n" className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
    </Modal>
  );
}

function UnitModal({ group, onClose, onSave }: { group: UnitGroupItem; onClose: () => void; onSave: (d: { name: string; symbol: string; factor: number; is_base: boolean; case_insensitive: boolean; aliases: string[] }) => Promise<void> }) {
  const hasBase = group.units.some((u) => u.is_base);
  const [name, setName] = useState("");
  const [symbol, setSymbol] = useState("");
  const [factor, setFactor] = useState(hasBase ? "1" : "1");
  const [isBase, setIsBase] = useState(!hasBase);
  const [ci, setCi] = useState(true);
  const [aliases, setAliases] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function save() {
    try {
      await onSave({ name: name.trim(), symbol: symbol.trim(), factor: isBase ? 1 : Number(factor.replace(",", ".")), is_base: isBase, case_insensitive: ci, aliases: aliases.split(",").map((s) => s.trim()).filter(Boolean) });
    } catch (e) { setError(extractApiError(e, "Einheit konnte nicht angelegt werden")); }
  }
  return (
    <Modal title={`Neue Einheit in „${group.name}“`} onClose={onClose} footer={<><button className="btn btn-secondary" onClick={onClose}>Abbrechen</button><button className="btn btn-primary" disabled={!name.trim() || !symbol.trim()} onClick={save}>Anlegen</button></>}>
      <div className="grid gap-4 sm:grid-cols-2">
        {error && <div className="sm:col-span-2"><Alert tone="error">{error}</Alert></div>}
        <div><label className="label" htmlFor="u-name">Name</label><input id="u-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Kilogramm" /></div>
        <div><label className="label" htmlFor="u-sym">Symbol</label><input id="u-sym" className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="kg" /></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={isBase} disabled={hasBase} onChange={(e) => setIsBase(e.target.checked)} /> Basiseinheit {hasBase && <span className="text-xs text-gray-400">(gibt es schon)</span>}</label>
        {!isBase && <div><label className="label" htmlFor="u-f">Faktor (pro 1 Basiseinheit)</label><input id="u-f" className="input font-mono" value={factor} onChange={(e) => setFactor(e.target.value)} /></div>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={ci} onChange={(e) => setCi(e.target.checked)} /> Groß-/Kleinschreibung egal</label>
        <div className="sm:col-span-2"><label className="label" htmlFor="u-al">Aliasse (Komma-getrennt)</label><input id="u-al" className="input" value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="Kilo, KG, kgs" /></div>
      </div>
    </Modal>
  );
}
