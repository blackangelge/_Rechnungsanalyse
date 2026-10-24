"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Badge, Card, Icon, PageHeader, Spinner, Tabs } from "@/components/ui";
import {
  MigrationMappingEntry, MigrationState, MigrationSummary, MigrationTarget, WorkflowListItem,
  extractApiError, mainMigrationApi, workflowsApi,
} from "@/lib/api";

type Tab = "connection" | "mapping" | "run" | "after";

const COUNT_LABELS: Record<string, string> = {
  imported: "übernommen", no_data: "übernommen ohne Daten (keine KI-Antwort)", error: "Fehler", skipped_done: "schon übernommen (übersprungen)",
  pdf_missing: "PDF nicht gefunden", suppliers: "Lieferanten-Stammdaten", supplier_error: "Lieferanten-Fehler",
  rules_ok: "Regeln: in Ordnung", rules_warn: "Regeln: Hinweis", rules_needs_review: "Regeln: Prüfen!", rules_unchecked: "Regeln: nicht prüfbar",
};
const TRANSFORM_LABELS: Record<string, string> = { "": "keine", sum: "Summe", first: "erster Wert", join: "zusammenfügen", price_unit: "Preiseinheit säubern (je t → t)" };

function splitList(text: string): string[] {
  return text.split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
}

export default function MigrationPage() {
  const [tab, setTab] = useState<Tab>("connection");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  return (
    <div>
      <PageHeader title="Übernahme aus MAIN"
        subtitle="Imports, Belege und Lieferanten aus dem MAIN-Projekt übernehmen — ohne KI. Beleg- und Import-IDs bleiben gleich (die PDFs heißen {ID}.pdf). Danach rechnen die Prüfregeln; nur Auffälliges geht erneut an die KI." />
      {error && <Alert tone="error">{error}</Alert>}
      {info && <Alert tone="success">{info}</Alert>}
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { id: "connection", label: "1 · Verbindung" }, { id: "mapping", label: "2 · Übersetzung" },
        { id: "run", label: "3 · Übernahme" }, { id: "after", label: "4 · Nachbearbeitung" },
      ]} />
      {tab === "connection" && <ConnectionTab setError={setError} setInfo={setInfo} />}
      {tab === "mapping" && <MappingTab setError={setError} setInfo={setInfo} />}
      {tab === "run" && <RunTab setError={setError} />}
      {tab === "after" && <AfterTab setError={setError} setInfo={setInfo} />}
    </div>
  );
}

// ─── 1 · Verbindung ───────────────────────────────────────────────────────────

function ConnectionTab({ setError, setInfo }: { setError: (m: string | null) => void; setInfo: (m: string | null) => void }) {
  const [url, setUrl] = useState("");
  const [storage, setStorage] = useState("");
  const [hasUrl, setHasUrl] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Awaited<ReturnType<typeof mainMigrationApi.test>> | null>(null);

  useEffect(() => {
    mainMigrationApi.config().then((c) => { setUrl(c.source_url ?? ""); setStorage(c.source_storage_path ?? ""); setHasUrl(c.has_url); })
      .catch((e) => setError(extractApiError(e, "Konfiguration konnte nicht geladen werden")));
  }, [setError]);

  async function save(andTest: boolean) {
    setBusy(true); setError(null); setInfo(null); setResult(null);
    try {
      const c = await mainMigrationApi.saveConfig({ source_url: url, source_storage_path: storage });
      setUrl(c.source_url ?? ""); setHasUrl(c.has_url);
      if (andTest) setResult(await mainMigrationApi.test()); else setInfo("Gespeichert.");
    } catch (e) { setError(extractApiError(e, "Fehlgeschlagen")); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="space-y-4">
          <div>
            <label className="label" htmlFor="m-url">Datenbank-URL von MAIN (wird nur lesend benutzt)</label>
            <input id="m-url" className="input font-mono" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false}
              placeholder="postgresql://appuser:PASSWORT@HOST:5432/rechnungsanalyse" />
            <p className="hint mt-1">Das Passwort wird nach dem Speichern verborgen. Aus dem Container muss der Rechner mit der MAIN-Datenbank erreichbar sein (Host/Port der MAIN-Datenbank, nicht „db“ des alten Stacks, sofern der Port nach außen freigegeben ist).</p>
          </div>
          <div>
            <label className="label" htmlFor="m-storage">Ordner mit den MAIN-PDFs (im Container sichtbar)</label>
            <input id="m-storage" className="input font-mono" value={storage} onChange={(e) => setStorage(e.target.value)} spellCheck={false}
              placeholder="/app/storage" />
            <p className="hint mt-1">Der kopierte MAIN-Speicherordner mit den Unterordnern <code>{"{Firma}_{Jahr}"}</code> und den Dateien <code>{"{ID}.pdf"}</code>. Ist es derselbe Ordner wie der Speicher dieses Systems, wird nichts doppelt kopiert.</p>
          </div>
          <div className="flex gap-2">
            <button className="btn btn-primary" disabled={busy || (!url && !hasUrl)} onClick={() => save(true)}>{busy ? "Prüft…" : "Speichern und Verbindung testen"}</button>
            <button className="btn btn-secondary" disabled={busy} onClick={() => save(false)}>Nur speichern</button>
          </div>
        </div>
      </Card>
      {result && (
        <Card title="Verbindung steht">
          <div className="grid gap-3 sm:grid-cols-5">
            {Object.entries(result.counts).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-gray-50 p-3"><div className="text-xs text-gray-500">{({ batches: "Imports", documents: "Belege", extractions: "Auslesungen", positions: "Positionen", suppliers: "Lieferanten" } as Record<string, string>)[k] ?? k}</div>
                <div className="text-xl font-semibold tabular-nums">{v.toLocaleString("de-DE")}</div></div>
            ))}
          </div>
          <p className="mt-3 text-sm">
            PDF-Ordner: {result.storage.path ? (result.storage.exists
              ? <Badge tone="green">gefunden{result.storage.sample ? ` · z. B. ${result.storage.sample}` : ""}</Badge> : <Badge tone="red">nicht gefunden</Badge>) : <Badge tone="amber">nicht angegeben (PDFs werden nicht kopiert)</Badge>}
          </p>
        </Card>
      )}
    </div>
  );
}

