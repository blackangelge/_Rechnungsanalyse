"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChangeEntry, ChangeRun, changeLogApi, extractApiError } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  backfill_rules: "Prüfung nachgerüstet",
  backfill_normalize: "Einheiten auf Tonnen",
  backfill_restore: "Originalwerte wiederhergestellt",
  backfill_fill_prices: "Gesamtpreise ergänzt",
  browser: "Daten-Browser",
  assistant: "KI-Vorschlag freigegeben",
};

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString("de-DE") : "–");

function Details({ run, onClose }: { run: ChangeRun; onClose: () => void }) {
  const [items, setItems] = useState<ChangeEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const size = 100;

  useEffect(() => {
    changeLogApi.changes(run.id, offset, size).then((r) => { setItems(r.items); setTotal(r.total); })
      .catch((e) => setError(extractApiError(e, "Änderungen konnten nicht geladen werden")));
  }, [run.id, offset]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6">
      <div className="w-full max-w-5xl rounded-lg bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-gray-900">Vorgang #{run.id} — {KIND_LABEL[run.kind] ?? run.kind}</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">✕</button>
        </div>
        {error && <div className="mb-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
        <div className="mb-2 flex items-center gap-3 text-xs text-gray-500">
          <span>{total.toLocaleString("de-DE")} Feldänderung(en)</span>
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - size))} className="rounded border px-2 py-0.5 disabled:opacity-40">‹</button>
          <span>{offset + 1}–{Math.min(offset + size, total)}</span>
          <button disabled={offset + size >= total} onClick={() => setOffset(offset + size)} className="rounded border px-2 py-0.5 disabled:opacity-40">›</button>
        </div>
        <div className="max-h-[60vh] overflow-auto rounded border">
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 bg-gray-50 text-gray-600">
              <tr><th className="px-2 py-1 text-left">Beleg</th><th className="px-2 py-1 text-left">Tabelle #ID</th><th className="px-2 py-1 text-left">Feld</th>
                <th className="px-2 py-1 text-left">Vorher</th><th className="px-2 py-1 text-left">Nachher</th><th className="px-2 py-1 text-left">Stand</th></tr>
            </thead>
            <tbody className="divide-y">
              {items.map((c) => (
                <tr key={c.id}>
                  <td className="px-2 py-1">{c.document_id ? <Link href={`/belege?doc_id=${c.document_id}`} className="text-blue-600 hover:underline">#{c.document_id}</Link> : "–"}</td>
                  <td className="px-2 py-1 text-gray-600">{c.table} #{c.row_id}</td>
                  <td className="px-2 py-1 font-medium">{c.field}</td>
                  <td className="max-w-[16rem] truncate px-2 py-1 text-gray-500" title={c.old ?? ""}>{c.old ?? <i>NULL</i>}</td>
                  <td className="max-w-[16rem] truncate px-2 py-1 text-emerald-700" title={c.new ?? ""}>{c.new ?? <i>NULL</i>}</td>
                  <td className="px-2 py-1">{c.status === 1 ? "zurückgesetzt" : c.status === 2 ? "übersprungen" : "geändert"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** Verlauf der Nachrüst-Läufe und manuellen Änderungen — mit „Rückgängig machen“ */
export default function ChangeRuns({ kindPrefix }: { kindPrefix?: string }) {
  const [runs, setRuns] = useState<ChangeRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [details, setDetails] = useState<ChangeRun | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try { setRuns(await changeLogApi.runs(kindPrefix, 15)); setError(null); }
    catch (e) { setError(extractApiError(e, "Protokoll konnte nicht geladen werden")); }
  }, [kindPrefix]);
  useEffect(() => { load(); }, [load]);

  // Solange ein Rückgängig-Lauf läuft, regelmäßig nachladen
  useEffect(() => {
    if (busyId === null) return;
    const t = setInterval(async () => {
      const st = await changeLogApi.undoState().catch(() => null);
      if (st && !st.running) { setBusyId(null); load(); }
    }, 2000);
    return () => clearInterval(t);
  }, [busyId, load]);

  async function undo(run: ChangeRun) {
    if (!window.confirm(
      `Vorgang #${run.id} (${KIND_LABEL[run.kind] ?? run.kind}, ${run.changed_fields.toLocaleString("de-DE")} Feldänderungen) rückgängig machen?\n\n` +
      "Alle Felder werden auf den Wert VOR dem Vorgang zurückgesetzt — außer, sie wurden danach erneut geändert (die bleiben unberührt).")) return;
    try { await changeLogApi.undo(run.id); setBusyId(run.id); }
    catch (e) { setError(extractApiError(e, "Rückgängig machen fehlgeschlagen")); }
  }

  return (
    <div className="mt-6 border-t pt-4" data-testid="change-runs">
      <h3 className="mb-2 text-sm font-semibold text-gray-800">Änderungsprotokoll (zuletzt)</h3>
      {error && <div className="mb-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
      {runs.length === 0 ? <p className="text-sm text-gray-400">Noch keine protokollierten Änderungen.</p> : (
        <table className="min-w-full text-xs">
          <thead className="text-gray-500"><tr>
            <th className="px-2 py-1 text-left">#</th><th className="px-2 py-1 text-left">Art</th><th className="px-2 py-1 text-left">Zeit</th>
            <th className="px-2 py-1 text-right">Zeilen</th><th className="px-2 py-1 text-right">Felder</th><th className="px-2 py-1 text-left">Stand</th><th />
          </tr></thead>
          <tbody className="divide-y">
            {runs.map((r) => (
              <tr key={r.id}>
                <td className="px-2 py-1">{r.id}</td>
                <td className="px-2 py-1" title={r.label ?? ""}>{KIND_LABEL[r.kind] ?? r.kind}</td>
                <td className="px-2 py-1 text-gray-500">{fmtDate(r.started_at)}</td>
                <td className="px-2 py-1 text-right tabular-nums">{r.changed_rows.toLocaleString("de-DE")}</td>
                <td className="px-2 py-1 text-right tabular-nums">{r.changed_fields.toLocaleString("de-DE")}</td>
                <td className="px-2 py-1 text-gray-600">
                  {r.undone_at ? <span title={r.undo_summary ?? ""}>rückgängig gemacht</span> : !r.finished_at ? "läuft / unterbrochen" : "abgeschlossen"}
                </td>
                <td className="whitespace-nowrap px-2 py-1 text-right">
                  <button onClick={() => setDetails(r)} className="mr-2 text-blue-600 hover:underline">Ansehen</button>
                  {r.finished_at && !r.undone_at && r.changed_fields > 0 && (
                    <button onClick={() => undo(r)} disabled={busyId !== null} className="rounded border border-red-300 px-2 py-0.5 text-red-700 hover:bg-red-50 disabled:opacity-50">
                      {busyId === r.id ? "Setze zurück…" : "Rückgängig"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {details && <Details run={details} onClose={() => setDetails(null)} />}
    </div>
  );
}
