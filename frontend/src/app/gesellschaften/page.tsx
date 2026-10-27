"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Card, EmptyState, Icon, Modal, PageHeader, Spinner } from "@/components/ui";
import { OwnCompany, OwnCompanyInput, OwnCompanySuggestion, extractApiError, ownCompaniesApi } from "@/lib/api";

const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);

function CompanyModal({ company, preset, onClose, onSaved }: {
  company: OwnCompany | null; preset?: Partial<OwnCompanyInput>; onClose: () => void; onSaved: () => void;
}) {
  const init = company ?? { name: null, aliases: [], vat_ids: [], tax_numbers: [], ibans: [], note: null, active: true, ...preset };
  const [name, setName] = useState(init.name ?? "");
  const [aliases, setAliases] = useState((init.aliases ?? []).join("\n"));
  const [vat, setVat] = useState((init.vat_ids ?? []).join("\n"));
  const [tax, setTax] = useState((init.tax_numbers ?? []).join("\n"));
  const [ibans, setIbans] = useState((init.ibans ?? []).join("\n"));
  const [note, setNote] = useState(init.note ?? "");
  const [active, setActive] = useState(init.active ?? true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true); setError(null);
    const body: OwnCompanyInput = { name: name.trim() || null, aliases: lines(aliases), vat_ids: lines(vat), tax_numbers: lines(tax), ibans: lines(ibans), note: note.trim() || null, active };
    try {
      if (company) await ownCompaniesApi.update(company.id, body); else await ownCompaniesApi.create(body);
      onSaved();
    } catch (e) { setError(extractApiError(e, "Speichern fehlgeschlagen")); } finally { setSaving(false); }
  }

  const area = "input font-mono text-sm min-h-[4.5rem]";
  return (
    <Modal title={company ? "Gesellschaft bearbeiten" : "Eigene Gesellschaft anlegen"} onClose={onClose}
      footer={<><button className="btn btn-secondary" onClick={onClose}>Abbrechen</button><button className="btn btn-primary" disabled={saving} onClick={save}>{saving ? "Speichert…" : "Speichern"}</button></>}>
      <div className="space-y-4">
        {error && <Alert tone="error">{error}</Alert>}
        <p className="hint">Alle Angaben sind optional — mindestens eine Kennung (Name, Schreibweise, USt-IdNr., Steuernummer oder IBAN) ist nötig. Mehrere Werte: je einer pro Zeile.</p>
        <div>
          <label className="label" htmlFor="g-name">Name</label>
          <input id="g-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="z. B. Muster Handels GmbH" />
        </div>
        <div>
          <label className="label" htmlFor="g-alias">Weitere Schreibweisen / frühere Namen</label>
          <textarea id="g-alias" className={area} value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder={"Muster Handel GmbH & Co. KG\nMuster Handels"} />
          <p className="hint mt-1">Rechtsform, Groß/Klein, Umlaute und Satzzeichen spielen beim Vergleich keine Rolle; kleine Tippfehler werden toleriert.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div><label className="label" htmlFor="g-vat">USt-IdNr.</label><textarea id="g-vat" className={area} value={vat} onChange={(e) => setVat(e.target.value)} placeholder="DE123456789" /></div>
          <div><label className="label" htmlFor="g-tax">Steuernummer</label><textarea id="g-tax" className={area} value={tax} onChange={(e) => setTax(e.target.value)} /></div>
        </div>
        <div>
          <label className="label" htmlFor="g-iban">IBAN(s)</label>
          <textarea id="g-iban" className={area} value={ibans} onChange={(e) => setIbans(e.target.value)} placeholder="DE02 1203 0000 0000 2020 51" />
        </div>
        <div>
          <label className="label" htmlFor="g-note">Notiz</label>
          <input id="g-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /> Aktiv (wird beim Prüfen berücksichtigt)</label>
      </div>
    </Modal>
  );
}

