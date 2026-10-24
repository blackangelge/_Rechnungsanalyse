"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  BackupFile, BackupJobState, ImportBatch, RestoreOptions, dataBackupApi, extractApiError, importsApi,
} from "@/lib/api";

const fmtSize = (b: number) => (b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
const fmtDate = (s: string) => new Date(s).toLocaleString("de-DE");
const TABLE_LABEL: Record<string, string> = {
  import_batches: "Imports", documents: "Belege", invoice_extractions: "Rechnungsdaten (KI)", order_positions: "Positionen",
  document_versions: "PDF-Versionen", analysis_history: "Analyse-Historie", documents_token_counts: "Token-Statistik",
  vendor: "Lieferanten", vendor_bank_accounts: "Bankverbindungen",
};

function Progress({ job, onCancel }: { job: BackupJobState; onCancel: () => void }) {
  const pct = job.total ? Math.min(100, Math.round(((job.done ?? 0) / job.total) * 100)) : 0;
  return (
    <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-4" data-testid="job-progress">
      <div className="mb-1 flex items-center justify-between text-sm text-blue-900">
        <span className="font-medium">{job.kind === "backup" ? "Backup läuft" : job.dry_run ? "Vorschau läuft" : "Import läuft"} — {job.phase}</span>
        <button onClick={onCancel} className="btn btn-secondary btn-sm">Abbrechen</button>
      </div>
      <div className="h-2 overflow-hidden rounded bg-blue-100"><div className="h-2 bg-blue-600 transition-all" style={{ width: `${pct}%` }} /></div>
      <div className="mt-1 text-xs text-blue-700">{(job.done ?? 0).toLocaleString("de-DE")} / {(job.total ?? 0).toLocaleString("de-DE")}{job.files_done ? ` · ${job.files_done.toLocaleString("de-DE")} PDFs` : ""}</div>
    </div>
  );
}

function ResultBox({ job }: { job: BackupJobState }) {
  if (job.running || !job.kind) return null;
  const r = job.result;
  const ok = !job.error && !job.cancelled;
  return (
    <div className={`mb-4 rounded-lg border p-4 text-sm ${job.error ? "border-red-200 bg-red-50 text-red-800" : job.cancelled ? "border-amber-200 bg-amber-50 text-amber-800" : "border-emerald-200 bg-emerald-50 text-emerald-900"}`} data-testid="job-result">
      <div className="font-medium">
        {job.kind === "backup" ? "Backup" : job.dry_run ? "Vorschau (nichts gespeichert)" : "Import"}: {job.error ? `Fehlgeschlagen — ${job.error}` : job.cancelled ? "abgebrochen (nichts geändert)" : "erfolgreich"}
      </div>
      {ok && r?.name && <div className="mt-1">Datei: <b>{r.name}</b> ({fmtSize(r.size ?? 0)})</div>}
      {ok && (r?.counts || r?.tables) && (
        <ul className="mt-1 list-inside list-disc">
          {Object.entries(r.counts ?? r.tables ?? {}).map(([k, n]) => <li key={k}>{TABLE_LABEL[k] ?? k}: {n.toLocaleString("de-DE")}</li>)}
        </ul>
      )}
      {ok && r?.files && Object.keys(r.files).length > 0 && (
        <div className="mt-1 text-xs">PDF-Dateien: {Object.entries(r.files).map(([k, n]) => `${k} ${n}`).join(" · ")}</div>
      )}
      {r?.skipped_batches?.map((b) => <div key={b.id} className="mt-1 text-xs">Übersprungen: Import #{b.id} — {b.reason}</div>)}
      {job.warnings?.map((w, i) => <div key={i} className="mt-1 text-xs text-amber-800">⚠ {w}</div>)}
    </div>
  );
}

function RestoreDialog({ backup, onClose, onStarted }: { backup: BackupFile; onClose: () => void; onStarted: () => void }) {
  const m = backup.manifest!;
  const [mode, setMode] = useState<"preserve" | "remap">("remap");
  const [selected, setSelected] = useState<Set<number>>(new Set(m.batches.map((b) => b.id)));
  const [restoreFiles, setRestoreFiles] = useState(m.include_pdfs);
  const [vendors, setVendors] = useState(m.include_vendors);
  const [dups, setDups] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start(dry: boolean) {
    if (!dry && !window.confirm(
      `${selected.size} Import(s) aus „${backup.name}“ ${mode === "preserve" ? "mit Original-IDs wiederherstellen" : "als neue Einträge importieren"}?\n\n` +
      "Vorhandene Daten werden nicht überschrieben. Schlägt etwas fehl, wird nichts geändert. Empfehlung: vorher die Vorschau laufen lassen.")) return;
    setBusy(true);
    setError(null);
    const opts: RestoreOptions = {
      mode, batch_ids: selected.size === m.batches.length ? null : Array.from(selected),
      restore_files: restoreFiles, include_vendors: vendors, allow_duplicates: dups, dry_run: dry,
    };
    try { await dataBackupApi.restore(backup.name, opts); onStarted(); }
    catch (e) { setError(extractApiError(e, "Start fehlgeschlagen")); }
    finally { setBusy(false); }
  }

  const radio = "mt-0.5";
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
      <div className="w-full max-w-2xl rounded-xl bg-white p-5 shadow-pop" data-testid="restore-dialog">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">Importieren aus {backup.name}</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">✕</button>
        </div>
        {error && <div className="mb-3 rounded-lg border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        <fieldset className="mb-4 space-y-2 text-sm text-gray-800">
          <legend className="mb-1 text-xs font-medium uppercase text-gray-500">Wie importieren?</legend>
          <label className="flex gap-2"><input type="radio" className={radio} checked={mode === "remap"} onChange={() => setMode("remap")} />
            <span><b>Als neue Einträge (neue IDs)</b> — zum Zusammenführen in den laufenden Bestand. Nichts wird überschrieben; bereits vorhandene Imports (gleiche Firma, Jahr, Zeitpunkt) werden übersprungen. PDFs kommen aus dem Backup.</span></label>
          <label className="flex gap-2"><input type="radio" className={radio} checked={mode === "preserve"} onChange={() => setMode("preserve")} />
            <span><b>Original-IDs behalten</b> — für die Wiederherstellung nach Datenverlust. Die vorhandenen PDF-Dateien im Speicher passen dann weiter. Bei ID-Konflikt bricht der Import ab.</span></label>
        </fieldset>
        <div className="mb-4">
          <div className="mb-1 flex items-center justify-between text-xs font-medium uppercase text-gray-500">
            <span>Imports</span>
            <span className="space-x-2 normal-case">
              <button className="text-blue-600 hover:underline" onClick={() => setSelected(new Set(m.batches.map((b) => b.id)))}>alle</button>
              <button className="text-blue-600 hover:underline" onClick={() => setSelected(new Set())}>keine</button>
            </span>
          </div>
          <div className="max-h-48 overflow-y-auto rounded border">
            {m.batches.map((b) => (
              <label key={b.id} className="flex items-center gap-2 border-b px-2 py-1 text-sm last:border-b-0">
                <input type="checkbox" checked={selected.has(b.id)} onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(b.id); else n.delete(b.id); return n; })} />
                <span className="flex-1">#{b.id} {b.company_name} {b.year}</span>
                <span className="text-xs text-gray-500">{b.documents.toLocaleString("de-DE")} Belege</span>
              </label>
            ))}
          </div>
        </div>
        <div className="mb-4 space-y-1 text-sm text-gray-700">
          <label className="flex items-center gap-2"><input type="checkbox" checked={restoreFiles} disabled={!m.include_pdfs} onChange={(e) => setRestoreFiles(e.target.checked)} />
            PDF-Dateien aus dem Backup zurückschreiben {!m.include_pdfs && <span className="text-xs text-gray-400">(Backup enthält keine PDFs)</span>}</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={vendors} disabled={!m.include_vendors} onChange={(e) => setVendors(e.target.checked)} />
            Lieferanten-Stammdaten ergänzen (nur neue Namen / fehlende Bankverbindungen)</label>
          {mode === "remap" && <label className="flex items-center gap-2"><input type="checkbox" checked={dups} onChange={(e) => setDups(e.target.checked)} />
            Auch bereits vorhandene Imports erneut anlegen (Duplikate erlauben)</label>}
        </div>
        {mode === "remap" && !restoreFiles && <p className="mb-3 rounded bg-amber-50 p-2 text-xs text-amber-800">Ohne PDF-Dateien fehlen bei neuen Beleg-IDs die PDFs (Daten sind da, Vorschau/Neu-Analyse nicht).</p>}
        <div className="flex gap-2">
          <button onClick={() => start(true)} disabled={busy || selected.size === 0} className="rounded border border-blue-600 px-4 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-50" data-testid="restore-preview">Vorschau</button>
          <button onClick={() => start(false)} disabled={busy || selected.size === 0} className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50" data-testid="restore-run">Jetzt importieren</button>
          <button onClick={onClose} className="ml-auto btn btn-secondary btn-sm">Schließen</button>
        </div>
      </div>
    </div>
  );
}