// ─── 2 · Übersetzung ──────────────────────────────────────────────────────────

function MappingTab({ setError, setInfo }: { setError: (m: string | null) => void; setInfo: (m: string | null) => void }) {
  const [rows, setRows] = useState<MigrationMappingEntry[]>([]);
  const [targets, setTargets] = useState<MigrationTarget[]>([]);
  const [transforms, setTransforms] = useState<string[]>([]);
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [problems, setProblems] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [docId, setDocId] = useState("");
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof mainMigrationApi.preview>> | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([mainMigrationApi.mapping(), mainMigrationApi.targets()]).then(([m, t]) => {
      setRows(m.mapping); setProblems(m.problems); setTargets(t.targets); setTransforms(t.transforms);
    }).catch((e) => setError(extractApiError(e, "Übersetzungstabelle konnte nicht geladen werden"))).finally(() => setLoading(false));
    mainMigrationApi.sourceFields().then(setFields).catch(() => setFields({}));        // ohne Verbindung nur keine Vorschläge
  }, [setError]);

  const byTarget = Object.fromEntries(targets.map((t) => [t.target, t]));
  const used = new Set(rows.map((r) => r.target));
  const open = targets.filter((t) => !used.has(t.target));
  const allSources = Object.values(fields).flat();

  function update(i: number, patch: Partial<MigrationMappingEntry>) { setRows((r) => r.map((x, j) => (j === i ? { ...x, ...patch } : x))); setDirty(true); }

  async function save() {
    setError(null); setInfo(null);
    try { const r = await mainMigrationApi.saveMapping(rows); setProblems(r.problems); setDirty(false); setInfo(r.problems.length ? "Gespeichert — aber mit Hinweisen." : "Gespeichert."); }
    catch (e) { setError(extractApiError(e, "Speichern fehlgeschlagen")); }
  }
  async function loadDefault() {
    if (rows.length && !confirm("Die Tabelle durch den Vorschlag zum aktiven Schema ersetzen?")) return;
    try { const r = await mainMigrationApi.defaultMapping(); setRows(r.mapping); setProblems(r.problems); setDirty(true); }
    catch (e) { setError(extractApiError(e, "Vorschlag nicht möglich")); }
  }
  async function runPreview() {
    setError(null); setPreview(null);
    try { if (dirty) await save(); setPreview(await mainMigrationApi.preview(Number(docId))); }
    catch (e) { setError(extractApiError(e, "Vorschau nicht möglich")); }
  }

  if (loading) return <Spinner />;
  return (
    <div className="space-y-4">
      <Alert tone="info">
        Die Tabelle ordnet jedes Feld des aktiven Schemas einer Quelle aus MAIN zu. Quellen: <code>raw.…</code> (letzte KI-Antwort), <code>ext.…</code> (Auslesung), <code>pos.…</code> / <code>rawpos.…</code> (Position),
        <code>sup.…</code> (Lieferant), <code>doc.…</code> (Beleg), <code>const:Text</code>. Mehrere Quellen mit Komma trennen — die erste gefüllte gewinnt. Ändert sich das Schema, passt du die Tabelle hier an.
      </Alert>
      {problems.length > 0 && <Alert tone="warn"><ul className="list-disc pl-5">{problems.map((p) => <li key={p}>{p}</li>)}</ul></Alert>}
      <datalist id="mig-sources">{allSources.map((s) => <option key={s} value={s} />)}</datalist>
      <Card pad={false} title="Übersetzungstabelle" actions={<>
        <button className="btn btn-secondary" onClick={loadDefault}>Vorschlag zum Schema laden</button>
        <button className="btn btn-primary" disabled={!dirty} onClick={save}>Speichern</button>
      </>}>
        <div className="overflow-x-auto">
          <table className="table-ra">
            <thead><tr><th>Ziel im Schema</th><th>Quellen aus MAIN (Reihenfolge = Vorrang)</th><th>Einheit aus</th><th>Umwandlung</th><th></th></tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const t = byTarget[r.target];
                return (
                  <tr key={r.target + i}>
                    <td className="whitespace-nowrap"><div className="font-medium">{t ? t.label : r.target}</div><div className="hint">{r.target}{t?.required ? " · Pflicht" : ""}{t?.data_type === "quantity" ? " · Menge" : ""}</div></td>
                    <td><input className="input font-mono text-xs" list="mig-sources" value={r.sources.join(", ")} spellCheck={false} onChange={(e) => update(i, { sources: splitList(e.target.value) })} /></td>
                    <td>{t?.data_type === "quantity"
                      ? <input className="input font-mono text-xs" list="mig-sources" value={(r.unit_sources ?? []).join(", ")} spellCheck={false} onChange={(e) => update(i, { unit_sources: splitList(e.target.value) })} />
                      : <span className="text-gray-300">–</span>}</td>
                    <td><select className="input" value={r.transform ?? ""} onChange={(e) => update(i, { transform: e.target.value || null })}>
                      {["", ...transforms].map((x) => <option key={x} value={x}>{TRANSFORM_LABELS[x] ?? x}</option>)}</select></td>
                    <td><button className="btn btn-ghost" aria-label="Zeile entfernen" onClick={() => { setRows((x) => x.filter((_, j) => j !== i)); setDirty(true); }}><Icon name="trash" /></button></td>
                  </tr>
                );
              })}
              {!rows.length && <tr><td colSpan={5} className="text-center text-gray-500">Noch leer — „Vorschlag zum Schema laden“ füllt sie passend zum aktiven Schema.</td></tr>}
            </tbody>
          </table>
        </div>
        {open.length > 0 && (
          <div className="border-t border-gray-100 p-3">
            <label className="label" htmlFor="m-add">Zeile für ein weiteres Feld</label>
            <select id="m-add" className="input w-72" value="" onChange={(e) => { if (e.target.value) { setRows((x) => [...x, { target: e.target.value, sources: [], unit_sources: [], transform: null }]); setDirty(true); } }}>
              <option value="">— Feld wählen ({open.length} ohne Zeile) —</option>
              {open.map((t) => <option key={t.target} value={t.target}>{t.entity_label} · {t.label}</option>)}
            </select>
          </div>
        )}
      </Card>
      <Card title="Ausprobieren an einem MAIN-Beleg">
        <div className="flex gap-2">
          <input className="input w-48" placeholder="Beleg-ID aus MAIN" value={docId} onChange={(e) => setDocId(e.target.value.replace(/\D/g, ""))} />
          <button className="btn btn-secondary" disabled={!docId} onClick={runPreview}>Übersetzen</button>
        </div>
        {preview && (
          <div className="mt-3 space-y-2">
            {!preview.has_raw_response && <Alert tone="warn">Keine verwertbare KI-Antwort — der Beleg würde ohne Daten übernommen.</Alert>}
            {preview.schema_errors.length > 0 && <Alert tone="error"><ul className="list-disc pl-5">{preview.schema_errors.map((m) => <li key={m}>{m}</li>)}</ul></Alert>}
            {preview.parsed && preview.schema_errors.length === 0 && <Alert tone="success">Das übersetzte Ergebnis passt zum Schema.</Alert>}
            {preview.parsed && <pre className="max-h-96 overflow-auto rounded-lg bg-gray-50 p-3 text-xs">{JSON.stringify(preview.parsed, null, 2)}</pre>}
          </div>
        )}
      </Card>
    </div>
  );
}