export default function GesellschaftenPage() {
  const [list, setList] = useState<OwnCompany[]>([]);
  const [suggestions, setSuggestions] = useState<OwnCompanySuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<{ company: OwnCompany | null; preset?: Partial<OwnCompanyInput> } | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [l, s] = await Promise.all([ownCompaniesApi.list(), ownCompaniesApi.suggestions().catch(() => [])]);
      setList(l); setSuggestions(s);
    } catch (e) { setError(extractApiError(e, "Liste konnte nicht geladen werden")); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function remove(c: OwnCompany) {
    if (!confirm(`„${c.name ?? "Gesellschaft"}“ aus der Liste löschen?`)) return;
    try { await ownCompaniesApi.remove(c.id); await load(); } catch (e) { setError(extractApiError(e, "Löschen fehlgeschlagen")); }
  }

  return (
    <div>
      <PageHeader title="Eigene Gesellschaften"
        subtitle="Firmen, an die Rechnungen gerichtet sein können. Das System markiert Belege als „Prüfen!“, wenn als Lieferant eine dieser Firmen gelesen wurde (Empfänger und Lieferant vertauscht) — es wird nichts automatisch geändert."
        actions={<button className="btn btn-primary" onClick={() => setEdit({ company: null })}><Icon name="plus" /> Neue Gesellschaft</button>} />
      {error && <Alert tone="error">{error}</Alert>}
      {loading && !list.length && <Spinner />}

      {suggestions.length > 0 && (
        <Card title="Vorschläge aus den gelesenen Belegen">
          <p className="hint mb-3">Diese Rechnungsempfänger kommen auffällig oft vor und stehen noch nicht in der Liste — vermutlich eigene Gesellschaften.</p>
          <ul className="divide-y divide-gray-100">
            {suggestions.map((s) => (
              <li key={s.name} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div>
                  <span className="font-medium">{s.name}</span> <Badge tone="blue">{s.count.toLocaleString("de-DE")} Belege</Badge>
                  <div className="text-xs text-gray-500">{[s.vat_id && `USt-IdNr. ${s.vat_id}`, s.aliases.length ? `auch: ${s.aliases.join("; ")}` : ""].filter(Boolean).join(" · ")}</div>
                </div>
                <button className="btn btn-secondary" onClick={() => setEdit({ company: null, preset: { name: s.name, aliases: s.aliases, vat_ids: s.vat_id ? [s.vat_id] : [] } })}>Übernehmen…</button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="mt-4">
        {!loading && !list.length ? (
          <EmptyState title="Noch keine eigene Gesellschaft" text="Lege deine Firmen an (oder übernimm die Vorschläge). Solange die Liste leer ist, wird nur geprüft, ob Lieferant und Empfänger dieselbe Firma sind." />
        ) : (
          <Card pad={false}>
            <table className="table-ra w-full">
              <thead><tr><th>Name</th><th>Weitere Schreibweisen</th><th>USt-IdNr.</th><th>IBANs</th><th>Status</th><th /></tr></thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.id}>
                    <td className="font-medium">{c.name ?? <span className="text-gray-400">(ohne Namen)</span>}{c.note && <div className="text-xs font-normal text-gray-500">{c.note}</div>}</td>
                    <td className="text-sm">{c.aliases.join(", ") || "–"}</td>
                    <td className="font-mono text-xs">{c.vat_ids.join(", ") || "–"}</td>
                    <td className="font-mono text-xs">{c.ibans.length ? `${c.ibans.length} IBAN${c.ibans.length > 1 ? "s" : ""}` : "–"}</td>
                    <td>{c.active ? <Badge tone="green">aktiv</Badge> : <Badge tone="gray">inaktiv</Badge>}</td>
                    <td className="whitespace-nowrap text-right">
                      <button className="btn btn-ghost" onClick={() => setEdit({ company: c })}>Bearbeiten</button>
                      <button className="btn btn-ghost" onClick={() => remove(c)}>Löschen</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>
      <p className="hint mt-3">Nach Änderungen an der Liste: Seite „Prüfregeln“ → „Alle Belege neu prüfen“, damit bestehende Belege neu bewertet werden.</p>
      {edit && <CompanyModal company={edit.company} preset={edit.preset} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}