export default function DataBackupPage() {
  const [path, setPath] = useState("");
  const [backups, setBackups] = useState<BackupFile[]>([]);
  const [job, setJob] = useState<BackupJobState>({ running: false });
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [includePdfs, setIncludePdfs] = useState(false);
  const [includeVendors, setIncludeVendors] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<BackupFile | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const o = await dataBackupApi.overview();
      setPath(o.path); setBackups(o.backups); setJob(o.state);
    } catch (e) { setError(extractApiError(e, "Backups konnten nicht geladen werden")); }
  }, []);

  useEffect(() => {
    load();
    importsApi.list({ include_hidden: true }).then(setBatches).catch(() => undefined);
  }, [load]);

  // Fortschritt abfragen, solange ein Auftrag läuft
  useEffect(() => {
    if (!job.running) return;
    const t = setInterval(async () => {
      try {
        const st = await dataBackupApi.state();
        setJob(st);
        if (!st.running) load();
      } catch { /* nächster Takt */ }
    }, 1500);
    return () => clearInterval(t);
  }, [job.running, load]);

  async function create() {
    setError(null);
    try { setJob(await dataBackupApi.create({ include_pdfs: includePdfs, include_vendors: includeVendors, batch_ids: selected.size ? Array.from(selected) : null })); }
    catch (e) { setError(extractApiError(e, "Backup konnte nicht gestartet werden")); }
  }

  async function remove(b: BackupFile) {
    if (!window.confirm(`Backup „${b.name}“ endgültig löschen?`)) return;
    try { await dataBackupApi.remove(b.name); load(); } catch (e) { setError(extractApiError(e, "Löschen fehlgeschlagen")); }
  }

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try { await dataBackupApi.upload(file); await load(); }
    catch (e) { setError(extractApiError(e, "Hochladen fehlgeschlagen")); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  }

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Daten-Backup &amp; Import</h1>
      <p className="mb-4 text-sm text-gray-500">
        Sichert alles, was zu den Imports gehört: Imports, Belege, KI-Auswertungen (Rechnungsdaten, Positionen, Analyse-Historie, PDF-Versionen, Token-Statistik),
        auf Wunsch Lieferanten und die PDF-Dateien. Das Backup liegt als ZIP-Datei im Ordner <code className="rounded bg-gray-100 px-1">{path || "…"}</code> auf dem NAS
        (Export-Ordner) — kopiere sie zusätzlich auf ein anderes Laufwerk. Einstellungen (KI, Prompts, Workflows) sichert „Backups / Wiederherstellung“.
      </p>
      {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {job.running && <Progress job={job} onCancel={() => dataBackupApi.cancel().then(() => undefined)} />}
      <ResultBox job={job} />

      <section className="mb-6 card p-4">
        <h2 className="mb-2 text-lg font-semibold text-gray-900">Neues Backup</h2>
        <div className="mb-3 space-y-1 text-sm text-gray-700">
          <label className="flex items-center gap-2"><input type="checkbox" checked={includePdfs} onChange={(e) => setIncludePdfs(e.target.checked)} data-testid="opt-pdfs" />
            PDF-Dateien mitsichern <span className="text-xs text-gray-400">(deutlich größer; ohne PDFs lassen sich die Daten bei intaktem Speicher mit Original-IDs wiederherstellen)</span></label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={includeVendors} onChange={(e) => setIncludeVendors(e.target.checked)} />Lieferanten-Stammdaten mitsichern</label>
        </div>
        <details className="mb-3 text-sm">
          <summary className="cursor-pointer text-gray-600">Nur bestimmte Imports sichern ({selected.size ? `${selected.size} gewählt` : "alle"})</summary>
          <div className="mt-2 max-h-48 overflow-y-auto rounded border">
            {batches.map((b) => (
              <label key={b.id} className="flex items-center gap-2 border-b px-2 py-1 last:border-b-0">
                <input type="checkbox" checked={selected.has(b.id)} onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(b.id); else n.delete(b.id); return n; })} />
                #{b.id} {b.company_name} {b.year}{b.hidden_at ? " (ausgeblendet)" : ""}
              </label>
            ))}
          </div>
        </details>
        <button onClick={create} disabled={job.running} className="btn btn-primary" data-testid="create-backup">
          Backup erstellen
        </button>
        <p className="mt-2 text-xs text-gray-400">Läuft im Hintergrund; die Seite darf geschlossen werden. Während eines Backups bitte keine großen Importe starten.</p>
      </section>

      <section className="card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Vorhandene Backups</h2>
          <div>
            <input ref={fileInput} type="file" accept=".zip" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <button onClick={() => fileInput.current?.click()} disabled={uploading} className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">
              {uploading ? "Lade hoch…" : "Backup-Datei hochladen"}
            </button>
          </div>
        </div>
        {backups.length === 0 ? <p className="text-sm text-gray-400">Noch keine Backups.</p> : (
          <table className="min-w-full text-sm" data-testid="backup-list">
            <thead className="text-xs text-gray-500"><tr>
              <th className="px-2 py-1 text-left">Datei</th><th className="px-2 py-1 text-left">Erstellt</th><th className="px-2 py-1 text-left">Inhalt</th>
              <th className="px-2 py-1 text-right">Größe</th><th />
            </tr></thead>
            <tbody className="divide-y">
              {backups.map((b) => (
                <tr key={b.name}>
                  <td className="px-2 py-1.5 font-medium text-gray-800">{b.name}</td>
                  <td className="px-2 py-1.5 text-gray-500">{b.manifest ? fmtDate(b.manifest.created_at) : fmtDate(b.modified)}</td>
                  <td className="px-2 py-1.5 text-xs text-gray-600">
                    {b.error ? <span className="text-red-600">{b.error}</span> : (
                      <>{b.manifest!.batches.length} Import(s) · {(b.manifest!.counts.documents ?? 0).toLocaleString("de-DE")} Belege · {(b.manifest!.counts.order_positions ?? 0).toLocaleString("de-DE")} Positionen
                        {b.manifest!.include_pdfs ? ` · ${(b.manifest!.files.docs + b.manifest!.files.versions).toLocaleString("de-DE")} PDFs` : " · ohne PDFs"}</>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{fmtSize(b.size)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    {b.manifest && <button onClick={() => setRestoring(b)} disabled={job.running} className="mr-2 rounded border border-emerald-600 px-2 py-0.5 text-xs text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">Importieren…</button>}
                    <a href={dataBackupApi.downloadUrl(b.name)} download className="mr-2 text-xs text-blue-600 hover:underline">Download</a>
                    <button onClick={() => remove(b)} disabled={job.running} className="text-xs text-red-600 hover:underline disabled:opacity-50">Löschen</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {restoring && <RestoreDialog backup={restoring} onClose={() => setRestoring(null)} onStarted={() => { setRestoring(null); load(); setJob((j) => ({ ...j, running: true })); }} />}
    </div>
  );
}