// ─── 3 · Übernahme ────────────────────────────────────────────────────────────

function RunTab({ setError }: { setError: (m: string | null) => void }) {
  const [state, setState] = useState<MigrationState | null>(null);
  const [dry, setDry] = useState(true);
  const [limit, setLimit] = useState("100");
  const [batchIds, setBatchIds] = useState("");
  const [docIds, setDocIds] = useState("");
  const [deleted, setDeleted] = useState(true);
  const [suppliers, setSuppliers] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async () => { try { setState(await mainMigrationApi.state()); } catch { /* nächster Versuch */ } }, []);
  useEffect(() => { poll(); timer.current = setInterval(poll, 2000); return () => { if (timer.current) clearInterval(timer.current); }; }, [poll]);

  const nums = (t: string) => { const v = t.split(/[\s,;]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0); return v.length ? v : null; };

  async function start() {
    setError(null);
    if (!dry && !confirm("Jetzt wirklich übernehmen? Belege, Datensätze und Lieferanten werden im neuen System angelegt (Beleg-IDs bleiben gleich).")) return;
    try {
      setState(await mainMigrationApi.run({ dry_run: dry, limit: limit ? Number(limit) : null, old_batch_ids: nums(batchIds), old_document_ids: nums(docIds), include_deleted: deleted, suppliers }));
    } catch (e) { setError(extractApiError(e, "Start fehlgeschlagen")); }
  }

  const running = !!state?.running;
  const pct = state?.total ? Math.min(100, Math.round(((state.processed ?? 0) / state.total) * 100)) : 0;
  return (
    <div className="space-y-4">
      <Card title="Lauf starten">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="flex items-start gap-2 text-sm sm:col-span-2"><input type="checkbox" className="mt-1" checked={dry} onChange={(e) => setDry(e.target.checked)} />
            <span><b>Trockenlauf</b> — alles durchrechnen und berichten, aber <b>nichts speichern</b> (empfohlen als Erstes).</span></label>
          <div><label className="label" htmlFor="m-limit">Höchstens so viele Belege (Stichprobe, leer = alle)</label><input id="m-limit" className="input" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} /></div>
          <div><label className="label" htmlFor="m-batches">Nur diese MAIN-Import-IDs (optional)</label><input id="m-batches" className="input" placeholder="z. B. 3, 4" value={batchIds} onChange={(e) => setBatchIds(e.target.value)} /></div>
          <div><label className="label" htmlFor="m-docs">Nur diese MAIN-Beleg-IDs (optional)</label><input id="m-docs" className="input" placeholder="z. B. 101, 102" value={docIds} onChange={(e) => setDocIds(e.target.value)} /></div>
          <div className="space-y-1 text-sm">
            <label className="flex items-center gap-2"><input type="checkbox" checked={deleted} onChange={(e) => setDeleted(e.target.checked)} /> in MAIN gelöschte Belege mitnehmen (landen im Papierkorb)</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={suppliers} onChange={(e) => setSuppliers(e.target.checked)} /> Lieferanten-Stammdaten aus MAIN vorab übernehmen</label>
          </div>
        </div>
        <div className="mt-4 flex gap-2">
          <button className="btn btn-primary" disabled={running} onClick={start}>{dry ? "Trockenlauf starten" : "Übernahme starten"}</button>
          {running && <button className="btn btn-secondary" onClick={() => mainMigrationApi.cancel().then(poll)}>Abbrechen</button>}
        </div>
        <p className="hint mt-2">Schon übernommene Belege werden übersprungen — der Lauf lässt sich beliebig wiederholen oder nach einem Abbruch fortsetzen.</p>
      </Card>
      {state && (state.running || state.counts || state.message) && (
        <Card title={running ? (state.dry_run ? "Trockenlauf läuft …" : "Übernahme läuft …") : (state.dry_run ? "Ergebnis des Trockenlaufs" : "Ergebnis")}>
          {state.message && <p className="mb-2 text-sm text-gray-700">{state.message}</p>}
          {!!state.total && <div className="mb-3"><div className="h-2 overflow-hidden rounded-full bg-gray-100"><div className="h-full bg-blue-600 transition-all" style={{ width: `${pct}%` }} /></div>
            <p className="mt-1 text-xs text-gray-500">{(state.processed ?? 0).toLocaleString("de-DE")} von {state.total.toLocaleString("de-DE")} Belegen</p></div>}
          <div className="grid gap-2 sm:grid-cols-3">
            {Object.entries(state.counts ?? {}).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-gray-50 p-2 text-sm"><span className="font-semibold tabular-nums">{v.toLocaleString("de-DE")}</span> <span className="text-gray-600">{COUNT_LABELS[k] ?? k}</span></div>
            ))}
          </div>
          {!!state.errors?.length && <Alert tone="error"><div className="font-medium">Fehler (erste {state.errors.length})</div><ul className="list-disc pl-5 text-xs">{state.errors.map((m, i) => <li key={i}>{m}</li>)}</ul></Alert>}
        </Card>
      )}
    </div>
  );
}

