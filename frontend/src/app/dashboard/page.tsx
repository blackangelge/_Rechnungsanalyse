/**
 * Seite: Import-Dashboard (/dashboard)
 *
 * Übersicht aller Import-Batches mit Auto-Refresh alle 10 Sekunden.
 * Zeigt eine Warnung wenn keine KI-Konfiguration verfügbar ist
 * (noAI=true aus worker-stats) oder Dokumente in der Queue warten.
 *
 * Lädt parallel:
 *   - Alle Import-Batches (importsApi.list) — alle 10 s
 *   - Worker-Status (workerApi.getStats) — alle 30 s
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ImportBatch, WorkerStats, importsApi, workerApi, extractApiError } from "@/lib/api";
import BatchTable from "@/components/dashboard/BatchTable";

export default function DashboardPage() {
  const [batches, setBatches]           = useState<ImportBatch[]>([]);
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [workerStats, setWorkerStats]   = useState<WorkerStats | null>(null);
  const [showHidden, setShowHidden]     = useState(false);
  const [selected, setSelected]         = useState<Set<number>>(new Set());
  const [actionMsg, setActionMsg]       = useState<string | null>(null);

  /** Alle Import-Batches laden (ohne Dokumente — schneller als die Detail-Ansicht). */
  const load = useCallback(async () => {
    try {
      setError(null);
      // Ausgeblendete werden immer mitgeladen (Anzahl für den Schalter); angezeigt nur auf Wunsch.
      const data = await importsApi.list({ include_hidden: true });
      setBatches(data);
      // Auswahl um nicht mehr vorhandene Imports bereinigen (Auto-Refresh, Löschen)
      setSelected((prev) => {
        const ids = new Set(data.map((b) => b.id));
        const next = new Set(Array.from(prev).filter((id) => ids.has(id)));
        return next.size === prev.size ? prev : next;
      });
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der Imports"));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorkerStats = useCallback(async () => {
    try {
      setWorkerStats(await workerApi.getStats());
    } catch {
      // Fehler still ignorieren — nur ein Hinweis, kein kritischer Fehler
    }
  }, []);

  useEffect(() => {
    load();
    loadWorkerStats();
    const interval = setInterval(load, 10_000);
    const wInterval = setInterval(loadWorkerStats, 30_000);
    return () => { clearInterval(interval); clearInterval(wInterval); };
  }, [load, loadWorkerStats]);

  const visibleBatches = batches.filter((b) => showHidden || !b.hidden_at);
  const hiddenCount = batches.filter((b) => b.hidden_at).length;
  const selectedBatches = batches.filter((b) => selected.has(b.id));
  const selectedHidden = selectedBatches.filter((b) => b.hidden_at).length;

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleSelectAll() {
    const visible = visibleBatches.map((b) => b.id);
    setSelected((prev) => (visible.every((id) => prev.has(id)) ? new Set() : new Set(visible)));
  }

  /** Imports aus-/einblenden (Auswahl oder einzelner Import) */
  async function setHidden(ids: number[], hidden: boolean) {
    setActionMsg(null);
    try {
      const r = await importsApi.setHidden(ids, hidden);
      setActionMsg(
        `${r.updated} Import${r.updated !== 1 ? "s" : ""} ${hidden ? "ausgeblendet" : "eingeblendet"}.` +
        (hidden ? " Rückgängig: Ausgeblendete anzeigen, dann Einblenden." : ""),
      );
      setSelected(new Set());
      await load();
    } catch {
      setError("Aus-/Einblenden fehlgeschlagen");
    }
  }

  const runningCount = batches.filter((b) => b.status === "running").length;
  const noAI         = workerStats?.no_ai_available ?? false;
  const queueLen     = workerStats?.queue_length ?? 0;

  return (
    <div className="space-y-4">
      {/* Kopfzeile */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Import-Dashboard</h1>
          {runningCount > 0 && (
            <p className="text-sm text-blue-600">
              {runningCount} Import{runningCount > 1 ? "s" : ""} aktiv — wird alle 10 s aktualisiert
            </p>
          )}
        </div>
        <div className="flex items-center gap-4">
          {workerStats && (
            <span className="text-sm text-gray-500">
              <span className="font-semibold text-gray-700">
                {workerStats.documents_processed_total.toLocaleString("de-DE")}
              </span>{" "}
              Dokumente insgesamt von der KI verarbeitet
            </span>
          )}
          {hiddenCount > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-gray-600">
              <input
                type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)}
                className="rounded border-gray-300 text-blue-600"
              />
              Ausgeblendete anzeigen ({hiddenCount})
            </label>
          )}
          <button onClick={load} className="text-xs text-blue-600 hover:underline">
            Aktualisieren
          </button>
        </div>
      </div>

      {/* KI-Warnung: keine aktive KI */}
      {noAI && (
        <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="font-semibold">⚠ Keine KI verfügbar!</span>{" "}
          Alle KI-Konfigurationen sind deaktiviert oder temporär gesperrt.
          {queueLen > 0 && (
            <span> {queueLen} Dokument{queueLen !== 1 ? "e" : ""} warten in der Warteschlange.</span>
          )}
          {" "}→{" "}
          <a href="/settings/ai" className="underline font-medium">KI-Einstellungen</a>
          {" "}oder{" "}
          <a href="/logs" className="underline font-medium">KI-Statistiken</a>
        </div>
      )}

      {actionMsg && (
        <div className="rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700" data-testid="action-msg">
          {actionMsg}
        </div>
      )}

      {/* Aktionsleiste bei Auswahl */}
      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5" data-testid="selection-bar">
          <span className="text-sm font-medium text-blue-800">
            {selected.size} Import{selected.size !== 1 ? "s" : ""} ausgewählt
          </span>
          <a
            href={importsApi.exportUrl(Array.from(selected))}
            download
            className="rounded border border-emerald-600 bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-700 hover:bg-emerald-100"
          >
            ↓ Excel exportieren{selected.size > 1 ? " (eine Datei)" : ""}
          </a>
          {selectedHidden < selected.size && (
            <button
              onClick={() => setHidden(selectedBatches.filter((b) => !b.hidden_at).map((b) => b.id), true)}
              className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50"
            >
              Ausblenden
            </button>
          )}
          {selectedHidden > 0 && (
            <button
              onClick={() => setHidden(selectedBatches.filter((b) => b.hidden_at).map((b) => b.id), false)}
              className="rounded border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 hover:bg-gray-50"
            >
              Einblenden
            </button>
          )}
          <button onClick={() => setSelected(new Set())} className="ml-auto text-xs text-blue-700 hover:underline">
            Auswahl aufheben
          </button>
        </div>
      )}

      {/* Status */}
      {loading && batches.length === 0 && (
        <p className="text-sm text-gray-500">Lade...</p>
      )}
      {error && <p className="text-sm text-red-500">{error}</p>}

      {/* Batch-Tabelle */}
      <BatchTable
        batches={visibleBatches}
        onDeleted={(id) => setBatches((prev) => prev.filter((b) => b.id !== id))}
        selectedIds={selected}
        onToggleSelect={toggleSelect}
        onToggleSelectAll={toggleSelectAll}
        onSetHidden={(id, hidden) => setHidden([id], hidden)}
      />

      {/* "+ Neuer Import"-Button unten rechts */}
      <div className="flex justify-end pt-2">
        <Link
          href="/imports/new"
          className="rounded-full bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow hover:bg-blue-700 transition-colors"
        >
          + Neuer Import
        </Link>
      </div>
    </div>
  );
}