// ─── 4 · Nachbearbeitung ──────────────────────────────────────────────────────

function AfterTab({ setError, setInfo }: { setError: (m: string | null) => void; setInfo: (m: string | null) => void }) {
  const [sum, setSum] = useState<MigrationSummary | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [wf, setWf] = useState("");
  const [inc, setInc] = useState<Record<string, boolean>>({ needs_review: true, warn: false, unchecked: false, no_data: true });
  const [limit, setLimit] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    mainMigrationApi.summary().then(setSum).catch((e) => setError(extractApiError(e, "Stand konnte nicht geladen werden")));
    workflowsApi.list().then(setWorkflows).catch(() => setWorkflows([]));
  }, [setError]);

  const chosen = Object.entries(inc).filter(([, v]) => v).map(([k]) => k);
  async function send() {
    if (!wf) return;
    if (!confirm("Die gewählten Belege jetzt zur KI-Analyse einreihen?")) return;
    setBusy(true); setError(null); setInfo(null);
    try { const r = await mainMigrationApi.reanalyze({ workflow_id: Number(wf), include: chosen, limit: limit ? Number(limit) : null }); setInfo(r.message ?? `${r.enqueued} Beleg(e) eingereiht.`); }
    catch (e) { setError(extractApiError(e, "Einreihen fehlgeschlagen")); } finally { setBusy(false); }
  }

  if (!sum) return <Spinner />;
  const n = (k: string) => (sum.rules as Record<string, number>)[k] ?? 0;
  return (
    <div className="space-y-4">
      <Card title="Stand der Übernahme">
        <div className="grid gap-3 sm:grid-cols-4">
          {[["Belege übernommen", sum.documents.imported ?? 0], ["ohne Daten (keine KI-Antwort)", sum.documents.no_data ?? 0], ["Imports", sum.batches], ["Lieferanten (Stammdaten)", sum.suppliers]].map(([l, v]) => (
            <div key={String(l)} className="rounded-lg bg-gray-50 p-3"><div className="text-xs text-gray-500">{l}</div><div className="text-xl font-semibold tabular-nums">{Number(v).toLocaleString("de-DE")}</div></div>
          ))}
        </div>
        <h3 className="mb-1 mt-4 text-sm font-semibold">Ergebnis der Prüfregeln über die übernommenen Belege</h3>
        <div className="flex flex-wrap gap-2 text-sm">
          <Badge tone="green">in Ordnung · {n("ok")}</Badge><Badge tone="amber">Hinweis · {n("warn")}</Badge><Badge tone="red">Prüfen! · {n("needs_review")}</Badge>
          <Badge tone="gray">nicht prüfbar · {n("unchecked")}</Badge><Badge tone="gray">ohne Regel-Ergebnis · {n("none")}</Badge>
        </div>
      </Card>
      <Card title="Auffällige Belege erneut an die KI">
        <div className="space-y-3">
          <div className="flex flex-wrap gap-4 text-sm">
            {([["needs_review", `Prüfen! (${n("needs_review")})`], ["warn", `Hinweis (${n("warn")})`], ["unchecked", `nicht prüfbar (${n("unchecked")})`], ["no_data", `ohne Daten (${sum.documents.no_data ?? 0})`]] as const).map(([k, l]) => (
              <label key={k} className="flex items-center gap-2"><input type="checkbox" checked={!!inc[k]} onChange={(e) => setInc((x) => ({ ...x, [k]: e.target.checked }))} /> {l}</label>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div><label className="label" htmlFor="a-wf">Workflow</label>
              <select id="a-wf" className="input" value={wf} onChange={(e) => setWf(e.target.value)}><option value="">— Workflow wählen —</option>
                {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></div>
            <div><label className="label" htmlFor="a-lim">Höchstens (optional)</label><input id="a-lim" className="input" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} /></div>
          </div>
          <button className="btn btn-primary" disabled={!wf || !chosen.length || busy} onClick={send}>{busy ? "Reiht ein…" : "An die KI senden"}</button>
          <p className="hint">Die Belege laufen mit dem gewählten Workflow durch die KI. Die bisherige Auslesung bleibt in der Analyse-Historie. Ob alle Treffer in den Prüfregel-Zahlen zählen, siehst du danach auf der Belege-Seite (Filter „Plausibilität“).</p>
        </div>
      </Card>
    </div>
  );
}
